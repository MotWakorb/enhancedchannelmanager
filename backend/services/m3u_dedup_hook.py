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

    # Import inside the function so test code that imports the module
    # doesn't need PendingMerge available at import time (avoids a
    # circular-import risk if someone later wires the executor through
    # a shared module).
    from models import PendingMerge

    created_at_ms = int(time.time() * 1000)
    row = PendingMerge(
        stream_name=stream_name,
        group_id=group_id,
        candidate_channel_id=match.candidate_channel_id,
        confidence=match.confidence,
        status="pending",
        created_at=created_at_ms,
        trigger_context=M3U_REFRESH_TRIGGER_CONTEXT,
    )
    db_session.add(row)
    try:
        db_session.commit()
    except IntegrityError:
        # ADR-008 §D5 partial unique index collision: a pending row for
        # this (stream_name, candidate_channel_id) pair already exists.
        # This is the expected repeat-import path — log at INFO so the
        # operator can see in trace why a stream they expected to
        # re-prompt did not, then signal "skip channel creation"
        # because the prior pending row is still authoritative.
        db_session.rollback()
        logger.info(
            "[DEDUP] Pending merge for stream=%r candidate=%s already queued; "
            "skipping (partial-unique-index collision per ADR-008 §D5)",
            stream_name, match.candidate_channel_id,
        )
        return DedupHookResult(enqueued=True, candidate=None)

    # Fresh insert — emit the LOCKED-CONTRACT counter per BD-M.
    try:
        from observability import get_metric
        get_metric("pending_merges_queue_depth_added_total").inc()
    except Exception:  # pragma: no cover
        # Observability must not break the import path. A failed
        # metric emission is logged at DEBUG and the enqueue still
        # succeeded — the pending row is the source of truth.
        logger.debug(
            "[DEDUP] metric increment failed for "
            "ecm_pending_merges_queue_depth_added_total",
            exc_info=True,
        )

    # Update the companion gauge (bd-wvr1d). Best-effort: a failed COUNT
    # or gauge.set is logged at WARN inside the helper and never blocks
    # the enqueue path — the pending row is the source of truth.
    try:
        from observability import set_pending_merges_queue_depth_gauge
        set_pending_merges_queue_depth_gauge(db_session)
    except Exception:  # pragma: no cover — defensive import guard
        logger.warning(
            "[DEDUP] gauge update failed after pending_merges insert",
            exc_info=True,
        )

    logger.info(
        "[DEDUP] Enqueued pending merge: stream=%r group_id=%s "
        "candidate_channel_id=%s confidence=%.2f trigger=%s",
        stream_name, group_id, match.candidate_channel_id,
        match.confidence, M3U_REFRESH_TRIGGER_CONTEXT,
    )
    return DedupHookResult(enqueued=True, candidate=match)
