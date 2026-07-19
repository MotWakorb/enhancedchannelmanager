"""
Event sync operator-exclusion persistence (bead enhancedchannelmanager-ti939.3.5).

The DB half of the "never attach this provider stream to that event"
feature: loading a rule's exclusion fingerprints for the ONE shared
resolver (``services.event_sync_resolver.resolve_event_sync`` — preview
and attach receive the same set, so exclusion application cannot diverge
between them). CRUD lives in ``routers/event_sync_exclusions.py``; the
model is ``models.EventSyncExclusion``; fingerprint semantics live in
``services/event_sync_review.py``.

**Keying (HARD constraint, epic ti939.3):** exclusions key on the content
fingerprint ``(rule_id, provider_id, stream_name_hash, event_key)`` and
NEVER on stream/channel IDs — survival across provider refreshes and
stream-ID churn is by construction, not by bookkeeping.

Sessions are caller-owned (read-only module — mirrors the read half of
``services.event_sync_review_store``).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from models import EventSyncExclusion
from services.event_sync_review import PairingKey

__all__ = ["load_exclusion_keys"]


def load_exclusion_keys(db: Session, rule_id: int) -> frozenset[PairingKey]:
    """The rule's exclusion fingerprints, in-memory keyed like decisions.

    Loaded once per run/preview alongside ``load_review_decisions`` and
    threaded into ``resolve_event_sync``. rule_id scoping happens here
    (same convention as ReviewDecisions: the in-memory key carries the
    other three components).
    """
    rows = (
        db.query(
            EventSyncExclusion.provider_id,
            EventSyncExclusion.stream_name_hash,
            EventSyncExclusion.event_key,
        )
        .filter(EventSyncExclusion.rule_id == rule_id)
        .all()
    )
    return frozenset((p, h, k) for p, h, k in rows)
