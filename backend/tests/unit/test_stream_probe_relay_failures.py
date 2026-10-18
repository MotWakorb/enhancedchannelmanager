"""GH995 follow-ups: bounded relay acquisition, retries and optional sampling."""

import asyncio
from contextlib import asynccontextmanager
import ipaddress
import json
import re
import threading
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from aiohttp import ClientPayloadError, ClientSession

from models import StreamStats
from security import stream_outbound as outbound
from security.ssrf import DNSResolutionError, SSRFError
import stream_prober as probes


URL = "https://provider.example/account/synthetic-secret/live.ts"
METADATA = {"streams": [{"codec_type": "video", "codec_name": "h264",
                         "width": 1920, "height": 1080, "avg_frame_rate": "25/1"}],
            "format": {"format_name": "mpegts"}}
pytestmark = pytest.mark.usefixtures("vlc_stream_user_agent")


@pytest.fixture
def provider(monkeypatch):
    """Keep real validation, HTTPX and loopback relay; fake only provider sockets."""
    calls = []
    outcomes = []
    validations = []

    def resolve(host, port):
        validations.append(host)
        return [ipaddress.ip_address("93.184.216.34")]

    async def handler(request):
        calls.append(request)
        outcome = outcomes.pop(0) if outcomes else 200
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return await outcome(request)
        return httpx.Response(outcome, content=b"media", request=request)

    transport = outbound.SSRFPinnedTransport
    monkeypatch.setattr("security.ssrf._resolve", resolve)
    monkeypatch.setattr(outbound, "SSRFPinnedTransport", lambda **kwargs: transport(
        inner_factory=lambda: httpx.MockTransport(handler), **kwargs))
    return outcomes, calls, validations


def process(returncode=0, stderr=b"", stdout=b'{"streams": []}'):
    result = MagicMock(returncode=returncode)
    result.communicate = AsyncMock(return_value=(stdout, stderr))
    result.wait = AsyncMock()
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [500, 502, 503, 504, 520,
                                    httpx.ReadError(URL), httpx.ConnectError(URL),
                                    httpx.WriteError(URL), httpx.RemoteProtocolError(URL)])
async def test_startup_retry_revalidates_and_reuses_identity(provider, monkeypatch, failure, caplog):
    outcomes, calls, validations = provider
    outcomes.extend([failure, 200])
    spawn = AsyncMock(return_value=process())
    monkeypatch.setattr(probes.asyncio, "create_subprocess_exec", spawn)
    prober = probes.StreamProber(MagicMock(), probe_retry_count=1, probe_retry_delay=0)
    assert await prober._run_ffprobe(URL, user_agent="Chosen-UA") == {"streams": []}
    assert len(calls) == len(validations) == 2
    assert all(call.headers["User-Agent"] == "Chosen-UA" for call in calls)
    spawn.assert_awaited_once()
    assert URL not in caplog.text
    assert "synthetic-secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [0, 2])
async def test_startup_retry_exhaustion_is_bounded(provider, monkeypatch, budget):
    outcomes, calls, validations = provider
    outcomes.extend([503] * (budget + 2))
    spawn = AsyncMock()
    monkeypatch.setattr(probes.asyncio, "create_subprocess_exec", spawn)
    prober = probes.StreamProber(MagicMock(), probe_retry_count=budget, probe_retry_delay=0)
    with pytest.raises(httpx.HTTPStatusError):
        await prober._run_ffprobe(URL, user_agent="UA")
    assert len(calls) == len(validations) == budget + 1
    spawn.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 501])
async def test_permanent_startup_refusal_never_retries(provider, status):
    outcomes, calls, _ = provider
    outcomes.extend([status, 200])
    prober = probes.StreamProber(MagicMock(), probe_retry_count=2, probe_retry_delay=0)
    with pytest.raises(httpx.HTTPStatusError):
        await prober._run_ffprobe(URL, user_agent="UA")
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectTimeout(URL), httpx.ReadTimeout(URL)])
async def test_startup_timeout_never_retries(provider, error):
    outcomes, calls, _ = provider
    outcomes.extend([error, 200])
    prober = probes.StreamProber(MagicMock(), probe_retry_count=2, probe_retry_delay=1)
    with pytest.raises(type(error)):
        await prober._run_ffprobe(URL, user_agent="UA")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_startup_and_stderr_share_one_retry_budget(provider, monkeypatch):
    outcomes, calls, _ = provider
    outcomes.extend([503, 200])
    spawn = AsyncMock(return_value=process(1, b"Connection reset"))
    monkeypatch.setattr(probes.asyncio, "create_subprocess_exec", spawn)
    prober = probes.StreamProber(MagicMock(), probe_retry_count=1, probe_retry_delay=0)
    with pytest.raises(RuntimeError):
        await prober._run_ffprobe(URL, user_agent="UA")
    assert len(calls) == 2
    spawn.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["_run_ffprobe", "_detect_black_screen"])
@pytest.mark.parametrize("error", [SSRFError("Denied"), asyncio.CancelledError(),
                                 FileNotFoundError("missing ffmpeg"), ValueError("bug"),
                                 httpx.LocalProtocolError("bad headers"),
                                 httpx.UnsupportedProtocol("configuration error")])
async def test_security_cancellation_and_programming_errors_propagate(monkeypatch, method, error):
    entries = []

    @asynccontextmanager
    async def fail(*args, **kwargs):
        entries.append(kwargs)
        raise error
        yield

    monkeypatch.setattr(probes, "validated_subprocess_input", fail)
    prober = probes.StreamProber(MagicMock(), probe_retry_count=2, probe_retry_delay=0)
    with pytest.raises(type(error)):
        await getattr(prober, method)(URL, user_agent="UA")
    assert len(entries) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ReadError(URL), httpx.ConnectTimeout(URL),
                                 httpx.HTTPStatusError(URL, request=httpx.Request("GET", URL),
                                                       response=httpx.Response(503)),
                                 asyncio.TimeoutError(), DNSResolutionError()])
async def test_optional_network_failure_preserves_success_and_prior_black_state(
    monkeypatch, test_session, error, caplog
):
    stats = StreamStats(stream_id=7, is_black_screen=True, consecutive_failures=2)
    test_session.add(stats)
    test_session.commit()
    monkeypatch.setattr(probes, "get_session", lambda: test_session)

    @asynccontextmanager
    async def fail(*args, **kwargs):
        raise error
        yield

    monkeypatch.setattr(probes, "validated_subprocess_input", fail)
    prober = probes.StreamProber(MagicMock(), black_screen_detection_enabled=True)
    assert await prober._detect_black_screen(URL, user_agent="UA") is None
    prober._run_ffprobe = AsyncMock(return_value=METADATA)
    prober._measure_stream_bitrate = AsyncMock(return_value=5_000_000)
    prober._push_stats_to_dispatcharr = AsyncMock()
    saved = await prober.probe_stream(7, URL, "Test")
    persisted = test_session.query(StreamStats).filter_by(stream_id=7).one()
    assert saved["probe_status"] == persisted.probe_status == "success"
    assert persisted.resolution == "1920x1080"
    assert persisted.video_bitrate == 5_000_000
    assert persisted.is_black_screen is True
    assert persisted.consecutive_failures == 0
    assert "synthetic-secret" not in caplog.text


@pytest.mark.asyncio
async def test_abnormal_ffmpeg_exit_discards_partial_yavg(monkeypatch):
    @asynccontextmanager
    async def allowed(*args, **kwargs):
        yield outbound.ValidatedSubprocessInput("http://127.0.0.1/input", True)

    monkeypatch.setattr(probes, "validated_subprocess_input", allowed)
    monkeypatch.setattr(probes.asyncio, "create_subprocess_exec", AsyncMock(
        return_value=process(1, b"lavfi.signalstats.YAVG=16\n")))
    prober = probes.StreamProber(MagicMock())
    assert await prober._detect_black_screen(URL, user_agent="UA") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["_run_ffprobe", "_run_resdet_pipeline", "_detect_black_screen"])
async def test_all_subprocess_phases_pass_finite_io_and_startup_budgets(monkeypatch, phase):
    captured = []

    @asynccontextmanager
    async def inspect(*args, **kwargs):
        captured.append(kwargs)
        raise SSRFError("stop before spawn")
        yield

    monkeypatch.setattr(probes, "validated_subprocess_input", inspect)
    prober = probes.StreamProber(MagicMock(), probe_timeout=40, black_screen_sample_duration=60)
    args = (URL, 123) if phase == "_run_resdet_pipeline" else (URL,)
    with pytest.raises(SSRFError):
        await getattr(prober, phase)(*args, user_agent="UA")
    timeout = captured[0]["timeout"]
    assert all(0 < value < float("inf") for value in timeout.as_dict().values())
    assert timeout.read >= (60 if phase == "_detect_black_screen" else 40)
    assert 0 < captured[0]["startup_timeout"] <= 40


@pytest.mark.asyncio
@pytest.mark.parametrize("stall", ["dns", "redirect"])
async def test_complete_startup_is_bounded(provider, monkeypatch, stall):
    outcomes, calls, _ = provider
    cancelled = asyncio.Event()

    async def never(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    if stall == "dns":
        monkeypatch.setattr(outbound, "_validate_with_dns_retry", never)
    else:
        outcomes.extend([
            lambda request: redirect_response(request),
            lambda request: never(),
        ])
    # Outer deadline is a test watchdog, not the production startup bound.
    async with asyncio.timeout(1):
        with pytest.raises(TimeoutError):
            async with outbound.validated_subprocess_input(URL, timeout=httpx.Timeout(10),
                                                            startup_timeout=0.03):
                pytest.fail("stalled startup yielded")
    assert cancelled.is_set()
    if stall == "redirect":
        assert len(calls) == 2


async def redirect_response(request):
    return httpx.Response(302, headers={"Location": "/edge.ts"}, request=request)


@pytest.mark.asyncio
async def test_startup_deadline_stops_awaiting_real_dns_worker(provider, monkeypatch):
    _, calls, _ = provider
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def resolve(host, port):
        entered.set()
        try:
            assert release.wait(2), "test must release its DNS worker"
            return [ipaddress.ip_address("93.184.216.34")]
        finally:
            finished.set()

    monkeypatch.setattr("security.ssrf._resolve", resolve)
    try:
        async with asyncio.timeout(1):
            with pytest.raises(TimeoutError):
                async with outbound.validated_subprocess_input(URL, startup_timeout=0.03):
                    pytest.fail("DNS startup yielded")
        assert entered.is_set()
        assert not finished.is_set()
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 1)
    assert calls == []


@pytest.mark.asyncio
async def test_timeout_values_reach_pinned_provider_request(provider):
    _, calls, _ = provider
    timeout = httpx.Timeout(connect=2, read=65, write=3, pool=4)
    async with outbound.validated_subprocess_input(URL, timeout=timeout, startup_timeout=1):
        assert calls[0].extensions["timeout"] == timeout.as_dict()


@pytest.mark.asyncio
async def test_retry_closes_upstream_before_delay(provider, monkeypatch):
    outcomes, _, _ = provider
    body = DisconnectStream()

    async def unavailable(request):
        return httpx.Response(503, stream=body, request=request)

    outcomes.extend([unavailable, 200])
    delays = []
    real_sleep = asyncio.sleep

    async def delay(seconds):
        if seconds == 0:  # aiohttp also yields to the loop during cleanup.
            return await real_sleep(0)
        assert body.closed.is_set()
        delays.append(seconds)

    monkeypatch.setattr(probes.asyncio, "sleep", delay)
    monkeypatch.setattr(probes.asyncio, "create_subprocess_exec", AsyncMock(return_value=process()))
    prober = probes.StreamProber(MagicMock(), probe_retry_count=1, probe_retry_delay=3)
    assert await prober._run_ffprobe(URL, user_agent="UA") == {"streams": []}
    assert delays == [3]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["_run_ffprobe", "_detect_black_screen"])
async def test_cancellation_after_spawn_reaps_process(monkeypatch, method):
    @asynccontextmanager
    async def allowed(*args, **kwargs):
        yield outbound.ValidatedSubprocessInput("http://127.0.0.1/input", True)

    child = process()
    child.communicate.side_effect = asyncio.CancelledError()
    monkeypatch.setattr(probes, "validated_subprocess_input", allowed)
    monkeypatch.setattr(probes.asyncio, "create_subprocess_exec", AsyncMock(return_value=child))
    prober = probes.StreamProber(MagicMock())
    with pytest.raises(asyncio.CancelledError):
        await getattr(prober, method)(URL, user_agent="UA")
    child.kill.assert_called_once()
    child.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_optional_sampling_process_spawn_failure_propagates(monkeypatch):
    @asynccontextmanager
    async def allowed(*args, **kwargs):
        yield outbound.ValidatedSubprocessInput("http://127.0.0.1/input", True)

    monkeypatch.setattr(probes, "validated_subprocess_input", allowed)
    monkeypatch.setattr(probes.asyncio, "create_subprocess_exec", AsyncMock(
        side_effect=FileNotFoundError("missing ffmpeg")))
    with pytest.raises(FileNotFoundError):
        await probes.StreamProber(MagicMock())._detect_black_screen(URL, user_agent="UA")


@pytest.mark.asyncio
async def test_optional_sampling_cancellation_does_not_save_or_increment_strikes(monkeypatch):
    @asynccontextmanager
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()
        yield

    monkeypatch.setattr(probes, "validated_subprocess_input", cancelled)
    prober = probes.StreamProber(MagicMock(), black_screen_detection_enabled=True)
    prober._run_ffprobe = AsyncMock(return_value=METADATA)
    prober._measure_stream_bitrate = AsyncMock(return_value=5_000_000)
    prober._save_probe_result = MagicMock()
    with pytest.raises(asyncio.CancelledError):
        await prober.probe_stream(7, URL, "Test")
    prober._save_probe_result.assert_not_called()


class DisconnectStream(httpx.AsyncByteStream):
    def __init__(self, stalled=False):
        self.closed = asyncio.Event()
        self.stalled = stalled

    async def __aiter__(self):
        while not self.closed.is_set():
            yield b"x" * 65536
            if self.stalled:
                await asyncio.Event().wait()
            await asyncio.sleep(0.005)

    async def aclose(self):
        self.closed.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("stalled", [False, True])
@pytest.mark.parametrize("initial", [True, False])
async def test_real_relay_consumer_quit_closes_upstream_before_context_exit(
    provider, caplog, stalled, initial
):
    outcomes, _, _ = provider
    body = DisconnectStream(stalled=stalled)

    async def streaming(request):
        return httpx.Response(200, stream=body, request=request)

    outcomes.extend([streaming] if initial else [200, streaming])
    async with outbound.validated_subprocess_input(URL, timeout=httpx.Timeout(1)) as source:
        async with ClientSession() as client:
            if not initial:
                async with client.get(source.argument) as first:
                    assert await first.read() == b"media"
            response = await client.get(source.argument)
            assert await response.content.read(1) == b"x"
            response.close()
        await asyncio.wait_for(body.closed.wait(), timeout=1)
        assert "Error handling request" not in caplog.text
        assert "ConnectionResetError" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ReadError(URL), ConnectionResetError("upstream"),
                                 asyncio.CancelledError()])
async def test_relay_does_not_swallow_upstream_failure_or_cancellation(monkeypatch, error):
    class FailingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise error
            yield

    downstream = MagicMock()
    downstream.prepare = AsyncMock()
    downstream.write_eof = AsyncMock()
    monkeypatch.setattr(outbound.web, "StreamResponse", MagicMock(return_value=downstream))
    relay = outbound._LocalStreamRelay(URL, None, httpx.Timeout(1))
    response = httpx.Response(200, stream=FailingStream())
    with pytest.raises(type(error)):
        await relay._serve(MagicMock(), response, URL)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["prepare", "write", "write_eof"])
@pytest.mark.parametrize("error", [ConnectionResetError, BrokenPipeError])
async def test_relay_suppresses_only_expected_downstream_disconnect(monkeypatch, operation, error):
    downstream = MagicMock()
    for name in ("prepare", "write", "write_eof"):
        setattr(downstream, name, AsyncMock())
    getattr(downstream, operation).side_effect = error("consumer quit")
    monkeypatch.setattr(outbound.web, "StreamResponse", MagicMock(return_value=downstream))
    relay = outbound._LocalStreamRelay(URL, None, httpx.Timeout(1))
    response = httpx.Response(200, content=b"media")
    assert await relay._serve(MagicMock(), response, URL) is downstream


HLS_URL = "https://provider.example/account/synthetic-secret/live.m3u8"


def install_resource_provider(provider, failure, resource="segment", *, recover=False):
    """Real validator/pinned transport/relay, including keys and nested manifests."""
    outcomes, calls, _ = provider
    attempts = 0

    async def serve(request):
        nonlocal attempts
        if request.url.path.endswith("live.m3u8"):
            attempts += 1
            target = "http://169.254.169.254/latest/meta-data" if failure == "policy" else {
                "segment": "segment.ts", "key": "key.bin", "nested": "nested.m3u8",
            }[resource]
            manifest = (f'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="{target}"\n'
                        if resource == "key" else f"#EXTM3U\n{target}\n")
            return httpx.Response(200, content=manifest.encode(), request=request)
        if recover and attempts > 1:
            return httpx.Response(200, content=b"media", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout(URL)
        if failure == "drop":
            raise httpx.ReadError(URL)
        return httpx.Response(failure, request=request)

    outcomes.extend([serve] * 20)
    return calls


def install_relay_decoder(monkeypatch, *, black=False, returncode=1, body=False, resdet=False,
                           decoder_timeout=False, resource_body_failure=False):
    """Only decoder is replaced: consume actual tokenized loopback HTTP resources."""
    spawns = []

    async def spawn(*cmd, **kwargs):
        spawns.append(cmd)
        child = process(returncode)
        local_url = cmd[cmd.index("-i") + 1] if black or resdet else cmd[-1]

        async def communicate():
            async with ClientSession() as client:
                async with client.get(local_url) as manifest:
                    try:
                        text = await manifest.text()
                    except ClientPayloadError:
                        assert body
                        text = ""
                if body:
                    status = manifest.status
                else:
                    resource_url = re.search(r"http://[^\s\"]+", text).group()
                    async with client.get(resource_url) as response:
                        try:
                            await response.read()
                        except ClientPayloadError:
                            # A real decoder may return partial metadata after
                            # the relay aborts an already-started resource body.
                            assert resource_body_failure
                        status = response.status
            # Simulate partial metadata/brightness even on a failing HTTP fetch.
            diagnostic = f"HTTP error {status} Bad Gateway\nlavfi.signalstats.YAVG=16\n"
            if decoder_timeout:
                raise asyncio.TimeoutError()
            return json.dumps(METADATA).encode(), diagnostic.encode()

        child.communicate.side_effect = communicate
        if resdet:
            async def read_frame(size):
                await communicate()
                # Decoder claims a valid frame even after a resource failure.
                frame = b"YUV4MPEG2 W2 H2 F25:1 Ip A1:1 C420\nFRAME\n" + b"\0" * 6
                raise asyncio.IncompleteReadError(frame, size)

            child.stdout.readexactly = AsyncMock(side_effect=read_frame)
        return child

    monkeypatch.setattr(probes.asyncio, "create_subprocess_exec", spawn)
    return spawns


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["segment", "key", "nested"])
@pytest.mark.parametrize("failure,expected", [
    ("policy", SSRFError), ("timeout", httpx.ReadTimeout),
    (401, httpx.HTTPStatusError), (403, httpx.HTTPStatusError), (404, httpx.HTTPStatusError),
])
@pytest.mark.parametrize("returncode", [0, 1])
async def test_later_resource_nonretryable_failure_overrides_decoder_output(
    provider, monkeypatch, resource, failure, expected, returncode, caplog
):
    calls = install_resource_provider(provider, failure, resource)
    spawns = install_relay_decoder(monkeypatch, returncode=returncode)
    prober = probes.StreamProber(MagicMock(), probe_retry_count=1, probe_retry_delay=0)
    with pytest.raises(expected):
        await prober._run_ffprobe(HLS_URL, user_agent="Chosen-UA")
    assert len([call for call in calls if call.url.path.endswith("live.m3u8")]) == 1
    assert len(spawns) == 1
    assert all(call.headers["User-Agent"] == "Chosen-UA" for call in calls)
    assert "synthetic-secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [503, "drop"])
async def test_later_resource_transient_failure_uses_existing_retry_budget(provider, monkeypatch, failure):
    calls = install_resource_provider(provider, failure, recover=True)
    spawns = install_relay_decoder(monkeypatch, returncode=0)
    prober = probes.StreamProber(MagicMock(), probe_retry_count=1, probe_retry_delay=0)
    assert await prober._run_ffprobe(HLS_URL, user_agent="UA") == METADATA
    assert len([call for call in calls if call.url.path.endswith("live.m3u8")]) == 2
    assert len(spawns) == 2
    assert spawns[0][-1] != spawns[1][-1]  # Fresh opaque relay per retry.
    assert len(provider[2]) == 4  # Initial manifest and resource validated each time.


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["policy", "timeout", 401, 404, 503])
@pytest.mark.parametrize("returncode", [0, 1])
async def test_optional_later_resource_failure_persistence(
    provider, monkeypatch, test_session, failure, returncode, caplog
):
    install_resource_provider(provider, failure)
    spawns = install_relay_decoder(monkeypatch, black=True, returncode=returncode)
    test_session.add(StreamStats(stream_id=7, is_black_screen=True, consecutive_failures=2))
    test_session.commit()
    monkeypatch.setattr(probes, "get_session", lambda: test_session)
    prober = probes.StreamProber(MagicMock(), black_screen_detection_enabled=True)
    if failure == "policy":
        with pytest.raises(SSRFError):
            await prober._detect_black_screen(HLS_URL, user_agent="UA")
    else:
        assert await prober._detect_black_screen(HLS_URL, user_agent="UA") is None
    prober._run_ffprobe = AsyncMock(return_value=METADATA)
    prober._measure_stream_bitrate = AsyncMock(return_value=5_000_000)
    prober._push_stats_to_dispatcharr = AsyncMock()
    saved = await prober.probe_stream(7, HLS_URL, "Test")
    persisted = test_session.query(StreamStats).filter_by(stream_id=7).one()
    if failure == "policy":
        assert saved["probe_status"] == persisted.probe_status == "failed"
        assert persisted.consecutive_failures == 3
    else:
        assert saved["probe_status"] == persisted.probe_status == "success"
        assert persisted.consecutive_failures == 0
        assert persisted.is_black_screen is True
        assert persisted.resolution == "1920x1080"
        assert persisted.video_bitrate == 5_000_000
    assert len(spawns) == 2
    assert "synthetic-secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ReadTimeout(URL), httpx.ReadError(URL), SSRFError("Denied")])
@pytest.mark.parametrize("url", [HLS_URL, URL])
async def test_initial_body_failure_retains_provenance(provider, monkeypatch, failure, url, caplog):
    class FailingBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise failure
            yield

    async def serve(request):
        return httpx.Response(200, stream=FailingBody(), request=request)

    provider[0].extend([serve] * 3)
    spawns = install_relay_decoder(monkeypatch, returncode=0, body=True)
    prober = probes.StreamProber(MagicMock(), probe_retry_count=0)
    with pytest.raises(type(failure)):
        await prober._run_ffprobe(url, user_agent="UA")
    assert len(spawns) == 1
    assert "synthetic-secret" not in caplog.text
    assert "Error handling request" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected", [("policy", SSRFError), (401, httpx.HTTPStatusError),
                                            ("timeout", httpx.ReadTimeout), (503, httpx.HTTPStatusError)])
@pytest.mark.parametrize("oversized_frame", [False, True])
async def test_resdet_later_resource_failure_precedes_frame_analysis(
    provider, monkeypatch, failure, expected, oversized_frame
):
    install_resource_provider(provider, failure)
    spawns = install_relay_decoder(monkeypatch, returncode=0, resdet=True)
    if oversized_frame:
        monkeypatch.setattr(probes, "RESDET_FRAME_MAX_BYTES", 2)
    prober = probes.StreamProber(MagicMock())
    with pytest.raises(expected):
        await prober._run_resdet_pipeline(HLS_URL, 123, user_agent="UA")
    assert len(spawns) == 1  # Never start resdet on a partial frame from failed HTTP.


@pytest.mark.asyncio
@pytest.mark.parametrize("black", [False, True])
async def test_policy_failure_precedes_subprocess_timeout(provider, monkeypatch, black):
    install_resource_provider(provider, "policy")
    spawns = install_relay_decoder(monkeypatch, black=black, decoder_timeout=True)
    prober = probes.StreamProber(MagicMock())
    with pytest.raises(SSRFError):
        await (prober._detect_black_screen if black else prober._run_ffprobe)(HLS_URL, user_agent="UA")
    assert len(spawns) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("second,expected", [("policy", SSRFError), (404, httpx.HTTPStatusError),
                                           ("timeout", httpx.ReadTimeout)])
async def test_nonretryable_resource_failure_dominates_earlier_transient(provider, monkeypatch, second, expected):
    outcomes, calls, _ = provider
    target = "http://169.254.169.254/latest/meta-data" if second == "policy" else "second.ts"

    async def serve(request):
        if request.url.path.endswith("live.m3u8"):
            return httpx.Response(200, content=f"#EXTM3U\nfirst.ts\n{target}\n".encode(), request=request)
        if request.url.path.endswith("first.ts"):
            return httpx.Response(503, request=request)
        if second == "timeout":
            raise httpx.ReadTimeout(URL)
        return httpx.Response(second, request=request)

    outcomes.extend([serve] * 10)

    async def consume(*cmd, **kwargs):
        child = process(0)

        async def communicate():
            async with ClientSession() as client:
                async with client.get(cmd[-1]) as manifest:
                    text = await manifest.text()
                for resource in text.splitlines()[1:]:
                    async with client.get(resource) as response:
                        assert "synthetic-secret" not in await response.text()
            return json.dumps(METADATA).encode(), b""

        child.communicate.side_effect = communicate
        return child

    monkeypatch.setattr(probes.asyncio, "create_subprocess_exec", consume)
    prober = probes.StreamProber(MagicMock(), probe_retry_count=1, probe_retry_delay=0)
    with pytest.raises(expected):
        await prober._run_ffprobe(HLS_URL, user_agent="UA")
    assert len([call for call in calls if call.url.path.endswith("live.m3u8")]) == 1


@pytest.mark.asyncio
async def test_handler_cleanup_failure_settles_before_success_classification(provider, monkeypatch):
    closed = asyncio.Event()

    class CloseFailure(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"#EXTM3U\n"

        async def aclose(self):
            # Force task completion to occur AFTER its response body is ready.
            await asyncio.sleep(0.01)
            closed.set()
            raise httpx.ReadTimeout(URL)

    async def serve(request):
        return httpx.Response(200, stream=CloseFailure(), request=request)

    provider[0].append(serve)
    install_relay_decoder(monkeypatch, returncode=0, body=True)
    prober = probes.StreamProber(MagicMock(), probe_retry_count=1, probe_retry_delay=0)
    with pytest.raises(httpx.ReadTimeout):
        await prober._run_ffprobe(HLS_URL, user_agent="UA")
    assert closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["startup", "initial-body", "later-resource"])
@pytest.mark.parametrize("primary", ["policy", 401, 404])
@pytest.mark.parametrize("cleanup", [httpx.ReadError, httpx.ReadTimeout])
@pytest.mark.parametrize("black", [False, True])
async def test_primary_refusal_survives_cleanup_failure(
    provider, monkeypatch, phase, primary, cleanup, black
):
    """A secondary close error cannot turn a policy/HTTP refusal into a retry."""
    outcomes, calls, _ = provider

    class RefusedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise SSRFError("Denied")
            yield

        async def aclose(self):
            raise cleanup(URL)

    class CleanupFailure(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"refused"

        async def aclose(self):
            raise cleanup(URL)

    async def serve(request):
        if phase == "later-resource" and request.url.path.endswith("live.m3u8"):
            return httpx.Response(200, content=b"#EXTM3U\nsegment.ts\n", request=request)
        if primary == "policy":
            # In the startup case, fail transport validation while its client
            # close fails too. Body/resource cases fail while reading a response.
            if phase == "startup":
                raise SSRFError("Denied")
            return httpx.Response(200, stream=RefusedBody(), request=request)
        return httpx.Response(primary, stream=CleanupFailure(), request=request)

    outcomes.extend([serve] * 10)
    if phase == "startup" and primary == "policy":
        transport_class = type(outbound.SSRFPinnedTransport())
        real_close = transport_class.aclose

        async def bad_close(transport):
            await real_close(transport)
            raise cleanup(URL)

        monkeypatch.setattr(transport_class, "aclose", bad_close)
    spawns = install_relay_decoder(
        monkeypatch, black=black, returncode=0, body=phase == "initial-body",
        resource_body_failure=phase == "later-resource" and primary == "policy",
    )
    prober = probes.StreamProber(MagicMock(), probe_retry_count=1, probe_retry_delay=0)
    method = prober._detect_black_screen if black else prober._run_ffprobe
    if black and primary != "policy":
        assert await method(HLS_URL, user_agent="UA") is None
    else:
        expected = SSRFError if primary == "policy" else httpx.HTTPStatusError
        with pytest.raises(expected):
            await method(HLS_URL, user_agent="UA")
    assert len([call for call in calls if call.url.path.endswith("live.m3u8")]) == 1
    assert len(spawns) == (0 if phase == "startup" or primary != "policy" and phase != "later-resource" else 1)
