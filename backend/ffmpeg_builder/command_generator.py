"""Full command generation and annotation from builder state."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ffmpeg_builder.input import generate_input_flags, generate_hwaccel_flags
from ffmpeg_builder.output import generate_output_flags
from ffmpeg_builder.video_codec import generate_video_codec_flags
from ffmpeg_builder.audio_codec import generate_audio_codec_flags
from ffmpeg_builder.video_filters import generate_video_filter_flags
from ffmpeg_builder.audio_filters import generate_audio_filter_flags
from ffmpeg_builder.stream_mapping import generate_map_flags


@dataclass
class Annotation:
    """A single annotation for a command flag."""

    flag: str
    explanation: str
    category: str  # input, output, video, audio, filter, global


@dataclass
class AnnotatedCommand:
    """A fully annotated ffmpeg command."""

    command: List[str]
    annotations: List[Annotation] = field(default_factory=list)


def generate_command(state: dict) -> List[str]:
    """Generate a complete ffmpeg command list from a builder state dict.

    Argument order: ffmpeg [global] [hwaccel] [input_opts] -i <input>
                    [codec_opts] [filter_opts] [map_opts] [output_opts] <output>
    """
    cmd: List[str] = ["ffmpeg"]

    # Global options
    global_opts = state.get("globalOptions", {})
    for key, val in global_opts.items():
        cmd.extend([f"-{key}", str(val)])

    # Input flags (includes hwaccel, seek, format, -i)
    input_cfg = state.get("input", {})
    input_flags = generate_input_flags(input_cfg)
    cmd.extend(input_flags)

    # Additional inputs
    additional = state.get("additionalInputs", [])
    for extra_input in additional:
        extra_flags = generate_input_flags(extra_input)
        cmd.extend(extra_flags)

    # Stream mappings
    mappings = state.get("streamMappings", [])
    map_flags = generate_map_flags(mappings)
    cmd.extend(map_flags)

    # Video codec
    video_codec = state.get("videoCodec", {})
    if video_codec:
        codec_flags = generate_video_codec_flags(video_codec)
        cmd.extend(codec_flags)

    # Audio codec
    audio_codec = state.get("audioCodec", {})
    if audio_codec:
        audio_flags = generate_audio_codec_flags(audio_codec)
        cmd.extend(audio_flags)

    # Video filters
    video_filters = state.get("videoFilters", [])
    hwaccel = input_cfg.get("hwaccel") if input_cfg else None
    vf_flags = generate_video_filter_flags(video_filters, hwaccel=hwaccel)
    cmd.extend(vf_flags)

    # Audio filters
    audio_filters = state.get("audioFilters", [])
    af_flags = generate_audio_filter_flags(audio_filters)
    cmd.extend(af_flags)

    # Output flags (format, movflags, path last)
    output_cfg = state.get("output", {})
    output_flags = generate_output_flags(output_cfg)
    cmd.extend(output_flags)

    return cmd


def annotate_command(state: dict) -> AnnotatedCommand:
    """Generate a command and annotate each flag group with explanations."""
    cmd = generate_command(state)
    annotations: List[Annotation] = []

    input_cfg = state.get("input", {})
    video_codec = state.get("videoCodec", {})
    audio_codec = state.get("audioCodec", {})
    output_cfg = state.get("output", {})
    video_filters = state.get("videoFilters", [])
    audio_filters = state.get("audioFilters", [])

    # Input annotation
    path = input_cfg.get("path", "")
    annotations.append(Annotation(
        flag=f"-i {path}",
        explanation=f"Input file: {path}",
        category="input",
    ))

    # HW accel annotation
    hwaccel = input_cfg.get("hwaccel")
    if hwaccel and hwaccel.get("api", "none") != "none":
        api = hwaccel["api"]
        annotations.append(Annotation(
            flag=f"-hwaccel {api}",
            explanation=f"Hardware acceleration using {api.upper()}",
            category="input",
        ))

    # Video codec annotation
    codec_name = video_codec.get("codec", "")
    if codec_name:
        if codec_name == "copy":
            explanation = "Video stream copy (no re-encoding)"
        else:
            explanation = f"Video codec: {codec_name}"
        annotations.append(Annotation(
            flag=f"-c:v {codec_name}",
            explanation=explanation,
            category="video",
        ))

    # CRF/rate control annotation
    crf = video_codec.get("crf")
    if crf is not None and codec_name != "copy":
        annotations.append(Annotation(
            flag=f"-crf {crf}",
            explanation=f"Constant Rate Factor {crf} (lower = higher quality)",
            category="video",
        ))

    # Audio codec annotation
    acodec = audio_codec.get("codec", "")
    if acodec:
        if acodec == "copy":
            explanation = "Audio stream copy (no re-encoding)"
        else:
            explanation = f"Audio codec: {acodec}"
        annotations.append(Annotation(
            flag=f"-c:a {acodec}",
            explanation=explanation,
            category="audio",
        ))

    # Video filter annotation
    enabled_vf = [f for f in video_filters if f.get("enabled", True)]
    if enabled_vf:
        filter_names = [f.get("type", "") for f in enabled_vf]
        annotations.append(Annotation(
            flag="-vf " + ",".join(filter_names),
            explanation=f"Video filter chain: {', '.join(filter_names)}",
            category="filter",
        ))

    # Audio filter annotation
    enabled_af = [f for f in audio_filters if f.get("enabled", True)]
    if enabled_af:
        filter_names = [f.get("type", "") for f in enabled_af]
        annotations.append(Annotation(
            flag="-af " + ",".join(filter_names),
            explanation=f"Audio filter chain: {', '.join(filter_names)}",
            category="filter",
        ))

    # Output annotation
    out_path = output_cfg.get("path", "")
    out_fmt = output_cfg.get("format", "")
    annotations.append(Annotation(
        flag=out_path,
        explanation=f"Output file ({out_fmt} format): {out_path}",
        category="output",
    ))

    return AnnotatedCommand(command=cmd, annotations=annotations)
