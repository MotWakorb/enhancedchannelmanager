"""
Unit tests for the FFMPEG Builder probe module.

Tests ffprobe/ffmpeg subprocess integration, output parsing, capabilities
detection, and caching.
These are TDD tests -- they will FAIL until the backend modules are implemented.
"""
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from ffmpeg_builder.probe import (
    probe_source,
    parse_probe_output,
    detect_capabilities,
    ProbeResult,
)


# ---------------------------------------------------------------------------
# Sample ffprobe JSON output
# ---------------------------------------------------------------------------

SAMPLE_PROBE_JSON = {
    "format": {
        "filename": "/media/input.mp4",
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "format_long_name": "QuickTime / MOV",
        "duration": "120.500000",
        "size": "15000000",
        "bit_rate": "996000",
    },
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "codec_long_name": "H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10",
            "width": 1920,
            "height": 1080,
            "r_frame_rate": "30/1",
            "avg_frame_rate": "30/1",
            "duration": "120.500000",
            "bit_rate": "900000",
            "pix_fmt": "yuv420p",
            "profile": "High",
            "level": 41,
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "codec_long_name": "AAC (Advanced Audio Coding)",
            "sample_rate": "48000",
            "channels": 2,
            "channel_layout": "stereo",
            "bit_rate": "96000",
            "duration": "120.500000",
        },
    ],
}


SAMPLE_ENCODERS_OUTPUT = """\
 V..... libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V..... libx265              libx265 H.265 / HEVC (codec hevc)
 V..... h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)
 V..... hevc_nvenc           NVIDIA NVENC hevc encoder (codec hevc)
 A..... aac                  AAC (Advanced Audio Coding) (codec aac)
 A..... libmp3lame           libmp3lame MP3 (MPEG audio layer 3) (codec mp3)
"""

SAMPLE_DECODERS_OUTPUT = """\
 V..... h264                 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
 V..... hevc                 H.265 / HEVC (High Efficiency Video Coding)
 A..... aac                  AAC (Advanced Audio Coding)
 A..... mp3                  MP3 (MPEG audio layer 3)
"""

SAMPLE_FORMATS_OUTPUT = """\
 DE mp4             MP4 (MPEG-4 Part 14)
 DE matroska        Matroska / WebM
  E webm            WebM
 DE mpegts          MPEG-TS (MPEG-2 Transport Stream)
"""

SAMPLE_FILTERS_OUTPUT = """\
 ... scale            V->V       Scale the input video size
 ... crop             V->V       Crop the input video
 ... fps              V->V       Force constant framerate
 ... volume           A->A       Change input volume.
 ... loudnorm         A->A       EBU R128 loudness normalization
"""

SAMPLE_VERSION_OUTPUT = "ffmpeg version 6.1.1 Copyright (c) 2000-2023 the FFmpeg developers"


class TestProbeSource:
    """Tests for probing an input source with ffprobe."""

    def test_probes_file_successfully(self):
        """probe_source returns a ProbeResult for a valid file."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(SAMPLE_PROBE_JSON)
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = probe_source("/media/input.mp4")

        assert isinstance(result, ProbeResult)
        assert result.success is True

    def test_probe_returns_format_info(self):
        """ProbeResult includes format name and duration."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(SAMPLE_PROBE_JSON)
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = probe_source("/media/input.mp4")

        assert "mp4" in result.format_name.lower() or "mov" in result.format_name.lower()
        assert result.duration == pytest.approx(120.5, abs=0.1)

    def test_probe_returns_streams(self):
        """ProbeResult includes video and audio stream info."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(SAMPLE_PROBE_JSON)
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = probe_source("/media/input.mp4")

        assert len(result.streams) == 2
        video = [s for s in result.streams if s["codec_type"] == "video"]
        audio = [s for s in result.streams if s["codec_type"] == "audio"]
        assert len(video) == 1
        assert len(audio) == 1

    def test_probe_returns_video_dimensions(self):
        """Video stream in ProbeResult has width and height."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(SAMPLE_PROBE_JSON)
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = probe_source("/media/input.mp4")

        video = [s for s in result.streams if s["codec_type"] == "video"][0]
        assert video["width"] == 1920
        assert video["height"] == 1080

    def test_probe_handles_timeout(self):
        """probe_source returns a failure result on subprocess timeout."""
        with patch("subprocess.run", side_effect=TimeoutError("timed out")):
            result = probe_source("/media/input.mp4", timeout=5)

        assert result.success is False
        assert "timeout" in result.error.lower()

    def test_probe_handles_missing_file(self):
        """probe_source returns a failure result when ffprobe fails."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "No such file or directory"

        with patch("subprocess.run", return_value=mock_proc):
            result = probe_source("/nonexistent/file.mp4")

        assert result.success is False
        assert result.error

    def test_probe_handles_url_input(self):
        """probe_source works with URL inputs."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(SAMPLE_PROBE_JSON)
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            result = probe_source("http://example.com/stream.m3u8")

        assert result.success is True
        # Verify the URL was passed to ffprobe
        call_args = mock_run.call_args[0][0]
        assert "http://example.com/stream.m3u8" in call_args

    def test_probe_returns_bitrate(self):
        """ProbeResult includes overall bitrate."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(SAMPLE_PROBE_JSON)
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = probe_source("/media/input.mp4")

        assert result.bit_rate is not None


class TestParseProbeOutput:
    """Tests for parsing raw ffprobe JSON output."""

    def test_parses_valid_json(self):
        """parse_probe_output handles valid ffprobe JSON."""
        result = parse_probe_output(json.dumps(SAMPLE_PROBE_JSON))

        assert result.success is True
        assert len(result.streams) == 2

    def test_handles_invalid_json(self):
        """parse_probe_output returns error for invalid JSON."""
        result = parse_probe_output("not valid json {{{")

        assert result.success is False
        assert "json" in result.error.lower() or "parse" in result.error.lower()

    def test_handles_empty_streams(self):
        """parse_probe_output handles probe output with no streams."""
        data = {"format": SAMPLE_PROBE_JSON["format"], "streams": []}
        result = parse_probe_output(json.dumps(data))

        assert result.success is True
        assert len(result.streams) == 0


class TestDetectCapabilities:
    """Tests for detecting system ffmpeg capabilities."""

    def test_detects_version(self):
        """detect_capabilities returns the ffmpeg version string."""
        def fake_run(cmd, *args, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            if "-version" in cmd:
                mock.stdout = SAMPLE_VERSION_OUTPUT
            elif "-encoders" in cmd:
                mock.stdout = SAMPLE_ENCODERS_OUTPUT
            elif "-decoders" in cmd:
                mock.stdout = SAMPLE_DECODERS_OUTPUT
            elif "-formats" in cmd:
                mock.stdout = SAMPLE_FORMATS_OUTPUT
            elif "-filters" in cmd:
                mock.stdout = SAMPLE_FILTERS_OUTPUT
            else:
                mock.stdout = ""
            mock.stderr = ""
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            caps = detect_capabilities()

        assert "6.1" in caps["version"]

    def test_detects_encoders(self):
        """detect_capabilities returns a list of available encoders."""
        def fake_run(cmd, *args, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            if "-encoders" in cmd:
                mock.stdout = SAMPLE_ENCODERS_OUTPUT
            elif "-version" in cmd:
                mock.stdout = SAMPLE_VERSION_OUTPUT
            else:
                mock.stdout = ""
            mock.stderr = ""
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            caps = detect_capabilities()

        assert "libx264" in caps["encoders"]
        assert "libx265" in caps["encoders"]
        assert "h264_nvenc" in caps["encoders"]

    def test_detects_decoders(self):
        """detect_capabilities returns a list of available decoders."""
        def fake_run(cmd, *args, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            if "-decoders" in cmd:
                mock.stdout = SAMPLE_DECODERS_OUTPUT
            elif "-version" in cmd:
                mock.stdout = SAMPLE_VERSION_OUTPUT
            else:
                mock.stdout = ""
            mock.stderr = ""
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            caps = detect_capabilities()

        assert "h264" in caps["decoders"]
        assert "aac" in caps["decoders"]

    def test_detects_formats(self):
        """detect_capabilities returns available container formats."""
        def fake_run(cmd, *args, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            if "-formats" in cmd:
                mock.stdout = SAMPLE_FORMATS_OUTPUT
            elif "-version" in cmd:
                mock.stdout = SAMPLE_VERSION_OUTPUT
            else:
                mock.stdout = ""
            mock.stderr = ""
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            caps = detect_capabilities()

        assert "mp4" in caps["formats"]

    def test_detects_filters(self):
        """detect_capabilities returns available filters."""
        def fake_run(cmd, *args, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            if "-filters" in cmd:
                mock.stdout = SAMPLE_FILTERS_OUTPUT
            elif "-version" in cmd:
                mock.stdout = SAMPLE_VERSION_OUTPUT
            else:
                mock.stdout = ""
            mock.stderr = ""
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            caps = detect_capabilities()

        assert "scale" in caps["filters"]
        assert "volume" in caps["filters"]

    def test_handles_missing_ffmpeg(self):
        """detect_capabilities returns empty result if ffmpeg not found."""
        with patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg not found")):
            caps = detect_capabilities()

        assert caps["version"] == ""
        assert caps["encoders"] == []

    def test_detects_hwaccel(self):
        """detect_capabilities checks for hardware acceleration."""
        def fake_run(cmd, *args, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            if "-encoders" in cmd:
                mock.stdout = SAMPLE_ENCODERS_OUTPUT
            elif "-version" in cmd:
                mock.stdout = SAMPLE_VERSION_OUTPUT
            else:
                mock.stdout = ""
            mock.stderr = ""
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            caps = detect_capabilities()

        # Should detect NVENC from encoder list
        hwaccels = caps.get("hwaccels", [])
        apis = [h["api"] for h in hwaccels]
        assert "cuda" in apis
