"""
Profiles router — stream and channel profile endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import hmac
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from dispatcharr_client import DispatcharrClient, get_client, upstream_http_exception
from profile_recovery import recovery_binding, recovery_destination_matches, recovery_operator

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
    return await _get_channel_profiles(get_client())


async def _get_channel_profiles(client: DispatcharrClient):
    logger.debug("[PROFILES] GET /channel-profiles")
    try:
        start = time.time()
        result = await client.get_channel_profiles()
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[PROFILES] Fetched channel profiles in %.1fms", elapsed_ms)
        return result
    except Exception as e:
        # An upstream 4xx (e.g. auth/validation) surfaces the actionable status
        # + detail instead of an opaque 500 (bd-lsctv, GH #720).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[PROFILES] Fetch channel profiles rejected by Dispatcharr: %s", e)
            raise mapped
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
        # An upstream 4xx (e.g. validation, name conflict) surfaces the
        # actionable status + detail instead of an opaque 500 (bd-lsctv, GH #720).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[PROFILES] Create channel profile rejected by Dispatcharr: %s", e)
            raise mapped
        logger.exception("[PROFILES] Failed to create channel profile")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/channel-profiles/{profile_id}", tags=["Channel Profiles"])
async def get_channel_profile(profile_id: int):
    """Get a single channel profile."""
    return await _get_channel_profile(get_client(), profile_id)


async def _get_channel_profile(client: DispatcharrClient, profile_id: int):
    logger.debug("[PROFILES] GET /channel-profiles/%s", profile_id)
    try:
        start = time.time()
        result = await client.get_channel_profile(profile_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[PROFILES] Fetched channel profile id=%s in %.1fms", profile_id, elapsed_ms)
        return result
    except Exception as e:
        # An upstream 4xx (e.g. a 404 for a missing profile) surfaces the
        # actionable status + detail instead of an opaque 500 (bd-lsctv, GH #720).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[PROFILES] Fetch channel profile %s rejected by Dispatcharr: %s", profile_id, e)
            raise mapped
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
        # An upstream 4xx (e.g. validation, name conflict, missing profile)
        # surfaces the actionable status + detail instead of an opaque 500
        # (bd-lsctv, GH #720).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[PROFILES] Update channel profile %s rejected by Dispatcharr: %s", profile_id, e)
            raise mapped
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
        # An upstream 4xx (e.g. a 404 for a missing profile, or a conflict)
        # surfaces the actionable status + detail instead of an opaque 500
        # (bd-lsctv, GH #720).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[PROFILES] Delete channel profile %s rejected by Dispatcharr: %s", profile_id, e)
            raise mapped
        logger.exception("[PROFILES] Failed to delete channel profile id=%s", profile_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/api/channel-profiles/{profile_id}/channels/bulk-update", tags=["Channel Profiles"])
async def bulk_update_profile_channels(profile_id: int, request: Request):
    """Bulk enable/disable channels for a profile."""
    return await _bulk_update_profile_channels(get_client(), profile_id, request)


async def _bulk_update_profile_channels(client: DispatcharrClient, profile_id: int, request: Request):
    logger.debug("[PROFILES] PATCH /channel-profiles/%s/channels/bulk-update", profile_id)
    try:
        data = await request.json()
        start = time.time()
        result = await client.bulk_update_profile_channels(profile_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[PROFILES] Bulk updated channels for profile id=%s in %.1fms", profile_id, elapsed_ms)
        return result
    except Exception as e:
        # A missing profile id (or invalid channel id) surfaces as an upstream
        # 4xx — map it to a clean 4xx instead of an opaque 500 (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[PROFILES] Bulk update profile %s channels rejected by Dispatcharr: %s", profile_id, e)
            raise mapped
        logger.exception("[PROFILES] Failed to bulk update profile channels id=%s", profile_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/api/channel-profiles/{profile_id}/channels/{channel_id}", tags=["Channel Profiles"])
async def update_profile_channel(profile_id: int, channel_id: int, request: Request):
    """Enable/disable a single channel for a profile."""
    return await _update_profile_channel(get_client(), profile_id, channel_id, request)


async def _update_profile_channel(client: DispatcharrClient, profile_id: int, channel_id: int, request: Request):
    logger.debug("[PROFILES] PATCH /channel-profiles/%s/channels/%s", profile_id, channel_id)
    try:
        data = await request.json()
        start = time.time()
        result = await client.update_profile_channel(profile_id, channel_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[PROFILES] Updated channel %s in profile %s in %.1fms", channel_id, profile_id, elapsed_ms)
        return result
    except Exception as e:
        # An upstream 4xx (e.g. a missing profile/channel id, or validation)
        # surfaces the actionable status + detail instead of an opaque 500
        # (bd-lsctv, GH #720).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[PROFILES] Update profile %s channel %s rejected by Dispatcharr: %s", profile_id, channel_id, e)
            raise mapped
        logger.exception("[PROFILES] Failed to update profile channel profile_id=%s channel_id=%s", profile_id, channel_id)
        raise HTTPException(status_code=500, detail="Internal server error")


async def _recovery_client(request: Request) -> DispatcharrClient:
    binding = request.headers.get("X-ECM-Profile-Recovery", "")
    if len(binding) == 64 and binding.isascii():
        operator = await recovery_operator(request)
        client = get_client()
        if hmac.compare_digest(binding, recovery_binding(client, operator)):
            return client
    raise HTTPException(409, "Profile recovery requires the original ECM operator and Dispatcharr connection.")


# Separate routes fail closed when the binding is missing or when a new
# frontend talks to an older backend. Ordinary profile callers are unchanged.
@router.get("/api/created-channel-profiles/catalog", tags=["Channel Profiles"])
async def get_recovery_catalog(request: Request, response: Response):
    """Load the picker catalog from the same destination as the tab's settings."""
    client = get_client()
    if not recovery_destination_matches(request.headers.get("X-ECM-Profile-Destination", ""), client):
        raise HTTPException(409, "The Dispatcharr destination changed. Reload the channel manager.")
    response.headers["Cache-Control"] = "private, no-store"
    return await _get_channel_profiles(client)


@router.get("/api/created-channel-profiles", tags=["Channel Profiles"])
async def get_recovery_profiles(response: Response, client: DispatcharrClient = Depends(_recovery_client)):
    response.headers["Cache-Control"] = "private, no-store"
    return await _get_channel_profiles(client)


@router.get("/api/created-channel-profiles/{profile_id}", tags=["Channel Profiles"])
async def get_recovery_profile(profile_id: int, response: Response, client: DispatcharrClient = Depends(_recovery_client)):
    response.headers["Cache-Control"] = "private, no-store"
    return await _get_channel_profile(client, profile_id)


@router.patch("/api/created-channel-profiles/{profile_id}/channels/bulk-update", tags=["Channel Profiles"])
async def bulk_update_recovery_profile(profile_id: int, request: Request, client: DispatcharrClient = Depends(_recovery_client)):
    return await _bulk_update_profile_channels(client, profile_id, request)


@router.patch("/api/created-channel-profiles/{profile_id}/channels/{channel_id}", tags=["Channel Profiles"])
async def update_recovery_profile(
    profile_id: int, channel_id: int, request: Request, client: DispatcharrClient = Depends(_recovery_client),
):
    return await _update_profile_channel(client, profile_id, channel_id, request)
