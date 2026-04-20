"""
Health & cache router — health check and cache management endpoints.

Two health endpoints with distinct purposes:

- ``/api/health``: cheap liveness probe. Returns 200 with version metadata.
  Called every 30s by the Dockerfile HEALTHCHECK, so it must stay fast and
  must NOT depend on external subsystems (DB, Dispatcharr, ffprobe). If this
  endpoint returns non-200, the container is considered unhealthy — we don't
  want a transient Dispatcharr outage to mark the whole app as down.

- ``/api/health/ready``: rich readiness probe. Verifies the app can actually
  do work by checking DB connectivity, Dispatcharr reachability (cached 30s
  to avoid hammering), and ffprobe availability. Returns 200 when all
  subsystems are healthy, 503 when any required subsystem is degraded
  (standard readiness convention).

Extracted from main.py (Phase 2 of v0.13.0 backend refactor). Rich readiness
added per bead enhancedchannelmanager-w0iyw (SRE operability improvement).
"""
import asyncio
import logging
import os
import shutil
import time
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Response
from sqlalchemy import text

from cache import get_cache
from config import get_settings
from database import get_session
from dispatcharr_client import get_client
from observability import get_metric

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


# ============================================================================
# Readiness-check helpers
# ============================================================================
# Cache the Dispatcharr reachability result to avoid hammering Dispatcharr
# when /api/health/ready is polled rapidly (e.g., by a load balancer). A
# simple module-level dict is sufficient — the cache lives as long as the
# process and has a single key. A full cache library would be overkill.
_DISPATCHARR_CACHE_TTL_SECONDS = 30.0
_DISPATCHARR_PING_TIMEOUT_SECONDS = 3.0
_dispatcharr_cache: dict = {"expires_at": 0.0, "result": None}

# Track last-known readiness state so we only log transitions, not every poll.
_last_ready_state: Optional[bool] = None


async def _check_database() -> dict:
    """Run a cheap ``SELECT 1`` to confirm the DB is reachable.

    Returns a dict with ``status`` ("ok" or "fail") and a ``detail`` string.
    Never raises — failures are captured as a "fail" result.
    """
    try:
        db = get_session()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        return {"status": "ok", "detail": "SELECT 1 succeeded"}
    except Exception as e:
        logger.warning("[HEALTH] Database readiness check failed: %s", e)
        return {"status": "fail", "detail": f"{type(e).__name__}: {e}"}


async def _ping_dispatcharr() -> dict:
    """Attempt to reach Dispatcharr with a short timeout.

    Only verifies the URL is reachable (TCP + HTTP response). Does NOT
    authenticate — we don't want a transient auth problem to mark readiness
    as failed, and authenticating on every readiness poll would defeat the
    purpose of a cheap probe.

    Uses the Dispatcharr client's underlying httpx client so we inherit the
    configured connection limits. Returns a dict with ``status``, ``detail``,
    and ``cached_until`` fields.
    """
    settings = get_settings()
    if not settings.url:
        return {"status": "skipped", "detail": "not configured"}

    try:
        client = get_client()
        # Hit the base URL — any response (including 4xx) means Dispatcharr
        # is up and responding, which is all readiness needs to confirm.
        response = await asyncio.wait_for(
            client._client.get(client.base_url),
            timeout=_DISPATCHARR_PING_TIMEOUT_SECONDS,
        )
        return {
            "status": "ok",
            "detail": f"reachable (HTTP {response.status_code})",
        }
    except asyncio.TimeoutError:
        return {
            "status": "fail",
            "detail": f"timeout after {_DISPATCHARR_PING_TIMEOUT_SECONDS}s",
        }
    except Exception as e:
        return {"status": "fail", "detail": f"{type(e).__name__}: {e}"}


async def _check_dispatcharr() -> dict:
    """Ping Dispatcharr, caching the result for 30s.

    The readiness endpoint may be polled rapidly (load balancer, k8s probe),
    and pinging Dispatcharr on every call would add avoidable load to the
    downstream service. Caching for 30s gives us a fresh-enough signal
    without creating a thundering herd.
    """
    now = time.monotonic()
    cached = _dispatcharr_cache.get("result")
    expires_at = _dispatcharr_cache.get("expires_at", 0.0)
    if cached is not None and now < expires_at:
        # Echo the cached result with a wall-clock hint for observers.
        return {**cached, "cached_until": _dispatcharr_cache.get("cached_until_iso", "")}

    result = await _ping_dispatcharr()
    expires_at = now + _DISPATCHARR_CACHE_TTL_SECONDS
    # Use wall-clock time for the "cached_until" field so callers can read it
    # meaningfully. Monotonic time is used internally for the TTL check.
    import datetime
    cached_until_iso = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=_DISPATCHARR_CACHE_TTL_SECONDS)
    ).isoformat()

    _dispatcharr_cache["result"] = result
    _dispatcharr_cache["expires_at"] = expires_at
    _dispatcharr_cache["cached_until_iso"] = cached_until_iso
    return {**result, "cached_until": cached_until_iso}


def _check_ffprobe() -> dict:
    """Check whether ffprobe is available on PATH."""
    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path:
        return {"status": "ok", "detail": ffprobe_path}
    return {"status": "fail", "detail": "ffprobe not found on PATH"}


def _reset_dispatcharr_cache() -> None:
    """Reset the Dispatcharr readiness cache. Intended for tests."""
    _dispatcharr_cache["expires_at"] = 0.0
    _dispatcharr_cache["result"] = None
    _dispatcharr_cache["cached_until_iso"] = ""


async def _timed(check_name: str, probe: Callable[[], Awaitable[dict]]) -> dict:
    """Run an async probe, recording its duration on the readiness histogram.

    Why wrap the existing probes instead of sprinkling timing logic inside
    each? The probes were already written and tested; observability should
    be a wrapper, not an intrusion. ``check_name`` is a fixed vocabulary
    ({"database", "dispatcharr", "ffprobe"}), so the label cardinality is
    trivially bounded — exactly what the metric contract requires.
    """
    start = time.perf_counter()
    try:
        return await probe()
    finally:
        _observe_check(check_name, time.perf_counter() - start)


def _timed_sync(check_name: str, probe: Callable[[], dict]) -> dict:
    """Synchronous counterpart to :func:`_timed` for ffprobe-style checks."""
    start = time.perf_counter()
    try:
        return probe()
    finally:
        _observe_check(check_name, time.perf_counter() - start)


def _observe_check(check_name: str, duration_seconds: float) -> None:
    """Record a readiness sub-check duration on the Prometheus histogram."""
    try:
        get_metric("health_ready_check_duration_seconds").labels(
            check=check_name
        ).observe(duration_seconds)
    except Exception as exc:  # pragma: no cover — never let metrics break health
        logger.warning("[HEALTH] Metric emit for %s failed: %s", check_name, exc)


# ============================================================================
# Endpoints
# ============================================================================
@router.get("/api/health")
async def health_check():
    """Cheap liveness check — no subsystem probing.

    The Dockerfile HEALTHCHECK calls this every 30s; it must stay fast and
    must not fail for transient downstream issues. For rich subsystem
    verification, callers should hit ``/api/health/ready`` instead.
    """
    version = os.environ.get("ECM_VERSION", "unknown")
    release_channel = os.environ.get("RELEASE_CHANNEL", "latest")
    git_commit = os.environ.get("GIT_COMMIT", "unknown")

    return {
        "status": "healthy",
        "service": "enhanced-channel-manager",
        "version": version,
        "release_channel": release_channel,
        "git_commit": git_commit,
    }


@router.get("/api/health/ready")
async def readiness_check(response: Response) -> dict:
    """Rich readiness check — verifies DB, Dispatcharr, and ffprobe.

    Returns 200 when every required subsystem is "ok" (or Dispatcharr is
    "skipped" because no URL is configured — first-run state). Returns 503
    when any required subsystem fails, so load balancers and orchestrators
    can route traffic accordingly.

    The Dispatcharr ping is cached for 30s to avoid hammering the downstream
    service when readiness is polled rapidly.
    """
    global _last_ready_state

    # Run every sub-check under a histogram. ``_timed`` records a duration
    # sample labeled by the check name regardless of whether the probe
    # returned ok/fail/skipped — the histogram answers "how long do these
    # checks take?", which is the signal operators need to tune timeouts.
    db_result = await _timed("database", _check_database)
    dispatcharr_result = await _timed("dispatcharr", _check_dispatcharr)
    ffprobe_result = _timed_sync("ffprobe", _check_ffprobe)

    checks = {
        "database": db_result,
        "dispatcharr": dispatcharr_result,
        "ffprobe": ffprobe_result,
    }

    # "skipped" counts as acceptable — no Dispatcharr configured yet is a
    # valid first-run state, not a failure.
    is_ready = all(
        check["status"] in ("ok", "skipped") for check in checks.values()
    )

    # Log transitions only, not every poll, to keep log volume sane.
    if _last_ready_state is not None and _last_ready_state != is_ready:
        if is_ready:
            logger.info("[HEALTH] Readiness transitioned fail -> ok")
        else:
            failing = [
                f"{name}={check['status']} ({check['detail']})"
                for name, check in checks.items()
                if check["status"] == "fail"
            ]
            logger.info("[HEALTH] Readiness transitioned ok -> fail: %s", "; ".join(failing))
    _last_ready_state = is_ready

    # Publish the current readiness verdict as a 0/1 gauge so scrapers can
    # alert on ``ecm_health_ready_ok == 0`` without having to parse JSON.
    # We always write the gauge — even when the handler failed — so the
    # metric never goes stale.
    try:
        get_metric("health_ready_ok").set(1 if is_ready else 0)
    except Exception as exc:  # pragma: no cover — never let metrics break health
        logger.warning("[HEALTH] Metric emit for health_ready_ok failed: %s", exc)

    payload: dict[str, Any] = {
        "status": "ready" if is_ready else "not_ready",
        "checks": checks,
    }

    if not is_ready:
        response.status_code = 503

    return payload


@router.get("/api/health/schema")
async def schema_version() -> dict:
    """Report the applied Alembic schema revision and SQLite PRAGMA state.

    Exposed so DBAS restore/sync flows (bd-gb5r5.3 / bd-gb5r5.4) can gate on
    the target deployment's schema version and detect drift before importing
    a backup taken against a different revision.

    Public endpoint (see ``main.AUTH_EXEMPT_PATHS``): no auth, no rate limit,
    no subsystem probing beyond two cheap PRAGMA reads.
    """
    import database

    current_rev = database.get_current_schema_revision()
    head_rev = database.get_alembic_head_revision()

    fk_enabled: Optional[bool] = None
    journal_mode: Optional[str] = None
    try:
        # Prefer the live engine; fall back to the session's bound engine so
        # tests that only patch _SessionLocal can still probe PRAGMA state.
        try:
            engine = database.get_engine()
        except RuntimeError:
            session_local = getattr(database, "_SessionLocal", None)
            engine = session_local().get_bind() if session_local else None
        if engine is not None:
            with engine.connect() as conn:
                fk_row = conn.execute(text("PRAGMA foreign_keys")).fetchone()
                jm_row = conn.execute(text("PRAGMA journal_mode")).fetchone()
                fk_enabled = bool(fk_row[0]) if fk_row else None
                journal_mode = jm_row[0] if jm_row else None
    except Exception:
        logger.warning("[HEALTH] Could not query SQLite PRAGMA state", exc_info=True)

    return {
        "current_revision": current_rev,
        "head_revision": head_rev,
        "up_to_date": bool(current_rev) and current_rev == head_rev,
        "foreign_keys_enabled": fk_enabled,
        "journal_mode": journal_mode,
    }


# ============================================================================
# Cache management endpoints (unchanged)
# ============================================================================
@router.post("/api/cache/invalidate", tags=["Cache"])
async def invalidate_cache(prefix: Optional[str] = None):
    """Invalidate cached data. If prefix is provided, only invalidate matching keys."""
    cache = get_cache()
    if prefix:
        count = cache.invalidate_prefix(prefix)
        return {"message": f"Invalidated {count} cache entries with prefix '{prefix}'"}
    else:
        count = cache.clear()
        return {"message": f"Cleared entire cache ({count} entries)"}


@router.get("/api/cache/stats", tags=["Cache"])
async def cache_stats():
    """Get cache statistics."""
    cache = get_cache()
    return cache.stats()
