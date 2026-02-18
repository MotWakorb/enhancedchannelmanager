"""
Stats router — channel stats, enhanced stats, and popularity endpoints.

Extracted from main.py (Phase 3 of v0.13.0 backend refactor).
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from bandwidth_tracker import BandwidthTracker
from database import get_session
from dispatcharr_client import get_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Stats"])


# =============================================================================
# Stats & Monitoring
# =============================================================================


@router.get("/api/stats/channels")
async def get_channel_stats():
    """Get status of all active channels.

    Returns summary including active channels, client counts, bitrates, speeds, etc.
    """
    client = get_client()
    try:
        return await client.get_channel_stats()
    except Exception as e:
        logger.error(f"Failed to get channel stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/stats/channels/{channel_id}")
async def get_channel_stats_detail(channel_id: int):
    """Get detailed stats for a specific channel.

    Includes per-client information, buffer status, codec details, etc.
    """
    client = get_client()
    try:
        return await client.get_channel_stats_detail(channel_id)
    except Exception as e:
        logger.error(f"Failed to get channel stats for {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/stats/activity")
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
    client = get_client()
    try:
        return await client.get_system_events(
            limit=min(limit, 1000),
            offset=offset,
            event_type=event_type,
        )
    except Exception as e:
        logger.error(f"Failed to get system events: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/stats/channels/{channel_id}/stop")
async def stop_channel(channel_id: str):
    """Stop a channel and release all associated resources."""
    client = get_client()
    try:
        return await client.stop_channel(channel_id)
    except Exception as e:
        logger.error(f"Failed to stop channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/stats/channels/{channel_id}/stop-client")
async def stop_client(channel_id: str):
    """Stop a specific client connection."""
    client = get_client()
    try:
        return await client.stop_client(channel_id)
    except Exception as e:
        logger.error(f"Failed to stop client for channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/stats/bandwidth")
async def get_bandwidth_stats():
    """Get bandwidth usage summary for all time periods."""
    try:
        return BandwidthTracker.get_bandwidth_summary()
    except Exception as e:
        logger.error(f"Failed to get bandwidth stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/stats/top-watched")
async def get_top_watched_channels(limit: int = 10, sort_by: str = "views"):
    """Get the top watched channels by watch count or watch time."""
    try:
        return BandwidthTracker.get_top_watched_channels(limit=limit, sort_by=sort_by)
    except Exception as e:
        logger.error(f"Failed to get top watched channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Enhanced Statistics Endpoints (v0.11.0)
# =============================================================================


@router.get("/api/stats/unique-viewers")
async def get_unique_viewers_summary(days: int = 7):
    """Get unique viewer statistics for the specified period."""
    try:
        return BandwidthTracker.get_unique_viewers_summary(days=days)
    except Exception as e:
        logger.error(f"Failed to get unique viewers summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/stats/channel-bandwidth")
async def get_channel_bandwidth_stats(days: int = 7, limit: int = 20, sort_by: str = "bytes"):
    """Get per-channel bandwidth statistics."""
    try:
        return BandwidthTracker.get_channel_bandwidth_stats(days=days, limit=limit, sort_by=sort_by)
    except Exception as e:
        logger.error(f"Failed to get channel bandwidth stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/stats/unique-viewers-by-channel")
async def get_unique_viewers_by_channel(days: int = 7, limit: int = 20):
    """Get unique viewer counts per channel."""
    try:
        return BandwidthTracker.get_unique_viewers_by_channel(days=days, limit=limit)
    except Exception as e:
        logger.error(f"Failed to get unique viewers by channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/stats/watch-history")
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
        logger.error(f"Failed to get watch history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Popularity Endpoints (v0.11.0)
# =============================================================================


@router.get("/api/stats/popularity/rankings")
async def get_popularity_rankings(limit: int = 50, offset: int = 0):
    """Get channel popularity rankings."""
    try:
        from popularity_calculator import PopularityCalculator
        return PopularityCalculator.get_rankings(limit=limit, offset=offset)
    except Exception as e:
        logger.error(f"Failed to get popularity rankings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/stats/popularity/channel/{channel_id}")
async def get_channel_popularity(channel_id: str):
    """Get popularity score for a specific channel."""
    try:
        from popularity_calculator import PopularityCalculator
        result = PopularityCalculator.get_channel_score(channel_id)
        if not result:
            raise HTTPException(status_code=404, detail="Channel popularity score not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get channel popularity: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/stats/popularity/trending")
async def get_trending_channels(direction: str = "up", limit: int = 10):
    """Get channels that are trending up or down."""
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be 'up' or 'down'")
    try:
        from popularity_calculator import PopularityCalculator
        return PopularityCalculator.get_trending_channels(direction=direction, limit=limit)
    except Exception as e:
        logger.error(f"Failed to get trending channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/stats/popularity/calculate")
async def calculate_popularity_scores(
    period_days: int = 7,
):
    """
    Trigger popularity score calculation.

    Args:
        period_days: Number of days to consider for scoring
    """
    try:
        from popularity_calculator import calculate_popularity
        result = calculate_popularity(period_days=period_days)
        return result
    except Exception as e:
        logger.error(f"Failed to calculate popularity: {e}")
        raise HTTPException(status_code=500, detail=str(e))
