"""GH995: provider headers, startup failures and single-connection ownership."""

import asyncio
from contextlib import asynccontextmanager
import os
import signal
import subprocess
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anyio
import httpx
import pytest
from fastapi import FastAPI

from routers import stream_preview as preview
from security import stream_outbound
from security.ssrf import DNSResolutionError, SSRFError


@pytest.mark.asyncio
async def test_passthrough_opens_once_before_return_and_closes_on_disconnect():
    opened = []
    closed = []

    @asynccontextmanager
    async def upstream(url, **kwargs):
        opened.append(kwargs)
        try:
            yield httpx.Response(200, content=b"media", request=httpx.Request("GET", url))
        finally:
            closed.append(True)

    client = AsyncMock()
    client.get_stream.return_value = {"id": 1, "url": "https://provider.example/live.ts"}
    with patch.object(preview, "get_client", return_value=client), patch.object(
        preview, "get_settings", return_value=SimpleNamespace(
            stream_preview_mode="passthrough", stream_user_agent="tivimate"
        )
    ), patch.object(preview, "stream_request", upstream):
        response = await preview.stream_preview(1)
        assert len(opened) == 1, "Upstream must open before downstream headers"
        assert opened[0]["headers"]["User-Agent"] == "TiviMate/5.1.6 (Android 12)"
        assert not closed
        assert await anext(response.body_iterator) == b"media"
        await response.body_iterator.aclose()
    assert closed == [True]
    client.get_core_settings.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status", [
    (SSRFError("secret-url\r\n"), 403),
    (DNSResolutionError(), 502),
    (httpx.ConnectError("https://user:password@provider.example/private"), 502),
    (httpx.ReadTimeout("https://user:password@provider.example/private"), 504),
])
async def test_passthrough_startup_failure_is_json_not_broken_200(failure, status):
    @asynccontextmanager
    async def upstream(*args, **kwargs):
        raise failure
        yield

    client = AsyncMock()
    client.get_stream.return_value = {"id": 1, "url": "http://93.184.216.34/live.ts"}
    app = FastAPI()
    app.include_router(preview.router)
    with patch.object(preview, "get_client", return_value=client), patch.object(
        preview, "get_settings", return_value=SimpleNamespace(
            stream_preview_mode="passthrough", stream_user_agent="vlc"
        )
    ), patch.object(preview, "stream_request", upstream):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as api:
            response = await api.get("/api/stream-preview/1")
    assert response.status_code == status
    assert "password" not in response.text
    assert "secret-url" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["transcode", "video_only"])
async def test_cancellation_during_relay_acquisition_closes_upstream(mode):
    closed = []
    relays = []
    setup_ready = asyncio.Event()
    cancelled = []
    returned = []
    real_setup = stream_outbound.web.AppRunner.setup
    real_start = stream_outbound._LocalStreamRelay.start

    @asynccontextmanager
    async def upstream(*args, **kwargs):
        try:
            yield httpx.Response(200, request=httpx.Request("GET", "https://provider.example/live.ts"))
        finally:
            # A cancellation checkpoint proves that the unwind itself is shielded.
            await anyio.sleep(0)
            closed.append(True)

    async def setup(runner):
        await real_setup(runner)
        setup_ready.set()
        await asyncio.Event().wait()

    async def start(relay):
        relays.append(relay)
        return await real_start(relay)

    async def acquire():
        try:
            returned.append(await preview._preview_response(
                "https://provider.example/live.ts", mode, {}, provider_media=True
            ))
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    with patch.object(stream_outbound, "stream_request", upstream), patch.object(
        stream_outbound.web.AppRunner, "setup", setup
    ), patch.object(stream_outbound._LocalStreamRelay, "start", start), patch.object(
        preview.subprocess, "Popen"
    ) as spawn:
        try:
            with anyio.fail_after(5):
                async with anyio.create_task_group() as group:
                    group.start_soon(acquire)
                    await setup_ready.wait()
                    group.cancel_scope.cancel()
            assert cancelled == [True]
            assert not returned
            spawn.assert_not_called()
            assert closed == [True]
            await relays[0].close()
            assert closed == [True], "Cleanup is idempotent"
        finally:
            # Repair the old implementation's leak when proving this test red.
            for relay in relays:
                await relay.close()


@pytest.mark.asyncio
async def test_relay_runner_cleanup_failure_still_closes_upstream_once():
    closed = []

    @asynccontextmanager
    async def upstream(*args, **kwargs):
        try:
            yield httpx.Response(200, request=httpx.Request("GET", "https://provider.example/live.ts"))
        finally:
            await anyio.sleep(0)
            closed.append(True)

    relay = stream_outbound._LocalStreamRelay("https://provider.example/live.ts", {}, 5)
    with patch.object(stream_outbound, "stream_request", upstream):
        await relay.start()
        real_cleanup = relay._runner.cleanup

        async def failed_cleanup():
            await real_cleanup()
            raise RuntimeError("runner cleanup failed")

        try:
            with patch.object(stream_outbound.web.AppRunner, "cleanup", side_effect=failed_cleanup) as cleanup:
                with pytest.raises(RuntimeError, match="runner cleanup failed"):
                    await relay.close()
                assert closed == [True]
                await relay.close()
                cleanup.assert_awaited_once()
                assert closed == [True]
        finally:
            await relay.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.ReadTimeout])
async def test_late_passthrough_failure_aborts_with_safe_diagnostic(error_type, caplog):
    closed = []
    secret = "late-private-token-995"

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 65536
            raise error_type(f"https://user:password@provider.example/{secret}?auth=secret")

        async def aclose(self):
            closed.append(True)

    @asynccontextmanager
    async def upstream(*args, **kwargs):
        response = httpx.Response(200, stream=BrokenBody(), request=httpx.Request("GET", "https://provider.example/live.ts"))
        try:
            yield response
        finally:
            await response.aclose()

    messages = []

    async def send(message):
        messages.append(message)

    with patch.object(preview, "stream_request", upstream):
        response = await preview._preview_response("https://provider.example/live.ts", "passthrough", {})
        with pytest.raises(RuntimeError, match="Upstream preview stream failed") as error:
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, None, send)
    assert messages[0]["status"] == 200
    assert messages[1]["body"] == b"x" * 65536
    assert not any(message.get("more_body") is False for message in messages)
    assert closed == [True]
    assert f"[PREVIEW] Upstream streaming failed ({error_type.__name__})" in caplog.text
    rendered_error = "".join(traceback.format_exception(error.value))
    assert secret not in caplog.text + rendered_error
    assert "password" not in caplog.text + rendered_error


@pytest.fixture
def real_preview_child():
    """A real pipe/process lifecycle with no FFmpeg or external provider needed."""
    children = []
    closed = []
    real_popen = subprocess.Popen

    @asynccontextmanager
    async def upstream(*args, **kwargs):
        try:
            yield stream_outbound.ValidatedSubprocessInput("udp://127.0.0.1:1")
        finally:
            closed.append(True)

    def launch(script):
        def spawn(command, **kwargs):
            child = real_popen([sys.executable, "-u", "-c", script], **kwargs)
            children.append(child)
            return child

        return spawn

    with patch.object(preview, "validated_subprocess_input", upstream):
        yield launch, children, closed
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def assert_reaped(child):
    assert child.returncode is not None
    assert child.stdout.closed
    with pytest.raises(ChildProcessError):
        os.waitpid(child.pid, os.WNOHANG)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["transcode", "video_only"])
@pytest.mark.parametrize("returncode", [0, 7])
async def test_decoder_completion_is_awaited_and_failure_is_diagnosed(
    mode, returncode, real_preview_child, caplog
):
    launch, children, closed = real_preview_child
    messages = []

    async def send(message):
        messages.append(message)

    script = f'import sys; sys.stderr.write("private-decoder-token-995\\n"); sys.exit({returncode})'
    with patch.object(preview.subprocess, "Popen", launch(script)):
        response = await preview._preview_response("udp://127.0.0.1:1", mode, {})
        call = response({"type": "http", "asgi": {"spec_version": "2.4"}}, None, send)
        if returncode:
            with pytest.raises(RuntimeError, match="Preview decoder failed"):
                await asyncio.wait_for(call, 10)
            assert "[PREVIEW] Decoder exited abnormally (returncode=7)" in caplog.text
            assert not any(message.get("more_body") is False for message in messages)
        else:
            await asyncio.wait_for(call, 10)
            assert not any(record.name == preview.__name__ for record in caplog.records)
    assert messages[0]["status"] == 200
    assert children[0].returncode == returncode
    assert_reaped(children[0])
    assert closed == [True]
    assert "private-decoder-token-995" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("stubborn", [False, True])
async def test_decoder_disconnect_reaps_without_failure_warning(stubborn, real_preview_child, caplog):
    launch, children, closed = real_preview_child
    first_chunk = asyncio.Event()
    script = 'import os,signal,time; '
    if stubborn:
        script += 'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
    script += 'os.write(1,b"x"*65536); time.sleep(60)'

    async def send(message):
        if message.get("body"):
            first_chunk.set()

    async def receive():
        await first_chunk.wait()
        return {"type": "http.disconnect"}

    with patch.object(preview.subprocess, "Popen", launch(script)):
        response = await preview._preview_response("udp://127.0.0.1:1", "transcode", {})
        await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send), 12)
    assert first_chunk.is_set()
    assert children[0].returncode == -(signal.SIGKILL if stubborn else signal.SIGTERM)
    assert_reaped(children[0])
    assert closed == [True]
    assert not any(record.name == preview.__name__ for record in caplog.records)
