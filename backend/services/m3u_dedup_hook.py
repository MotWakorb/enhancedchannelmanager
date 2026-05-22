"""
Bulk M3U import dedup hook — populates the ``pending_merges`` queue
when the auto-creation pipeline (triggered by M3U refresh) is about
to create a new channel that fuzzy-matches an existing channel in
the same target group.

Encodes ADR-008 §D1 (bulk M3U is one of the four interactive trigger
surfaces), §D5 (partial unique index on ``(stream_name,
candidate_channel_id) WHERE status='pending'``), and §D9 (rows in
``pending_merges`` ARE the queue — no broker, write directly).

The hook fires AFTER the auto-creation executor's existing
exact/normalized lookups (``_find_channel_by_name``) have failed
and BEFORE the new-channel ``create_channel`` API call. When a
fuzzy candidate is found above the operator-configured threshold
(clamped to the §D2 confidence floor by the matcher itself), the
hook:

* INSERTs a ``pending_merges`` row with ``status='pending'`` and
  ``trigger_context='m3u_refresh'``.
* Increments the ``ecm_pending_merges_queue_depth_added_total``
  counter (LOCKED CONTRACT per BD-M).
* Signals the caller to SKIP the channel-creation step. The pending
  row encodes the deferred operator decision.

When the matcher returns no candidate, the hook is a no-op and the
auto-creation pipeline proceeds with normal channel creation.

When the same ``(stream_name, candidate_channel_id)`` pair is
re-enqueued (e.g. an M3U refresh repeats a stream the operator has
not yet resolved), the §D5 partial unique index raises
``IntegrityError`` at INSERT time. The hook catches this, logs at
INFO, and returns the same "skip channel creation" signal — the
prior pending row is still authoritative.

Dry-run handling: the hook is bypassed when ``dry_run=True``. A
preview must not mutate the queue. The auto-creation executor's
own dry-run branch handles the simulated-channel bookkeeping; the
hook simply returns ``DedupHookResult(enqueued=False)`` so the
preview shows the would-create action verbatim.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.exc import IntegrityError

from services.dedup_matcher import MatchResult, find_candidate

logger = logging.getLogger(__name__)

# trigger_context value written to pending_merges.trigger_context and
# pending_merge_journal.trigger_context for the bulk-M3U-import surface.
# ADR-008 §D6 enum: ('drag_drop','add_stream','m3u_refresh','mcp_tool').
M3U_REFRESH_TRIGGER_CONTEXT: str = "m3u_refresh"

# Value of ``triggered_by`` (engine-side) that activates this hook. Other
# trigger_by values (``manual``, ``scheduled``) do NOT enqueue pending
# merges — those paths run outside the four ADR-008 §D1 interactive
# surfaces and predate the dedup epic.
M3U_REFRESH_TRIGGERED_BY: str = "m3u_refresh"

# §D6 ``actor_token_id`` for the bulk-M3U enqueue. The hook runs from the
# auto-creation engine with no acting token (it is a background import
# surface, not an authenticated request). The literal ``"system"`` makes
# the audit trail explicit — "this row was queued by the import pipeline,
# not by an operator or an MCP agent" — and satisfies the NOT NULL audit
# invariant. The MCP path resolves a real token id; this is the only
# surface with no credential to attribute.
M3U_REFRESH_ACTOR_TOKEN_ID: str = "system"


@dataclass(frozen=True)
class DedupHookResult:
    """Outcome of one dedup hook invocation.

    Attributes
    ----------
    enqueued:
        True when a ``pending_merges`` row exists for this
        ``(stream_name, candidate_channel_id)`` pair after the call —
        either freshly inserted by this invocation, or already present
        from an earlier M3U refresh (partial unique-index collision
        caught and logged). The caller should skip the would-be
        ``create_channel`` step in both cases — the pending row is the
        deferred decision.

        False when no candidate was found, when dedup was disabled at
        the call site (dry-run, non-m3u_refresh triggered_by), or when
        an unexpected error short-circuited the hook. The caller
        proceeds with normal channel creation.
    candidate:
        The ``MatchResult`` that drove the enqueue, when enqueued is
        True AND the insert was fresh (not an idempotent collision).
        ``None`` for the collision branch (we did not re-score the
        existing pending row) and for the no-enqueue branches.
    """

    enqueued: bool
    candidate: Optional[MatchResult] = None


@dataclass(frozen=True)
class EnqueueResult:
    """Outcome of the shared :func:`enqueue_pending_merge` core.

    Returned by the trigger-context-agnostic enqueue primitive that backs
    BOTH the bulk-M3U hook (``trigger_context='m3u_refresh'``) and the
    MCP ``add_stream`` prompt path (``trigger_context='mcp_tool'``).

    Attributes
    ----------
    merge_id:
        The ``pending_merges.id`` of the row that is authoritative for
        this ``(stream_name, candidate_channel_id)`` pair after the call.
        On a fresh insert this is the newly-created row; on a §D5
        partial-unique collision this is the *existing* pending row's id
        (idempotent contract — same id every time the same pair is
        re-enqueued without resolution).
    fresh:
        True when this call inserted a new row (and therefore wrote the
        ``auto_queued`` journal row + bumped the BD-M metric). False when
        the row already existed (§D5 collision) — no new journal row, no
        metric bump, the prior row is the source of truth.
    candidate:
        The ``MatchResult`` that drove the enqueue. Always populated
        (the caller passes the already-scored match in), so MCP / hook
        callers can echo the candidate + confidence back to the agent
        without a re-score.
    """

    merge_id: int
    fresh: bool
    candidate: MatchResult


def enqueue_pending_merge(
    *,
    stream_name: str,
    group_id: Optional[int],
    match: MatchResult,
    trigger_context: str,
    actor_token_id: str,
    db_session,
) -> EnqueueResult:
    """Insert a ``pending_merges`` row + ``auto_queued`` journal row.

    This is the shared, trigger-context-agnostic enqueue primitive.
    BOTH the bulk-M3U hook (:func:`check_and_enqueue_pending_merge`,
    ``trigger_context='m3u_refresh'``) and the MCP prompt path
    (``POST /api/channel-merges``, ``trigger_context='mcp_tool'``) call
    it. It is NOT gated to ``m3u_refresh`` — the gate lives in the M3U
    hook wrapper, not here.

    Encodes the load-bearing ADR-008 invariants in one place so the two
    surfaces cannot drift:

    * §D5 idempotency — the partial unique index
      ``(stream_name, candidate_channel_id) WHERE status='pending'``
      raises ``IntegrityError`` on a duplicate pending row. This is
      caught, the existing row is looked up, and its ``merge_id`` is
      returned (``fresh=False``). No second journal row, no metric bump.
    * §D6 audit — EVERY fresh enqueue writes a ``pending_merge_journal``
      row with ``action_type='auto_queued'`` and the full seven-field
      audit set. ``source_channel_id`` is the raw ``stream_name`` (the
      bulk-import / queue-time audit-first fallback per §D6 — the
      Dispatcharr stream id is not resolved at queue time). ``confidence_
      score`` is the score captured at action time (``match.confidence``).
    * BD-M metric — a fresh insert increments
      ``ecm_pending_merges_queue_depth_added_total`` (LOCKED CONTRACT)
      and refreshes the companion queue-depth gauge.

    The row INSERT + journal write land in a single ``commit`` so a
    crash between them cannot leave a queue row with no audit trail.

    Parameters
    ----------
    stream_name:
        Raw incoming stream name (stored verbatim; normalization is the
        matcher's compare-time concern).
    group_id:
        Dispatcharr group id, or ``None`` for the ungrouped scope
        (stored as NULL).
    match:
        The already-scored :class:`MatchResult`. The caller runs the
        matcher; this core trusts the candidate id + confidence verbatim
        (the caller is responsible for using the authoritative
        action-time threshold, per §D6).
    trigger_context:
        ``'m3u_refresh'`` | ``'add_stream'`` | ``'drag_drop'`` |
        ``'mcp_tool'`` — the surface that enqueued the row. Written to
        both ``pending_merges.trigger_context`` and the journal row.
    actor_token_id:
        The §D6 audit actor — the token's DB id as a string, or
        ``"anonymous"`` when auth is disabled. For the M3U hook this is
        a fixed system-actor sentinel; for the MCP path it is resolved
        from the bearer token.
    db_session:
        SQLAlchemy session for ECM's ``journal.db``. The core commits
        its own unit of work and rolls back on the §D5 collision so the
        caller can keep using the session.

    Returns
    -------
    EnqueueResult
        See the dataclass docstring.
    """
    from models import PendingMerge, PendingMergeJournal

    now_ms = int(time.time() * 1000)
    row = PendingMerge(
        stream_name=stream_name,
        group_id=group_id,
        candidate_channel_id=match.candidate_channel_id,
        confidence=match.confidence,
        status="pending",
        created_at=now_ms,
        trigger_context=trigger_context,
    )
    db_session.add(row)
    try:
        # Flush (not commit) so the row gets its autoincrement id, which
        # the NOT NULL FK on the journal row needs. A §D5 collision raises
        # here at flush time.
        db_session.flush()
    except IntegrityError:
        # ADR-008 §D5: a pending row for this (stream_name,
        # candidate_channel_id) pair already exists. Roll back, look up
        # the authoritative existing row, and return its id idempotently.
        db_session.rollback()
        existing = (
            db_session.query(PendingMerge)
            .filter(
                PendingMerge.stream_name == stream_name,
                PendingMerge.candidate_channel_id == match.candidate_channel_id,
                PendingMerge.status == "pending",
            )
            .order_by(PendingMerge.created_at.asc(), PendingMerge.id.asc())
            .first()
        )
        if existing is None:  # pragma: no cover — defensive; the IntegrityError implies one exists
            raise
        logger.info(
            "[DEDUP] Pending merge for stream=%r candidate=%s already queued "
            "(merge_id=%s); idempotent no-op (partial-unique-index collision "
            "per ADR-008 §D5)",
            stream_name, match.candidate_channel_id, existing.id,
        )
        return EnqueueResult(merge_id=int(existing.id), fresh=False, candidate=match)

    # §D6: every queue action writes an audit row. action_type='auto_queued',
    # source_channel_id is the raw stream_name (queue-time audit-first
    # fallback — the Dispatcharr stream id is not resolved here).
    journal = PendingMergeJournal(
        pending_merge_id=row.id,
        actor_token_id=actor_token_id,
        action_type="auto_queued",
        source_channel_id=stream_name,
        target_channel_id=match.candidate_channel_id,
        confidence_score=match.confidence,
        timestamp_utc=now_ms,
        trigger_context=trigger_context,
    )
    db_session.add(journal)
    db_session.commit()

    # Fresh insert — emit the LOCKED-CONTRACT counter per BD-M.
    try:
        from observability import get_metric
        get_metric("pending_merges_queue_depth_added_total").inc()
    except Exception:  # pragma: no cover
        # Observability must not break the enqueue path. The pending row
        # is the source of truth; a failed metric emit is logged at DEBUG.
        logger.debug(
            "[DEDUP] metric increment failed for "
            "ecm_pending_merges_queue_depth_added_total",
            exc_info=True,
        )

    # Update the companion gauge (bd-wvr1d). Best-effort.
    try:
        from observability import set_pending_merges_queue_depth_gauge
        set_pending_merges_queue_depth_gauge(db_session)
    except Exception:  # pragma: no cover — defensive import guard
        logger.warning(
            "[DEDUP] gauge update failed after pending_merges insert",
            exc_info=True,
        )

    logger.info(
        "[DEDUP] Enqueued pending merge: merge_id=%s stream=%r group_id=%s "
        "candidate_channel_id=%s confidence=%.2f trigger=%s",
        row.id, stream_name, group_id, match.candidate_channel_id,
        match.confidence, trigger_context,
    )
    return EnqueueResult(merge_id=int(row.id), fresh=True, candidate=match)


def check_and_enqueue_pending_merge(
    *,
    stream_name: str,
    group_id: Optional[int],
    candidates: list[tuple[str, str]],
    threshold: float,
    triggered_by: str,
    dry_run: bool,
    db_session,
) -> DedupHookResult:
    """Evaluate ``stream_name`` against ``candidates`` and enqueue a
    ``pending_merges`` row if a fuzzy match is found.

    Parameters
    ----------
    stream_name:
        Raw incoming stream name (the M3U-delivered name; normalization
        is the matcher's compare-time concern, not stored).
    group_id:
        Dispatcharr group id for the target channel scope. ``None``
        means ungrouped — stored as NULL in ``pending_merges.group_id``.
    candidates:
        ``(channel_id, channel_name)`` tuples for existing channels in
        the same target group. ``channel_id`` may be int or str; it is
        stringified before scoring (the matcher accepts str) and before
        insertion (``candidate_channel_id`` is TEXT per ADR-008 §D8).
        Caller is responsible for the group-scope filter — the hook
        does not re-fetch.
    threshold:
        Operator-configured confidence threshold (typically
        ``settings.dedup_threshold``). The matcher clamps to
        ``CONFIDENCE_FLOOR`` before scoring (ADR-008 §D2 load-bearing
        enforcement) — passing a below-floor value still gets floor
        behaviour.
    triggered_by:
        Engine-side ``triggered_by`` string. Only ``"m3u_refresh"``
        activates the hook; other values short-circuit with
        ``DedupHookResult(enqueued=False)`` so the auto-creation
        pipeline's scheduled / manual paths are untouched.
    dry_run:
        When True, the hook short-circuits without writing or scoring.
        A dry-run preview must not mutate the queue.
    db_session:
        SQLAlchemy session for the ECM ``journal.db``. The hook
        commits its own INSERT (single-row write) and rolls back on
        IntegrityError so the caller can keep using the session.

    Returns
    -------
    DedupHookResult
        See dataclass docstring. ``enqueued=True`` means "skip the
        new-channel creation, the pending row encodes the deferred
        operator decision."
    """
    if dry_run:
        logger.debug(
            "[DEDUP] Hook bypassed for stream=%r (dry_run=True)", stream_name
        )
        return DedupHookResult(enqueued=False)

    if triggered_by != M3U_REFRESH_TRIGGERED_BY:
        # Auto-creation runs from scheduled / manual / API paths predate
        # the dedup epic and stay on the legacy "always create" semantics.
        # Only M3U refresh enqueues pending merges per ADR-008 §D1.
        logger.debug(
            "[DEDUP] Hook bypassed for stream=%r (triggered_by=%r != %r)",
            stream_name, triggered_by, M3U_REFRESH_TRIGGERED_BY,
        )
        return DedupHookResult(enqueued=False)

    if not candidates:
        # Empty target group — nothing to match against. The auto-creation
        # pipeline proceeds with normal channel creation.
        return DedupHookResult(enqueued=False)

    # Stringify channel ids before passing to the matcher. The matcher
    # signature is list[tuple[str, str]]; Dispatcharr returns int ids
    # from get_channels() (ADR-008 §D8 stores them as TEXT regardless
    # of integer vs UUID upstream representation).
    str_candidates: list[tuple[str, str]] = [
        (str(cid), cname) for cid, cname in candidates
    ]

    match: Optional[MatchResult] = find_candidate(
        stream_name=stream_name,
        candidates=str_candidates,
        threshold=threshold,
    )
    if match is None:
        # No candidate above threshold — proceed with normal channel
        # creation. This is the silent-refusal branch per ADR-008 §D2
        # (below the floor) and the no-match branch (no candidate
        # scored above the clamped threshold).
        return DedupHookResult(enqueued=False)

    # Delegate the INSERT → §D5 idempotency → §D6 journal → BD-M metric to
    # the shared, trigger-context-agnostic core. The M3U surface fixes
    # trigger_context='m3u_refresh' and the system-actor sentinel; the gate
    # above (triggered_by / dry_run) is the M3U-specific policy that stays
    # in this wrapper.
    result = enqueue_pending_merge(
        stream_name=stream_name,
        group_id=group_id,
        match=match,
        trigger_context=M3U_REFRESH_TRIGGER_CONTEXT,
        actor_token_id=M3U_REFRESH_ACTOR_TOKEN_ID,
        db_session=db_session,
    )
    # Preserve the historical hook contract: candidate is None on the §D5
    # collision branch (the prior pending row is authoritative; we did not
    # re-score it), and the MatchResult on a fresh insert.
    return DedupHookResult(
        enqueued=True,
        candidate=result.candidate if result.fresh else None,
    )
