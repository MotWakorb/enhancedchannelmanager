"""
Unit tests for the FFMPEG Builder audio filters module.

Tests audio filter validation, individual filter generation, filter chain
composition, and custom filter passthrough.
These are TDD tests -- they will FAIL until the backend modules are implemented.
"""
import pytest

from ffmpeg_builder.audio_filters import (
    validate_audio_filter,
    validate_audio_filter_chain,
    generate_audio_filter_flags,
    generate_single_audio_filter,
    KNOWN_AUDIO_FILTERS,
)

from tests.fixtures.ffmpeg_factories import (
    create_audio_filter,
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestAudioFilterValidation:
    """Tests for audio filter validation logic."""

    def test_validates_known_filter(self):
        """Known audio filters such as 'volume' are accepted."""
        af = create_audio_filter(filter_type="volume")
        result = validate_audio_filter(af)

        assert result.valid is True
        assert len(result.errors) == 0

    def test_rejects_unknown_filter(self):
        """An unrecognised audio filter type is rejected."""
        af = create_audio_filter(filter_type="bogus_audio_xyz")
        result = validate_audio_filter(af)

        assert result.valid is False
        assert any("filter" in e.lower() for e in result.errors)

    def test_validates_volume_range(self):
        """Volume level must be within a valid range (0.0 to 10.0)."""
        # Valid volume
        af_ok = create_audio_filter(
            filter_type="volume",
            params={"level": 1.5},
        )
        result_ok = validate_audio_filter(af_ok)
        assert result_ok.valid is True

        # Negative volume should be rejected
        af_neg = create_audio_filter(
            filter_type="volume",
            params={"level": -1.0},
        )
        result_neg = validate_audio_filter(af_neg)
        assert result_neg.valid is False
        assert any("volume" in e.lower() or "level" in e.lower() for e in result_neg.errors)

    def test_validates_loudnorm_params(self):
        """Loudnorm filter validates I, TP, and LRA parameters."""
        af = create_audio_filter(
            filter_type="loudnorm",
            params={"I": -24, "TP": -2.0, "LRA": 7},
        )
        result = validate_audio_filter(af)

        assert result.valid is True

    def test_validates_atempo_range(self):
        """Atempo value must be between 0.5 and 100.0."""
        # Valid range
        af_ok = create_audio_filter(
            filter_type="atempo",
            params={"tempo": 2.0},
        )
        result_ok = validate_audio_filter(af_ok)
        assert result_ok.valid is True

        # Below minimum
        af_low = create_audio_filter(
            filter_type="atempo",
            params={"tempo": 0.1},
        )
        result_low = validate_audio_filter(af_low)
        assert result_low.valid is False
        assert any("tempo" in e.lower() or "atempo" in e.lower() for e in result_low.errors)


# ---------------------------------------------------------------------------
# Individual filter generation
# ---------------------------------------------------------------------------

class TestAudioFilterGeneration:
    """Tests for generating individual audio filter strings."""

    def test_generates_volume_filter(self):
        """Volume filter generates 'volume=1.5'."""
        af = create_audio_filter(
            filter_type="volume",
            params={"level": 1.5},
        )
        fstr = generate_single_audio_filter(af)

        assert "volume=" in fstr
        assert "1.5" in fstr

    def test_generates_loudnorm_filter(self):
        """Loudnorm filter generates loudnorm=I=-24:TP=-2:LRA=7."""
        af = create_audio_filter(
            filter_type="loudnorm",
            params={"I": -24, "TP": -2.0, "LRA": 7},
        )
        fstr = generate_single_audio_filter(af)

        assert "loudnorm" in fstr
        assert "-24" in fstr

    def test_generates_aresample_filter(self):
        """Aresample filter generates 'aresample=48000'."""
        af = create_audio_filter(
            filter_type="aresample",
            params={"sample_rate": 48000},
        )
        fstr = generate_single_audio_filter(af)

        assert "aresample=" in fstr
        assert "48000" in fstr

    def test_generates_atempo_filter(self):
        """Atempo filter generates 'atempo=2.0'."""
        af = create_audio_filter(
            filter_type="atempo",
            params={"tempo": 2.0},
        )
        fstr = generate_single_audio_filter(af)

        assert "atempo=" in fstr
        assert "2.0" in fstr

    def test_generates_equalizer_filter(self):
        """Equalizer filter generates 'equalizer=f=1000:t=q:w=1:g=5'."""
        af = create_audio_filter(
            filter_type="equalizer",
            params={"frequency": 1000, "width_type": "q", "width": 1, "gain": 5},
        )
        fstr = generate_single_audio_filter(af)

        assert "equalizer=" in fstr
        assert "1000" in fstr

    def test_generates_highpass_filter(self):
        """Highpass filter generates 'highpass=f=200'."""
        af = create_audio_filter(
            filter_type="highpass",
            params={"frequency": 200},
        )
        fstr = generate_single_audio_filter(af)

        assert "highpass=" in fstr
        assert "200" in fstr

    def test_generates_lowpass_filter(self):
        """Lowpass filter generates 'lowpass=f=3000'."""
        af = create_audio_filter(
            filter_type="lowpass",
            params={"frequency": 3000},
        )
        fstr = generate_single_audio_filter(af)

        assert "lowpass=" in fstr
        assert "3000" in fstr


# ---------------------------------------------------------------------------
# Filter chain composition
# ---------------------------------------------------------------------------

class TestAudioFilterChain:
    """Tests for composing multiple audio filters into an -af chain."""

    def test_chains_multiple_audio_filters(self):
        """Multiple enabled filters produce a comma-separated -af value."""
        filters = [
            create_audio_filter(filter_type="volume", order=0,
                                params={"level": 1.5}),
            create_audio_filter(filter_type="loudnorm", order=1,
                                params={"I": -24, "TP": -2.0, "LRA": 7}),
        ]
        flags = generate_audio_filter_flags(filters)

        assert "-af" in flags
        af_idx = flags.index("-af")
        af_value = flags[af_idx + 1]
        assert "," in af_value
        assert "volume=" in af_value
        assert "loudnorm" in af_value

    def test_respects_order(self):
        """Filters are applied in the order defined by their 'order' field."""
        filters = [
            create_audio_filter(filter_type="loudnorm", order=1,
                                params={"I": -24, "TP": -2.0, "LRA": 7}),
            create_audio_filter(filter_type="volume", order=0,
                                params={"level": 1.5}),
        ]
        flags = generate_audio_filter_flags(filters)

        af_idx = flags.index("-af")
        af_value = flags[af_idx + 1]
        # volume (order=0) should appear before loudnorm (order=1)
        vol_pos = af_value.index("volume=")
        loud_pos = af_value.index("loudnorm")
        assert vol_pos < loud_pos

    def test_skips_disabled(self):
        """Disabled filters are excluded from the chain."""
        filters = [
            create_audio_filter(filter_type="volume", order=0, enabled=True,
                                params={"level": 1.5}),
            create_audio_filter(filter_type="loudnorm", order=1, enabled=False,
                                params={"I": -24, "TP": -2.0, "LRA": 7}),
        ]
        flags = generate_audio_filter_flags(filters)

        af_idx = flags.index("-af")
        af_value = flags[af_idx + 1]
        assert "volume=" in af_value
        assert "loudnorm" not in af_value

    def test_empty_chain_no_af_flag(self):
        """An empty filter list produces no -af flag at all."""
        flags = generate_audio_filter_flags([])

        assert "-af" not in flags


# ---------------------------------------------------------------------------
# Custom audio filter
# ---------------------------------------------------------------------------

class TestCustomAudioFilter:
    """Tests for custom/raw audio filter passthrough."""

    def test_custom_filter_passthrough(self):
        """Custom filter type passes the raw filter string unchanged."""
        af = create_audio_filter(
            filter_type="custom",
            params={"raw": "acompressor=threshold=-20dB:ratio=4"},
        )
        fstr = generate_single_audio_filter(af)

        assert "acompressor=threshold=-20dB:ratio=4" in fstr

    def test_validates_custom_syntax(self):
        """Custom filter with empty raw string is rejected."""
        af = create_audio_filter(
            filter_type="custom",
            params={"raw": ""},
        )
        result = validate_audio_filter(af)

        assert result.valid is False
        assert any("empty" in e.lower() or "raw" in e.lower() or "custom" in e.lower()
                    for e in result.errors)
