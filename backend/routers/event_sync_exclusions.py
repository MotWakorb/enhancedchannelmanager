"""
Event Sync operator-exclusions router (bead enhancedchannelmanager-ti939.3.5).

Owns the ``/api/event-sync-exclusions/*`` route family — the operator
surface for "never attach this provider stream to that event" standing
orders:

  GET    /api/event-sync-exclusions        — paginated exclusion list.
  POST   /api/event-sync-exclusions        — create (idempotent on the
                                             fingerprint: an existing row
                                             is returned, never duplicated).
  DELETE /api/event-sync-exclusions/{id}   — remove the standing order; the
                                             pairing becomes matchable again
                                             on the next run/preview.

Why this exists (the epic's predicted loop): stateless recompute means a
false-positive attach the operator manually detaches is re-attached on the
next run, forever. An exclusion row is the durable "never" — the shared
resolver (``services.event_sync_resolver``) filters the pairing out BEFORE
the attach band is honored, on every run and preview.

**Keying (HARD security constraint, epic ti939.3).** Rows key on content
fingerprints — ``(rule_id, provider_id, stream_name_hash, event_key)`` —
never channel/stream IDs (``services/event_sync_review.py`` defines the
semantics), so an exclusion survives provider refreshes and stream-ID
churn by construction. The optional ``evidence`` JSON is display-only
(raw names for the list surface), same role as review-row evidence.

**Precedence.** An exclusion outranks a prior review-queue ACCEPT for the
same fingerprint — the resolver removes excluded candidates before the
accept-upgrade step (pinned in ``tests/services/test_event_sync_resolver.py``).

Journaling: create/delete write ``journal_entries`` rows under category
``event_sync`` (action_type ``exclusion_create`` / ``exclusion_delete``) —
same no-second-journal-table posture as review decisions.

Auth posture mirrors event_sync_reviews: list is ``RequireAuthIfEnabled``
(read); create/delete are ``RequireAdminIfEnabled`` (they change what
future runs attach).
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi import status as http_status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import journal
from auth import RequireAdminIfEnabled, RequireAuthIfEnabled
from database import get_session
from models import ChannelPipelineRule, EventSyncExclusion
from services.event_sync_review_store import now_epoch_ms

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/event-sync-exclusions", tags=["Event Sync Exclusions"]
)

DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_NOTE_LENGTH = 2000


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class EventSyncExclusionCreate(BaseModel):
    """Create body — the four fingerprint components + display extras.

    The fingerprint components are supplied verbatim (the review-queue UI
    and the preview response both carry them); the server never derives
    identity from channel/stream IDs.
    """

    rule_id: int
    provider_id: int = Field(ge=0)
    stream_name_hash: str = Field(min_length=1, max_length=128)
    event_key: str = Field(min_length=1, max_length=1024)
    note: Optional[str] = Field(default=None, max_length=MAX_NOTE_LENGTH)
    # Display-only snapshot (raw names etc.) — never identity-authoritative.
    evidence: Optional[dict] = None


class EventSyncExclusionRecord(BaseModel):
    """One event_sync_exclusions row, evidence JSON parsed for display."""

    id: int
    rule_id: int
    provider_id: int
    stream_name_hash: str
    event_key: str
    created_at: int
    note: Optional[str] = None
    evidence: dict
    # True on POST responses when the fingerprint already existed and the
    # stored row was returned instead of a duplicate being created.
    already_existed: bool = False


class EventSyncExclusionsListResponse(BaseModel):
    """Paginated envelope (standard ECM list-endpoint shape)."""

    exclusions: List[EventSyncExclusionRecord]
    total: int
    page: int
    page_size: int
    total_pages: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record_to_dict(row: EventSyncExclusion) -> dict:
    """Explicit projection (adding a model column never widens the API)."""
    try:
        evidence = json.loads(row.evidence) if row.evidence else {}
        if not isinstance(evidence, dict):
            evidence = {}
    except (TypeError, ValueError):
        evidence = {}
    return {
        "id": row.id,
        "rule_id": row.rule_id,
        "provider_id": row.provider_id,
        "stream_name_hash": row.stream_name_hash,
        "event_key": row.event_key,
        "created_at": row.created_at,
        "note": row.note,
        "evidence": evidence,
    }


def _actor_token_id(user) -> str:
    """Opaque acting-user DB id ('anonymous' when auth is disabled) —
    mirrors ``routers.event_sync_reviews._actor_token_id``."""
    if user is None:
        return "anonymous"
    return str(user.id)


def _journal_exclusion(row: EventSyncExclusion, action_type: str,
                       actor: str) -> None:
    """Write the exclusion audit entry (category event_sync).

    Best-effort by journal.log_entry's own contract — the DB row is the
    source of truth.
    """
    evidence = _record_to_dict(row)["evidence"]
    verb = "created" if action_type == "exclusion_create" else "removed"
    payload = {
        "fingerprint": {
            "rule_id": row.rule_id,
            "provider_id": row.provider_id,
            "stream_name_hash": row.stream_name_hash,
            "event_key": row.event_key,
        },
        "note": row.note,
        "actor_token_id": actor,
    }
    journal.log_entry(
        category="event_sync",
        action_type=action_type,
        entity_id=row.id,
        entity_name=(
            evidence.get("stream_name") or f"exclusion {row.id}"
        ),
        description=(
            f"Never-attach exclusion {verb}: stream "
            f"'{evidence.get('stream_name', '?')}' ↔ master "
            f"'{evidence.get('master_channel_name', '?')}' "
            f"(rule '{evidence.get('rule_name', row.rule_id)}')"
        ),
        before_value=None if action_type == "exclusion_create" else payload,
        after_value=payload if action_type == "exclusion_create" else None,
        user_initiated=True,
    )


# ---------------------------------------------------------------------------
# GET /api/event-sync-exclusions — paginated list
# ---------------------------------------------------------------------------


@router.get("", response_model=EventSyncExclusionsListResponse)
async def list_event_sync_exclusions(
    rule_id: Optional[int] = None,
    page: int = DEFAULT_PAGE,
    page_size: int = DEFAULT_PAGE_SIZE,
    db: Session = Depends(get_session),
    _user=RequireAuthIfEnabled,
) -> EventSyncExclusionsListResponse:
    """List exclusions, newest first (optionally scoped to one rule)."""
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

    query = db.query(EventSyncExclusion)
    if rule_id is not None:
        query = query.filter(EventSyncExclusion.rule_id == rule_id)

    total = query.count()
    rows = (
        query.order_by(
            EventSyncExclusion.created_at.desc(), EventSyncExclusion.id.desc()
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    total_pages = (total + page_size - 1) // page_size if total else 0
    return EventSyncExclusionsListResponse(
        exclusions=[
            EventSyncExclusionRecord(**_record_to_dict(r)) for r in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ---------------------------------------------------------------------------
# POST /api/event-sync-exclusions — create (idempotent on the fingerprint)
# ---------------------------------------------------------------------------


@router.post("", response_model=EventSyncExclusionRecord)
async def create_event_sync_exclusion(
    body: EventSyncExclusionCreate,
    db: Session = Depends(get_session),
    user=RequireAdminIfEnabled,
) -> EventSyncExclusionRecord:
    """Record a durable "never attach this pairing" standing order.

    Idempotent: a POST for an already-excluded fingerprint returns the
    existing row (``already_existed=true``), refreshing its note when one
    is supplied — never a duplicate (unique fingerprint index).
    """
    rule = db.query(ChannelPipelineRule).filter(
        ChannelPipelineRule.id == body.rule_id
    ).first()
    if rule is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"rule id={body.rule_id} not found",
        )

    existing = (
        db.query(EventSyncExclusion)
        .filter(
            EventSyncExclusion.rule_id == body.rule_id,
            EventSyncExclusion.provider_id == body.provider_id,
            EventSyncExclusion.stream_name_hash == body.stream_name_hash,
            EventSyncExclusion.event_key == body.event_key,
        )
        .first()
    )
    if existing is not None:
        if body.note is not None and body.note != existing.note:
            existing.note = body.note
            db.commit()
        logger.info(
            "[EVENT-SYNC] exclusion create idempotent: id=%s rule=%s",
            existing.id, existing.rule_id,
        )
        return EventSyncExclusionRecord(
            **_record_to_dict(existing), already_existed=True
        )

    actor = _actor_token_id(user)
    row = EventSyncExclusion(
        rule_id=body.rule_id,
        provider_id=body.provider_id,
        stream_name_hash=body.stream_name_hash,
        event_key=body.event_key,
        created_at=now_epoch_ms(),
        note=body.note,
        actor_token_id=actor,
        evidence=json.dumps(body.evidence or {}),
    )
    db.add(row)
    try:
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.exception(
            "[EVENT-SYNC] exclusion create failed to persist "
            "(rule=%s): %s", body.rule_id, e,
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error persisting exclusion",
        )
    db.refresh(row)

    _journal_exclusion(row, "exclusion_create", actor)
    logger.info(
        "[EVENT-SYNC] exclusion created: id=%s rule=%s provider=%s actor=%s",
        row.id, row.rule_id, row.provider_id, actor,
    )
    return EventSyncExclusionRecord(**_record_to_dict(row))


# ---------------------------------------------------------------------------
# DELETE /api/event-sync-exclusions/{id}
# ---------------------------------------------------------------------------


@router.delete("/{exclusion_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_event_sync_exclusion(
    exclusion_id: int,
    db: Session = Depends(get_session),
    user=RequireAdminIfEnabled,
) -> Response:
    """Remove a standing order — the pairing becomes matchable again on the
    next run/preview (nothing is re-attached here; the idempotent run is
    the applier, exactly as with review accepts)."""
    row = db.query(EventSyncExclusion).filter(
        EventSyncExclusion.id == exclusion_id
    ).first()
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"event sync exclusion id={exclusion_id} not found",
        )

    actor = _actor_token_id(user)
    # Journal BEFORE the delete so the entry can read the row's evidence.
    _journal_exclusion(row, "exclusion_delete", actor)
    db.delete(row)
    try:
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.exception(
            "[EVENT-SYNC] exclusion delete failed to persist (id=%s): %s",
            exclusion_id, e,
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error removing exclusion",
        )
    logger.info(
        "[EVENT-SYNC] exclusion removed: id=%s rule=%s actor=%s",
        exclusion_id, row.rule_id, actor,
    )
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
