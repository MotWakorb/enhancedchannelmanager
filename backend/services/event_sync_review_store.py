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
]


def now_epoch_ms() -> int:
    """Current UTC time as epoch-ms (ADR-007 / pending_merges convention).

    Centralized so tests can monkeypatch one function for deterministic
    timestamps (mirrors ``routers/channel_merges._now_epoch_ms``).
    """
    return int(time.time() * 1000)


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
