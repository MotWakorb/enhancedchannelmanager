"""SSRF prevalidation for subprocess-backed stream probing (04c0u.6)."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from security.ssrf import SSRFError, SSRFMode, ResolvedTarget
from security.stream_outbound import SSRFPinnedTransport, validate_stream_subprocess_url
from security.stream_outbound import validated_subprocess_input as real_subprocess_input
from stream_prober import StreamProber


def _public_target(url):
    return ResolvedTarget(
        scheme="http",
        hostname="provider.example",
        port=80,
        ip=__import__("ipaddress").ip_address("93.184.216.34"),
        url=url,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["_run_ffprobe", "_run_resdet", "_detect_black_screen"])
async def test_subprocess_probe_rejects_denied_destination_before_spawn(method):
    prober = StreamProber(MagicMock())
    spawn = AsyncMock()

    @asynccontextmanager
    async def denied(*_args, **_kwargs):
        raise SSRFError("denied")
        yield

    with patch("stream_prober.validated_subprocess_input", denied), \
         patch("stream_prober.asyncio.create_subprocess_exec", spawn):
        with pytest.raises(SSRFError):
            await getattr(prober, method)("http://169.254.169.254/latest")

    spawn.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["_run_ffprobe", "_run_resdet", "_detect_black_screen"])
async def test_real_redirect_to_metadata_is_denied_before_subprocess_spawn(method):
    async def handler(request):
        return httpx.Response(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data"},
            request=request,
        )

    transport = SSRFPinnedTransport(
        inner=httpx.MockTransport(handler), mode=SSRFMode.LAN_FRIENDLY
    )
    prober = StreamProber(MagicMock())
    spawn = AsyncMock()
    url = "http://provider.example/live.ts"

    with patch(
        "security.stream_outbound.SSRFPinnedTransport", return_value=transport
    ), patch(
        "security.stream_outbound.validate_outbound_url",
        return_value=_public_target(url),
    ), patch("stream_prober.asyncio.create_subprocess_exec", spawn):
        with pytest.raises(SSRFError):
            await getattr(prober, method)(url)

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_ffprobe_validates_immediately_before_every_spawn_and_retry():
    prober = StreamProber(MagicMock(), probe_retry_count=1, probe_retry_delay=0)
    failed = MagicMock(returncode=1)
    failed.communicate = AsyncMock(return_value=(b"", b"HTTP 503"))
    passed = MagicMock(returncode=0)
    passed.communicate = AsyncMock(return_value=(b'{"streams": []}', b""))

    validations = []

    @asynccontextmanager
    async def allowed(url, **_kwargs):
        validations.append(url)
        yield SimpleNamespace(argument=url, response=None, is_http_relay=False)

    with patch("stream_prober.validated_subprocess_input", allowed), \
         patch(
             "stream_prober.asyncio.create_subprocess_exec",
             AsyncMock(side_effect=[failed, passed]),
         ):
        await prober._run_ffprobe("http://provider.example/live.ts")

    assert validations == [
        "http://provider.example/live.ts",
        "http://provider.example/live.ts",
    ]


@pytest.mark.asyncio
async def test_ffprobe_retry_redirect_to_metadata_is_denied_before_retry_spawn():
    url = "http://provider.example/live.ts"
    attempts = 0

    @asynccontextmanager
    async def input_for_attempt(candidate, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            yield SimpleNamespace(argument=candidate, response=None, is_http_relay=False)
            return
        async with real_subprocess_input(candidate, **kwargs) as value:
            yield value

    async def redirect_handler(request):
        return httpx.Response(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data"},
            request=request,
        )

    transport = SSRFPinnedTransport(
        inner=httpx.MockTransport(redirect_handler), mode=SSRFMode.LAN_FRIENDLY
    )
    failed = MagicMock(returncode=1)
    failed.communicate = AsyncMock(return_value=(b"", b"HTTP 503"))
    spawn = AsyncMock(return_value=failed)
    prober = StreamProber(MagicMock(), probe_retry_count=1, probe_retry_delay=0)

    with patch("stream_prober.validated_subprocess_input", input_for_attempt), patch(
        "security.stream_outbound.SSRFPinnedTransport", return_value=transport
    ), patch(
        "security.stream_outbound.validate_outbound_url",
        return_value=_public_target(url),
    ), patch("stream_prober.asyncio.create_subprocess_exec", spawn):
        with pytest.raises(SSRFError):
            await prober._run_ffprobe(url)

    assert attempts == 2
    assert spawn.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["_run_ffprobe", "_detect_black_screen"])
async def test_http_relay_subprocess_uses_only_relay_and_crypto_protocols(method):
    @asynccontextmanager
    async def relayed(_url, **_kwargs):
        yield SimpleNamespace(
            argument="http://127.0.0.1:1234/resource/0",
            response=None,
            is_http_relay=True,
        )

    process = MagicMock(returncode=0)
    process.stdin = MagicMock()
    process.communicate = AsyncMock()
    spawn = AsyncMock(return_value=process)
    process.communicate.return_value = (
        b'{"streams": []}' if method == "_run_ffprobe" else b"",
        b"lavfi.signalstats.YAVG=50.0" if method == "_detect_black_screen" else b"",
    )
    prober = StreamProber(MagicMock())

    with patch("stream_prober.validated_subprocess_input", relayed), patch(
        "stream_prober.asyncio.create_subprocess_exec", spawn
    ):
        await getattr(prober, method)("https://provider.example/live.ts")

    command = spawn.await_args.args
    assert command[command.index("-protocol_whitelist") + 1] == "http,tcp,crypto"
    assert "https://provider.example/live.ts" not in command


def test_subprocess_validator_preserves_lan_udp_iptv_policy():
    with patch(
        "security.stream_outbound.get_ssrf_mode",
        return_value=SSRFMode.LAN_FRIENDLY,
    ):
        validate_stream_subprocess_url("udp://192.168.50.20:5000")


def test_subprocess_validator_rejects_lan_udp_in_public_only_mode():
    with patch(
        "security.stream_outbound.get_ssrf_mode",
        return_value=SSRFMode.PUBLIC_ONLY,
    ):
        with pytest.raises(SSRFError):
            validate_stream_subprocess_url("udp://192.168.50.20:5000")


def test_subprocess_validator_always_rejects_multicast_iptv_destination():
    with patch(
        "security.stream_outbound.get_ssrf_mode",
        return_value=SSRFMode.LAN_FRIENDLY,
    ):
        with pytest.raises(SSRFError):
            validate_stream_subprocess_url("udp://239.10.10.10:5000")
