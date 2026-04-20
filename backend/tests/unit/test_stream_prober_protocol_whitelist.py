"""Regression tests for the ffmpeg/ffprobe protocol whitelist in stream_prober.

Background: ppe28.1 added -protocol_whitelist to ffmpeg_builder/probe.py and
routers/stream_preview.py but missed the scheduled probing path in
backend/stream_prober.py (_run_ffprobe and _detect_black_screen). Bead v3xfl
closed that gap. These tests exist so that any future refactor of those cmd
arrays that drops the whitelist fails loudly in CI — the string-level assertion
is intentional. Do not "soften" these assertions without re-doing the threat
model: the whitelist is the control that stops file://, concat:, subfile:,
data:, etc. from being dereferenced against the ECM host.
"""
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from stream_prober import FFPROBE_PROTOCOL_WHITELIST, StreamProber


EXPECTED_WHITELIST = "http,https,tcp,udp,rtp,rtmp,pipe"


def _make_prober(**kwargs) -> StreamProber:
    """Build a StreamProber with test-friendly defaults."""
    mock_client = MagicMock()
    defaults = {
        "probe_timeout": 1,
        "black_screen_detection_enabled": True,
        "black_screen_sample_duration": 1,
    }
    defaults.update(kwargs)
    return StreamProber(client=mock_client, **defaults)


def _make_mock_process(stdout: bytes = b"{}", stderr: bytes = b"", returncode: int = 0):
    """Build an awaitable mock subprocess for asyncio.create_subprocess_exec."""
    mock_process = AsyncMock()

    async def mock_communicate():
        return (stdout, stderr)

    mock_process.communicate = mock_communicate
    mock_process.kill = Mock()
    mock_process.wait = AsyncMock()
    mock_process.returncode = returncode
    return mock_process


def test_protocol_whitelist_constant_matches_canonical_value():
    """The constant must match the value used in probe.py and stream_preview.py.

    Consistency is the point — divergent whitelists across sites create confusion
    and increase the odds someone "fixes" one and forgets the others.
    """
    assert FFPROBE_PROTOCOL_WHITELIST == EXPECTED_WHITELIST


@pytest.mark.asyncio
async def test_run_ffprobe_includes_protocol_whitelist():
    """_run_ffprobe must pass -protocol_whitelist to ffprobe.

    Without this flag an attacker who can write a stream URL into Dispatcharr
    can coerce the scheduled prober into dereferencing file://, concat:,
    subfile:, data:, etc. on the ECM host.
    """
    prober = _make_prober()
    captured_cmd = []

    async def fake_create_subprocess_exec(*args, **_kwargs):
        captured_cmd.extend(args)
        return _make_mock_process(stdout=b'{"streams": [], "format": {}}')

    with patch(
        "stream_prober.asyncio.create_subprocess_exec",
        side_effect=fake_create_subprocess_exec,
    ):
        await prober._run_ffprobe("http://example.test/stream.ts")

    assert captured_cmd[0] == "ffprobe"
    assert "-protocol_whitelist" in captured_cmd, (
        f"-protocol_whitelist missing from ffprobe cmd: {captured_cmd}"
    )
    idx = captured_cmd.index("-protocol_whitelist")
    assert captured_cmd[idx + 1] == EXPECTED_WHITELIST


@pytest.mark.asyncio
async def test_detect_black_screen_includes_protocol_whitelist():
    """_detect_black_screen must pass -protocol_whitelist to ffmpeg.

    Same threat model as _run_ffprobe — the black-screen scan runs ffmpeg
    against URLs from Dispatcharr and must not dereference unsafe protocols.
    """
    prober = _make_prober()
    captured_cmd = []

    async def fake_create_subprocess_exec(*args, **_kwargs):
        captured_cmd.extend(args)
        return _make_mock_process(stderr=b"lavfi.signalstats.YAVG=50.0\n")

    with patch(
        "stream_prober.asyncio.create_subprocess_exec",
        side_effect=fake_create_subprocess_exec,
    ):
        await prober._detect_black_screen("http://example.test/stream.ts")

    assert captured_cmd[0] == "ffmpeg"
    assert "-protocol_whitelist" in captured_cmd, (
        f"-protocol_whitelist missing from ffmpeg cmd: {captured_cmd}"
    )
    idx = captured_cmd.index("-protocol_whitelist")
    assert captured_cmd[idx + 1] == EXPECTED_WHITELIST
