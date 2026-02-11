"""
Unit tests for the FFMPEG Builder output module.

Tests output configuration validation and command flag generation.
These are TDD tests -- they will FAIL until the backend modules are implemented.
"""
import pytest
from unittest.mock import patch

from ffmpeg_builder.output import (
    validate_output_config,
    generate_output_flags,
    OutputConfig,
    VALID_FORMATS,
)

from tests.fixtures.ffmpeg_factories import create_output_config


class TestOutputConfigValidation:
    """Tests for output configuration validation."""

    def test_validates_output_path_not_empty(self):
        """Empty output path is rejected with a validation error."""
        config = create_output_config(path="")
        result = validate_output_config(config)

        assert result.valid is False
        assert any("path" in e.lower() for e in result.errors)

    def test_validates_format_string(self):
        """Valid format strings are accepted."""
        config = create_output_config(format="mp4")
        result = validate_output_config(config)

        assert result.valid is True
        assert len(result.errors) == 0

    @pytest.mark.parametrize("fmt", ["mp4", "mkv", "webm", "ts", "flv", "hls", "dash"])
    def test_accepts_valid_formats(self, fmt):
        """All standard container formats are accepted."""
        config = create_output_config(path=f"/media/output.{fmt}", format=fmt)
        result = validate_output_config(config)

        assert result.valid is True

    def test_rejects_invalid_format(self):
        """Invalid/unknown format strings are rejected."""
        config = create_output_config(format="notaformat")
        result = validate_output_config(config)

        assert result.valid is False
        assert any("format" in e.lower() for e in result.errors)

    def test_warns_extension_format_mismatch(self):
        """Mismatched extension and format generates a warning."""
        config = create_output_config(path="/media/output.avi", format="mp4")
        result = validate_output_config(config)

        # Should still be valid but with a warning
        assert result.valid is True
        assert len(result.warnings) > 0
        assert any("mismatch" in w.lower() or "extension" in w.lower() for w in result.warnings)

    def test_validates_hls_creates_segments_dir(self):
        """HLS format validation checks or notes segment directory requirements."""
        config = create_output_config(
            path="/media/stream/playlist.m3u8",
            format="hls",
        )
        result = validate_output_config(config)

        # HLS is a valid format
        assert result.valid is True


class TestOutputCommandGeneration:
    """Tests for generating ffmpeg output command-line flags."""

    def test_generates_output_path(self):
        """Output path is the last element in the flags list."""
        config = create_output_config(path="/media/output.mp4")
        flags = generate_output_flags(config)

        # Output path should be the last flag
        assert flags[-1] == "/media/output.mp4"

    def test_generates_format_flag(self):
        """Format generates -f flag."""
        config = create_output_config(path="/media/output.mp4", format="mp4")
        flags = generate_output_flags(config)

        assert "-f" in flags
        f_idx = flags.index("-f")
        assert flags[f_idx + 1] == "mp4"

    def test_generates_overwrite_flag(self):
        """Overwrite=True generates -y flag."""
        config = create_output_config(overwrite=True)
        flags = generate_output_flags(config)

        assert "-y" in flags

    def test_generates_movflags_faststart(self):
        """MP4 faststart generates -movflags faststart."""
        config = create_output_config(
            format="mp4",
            movflags=["faststart"],
        )
        flags = generate_output_flags(config)

        assert "-movflags" in flags
        movflags_idx = flags.index("-movflags")
        assert "faststart" in flags[movflags_idx + 1]

    def test_generates_movflags_frag_keyframe(self):
        """Fragmented MP4 generates -movflags frag_keyframe."""
        config = create_output_config(
            format="mp4",
            movflags=["frag_keyframe"],
        )
        flags = generate_output_flags(config)

        assert "-movflags" in flags
        movflags_idx = flags.index("-movflags")
        assert "frag_keyframe" in flags[movflags_idx + 1]

    def test_combines_multiple_movflags(self):
        """Multiple movflags are combined with + separator."""
        config = create_output_config(
            format="mp4",
            movflags=["faststart", "frag_keyframe"],
        )
        flags = generate_output_flags(config)

        assert "-movflags" in flags
        movflags_idx = flags.index("-movflags")
        movflags_value = flags[movflags_idx + 1]
        assert "faststart" in movflags_value
        assert "frag_keyframe" in movflags_value
        assert "+" in movflags_value

    def test_no_movflags_for_non_mp4(self):
        """Non-MP4 formats do not include -movflags even if specified."""
        config = create_output_config(
            format="mkv",
            movflags=["faststart"],
        )
        flags = generate_output_flags(config)

        assert "-movflags" not in flags

    def test_generates_hls_segment_options(self):
        """HLS format generates segment-related options."""
        config = create_output_config(
            path="/media/stream/playlist.m3u8",
            format="hls",
            hls_time=6,
            hls_list_size=10,
        )
        flags = generate_output_flags(config)

        assert "-hls_time" in flags
        hls_time_idx = flags.index("-hls_time")
        assert str(6) == str(flags[hls_time_idx + 1])

        assert "-hls_list_size" in flags
        hls_list_idx = flags.index("-hls_list_size")
        assert str(10) == str(flags[hls_list_idx + 1])

    def test_generates_dash_segment_options(self):
        """DASH format generates segment-related options."""
        config = create_output_config(
            path="/media/stream/manifest.mpd",
            format="dash",
            seg_duration=4,
        )
        flags = generate_output_flags(config)

        assert "-seg_duration" in flags
        seg_idx = flags.index("-seg_duration")
        assert str(4) == str(flags[seg_idx + 1])

    def test_null_format_for_benchmark(self):
        """Null output format generates -f null for benchmarking."""
        config = create_output_config(
            path="/dev/null",
            format="null",
        )
        flags = generate_output_flags(config)

        assert "-f" in flags
        f_idx = flags.index("-f")
        assert flags[f_idx + 1] == "null"
