"""FFprobe integration — probe sources and detect system capabilities."""

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

FFPROBE_BIN = "ffprobe"
FFMPEG_BIN = "ffmpeg"
DEFAULT_TIMEOUT = 30


@dataclass
class ProbeResult:
    """Result of probing a media source with ffprobe."""

    success: bool = True
    streams: List[Dict[str, Any]] = field(default_factory=list)
    format_name: str = ""
    duration: Optional[float] = None
    bit_rate: Optional[int] = None
    size: Optional[int] = None
    error: str = ""
    raw: Optional[Dict[str, Any]] = None


def probe_source(path: str, timeout: int = DEFAULT_TIMEOUT) -> ProbeResult:
    """Probe a media file or URL using ffprobe.

    Args:
        path: File path, URL, or device to probe.
        timeout: Subprocess timeout in seconds.

    Returns:
        ProbeResult with stream and format information.
    """
    cmd = [
        FFPROBE_BIN,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (TimeoutError, subprocess.TimeoutExpired):
        return ProbeResult(success=False, error=f"Probe timeout after {timeout}s")
    except FileNotFoundError:
        return ProbeResult(success=False, error=f"{FFPROBE_BIN} not found on system")
    except Exception as exc:
        return ProbeResult(success=False, error=str(exc))

    if proc.returncode != 0:
        return ProbeResult(
            success=False,
            error=proc.stderr.strip() or f"ffprobe exited with code {proc.returncode}",
        )

    return parse_probe_output(proc.stdout)


def parse_probe_output(raw_json: str) -> ProbeResult:
    """Parse ffprobe JSON output into a ProbeResult.

    Args:
        raw_json: Raw JSON string from ffprobe -print_format json.

    Returns:
        ProbeResult populated from the parsed data.
    """
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, ValueError) as exc:
        return ProbeResult(success=False, error=f"Failed to parse JSON: {exc}")

    fmt = data.get("format", {})
    streams = data.get("streams", [])

    duration = None
    dur_str = fmt.get("duration")
    if dur_str:
        try:
            duration = float(dur_str)
        except (ValueError, TypeError):
            pass

    bit_rate = None
    br_str = fmt.get("bit_rate")
    if br_str:
        try:
            bit_rate = int(br_str)
        except (ValueError, TypeError):
            pass

    size = None
    size_str = fmt.get("size")
    if size_str:
        try:
            size = int(size_str)
        except (ValueError, TypeError):
            pass

    return ProbeResult(
        success=True,
        streams=streams,
        format_name=fmt.get("format_name", ""),
        duration=duration,
        bit_rate=bit_rate,
        size=size,
        raw=data,
    )


# ---------------------------------------------------------------------------
# System capability detection
# ---------------------------------------------------------------------------

def detect_capabilities() -> Dict[str, Any]:
    """Detect system ffmpeg capabilities (codecs, formats, filters, hwaccel).

    Returns a dict matching the FFMPEGCapabilities TypeScript type.
    """
    result: Dict[str, Any] = {
        "version": "",
        "encoders": [],
        "decoders": [],
        "formats": [],
        "filters": [],
        "hwaccels": [],
    }

    # Version
    result["version"] = _run_ffmpeg_query(["-version"])

    # Encoders
    enc_output = _run_ffmpeg_query(["-encoders"])
    result["encoders"] = _parse_codec_list(enc_output)

    # Decoders
    dec_output = _run_ffmpeg_query(["-decoders"])
    result["decoders"] = _parse_codec_list(dec_output)

    # Formats
    fmt_output = _run_ffmpeg_query(["-formats"])
    result["formats"] = _parse_format_list(fmt_output)

    # Filters
    flt_output = _run_ffmpeg_query(["-filters"])
    result["filters"] = _parse_filter_list(flt_output)

    # HW acceleration detection from encoder names
    result["hwaccels"] = _detect_hwaccels(result["encoders"])

    return result


def _run_ffmpeg_query(extra_args: List[str]) -> str:
    """Run ffmpeg with query flags and return stdout."""
    try:
        proc = subprocess.run(
            [FFMPEG_BIN] + extra_args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return ""


def _parse_codec_list(output: str) -> List[str]:
    """Parse encoder/decoder list from ffmpeg -encoders/-decoders output."""
    codecs = []
    # Lines look like: " V..... libx264  description..."
    pattern = re.compile(r"^\s+[VASD][.F][.S][.X][.B][.D]\s+(\S+)")
    for line in output.splitlines():
        m = pattern.match(line)
        if m:
            codecs.append(m.group(1))
    return codecs


def _parse_format_list(output: str) -> List[str]:
    """Parse format list from ffmpeg -formats output."""
    formats = []
    # Lines look like: " DE mp4  description..."
    pattern = re.compile(r"^\s+[D ][E ]\s+(\S+)")
    for line in output.splitlines():
        m = pattern.match(line)
        if m:
            name = m.group(1)
            # Skip header lines
            if name not in ("--", "Flags:"):
                formats.append(name)
    return formats


def _parse_filter_list(output: str) -> List[str]:
    """Parse filter list from ffmpeg -filters output."""
    filters = []
    # Lines look like: " ... scale  V->V  description"
    pattern = re.compile(r"^\s+[T.][S.][C.]\s+(\S+)")
    for line in output.splitlines():
        m = pattern.match(line)
        if m:
            filters.append(m.group(1))
    return filters


HWACCEL_ENCODER_PATTERNS = {
    "cuda": re.compile(r"_(nvenc|cuvid)$"),
    "qsv": re.compile(r"_qsv$"),
    "vaapi": re.compile(r"_vaapi$"),
}


def _detect_hwaccels(encoders: List[str]) -> List[Dict[str, Any]]:
    """Detect hardware acceleration from available encoder names."""
    detected: Dict[str, List[str]] = {}

    for enc in encoders:
        for api, pattern in HWACCEL_ENCODER_PATTERNS.items():
            if pattern.search(enc):
                detected.setdefault(api, []).append(enc)

    hwaccels = []
    for api, enc_list in detected.items():
        hwaccels.append({
            "api": api,
            "available": True,
            "encoders": enc_list,
            "decoders": [],
            "devices": [],
        })

    return hwaccels
