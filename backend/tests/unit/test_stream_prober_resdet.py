"""Opt-in resdet resolution detection for stream probing (6cyl3 / GH #618)."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stream_prober as stream_prober_module

from config import DispatcharrSettings
from stream_prober import (
    RELAY_PROTOCOL_WHITELIST,
    RESDET_FRAME_MAX_BYTES,
    RESDET_MAX_HEIGHT,
    RESDET_MAX_PIXELS,
    RESDET_MAX_WIDTH,
    RESDET_OUTPUT_MAX_BYTES,
    ResolutionDetectionError,
    StreamProber,
)


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


@pytest.fixture(autouse=True)
def isolated_resdet_lock(tmp_path: Path, monkeypatch):
    production_lock = stream_prober_module.ResdetPipelineLock

    def lock_factory(path):
        if path == stream_prober_module.RESDET_PIPELINE_LOCK_PATH:
            path = tmp_path / "resdet.pipeline.lock"
        return production_lock(path, poll_interval=0.001)

    monkeypatch.setattr(stream_prober_module, "ResdetPipelineLock", lock_factory)


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


def _stdout(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


def _process(*, returncode=0, stdout=b""):
    process = MagicMock(returncode=returncode)
    process.pid = 4321
    process.stdout = _stdout(stdout)
    process.wait = AsyncMock(return_value=returncode)
    return process


@asynccontextmanager
async def _allowed_relay(_url, **_kwargs):
    yield SimpleNamespace(
        argument="http://127.0.0.1:1234/resource/0",
        response=None,
        is_http_relay=True,
    )


def _spawn_pipeline(*, frame=b"YUV4MPEG2 W2 H2 F25:1 Ip A1:1 C420\nFRAME\n" + b"\0" * 6,
                    output=b"1280 720\n", resdet_returncode=0):
    ffmpeg = _process(stdout=frame)
    resdet = _process(returncode=resdet_returncode, stdout=output)
    calls = []

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        if "ffmpeg" in args:
            return ffmpeg
        return resdet

    return AsyncMock(side_effect=spawn), ffmpeg, resdet, calls


def _y4m_frame(width: int, height: int) -> bytes:
    pixels = width * height
    chroma = ((width + 1) // 2) * ((height + 1) // 2)
    return (
        f"YUV4MPEG2 W{width} H{height} F25:1 Ip A1:1 C420\nFRAME\n".encode()
        + b"\0" * (pixels + 2 * chroma)
    )


@pytest.mark.asyncio
async def test_resdet_decodes_one_hardened_local_y4m_frame_before_analysis():
    provider_url = "https://user:password@provider.example/live.ts"
    spawn, ffmpeg, resdet, calls = _spawn_pipeline()
    prober = StreamProber(MagicMock(), use_resdet_for_resolution=True, probe_timeout=20)

    with patch("stream_prober.validated_subprocess_input", _allowed_relay), patch(
        "stream_prober.asyncio.create_subprocess_exec", spawn
    ):
        resolution = await prober._run_resdet(provider_url)

    assert resolution == (1280, 720)
    assert len(calls) == 2
    ffmpeg_args, ffmpeg_kwargs = calls[0]
    resdet_args, resdet_kwargs = calls[1]
    assert ffmpeg_args[:9] == (
        "/usr/bin/timeout",
        "--signal=KILL",
        "25s",
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-protocol_whitelist",
        RELAY_PROTOCOL_WHITELIST,
    )
    assert "-frames:v" in ffmpeg_args
    assert ffmpeg_args[ffmpeg_args.index("-frames:v") + 1] == "1"
    assert ffmpeg_args[ffmpeg_args.index("-fs") + 1] == str(RESDET_FRAME_MAX_BYTES)
    assert "http://127.0.0.1:1234/resource/0" in ffmpeg_args
    assert ffmpeg_kwargs["stdout"] == asyncio.subprocess.PIPE
    assert ffmpeg_kwargs["stderr"] == asyncio.subprocess.DEVNULL
    assert ffmpeg_kwargs["start_new_session"] is True
    assert len(ffmpeg_kwargs["pass_fds"]) == 1
    assert resdet_args[:10] == (
        "/usr/bin/timeout", "--signal=KILL", "25s", "resdet", "-R", "Y4M", "-v", "1", "-n", "1"
    )
    assert resdet_args[-1].endswith(".y4m")
    assert provider_url not in resdet_args
    assert "http://127.0.0.1:1234/resource/0" not in resdet_args
    assert resdet_kwargs == {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.DEVNULL,
        "start_new_session": True,
        "pass_fds": ffmpeg_kwargs["pass_fds"],
    }
    ffmpeg.wait.assert_awaited_once_with()
    resdet.wait.assert_awaited_once_with()
    assert not Path(resdet_args[-1]).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        (1, b""),
        (0, b""),
        (0, b"1920x1080\n"),
        (0, b"0 1080\n"),
        (0, b"1920 1080 extra\n"),
        (0, b"1" * (RESDET_OUTPUT_MAX_BYTES + 1)),
    ],
)
async def test_resdet_rejects_nonzero_and_malformed_results(returncode, stdout):
    spawn, _ffmpeg, _resdet, _calls = _spawn_pipeline(
        output=stdout, resdet_returncode=returncode
    )
    prober = StreamProber(MagicMock(), use_resdet_for_resolution=True)

    with patch("stream_prober.validated_subprocess_input", _allowed_relay), patch(
        "stream_prober.asyncio.create_subprocess_exec", spawn
    ):
        with pytest.raises(ResolutionDetectionError, match="resdet") as exc_info:
            await prober._run_resdet("https://user:password@provider.example/live.ts")

    assert "password" not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frame",
    [b"not y4m", b"YUV4MPEG2 " + b"0" * 33],
    ids=["malformed", "oversized"],
)
async def test_resdet_rejects_malformed_or_oversized_frame_before_native_analysis(frame):
    spawn, _ffmpeg, _resdet, calls = _spawn_pipeline(frame=frame)
    prober = StreamProber(MagicMock(), use_resdet_for_resolution=True)
    limit = 32 if frame.startswith(b"YUV4MPEG2 ") else RESDET_FRAME_MAX_BYTES

    with patch("stream_prober.RESDET_FRAME_MAX_BYTES", limit), patch(
        "stream_prober.validated_subprocess_input", _allowed_relay
    ), patch("stream_prober.asyncio.create_subprocess_exec", spawn):
        with pytest.raises(ResolutionDetectionError, match="frame"):
            await prober._run_resdet("https://provider.example/live.ts")

    assert ["ffmpeg" if "ffmpeg" in args else "resdet" for args, _kwargs in calls] == ["ffmpeg"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("width", "height"),
    [(3840, 2160), (4096, 2160)],
    ids=["uhd-4k", "dci-4k"],
)
async def test_resdet_accepts_common_dimensions_through_the_dci_4k_ceiling(width, height):
    spawn, _ffmpeg, _resdet, calls = _spawn_pipeline(frame=_y4m_frame(width, height))
    prober = StreamProber(MagicMock(), use_resdet_for_resolution=True)

    with patch("stream_prober.validated_subprocess_input", _allowed_relay), patch(
        "stream_prober.asyncio.create_subprocess_exec", spawn
    ):
        assert await prober._run_resdet("https://provider.example/live.ts") == (1280, 720)

    assert ["ffmpeg" if "ffmpeg" in args else "resdet" for args, _kwargs in calls] == ["ffmpeg", "resdet"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        b"YUV4MPEG2 W4097 H1 F25:1 Ip A1:1 C420\nFRAME\n",
        b"YUV4MPEG2 W1 H2161 F25:1 Ip A1:1 C420\nFRAME\n",
        b"YUV4MPEG2 W4096 H2160 W2 F25:1 Ip A1:1 C420\nFRAME\n",
        b"YUV4MPEG2 W4096 Hx F25:1 Ip A1:1 C420\nFRAME\n",
        b"YUV4MPEG2 W999999999999999999999 H1 F25:1 Ip A1:1 C420\nFRAME\n",
    ],
    ids=["width", "height", "duplicate", "malformed", "overflow"],
)
async def test_resdet_rejects_crafted_small_artifacts_with_unsafe_dimensions_before_write_or_native(header):
    assert len(header) < 1024
    spawn, _ffmpeg, _resdet, calls = _spawn_pipeline(frame=header)
    prober = StreamProber(MagicMock(), use_resdet_for_resolution=True)

    with patch("stream_prober.validated_subprocess_input", _allowed_relay), patch(
        "stream_prober.asyncio.create_subprocess_exec", spawn
    ), patch("pathlib.Path.write_bytes") as write_bytes:
        with pytest.raises(ResolutionDetectionError, match="frame"):
            await prober._run_resdet("https://provider.example/live.ts")

    write_bytes.assert_not_called()
    assert ["ffmpeg" if "ffmpeg" in args else "resdet" for args, _kwargs in calls] == ["ffmpeg"]


@pytest.mark.asyncio
async def test_resdet_rejects_a_truncated_bounded_frame_before_native_analysis():
    frame = _y4m_frame(1920, 1080)[:-1]
    spawn, _ffmpeg, _resdet, calls = _spawn_pipeline(frame=frame)
    prober = StreamProber(MagicMock(), use_resdet_for_resolution=True)

    with patch("stream_prober.validated_subprocess_input", _allowed_relay), patch(
        "stream_prober.asyncio.create_subprocess_exec", spawn
    ):
        with pytest.raises(ResolutionDetectionError, match="frame"):
            await prober._run_resdet("https://provider.example/live.ts")

    assert ["ffmpeg" if "ffmpeg" in args else "resdet" for args, _kwargs in calls] == ["ffmpeg"]


def test_resdet_dimension_contract_matches_the_compiled_product_ceiling():
    assert (RESDET_MAX_WIDTH, RESDET_MAX_HEIGHT) == (4096, 2160)
    assert RESDET_MAX_PIXELS == 8_847_360


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["ffmpeg", "resdet"])
async def test_resdet_timeout_kills_reaps_then_releases_lock(stage, tmp_path):
    spawn, ffmpeg, resdet, _calls = _spawn_pipeline()
    active = ffmpeg if stage == "ffmpeg" else resdet
    active.returncode = None
    active.wait = AsyncMock(side_effect=[asyncio.TimeoutError, None])
    lock_path = tmp_path / "resdet.pipeline.lock"
    prober = StreamProber(MagicMock(), use_resdet_for_resolution=True, _resdet_lock_path=lock_path)

    with patch("stream_prober.validated_subprocess_input", _allowed_relay), patch(
        "stream_prober.asyncio.create_subprocess_exec", spawn
    ), patch("stream_prober.os.killpg") as killpg:
        with pytest.raises(ResolutionDetectionError, match="timed out"):
            await prober._run_resdet("https://provider.example/live.ts")

    killpg.assert_called_once_with(active.pid, 9)
    active.kill.assert_not_called()
    assert active.wait.await_count == 2
    async with stream_prober_module.ResdetPipelineLock(lock_path):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["ffmpeg", "resdet"])
async def test_resdet_cancellation_kills_reaps_then_releases_lock(stage, tmp_path):
    spawn, ffmpeg, resdet, calls = _spawn_pipeline()
    active = ffmpeg if stage == "ffmpeg" else resdet
    active.returncode = None
    active.wait = AsyncMock(side_effect=[asyncio.CancelledError, None])
    lock_path = tmp_path / "resdet.pipeline.lock"
    prober = StreamProber(MagicMock(), use_resdet_for_resolution=True, _resdet_lock_path=lock_path)

    with patch("stream_prober.validated_subprocess_input", _allowed_relay), patch(
        "stream_prober.asyncio.create_subprocess_exec", spawn
    ), patch("stream_prober.os.killpg") as killpg:
        with pytest.raises(asyncio.CancelledError):
            await prober._run_resdet("https://provider.example/live.ts")

    killpg.assert_called_once_with(active.pid, 9)
    active.kill.assert_not_called()
    assert active.wait.await_count == 2
    if stage == "resdet":
        ffmpeg.wait.assert_awaited_once_with()
        ffmpeg.kill.assert_not_called()
    frame_paths = [Path(args[-1]) for args, _kwargs in calls if "resdet" in args]
    assert all(not path.exists() for path in frame_paths)
    async with stream_prober_module.ResdetPipelineLock(lock_path):
        pass


@pytest.mark.asyncio
async def test_resdet_pipelines_are_serialized_across_stream_probers(tmp_path):
    lock_path = tmp_path / "resdet.pipeline.lock"
    probers = [
        StreamProber(MagicMock(), use_resdet_for_resolution=True, _resdet_lock_path=lock_path)
        for _ in range(2)
    ]
    active = 0
    maximum = 0

    async def pipeline(_url, _lock_fd):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1
        return (1920, 1080)

    with patch.object(probers[0], "_run_resdet_pipeline", pipeline), patch.object(
        probers[1], "_run_resdet_pipeline", pipeline
    ):
        assert await asyncio.gather(
            probers[0]._run_resdet("https://provider.example/one"),
            probers[1]._run_resdet("https://provider.example/two"),
            probers[0]._run_resdet("https://provider.example/three"),
        ) == [(1920, 1080)] * 3

    assert maximum == 1


@pytest.mark.asyncio
async def test_cancelling_a_resdet_lock_waiter_does_not_disturb_the_owner(tmp_path):
    lock_path = tmp_path / "resdet.pipeline.lock"
    owner_prober = StreamProber(MagicMock(), use_resdet_for_resolution=True, _resdet_lock_path=lock_path)
    replacement = StreamProber(MagicMock(), use_resdet_for_resolution=True, _resdet_lock_path=lock_path)
    owner_entered = asyncio.Event()
    release_owner = asyncio.Event()
    calls = 0

    async def pipeline(_url, _lock_fd):
        nonlocal calls
        calls += 1
        owner_entered.set()
        await release_owner.wait()
        return (1920, 1080)

    with patch.object(owner_prober, "_run_resdet_pipeline", pipeline), patch.object(
        replacement, "_run_resdet_pipeline", pipeline
    ):
        owner = asyncio.create_task(owner_prober._run_resdet("https://provider.example/owner"))
        await owner_entered.wait()
        waiter = asyncio.create_task(replacement._run_resdet("https://provider.example/waiter"))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert calls == 1
        assert not owner.done()
        release_owner.set()
        assert await owner == (1920, 1080)


@pytest.mark.asyncio
async def test_cancelling_a_stale_resdet_owner_releases_the_replacement(tmp_path):
    lock_path = tmp_path / "resdet.pipeline.lock"
    stale = StreamProber(MagicMock(), use_resdet_for_resolution=True, _resdet_lock_path=lock_path)
    replacement = StreamProber(MagicMock(), use_resdet_for_resolution=True, _resdet_lock_path=lock_path)
    owner_entered = asyncio.Event()
    calls = 0

    async def pipeline(_url, _lock_fd):
        nonlocal calls
        calls += 1
        if calls == 1:
            owner_entered.set()
            await asyncio.Event().wait()
        return (1280, 720)

    with patch.object(stale, "_run_resdet_pipeline", pipeline), patch.object(
        replacement, "_run_resdet_pipeline", pipeline
    ):
        owner = asyncio.create_task(stale._run_resdet("https://provider.example/owner"))
        await owner_entered.wait()
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert await replacement._run_resdet("https://provider.example/next") == (1280, 720)
