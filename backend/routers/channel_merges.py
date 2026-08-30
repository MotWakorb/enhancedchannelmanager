"""
Channel merges router — dedup candidate lookup + pending-merge resolution.

Part of the interactive stream-to-channel deduplication feature (bd-1v4ht,
ADR-008). This module owns the ``/api/channel-merges/*`` route family.

Endpoint surface (ADR-008 §D1):

  GET  /api/channel-merges/candidates    — BD-D (bd-kbqwb): synchronous
                                            top-1 candidate lookup for
                                            the operator-facing dedup
                                            modal.
  GET  /api/channel-merges               — BD-E (bd-acqkb): paginated
                                            pending-merges queue.
  POST /api/channel-merges/{id}/accept   — BD-E (bd-acqkb): operator
                                            confirms a merge.
  POST /api/channel-merges/{id}/dismiss  — BD-E (bd-acqkb): operator
                                            rejects a candidate.

Companion surface:
  Bulk-import enqueueing of pending_merges rows is BD-F (bd-a5lb2 —
  ``backend/services/m3u_dedup_hook.py``).

API contract per ADR-008 §D1 (D4 override — plural-noun resource path,
not the original /api/dedup/* draft). Response envelope follows the ECM
flat-outcome pattern — no top-level ``data`` wrapper.

**Auth posture** (post-BD-E review):
  * List (GET /api/channel-merges) — ``RequireAuthIfEnabled`` (read).
  * /candidates lookup — ``RequireAdminIfEnabled`` (BD-D matched the
    rest of the protected API surface; preserved).
  * /accept and /dismiss — ``RequireAdminIfEnabled`` (writes that
    materially mutate Dispatcharr channel structure; aligned with the
    rest of the channel-mutation endpoints).

The acting User's ``id`` is recorded as ``actor_token_id`` on the
journal — per ADR-008 §D6 the "token's DB id" — so audit revocation /
rotation traces back to the action that used the credential. When auth
is disabled (``RequireAdminIfEnabled`` returns ``None``), the literal
string ``"anonymous"`` is recorded so the NOT NULL invariant on the
audit substrate still holds.

Metrics (BD-M LOCKED CONTRACT, ``docs/runbooks/dedup-merge-api-error-
rate-high.md`` + ``docs/runbooks/dedup-candidate-lookup-latency.md``):

  * ``ecm_dedup_candidate_lookup_duration_seconds`` (Histogram) —
    emitted by /candidates wrapping the matcher call. SLO-10 latency
    SLI. Owned by BD-D.
  * ``ecm_dedup_merge_requests_total{status="success"|"error"|"dismissed"}``
    — emitted by /accept and /dismiss on every terminal-state
    transition. Owned by BD-E. The ``cancelled`` label is reserved
    for the modal-cancel surface (BD-G) and is NOT emitted here.
  * ``ecm_pending_merges_queue_depth_added_total`` — emitted by BD-F's
    bulk-import hook (``backend/services/m3u_dedup_hook.py``), NOT
    by this router. accept/dismiss transition status; they do not INSERT.

State machine (ADR-008 §D3):

  pending → merged    via POST /api/channel-merges/{id}/accept
  pending → dismissed via POST /api/channel-merges/{id}/dismiss

Terminal states are idempotent: a second accept on a ``merged`` row
returns the prior outcome envelope (not 409). Same for dismiss. An
invalid cross-state transition (accept on a dismissed row, dismiss on
a merged row) returns 409 with a clear detail.

Audit substrate (ADR-008 §D6): every accept / dismiss writes a
``pending_merge_journal`` row with all seven contract fields
(``actor_token_id``, ``action_type``, ``source_channel_id``,
``target_channel_id``, ``confidence_score``, ``timestamp_utc``,
``trigger_context``). No JSON blobs — every field is a queryable
column. The MCP-vs-operator distinction comes from
``actor_token_id`` + ``trigger_context``, answerable from a single SQL
query (no log-correlation required).

Lazy resolution (ADR-008 §D4): the accept endpoint calls
``client.get_channel(candidate_channel_id)`` as its first step.
A 404 returns HTTP 404 with the operator-actionable detail
"target channel no longer exists — dismiss this pending merge and
refresh"; recovery is then a /dismiss + re-trigger of the original
import / drag-drop.
"""

from __future__ import annotations

import logging
import time
from typing import List, Literal, NamedTuple, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi import status as http_status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import RequireAdminIfEnabled, RequireAuthIfEnabled
from config import get_settings
from database import get_session
from dispatcharr_client import get_client
from models import PendingMerge, PendingMergeJournal
from observability import get_metric
# The ONE journal writer, which CHECKS both of `journal`'s return values (bead
# …-kz089 fix round 5, reused here for …-i5ic0). No import cycle —
# `routers.channels` does not import this module.
from routers.channels import flush_journal_rows_on_exit, write_journal_rows
from services.dedup_matcher import CONFIDENCE_FLOOR, find_candidate, MatchResult
from services.m3u_dedup_hook import enqueue_pending_merge

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channel-merges", tags=["Channel Merges"])


# ---------------------------------------------------------------------------
# BD-E pagination defaults (ADR-008 §D1: "page, page_size with sane defaults
# — 1/50").
# ---------------------------------------------------------------------------
DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200  # bound the worst-case response size — mirrors other ECM list endpoints
MAX_SNAPSHOT_ROWS = 20_000

# Status enum — matches the CHECK constraint on pending_merges.status (§D8).
_VALID_STATUSES = ("pending", "merged", "dismissed")

# Stream-name resolution pagination ceiling. Substring/full-text search on
# Dispatcharr can match many streams for a common prefix (e.g. "ESPN" →
# "ESPN HD", "ESPN HD West", "ESPN 2 HD", ...). page_size=500 mirrors the
# bulk-import pattern in dispatcharr_client.py (get_logos, get_channels_bulk)
# and is a defensible defense ceiling; results hitting this ceiling are
# logged at WARN so operators can see the ambiguity.
STREAM_LOOKUP_PAGE_SIZE = 500


# ---------------------------------------------------------------------------
# BD-D response models (preserved verbatim)
# ---------------------------------------------------------------------------

class DedupCandidate(BaseModel):
    """Single dedup candidate returned by the lookup endpoint.

    ``channel_id`` is a string because ``candidate_channel_id`` is stored as
    TEXT in the ``pending_merges`` schema (ADR-008 §D8 channel-id-type note —
    corrects the epic body's ``DedupCandidate.channel_id: number`` to string).
    """

    channel_id: str
    channel_name: str
    confidence: float


class CandidatesResponse(BaseModel):
    """Response envelope for GET /api/channel-merges/candidates.

    Pagination fields are always present for forward-compat (ADR-008 §D1
    notes a future bead may expose top-N). In v0.17.1 the matcher returns
    top-1 only, so ``total`` is 0 or 1 and ``total_pages`` is 0 or 1.
    """

    stream_name: str
    candidates: list[DedupCandidate]
    total: int
    page: int
    page_size: int
    total_pages: int


# ---------------------------------------------------------------------------
# BD-E response models
# ---------------------------------------------------------------------------

class PendingMergeRecord(BaseModel):
    """Single pending_merges row, shaped for the list endpoint.

    Base field set matches ADR-008 §D1's response contract:
    ``{id, stream_name, group_id, candidate_channel_id, confidence,
       status, created_at, resolved_at?, resolution_source?,
       trigger_context}``.

    ``candidate_channel_name`` / ``candidate_channel_number`` /
    ``candidate_channel_group_name`` are additive fields (bead
    enhancedchannelmanager-09x38.14) resolved from Dispatcharr at list
    time so the operator can see what they'd be merging into without
    leaving the page — previously only the bare ``candidate_channel_id``
    was available. All three are ``None`` when the candidate channel
    could not be resolved (deleted since queuing, or Dispatcharr was
    unreachable at list time) — the frontend renders an explicit
    "channel no longer exists" fallback using the id in that case.
    Additive-only: no existing field was removed or renamed.
    """

    id: int
    stream_name: str
    group_id: Optional[int] = None
    candidate_channel_id: str
    candidate_channel_name: Optional[str] = None
    candidate_channel_number: Optional[float] = None
    candidate_channel_group_name: Optional[str] = None
    confidence: float
    status: str
    created_at: int
    resolved_at: Optional[int] = None
    resolution_source: Optional[str] = None
    trigger_context: str
    #: Why the LAST accept on this row could not be applied to Dispatcharr, in
    #: operator-actionable prose; ``None`` when no accept has failed to apply.
    #: A row with ``status='pending'`` AND this set is a merge the operator
    #: accepted that ECM could not carry out — it stays in the queue, flagged,
    #: and retrying it is an ordinary accept (bead
    #: ``enhancedchannelmanager-i5ic0``, PO decision 2026-08-16).
    unapplied_reason: Optional[str] = None


class PendingMergesListResponse(BaseModel):
    """Paginated envelope for GET /api/channel-merges.

    Pagination shape matches the existing ECM list-endpoint pattern
    (``total``, ``page``, ``page_size``, ``total_pages``) so a frontend
    that already paginates against ``/api/channels`` can reuse the same
    helpers without a new envelope shape.
    """

    merges: List[PendingMergeRecord]
    total: int
    page: int
    page_size: int
    total_pages: int


class PendingMergesSnapshotResponse(BaseModel):
    """One coherent, bounded snapshot of the complete pending queue."""

    merges: List[PendingMergeRecord]
    total: int


class StreamLookup(NamedTuple):
    """What a stream-name resolution actually established.

    ``matches`` empty means three different things and the caller must be able
    to tell them apart, because two of them are statements about ECM and one is
    a statement about the operator's data (bead
    ``enhancedchannelmanager-i5ic0``):

    * ``failed`` — the lookup itself did not complete. NO evidence either way
      about whether the stream exists. Reported as "no match", an upstream
      outage became an accusation against the operator's catalogue.
    * ``truncated`` — the search filled its single page, so an exact match may
      exist on a page nobody asked for. Also not evidence of absence.
    * neither — the search completed, saw everything it asked for, and nothing
      matched. This is the only one that means "that stream is not there".

    Completeness cuts BOTH ways, which is what round 1 got wrong. ``failed`` and
    ``truncated`` were consulted on the no-match path and nowhere else, so one
    usable exact match on a full page went straight to the PATCH: the 500th
    result is not the last result, and the match chosen may be one of several
    duplicates spread across pages nobody asked for. Reading ``matches`` to
    decide what to PATCH is therefore not something a caller may do —
    :meth:`conclusive_match` is the only door, and it is shut whenever the
    search did not see everything it asked about. ``matches`` remains readable
    for REPORTING (:func:`_unapplied_reason` says how many were visible), which
    is a different question from what the lookup established.
    """

    matches: list[dict]
    truncated: bool
    failed: bool

    @property
    def complete(self) -> bool:
        """Whether this search saw everything it asked about.

        A lookup that did not complete, and one that filled its single page,
        are the same fact for every decision built on it: what is in
        ``matches`` may not be all there is.
        """
        return not self.failed and not self.truncated

    def conclusive_match(self) -> Optional[dict]:
        """The one stream this lookup ESTABLISHED, or ``None``.

        The single decision point for "is there a stream to add". ``None`` means
        the merge must not be applied — for any of the four reasons
        :func:`_unapplied_reason` puts into words — and a caller cannot reach a
        stream id without going through it. Uniqueness is only knowable when the
        search was complete, so an incomplete search answers ``None`` however
        promising its visible page looks.
        """
        if not self.complete:
            return None
        if len(self.matches) != 1:
            return None
        if self.matches[0].get("id") is None:
            return None
        return self.matches[0]


def _unapplied_reason(lookup: StreamLookup, stream_name: str) -> Optional[str]:
    """Why the Dispatcharr-side merge did not happen, in words, or ``None``.

    Every branch names the stream, because the operator's next action is to go
    and look for it. ``None`` means the merge applied and there is nothing to
    report — the flag has to be able to read clean or it carries no information.
    """
    if lookup.failed:
        return (
            f"The Dispatcharr stream lookup for \"{stream_name}\" could not be "
            "completed, so this merge was recorded but NOT applied upstream. "
            "The stream may well exist. Retry once Dispatcharr is reachable, or "
            "add the stream to the channel by hand."
        )
    if lookup.truncated:
        # BEFORE the several-matches branch, because truncation is the stronger
        # statement: it says ECM does not know how many there are, and that is
        # true whether the visible page held none, one or several. Round 1 put
        # this last and worded it "without an exact match", which is false in
        # the case that matters most — one visible match, uniqueness unknown —
        # and an operator told there is no match stops looking for a duplicate.
        visible = len(lookup.matches)
        seen = (
            f"{visible} stream(s) on that page are named exactly "
            f"\"{stream_name}\""
            if visible
            else f"nothing on that page is named exactly \"{stream_name}\""
        )
        return (
            f"The search for \"{stream_name}\" filled its single page of "
            f"{STREAM_LOOKUP_PAGE_SIZE} results, so ECM never saw the whole "
            f"catalogue — {seen}, and further matches may exist on a page it "
            "did not ask for. This merge was recorded but NOT applied "
            "upstream, because adding one of several possible streams is not "
            "the merge you asked for. Narrow the stream's name or add it to "
            "the channel by hand."
        )
    if len(lookup.matches) > 1:
        return (
            f"{len(lookup.matches)} Dispatcharr streams are named "
            f"\"{stream_name}\", so this merge was recorded but NOT applied "
            "upstream — ECM cannot tell which one you meant. Add the right "
            "stream to the channel by hand, or rename the duplicates."
        )
    if len(lookup.matches) == 1:
        # Complete, unambiguous, and still not usable: the one match carries no
        # id. Round 1 folded this into the no-match branch, which then accused
        # the operator's catalogue of a gap that is really ECM's.
        return (
            f"The Dispatcharr stream named \"{stream_name}\" was found but "
            "carries no usable id, so this merge was recorded but NOT applied "
            "upstream. Add the stream to the channel by hand."
        )
    return (
        f"No Dispatcharr stream is named \"{stream_name}\", so this merge was "
        "recorded but NOT applied upstream. The stream may have been renamed or "
        "removed since it was queued."
    )


class AcceptOutcome(BaseModel):
    """Flat-outcome response for POST /api/channel-merges/{id}/accept.

    ADR-008 §D1: returns ``{merged_into_channel_id, journal_entry_id,
    source_stream_id, confidence, status: 'merged'}`` flat.

    ``source_stream_id`` carries the resolved Dispatcharr stream id
    when the stream-name lookup found a unique match; otherwise it
    falls back to the raw ``stream_name`` (audit-first contract — see
    ``PendingMergeJournal.source_channel_id`` in models.py and ADR-008
    §D6 for the documented fallback semantics).

    ``confidence`` is the RapidFuzz score captured at queue-time, mirrored
    here so the operator's UI / MCP client sees what the decision was
    made against without a second round-trip to the journal.

    ``status`` describes the QUEUE ROW, and it is no longer always terminal
    (PO decision 2026-08-16). ``'merged'`` when the merge was applied upstream
    and the row left the queue; ``'pending'`` when ECM could not apply it, in
    which case the row STAYS in the queue carrying ``unapplied_reason``, stays
    counted by the queue badge, and stays retryable — a later accept on that
    row is a real accept, not an idempotent replay. A consumer that hardcoded
    ``'merged'`` is reading a claim this response no longer makes.

    Whether DISPATCHARR was updated is a separate fact and used to be
    unanswerable from this response — the queue row went terminal, the audit
    row was written and the caller got a ``200`` whether the stream had been
    added upstream or the name had matched nothing at all (bead
    ``enhancedchannelmanager-i5ic0``). Three fields carry that fact now:

    ``dispatcharr_updated``
        ``True`` when the candidate channel ends this request holding the
        stream — whether this call PATCHed it or it was already there. ``False``
        when it does not, which includes every lookup whose COMPLETENESS is
        unknown: a truncated page cannot establish uniqueness even when exactly
        one exact match is visible on it, so it is not conclusive in either
        direction (:meth:`StreamLookup.conclusive_match`). ``None`` on an
        idempotent replay, which performed no Dispatcharr call and therefore has
        no evidence about what the original one did; guessing ``True`` there
        would be the same false claim one branch over.

        Three values, so a consumer that tests ``!= False`` has two of them
        collapsed. Each has its own path in ``PendingMergesPage``. They map
        one-to-one onto the queue state rather than duplicating it:
        ``True`` -> ``status='merged'``, ``pending_merges.unapplied_reason``
        clear; ``False`` -> ``status='pending'``, that column set; ``None`` ->
        a replay, which only an ALREADY-terminal row can produce. A row
        deliberately still queued can therefore never answer ``None``, which is
        what keeps "this request obtained no upstream evidence" and "still
        queued on purpose" from collapsing into one value.
    ``unapplied_reason``
        Operator-actionable prose for anything other than a clean apply, naming
        the stream and WHY it could not be resolved. ``None`` when applied. The
        same text is persisted on the queue row, so the operator sees it on the
        row itself rather than only in this response.
    ``journal_rows_unwritten``
        Rows of the operator-facing journal this request could not write.
        Always present, so a caller checks a number rather than probing.
    """

    merged_into_channel_id: str
    journal_entry_id: int
    source_stream_id: str
    confidence: float
    status: Literal["merged", "pending"] = "merged"
    dispatcharr_updated: Optional[bool] = True
    unapplied_reason: Optional[str] = None
    journal_rows_unwritten: int = 0


class DismissOutcome(BaseModel):
    """Flat-outcome response for POST /api/channel-merges/{id}/dismiss.

    ADR-008 §D1: "Response: {journal_entry_id, status: 'dismissed'} flat".
    """

    journal_entry_id: int
    status: Literal["dismissed"] = "dismissed"


# ---------------------------------------------------------------------------
# bd-b3czq: POST /api/channel-merges — enqueue (ADR-008 §D7 MCP prompt path)
# ---------------------------------------------------------------------------


class EnqueueMergeRequest(BaseModel):
    """Request body for POST /api/channel-merges (the MCP prompt-mode enqueue).

    The caller supplies only the stream *context* — never a confidence.
    The endpoint re-runs the matcher server-side against the live
    Dispatcharr candidate set to capture the authoritative confidence at
    action time (ADR-008 §D6, mirroring the bulk-M3U hook). A
    client-supplied confidence cannot be trusted: the candidate pool and
    the operator threshold may have drifted since the client last looked.

    ``group_id`` is optional — ``None`` is the ungrouped scope (the matcher
    searches all groups), matching ``GET /api/channel-merges/candidates``.
    """

    stream_name: str
    group_id: Optional[int] = None


class EnqueueMergeResponse(BaseModel):
    """Response for POST /api/channel-merges.

    Two shapes, distinguished by ``created`` / ``merge_id``:

    * **Candidate found (``created`` may be True or False).** A
      ``pending_merges`` row exists for this pair. ``merge_id`` is the row
      id the agent passes to ``accept_channel_merge`` /
      ``dismiss_channel_merge``. ``created`` is True when this call
      inserted the row, False when an existing pending row was returned
      idempotently (§D5 collision). The candidate fields + ``confidence``
      echo what the row was queued against so the agent does not need a
      second round-trip.
    * **No candidate above threshold.** ``merge_id`` is ``None``,
      ``created`` is False, and the candidate fields are ``None``. The
      caller proceeds with normal channel creation (the matcher found
      nothing above ``max(threshold, floor)``).

    The HTTP status carries the same fresh-vs-idempotent signal: 201 on a
    fresh insert, 200 on an idempotent collision or a no-candidate result.

    ``meets_threshold`` is the server-authoritative answer to "is this
    candidate's confidence at or above the operator's auto-merge
    threshold?" — so a ``merge_if_found`` caller can decide auto-accept vs
    prompt without re-reading settings. The enqueue matcher runs at the
    §D2 *floor* (not the threshold) so it surfaces every show-able
    candidate to the agent, mirroring the operator modal; the threshold is
    the auto-merge bar the caller applies, reported here. ``None`` when no
    candidate was found.
    """

    merge_id: Optional[int] = None
    created: bool = False
    candidate_channel_id: Optional[str] = None
    candidate_channel_name: Optional[str] = None
    confidence: Optional[float] = None
    meets_threshold: Optional[bool] = None
    status: Optional[str] = None


# ---------------------------------------------------------------------------
# BD-E internal helpers
# ---------------------------------------------------------------------------
def _now_epoch_ms() -> int:
    """Return the current UTC time as an epoch-ms integer.

    Matches the ADR-007 / ADR-008 §D8 epoch-ms convention used by
    ``pending_merges.created_at`` and the journal's ``timestamp_utc``.
    Centralized here so tests can monkeypatch a single function for
    deterministic timestamps.
    """
    return int(time.time() * 1000)


def _record_to_dict(row: PendingMerge) -> dict:
    """Project a ``PendingMerge`` ORM row to the list-endpoint dict shape.

    Explicit projection (not ``__dict__``) so adding a column to the
    model later cannot accidentally widen the API response — a new
    public field requires a deliberate edit here.
    """
    return {
        "id": row.id,
        "stream_name": row.stream_name,
        "group_id": row.group_id,
        "candidate_channel_id": row.candidate_channel_id,
        "confidence": row.confidence,
        "status": row.status,
        "created_at": row.created_at,
        "resolved_at": row.resolved_at,
        "resolution_source": row.resolution_source,
        "trigger_context": row.trigger_context,
        "unapplied_reason": row.unapplied_reason,
    }


def _actor_token_id(user) -> str:
    """Resolve the audit-journal ``actor_token_id`` for an HTTP caller.

    Per ADR-008 §D6 the field is "the token's DB id, not a username
    string". For JWT-authenticated calls the bearer is the ``User``
    row — its ``id`` is the closest stable, opaque, revocation-traceable
    identifier ECM has today. When auth is disabled
    (``RequireAdminIfEnabled`` returns ``None``), record the literal
    string ``"anonymous"`` so the audit row is still complete and the
    schema NOT NULL invariant holds — operators running without auth
    still get a usable audit trail of who-did-what, just without the
    actor identity ECM cannot prove anyway.
    """
    if user is None:
        return "anonymous"
    return str(user.id)


def _latest_journal_entry_id(db: Session, pending_merge_id: int) -> int:
    """Return the most-recent journal row id for this pending merge.

    Used by the idempotent branches — on a double-accept or double-
    dismiss we return the outcome envelope of the original action.
    The original journal row id is the stable handle the operator can
    correlate back to the audit log. ``int`` to match the response model.

    Raises if no journal row exists (which would mean the
    pending_merges row is in a terminal state but the audit trail is
    missing — a data-integrity bug we want to fail loud on, not paper
    over).
    """
    row = (
        db.query(PendingMergeJournal)
        .filter(PendingMergeJournal.pending_merge_id == pending_merge_id)
        .order_by(PendingMergeJournal.id.desc())
        .first()
    )
    if row is None:
        raise RuntimeError(
            f"pending_merges.id={pending_merge_id} is in a terminal state "
            "but has no pending_merge_journal row — audit-trail invariant "
            "violated"
        )
    return int(row.id)


def _latest_journal_source(db: Session, pending_merge_id: int) -> str:
    """Return the ``source_channel_id`` recorded on the most-recent journal row.

    Mirrors ``_latest_journal_entry_id`` — used by the idempotent
    double-accept path so the prior outcome envelope can echo the same
    ``source_stream_id`` the original action recorded (rather than
    re-resolving by name, which may now drift). Same data-integrity
    contract: a terminal-state row without an audit row is a fail-loud
    bug.
    """
    row = (
        db.query(PendingMergeJournal)
        .filter(PendingMergeJournal.pending_merge_id == pending_merge_id)
        .order_by(PendingMergeJournal.id.desc())
        .first()
    )
    if row is None:
        raise RuntimeError(
            f"pending_merges.id={pending_merge_id} is in a terminal state "
            "but has no pending_merge_journal row — audit-trail invariant "
            "violated"
        )
    return str(row.source_channel_id)


def _write_journal(
    db: Session,
    *,
    pending_merge_id: int,
    actor_token_id: str,
    action_type: Literal["merge_confirmed", "merge_dismissed"],
    source_channel_id: str,
    target_channel_id: str,
    confidence_score: float,
    trigger_context: str,
) -> PendingMergeJournal:
    """Append a single audit row to ``pending_merge_journal``.

    All seven §D6 fields are required arguments — there is no default
    or fallback. A missing field is a coding bug, not a runtime data
    case, so an immediate TypeError at the call site is better than
    silently writing an under-specified audit row.

    ``source_channel_id`` carries the Dispatcharr stream id when the
    name lookup resolved unambiguously; otherwise it falls back to the
    raw ``stream_name`` (audit-first contract — see the column docstring
    in ``models.py`` and ADR-008 §D6 for the documented fallback).

    Returns the newly-flushed row so callers can capture
    ``row.id`` for the response envelope. The transaction is NOT
    committed here — the calling endpoint owns the unit of work so the
    journal write and the pending_merges status flip land in a single
    commit (or rollback together on error).
    """
    entry = PendingMergeJournal(
        pending_merge_id=pending_merge_id,
        actor_token_id=actor_token_id,
        action_type=action_type,
        source_channel_id=source_channel_id,
        target_channel_id=target_channel_id,
        confidence_score=confidence_score,
        timestamp_utc=_now_epoch_ms(),
        trigger_context=trigger_context,
    )
    db.add(entry)
    db.flush()  # populate entry.id without committing yet
    return entry


async def _resolve_candidate_channels(candidate_ids: set) -> dict:
    """Resolve ``candidate_channel_id`` -> Dispatcharr channel dict for a page.

    Join-cost note (bead enhancedchannelmanager-09x38.14): this issues
    exactly ONE Dispatcharr call — ``get_channels(page_size=1000)`` — for
    the whole list page, mirroring the "candidate pool" convention already
    used by ``GET /candidates`` and the enqueue endpoint above. Resolving
    per-row (one ``get_channel(id)`` per pending_merges row) would scale
    with queue depth — up to ``MAX_PAGE_SIZE`` (200) Dispatcharr round-
    trips for a single page load — and was rejected as an N+1 explosion.
    The batch approach costs O(1) regardless of how many rows are on the
    page; the accepted trade-off is the same 1000-channel ceiling already
    baked into the other two endpoints in this file — an install with
    more than 1000 Dispatcharr channels could have a legitimate candidate
    beyond the first page render as unresolved rather than a confirmed
    delete. That is judged acceptable here: it degrades to the same
    "channel no longer exists" UI fallback the deleted-candidate case
    already needs, it never fails the request, and fixing it properly
    would require a bulk id-filter query param Dispatcharr does not
    expose today.

    Returns ``{}`` (every candidate unresolved) when ``candidate_ids`` is
    empty (skips the Dispatcharr call entirely) or when the Dispatcharr
    fetch itself fails — a name-resolution problem must never turn into
    a 500 for the whole pending-merges queue view.
    """
    if not candidate_ids:
        return {}
    try:
        client = get_client()
        channels_data = await client.get_channels(page=1, page_size=1000)
    except Exception as e:
        logger.warning(
            "[CHANNEL-MERGES] Failed to fetch channels from Dispatcharr for "
            "pending-merges name resolution: %s", e,
        )
        return {}

    results = channels_data.get("results", [])
    return {
        str(ch["id"]): ch
        for ch in results
        if ch.get("id") is not None and str(ch["id"]) in candidate_ids
    }


def _bump_metric(status: str) -> None:
    """Increment ``ecm_dedup_merge_requests_total{status=...}``.

    Defensive: a metric-emit failure must NEVER break the merge
    endpoint — the merge is the load-bearing write path of the dedup
    epic (SLO-10c) and an observability failure cannot become a
    business failure. A failed emit logs at DEBUG and continues.

    The ``status`` argument is the BD-M contract label
    (``success`` | ``error`` | ``dismissed`` | ``unapplied``);
    ``cancelled`` is reserved for the modal surface and never emitted from
    this router.

    ``unapplied`` was added with the PO decision of 2026-08-16 (bead
    ``enhancedchannelmanager-i5ic0``) and is REQUIRED by SLI-10b's own
    definition rather than being an extra dimension. ``docs/sre/slos.md``
    defines that SLI's numerator as "terminal-state transitions out of the
    queue", and an accept ECM could not apply now makes no such transition:
    its row stays ``pending``, flagged. Counting it as ``success`` would have
    reported the queue being cleared while flagged rows accumulated in it, and
    would have suppressed ``ECMDedupPendingMergeResolutionStale``, the one
    alert that exists to notice that. Dropping the emit entirely was the other
    option and is worse: the request happened, and not counting it shrinks
    SLI-10c's error-rate DENOMINATOR instead. Additive for every existing
    query — ``{status="error"}`` and ``{status=~"success|dismissed"}`` keep
    working and become more accurate. ``docs/sre/slos.md`` and
    ``docs/runbooks/dedup-*.md`` enumerate the label values and need amending.
    """
    try:
        get_metric("dedup_merge_requests_total").labels(status=status).inc()
    except Exception:  # pragma: no cover — observability must not break the write path
        logger.debug("[DEDUP] metric emit failed for status=%s", status)


# ---------------------------------------------------------------------------
# BD-D: GET /api/channel-merges/candidates — synchronous lookup
# ---------------------------------------------------------------------------

@router.get("/candidates", response_model=CandidatesResponse)
async def get_dedup_candidates(
    stream_name: str = Query(..., description="Raw stream name to find candidates for"),
    group_id: Optional[int] = Query(None, description="Restrict to candidates in this group; omit to search all groups"),
    page: int = Query(1, ge=1, description="Page number (always 1 in v0.17.1 — top-1 matcher)"),
    page_size: int = Query(50, ge=1, le=200, description="Page size (pagination placeholder; top-1 only in v0.17.1)"),
    _admin=RequireAdminIfEnabled,
) -> CandidatesResponse:
    """Synchronous top-1 candidate lookup for the dedup modal (ADR-008 §D1).

    Fetches channels from Dispatcharr (filtered by ``group_id`` when provided),
    passes them to the dedup matcher, and returns the top-1 candidate or an
    empty list when no match clears the floor.

    The operator-configured ``dedup_threshold`` is read from settings at
    request time — live, not cached — so a Settings change takes effect on
    the next modal open without a container restart.

    Confidence floor enforcement is delegated to the matcher (BD-A). This
    endpoint does NOT duplicate the floor check; it passes the configured
    threshold and trusts the matcher's ADR-008 §D2 clamp.

    Metrics: emits ``ecm_dedup_candidate_lookup_duration_seconds`` (the BD-M
    SLO-10 latency SLI) wrapping the matcher call.
    """
    if not stream_name.strip():
        # Validate non-blank stream_name early so the error message is useful.
        # Pydantic's Query(...) guarantees presence; this guards the empty-string
        # case which Query cannot reject by type alone.
        raise HTTPException(status_code=400, detail="stream_name must not be blank")

    try:
        client = get_client()
        # Fetch channels, filtered by group_id when provided. The Dispatcharr
        # client's get_channels() accepts channel_group as an int filter param
        # that maps to Dispatcharr's ?channel_group= query param.
        channels_data = await client.get_channels(
            page=1,
            page_size=1000,  # Fetch a large batch — candidate pool for fuzzy matching
            channel_group=group_id,
        )
    except Exception as e:
        logger.warning("[CHANNEL-MERGES] Failed to fetch channels from Dispatcharr: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    results = channels_data.get("results", [])

    # Build the candidate list. channel_id is cast to str per ADR-008 §D8
    # (TEXT column in pending_merges; Dispatcharr UUIDs arrive as ints or
    # strings depending on the Dispatcharr version — normalize to string so
    # the matcher's tie-break comparisons are consistent).
    candidates: list[tuple[str, str]] = [
        (str(ch["id"]), ch["name"])
        for ch in results
        if ch.get("id") is not None and ch.get("name")
    ]

    settings = get_settings()
    threshold = settings.dedup_threshold  # 0.0–1.0; clamped to floor by BD-A

    # Emit the BD-M locked-contract metric wrapping the matcher call only.
    start = time.perf_counter()
    try:
        match: MatchResult | None = find_candidate(stream_name, candidates, threshold)
    except Exception as e:
        logger.warning("[CHANNEL-MERGES] Matcher raised unexpectedly: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        duration = time.perf_counter() - start
        try:
            get_metric("dedup_candidate_lookup_duration_seconds").observe(duration)
        except Exception:
            # Observability must never break the request path it wraps.
            logger.debug("[CHANNEL-MERGES] Failed to emit lookup duration metric", exc_info=True)

    logger.debug(
        "[CHANNEL-MERGES] candidates lookup stream_name=%r group_id=%s candidates=%d match=%s duration_ms=%.1f",
        stream_name,
        group_id,
        len(candidates),
        match.candidate_channel_id if match else None,
        duration * 1000,
    )

    # The matcher returns top-1. Wrap in a 1-element list (or empty list) for
    # stable typing. Pagination fields are degenerate but always present for
    # forward-compat (ADR-008 §D1: a future bead may expose top-N).
    candidate_list: list[DedupCandidate] = []
    if match is not None:
        candidate_list.append(
            DedupCandidate(
                channel_id=match.candidate_channel_id,
                channel_name=match.candidate_name,
                confidence=match.confidence,
            )
        )

    total = len(candidate_list)
    total_pages = 1 if total > 0 else 0

    return CandidatesResponse(
        stream_name=stream_name,
        candidates=candidate_list,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ---------------------------------------------------------------------------
# bd-b3czq: POST /api/channel-merges — enqueue (ADR-008 §D7 MCP prompt path)
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=EnqueueMergeResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def enqueue_pending_merge_endpoint(
    request: Request,
    body: EnqueueMergeRequest,
    response: Response,
    db: Session = Depends(get_session),
    user=RequireAdminIfEnabled,
) -> EnqueueMergeResponse:
    """Async-queue a merge candidate and return its ``merge_id`` (ADR-008 §D7).

    This is the enqueue half of the §D7 MCP prompt path. The
    ``add_stream(dedup_action='prompt')`` MCP tool calls it when it wants
    to defer a probable-duplicate decision to the AI agent: the endpoint
    creates a ``pending_merges`` row (``trigger_context='mcp_tool'``),
    writes the §D6 ``auto_queued`` audit row, and returns the new
    ``merge_id`` so the agent can call back via
    ``POST /api/channel-merges/{merge_id}/accept`` or ``.../dismiss``.

    Admin-gated — same auth posture as accept / dismiss. ADR-008 §D7
    ratifies MCP api_key holders as authorized to trigger merges; the
    journal's ``actor_token_id`` + ``trigger_context='mcp_tool'`` is the
    audit trail for that posture.

    **Confidence is captured server-side.** The matcher re-runs against
    the live Dispatcharr candidate set (filtered by ``group_id`` when
    given) using the operator-configured ``dedup_threshold`` read at
    request time — exactly like ``GET /candidates`` and the bulk-M3U hook.
    A client-supplied confidence is not accepted; the action-time score is
    the authoritative one per §D6.

    Outcomes:

    * **Candidate found, fresh insert** → 201, ``{merge_id, created: true,
      candidate_*, confidence, status: 'pending'}``.
    * **Candidate found, §D5 idempotent collision** → 200, the existing
      pending row's ``merge_id`` with ``created: false`` (same contract as
      the bulk-M3U hook — the prior row is authoritative).
    * **No candidate above the §D2 floor** → 200,
      ``{merge_id: null, created: false}``. The caller proceeds with normal
      channel creation.

    The enqueue itself (INSERT → §D5 → §D6 journal → BD-M metric) is
    delegated to the shared ``enqueue_pending_merge`` core so this path and
    the M3U hook cannot drift.
    """
    stream_name = body.stream_name.strip()
    if not stream_name:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="stream_name must not be blank",
        )

    # Re-run the matcher server-side against the live candidate set, exactly
    # like GET /candidates — so the queued confidence is the action-time one.
    try:
        client = get_client()
        channels_data = await client.get_channels(
            page=1,
            page_size=1000,
            channel_group=body.group_id,
        )
    except Exception as e:
        logger.warning(
            "[CHANNEL-MERGES] enqueue: failed to fetch channels from "
            "Dispatcharr: %s", e,
        )
        raise HTTPException(status_code=500, detail=str(e))

    results = channels_data.get("results", [])
    candidates: list[tuple[str, str]] = [
        (str(ch["id"]), ch["name"])
        for ch in results
        if ch.get("id") is not None and ch.get("name")
    ]

    settings = get_settings()
    threshold = settings.dedup_threshold  # operator auto-merge bar

    # Match at the §D2 FLOOR (not the threshold) so the enqueue surfaces
    # every show-able candidate to the agent — mirroring the operator modal
    # "prompt at MCP scale" intent of §D7. The threshold is the auto-merge
    # bar; we report whether the found candidate meets it via
    # ``meets_threshold`` so a merge_if_found caller can decide auto-accept
    # vs prompt without re-reading settings. (The bulk-M3U hook and the
    # operator /candidates lookup keep matching at the threshold — only this
    # MCP enqueue path widens to the floor.)
    try:
        match: MatchResult | None = find_candidate(
            stream_name, candidates, CONFIDENCE_FLOOR
        )
    except Exception as e:
        logger.warning("[CHANNEL-MERGES] enqueue: matcher raised: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    if match is None:
        # No candidate above the floor: nothing to queue. The caller proceeds
        # with normal channel creation. 200, not 201 — no resource created.
        response.status_code = http_status.HTTP_200_OK
        logger.debug(
            "[CHANNEL-MERGES] enqueue: no candidate for stream=%r group_id=%s "
            "(floor=%.2f, candidates=%d) — no row queued",
            stream_name, body.group_id, CONFIDENCE_FLOOR, len(candidates),
        )
        return EnqueueMergeResponse(merge_id=None, created=False)

    meets_threshold = match.confidence >= threshold

    # Delegate the INSERT → §D5 idempotency → §D6 journal → BD-M metric to
    # the shared core. trigger_context='mcp_tool' per §D7; actor is the
    # acting token id (or 'anonymous' when auth is disabled).
    try:
        enqueued = enqueue_pending_merge(
            stream_name=stream_name,
            group_id=body.group_id,
            match=match,
            trigger_context="mcp_tool",
            actor_token_id=_actor_token_id(user),
            db_session=db,
        )
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.exception(
            "[CHANNEL-MERGES] enqueue failed for stream=%r candidate=%s: %s",
            stream_name, match.candidate_channel_id, e,
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error enqueueing pending merge",
        )

    # Fresh insert → 201; idempotent collision → 200 (no resource created).
    response.status_code = (
        http_status.HTTP_201_CREATED if enqueued.fresh else http_status.HTTP_200_OK
    )
    logger.info(
        "[CHANNEL-MERGES] enqueue ok: merge_id=%s fresh=%s stream=%r "
        "candidate=%s confidence=%.2f actor=%s trigger=mcp_tool",
        enqueued.merge_id, enqueued.fresh, stream_name,
        match.candidate_channel_id, match.confidence, _actor_token_id(user),
    )
    return EnqueueMergeResponse(
        merge_id=enqueued.merge_id,
        created=enqueued.fresh,
        candidate_channel_id=match.candidate_channel_id,
        candidate_channel_name=match.candidate_name,
        confidence=match.confidence,
        meets_threshold=meets_threshold,
        status="pending",
    )


# ---------------------------------------------------------------------------
# GH #642: GET /api/channel-merges/snapshot — complete pending queue
# ---------------------------------------------------------------------------
@router.get("/snapshot", response_model=PendingMergesSnapshotResponse)
async def snapshot_pending_merges(
    group_id: Optional[int] = None,
    db: Session = Depends(get_session),
    _user=RequireAdminIfEnabled,
) -> PendingMergesSnapshotResponse:
    """Return a transactionally coherent, admin-gated pending-queue snapshot.

    A single ordered SELECT avoids offset-pagination races while another actor
    resolves the queue. Reading one row beyond the cap makes oversized queues
    fail closed without returning a partial target set.
    """
    query = db.query(PendingMerge).filter(PendingMerge.status == "pending")
    if group_id is not None:
        query = query.filter(PendingMerge.group_id == group_id)
    rows = (
        query
        .order_by(PendingMerge.created_at.desc(), PendingMerge.id.desc())
        .limit(MAX_SNAPSHOT_ROWS + 1)
        .all()
    )
    if len(rows) > MAX_SNAPSHOT_ROWS:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"Pending merge snapshot exceeds the safety limit of "
                f"{MAX_SNAPSHOT_ROWS} records. Nothing was changed."
            ),
        )

    candidate_ids = {row.candidate_channel_id for row in rows}
    channel_lookup = await _resolve_candidate_channels(candidate_ids)
    merges: list[PendingMergeRecord] = []
    for row in rows:
        record_dict = _record_to_dict(row)
        channel = channel_lookup.get(row.candidate_channel_id)
        if channel is not None:
            record_dict["candidate_channel_name"] = channel.get("name")
            record_dict["candidate_channel_number"] = channel.get("channel_number")
            record_dict["candidate_channel_group_name"] = channel.get(
                "channel_group_name"
            )
        merges.append(PendingMergeRecord(**record_dict))
    return PendingMergesSnapshotResponse(merges=merges, total=len(merges))


# ---------------------------------------------------------------------------
# BD-E: GET /api/channel-merges — paginated queue list
# ---------------------------------------------------------------------------
@router.get("", response_model=PendingMergesListResponse)
async def list_pending_merges(
    status: str = "pending",
    group_id: Optional[int] = None,
    page: int = DEFAULT_PAGE,
    page_size: int = DEFAULT_PAGE_SIZE,
    db: Session = Depends(get_session),
    _user=RequireAuthIfEnabled,
) -> PendingMergesListResponse:
    """List pending_merges rows filtered by status and optional group.

    Defaults follow ADR-008 §D1:
      * ``status='pending'`` — the operator-facing queue view.
      * ``page=1, page_size=50`` — the same envelope shape other ECM
        list endpoints use.

    Ordering: ``created_at DESC`` so the operator sees the most recent
    candidates first — matches the "Pending Merges page" UX intent
    (BD-J).

    Read posture — uses ``RequireAuthIfEnabled`` rather than the
    admin-gated dependency the mutation endpoints use; listing the
    queue is information-only.
    """
    if status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=(
                f"status must be one of {list(_VALID_STATUSES)}; got {status!r}"
            ),
        )

    if page < 1:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="page must be >= 1",
        )
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"page_size must be between 1 and {MAX_PAGE_SIZE}",
        )

    query = db.query(PendingMerge).filter(PendingMerge.status == status)
    if group_id is not None:
        query = query.filter(PendingMerge.group_id == group_id)

    total = query.count()
    rows = (
        query.order_by(PendingMerge.created_at.desc(), PendingMerge.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    total_pages = (total + page_size - 1) // page_size if total else 0

    # Resolve candidate channel name/number/group in a single batched
    # Dispatcharr call — see ``_resolve_candidate_channels`` docstring for
    # the join-cost analysis (bead enhancedchannelmanager-09x38.14).
    candidate_ids = {r.candidate_channel_id for r in rows}
    channel_lookup = await _resolve_candidate_channels(candidate_ids)

    merges: list[PendingMergeRecord] = []
    for r in rows:
        record_dict = _record_to_dict(r)
        channel = channel_lookup.get(r.candidate_channel_id)
        if channel is not None:
            record_dict["candidate_channel_name"] = channel.get("name")
            record_dict["candidate_channel_number"] = channel.get("channel_number")
            record_dict["candidate_channel_group_name"] = channel.get(
                "channel_group_name"
            )
        merges.append(PendingMergeRecord(**record_dict))

    return PendingMergesListResponse(
        merges=merges,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ---------------------------------------------------------------------------
# BD-E: POST /api/channel-merges/{id}/accept — operator confirms the merge
# ---------------------------------------------------------------------------
@router.post("/{merge_id}/accept", response_model=AcceptOutcome)
async def accept_pending_merge(
    merge_id: int,
    request: Request,
    db: Session = Depends(get_session),
    user=RequireAdminIfEnabled,
) -> AcceptOutcome:
    """Accept a pending merge: trigger the Dispatcharr update + audit.

    Admin-gated — this is a write that materially mutates Dispatcharr
    channel structure (adds a stream to a channel). The auth posture
    mirrors ``POST /api/channels/merge`` and the rest of the
    channel-mutation surface.

    Flow (ADR-008 §D3 / §D4 / §D6):

      1. Load the pending_merges row. 404 if missing.
      2. Idempotent terminal-state check:
         * already 'merged'  → return the prior outcome envelope (200).
         * already 'dismissed' → 409 (invalid cross-transition).
      3. Lazy resolution per §D4: ``client.get_channel(candidate_channel_id)``.
         A 404 from Dispatcharr returns HTTP 404 with the operator-actionable
         detail. The pending row stays 'pending' so the operator can
         /dismiss + re-trigger.
      4. Effect the merge — add the matching stream to the candidate
         channel via ``client.update_channel`` (best-effort, see below).
      5. Flip pending_merges row to 'merged' + resolved_at + resolution_source.
      6. Write a ``pending_merge_journal`` row with the full §D6 audit set.
      7. Commit; emit ``ecm_dedup_merge_requests_total{status=success}``.
      8. Return ``{merged_into_channel_id, journal_entry_id,
         source_stream_id, confidence, status='merged'}``.

    **Stream-resolution semantics for the actual merge (BD-E scope
    note).** The ``pending_merges`` schema stores ``stream_name``, not
    a stream id. To effect the Dispatcharr-side merge we search streams
    by name and add the unique match to the candidate channel. When the
    name search returns zero matches or multiple ambiguous matches, the
    audit-first contract still records the operator's decision — the
    merge is marked ``merged``, the journal row is written, and the
    metric is bumped — and a WARN logs the resolution problem so the
    operator can reconcile manually. The journal's ``source_channel_id``
    column carries the resolved stream id when the lookup succeeded
    unambiguously, and the raw ``stream_name`` as the documented
    audit-first fallback otherwise (ADR-008 §D6; see also the
    ``PendingMergeJournal.source_channel_id`` docstring in models.py).

    Any unhandled exception in the Dispatcharr-effect step rolls back
    the DB transaction, bumps the ``status='error'`` counter, and
    re-raises as HTTP 500 — the operator sees a clear failure and the
    SLI-10c error rate climbs as expected.
    """
    row = db.query(PendingMerge).filter(PendingMerge.id == merge_id).first()
    if row is None:
        # Not an SLI-10c error — this is operator-input error (a stale
        # frontend reference). The 4xx exclusion in the runbook applies.
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"pending merge id={merge_id} not found",
        )

    # ----- Idempotency: already in a terminal state ------------------------
    if row.status == "merged":
        # Double-accept — return the prior outcome envelope (§D1).
        journal_id = _latest_journal_entry_id(db, row.id)
        source_stream_id = _latest_journal_source(db, row.id)
        logger.info(
            "[DEDUP] accept idempotent: pending_merges id=%s already merged "
            "(journal_entry_id=%s); returning prior outcome",
            row.id, journal_id,
        )
        # No metric bump — idempotent replays are not a new business event.
        return AcceptOutcome(
            merged_into_channel_id=row.candidate_channel_id,
            journal_entry_id=journal_id,
            source_stream_id=source_stream_id,
            confidence=row.confidence,
            # NOT `True`. This request made no Dispatcharr call, so it has no
            # evidence about what the original one did, and asserting an
            # outcome it did not observe is the same false success claim bead
            # …-i5ic0 is about — one branch over (see `AcceptOutcome`).
            dispatcharr_updated=None,
            unapplied_reason=(
                "This merge was already resolved by an earlier request, which "
                "this one replayed without contacting Dispatcharr. Whether "
                "Dispatcharr was updated is recorded in the journal against "
                f"channel {row.candidate_channel_id}, not here."
            ),
        )

    if row.status == "dismissed":
        # Cross-state transition — 409 per §D3 invariant; counts as
        # a 4xx-by-design, NOT an SLI-10c error. Per the runbook
        # contract, status='rejected' / 409-by-design is recorded as
        # 'dismissed' on the metric so SLI-10b sees the resolution
        # signal (the row already reached a terminal state; the
        # operator just clicked the wrong button).
        _bump_metric("dismissed")
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"pending merge id={merge_id} is already dismissed; "
                "cannot accept a row that was rejected"
            ),
        )

    # ----- Lazy resolution: candidate channel must still exist -------------
    # ADR-008 §D4: this is the FIRST mutation-adjacent call. A 404 here
    # is operator-actionable (dismiss + retrigger), not an SLI-10c error.
    client = get_client()
    try:
        channel = await client.get_channel(row.candidate_channel_id)
    except httpx.HTTPStatusError as fetch_err:
        if fetch_err.response.status_code == 404:
            logger.warning(
                "[DEDUP] accept rejected: candidate_channel_id=%s no "
                "longer exists in Dispatcharr (pending_merges.id=%s)",
                row.candidate_channel_id, row.id,
            )
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=(
                    "Target channel no longer exists in Dispatcharr — "
                    "dismiss this pending merge and refresh the channel list"
                ),
            )
        # Any other HTTP error from Dispatcharr is an SLI-10c error.
        _bump_metric("error")
        logger.exception(
            "[DEDUP] accept failed: Dispatcharr get_channel returned %s "
            "for candidate=%s (pending_merges.id=%s)",
            fetch_err.response.status_code, row.candidate_channel_id, row.id,
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Dispatcharr API error during merge candidate lookup",
        )
    except Exception as e:  # noqa: BLE001 — broad on purpose; any failure is SLI-10c
        _bump_metric("error")
        logger.exception(
            "[DEDUP] accept failed: candidate lookup raised "
            "(pending_merges.id=%s): %s", row.id, e,
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error during merge candidate lookup",
        )

    # ----- Best-effort Dispatcharr-side merge ------------------------------
    # Resolve the source stream by name. The schema gap (no stream_id
    # in pending_merges) means we search; ambiguity is a WARN, not an
    # abort — the audit-first contract still records the decision.
    source_stream_identifier = row.stream_name  # used by the journal if unresolved
    # What actually happened upstream, decided here and reported verbatim
    # rather than assumed from the fact that the request did not raise (bead
    # …-i5ic0). `patched` is narrower than `dispatcharr_updated`: a stream
    # already on the channel means the merge IS applied and no row is owed.
    dispatcharr_updated = False
    unapplied_reason: Optional[str] = None
    patched = False
    resolved_stream_id: Optional[int] = None
    channel_name = channel.get("name") or f"Channel {row.candidate_channel_id}"

    # The operator-facing journal, which is where the user guide tells an
    # operator to trace a channel's history — and the only place a merge that
    # was NOT applied upstream becomes findable afterwards. The
    # `pending_merge_journal` row below records the DECISION (ADR-008 §D6);
    # these record the OUTCOME, which is a different fact and was recorded
    # nowhere (bead …-i5ic0).
    #
    # Queued the moment the write they describe lands, and flushed on every
    # exit through the `finally` at the bottom. Round 1 CONSTRUCTED them after
    # `db.commit()` returned, so a commit that failed after a landed PATCH left
    # the stream attached upstream with no row even attempted — and a retry
    # then reads as "already in the desired state", concealing which request
    # performed the mutation. Same shape as the immediate group-delete path in
    # `routers/channel_groups.py`: a pending list, an idempotent drain-then-
    # write flush, and a `try/finally`.
    outcome_rows: list[dict] = []
    # Set by the `except BaseException` below, read by the `finally`: a flush
    # that raises must never REPLACE an exception already on its way out.
    unwinding = False

    def flush_outcome_rows() -> int:
        """Write what is queued and return how many could NOT be written.

        Idempotent by construction — the queue is emptied before the write, so
        the ``finally`` cannot write a row the success path already wrote.
        """
        if not outcome_rows:
            return 0
        draining = list(outcome_rows)
        outcome_rows.clear()
        return write_journal_rows(draining, log_tag="DEDUP")

    try:
        try:
            lookup = await _resolve_streams_by_name(client, row.stream_name)
        except Exception as e:  # noqa: BLE001 — any Dispatcharr failure is SLI-10c
            _bump_metric("error")
            logger.exception(
                "[DEDUP] accept failed during stream resolution "
                "(pending_merges.id=%s): %s", row.id, e,
            )
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Dispatcharr API error during merge",
            )

        # THE single decision point. `conclusive_match` is shut whenever the
        # search did not see everything it asked about, so a truncated or
        # failed lookup cannot reach the PATCH however promising its visible
        # page looks — the fix that generalises past the branch that read
        # `matches[0]["id"]` directly.
        match = lookup.conclusive_match()
        if match is not None:
            resolved_stream_id = match["id"]
            source_stream_identifier = str(resolved_stream_id)
        else:
            # Zero matches, several matches, a match with no usable id, a
            # truncated page, a failed lookup — five ways to reach here and one
            # sentence for each.
            unapplied_reason = _unapplied_reason(lookup, row.stream_name)
            logger.warning(
                "[DEDUP] accept: pending_merges.id=%s recorded WITHOUT a "
                "Dispatcharr-side update: %s", row.id, unapplied_reason,
            )

        # ----- DB state transition + audit row -----------------------------
        # Both writes happen in one commit so a crash between them cannot
        # leave the queue in a half-resolved state.
        #
        # ATTEMPTED BEFORE THE PATCH, on purpose. `_write_journal` flushes, so
        # a read-only, full or locked database fails HERE — before anything
        # irreversible has happened upstream — and the 500 that follows is then
        # true in both directions: nothing was recorded and nothing was
        # written. Round 1 PATCHed first, so the ordinary local-persistence
        # failure returned a 500 for a request that had already mutated
        # Dispatcharr. The commit itself is still after the PATCH, because the
        # queue row must not go terminal for a merge Dispatcharr rejected; that
        # residual window is what the queued `stream_add` row below covers.
        #
        # A MERGE ECM COULD NOT APPLY DOES NOT TRANSITION AT ALL (PO decision
        # 2026-08-16, bead …-i5ic0). The previous shape flipped the row to
        # `merged` and let it leave the queue carrying its reason — internally
        # consistent, but the reason then outlived the row where only an
        # operator who went looking in the journal would ever find it. The row
        # now stays `pending` with `unapplied_reason` set: still in the list,
        # still counted by the badge, still holding its §D5 uniqueness slot,
        # and still retryable. `resolved_at` / `resolution_source` describe a
        # row that LEFT the queue, so they stay NULL. The operator's DECISION
        # is still recorded — that is the `merge_confirmed` audit row below,
        # and §D6's audit-first contract is what makes recording it right even
        # when ECM cannot act on it.
        now_ms = _now_epoch_ms()
        if unapplied_reason is None:
            row.status = "merged"
            row.resolved_at = now_ms
            row.resolution_source = "operator"
            # Cleared on the way out, so a retry that resolves leaves no stale
            # reason behind and reads exactly like a first-time accept.
            row.unapplied_reason = None
        else:
            row.unapplied_reason = unapplied_reason

        try:
            entry = _write_journal(
                db=db,
                pending_merge_id=row.id,
                actor_token_id=_actor_token_id(user),
                action_type="merge_confirmed",
                source_channel_id=source_stream_identifier,
                target_channel_id=row.candidate_channel_id,
                confidence_score=row.confidence,
                trigger_context=row.trigger_context,
            )
        except Exception as e:  # noqa: BLE001
            db.rollback()
            _bump_metric("error")
            logger.exception(
                "[DEDUP] accept failed while staging the audit row, BEFORE any "
                "Dispatcharr write (pending_merges.id=%s): %s", row.id, e,
            )
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal error persisting merge outcome",
            )

        if match is not None:
            try:
                patched = await _add_stream_to_channel(
                    client=client,
                    channel=channel,
                    stream_id=resolved_stream_id,
                )
            except Exception as e:  # noqa: BLE001 — any Dispatcharr failure is SLI-10c
                db.rollback()
                _bump_metric("error")
                logger.exception(
                    "[DEDUP] accept failed during Dispatcharr merge "
                    "(pending_merges.id=%s): %s", row.id, e,
                )
                raise HTTPException(
                    status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Dispatcharr API error during merge",
                )
            dispatcharr_updated = True
            if patched:
                # QUEUED HERE — the PATCH has returned, so the stream is on the
                # channel and that is true whatever happens next. A merge that
                # landed by already being in the desired state gets no row:
                # nothing changed, so there is nothing to trace.
                outcome_rows.append({
                    "category": "channel",
                    "action_type": "stream_add",
                    "entity_id": None,
                    "entity_name": channel_name,
                    "description": (
                        f"Added stream '{row.stream_name}' to channel "
                        f"'{channel_name}' from the pending-merge queue"
                    ),
                    "after_value": {
                        "streams": [resolved_stream_id],
                        "channel_id": row.candidate_channel_id,
                        "pending_merge_id": row.id,
                    },
                })

        try:
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            _bump_metric("error")
            logger.exception(
                "[DEDUP] accept failed during commit "
                "(pending_merges.id=%s): %s", row.id, e,
            )
            if dispatcharr_updated:
                # The queue row really did roll back, so a failure response is
                # the truth about THIS request — what it must not do is bury
                # the upstream write. `sanitized_http_exception_handler`
                # replaces the detail of every 500, so the log is the only
                # place this advisory can reach a human, the same posture the
                # immediate group-delete path takes for its landed moves.
                logger.error(
                    "[DEDUP] Stream %s IS attached to channel %s in Dispatcharr "
                    "and ECM could not record it: pending_merges.id=%s stays "
                    "pending, so a retry will find the channel already in the "
                    "desired state and report it as applied without a PATCH",
                    resolved_stream_id, row.candidate_channel_id, row.id,
                )
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal error persisting merge outcome",
            )

        # `success` is SLI-10b's numerator and means the row LEFT the queue.
        # An accept ECM could not apply did not resolve anything, so it is
        # counted as itself; see `_bump_metric`.
        _bump_metric("success" if dispatcharr_updated else "unapplied")
        # Update the companion queue-depth gauge (bd-wvr1d). Best-effort:
        # a failed COUNT or gauge.set is logged at WARN inside the helper and
        # never blocks the accept response — the DB commit is the source of truth.
        try:
            from observability import set_pending_merges_queue_depth_gauge
            set_pending_merges_queue_depth_gauge(db)
        except Exception:  # pragma: no cover — defensive import guard
            logger.warning("[DEDUP] gauge update failed after accept commit")
        logger.info(
            "[DEDUP] accept ok: pending_merges.id=%s %s candidate=%s "
            "journal_entry_id=%s actor=%s",
            row.id,
            "merged into" if dispatcharr_updated else "NOT applied to",
            row.candidate_channel_id, entry.id, _actor_token_id(user),
        )

        if not dispatcharr_updated:
            # Queued only once the decision is COMMITTED. Unlike the stream_add
            # row this describes an ECM-side fact — "accepted, and not applied"
            # — which is not true of a request whose transition rolled back.
            outcome_rows.append({
                "category": "channel",
                "action_type": "merge_unapplied",
                "entity_id": None,
                "entity_name": channel_name,
                "description": (
                    f"Accepted the pending merge of stream '{row.stream_name}' into "
                    f"channel '{channel_name}', but Dispatcharr was NOT updated: "
                    f"{unapplied_reason} The merge stays in Pending Merges, "
                    "flagged as not applied, and can be retried."
                ),
                "after_value": {
                    "channel_id": row.candidate_channel_id,
                    "pending_merge_id": row.id,
                    "stream_name": row.stream_name,
                    "dispatcharr_updated": False,
                    # The queue state this outcome left behind, recorded beside
                    # the outcome so the two cannot be read apart later.
                    "pending_merge_status": "pending",
                },
            })

        return AcceptOutcome(
            merged_into_channel_id=row.candidate_channel_id,
            journal_entry_id=int(entry.id),
            source_stream_id=source_stream_identifier,
            confidence=row.confidence,
            # The queue row's real state, not a constant. A merge ECM could not
            # apply is still queued.
            status="merged" if dispatcharr_updated else "pending",
            dispatcharr_updated=dispatcharr_updated,
            unapplied_reason=unapplied_reason,
            journal_rows_unwritten=flush_outcome_rows(),
        )
    except BaseException:
        # Every way out that is not the return above: the 500s raised in the
        # clauses inside, a cancellation from a client disconnect or
        # application shutdown, a `SystemExit`. Nothing to record and no
        # envelope to return; this clause exists only to tell the `finally`
        # that something is already on its way out, so the flush there cannot
        # take its place.
        unwinding = True
        raise
    finally:
        # Every exit that is NOT the return above: a 500, a cancellation from
        # application shutdown, a `SystemExit`. `asyncio.CancelledError`
        # inherits from `BaseException`, so none of the `except Exception`
        # clauses saw it. Whatever landed upstream, landed. `flush_outcome_rows`
        # has already emptied the queue on the success path, so this writes only
        # what that path never reached, and `write_journal_rows` logs every row
        # it has not resolved before letting a `BaseException` past.
        flush_journal_rows_on_exit(
            flush_outcome_rows,
            unwinding=unwinding,
            context=f"pending_merges.id={row.id}",
            log_tag="DEDUP",
        )


# ---------------------------------------------------------------------------
# BD-E: POST /api/channel-merges/{id}/dismiss — operator rejects the candidate
# ---------------------------------------------------------------------------
@router.post("/{merge_id}/dismiss", response_model=DismissOutcome)
async def dismiss_pending_merge(
    merge_id: int,
    request: Request,
    db: Session = Depends(get_session),
    user=RequireAdminIfEnabled,
) -> DismissOutcome:
    """Dismiss a pending merge: state-flip + audit, no Dispatcharr call.

    Admin-gated — dismissal does not touch Dispatcharr but it does
    materially close out an operator decision in the audit substrate
    that downstream automation (BD-O MCP, future retention reaper)
    keys off. Aligned with /accept on the same auth posture so the
    pair has uniform access semantics.

    Flow (ADR-008 §D3 / §D6):

      1. Load the pending_merges row. 404 if missing.
      2. Idempotent terminal-state check:
         * already 'dismissed' → return the prior outcome envelope (200).
         * already 'merged'    → 409 (invalid cross-transition).
      3. Flip pending_merges row to 'dismissed' + resolved_at + resolution_source.
      4. Write a ``pending_merge_journal`` row with action='merge_dismissed'.
      5. Commit; emit ``ecm_dedup_merge_requests_total{status=dismissed}``.
      6. Return ``{journal_entry_id, status='dismissed'}``.

    No Dispatcharr call — dismissal is a pure ECM-side decision; the
    candidate channel is left untouched. This matches the §D7 MCP tool
    semantic where ``dismiss_channel_merge`` succeeds even when the
    target channel is gone in Dispatcharr (which is the §D4 recovery
    path for a stale-candidate /accept).
    """
    row = db.query(PendingMerge).filter(PendingMerge.id == merge_id).first()
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"pending merge id={merge_id} not found",
        )

    # ----- Idempotency: already in a terminal state ------------------------
    if row.status == "dismissed":
        journal_id = _latest_journal_entry_id(db, row.id)
        logger.info(
            "[DEDUP] dismiss idempotent: pending_merges id=%s already "
            "dismissed (journal_entry_id=%s); returning prior outcome",
            row.id, journal_id,
        )
        return DismissOutcome(journal_entry_id=journal_id)

    if row.status == "merged":
        # Cross-state transition — 409 per §D3 invariant. Counts as
        # 'dismissed' on the metric (4xx-by-design, not an SLI-10c
        # error, but still a terminal-state-related interaction).
        _bump_metric("dismissed")
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"pending merge id={merge_id} is already merged; "
                "cannot dismiss a row that was accepted"
            ),
        )

    # ----- DB state transition + audit row ---------------------------------
    now_ms = _now_epoch_ms()
    row.status = "dismissed"
    row.resolved_at = now_ms
    row.resolution_source = "operator"

    try:
        entry = _write_journal(
            db=db,
            pending_merge_id=row.id,
            actor_token_id=_actor_token_id(user),
            action_type="merge_dismissed",
            source_channel_id=row.stream_name,
            target_channel_id=row.candidate_channel_id,
            confidence_score=row.confidence,
            trigger_context=row.trigger_context,
        )
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        _bump_metric("error")
        logger.exception(
            "[DEDUP] dismiss failed during journal+commit "
            "(pending_merges.id=%s): %s", row.id, e,
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error persisting dismissal outcome",
        )

    _bump_metric("dismissed")
    # Update the companion queue-depth gauge (bd-wvr1d). Best-effort:
    # a failed COUNT or gauge.set is logged at WARN inside the helper and
    # never blocks the dismiss response — the DB commit is the source of truth.
    try:
        from observability import set_pending_merges_queue_depth_gauge
        set_pending_merges_queue_depth_gauge(db)
    except Exception:  # pragma: no cover — defensive import guard
        logger.warning("[DEDUP] gauge update failed after dismiss commit")
    logger.info(
        "[DEDUP] dismiss ok: pending_merges.id=%s "
        "journal_entry_id=%s actor=%s",
        row.id, entry.id, _actor_token_id(user),
    )
    return DismissOutcome(journal_entry_id=int(entry.id))


# ---------------------------------------------------------------------------
# BD-E: Dispatcharr-side merge helpers
# ---------------------------------------------------------------------------
async def _resolve_streams_by_name(client, stream_name: str) -> list[dict]:
    """Return Dispatcharr streams whose exact name matches ``stream_name``.

    Uses ``client.get_streams(search=...)`` which is a substring/full-
    text search server-side; we filter the result down to exact-name
    matches so a stream named "ESPN HD" does not get conflated with
    "ESPN HD West". The matcher service (BD-A) handles fuzzy matching
    at queue time — by the time a pending_merges row exists, the
    operator has already accepted that ``stream_name`` is the source.

    Pagination posture (post-BD-E review B2): ``page_size`` is
    ``STREAM_LOOKUP_PAGE_SIZE`` (500) so a common-prefix substring
    search (e.g. "ESPN" → "ESPN HD" / "ESPN HD West" / "ESPN 2 HD" /
    ...) is unlikely to overflow a single page and silently push the
    exact match onto an untested page 2+. If the response hits the
    ceiling, a WARN is logged so the operator can see the ambiguity in
    trace — the exact match may still be present in the returned set,
    but it may also be on a later page; downstream audit-first
    semantics still record the operator decision.

    Returns a :class:`StreamLookup`, not a bare list. The three ways this can
    come back empty are NOT the same fact and the caller has to tell them apart
    (bead ``enhancedchannelmanager-i5ic0``): nothing matched, the search was
    truncated at the page ceiling so a match may exist on a page nobody asked
    for, or the lookup itself failed. Collapsing all three into ``[]`` is what
    let an outage and a truncated page be reported to the operator as "no
    streams matched that name" — a claim about their data that the search never
    established.
    """
    try:
        response = await client.get_streams(
            search=stream_name,
            page=1,
            page_size=STREAM_LOOKUP_PAGE_SIZE,
        )
    except Exception:  # noqa: BLE001 — caller decides what to do with empty results
        logger.warning(
            "[DEDUP] stream-name resolution failed for name=%r; "
            "no evidence either way about whether the stream exists", stream_name,
        )
        return StreamLookup(matches=[], truncated=False, failed=True)

    results = response.get("results", []) if isinstance(response, dict) else []

    # Pagination ceiling check (post-review B2). If results length equals
    # the configured page_size, Dispatcharr may have more rows for this
    # substring search — emit a WARN so operators can see ambiguity in
    # trace. The exact-name filter below still selects the intended
    # stream if it is in this page, but operators should know when the
    # response was truncated.
    truncated = len(results) >= STREAM_LOOKUP_PAGE_SIZE
    if truncated:
        logger.warning(
            "[DEDUP] Stream-name lookup hit page_size ceiling (%d) for "
            "stream=%r; exact match may be in untested pages",
            STREAM_LOOKUP_PAGE_SIZE, stream_name,
        )

    # Exact-name filter — case-insensitive to match operator expectation.
    needle = stream_name.lower()
    return StreamLookup(
        matches=[s for s in results if str(s.get("name", "")).lower() == needle],
        truncated=truncated,
        failed=False,
    )


async def _add_stream_to_channel(client, channel: dict, stream_id: int) -> bool:
    """Add ``stream_id`` to ``channel``'s stream list via Dispatcharr.

    Mirrors the proven pattern in ``backend/routers/channels.py``
    (``add_stream_to_channel``) and ``backend/channel_pipeline_executor.py``
    (``_add_stream_to_channel``). No-op if the stream is already
    present — Dispatcharr would silently dedup the list, but skipping
    the PATCH saves an HTTP round-trip.

    Returns whether a PATCH was actually sent. The caller needs the difference
    for the JOURNAL, not for the outcome: either way the channel ends holding
    the stream, so the merge IS applied, but only one of the two is a mutation
    worth a row (bead ``enhancedchannelmanager-i5ic0``).
    """
    current_streams = channel.get("streams", [])
    # The streams collection in Dispatcharr's channel payload can be
    # either a list of ids or a list of {id, ...} dicts depending on
    # the serializer in play. Normalize before the membership check.
    normalized = [s["id"] if isinstance(s, dict) else s for s in current_streams]
    if stream_id in normalized:
        logger.debug(
            "[DEDUP] stream %s already present in channel %s — skipping PATCH",
            stream_id, channel.get("id"),
        )
        return False
    new_streams = list(normalized) + [stream_id]
    await client.update_channel(channel["id"], {"streams": new_streams})
    logger.info(
        "[DEDUP] added stream %s to channel %s as part of pending-merge accept",
        stream_id, channel.get("id"),
    )
    return True
