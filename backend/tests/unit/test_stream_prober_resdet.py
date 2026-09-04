"""Opt-in resdet resolution detection for stream probing (6cyl3 / GH #618)."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import DispatcharrSettings
from stream_prober import ResolutionDetectionError, StreamProber


FFPROBE_RESULT = {
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "avg_frame_rate": "25/1",
        },
        {"codec_type": "audio", "codec_name": "aac", "channels": 2},
    ],
    "format": {"format_name": "mpegts"},
}


def test_resdet_resolution_detection_defaults_off():
    assert DispatcharrSettings().use_resdet_for_resolution is False
    assert StreamProber(MagicMock()).use_resdet_for_resolution is False


@pytest.mark.asyncio
async def test_default_probe_uses_ffprobe_resolution_without_running_resdet():
    prober = StreamProber(MagicMock())
    prober._run_ffprobe = AsyncMock(return_value=deepcopy(FFPROBE_RESULT))
    prober._run_resdet = AsyncMock()
    prober._measure_stream_bitrate = AsyncMock(return_value=None)
    prober._push_stats_to_dispatcharr = AsyncMock()
    prober._save_probe_result = MagicMock(return_value={"probe_status": "success"})

    await prober.probe_stream(7, "https://provider.example/live.ts", "Test")

    prober._run_resdet.assert_not_awaited()
    saved_ffprobe_data = prober._save_probe_result.call_args.args[2]
    assert saved_ffprobe_data["streams"][0]["width"] == 1920
    assert saved_ffprobe_data["streams"][0]["height"] == 1080


@pytest.mark.asyncio
async def test_enabled_probe_replaces_only_ffprobe_video_dimensions():
    prober = StreamProber(MagicMock(), use_resdet_for_resolution=True)
    prober._run_ffprobe = AsyncMock(return_value=deepcopy(FFPROBE_RESULT))
    prober._run_resdet = AsyncMock(return_value=(1280, 720))
    prober._measure_stream_bitrate = AsyncMock(return_value=None)
    prober._push_stats_to_dispatcharr = AsyncMock()
    prober._save_probe_result = MagicMock(return_value={"probe_status": "success"})

    await prober.probe_stream(7, "https://provider.example/live.ts", "Test")

    prober._run_resdet.assert_awaited_once_with("https://provider.example/live.ts")
    saved_ffprobe_data = prober._save_probe_result.call_args.args[2]
    video = saved_ffprobe_data["streams"][0]
    assert (video["width"], video["height"]) == (1280, 720)
    assert video["codec_name"] == "h264"
    assert saved_ffprobe_data["streams"][1] == FFPROBE_RESULT["streams"][1]
    assert saved_ffprobe_data["format"] == FFPROBE_RESULT["format"]


@pytest.mark.asyncio
async def test_enabled_resdet_failure_fails_probe_without_ffprobe_fallback():
    prober = StreamProber(MagicMock(), use_resdet_for_resolution=True)
    prober._run_ffprobe = AsyncMock(return_value=deepcopy(FFPROBE_RESULT))
    prober._run_resdet = AsyncMock(
        side_effect=ResolutionDetectionError("resdet resolution detection failed")
    )
    prober._measure_stream_bitrate = AsyncMock()
    prober._save_probe_result = MagicMock(
        side_effect=lambda *args, **_kwargs: {
            "probe_status": args[3],
            "error_message": args[4],
        }
    )

    result = await prober.probe_stream(
        7, "https://user:password@provider.example/live.ts", "Test"
    )

    assert result == {
        "probe_status": "failed",
        "error_message": "resdet resolution detection failed",
    }
    prober._measure_stream_bitrate.assert_not_awaited()
    assert prober._save_probe_result.call_args.args[2] is None


@pytest.mark.asyncio
async def test_resdet_invocation_parses_best_guess_dimensions():
    @asynccontextmanager
    async def allowed(_url, **_kwargs):
        yield SimpleNamespace(
            argument="http://127.0.0.1:1234/resource/0",
            response=None,
            is_http_relay=True,
        )

    process = MagicMock(returncode=0)
    process.communicate = AsyncMock(return_value=(b"1280 720\n", b""))
    spawn = AsyncMock(return_value=process)
    prober = StreamProber(MagicMock(), use_resdet_for_resolution=True, probe_timeout=20)

    with patch("stream_prober.validated_subprocess_input", allowed), patch(
        "stream_prober.asyncio.create_subprocess_exec", spawn
    ):
        resolution = await prober._run_resdet("https://provider.example/live.ts")

    assert resolution == (1280, 720)
    assert spawn.await_args.args == (
        "resdet",
        "-v",
        "1",
        "-n",
        "1",
        "http://127.0.0.1:1234/resource/0",
    )
    assert "https://provider.example/live.ts" not in spawn.await_args.args


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [(1, b""), (0, b""), (0, b"1920x1080\n"), (0, b"0 1080\n")],
)
async def test_resdet_rejects_nonzero_and_malformed_results(returncode, stdout):
    @asynccontextmanager
    async def allowed(url, **_kwargs):
        yield SimpleNamespace(argument=url, response=None, is_http_relay=False)

    process = MagicMock(returncode=returncode)
    process.communicate = AsyncMock(return_value=(stdout, b"private provider output"))
    prober = StreamProber(MagicMock(), use_resdet_for_resolution=True)

    with patch("stream_prober.validated_subprocess_input", allowed), patch(
        "stream_prober.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
    ):
        with pytest.raises(ResolutionDetectionError, match="resdet") as exc_info:
            await prober._run_resdet("https://user:password@provider.example/live.ts")

    assert "password" not in str(exc_info.value)
    assert "private provider output" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_resdet_timeout_kills_and_reaps_process():
    @asynccontextmanager
    async def allowed(url, **_kwargs):
        yield SimpleNamespace(argument=url, response=None, is_http_relay=False)

    process = MagicMock(returncode=None)
    process.communicate = AsyncMock(side_effect=TimeoutError)
    process.wait = AsyncMock()
    prober = StreamProber(MagicMock(), use_resdet_for_resolution=True)

    with patch("stream_prober.validated_subprocess_input", allowed), patch(
        "stream_prober.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
    ):
        with pytest.raises(ResolutionDetectionError, match="timed out"):
            await prober._run_resdet("https://provider.example/live.ts")

    process.kill.assert_called_once_with()
    process.wait.assert_awaited_once_with()
