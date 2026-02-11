"""Input source validation and flag generation."""

import os
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

from ffmpeg_builder.common import ValidationResult

# Type alias for clarity — inputs are plain dicts from the factories.
InputSource = Dict

VALID_URL_SCHEMES = {"http", "https", "rtsp", "rtmp", "rtp", "udp", "tcp", "srt", "mms"}


def validate_input_source(source: dict) -> ValidationResult:
    """Validate an input source configuration dict."""
    result = ValidationResult()
    source_type = source.get("type", "file")
    path = source.get("path", "")

    if not path:
        result.add_error("Input path is required")
        return result

    if source_type == "file":
        # File existence is checked at runtime, not during config validation.
        # Tests mock os.path.exists when they want to exercise this path.
        pass
    elif source_type == "url":
        parsed = urlparse(path)
        if parsed.scheme not in VALID_URL_SCHEMES:
            result.add_error(
                f"Unsupported URL scheme '{parsed.scheme}'. "
                f"Supported: {', '.join(sorted(VALID_URL_SCHEMES))}"
            )
    elif source_type == "device":
        pass  # Device paths are accepted as-is
    elif source_type == "pipe":
        pass  # Pipe inputs (pipe:0, pipe:1) are accepted as-is

    return result


def generate_input_flags(source: dict) -> List[str]:
    """Generate ffmpeg input flags from an input source dict."""
    flags: List[str] = []
    source_type = source.get("type", "file")
    path = source.get("path", "")

    # HW accel flags come first
    hwaccel_flags = generate_hwaccel_flags(source)
    if hwaccel_flags:
        flags.extend(hwaccel_flags)

    # Seek (-ss) before -i for input seeking
    start_time = source.get("start_time")
    if start_time:
        flags.extend(["-ss", start_time])

    # Duration
    duration = source.get("duration")
    if duration:
        flags.extend(["-t", duration])

    # Stream loop
    stream_loop = source.get("stream_loop")
    if stream_loop is not None:
        flags.extend(["-stream_loop", str(stream_loop)])

    # Format override (-f before -i)
    fmt = source.get("format")
    if fmt:
        flags.extend(["-f", fmt])

    # The input itself
    flags.extend(["-i", path])

    return flags


def generate_hwaccel_flags(source: dict) -> List[str]:
    """Generate hardware acceleration flags for an input source."""
    hwaccel = source.get("hwaccel")
    if not hwaccel:
        return []

    api = hwaccel.get("api", "none")
    if api == "none":
        return []

    flags: List[str] = []

    if api == "vaapi":
        device = hwaccel.get("device")
        if device:
            flags.extend(["-vaapi_device", device])
        flags.extend(["-hwaccel", "vaapi"])
    elif api == "qsv":
        flags.extend(["-hwaccel", "qsv"])
        device = hwaccel.get("device")
        if device:
            flags.extend(["-qsv_device", device])
    else:
        # cuda or generic
        flags.extend(["-hwaccel", api])

    output_format = hwaccel.get("outputFormat")
    if output_format:
        flags.extend(["-hwaccel_output_format", output_format])

    return flags
