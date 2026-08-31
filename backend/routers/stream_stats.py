"""
Stream stats router — stream probe stats, probe operations, sort, dismiss/clear.

Extracted from main.py (Phase 3 of v0.13.0 backend refactor).
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_

from config import get_settings, stream_sort_point_rules_for_evaluator
from database import get_session
from dispatcharr_client import get_client
from services.notification_service import task_start_alerts_enabled
from stream_prober import StreamProber, ensure_prober
from smart_sort_evaluator import stream_metadata_criteria

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
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/summary")
async def get_stream_stats_summary():
    """Get summary of stream probe statistics."""
    logger.debug("[STREAM-STATS] GET /api/stream-stats/summary")
    try:
        return StreamProber.get_stats_summary()
    except Exception as e:
        logger.exception("[STREAM-STATS] Failed to get stream stats summary: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


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
        raise HTTPException(status_code=500, detail="Internal server error")
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
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/stale")
async def get_stale_streams(days: int = 7):
    """Get streams that are stale by either of two independent signals:

    - **not_probed_recently**: ECM hasn't ffprobed the stream in `days` days,
      or never has. The stream may still be listed and playable — we just
      haven't re-checked it.
    - **provider_stale**: Dispatcharr's own M3U refresh no longer re-matched
      this stream in the source playlist (its `is_stale` flag), meaning the
      provider may have removed it entirely. Independent of `days` — this is
      Dispatcharr's own grace-period bookkeeping, not ours.

    A `StreamStats` row whose `stream_id` no longer exists in Dispatcharr's
    current stream inventory at all (deleted upstream, probe history never
    cleaned up) is NOT surfaced under `not_probed_recently` — there is
    nothing left to (re-)probe, so it isn't an actionable stale stream. This
    matters in practice: on this deployment, `StreamStats` had ~4857 rows
    against ~2620 live Dispatcharr streams, so without this cross-check the
    majority of "stale" results would be dead references.

    Distinct from struck-out (consecutive probe *failures*): a stale stream
    may be passing every probe it gets, or may never have been probed at all.
    """
    logger.debug("[STREAM-STATS] GET /api/stream-stats/stale?days=%s", days)
    from models import StreamStats

    if days <= 0:
        raise HTTPException(status_code=400, detail="days must be positive")

    cutoff = datetime.utcnow() - timedelta(days=days)

    session = get_session()
    try:
        probe_stale = session.query(StreamStats).filter(
            or_(StreamStats.last_probed.is_(None), StreamStats.last_probed < cutoff)
        ).all()
        probe_stale_by_id = {s.stream_id: s for s in probe_stale}

        client = get_client()
        start = time.time()

        provider_stale_by_id: dict[int, dict] = {}
        provider_stream_ids: set[int] = set()
        fetched = 0
        page = 1
        while True:
            result = await client.get_streams(page=page, page_size=1000)
            page_streams = result.get("results", [])
            fetched += len(page_streams)
            for s in page_streams:
                provider_stream_ids.add(s["id"])
                if s.get("is_stale"):
                    provider_stale_by_id[s["id"]] = s
            if fetched >= result.get("count", 0) or not page_streams:
                break
            page += 1
        elapsed_ms = (time.time() - start) * 1000
        logger.debug(
            "[STREAM-STATS] Scanned %s provider streams (%s provider-stale) in %.1fms",
            fetched, len(provider_stale_by_id), elapsed_ms,
        )

        # Drop StreamStats rows whose stream_id Dispatcharr no longer has any
        # record of at all — orphaned probe history for a long-deleted stream,
        # not an actionable "go re-probe this" candidate. See docstring.
        orphaned = set(probe_stale_by_id) - provider_stream_ids
        if orphaned:
            logger.debug(
                "[STREAM-STATS] Excluding %s orphaned StreamStats row(s) with no matching provider stream",
                len(orphaned),
            )
            probe_stale_by_id = {
                sid: stats for sid, stats in probe_stale_by_id.items() if sid not in orphaned
            }

        all_ids = set(probe_stale_by_id) | set(provider_stale_by_id)
        if not all_ids:
            return {"streams": [], "threshold_days": days}

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
        logger.debug("[STREAM-STATS] Fetched %s channels for stale lookup in %.1fms", len(all_channels), elapsed_ms)

        # Iterate each channel's own (small) streams list once, checking against
        # all_ids via set membership, rather than iterating all_ids per channel.
        # /stale surfaces far more results at scale than /struck-out (e.g. 1690
        # on this deployment), so the O(channels * |all_ids|) shape hits harder
        # here — inverted to O(channels * |ch_streams|) with O(1) lookups.
        stream_channels: dict[int, list[dict]] = {sid: [] for sid in all_ids}
        for ch in all_channels:
            ch_streams = set(ch.get("streams", []))
            for sid in ch_streams & all_ids:
                stream_channels[sid].append({
                    "id": ch["id"],
                    "name": ch.get("name", "Unknown"),
                })

        result = []
        for sid in all_ids:
            reasons = []
            if sid in probe_stale_by_id:
                d = probe_stale_by_id[sid].to_dict()
                reasons.append("not_probed_recently")
            else:
                # Provider-stale only: no StreamStats row exists for this stream
                # (e.g. it was probed and then cleared, or never probed at all).
                # Keep the response shape consistent with the to_dict() branch
                # above so API consumers can rely on `last_probed` always being
                # present (never absent — null when unknown).
                d = {"stream_id": sid, "stream_name": None, "last_probed": None}

            if sid in provider_stale_by_id:
                reasons.append("provider_stale")
                provider = provider_stale_by_id[sid]
                d["provider_last_seen"] = provider.get("last_seen")
                if not d.get("stream_name"):
                    d["stream_name"] = provider.get("name")
            else:
                d["provider_last_seen"] = None

            d["reasons"] = reasons
            d["channels"] = stream_channels.get(sid, [])
            result.append(d)

        return {"streams": result, "threshold_days": days}
    except Exception as e:
        logger.exception("[STREAM-STATS] Failed to get stale streams: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()


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
    valid_criteria = {"resolution", "bitrate", "framerate", "video_codec", "m3u_priority", "audio_channels", "custom_streams", "catchup"}
    if request.mode == "smart":
        sort_priority = [c for c in settings.stream_sort_priority if settings.stream_sort_enabled.get(c, False)]
        sort_enabled = {c: True for c in sort_priority}
        sort_strategy = settings.stream_sort_strategy
        point_rules = stream_sort_point_rules_for_evaluator(settings)
    elif request.mode in valid_criteria:
        sort_priority = [request.mode]
        sort_enabled = {request.mode: True}
        sort_strategy = "priority"
        point_rules = ()
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

    # Build M3U account map (m3u_priority) and custom-stream ID set (custom_streams)
    # from a single stream-data fetch. The fetch is gated to only run when at least
    # one of those account-derived criteria is active (generalizes the original
    # m3u_priority-only gate so custom_streams gets its data too — bead ap1ud / GH #244).
    stream_m3u_map = {}
    custom_stream_ids: set[int] = set()
    catchup_stream_ids: set[int] = set()
    stream_metadata_known_ids: set[int] = set()
    metadata_criteria = stream_metadata_criteria(
        sort_strategy,
        priority_criteria=sort_priority,
        point_rules=point_rules,
    )
    needs_m3u = "m3u_priority" in metadata_criteria
    needs_custom = "custom_streams" in metadata_criteria
    needs_catchup = "catchup" in metadata_criteria
    if needs_m3u or needs_custom or needs_catchup:
        try:
            client = get_client()
            start_fetch = time.time()
            streams_data = await client.get_streams_by_ids(all_stream_ids)
            elapsed_ms = (time.time() - start_fetch) * 1000
            logger.debug("[STREAM-STATS-SORT] Fetched %s streams for sort criteria in %.1fms", len(streams_data), elapsed_ms)
            for s in streams_data:
                # Dispatcharr has historically returned either "id" or "stream_id" as the identifier.
                # Be tolerant so M3U priority / custom-stream sorting doesn't silently no-op.
                stream_id = s.get("id", s.get("stream_id"))
                if stream_id is None:
                    continue
                stream_metadata_known_ids.add(int(stream_id))
                if needs_m3u:
                    stream_m3u_map[int(stream_id)] = extract_m3u_account_id(s.get("m3u_account"))
                if needs_custom and s.get("is_custom"):
                    custom_stream_ids.add(int(stream_id))
                if needs_catchup and s.get("is_catchup"):
                    catchup_stream_ids.add(int(stream_id))
        except Exception as e:
            logger.warning("[STREAM-STATS-SORT] Failed to fetch stream data for sort: %s", e)

    # Sort each channel
    results = []
    for ch in request.channels:
        # Direct metadata-only sorts do not require probe stats; respect them
        # even when the preferred stream has not been probed yet.
        deprioritize_failed = settings.deprioritize_failed_streams
        if request.mode in {"m3u_priority", "catchup"}:
            deprioritize_failed = False

        sorted_ids = smart_sort_streams(
            stream_ids=ch.stream_ids,
            stats_map=stats_map,
            stream_m3u_map=stream_m3u_map,
            stream_sort_priority=sort_priority,
            stream_sort_enabled=sort_enabled,
            m3u_account_priorities=settings.m3u_account_priorities,
            deprioritize_failed_streams=deprioritize_failed,
            deprioritize_black_screen=getattr(settings, 'deprioritize_black_screen', True),
            deprioritize_low_fps=getattr(settings, 'deprioritize_low_fps', True),
            failed_stream_sort_order=getattr(settings, 'failed_stream_sort_order', None),
            channel_name=f"channel-{ch.channel_id}",
            custom_stream_ids=custom_stream_ids,
            catchup_stream_ids=catchup_stream_ids,
            stream_sort_strategy=sort_strategy,
            stream_sort_point_rules=point_rules,
            stream_metadata_known_ids=stream_metadata_known_ids,
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
        raise HTTPException(status_code=500, detail="Internal server error")
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
        raise HTTPException(status_code=500, detail="Internal server error")


# BulkStreamIdsRequest is in routers/streams.py
from routers.streams import BulkStreamIdsRequest


@router.post("/by-ids")
async def get_stream_stats_by_ids(request: BulkStreamIdsRequest):
    """Get probe stats for multiple streams by their IDs."""
    logger.debug("[STREAM-STATS] POST /api/stream-stats/by-ids - %d streams", len(request.stream_ids))
    try:
        return StreamProber.get_stats_by_stream_ids(request.stream_ids)
    except Exception as e:
        logger.error("[STREAM-STATS] Failed to get stream stats by IDs: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# NOTE: /probe/bulk and /probe/all MUST be defined BEFORE /probe/{stream_id}
# to avoid the path parameter matching "bulk" or "all" as a stream_id
@router.post("/probe/bulk")
async def probe_bulk_streams(request: BulkProbeRequest):
    """Start an on-demand background probe for a specific list of streams.

    This is the async sibling of POST /probe/all: instead of probing each stream
    synchronously (which 504'd at batches >=~3 — enhancedchannelmanager-znc76.5),
    it kicks off a background probe of the requested ``stream_ids`` using the
    SAME prober job/progress/results-envelope machinery probe/all uses, then
    returns immediately. Callers poll GET /probe/progress and read GET
    /probe/results to get the outcome — those now reflect the bulk run.

    Honors the single-probe-at-a-time invariant: if a probe is already running
    (scheduled probe-all or another bulk run) this returns
    ``{"status": "already_running"}`` rather than starting a second.
    """
    logger.debug("[STREAM-STATS-PROBE] POST /api/stream-stats/probe/bulk - %d streams", len(request.stream_ids))

    prober = ensure_prober()
    logger.debug("[STREAM-STATS-PROBE] get_prober() returned: %s", prober is not None)

    if not prober:
        logger.error("[STREAM-STATS-PROBE] Stream prober not available - returning 503")
        raise HTTPException(status_code=503, detail="Stream prober not available")

    # Single-probe-at-a-time guard: do NOT start a second probe over a running
    # one (it would clobber the shared progress/results envelope). Report that a
    # probe is already in progress so the caller can poll the existing run.
    if prober._probing_in_progress:
        logger.info("[STREAM-STATS-PROBE] Probe already in progress - rejecting bulk start")
        return {
            "status": "already_running",
            "message": "A probe is already in progress; check /probe/progress",
        }

    # The "probe started" alert is info-level; a manual probe should only push it
    # externally when the stream_probe task opted into info alerts (GH #462).
    start_send_alerts = task_start_alerts_enabled("stream_probe")

    async def run_bulk_probe_with_logging():
        """Wrapper to catch and log any errors from the background bulk probe."""
        try:
            logger.info("[STREAM-STATS-PROBE] Background bulk probe task starting (%d streams)...", len(request.stream_ids))
            await prober.probe_streams_by_ids(request.stream_ids, start_send_alerts=start_send_alerts)
            logger.info("[STREAM-STATS-PROBE] Background bulk probe task completed successfully")
        except Exception as e:
            logger.exception("[STREAM-STATS-PROBE] Background bulk probe task failed: %s", e)

    asyncio.create_task(run_bulk_probe_with_logging())
    logger.debug("[STREAM-STATS-PROBE] Background bulk probe task created, returning response")
    return {
        "status": "started",
        "message": f"Background bulk probe started for {len(request.stream_ids)} streams",
        "total": len(request.stream_ids),
    }


@router.post("/probe/all")
async def probe_all_streams_endpoint(request: ProbeAllRequest = ProbeAllRequest()):
    """Trigger probe for all streams (background task).

    Optionally filter by channel groups or specific stream IDs.
    If channel_groups is empty, probes all groups.
    If stream_ids is provided, probes only those specific streams (useful for re-probing failed streams).
    """
    logger.debug("[STREAM-STATS-PROBE] POST /api/stream-stats/probe/all - groups=%s, stream_ids=%d", request.channel_groups, len(request.stream_ids) if request.stream_ids else 0)

    prober = ensure_prober()
    logger.debug("[STREAM-STATS-PROBE] get_prober() returned: %s", prober is not None)

    if not prober:
        logger.error("[STREAM-STATS-PROBE] Stream prober not available - returning 503")
        raise HTTPException(status_code=503, detail="Stream prober not available")

    # If a probe is already "in progress" (possibly stuck), reset it first
    if prober._probing_in_progress:
        logger.warning("[STREAM-STATS-PROBE] Probe state shows in_progress - resetting before starting new probe")
        prober.force_reset_probe_state()

    # Info-level "probe started" alert: gate external dispatch on the stream_probe
    # task's (send_alerts AND alert_on_info), even for this manual trigger (GH #462).
    start_send_alerts = task_start_alerts_enabled("stream_probe")

    async def run_probe_with_logging():
        """Wrapper to catch and log any errors from the probe task."""
        try:
            logger.info("[STREAM-STATS-PROBE] Background probe task starting...")
            await prober.probe_all_streams(
                channel_groups_override=request.channel_groups or None,
                skip_m3u_refresh=request.skip_m3u_refresh,
                stream_ids_filter=request.stream_ids or None,
                start_send_alerts=start_send_alerts,
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
    prober = ensure_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.get_probe_progress()


@router.get("/probe/results")
async def get_probe_results():
    """Get detailed results of the last probe all streams operation."""
    logger.debug("[STREAM-STATS-PROBE] GET /api/stream-stats/probe/results")
    prober = ensure_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.get_probe_results()


@router.get("/probe/history")
async def get_probe_history():
    """Get probe run history (last 5 runs)."""
    logger.debug("[STREAM-STATS-PROBE] GET /api/stream-stats/probe/history")
    prober = ensure_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.get_probe_history()


@router.post("/probe/cancel")
async def cancel_probe():
    """Cancel an in-progress probe operation."""
    logger.debug("[STREAM-STATS-PROBE] POST /api/stream-stats/probe/cancel")
    prober = ensure_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.cancel_probe()


@router.post("/probe/pause")
async def pause_probe():
    """Pause an in-progress probe operation.

    The StreamProber's probe loops (sequential and parallel) already honor
    ``_probe_paused`` internally (bd-vdrku: this endpoint was the missing
    HTTP wiring for pre-existing, already-integrated prober pause support).
    """
    logger.debug("[STREAM-STATS-PROBE] POST /api/stream-stats/probe/pause")
    prober = ensure_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.pause_probe()


@router.post("/probe/resume")
async def resume_probe():
    """Resume a paused probe operation."""
    logger.debug("[STREAM-STATS-PROBE] POST /api/stream-stats/probe/resume")
    prober = ensure_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.resume_probe()


@router.post("/probe/reset")
async def reset_probe_state():
    """Force reset the probe state if it gets stuck."""
    logger.debug("[STREAM-STATS-PROBE] POST /api/stream-stats/probe/reset")
    prober = ensure_prober()
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
        raise HTTPException(status_code=500, detail="Internal server error")
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
        raise HTTPException(status_code=500, detail="Internal server error")
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
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()


@router.post("/probe/{stream_id}")
async def probe_single_stream(stream_id: int):
    """Trigger on-demand probe for a single stream."""
    logger.debug("[STREAM-STATS-PROBE] POST /api/stream-stats/probe/%s", stream_id)

    prober = ensure_prober()
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[STREAM-STATS-PROBE] Failed to probe stream %s: %s", stream_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")

    logger.info("[STREAM-STATS-PROBE] Single stream probe completed for %s", stream_id)
    return result
