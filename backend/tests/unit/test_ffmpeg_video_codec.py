"""
Unit tests for the FFMPEG Builder video codec module.

Tests video codec validation, software/hardware codec flag generation,
and stream copy behavior.
These are TDD tests -- they will FAIL until the backend modules are implemented.
"""
import pytest

from ffmpeg_builder.video_codec import (
    validate_video_codec,
    generate_video_codec_flags,
    VideoCodecSettings,
    KNOWN_VIDEO_CODECS,
)

from tests.fixtures.ffmpeg_factories import (
    create_video_codec_settings,
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestVideoCodecValidation:
    """Tests for video codec validation logic."""

    def test_validates_known_codec(self):
        """Known codecs such as libx264 are accepted."""
        settings = create_video_codec_settings(codec="libx264")
        result = validate_video_codec(settings)

        assert result.valid is True
        assert len(result.errors) == 0

    def test_rejects_unknown_codec(self):
        """An unrecognised codec name is rejected with a validation error."""
        settings = create_video_codec_settings(codec="libfakecodec999")
        result = validate_video_codec(settings)

        assert result.valid is False
        assert any("codec" in e.lower() for e in result.errors)

    def test_validates_crf_range_0_to_51(self):
        """CRF values within the 0-51 range are accepted."""
        for crf in (0, 18, 23, 51):
            settings = create_video_codec_settings(crf=crf)
            result = validate_video_codec(settings)

            assert result.valid is True, f"CRF {crf} should be valid"

    def test_rejects_negative_crf(self):
        """Negative CRF values are rejected."""
        settings = create_video_codec_settings(crf=-1)
        result = validate_video_codec(settings)

        assert result.valid is False
        assert any("crf" in e.lower() for e in result.errors)

    def test_rejects_crf_above_51(self):
        """CRF values above 51 are rejected."""
        settings = create_video_codec_settings(crf=52)
        result = validate_video_codec(settings)

        assert result.valid is False
        assert any("crf" in e.lower() for e in result.errors)

    def test_validates_bitrate_format(self):
        """Bitrate strings like '5000k' or '5M' are accepted."""
        for br in ("5000k", "5M", "2500K", "10m"):
            settings = create_video_codec_settings(
                rate_control="cbr", crf=None, bitrate=br,
            )
            result = validate_video_codec(settings)

            assert result.valid is True, f"Bitrate '{br}' should be valid"

    def test_rejects_invalid_bitrate(self):
        """Malformed bitrate strings are rejected."""
        settings = create_video_codec_settings(
            rate_control="cbr", crf=None, bitrate="notabitrate",
        )
        result = validate_video_codec(settings)

        assert result.valid is False
        assert any("bitrate" in e.lower() for e in result.errors)

    def test_validates_preset_for_codec(self):
        """Codec-specific presets are accepted (medium for x264, p4 for nvenc)."""
        x264 = create_video_codec_settings(codec="libx264", preset="medium")
        result_x264 = validate_video_codec(x264)
        assert result_x264.valid is True

        nvenc = create_video_codec_settings(codec="h264_nvenc", preset="p4")
        result_nvenc = validate_video_codec(nvenc)
        assert result_nvenc.valid is True

    def test_validates_profile_for_codec(self):
        """Valid profiles for a given codec are accepted."""
        settings = create_video_codec_settings(codec="libx264", profile="high")
        result = validate_video_codec(settings)

        assert result.valid is True

    def test_validates_pixel_format(self):
        """Common pixel formats like yuv420p are accepted."""
        settings = create_video_codec_settings(pixel_format="yuv420p")
        result = validate_video_codec(settings)

        assert result.valid is True


# ---------------------------------------------------------------------------
# Software codec flag generation
# ---------------------------------------------------------------------------

class TestSoftwareCodecGeneration:
    """Tests for software video encoder flag generation."""

    def test_generates_x264_crf_flags(self):
        """libx264 with CRF generates -c:v libx264 -crf 23."""
        settings = create_video_codec_settings(
            codec="libx264", rate_control="crf", crf=23,
        )
        flags = generate_video_codec_flags(settings)

        assert "-c:v" in flags
        assert "libx264" in flags
        assert "-crf" in flags
        crf_idx = flags.index("-crf")
        assert str(flags[crf_idx + 1]) == "23"

    def test_generates_x265_crf_flags(self):
        """libx265 with CRF generates -c:v libx265 -crf value."""
        settings = create_video_codec_settings(
            codec="libx265", rate_control="crf", crf=28,
        )
        flags = generate_video_codec_flags(settings)

        assert "-c:v" in flags
        assert "libx265" in flags
        assert "-crf" in flags
        crf_idx = flags.index("-crf")
        assert str(flags[crf_idx + 1]) == "28"

    def test_generates_vp9_flags(self):
        """libvpx-vp9 generates appropriate -c:v flag."""
        settings = create_video_codec_settings(
            codec="libvpx-vp9", rate_control="crf", crf=30,
        )
        flags = generate_video_codec_flags(settings)

        assert "-c:v" in flags
        assert "libvpx-vp9" in flags

    def test_generates_av1_flags(self):
        """libaom-av1 generates appropriate -c:v flag."""
        settings = create_video_codec_settings(
            codec="libaom-av1", rate_control="crf", crf=30,
        )
        flags = generate_video_codec_flags(settings)

        assert "-c:v" in flags
        assert "libaom-av1" in flags

    def test_generates_svtav1_flags(self):
        """libsvtav1 generates appropriate -c:v flag."""
        settings = create_video_codec_settings(
            codec="libsvtav1", rate_control="crf", crf=35,
        )
        flags = generate_video_codec_flags(settings)

        assert "-c:v" in flags
        assert "libsvtav1" in flags

    def test_generates_preset_flag(self):
        """Preset generates -preset flag."""
        settings = create_video_codec_settings(preset="fast")
        flags = generate_video_codec_flags(settings)

        assert "-preset" in flags
        preset_idx = flags.index("-preset")
        assert flags[preset_idx + 1] == "fast"

    def test_generates_profile_flag(self):
        """Profile generates -profile:v flag."""
        settings = create_video_codec_settings(profile="high")
        flags = generate_video_codec_flags(settings)

        assert "-profile:v" in flags
        profile_idx = flags.index("-profile:v")
        assert flags[profile_idx + 1] == "high"

    def test_generates_tune_flag(self):
        """Tune setting generates -tune flag."""
        settings = create_video_codec_settings(tune="film")
        flags = generate_video_codec_flags(settings)

        assert "-tune" in flags
        tune_idx = flags.index("-tune")
        assert flags[tune_idx + 1] == "film"

    def test_generates_pixel_format_flag(self):
        """Pixel format generates -pix_fmt flag."""
        settings = create_video_codec_settings(pixel_format="yuv420p")
        flags = generate_video_codec_flags(settings)

        assert "-pix_fmt" in flags
        pf_idx = flags.index("-pix_fmt")
        assert flags[pf_idx + 1] == "yuv420p"

    def test_generates_keyframe_interval(self):
        """Keyframe interval generates -g flag."""
        settings = create_video_codec_settings(keyframe_interval=250)
        flags = generate_video_codec_flags(settings)

        assert "-g" in flags
        g_idx = flags.index("-g")
        assert str(flags[g_idx + 1]) == "250"

    def test_generates_bframes_flag(self):
        """B-frames count generates -bf flag."""
        settings = create_video_codec_settings(bframes=3)
        flags = generate_video_codec_flags(settings)

        assert "-bf" in flags
        bf_idx = flags.index("-bf")
        assert str(flags[bf_idx + 1]) == "3"


# ---------------------------------------------------------------------------
# NVENC codec flag generation
# ---------------------------------------------------------------------------

class TestNVENCCodecGeneration:
    """Tests for NVIDIA NVENC hardware encoder flag generation."""

    def test_generates_nvenc_h264_flags(self):
        """h264_nvenc generates -c:v h264_nvenc."""
        settings = create_video_codec_settings(codec="h264_nvenc")
        flags = generate_video_codec_flags(settings)

        assert "-c:v" in flags
        assert "h264_nvenc" in flags

    def test_generates_nvenc_hevc_flags(self):
        """hevc_nvenc generates -c:v hevc_nvenc."""
        settings = create_video_codec_settings(codec="hevc_nvenc")
        flags = generate_video_codec_flags(settings)

        assert "-c:v" in flags
        assert "hevc_nvenc" in flags

    def test_generates_nvenc_preset(self):
        """NVENC preset generates -preset flag with NVENC-specific value."""
        settings = create_video_codec_settings(codec="h264_nvenc", preset="p4")
        flags = generate_video_codec_flags(settings)

        assert "-preset" in flags
        preset_idx = flags.index("-preset")
        assert flags[preset_idx + 1] == "p4"

    def test_generates_nvenc_rc_mode(self):
        """NVENC rate control mode generates -rc flag."""
        settings = create_video_codec_settings(
            codec="h264_nvenc", rc="constqp",
        )
        flags = generate_video_codec_flags(settings)

        assert "-rc" in flags
        rc_idx = flags.index("-rc")
        assert flags[rc_idx + 1] == "constqp"

    def test_generates_spatial_aq(self):
        """NVENC spatial AQ generates -spatial-aq 1 flag."""
        settings = create_video_codec_settings(
            codec="h264_nvenc", spatial_aq=True,
        )
        flags = generate_video_codec_flags(settings)

        assert "-spatial-aq" in flags or "-spatial_aq" in flags

    def test_generates_temporal_aq(self):
        """NVENC temporal AQ generates -temporal-aq 1 flag."""
        settings = create_video_codec_settings(
            codec="h264_nvenc", temporal_aq=True,
        )
        flags = generate_video_codec_flags(settings)

        assert "-temporal-aq" in flags or "-temporal_aq" in flags

    def test_generates_cq_value(self):
        """NVENC constant quality generates -cq flag."""
        settings = create_video_codec_settings(
            codec="h264_nvenc", cq=20,
        )
        flags = generate_video_codec_flags(settings)

        assert "-cq" in flags
        cq_idx = flags.index("-cq")
        assert str(flags[cq_idx + 1]) == "20"

    def test_generates_b_ref_mode(self):
        """NVENC B-ref mode generates -b_ref_mode flag."""
        settings = create_video_codec_settings(
            codec="h264_nvenc", b_ref_mode="middle",
        )
        flags = generate_video_codec_flags(settings)

        assert "-b_ref_mode" in flags
        brm_idx = flags.index("-b_ref_mode")
        assert flags[brm_idx + 1] == "middle"


# ---------------------------------------------------------------------------
# QSV codec flag generation
# ---------------------------------------------------------------------------

class TestQSVCodecGeneration:
    """Tests for Intel QSV hardware encoder flag generation."""

    def test_generates_qsv_h264_flags(self):
        """h264_qsv generates -c:v h264_qsv."""
        settings = create_video_codec_settings(codec="h264_qsv")
        flags = generate_video_codec_flags(settings)

        assert "-c:v" in flags
        assert "h264_qsv" in flags

    def test_generates_qsv_hevc_flags(self):
        """hevc_qsv generates -c:v hevc_qsv."""
        settings = create_video_codec_settings(codec="hevc_qsv")
        flags = generate_video_codec_flags(settings)

        assert "-c:v" in flags
        assert "hevc_qsv" in flags

    def test_generates_global_quality(self):
        """QSV global quality generates -global_quality flag."""
        settings = create_video_codec_settings(
            codec="h264_qsv", global_quality=25,
        )
        flags = generate_video_codec_flags(settings)

        assert "-global_quality" in flags
        gq_idx = flags.index("-global_quality")
        assert str(flags[gq_idx + 1]) == "25"

    def test_generates_look_ahead(self):
        """QSV look-ahead generates -look_ahead 1 flag."""
        settings = create_video_codec_settings(
            codec="h264_qsv", look_ahead=True,
        )
        flags = generate_video_codec_flags(settings)

        assert "-look_ahead" in flags


# ---------------------------------------------------------------------------
# VAAPI codec flag generation
# ---------------------------------------------------------------------------

class TestVAAPICodecGeneration:
    """Tests for VAAPI hardware encoder flag generation."""

    def test_generates_vaapi_h264_flags(self):
        """h264_vaapi generates -c:v h264_vaapi."""
        settings = create_video_codec_settings(codec="h264_vaapi")
        flags = generate_video_codec_flags(settings)

        assert "-c:v" in flags
        assert "h264_vaapi" in flags

    def test_generates_vaapi_hevc_flags(self):
        """hevc_vaapi generates -c:v hevc_vaapi."""
        settings = create_video_codec_settings(codec="hevc_vaapi")
        flags = generate_video_codec_flags(settings)

        assert "-c:v" in flags
        assert "hevc_vaapi" in flags

    def test_generates_vaapi_quality(self):
        """VAAPI quality generates -quality flag."""
        settings = create_video_codec_settings(
            codec="h264_vaapi", quality=20,
        )
        flags = generate_video_codec_flags(settings)

        assert "-quality" in flags
        q_idx = flags.index("-quality")
        assert str(flags[q_idx + 1]) == "20"

    def test_generates_compression_level(self):
        """VAAPI compression level generates -compression_level flag."""
        settings = create_video_codec_settings(
            codec="h264_vaapi", compression_level=4,
        )
        flags = generate_video_codec_flags(settings)

        assert "-compression_level" in flags
        cl_idx = flags.index("-compression_level")
        assert str(flags[cl_idx + 1]) == "4"


# ---------------------------------------------------------------------------
# Stream copy
# ---------------------------------------------------------------------------

class TestStreamCopy:
    """Tests for video stream copy (passthrough) mode."""

    def test_generates_copy_flag(self):
        """Stream copy generates -c:v copy."""
        settings = create_video_codec_settings(codec="copy")
        flags = generate_video_codec_flags(settings)

        assert "-c:v" in flags
        assert "copy" in flags

    def test_copy_ignores_all_settings(self):
        """Stream copy ignores CRF, preset, profile, and pixel format."""
        settings = create_video_codec_settings(
            codec="copy",
            preset="fast",
            crf=18,
            profile="high",
            pixel_format="yuv420p",
        )
        flags = generate_video_codec_flags(settings)

        assert "-c:v" in flags
        assert "copy" in flags
        # None of the encoding options should appear
        assert "-crf" not in flags
        assert "-preset" not in flags
        assert "-profile:v" not in flags
        assert "-pix_fmt" not in flags
