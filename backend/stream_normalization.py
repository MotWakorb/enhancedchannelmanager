"""
Stream Normalization Module

Provides functions for normalizing, parsing, and filtering stream names.
Ported from frontend/src/services/streamNormalization.ts and
frontend/src/constants/streamNormalization.ts.

Key capabilities:
- Unicode to ASCII normalization (superscript, subscript, small caps, full-width)
- Quality/resolution extraction and prioritization
- Country prefix detection and stripping
- Network prefix/suffix detection and stripping
- Regional variant (East/West) detection and timezone filtering
- Provider-interleaved quality sorting
"""
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (ported from frontend/src/constants/streamNormalization.ts)
# ---------------------------------------------------------------------------

QUALITY_SUFFIXES = [
    "FHD", "UHD", "4K", "HD", "SD",
    "1080P", "1080I", "720P", "480P", "2160P",
    "HEVC", "H264", "H265",
]

TIMEZONE_SUFFIXES = ["EAST", "WEST", "ET", "PT", "CT", "MT"]

LEAGUE_PREFIXES = [
    "NFL", "NBA", "MLB", "NHL", "MLS", "WNBA", "NCAA", "CFB", "CBB",
    "EPL", "PREMIER LEAGUE", "LA LIGA", "LALIGA", "BUNDESLIGA", "SERIE A", "LIGUE 1",
    "UEFA", "FIFA", "F1", "NASCAR", "PGA", "ATP", "WTA",
    "WWE", "UFC", "AEW", "BOXING",
]

NETWORK_PREFIXES = [
    "CHAMP", "CHAMPIONSHIP", "PPV", "PAY PER VIEW",
    "PREMIER", "PREMIER LEAGUE", "PL", "PRIME",
    "NFL", "NBA", "MLB", "NHL", "MLS", "NCAA",
    "UFC", "WWE", "AEW", "BOXING",
    "GOLF", "TENNIS", "CRICKET", "RUGBY",
    "RACING", "MOTORSPORT", "F1", "NASCAR",
    "LIVE", "SPORTS", "MATCH", "GAME",
    "24/7", "LINEAR",
    "RSN",
]

NETWORK_SUFFIXES = [
    "ENGLISH", "ENG", "SPANISH", "ESP", "FRENCH", "FRA", "GERMAN", "DEU",
    "PORTUGUESE", "POR",
    "LIVE", "REPLAY", "DELAY", "BACKUP", "ALT", "ALTERNATE", "MAIN",
    "FEED", "MULTI", "CLEAN", "RAW", "PRIMARY", "SECONDARY",
    "PPV", "EVENT", "SPECIAL", "EXCLUSIVE",
    "MPEG2", "MPEG4", "AVC", "STEREO", "MONO", "5.1", "SURROUND",
]

QUALITY_PRIORITY: dict[str, int] = {
    "UHD": 10, "4K": 10, "2160P": 10,
    "FHD": 20, "1080P": 20, "1080I": 21,
    "HD": 30, "720P": 30,
    "SD": 40, "480P": 40,
}

DEFAULT_QUALITY_PRIORITY = 30

COUNTRY_PREFIXES = [
    "US", "USA", "UK", "CA", "AU", "NZ", "IE", "IN", "PH", "MX", "BR",
    "DE", "FR", "ES", "IT", "NL", "BE", "CH", "AT", "PL", "SE", "NO",
    "DK", "FI", "PT", "GR", "TR", "RU", "JP", "KR", "CN", "TW", "HK",
    "SG", "MY", "TH", "ID", "VN", "PK", "BD", "LK", "ZA", "EG", "NG",
    "KE", "GH", "AR", "CL", "CO", "PE", "VE", "EC", "PR", "DO", "CU",
    "JM", "TT", "BB", "CR", "PA", "HN", "SV", "GT", "NI", "BZ", "IL",
    "AE", "SA", "QA", "KW", "BH", "OM", "JO", "LB", "IR", "IQ", "AF",
    "LATAM", "LATINO", "LATIN",
]

# ---------------------------------------------------------------------------
# Unicode → ASCII map
# ---------------------------------------------------------------------------

UNICODE_TO_ASCII_MAP: dict[str, str] = {
    # Superscript letters (Modifier Letter Capital)
    "\u1D2C": "A", "\u1D2E": "B", "\u1D30": "D", "\u1D31": "E", "\u1D33": "G",
    "\u1D34": "H", "\u1D35": "I", "\u1D36": "J", "\u1D37": "K", "\u1D38": "L",
    "\u1D39": "M", "\u1D3A": "N", "\u1D3C": "O", "\u1D3E": "P", "\u1D3F": "R",
    "\u1D40": "T", "\u1D41": "U", "\u1D42": "W",
    # Superscript letters (Modifier Letter Small)
    "\u1D43": "a", "\u1D47": "b", "\u1D48": "d", "\u1D49": "e", "\u1D4D": "g",
    "\u02B0": "h", "\u2071": "i", "\u02B2": "j", "\u1D4F": "k", "\u02E1": "l",
    "\u1D50": "m", "\u207F": "n", "\u1D52": "o", "\u1D56": "p", "\u02B3": "r",
    "\u02E2": "s", "\u1D57": "t", "\u1D58": "u", "\u1D5B": "v", "\u02B7": "w",
    "\u02E3": "x", "\u02B8": "y", "\u1DBB": "z",
    # Common superscript characters
    "\u00B2": "2", "\u00B3": "3", "\u00B9": "1", "\u2070": "0", "\u2074": "4",
    "\u2075": "5", "\u2076": "6", "\u2077": "7", "\u2078": "8", "\u2079": "9",
    "\u207A": "+", "\u207B": "-", "\u207C": "=", "\u207D": "(", "\u207E": ")",
    # Subscript numbers
    "\u2080": "0", "\u2081": "1", "\u2082": "2", "\u2083": "3", "\u2084": "4",
    "\u2085": "5", "\u2086": "6", "\u2087": "7", "\u2088": "8", "\u2089": "9",
    "\u208A": "+", "\u208B": "-", "\u208C": "=", "\u208D": "(", "\u208E": ")",
    # Small capitals
    "\u1D00": "A", "\u0299": "B", "\u1D04": "C", "\u1D05": "D", "\u1D07": "E",
    "\u0493": "F", "\u0262": "G", "\u029C": "H", "\u026A": "I", "\u1D0A": "J",
    "\u1D0B": "K", "\u029F": "L", "\u1D0D": "M", "\u0274": "N", "\u1D0F": "O",
    "\u1D18": "P", "\u0280": "R", "\u0455": "S", "\u1D1B": "T", "\u1D1C": "U",
    "\u1D20": "V", "\u1D21": "W", "\u028F": "Y", "\u1D22": "Z",
    # Full-width letters A-Z
    "\uFF21": "A", "\uFF22": "B", "\uFF23": "C", "\uFF24": "D", "\uFF25": "E",
    "\uFF26": "F", "\uFF27": "G", "\uFF28": "H", "\uFF29": "I", "\uFF2A": "J",
    "\uFF2B": "K", "\uFF2C": "L", "\uFF2D": "M", "\uFF2E": "N", "\uFF2F": "O",
    "\uFF30": "P", "\uFF31": "Q", "\uFF32": "R", "\uFF33": "S", "\uFF34": "T",
    "\uFF35": "U", "\uFF36": "V", "\uFF37": "W", "\uFF38": "X", "\uFF39": "Y",
    "\uFF3A": "Z",
    # Full-width letters a-z
    "\uFF41": "a", "\uFF42": "b", "\uFF43": "c", "\uFF44": "d", "\uFF45": "e",
    "\uFF46": "f", "\uFF47": "g", "\uFF48": "h", "\uFF49": "i", "\uFF4A": "j",
    "\uFF4B": "k", "\uFF4C": "l", "\uFF4D": "m", "\uFF4E": "n", "\uFF4F": "o",
    "\uFF50": "p", "\uFF51": "q", "\uFF52": "r", "\uFF53": "s", "\uFF54": "t",
    "\uFF55": "u", "\uFF56": "v", "\uFF57": "w", "\uFF58": "x", "\uFF59": "y",
    "\uFF5A": "z",
    # Full-width numbers
    "\uFF10": "0", "\uFF11": "1", "\uFF12": "2", "\uFF13": "3", "\uFF14": "4",
    "\uFF15": "5", "\uFF16": "6", "\uFF17": "7", "\uFF18": "8", "\uFF19": "9",
}

# Pre-compile regex patterns
_LEADING_SEPARATORS_RE = re.compile(r"^[\s|:\-/]+")
_ARBITRARY_RESOLUTION_RE = re.compile(r"[\s\-_|:]*\d+[pPiI]\s*$")
_REGIONAL_EAST_RE = re.compile(r"[\s\-_|:]+EAST\s*$", re.IGNORECASE)
_REGIONAL_WEST_RE = re.compile(r"[\s\-_|:]+WEST\s*$", re.IGNORECASE)
_REGIONAL_STRIP_RE = re.compile(r"[\s\-_|:]+(?:EAST|WEST)\s*$", re.IGNORECASE)
_RESOLUTION_SCORE_RE = re.compile(r"(?:^|[\s\-_|:])(\d+)[PI](?:$|[\s\-_|:])")
_NUMERIC_RESOLUTION_RE = re.compile(r"^\d+[PI]$")


# ---------------------------------------------------------------------------
# Data classes for enriched stream data
# ---------------------------------------------------------------------------

@dataclass
class StreamEnrichment:
    """Enrichment data computed for a single stream."""
    normalized_name: str = ""
    quality_tier: str = ""
    quality_priority: int = DEFAULT_QUALITY_PRIORITY
    detected_country: Optional[str] = None
    detected_network_prefix: Optional[str] = None
    regional_variant: Optional[str] = None  # "east", "west", or None


@dataclass
class BatchEnrichmentResult:
    """Result of batch stream enrichment."""
    enrichments: dict[int, StreamEnrichment] = field(default_factory=dict)
    elapsed_ms: float = 0.0


# ---------------------------------------------------------------------------
# Core normalization functions
# ---------------------------------------------------------------------------

def normalize_unicode_to_ascii(text: str) -> str:
    """Convert Unicode superscript/subscript/small-caps/full-width to ASCII."""
    return "".join(UNICODE_TO_ASCII_MAP.get(ch, ch) for ch in text)


def strip_leading_separators(name: str) -> str:
    """Strip leading separator characters (pipes, dashes, colons)."""
    return _LEADING_SEPARATORS_RE.sub("", name)


def strip_quality_suffixes(name: str) -> str:
    """Strip quality/resolution suffixes from a name.

    Handles named suffixes (FHD, UHD, 4K, HD, SD) and arbitrary resolutions
    (1080p, 720p, 476p, etc.).
    """
    result = normalize_unicode_to_ascii(name)
    for suffix in QUALITY_SUFFIXES:
        pattern = re.compile(rf"[\s\-_|:]*{suffix}\s*$", re.IGNORECASE)
        result = pattern.sub("", result)
    result = _ARBITRARY_RESOLUTION_RE.sub("", result)
    return result.strip()


def get_quality_tier(stream_name: str) -> str:
    """Return a human-readable quality tier label for a stream name."""
    priority = get_stream_quality_priority(stream_name)
    if priority <= 10:
        return "4K"
    if priority <= 20:
        return "FHD"
    if priority <= 21:
        return "FHD"  # 1080i
    if priority <= 30:
        return "HD"
    if priority <= 40:
        return "SD"
    return "Unknown"


def get_stream_quality_priority(stream_name: str) -> int:
    """Get quality priority score. Lower = higher quality.

    Handles named indicators (4K, UHD, FHD, HD, SD) and arbitrary resolutions.
    """
    upper_name = normalize_unicode_to_ascii(stream_name).upper()

    # Check named quality indicators first
    for quality, priority in QUALITY_PRIORITY.items():
        if _NUMERIC_RESOLUTION_RE.match(quality):
            continue
        pattern = re.compile(
            rf"(?:^|[\s\-_|:]){re.escape(quality)}(?:$|[\s\-_|:])",
            re.IGNORECASE,
        )
        if pattern.search(upper_name):
            return priority

    # Look for arbitrary resolution pattern (e.g. 1080P, 720P, 476P)
    m = _RESOLUTION_SCORE_RE.search(upper_name)
    if m:
        resolution = int(m.group(1))
        if resolution > 0:
            calculated = round(20000 / resolution)
            return max(5, min(60, calculated))

    return DEFAULT_QUALITY_PRIORITY


# ---------------------------------------------------------------------------
# Network prefix / suffix
# ---------------------------------------------------------------------------

def strip_network_prefix(name: str, custom_prefixes: Optional[list[str]] = None) -> str:
    """Strip network prefix (e.g. 'CHAMP |', 'PPV |') from a stream name."""
    trimmed = name.strip()
    all_prefixes = NETWORK_PREFIXES + (custom_prefixes or [])
    # Sort longest first for greedy matching
    sorted_prefixes = sorted(all_prefixes, key=len, reverse=True)

    for prefix in sorted_prefixes:
        pattern = re.compile(
            rf"^{re.escape(prefix)}\s*[|:\-/]\s*(.+)$", re.IGNORECASE
        )
        m = pattern.match(trimmed)
        if m:
            content = m.group(1).strip()
            if len(content) >= 3:
                return content
    return trimmed


def has_network_prefix(name: str, custom_prefixes: Optional[list[str]] = None) -> bool:
    """Check if a stream name has a strippable network prefix."""
    return strip_network_prefix(name, custom_prefixes) != name.strip()


def strip_network_suffix(name: str, custom_suffixes: Optional[list[str]] = None) -> str:
    """Strip network suffix (e.g. '(ENGLISH)', '[LIVE]', 'BACKUP') from a name."""
    result = name.strip()
    all_suffixes = NETWORK_SUFFIXES + (custom_suffixes or [])
    sorted_suffixes = sorted(all_suffixes, key=len, reverse=True)

    for suffix in sorted_suffixes:
        escaped = re.escape(suffix)
        # Pattern 1: Suffix in parentheses
        paren = re.compile(rf"\s*\(\s*{escaped}\s*\)\s*$", re.IGNORECASE)
        if paren.search(result):
            result = paren.sub("", result).strip()
            continue
        # Pattern 2: Suffix in brackets
        bracket = re.compile(rf"\s*\[\s*{escaped}\s*\]\s*$", re.IGNORECASE)
        if bracket.search(result):
            result = bracket.sub("", result).strip()
            continue
        # Pattern 3: Bare suffix with separator
        bare_sep = re.compile(
            rf"^(.{{3,}})[\s\-|:]+{escaped}\s*$", re.IGNORECASE
        )
        m = bare_sep.match(result)
        if m:
            result = m.group(1).strip().rstrip("-|: ")
            continue
        # Pattern 4: Bare suffix with just space
        bare_space = re.compile(rf"^(.{{3,}})\s+{escaped}\s*$", re.IGNORECASE)
        m = bare_space.match(result)
        if m:
            result = m.group(1).strip().rstrip("-|: ")
            continue

    return result


def has_network_suffix(name: str, custom_suffixes: Optional[list[str]] = None) -> bool:
    """Check if a stream name has a strippable network suffix."""
    return strip_network_suffix(name, custom_suffixes) != name.strip()


# ---------------------------------------------------------------------------
# Country prefix
# ---------------------------------------------------------------------------

def get_country_prefix(name: str) -> Optional[str]:
    """Detect country prefix in a stream name. Returns uppercase code or None."""
    trimmed = strip_leading_separators(name.strip())
    for prefix in COUNTRY_PREFIXES:
        pattern = re.compile(rf"^{re.escape(prefix)}(?:[\s:\-|/]+)", re.IGNORECASE)
        if pattern.match(trimmed):
            return prefix.upper()
    return None


def strip_country_prefix(name: str) -> str:
    """Strip country prefix and trailing punctuation from a name."""
    trimmed = strip_leading_separators(name.strip())
    for prefix in COUNTRY_PREFIXES:
        pattern = re.compile(rf"^{re.escape(prefix)}[\s:\-|/]+", re.IGNORECASE)
        if pattern.match(trimmed):
            return pattern.sub("", trimmed).strip()
    return trimmed


# ---------------------------------------------------------------------------
# Regional variants
# ---------------------------------------------------------------------------

def get_regional_suffix(name: str) -> Optional[str]:
    """Check if a stream name has a regional suffix. Returns 'east', 'west', or None."""
    if _REGIONAL_EAST_RE.search(name):
        return "east"
    if _REGIONAL_WEST_RE.search(name):
        return "west"
    return None


def strip_regional_suffix(name: str) -> str:
    """Strip East/West regional suffix from a name."""
    return _REGIONAL_STRIP_RE.sub("", name).strip()


def detect_regional_variants(streams: list[dict]) -> bool:
    """Detect if a list of streams has regional variants (both East and West)."""
    base_names: dict[str, set[str]] = {}
    for stream in streams:
        name_no_quality = strip_quality_suffixes(stream.get("name", ""))
        name_no_quality = re.sub(r"\s+", " ", name_no_quality).strip()
        regional = get_regional_suffix(name_no_quality)
        base = strip_regional_suffix(name_no_quality).lower()
        if base not in base_names:
            base_names[base] = set()
        base_names[base].add(regional or "none")

    for variants in base_names.values():
        has_east_or_none = "east" in variants or "none" in variants
        has_west = "west" in variants
        if has_east_or_none and has_west:
            return True
    return False


def filter_streams_by_timezone(
    streams: list[dict], preference: str
) -> list[dict]:
    """Filter streams by timezone preference ('east', 'west', or 'both')."""
    if preference == "both":
        return streams
    result = []
    for stream in streams:
        name_no_quality = strip_quality_suffixes(stream.get("name", ""))
        regional = get_regional_suffix(name_no_quality)
        if preference == "east":
            if regional == "east" or regional is None:
                result.append(stream)
        elif preference == "west":
            if regional == "west":
                result.append(stream)
    return result


# ---------------------------------------------------------------------------
# Quality sorting with provider interleaving
# ---------------------------------------------------------------------------

def sort_streams_by_quality(streams: list[dict]) -> list[dict]:
    """Sort streams by quality priority, interleaving providers for failover."""
    start = time.time()

    # Group by quality tier
    quality_groups: dict[int, list[dict]] = {}
    for stream in streams:
        priority = get_stream_quality_priority(stream.get("name", ""))
        quality_groups.setdefault(priority, []).append(stream)

    sorted_priorities = sorted(quality_groups.keys())
    result: list[dict] = []

    for priority in sorted_priorities:
        tier_streams = quality_groups[priority]
        # Group by provider within tier
        provider_groups: dict[Optional[int], list[dict]] = {}
        for stream in tier_streams:
            provider_id = stream.get("m3u_account")
            provider_groups.setdefault(provider_id, []).append(stream)

        # Sort provider IDs (None last)
        sorted_provider_ids = sorted(
            provider_groups.keys(),
            key=lambda x: (x is None, x or 0),
        )

        # Round-robin interleave
        iterators = [
            {"streams": provider_groups[pid], "index": 0}
            for pid in sorted_provider_ids
        ]
        has_more = True
        while has_more:
            has_more = False
            for it in iterators:
                if it["index"] < len(it["streams"]):
                    result.append(it["streams"][it["index"]])
                    it["index"] += 1
                    has_more = True

    elapsed = (time.time() - start) * 1000
    logger.debug(
        "[STREAM-NORM] Sorted %d streams by quality in %.1fms",
        len(streams), elapsed,
    )
    return result


# ---------------------------------------------------------------------------
# Batch enrichment — enriches a list of streams with all computed fields
# ---------------------------------------------------------------------------

def enrich_stream(stream: dict) -> StreamEnrichment:
    """Compute all enrichment fields for a single stream."""
    name = stream.get("name", "")
    # Normalized name: strip quality, country, network prefix/suffix
    normalized = strip_quality_suffixes(name)
    normalized = strip_network_prefix(normalized)
    normalized = strip_network_suffix(normalized)
    normalized = strip_country_prefix(normalized)
    normalized = normalized.strip()

    return StreamEnrichment(
        normalized_name=normalized,
        quality_tier=get_quality_tier(name),
        quality_priority=get_stream_quality_priority(name),
        detected_country=get_country_prefix(name),
        detected_network_prefix=(
            name[:len(name) - len(strip_network_prefix(name))].strip(" |:-/")
            if has_network_prefix(name) else None
        ),
        regional_variant=get_regional_suffix(
            strip_quality_suffixes(name)
        ),
    )


def enrich_streams_batch(streams: list[dict]) -> BatchEnrichmentResult:
    """Enrich a batch of streams with normalization metadata.

    Each stream dict must have at least 'id' and 'name' keys.
    Returns enrichments keyed by stream id.
    """
    start = time.time()
    enrichments: dict[int, StreamEnrichment] = {}

    for stream in streams:
        stream_id = stream.get("id")
        if stream_id is None:
            continue
        enrichments[stream_id] = enrich_stream(stream)

    elapsed = (time.time() - start) * 1000
    logger.info(
        "[STREAM-NORM] Enriched %d streams in %.1fms",
        len(enrichments), elapsed,
    )
    return BatchEnrichmentResult(enrichments=enrichments, elapsed_ms=elapsed)
