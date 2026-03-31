"""
EPG Matching Module

Provides functions for matching channels to EPG (Electronic Program Guide) data.
Ported from frontend/src/services/epgMatching.ts.

Key capabilities:
- Batch EPG matching with confidence scoring
- League prefix extraction and matching
- Broadcast call sign detection (K/W stations)
- Country-aware matching from stream metadata
- TVG-ID parsing for structured EPG lookups
- Multi-word EPG search
"""
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from stream_normalization import (
    LEAGUE_PREFIXES,
    QUALITY_SUFFIXES,
    TIMEZONE_SUFFIXES,
    get_country_prefix,
    strip_country_prefix,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_PREFIX_LENGTH = 4

LEAGUE_SUFFIXES = [
    "nfl", "nba", "mlb", "nhl", "mls", "wnba", "ncaa", "cfb", "cbb",
    "epl", "premierleague", "laliga", "bundesliga", "seriea", "ligue1",
    "uefa", "fifa", "f1", "nascar", "pga", "atp", "wta",
    "wwe", "ufc", "aew", "boxing",
]

REGIONAL_VARIANTS = ["east", "west", "pacific", "central", "mountain"]

# ---------------------------------------------------------------------------
# Pre-compiled regex patterns (module level for performance)
# ---------------------------------------------------------------------------

# Broadcast call sign: K or W followed by 2-4 uppercase letters,
# optionally followed by a suffix like DT, TV, HD, LP, CD, CA, LD
_BROADCAST_CALL_SIGN_RE = re.compile(
    r"\b([KW][A-Z]{2,4})(?:[-]?(?:DT|TV|HD|LP|CD|CA|LD))?\b", re.IGNORECASE
)

# Strip channel number prefix (e.g., "535 | ESPN" -> "ESPN")
_CHANNEL_NUMBER_PREFIX_RE = re.compile(r"^\d+\s*\|\s*")

# Strip non-alphanumeric characters for normalization
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")

# Quality suffix patterns (pre-compiled for stripping during normalization)
_QUALITY_STRIP_PATTERNS = [
    re.compile(rf"\b{re.escape(s.lower())}\b") for s in QUALITY_SUFFIXES
]

# Timezone suffix patterns
_TIMEZONE_STRIP_PATTERNS = [
    re.compile(rf"\b{re.escape(s.lower())}\b") for s in TIMEZONE_SUFFIXES
]

# League prefix patterns for extraction (sorted longest first)
_LEAGUE_PREFIX_PATTERNS = sorted(LEAGUE_PREFIXES, key=len, reverse=True)

# TVG-ID separator pattern
_TVG_ID_SEPARATOR_RE = re.compile(r"[._]")

# Country code pattern in TVG-ID (2-3 letter code at end)
_TVG_ID_COUNTRY_RE = re.compile(r"^[A-Za-z]{2,3}$")

# Special punctuation pattern (!, @, #, etc.)
_SPECIAL_PUNCT_RE = re.compile(r"[!@#$%^&*]")

# Regional variant pattern
_REGIONAL_RE = re.compile(
    r"\b(?:east|west|pacific|central|mountain)\b", re.IGNORECASE
)

# HD pattern
_HD_RE = re.compile(r"\bhd\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class EPGMatchWithScore:
    """An EPG entry with its computed match confidence score."""
    epg_id: int
    epg_name: str
    tvg_id: str
    epg_source: dict  # {id, name}
    confidence: int
    match_type: str  # "exact", "prefix", "league", "callsign"


@dataclass
class EPGMatchResult:
    """Result of EPG matching for a single channel."""
    channel_id: int
    channel_name: str
    matches: list[EPGMatchWithScore] = field(default_factory=list)
    best_match: Optional[EPGMatchWithScore] = None
    detected_country: Optional[str] = None
    detected_league: Optional[str] = None


@dataclass
class EPGAssignment:
    """A confirmed EPG assignment for a channel."""
    channel_id: int
    epg_id: int
    epg_name: str
    tvg_id: str
    source_id: int
    confidence: int


@dataclass
class BatchMatchProgress:
    """Progress tracking for batch EPG matching."""
    total: int = 0
    completed: int = 0
    matched: int = 0
    unmatched: int = 0
    elapsed_ms: float = 0.0


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def extract_league_prefix(name: str) -> Optional[dict]:
    """Extract league prefix from a channel name.

    Returns dict with 'league' and 'name' keys, or None if no league found.
    E.g. "NFL RedZone" -> {"league": "NFL", "name": "RedZone"}
    """
    upper_name = name.strip().upper()
    for prefix in _LEAGUE_PREFIX_PATTERNS:
        upper_prefix = prefix.upper()
        if upper_name.startswith(upper_prefix):
            rest = name.strip()[len(prefix):]
            # Must be followed by separator or end of string
            if not rest or rest[0] in (" ", ":", "-", "|", "/"):
                cleaned = rest.lstrip(" :-|/").strip()
                if cleaned:
                    logger.debug(
                        "[EPG-MATCH] Extracted league prefix %s from %s",
                        prefix, name,
                    )
                    return {"league": prefix.upper(), "name": cleaned}
    return None


def extract_broadcast_call_sign(name: str) -> Optional[str]:
    """Extract K/W broadcast call sign from a channel name.

    Returns the base call sign (e.g. "WABC" from "WABC-DT") or None.
    """
    match = _BROADCAST_CALL_SIGN_RE.search(name)
    if match:
        sign = match.group(1).upper()
        logger.debug("[EPG-MATCH] Extracted call sign %s from %s", sign, name)
        return sign
    return None


def detect_country_from_streams(streams: list[dict]) -> Optional[str]:
    """Detect the most common country prefix from a list of streams.

    Examines both stream names and channel group names.
    Returns uppercase country code or None.
    """
    if not streams:
        return None

    logger.debug(
        "[EPG-MATCH] Detecting country from %s streams", len(streams),
    )

    country_counts: dict[str, int] = {}
    for stream in streams:
        # Check stream name
        name = stream.get("name", "")
        country = get_country_prefix(name)
        if country:
            country_counts[country] = country_counts.get(country, 0) + 1

        # Check channel group name
        group = stream.get("channel_group_name", "")
        if group:
            group_country = get_country_prefix(group)
            if group_country:
                country_counts[group_country] = (
                    country_counts.get(group_country, 0) + 1
                )

    if not country_counts:
        return None

    best_country = max(country_counts, key=lambda k: country_counts[k])
    logger.debug(
        "[EPG-MATCH] Detected country %s from streams (count=%s)",
        best_country, country_counts[best_country],
    )
    return best_country


def normalize_for_epg_match(name: str) -> str:
    """Normalize a name for EPG matching.

    Strips country prefix, quality/timezone suffixes, and non-alphanumeric
    characters. Returns lowercase alphanumeric string.
    """
    if not name:
        return ""

    # Strip channel number prefix (e.g., "535 | ESPN" -> "ESPN")
    result = _CHANNEL_NUMBER_PREFIX_RE.sub("", name)

    # Strip country prefix
    result = strip_country_prefix(result)

    # Lowercase for processing
    result = result.lower()

    # Strip quality suffixes
    for pattern in _QUALITY_STRIP_PATTERNS:
        result = pattern.sub("", result)

    # Strip timezone suffixes
    for pattern in _TIMEZONE_STRIP_PATTERNS:
        result = pattern.sub("", result)

    # Remove non-alphanumeric
    result = _NON_ALNUM_RE.sub("", result)

    return result


def normalize_for_epg_match_with_league(name: str) -> dict:
    """Extended normalization that also extracts league info.

    Returns dict with keys: 'normalized', 'league', 'original_name'.
    """
    league_info = extract_league_prefix(name)
    league = None
    name_to_normalize = name

    if league_info:
        league = league_info["league"]
        name_to_normalize = league_info["name"]

    normalized = normalize_for_epg_match(name_to_normalize)

    return {
        "normalized": normalized,
        "league": league,
        "original_name": name,
    }


def parse_tvg_id(tvg_id: str) -> tuple[str, Optional[str], Optional[str]]:
    """Parse a TVG-ID into (normalized_name, country, league).

    TVG-IDs can be in formats like:
    - "CNN.us" -> ("cnn", "US", None)
    - "ESPN_NFL.us" -> ("espn", "US", "NFL")
    - "BBCOne" -> ("bbcone", None, None)
    """
    if not tvg_id:
        return ("", None, None)

    parts = _TVG_ID_SEPARATOR_RE.split(tvg_id)

    country = None
    league = None

    # Check last part for country code
    if len(parts) > 1:
        last_part = parts[-1]
        if _TVG_ID_COUNTRY_RE.match(last_part) and len(last_part) <= 3:
            country = last_part.upper()
            parts = parts[:-1]

    # Rejoin remaining parts and normalize
    raw_name = "".join(parts)

    # Check for league prefix in the name
    league_info = extract_league_prefix(raw_name)
    if league_info:
        league = league_info["league"]
        raw_name = league_info["name"]

    normalized = _NON_ALNUM_RE.sub("", raw_name.lower())

    return (normalized, country, league)


def build_epg_lookup(
    epg_data: list[dict],
) -> dict:
    """Build lookup maps from EPG data for O(1) matching.

    Returns a dict with keys:
    - 'by_normalized_name': dict mapping normalized name -> list of EPG entries
    - 'by_tvg_id': dict mapping normalized TVG-ID name -> list of EPG entries
    - 'by_call_sign': dict mapping call sign -> list of EPG entries
    - 'all_entries': list of all processed EPG entries with normalized data
    """
    start = time.time()
    logger.info(
        "[EPG-MATCH] Building EPG lookup from %s entries", len(epg_data),
    )

    by_normalized_name: dict[str, list[dict]] = {}
    by_tvg_id: dict[str, list[dict]] = {}
    by_call_sign: dict[str, list[dict]] = {}
    all_entries: list[dict] = []

    for epg in epg_data:
        epg_name = epg.get("name", "")
        tvg_id = epg.get("tvg_id", "")
        epg_source = epg.get("epg_source", {})

        # Normalize the EPG name
        norm_info = normalize_for_epg_match_with_league(epg_name)
        normalized_name = norm_info["normalized"]
        league = norm_info["league"]

        # Parse TVG-ID
        tvg_normalized, tvg_country, tvg_league = parse_tvg_id(tvg_id)

        entry = {
            "id": epg.get("id"),
            "name": epg_name,
            "tvg_id": tvg_id,
            "epg_source": epg_source,
            "normalized_name": normalized_name,
            "league": league or tvg_league,
            "country": tvg_country,
            "tvg_normalized": tvg_normalized,
            "call_sign": extract_broadcast_call_sign(epg_name),
        }

        all_entries.append(entry)

        # Index by normalized name
        if normalized_name:
            by_normalized_name.setdefault(normalized_name, []).append(entry)

        # Index by TVG-ID normalized name
        if tvg_normalized and tvg_normalized != normalized_name:
            by_tvg_id.setdefault(tvg_normalized, []).append(entry)

        # Index by call sign
        if entry["call_sign"]:
            by_call_sign.setdefault(
                entry["call_sign"], []
            ).append(entry)

    elapsed = (time.time() - start) * 1000
    logger.info(
        "[EPG-MATCH] Built EPG lookup in %.1fms: "
        "%s name entries, %s tvg-id entries, %s call sign entries",
        elapsed, len(by_normalized_name), len(by_tvg_id), len(by_call_sign),
    )

    return {
        "by_normalized_name": by_normalized_name,
        "by_tvg_id": by_tvg_id,
        "by_call_sign": by_call_sign,
        "all_entries": all_entries,
    }


def _compute_confidence(
    channel_normalized: str,
    channel_league: Optional[str],
    channel_country: Optional[str],
    channel_call_sign: Optional[str],
    epg_entry: dict,
    match_type: str,
) -> int:
    """Compute confidence score for a channel-EPG match.

    Scoring (100 points total):
    - Country OR League match: 40 points
    - Exact vs prefix match: 25 points
    - Name length similarity: 20 points
    - Call sign match: 10 points
    - HD variant: 5 points
    """
    score = 0

    epg_normalized = epg_entry["normalized_name"]
    epg_league = epg_entry.get("league")
    epg_country = epg_entry.get("country")
    epg_call_sign = epg_entry.get("call_sign")

    # Country or League match: 40 points
    if channel_league and epg_league:
        if channel_league.upper() == epg_league.upper():
            score += 40
    elif channel_country and epg_country:
        if channel_country.upper() == epg_country.upper():
            score += 40

    # Exact vs prefix match: 25 points
    if match_type == "exact":
        score += 25
    elif match_type == "prefix" and len(channel_normalized) > MIN_PREFIX_LENGTH:
        # Partial credit for prefix matches
        score += 15

    # Name length similarity: 20 points
    if channel_normalized and epg_normalized:
        len_ratio = min(len(channel_normalized), len(epg_normalized)) / max(
            len(channel_normalized), len(epg_normalized), 1
        )
        score += int(len_ratio * 20)

    # Call sign match: 10 points
    if channel_call_sign and epg_call_sign:
        if channel_call_sign == epg_call_sign:
            score += 10

    # HD variant: 5 points
    if _HD_RE.search(epg_entry.get("name", "")):
        score += 5

    return score


def _sort_matches(
    matches: list[EPGMatchWithScore],
    channel_league: Optional[str],
    channel_normalized: str,
) -> list[EPGMatchWithScore]:
    """Sort matches by priority rules.

    Sort priority:
    1. League match (if channel has league prefix)
    2. Country match
    3. Exact over prefix (for names > 2 chars)
    4. Special punctuation match
    5. Channel-is-prefix-of-EPG preferred
    6. Length similarity (closer = better)
    7. Regional variant matching
    8. HD + call sign combined scoring
    9. Alphabetical
    """

    def sort_key(m: EPGMatchWithScore) -> tuple:
        # Higher confidence first (negate for descending)
        # Then apply tiebreaker rules
        epg_norm = normalize_for_epg_match(m.epg_name)

        # 1. League match bonus
        league_bonus = 0
        if channel_league and m.match_type == "league":
            league_bonus = -1  # Sort first

        # 2. Country (encoded in confidence already)
        # 3. Exact over prefix
        exact_bonus = 0 if m.match_type == "exact" else 1

        # 4. Special punctuation
        has_special = 1 if _SPECIAL_PUNCT_RE.search(m.epg_name) else 0

        # 5. Channel is prefix of EPG
        is_prefix = 0 if epg_norm.startswith(channel_normalized) else 1

        # 6. Length similarity
        len_diff = abs(len(channel_normalized) - len(epg_norm))

        # 7. Regional variant
        has_regional = 1 if _REGIONAL_RE.search(m.epg_name) else 0

        # 8. HD + call sign (encoded in confidence)
        # 9. Alphabetical
        return (
            league_bonus,
            -m.confidence,
            exact_bonus,
            has_special,
            is_prefix,
            len_diff,
            has_regional,
            m.epg_name.lower(),
        )

    return sorted(matches, key=sort_key)


def find_epg_matches_with_lookup(
    channel: dict,
    streams: list[dict],
    lookup: dict,
    source_order: Optional[dict[int, int]] = None,
) -> EPGMatchResult:
    """Core matching: find EPG matches for a channel using pre-built lookup.

    Args:
        channel: dict with 'id', 'name', 'streams' keys
        streams: list of stream dicts for this channel
        lookup: pre-built lookup from build_epg_lookup()
        source_order: optional dict mapping source_id -> priority order

    Returns:
        EPGMatchResult with sorted matches and best match
    """
    start = time.time()
    channel_id = channel.get("id")
    channel_name = channel.get("name", "")

    logger.debug(
        "[EPG-MATCH] Finding matches for channel %s: %s",
        channel_id, channel_name,
    )

    # Normalize channel name
    norm_info = normalize_for_epg_match_with_league(channel_name)
    channel_normalized = norm_info["normalized"]
    channel_league = norm_info["league"]
    channel_call_sign = extract_broadcast_call_sign(channel_name)

    # Detect country from streams
    channel_country = detect_country_from_streams(streams)

    result = EPGMatchResult(
        channel_id=channel_id,
        channel_name=channel_name,
        detected_country=channel_country,
        detected_league=channel_league,
    )

    if not channel_normalized:
        return result

    seen_epg_ids: set[int] = set()
    matches: list[EPGMatchWithScore] = []

    by_name = lookup["by_normalized_name"]
    by_tvg = lookup["by_tvg_id"]
    by_sign = lookup["by_call_sign"]

    # 1. Exact name matches
    if channel_normalized in by_name:
        for entry in by_name[channel_normalized]:
            epg_id = entry["id"]
            if epg_id in seen_epg_ids:
                continue
            seen_epg_ids.add(epg_id)
            confidence = _compute_confidence(
                channel_normalized, channel_league, channel_country,
                channel_call_sign, entry, "exact",
            )
            matches.append(EPGMatchWithScore(
                epg_id=epg_id,
                epg_name=entry["name"],
                tvg_id=entry["tvg_id"],
                epg_source=entry["epg_source"],
                confidence=confidence,
                match_type="exact",
            ))

    # 2. Exact TVG-ID matches
    if channel_normalized in by_tvg:
        for entry in by_tvg[channel_normalized]:
            epg_id = entry["id"]
            if epg_id in seen_epg_ids:
                continue
            seen_epg_ids.add(epg_id)
            confidence = _compute_confidence(
                channel_normalized, channel_league, channel_country,
                channel_call_sign, entry, "exact",
            )
            matches.append(EPGMatchWithScore(
                epg_id=epg_id,
                epg_name=entry["name"],
                tvg_id=entry["tvg_id"],
                epg_source=entry["epg_source"],
                confidence=confidence,
                match_type="exact",
            ))

    # 3. Prefix matches (only if channel name long enough)
    if len(channel_normalized) >= MIN_PREFIX_LENGTH:
        for norm_name, entries in by_name.items():
            if norm_name.startswith(channel_normalized) or (
                channel_normalized.startswith(norm_name)
                and len(norm_name) >= MIN_PREFIX_LENGTH
            ):
                if norm_name == channel_normalized:
                    continue  # Already handled as exact
                for entry in entries:
                    epg_id = entry["id"]
                    if epg_id in seen_epg_ids:
                        continue
                    seen_epg_ids.add(epg_id)
                    confidence = _compute_confidence(
                        channel_normalized, channel_league, channel_country,
                        channel_call_sign, entry, "prefix",
                    )
                    matches.append(EPGMatchWithScore(
                        epg_id=epg_id,
                        epg_name=entry["name"],
                        tvg_id=entry["tvg_id"],
                        epg_source=entry["epg_source"],
                        confidence=confidence,
                        match_type="prefix",
                    ))

        # Also check TVG-ID prefix matches
        for tvg_name, entries in by_tvg.items():
            if tvg_name.startswith(channel_normalized) or (
                channel_normalized.startswith(tvg_name)
                and len(tvg_name) >= MIN_PREFIX_LENGTH
            ):
                if tvg_name == channel_normalized:
                    continue
                for entry in entries:
                    epg_id = entry["id"]
                    if epg_id in seen_epg_ids:
                        continue
                    seen_epg_ids.add(epg_id)
                    confidence = _compute_confidence(
                        channel_normalized, channel_league, channel_country,
                        channel_call_sign, entry, "prefix",
                    )
                    matches.append(EPGMatchWithScore(
                        epg_id=epg_id,
                        epg_name=entry["name"],
                        tvg_id=entry["tvg_id"],
                        epg_source=entry["epg_source"],
                        confidence=confidence,
                        match_type="prefix",
                    ))

    # 4. Call sign matches
    if channel_call_sign and channel_call_sign in by_sign:
        for entry in by_sign[channel_call_sign]:
            epg_id = entry["id"]
            if epg_id in seen_epg_ids:
                continue
            seen_epg_ids.add(epg_id)
            confidence = _compute_confidence(
                channel_normalized, channel_league, channel_country,
                channel_call_sign, entry, "callsign",
            )
            matches.append(EPGMatchWithScore(
                epg_id=epg_id,
                epg_name=entry["name"],
                tvg_id=entry["tvg_id"],
                epg_source=entry["epg_source"],
                confidence=confidence,
                match_type="callsign",
            ))

    # 5. League-specific matches
    if channel_league:
        league_lower = channel_league.lower()
        for entry in lookup["all_entries"]:
            epg_id = entry["id"]
            if epg_id in seen_epg_ids:
                continue
            entry_league = (entry.get("league") or "").lower()
            if entry_league == league_lower:
                # Check if names are similar enough
                epg_norm = entry["normalized_name"]
                if (
                    epg_norm.startswith(channel_normalized)
                    or channel_normalized.startswith(epg_norm)
                ) and len(epg_norm) >= MIN_PREFIX_LENGTH:
                    seen_epg_ids.add(epg_id)
                    confidence = _compute_confidence(
                        channel_normalized, channel_league, channel_country,
                        channel_call_sign, entry, "league",
                    )
                    matches.append(EPGMatchWithScore(
                        epg_id=epg_id,
                        epg_name=entry["name"],
                        tvg_id=entry["tvg_id"],
                        epg_source=entry["epg_source"],
                        confidence=confidence,
                        match_type="league",
                    ))

    # Apply source order preference if provided
    if source_order and matches:
        for match in matches:
            source_id = None
            if isinstance(match.epg_source, dict):
                source_id = match.epg_source.get("id")
            if source_id is not None and source_id in source_order:
                # Boost confidence slightly for preferred sources
                order = source_order[source_id]
                if order == 0:
                    match.confidence = min(100, match.confidence + 3)
                elif order == 1:
                    match.confidence = min(100, match.confidence + 1)

    # Sort matches
    matches = _sort_matches(matches, channel_league, channel_normalized)

    result.matches = matches
    result.best_match = matches[0] if matches else None

    elapsed = (time.time() - start) * 1000
    if elapsed > 10:
        logger.debug(
            "[EPG-MATCH] Channel %s matching took %.1fms (%s matches)",
            channel_id, elapsed, len(matches),
        )

    return result


def matches_epg_search(
    epg: dict,
    search_words: list[str],
    source_name: Optional[str] = None,
) -> bool:
    """Check if an EPG entry matches a multi-word search query.

    All search words must match against the EPG name, TVG-ID, or source name.
    """
    if not search_words:
        return True

    epg_name = (epg.get("name") or "").lower()
    tvg_id = (epg.get("tvg_id") or "").lower()
    epg_source_name = ""
    if isinstance(epg.get("epg_source"), dict):
        epg_source_name = (epg["epg_source"].get("name") or "").lower()

    searchable = f"{epg_name} {tvg_id} {epg_source_name}"
    if source_name:
        searchable = f"{searchable} {source_name.lower()}"

    for word in search_words:
        if word.lower() not in searchable:
            return False

    return True


def batch_find_epg_matches(
    channels: list[dict],
    all_streams: list[dict],
    epg_data: list[dict],
    source_order: Optional[dict[int, int]] = None,
) -> list[EPGMatchResult]:
    """Batch process EPG matching for multiple channels.

    Args:
        channels: list of channel dicts with 'id', 'name', 'streams' keys
        all_streams: list of all stream dicts with 'id', 'name',
                     'channel_group_name' keys
        epg_data: list of EPG entry dicts with 'id', 'name', 'tvg_id',
                  'epg_source' keys
        source_order: optional dict mapping EPG source_id -> priority (0=best)

    Returns:
        List of EPGMatchResult, one per channel
    """
    start = time.time()
    logger.info(
        "[EPG-MATCH] Batch matching %s channels against %s EPG entries "
        "with %s streams",
        len(channels), len(epg_data), len(all_streams),
    )

    # Build stream lookup by ID for fast access
    stream_by_id: dict[int, dict] = {}
    for stream in all_streams:
        sid = stream.get("id")
        if sid is not None:
            stream_by_id[sid] = stream

    # Build EPG lookup
    lookup = build_epg_lookup(epg_data)

    progress = BatchMatchProgress(total=len(channels))
    results: list[EPGMatchResult] = []

    for channel in channels:
        # Gather streams for this channel
        channel_stream_ids = channel.get("streams", [])
        channel_streams = [
            stream_by_id[sid]
            for sid in channel_stream_ids
            if sid in stream_by_id
        ]

        # Find matches
        match_result = find_epg_matches_with_lookup(
            channel, channel_streams, lookup, source_order,
        )
        results.append(match_result)

        progress.completed += 1
        if match_result.best_match:
            progress.matched += 1
        else:
            progress.unmatched += 1

    elapsed = (time.time() - start) * 1000
    progress.elapsed_ms = elapsed

    logger.info(
        "[EPG-MATCH] Batch matching complete in %.1fms: "
        "%s/%s matched, %s unmatched",
        elapsed, progress.matched, progress.total, progress.unmatched,
    )

    return results
