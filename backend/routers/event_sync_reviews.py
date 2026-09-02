"""
Event Sync review-queue router (bead enhancedchannelmanager-ti939.3.2).

Owns the ``/api/event-sync-reviews/*`` route family — the operator surface
for ambiguous-band event_sync matches that runs enqueue instead of
silently skipping (ADR-008 pending-merges review PATTERN, adapted for
event pairings):

  GET  /api/event-sync-reviews               — paginated queue/decision list.
  POST /api/event-sync-reviews/{id}/accept   — accept a pairing: attach the
                                               stream now (best-effort) AND
                                               record the fingerprint-keyed
                                               decision so every future run
                                               auto-attaches it.
  POST /api/event-sync-reviews/{id}/reject   — reject a pairing: record the
                                               decision so future runs
                                               suppress it without re-asking.

**Keying (HARD security constraint, epic ti939.3).** Rows key on content
fingerprints — ``(rule_id, provider_id, stream_name_hash, event_key)`` —
never channel/stream IDs (``services/event_sync_review.py`` defines the
semantics). The ``evidence`` JSON carries SNAPSHOT channel/stream ids for
the accept endpoint's immediate-attach fast path only, and both are
RE-VERIFIED against live Dispatcharr before use:

* the snapshot channel's CURRENT name must still parse to the row's
  ``event_key`` (the channel id may have been recycled onto a different
  event since enqueue);
* the snapshot stream's CURRENT name must still hash to the row's
  ``stream_name_hash``.

When either verification fails the accept still succeeds — the decision is
the durable artifact; the next (idempotent) run re-resolves by fingerprint
and attaches. This ordering is a deliberate inversion of
``channel_merges.accept_pending_merge`` (which effects Dispatcharr first):
that queue's decision cannot self-apply later, this one's can.

**Decision-vs-attach journaling.** Every accept/reject writes a
``journal_entries`` row (category ``event_sync``, action_type
``review_accept`` / ``review_reject``) recording the fingerprint decision.
An accept that ALSO attaches immediately writes the standard
``merge_stream`` entry with before/after stream ids — same shape the
executor writes, so the journal-driven surgical unmerge covers these
attaches too — with ``after_value.match.attach_source="review_queue"``
distinguishing it from threshold attaches (the bead's journal-distinction
acceptance criterion).

Auth posture mirrors channel_merges: list is ``RequireAuthIfEnabled``
(read); accept/reject are ``RequireAdminIfEnabled`` (writes that mutate
Dispatcharr channel structure / close out operator decisions).
"""

from __future__ import annotations

import json
import logging
from typing import List, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel, StrictInt, field_validator
from sqlalchemy.orm import Session

import journal
from auth import RequireAdminIfEnabled, RequireAuthIfEnabled
from database import get_session
from dispatcharr_client import get_client
from models import ChannelPipelineRule, EventSyncReview
from services.event_sync_review import (
    REVIEW_STATUS_ACCEPTED,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_REJECTED,
    REVIEW_STATUS_SUPERSEDED,
    master_event_key,
    stream_name_hash,
)
from services.event_sync_review_store import now_epoch_ms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/event-sync-reviews", tags=["Event Sync Reviews"])

DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

_VALID_STATUSES = (
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_ACCEPTED,
    REVIEW_STATUS_REJECTED,
    REVIEW_STATUS_SUPERSEDED,
)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class EventSyncReviewRecord(BaseModel):
    """One event_sync_reviews row, evidence JSON parsed for display."""

    id: int
    rule_id: int
    provider_id: int
    stream_name_hash: str
    event_key: str
    status: str
    created_at: int
    last_seen_at: int
    resolved_at: Optional[int] = None
    resolution_source: Optional[str] = None
    evidence: dict


class EventSyncReviewsListResponse(BaseModel):
    """Paginated envelope (standard ECM list-endpoint shape)."""

    reviews: List[EventSyncReviewRecord]
    total: int
    page: int
    page_size: int
    total_pages: int


class AcceptReviewOutcome(BaseModel):
    """Flat outcome for POST /{id}/accept.

    ``attached`` / ``already_attached`` describe the best-effort immediate
    attach; when both are False, ``attach_deferred_reason`` says why the
    attach waits for the next run (the DECISION is recorded regardless —
    that is the durable artifact).
    """

    status: Literal["accepted"] = "accepted"
    attached: bool = False
    already_attached: bool = False
    attach_deferred_reason: Optional[str] = None
    superseded_siblings: int = 0


class RejectReviewOutcome(BaseModel):
    """Flat outcome for POST /{id}/reject."""

    status: Literal["rejected"] = "rejected"


class BulkDiscardReviewsRequest(BaseModel):
    review_ids: List[StrictInt]

    @field_validator("review_ids")
    @classmethod
    def validate_review_ids(cls, value: List[int]) -> List[int]:
        if not value:
            raise ValueError("review_ids must contain at least one id")
        if len(value) > MAX_PAGE_SIZE:
            raise ValueError(f"review_ids may contain at most {MAX_PAGE_SIZE} ids")
        if any(review_id <= 0 for review_id in value):
            raise ValueError("review_ids must contain only positive integers")
        if len(set(value)) != len(value):
            raise ValueError("review_ids must not contain duplicates")
        return value


class BulkDiscardReviewsOutcome(BaseModel):
    requested_ids: List[int]
    discarded_ids: List[int]
    missing_ids: List[int]
    not_pending_ids: List[int]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record_to_dict(row: EventSyncReview) -> dict:
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
        "status": row.status,
        "created_at": row.created_at,
        "last_seen_at": row.last_seen_at,
        "resolved_at": row.resolved_at,
        "resolution_source": row.resolution_source,
        "evidence": evidence,
    }


def _actor_token_id(user) -> str:
    """Opaque acting-user DB id ('anonymous' when auth is disabled) —
    mirrors ``routers.channel_merges._actor_token_id`` (ADR-008 §D6)."""
    if user is None:
        return "anonymous"
    return str(user.id)


def _journal_decision(row: EventSyncReview, action_type: str, actor: str,
                      extra_after: dict | None = None) -> None:
    """Write the decision audit entry (category event_sync).

    Best-effort by journal.log_entry's own contract (it logs and returns
    None on failure) — the DB status flip is the source of truth.
    """
    evidence = {}
    try:
        evidence = json.loads(row.evidence) if row.evidence else {}
    except (TypeError, ValueError):
        pass
    after_value = {
        "status": row.status,
        "fingerprint": {
            "rule_id": row.rule_id,
            "provider_id": row.provider_id,
            "stream_name_hash": row.stream_name_hash,
            "event_key": row.event_key,
        },
        "actor_token_id": actor,
    }
    if extra_after:
        after_value.update(extra_after)
    journal.log_entry(
        category="event_sync",
        action_type=action_type,
        entity_id=row.id,
        entity_name=(
            evidence.get("stream_name") or f"review {row.id}"
        ),
        description=(
            f"Review {action_type.replace('review_', '')}: stream "
            f"'{evidence.get('stream_name', '?')}' ↔ master "
            f"'{evidence.get('master_channel_name', '?')}' "
            f"(rule '{evidence.get('rule_name', row.rule_id)}')"
        ),
        before_value={"status": REVIEW_STATUS_PENDING},
        after_value=after_value,
        user_initiated=True,
    )


async def _verify_and_attach(row: EventSyncReview, evidence: dict) -> AcceptReviewOutcome:
    """Best-effort immediate attach for an accepted pairing.

    Uses the evidence SNAPSHOT ids only after re-verifying them against
    live Dispatcharr (see module docstring). Never raises — every failure
    degrades to "decision recorded; the next run attaches it" because the
    fingerprint-keyed decision self-applies on every future run.
    """
    outcome = AcceptReviewOutcome()
    channel_id = evidence.get("master_channel_id")
    stream_id = evidence.get("stream_id")
    if channel_id is None or stream_id is None:
        outcome.attach_deferred_reason = (
            "evidence snapshot carries no channel/stream ids; the next "
            "pipeline run attaches this pairing"
        )
        return outcome

    # The rule's master patterns re-parse the channel's CURRENT name so a
    # recycled channel id cannot receive the stream.
    db = get_session()
    try:
        rule = db.query(ChannelPipelineRule).filter(
            ChannelPipelineRule.id == row.rule_id
        ).first()
        config = rule.get_event_sync_config() if rule else None
    finally:
        db.close()
    if not config:
        outcome.attach_deferred_reason = (
            "rule config unavailable; the next pipeline run attaches this "
            "pairing"
        )
        return outcome

    from services.event_sync_matcher import parse_event_name
    from services.event_sync_resolver import effective_patterns

    client = get_client()
    try:
        channel = await client.get_channel(channel_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            outcome.attach_deferred_reason = (
                "snapshot master channel no longer exists; the next "
                "pipeline run re-resolves the master by event identity"
            )
            return outcome
        logger.warning(
            "[EVENT-SYNC] review accept: channel lookup failed for "
            "review=%s channel=%s: %s", row.id, channel_id, e,
        )
        outcome.attach_deferred_reason = (
            "Dispatcharr channel lookup failed; the next pipeline run "
            "attaches this pairing"
        )
        return outcome
    except Exception as e:  # noqa: BLE001 — degrade, never fail the accept
        logger.warning(
            "[EVENT-SYNC] review accept: channel lookup raised for "
            "review=%s channel=%s: %s", row.id, channel_id, e,
        )
        outcome.attach_deferred_reason = (
            "Dispatcharr channel lookup failed; the next pipeline run "
            "attaches this pairing"
        )
        return outcome

    master_patterns = effective_patterns(config, config["master_group_id"])
    parsed = parse_event_name(channel.get("name") or "", master_patterns)
    if master_event_key(parsed) != row.event_key:
        logger.warning(
            "[EVENT-SYNC] review accept: snapshot channel %s name %r no "
            "longer parses to the accepted event identity (review=%s) — "
            "attach deferred to the next run's fingerprint re-resolution",
            channel_id, channel.get("name"), row.id,
        )
        outcome.attach_deferred_reason = (
            "snapshot master channel no longer carries the accepted event "
            "identity; the next pipeline run re-resolves it"
        )
        return outcome

    try:
        stream = await client.get_stream(stream_id)
    except Exception as e:  # noqa: BLE001 — includes 404; degrade uniformly
        logger.info(
            "[EVENT-SYNC] review accept: snapshot stream %s lookup failed "
            "(review=%s): %s — attach deferred (stream ids churn on "
            "refresh; the fingerprint re-resolves next run)",
            stream_id, row.id, e,
        )
        outcome.attach_deferred_reason = (
            "snapshot stream id no longer resolves (provider refresh); "
            "the next pipeline run attaches this pairing"
        )
        return outcome
    if stream_name_hash(stream.get("name") or "") != row.stream_name_hash:
        outcome.attach_deferred_reason = (
            "snapshot stream id now carries a different name; the next "
            "pipeline run attaches this pairing by fingerprint"
        )
        return outcome

    current_streams = [
        s["id"] if isinstance(s, dict) else s
        for s in channel.get("streams", [])
    ]
    if stream_id in current_streams:
        outcome.already_attached = True
        return outcome

    new_streams = current_streams + [stream_id]
    try:
        await client.update_channel(channel_id, {"streams": new_streams})
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[EVENT-SYNC] review accept: attach write failed for review=%s "
            "channel=%s stream=%s: %s", row.id, channel_id, stream_id, e,
        )
        outcome.attach_deferred_reason = (
            "Dispatcharr update failed; the next pipeline run attaches "
            "this pairing"
        )
        return outcome

    # Standard merge_stream journal entry (same shape as the executor's) so
    # journal-driven surgical unmerge covers review-queue attaches, with
    # attach_source distinguishing it from threshold attaches (ti939.3.2).
    journal.log_entry(
        category="event_sync",
        action_type="merge_stream",
        entity_id=channel_id,
        entity_name=channel.get("name", str(channel_id)),
        description=(
            f"Merged stream '{stream.get('name')}' (id {stream_id}) into "
            f"channel '{channel.get('name')}' via review-queue accept"
        ),
        before_value={"stream_ids": current_streams},
        after_value={
            "stream_ids": new_streams,
            "match": {
                "kind": "event_sync",
                "rule_id": row.rule_id,
                "secondary_stream_id": stream_id,
                "secondary_stream_name": stream.get("name"),
                "provider": evidence.get("provider"),
                "master_channel_id": channel_id,
                "master_channel_name": channel.get("name"),
                "score": evidence.get("score"),
                "band": evidence.get("band"),
                "team_verdict": evidence.get("team_verdict"),
                "time_delta_minutes": evidence.get("time_delta_minutes"),
                "attach_source": "review_queue",
                "review_id": row.id,
            },
        },
        user_initiated=True,
    )
    outcome.attached = True
    return outcome


# ---------------------------------------------------------------------------
# GET /api/event-sync-reviews — paginated list
# ---------------------------------------------------------------------------


@router.get("", response_model=EventSyncReviewsListResponse)
async def list_event_sync_reviews(
    status: str = REVIEW_STATUS_PENDING,
    rule_id: Optional[int] = None,
    page: int = DEFAULT_PAGE,
    page_size: int = DEFAULT_PAGE_SIZE,
    db: Session = Depends(get_session),
    _user=RequireAuthIfEnabled,
) -> EventSyncReviewsListResponse:
    """List review rows by status (default: the open queue).

    Ordering: ``last_seen_at DESC, id DESC`` — most recently re-confirmed
    questions first (a pairing still being surfaced by runs outranks one
    whose event may already be over).
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

    query = db.query(EventSyncReview).filter(EventSyncReview.status == status)
    if rule_id is not None:
        query = query.filter(EventSyncReview.rule_id == rule_id)

    total = query.count()
    rows = (
        query.order_by(
            EventSyncReview.last_seen_at.desc(), EventSyncReview.id.desc()
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    total_pages = (total + page_size - 1) // page_size if total else 0
    return EventSyncReviewsListResponse(
        reviews=[EventSyncReviewRecord(**_record_to_dict(r)) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ---------------------------------------------------------------------------
# POST /api/event-sync-reviews/bulk-discard
# ---------------------------------------------------------------------------


@router.post("/bulk-discard", response_model=BulkDiscardReviewsOutcome)
async def bulk_discard_event_sync_reviews(
    request: BulkDiscardReviewsRequest,
    db: Session = Depends(get_session),
    user=RequireAdminIfEnabled,
) -> BulkDiscardReviewsOutcome:
    """Discard exactly the selected pending review rows in one transaction."""
    rows = db.query(EventSyncReview.id, EventSyncReview.status).filter(
        EventSyncReview.id.in_(request.review_ids)
    ).all()
    status_by_id = {row.id: row.status for row in rows}
    discarded_ids = [
        review_id for review_id in request.review_ids
        if status_by_id.get(review_id) == REVIEW_STATUS_PENDING
    ]
    missing_ids = [
        review_id for review_id in request.review_ids
        if review_id not in status_by_id
    ]
    not_pending_ids = [
        review_id for review_id in request.review_ids
        if review_id in status_by_id
        and status_by_id[review_id] != REVIEW_STATUS_PENDING
    ]

    try:
        if discarded_ids:
            deleted = db.query(EventSyncReview).filter(
                EventSyncReview.id.in_(discarded_ids),
                EventSyncReview.status == REVIEW_STATUS_PENDING,
            ).delete(synchronize_session=False)
            if deleted != len(discarded_ids):
                raise RuntimeError(
                    "selected review rows changed during discard; no rows committed"
                )
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.exception(
            "[EVENT-SYNC] bulk review discard failed requested=%s: %s",
            request.review_ids,
            e,
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Event Sync review discard failed; no selected rows were discarded",
        )

    outcome = BulkDiscardReviewsOutcome(
        requested_ids=request.review_ids,
        discarded_ids=discarded_ids,
        missing_ids=missing_ids,
        not_pending_ids=not_pending_ids,
    )
    actor = _actor_token_id(user)
    if discarded_ids:
        journal.log_entry(
            category="event_sync",
            action_type="review_bulk_discard",
            entity_id=discarded_ids[0],
            entity_name=f"{len(discarded_ids)} Event Sync review items",
            description=(
                f"Discarded {len(discarded_ids)} selected pending Event Sync "
                f"review item(s); {len(missing_ids)} missing and "
                f"{len(not_pending_ids)} no longer pending"
            ),
            before_value={"status": REVIEW_STATUS_PENDING},
            after_value={
                **outcome.model_dump(),
                "actor_token_id": actor,
            },
            user_initiated=True,
        )
    logger.info(
        "[EVENT-SYNC] bulk review discard requested=%s discarded=%s missing=%s "
        "not_pending=%s actor=%s",
        request.review_ids,
        discarded_ids,
        missing_ids,
        not_pending_ids,
        actor,
    )
    return outcome


# ---------------------------------------------------------------------------
# POST /api/event-sync-reviews/{id}/accept
# ---------------------------------------------------------------------------


@router.post("/{review_id}/accept", response_model=AcceptReviewOutcome)
async def accept_event_sync_review(
    review_id: int,
    db: Session = Depends(get_session),
    user=RequireAdminIfEnabled,
) -> AcceptReviewOutcome:
    """Accept a pairing: record the durable decision, then attach now.

    Flow:

      1. Load the row; 404 if missing.
      2. Idempotency: already ``accepted`` → prior outcome (200, decision
         stands, no re-attach — the next run is the idempotent applier);
         ``rejected``/``superseded`` → 409 (cross-state transition).
      3. Flip to ``accepted`` and SUPERSEDE sibling pending pairings for
         the same stream fingerprint (the stream-level question was
         answered; the losing candidates must not linger as open
         questions). Commit — the decision is the durable artifact.
      4. Journal the decision (``review_accept``).
      5. Best-effort immediate attach with snapshot re-verification
         (:func:`_verify_and_attach`) — failure degrades to "next run
         attaches"; the accept has already succeeded.
    """
    row = db.query(EventSyncReview).filter(
        EventSyncReview.id == review_id
    ).first()
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"event sync review id={review_id} not found",
        )

    if row.status == REVIEW_STATUS_ACCEPTED:
        logger.info(
            "[EVENT-SYNC] review accept idempotent: id=%s already accepted",
            row.id,
        )
        return AcceptReviewOutcome(
            attach_deferred_reason=(
                "already accepted previously; runs auto-attach this pairing"
            ),
        )
    if row.status in (REVIEW_STATUS_REJECTED, REVIEW_STATUS_SUPERSEDED):
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"event sync review id={review_id} is already "
                f"{row.status}; cannot accept"
            ),
        )

    now_ms = now_epoch_ms()
    actor = _actor_token_id(user)
    row.status = REVIEW_STATUS_ACCEPTED
    row.resolved_at = now_ms
    row.resolution_source = "operator"
    row.actor_token_id = actor

    # Supersede sibling PENDING pairings for the same stream fingerprint:
    # accepting "stream S ↔ master A" answers the S-level question, so the
    # open "S ↔ B" rows close as superseded (terminal, dedup-preserving,
    # but distinct from an operator "no").
    siblings = (
        db.query(EventSyncReview)
        .filter(
            EventSyncReview.rule_id == row.rule_id,
            EventSyncReview.provider_id == row.provider_id,
            EventSyncReview.stream_name_hash == row.stream_name_hash,
            EventSyncReview.status == REVIEW_STATUS_PENDING,
            EventSyncReview.id != row.id,
        )
        .all()
    )
    for sibling in siblings:
        sibling.status = REVIEW_STATUS_SUPERSEDED
        sibling.resolved_at = now_ms
        sibling.resolution_source = "superseded_by_accept"

    try:
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.exception(
            "[EVENT-SYNC] review accept failed to persist decision "
            "(id=%s): %s", review_id, e,
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error persisting review decision",
        )

    _journal_decision(
        row, "review_accept", actor,
        extra_after={"superseded_sibling_ids": [s.id for s in siblings]},
    )

    evidence = _record_to_dict(row)["evidence"]
    outcome = await _verify_and_attach(row, evidence)
    outcome.superseded_siblings = len(siblings)
    logger.info(
        "[EVENT-SYNC] review accept ok: id=%s attached=%s "
        "already_attached=%s deferred=%r superseded=%d actor=%s",
        row.id, outcome.attached, outcome.already_attached,
        outcome.attach_deferred_reason, len(siblings), actor,
    )
    return outcome


# ---------------------------------------------------------------------------
# POST /api/event-sync-reviews/{id}/reject
# ---------------------------------------------------------------------------


@router.post("/{review_id}/reject", response_model=RejectReviewOutcome)
async def reject_event_sync_review(
    review_id: int,
    db: Session = Depends(get_session),
    user=RequireAdminIfEnabled,
) -> RejectReviewOutcome:
    """Reject a pairing: record the durable suppression. No Dispatcharr call.

    Future runs (and previews) filter the pairing out of candidate
    consideration entirely — it can neither attach (threshold or decision)
    nor re-enter the queue. Idempotent on ``rejected``; 409 on
    ``accepted``/``superseded``.
    """
    row = db.query(EventSyncReview).filter(
        EventSyncReview.id == review_id
    ).first()
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"event sync review id={review_id} not found",
        )

    if row.status == REVIEW_STATUS_REJECTED:
        logger.info(
            "[EVENT-SYNC] review reject idempotent: id=%s already rejected",
            row.id,
        )
        return RejectReviewOutcome()
    if row.status in (REVIEW_STATUS_ACCEPTED, REVIEW_STATUS_SUPERSEDED):
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"event sync review id={review_id} is already "
                f"{row.status}; cannot reject"
            ),
        )

    actor = _actor_token_id(user)
    row.status = REVIEW_STATUS_REJECTED
    row.resolved_at = now_epoch_ms()
    row.resolution_source = "operator"
    row.actor_token_id = actor
    try:
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.exception(
            "[EVENT-SYNC] review reject failed to persist decision "
            "(id=%s): %s", review_id, e,
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error persisting review decision",
        )

    _journal_decision(row, "review_reject", actor)
    logger.info(
        "[EVENT-SYNC] review reject ok: id=%s actor=%s", row.id, actor,
    )
    return RejectReviewOutcome()
