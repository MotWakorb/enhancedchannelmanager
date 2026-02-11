"""
Unit tests for the FFMPEG Builder audio codec module.

Tests audio codec validation, flag generation, and stream copy behavior.
These are TDD tests -- they will FAIL until the backend modules are implemented.
"""
import pytest

from ffmpeg_builder.audio_codec import (
    validate_audio_codec,
    generate_audio_codec_flags,
    AudioCodecSettings,
    KNOWN_AUDIO_CODECS,
)

from tests.fixtures.ffmpeg_factories import (
    create_audio_codec_settings,
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestAudioCodecValidation:
    """Tests for audio codec validation logic."""

    def test_validates_known_codec(self):
        """Known audio codecs such as aac are accepted."""
        settings = create_audio_codec_settings(codec="aac")
        result = validate_audio_codec(settings)

        assert result.valid is True
        assert len(result.errors) == 0

    def test_rejects_unknown_codec(self):
        """An unrecognised audio codec name is rejected."""
        settings = create_audio_codec_settings(codec="libfakeaudio999")
        result = validate_audio_codec(settings)

        assert result.valid is False
        assert any("codec" in e.lower() for e in result.errors)

    def test_validates_bitrate_format(self):
        """Bitrate strings like '192k' or '320k' are accepted."""
        for br in ("128k", "192k", "256k", "320k"):
            settings = create_audio_codec_settings(bitrate=br)
            result = validate_audio_codec(settings)

            assert result.valid is True, f"Bitrate '{br}' should be valid"

    def test_validates_sample_rate(self):
        """Standard sample rates (44100, 48000, etc.) are accepted."""
        for sr in (22050, 44100, 48000, 96000):
            settings = create_audio_codec_settings(sample_rate=sr)
            result = validate_audio_codec(settings)

            assert result.valid is True, f"Sample rate {sr} should be valid"

    def test_validates_channels_range(self):
        """Channel counts within a valid range (1-8) are accepted."""
        for ch in (1, 2, 6, 8):
            settings = create_audio_codec_settings(channels=ch)
            result = validate_audio_codec(settings)

            assert result.valid is True, f"Channel count {ch} should be valid"

    def test_validates_channel_layout(self):
        """Named channel layouts like 'stereo' and '5.1' are accepted."""
        for layout in ("mono", "stereo", "5.1", "7.1"):
            settings = create_audio_codec_settings(channel_layout=layout)
            result = validate_audio_codec(settings)

            assert result.valid is True, f"Layout '{layout}' should be valid"


# ---------------------------------------------------------------------------
# Flag generation
# ---------------------------------------------------------------------------

class TestAudioCodecGeneration:
    """Tests for audio encoder flag generation."""

    def test_generates_aac_flags(self):
        """AAC codec generates -c:a aac."""
        settings = create_audio_codec_settings(codec="aac")
        flags = generate_audio_codec_flags(settings)

        assert "-c:a" in flags
        assert "aac" in flags

    def test_generates_mp3_flags(self):
        """MP3 codec generates -c:a libmp3lame."""
        settings = create_audio_codec_settings(codec="libmp3lame")
        flags = generate_audio_codec_flags(settings)

        assert "-c:a" in flags
        assert "libmp3lame" in flags

    def test_generates_opus_flags(self):
        """Opus codec generates -c:a libopus."""
        settings = create_audio_codec_settings(codec="libopus")
        flags = generate_audio_codec_flags(settings)

        assert "-c:a" in flags
        assert "libopus" in flags

    def test_generates_vorbis_flags(self):
        """Vorbis codec generates -c:a libvorbis."""
        settings = create_audio_codec_settings(codec="libvorbis")
        flags = generate_audio_codec_flags(settings)

        assert "-c:a" in flags
        assert "libvorbis" in flags

    def test_generates_ac3_flags(self):
        """AC3 codec generates -c:a ac3."""
        settings = create_audio_codec_settings(codec="ac3")
        flags = generate_audio_codec_flags(settings)

        assert "-c:a" in flags
        assert "ac3" in flags

    def test_generates_flac_flags(self):
        """FLAC codec generates -c:a flac."""
        settings = create_audio_codec_settings(codec="flac")
        flags = generate_audio_codec_flags(settings)

        assert "-c:a" in flags
        assert "flac" in flags

    def test_generates_pcm_flags(self):
        """PCM codec generates -c:a pcm_s16le."""
        settings = create_audio_codec_settings(codec="pcm_s16le")
        flags = generate_audio_codec_flags(settings)

        assert "-c:a" in flags
        assert "pcm_s16le" in flags

    def test_generates_bitrate_flag(self):
        """Bitrate generates -b:a flag."""
        settings = create_audio_codec_settings(bitrate="192k")
        flags = generate_audio_codec_flags(settings)

        assert "-b:a" in flags
        ba_idx = flags.index("-b:a")
        assert flags[ba_idx + 1] == "192k"

    def test_generates_sample_rate_flag(self):
        """Sample rate generates -ar flag."""
        settings = create_audio_codec_settings(sample_rate=48000)
        flags = generate_audio_codec_flags(settings)

        assert "-ar" in flags
        ar_idx = flags.index("-ar")
        assert str(flags[ar_idx + 1]) == "48000"

    def test_generates_channels_flag(self):
        """Channel count generates -ac flag."""
        settings = create_audio_codec_settings(channels=2)
        flags = generate_audio_codec_flags(settings)

        assert "-ac" in flags
        ac_idx = flags.index("-ac")
        assert str(flags[ac_idx + 1]) == "2"

    def test_generates_channel_layout_flag(self):
        """Channel layout generates -channel_layout flag."""
        settings = create_audio_codec_settings(channel_layout="5.1")
        flags = generate_audio_codec_flags(settings)

        assert "-channel_layout" in flags
        cl_idx = flags.index("-channel_layout")
        assert flags[cl_idx + 1] == "5.1"

    def test_generates_profile_flag_aac(self):
        """AAC profile generates -profile:a flag."""
        settings = create_audio_codec_settings(codec="aac", profile="aac_low")
        flags = generate_audio_codec_flags(settings)

        assert "-profile:a" in flags
        pa_idx = flags.index("-profile:a")
        assert flags[pa_idx + 1] == "aac_low"


# ---------------------------------------------------------------------------
# Stream copy
# ---------------------------------------------------------------------------

class TestAudioStreamCopy:
    """Tests for audio stream copy (passthrough) mode."""

    def test_generates_copy_flag(self):
        """Stream copy generates -c:a copy."""
        settings = create_audio_codec_settings(codec="copy")
        flags = generate_audio_codec_flags(settings)

        assert "-c:a" in flags
        assert "copy" in flags

    def test_copy_ignores_settings(self):
        """Stream copy ignores bitrate, sample rate, and channels."""
        settings = create_audio_codec_settings(
            codec="copy",
            bitrate="320k",
            sample_rate=96000,
            channels=6,
        )
        flags = generate_audio_codec_flags(settings)

        assert "-c:a" in flags
        assert "copy" in flags
        # None of the encoding options should appear
        assert "-b:a" not in flags
        assert "-ar" not in flags
        assert "-ac" not in flags
