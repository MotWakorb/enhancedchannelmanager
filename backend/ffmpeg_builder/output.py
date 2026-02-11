"""Output configuration validation and flag generation."""

import os
from typing import Dict, List

from ffmpeg_builder.common import ValidationResult

# Type alias
OutputConfig = Dict

VALID_FORMATS = {
    "mp4", "mkv", "webm", "ts", "flv", "avi", "mov", "ogg",
    "hls", "dash", "null",
}

# Map of format -> typical file extension(s)
FORMAT_EXTENSIONS = {
    "mp4": {"mp4", "m4v"},
    "mkv": {"mkv"},
    "webm": {"webm"},
    "ts": {"ts", "m2ts", "mts"},
    "flv": {"flv"},
    "avi": {"avi"},
    "mov": {"mov"},
    "ogg": {"ogg", "ogv"},
    "hls": {"m3u8"},
    "dash": {"mpd"},
    "null": set(),
}


def validate_output_config(config: dict) -> ValidationResult:
    """Validate an output configuration dict."""
    result = ValidationResult()
    path = config.get("path", "")
    fmt = config.get("format", "")

    if not path:
        result.add_error("Output path is required")
        return result

    if fmt and fmt not in VALID_FORMATS:
        result.add_error(f"Invalid output format '{fmt}'. Valid: {', '.join(sorted(VALID_FORMATS))}")
        return result

    # Warn on extension/format mismatch
    if fmt and path and fmt != "null":
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        expected_exts = FORMAT_EXTENSIONS.get(fmt, set())
        if ext and expected_exts and ext not in expected_exts:
            result.add_warning(
                f"File extension '.{ext}' does not match format '{fmt}' "
                f"(expected: {', '.join('.' + e for e in expected_exts)})"
            )

    return result


def generate_output_flags(config: dict) -> List[str]:
    """Generate ffmpeg output flags from an output config dict."""
    flags: List[str] = []
    path = config.get("path", "")
    fmt = config.get("format", "")
    overwrite = config.get("overwrite", False)

    # Overwrite flag
    if overwrite:
        flags.append("-y")

    # Format
    if fmt:
        flags.extend(["-f", fmt])

    # Movflags (MP4 only)
    movflags = config.get("movflags")
    if movflags and fmt in ("mp4", "mov"):
        flags.extend(["-movflags", "+".join(movflags)])

    # HLS options
    if fmt == "hls":
        hls_time = config.get("hls_time")
        if hls_time is not None:
            flags.extend(["-hls_time", str(hls_time)])
        hls_list_size = config.get("hls_list_size")
        if hls_list_size is not None:
            flags.extend(["-hls_list_size", str(hls_list_size)])

    # DASH options
    if fmt == "dash":
        seg_duration = config.get("seg_duration")
        if seg_duration is not None:
            flags.extend(["-seg_duration", str(seg_duration)])

    # Output path is always last
    flags.append(path)

    return flags
