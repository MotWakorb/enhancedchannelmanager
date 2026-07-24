"""
Streams & providers router — stream listing and provider endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
Enriched with server-side normalization in v0.15.0.
"""
import asyncio
import logging
import time
from enum import Enum
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from cache import get_cache
from dispatcharr_client import get_client
from stream_normalization import (
    enrich_stream,
    sort_streams_by_quality,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Streams"])

# Cache key + TTL for the reverse stream_id -> [channel_id] assignment index.
# Dispatcharr's stream payloads carry NO per-stream channel linkage, so
# assignment must be derived by paginating get_channels() and inverting each
# channel's `streams` list. That paginated fetch is expensive, so the inverted
# index is cached under a stable key with a SHORT TTL: assignment changes when
# streams are added/removed from channels, and a 45s window keeps repeated
# opt-in (include_assignment) calls cheap without serving badly stale linkage.
ASSIGNMENT_INDEX_CACHE_KEY = "stream_channel_assignment_index"
ASSIGNMENT_INDEX_TTL_SECONDS = 45

# Cache key + TTL for the provider-stale stream id set (bead
# enhancedchannelmanager-po78p / GH #696). Dispatcharr flags a stream
# `is_stale` when its own M3U refresh no longer re-matches it in the source
# playlist; this endpoint pages through client.get_streams() to collect that
# set cheaply for the Channels/Streams pane decorations. A 300s TTL keeps
# repeated pane loads/refreshes from re-scanning every stream on each render.
PROVIDER_STALE_IDS_CACHE_KEY = "provider_stale_stream_ids"
PROVIDER_STALE_IDS_TTL_SECONDS = 300


class StreamSortOrder(str, Enum):
    """Available server-side sort orders for streams."""
    name_asc = "name"
    name_desc = "-name"
    quality = "quality"
    quality_desc = "-quality"


def _enrich_stream_results(streams: list[dict]) -> None:
    """Enrich a list of stream dicts in-place with normalization metadata."""
    start = time.time()
    for stream in streams:
        enrichment = enrich_stream(stream)
        stream["normalized_name"] = enrichment.normalized_name
        stream["quality_tier"] = enrichment.quality_tier
        stream["quality_priority"] = enrichment.quality_priority
        stream["detected_country"] = enrichment.detected_country
        stream["detected_network_prefix"] = enrichment.detected_network_prefix
        stream["regional_variant"] = enrichment.regional_variant
    elapsed = (time.time() - start) * 1000
    if elapsed > 10:
        logger.debug(
            "[STREAMS] Enriched %d streams in %.1fms", len(streams), elapsed
        )


async def _get_stream_channel_index() -> dict[int, list[int]]:
    """Build (or reuse a cached) reverse stream_id -> [channel_id] index.

    Dispatcharr stream payloads carry no channel linkage, so assignment is
    derived by paginating get_channels() and inverting each channel's `streams`
    ID list — the same cross-reference the struck-out-streams endpoint uses
    (see backend/routers/stream_stats.py). The result is cached under
    ASSIGNMENT_INDEX_CACHE_KEY with the short ASSIGNMENT_INDEX_TTL_SECONDS TTL so
    repeated opt-in (include_assignment) calls don't re-fetch every channel.

    Returns a dict mapping stream_id to the list of channel_ids it belongs to,
    in channel-encounter order (first encountered channel first).
    """
    cache = get_cache()
    cached = cache.get(ASSIGNMENT_INDEX_CACHE_KEY, ttl=ASSIGNMENT_INDEX_TTL_SECONDS)
    if cached is not None:
        return cached

    client = get_client()
    start = time.time()
    all_channels: list[dict] = []
    page = 1
    while True:
        result = await client.get_channels(page=page, page_size=100)
        page_channels = result.get("results", [])
        all_channels.extend(page_channels)
        if len(all_channels) >= result.get("count", 0) or not page_channels:
            break
        page += 1

    index: dict[int, list[int]] = {}
    for ch in all_channels:
        ch_id = ch.get("id")
        if ch_id is None:
            continue
        for sid in ch.get("streams", []) or []:
            index.setdefault(sid, []).append(ch_id)

    elapsed_ms = (time.time() - start) * 1000
    logger.debug(
        "[STREAMS] Built stream->channel assignment index from %d channels "
        "(%d assigned streams) in %.1fms",
        len(all_channels), len(index), elapsed_ms,
    )
    cache.set(ASSIGNMENT_INDEX_CACHE_KEY, index)
    return index


def _apply_assignment(streams: list[dict], index: dict[int, list[int]]) -> None:
    """Annotate each stream in-place with channel-assignment status.

    Sets ``has_channel`` (bool), ``assigned_channel_id`` (first channel id or
    None) and ``assigned_channel_ids`` (full list) from the reverse index.
    """
    for stream in streams:
        sid = stream.get("id")
        channel_ids = index.get(sid, [])
        stream["assigned_channel_ids"] = list(channel_ids)
        stream["assigned_channel_id"] = channel_ids[0] if channel_ids else None
        stream["has_channel"] = bool(channel_ids)


@router.get("/api/streams")
async def get_streams(
    # Bounds enforced here (bead enhancedchannelmanager-g4z2h, systemic sibling
    # of 1a5mf): page<1 / page_size<1 were passed straight to the upstream
    # Dispatcharr client, which raised and surfaced as a 500. Upper bound is
    # generous — App.tsx legitimately requests page_size=500 (searchStreams,
    # loadStreamGroup).
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(100, ge=1, le=1000, description="Results per page"),
    search: Optional[str] = None,
    channel_group_name: Optional[str] = None,
    m3u_account: Optional[int] = None,
    sort: Optional[StreamSortOrder] = None,
    enrich: bool = True,
    include_assignment: bool = False,
    bypass_cache: bool = False,
):
    """List streams with pagination, search, filtering, and enrichment.

    When ``include_assignment`` is True, each returned stream is annotated with
    ``has_channel`` / ``assigned_channel_id`` / ``assigned_channel_ids`` derived
    from a cached reverse stream->channel index (see
    ``_get_stream_channel_index``). This costs an extra paginated channel fetch
    (cached for ASSIGNMENT_INDEX_TTL_SECONDS), so it is opt-in; the default path
    is unchanged.
    """
    start_time = time.time()
    logger.debug(
        "[STREAMS] Fetching streams - page=%s, page_size=%s, "
        "search=%s, group=%s, m3u=%s, include_assignment=%s, bypass_cache=%s",
        page, page_size,
        search, channel_group_name, m3u_account, include_assignment, bypass_cache
    )

    cache = get_cache()
    sort_str = sort.value if sort else ""
    cache_key = f"streams:p{page}:ps{page_size}:s{search or ''}:g{channel_group_name or ''}:m{m3u_account or ''}:sort{sort_str}:e{enrich}:a{include_assignment}"

    # Try cache first (unless bypassed)
    if not bypass_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            cache_time = (time.time() - start_time) * 1000
            result_count = len(cached.get("results", []))
            total_count = cached.get("count", 0)
            logger.debug(
                "[STREAMS] Cache HIT - returned %s streams "
                "(total=%s) in %.1fms",
                result_count, total_count, cache_time
            )
            return cached

    client = get_client()
    try:
        fetch_start = time.time()
        result = await client.get_streams(
            page=page,
            page_size=page_size,
            search=search,
            channel_group_name=channel_group_name,
            m3u_account=m3u_account,
        )
        fetch_time = (time.time() - fetch_start) * 1000

        # Get channel groups for name lookup (also cached)
        groups_cache_key = "channel_groups"
        groups = cache.get(groups_cache_key)
        if groups is None:
            groups = await client.get_channel_groups()
            cache.set(groups_cache_key, groups)
        group_map = {g["id"]: g["name"] for g in groups}

        # Add channel_group_name to each stream
        streams = result.get("results", [])
        for stream in streams:
            group_id = stream.get("channel_group")
            stream["channel_group_name"] = group_map.get(group_id) if group_id else None

        # Enrich streams with normalization metadata
        if enrich:
            _enrich_stream_results(streams)

        # Optionally annotate channel-assignment status (opt-in: extra cost).
        if include_assignment and streams:
            index = await _get_stream_channel_index()
            _apply_assignment(streams, index)

        # Apply server-side sorting
        if sort and streams:
            if sort == StreamSortOrder.quality:
                streams = sort_streams_by_quality(streams)
            elif sort == StreamSortOrder.quality_desc:
                streams = list(reversed(sort_streams_by_quality(streams)))
            elif sort == StreamSortOrder.name_asc:
                streams.sort(key=lambda s: (s.get("name") or "").lower())
            elif sort == StreamSortOrder.name_desc:
                streams.sort(key=lambda s: (s.get("name") or "").lower(), reverse=True)
            result["results"] = streams

        # Cache the result
        cache.set(cache_key, result)

        total_time = (time.time() - start_time) * 1000
        result_count = len(result.get("results", []))
        total_count = result.get("count", 0)
        logger.debug(
            "[STREAMS] Cache MISS - fetched %s streams "
            "(total=%s) - fetch=%.1fms, total=%.1fms",
            result_count, total_count, fetch_time, total_time
        )
        return result
    except Exception as e:
        logger.exception("[STREAMS] Failed to fetch streams: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/stream-groups")
async def get_stream_groups(bypass_cache: bool = False, m3u_account_id: Optional[int] = None):
    """Get all stream groups with their stream counts.

    Args:
        bypass_cache: Skip cache and fetch fresh data
        m3u_account_id: Optional provider ID to filter groups. When provided,
                       only returns groups that have streams from this provider.

    Returns list of objects: [{"name": "Group Name", "count": 42}, ...]
    """
    logger.debug("[STREAMS] GET /api/stream-groups - bypass_cache=%s m3u_account_id=%s", bypass_cache, m3u_account_id)
    cache = get_cache()
    # Include provider filter in cache key for proper cache isolation
    cache_key = f"stream_groups_with_counts:{m3u_account_id}" if m3u_account_id else "stream_groups_with_counts"

    # Try cache first (unless bypassed)
    if not bypass_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    client = get_client()
    try:
        start = time.time()
        result = await client.get_stream_groups_with_counts(m3u_account_id=m3u_account_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STREAMS] Fetched stream groups in %.1fms", elapsed_ms)
        cache.set(cache_key, result)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


class BulkStreamIdsRequest(BaseModel):
    stream_ids: list[int]
    # Opt-in channel-assignment status. When True, each returned stream is
    # annotated with has_channel / assigned_channel_id / assigned_channel_ids
    # derived from a cached reverse stream->channel index (costs an extra
    # paginated channel fetch, cached for ASSIGNMENT_INDEX_TTL_SECONDS). Default
    # False keeps the proxy path cheap and behaviorally unchanged.
    include_assignment: bool = False


@router.post("/api/streams/by-ids")
async def get_streams_by_ids(request: BulkStreamIdsRequest):
    """Get multiple streams by their IDs (proxies to Dispatcharr).

    With ``include_assignment=True``, results carry channel-assignment status
    (has_channel / assigned_channel_id / assigned_channel_ids). Dispatcharr's
    payload has no channel linkage, so this is derived from a cached reverse
    stream->channel index — hence opt-in.
    """
    logger.debug(
        "[STREAMS] POST /api/streams/by-ids - %d streams include_assignment=%s",
        len(request.stream_ids), request.include_assignment,
    )
    try:
        client = get_client()
        start = time.time()
        result = await client.get_streams_by_ids(request.stream_ids)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STREAMS] Fetched %d streams by IDs in %.1fms", len(request.stream_ids), elapsed_ms)
        # Enrich results
        if isinstance(result, list):
            _enrich_stream_results(result)
            # Opt-in channel-assignment status (extra cost: cached channel fetch).
            if request.include_assignment and result:
                index = await _get_stream_channel_index()
                _apply_assignment(result, index)
        return result
    except Exception as e:
        logger.exception("[STREAMS] Failed to get streams by IDs: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/streams/stale-ids")
async def get_stale_stream_ids(bypass_cache: bool = False):
    """Return the set of Dispatcharr stream IDs currently flagged ``is_stale``.

    Dispatcharr flags a stream stale (+ ``last_seen``) when its own M3U
    refresh no longer re-matches it in the source playlist. This endpoint
    pages through ``client.get_streams()`` (same paged-scan shape as
    ``stream_stats.get_stale_streams``, see
    backend/routers/stream_stats.py:263-274) and returns just the stale id
    set + last-seen timestamps, cheaply enough for the Channels/Streams pane
    to decorate rows without an N+1 against Dispatcharr. Cached for
    PROVIDER_STALE_IDS_TTL_SECONDS; pass ``bypass_cache=True`` to force a
    fresh scan.
    """
    logger.debug("[STREAMS] GET /api/streams/stale-ids bypass_cache=%s", bypass_cache)
    cache = get_cache()

    if not bypass_cache:
        cached = cache.get(PROVIDER_STALE_IDS_CACHE_KEY, ttl=PROVIDER_STALE_IDS_TTL_SECONDS)
        if cached is not None:
            logger.debug("[STREAMS] Cache HIT for stale-ids (%d stale)", cached.get("count", 0))
            return cached

    client = get_client()
    try:
        start = time.time()
        stale_ids: list[int] = []
        last_seen: dict[int, Optional[str]] = {}
        fetched = 0
        page = 1
        while True:
            result = await client.get_streams(page=page, page_size=1000)
            page_streams = result.get("results", [])
            fetched += len(page_streams)
            for s in page_streams:
                if s.get("is_stale"):
                    sid = s["id"]
                    stale_ids.append(sid)
                    last_seen[sid] = s.get("last_seen")
            if fetched >= result.get("count", 0) or not page_streams:
                break
            page += 1
        elapsed_ms = (time.time() - start) * 1000
        logger.debug(
            "[STREAMS] Scanned %d provider streams (%d stale) in %.1fms",
            fetched, len(stale_ids), elapsed_ms,
        )

        response = {
            "stale_stream_ids": stale_ids,
            "last_seen": last_seen,
            "count": len(stale_ids),
        }
        cache.set(PROVIDER_STALE_IDS_CACHE_KEY, response)
        return response
    except Exception as e:
        logger.exception("[STREAMS] Failed to fetch stale stream ids: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/providers", tags=["Providers"])
async def get_providers():
    """List M3U accounts (legacy endpoint)."""
    logger.debug("[STREAMS] GET /api/providers")
    client = get_client()
    try:
        start = time.time()
        result = await client.get_m3u_accounts()
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STREAMS] Fetched M3U accounts in %.1fms", elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/providers/catchup-status", tags=["Providers"])
async def get_provider_catchup_status():
    """Per-M3U-account catch-up availability for the M3U manager badge (bead 4dpiz).

    For each provider we ask Dispatcharr for a single catch-up stream
    (``is_catchup=true``, ``page_size=1``) and read the paginated ``count``:
    ``count > 0`` means the provider has at least one catch-up stream. The one
    returned row's ``catchup_days`` is sampled as the provider's catch-up depth
    — Dispatcharr sets catch-up depth per-provider (verified provider-uniform on
    live data), and its streams endpoint does NOT honour an ``ordering`` param,
    so a guaranteed maximum would require an O(catch-up-streams) scan we
    deliberately avoid.

    ``is_catchup`` is ``db_index=True`` upstream, so each probe is a cheap
    indexed count query — O(providers) total, run concurrently. Best-effort per
    account: a probe failure degrades that account to ``has_catchup=false``
    (logged), never 500s the whole M3U manager.

    Returns ``{ "<account_id>": {"has_catchup": bool, "catchup_days": int|null} }``.
    """
    logger.debug("[STREAMS] GET /api/providers/catchup-status")
    client = get_client()
    try:
        accounts = await client.get_m3u_accounts()
    except Exception as e:
        logger.warning("[STREAMS] catchup-status: could not list M3U accounts: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")

    async def _probe(account: dict):
        account_id = account.get("id")
        try:
            page = await client.get_streams(
                m3u_account=account_id, is_catchup=True, page_size=1
            )
            count = page.get("count") or 0
            results = page.get("results") or []
            days = results[0].get("catchup_days") if results else None
            return account_id, {
                "has_catchup": count > 0,
                "catchup_days": days if count > 0 else None,
            }
        except Exception as e:
            logger.warning(
                "[STREAMS] catchup-status: probe failed for account %s: %s",
                account_id, e,
            )
            return account_id, {"has_catchup": False, "catchup_days": None}

    start = time.time()
    probes = await asyncio.gather(*(_probe(a) for a in accounts))
    elapsed_ms = (time.time() - start) * 1000
    logger.debug(
        "[STREAMS] Probed catch-up status for %d providers in %.1fms",
        len(probes), elapsed_ms,
    )
    return {
        str(account_id): status
        for account_id, status in probes
        if account_id is not None
    }


@router.get("/api/providers/group-settings", tags=["Providers"])
async def get_all_provider_group_settings():
    """Get group settings from all M3U providers, mapped by channel_group_id."""
    logger.debug("[STREAMS] GET /api/providers/group-settings")
    client = get_client()
    try:
        start = time.time()
        result = await client.get_all_m3u_group_settings()
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STREAMS] Fetched provider group settings in %.1fms", elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/providers/group-settings/by-provider", tags=["Providers"])
async def get_provider_group_settings_by_provider():
    """Per-(provider, group) group settings, NON-collapsed (bead 38dzi).

    Unlike ``/api/providers/group-settings`` (one row per channel-group id,
    collapsed preferring auto_channel_sync ON), this returns ONE row per
    (m3u_account, channel_group) junction — so the UI can offer provider-scoped
    Event Sync group selection (Provider A's "MLB PPV" vs Provider B's
    "MLB PPV", which share one channel-group id). The group NAME is not
    included; the caller joins on ``channel_group_id`` against the channel
    groups it already loads.
    """
    logger.debug("[STREAMS] GET /api/providers/group-settings/by-provider")
    client = get_client()
    try:
        by_pair = await client.get_m3u_group_settings_by_provider()
        return [
            {
                "m3u_account_id": setting.get("m3u_account_id"),
                "m3u_account_name": setting.get("m3u_account_name"),
                "channel_group_id": setting.get("channel_group"),
                "auto_channel_sync": bool(setting.get("auto_channel_sync")),
                "enabled": bool(setting.get("enabled")),
                "stream_count": setting.get("stream_count"),
            }
            for setting in by_pair.values()
        ]
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
