"""
Event sync matcher service — parse → time-block → parsed-title scoring
(bead enhancedchannelmanager-ti939.1.1, epic ti939 "Event Sync").

Matches SECONDARY-provider event streams to MASTER-group channels for the
event_sync feature: one channel per real-world live event, with every
provider's stream attached. Same event, different provider spellings —
slot prefixes ("Peacock 14:", "Fubo Sports Network 07 :"), team
abbreviations (Man United / MUFC / Manchester United), and two observed
date shapes ("11 Jul 06:00 PM ET" day-first, "Jan 17 02:45 PM ET"
month-first).

**Pure module.** No imports from channel_pipeline_engine or
channel_pipeline_executor (same isolation pattern as
services/dedup_matcher.py). The Phase 1A preview endpoint and the Phase 1B
attach resolver both call this exact code path — dry-run parity by
construction.

Layered matching (in order):

1. **Parse** — stream/channel name → (event title, start datetime), reusing
   the dummy-EPG machinery (``extract_groups`` / ``compute_event_times``,
   which route operator-authored patterns through ``safe_regex`` against
   untrusted provider strings). A name with no complete parsed date+time is
   UNMATCHABLE — the start time is never guessed from "now" (that guess is
   dummy-EPG behavior this module deliberately does not inherit). Derived
   titles are length-capped before they leave the module.
2. **Time-window blocking** — candidate pairs only when parsed start times
   are within ±``window_minutes`` (default 30). This is candidate
   *generation*, not a safety rail.
3. **Fuzzy score of PARSED TITLES** (never raw names) — RapidFuzz
   ``token_set_ratio`` on LOCALS-cleaned strings via the shared cleaner in
   services/dedup_matcher.py.
4. **Team-token check** — split the title on vs / v. / @, compare the two
   sides order-insensitively. A token CONFLICT (both titles parse to team
   pairs and the pairs clearly differ, including gender/age-qualifier
   mismatches like "W" / "U21") is a HARD REJECT: score 0.0, mirroring the
   M1 callsign hard-reject rail. Token agreement raises confidence.
5. **Event admission policy** — :func:`is_event_attachable`, gated by its
   OWN floor constant :data:`EVENT_ATTACH_FLOOR` (0.80, per-rule adjustable,
   hard-clamped ≥ 0.80). Deliberately NOT the dedup matcher's
   admission policy — two different risk profiles must never share one knob.

**Identity contract.** Master channels are identified by NAME / parsed
identity only. This module never sees, caches, or returns channel IDs —
master channels are Dispatcharr-owned and their IDs must be re-resolved by
the caller on every run (PO decision: stateless recompute).

The frozen regression corpus
(``tests/fixtures/event_sync/matcher_corpus.jsonl``) gates this module's
precision/recall in CI — see ``tests/test_event_sync_matcher_corpus.py``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

import pytz
from rapidfuzz import fuzz

from dummy_epg_engine import MONTH_NAMES, compute_event_times, extract_groups
# Reuse the ONE shared name cleaner (LOCALS mode) so parsed-title fuzzy
# scoring matches the unified scoring core byte-for-byte. ``clean_name`` is
# the public alias of the documented single cleaner in the system (see
# dedup_matcher docstring).
from services.dedup_matcher import NameCleanMode, clean_name

logger = logging.getLogger(__name__)

__all__ = [
    "BAND_ATTACH",
    "BAND_AMBIGUOUS",
    "BAND_REJECT",
    "DEFAULT_EVENT_PATTERNS",
    "DEFAULT_EVENT_TIMEZONE",
    "DEFAULT_TIME_WINDOW_MINUTES",
    "EVENT_AMBIGUOUS_FLOOR",
    "EVENT_ATTACH_FLOOR",
    "EVENT_NO_TEAMS_FLOOR",
    "EVENT_TITLE_MAX_LEN",
    "REJECT_BELOW_AMBIGUOUS_FLOOR",
    "REJECT_NO_PARSED_TIME",
    "REJECT_NUMERIC_IDENTITY_CONFLICT",
    "REJECT_OUTSIDE_TIME_WINDOW",
    "REJECT_PARSE_FAILURE",
    "REJECT_TEAM_TOKEN_CONFLICT",
    "TEAM_VERDICT_ABSENT",
    "TEAM_VERDICT_AGREE",
    "TEAM_VERDICT_CONFLICT",
    "TEAM_VERDICT_UNCERTAIN",
    "MasterCandidate",
    "PairScore",
    "ParsedEvent",
    "StreamMatchResult",
    "is_event_attachable",
    "match_stream_to_masters",
    "match_streams",
    "parse_event_name",
    "score_pair",
]


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

# EVENT admission floor — the matcher's own knob (bead ti939.1.1). This is
# deliberately NOT dedup's no-callsign floor: the event path and the dedup
# path carry different risk profiles and must never share one constant
# (drift protection). Default auto-attach threshold 0.80 (PO: "calibrated"),
# per-rule adjustable, hard-clamped >= 0.80 by is_event_attachable().
EVENT_ATTACH_FLOOR: float = 0.80

# Scores in [EVENT_AMBIGUOUS_FLOOR, effective attach threshold) land in the
# "ambiguous" band: surfaced for operator review, never auto-attached.
EVENT_AMBIGUOUS_FLOOR: float = 0.60

# Higher attach bar when the team-token check contributes no positive
# signal (verdict "absent" — no team pair on at least one side — or
# "uncertain" — pairs parsed but alignment inconclusive). Lexical overlap
# alone is a weaker signal than agreeing team tokens: sibling studio shows
# ("Vive el Mundial" / "Hoy en el Mundial") token-set-score ~0.89 while
# being different programs. Philosophically parallel to — but deliberately
# NOT shared with — the dedup matcher's no-callsign bar: separate risk
# profile, separate knob (drift protection, bead ti939.1.1).
EVENT_NO_TEAMS_FLOOR: float = 0.90

# Candidate generation: parsed start times must be within ± this many
# minutes (per-rule adjustable).
DEFAULT_TIME_WINDOW_MINUTES: int = 30

# Observed provider names carry "ET" suffixes. Canonical IANA zone name —
# the legacy "US/Eastern" alias is absent from slimmed system tz databases.
DEFAULT_EVENT_TIMEZONE: str = "America/New_York"

# Hard cap on a derived title before it leaves this module. Real event
# titles are short; anything longer is junk or an amplification attempt.
EVENT_TITLE_MAX_LEN: int = 120

# Confidence bands.
BAND_ATTACH = "attach"
BAND_AMBIGUOUS = "ambiguous"
BAND_REJECT = "reject"

# Team-token verdicts.
TEAM_VERDICT_AGREE = "agree"          # both pairs parsed; teams align
TEAM_VERDICT_CONFLICT = "conflict"    # both pairs parsed; teams clearly differ
TEAM_VERDICT_UNCERTAIN = "uncertain"  # both pairs parsed; alignment unclear
TEAM_VERDICT_ABSENT = "absent"        # at least one side has no team pair

# Machine-readable reject reasons (journal / preview UI contract).
REJECT_PARSE_FAILURE = "parse_failure"
REJECT_NO_PARSED_TIME = "no_parsed_time"
REJECT_OUTSIDE_TIME_WINDOW = "outside_time_window"
REJECT_TEAM_TOKEN_CONFLICT = "team_token_conflict"
REJECT_NUMERIC_IDENTITY_CONFLICT = "numeric_identity_conflict"
REJECT_BELOW_AMBIGUOUS_FLOOR = "below_ambiguous_floor"

# Team-token comparison floors (internal). A pair of aligned teams whose
# worst per-team similarity is below the conflict ceiling "clearly differs"
# (hard reject — 'islanders' vs 'yankees' scores 0.50, 'brentford' vs
# 'everton' 0.38); at or above the agree floor the tokens agree (confidence
# boost); in between the check is inconclusive ("uncertain") and the pair
# faces the stricter EVENT_NO_TEAMS_FLOOR for admission.
#
# The agree floor sits at 0.90 (PR #611 review, finding 3): every POSITIVE
# team-identity signal reaches it (exact 1.0, initialism 0.95, bounded
# abbreviation 0.90, single-typo forms ~0.93) while near-length sibling
# words that are genuinely different teams do not ('austria' vs 'australia'
# is 0.875 raw ratio and must NOT count as agreement).
_TEAM_CONFLICT_CEILING: float = 0.60
_TEAM_AGREE_FLOOR: float = 0.90

# ---------------------------------------------------------------------------
# Default parse patterns (shipped defaults; per-rule overridable).
#
# These are developer-authored raw-string constants, but they execute via
# dummy_epg_engine.extract_groups → safe_regex because per-rule OVERRIDES are
# operator-authored (untrusted) and both must run through the same machinery.
#
# Title: strip an optional SLOT prefix, then capture everything up to the
# "@ <date>" delimiter.
#
# Slot-prefix shape (PR #611 review, finding 4): anchored, bounded
# (≤ 40 chars, no ':' or '@' inside), ending in an EXACTLY-two-digit slot
# number + colon — every live-observed slot list is zero-padded to two
# digits ("Peacock 14:", "Fubo Sports Network 07 :", "NFL Game Pass 01:").
# The bound deliberately does NOT strip series-identity prefixes like
# "UFC 317:" / "Bellator 300:" (3 digits, guarded by the (?<!\d)
# lookbehind) or "Formula 1:" (1 digit) — those are part of the event
# identity; the old greedy any-digits strip collapsed "UFC 317: Early
# Prelims" and "Bellator 300: Early Prelims" to the same title. Residual
# risk (PR #611 review nit — empirically verified): a provider using
# unpadded 1-digit or 3-digit slot numbers keeps its slot prefix inside the
# parsed title, where it rides into the team-token split. When BOTH sides
# retain such prefixes ("Peacock 5: Lyon vs. Marseille" / "Fubo 3 : Lyon
# vs. Marseille") the provider tokens land on opposite team sides and the
# pair HARD team-token-CONFLICTS (score 0.0) — a SILENT FALSE NEGATIVE,
# not graceful degradation. One-sided retention only dilutes the fuzzy
# score. The escape hatch for such providers is a per-rule pattern
# override (event_sync_config.patterns / group_patterns).
#
# Date-delimiter "@"/"|" vs team-separator "@" (PR #611 review, finding 2;
# broadened for the live "|"-delimited + trailing-suffix shapes, bead
# 9c9j7): a title may itself contain "@" as a home/away separator ("Rangers
# @ Islanders @ 11 Jul 07:00 PM ET"), so the title capture is ".+?" bounded
# by an explicitly DATE-SHAPED tail — the delimiter followed by
# "<day> <month> <h:mm>" or "<month> <day> [year] <h:mm>" — not by the
# first delimiter in the name. A team-separator can never satisfy the date
# shape (it is never followed by "<h:mm>" immediately after the day/month
# tokens), so the title extends through it.
#
# Three broadenings over the original "@ <date> <time>$" shape, each driven
# by live provider names that were producing "Incomplete date/time" parse
# FAILURES (bead 9c9j7):
#   1. TRAILING SUFFIX after the time — a slot/provider label the provider
#      appends AFTER the date ("... @ Jul 11 9:30 AM :Flo Racing 03",
#      "... | Sun 12 Jul 02:00 EDT (US) | | US: ESPN+ PPV 40"). Fixed by
#      folding the CAPTURED time into the date pattern (``_TIME_CAPTURE``)
#      so hour/minute are positionally locked to the date and the pattern
#      stops at the timezone instead of having to reach end-of-string.
#   2. "|" DELIMITER as well as "@" (``_DATE_DELIM``), for pipe-delimited
#      listing formats.
#   3. Optional leading WEEKDAY token before the date (``_WEEKDAY_PREFIX``),
#      e.g. "| Sun 12 Jul 02:00 EDT". Weekday abbreviations never collide
#      with month abbreviations, so this is unambiguous before either shape.
# ---------------------------------------------------------------------------

# Date delimiter: "@" (most providers), "|" (pipe-delimited listings), or
# "(" (parenthesized dates, e.g. "Title (7.12 9:15 AM ET)" — bead for the
# numeric-date shape).
_DATE_DELIM = r"(?:@|\||\()"

# Optional leading weekday token ("Sun", "Sunday", "Mon,") before the date.
_WEEKDAY_PREFIX = r"(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[A-Za-z]*\.?,?\s+)?"

# Time-of-day CAPTURED, folded into the date pattern so hour/minute are
# positionally locked to the date tokens (never re-searched across the whole
# name) — this is what lets a trailing suffix after the time be ignored.
# 12h with AM/PM ("06:00 PM ET") or 24h ("02:00 EDT"); optional ET/EST/EDT.
_TIME_CAPTURE = (
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    r"(?:\s*(?P<ampm>[AaPp])\.?[Mm]?\.?)?"
    r"(?:\s*E[SD]?T)?"
)

# Non-capturing time shape — bounds the title's date tail only.
_TIME_SHAPE = r"\d{1,2}:\d{2}(?:\s*[AaPp]\.?[Mm]?\.?)?(?:\s*E[SD]?T)?"

_TITLE_PATTERN = (
    r"^(?:[^@:|(]{0,40}?(?<!\d)\d{2}\s*:\s*)?"
    + r"\s*(?P<title>.+?)\s*"
    + r"(?:" + _DATE_DELIM + r"\s*" + _WEEKDAY_PREFIX + r"(?:"
    + r"\d{1,2}\s+[A-Za-z]{3,9}\.?\s+" + _TIME_SHAPE
    + r"|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:\s*,?\s*\d{4})?\s+" + _TIME_SHAPE
    + r"|\d{1,2}[./]\d{1,2}\s+" + _TIME_SHAPE
    + r").*)?$"
)

# End-anchored FALLBACK time source for names whose date pattern does not
# itself capture the time; the date pattern's captured time wins on merge
# (``extract_groups`` applies ``date_pattern`` last). 12h with AM/PM
# ("06:00 PM ET") or 24h without ("18:30 ET"). Optional ET/EST/EDT suffix.
_TIME_PATTERN = (
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    r"(?:\s*(?P<ampm>[AaPp])\.?[Mm]?\.?)?"
    r"\s*(?:E[SD]?T)?\s*$"
)

# Date shape 1 (observed): "@ 11 Jul 06:00 PM ET" / "| Sun 12 Jul 02:00
# EDT" — day first, captured time.
_DATE_PATTERN_DAY_FIRST = (
    _DATE_DELIM + r"\s*" + _WEEKDAY_PREFIX
    + r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})\.?\s+" + _TIME_CAPTURE
)

# Date shape 2 (observed): "@ Jan 17 02:45 PM ET" — month first, optional
# explicit 4-digit year ("@ Jan 17 2027 02:45 PM ET"), captured time.
_DATE_PATTERN_MONTH_FIRST = (
    _DATE_DELIM + r"\s*" + _WEEKDAY_PREFIX
    + r"(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})"
    + r"(?:\s*,?\s*(?P<year>\d{4}))?\s+" + _TIME_CAPTURE
)

# Date shape 3 (observed): "(7.12 9:15 AM ET)" — NUMERIC month-first date,
# "." or "/" separated, month-first (US convention, matching the ET default
# timezone). A provider that lists numeric dates day-first needs a per-rule
# override. Captured time; the parenthesis opener is one of the delimiters.
_DATE_PATTERN_NUMERIC = (
    _DATE_DELIM + r"\s*" + _WEEKDAY_PREFIX
    + r"(?P<month>\d{1,2})[./](?P<day>\d{1,2})\s+" + _TIME_CAPTURE
)

DEFAULT_EVENT_PATTERNS: tuple[dict, ...] = (
    {
        "name": "slot-title-day-first-date",
        "title_pattern": _TITLE_PATTERN,
        "time_pattern": _TIME_PATTERN,
        "date_pattern": _DATE_PATTERN_DAY_FIRST,
    },
    {
        "name": "slot-title-month-first-date",
        "title_pattern": _TITLE_PATTERN,
        "time_pattern": _TIME_PATTERN,
        "date_pattern": _DATE_PATTERN_MONTH_FIRST,
    },
    {
        "name": "slot-title-numeric-date",
        "title_pattern": _TITLE_PATTERN,
        "time_pattern": _TIME_PATTERN,
        "date_pattern": _DATE_PATTERN_NUMERIC,
    },
)

# ---------------------------------------------------------------------------
# Dateless "today's live schedule" support (bead assume-current-date).
#
# OPT-IN ONLY, via the per-rule ``assume_current_date`` flag. Some providers
# list a live schedule with a TIME but NO date ("Boxing 05 : FURY vs HALL
# 6PM", "LIVE EVENT 05 - 4:15pm Zenith Racing Series"). The never-guess rail
# rejects these by default (a time with no date is unmatchable). When the
# flag is on, :func:`parse_event_name` fills the CURRENT date so the time
# becomes matchable — accepting the cross-day risk the operator opted into.
#
# These variants are tried ONLY on the opt-in path (never in
# DEFAULT_EVENT_PATTERNS), so the shipped defaults' precision is untouched. A
# bare time REQUIRES a colon (``H:MM``) or an am/pm marker, so a lone number
# in a title is never mistaken for a time. A slightly broader slot strip
# handles the 1-digit / colon-less slots common in these PPV feeds.
# ---------------------------------------------------------------------------

_BARE_SLOT = r"(?:[^@:|(]{0,40}?(?<!\d)\d{1,2}\s*[:\-]\s*)?"

_ASSUME_DATE_PATTERNS: tuple[dict, ...] = (
    # Time-of-day at the END, am/pm form: "6PM" / "4:15pm" / "12am".
    {
        "name": "dateless-title-time-ampm",
        "title_pattern": (
            r"^" + _BARE_SLOT + r"\s*(?P<title>.+?)\s+"
            r"\d{1,2}(?::\d{2})?\s*[AaPp]\.?[Mm]?\.?(?:\s*E[SD]?T)?\s*$"
        ),
        "time_pattern": (
            r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?"
            r"\s*(?P<ampm>[AaPp])\.?[Mm]?\.?(?:\s*E[SD]?T)?\s*$"
        ),
    },
    # Time-of-day at the END, 24h form: "19:00" / "18:30 ET".
    {
        "name": "dateless-title-time-24h",
        "title_pattern": (
            r"^" + _BARE_SLOT + r"\s*(?P<title>.+?)\s+"
            r"\d{1,2}:\d{2}(?:\s*E[SD]?T)?\s*$"
        ),
        "time_pattern": r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?:\s*E[SD]?T)?\s*$",
    },
    # Time-of-day FIRST after a "-"/"|" separator: "LIVE EVENT 05 - 4:15pm
    # <title>". am/pm required here (a leading 24h "HH:MM" is too easily a
    # score/round number).
    {
        "name": "dateless-time-first",
        "title_pattern": (
            r"^.*?[-|]\s*\d{1,2}(?::\d{2})?\s*[AaPp]\.?[Mm]?\.?\s+"
            r"(?P<title>.+?)\s*$"
        ),
        "time_pattern": (
            r"[-|]\s*(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?"
            r"\s*(?P<ampm>[AaPp])\.?[Mm]?\.?"
        ),
    },
)

# ---------------------------------------------------------------------------
# Team-token support tables (module-level raw-literal constants — stdlib
# ``re`` is correct here per docs/style_guide.md#regex; these never carry
# operator input).
# ---------------------------------------------------------------------------

# Split a parsed title into its two team sides: " vs " / " vs. " / " v. " /
# " @ ". ("at" is deliberately NOT a separator — it is too common as a plain
# English word in non-team event titles.)
_TEAM_SEPARATOR_RE = re.compile(r"\s+(?:vs\.?|v\.)\s+|\s+@\s+", re.IGNORECASE)

_TEAM_TOKEN_RE = re.compile(r"\w+")

# Gender / age / squad qualifiers, mapped to CANONICAL CLASSES. A
# qualifier-CLASS mismatch between two otherwise-similar team names is a
# DIFFERENT team (women's vs men's side, U21 vs senior side, reserves vs
# first team) — hard conflict. Synonyms within one class must NOT conflict:
# "Barcelona W" and "Barcelona Women" are the SAME women's side spelled by
# two providers (PR #611 review, finding 5). Age groups stay distinct
# classes (U21 vs U23 are different squads).
_TEAM_QUALIFIER_CLASSES: dict[str, str] = {
    "w": "women", "women": "women", "womens": "women", "ladies": "women",
    "fem": "women", "femenil": "women", "femenino": "women",
    "u16": "u16", "u17": "u17", "u18": "u18", "u19": "u19",
    "u20": "u20", "u21": "u21", "u23": "u23",
    "reserve": "reserves", "reserves": "reserves", "res": "reserves",
    "ii": "reserves", "b": "reserves",
    "academy": "youth", "youth": "youth",
}
_TEAM_QUALIFIER_TOKENS = frozenset(_TEAM_QUALIFIER_CLASSES)

# Generic club-suffix tokens that carry no identity ("Juventus FC" ==
# "Juventus"). Kept deliberately small — "AC", "City", "United" etc. ARE
# identity-bearing and must not be stripped.
_TEAM_GENERIC_TOKENS = frozenset({"fc", "afc", "cf", "sc", "cfc", "club"})

# Initialism suffixes strippable when testing "MUFC" ↔ "Manchester United".
_INITIALISM_SUFFIXES = ("afc", "fc", "cf", "sc")


# ---------------------------------------------------------------------------
# Result dataclasses.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedEvent:
    """Parsed identity of one provider event name.

    ``title`` is the length-capped parsed event title, or ``None`` when the
    title pattern did not match (parse failure). ``start`` is the tz-aware
    parsed start datetime, or ``None`` when no COMPLETE date+time was
    captured — an event with ``start is None`` is unmatchable by contract
    (never guessed). ``teams`` is the order-preserving two-sided team split
    of the title, or ``None`` when the title has no team separator.
    """

    raw_name: str
    title: str | None
    start: datetime | None
    teams: tuple[str, str] | None
    matched_pattern: str | None


@dataclass(frozen=True)
class PairScore:
    """Score of one (name_a, name_b) event pair through every layer."""

    score: float
    band: str
    team_verdict: str
    fuzzy_score: float
    team_score: float | None
    time_delta_minutes: float | None
    reject_reasons: tuple[str, ...]
    parsed_a: ParsedEvent
    parsed_b: ParsedEvent


@dataclass(frozen=True)
class MasterCandidate:
    """One master-channel candidate for a secondary stream.

    Master identity is carried by ``master_name`` (and its parsed fields)
    ONLY — never a channel ID (stateless recompute; IDs are re-resolved by
    the caller each run).
    """

    master_name: str
    parsed: ParsedEvent
    score: float
    band: str
    team_verdict: str
    time_delta_minutes: float
    reject_reasons: tuple[str, ...]


@dataclass(frozen=True)
class StreamMatchResult:
    """Ordered candidate result for one secondary stream."""

    stream_name: str
    parsed: ParsedEvent
    candidates: tuple[MasterCandidate, ...] = field(default_factory=tuple)
    unmatchable_reason: str | None = None


# ---------------------------------------------------------------------------
# Layer 1 — parse.
# ---------------------------------------------------------------------------


def _month_to_int(value: str | None) -> int | None:
    """Parse a captured month group ('Jul', 'January', '7') to 1-12."""
    if not value:
        return None
    text = str(value).strip()
    if text.isdigit():
        month = int(text)
        return month if 1 <= month <= 12 else None
    return MONTH_NAMES.get(text.lower())


def _hour_24(hour: int, ampm: str | None) -> int:
    """12h→24h conversion mirroring compute_event_times' ampm handling."""
    if ampm:
        marker = ampm.lower().strip().rstrip(".")
        if marker.startswith("a"):
            return 0 if hour == 12 else hour
        if marker.startswith("p"):
            return hour if hour == 12 else hour + 12
    return hour


def _groups_have_complete_time(groups: dict) -> bool:
    """True when the captured groups carry a full date+time (no guessing)."""
    if groups.get("hour") is None or groups.get("minute") is None:
        return False
    if groups.get("day") is None:
        return False
    return _month_to_int(groups.get("month")) is not None


def _infer_year(
    month: int, day: int, hour24: int, minute: int, tz, now: datetime
) -> int | None:
    """Pick the year that puts (month, day) closest to ``now``.

    Handles the Dec 31 / Jan 1 boundary: on Dec 30 a "Jan 2" event belongs
    to NEXT year; on Jan 2 a "Dec 30" event belongs to LAST year. Candidate
    years that don't exist (Feb 29 in a non-leap year) are skipped.
    """
    best_year: int | None = None
    best_distance: float | None = None
    for year in (now.year - 1, now.year, now.year + 1):
        try:
            candidate = tz.localize(datetime(year, month, day, hour24, minute, 0))
        except (ValueError, OverflowError):
            continue
        distance = abs((candidate - now).total_seconds())
        if best_distance is None or distance < best_distance:
            best_year = year
            best_distance = distance
    return best_year


def parse_event_name(
    name: str,
    patterns: Sequence[dict] | None = None,
    *,
    event_timezone: str = DEFAULT_EVENT_TIMEZONE,
    now: datetime | None = None,
    assume_current_date: bool = False,
) -> ParsedEvent:
    """Parse one provider event name into a :class:`ParsedEvent`.

    Tries each pattern variant in order (``extract_groups`` →
    ``safe_regex`` under the hood) and takes the FIRST variant whose
    captured groups carry a complete date+time. A variant whose title
    matches but whose date/time groups are incomplete is kept only as a
    title-only fallback — the start time is never filled in from "now"
    (the dummy-EPG fallback this module must not inherit).

    ``now`` (tz-aware) anchors year inference for the observed yearless
    date shapes and defaults to the current time in ``event_timezone``.

    ``assume_current_date`` (bead assume-current-date, OPT-IN) relaxes the
    never-guess rail for dateless "today's schedule" listings: when no
    variant yields a complete date but the name carries a bare time-of-day,
    the CURRENT date (in ``event_timezone``) is filled so the time is
    matchable. Default False preserves the never-guess behavior exactly.
    """
    variants = patterns if patterns is not None else DEFAULT_EVENT_PATTERNS
    tz = pytz.timezone(event_timezone)
    if now is None:
        now = datetime.now(tz)

    complete_groups: dict | None = None
    complete_variant: str | None = None
    fallback_title: str | None = None
    fallback_variant: str | None = None

    for variant in variants:
        title_pattern = variant.get("title_pattern")
        if not title_pattern:
            continue
        groups = extract_groups(
            name,
            title_pattern,
            variant.get("time_pattern"),
            variant.get("date_pattern"),
        )
        if groups is None:
            continue
        if _groups_have_complete_time(groups):
            complete_groups = groups
            complete_variant = variant.get("name") or title_pattern
            break
        if fallback_title is None and groups.get("title"):
            fallback_title = groups["title"]
            fallback_variant = variant.get("name") or title_pattern

    # Dateless opt-in: no complete date parsed, but with assume_current_date
    # a bare time-of-day is placed on TODAY (event_timezone). Tried only here
    # so the shipped defaults never inherit the "guess the date" behavior.
    if complete_groups is None and assume_current_date:
        now_local = now.astimezone(tz)
        for variant in _ASSUME_DATE_PATTERNS:
            groups = extract_groups(
                name, variant["title_pattern"], variant.get("time_pattern"),
            )
            if groups is None or groups.get("hour") is None:
                continue
            complete_groups = {
                **groups,
                "day": str(now_local.day),
                "month": str(now_local.month),
                "year": str(now_local.year),
            }
            complete_variant = variant["name"]
            break

    if complete_groups is None:
        title = _cap_title(fallback_title)
        return ParsedEvent(
            raw_name=name,
            title=title,
            start=None,
            teams=_split_teams(title),
            matched_pattern=fallback_variant if title else None,
        )

    start = _build_start(complete_groups, tz, event_timezone, now)
    title = _cap_title(complete_groups.get("title"))
    return ParsedEvent(
        raw_name=name,
        title=title,
        start=start,
        teams=_split_teams(title),
        matched_pattern=complete_variant,
    )


def _build_start(groups: dict, tz, event_timezone: str, now: datetime) -> datetime | None:
    """Build the aware start datetime, inferring the year when absent."""
    month = _month_to_int(groups.get("month"))
    try:
        day = int(groups.get("day"))
        hour = int(groups.get("hour"))
        # Bare am/pm times ("6PM") carry no minutes — default to :00.
        minute_raw = groups.get("minute")
        minute = int(minute_raw) if minute_raw is not None else 0
    except (TypeError, ValueError):
        return None
    if month is None:
        return None

    hour24 = _hour_24(hour, groups.get("ampm"))
    if not (0 <= hour24 <= 23 and 0 <= minute <= 59):
        return None

    year_raw = groups.get("year")
    if year_raw is None:
        year = _infer_year(month, day, hour24, minute, tz, now)
        if year is None:
            return None
    else:
        try:
            year = int(year_raw)
        except (TypeError, ValueError):
            return None
        if year < 100:  # normalize 2-digit years the same way dummy-EPG does
            year += 2000

    # NEVER-GUESS rail (PR #611 review, finding 1): validate that
    # (year, month, day) is a REAL calendar date before delegating.
    # compute_event_times falls back to a "now"-based guess on a
    # ValueError ("Feb 30 2027" → tonight) — acceptable for dummy-EPG
    # filler programming, incident-class for event matching.
    try:
        datetime(year, month, day, hour24, minute, 0)
    except (ValueError, OverflowError):
        return None

    # Normalize the delegated groups: fill the defaulted minute (bare am/pm
    # times have none) so compute_event_times never sees ``minute=None``.
    groups = {**groups, "year": str(year), "minute": str(minute)}

    # Delegate the actual datetime construction (incl. 12h→24h and DST
    # localization) to the shared dummy-EPG machinery so event_sync and
    # dummy-EPG can never disagree about what a captured time means.
    time_vars = compute_event_times(groups, event_timezone)
    start = time_vars.get("start_dt")
    return start if isinstance(start, datetime) else None


def groups_would_build_start(
    groups: dict | None,
    *,
    event_timezone: str = DEFAULT_EVENT_TIMEZONE,
    now: datetime | None = None,
) -> bool:
    """True when captured pattern groups would yield a REAL start time.

    The public matcher-level validity surface for the Test Patterns panel
    (bead hirm6): 'Parsed' in the panel must mean "the matcher would
    actually get a start time", not merely "the date/time groups were
    captured". Delegates to the EXACT internals :func:`parse_event_name`
    uses — :func:`_groups_have_complete_time` (all groups present, month
    resolvable to 1-12) then :func:`_build_start` (hour <= 23 after am/pm
    normalization, minute <= 59, a real calendar date — 'July 45' and
    'Feb 30' are rejected, never guessed) — so the panel's flag can never
    drift from the never-guess semantics.

    ``now`` (tz-aware) anchors year inference for yearless dates, exactly
    as in :func:`parse_event_name`.
    """
    if not groups or not _groups_have_complete_time(groups):
        return False
    tz = pytz.timezone(event_timezone)
    if now is None:
        now = datetime.now(tz)
    return _build_start(groups, tz, event_timezone, now) is not None


def _cap_title(title: str | None) -> str | None:
    if title is None:
        return None
    capped = title.strip()[:EVENT_TITLE_MAX_LEN].strip()
    return capped or None


# ---------------------------------------------------------------------------
# Layer 4 — team-token check.
# ---------------------------------------------------------------------------


def _split_teams(title: str | None) -> tuple[str, str] | None:
    """Split a parsed title into its two team sides, or None."""
    if not title:
        return None
    parts = [p.strip() for p in _TEAM_SEPARATOR_RE.split(title)]
    parts = [p for p in parts if p]
    if len(parts) != 2:
        return None
    return (parts[0], parts[1])


def _team_tokens(team: str) -> list[str]:
    """Lowercased identity tokens of one team side (apostrophes fused)."""
    return _TEAM_TOKEN_RE.findall(team.replace("'", "").lower())


def _qualifiers(tokens: list[str]) -> frozenset[str]:
    """Canonical qualifier CLASSES present in a team's tokens."""
    return frozenset(
        _TEAM_QUALIFIER_CLASSES[t] for t in tokens if t in _TEAM_QUALIFIER_CLASSES
    )


def _identity_tokens(tokens: list[str]) -> list[str]:
    return [
        t for t in tokens
        if t not in _TEAM_QUALIFIER_TOKENS and t not in _TEAM_GENERIC_TOKENS
    ]


# An abbreviation must be SUBSTANTIALLY shorter than the word it
# abbreviates. Without this bound the subsequence test accepts near-length
# sibling words — 'austria' is a same-first-letter subsequence of
# 'australia' (7/9 chars) and would score 0.90 "abbreviation" agreement
# between two different national teams (PR #611 review, finding 3). Real
# abbreviations are much shorter than their expansions: man/manchester
# (0.30), utd/united (0.50), juve/juventus (0.50), inter/internazionale
# (0.36).
_ABBREV_MAX_LENGTH_RATIO: float = 0.60


def _is_abbrev_of(short: str, long: str) -> bool:
    """'man'→'manchester', 'utd'→'united': same first letter + subsequence,
    with a length-ratio bound so near-length siblings ('austria' ⊆
    'australia') are NOT treated as abbreviations."""
    if len(short) < 2 or short[0] != long[0]:
        return False
    if len(short) > len(long) * _ABBREV_MAX_LENGTH_RATIO:
        return False
    it = iter(long)
    return all(ch in it for ch in short)


def _token_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if _is_abbrev_of(a, b) or _is_abbrev_of(b, a):
        return 0.90
    return fuzz.ratio(a, b) / 100.0


def _initialism_matches(single: str, words: list[str]) -> bool:
    """'mufc' ↔ ['manchester', 'united'] (strippable FC-style suffix)."""
    if not words or len(words) < 2:
        return False
    token = single
    for suffix in _INITIALISM_SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix):
            token = token[: -len(suffix)]
            break
    initials = "".join(w[0] for w in words)
    return len(token) >= 2 and token == initials


def _team_similarity(team_a: str, team_b: str) -> float:
    """Similarity of two single-team names in [0.0, 1.0].

    Qualifier mismatch (women's/men's, U21/senior, reserves/first team)
    returns 0.0 — a clearly different team regardless of lexical overlap.
    Otherwise the score is the WORST aligned-token similarity from the
    shorter side (precision-first: one clearly-different distinctive token
    — 'rangers' vs 'knicks' — drags the whole team comparison down even
    when the city tokens agree).
    """
    tokens_a = _team_tokens(team_a)
    tokens_b = _team_tokens(team_b)
    if _qualifiers(tokens_a) != _qualifiers(tokens_b):
        return 0.0

    ident_a = _identity_tokens(tokens_a)
    ident_b = _identity_tokens(tokens_b)
    if not ident_a or not ident_b:
        return fuzz.ratio(" ".join(tokens_a), " ".join(tokens_b)) / 100.0
    if ident_a == ident_b:
        return 1.0

    if len(ident_a) == 1 and _initialism_matches(ident_a[0], ident_b):
        return 0.95
    if len(ident_b) == 1 and _initialism_matches(ident_b[0], ident_a):
        return 0.95

    shorter, longer = (
        (ident_a, ident_b) if len(ident_a) <= len(ident_b) else (ident_b, ident_a)
    )
    worst = 1.0
    for token in shorter:
        best = max(_token_similarity(token, other) for other in longer)
        worst = min(worst, best)
    return worst


def _team_pair_verdict(
    teams_a: tuple[str, str] | None,
    teams_b: tuple[str, str] | None,
) -> tuple[str, float | None]:
    """Order-insensitive verdict for two (team, team) pairs.

    Returns ``(verdict, team_score)`` where ``team_score`` is the worst
    per-team similarity of the best home/away assignment (None when either
    side has no team pair).
    """
    if teams_a is None or teams_b is None:
        return TEAM_VERDICT_ABSENT, None

    straight = (
        _team_similarity(teams_a[0], teams_b[0]),
        _team_similarity(teams_a[1], teams_b[1]),
    )
    swapped = (
        _team_similarity(teams_a[0], teams_b[1]),
        _team_similarity(teams_a[1], teams_b[0]),
    )
    best = straight if sum(straight) >= sum(swapped) else swapped
    team_score = min(best)

    if team_score < _TEAM_CONFLICT_CEILING:
        return TEAM_VERDICT_CONFLICT, team_score
    if team_score >= _TEAM_AGREE_FLOOR:
        return TEAM_VERDICT_AGREE, team_score
    # MIXED alignment (PR #611 review, finding 3): one team clearly the
    # same (>= agree floor) while the other is not — that is the
    # "different fixture sharing one side" shape ('Australia vs France' /
    # 'Austria vs France': france 1.0, austria↔australia 0.875). A shared
    # opponent plus a not-clearly-equal second team is evidence of a
    # DIFFERENT event, not weak evidence of the same one — hard conflict.
    if max(best) >= _TEAM_AGREE_FLOOR:
        return TEAM_VERDICT_CONFLICT, team_score
    return TEAM_VERDICT_UNCERTAIN, team_score


# Pure-digit tokens inside a cleaned title ("UFC 317", "Formula 1",
# "Stage 8"). Module-level raw-literal constant — stdlib ``re`` per
# docs/style_guide.md#regex.
_NUMERIC_TOKEN_RE = re.compile(r"\b\d+\b")


def _numeric_identity_conflict(title_a: str, title_b: str) -> bool:
    """True when the titles carry DISJOINT numeric identities.

    Series/edition numbers are identity-bearing for team-less titles:
    'UFC 317: Early Prelims' vs 'Bellator 300: Early Prelims' and
    'Formula 1: Qualifying' vs 'Formula 2: Qualifying' share most words
    while denoting different events (PR #611 review, finding 4). When BOTH
    cleaned titles contain numeric tokens and the sets share nothing, the
    pair clearly differs. One-sided or overlapping numbers pass — a
    provider that omits the series number ('Topuria vs Oliveira') or adds
    a year ('Tour de France 2026: Stage 8' vs 'Tour de France: Stage 8')
    must not be rejected by this rail.

    Known false-negative class: cross-provider numbering schemes for the
    SAME event — "F1 Round 12: British GP Qualifying" vs "Formula 1
    British Grand Prix Qualifying" carry disjoint number sets ({12} vs
    {1}) and hard-reject despite denoting one session.
    """
    nums_a = set(_NUMERIC_TOKEN_RE.findall(
        clean_name(title_a, mode=NameCleanMode.LOCALS)
    ))
    nums_b = set(_NUMERIC_TOKEN_RE.findall(
        clean_name(title_b, mode=NameCleanMode.LOCALS)
    ))
    return bool(nums_a) and bool(nums_b) and not (nums_a & nums_b)


# ---------------------------------------------------------------------------
# Layer 3 — fuzzy score of parsed titles.
# ---------------------------------------------------------------------------


def _fuzzy_title_score(title_a: str, title_b: str) -> float:
    """RapidFuzz token_set_ratio on LOCALS-cleaned parsed titles."""
    norm_a = clean_name(title_a, mode=NameCleanMode.LOCALS)
    norm_b = clean_name(title_b, mode=NameCleanMode.LOCALS)
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 1.0
    return fuzz.token_set_ratio(norm_a, norm_b) / 100.0


# ---------------------------------------------------------------------------
# Layer 5 — event admission policy (its own function, its own floor).
# ---------------------------------------------------------------------------


def is_event_attachable(
    score: float,
    team_verdict: str,
    *,
    threshold: float = EVENT_ATTACH_FLOOR,
) -> bool:
    """THE event admission policy (bead ti939.1.1 — structurally separate).

    NOT a reuse of dedup's admission policy: the event path has its own
    floor constant (:data:`EVENT_ATTACH_FLOOR`) so the two risk profiles
    can never drift together on one knob.

    * A team-token ``conflict`` is NEVER attachable, at any score — the
      hard-reject rail mirrors the M1 callsign rail (non-negotiable).
    * ``threshold`` is per-rule adjustable but hard-clamped to
      ``EVENT_ATTACH_FLOOR``: a request for 0.50 gets 0.80 behavior.
    * Without positive team-token agreement (verdict ``absent`` or
      ``uncertain``) the bar additionally rises to
      :data:`EVENT_NO_TEAMS_FLOOR` — lexical overlap alone must clear a
      higher score to auto-attach.
    """
    if team_verdict == TEAM_VERDICT_CONFLICT:
        return False
    effective_threshold = max(threshold, EVENT_ATTACH_FLOOR)
    if team_verdict != TEAM_VERDICT_AGREE:
        effective_threshold = max(effective_threshold, EVENT_NO_TEAMS_FLOOR)
    return score >= effective_threshold


# ---------------------------------------------------------------------------
# Pair scoring (parse → block → score → verdict → band).
# ---------------------------------------------------------------------------


def _score_parsed_pair(
    parsed_a: ParsedEvent,
    parsed_b: ParsedEvent,
    *,
    window_minutes: int,
    threshold: float,
) -> PairScore:
    def _result(
        score: float,
        band: str,
        verdict: str,
        fuzzy: float,
        team_score: float | None,
        delta: float | None,
        reasons: tuple[str, ...],
    ) -> PairScore:
        return PairScore(
            score=score,
            band=band,
            team_verdict=verdict,
            fuzzy_score=fuzzy,
            team_score=team_score,
            time_delta_minutes=delta,
            reject_reasons=reasons,
            parsed_a=parsed_a,
            parsed_b=parsed_b,
        )

    if parsed_a.title is None or parsed_b.title is None:
        return _result(
            0.0, BAND_REJECT, TEAM_VERDICT_ABSENT, 0.0, None, None,
            (REJECT_PARSE_FAILURE,),
        )

    if parsed_a.start is None or parsed_b.start is None:
        # No parsed time → unmatchable, never guessed.
        return _result(
            0.0, BAND_REJECT, TEAM_VERDICT_ABSENT, 0.0, None, None,
            (REJECT_NO_PARSED_TIME,),
        )

    delta = abs((parsed_a.start - parsed_b.start).total_seconds()) / 60.0
    verdict, team_score = _team_pair_verdict(parsed_a.teams, parsed_b.teams)
    fuzzy = _fuzzy_title_score(parsed_a.title, parsed_b.title)

    if delta > window_minutes:
        # Time-window blocking: not a candidate pair at all. Diagnostic
        # fields (verdict, fuzzy) are still populated for the preview UI.
        return _result(
            0.0, BAND_REJECT, verdict, fuzzy, team_score, delta,
            (REJECT_OUTSIDE_TIME_WINDOW,),
        )

    if verdict == TEAM_VERDICT_CONFLICT:
        # HARD REJECT — mirrors the M1 callsign rail. Score forced to 0.0.
        return _result(
            0.0, BAND_REJECT, verdict, fuzzy, team_score, delta,
            (REJECT_TEAM_TOKEN_CONFLICT,),
        )

    if verdict != TEAM_VERDICT_AGREE and _numeric_identity_conflict(
        parsed_a.title, parsed_b.title
    ):
        # Numeric-identity rail (PR #611 review, finding 4): with no
        # positive team corroboration, disjoint series/edition numbers
        # ('UFC 317' vs 'Bellator 300', 'Formula 1' vs 'Formula 2') mean
        # different events regardless of shared surrounding words. Team
        # AGREEMENT deliberately bypasses this rail — a mislabeled week
        # number must not reject two providers carrying the same fixture.
        return _result(
            0.0, BAND_REJECT, verdict, fuzzy, team_score, delta,
            (REJECT_NUMERIC_IDENTITY_CONFLICT,),
        )

    # Token agreement raises confidence: an agreeing team pair can lift a
    # lexically-distant abbreviation ('MUFC' / 'Manchester United') that
    # pure title fuzz under-scores.
    #
    # DESIGN NOTE (PR #611 review, finding 3): because team_score >= 0.80
    # whenever the verdict is AGREE, this max() means team agreement within
    # the time window is BY ITSELF sufficient to reach the attach band —
    # fuzzy corroboration of the full title is not additionally required.
    # This is intentional: both teams aligning (order-insensitively, with
    # qualifier equality) at the same parsed kickoff IS the event identity;
    # the surrounding title text is provider dressing (slot words,
    # competition labels, language). The rails stay intact — a qualifier
    # or nickname mismatch is a hard conflict long before this line.
    if verdict == TEAM_VERDICT_AGREE and team_score is not None:
        score = min(1.0, max(fuzzy, team_score))
    else:
        score = fuzzy

    if is_event_attachable(score, verdict, threshold=threshold):
        return _result(score, BAND_ATTACH, verdict, fuzzy, team_score, delta, ())
    if score >= EVENT_AMBIGUOUS_FLOOR:
        return _result(score, BAND_AMBIGUOUS, verdict, fuzzy, team_score, delta, ())
    return _result(
        score, BAND_REJECT, verdict, fuzzy, team_score, delta,
        (REJECT_BELOW_AMBIGUOUS_FLOOR,),
    )


def score_pair(
    name_a: str,
    name_b: str,
    *,
    patterns: Sequence[dict] | None = None,
    window_minutes: int = DEFAULT_TIME_WINDOW_MINUTES,
    threshold: float = EVENT_ATTACH_FLOOR,
    event_timezone: str = DEFAULT_EVENT_TIMEZONE,
    now: datetime | None = None,
) -> PairScore:
    """Score two raw provider event names through every matcher layer.

    The pairwise entry point used by the frozen-corpus CI gate; the
    stream-vs-masters path (:func:`match_streams`) routes through the same
    ``_score_parsed_pair`` body so the two can never diverge.
    """
    parsed_a = parse_event_name(
        name_a, patterns, event_timezone=event_timezone, now=now
    )
    parsed_b = parse_event_name(
        name_b, patterns, event_timezone=event_timezone, now=now
    )
    return _score_parsed_pair(
        parsed_a, parsed_b, window_minutes=window_minutes, threshold=threshold
    )


# ---------------------------------------------------------------------------
# Stream → master-channel matching (the preview / attach resolver surface).
# ---------------------------------------------------------------------------


def match_streams(
    stream_names: Sequence[str],
    master_names: Sequence[str],
    *,
    patterns: Sequence[dict] | None = None,
    master_patterns: Sequence[dict] | None = None,
    window_minutes: int = DEFAULT_TIME_WINDOW_MINUTES,
    threshold: float = EVENT_ATTACH_FLOOR,
    event_timezone: str = DEFAULT_EVENT_TIMEZONE,
    now: datetime | None = None,
    assume_current_date: bool = False,
) -> list[StreamMatchResult]:
    """Match every secondary stream name against the master channel names.

    Masters are parsed once. For each stream, candidate generation is the
    time-window block (masters whose parsed start is within
    ±``window_minutes``); each candidate is scored and banded, ordered
    best-first (score desc, then master name asc for determinism).

    ``patterns`` parses the secondary stream names; ``master_patterns``
    (when given) parses the master names instead of ``patterns`` — the
    per-group pattern override surface (event_sync_config.group_patterns,
    bead ti939.1.4): a master group and a secondary group may ship
    different name shapes, and parsing masters with the secondary group's
    override would silently fail or mis-title them. ``None`` keeps the
    original behavior (both sides share ``patterns``).

    Masters are identified by NAME only — the caller re-resolves channel
    IDs against Dispatcharr on every run.
    """
    tz = pytz.timezone(event_timezone)
    if now is None:
        now = datetime.now(tz)

    effective_master_patterns = (
        master_patterns if master_patterns is not None else patterns
    )
    parsed_masters = [
        parse_event_name(
            m, effective_master_patterns, event_timezone=event_timezone,
            now=now, assume_current_date=assume_current_date,
        )
        for m in master_names
    ]
    # Only masters with a complete parsed identity can ever be candidates.
    usable_masters = [
        p for p in parsed_masters if p.title is not None and p.start is not None
    ]

    results: list[StreamMatchResult] = []
    for stream_name in stream_names:
        parsed = parse_event_name(
            stream_name, patterns, event_timezone=event_timezone, now=now,
            assume_current_date=assume_current_date,
        )
        if parsed.title is None:
            results.append(StreamMatchResult(
                stream_name=stream_name,
                parsed=parsed,
                candidates=(),
                unmatchable_reason=REJECT_PARSE_FAILURE,
            ))
            continue
        if parsed.start is None:
            results.append(StreamMatchResult(
                stream_name=stream_name,
                parsed=parsed,
                candidates=(),
                unmatchable_reason=REJECT_NO_PARSED_TIME,
            ))
            continue

        candidates: list[MasterCandidate] = []
        for master in usable_masters:
            delta = abs((parsed.start - master.start).total_seconds()) / 60.0
            if delta > window_minutes:
                continue  # blocked — not a candidate
            pair = _score_parsed_pair(
                parsed, master, window_minutes=window_minutes, threshold=threshold
            )
            candidates.append(MasterCandidate(
                master_name=master.raw_name,
                parsed=master,
                score=pair.score,
                band=pair.band,
                team_verdict=pair.team_verdict,
                time_delta_minutes=delta,
                reject_reasons=pair.reject_reasons,
            ))
        candidates.sort(key=lambda c: (-c.score, c.master_name))
        results.append(StreamMatchResult(
            stream_name=stream_name,
            parsed=parsed,
            candidates=tuple(candidates),
            unmatchable_reason=None,
        ))
    return results


def match_stream_to_masters(
    stream_name: str,
    master_names: Sequence[str],
    *,
    patterns: Sequence[dict] | None = None,
    window_minutes: int = DEFAULT_TIME_WINDOW_MINUTES,
    threshold: float = EVENT_ATTACH_FLOOR,
    event_timezone: str = DEFAULT_EVENT_TIMEZONE,
    now: datetime | None = None,
) -> StreamMatchResult:
    """Single-stream convenience wrapper over :func:`match_streams`."""
    return match_streams(
        [stream_name],
        master_names,
        patterns=patterns,
        window_minutes=window_minutes,
        threshold=threshold,
        event_timezone=event_timezone,
        now=now,
    )[0]
