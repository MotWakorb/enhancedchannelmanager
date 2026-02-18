"""
Profiles router — stream and channel profile endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import logging
import time

from fastapi import APIRouter, HTTPException, Request

from dispatcharr_client import get_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Stream Profiles"])


# Stream Profiles
@router.get("/api/stream-profiles")
async def get_stream_profiles():
    """List available stream profiles."""
    logger.debug("[PROFILES] GET /stream-profiles")
    client = get_client()
    try:
        start = time.time()
        result = await client.get_stream_profiles()
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[PROFILES] Fetched stream profiles in %.1fms", elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[PROFILES] Failed to fetch stream profiles")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/stream-profiles")
async def create_stream_profile(request: Request):
    """Create a new stream profile in Dispatcharr."""
    logger.debug("[PROFILES] POST /stream-profiles")
    client = get_client()
    try:
        body = await request.json()
        start = time.time()
        result = await client.create_stream_profile(body)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[PROFILES] Created stream profile id=%s name=%s in %.1fms", result.get("id"), result.get("name"), elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[PROFILES] Failed to create stream profile")
        raise HTTPException(status_code=500, detail="Internal server error")


# Channel Profiles
@router.get("/api/channel-profiles", tags=["Channel Profiles"])
async def get_channel_profiles():
    """Get all channel profiles."""
    logger.debug("[PROFILES] GET /channel-profiles")
    client = get_client()
    try:
        start = time.time()
        result = await client.get_channel_profiles()
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[PROFILES] Fetched channel profiles in %.1fms", elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[PROFILES] Failed to fetch channel profiles")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/channel-profiles", tags=["Channel Profiles"])
async def create_channel_profile(request: Request):
    """Create a new channel profile."""
    logger.debug("[PROFILES] POST /channel-profiles")
    client = get_client()
    try:
        data = await request.json()
        start = time.time()
        result = await client.create_channel_profile(data)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[PROFILES] Created channel profile id=%s name=%s in %.1fms", result.get("id"), result.get("name"), elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[PROFILES] Failed to create channel profile")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/channel-profiles/{profile_id}", tags=["Channel Profiles"])
async def get_channel_profile(profile_id: int):
    """Get a single channel profile."""
    logger.debug("[PROFILES] GET /channel-profiles/%s", profile_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_channel_profile(profile_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[PROFILES] Fetched channel profile id=%s in %.1fms", profile_id, elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[PROFILES] Failed to fetch channel profile id=%s", profile_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/api/channel-profiles/{profile_id}", tags=["Channel Profiles"])
async def update_channel_profile(profile_id: int, request: Request):
    """Update a channel profile."""
    logger.debug("[PROFILES] PATCH /channel-profiles/%s", profile_id)
    client = get_client()
    try:
        data = await request.json()
        start = time.time()
        result = await client.update_channel_profile(profile_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[PROFILES] Updated channel profile id=%s in %.1fms", profile_id, elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[PROFILES] Failed to update channel profile id=%s", profile_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/api/channel-profiles/{profile_id}", tags=["Channel Profiles"])
async def delete_channel_profile(profile_id: int):
    """Delete a channel profile."""
    logger.debug("[PROFILES] DELETE /channel-profiles/%s", profile_id)
    client = get_client()
    try:
        start = time.time()
        await client.delete_channel_profile(profile_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[PROFILES] Deleted channel profile id=%s in %.1fms", profile_id, elapsed_ms)
        return {"status": "deleted"}
    except Exception as e:
        logger.exception("[PROFILES] Failed to delete channel profile id=%s", profile_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/api/channel-profiles/{profile_id}/channels/bulk-update", tags=["Channel Profiles"])
async def bulk_update_profile_channels(profile_id: int, request: Request):
    """Bulk enable/disable channels for a profile."""
    logger.debug("[PROFILES] PATCH /channel-profiles/%s/channels/bulk-update", profile_id)
    client = get_client()
    try:
        data = await request.json()
        start = time.time()
        result = await client.bulk_update_profile_channels(profile_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[PROFILES] Bulk updated channels for profile id=%s in %.1fms", profile_id, elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[PROFILES] Failed to bulk update profile channels id=%s", profile_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/api/channel-profiles/{profile_id}/channels/{channel_id}", tags=["Channel Profiles"])
async def update_profile_channel(profile_id: int, channel_id: int, request: Request):
    """Enable/disable a single channel for a profile."""
    logger.debug("[PROFILES] PATCH /channel-profiles/%s/channels/%s", profile_id, channel_id)
    client = get_client()
    try:
        data = await request.json()
        start = time.time()
        result = await client.update_profile_channel(profile_id, channel_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[PROFILES] Updated channel %s in profile %s in %.1fms", channel_id, profile_id, elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[PROFILES] Failed to update profile channel profile_id=%s channel_id=%s", profile_id, channel_id)
        raise HTTPException(status_code=500, detail="Internal server error")
