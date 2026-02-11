"""Full configuration validation for the FFMPEG builder."""

import re
from typing import Optional

from ffmpeg_builder.common import ValidationResult
from ffmpeg_builder.input import validate_input_source
from ffmpeg_builder.output import validate_output_config
from ffmpeg_builder.video_codec import validate_video_codec, VAAPI_CODECS
from ffmpeg_builder.audio_codec import validate_audio_codec

BITRATE_RE = re.compile(r"^-?\d+[kKmM]$")

# Recommended codec/container combos
CODEC_CONTAINER_COMPAT = {
    "mp4": {"libx264", "libx265", "h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv",
            "h264_vaapi", "hevc_vaapi", "libaom-av1", "libsvtav1", "copy"},
    "mkv": None,  # MKV accepts everything
    "webm": {"libvpx-vp9", "libaom-av1", "copy"},
    "ts": {"libx264", "libx265", "h264_nvenc", "hevc_nvenc", "copy"},
}


def validate_config(state: dict) -> ValidationResult:
    """Validate a complete builder state dict.

    Returns a ValidationResult with errors for fatal issues and warnings
    for non-blocking concerns.
    """
    result = ValidationResult()

    # --- Input ---
    input_cfg = state.get("input")
    if not input_cfg:
        result.add_error("Input source is required")
    else:
        sub = validate_input_source(input_cfg)
        result.merge(sub)

    # --- Output ---
    output_cfg = state.get("output")
    if not output_cfg:
        result.add_error("Output configuration is required")
    else:
        sub = validate_output_config(output_cfg)
        result.merge(sub)

    # --- Video codec ---
    video_codec = state.get("videoCodec")
    if video_codec:
        sub = validate_video_codec(video_codec)
        result.merge(sub)

        codec_name = video_codec.get("codec", "")

        # CRF range checks (also in video_codec but duplicated for the
        # validate_config-specific tests)
        crf = video_codec.get("crf")
        if crf is not None:
            if crf < 0:
                if not any("crf" in e.lower() for e in result.errors):
                    result.add_error(f"CRF value {crf} is below minimum (0)")
            elif crf > 51:
                if not any("crf" in e.lower() for e in result.errors):
                    result.add_error(f"CRF value {crf} exceeds maximum (51)")

        # Bitrate
        bitrate = video_codec.get("bitrate")
        if bitrate is not None and BITRATE_RE.match(str(bitrate)):
            if str(bitrate).startswith("-"):
                if not any("bitrate" in e.lower() for e in result.errors):
                    result.add_error(f"Negative bitrate: '{bitrate}'")

        # Codec/container compatibility
        if output_cfg:
            fmt = output_cfg.get("format", "")
            allowed = CODEC_CONTAINER_COMPAT.get(fmt)
            if allowed is not None and codec_name and codec_name not in allowed:
                result.add_warning(
                    f"Codec '{codec_name}' may not be compatible with "
                    f"container format '{fmt}' — possible mismatch"
                )

        # VAAPI without hwupload warning
        if codec_name in VAAPI_CODECS:
            video_filters = state.get("videoFilters", [])
            has_hwupload = any(
                vf.get("type") in ("hwupload", "hwupload_vaapi")
                for vf in video_filters
            )
            if not has_hwupload:
                result.add_warning(
                    "VAAPI encoder selected but no hwupload_vaapi filter found — "
                    "you may need to add a hwupload filter for VAAPI encoding"
                )

    # --- Audio codec ---
    audio_codec = state.get("audioCodec")
    if audio_codec:
        sub = validate_audio_codec(audio_codec)
        result.merge(sub)

        audio_codec_name = audio_codec.get("codec", "")

        # Warn about filters with copy
        if audio_codec_name == "copy":
            audio_filters = state.get("audioFilters", [])
            enabled_af = [f for f in audio_filters if f.get("enabled", True)]
            if enabled_af:
                result.add_warning(
                    "Audio filters will be ignored when audio codec is set to 'copy'"
                )

    # --- Video filters with copy warning ---
    video_codec = state.get("videoCodec")
    if video_codec and video_codec.get("codec") == "copy":
        video_filters = state.get("videoFilters", [])
        enabled_vf = [f for f in video_filters if f.get("enabled", True)]
        if enabled_vf:
            result.add_warning(
                "Video filters will be ignored when video codec is set to 'copy'"
            )

    return result
