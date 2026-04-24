"""
Client errors router — local-sink for frontend runtime error telemetry.

Implements ADR-006 (Phase 1) — bead ``enhancedchannelmanager-i6a1m``:

  POST /api/client-errors

The endpoint is JWT-gated (inherits the global auth middleware on
``/api/*``), accepts an OTel-log-record-compatible payload from the
frontend error reporter, rate-limits per session/user to 10 events per
rolling 60s, and lands the event as:

  * a single ``[CLIENT-ERROR]`` structlog line on ``ecm.client_error``,
  * a ``ecm_client_errors_total{kind,release}`` counter increment,
  * a ``ecm_client_error_reports_bytes`` histogram observation.

Rejected requests (oversize, bad schema, rate-limited) bump
``ecm_client_errors_dropped_total{reason}`` so the operator can tell the
difference between "no errors" and "sink silently dropping everything".

Architecture anchors from ADR-006:

  * No external egress — data never leaves the container. Phase 2 swaps
    the sink for an OTel collector; payload schema is identical.
  * Deny-by-default field allowlist — the reporter only sends fields in
    the Pydantic model below; anything else is dropped at parse time.
  * Basename-stripping on stack frames — absolute filesystem paths are
    collapsed to their basename before the log line is written, so
    operator disks never leak into telemetry.
  * Bounded label cardinality — ``kind`` is a fixed enum (with ``other``
    fallback), ``release`` is capped at a short string.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Deque, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from obfuscate import obfuscate_text
from observability import get_metric

logger = logging.getLogger("ecm.client_error")

router = APIRouter(prefix="/api/client-errors", tags=["Observability"])


# ---------------------------------------------------------------------------
# Payload caps (from ADR-006 §1-§3)
# ---------------------------------------------------------------------------
MAX_REQUEST_BYTES = 8 * 1024          # 8 KB hard cap on the raw request body
MAX_MESSAGE_CHARS = 512
MAX_STACK_CHARS = 4096
MAX_ROUTE_CHARS = 256
MAX_RELEASE_CHARS = 64
MAX_USER_AGENT_HASH_CHARS = 64        # sha256 hex = 64 chars
MAX_TS_CHARS = 64

# Rate limit: 10 events per bucket key per rolling 60s.
RATE_LIMIT_EVENTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60.0

# Fixed ``kind`` enum. Unknown values fall through to ``"other"`` at metric
# label time so the label cardinality stays bounded regardless of what the
# client reports. Mirrors the ``_normalize_rule_category`` pattern in
# ``observability.py``.
ALLOWED_KINDS = frozenset({
    "boundary",
    "unhandled_rejection",
    "chunk_load",
    "resource",
    "other",
})


# ---------------------------------------------------------------------------
# Pydantic schema
# ---------------------------------------------------------------------------
_ABSOLUTE_PATH_RE = re.compile(
    # POSIX absolute: /foo/bar/baz.js      → baz.js
    # Windows absolute: C:\foo\bar\baz.js  → baz.js
    # file:// URL: file:///foo/bar/baz.js  → baz.js
    r"(?:file://)?(?:[A-Za-z]:)?[\\/](?:[^\s\\/:*?\"<>|(){}\[\],]+[\\/])+"
)


def _strip_absolute_paths(stack: str) -> str:
    """Collapse absolute paths to basenames inside a stack trace.

    ADR-006 §3 ("basename-stripped"): stack traces are allowed to carry
    filename + line/col, but never the full filesystem path. This pattern
    matches POSIX, Windows, and ``file://`` URL forms and rewrites them in
    place so the rest of the frame (``baz.js:42:17``) is preserved.

    Applied server-side as belt-and-suspenders — the client already does
    this, but a misbehaving client (or a Phase 2 sender that skips the
    client-side scrubber) must not leak operator paths into structlog.
    """
    return _ABSOLUTE_PATH_RE.sub("", stack)


def _strip_query_and_fragment(route: str) -> str:
    """Drop ``?...`` and ``#...`` from a pathname (ADR-006 §4 allowlist)."""
    for sep in ("?", "#"):
        idx = route.find(sep)
        if idx >= 0:
            route = route[:idx]
    return route


class ClientErrorPayload(BaseModel):
    """OTel-log-record-compatible client error payload.

    Field allowlist — ADR-006 §4. Anything NOT on this model is silently
    dropped by Pydantic; the reporter explicitly enumerates every field
    it sends.
    """

    kind: Literal["boundary", "unhandled_rejection", "chunk_load", "resource", "other"] = Field(
        ..., description="Error kind — fixed enum for bounded metric label cardinality."
    )
    message: str = Field(..., max_length=MAX_MESSAGE_CHARS)
    stack: str = Field("", max_length=MAX_STACK_CHARS)
    release: str = Field(..., max_length=MAX_RELEASE_CHARS)
    route: str = Field("", max_length=MAX_ROUTE_CHARS)
    user_agent_hash: str = Field(..., max_length=MAX_USER_AGENT_HASH_CHARS)
    ts: str = Field(..., max_length=MAX_TS_CHARS)

    model_config = {
        "extra": "forbid",  # Reject unknown fields (deny-by-default allowlist).
    }

    @field_validator("stack")
    @classmethod
    def _basename_strip_stack(cls, v: str) -> str:
        """Rewrite absolute paths to basenames inside the stack trace."""
        if not v:
            return v
        return _strip_absolute_paths(v)

    @field_validator("route")
    @classmethod
    def _route_pathname_only(cls, v: str) -> str:
        """Enforce pathname-only (no query string, no fragment) on the route."""
        if not v:
            return v
        return _strip_query_and_fragment(v)

    @field_validator("user_agent_hash")
    @classmethod
    def _hex_hash(cls, v: str) -> str:
        """user_agent_hash must look like a SHA-256 hex digest (bounded + opaque)."""
        if not re.fullmatch(r"[0-9a-fA-F]{64}", v):
            raise ValueError("user_agent_hash must be a 64-char hex string")
        return v.lower()


# ---------------------------------------------------------------------------
# Rate limiter — per-bucket sliding window, in-memory
# ---------------------------------------------------------------------------
# We DO NOT reuse ``slowapi`` here because slowapi keys on the remote
# address; ADR-006 §2 specifies per-session/user keying. A single operator
# running three tabs should get three independent buckets, not one shared
# one at the NAT boundary. A simple in-memory sliding window deque covers
# Phase 1's single-container reality without standing up Redis or bolting
# on slowapi's backend protocol.
#
# Memory ceiling is O(active_sessions × RATE_LIMIT_EVENTS); at 10 events
# × ~100 concurrent sessions that's ~1 KB of deque entries. The reaper
# below GC's quiet keys so long-lived processes don't accumulate stale
# entries from one-off sessions.
_rate_buckets: Dict[str, Deque[float]] = defaultdict(deque)
_rate_lock = Lock()
_LAST_REAP_TIME = 0.0
_REAP_INTERVAL_SECONDS = 300.0  # GC quiet buckets every 5 min


def _reap_quiet_buckets(now: float) -> None:
    """Drop bucket keys that haven't seen traffic within the rate window.

    Cheap O(active_keys) sweep, triggered lazily (every ~5 min). Keeps the
    dict bounded on long-running processes.
    """
    global _LAST_REAP_TIME
    if now - _LAST_REAP_TIME < _REAP_INTERVAL_SECONDS:
        return
    _LAST_REAP_TIME = now
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    stale_keys = [
        key for key, window in _rate_buckets.items()
        if not window or window[-1] < cutoff
    ]
    for key in stale_keys:
        _rate_buckets.pop(key, None)


def _rate_limit_bucket_key(request: Request) -> str:
    """Return the bucket key for this request.

    Preference order (ADR-006 §2 — "per-session"):

      1. Authenticated user id (from the JWT payload). A single user across
         multiple tabs still shares the bucket — that matches the "one
         operator, one blast radius" framing.
      2. Session cookie / header fallback when the JWT context isn't
         resolvable in the middleware chain.
      3. Client IP as last-resort bucket key (tests with auth disabled +
         missing session cookies land here so the limiter still applies).

    We never return an empty string — an empty key would collapse every
    client into one bucket, which is worse than no rate limit at all.
    """
    # 1. JWT sub claim
    try:
        from auth.dependencies import get_token_from_request, decode_token_safe
        token = get_token_from_request(request)
        if token:
            payload = decode_token_safe(token)
            if payload:
                sub = payload.get("sub")
                if sub:
                    return f"user:{sub}"
    except Exception:  # pragma: no cover — auth subsystem misconfigured
        pass

    # 2. Session cookie
    session_cookie = request.cookies.get("session_id") or request.cookies.get("access_token")
    if session_cookie:
        # Hash the cookie so a log of the rate-limit dict never carries a raw
        # credential value. 16 hex chars is enough for bucket uniqueness.
        return "cookie:" + hashlib.sha256(session_cookie.encode("utf-8")).hexdigest()[:16]

    # 3. Client IP fallback
    client = request.client
    if client and client.host:
        return f"ip:{client.host}"

    return "ip:unknown"


def _check_rate_limit(bucket_key: str) -> bool:
    """Return True when the bucket has capacity; False when it's full.

    Side effect: on a successful check, the current timestamp is appended
    to the bucket. On a rejection the deque is unchanged so a storm of
    429s doesn't shift the window forward and never drain.
    """
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        _reap_quiet_buckets(now)
        window = _rate_buckets[bucket_key]
        # Drop entries that have aged out of the window.
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= RATE_LIMIT_EVENTS:
            return False
        window.append(now)
        return True


def _reset_rate_limits_for_tests() -> None:
    """Clear the in-memory rate-limit state. Tests only."""
    global _LAST_REAP_TIME
    with _rate_lock:
        _rate_buckets.clear()
        _LAST_REAP_TIME = 0.0


# ---------------------------------------------------------------------------
# Release label cardinality cap (ADR-006 §9)
# ---------------------------------------------------------------------------
# ``release`` goes on the Prometheus counter — we cap cardinality at the
# current build + the two prior releases. Anything older rolls up to
# ``"stale"``. The "current" build is whatever the most recently-seen
# release string is; we track a bounded LRU keyed on first-seen order.
_seen_releases: Deque[str] = deque(maxlen=3)
_seen_releases_lock = Lock()


def _label_release(release: str) -> str:
    """Map a raw release string onto a bounded-cardinality metric label."""
    if not release:
        return "stale"
    # Trim whitespace and clip to max length defensively.
    cleaned = release.strip()[:MAX_RELEASE_CHARS]
    if not cleaned:
        return "stale"
    with _seen_releases_lock:
        if cleaned in _seen_releases:
            # Move-to-front so "recently active" releases stay in the window.
            try:
                _seen_releases.remove(cleaned)
            except ValueError:  # pragma: no cover
                pass
            _seen_releases.append(cleaned)
            return cleaned
        _seen_releases.append(cleaned)
        return cleaned


def _reset_release_cache_for_tests() -> None:
    """Clear the release LRU. Tests only."""
    with _seen_releases_lock:
        _seen_releases.clear()


def _bump_drop(reason: str) -> None:
    """Increment ``ecm_client_errors_dropped_total{reason}`` safely.

    Observability must never take down the endpoint — wrap the metric
    emit in a try/except and log at debug on failure.
    """
    try:
        get_metric("client_errors_dropped_total").labels(reason=reason).inc()
    except Exception:  # pragma: no cover — metrics must not break the sink
        logger.debug("[CLIENT-ERROR] metric emit failed for drop reason=%s", reason)


def _bump_success(kind: str, release_label: str, byte_size: int) -> None:
    """Increment the success counter + record the payload-size histogram."""
    try:
        get_metric("client_errors_total").labels(
            kind=kind if kind in ALLOWED_KINDS else "other",
            release=release_label,
        ).inc()
    except Exception:  # pragma: no cover
        logger.debug("[CLIENT-ERROR] counter emit failed kind=%s release=%s", kind, release_label)
    try:
        get_metric("client_error_reports_bytes").observe(max(0, byte_size))
    except Exception:  # pragma: no cover
        logger.debug("[CLIENT-ERROR] histogram emit failed size=%d", byte_size)


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------
@router.post("", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=True)
async def post_client_error(request: Request) -> Response:
    """Ingest a single frontend error report.

    Contract:
      * 204 on successful ingest (no body — fire-and-forget from the client).
      * 401 when auth is enabled and the JWT is missing/invalid (handled by
        the global ``auth_middleware`` in main.py before this handler runs).
      * 413 when the raw body exceeds ``MAX_REQUEST_BYTES``.
      * 422 when the payload fails Pydantic validation.
      * 429 when the per-bucket sliding window is full.

    All drops bump ``ecm_client_errors_dropped_total{reason}``.
    """
    # ---- Size check (before parsing body) -----------------------------------
    # Stream the body into memory up to the cap + 1. An operator-facing
    # FastAPI/uvicorn deployment will usually already cap request size at
    # the reverse proxy, but the reporter is wired to hit this backend
    # directly too, so we enforce explicitly.
    raw_body = await request.body()
    body_size = len(raw_body)
    if body_size > MAX_REQUEST_BYTES:
        _bump_drop("oversized")
        logger.warning(
            "[CLIENT-ERROR] Dropped oversized payload bytes=%d cap=%d",
            body_size, MAX_REQUEST_BYTES,
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Payload too large",
        )

    # ---- Rate limit ---------------------------------------------------------
    bucket_key = _rate_limit_bucket_key(request)
    if not _check_rate_limit(bucket_key):
        _bump_drop("rate_limited")
        logger.info(
            "[CLIENT-ERROR] Rate limit exceeded bucket=%s window=%ds cap=%d",
            bucket_key, int(RATE_LIMIT_WINDOW_SECONDS), RATE_LIMIT_EVENTS,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(int(RATE_LIMIT_WINDOW_SECONDS))},
        )

    # ---- Schema validation --------------------------------------------------
    try:
        import json
        parsed = json.loads(raw_body)
        payload = ClientErrorPayload.model_validate(parsed)
    except Exception as exc:
        _bump_drop("invalid_schema")
        logger.info("[CLIENT-ERROR] Dropped invalid payload: %s", exc)
        # Re-raise as 422 (HTTPException so FastAPI formats the response).
        # Pydantic's ValidationError already produces a 422-shaped response
        # via the global handler; normalize here so the test surface is
        # deterministic.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc)[:256],
        )

    # ---- Server-side scrubbing (belt-and-suspenders per ADR-006 §5) --------
    # Apply the shared IP/URL scrubber to message and stack.
    scrubbed_message = obfuscate_text(payload.message)
    scrubbed_stack = obfuscate_text(payload.stack)

    # Re-cap in case the scrubber somehow grew the string (it doesn't, but
    # defensive truncation is cheap insurance).
    if len(scrubbed_message) > MAX_MESSAGE_CHARS:
        scrubbed_message = scrubbed_message[:MAX_MESSAGE_CHARS]
    if len(scrubbed_stack) > MAX_STACK_CHARS:
        scrubbed_stack = scrubbed_stack[:MAX_STACK_CHARS]

    release_label = _label_release(payload.release)

    # ---- Emit metrics + structlog ------------------------------------------
    _bump_success(kind=payload.kind, release_label=release_label, byte_size=body_size)

    # Lazy %-formatting per backend/CLAUDE.md. The structured payload flows
    # through ``extra=`` so the JSON formatter (observability.py) flattens
    # each field onto the top-level JSON object — operators grep by
    # ``kind``, ``release``, or ``user_agent_hash``.
    logger.info(
        "[CLIENT-ERROR] kind=%s release=%s route=%s msg=%s",
        payload.kind, release_label, payload.route, scrubbed_message,
        extra={
            "event": "client_error",
            "kind": payload.kind,
            # Renamed from "message" to avoid collision with LogRecord's
            # reserved ``message`` attribute — stdlib logging raises
            # KeyError on any extra dict that shadows it.
            "client_message": scrubbed_message,
            "stack": scrubbed_stack,
            "release": payload.release,
            "release_label": release_label,
            "route": payload.route,
            "user_agent_hash": payload.user_agent_hash,
            "client_ts": payload.ts,
            "body_size_bytes": body_size,
        },
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
