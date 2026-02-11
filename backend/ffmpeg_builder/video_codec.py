"""Video codec validation and flag generation."""

import re
from typing import Dict, List

from ffmpeg_builder.common import ValidationResult

# Type alias
VideoCodecSettings = Dict

KNOWN_VIDEO_CODECS = {
    # Software
    "libx264", "libx265", "libvpx-vp9", "libaom-av1", "libsvtav1",
    # NVIDIA NVENC
    "h264_nvenc", "hevc_nvenc",
    # Intel QSV
    "h264_qsv", "hevc_qsv",
    # VAAPI
    "h264_vaapi", "hevc_vaapi",
    # Special
    "copy",
}

NVENC_CODECS = {"h264_nvenc", "hevc_nvenc"}
QSV_CODECS = {"h264_qsv", "hevc_qsv"}
VAAPI_CODECS = {"h264_vaapi", "hevc_vaapi"}

BITRATE_RE = re.compile(r"^\d+[kKmM]$")


def validate_video_codec(settings: dict) -> ValidationResult:
    """Validate video codec settings dict."""
    result = ValidationResult()
    codec = settings.get("codec", "")

    if codec not in KNOWN_VIDEO_CODECS:
        result.add_error(f"Unknown video codec '{codec}'")
        return result

    if codec == "copy":
        return result

    # CRF validation
    crf = settings.get("crf")
    if crf is not None:
        if crf < 0:
            result.add_error(f"CRF value {crf} is below minimum (0)")
        elif crf > 51:
            result.add_error(f"CRF value {crf} exceeds maximum (51)")

    # Bitrate validation
    bitrate = settings.get("bitrate")
    if bitrate is not None:
        if not BITRATE_RE.match(str(bitrate)):
            result.add_error(
                f"Invalid bitrate format '{bitrate}'. Use e.g. '5000k' or '5M'"
            )
        elif str(bitrate).startswith("-"):
            result.add_error(f"Bitrate cannot be negative: '{bitrate}'")

    return result


def generate_video_codec_flags(settings: dict) -> List[str]:
    """Generate ffmpeg video codec flags from settings dict."""
    flags: List[str] = []
    codec = settings.get("codec", "libx264")

    flags.extend(["-c:v", codec])

    # Stream copy — no encoding options
    if codec == "copy":
        return flags

    # Preset
    preset = settings.get("preset")
    if preset:
        flags.extend(["-preset", preset])

    # Rate control
    rate_control = settings.get("rateControl", "crf")
    crf = settings.get("crf")
    bitrate = settings.get("bitrate")
    max_bitrate = settings.get("maxBitrate")

    if rate_control == "crf" and crf is not None:
        flags.extend(["-crf", str(crf)])
    elif rate_control in ("cbr", "vbr") and bitrate:
        flags.extend(["-b:v", str(bitrate)])
        if max_bitrate:
            flags.extend(["-maxrate", str(max_bitrate)])
        bufsize = settings.get("bufsize")
        if bufsize:
            flags.extend(["-bufsize", str(bufsize)])

    # Profile
    profile = settings.get("profile")
    if profile:
        flags.extend(["-profile:v", profile])

    # Level
    level = settings.get("level")
    if level:
        flags.extend(["-level", level])

    # Tune
    tune = settings.get("tune")
    if tune:
        flags.extend(["-tune", tune])

    # Pixel format
    pix_fmt = settings.get("pixelFormat")
    if pix_fmt:
        flags.extend(["-pix_fmt", pix_fmt])

    # GOP / keyframe interval
    keyframe_interval = settings.get("keyframe_interval")
    if keyframe_interval is not None:
        flags.extend(["-g", str(keyframe_interval)])

    # B-frames
    bframes = settings.get("bframes")
    if bframes is not None:
        flags.extend(["-bf", str(bframes)])

    # --- NVENC-specific ---
    if codec in NVENC_CODECS:
        rc = settings.get("rc")
        if rc:
            flags.extend(["-rc", rc])
        if settings.get("spatial_aq"):
            flags.extend(["-spatial-aq", "1"])
        if settings.get("temporal_aq"):
            flags.extend(["-temporal-aq", "1"])
        cq = settings.get("cq")
        if cq is not None:
            flags.extend(["-cq", str(cq)])
        b_ref_mode = settings.get("b_ref_mode")
        if b_ref_mode:
            flags.extend(["-b_ref_mode", b_ref_mode])

    # --- QSV-specific ---
    if codec in QSV_CODECS:
        gq = settings.get("global_quality")
        if gq is not None:
            flags.extend(["-global_quality", str(gq)])
        if settings.get("look_ahead"):
            flags.extend(["-look_ahead", "1"])

    # --- VAAPI-specific ---
    if codec in VAAPI_CODECS:
        quality = settings.get("quality")
        if quality is not None:
            flags.extend(["-quality", str(quality)])
        compression_level = settings.get("compression_level")
        if compression_level is not None:
            flags.extend(["-compression_level", str(compression_level)])

    return flags
