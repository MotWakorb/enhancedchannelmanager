"""
Stats router — channel stats, enhanced stats, and popularity endpoints.

Extracted from main.py (Phase 3 of v0.13.0 backend refactor).
"""
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException

from bandwidth_tracker import BandwidthTracker
from database import get_session
from dispatcharr_client import get_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stats", tags=["Stats"])


# =============================================================================
# Stats & Monitoring
# =============================================================================


@router.get("/channels")
async def get_channel_stats():
    """Get status of all active channels.

    Returns summary including active channels, client counts, bitrates, speeds, etc.
    """
    logger.debug("[STATS] GET /api/stats/channels")
    client = get_client()
    try:
        start = time.time()
        result = await client.get_channel_stats()
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STATS] get_channel_stats completed in %.1fms", elapsed_ms)
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
