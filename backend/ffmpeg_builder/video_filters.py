"""Video filter validation, generation, and chain composition."""

from typing import Dict, List, Optional

from ffmpeg_builder.common import ValidationResult

KNOWN_VIDEO_FILTERS = {
    "scale", "crop", "pad", "fps",
    "deinterlace", "denoise", "sharpen",
    "rotate", "flip", "transpose",
    "overlay", "drawtext", "colorbalance",
    "format", "hwupload", "hwdownload",
    "hwupload_vaapi",
    "custom",
}


def validate_video_filter(vf: dict) -> ValidationResult:
    """Validate a single video filter dict."""
    result = ValidationResult()
    ftype = vf.get("type", "")

    if ftype not in KNOWN_VIDEO_FILTERS:
        result.add_error(f"Unknown video filter type '{ftype}'")
        return result

    params = vf.get("params", {})

    if ftype == "fps":
        fps = params.get("fps", 0)
        if fps <= 0:
            result.add_error(f"FPS must be positive, got {fps}")

    if ftype == "custom":
        raw = params.get("raw", "")
        if not raw:
            result.add_error("Custom video filter requires a non-empty 'raw' string")

    return result


def validate_video_filter_chain(filters: list) -> ValidationResult:
    """Validate a list of video filters (the full chain)."""
    result = ValidationResult()

    orders = []
    for vf in filters:
        sub = validate_video_filter(vf)
        result.merge(sub)
        orders.append(vf.get("order", 0))

    # Check for duplicate order values
    if len(orders) != len(set(orders)):
        result.add_error("Duplicate order values in video filter chain")

    return result


def generate_single_video_filter(vf: dict, hwaccel: Optional[dict] = None) -> str:
    """Generate a single video filter string from a filter dict."""
    ftype = vf.get("type", "")
    params = vf.get("params", {})

    if ftype == "scale":
        w = params.get("width", 1920)
        h = params.get("height", 1080)
        if hwaccel:
            api = hwaccel.get("api", "none")
            if api == "cuda":
                return f"scale_cuda={w}:{h}"
            elif api == "vaapi":
                return f"scale_vaapi=w={w}:h={h}"
        return f"scale={w}:{h}"

    if ftype == "crop":
        w = params.get("width", 0)
        h = params.get("height", 0)
        x = params.get("x", 0)
        y = params.get("y", 0)
        return f"crop={w}:{h}:{x}:{y}"

    if ftype == "fps":
        fps = params.get("fps", 30)
        return f"fps={fps}"

    if ftype == "deinterlace":
        mode = params.get("mode", "yadif")
        return mode

    if ftype == "denoise":
        method = params.get("method", "hqdn3d")
        return method

    if ftype == "drawtext":
        text = params.get("text", "")
        fontsize = params.get("fontsize", 24)
        x = params.get("x", 10)
        y = params.get("y", 10)
        return f"drawtext=text='{text}':fontsize={fontsize}:x={x}:y={y}"

    if ftype == "rotate":
        angle = params.get("angle", 0)
        # 90-degree rotations use transpose
        if angle in (90, 180, 270):
            if angle == 90:
                return "transpose=1"
            elif angle == 180:
                return "transpose=1,transpose=1"
            else:
                return "transpose=2"
        return f"rotate={angle}*PI/180"

    if ftype == "flip":
        direction = params.get("direction", "horizontal")
        return "hflip" if direction == "horizontal" else "vflip"

    if ftype == "format":
        pix_fmt = params.get("pix_fmt", "nv12")
        return f"format={pix_fmt}"

    if ftype == "hwupload":
        return "hwupload"

    if ftype == "hwupload_vaapi":
        return "hwupload"

    if ftype == "custom":
        return params.get("raw", "")

    # Fallback: filter_type=params
    parts = [f"{k}={v}" for k, v in params.items()]
    return f"{ftype}={':'.join(parts)}" if parts else ftype


def generate_video_filter_flags(
    filters: list,
    hwaccel: Optional[dict] = None,
) -> List[str]:
    """Generate -vf flags from a list of video filter dicts."""
    enabled = [f for f in filters if f.get("enabled", True)]
    if not enabled:
        return []

    # Sort by order
    enabled.sort(key=lambda f: f.get("order", 0))

    parts: List[str] = []

    # For VAAPI, prepend format/hwupload if needed
    if hwaccel and hwaccel.get("api") == "vaapi":
        parts.append("format=nv12")
        parts.append("hwupload")

    for vf in enabled:
        parts.append(generate_single_video_filter(vf, hwaccel=hwaccel))

    return ["-vf", ",".join(parts)]
