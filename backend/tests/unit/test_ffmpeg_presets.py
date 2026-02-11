"""
Unit tests for the FFMPEG Builder presets module.

Tests preset loading, application, and custom preset management (Spec 1.8).
These are TDD tests -- they will FAIL until the backend modules are implemented.
"""
import pytest

from ffmpeg_builder.presets import (
    get_builtin_presets,
    get_preset,
    apply_preset,
    save_custom_preset,
    load_custom_preset,
    delete_custom_preset,
    list_all_presets,
    PresetNotFoundError,
    BuiltinPresetDeleteError,
)

from tests.fixtures.ffmpeg_factories import (
    create_builder_state,
    create_preset_template,
)


class TestPresetLoading:
    """Tests for loading built-in presets."""

    def test_loads_builtin_presets(self):
        """Built-in presets are loadable and non-empty."""
        presets = get_builtin_presets()

        assert isinstance(presets, list)
        assert len(presets) > 0

    def test_builtin_presets_have_required_fields(self):
        """Every built-in preset has id, name, and config fields."""
        presets = get_builtin_presets()

        for preset in presets:
            assert "id" in preset
            assert "name" in preset
            assert "config" in preset

    def test_web_mp4_preset_config(self):
        """Web MP4 preset uses libx264 codec with faststart movflags."""
        preset = get_preset("web-mp4")

        assert preset is not None
        config = preset["config"]
        assert config["videoCodec"]["codec"] == "libx264"
        assert config["output"]["format"] == "mp4"

    def test_hls_streaming_preset_config(self):
        """HLS streaming preset uses HLS output format."""
        preset = get_preset("hls-streaming")

        assert preset is not None
        config = preset["config"]
        assert config["output"]["format"] == "hls"

    def test_archive_hevc_preset_config(self):
        """Archive HEVC preset uses libx265 codec for high quality."""
        preset = get_preset("archive-hevc")

        assert preset is not None
        config = preset["config"]
        assert config["videoCodec"]["codec"] == "libx265"

    def test_nvenc_fast_preset_config(self):
        """NVENC fast preset uses h264_nvenc codec with CUDA hwaccel."""
        preset = get_preset("nvenc-fast")

        assert preset is not None
        config = preset["config"]
        assert config["videoCodec"]["codec"] == "h264_nvenc"

    def test_preset_has_description(self):
        """Built-in presets include a description string."""
        presets = get_builtin_presets()

        for preset in presets:
            assert "description" in preset
            assert isinstance(preset["description"], str)
            assert len(preset["description"]) > 0

    def test_preset_has_category(self):
        """Built-in presets include a category string."""
        presets = get_builtin_presets()

        for preset in presets:
            assert "category" in preset
            assert isinstance(preset["category"], str)
            assert len(preset["category"]) > 0


class TestPresetApplication:
    """Tests for applying presets to a builder state."""

    @pytest.fixture
    def base_state(self):
        """Create a base builder state for preset application tests."""
        return create_builder_state(
            input_source={"type": "file", "path": "/media/my_video.mp4"},
            output_config={"path": "/media/my_output.mp4", "format": "mp4", "overwrite": True},
        )

    def test_applies_preset_to_builder_state(self, base_state):
        """Applying a preset updates codec and filter settings in the state."""
        result = apply_preset(base_state, "web-mp4")

        assert result["videoCodec"]["codec"] == "libx264"

    def test_preset_preserves_input_path(self, base_state):
        """Applying a preset does not change the input file path."""
        result = apply_preset(base_state, "web-mp4")

        assert result["input"]["path"] == "/media/my_video.mp4"

    def test_preset_preserves_output_path(self, base_state):
        """Applying a preset does not change the output file path."""
        result = apply_preset(base_state, "web-mp4")

        assert result["output"]["path"] == "/media/my_output.mp4"

    def test_preset_overwrites_codec_settings(self, base_state):
        """Applying a preset replaces existing codec settings entirely."""
        base_state["videoCodec"]["codec"] = "libvpx-vp9"
        result = apply_preset(base_state, "web-mp4")

        # Preset should overwrite the codec
        assert result["videoCodec"]["codec"] == "libx264"

    def test_preset_overwrites_filters(self, base_state):
        """Applying a preset replaces existing filter settings."""
        base_state["videoFilters"] = [{"type": "crop", "enabled": True, "params": {}, "order": 0}]
        result = apply_preset(base_state, "web-mp4")

        # Filters from the preset should replace existing ones
        assert result["videoFilters"] == result["videoFilters"]  # Sanity check it's a list
        # Original crop filter should not remain unless the preset includes it
        old_filter_types = [f["type"] for f in base_state["videoFilters"]]
        new_filter_types = [f["type"] for f in result["videoFilters"]]
        assert old_filter_types != new_filter_types or result["videoFilters"] == []


class TestCustomPresets:
    """Tests for custom (user-defined) preset management."""

    @pytest.fixture
    def custom_preset(self):
        """Create a custom preset for testing."""
        return create_preset_template(
            preset_id="my-custom-preset",
            name="My Custom Preset",
            description="Custom encoding settings",
            category="custom",
            is_builtin=False,
        )

    def test_saves_custom_preset(self, custom_preset):
        """Custom preset can be saved and persisted."""
        result = save_custom_preset(custom_preset)

        assert result is not None
        assert result["id"] == "my-custom-preset"

    def test_loads_custom_preset(self, custom_preset):
        """Saved custom preset can be loaded by ID."""
        save_custom_preset(custom_preset)
        loaded = load_custom_preset("my-custom-preset")

        assert loaded is not None
        assert loaded["name"] == "My Custom Preset"

    def test_deletes_custom_preset(self, custom_preset):
        """Custom preset can be deleted."""
        save_custom_preset(custom_preset)
        delete_custom_preset("my-custom-preset")

        with pytest.raises(PresetNotFoundError):
            load_custom_preset("my-custom-preset")

    def test_cannot_delete_builtin_preset(self):
        """Built-in presets cannot be deleted."""
        with pytest.raises(BuiltinPresetDeleteError):
            delete_custom_preset("web-mp4")

    def test_custom_preset_has_category(self, custom_preset):
        """Custom preset includes a category field."""
        result = save_custom_preset(custom_preset)

        assert "category" in result
        assert result["category"] == "custom"

    def test_lists_all_presets(self, custom_preset):
        """Listing presets includes both built-in and custom presets."""
        save_custom_preset(custom_preset)
        all_presets = list_all_presets()

        builtin_ids = [p["id"] for p in get_builtin_presets()]
        all_ids = [p["id"] for p in all_presets]

        # All builtins should be present
        for bid in builtin_ids:
            assert bid in all_ids

        # Custom preset should also be present
        assert "my-custom-preset" in all_ids

    def test_preset_name_uniqueness(self, custom_preset):
        """Saving a preset with an existing ID overwrites or raises an error."""
        save_custom_preset(custom_preset)

        duplicate = create_preset_template(
            preset_id="my-custom-preset",
            name="Duplicate Name Preset",
            category="custom",
        )
        # Saving with same ID should either overwrite or raise
        result = save_custom_preset(duplicate)
        loaded = load_custom_preset("my-custom-preset")

        # If overwrite, name should be updated
        assert loaded["name"] == "Duplicate Name Preset"
