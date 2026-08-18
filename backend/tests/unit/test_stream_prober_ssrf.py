"""SSRF prevalidation for subprocess-backed stream probing (04c0u.6)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from security.ssrf import SSRFError, SSRFMode
from security.stream_outbound import validate_stream_subprocess_url
from stream_prober import StreamProber


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["_run_ffprobe", "_detect_black_screen"])
async def test_subprocess_probe_rejects_denied_destination_before_spawn(method):
    prober = StreamProber(MagicMock())
    spawn = AsyncMock()

    with patch("stream_prober.validate_stream_subprocess_url", side_effect=SSRFError("denied")), \
         patch("stream_prober.asyncio.create_subprocess_exec", spawn):
        with pytest.raises(SSRFError):
            await getattr(prober, method)("http://169.254.169.254/latest")

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_ffprobe_validates_immediately_before_every_spawn_and_retry():
    prober = StreamProber(MagicMock(), probe_retry_count=1, probe_retry_delay=0)
    failed = MagicMock(returncode=1)
    failed.communicate = AsyncMock(return_value=(b"", b"HTTP 503"))
    passed = MagicMock(returncode=0)
    passed.communicate = AsyncMock(return_value=(b'{"streams": []}', b""))

    with patch("stream_prober.validate_stream_subprocess_url") as validate, \
         patch(
             "stream_prober.asyncio.create_subprocess_exec",
             AsyncMock(side_effect=[failed, passed]),
         ):
        await prober._run_ffprobe("http://provider.example/live.ts")

    assert validate.call_count == 2


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
