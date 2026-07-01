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

# EPG source priority is a banded tiebreaker, NOT a confidence override.
# Candidates whose confidence scores fall in the same band (width
# PRIORITY_TIEBREAK_BAND) are reordered so the higher-priority source wins;
# a candidate more than one band better on confidence is never demoted.
# N=5 is the smallest meaningful confidence signal (HD variant = 5 pts), so
# priority only arbitrates genuine near-ties. See bead s5vyz.1 for the decision.
PRIORITY_TIEBREAK_BAND = 5

# Sentinel rank for an EPG source absent from the priority map: sorts after
# every ranked source within a confidence band (but never reorders across bands).
_UNRANKED_SOURCE = 1_000_000

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

# --- Token-overlap signal (a6445) ---------------------------------------
# Among same-call-sign candidates, the entry sharing MORE of the channel
# name's meaningful tokens (call sign AND network/affiliate/city) should win.
# Tokens are derived from the SPACED cleaned core (engine.extract_core_name)
# before it is flattened into the match key, because flattening destroys word
# boundaries ("FOX WTVT Tampa" -> "foxwtvttampa").
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")

# Subchannel / feed markers ("DT", "DT2", "DT3", ...): identity-free noise that
# never distinguishes the correct network, so drop them from token sets.
_SUBCHANNEL_TOKEN_RE = re.compile(r"^d?t?\d+$|^dt$")

# Generic tokens that carry no network/affiliate identity and would only create
# spurious overlap if counted (format/quality markers, bare articles).
_TOKEN_STOPWORDS = frozenset(
    {"dt", "tv", "hd", "sd", "uhd", "fhd", "4k", "the"}
)

# Token-set match QUALITY (m4hp1): precision-aware, not just recall.
# A candidate is rewarded for SHARING the channel's meaningful tokens AND
# penalised for carrying EXTRA tokens the channel does not have (e.g. the noise
# in "TW BAK KABC B/O (ABC)" or the diginet brand in "KCOP-DT4 (HEROICN)").
# The clean primary-feed network entry (whose tokens are a subset of the
# channel's) therefore strictly outranks a noisier same-call-sign sibling.
#
# quality = min(shared, CAP) * SHARED_WEIGHT - min(extra, EXTRA_CAP) * EXTRA_WEIGHT
#
# Magnitudes are tuned to stay BELOW the country/league (40) and exact (25)
# tiers so a fuzzy call-sign match can never beat an exact/country-preferred
# match: the positive range is capped at 36 (3 * 12) and the penalty at 16
# (4 * 4). One extra SHARED token (12) outweighs four extra noise tokens, and
# every shared token outweighs the per-token noise penalty, so recall still
# dominates precision — extra tokens only break what would otherwise be ties.
_TOKEN_SHARED_WEIGHT = 12
_TOKEN_SHARED_CAP = 3
_TOKEN_EXTRA_WEIGHT = 4
_TOKEN_EXTRA_CAP = 4

# --- Parenthetical-acronym match weight (bd-6rz70) ----------------------
# An "acronym" candidate comes from an EXACT dict-key lookup against a
# parenthetical abbreviation Dispatcharr embeds in the tvg_id (and sometimes
# the display name) — e.g. "OWN" resolves via OprahWinfreyNetwork(OWN).us,
# "HGTV" via Home&GardenTelevision(HGTV).us. This is NOT a fuzzy/partial
# signal like "prefix" (15pts): the acronym is the deliberate, canonical
# short identifier for the network, and the lookup matches it exactly, not
# via a substring guess. It is therefore weighted EQUAL to "exact" (25pts)
# rather than below it — matching the acronym carries the same certainty as
# matching the full flattened name/tvg_id, just through a different (and,
# for channels literally named after the acronym, e.g. "890 | Fetv", the
# ONLY working) field.
#
# It is deliberately NOT weighted ABOVE "exact": a full name/tvg_id match
# remains the most complete identity signal when it is available; acronym
# matching only kicks in as an alternate discovery path when the flattened
# name/tvg_id blob buries the acronym mid-string where no boundary check can
# find it (see parse_tvg_id / epg_match_key). build_epg_lookup folds the
# extracted acronym into the entry's match_tokens set, so when multiple
# candidates share the same acronym (e.g. HGTV entries from several
# countries), the token-overlap term below still discriminates between them
# via the surrounding network-name tokens (and, when stream data is
# available, country/league +40 discriminates further).
_ACRONYM_MATCH_WEIGHT = 25

# Parse a broadcast subchannel ("digital subchannel") number from a RAW EPG or
# channel display name. The ATSC convention is CALLSIGN-DT for the primary feed
# and CALLSIGN-DT2/-DT3/... for diginets. Primary feed (no marker, "-DT", or
# "-DT1") is treated as subchannel 1; "-DT2" and up are the higher diginets.
# Used ONLY as a tie-break so the clean primary feed wins when two candidates
# are otherwise indistinguishable (identical tokens, e.g. KOCE-DT vs KOCE-DT2).
_SUBCHANNEL_RE = re.compile(r"[-\s]DT(\d*)\b", re.IGNORECASE)


def _parse_subchannel(name: str) -> int:
    """Return the broadcast subchannel number (1 = primary feed, >=2 diginet).

    Names without an ATSC ``-DT`` marker, or with a bare ``-DT``/``-DT1``, are
    treated as the primary feed (1). ``-DT2``/``-DT3``/``-DT4`` -> 2/3/4. This is
    a pure tie-break signal; it never changes how strongly a name matches.
    """
    if not name:
        return 1
    m = _SUBCHANNEL_RE.search(name)
    if not m:
        return 1
    digits = m.group(1)
    if not digits:
        return 1
    try:
        n = int(digits)
    except ValueError:
        return 1
    return n if n >= 1 else 1


def _extract_match_tokens(name: str, engine=None) -> frozenset:
    """Derive a set of meaningful tokens from a channel/EPG display name.

    Tokens come from the SPACED core name (``extract_core_name(..., for_match
    ing=True)`` when an engine is supplied; the raw name on the deprecated
    engine-less path). Subchannel markers and generic format words are dropped
    so the overlap reflects real network/affiliate/city identity, not noise.

    A standalone digit-run token is fused onto the immediately preceding
    token (e.g. ["espn", "2"] -> ["espn2"]) before the drop filters run
    (bd-gl3sd). Without this, a spaced channel name like "ESPN 2" and its
    fused EPG counterpart "ESPN2" — which already collapse to the IDENTICAL
    match key via epg_match_key's flatten-and-strip — produce disjoint token
    sets ({"espn"} vs {"espn2"}), because the lone digit "2" is dropped by
    the length filter while "espn2" survives whole. That let a coincidental
    "ESPN HD" prefix match outscore the correct "ESPN2" exact match on the
    token-overlap term alone. The fuse is intentionally conservative:
    - Only merges into the RAW previous token (not an already-fused one), so
      two independent digit runs ("Channel 4 +1" -> "4", "1") never get
      glued into one bogus number ("channel41").
    - Skips fusing onto a stopword or subchannel-marker predecessor ("dt",
      "hd", ...), so "WUNC-DT 20 (PBS)" keeps dropping "dt" and "20"
      independently exactly as before, and "Eurosport 360 HD 8" doesn't let
      "8" smuggle through fused to "hd" as "hd8" (which would evade both the
      stopword and subchannel filters as one new token).
    """
    if not name:
        return frozenset()

    core = engine.extract_core_name(name, for_matching=True) if engine else name

    raw_tokens = _TOKEN_SPLIT_RE.split(core.lower())
    fused_tokens: list[str] = []
    for i, tok in enumerate(raw_tokens):
        prev = raw_tokens[i - 1] if i > 0 else ""
        if (
            tok.isdigit()
            and fused_tokens
            and prev
            and not prev.isdigit()
            and prev not in _TOKEN_STOPWORDS
            and not _SUBCHANNEL_TOKEN_RE.match(prev)
        ):
            fused_tokens[-1] += tok
        else:
            fused_tokens.append(tok)

    tokens = set()
    for tok in fused_tokens:
        if len(tok) < 2:
            continue
        if tok in _TOKEN_STOPWORDS or _SUBCHANNEL_TOKEN_RE.match(tok):
            continue
        tokens.add(tok)
    return frozenset(tokens)


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
    match_type: str  # "exact", "prefix", "league", "callsign", "acronym"
    # The EPG name's match key, carried from the lookup entry so _sort_matches
    # reuses it instead of re-normalizing (bd-xxzxe). Empty for objects built
    # outside the lookup path (e.g. direct unit-test construction); _sort_matches
    # falls back to recomputing in that case.
    epg_normalized: str = ""
    # Broadcast subchannel number (1 = primary feed, >=2 diginet), carried from
    # the lookup entry (m4hp1). Used by _sort_matches ONLY as a tie-break so the
    # primary feed wins when two candidates are otherwise indistinguishable.
    # Defaults to 1 (neutral) for objects built outside the lookup path.
    subchannel: int = 1


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


# Residual leading-digit sweep — EPG-MATCH-KEY ONLY (bd-xxzxe). After the engine
# has stripped channel-number noise and we have flattened to alnum, sweep any
# residual leading digits ("5033cw" -> "cw") so a separator the engine missed
# still can't block a match. This MUST NOT live in the engine: it would eat
# brand digits like "3ABN"/"9Gem". Here it is safe because the key is a
# match-only artifact, never a stored/displayed core name.
_RESIDUAL_LEADING_DIGITS_RE = re.compile(r"^\d+")


def epg_match_key(engine, name: str) -> str:
    """Build the EPG match key for a display name via the shared engine.

    This is the ONE consolidated EPG normalization (bd-xxzxe). It runs the
    shared ``NormalizationEngine.extract_core_name(name, for_matching=True)``
    (which strips channel-number noise, country/quality, AND timezone), then
    folds ``+``/``&`` to words and flattens to lowercase alphanumeric. A final
    EPG-only residual leading-digit sweep catches any separator the engine
    missed.

    Args:
        engine: a ``NormalizationEngine`` bound to a DB session.
        name: the channel or EPG display name.
    """
    if not name:
        return ""

    core = engine.extract_core_name(name, for_matching=True)

    # Fold semantic punctuation BEFORE flattening so "AMC+" -> "amcplus",
    # "A&E" -> "aande" (channel and EPG fold identically, so they still match).
    core = core.replace("+", "plus").replace("&", "and")

    key = _NON_ALNUM_RE.sub("", core.lower())

    # EPG-only residual leading-digit sweep (NOT in the engine — see above).
    key = _RESIDUAL_LEADING_DIGITS_RE.sub("", key)

    return key


def normalize_for_epg_match(name: str, engine=None) -> str:
    """Normalize a name for EPG matching.

    When ``engine`` is supplied, delegates to the shared ``NormalizationEngine``
    via :func:`epg_match_key` — the canonical path (bd-xxzxe).

    When ``engine`` is omitted this falls back to the legacy stateless
    stream_normalization rules. **DEPRECATED**: the legacy path exists only for
    backward compatibility with call sites that have no DB session; production
    EPG matching always passes an engine. New code should call
    :func:`epg_match_key` directly.
    """
    if engine is not None:
        return epg_match_key(engine, name)

    if not name:
        return ""

    # --- legacy stateless path (deprecated) ---
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


def normalize_for_epg_match_with_league(name: str, engine=None) -> dict:
    """Extended normalization that also extracts league info.

    League extraction is shared; only the key computation differs by path. When
    ``engine`` is supplied the remainder is keyed via :func:`epg_match_key`
    (the canonical shared-engine path, bd-xxzxe); otherwise the deprecated
    legacy stateless path is used.

    Returns dict with keys: 'normalized', 'league', 'original_name'.
    """
    league_info = extract_league_prefix(name)
    league = None
    name_to_normalize = name

    if league_info:
        league = league_info["league"]
        name_to_normalize = league_info["name"]

    normalized = normalize_for_epg_match(name_to_normalize, engine=engine)

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


# Parenthetical acronym embedded in a tvg_id or display name, e.g.
# "FamilyEntertainmentTelevision(FETV).us" or "Family Entertainment
# Television (FETV)". Captures the FIRST parenthetical group — matches the
# precedent in auto_creation_executor._match_epg_data's call-sign extraction
# (bd-6rz70), which is a different, non-bulk matching path but established
# the pattern.
_PAREN_ACRONYM_RE = re.compile(r"\(([^)]+)\)")

# An extracted acronym shorter than this is too generic to trust as an exact
# lookup key (collision risk, e.g. stray single-letter annotations) — same
# 2-character floor _extract_match_tokens already applies to any token.
_MIN_ACRONYM_LENGTH = 2


def extract_paren_acronym(text: str) -> Optional[str]:
    """Extract a parenthetical acronym from a TVG-ID or display name.

    Dispatcharr's guide database often encodes a network's canonical short
    acronym as a parenthetical INSIDE the tvg_id (and sometimes the display
    name), e.g. ``FamilyEntertainmentTelevision(FETV).us`` or
    ``Family Entertainment Television (FETV)``. ``epg_match_key`` flattens
    the whole string (parens included) into one blob, which buries the
    acronym mid-string where no ``.startswith()``/``.endswith()`` check can
    find it — this extracts it directly instead (bd-6rz70).

    The acronym is a bare token, not a display name needing tag-stripping,
    so it is normalized with the plain ``_NON_ALNUM_RE`` + lowercase fold —
    NOT routed through ``epg_match_key``'s full engine pipeline.

    Returns the normalized acronym, or ``None`` if no parenthetical is
    present or it normalizes to fewer than ``_MIN_ACRONYM_LENGTH`` characters.
    """
    if not text:
        return None
    match = _PAREN_ACRONYM_RE.search(text)
    if not match:
        return None
    acronym = _NON_ALNUM_RE.sub("", match.group(1).lower())
    if len(acronym) < _MIN_ACRONYM_LENGTH:
        return None
    return acronym


def build_epg_lookup(
    epg_data: list[dict],
    engine=None,
) -> dict:
    """Build lookup maps from EPG data for O(1) matching.

    Args:
        epg_data: list of EPG entry dicts.
        engine: optional ``NormalizationEngine`` (bd-xxzxe). When supplied, EPG
            names are keyed through the shared engine via :func:`epg_match_key`;
            otherwise the deprecated legacy normalizer is used.

    Returns a dict with keys:
    - 'by_normalized_name': dict mapping normalized name -> list of EPG entries
    - 'by_tvg_id': dict mapping normalized TVG-ID name -> list of EPG entries
    - 'by_call_sign': dict mapping call sign -> list of EPG entries
    - 'by_acronym': dict mapping normalized parenthetical acronym (extracted
      from tvg_id or name, e.g. "OWN") -> list of EPG entries (bd-6rz70)
    - 'all_entries': list of all processed EPG entries with normalized data
    """
    start = time.time()
    logger.info(
        "[EPG-MATCH] Building EPG lookup from %s entries", len(epg_data),
    )

    by_normalized_name: dict[str, list[dict]] = {}
    by_tvg_id: dict[str, list[dict]] = {}
    by_call_sign: dict[str, list[dict]] = {}
    by_acronym: dict[str, list[dict]] = {}
    all_entries: list[dict] = []

    for epg in epg_data:
        epg_name = epg.get("name", "")
        tvg_id = epg.get("tvg_id", "")
        epg_source = epg.get("epg_source", {})

        # Normalize the EPG name
        norm_info = normalize_for_epg_match_with_league(epg_name, engine=engine)
        normalized_name = norm_info["normalized"]
        league = norm_info["league"]

        # Parse TVG-ID
        tvg_normalized, tvg_country, tvg_league = parse_tvg_id(tvg_id)

        # Parenthetical acronym extraction (bd-6rz70): Dispatcharr often
        # buries a network's canonical acronym as "(ACRONYM)" inside the
        # tvg_id, and sometimes inside the display name too. Extract both
        # explicitly — the tvg_id form is invisible to normalized_name (it's
        # flattened into a long blob) and the name form CAN be buried by
        # flattening too (e.g. "Family Entertainment Television (FETV)" ->
        # "familyentertainmenttelevisionfetv" is not a prefix of "fetv").
        tvg_acronym = extract_paren_acronym(tvg_id)
        name_acronym = extract_paren_acronym(epg_name)
        acronym_tokens = frozenset(
            a for a in (tvg_acronym, name_acronym) if a
        )

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
            # Meaningful tokens for the overlap signal (a6445), extended
            # with any parenthetical acronym (bd-6rz70) — the acronym IS a
            # network identity token, on par with a call sign, so folding it
            # in lets the token-quality term discriminate between multiple
            # candidates that share the same acronym (e.g. HGTV entries from
            # several countries). A no-op when the acronym already appears
            # in the display name (already tokenized by the split below).
            "match_tokens": (
                _extract_match_tokens(epg_name, engine=engine)
                | acronym_tokens
            ),
            # Broadcast subchannel number parsed from the RAW name (m4hp1).
            # Primary feed (no marker / -DT / -DT1) = 1; diginets -DT2.. >= 2.
            # Pure tie-break: prefer the primary feed when tokens tie.
            "subchannel": _parse_subchannel(epg_name),
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

        # Index by parenthetical acronym (bd-6rz70). acronym_tokens is a set
        # derived from THIS entry alone, so each key gets the entry once.
        for acronym in acronym_tokens:
            by_acronym.setdefault(acronym, []).append(entry)

    elapsed = (time.time() - start) * 1000
    logger.info(
        "[EPG-MATCH] Built EPG lookup in %.1fms: "
        "%s name entries, %s tvg-id entries, %s call sign entries, "
        "%s acronym entries",
        elapsed, len(by_normalized_name), len(by_tvg_id), len(by_call_sign),
        len(by_acronym),
    )

    return {
        "by_normalized_name": by_normalized_name,
        "by_tvg_id": by_tvg_id,
        "by_call_sign": by_call_sign,
        "by_acronym": by_acronym,
        "all_entries": all_entries,
    }


def _compute_confidence(
    channel_normalized: str,
    channel_league: Optional[str],
    channel_country: Optional[str],
    channel_call_sign: Optional[str],
    epg_entry: dict,
    match_type: str,
    channel_tokens: Optional[frozenset] = None,
) -> int:
    """Compute confidence score for a channel-EPG match.

    Scoring:
    - Country OR League match: 40 points
    - Exact vs acronym vs prefix match: 25 / 25 / 15 points (bd-6rz70: an
      "acronym" candidate is an exact dict-key lookup on a parenthetical
      abbreviation, weighted equal to "exact" — see _ACRONYM_MATCH_WEIGHT)
    - Token-set match quality: +36 .. -16 points (m4hp1 — PRIMARY same-bucket
      discriminator: rewards shared tokens, penalises extra/noise EPG tokens)
    - Call sign match: 10 points
    - HD variant: 5 points

    The token-quality term (m4hp1, evolving a6445) rewards candidates that share
    more of the channel name's meaningful tokens (call sign AND
    network/affiliate/city) AND penalises candidates carrying EXTRA tokens the
    channel lacks (the "TW BAK"/"Stream"/"HEROICN"/"THENEST" noise). It is
    weighted above call sign so that, among entries in the same call-sign
    bucket, the clean primary-feed network entry wins.

    The former name-length-similarity term was REMOVED (m4hp1): it rewarded
    coincidental character counts and actively mis-ranked — a noisier subchannel
    whose flattened name happened to be closer in length to the channel key
    would beat the clean network entry (it ranked 'KABC-DT Stream (ABC)' and
    'WTVT-DT2 (MVIES)' over the correct feed). Token-set quality, not length, is
    the same-bucket discriminator now; genuine ties are broken in _sort_matches
    (subchannel number, then deterministic keys).
    """
    score = 0

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

    # Exact vs acronym vs prefix match: 25 / 25 / 15 points
    if match_type == "exact":
        score += 25
    elif match_type == "acronym":
        # bd-6rz70: exact dict-key lookup on a parenthetical acronym — see
        # _ACRONYM_MATCH_WEIGHT for the full weighting rationale.
        score += _ACRONYM_MATCH_WEIGHT
    elif match_type == "prefix" and len(channel_normalized) > MIN_PREFIX_LENGTH:
        # Partial credit for prefix matches
        score += 15

    # Token-set match quality (m4hp1): reward shared tokens, penalise extra
    # EPG tokens absent from the channel. Skipped entirely when either side
    # lacks tokens (e.g. legacy direct callers) so those scores are unchanged.
    epg_tokens = epg_entry.get("match_tokens")
    if channel_tokens and epg_tokens:
        shared = len(channel_tokens & epg_tokens)
        extra = len(epg_tokens - channel_tokens)
        score += (
            min(shared, _TOKEN_SHARED_CAP) * _TOKEN_SHARED_WEIGHT
            - min(extra, _TOKEN_EXTRA_CAP) * _TOKEN_EXTRA_WEIGHT
        )

    # Call sign match: 10 points
    if channel_call_sign and epg_call_sign:
        if channel_call_sign == epg_call_sign:
            score += 10

    # HD variant: 5 points
    if _HD_RE.search(epg_entry.get("name", "")):
        score += 5

    return score


def _epg_source_id(epg_source) -> Optional[int]:
    """Resolve an EPG source id from either a ``{id, name}`` dict or a bare int.

    Dispatcharr's epgdata may serialize ``epg_source`` as a nested object or as
    a plain foreign-key id depending on version; handle both.
    """
    if isinstance(epg_source, dict):
        return epg_source.get("id")
    if isinstance(epg_source, int):
        return epg_source
    return None


def build_source_priority_order(epg_sources: list[dict]) -> dict[int, int]:
    """Map EPG ``source_id -> rank`` where rank 0 is the most-preferred source.

    Sources are ranked by their ``priority`` field DESCENDING — higher priority
    number = more preferred, matching the EPG Manager UI convention
    (EPGManagerTab sorts ``b.priority - a.priority``). Ties broken by source id
    so the ranking is deterministic regardless of fetch order.
    """
    ranked = sorted(
        (s for s in epg_sources if s.get("id") is not None),
        key=lambda s: (-(s.get("priority") or 0), s["id"]),
    )
    return {s["id"]: rank for rank, s in enumerate(ranked)}


def _sort_matches(
    matches: list[EPGMatchWithScore],
    channel_league: Optional[str],
    channel_normalized: str,
    source_order: Optional[dict[int, int]] = None,
) -> list[EPGMatchWithScore]:
    """Sort matches by priority rules.

    Sort priority:
    1. League match (if channel has league prefix)
    2. Confidence band (higher confidence first, bucketed by
       PRIORITY_TIEBREAK_BAND)
    3. EPG source priority — within a confidence band only (rank 0 = best)
    4. Raw confidence (higher first, within band + source)
    5. Subchannel number (m4hp1: primary feed before diginet) — a pure
       tie-break, applied only AFTER token-quality/confidence already tied, so
       it never beats a genuinely higher-overlap candidate (e.g. won't let
       KCOP-DT beat a real KCOP-DT4 (MY) MyNet diginet match).
    6. Exact over prefix (for names > 2 chars)
    7. Special punctuation match
    8. Channel-is-prefix-of-EPG preferred
    9. Regional variant matching
    10. Alphabetical, then EPG id (fully deterministic)

    The former name-length-similarity tie-break (``len_diff``) was REMOVED
    (m4hp1): like the length term in _compute_confidence, it rewarded
    coincidental character counts and mis-ranked noisier subchannels over the
    clean primary feed. Token-set quality (confidence) plus the subchannel
    tie-break replace it.

    ``source_order`` maps EPG ``source_id -> rank`` (0 = most preferred). It is a
    *banded tiebreaker*: it only reorders candidates that share a confidence
    band, so it never promotes a candidate more than one band worse on
    confidence. When ``source_order`` is None/empty every match gets an equal
    rank, and because the band is a monotonic function of confidence the result
    is identical to ordering by raw confidence alone (no behavior change).
    """

    def sort_key(m: EPGMatchWithScore) -> tuple:
        # Reuse the key carried from the lookup entry (bd-xxzxe); only recompute
        # for objects built outside the lookup path (epg_normalized == "").
        epg_norm = m.epg_normalized or normalize_for_epg_match(m.epg_name)

        # 1. League match bonus
        league_bonus = 0
        if channel_league and m.match_type == "league":
            league_bonus = -1  # Sort first

        # 2. Confidence band (negate for descending). Coarser than raw
        #    confidence so near-ties share a band and can be broken by source.
        confidence_band = -(m.confidence // PRIORITY_TIEBREAK_BAND)

        # 3. Source priority rank — only differentiates within a band.
        if source_order:
            source_rank = source_order.get(
                _epg_source_id(m.epg_source), _UNRANKED_SOURCE
            )
        else:
            source_rank = 0

        # 4. Raw confidence (still prefer the better match within band + source)
        # 5. Subchannel: lower number first (primary feed before diginet).
        #    Placed AFTER -m.confidence so it only arbitrates genuine ties —
        #    a candidate with higher token-quality/confidence still wins.
        subchannel = m.subchannel

        # 6. Exact over prefix
        exact_bonus = 0 if m.match_type == "exact" else 1

        # 7. Special punctuation
        has_special = 1 if _SPECIAL_PUNCT_RE.search(m.epg_name) else 0

        # 8. Channel is prefix of EPG
        is_prefix = 0 if epg_norm.startswith(channel_normalized) else 1

        # 9. Regional variant
        has_regional = 1 if _REGIONAL_RE.search(m.epg_name) else 0

        # 10. Alphabetical, then EPG id as a final deterministic tiebreaker
        return (
            league_bonus,
            confidence_band,
            source_rank,
            -m.confidence,
            subchannel,
            exact_bonus,
            has_special,
            is_prefix,
            has_regional,
            m.epg_name.lower(),
            m.epg_id,
        )

    return sorted(matches, key=sort_key)


def find_epg_matches_with_lookup(
    channel: dict,
    streams: list[dict],
    lookup: dict,
    source_order: Optional[dict[int, int]] = None,
    engine=None,
) -> EPGMatchResult:
    """Core matching: find EPG matches for a channel using pre-built lookup.

    Args:
        channel: dict with 'id', 'name', 'streams' keys
        streams: list of stream dicts for this channel
        lookup: pre-built lookup from build_epg_lookup()
        source_order: optional dict mapping source_id -> priority order
        engine: optional ``NormalizationEngine`` (bd-xxzxe). Must be the SAME
            engine passed to ``build_epg_lookup`` so the channel name and the
            EPG names are keyed identically.

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
    norm_info = normalize_for_epg_match_with_league(channel_name, engine=engine)
    channel_normalized = norm_info["normalized"]
    channel_league = norm_info["league"]
    channel_call_sign = extract_broadcast_call_sign(channel_name)
    # Meaningful tokens for the overlap signal (a6445).
    channel_tokens = _extract_match_tokens(channel_name, engine=engine)

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
    by_acronym = lookup["by_acronym"]

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
                channel_tokens=channel_tokens,
            )
            matches.append(EPGMatchWithScore(
                epg_id=epg_id,
                epg_name=entry["name"],
                tvg_id=entry["tvg_id"],
                epg_source=entry["epg_source"],
                epg_normalized=entry["normalized_name"],
                subchannel=entry["subchannel"],
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
                channel_tokens=channel_tokens,
            )
            matches.append(EPGMatchWithScore(
                epg_id=epg_id,
                epg_name=entry["name"],
                tvg_id=entry["tvg_id"],
                epg_source=entry["epg_source"],
                epg_normalized=entry["normalized_name"],
                subchannel=entry["subchannel"],
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
                        channel_tokens=channel_tokens,
                    )
                    matches.append(EPGMatchWithScore(
                        epg_id=epg_id,
                        epg_name=entry["name"],
                        tvg_id=entry["tvg_id"],
                        epg_source=entry["epg_source"],
                        epg_normalized=entry["normalized_name"],
                        subchannel=entry["subchannel"],
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
                        channel_tokens=channel_tokens,
                    )
                    matches.append(EPGMatchWithScore(
                        epg_id=epg_id,
                        epg_name=entry["name"],
                        tvg_id=entry["tvg_id"],
                        epg_source=entry["epg_source"],
                        epg_normalized=entry["normalized_name"],
                        subchannel=entry["subchannel"],
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
                channel_tokens=channel_tokens,
            )
            matches.append(EPGMatchWithScore(
                epg_id=epg_id,
                epg_name=entry["name"],
                tvg_id=entry["tvg_id"],
                epg_source=entry["epg_source"],
                epg_normalized=entry["normalized_name"],
                subchannel=entry["subchannel"],
                confidence=confidence,
                match_type="callsign",
            ))

    # 5. Acronym matches (bd-6rz70): exact lookup on a parenthetical
    # acronym extracted from the EPG entry's tvg_id or display name (e.g.
    # "OWN" -> OprahWinfreyNetwork(OWN).us). Deliberately NOT gated on
    # MIN_PREFIX_LENGTH — short cable acronyms like "OWN" (3 chars) are
    # exactly the case this exists for; gating them out would defeat the
    # purpose (they'd never reach the prefix-scan path either).
    if channel_normalized in by_acronym:
        for entry in by_acronym[channel_normalized]:
            epg_id = entry["id"]
            if epg_id in seen_epg_ids:
                continue
            seen_epg_ids.add(epg_id)
            confidence = _compute_confidence(
                channel_normalized, channel_league, channel_country,
                channel_call_sign, entry, "acronym",
                channel_tokens=channel_tokens,
            )
            matches.append(EPGMatchWithScore(
                epg_id=epg_id,
                epg_name=entry["name"],
                tvg_id=entry["tvg_id"],
                epg_source=entry["epg_source"],
                epg_normalized=entry["normalized_name"],
                subchannel=entry["subchannel"],
                confidence=confidence,
                match_type="acronym",
            ))

    # 6. League-specific matches
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
                        channel_tokens=channel_tokens,
                    )
                    matches.append(EPGMatchWithScore(
                        epg_id=epg_id,
                        epg_name=entry["name"],
                        tvg_id=entry["tvg_id"],
                        epg_source=entry["epg_source"],
                        epg_normalized=entry["normalized_name"],
                        subchannel=entry["subchannel"],
                        confidence=confidence,
                        match_type="league",
                    ))

    # Sort matches. Source priority is applied inside the sort as a banded
    # tiebreaker (see _sort_matches) — confidence is never mutated, so the
    # reported score stays an honest measure of match quality.
    matches = _sort_matches(
        matches, channel_league, channel_normalized, source_order,
    )

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
    engine=None,
) -> list[EPGMatchResult]:
    """Batch process EPG matching for multiple channels.

    Args:
        channels: list of channel dicts with 'id', 'name', 'streams' keys
        all_streams: list of all stream dicts with 'id', 'name',
                     'channel_group_name' keys
        epg_data: list of EPG entry dicts with 'id', 'name', 'tvg_id',
                  'epg_source' keys
        source_order: optional dict mapping EPG source_id -> priority (0=best)
        engine: optional ``NormalizationEngine`` (bd-xxzxe). When supplied, all
            EPG matching is keyed through ECM's one shared engine. When omitted
            the deprecated legacy normalizer is used (back-compat only).

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
    lookup = build_epg_lookup(epg_data, engine=engine)

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
            channel, channel_streams, lookup, source_order, engine=engine,
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
