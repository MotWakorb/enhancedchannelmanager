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
from typing import List, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import RequireAdminIfEnabled, RequireAuthIfEnabled
from config import get_settings
from database import get_session
from dispatcharr_client import get_client
from models import PendingMerge, PendingMergeJournal
from observability import get_metric
from services.dedup_matcher import find_candidate, MatchResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channel-merges", tags=["Channel Merges"])


# ---------------------------------------------------------------------------
# BD-E pagination defaults (ADR-008 §D1: "page, page_size with sane defaults
# — 1/50").
# ---------------------------------------------------------------------------
DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200  # bound the worst-case response size — mirrors other ECM list endpoints

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

    Field set matches ADR-008 §D1's response contract:
    ``{id, stream_name, group_id, candidate_channel_id, confidence,
       status, created_at, resolved_at?, resolution_source?,
       trigger_context}``.
    """

    id: int
    stream_name: str
    group_id: Optional[int] = None
    candidate_channel_id: str
    confidence: float
    status: str
    created_at: int
    resolved_at: Optional[int] = None
    resolution_source: Optional[str] = None
    trigger_context: str


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
    """

    merged_into_channel_id: str
    journal_entry_id: int
    source_stream_id: str
    confidence: float
    status: Literal["merged"] = "merged"


class DismissOutcome(BaseModel):
    """Flat-outcome response for POST /api/channel-merges/{id}/dismiss.

    ADR-008 §D1: "Response: {journal_entry_id, status: 'dismissed'} flat".
    """

    journal_entry_id: int
    status: Literal["dismissed"] = "dismissed"


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


def _bump_metric(status: str) -> None:
    """Increment ``ecm_dedup_merge_requests_total{status=...}``.

    Defensive: a metric-emit failure must NEVER break the merge
    endpoint — the merge is the load-bearing write path of the dedup
    epic (SLO-10c) and an observability failure cannot become a
    business failure. A failed emit logs at DEBUG and continues.

    The ``status`` argument is the BD-M contract label
    (``success`` | ``error`` | ``dismissed``); ``cancelled`` is
    reserved for the modal surface and never emitted from this router.
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

    return PendingMergesListResponse(
        merges=[PendingMergeRecord(**_record_to_dict(r)) for r in rows],
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
    try:
        matched_streams = await _resolve_streams_by_name(client, row.stream_name)
        if len(matched_streams) == 1:
            stream = matched_streams[0]
            stream_id = stream.get("id")
            if stream_id is not None:
                source_stream_identifier = str(stream_id)
                await _add_stream_to_channel(
                    client=client,
                    channel=channel,
                    stream_id=stream_id,
                )
        elif len(matched_streams) == 0:
            logger.warning(
                "[DEDUP] accept: no streams in Dispatcharr matched name=%r "
                "(pending_merges.id=%s) — recording operator decision in "
                "audit trail without a Dispatcharr-side update",
                row.stream_name, row.id,
            )
        else:
            logger.warning(
                "[DEDUP] accept: %d streams matched name=%r "
                "(pending_merges.id=%s) — ambiguous; recording operator "
                "decision in audit trail without a Dispatcharr-side update",
                len(matched_streams), row.stream_name, row.id,
            )
    except Exception as e:  # noqa: BLE001 — any Dispatcharr failure is SLI-10c
        _bump_metric("error")
        logger.exception(
            "[DEDUP] accept failed during Dispatcharr merge "
            "(pending_merges.id=%s): %s", row.id, e,
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Dispatcharr API error during merge",
        )

    # ----- DB state transition + audit row ---------------------------------
    # Both writes happen in one commit so a crash between them cannot
    # leave the queue in a half-resolved state.
    now_ms = _now_epoch_ms()
    row.status = "merged"
    row.resolved_at = now_ms
    row.resolution_source = "operator"

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
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        _bump_metric("error")
        logger.exception(
            "[DEDUP] accept failed during journal+commit "
            "(pending_merges.id=%s): %s", row.id, e,
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error persisting merge outcome",
        )

    _bump_metric("success")
    # Update the companion queue-depth gauge (bd-wvr1d). Best-effort:
    # a failed COUNT or gauge.set is logged at WARN inside the helper and
    # never blocks the accept response — the DB commit is the source of truth.
    try:
        from observability import set_pending_merges_queue_depth_gauge
        set_pending_merges_queue_depth_gauge(db)
    except Exception:  # pragma: no cover — defensive import guard
        logger.warning("[DEDUP] gauge update failed after accept commit")
    logger.info(
        "[DEDUP] accept ok: pending_merges.id=%s merged into "
        "candidate=%s journal_entry_id=%s actor=%s",
        row.id, row.candidate_channel_id, entry.id, _actor_token_id(user),
    )
    return AcceptOutcome(
        merged_into_channel_id=row.candidate_channel_id,
        journal_entry_id=int(entry.id),
        source_stream_id=source_stream_identifier,
        confidence=row.confidence,
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

    Returns ``[]`` when nothing matches or the API call fails — the
    caller's audit-first contract handles the empty case.
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
            "treating as no-match", stream_name,
        )
        return []

    results = response.get("results", []) if isinstance(response, dict) else []

    # Pagination ceiling check (post-review B2). If results length equals
    # the configured page_size, Dispatcharr may have more rows for this
    # substring search — emit a WARN so operators can see ambiguity in
    # trace. The exact-name filter below still selects the intended
    # stream if it is in this page, but operators should know when the
    # response was truncated.
    if len(results) >= STREAM_LOOKUP_PAGE_SIZE:
        logger.warning(
            "[DEDUP] Stream-name lookup hit page_size ceiling (%d) for "
            "stream=%r; exact match may be in untested pages",
            STREAM_LOOKUP_PAGE_SIZE, stream_name,
        )

    # Exact-name filter — case-insensitive to match operator expectation.
    needle = stream_name.lower()
    return [s for s in results if str(s.get("name", "")).lower() == needle]


async def _add_stream_to_channel(client, channel: dict, stream_id: int) -> None:
    """Add ``stream_id`` to ``channel``'s stream list via Dispatcharr.

    Mirrors the proven pattern in ``backend/routers/channels.py``
    (``add_stream_to_channel``) and ``backend/auto_creation_executor.py``
    (``_add_stream_to_channel``). No-op if the stream is already
    present — Dispatcharr would silently dedup the list, but skipping
    the PATCH saves an HTTP round-trip.
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
        return
    new_streams = list(normalized) + [stream_id]
    await client.update_channel(channel["id"], {"streams": new_streams})
    logger.info(
        "[DEDUP] added stream %s to channel %s as part of pending-merge accept",
        stream_id, channel.get("id"),
    )
