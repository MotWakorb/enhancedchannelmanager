"""
Unit tests for the FFMPEG Builder video filters module.

Tests video filter validation, individual filter generation, filter chain
composition, and hardware-accelerated filter variants.
These are TDD tests -- they will FAIL until the backend modules are implemented.
"""
import pytest

from ffmpeg_builder.video_filters import (
    validate_video_filter,
    validate_video_filter_chain,
    generate_video_filter_flags,
    generate_single_video_filter,
    KNOWN_VIDEO_FILTERS,
)

from tests.fixtures.ffmpeg_factories import (
    create_video_filter,
    create_hwaccel_config,
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestVideoFilterValidation:
    """Tests for video filter validation logic."""

    def test_validates_known_filter_type(self):
        """Known filter types such as 'scale' are accepted."""
        vf = create_video_filter(filter_type="scale")
        result = validate_video_filter(vf)

        assert result.valid is True
        assert len(result.errors) == 0

    def test_rejects_unknown_filter(self):
        """An unrecognised filter type is rejected."""
        vf = create_video_filter(filter_type="bogus_filter_xyz")
        result = validate_video_filter(vf)

        assert result.valid is False
        assert any("filter" in e.lower() for e in result.errors)

    def test_validates_scale_params(self):
        """Scale filter requires valid width and height."""
        vf = create_video_filter(
            filter_type="scale",
            params={"width": 1920, "height": 1080},
        )
        result = validate_video_filter(vf)

        assert result.valid is True

    def test_validates_crop_params(self):
        """Crop filter requires valid width, height, x, and y."""
        vf = create_video_filter(
            filter_type="crop",
            params={"width": 1280, "height": 720, "x": 0, "y": 0},
        )
        result = validate_video_filter(vf)

        assert result.valid is True

    def test_validates_fps_positive(self):
        """FPS filter value must be a positive number."""
        vf = create_video_filter(
            filter_type="fps",
            params={"fps": 0},
        )
        result = validate_video_filter(vf)

        assert result.valid is False
        assert any("fps" in e.lower() for e in result.errors)

    def test_validates_filter_order_unique(self):
        """Filter chain rejects duplicate order values."""
        filters = [
            create_video_filter(filter_type="scale", order=0),
            create_video_filter(filter_type="fps", order=0, params={"fps": 30}),
        ]
        result = validate_video_filter_chain(filters)

        assert result.valid is False
        assert any("order" in e.lower() or "duplicate" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# Individual filter generation
# ---------------------------------------------------------------------------

class TestVideoFilterGeneration:
    """Tests for generating individual video filter strings."""

    def test_generates_scale_filter(self):
        """Scale filter generates 'scale=1920:1080'."""
        vf = create_video_filter(
            filter_type="scale",
            params={"width": 1920, "height": 1080},
        )
        fstr = generate_single_video_filter(vf)

        assert "scale=" in fstr
        assert "1920" in fstr
        assert "1080" in fstr

    def test_generates_crop_filter(self):
        """Crop filter generates 'crop=w:h:x:y'."""
        vf = create_video_filter(
            filter_type="crop",
            params={"width": 1280, "height": 720, "x": 320, "y": 180},
        )
        fstr = generate_single_video_filter(vf)

        assert "crop=" in fstr
        assert "1280" in fstr
        assert "720" in fstr

    def test_generates_fps_filter(self):
        """FPS filter generates 'fps=30'."""
        vf = create_video_filter(
            filter_type="fps",
            params={"fps": 30},
        )
        fstr = generate_single_video_filter(vf)

        assert "fps=" in fstr
        assert "30" in fstr

    def test_generates_deinterlace_filter(self):
        """Deinterlace filter generates 'yadif' filter string."""
        vf = create_video_filter(
            filter_type="deinterlace",
            params={"mode": "yadif"},
        )
        fstr = generate_single_video_filter(vf)

        assert "yadif" in fstr

    def test_generates_denoise_filter(self):
        """Denoise filter generates 'hqdn3d' or 'nlmeans' filter string."""
        vf = create_video_filter(
            filter_type="denoise",
            params={"method": "hqdn3d"},
        )
        fstr = generate_single_video_filter(vf)

        assert "hqdn3d" in fstr

    def test_generates_drawtext_filter(self):
        """Drawtext filter generates drawtext= with text parameter."""
        vf = create_video_filter(
            filter_type="drawtext",
            params={"text": "Hello World", "fontsize": 24, "x": 10, "y": 10},
        )
        fstr = generate_single_video_filter(vf)

        assert "drawtext=" in fstr
        assert "Hello World" in fstr or "Hello" in fstr

    def test_generates_rotate_filter(self):
        """Rotate filter generates 'rotate=' or 'transpose=' filter string."""
        vf = create_video_filter(
            filter_type="rotate",
            params={"angle": 90},
        )
        fstr = generate_single_video_filter(vf)

        assert "rotate" in fstr or "transpose" in fstr

    def test_generates_flip_filters(self):
        """Flip filter generates 'hflip' or 'vflip' filter string."""
        vf_h = create_video_filter(
            filter_type="flip",
            params={"direction": "horizontal"},
        )
        fstr_h = generate_single_video_filter(vf_h)
        assert "hflip" in fstr_h

        vf_v = create_video_filter(
            filter_type="flip",
            params={"direction": "vertical"},
        )
        fstr_v = generate_single_video_filter(vf_v)
        assert "vflip" in fstr_v


# ---------------------------------------------------------------------------
# Filter chain composition
# ---------------------------------------------------------------------------

class TestFilterChain:
    """Tests for composing multiple video filters into a -vf chain."""

    def test_chains_multiple_filters(self):
        """Multiple enabled filters produce a comma-separated -vf value."""
        filters = [
            create_video_filter(filter_type="scale", order=0,
                                params={"width": 1920, "height": 1080}),
            create_video_filter(filter_type="fps", order=1,
                                params={"fps": 30}),
        ]
        flags = generate_video_filter_flags(filters)

        assert "-vf" in flags
        vf_idx = flags.index("-vf")
        vf_value = flags[vf_idx + 1]
        assert "," in vf_value
        assert "scale=" in vf_value
        assert "fps=" in vf_value

    def test_respects_filter_order(self):
        """Filters are applied in the order defined by their 'order' field."""
        filters = [
            create_video_filter(filter_type="fps", order=1,
                                params={"fps": 30}),
            create_video_filter(filter_type="scale", order=0,
                                params={"width": 1920, "height": 1080}),
        ]
        flags = generate_video_filter_flags(filters)

        vf_idx = flags.index("-vf")
        vf_value = flags[vf_idx + 1]
        # scale (order=0) should appear before fps (order=1)
        scale_pos = vf_value.index("scale=")
        fps_pos = vf_value.index("fps=")
        assert scale_pos < fps_pos

    def test_skips_disabled_filters(self):
        """Disabled filters are excluded from the chain."""
        filters = [
            create_video_filter(filter_type="scale", order=0, enabled=True,
                                params={"width": 1920, "height": 1080}),
            create_video_filter(filter_type="fps", order=1, enabled=False,
                                params={"fps": 30}),
        ]
        flags = generate_video_filter_flags(filters)

        vf_idx = flags.index("-vf")
        vf_value = flags[vf_idx + 1]
        assert "scale=" in vf_value
        assert "fps=" not in vf_value

    def test_empty_chain_no_vf_flag(self):
        """An empty filter list produces no -vf flag at all."""
        flags = generate_video_filter_flags([])

        assert "-vf" not in flags

    def test_single_filter_no_comma(self):
        """A single filter produces a -vf value without commas."""
        filters = [
            create_video_filter(filter_type="scale", order=0,
                                params={"width": 1280, "height": 720}),
        ]
        flags = generate_video_filter_flags(filters)

        vf_idx = flags.index("-vf")
        vf_value = flags[vf_idx + 1]
        assert "," not in vf_value
        assert "scale=" in vf_value


# ---------------------------------------------------------------------------
# Hardware-accelerated filters
# ---------------------------------------------------------------------------

class TestHardwareFilters:
    """Tests for hardware-accelerated video filter variants."""

    def test_vaapi_inserts_format_hwupload(self):
        """VAAPI scale inserts format=nv12|vaapi,hwupload before the filter."""
        hwaccel = create_hwaccel_config(api="vaapi")
        vf = create_video_filter(
            filter_type="scale",
            params={"width": 1920, "height": 1080},
        )
        filters = [vf]
        flags = generate_video_filter_flags(filters, hwaccel=hwaccel)

        vf_idx = flags.index("-vf")
        vf_value = flags[vf_idx + 1]
        assert "hwupload" in vf_value
        assert "scale_vaapi" in vf_value or "scale" in vf_value

    def test_cuda_scale_filter(self):
        """CUDA scale uses scale_cuda or scale_npp filter."""
        hwaccel = create_hwaccel_config(api="cuda")
        vf = create_video_filter(
            filter_type="scale",
            params={"width": 1920, "height": 1080},
        )
        filters = [vf]
        flags = generate_video_filter_flags(filters, hwaccel=hwaccel)

        vf_idx = flags.index("-vf")
        vf_value = flags[vf_idx + 1]
        assert "scale_cuda" in vf_value or "scale_npp" in vf_value

    def test_custom_filter_passthrough(self):
        """Custom filter type passes the raw filter string unchanged."""
        vf = create_video_filter(
            filter_type="custom",
            params={"raw": "eq=brightness=0.06:contrast=1.5"},
        )
        fstr = generate_single_video_filter(vf)

        assert "eq=brightness=0.06:contrast=1.5" in fstr
