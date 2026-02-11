"""
Unit tests for the FFMPEG Builder input module.

Tests input source parsing, validation, and command flag generation.
These are TDD tests -- they will FAIL until the backend modules are implemented.
"""
import pytest
from unittest.mock import patch, MagicMock

from ffmpeg_builder.input import (
    validate_input_source,
    generate_input_flags,
    generate_hwaccel_flags,
    InputSource,
)

from tests.fixtures.ffmpeg_factories import (
    create_input_source,
    create_hwaccel_config,
)


class TestInputSourceValidation:
    """Tests for input source validation logic."""

    def test_validates_file_path_exists(self):
        """File input type validates that the file path exists on disk."""
        source = create_input_source(source_type="file", path="/media/input.mp4")

        with patch("os.path.exists", return_value=True):
            result = validate_input_source(source)

        assert result.valid is True
        assert len(result.errors) == 0

    def test_validates_url_format(self):
        """URL input type validates that the URL is well-formed."""
        source = create_input_source(
            source_type="url",
            path="http://example.com/stream.m3u8",
        )
        result = validate_input_source(source)

        assert result.valid is True
        assert len(result.errors) == 0

    def test_validates_rtsp_url(self):
        """RTSP URLs are accepted as valid input sources."""
        source = create_input_source(
            source_type="url",
            path="rtsp://192.168.1.100:554/stream1",
        )
        result = validate_input_source(source)

        assert result.valid is True

    def test_validates_device_path(self):
        """Device input type accepts valid device paths."""
        source = create_input_source(
            source_type="device",
            path="/dev/video0",
        )
        result = validate_input_source(source)

        assert result.valid is True

    def test_rejects_empty_path(self):
        """Empty path is rejected with a validation error."""
        source = create_input_source(source_type="file", path="")
        result = validate_input_source(source)

        assert result.valid is False
        assert any("path" in e.lower() for e in result.errors)

    def test_rejects_invalid_url_scheme(self):
        """URLs with unsupported schemes are rejected."""
        source = create_input_source(
            source_type="url",
            path="ftp://example.com/video.mp4",
        )
        result = validate_input_source(source)

        assert result.valid is False
        assert any("scheme" in e.lower() or "url" in e.lower() for e in result.errors)

    def test_accepts_pipe_input(self):
        """Pipe input (stdin) is accepted as a valid source."""
        source = create_input_source(source_type="pipe", path="pipe:0")
        result = validate_input_source(source)

        assert result.valid is True

    def test_validates_input_format_string(self):
        """Format string override is validated when provided."""
        source = create_input_source(
            source_type="file",
            path="/media/input.raw",
            format="rawvideo",
        )

        with patch("os.path.exists", return_value=True):
            result = validate_input_source(source)

        assert result.valid is True


class TestInputSourceCommandGeneration:
    """Tests for generating ffmpeg input command-line flags."""

    def test_generates_file_input_flags(self):
        """File input generates -i flag with file path."""
        source = create_input_source(source_type="file", path="/media/input.mp4")
        flags = generate_input_flags(source)

        assert "-i" in flags
        assert "/media/input.mp4" in flags

    def test_generates_url_input_flags(self):
        """URL input generates -i flag with the URL."""
        source = create_input_source(
            source_type="url",
            path="http://example.com/stream.m3u8",
        )
        flags = generate_input_flags(source)

        assert "-i" in flags
        assert "http://example.com/stream.m3u8" in flags

    def test_generates_device_input_with_format(self):
        """Device input includes -f flag for the device format."""
        source = create_input_source(
            source_type="device",
            path="/dev/video0",
            format="v4l2",
        )
        flags = generate_input_flags(source)

        assert "-f" in flags
        assert "v4l2" in flags
        assert "-i" in flags
        assert "/dev/video0" in flags

    def test_generates_pipe_input(self):
        """Pipe input generates -i pipe:0 flag."""
        source = create_input_source(source_type="pipe", path="pipe:0")
        flags = generate_input_flags(source)

        assert "-i" in flags
        assert "pipe:0" in flags

    def test_includes_seek_flag(self):
        """Start time generates -ss seek flag before -i."""
        source = create_input_source(
            source_type="file",
            path="/media/input.mp4",
            start_time="00:01:30",
        )
        flags = generate_input_flags(source)

        assert "-ss" in flags
        assert "00:01:30" in flags
        # -ss should come before -i for input seeking
        ss_idx = flags.index("-ss")
        i_idx = flags.index("-i")
        assert ss_idx < i_idx

    def test_includes_duration_flag(self):
        """Duration generates -t flag."""
        source = create_input_source(
            source_type="file",
            path="/media/input.mp4",
            duration="00:05:00",
        )
        flags = generate_input_flags(source)

        assert "-t" in flags
        assert "00:05:00" in flags

    def test_includes_stream_loop(self):
        """Stream loop count generates -stream_loop flag."""
        source = create_input_source(
            source_type="file",
            path="/media/input.mp4",
            stream_loop=-1,
        )
        flags = generate_input_flags(source)

        assert "-stream_loop" in flags
        assert "-1" in flags or -1 in flags

    def test_includes_format_override(self):
        """Format override generates -f flag before -i."""
        source = create_input_source(
            source_type="file",
            path="/media/input.raw",
            format="rawvideo",
        )
        flags = generate_input_flags(source)

        assert "-f" in flags
        assert "rawvideo" in flags
        # -f should come before -i
        f_idx = flags.index("-f")
        i_idx = flags.index("-i")
        assert f_idx < i_idx


class TestHWAccelInput:
    """Tests for hardware acceleration input flag generation."""

    def test_generates_cuda_hwaccel_flags(self):
        """CUDA hardware acceleration generates -hwaccel cuda flag."""
        hwaccel = create_hwaccel_config(api="cuda")
        source = create_input_source(
            source_type="file",
            path="/media/input.mp4",
            hwaccel=hwaccel,
        )
        flags = generate_hwaccel_flags(source)

        assert "-hwaccel" in flags
        assert "cuda" in flags

    def test_generates_qsv_hwaccel_flags(self):
        """QSV hardware acceleration generates -hwaccel qsv flag."""
        hwaccel = create_hwaccel_config(api="qsv")
        source = create_input_source(
            source_type="file",
            path="/media/input.mp4",
            hwaccel=hwaccel,
        )
        flags = generate_hwaccel_flags(source)

        assert "-hwaccel" in flags
        assert "qsv" in flags

    def test_generates_vaapi_device_flag(self):
        """VAAPI generates -vaapi_device flag with the device path."""
        hwaccel = create_hwaccel_config(
            api="vaapi",
            device="/dev/dri/renderD128",
        )
        source = create_input_source(
            source_type="file",
            path="/media/input.mp4",
            hwaccel=hwaccel,
        )
        flags = generate_hwaccel_flags(source)

        assert "-vaapi_device" in flags
        assert "/dev/dri/renderD128" in flags

    def test_includes_hwaccel_output_format(self):
        """HW accel output format generates -hwaccel_output_format flag."""
        hwaccel = create_hwaccel_config(
            api="cuda",
            output_format="cuda",
        )
        source = create_input_source(
            source_type="file",
            path="/media/input.mp4",
            hwaccel=hwaccel,
        )
        flags = generate_hwaccel_flags(source)

        assert "-hwaccel_output_format" in flags
        assert "cuda" in flags

    def test_no_hwaccel_when_none(self):
        """No HW accel flags when hwaccel is None or api is 'none'."""
        source = create_input_source(
            source_type="file",
            path="/media/input.mp4",
            hwaccel=None,
        )
        flags = generate_hwaccel_flags(source)

        assert flags == [] or flags is None or len(flags) == 0

    def test_cuda_with_output_format(self):
        """CUDA with output format generates both -hwaccel and -hwaccel_output_format."""
        hwaccel = create_hwaccel_config(
            api="cuda",
            output_format="cuda",
        )
        source = create_input_source(
            source_type="file",
            path="/media/input.mp4",
            hwaccel=hwaccel,
        )
        flags = generate_hwaccel_flags(source)

        assert "-hwaccel" in flags
        assert "cuda" in flags
        assert "-hwaccel_output_format" in flags

    def test_vaapi_with_device_path(self):
        """VAAPI with custom device path includes the specified device."""
        hwaccel = create_hwaccel_config(
            api="vaapi",
            device="/dev/dri/renderD129",
        )
        source = create_input_source(
            source_type="file",
            path="/media/input.mp4",
            hwaccel=hwaccel,
        )
        flags = generate_hwaccel_flags(source)

        assert "-vaapi_device" in flags
        assert "/dev/dri/renderD129" in flags

    def test_qsv_with_device_selection(self):
        """QSV with device generates appropriate device selection flags."""
        hwaccel = create_hwaccel_config(
            api="qsv",
            device="/dev/dri/renderD128",
        )
        source = create_input_source(
            source_type="file",
            path="/media/input.mp4",
            hwaccel=hwaccel,
        )
        flags = generate_hwaccel_flags(source)

        assert "-hwaccel" in flags
        assert "qsv" in flags
        # QSV should reference the device somehow
        assert any("/dev/dri/renderD128" in str(f) for f in flags)
