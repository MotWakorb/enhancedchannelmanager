"""
Channel merges router — dedup candidate lookup endpoint.

Part of the interactive stream-to-channel deduplication feature (bd-1v4ht,
ADR-008). This module owns the ``/api/channel-merges/*`` route family.

Current scope (BD-D, bd-kbqwb):
    GET /api/channel-merges/candidates — synchronous top-1 candidate lookup
    for the operator-facing dedup modal.

Future scope (BD-E, bd-acqkb):
    GET  /api/channel-merges          — paginated pending-merges queue
    POST /api/channel-merges/{id}/accept  — confirm a merge
    POST /api/channel-merges/{id}/dismiss — reject a candidate

API contract per ADR-008 §D1 (D4 override — plural-noun resource path,
not the original /api/dedup/* draft). Response envelope follows the ECM
flat-outcome pattern — no top-level ``data`` wrapper.
"""

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth import RequireAdminIfEnabled
from config import get_settings
from dispatcharr_client import get_client
from observability import get_metric
from services.dedup_matcher import find_candidate, MatchResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channel-merges", tags=["Channel Merges"])


# ---------------------------------------------------------------------------
# Response models
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
# Endpoints
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
