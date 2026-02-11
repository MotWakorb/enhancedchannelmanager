"""
Unit tests for the FFMPEG Builder validation and command generation modules.

Tests full command validation, generation, and annotation (Spec 1.9 backend).
These are TDD tests -- they will FAIL until the backend modules are implemented.
"""
import pytest

from ffmpeg_builder.validation import (
    validate_config,
    ValidationResult,
)
from ffmpeg_builder.command_generator import (
    generate_command,
    annotate_command,
    AnnotatedCommand,
)

from tests.fixtures.ffmpeg_factories import (
    create_builder_state,
    create_input_source,
    create_output_config,
    create_video_codec_settings,
    create_audio_codec_settings,
    create_video_filter,
    create_audio_filter,
    create_stream_mapping,
    create_hwaccel_config,
)


class TestCommandGeneration:
    """Tests for generating full ffmpeg command strings."""

    @pytest.fixture
    def basic_state(self):
        """Create a basic builder state for command generation tests."""
        return create_builder_state()

    def test_generates_basic_transcode_command(self, basic_state):
        """Basic transcode config produces a valid ffmpeg command list."""
        cmd = generate_command(basic_state)

        assert cmd[0] == "ffmpeg"
        assert "-i" in cmd
        assert "/media/input.mp4" in cmd
        assert "-c:v" in cmd
        assert "libx264" in cmd
        assert "-c:a" in cmd
        assert "aac" in cmd
        assert cmd[-1] == "/media/output.mp4"

    def test_generates_nvenc_command(self):
        """NVENC config produces h264_nvenc codec flag and CUDA hwaccel."""
        state = create_builder_state(
            input_source=create_input_source(
                hwaccel=create_hwaccel_config(api="cuda", output_format="cuda"),
            ),
            video_codec=create_video_codec_settings(codec="h264_nvenc"),
        )
        cmd = generate_command(state)

        assert "h264_nvenc" in cmd
        assert "-hwaccel" in cmd
        assert "cuda" in cmd

    def test_generates_qsv_command(self):
        """QSV config produces h264_qsv codec flag and QSV hwaccel."""
        state = create_builder_state(
            input_source=create_input_source(
                hwaccel=create_hwaccel_config(api="qsv"),
            ),
            video_codec=create_video_codec_settings(codec="h264_qsv"),
        )
        cmd = generate_command(state)

        assert "h264_qsv" in cmd
        assert "-hwaccel" in cmd
        assert "qsv" in cmd

    def test_generates_vaapi_command(self):
        """VAAPI config produces h264_vaapi codec and vaapi_device flag."""
        state = create_builder_state(
            input_source=create_input_source(
                hwaccel=create_hwaccel_config(
                    api="vaapi",
                    device="/dev/dri/renderD128",
                ),
            ),
            video_codec=create_video_codec_settings(codec="h264_vaapi"),
        )
        cmd = generate_command(state)

        assert "h264_vaapi" in cmd
        assert "-vaapi_device" in cmd
        assert "/dev/dri/renderD128" in cmd

    def test_generates_copy_command(self):
        """Stream copy generates -c:v copy and -c:a copy flags."""
        state = create_builder_state(
            video_codec=create_video_codec_settings(codec="copy"),
            audio_codec=create_audio_codec_settings(codec="copy"),
        )
        cmd = generate_command(state)

        assert "-c:v" in cmd
        cv_idx = cmd.index("-c:v")
        assert cmd[cv_idx + 1] == "copy"

        assert "-c:a" in cmd
        ca_idx = cmd.index("-c:a")
        assert cmd[ca_idx + 1] == "copy"

    def test_generates_command_with_video_filters(self):
        """Video filters produce a -vf flag with the filter chain."""
        state = create_builder_state(
            video_filters=[
                create_video_filter(
                    filter_type="scale",
                    params={"width": 1920, "height": 1080},
                ),
                create_video_filter(
                    filter_type="fps",
                    params={"fps": 30},
                    order=1,
                ),
            ],
        )
        cmd = generate_command(state)

        assert "-vf" in cmd or "-filter:v" in cmd

    def test_generates_command_with_audio_filters(self):
        """Audio filters produce a -af flag with the filter chain."""
        state = create_builder_state(
            audio_filters=[
                create_audio_filter(
                    filter_type="volume",
                    params={"level": 1.5},
                ),
                create_audio_filter(
                    filter_type="loudnorm",
                    params={},
                    order=1,
                ),
            ],
        )
        cmd = generate_command(state)

        assert "-af" in cmd or "-filter:a" in cmd

    def test_generates_command_with_stream_mappings(self):
        """Stream mappings produce -map flags."""
        state = create_builder_state(
            stream_mappings=[
                create_stream_mapping(input_index=0, stream_type="video", stream_index=0),
                create_stream_mapping(input_index=0, stream_type="audio", stream_index=1),
            ],
        )
        cmd = generate_command(state)

        assert "-map" in cmd

    def test_generates_multi_input_command(self):
        """Multiple inputs generate multiple -i flags."""
        state = create_builder_state(
            input_source=create_input_source(path="/media/video.mp4"),
        )
        # Add a second input as an additional input list
        state["additionalInputs"] = [
            create_input_source(path="/media/audio.wav"),
        ]
        cmd = generate_command(state)

        # Should have at least two -i flags
        i_count = cmd.count("-i")
        assert i_count >= 2

    def test_flag_ordering_input_before_output(self):
        """Input -i flags come before the output path."""
        state = create_builder_state()
        cmd = generate_command(state)

        i_idx = cmd.index("-i")
        # Output path is last
        output_idx = len(cmd) - 1
        assert i_idx < output_idx

    def test_hwaccel_flags_before_input(self):
        """HW accel flags appear before the -i flag."""
        state = create_builder_state(
            input_source=create_input_source(
                hwaccel=create_hwaccel_config(api="cuda"),
            ),
        )
        cmd = generate_command(state)

        hwaccel_idx = cmd.index("-hwaccel")
        i_idx = cmd.index("-i")
        assert hwaccel_idx < i_idx

    def test_filter_flags_between_codecs_and_output(self):
        """Filter flags (-vf/-af) appear after codec flags and before output path."""
        state = create_builder_state(
            video_filters=[
                create_video_filter(filter_type="scale", params={"width": 1280, "height": 720}),
            ],
        )
        cmd = generate_command(state)

        # Find the video filter flag
        vf_flag = "-vf" if "-vf" in cmd else "-filter:v"
        assert vf_flag in cmd

        vf_idx = cmd.index(vf_flag)
        output_idx = len(cmd) - 1
        assert vf_idx < output_idx


class TestCommandValidation:
    """Tests for validating a complete ffmpeg builder configuration."""

    def test_valid_basic_config(self):
        """Basic valid configuration passes validation."""
        state = create_builder_state()
        result = validate_config(state)

        assert result.valid is True
        assert len(result.errors) == 0

    def test_invalid_missing_input(self):
        """Config with no input source fails validation."""
        state = create_builder_state()
        state["input"] = None
        result = validate_config(state)

        assert result.valid is False
        assert any("input" in e.lower() for e in result.errors)

    def test_invalid_missing_output(self):
        """Config with no output config fails validation."""
        state = create_builder_state()
        state["output"] = None
        result = validate_config(state)

        assert result.valid is False
        assert any("output" in e.lower() for e in result.errors)

    def test_warns_codec_container_mismatch(self):
        """VP9 codec in MP4 container generates a warning."""
        state = create_builder_state(
            video_codec=create_video_codec_settings(codec="libvpx-vp9"),
            output_config=create_output_config(format="mp4"),
        )
        result = validate_config(state)

        # May still be valid but should warn
        assert len(result.warnings) > 0
        assert any(
            "mismatch" in w.lower() or "container" in w.lower() or "codec" in w.lower()
            for w in result.warnings
        )

    def test_warns_vaapi_without_hwupload(self):
        """VAAPI encoder without hwupload_vaapi filter generates a warning."""
        state = create_builder_state(
            input_source=create_input_source(
                hwaccel=create_hwaccel_config(api="vaapi", device="/dev/dri/renderD128"),
            ),
            video_codec=create_video_codec_settings(codec="h264_vaapi"),
            video_filters=[],  # No hwupload_vaapi filter
        )
        result = validate_config(state)

        assert len(result.warnings) > 0
        assert any("vaapi" in w.lower() or "hwupload" in w.lower() for w in result.warnings)

    def test_warns_audio_filters_with_copy(self):
        """Audio filters with codec=copy generates a warning."""
        state = create_builder_state(
            audio_codec=create_audio_codec_settings(codec="copy"),
            audio_filters=[
                create_audio_filter(filter_type="volume", params={"level": 2.0}),
            ],
        )
        result = validate_config(state)

        assert len(result.warnings) > 0
        assert any(
            "copy" in w.lower() and ("filter" in w.lower() or "audio" in w.lower())
            for w in result.warnings
        )

    def test_warns_video_filters_with_copy(self):
        """Video filters with codec=copy generates a warning."""
        state = create_builder_state(
            video_codec=create_video_codec_settings(codec="copy"),
            video_filters=[
                create_video_filter(filter_type="scale", params={"width": 1280, "height": 720}),
            ],
        )
        result = validate_config(state)

        assert len(result.warnings) > 0
        assert any(
            "copy" in w.lower() and ("filter" in w.lower() or "video" in w.lower())
            for w in result.warnings
        )

    def test_valid_nvenc_config(self):
        """NVENC configuration with CUDA hwaccel passes validation."""
        state = create_builder_state(
            input_source=create_input_source(
                hwaccel=create_hwaccel_config(api="cuda", output_format="cuda"),
            ),
            video_codec=create_video_codec_settings(codec="h264_nvenc", preset="p4"),
        )
        result = validate_config(state)

        assert result.valid is True

    def test_valid_qsv_config(self):
        """QSV configuration passes validation."""
        state = create_builder_state(
            input_source=create_input_source(
                hwaccel=create_hwaccel_config(api="qsv"),
            ),
            video_codec=create_video_codec_settings(codec="h264_qsv", preset="medium"),
        )
        result = validate_config(state)

        assert result.valid is True

    def test_valid_vaapi_config(self):
        """VAAPI configuration with hwupload filter passes validation."""
        state = create_builder_state(
            input_source=create_input_source(
                hwaccel=create_hwaccel_config(
                    api="vaapi",
                    device="/dev/dri/renderD128",
                ),
            ),
            video_codec=create_video_codec_settings(codec="h264_vaapi"),
            video_filters=[
                create_video_filter(
                    filter_type="hwupload_vaapi",
                    params={},
                    order=0,
                ),
            ],
        )
        result = validate_config(state)

        assert result.valid is True

    def test_rejects_negative_crf(self):
        """Negative CRF value fails validation."""
        state = create_builder_state(
            video_codec=create_video_codec_settings(crf=-1),
        )
        result = validate_config(state)

        assert result.valid is False
        assert any("crf" in e.lower() for e in result.errors)

    def test_rejects_crf_above_51(self):
        """CRF value above 51 fails validation."""
        state = create_builder_state(
            video_codec=create_video_codec_settings(crf=52),
        )
        result = validate_config(state)

        assert result.valid is False
        assert any("crf" in e.lower() for e in result.errors)

    def test_rejects_negative_bitrate(self):
        """Negative bitrate value fails validation."""
        state = create_builder_state(
            video_codec=create_video_codec_settings(
                rate_control="cbr",
                crf=None,
                bitrate="-1000k",
            ),
        )
        result = validate_config(state)

        assert result.valid is False
        assert any("bitrate" in e.lower() for e in result.errors)


class TestCommandAnnotation:
    """Tests for command annotation (human-readable explanations)."""

    @pytest.fixture
    def basic_state(self):
        """Create a basic builder state for annotation tests."""
        return create_builder_state()

    def test_annotates_input_flags(self, basic_state):
        """Input flags are annotated with explanations."""
        annotated = annotate_command(basic_state)

        # Find the annotation for the -i flag
        input_annotations = [
            a for a in annotated.annotations
            if "-i" in a.flag or "input" in a.category.lower()
        ]
        assert len(input_annotations) > 0

    def test_annotates_video_codec_flags(self, basic_state):
        """Video codec flags are annotated."""
        annotated = annotate_command(basic_state)

        video_annotations = [
            a for a in annotated.annotations
            if "video" in a.category.lower() or "-c:v" in a.flag
        ]
        assert len(video_annotations) > 0

    def test_annotates_audio_codec_flags(self, basic_state):
        """Audio codec flags are annotated."""
        annotated = annotate_command(basic_state)

        audio_annotations = [
            a for a in annotated.annotations
            if "audio" in a.category.lower() or "-c:a" in a.flag
        ]
        assert len(audio_annotations) > 0

    def test_annotates_filter_flags(self):
        """Filter flags are annotated with explanations."""
        state = create_builder_state(
            video_filters=[
                create_video_filter(
                    filter_type="scale",
                    params={"width": 1280, "height": 720},
                ),
            ],
        )
        annotated = annotate_command(state)

        filter_annotations = [
            a for a in annotated.annotations
            if "filter" in a.category.lower() or "-vf" in a.flag
        ]
        assert len(filter_annotations) > 0

    def test_annotates_output_flags(self, basic_state):
        """Output flags are annotated."""
        annotated = annotate_command(basic_state)

        output_annotations = [
            a for a in annotated.annotations
            if "output" in a.category.lower()
        ]
        assert len(output_annotations) > 0

    def test_annotations_have_explanations(self, basic_state):
        """Every annotation includes a non-empty explanation string."""
        annotated = annotate_command(basic_state)

        assert len(annotated.annotations) > 0
        for annotation in annotated.annotations:
            assert hasattr(annotation, "explanation")
            assert annotation.explanation is not None
            assert len(annotation.explanation.strip()) > 0
