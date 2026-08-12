from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stream_prober import (
    PROBE_NETWORK_ROUTE_GUIDANCE,
    ProbeNetworkRouteError,
    StreamProber,
)


SYNTHETIC_URL = "http://provider.invalid/account/token/stream.ts"


@pytest.mark.asyncio
async def test_ffprobe_diagnostic_redacts_provider_url():
    prober = StreamProber(MagicMock(), probe_retry_count=0)
    process = MagicMock(returncode=1)
    process.communicate = AsyncMock(
        return_value=(b"", f"Connection to {SYNTHETIC_URL} timed out".encode())
    )

    with patch(
        "stream_prober.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
    ):
        with pytest.raises(ProbeNetworkRouteError) as exc_info:
            await prober._run_ffprobe(SYNTHETIC_URL)

    diagnostic = str(exc_info.value)
    assert SYNTHETIC_URL not in diagnostic
    assert diagnostic == "Provider connection failed"


@pytest.mark.asyncio
async def test_ffprobe_non_network_diagnostic_is_replaced_not_partially_scrubbed():
    prober = StreamProber(MagicMock(), probe_retry_count=0)
    process = MagicMock(returncode=1)
    process.communicate = AsyncMock(
        return_value=(b"", f"Invalid data at redirect {SYNTHETIC_URL}".encode())
    )

    with patch(
        "stream_prober.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await prober._run_ffprobe(SYNTHETIC_URL)

    assert str(exc_info.value) == "ffprobe failed: [REDACTED diagnostic]"
    assert SYNTHETIC_URL not in str(exc_info.value)


@pytest.mark.asyncio
async def test_network_marker_inside_provider_url_does_not_classify_failure():
    url = "http://provider.invalid/account/connection to/token/stream.ts"
    prober = StreamProber(MagicMock(), probe_retry_count=0)
    process = MagicMock(returncode=1)
    process.communicate = AsyncMock(
        return_value=(b"", f"Invalid data at {url}".encode())
    )

    with patch(
        "stream_prober.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await prober._run_ffprobe(url)

    assert type(exc_info.value) is RuntimeError
    assert str(exc_info.value) == "ffprobe failed: [REDACTED diagnostic]"
    assert url not in str(exc_info.value)


@pytest.mark.asyncio
async def test_network_marker_after_long_provider_url_is_not_lost():
    url = f"http://provider.invalid/{'a' * 600}/stream.ts"
    prober = StreamProber(MagicMock(), probe_retry_count=0)
    process = MagicMock(returncode=1)
    process.communicate = AsyncMock(
        return_value=(b"", f"Request for {url} failed: connection refused".encode())
    )

    with patch(
        "stream_prober.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
    ):
        with pytest.raises(ProbeNetworkRouteError) as exc_info:
            await prober._run_ffprobe(url)

    assert str(exc_info.value) == "Provider connection failed"
    assert url not in str(exc_info.value)


@pytest.mark.asyncio
async def test_direct_probe_network_failure_returns_route_guidance_without_url(caplog):
    prober = StreamProber(MagicMock())
    prober._run_ffprobe = AsyncMock(
        side_effect=ProbeNetworkRouteError("Provider connection failed")
    )
    prober._save_probe_result = MagicMock(side_effect=lambda *args: {
        "probe_status": args[3], "error_message": args[4]
    })

    result = await prober.probe_stream(42, SYNTHETIC_URL, "Synthetic")

    assert result["probe_status"] == "failed"
    assert result["error_message"] == PROBE_NETWORK_ROUTE_GUIDANCE
    assert SYNTHETIC_URL not in result["error_message"]
    assert SYNTHETIC_URL not in caplog.text


@pytest.mark.asyncio
async def test_direct_probe_timeout_returns_same_route_guidance():
    prober = StreamProber(MagicMock())
    prober._run_ffprobe = AsyncMock(side_effect=TimeoutError())
    prober._save_probe_result = MagicMock(side_effect=lambda *args: {
        "probe_status": args[3], "error_message": args[4]
    })

    result = await prober.probe_stream(43, SYNTHETIC_URL, "Synthetic")

    assert result == {
        "probe_status": "timeout",
        "error_message": PROBE_NETWORK_ROUTE_GUIDANCE,
    }


@pytest.mark.asyncio
async def test_non_network_probe_failure_keeps_generic_error():
    prober = StreamProber(MagicMock())
    prober._run_ffprobe = AsyncMock(side_effect=RuntimeError("invalid media data"))
    prober._save_probe_result = MagicMock(side_effect=lambda *args: {
        "probe_status": args[3], "error_message": args[4]
    })

    result = await prober.probe_stream(44, SYNTHETIC_URL, "Synthetic")

    assert result["error_message"] == "Probe failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "diagnostic",
    [
        f"client failed for {SYNTHETIC_URL}",
        "redirect failed at http://redirect.invalid/private-credential/stream.ts",
    ],
)
async def test_bitrate_client_exceptions_log_only_safe_category(caplog, diagnostic):
    prober = StreamProber(MagicMock())

    with patch(
        "stream_prober.httpx.AsyncClient",
        side_effect=RuntimeError(diagnostic),
    ):
        assert await prober._measure_stream_bitrate(SYNTHETIC_URL) is None

    assert diagnostic not in caplog.text
    assert SYNTHETIC_URL not in caplog.text
    assert "private-credential" not in caplog.text
    assert "RuntimeError" in caplog.text
