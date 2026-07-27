"""
Event sync review-queue identity layer — content fingerprints + operator
decisions (bead enhancedchannelmanager-ti939.3.2, epic ti939 "Event Sync").

Ambiguous-band matches (including the contested rail) route into an
operator review queue instead of being silently skipped. This module owns
the PURE half of that feature: how a queue item is IDENTIFIED and how an
operator's prior decisions feed back into resolution. Persistence lives in
``services.event_sync_review_store``; the DB model is
``models.EventSyncReview``.

**Fingerprint keying (HARD security constraint, epic ti939.3).** Every
pending question and every accepted/rejected outcome keys on the content
fingerprint

    (rule_id, provider_id, normalized_stream_name_hash, normalized_event_key)

and NEVER on channel or stream IDs. Rationale: Dispatcharr stream IDs churn
on every provider refresh and channel IDs are stable only while an event's
channel lives — a queue keyed on IDs would refill with duplicates of
already-answered questions after every refresh (the exact failure the epic
forbids). The four components:

* ``rule_id`` — the event_sync rule the question arose under (scoping is
  the caller's job; this module never sees rule ids).
* ``provider_id`` — the secondary stream's M3U account id. Account rows are
  ECM/Dispatcharr configuration, stable across refreshes.
  :data:`PROVIDER_ID_UNKNOWN` (0) is the documented sentinel when a stream
  carries no resolvable account id — such decisions still persist, but only
  re-apply to streams that are equally account-less.
* ``normalized_stream_name_hash`` — SHA-256 hex of the LOCALS-cleaned raw
  stream name (:func:`normalize_stream_name`). The same shared cleaner the
  unified scoring core uses, so cosmetic churn (case, punctuation, spacing,
  quality tags) does not mint a new question, while any change the MATCHER
  would see differently also re-opens the question — by construction the
  fingerprint can never be more forgiving than the scorer.
* ``normalized_event_key`` — the MASTER side's parsed event identity
  (:func:`master_event_key`): LOCALS-cleaned parsed title + the parsed
  start time normalized to UTC. Master channels are identified by parsed
  identity only (the epic's stateless-recompute contract); the key survives
  master-channel recreation and name dressing, and two sessions of the same
  fixture (main card vs. prelims) stay distinct via title and/or start.
  EXCEPTION (bead t6bin): when the parse SYNTHESIZED the date from "now"
  (the assume_current_date dateless variants), the key carries the literal
  ``dateless`` marker plus the LOCAL clock time instead of the fabricated
  date — a recurring dateless slot mints the SAME key every day, so
  accepts/rejects carry forward across midnight instead of re-asking.

**Decisions are an input to the ONE resolver, not a second scorer.**
:class:`ReviewDecisions` is consumed by
``services.event_sync_resolver.resolve_event_sync`` (the single decision
path shared by preview and attach — dry-run parity by construction):

* an ACCEPTED pairing auto-attaches when the matcher still surfaces that
  master as an attach- or ambiguous-band candidate. Accepts never override
  the matcher's hard rejects (team-token conflict, time window, below the
  ambiguous floor) — precision over recall, the 1,341-incident benchmark;
* a REJECTED pairing is suppressed entirely: the candidate is removed
  before classification, so it can neither attach (threshold OR decision)
  nor re-enqueue. An explicit operator "no" outranks score drift.

**Pure module.** No DB, no Dispatcharr client, no engine imports — same
isolation contract as ``services.event_sync_matcher``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytz

from services.dedup_matcher import NameCleanMode, clean_name
from services.event_sync_matcher import (
    SYNTHESIZED_DATE_PATTERN_NAMES,
    ParsedEvent,
)

_UTC = pytz.utc
_ZERO = timedelta(0)

__all__ = [
    "ATTACH_SOURCE_REVIEW_QUEUE",
    "ATTACH_SOURCE_THRESHOLD",
    "EMPTY_DECISIONS",
    "MAX_REVIEW_CANDIDATES_PER_STREAM",
    "MAX_REVIEW_ENQUEUE_PER_RUN",
    "PROVIDER_ID_UNKNOWN",
    "REVIEW_STATUS_ACCEPTED",
    "REVIEW_STATUS_PENDING",
    "REVIEW_STATUS_REJECTED",
    "REVIEW_STATUS_SUPERSEDED",
    "PairingKey",
    "ReviewDecisions",
    "master_event_key",
    "normalize_stream_name",
    "pairing_key",
    "stream_name_hash",
]

# Review-item state machine (mirrors the pending_merges precedent, ADR-008
# §D3, with one addition): pending → accepted | rejected via the operator
# endpoints; pending → superseded when a SIBLING pairing for the same stream
# fingerprint is accepted (the stream-level question was answered, so the
# other candidates must not linger as open questions — but they are not an
# operator "no" either, hence the distinct terminal state).
REVIEW_STATUS_PENDING = "pending"
REVIEW_STATUS_ACCEPTED = "accepted"
REVIEW_STATUS_REJECTED = "rejected"
REVIEW_STATUS_SUPERSEDED = "superseded"

# How a would_attach disposition was reached — journal + preview contract
# (the bead's human-factors requirement: queue-driven attaches must be
# distinguishable from threshold attaches).
ATTACH_SOURCE_THRESHOLD = "threshold"
ATTACH_SOURCE_REVIEW_QUEUE = "review_queue"

# Documented sentinel for "stream carried no resolvable M3U account id".
# The fingerprint column is NOT NULL (SQLite treats NULLs as distinct in
# unique indexes, which would silently break the dedup invariant).
PROVIDER_ID_UNKNOWN = 0

# Per-stream cap on enqueue-eligible candidate pairings. Candidates arrive
# best-first from the matcher, so the cap only trims the diagnostic tail of
# a pathologically contested stream.
MAX_REVIEW_CANDIDATES_PER_STREAM = 5

# Per-run cap on NEW review rows (blast-radius control, parallel to
# max_attach_per_run): a silently broken pattern on an unattended run must
# not flood the queue. Runs are idempotent — the remainder re-surfaces on
# the next run once the operator drains or fixes.
MAX_REVIEW_ENQUEUE_PER_RUN = 500

# (provider_id, normalized_stream_name_hash, normalized_event_key) —
# rule_id scoping is applied by the caller when loading decisions, so the
# in-memory key carries the other three components.
PairingKey = tuple[int, str, str]


def normalize_stream_name(name: str) -> str:
    """Normalize a raw secondary-stream name for fingerprinting.

    LOCALS-mode :func:`services.dedup_matcher.clean_name` — the ONE shared
    cleaner the scoring stack uses — so the identity the queue keys on is
    exactly as forgiving as the identity the matcher scores on. Falls back
    to lowercased whitespace-collapsed raw text when the cleaner strips a
    name to nothing (an all-punctuation name must still fingerprint
    deterministically rather than collide on the empty string).
    """
    cleaned = clean_name(name, mode=NameCleanMode.LOCALS)
    if cleaned:
        return cleaned
    return " ".join(name.lower().split())


def stream_name_hash(name: str) -> str:
    """SHA-256 hex digest of the normalized stream name.

    Hashed (rather than stored verbatim) so the fingerprint column has a
    fixed shape and cannot be confused with a display name — the RAW name
    lives in the row's evidence snapshot for the operator's eyes; the hash
    is the identity.
    """
    return hashlib.sha256(normalize_stream_name(name).encode("utf-8")).hexdigest()


def _dateless_clock_token(start: datetime) -> str:
    """``HH:MM±HH:MM`` — LOCAL clock time + STANDARD offset of the parse tz.

    ``start`` is localized in the rule's event timezone by the parser, so
    ``strftime`` reads the clock time the provider name literally carried
    ("6PM" → ``18:00``) — stable across DST. The offset is the timezone's
    STANDARD (non-DST) offset, ``utcoffset - dst``: the datetime's ACTUAL
    offset shifts twice a year (EDT −04:00 vs EST −05:00) and would re-open
    every dateless slot at each DST transition; the standard offset is a
    season-independent identifier of the parse timezone. NOT UTC
    time-of-day, for the same DST reason.
    """
    offset = (start.utcoffset() or _ZERO) - (start.dst() or _ZERO)
    total_minutes = int(offset.total_seconds()) // 60
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return (
        f"{start.strftime('%H:%M')}"
        f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"
    )


def master_event_key(parsed: ParsedEvent) -> str | None:
    """Normalized event identity of one parsed MASTER channel name.

    ``<LOCALS-cleaned parsed title>|<parsed start as UTC ISO-8601>`` — or
    ``None`` when the parse is incomplete (an unparsed master can never be
    an attach target, so it can never be the subject of a review question).

    The start time is converted to UTC so the key is independent of the
    rule's display timezone; the title is cleaned with the same shared
    cleaner the fuzzy scorer uses (see :func:`normalize_stream_name` for
    the empty-clean fallback rationale).

    SYNTHESIZED-DATE EXCEPTION (bead t6bin): when ``matched_pattern`` is
    one of the assume_current_date dateless variants
    (:data:`~services.event_sync_matcher.SYNTHESIZED_DATE_PATTERN_NAMES`)
    the date component of ``start`` was fabricated from "now" — embedding
    it would mint a NEW key at every midnight, so operator decisions on a
    recurring dateless slot could never carry forward (the jqwfq rail
    would re-demote the same slot daily). Such parses key on

        ``<cleaned title>|dateless|<LOCAL clock HH:MM><standard tz offset>``

    instead (see :func:`_dateless_clock_token`). Dated parses are
    byte-identical to the pre-t6bin format — stored rows for dated events
    need no migration. Pre-upgrade rows for DATELESS slots re-ask once
    (synthesized-ness is not recoverable from a stored key).
    """
    if parsed.title is None or parsed.start is None:
        return None
    cleaned = clean_name(parsed.title, mode=NameCleanMode.LOCALS)
    if not cleaned:
        cleaned = " ".join(parsed.title.lower().split())
    if parsed.matched_pattern in SYNTHESIZED_DATE_PATTERN_NAMES:
        return f"{cleaned}|dateless|{_dateless_clock_token(parsed.start)}"
    return f"{cleaned}|{parsed.start.astimezone(_UTC).isoformat()}"


def pairing_key(
    provider_id: int | None, name: str, parsed_master: ParsedEvent
) -> PairingKey | None:
    """Build the in-memory pairing key for one (stream, master) pair.

    ``None`` when the master has no complete parsed identity (see
    :func:`master_event_key`). ``provider_id=None`` maps to
    :data:`PROVIDER_ID_UNKNOWN`.
    """
    event_key = master_event_key(parsed_master)
    if event_key is None:
        return None
    return (
        provider_id if provider_id is not None else PROVIDER_ID_UNKNOWN,
        stream_name_hash(name),
        event_key,
    )


@dataclass(frozen=True)
class ReviewDecisions:
    """Prior operator decisions for ONE rule, keyed by pairing fingerprint.

    Loaded by ``services.event_sync_review_store.load_review_decisions``
    and threaded into ``resolve_event_sync`` — preview and attach receive
    the same object, so decision application cannot diverge between them.
    """

    accepted: frozenset[PairingKey] = frozenset()
    rejected: frozenset[PairingKey] = frozenset()

    def is_accepted(self, key: PairingKey | None) -> bool:
        return key is not None and key in self.accepted

    def is_rejected(self, key: PairingKey | None) -> bool:
        return key is not None and key in self.rejected


EMPTY_DECISIONS = ReviewDecisions()
