"""
Streams & providers router — stream listing and provider endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cache import get_cache
from dispatcharr_client import get_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Streams"])


@router.get("/api/streams")
async def get_streams(
    page: int = 1,
    page_size: int = 100,
    search: Optional[str] = None,
    channel_group_name: Optional[str] = None,
    m3u_account: Optional[int] = None,
    bypass_cache: bool = False,
):
    """List streams with pagination, search, and filtering."""
    start_time = time.time()
    logger.debug(
        f"[STREAMS] Fetching streams - page={page}, page_size={page_size}, "
        f"search={search}, group={channel_group_name}, m3u={m3u_account}, bypass_cache={bypass_cache}"
    )

    cache = get_cache()
    cache_key = f"streams:p{page}:ps{page_size}:s{search or ''}:g{channel_group_name or ''}:m{m3u_account or ''}"

    # Try cache first (unless bypassed)
    if not bypass_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            cache_time = (time.time() - start_time) * 1000
            result_count = len(cached.get("results", []))
            total_count = cached.get("count", 0)
            logger.debug(
                f"[STREAMS] Cache HIT - returned {result_count} streams "
                f"(total={total_count}) in {cache_time:.1f}ms"
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
        for stream in result.get("results", []):
            group_id = stream.get("channel_group")
            stream["channel_group_name"] = group_map.get(group_id) if group_id else None

        # Cache the result
        cache.set(cache_key, result)

        total_time = (time.time() - start_time) * 1000
        result_count = len(result.get("results", []))
        total_count = result.get("count", 0)
        logger.debug(
            f"[STREAMS] Cache MISS - fetched {result_count} streams "
            f"(total={total_count}) - fetch={fetch_time:.1f}ms, total={total_time:.1f}ms"
        )
        return result
    except Exception as e:
        logger.error(f"[STREAMS] Failed to fetch streams: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/stream-groups")
async def get_stream_groups(bypass_cache: bool = False, m3u_account_id: Optional[int] = None):
    """Get all stream groups with their stream counts.

    Args:
        bypass_cache: Skip cache and fetch fresh data
        m3u_account_id: Optional provider ID to filter groups. When provided,
                       only returns groups that have streams from this provider.

    Returns list of objects: [{"name": "Group Name", "count": 42}, ...]
    """
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
        result = await client.get_stream_groups_with_counts(m3u_account_id=m3u_account_id)
        cache.set(cache_key, result)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class BulkStreamIdsRequest(BaseModel):
    stream_ids: list[int]


@router.post("/api/streams/by-ids")
async def get_streams_by_ids(request: BulkStreamIdsRequest):
    """Get multiple streams by their IDs (proxies to Dispatcharr)."""
    try:
        client = get_client()
        return await client.get_streams_by_ids(request.stream_ids)
    except Exception as e:
        logger.error(f"Failed to get streams by IDs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/providers", tags=["Providers"])
async def get_providers():
    """List M3U accounts (legacy endpoint)."""
    client = get_client()
    try:
        return await client.get_m3u_accounts()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/providers/group-settings", tags=["Providers"])
async def get_all_provider_group_settings():
    """Get group settings from all M3U providers, mapped by channel_group_id."""
    client = get_client()
    try:
        return await client.get_all_m3u_group_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
