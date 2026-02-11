"""
Factory functions for creating FFMPEG Builder test data.

These factories create test objects (dicts and model instances) for
FFMPEG-related tests. Following the project's factory pattern from factories.py.
"""
import json
from datetime import datetime, timedelta
from typing import Optional, Any

# Counter for generating unique IDs
_ffmpeg_counter = {"value": 5000}


def _next_id() -> int:
    """Generate a unique incrementing ID for FFMPEG test data."""
    _ffmpeg_counter["value"] += 1
    return _ffmpeg_counter["value"]


def reset_ffmpeg_counter() -> None:
    """Reset the counter (useful between tests)."""
    _ffmpeg_counter["value"] = 5000


# -----------------------------------------------------------------------------
# Builder State Factories (dict-based, no ORM)
# -----------------------------------------------------------------------------

def create_input_source(
    source_type: str = "file",
    path: str = "/media/input.mp4",
    format: Optional[str] = None,
    hwaccel: Optional[dict] = None,
    start_time: Optional[str] = None,
    duration: Optional[str] = None,
    **kwargs
) -> dict:
    """Create an input source configuration dict.

    Args:
        source_type: Input type (file, url, device, pipe)
        path: File path, URL, or device path
        format: Force input format
        hwaccel: Hardware acceleration config
        start_time: Seek position (-ss)
        duration: Limit duration (-t)

    Returns:
        Input source configuration dict
    """
    source = {
        "type": source_type,
        "path": path,
    }
    if format:
        source["format"] = format
    if hwaccel:
        source["hwaccel"] = hwaccel
    if start_time:
        source["start_time"] = start_time
    if duration:
        source["duration"] = duration
    source.update(kwargs)
    return source


def create_output_config(
    path: str = "/media/output.mp4",
    format: str = "mp4",
    movflags: Optional[list] = None,
    overwrite: bool = True,
    **kwargs
) -> dict:
    """Create an output configuration dict.

    Args:
        path: Output file path
        format: Container format
        movflags: MP4 mov flags (e.g., ['faststart'])
        overwrite: Whether to overwrite existing file

    Returns:
        Output configuration dict
    """
    config = {
        "path": path,
        "format": format,
        "overwrite": overwrite,
    }
    if movflags:
        config["movflags"] = movflags
    config.update(kwargs)
    return config


def create_video_codec_settings(
    codec: str = "libx264",
    preset: str = "medium",
    rate_control: str = "crf",
    crf: Optional[int] = 23,
    bitrate: Optional[str] = None,
    max_bitrate: Optional[str] = None,
    pixel_format: str = "yuv420p",
    profile: Optional[str] = None,
    **kwargs
) -> dict:
    """Create video codec settings dict.

    Args:
        codec: Video encoder name
        preset: Encoder preset
        rate_control: Rate control mode
        crf: CRF value (for CRF mode)
        bitrate: Target bitrate (for CBR/VBR)
        max_bitrate: Maximum bitrate (for VBR)
        pixel_format: Output pixel format
        profile: Codec profile

    Returns:
        Video codec settings dict
    """
    settings = {
        "codec": codec,
        "preset": preset,
        "rateControl": rate_control,
        "pixelFormat": pixel_format,
    }
    if crf is not None and rate_control == "crf":
        settings["crf"] = crf
    if bitrate:
        settings["bitrate"] = bitrate
    if max_bitrate:
        settings["maxBitrate"] = max_bitrate
    if profile:
        settings["profile"] = profile
    settings.update(kwargs)
    return settings


def create_audio_codec_settings(
    codec: str = "aac",
    bitrate: str = "192k",
    sample_rate: Optional[int] = 48000,
    channels: Optional[int] = 2,
    **kwargs
) -> dict:
    """Create audio codec settings dict.

    Args:
        codec: Audio encoder name
        bitrate: Audio bitrate
        sample_rate: Audio sample rate
        channels: Number of audio channels

    Returns:
        Audio codec settings dict
    """
    settings = {
        "codec": codec,
        "bitrate": bitrate,
    }
    if sample_rate:
        settings["sampleRate"] = sample_rate
    if channels:
        settings["channels"] = channels
    settings.update(kwargs)
    return settings


def create_video_filter(
    filter_type: str = "scale",
    enabled: bool = True,
    params: Optional[dict] = None,
    order: int = 0,
    **kwargs
) -> dict:
    """Create a video filter configuration dict.

    Args:
        filter_type: Filter type (scale, crop, fps, etc.)
        enabled: Whether filter is active
        params: Filter parameters
        order: Position in filter chain

    Returns:
        Video filter configuration dict
    """
    return {
        "type": filter_type,
        "enabled": enabled,
        "params": params or {"width": 1920, "height": 1080},
        "order": order,
        **kwargs,
    }


def create_audio_filter(
    filter_type: str = "volume",
    enabled: bool = True,
    params: Optional[dict] = None,
    order: int = 0,
    **kwargs
) -> dict:
    """Create an audio filter configuration dict.

    Args:
        filter_type: Filter type (volume, loudnorm, etc.)
        enabled: Whether filter is active
        params: Filter parameters
        order: Position in filter chain

    Returns:
        Audio filter configuration dict
    """
    return {
        "type": filter_type,
        "enabled": enabled,
        "params": params or {"level": 1.0},
        "order": order,
        **kwargs,
    }


def create_stream_mapping(
    input_index: int = 0,
    stream_type: str = "video",
    stream_index: int = 0,
    output_index: int = 0,
    label: Optional[str] = None,
    language: Optional[str] = None,
    **kwargs
) -> dict:
    """Create a stream mapping entry dict.

    Args:
        input_index: Input file index
        stream_type: Stream type (video, audio, subtitle)
        stream_index: Stream index within type
        output_index: Output position
        label: Human-readable label
        language: Language metadata

    Returns:
        Stream mapping dict
    """
    mapping = {
        "inputIndex": input_index,
        "streamType": stream_type,
        "streamIndex": stream_index,
        "outputIndex": output_index,
    }
    if label:
        mapping["label"] = label
    if language:
        mapping["language"] = language
    mapping.update(kwargs)
    return mapping


def create_builder_state(
    input_source: Optional[dict] = None,
    output_config: Optional[dict] = None,
    video_codec: Optional[dict] = None,
    audio_codec: Optional[dict] = None,
    video_filters: Optional[list] = None,
    audio_filters: Optional[list] = None,
    stream_mappings: Optional[list] = None,
    **kwargs
) -> dict:
    """Create a complete FFMPEG builder state dict.

    Args:
        input_source: Input source config (defaults to basic file input)
        output_config: Output config (defaults to MP4 output)
        video_codec: Video codec settings (defaults to x264 CRF 23)
        audio_codec: Audio codec settings (defaults to AAC 192k)
        video_filters: List of video filters
        audio_filters: List of audio filters
        stream_mappings: List of stream mappings

    Returns:
        Complete builder state dict
    """
    return {
        "input": input_source or create_input_source(),
        "output": output_config or create_output_config(),
        "videoCodec": video_codec or create_video_codec_settings(),
        "audioCodec": audio_codec or create_audio_codec_settings(),
        "videoFilters": video_filters or [],
        "audioFilters": audio_filters or [],
        "streamMappings": stream_mappings or [],
        **kwargs,
    }


# -----------------------------------------------------------------------------
# Hardware Acceleration Factories
# -----------------------------------------------------------------------------

def create_hwaccel_config(
    api: str = "cuda",
    device: Optional[str] = None,
    output_format: Optional[str] = None,
) -> dict:
    """Create a hardware acceleration config dict.

    Args:
        api: HW accel API (none, cuda, qsv, vaapi)
        device: Device path
        output_format: HW output format

    Returns:
        HW accel config dict
    """
    config = {"api": api}
    if device:
        config["device"] = device
    if output_format:
        config["outputFormat"] = output_format
    return config


def create_hw_capability(
    api: str = "cuda",
    available: bool = True,
    encoders: Optional[list] = None,
    decoders: Optional[list] = None,
    devices: Optional[list] = None,
    reason: Optional[str] = None,
) -> dict:
    """Create a hardware capability detection result.

    Args:
        api: HW accel API
        available: Whether this HW accel is available
        encoders: Available hardware encoders
        decoders: Available hardware decoders
        devices: Available devices
        reason: Why unavailable (if applicable)

    Returns:
        HW capability dict
    """
    return {
        "api": api,
        "available": available,
        "encoders": encoders or [],
        "decoders": decoders or [],
        "devices": devices or [],
        "reason": reason,
    }


# -----------------------------------------------------------------------------
# Saved Config / Job Factories
# -----------------------------------------------------------------------------

def create_saved_config(
    name: Optional[str] = None,
    description: Optional[str] = None,
    config: Optional[dict] = None,
    **kwargs
) -> dict:
    """Create a saved FFMPEG configuration dict.

    Args:
        name: Config name
        description: Config description
        config: Builder state dict

    Returns:
        Saved config dict (as returned by API)
    """
    counter = _next_id()
    return {
        "id": counter,
        "name": name or f"Test Config {counter}",
        "description": description,
        "config": config or create_builder_state(),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
        **kwargs,
    }


def create_ffmpeg_job(
    name: Optional[str] = None,
    status: str = "queued",
    command: Optional[str] = None,
    progress: Optional[dict] = None,
    error: Optional[str] = None,
    **kwargs
) -> dict:
    """Create an FFMPEG job dict.

    Args:
        name: Job name
        status: Job status (queued, running, completed, failed, cancelled)
        command: FFMPEG command string
        progress: Job progress dict
        error: Error message if failed

    Returns:
        FFMPEG job dict
    """
    counter = _next_id()
    now = datetime.utcnow()
    job = {
        "id": f"job-{counter}",
        "name": name or f"Test Job {counter}",
        "status": status,
        "command": command or "ffmpeg -i input.mp4 -c:v libx264 -crf 23 output.mp4",
        "progress": progress,
        "started_at": now.isoformat() if status in ("running", "completed", "failed") else None,
        "completed_at": now.isoformat() if status in ("completed", "failed") else None,
        "error": error,
        "output_path": "/media/output.mp4" if status == "completed" else None,
        "created_at": (now - timedelta(minutes=5)).isoformat(),
    }
    job.update(kwargs)
    return job


def create_job_progress(
    percent: float = 50.0,
    fps: float = 30.0,
    speed: str = "2.0x",
    time: str = "00:05:00",
    size: str = "100MB",
    bitrate: str = "5000kbps",
    eta: Optional[str] = "00:05:00",
) -> dict:
    """Create a job progress dict.

    Args:
        percent: Progress percentage
        fps: Current FPS
        speed: Encoding speed multiplier
        time: Current position
        size: Current output size
        bitrate: Current bitrate
        eta: Estimated time remaining

    Returns:
        Job progress dict
    """
    return {
        "percent": percent,
        "fps": fps,
        "speed": speed,
        "time": time,
        "size": size,
        "bitrate": bitrate,
        "eta": eta,
    }


# -----------------------------------------------------------------------------
# Preset Template Factories
# -----------------------------------------------------------------------------

def create_preset_template(
    preset_id: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    category: str = "web",
    is_builtin: bool = False,
    config: Optional[dict] = None,
) -> dict:
    """Create a preset template dict.

    Args:
        preset_id: Preset ID
        name: Preset name
        description: Preset description
        category: Preset category
        is_builtin: Whether this is a built-in preset
        config: Builder state dict

    Returns:
        Preset template dict
    """
    counter = _next_id()
    return {
        "id": preset_id or f"preset-{counter}",
        "name": name or f"Test Preset {counter}",
        "description": description or f"Description for preset {counter}",
        "category": category,
        "isBuiltIn": is_builtin,
        "config": config or create_builder_state(),
    }


# -----------------------------------------------------------------------------
# FFMPEG Capabilities Factory
# -----------------------------------------------------------------------------

def create_capabilities(
    version: str = "6.1",
    encoders: Optional[list] = None,
    decoders: Optional[list] = None,
    formats: Optional[list] = None,
    filters: Optional[list] = None,
    hwaccels: Optional[list] = None,
) -> dict:
    """Create an FFMPEG capabilities response dict.

    Args:
        version: FFMPEG version string
        encoders: Available encoders
        decoders: Available decoders
        formats: Available container formats
        filters: Available filters
        hwaccels: Hardware acceleration capabilities

    Returns:
        Capabilities dict
    """
    return {
        "version": version,
        "encoders": encoders or [
            "libx264", "libx265", "libvpx-vp9", "libaom-av1", "libsvtav1",
            "h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv",
            "h264_vaapi", "hevc_vaapi",
            "aac", "libmp3lame", "libvorbis", "libopus", "ac3", "flac",
        ],
        "decoders": decoders or [
            "h264", "hevc", "vp9", "av1",
            "h264_cuvid", "hevc_cuvid",
            "aac", "mp3", "vorbis", "opus",
        ],
        "formats": formats or [
            "mp4", "mkv", "webm", "ts", "flv", "avi", "mov", "ogg", "hls", "dash",
        ],
        "filters": filters or [
            "scale", "crop", "fps", "yadif", "drawtext",
            "volume", "loudnorm", "aresample",
        ],
        "hwaccels": hwaccels or [
            create_hw_capability("cuda", True, ["h264_nvenc", "hevc_nvenc"], ["h264_cuvid"]),
            create_hw_capability("qsv", True, ["h264_qsv", "hevc_qsv"], ["h264_qsv"]),
            create_hw_capability("vaapi", True, ["h264_vaapi", "hevc_vaapi"], ["h264_vaapi"], ["/dev/dri/renderD128"]),
        ],
    }


# -----------------------------------------------------------------------------
# Validation Result Factories
# -----------------------------------------------------------------------------

def create_validation_result(
    valid: bool = True,
    errors: Optional[list] = None,
    warnings: Optional[list] = None,
    command: str = "ffmpeg -i input.mp4 -c:v libx264 -crf 23 output.mp4",
) -> dict:
    """Create a validation result dict.

    Args:
        valid: Whether config is valid
        errors: Validation errors
        warnings: Validation warnings
        command: Generated command string

    Returns:
        Validation result dict
    """
    return {
        "valid": valid,
        "errors": errors or [],
        "warnings": warnings or [],
        "command": command,
    }


def create_invalid_validation_result(
    errors: Optional[list] = None,
) -> dict:
    """Create an invalid validation result.

    Convenience factory for validation failures.
    """
    return create_validation_result(
        valid=False,
        errors=errors or ["Input path is required", "Output path is required"],
        command="",
    )
