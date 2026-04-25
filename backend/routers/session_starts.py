"""
Session-start router — SLO-6 denominator counter ingest.

Implements bead ``enhancedchannelmanager-arp3o`` per the spike decision
recorded in ``docs/sre/spike-slo-6-session-semantics.md`` (bd-1tl01).

Contract:

  POST /api/session-start

The frontend generates a SubtleCrypto-backed UUIDv4 once per
``sessionStorage`` lifetime and POSTs it here. The backend deduplicates
via an in-memory set with 24h TTL — only first-seen ``session_id``
values increment ``ecm_session_starts_total``. Re-submission inside the
TTL returns 200 with no metric bump.

Architecture anchors from the spike doc:

  * The counter has no labels (cardinality posture from ADR-006 §9).
  * ``session_id`` is **never** logged, **never** returned in a
    response, **never** written to the database. The dedup set lives
    only in-memory and dies with the process — same lifecycle as the
    Prometheus counter state itself.
  * A second metric — ``ecm_session_dedup_set_size`` (Gauge) — exposes
    the dedup set's current cardinality so SRE can spot pruner leaks.
  * Auth posture: **unauthenticated by design** (bd-m3vej, follow-up
    to bd-arp3o). Listed in ``AUTH_EXEMPT_PATHS`` in main.py so pre-auth
    sessions count toward the SLO-6 denominator. Note the asymmetry
    with ``/api/client-errors`` (which remains JWT-required): pre-auth
    bootstrap errors are NOT counted in the SLO-6 numerator, so the
    error rate is biased LOW. Documented in ADR-006 §1 and
    docs/sre/slos.md (SLO-6 "Known measurement bias").
"""
from __future__ import annotations

import logging
import re
import time
from threading import Lock
from typing import Dict

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from observability import get_metric

logger = logging.getLogger("ecm.session_start")

router = APIRouter(prefix="/api/session-start", tags=["Observability"])


# ---------------------------------------------------------------------------
# Payload caps + dedup TTL (from spike §6.2)
# ---------------------------------------------------------------------------
# A UUIDv4 string is 36 chars. We cap incoming raw body at 256 bytes so a
# malformed/oversized body cannot inflate the dedup set's per-entry cost.
MAX_REQUEST_BYTES = 256
MAX_SESSION_ID_CHARS = 36

# Dedup TTL — entries older than this are pruned at the next request.
# Spike §6.2 specifies 24 hours: long enough that a tab left open
# overnight isn't double-counted on the morning refresh, short enough
# that the in-memory set's steady-state size is bounded by daily session
# volume.
DEDUP_TTL_SECONDS = 24 * 60 * 60  # 24 hours

# UUIDv4 regex: 8-4-4-4-12 hex with version=4 nibble and variant in {8,9,a,b}.
# Stricter than a generic UUID regex so the endpoint matches the
# SubtleCrypto-generated value the frontend sends and rejects anything
# else (a v1 UUID, a hash, or a free-form string).
_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pydantic schema
# ---------------------------------------------------------------------------
class SessionStartPayload(BaseModel):
    """Single-field payload for the session-start ingest.

    Field allowlist (deny-by-default) — matches the spike's data-minimization
    posture: nothing about the user, the browser, or the page is sent here.
    The session_id alone is enough to deduplicate.
    """

    session_id: str = Field(..., max_length=MAX_SESSION_ID_CHARS)

    model_config = {
        "extra": "forbid",  # Reject unknown fields (deny-by-default allowlist).
    }

    @field_validator("session_id")
    @classmethod
    def _uuid_v4(cls, v: str) -> str:
        """session_id MUST be a UUIDv4 — reject anything else with 422."""
        if not _UUID_V4_RE.fullmatch(v or ""):
            raise ValueError("session_id must be a UUIDv4 string")
        return v.lower()


# ---------------------------------------------------------------------------
# In-memory dedup set
# ---------------------------------------------------------------------------
# Map session_id -> expiry_timestamp (monotonic seconds). A dict (not a
# set) lets us prune lazily by comparing expiry to ``now`` without
# tracking a separate parallel structure. Memory cost per entry is the
# 36-char UUID + a float — well under 100 bytes including dict overhead.
#
# Spike §6.2 specifies "lazy prune at request time" — we sweep expired
# entries on every POST. Sweep cost is O(active_entries); at the SLO-6
# §11 evaluation gate (~50 sessions/day → ~50 entries) this is
# negligible. At pathological scale (10k sessions in 24h) the sweep is
# still microseconds — and a leak there would be visible via the
# ``ecm_session_dedup_set_size`` gauge before it became a real problem.
_dedup: Dict[str, float] = {}
_dedup_lock = Lock()


def _prune_expired(now: float) -> None:
    """Drop dedup entries whose TTL has elapsed.

    Caller MUST hold ``_dedup_lock``. Pure side-effect: mutates ``_dedup``
    in place. Cheap O(active_entries) sweep — runs on every request so
    the set never grows unboundedly between scrapes.
    """
    expired = [sid for sid, expiry in _dedup.items() if expiry <= now]
    for sid in expired:
        _dedup.pop(sid, None)


def _publish_dedup_size() -> None:
    """Mirror the dedup set's current size onto the gauge.

    Defensive: never raises. If prometheus-client isn't available the
    gauge .set() call is a no-op via the metric stub layer.
    """
    try:
        get_metric("session_dedup_set_size").set(float(len(_dedup)))
    except Exception:  # pragma: no cover — observability must not break the sink
        logger.debug("[SESSION-START] gauge update failed for dedup_set_size")


def _bump_session_counter() -> None:
    """Increment ``ecm_session_starts_total``.

    Defensive: a metric emit failure must never break the endpoint —
    the SLO denominator is best-effort, not a transactional guarantee.
    """
    try:
        get_metric("session_starts_total").inc()
    except Exception:  # pragma: no cover
        logger.debug("[SESSION-START] counter emit failed")


def _reset_dedup_for_tests() -> None:
    """Clear the in-memory dedup state. Tests only."""
    with _dedup_lock:
        _dedup.clear()
    _publish_dedup_size()


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------
@router.post("", include_in_schema=True)
async def post_session_start(request: Request) -> JSONResponse:
    """Ingest a single session-start beacon.

    Contract:
      * 200 with ``{"deduplicated": false}`` on first sighting (counter +1).
      * 200 with ``{"deduplicated": true}`` on a re-submission inside TTL
        (no counter bump).
      * No 401: the path is in ``AUTH_EXEMPT_PATHS`` so unauthenticated
        POSTs are accepted by design (bd-m3vej, follow-up to bd-arp3o).
        Pre-auth sessions MUST count in the SLO-6 denominator.
      * 413 when the raw body exceeds ``MAX_REQUEST_BYTES``.
      * 422 when the payload fails Pydantic validation (e.g. non-UUIDv4).

    The endpoint honors the same operator toggle as ``/api/client-errors``
    — when ``settings.telemetry_client_errors_enabled`` is False, the
    handler short-circuits with 200 + ``{"deduplicated": false}`` and
    does NOT bump the counter. This matches the frontend's gate (one
    operator toggle controls both telemetry surfaces, per spike §6.1).

    The ``session_id`` is NEVER logged. NEVER returned. NEVER persisted.
    """
    # ---- Settings gate ------------------------------------------------------
    # Same lazy import pattern as routers/client_errors.py to avoid a
    # top-level config import loop in tests that stub get_settings().
    try:
        from config import get_settings
        if not get_settings().telemetry_client_errors_enabled:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"deduplicated": False},
            )
    except Exception:  # pragma: no cover — settings subsystem misconfigured
        logger.debug("[SESSION-START] Could not read telemetry setting; proceeding")

    # ---- Size check (before parsing body) -----------------------------------
    raw_body = await request.body()
    body_size = len(raw_body)
    if body_size > MAX_REQUEST_BYTES:
        logger.warning(
            "[SESSION-START] Dropped oversized payload bytes=%d cap=%d",
            body_size, MAX_REQUEST_BYTES,
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Payload too large",
        )

    # ---- Schema validation --------------------------------------------------
    try:
        import json
        parsed = json.loads(raw_body)
        payload = SessionStartPayload.model_validate(parsed)
    except Exception as exc:
        # Log the validation failure (NOT the body — could contain a
        # candidate session_id we don't want in logs even when invalid).
        logger.info("[SESSION-START] Dropped invalid payload: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid payload",
        )

    # ---- Dedup check + counter bump ----------------------------------------
    now = time.monotonic()
    with _dedup_lock:
        _prune_expired(now)
        if payload.session_id in _dedup:
            # Refresh the expiry so a long-lived tab that re-submits at
            # the 23h mark doesn't fall out of the dedup window 1h
            # later. Without this, the tab would be re-counted at 24h
            # exactly because of timing rather than because it's a real
            # new session.
            _dedup[payload.session_id] = now + DEDUP_TTL_SECONDS
            _publish_dedup_size()
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"deduplicated": True},
            )
        _dedup[payload.session_id] = now + DEDUP_TTL_SECONDS

    # First sighting — bump the counter. Done OUTSIDE the lock because
    # the counter has its own internal lock and we don't want to hold the
    # dedup lock across a metrics call.
    _bump_session_counter()
    _publish_dedup_size()

    # NEVER log the session_id. The audit message is intentionally
    # cardinality-free.
    logger.debug("[SESSION-START] Counted new session")

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"deduplicated": False},
    )
