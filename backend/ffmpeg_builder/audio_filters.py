"""Audio filter validation, generation, and chain composition."""

from typing import Dict, List

from ffmpeg_builder.common import ValidationResult

KNOWN_AUDIO_FILTERS = {
    "volume", "loudnorm", "aresample", "atempo",
    "equalizer", "highpass", "lowpass",
    "aecho", "adelay", "amix",
    "anull", "custom",
}


def validate_audio_filter(af: dict) -> ValidationResult:
    """Validate a single audio filter dict."""
    result = ValidationResult()
    ftype = af.get("type", "")

    if ftype not in KNOWN_AUDIO_FILTERS:
        result.add_error(f"Unknown audio filter type '{ftype}'")
        return result

    params = af.get("params", {})

    if ftype == "volume":
        level = params.get("level", 1.0)
        if level < 0:
            result.add_error(f"Volume level must be >= 0, got {level}")

    if ftype == "atempo":
        tempo = params.get("tempo", 1.0)
        if tempo < 0.5 or tempo > 100.0:
            result.add_error(f"Atempo value must be between 0.5 and 100.0, got {tempo}")

    if ftype == "custom":
        raw = params.get("raw", "")
        if not raw:
            result.add_error("Custom audio filter requires a non-empty 'raw' string")

    return result


def validate_audio_filter_chain(filters: list) -> ValidationResult:
    """Validate a list of audio filters (the full chain)."""
    result = ValidationResult()

    orders = []
    for af in filters:
        sub = validate_audio_filter(af)
        result.merge(sub)
        orders.append(af.get("order", 0))

    if len(orders) != len(set(orders)):
        result.add_error("Duplicate order values in audio filter chain")

    return result


def generate_single_audio_filter(af: dict) -> str:
    """Generate a single audio filter string from a filter dict."""
    ftype = af.get("type", "")
    params = af.get("params", {})

    if ftype == "volume":
        level = params.get("level", 1.0)
        return f"volume={level}"

    if ftype == "loudnorm":
        parts = []
        if "I" in params:
            parts.append(f"I={params['I']}")
        if "TP" in params:
            parts.append(f"TP={params['TP']}")
        if "LRA" in params:
            parts.append(f"LRA={params['LRA']}")
        return f"loudnorm={'='.join([]) if not parts else ''}{'='.join([]) if not parts else ''}" if not parts else f"loudnorm={':'.join(parts)}"

    if ftype == "aresample":
        sr = params.get("sample_rate", 48000)
        return f"aresample={sr}"

    if ftype == "atempo":
        tempo = params.get("tempo", 1.0)
        return f"atempo={tempo}"

    if ftype == "equalizer":
        f = params.get("frequency", 1000)
        wt = params.get("width_type", "q")
        w = params.get("width", 1)
        g = params.get("gain", 0)
        return f"equalizer=f={f}:t={wt}:w={w}:g={g}"

    if ftype == "highpass":
        f = params.get("frequency", 200)
        return f"highpass=f={f}"

    if ftype == "lowpass":
        f = params.get("frequency", 3000)
        return f"lowpass=f={f}"

    if ftype == "custom":
        return params.get("raw", "")

    # Fallback
    parts = [f"{k}={v}" for k, v in params.items()]
    return f"{ftype}={':'.join(parts)}" if parts else ftype


def generate_audio_filter_flags(filters: list) -> List[str]:
    """Generate -af flags from a list of audio filter dicts."""
    enabled = [f for f in filters if f.get("enabled", True)]
    if not enabled:
        return []

    enabled.sort(key=lambda f: f.get("order", 0))
    parts = [generate_single_audio_filter(af) for af in enabled]

    return ["-af", ",".join(parts)]
