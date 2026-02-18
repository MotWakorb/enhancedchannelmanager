"""
Stream stats router — stream probe stats, probe operations, sort, dismiss/clear.

Extracted from main.py (Phase 3 of v0.13.0 backend refactor).
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import get_settings
from database import get_session
from dispatcharr_client import get_client
from stream_prober import StreamProber, get_prober

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stream-stats", tags=["Stream Stats"])


# Pydantic models co-located with the router

class ChannelSortInput(BaseModel):
    channel_id: int
    stream_ids: list[int]


class ComputeSortRequest(BaseModel):
    channels: list[ChannelSortInput]
    mode: str = "smart"  # "smart", "resolution", "bitrate", "framerate", "m3u_priority", "audio_channels"


class ChannelSortResult(BaseModel):
    channel_id: int
    sorted_stream_ids: list[int]
    changed: bool


class ComputeSortResponse(BaseModel):
    results: list[ChannelSortResult]


class RemoveStruckOutRequest(BaseModel):
    stream_ids: list[int]


class BulkProbeRequest(BaseModel):
    stream_ids: list[int]


class ProbeAllRequest(BaseModel):
    """Request for probe all streams endpoint with optional group filtering."""
    channel_groups: list[str] = []  # Empty list means all groups
    skip_m3u_refresh: bool = False  # Skip M3U refresh for on-demand probes
    stream_ids: list[int] = []  # Optional list of specific stream IDs to probe (empty = all)


class DismissStatsRequest(BaseModel):
    """Request model for dismissing stream probe stats."""
    stream_ids: list[int]


class ClearStatsRequest(BaseModel):
    """Request model for clearing stream probe stats."""
    stream_ids: list[int]


# =============================================================================
# Endpoints
# =============================================================================


@router.get("")
async def get_all_stream_stats():
    """Get all stream probe statistics."""
    logger.debug("[STREAM-STATS] GET /api/stream-stats")
    try:
        return StreamProber.get_all_stats()
    except Exception as e:
        logger.exception("[STREAM-STATS] Failed to get stream stats: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary")
async def get_stream_stats_summary():
    """Get summary of stream probe statistics."""
    logger.debug("[STREAM-STATS] GET /api/stream-stats/summary")
    try:
        return StreamProber.get_stats_summary()
    except Exception as e:
        logger.exception("[STREAM-STATS] Failed to get stream stats summary: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# NOTE: These routes MUST be defined BEFORE /{stream_id} to avoid path parameter matching

@router.get("/struck-out")
async def get_struck_out_streams():
    """Get streams that have exceeded the strike threshold."""
    logger.debug("[STREAM-STATS] GET /api/stream-stats/struck-out")
    from models import StreamStats

    settings = get_settings()
    threshold = settings.strike_threshold

    if threshold <= 0:
        return {"streams": [], "threshold": 0, "enabled": False}

    session = get_session()
    try:
        struck = session.query(StreamStats).filter(
            StreamStats.consecutive_failures >= threshold
        ).all()

        if not struck:
            return {"streams": [], "threshold": threshold, "enabled": True}

        # Build a set of struck stream IDs for lookup
        struck_ids = {s.stream_id for s in struck}

        # Find which channels contain these streams (paginated)
        client = get_client()
        start = time.time()
        all_channels = []
        page = 1
        while True:
            result = await client.get_channels(page=page, page_size=100)
            page_channels = result.get("results", [])
            all_channels.extend(page_channels)
            if len(all_channels) >= result.get("count", 0) or not page_channels:
                break
            page += 1
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STREAM-STATS] Fetched %s channels for struck-out lookup in %.1fms", len(all_channels), elapsed_ms)

        stream_channels: dict[int, list[dict]] = {sid: [] for sid in struck_ids}

        for ch in all_channels:
            ch_streams = ch.get("streams", [])
            for sid in struck_ids:
                if sid in ch_streams:
                    stream_channels[sid].append({
                        "id": ch["id"],
                        "name": ch.get("name", "Unknown"),
                    })

        result = []
        for s in struck:
            d = s.to_dict()
            d["channels"] = stream_channels.get(s.stream_id, [])
            result.append(d)

        return {"streams": result, "threshold": threshold, "enabled": True}
    except Exception as e:
        logger.exception("[STREAM-STATS] Failed to get struck-out streams: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.post("/struck-out/remove")
async def remove_struck_out_streams(request: RemoveStruckOutRequest):
    """Remove struck-out streams from all channels they belong to."""
    logger.debug("[STREAM-STATS] POST /api/stream-stats/struck-out/remove - %d streams", len(request.stream_ids))
    from models import StreamStats

    client = get_client()
    removed_count = 0

    try:
        start = time.time()
        all_channels = []
        page = 1
        while True:
            result = await client.get_channels(page=page, page_size=100)
            page_channels = result.get("results", [])
            all_channels.extend(page_channels)
            if len(all_channels) >= result.get("count", 0) or not page_channels:
                break
            page += 1

        for ch in all_channels:
            ch_streams = ch.get("streams", [])
            filtered = [sid for sid in ch_streams if sid not in request.stream_ids]
            if len(filtered) < len(ch_streams):
                removed_here = len(ch_streams) - len(filtered)
                await client.update_channel(ch["id"], {"streams": filtered})
                removed_count += removed_here
                logger.info("[STREAM-STATS] Removed %s struck-out streams from channel %s (%s)", removed_here, ch['id'], ch.get('name'))

        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STREAM-STATS] Removed struck-out streams from channels in %.1fms", elapsed_ms)

        # Reset consecutive_failures for removed streams
        session = get_session()
        try:
            for sid in request.stream_ids:
                stats = session.query(StreamStats).filter_by(stream_id=sid).first()
                if stats:
                    stats.consecutive_failures = 0
            session.commit()
        finally:
            session.close()

        return {
            "removed_from_channels": removed_count,
            "stream_ids": request.stream_ids,
        }
    except Exception as e:
        logger.exception("[STREAM-STATS] Failed to remove struck-out streams: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/compute-sort", response_model=ComputeSortResponse)
async def compute_sort(request: ComputeSortRequest):
    """Compute sort orders for streams without applying them.

    Uses server-side sort settings (priority, enabled criteria, M3U priorities,
    deprioritize_failed) as the single source of truth.
    Stream IDs come from the frontend (may have staged edits).
    """
    logger.debug("[STREAM-STATS-SORT] POST /api/stream-stats/compute-sort - mode=%s, %d channels", request.mode, len(request.channels))
    from stream_prober import smart_sort_streams, extract_m3u_account_id

    settings = get_settings()

    # Determine sort priority based on mode
    valid_criteria = {"resolution", "bitrate", "framerate", "m3u_priority", "audio_channels"}
    if request.mode == "smart":
        sort_priority = [c for c in settings.stream_sort_priority if settings.stream_sort_enabled.get(c, False)]
        sort_enabled = {c: True for c in sort_priority}
    elif request.mode in valid_criteria:
        sort_priority = [request.mode]
        sort_enabled = {request.mode: True}
    else:
        raise HTTPException(status_code=400, detail=f"Invalid sort mode: {request.mode}")

    # Collect all unique stream IDs across all channels
    all_stream_ids = list({sid for ch in request.channels for sid in ch.stream_ids})

    if not all_stream_ids:
        return ComputeSortResponse(results=[])

    # Fetch StreamStats objects from DB
    from models import StreamStats as StreamStatsModel
    session = get_session()
    try:
        BATCH_SIZE = 500
        stats_map = {}
        for i in range(0, len(all_stream_ids), BATCH_SIZE):
            batch = all_stream_ids[i:i + BATCH_SIZE]
            stats = session.query(StreamStatsModel).filter(
                StreamStatsModel.stream_id.in_(batch)
            ).all()
            for s in stats:
                stats_map[s.stream_id] = s
    finally:
        session.close()

    # Build M3U account map if needed
    stream_m3u_map = {}
    needs_m3u = "m3u_priority" in sort_priority
    if needs_m3u:
        try:
            client = get_client()
            start_fetch = time.time()
            streams_data = await client.get_streams_by_ids(all_stream_ids)
            elapsed_ms = (time.time() - start_fetch) * 1000
            logger.debug("[STREAM-STATS-SORT] Fetched %s streams for M3U priority in %.1fms", len(streams_data), elapsed_ms)
            for s in streams_data:
                stream_m3u_map[s["id"]] = extract_m3u_account_id(s.get("m3u_account"))
        except Exception as e:
            logger.warning("[STREAM-STATS-SORT] Failed to fetch M3U data: %s", e)

    # Sort each channel
    results = []
    for ch in request.channels:
        sorted_ids = smart_sort_streams(
            stream_ids=ch.stream_ids,
            stats_map=stats_map,
            stream_m3u_map=stream_m3u_map,
            stream_sort_priority=sort_priority,
            stream_sort_enabled=sort_enabled,
            m3u_account_priorities=settings.m3u_account_priorities,
            deprioritize_failed_streams=settings.deprioritize_failed_streams,
            channel_name=f"channel-{ch.channel_id}",
        )
        changed = sorted_ids != ch.stream_ids
        results.append(ChannelSortResult(
            channel_id=ch.channel_id,
            sorted_stream_ids=sorted_ids,
            changed=changed,
        ))

    return ComputeSortResponse(results=results)


@router.get("/dismissed")
async def get_dismissed_stream_stats():
    """Get list of dismissed stream IDs.

    Returns stream IDs that have been dismissed (failures acknowledged).
    Used by frontend to filter out dismissed streams from probe results display.
    """
    logger.debug("[STREAM-STATS] GET /api/stream-stats/dismissed")
    from models import StreamStats

    session = get_session()
    try:
        dismissed = session.query(StreamStats.stream_id).filter(
            StreamStats.dismissed_at.isnot(None)
        ).all()
        stream_ids = [s.stream_id for s in dismissed]
        return {"dismissed_stream_ids": stream_ids, "count": len(stream_ids)}
    except Exception as e:
        logger.exception("[STREAM-STATS] Failed to get dismissed stream stats: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.get("/{stream_id}")
async def get_stream_stats_by_id(stream_id: int):
    """Get probe stats for a specific stream."""
    logger.debug("[STREAM-STATS] GET /api/stream-stats/%s", stream_id)
    try:
        stats = StreamProber.get_stats_by_stream_id(stream_id)
        if not stats:
            raise HTTPException(status_code=404, detail="Stream stats not found")
        return stats
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[STREAM-STATS] Failed to get stream stats for %s: %s", stream_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# BulkStreamIdsRequest is in routers/streams.py
from routers.streams import BulkStreamIdsRequest


@router.post("/by-ids")
async def get_stream_stats_by_ids(request: BulkStreamIdsRequest):
    """Get probe stats for multiple streams by their IDs."""
    logger.debug("[STREAM-STATS] POST /api/stream-stats/by-ids - %d streams", len(request.stream_ids))
    try:
        return StreamProber.get_stats_by_stream_ids(request.stream_ids)
    except Exception as e:
        logger.exception("[STREAM-STATS] Failed to get stream stats by IDs: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# NOTE: /probe/bulk and /probe/all MUST be defined BEFORE /probe/{stream_id}
# to avoid the path parameter matching "bulk" or "all" as a stream_id
@router.post("/probe/bulk")
async def probe_bulk_streams(request: BulkProbeRequest):
    """Trigger on-demand probe for multiple streams."""
    logger.debug("[STREAM-STATS-PROBE] POST /api/stream-stats/probe/bulk - %d streams", len(request.stream_ids))

    prober = get_prober()
    logger.debug("[STREAM-STATS-PROBE] get_prober() returned: %s", prober is not None)

    if not prober:
        logger.error("[STREAM-STATS-PROBE] Stream prober not available - returning 503")
        raise HTTPException(status_code=503, detail="Stream prober not available")

    try:
        logger.debug("[STREAM-STATS-PROBE] Fetching all streams for bulk probe")
        all_streams = await prober._fetch_all_streams()
        logger.debug("[STREAM-STATS-PROBE] Fetched %s total streams", len(all_streams))

        stream_map = {s["id"]: s for s in all_streams}

        results = []
        for stream_id in request.stream_ids:
            stream = stream_map.get(stream_id)
            if stream:
                logger.debug("[STREAM-STATS-PROBE] Probing stream %s", stream_id)
                result = await prober.probe_stream(
                    stream_id, stream.get("url"), stream.get("name")
                )
                results.append(result)
                await asyncio.sleep(0.5)  # Rate limiting
            else:
                logger.warning("[STREAM-STATS-PROBE] Stream %s not found in stream list", stream_id)

        logger.info("[STREAM-STATS-PROBE] Bulk probe completed: %s streams probed", len(results))
        return {"probed": len(results), "results": results}
    except Exception as e:
        logger.exception("[STREAM-STATS-PROBE] Bulk probe failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/probe/all")
async def probe_all_streams_endpoint(request: ProbeAllRequest = ProbeAllRequest()):
    """Trigger probe for all streams (background task).

    Optionally filter by channel groups or specific stream IDs.
    If channel_groups is empty, probes all groups.
    If stream_ids is provided, probes only those specific streams (useful for re-probing failed streams).
    """
    logger.debug("[STREAM-STATS-PROBE] POST /api/stream-stats/probe/all - groups=%s, stream_ids=%d", request.channel_groups, len(request.stream_ids) if request.stream_ids else 0)

    prober = get_prober()
    logger.debug("[STREAM-STATS-PROBE] get_prober() returned: %s", prober is not None)

    if not prober:
        logger.error("[STREAM-STATS-PROBE] Stream prober not available - returning 503")
        raise HTTPException(status_code=503, detail="Stream prober not available")

    # If a probe is already "in progress" (possibly stuck), reset it first
    if prober._probing_in_progress:
        logger.warning("[STREAM-STATS-PROBE] Probe state shows in_progress - resetting before starting new probe")
        prober.force_reset_probe_state()

    async def run_probe_with_logging():
        """Wrapper to catch and log any errors from the probe task."""
        try:
            logger.info("[STREAM-STATS-PROBE] Background probe task starting...")
            await prober.probe_all_streams(
                channel_groups_override=request.channel_groups or None,
                skip_m3u_refresh=request.skip_m3u_refresh,
                stream_ids_filter=request.stream_ids or None
            )
            logger.info("[STREAM-STATS-PROBE] Background probe task completed successfully")
        except Exception as e:
            logger.exception("[STREAM-STATS-PROBE] Background probe task failed with error: %s", e)

    # Start background task with optional group filter
    stream_ids_msg = ", stream_ids: %s" % len(request.stream_ids) if request.stream_ids else ""
    logger.info("[STREAM-STATS-PROBE] Starting background probe task (groups: %s, skip_m3u_refresh: %s%s)", request.channel_groups or 'all', request.skip_m3u_refresh, stream_ids_msg)
    asyncio.create_task(run_probe_with_logging())
    logger.debug("[STREAM-STATS-PROBE] Background task created, returning response")
    return {"status": "started", "message": "Background probe started"}


@router.get("/probe/progress")
async def get_probe_progress():
    """Get current probe all streams progress."""
    logger.debug("[STREAM-STATS-PROBE] GET /api/stream-stats/probe/progress")
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.get_probe_progress()


@router.get("/probe/results")
async def get_probe_results():
    """Get detailed results of the last probe all streams operation."""
    logger.debug("[STREAM-STATS-PROBE] GET /api/stream-stats/probe/results")
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.get_probe_results()


@router.get("/probe/history")
async def get_probe_history():
    """Get probe run history (last 5 runs)."""
    logger.debug("[STREAM-STATS-PROBE] GET /api/stream-stats/probe/history")
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.get_probe_history()


@router.post("/probe/cancel")
async def cancel_probe():
    """Cancel an in-progress probe operation."""
    logger.debug("[STREAM-STATS-PROBE] POST /api/stream-stats/probe/cancel")
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.cancel_probe()


@router.post("/probe/reset")
async def reset_probe_state():
    """Force reset the probe state if it gets stuck."""
    logger.debug("[STREAM-STATS-PROBE] POST /api/stream-stats/probe/reset")
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.force_reset_probe_state()


@router.post("/dismiss")
async def dismiss_stream_stats(request: DismissStatsRequest):
    """Dismiss probe failures for the specified streams.

    Marks the streams as 'dismissed' so they don't appear in failed lists.
    The dismissal is cleared automatically when the stream is re-probed.
    """
    logger.debug("[STREAM-STATS] POST /api/stream-stats/dismiss - %d streams", len(request.stream_ids))
    from models import StreamStats

    if not request.stream_ids:
        raise HTTPException(status_code=400, detail="stream_ids is required")

    session = get_session()
    try:
        now = datetime.utcnow()
        updated = session.query(StreamStats).filter(
            StreamStats.stream_id.in_(request.stream_ids)
        ).update(
            {StreamStats.dismissed_at: now},
            synchronize_session=False
        )
        session.commit()
        logger.info("[STREAM-STATS] Dismissed %s stream stats for IDs: %s", updated, request.stream_ids)
        return {"dismissed": updated, "stream_ids": request.stream_ids}
    except Exception as e:
        session.rollback()
        logger.exception("[STREAM-STATS] Failed to dismiss stream stats: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.post("/clear")
async def clear_stream_stats(request: ClearStatsRequest):
    """Clear (delete) probe stats for the specified streams.

    Completely removes the probe history for these streams.
    They will appear as 'pending' (never probed) until re-probed.
    """
    logger.debug("[STREAM-STATS] POST /api/stream-stats/clear - %d streams", len(request.stream_ids))
    from models import StreamStats

    if not request.stream_ids:
        raise HTTPException(status_code=400, detail="stream_ids is required")

    session = get_session()
    try:
        deleted = session.query(StreamStats).filter(
            StreamStats.stream_id.in_(request.stream_ids)
        ).delete(synchronize_session=False)
        session.commit()
        logger.info("[STREAM-STATS] Cleared %s stream stats for IDs: %s", deleted, request.stream_ids)
        return {"cleared": deleted, "stream_ids": request.stream_ids}
    except Exception as e:
        session.rollback()
        logger.exception("[STREAM-STATS] Failed to clear stream stats: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.post("/clear-all")
async def clear_all_stream_stats():
    """Clear (delete) all probe stats for all streams.

    Completely removes all probe history. All streams will appear as
    'pending' (never probed) until re-probed.
    """
    logger.debug("[STREAM-STATS] POST /api/stream-stats/clear-all")
    from models import StreamStats

    session = get_session()
    try:
        deleted = session.query(StreamStats).delete(synchronize_session=False)
        session.commit()
        logger.info("[STREAM-STATS] Cleared all stream stats (%s records)", deleted)
        return {"cleared": deleted}
    except Exception as e:
        session.rollback()
        logger.exception("[STREAM-STATS] Failed to clear all stream stats: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.post("/probe/{stream_id}")
async def probe_single_stream(stream_id: int):
    """Trigger on-demand probe for a single stream."""
    logger.debug("[STREAM-STATS-PROBE] POST /api/stream-stats/probe/%s", stream_id)

    prober = get_prober()
    logger.debug("[STREAM-STATS-PROBE] get_prober() returned: %s", prober is not None)

    if not prober:
        logger.error("[STREAM-STATS-PROBE] Stream prober not available - returning 503")
        raise HTTPException(status_code=503, detail="Stream prober not available")

    try:
        # Get all streams and find the one we want
        logger.debug("[STREAM-STATS-PROBE] Fetching all streams to find stream %s", stream_id)
        all_streams = await prober._fetch_all_streams()
        stream = next((s for s in all_streams if s["id"] == stream_id), None)

        if not stream:
            logger.warning("[STREAM-STATS-PROBE] Stream %s not found", stream_id)
            raise HTTPException(status_code=404, detail="Stream not found")

        logger.debug("[STREAM-STATS-PROBE] Probing single stream %s", stream_id)
        result = await prober.probe_stream(
            stream_id, stream.get("url"), stream.get("name")
        )
        logger.info("[STREAM-STATS-PROBE] Single stream probe completed for %s", stream_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[STREAM-STATS-PROBE] Failed to probe stream %s: %s", stream_id, e)
        raise HTTPException(status_code=500, detail=str(e))
