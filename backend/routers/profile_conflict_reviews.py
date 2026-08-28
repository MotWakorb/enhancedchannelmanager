"""Admin API for the durable channel-profile conflict review queue."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from auth import RequireHumanAdminForOperatorDecision
from database import get_session
from dispatcharr_client import get_client
from models import ProfileConflictReview
from services.profile_conflict_review import (
    InvalidChoice,
    REVIEW_STATUS_ACCEPTED,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_SUPERSEDED,
    ReviewNotFound,
    StaleReview,
    accept_profile_conflict_review,
    reconcile_profile_conflict_reviews,
)

router = APIRouter(
    prefix="/api/profile-conflict-reviews", tags=["Profile Conflict Reviews"]
)


class ProfileConflictSourceEvidence(BaseModel):
    source_group_id: int
    source_group_name: str
    m3u_account_id: int | None = None
    m3u_account_name: str


class ProfileConflictChoiceEvidence(BaseModel):
    choice_key: str
    profile_ids: list[int]
    profile_names: list[str]
    sources: list[ProfileConflictSourceEvidence]


class ProfileConflictTargetEvidence(BaseModel):
    effective_group_id: int
    name: str


class ProfileConflictEvidence(BaseModel):
    fingerprint_version: int
    target: ProfileConflictTargetEvidence
    choices: list[ProfileConflictChoiceEvidence]


class ProfileConflictReviewRecord(BaseModel):
    id: int
    fingerprint: str
    effective_group_id: int
    status: str
    accepted_choice_key: str | None = None
    accepted_profile_ids: list[int] | None = None
    evidence: ProfileConflictEvidence
    created_at: int
    last_seen_at: int
    resolved_at: int | None = None
    applied_at: int | None = None
    retry_error: str | None = None


class ProfileConflictReviewsResponse(BaseModel):
    reviews: list[ProfileConflictReviewRecord]
    total: int


class AcceptProfileConflictRequest(BaseModel):
    choice_key: str


class AcceptProfileConflictOutcome(BaseModel):
    status: Literal["accepted"] = "accepted"
    applied: bool
    updated_account_ids: list[int]
    failed_account_ids: list[int]
    retry_error: str | None = None


def _record(row: ProfileConflictReview) -> ProfileConflictReviewRecord:
    try:
        evidence = ProfileConflictEvidence(**json.loads(row.evidence))
    except (TypeError, ValueError, ValidationError):
        evidence = ProfileConflictEvidence(
            fingerprint_version=row.fingerprint_version,
            target=ProfileConflictTargetEvidence(
                effective_group_id=row.effective_group_id,
                name=f"Group {row.effective_group_id}",
            ),
            choices=[],
        )
    try:
        accepted = json.loads(row.accepted_profile_ids) if row.accepted_profile_ids else None
    except (TypeError, ValueError):
        accepted = None
    if not isinstance(accepted, list) or not all(
        isinstance(profile_id, int) for profile_id in accepted
    ):
        accepted = None
    return ProfileConflictReviewRecord(
        id=row.id,
        fingerprint=row.fingerprint,
        effective_group_id=row.effective_group_id,
        status=row.status,
        accepted_choice_key=row.accepted_choice_key,
        accepted_profile_ids=accepted,
        evidence=evidence,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        resolved_at=row.resolved_at,
        applied_at=row.applied_at,
        retry_error=row.retry_error,
    )


@router.get("", response_model=ProfileConflictReviewsResponse)
async def list_profile_conflict_reviews(
    status: str = REVIEW_STATUS_PENDING,
    db: Session = Depends(get_session),
    _user=RequireHumanAdminForOperatorDecision,
) -> ProfileConflictReviewsResponse:
    if status not in (
        REVIEW_STATUS_PENDING, REVIEW_STATUS_ACCEPTED, REVIEW_STATUS_SUPERSEDED
    ):
        raise HTTPException(status_code=400, detail="invalid profile conflict review status")
    query = db.query(ProfileConflictReview)
    if status == REVIEW_STATUS_PENDING:
        query = query.filter(or_(
            ProfileConflictReview.status == REVIEW_STATUS_PENDING,
            and_(
                ProfileConflictReview.status == REVIEW_STATUS_ACCEPTED,
                ProfileConflictReview.applied_at.is_(None),
            ),
        ))
    else:
        query = query.filter(ProfileConflictReview.status == status)
    rows = query.order_by(
        ProfileConflictReview.last_seen_at.desc(), ProfileConflictReview.id.desc()
    ).all()
    return ProfileConflictReviewsResponse(
        reviews=[_record(row) for row in rows], total=len(rows)
    )


def _actor_token_id(user) -> str:
    return "anonymous" if user is None else str(user.id)


@router.post("/{review_id}/accept", response_model=AcceptProfileConflictOutcome)
async def accept_profile_conflict(
    review_id: int,
    request: AcceptProfileConflictRequest,
    db: Session = Depends(get_session),
    user=RequireHumanAdminForOperatorDecision,
) -> AcceptProfileConflictOutcome:
    client = get_client()
    try:
        outcome = await accept_profile_conflict_review(
            db, client, review_id, request.choice_key, _actor_token_id(user)
        )
        return AcceptProfileConflictOutcome(**outcome)
    except ReviewNotFound:
        raise HTTPException(status_code=404, detail="profile conflict review not found")
    except InvalidChoice:
        raise HTTPException(status_code=422, detail="choice_key is not part of this review")
    except StaleReview:
        # Reconcile the current question immediately so a shape change is visible
        # without waiting for the next five-minute sweep.
        row = db.query(ProfileConflictReview).filter(
            ProfileConflictReview.id == review_id
        ).first()
        if row is not None:
            try:
                current = await client.get_all_m3u_group_settings()
                await reconcile_profile_conflict_reviews(client, current)
            except Exception:
                # The 409 remains authoritative; the scheduled sweep retries
                # this best-effort queue refresh.
                pass
        raise HTTPException(
            status_code=409,
            detail="This conflict changed since it was shown. Review the current choices.",
        )
