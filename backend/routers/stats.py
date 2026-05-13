"""
Stats router — channel stats, enhanced stats, and popularity endpoints.

Extracted from main.py (Phase 3 of v0.13.0 backend refactor).
"""
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth.dependencies import get_current_user
from auth.settings import get_auth_settings
from bandwidth_tracker import BandwidthTracker
from database import get_session
from dispatcharr_client import get_client
from models import SessionTelemetry, UniqueClientConnection, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stats", tags=["Stats"])


# =============================================================================
# GH-62 watch-time read API (bd-skqln.5) — module-level helpers
# =============================================================================


async def get_watch_time_caller(
    request: Request,
    session: Session = Depends(get_session),
) -> Optional[User]:
    """Resolve the calling user for watch-time endpoints.

    Returns the authenticated ``User`` when global auth is enabled, ``None``
    when auth is disabled (the global auth middleware has already let the
    request through in that posture). The watch-time handlers use this to
    enforce the "non-admin can only query own user_id" rule per bd-skqln.5
    acceptance.

    Defined as a module-level function (rather than reusing
    ``auth.RequireAuthIfEnabled``) so tests can override it via
    ``app.dependency_overrides[get_watch_time_caller]`` — the standard
    FastAPI test seam.
    """
    settings = get_auth_settings()
    if not settings.require_auth or not settings.setup_complete:
        return None
    return await get_current_user(request, session)


def _parse_iso_utc(value: str, *, param: str) -> int:
    """Parse an ISO-8601 UTC string into unix-epoch milliseconds.

    Accepts both ``...Z`` and ``...+00:00`` forms. Rejects anything else with
    HTTP 400 so timezone-naive inputs cannot silently slip through and be
    interpreted as local time.
    """
    raw = value.strip()
    # Python's fromisoformat doesn't accept the 'Z' suffix until 3.11; accept
    # either form by normalising 'Z' -> '+00:00'.
    normalised = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"{param} must be an ISO-8601 UTC timestamp (got {value!r})",
        )
    if dt.tzinfo is None:
        raise HTTPException(
            status_code=400,
            detail=f"{param} must include a timezone (Z or +00:00)",
        )
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _ms_to_iso_z(ms: Optional[int]) -> Optional[str]:
    """Convert ms-since-epoch -> ISO-8601 UTC string with trailing Z."""
    if ms is None:
        return None
    return (
        datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _distinct_poll_subquery(session: Session):
    """DISTINCT (user_id, channel_id, observed_at) collapse subquery.

    Returns one row per (user, channel, poll-tick) tuple with the poll
    interval. Collapses multi-client overcount: a user with N concurrent
    sessions on the same channel in one poll contributes ONE poll interval,
    not N. Mirrors the ``channel_watch_stats_v`` view's collapse pattern
    (migration 0008) but adds ``user_id`` to the DISTINCT key so per-user
    aggregations get the same guarantee.

    ``MAX(poll_interval_ms)`` inside the GROUP BY is defensive: in the rare
    case where two clients report different poll intervals for the same
    (user, channel, observed_at) tuple, take the longer one — never
    overcount.
    """
    return (
        session.query(
            SessionTelemetry.user_id.label("user_id"),
            SessionTelemetry.channel_id.label("channel_id"),
            SessionTelemetry.observed_at.label("observed_at"),
            func.max(SessionTelemetry.poll_interval_ms).label("poll_interval_ms"),
        )
        .group_by(
            SessionTelemetry.user_id,
            SessionTelemetry.channel_id,
            SessionTelemetry.observed_at,
        )
        .subquery()
    )


def _build_envelope(data, *, from_ms, to_ms, group_by):
    """Standard response envelope for watch-time endpoints.

    Shape: ``{data: [...], meta: {...}, pagination: null}``. The pagination
    slot is reserved for a future page-cursor extension (bd-skqln.10 perf
    work may add it); for now both endpoints return all rows in-range and
    leave the slot ``null`` so clients can be coded against the full shape
    upfront.
    """
    return {
        "data": data,
        "meta": {
            "from_iso": _ms_to_iso_z(from_ms),
            "to_iso": _ms_to_iso_z(to_ms),
            "group_by": group_by,
            "total_rows": len(data),
        },
        "pagination": None,
    }


def _channel_name_or_fallback(channel_id: str, name_map: dict) -> str:
    """Look up channel name from UniqueClientConnection map, else synth fallback.

    Matches the skqln.3 step (d) precedent for popularity_calculator:
    side-load from ``UniqueClientConnection.channel_name``, fall back to
    ``"Channel <first-8-chars>..."`` when the connection table has no row
    yet (e.g., a brand-new channel observed only by ``session_telemetry``).
    """
    name = name_map.get(channel_id)
    if name:
        return name
    return f"Channel {channel_id[:8]}..."


# =============================================================================
# Stats & Monitoring
# =============================================================================


@router.get("/channels")
async def get_channel_stats():
    """Get status of all active channels.

    Returns summary including active channels, client counts, bitrates, speeds, etc.
    Enriches client data with usernames resolved from Dispatcharr user accounts.
    """
    logger.debug("[STATS] GET /api/stats/channels")
    client = get_client()
    try:
        start = time.time()
        result = await client.get_channel_stats()
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STATS] get_channel_stats completed in %.1fms", elapsed_ms)

        # Resolve user_id → username for connected clients
        has_user_ids = any(
            c.get("user_id")
            for ch in result.get("channels", [])
            for c in ch.get("clients", [])
        )
        if has_user_ids:
            try:
                users = await client.get_users()
                user_map = {str(u["id"]): u.get("username", "") for u in users}
                for ch in result.get("channels", []):
                    for c in ch.get("clients", []):
                        uid = c.get("user_id")
                        if uid and str(uid) in user_map:
                            c["username"] = user_map[str(uid)]
            except Exception as e:
                logger.warning("[STATS] Failed to resolve usernames: %s", e)

        return result
    except Exception as e:
        logger.exception("[STATS] Failed to get channel stats")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/channels/{channel_id}")
async def get_channel_stats_detail(channel_id: int):
    """Get detailed stats for a specific channel.

    Includes per-client information, buffer status, codec details, etc.
    """
    logger.debug("[STATS] GET /api/stats/channels/%s", channel_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_channel_stats_detail(channel_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STATS] get_channel_stats_detail for %s completed in %.1fms", channel_id, elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[STATS] Failed to get channel stats for %s", channel_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/activity")
async def get_system_events(
    limit: int = 100,
    offset: int = 0,
    event_type: Optional[str] = None,
):
    """Get recent system events (channel start/stop, buffering, client connections).

    Args:
        limit: Number of events to return (default 100, max 1000)
        offset: Pagination offset
        event_type: Optional filter by event type
    """
    logger.debug("[STATS] GET /api/stats/activity - limit=%s offset=%s event_type=%s", limit, offset, event_type)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_system_events(
            limit=min(limit, 1000),
            offset=offset,
            event_type=event_type,
        )
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STATS] get_system_events completed in %.1fms", elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[STATS] Failed to get system events")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/channels/{channel_id}/stop")
async def stop_channel(channel_id: str):
    """Stop a channel and release all associated resources."""
    logger.debug("[STATS] POST /api/stats/channels/%s/stop", channel_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.stop_channel(channel_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STATS] stop_channel %s completed in %.1fms", channel_id, elapsed_ms)
        logger.info("[STATS] Stopped channel id=%s", channel_id)
        return result
    except Exception as e:
        logger.exception("[STATS] Failed to stop channel %s", channel_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/channels/{channel_id}/stop-client")
async def stop_client(channel_id: str):
    """Stop a specific client connection."""
    logger.debug("[STATS] POST /api/stats/channels/%s/stop-client", channel_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.stop_client(channel_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STATS] stop_client for channel %s completed in %.1fms", channel_id, elapsed_ms)
        logger.info("[STATS] Stopped client for channel id=%s", channel_id)
        return result
    except Exception as e:
        logger.exception("[STATS] Failed to stop client for channel %s", channel_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/bandwidth")
async def get_bandwidth_stats():
    """Get bandwidth usage summary for all time periods."""
    logger.debug("[STATS] GET /api/stats/bandwidth")
    try:
        return BandwidthTracker.get_bandwidth_summary()
    except Exception as e:
        logger.exception("[STATS] Failed to get bandwidth stats")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/top-watched")
async def get_top_watched_channels(limit: int = 10, sort_by: str = "views"):
    """Get the top watched channels by watch count or watch time."""
    logger.debug("[STATS] GET /api/stats/top-watched - limit=%s sort_by=%s", limit, sort_by)
    try:
        return BandwidthTracker.get_top_watched_channels(limit=limit, sort_by=sort_by)
    except Exception as e:
        logger.exception("[STATS] Failed to get top watched channels")
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# Enhanced Statistics Endpoints (v0.11.0)
# =============================================================================


@router.get("/unique-viewers")
async def get_unique_viewers_summary(days: int = 7):
    """Get unique viewer statistics for the specified period."""
    logger.debug("[STATS] GET /api/stats/unique-viewers - days=%s", days)
    try:
        return BandwidthTracker.get_unique_viewers_summary(days=days)
    except Exception as e:
        logger.exception("[STATS] Failed to get unique viewers summary")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/channel-bandwidth")
async def get_channel_bandwidth_stats(days: int = 7, limit: int = 20, sort_by: str = "bytes"):
    """Get per-channel bandwidth statistics."""
    logger.debug("[STATS] GET /api/stats/channel-bandwidth - days=%s limit=%s sort_by=%s", days, limit, sort_by)
    try:
        return BandwidthTracker.get_channel_bandwidth_stats(days=days, limit=limit, sort_by=sort_by)
    except Exception as e:
        logger.exception("[STATS] Failed to get channel bandwidth stats")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/unique-viewers-by-channel")
async def get_unique_viewers_by_channel(days: int = 7, limit: int = 20):
    """Get unique viewer counts per channel."""
    logger.debug("[STATS] GET /api/stats/unique-viewers-by-channel - days=%s limit=%s", days, limit)
    try:
        return BandwidthTracker.get_unique_viewers_by_channel(days=days, limit=limit)
    except Exception as e:
        logger.exception("[STATS] Failed to get unique viewers by channel")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/watch-history")
async def get_watch_history(
    page: int = 1,
    page_size: int = 50,
    channel_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    days: Optional[int] = None,
):
    """
    Get watch history log - all channel viewing sessions.

    Args:
        page: Page number (1-indexed)
        page_size: Number of records per page (max 100)
        channel_id: Filter by specific channel
        ip_address: Filter by specific IP address
        days: Filter to last N days (None = all time)
    """
    logger.debug("[STATS] GET /api/stats/watch-history - page=%s page_size=%s channel_id=%s", page, page_size, channel_id)
    try:
        from models import UniqueClientConnection
        from sqlalchemy import func, desc
        from datetime import date, timedelta

        session = get_session()
        try:
            # Build query
            query = session.query(UniqueClientConnection)

            # Apply filters
            if channel_id:
                query = query.filter(UniqueClientConnection.channel_id == channel_id)
            if ip_address:
                query = query.filter(UniqueClientConnection.ip_address == ip_address)
            if days:
                cutoff_date = date.today() - timedelta(days=days)
                query = query.filter(UniqueClientConnection.date >= cutoff_date)

            # Get total count
            total = query.count()

            # Limit page_size
            page_size = min(page_size, 100)

            # Apply pagination and ordering (most recent first)
            offset = (page - 1) * page_size
            records = query.order_by(
                desc(UniqueClientConnection.connected_at)
            ).offset(offset).limit(page_size).all()

            # Get summary stats
            summary_query = session.query(
                func.count(func.distinct(UniqueClientConnection.channel_id)).label("unique_channels"),
                func.count(func.distinct(UniqueClientConnection.ip_address)).label("unique_ips"),
                func.sum(UniqueClientConnection.watch_seconds).label("total_watch_seconds"),
            )
            if channel_id:
                summary_query = summary_query.filter(UniqueClientConnection.channel_id == channel_id)
            if ip_address:
                summary_query = summary_query.filter(UniqueClientConnection.ip_address == ip_address)
            if days:
                summary_query = summary_query.filter(UniqueClientConnection.date >= cutoff_date)

            summary = summary_query.first()

            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if total > 0 else 1,
                "summary": {
                    "unique_channels": summary.unique_channels or 0,
                    "unique_ips": summary.unique_ips or 0,
                    "total_watch_seconds": summary.total_watch_seconds or 0,
                },
                "history": [
                    {
                        "id": r.id,
                        "channel_id": r.channel_id,
                        "channel_name": r.channel_name,
                        "ip_address": r.ip_address,
                        "user_id": r.user_id,
                        "username": r.username,
                        "date": r.date.isoformat() if r.date else None,
                        "connected_at": r.connected_at.isoformat() + "Z" if r.connected_at else None,
                        "disconnected_at": r.disconnected_at.isoformat() + "Z" if r.disconnected_at else None,
                        "watch_seconds": r.watch_seconds,
                    }
                    for r in records
                ],
            }
        finally:
            session.close()
    except Exception as e:
        logger.exception("[STATS] Failed to get watch history")
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# Popularity Endpoints (v0.11.0)
# =============================================================================


@router.get("/popularity/rankings")
async def get_popularity_rankings(limit: int = 50, offset: int = 0):
    """Get channel popularity rankings."""
    logger.debug("[STATS] GET /api/stats/popularity/rankings - limit=%s offset=%s", limit, offset)
    try:
        from popularity_calculator import PopularityCalculator
        return PopularityCalculator.get_rankings(limit=limit, offset=offset)
    except Exception as e:
        logger.exception("[STATS] Failed to get popularity rankings")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/popularity/channel/{channel_id}")
async def get_channel_popularity(channel_id: str):
    """Get popularity score for a specific channel."""
    logger.debug("[STATS] GET /api/stats/popularity/channel/%s", channel_id)
    try:
        from popularity_calculator import PopularityCalculator
        result = PopularityCalculator.get_channel_score(channel_id)
        if not result:
            raise HTTPException(status_code=404, detail="Channel popularity score not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[STATS] Failed to get channel popularity")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/popularity/trending")
async def get_trending_channels(direction: str = "up", limit: int = 10):
    """Get channels that are trending up or down."""
    logger.debug("[STATS] GET /api/stats/popularity/trending - direction=%s limit=%s", direction, limit)
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be 'up' or 'down'")
    try:
        from popularity_calculator import PopularityCalculator
        return PopularityCalculator.get_trending_channels(direction=direction, limit=limit)
    except Exception as e:
        logger.exception("[STATS] Failed to get trending channels")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/popularity/calculate")
async def calculate_popularity_scores(
    period_days: int = 7,
):
    """
    Trigger popularity score calculation.

    Args:
        period_days: Number of days to consider for scoring
    """
    logger.debug("[STATS] POST /api/stats/popularity/calculate - period_days=%s", period_days)
    try:
        from popularity_calculator import calculate_popularity
        result = calculate_popularity(period_days=period_days)
        logger.info("[STATS] Completed popularity calculation for %s days", period_days)
        return result
    except Exception as e:
        logger.exception("[STATS] Failed to calculate popularity")
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# GH-62 watch-time read API (bd-skqln.5)
# =============================================================================
#
# Both endpoints read directly from ``session_telemetry`` (the per-poll fact
# table from bd-skqln.2 / .3). The ``channel_watch_stats_v`` view from
# bd-skqln.3 step (b) aggregates per-channel only and does NOT expose
# ``user_id``, so it cannot satisfy these per-user queries — using
# ``session_telemetry`` directly with a DISTINCT-by-(user, channel,
# observed_at) subquery is the honest fit.
#
# Performance: relies on the ``idx_session_telemetry_user_observed``
# composite index (migration 0006) for range scans by user_id, and the
# bare ``idx_session_telemetry_observed_at`` index when ``user_id`` is not
# filtered. p95 < 800ms / p99 < 2s at 3-6 months of data per
# bd-skqln.5 acceptance — pytest-benchmark gate lands separately in
# bd-skqln.10.


_VALID_GROUP_BY = {"total", "day"}


@router.get("/watch-time")
async def get_watch_time_by_user(
    db: Session = Depends(get_session),
    caller: Optional[User] = Depends(get_watch_time_caller),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    user_id: Optional[int] = None,
    group_by: str = "total",
):
    """List per-user watch-time totals (GH-62).

    Query parameters (all snake_case in the URL):

    * ``from`` — ISO-8601 UTC range start (inclusive). Optional.
    * ``to`` — ISO-8601 UTC range end (exclusive). Optional.
    * ``user_id`` — filter to a single user. Optional. **Admin-only
      endpoint**: non-admin callers receive 403.
    * ``group_by`` — ``total`` (default) or ``day``. ``total`` returns one
      row per user. ``day`` returns one row per (user, UTC-day) pair.

    Response envelope (per bd-skqln.5):
        ``{data: [...], meta: {from_iso, to_iso, group_by, total_rows},
           pagination: null}``

    Row shape for ``group_by=total``:
        ``{user_id, username, total_watch_seconds, last_watched}``

    Row shape for ``group_by=day``:
        ``{user_id, username, day, watch_seconds}``
    """
    logger.debug(
        "[STATS] GET /api/stats/watch-time from=%s to=%s user_id=%s group_by=%s",
        from_, to, user_id, group_by,
    )
    if group_by not in _VALID_GROUP_BY:
        raise HTTPException(
            status_code=400,
            detail=f"group_by must be one of {sorted(_VALID_GROUP_BY)}",
        )

    # Auth enforcement: watch-time stats are admin-only. Non-admin callers
    # are blocked regardless of which user_id they query (or whether they
    # omit the filter). PO directive 2026-05-13: non-admins do not see stats.
    # (caller is None when global auth is disabled — auth-disabled mode is
    # the test-default posture and operator-only deployments; treat as
    # admin-equivalent there.)
    if caller is not None and not caller.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Watch-time stats are admin-only",
        )

    from_ms = _parse_iso_utc(from_, param="from") if from_ else None
    to_ms = _parse_iso_utc(to, param="to") if to else None

    # Empty-range short-circuit: if from == to, the inclusive/exclusive
    # convention makes the window empty. Skip the DB round-trip.
    if from_ms is not None and to_ms is not None and from_ms >= to_ms:
        return _build_envelope([], from_ms=from_ms, to_ms=to_ms, group_by=group_by)

    try:
        distinct = _distinct_poll_subquery(db)
        # Only count rows whose user_id is non-NULL: NULL user_id == anonymous
        # poll (no logged-in user). Watch-time-by-user has no meaningful row
        # for the anonymous bucket — drop them rather than emit a NULL-user
        # row.
        base_q = db.query(distinct).filter(distinct.c.user_id.isnot(None))
        if from_ms is not None:
            base_q = base_q.filter(distinct.c.observed_at >= from_ms)
        if to_ms is not None:
            base_q = base_q.filter(distinct.c.observed_at < to_ms)
        if user_id is not None:
            base_q = base_q.filter(distinct.c.user_id == user_id)
        inner = base_q.subquery()

        if group_by == "total":
            rows = (
                db.query(
                    inner.c.user_id,
                    func.sum(inner.c.poll_interval_ms).label("total_ms"),
                    func.max(inner.c.observed_at).label("last_observed_at"),
                )
                .group_by(inner.c.user_id)
                .all()
            )
            user_ids = [r.user_id for r in rows]
            usernames = _load_usernames(db, user_ids)
            data = [
                {
                    "user_id": r.user_id,
                    "username": usernames.get(r.user_id),
                    "total_watch_seconds": int((r.total_ms or 0) // 1000),
                    "last_watched": _ms_to_iso_z(r.last_observed_at),
                }
                for r in rows
            ]
            # Stable ordering: highest total first, then user_id ASC.
            data.sort(key=lambda d: (-d["total_watch_seconds"], d["user_id"]))
        else:  # group_by == "day"
            # SQLite ``date()`` accepts unixepoch seconds — divide ms by 1000.
            day_expr = func.date(inner.c.observed_at / 1000, "unixepoch").label("day")
            rows = (
                db.query(
                    inner.c.user_id,
                    day_expr,
                    func.sum(inner.c.poll_interval_ms).label("total_ms"),
                )
                .group_by(inner.c.user_id, day_expr)
                .all()
            )
            user_ids = [r.user_id for r in rows]
            usernames = _load_usernames(db, user_ids)
            data = [
                {
                    "user_id": r.user_id,
                    "username": usernames.get(r.user_id),
                    "day": r.day,
                    "watch_seconds": int((r.total_ms or 0) // 1000),
                }
                for r in rows
            ]
            data.sort(key=lambda d: (d["user_id"], d["day"]))

        return _build_envelope(data, from_ms=from_ms, to_ms=to_ms, group_by=group_by)
    except HTTPException:
        raise
    except Exception:
        logger.exception("[STATS] Failed to get watch-time totals")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/watch-time/{user_id}")
async def get_watch_time_for_user(
    user_id: int,
    db: Session = Depends(get_session),
    caller: Optional[User] = Depends(get_watch_time_caller),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
):
    """Per-user breakdown of watch time by channel (GH-62).

    Query parameters:

    * ``from`` — ISO-8601 UTC range start (inclusive). Optional.
    * ``to`` — ISO-8601 UTC range end (exclusive). Optional.

    Response envelope (per bd-skqln.5):
        ``{data: [...], meta: {from_iso, to_iso, group_by, total_rows},
           pagination: null}``

    Row shape:
        ``{channel_id, channel_name, total_watch_seconds, session_count,
           last_watched}``

    ``channel_name`` is side-loaded from
    ``UniqueClientConnection.channel_name`` (the skqln.3 step (d)
    precedent) — falls back to ``"Channel <first-8-chars-of-uuid>..."``
    when no connection row carries the name yet (brand-new channels first
    observed only via ``session_telemetry``).
    """
    logger.debug(
        "[STATS] GET /api/stats/watch-time/%s from=%s to=%s",
        user_id, from_, to,
    )

    # Auth enforcement: watch-time stats are admin-only. PO directive
    # 2026-05-13: non-admins do not see stats — including their own.
    if caller is not None and not caller.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Watch-time stats are admin-only",
        )

    from_ms = _parse_iso_utc(from_, param="from") if from_ else None
    to_ms = _parse_iso_utc(to, param="to") if to else None
    if from_ms is not None and to_ms is not None and from_ms >= to_ms:
        return _build_envelope([], from_ms=from_ms, to_ms=to_ms, group_by="channel")

    try:
        distinct = _distinct_poll_subquery(db)
        base_q = db.query(distinct).filter(distinct.c.user_id == user_id)
        if from_ms is not None:
            base_q = base_q.filter(distinct.c.observed_at >= from_ms)
        if to_ms is not None:
            base_q = base_q.filter(distinct.c.observed_at < to_ms)
        inner = base_q.subquery()

        # session_count: count distinct session_ids per channel (informational
        # only — not the legacy ``watch_count`` state-transition counter,
        # which is not derivable from per-poll rows; see migration 0008
        # docstring).
        sess_q = (
            db.query(
                SessionTelemetry.channel_id.label("channel_id"),
                func.count(func.distinct(SessionTelemetry.session_id)).label(
                    "session_count"
                ),
            )
            .filter(SessionTelemetry.user_id == user_id)
        )
        if from_ms is not None:
            sess_q = sess_q.filter(SessionTelemetry.observed_at >= from_ms)
        if to_ms is not None:
            sess_q = sess_q.filter(SessionTelemetry.observed_at < to_ms)
        sess_q = sess_q.group_by(SessionTelemetry.channel_id)
        session_counts = {r.channel_id: int(r.session_count) for r in sess_q.all()}

        agg_rows = (
            db.query(
                inner.c.channel_id,
                func.sum(inner.c.poll_interval_ms).label("total_ms"),
                func.max(inner.c.observed_at).label("last_observed_at"),
            )
            .group_by(inner.c.channel_id)
            .all()
        )

        # Side-load channel names from UniqueClientConnection. One query per
        # request — the in-range channel_id set is bounded by the user's
        # viewing footprint (typically O(10) channels), so an IN-list lookup
        # is fine without a join.
        channel_ids = [r.channel_id for r in agg_rows]
        name_map = _load_channel_names(db, channel_ids)

        data = [
            {
                "channel_id": r.channel_id,
                "channel_name": _channel_name_or_fallback(r.channel_id, name_map),
                "total_watch_seconds": int((r.total_ms or 0) // 1000),
                "session_count": session_counts.get(r.channel_id, 0),
                "last_watched": _ms_to_iso_z(r.last_observed_at),
            }
            for r in agg_rows
        ]
        # Stable ordering: highest total first, then channel_id ASC.
        data.sort(key=lambda d: (-d["total_watch_seconds"], d["channel_id"]))
        return _build_envelope(data, from_ms=from_ms, to_ms=to_ms, group_by="channel")
    except HTTPException:
        raise
    except Exception:
        logger.exception("[STATS] Failed to get per-user watch-time breakdown")
        raise HTTPException(status_code=500, detail="Internal server error")


def _load_usernames(db: Session, user_ids):
    """Bulk-resolve ``user_id -> username`` for the given ids. NULL-safe."""
    ids = [uid for uid in user_ids if uid is not None]
    if not ids:
        return {}
    rows = db.query(User.id, User.username).filter(User.id.in_(ids)).all()
    return {r.id: r.username for r in rows}


def _load_channel_names(db: Session, channel_ids):
    """Bulk-resolve ``channel_id -> channel_name`` via UniqueClientConnection.

    Picks an arbitrary name per channel — the connection table stores one
    row per (ip, channel) viewing session and ECM keeps the channel name
    cached in every row, so they all agree under normal operation. ``MAX``
    is the cheap, stable picker.
    """
    if not channel_ids:
        return {}
    rows = (
        db.query(
            UniqueClientConnection.channel_id,
            func.max(UniqueClientConnection.channel_name).label("channel_name"),
        )
        .filter(UniqueClientConnection.channel_id.in_(channel_ids))
        .group_by(UniqueClientConnection.channel_id)
        .all()
    )
    return {r.channel_id: r.channel_name for r in rows}
