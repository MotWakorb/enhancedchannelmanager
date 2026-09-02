"""
Event sync review-queue persistence (bead enhancedchannelmanager-ti939.3.2).

The DB half of the review queue: loading prior decisions for a rule
(consumed by ``services.event_sync_resolver.resolve_event_sync`` — the ONE
decision path preview and attach share) and enqueueing new pending
questions from a live run. The pure identity layer — fingerprint
normalization, decision semantics — lives in
``services.event_sync_review``; the model is ``models.EventSyncReview``.

Sessions are caller-owned for reads; :func:`enqueue_review_candidates`
owns its transaction (commits) because it is the single write entry point
from the engine's attach phase.
"""
from __future__ import annotations

import json
import logging
import time

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import EventSyncReview
from services.event_sync_review import (
    REVIEW_STATUS_ACCEPTED,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_REJECTED,
    PairingKey,
    ReviewDecisions,
)

logger = logging.getLogger(__name__)

__all__ = [
    "enqueue_review_candidates",
    "load_pending_fingerprints",
    "load_review_decisions",
    "now_epoch_ms",
    "purge_stale_pending_reviews",
]

EVENT_SYNC_REVIEW_RETENTION_MAX_DAYS = 3650
EVENT_SYNC_REVIEW_PURGE_BATCH_SIZE = 200


def now_epoch_ms() -> int:
    """Current UTC time as epoch-ms (ADR-007 / pending_merges convention).

    Centralized so tests can monkeypatch one function for deterministic
    timestamps (mirrors ``routers/channel_merges._now_epoch_ms``).
    """
    return int(time.time() * 1000)


def validate_review_retention_days(value: object) -> int:
    """Validate the persisted integer-days contract; zero disables purge."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            "event_sync_review_retention_days must be an integer from 0 to "
            f"{EVENT_SYNC_REVIEW_RETENTION_MAX_DAYS} (0 disables retention)"
        )
    if value < 0 or value > EVENT_SYNC_REVIEW_RETENTION_MAX_DAYS:
        raise ValueError(
            "event_sync_review_retention_days must be between 0 and "
            f"{EVENT_SYNC_REVIEW_RETENTION_MAX_DAYS} (0 disables retention)"
        )
    return value


def purge_stale_pending_reviews(
    db: Session,
    *,
    retention_days: int,
    batch_limit: int = EVENT_SYNC_REVIEW_PURGE_BATCH_SIZE,
    now_ms: int | None = None,
) -> dict:
    """Delete one deterministic batch of pending reviews older than cutoff.

    Terminal decisions are deliberately retained because deleting an accepted
    or rejected fingerprint would change future matching behavior. The
    ``last_seen_at`` clock keeps questions that active runs still encounter.
    """
    days = validate_review_retention_days(retention_days)
    if batch_limit < 1 or batch_limit > EVENT_SYNC_REVIEW_PURGE_BATCH_SIZE:
        raise ValueError(
            f"batch_limit must be between 1 and {EVENT_SYNC_REVIEW_PURGE_BATCH_SIZE}"
        )
    if days == 0:
        return {
            "enabled": False,
            "cutoff_ms": None,
            "deleted": 0,
            "batch_limit": batch_limit,
        }

    cutoff_ms = (now_epoch_ms() if now_ms is None else now_ms) - days * 86_400_000
    candidate_ids = [
        row.id
        for row in (
            db.query(EventSyncReview.id)
            .filter(
                EventSyncReview.status == REVIEW_STATUS_PENDING,
                EventSyncReview.last_seen_at < cutoff_ms,
            )
            .order_by(EventSyncReview.last_seen_at.asc(), EventSyncReview.id.asc())
            .limit(batch_limit)
            .all()
        )
    ]
    if not candidate_ids:
        return {
            "enabled": True,
            "cutoff_ms": cutoff_ms,
            "deleted": 0,
            "deleted_ids": [],
            "batch_limit": batch_limit,
        }

    try:
        deleted = (
            db.query(EventSyncReview)
            .filter(
                EventSyncReview.id.in_(candidate_ids),
                EventSyncReview.status == REVIEW_STATUS_PENDING,
                EventSyncReview.last_seen_at < cutoff_ms,
            )
            .delete(synchronize_session=False)
        )
        if deleted != len(candidate_ids):
            raise RuntimeError(
                "Event Sync review purge target changed during the transaction; "
                "no selected rows were committed"
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "enabled": True,
        "cutoff_ms": cutoff_ms,
        "deleted": deleted,
        "deleted_ids": candidate_ids,
        "batch_limit": batch_limit,
    }


def load_review_decisions(db: Session, rule_id: int) -> ReviewDecisions:
    """Prior accepts/rejects for one rule, fingerprint-keyed.

    Loaded once per run/preview and threaded into ``resolve_event_sync``.
    ``superseded`` rows are deliberately NOT decisions — they were answered
    mechanically (a sibling pairing was accepted), so they neither attach
    nor suppress; their dedup role is enforced by the unique index at
    enqueue time instead.
    """
    accepted: set[PairingKey] = set()
    rejected: set[PairingKey] = set()
    rows = (
        db.query(
            EventSyncReview.provider_id,
            EventSyncReview.stream_name_hash,
            EventSyncReview.event_key,
            EventSyncReview.status,
        )
        .filter(
            EventSyncReview.rule_id == rule_id,
            EventSyncReview.status.in_(
                (REVIEW_STATUS_ACCEPTED, REVIEW_STATUS_REJECTED)
            ),
        )
        .all()
    )
    for provider_id, stream_hash, event_key, status in rows:
        key: PairingKey = (provider_id, stream_hash, event_key)
        if status == REVIEW_STATUS_ACCEPTED:
            accepted.add(key)
        else:
            rejected.add(key)
    return ReviewDecisions(
        accepted=frozenset(accepted), rejected=frozenset(rejected)
    )


def load_pending_fingerprints(db: Session, rule_id: int) -> frozenset[PairingKey]:
    """Fingerprints of the rule's OPEN questions (preview markers)."""
    rows = (
        db.query(
            EventSyncReview.provider_id,
            EventSyncReview.stream_name_hash,
            EventSyncReview.event_key,
        )
        .filter(
            EventSyncReview.rule_id == rule_id,
            EventSyncReview.status == REVIEW_STATUS_PENDING,
        )
        .all()
    )
    return frozenset((p, h, k) for p, h, k in rows)


def enqueue_review_candidates(
    db: Session, rule_id: int, payloads: list[dict]
) -> dict:
    """Persist one live run's ambiguous pairings as pending review rows.

    Each payload carries the fingerprint components (``provider_id``,
    ``stream_name_hash``, ``event_key``) plus a display-only ``evidence``
    dict. Dedup semantics (the epic's "the queue must NOT refill with
    already-answered questions"):

    * no row for the fingerprint → INSERT a ``pending`` row;
    * ``pending`` row exists → refresh ``last_seen_at`` + ``evidence``
      (the snapshot ids inside evidence go stale on every provider
      refresh; refreshing keeps the accept endpoint's fast path useful);
    * terminal row exists (``accepted``/``rejected``/``superseded``) →
      skip. Accepted/rejected pairings normally never reach this function
      (the resolver consumed them), but a superseded pairing can resurface
      when its accepted sibling's master vanishes — it stays answered.

    Returns ``{"enqueued": n, "refreshed": n, "already_answered": n}``.
    Commits on success (single write entry point — see module docstring).
    """
    counts = {"enqueued": 0, "refreshed": 0, "already_answered": 0}
    if not payloads:
        return counts
    now_ms = now_epoch_ms()

    for payload in payloads:
        row = (
            db.query(EventSyncReview)
            .filter(
                EventSyncReview.rule_id == rule_id,
                EventSyncReview.provider_id == payload["provider_id"],
                EventSyncReview.stream_name_hash == payload["stream_name_hash"],
                EventSyncReview.event_key == payload["event_key"],
            )
            .first()
        )
        if row is None:
            db.add(EventSyncReview(
                rule_id=rule_id,
                provider_id=payload["provider_id"],
                stream_name_hash=payload["stream_name_hash"],
                event_key=payload["event_key"],
                status=REVIEW_STATUS_PENDING,
                created_at=now_ms,
                last_seen_at=now_ms,
                evidence=json.dumps(payload.get("evidence") or {}),
            ))
            counts["enqueued"] += 1
        elif row.status == REVIEW_STATUS_PENDING:
            row.last_seen_at = now_ms
            row.evidence = json.dumps(payload.get("evidence") or {})
            counts["refreshed"] += 1
        else:
            counts["already_answered"] += 1

    try:
        db.commit()
    except IntegrityError as e:
        # Most likely the unique-fingerprint race (two overlapping runs) —
        # ECM is effectively single-writer, so belt-and-braces: the other
        # writer's row IS the row we wanted. Could also be an FK failure
        # (rule deleted mid-run); either way the run itself proceeds and
        # the exception detail is logged so the two cases stay
        # distinguishable.
        db.rollback()
        logger.warning(
            "[EVENT-SYNC] review enqueue commit failed for rule %s — "
            "counts for this run are approximate: %s", rule_id, e,
        )
        counts = {"enqueued": 0, "refreshed": 0, "already_answered": 0}
    return counts
