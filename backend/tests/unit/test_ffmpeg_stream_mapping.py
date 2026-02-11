"""
Unit tests for the FFMPEG Builder stream mapping module.

Tests stream mapping validation, flag generation, and default mapping behavior (Spec 1.7).
These are TDD tests -- they will FAIL until the backend modules are implemented.
"""
import pytest

from ffmpeg_builder.stream_mapping import (
    validate_stream_mapping,
    validate_stream_mappings,
    generate_map_flags,
    get_default_mappings,
    StreamMapping,
)

from tests.fixtures.ffmpeg_factories import (
    create_stream_mapping,
    create_builder_state,
    create_input_source,
)


class TestStreamMappingValidation:
    """Tests for stream mapping validation logic."""

    def test_validates_input_index_non_negative(self):
        """Input index must be >= 0."""
        mapping = create_stream_mapping(input_index=0)
        result = validate_stream_mapping(mapping)

        assert result.valid is True
        assert len(result.errors) == 0

    def test_validates_stream_type(self):
        """Stream type must be one of video, audio, subtitle, data."""
        for stype in ("video", "audio", "subtitle", "data"):
            mapping = create_stream_mapping(stream_type=stype)
            result = validate_stream_mapping(mapping)

            assert result.valid is True, f"Expected valid for stream_type={stype}"

    def test_validates_stream_index_non_negative(self):
        """Stream index must be >= 0."""
        mapping = create_stream_mapping(stream_index=0)
        result = validate_stream_mapping(mapping)

        assert result.valid is True

    def test_validates_output_index_non_negative(self):
        """Output index must be >= 0."""
        mapping = create_stream_mapping(output_index=0)
        result = validate_stream_mapping(mapping)

        assert result.valid is True

    def test_rejects_invalid_stream_type(self):
        """Invalid stream type is rejected with a validation error."""
        mapping = create_stream_mapping(stream_type="unknown")
        result = validate_stream_mapping(mapping)

        assert result.valid is False
        assert any("stream" in e.lower() or "type" in e.lower() for e in result.errors)

    def test_validates_no_duplicate_output_indices(self):
        """Multiple mappings with the same output index of the same type are rejected."""
        mappings = [
            create_stream_mapping(input_index=0, stream_type="audio", stream_index=0, output_index=0),
            create_stream_mapping(input_index=0, stream_type="audio", stream_index=1, output_index=0),
        ]
        result = validate_stream_mappings(mappings)

        assert result.valid is False
        assert any("duplicate" in e.lower() or "output" in e.lower() for e in result.errors)

    def test_rejects_negative_input_index(self):
        """Negative input index is rejected."""
        mapping = create_stream_mapping(input_index=-1)
        result = validate_stream_mapping(mapping)

        assert result.valid is False
        assert any("input" in e.lower() or "index" in e.lower() for e in result.errors)

    def test_rejects_negative_stream_index(self):
        """Negative stream index is rejected."""
        mapping = create_stream_mapping(stream_index=-1)
        result = validate_stream_mapping(mapping)

        assert result.valid is False
        assert any("stream" in e.lower() or "index" in e.lower() for e in result.errors)

    def test_rejects_negative_output_index(self):
        """Negative output index is rejected."""
        mapping = create_stream_mapping(output_index=-1)
        result = validate_stream_mapping(mapping)

        assert result.valid is False
        assert any("output" in e.lower() or "index" in e.lower() for e in result.errors)


class TestStreamMappingGeneration:
    """Tests for generating -map command-line flags from stream mappings."""

    def test_generates_map_flag_for_video(self):
        """Video stream mapping generates -map 0:v:0 flag."""
        mapping = create_stream_mapping(
            input_index=0, stream_type="video", stream_index=0,
        )
        flags = generate_map_flags([mapping])

        assert "-map" in flags
        assert any("0:v:0" in str(f) for f in flags)

    def test_generates_map_flag_for_audio(self):
        """Audio stream mapping generates -map 0:a:0 flag."""
        mapping = create_stream_mapping(
            input_index=0, stream_type="audio", stream_index=0,
        )
        flags = generate_map_flags([mapping])

        assert "-map" in flags
        assert any("0:a:0" in str(f) or "0:a" in str(f) for f in flags)

    def test_generates_map_flag_for_subtitle(self):
        """Subtitle stream mapping generates -map 0:s:0 flag."""
        mapping = create_stream_mapping(
            input_index=0, stream_type="subtitle", stream_index=0,
        )
        flags = generate_map_flags([mapping])

        assert "-map" in flags
        assert any("0:s:0" in str(f) or "0:s" in str(f) for f in flags)

    def test_generates_multiple_map_flags(self):
        """Multiple mappings generate multiple -map flags."""
        mappings = [
            create_stream_mapping(input_index=0, stream_type="video", stream_index=0, output_index=0),
            create_stream_mapping(input_index=0, stream_type="audio", stream_index=0, output_index=1),
            create_stream_mapping(input_index=0, stream_type="audio", stream_index=1, output_index=2),
        ]
        flags = generate_map_flags(mappings)

        map_count = flags.count("-map")
        assert map_count == 3

    def test_generates_map_with_input_index(self):
        """Mapping from second input generates -map 1:v:0."""
        mapping = create_stream_mapping(
            input_index=1, stream_type="video", stream_index=0,
        )
        flags = generate_map_flags([mapping])

        assert "-map" in flags
        assert any("1:v:0" in str(f) or "1:v" in str(f) for f in flags)

    def test_generates_negative_map(self):
        """Negative (exclude) mapping generates -map -0:d? for data exclusion."""
        mapping = create_stream_mapping(
            input_index=0, stream_type="data", stream_index=0, exclude=True,
        )
        flags = generate_map_flags([mapping])

        assert "-map" in flags
        # Negative map should have a minus prefix on the specifier
        assert any("-0:d" in str(f) for f in flags)

    def test_generates_language_metadata(self):
        """Mapping with language generates -metadata:s:<idx> language=<lang>."""
        mapping = create_stream_mapping(
            input_index=0, stream_type="audio", stream_index=0,
            output_index=1, language="eng",
        )
        flags = generate_map_flags([mapping])

        assert any("language" in str(f) for f in flags)
        assert any("eng" in str(f) for f in flags)

    def test_generates_title_metadata(self):
        """Mapping with a title generates -metadata:s:<idx> title=<title>."""
        mapping = create_stream_mapping(
            input_index=0, stream_type="audio", stream_index=0,
            output_index=1, title="Stereo",
        )
        flags = generate_map_flags([mapping])

        assert any("title" in str(f) for f in flags)
        assert any("Stereo" in str(f) for f in flags)


class TestDefaultMapping:
    """Tests for default stream mapping behavior when no explicit mappings are set."""

    def test_no_mappings_uses_default(self):
        """Empty mappings list results in default ffmpeg behavior (no -map flags)."""
        flags = generate_map_flags([])

        # No -map flags when no mappings specified
        assert "-map" not in flags

    def test_explicit_mapping_overrides_default(self):
        """Providing explicit mappings overrides default stream selection."""
        mappings = [
            create_stream_mapping(input_index=0, stream_type="video", stream_index=0),
        ]
        flags = generate_map_flags(mappings)

        assert "-map" in flags

    def test_maps_all_streams_with_wildcard(self):
        """Wildcard mapping maps all streams from an input."""
        defaults = get_default_mappings(input_count=1, map_all=True)

        # Should generate a mapping that selects all streams
        assert len(defaults) > 0

    def test_maps_specific_audio_track(self):
        """Mapping a specific audio track by index works correctly."""
        mapping = create_stream_mapping(
            input_index=0, stream_type="audio", stream_index=2,
        )
        flags = generate_map_flags([mapping])

        assert "-map" in flags
        assert any("0:a:2" in str(f) for f in flags)
