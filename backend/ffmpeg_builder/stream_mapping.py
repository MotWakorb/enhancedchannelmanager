"""Stream mapping validation and flag generation."""

from typing import Dict, List

from ffmpeg_builder.common import ValidationResult

# Type alias
StreamMapping = Dict

VALID_STREAM_TYPES = {"video", "audio", "subtitle", "data"}

# Short codes for stream types in -map flags
STREAM_TYPE_CODES = {
    "video": "v",
    "audio": "a",
    "subtitle": "s",
    "data": "d",
}


def validate_stream_mapping(mapping: dict) -> ValidationResult:
    """Validate a single stream mapping dict."""
    result = ValidationResult()

    input_index = mapping.get("inputIndex", 0)
    if input_index < 0:
        result.add_error(f"Input index must be >= 0, got {input_index}")

    stream_type = mapping.get("streamType", "")
    if stream_type not in VALID_STREAM_TYPES:
        result.add_error(
            f"Invalid stream type '{stream_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_STREAM_TYPES))}"
        )

    stream_index = mapping.get("streamIndex", 0)
    if stream_index < 0:
        result.add_error(f"Stream index must be >= 0, got {stream_index}")

    output_index = mapping.get("outputIndex", 0)
    if output_index < 0:
        result.add_error(f"Output index must be >= 0, got {output_index}")

    return result


def validate_stream_mappings(mappings: list) -> ValidationResult:
    """Validate a list of stream mappings."""
    result = ValidationResult()

    seen_outputs = {}
    for m in mappings:
        sub = validate_stream_mapping(m)
        result.merge(sub)

        stype = m.get("streamType", "")
        out_idx = m.get("outputIndex", 0)
        key = (stype, out_idx)
        if key in seen_outputs:
            result.add_error(
                f"Duplicate output index {out_idx} for stream type '{stype}'"
            )
        seen_outputs[key] = True

    return result


def generate_map_flags(mappings: list) -> List[str]:
    """Generate -map flags from a list of stream mapping dicts."""
    if not mappings:
        return []

    flags: List[str] = []

    for m in mappings:
        input_idx = m.get("inputIndex", 0)
        stream_type = m.get("streamType", "video")
        stream_idx = m.get("streamIndex", 0)
        exclude = m.get("exclude", False)
        type_code = STREAM_TYPE_CODES.get(stream_type, stream_type)

        spec = f"{input_idx}:{type_code}:{stream_idx}"
        if exclude:
            spec = f"-{spec}"

        flags.extend(["-map", spec])

        # Metadata (language, title) for this output stream
        output_idx = m.get("outputIndex", 0)
        language = m.get("language")
        if language:
            flags.extend([f"-metadata:s:{output_idx}", f"language={language}"])
        title = m.get("title")
        if title:
            flags.extend([f"-metadata:s:{output_idx}", f"title={title}"])

    return flags


def get_default_mappings(input_count: int = 1, map_all: bool = False) -> List[dict]:
    """Generate default stream mappings.

    When map_all is True, creates mappings for all streams from each input.
    """
    if map_all:
        mappings = []
        for i in range(input_count):
            mappings.append({
                "inputIndex": i,
                "streamType": "video",
                "streamIndex": 0,
                "outputIndex": len(mappings),
            })
            mappings.append({
                "inputIndex": i,
                "streamType": "audio",
                "streamIndex": 0,
                "outputIndex": len(mappings),
            })
        return mappings
    return []
