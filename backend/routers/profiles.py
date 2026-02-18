"""
Profiles router — stream and channel profile endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import logging

from fastapi import APIRouter, HTTPException, Request

from dispatcharr_client import get_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Stream Profiles"])


# Stream Profiles
@router.get("/api/stream-profiles")
async def get_stream_profiles():
    """List available stream profiles."""
    client = get_client()
    try:
        return await client.get_stream_profiles()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/stream-profiles")
async def create_stream_profile(request: Request):
    """Create a new stream profile in Dispatcharr."""
    client = get_client()
    try:
        body = await request.json()
        return await client.create_stream_profile(body)
    except Exception as e:
        logger.exception(f"Failed to create stream profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Channel Profiles
@router.get("/api/channel-profiles", tags=["Channel Profiles"])
async def get_channel_profiles():
    """Get all channel profiles."""
    client = get_client()
    try:
        return await client.get_channel_profiles()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/channel-profiles", tags=["Channel Profiles"])
async def create_channel_profile(request: Request):
    """Create a new channel profile."""
    client = get_client()
    try:
        data = await request.json()
        return await client.create_channel_profile(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/channel-profiles/{profile_id}", tags=["Channel Profiles"])
async def get_channel_profile(profile_id: int):
    """Get a single channel profile."""
    client = get_client()
    try:
        return await client.get_channel_profile(profile_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/channel-profiles/{profile_id}", tags=["Channel Profiles"])
async def update_channel_profile(profile_id: int, request: Request):
    """Update a channel profile."""
    client = get_client()
    try:
        data = await request.json()
        return await client.update_channel_profile(profile_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/channel-profiles/{profile_id}", tags=["Channel Profiles"])
async def delete_channel_profile(profile_id: int):
    """Delete a channel profile."""
    client = get_client()
    try:
        await client.delete_channel_profile(profile_id)
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/channel-profiles/{profile_id}/channels/bulk-update", tags=["Channel Profiles"])
async def bulk_update_profile_channels(profile_id: int, request: Request):
    """Bulk enable/disable channels for a profile."""
    client = get_client()
    try:
        data = await request.json()
        return await client.bulk_update_profile_channels(profile_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/channel-profiles/{profile_id}/channels/{channel_id}", tags=["Channel Profiles"])
async def update_profile_channel(profile_id: int, channel_id: int, request: Request):
    """Enable/disable a single channel for a profile."""
    client = get_client()
    try:
        data = await request.json()
        return await client.update_profile_channel(profile_id, channel_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
