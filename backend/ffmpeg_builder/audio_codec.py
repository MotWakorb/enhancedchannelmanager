"""Audio codec validation and flag generation."""

import re
from typing import Dict, List

from ffmpeg_builder.common import ValidationResult

# Type alias
AudioCodecSettings = Dict

KNOWN_AUDIO_CODECS = {
    "aac", "libmp3lame", "libvorbis", "libopus", "ac3", "eac3",
    "flac", "pcm_s16le", "pcm_s24le",
    "copy",
}

LOSSLESS_CODECS = {"flac", "pcm_s16le", "pcm_s24le"}

VALID_CHANNEL_LAYOUTS = {"mono", "stereo", "2.1", "3.0", "4.0", "5.0", "5.1", "6.1", "7.1"}

BITRATE_RE = re.compile(r"^\d+[kKmM]$")


def validate_audio_codec(settings: dict) -> ValidationResult:
    """Validate audio codec settings dict."""
    result = ValidationResult()
    codec = settings.get("codec", "")

    if codec not in KNOWN_AUDIO_CODECS:
        result.add_error(f"Unknown audio codec '{codec}'")
        return result

    if codec == "copy":
        return result

    # Bitrate validation
    bitrate = settings.get("bitrate")
    if bitrate is not None and not BITRATE_RE.match(str(bitrate)):
        result.add_error(f"Invalid audio bitrate format '{bitrate}'")

    # Sample rate validation
    sample_rate = settings.get("sampleRate")
    if sample_rate is not None:
        if sample_rate <= 0:
            result.add_error(f"Sample rate must be positive, got {sample_rate}")

    # Channels validation
    channels = settings.get("channels")
    if channels is not None:
        if channels < 1 or channels > 8:
            result.add_error(f"Channel count {channels} out of range (1-8)")

    # Channel layout
    channel_layout = settings.get("channel_layout")
    if channel_layout is not None:
        if channel_layout not in VALID_CHANNEL_LAYOUTS:
            result.add_error(f"Unknown channel layout '{channel_layout}'")

    return result


def generate_audio_codec_flags(settings: dict) -> List[str]:
    """Generate ffmpeg audio codec flags from settings dict."""
    flags: List[str] = []
    codec = settings.get("codec", "aac")

    flags.extend(["-c:a", codec])

    # Stream copy — no encoding options
    if codec == "copy":
        return flags

    # Bitrate
    bitrate = settings.get("bitrate")
    if bitrate and codec not in LOSSLESS_CODECS:
        flags.extend(["-b:a", str(bitrate)])

    # Sample rate
    sample_rate = settings.get("sampleRate")
    if sample_rate is not None:
        flags.extend(["-ar", str(sample_rate)])

    # Channels
    channels = settings.get("channels")
    if channels is not None:
        flags.extend(["-ac", str(channels)])

    # Channel layout
    channel_layout = settings.get("channel_layout")
    if channel_layout:
        flags.extend(["-channel_layout", channel_layout])

    # Profile
    profile = settings.get("profile")
    if profile:
        flags.extend(["-profile:a", profile])

    return flags
