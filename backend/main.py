from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import os
import re
import logging

from dispatcharr_client import get_client, reset_client
from config import (
    get_settings,
    save_settings,
    clear_settings_cache,
    DispatcharrSettings,
    log_config_status,
    CONFIG_DIR,
    CONFIG_FILE,
)
from cache import get_cache
from database import init_db
import journal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Enhanced Channel Manager",
    description="Drag-and-drop channel management for Dispatcharr",
    version="0.2.20007",
)

# CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    """Log configuration status on startup."""
    logger.info("=" * 60)
    logger.info("Enhanced Channel Manager starting up")

    # Initialize journal database
    init_db()
    logger.info(f"CONFIG_DIR: {CONFIG_DIR}")
    logger.info(f"CONFIG_FILE: {CONFIG_FILE}")
    logger.info(f"CONFIG_DIR exists: {CONFIG_DIR.exists()}")
    logger.info(f"CONFIG_FILE exists: {CONFIG_FILE.exists()}")

    if CONFIG_DIR.exists():
        try:
            contents = list(CONFIG_DIR.iterdir())
            logger.info(f"CONFIG_DIR contents: {[str(p) for p in contents]}")
        except Exception as e:
            logger.error(f"Failed to list CONFIG_DIR: {e}")

    # Load settings to log status
    settings = get_settings()
    logger.info(f"Settings configured: {settings.is_configured()}")
    if settings.url:
        logger.info(f"Dispatcharr URL: {settings.url}")
    logger.info("=" * 60)


# Request models
class AddStreamRequest(BaseModel):
    stream_id: int


class RemoveStreamRequest(BaseModel):
    stream_id: int


class ReorderStreamsRequest(BaseModel):
    stream_ids: list[int]


class AssignNumbersRequest(BaseModel):
    channel_ids: list[int]
    starting_number: Optional[float] = None


class CreateChannelGroupRequest(BaseModel):
    name: str


# Health check
@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "enhanced-channel-manager"}


# Settings
class SettingsRequest(BaseModel):
    url: str
    username: str
    password: Optional[str] = None  # Optional - only required if changing auth settings
    auto_rename_channel_number: bool = False
    include_channel_number_in_name: bool = False
    channel_number_separator: str = "-"
    remove_country_prefix: bool = False
    include_country_in_name: bool = False
    country_separator: str = "|"
    timezone_preference: str = "both"
    show_stream_urls: bool = True
    hide_auto_sync_groups: bool = False
    hide_ungrouped_streams: bool = True
    theme: str = "dark"
    default_channel_profile_ids: list[int] = []
    linked_m3u_accounts: list[list[int]] = []
    epg_auto_match_threshold: int = 80
    custom_network_prefixes: list[str] = []
    custom_network_suffixes: list[str] = []


class SettingsResponse(BaseModel):
    url: str
    username: str
    configured: bool
    auto_rename_channel_number: bool
    include_channel_number_in_name: bool
    channel_number_separator: str
    remove_country_prefix: bool
    include_country_in_name: bool
    country_separator: str
    timezone_preference: str
    show_stream_urls: bool
    hide_auto_sync_groups: bool
    hide_ungrouped_streams: bool
    theme: str
    default_channel_profile_ids: list[int]
    linked_m3u_accounts: list[list[int]]
    epg_auto_match_threshold: int
    custom_network_prefixes: list[str]
    custom_network_suffixes: list[str]


class TestConnectionRequest(BaseModel):
    url: str
    username: str
    password: str


@app.get("/api/settings")
async def get_current_settings():
    """Get current settings (password masked)."""
    settings = get_settings()
    return SettingsResponse(
        url=settings.url,
        username=settings.username,
        configured=settings.is_configured(),
        auto_rename_channel_number=settings.auto_rename_channel_number,
        include_channel_number_in_name=settings.include_channel_number_in_name,
        channel_number_separator=settings.channel_number_separator,
        remove_country_prefix=settings.remove_country_prefix,
        include_country_in_name=settings.include_country_in_name,
        country_separator=settings.country_separator,
        timezone_preference=settings.timezone_preference,
        show_stream_urls=settings.show_stream_urls,
        hide_auto_sync_groups=settings.hide_auto_sync_groups,
        hide_ungrouped_streams=settings.hide_ungrouped_streams,
        theme=settings.theme,
        default_channel_profile_ids=settings.default_channel_profile_ids,
        linked_m3u_accounts=settings.linked_m3u_accounts,
        epg_auto_match_threshold=settings.epg_auto_match_threshold,
        custom_network_prefixes=settings.custom_network_prefixes,
        custom_network_suffixes=settings.custom_network_suffixes,
    )


@app.post("/api/settings")
async def update_settings(request: SettingsRequest):
    """Update Dispatcharr connection settings."""
    current_settings = get_settings()

    # If password is not provided, keep the existing password
    # This allows updating non-auth settings without re-entering password
    password = request.password if request.password else current_settings.password

    # Check if auth settings are being changed and password is required
    auth_changed = (
        request.url != current_settings.url or
        request.username != current_settings.username
    )
    if auth_changed and not request.password:
        raise HTTPException(
            status_code=400,
            detail="Password is required when changing URL or username"
        )

    new_settings = DispatcharrSettings(
        url=request.url,
        username=request.username,
        password=password,
        auto_rename_channel_number=request.auto_rename_channel_number,
        include_channel_number_in_name=request.include_channel_number_in_name,
        channel_number_separator=request.channel_number_separator,
        remove_country_prefix=request.remove_country_prefix,
        include_country_in_name=request.include_country_in_name,
        country_separator=request.country_separator,
        timezone_preference=request.timezone_preference,
        show_stream_urls=request.show_stream_urls,
        hide_auto_sync_groups=request.hide_auto_sync_groups,
        hide_ungrouped_streams=request.hide_ungrouped_streams,
        theme=request.theme,
        default_channel_profile_ids=request.default_channel_profile_ids,
        linked_m3u_accounts=request.linked_m3u_accounts,
        epg_auto_match_threshold=request.epg_auto_match_threshold,
        custom_network_prefixes=request.custom_network_prefixes,
        custom_network_suffixes=request.custom_network_suffixes,
    )
    save_settings(new_settings)
    clear_settings_cache()
    reset_client()
    return {"status": "saved", "configured": new_settings.is_configured()}


@app.post("/api/settings/test")
async def test_connection(request: TestConnectionRequest):
    """Test connection to Dispatcharr with provided credentials."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = request.url.rstrip("/")
            response = await client.post(
                f"{url}/api/accounts/token/",
                json={
                    "username": request.username,
                    "password": request.password,
                },
            )
            if response.status_code == 200:
                return {"success": True, "message": "Connection successful"}
            else:
                return {
                    "success": False,
                    "message": f"Authentication failed: {response.status_code}",
                }
    except httpx.ConnectError:
        return {"success": False, "message": "Could not connect to server"}
    except httpx.TimeoutException:
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        return {"success": False, "message": str(e)}


# Channels
class CreateChannelRequest(BaseModel):
    name: str
    channel_number: Optional[float] = None
    channel_group_id: Optional[int] = None
    logo_id: Optional[int] = None
    tvg_id: Optional[str] = None


@app.get("/api/channels")
async def get_channels(
    page: int = 1,
    page_size: int = 100,
    search: Optional[str] = None,
    channel_group: Optional[int] = None,
):
    client = get_client()
    try:
        return await client.get_channels(
            page=page,
            page_size=page_size,
            search=search,
            channel_group=channel_group,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels")
async def create_channel(request: CreateChannelRequest):
    client = get_client()
    try:
        data = {"name": request.name}
        if request.channel_number is not None:
            data["channel_number"] = request.channel_number
        if request.channel_group_id is not None:
            data["channel_group_id"] = request.channel_group_id
        if request.logo_id is not None:
            data["logo_id"] = request.logo_id
        if request.tvg_id is not None:
            data["tvg_id"] = request.tvg_id
        result = await client.create_channel(data)
        logger.info(f"Created channel: id={result.get('id')}, name={result.get('name')}, number={result.get('channel_number')}")

        # Log to journal
        journal.log_entry(
            category="channel",
            action_type="create",
            entity_id=result.get("id"),
            entity_name=result.get("name", "Unknown"),
            description=f"Created channel '{result.get('name')}'" + (f" with number {result.get('channel_number')}" if result.get('channel_number') else ""),
            after_value={"channel_number": result.get("channel_number"), "name": result.get("name")},
        )

        return result
    except Exception as e:
        logger.error(f"Channel creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Logos - MUST be defined before /api/channels/{channel_id} routes
class CreateLogoRequest(BaseModel):
    name: str
    url: str


@app.get("/api/channels/logos")
async def get_logos(
    page: int = 1,
    page_size: int = 100,
    search: Optional[str] = None,
):
    client = get_client()
    try:
        return await client.get_logos(page=page, page_size=page_size, search=search)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channels/logos/{logo_id}")
async def get_logo(logo_id: int):
    client = get_client()
    try:
        return await client.get_logo(logo_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/logos")
async def create_logo(request: CreateLogoRequest):
    client = get_client()
    try:
        result = await client.create_logo({"name": request.name, "url": request.url})
        logger.info(f"Created new logo: id={result.get('id')}, name={result.get('name')}")
        return result
    except Exception as e:
        error_str = str(e)
        # Check if this is a "logo already exists" error from Dispatcharr
        if "logo with this url already exists" in error_str.lower() or "400" in error_str:
            try:
                existing_logo = await client.find_logo_by_url(request.url)
                if existing_logo:
                    logger.info(f"Found existing logo: id={existing_logo.get('id')}, name={existing_logo.get('name')}, url={existing_logo.get('url')}")
                    return existing_logo
                else:
                    logger.warning(f"Logo exists but could not find it by URL: {request.url}")
            except Exception as search_err:
                logger.error(f"Error searching for existing logo: {search_err}")
        logger.error(f"Logo creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/channels/logos/{logo_id}")
async def update_logo(logo_id: int, data: dict):
    client = get_client()
    try:
        return await client.update_logo(logo_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channels/logos/{logo_id}")
async def delete_logo(logo_id: int):
    client = get_client()
    try:
        await client.delete_logo(logo_id)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Channel by ID routes - must come after /api/channels/logos
@app.get("/api/channels/{channel_id}")
async def get_channel(channel_id: int):
    client = get_client()
    try:
        return await client.get_channel(channel_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channels/{channel_id}/streams")
async def get_channel_streams(channel_id: int):
    client = get_client()
    try:
        return await client.get_channel_streams(channel_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/channels/{channel_id}")
async def update_channel(channel_id: int, data: dict):
    client = get_client()
    try:
        # Get before state for logging
        before_channel = await client.get_channel(channel_id)

        result = await client.update_channel(channel_id, data)

        # Determine what changed for description and build before/after values
        changes = []
        before_value = {}
        after_value = {}

        if "name" in data and data["name"] != before_channel.get("name"):
            changes.append(f"name to '{data['name']}'")
            before_value["name"] = before_channel.get("name")
            after_value["name"] = data["name"]

        if "channel_number" in data and data["channel_number"] != before_channel.get("channel_number"):
            changes.append(f"number to {data['channel_number']}")
            before_value["channel_number"] = before_channel.get("channel_number")
            after_value["channel_number"] = data["channel_number"]

        if "tvg_id" in data and data["tvg_id"] != before_channel.get("tvg_id"):
            old_tvg = before_channel.get("tvg_id")
            new_tvg = data["tvg_id"]
            if new_tvg:
                changes.append(f"EPG mapping to '{new_tvg}'")
            else:
                changes.append("cleared EPG mapping")
            before_value["tvg_id"] = old_tvg
            after_value["tvg_id"] = new_tvg

        if "logo_id" in data and data["logo_id"] != before_channel.get("logo_id"):
            old_logo = before_channel.get("logo_id")
            new_logo = data["logo_id"]
            if new_logo:
                changes.append("logo")
            else:
                changes.append("cleared logo")
            before_value["logo_id"] = old_logo
            after_value["logo_id"] = new_logo

        if changes:
            journal.log_entry(
                category="channel",
                action_type="update",
                entity_id=channel_id,
                entity_name=result.get("name", before_channel.get("name", "Unknown")),
                description=f"Updated channel: {', '.join(changes)}",
                before_value=before_value,
                after_value=after_value,
            )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channels/{channel_id}")
async def delete_channel(channel_id: int):
    client = get_client()
    try:
        # Get channel info before deleting for logging
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")

        await client.delete_channel(channel_id)

        # Log to journal
        journal.log_entry(
            category="channel",
            action_type="delete",
            entity_id=channel_id,
            entity_name=channel_name,
            description=f"Deleted channel '{channel_name}'",
            before_value={"name": channel_name, "channel_number": channel.get("channel_number")},
        )

        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/{channel_id}/add-stream")
async def add_stream_to_channel(channel_id: int, request: AddStreamRequest):
    client = get_client()
    try:
        # Get current channel
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")
        current_streams = channel.get("streams", [])

        # Add stream if not already present
        if request.stream_id not in current_streams:
            before_streams = list(current_streams)
            current_streams.append(request.stream_id)
            result = await client.update_channel(channel_id, {"streams": current_streams})

            # Log to journal
            journal.log_entry(
                category="channel",
                action_type="stream_add",
                entity_id=channel_id,
                entity_name=channel_name,
                description=f"Added stream to channel '{channel_name}'",
                before_value={"streams": before_streams},
                after_value={"streams": current_streams},
            )

            return result
        return channel
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/{channel_id}/remove-stream")
async def remove_stream_from_channel(channel_id: int, request: RemoveStreamRequest):
    client = get_client()
    try:
        # Get current channel
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")
        current_streams = channel.get("streams", [])

        # Remove stream if present
        if request.stream_id in current_streams:
            before_streams = list(current_streams)
            current_streams.remove(request.stream_id)
            result = await client.update_channel(channel_id, {"streams": current_streams})

            # Log to journal
            journal.log_entry(
                category="channel",
                action_type="stream_remove",
                entity_id=channel_id,
                entity_name=channel_name,
                description=f"Removed stream from channel '{channel_name}'",
                before_value={"streams": before_streams},
                after_value={"streams": current_streams},
            )

            return result
        return channel
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/{channel_id}/reorder-streams")
async def reorder_channel_streams(channel_id: int, request: ReorderStreamsRequest):
    client = get_client()
    try:
        # Get before state
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")
        before_streams = channel.get("streams", [])

        result = await client.update_channel(channel_id, {"streams": request.stream_ids})

        # Log to journal
        journal.log_entry(
            category="channel",
            action_type="stream_reorder",
            entity_id=channel_id,
            entity_name=channel_name,
            description=f"Reordered streams in channel '{channel_name}'",
            before_value={"streams": before_streams},
            after_value={"streams": request.stream_ids},
        )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/assign-numbers")
async def assign_channel_numbers(request: AssignNumbersRequest):
    client = get_client()
    settings = get_settings()

    try:
        # Get current channel data for all affected channels (needed for journal and auto-rename)
        import uuid
        batch_id = str(uuid.uuid4())[:8]
        channels_before = {}
        name_updates = {}

        for idx, channel_id in enumerate(request.channel_ids):
            channel = await client.get_channel(channel_id)
            channels_before[channel_id] = {
                "name": channel.get("name", ""),
                "channel_number": channel.get("channel_number"),
            }

            # If auto-rename is enabled, calculate name updates
            if settings.auto_rename_channel_number and request.starting_number is not None:
                old_number = channel.get("channel_number")
                new_number = request.starting_number + idx
                channel_name = channel.get("name", "")

                if old_number is not None and old_number != new_number and channel_name:
                    # Check if channel name contains the old number
                    old_number_str = str(int(old_number) if old_number == int(old_number) else old_number)
                    new_number_str = str(int(new_number) if new_number == int(new_number) else new_number)
                    # Match the number as a standalone value (not part of a larger number)
                    pattern = re.compile(r'(^|[^0-9])' + re.escape(old_number_str) + r'([^0-9]|$)')
                    if pattern.search(channel_name):
                        new_name = pattern.sub(r'\g<1>' + new_number_str + r'\g<2>', channel_name)
                        if new_name != channel_name:
                            name_updates[channel_id] = new_name

        # Call the bulk assign API
        result = await client.assign_channel_numbers(
            request.channel_ids, request.starting_number
        )

        # Apply name updates if any
        for channel_id, new_name in name_updates.items():
            try:
                await client.update_channel(channel_id, {"name": new_name})
            except Exception:
                # Don't fail the whole operation if a name update fails
                pass

        # Log individual journal entries for each channel
        for idx, channel_id in enumerate(request.channel_ids):
            before_data = channels_before.get(channel_id, {})
            old_number = before_data.get("channel_number")
            new_number = request.starting_number + idx
            channel_name = before_data.get("name", f"Channel {channel_id}")
            new_name = name_updates.get(channel_id, channel_name)

            journal.log_entry(
                category="channel",
                action_type="reorder",
                entity_id=channel_id,
                entity_name=channel_name,
                description=f"Changed channel number from {old_number} to {new_number}",
                before_value={"channel_number": old_number, "name": channel_name},
                after_value={"channel_number": new_number, "name": new_name},
                batch_id=batch_id,
            )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Channel Groups
@app.get("/api/channel-groups")
async def get_channel_groups():
    client = get_client()
    try:
        return await client.get_channel_groups()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channel-groups")
async def create_channel_group(request: CreateChannelGroupRequest):
    client = get_client()
    try:
        result = await client.create_channel_group(request.name)
        logger.info(f"Created channel group: id={result.get('id')}, name={result.get('name')}")
        return result
    except Exception as e:
        error_str = str(e)
        # Check if this is a "group already exists" error from Dispatcharr
        if "400" in error_str or "already exists" in error_str.lower():
            try:
                # Look up the existing group by name
                groups = await client.get_channel_groups()
                for group in groups:
                    if group.get("name") == request.name:
                        logger.info(f"Found existing channel group: id={group.get('id')}, name={group.get('name')}")
                        return group
                logger.warning(f"Group exists error but could not find group by name: {request.name}")
            except Exception as search_err:
                logger.error(f"Error searching for existing group: {search_err}")
        logger.error(f"Channel group creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/channel-groups/{group_id}")
async def update_channel_group(group_id: int, data: dict):
    client = get_client()
    try:
        return await client.update_channel_group(group_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channel-groups/{group_id}")
async def delete_channel_group(group_id: int):
    client = get_client()
    try:
        await client.delete_channel_group(group_id)
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Streams
@app.get("/api/streams")
async def get_streams(
    page: int = 1,
    page_size: int = 100,
    search: Optional[str] = None,
    channel_group_name: Optional[str] = None,
    m3u_account: Optional[int] = None,
    bypass_cache: bool = False,
):
    cache = get_cache()
    cache_key = f"streams:p{page}:ps{page_size}:s{search or ''}:g{channel_group_name or ''}:m{m3u_account or ''}"

    # Try cache first (unless bypassed)
    if not bypass_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Returning cached streams for {cache_key}")
            return cached

    client = get_client()
    try:
        result = await client.get_streams(
            page=page,
            page_size=page_size,
            search=search,
            channel_group_name=channel_group_name,
            m3u_account=m3u_account,
        )

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
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stream-groups")
async def get_stream_groups(bypass_cache: bool = False):
    cache = get_cache()
    cache_key = "stream_groups"

    # Try cache first (unless bypassed)
    if not bypass_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    client = get_client()
    try:
        result = await client.get_stream_groups()
        cache.set(cache_key, result)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cache/invalidate")
async def invalidate_cache(prefix: Optional[str] = None):
    """Invalidate cached data. If prefix is provided, only invalidate matching keys."""
    cache = get_cache()
    if prefix:
        count = cache.invalidate_prefix(prefix)
        return {"message": f"Invalidated {count} cache entries with prefix '{prefix}'"}
    else:
        count = cache.clear()
        return {"message": f"Cleared entire cache ({count} entries)"}


@app.get("/api/cache/stats")
async def cache_stats():
    """Get cache statistics."""
    cache = get_cache()
    return cache.stats()


# Providers (M3U Accounts)
@app.get("/api/providers")
async def get_providers():
    client = get_client()
    try:
        return await client.get_m3u_accounts()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/providers/group-settings")
async def get_all_provider_group_settings():
    """Get group settings from all M3U providers, mapped by channel_group_id."""
    client = get_client()
    try:
        return await client.get_all_m3u_group_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# EPG Sources
@app.get("/api/epg/sources")
async def get_epg_sources():
    client = get_client()
    try:
        return await client.get_epg_sources()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/epg/sources/{source_id}")
async def get_epg_source(source_id: int):
    client = get_client()
    try:
        return await client.get_epg_source(source_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/epg/sources")
async def create_epg_source(request: Request):
    client = get_client()
    try:
        data = await request.json()
        result = await client.create_epg_source(data)

        # Log to journal
        journal.log_entry(
            category="epg",
            action_type="create",
            entity_id=result.get("id"),
            entity_name=result.get("name", data.get("name", "Unknown")),
            description=f"Created EPG source '{result.get('name', data.get('name'))}'",
            after_value={"name": result.get("name"), "url": data.get("url")},
        )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/epg/sources/{source_id}")
async def update_epg_source(source_id: int, request: Request):
    client = get_client()
    try:
        # Get before state
        before_source = await client.get_epg_source(source_id)
        data = await request.json()
        result = await client.update_epg_source(source_id, data)

        # Log to journal
        journal.log_entry(
            category="epg",
            action_type="update",
            entity_id=source_id,
            entity_name=result.get("name", before_source.get("name", "Unknown")),
            description=f"Updated EPG source '{result.get('name', before_source.get('name'))}'",
            before_value={"name": before_source.get("name"), "enabled": before_source.get("enabled")},
            after_value=data,
        )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/epg/sources/{source_id}")
async def delete_epg_source(source_id: int):
    client = get_client()
    try:
        # Get source info before deleting
        source = await client.get_epg_source(source_id)
        source_name = source.get("name", "Unknown")

        await client.delete_epg_source(source_id)

        # Log to journal
        journal.log_entry(
            category="epg",
            action_type="delete",
            entity_id=source_id,
            entity_name=source_name,
            description=f"Deleted EPG source '{source_name}'",
            before_value={"name": source_name},
        )

        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/epg/sources/{source_id}/refresh")
async def refresh_epg_source(source_id: int):
    client = get_client()
    try:
        # Get source info
        source = await client.get_epg_source(source_id)
        source_name = source.get("name", "Unknown")

        result = await client.refresh_epg_source(source_id)

        # Log to journal
        journal.log_entry(
            category="epg",
            action_type="refresh",
            entity_id=source_id,
            entity_name=source_name,
            description=f"Refreshed EPG source '{source_name}'",
        )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/epg/import")
async def trigger_epg_import():
    client = get_client()
    try:
        return await client.trigger_epg_import()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# EPG Data
@app.get("/api/epg/data")
async def get_epg_data(
    page: int = 1,
    page_size: int = 100,
    search: Optional[str] = None,
    epg_source: Optional[int] = None,
):
    client = get_client()
    try:
        return await client.get_epg_data(
            page=page,
            page_size=page_size,
            search=search,
            epg_source=epg_source,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/epg/data/{data_id}")
async def get_epg_data_by_id(data_id: int):
    client = get_client()
    try:
        return await client.get_epg_data_by_id(data_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/epg/grid")
async def get_epg_grid(start: Optional[str] = None, end: Optional[str] = None):
    """Get EPG grid (programs from previous hour + next 24 hours).

    Optionally accepts start and end datetime parameters in ISO format.
    """
    client = get_client()
    try:
        return await client.get_epg_grid(start=start, end=end)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Stream Profiles
@app.get("/api/stream-profiles")
async def get_stream_profiles():
    client = get_client()
    try:
        return await client.get_stream_profiles()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------------------
# Channel Profiles
# -------------------------------------------------------------------------

@app.get("/api/channel-profiles")
async def get_channel_profiles():
    """Get all channel profiles."""
    client = get_client()
    try:
        return await client.get_channel_profiles()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channel-profiles")
async def create_channel_profile(request: Request):
    """Create a new channel profile."""
    client = get_client()
    try:
        data = await request.json()
        return await client.create_channel_profile(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channel-profiles/{profile_id}")
async def get_channel_profile(profile_id: int):
    """Get a single channel profile."""
    client = get_client()
    try:
        return await client.get_channel_profile(profile_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/channel-profiles/{profile_id}")
async def update_channel_profile(profile_id: int, request: Request):
    """Update a channel profile."""
    client = get_client()
    try:
        data = await request.json()
        return await client.update_channel_profile(profile_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channel-profiles/{profile_id}")
async def delete_channel_profile(profile_id: int):
    """Delete a channel profile."""
    client = get_client()
    try:
        await client.delete_channel_profile(profile_id)
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/channel-profiles/{profile_id}/channels/bulk-update")
async def bulk_update_profile_channels(profile_id: int, request: Request):
    """Bulk enable/disable channels for a profile."""
    client = get_client()
    try:
        data = await request.json()
        return await client.bulk_update_profile_channels(profile_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/channel-profiles/{profile_id}/channels/{channel_id}")
async def update_profile_channel(profile_id: int, channel_id: int, request: Request):
    """Enable/disable a single channel for a profile."""
    client = get_client()
    try:
        data = await request.json()
        return await client.update_profile_channel(profile_id, channel_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------------------
# M3U Account Management
# -------------------------------------------------------------------------

@app.get("/api/m3u/accounts/{account_id}")
async def get_m3u_account(account_id: int):
    """Get a single M3U account by ID."""
    client = get_client()
    try:
        return await client.get_m3u_account(account_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/m3u/accounts")
async def create_m3u_account(request: Request):
    """Create a new M3U account."""
    client = get_client()
    try:
        data = await request.json()
        result = await client.create_m3u_account(data)

        # Log to journal
        journal.log_entry(
            category="m3u",
            action_type="create",
            entity_id=result.get("id"),
            entity_name=result.get("name", data.get("name", "Unknown")),
            description=f"Created M3U account '{result.get('name', data.get('name'))}'",
            after_value={"name": result.get("name"), "server_url": data.get("server_url")},
        )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/m3u/accounts/{account_id}")
async def update_m3u_account(account_id: int, request: Request):
    """Update an M3U account (full update)."""
    client = get_client()
    try:
        before_account = await client.get_m3u_account(account_id)
        data = await request.json()
        result = await client.update_m3u_account(account_id, data)

        # Log to journal
        journal.log_entry(
            category="m3u",
            action_type="update",
            entity_id=account_id,
            entity_name=result.get("name", before_account.get("name", "Unknown")),
            description=f"Updated M3U account '{result.get('name', before_account.get('name'))}'",
            before_value={"name": before_account.get("name")},
            after_value={"name": data.get("name")},
        )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/m3u/accounts/{account_id}")
async def patch_m3u_account(account_id: int, request: Request):
    """Partially update an M3U account (e.g., toggle is_active)."""
    client = get_client()
    try:
        before_account = await client.get_m3u_account(account_id)
        data = await request.json()
        result = await client.patch_m3u_account(account_id, data)

        # Log to journal
        changes = []
        if "is_active" in data:
            changes.append(f"{'enabled' if data['is_active'] else 'disabled'}")
        if "name" in data:
            changes.append(f"renamed to '{data['name']}'")

        if changes:
            journal.log_entry(
                category="m3u",
                action_type="update",
                entity_id=account_id,
                entity_name=result.get("name", before_account.get("name", "Unknown")),
                description=f"M3U account {', '.join(changes)}",
                before_value={"is_active": before_account.get("is_active")},
                after_value=data,
            )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/m3u/accounts/{account_id}")
async def delete_m3u_account(account_id: int):
    """Delete an M3U account."""
    client = get_client()
    try:
        # Get account info before deleting
        account = await client.get_m3u_account(account_id)
        account_name = account.get("name", "Unknown")

        await client.delete_m3u_account(account_id)

        # Log to journal
        journal.log_entry(
            category="m3u",
            action_type="delete",
            entity_id=account_id,
            entity_name=account_name,
            description=f"Deleted M3U account '{account_name}'",
            before_value={"name": account_name},
        )

        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------------------
# M3U Refresh
# -------------------------------------------------------------------------

@app.post("/api/m3u/refresh")
async def refresh_all_m3u_accounts():
    """Trigger refresh for all active M3U accounts."""
    client = get_client()
    try:
        return await client.refresh_all_m3u_accounts()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/m3u/refresh/{account_id}")
async def refresh_m3u_account(account_id: int):
    """Trigger refresh for a single M3U account."""
    client = get_client()
    try:
        # Get account info
        account = await client.get_m3u_account(account_id)
        account_name = account.get("name", "Unknown")

        result = await client.refresh_m3u_account(account_id)

        # Log to journal
        journal.log_entry(
            category="m3u",
            action_type="refresh",
            entity_id=account_id,
            entity_name=account_name,
            description=f"Refreshed M3U account '{account_name}'",
        )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/m3u/accounts/{account_id}/refresh-vod")
async def refresh_m3u_vod(account_id: int):
    """Refresh VOD content for an XtreamCodes account."""
    client = get_client()
    try:
        return await client.refresh_m3u_vod(account_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------------------
# M3U Filters
# -------------------------------------------------------------------------

@app.get("/api/m3u/accounts/{account_id}/filters")
async def get_m3u_filters(account_id: int):
    """Get all filters for an M3U account."""
    client = get_client()
    try:
        return await client.get_m3u_filters(account_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/m3u/accounts/{account_id}/filters")
async def create_m3u_filter(account_id: int, request: Request):
    """Create a new filter for an M3U account."""
    client = get_client()
    try:
        data = await request.json()
        return await client.create_m3u_filter(account_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/m3u/accounts/{account_id}/filters/{filter_id}")
async def update_m3u_filter(account_id: int, filter_id: int, request: Request):
    """Update a filter for an M3U account."""
    client = get_client()
    try:
        data = await request.json()
        return await client.update_m3u_filter(account_id, filter_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/m3u/accounts/{account_id}/filters/{filter_id}")
async def delete_m3u_filter(account_id: int, filter_id: int):
    """Delete a filter from an M3U account."""
    client = get_client()
    try:
        await client.delete_m3u_filter(account_id, filter_id)
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------------------
# M3U Group Settings
# -------------------------------------------------------------------------

@app.patch("/api/m3u/accounts/{account_id}/group-settings")
async def update_m3u_group_settings(account_id: int, request: Request):
    """Update group settings for an M3U account."""
    client = get_client()
    try:
        # Get account info and current group settings before update
        account = await client.get_m3u_account(account_id)
        account_name = account.get("name", "Unknown")
        # Store full settings for each group (all auto-sync related fields)
        before_groups = {}
        for g in account.get("channel_groups", []):
            before_groups[g.get("channel_group")] = {
                "enabled": g.get("enabled"),
                "auto_channel_sync": g.get("auto_channel_sync"),
                "auto_sync_channel_start": g.get("auto_sync_channel_start"),
                "custom_properties": g.get("custom_properties"),
            }

        # Get channel groups for name lookup
        channel_groups = await client.get_channel_groups()
        group_name_map = {g["id"]: g["name"] for g in channel_groups}

        data = await request.json()
        result = await client.update_m3u_group_settings(account_id, data)

        # Log to journal - compare before/after states for all settings
        group_settings = data.get("group_settings", [])
        if group_settings:
            enabled_names = []
            disabled_names = []
            auto_sync_enabled_names = []
            auto_sync_disabled_names = []
            start_channel_changed = []
            settings_changed_names = []
            changed_groups = []

            for gs in group_settings:
                channel_group_id = gs.get("channel_group")
                before = before_groups.get(channel_group_id, {})
                group_name = group_name_map.get(channel_group_id, f"Group {channel_group_id}")

                changes_for_group = {}

                # Check enabled change
                new_enabled = gs.get("enabled")
                old_enabled = before.get("enabled")
                if old_enabled is not None and new_enabled != old_enabled:
                    if new_enabled:
                        enabled_names.append(group_name)
                    else:
                        disabled_names.append(group_name)
                    changes_for_group["enabled"] = {"was": old_enabled, "now": new_enabled}

                # Check auto_channel_sync change
                new_auto_sync = gs.get("auto_channel_sync")
                old_auto_sync = before.get("auto_channel_sync")
                if old_auto_sync is not None and new_auto_sync != old_auto_sync:
                    if new_auto_sync:
                        auto_sync_enabled_names.append(group_name)
                    else:
                        auto_sync_disabled_names.append(group_name)
                    changes_for_group["auto_channel_sync"] = {"was": old_auto_sync, "now": new_auto_sync}

                # Check auto_sync_channel_start change
                new_start = gs.get("auto_sync_channel_start")
                old_start = before.get("auto_sync_channel_start")
                if old_start != new_start:
                    start_channel_changed.append(f"{group_name} ({old_start} → {new_start})")
                    changes_for_group["auto_sync_channel_start"] = {"was": old_start, "now": new_start}

                # Check custom_properties change
                # Normalize empty dict and None to be equivalent
                new_custom = gs.get("custom_properties")
                old_custom = before.get("custom_properties")
                # Treat empty dict {} as equivalent to None
                new_custom_normalized = new_custom if new_custom else None
                old_custom_normalized = old_custom if old_custom else None
                if old_custom_normalized != new_custom_normalized:
                    settings_changed_names.append(group_name)
                    changes_for_group["custom_properties"] = {"was": old_custom, "now": new_custom}

                if changes_for_group:
                    changed_groups.append({
                        "channel_group": channel_group_id,
                        "name": group_name,
                        "changes": changes_for_group,
                    })

            if changed_groups:
                changes = []
                if enabled_names:
                    changes.append(f"Enabled: {', '.join(enabled_names)}")
                if disabled_names:
                    changes.append(f"Disabled: {', '.join(disabled_names)}")
                if auto_sync_enabled_names:
                    changes.append(f"Auto-sync on: {', '.join(auto_sync_enabled_names)}")
                if auto_sync_disabled_names:
                    changes.append(f"Auto-sync off: {', '.join(auto_sync_disabled_names)}")
                if start_channel_changed:
                    changes.append(f"Start channel: {', '.join(start_channel_changed)}")
                if settings_changed_names:
                    changes.append(f"Settings: {', '.join(settings_changed_names)}")

                # Only include before state for groups that actually changed
                changed_group_ids = {g["channel_group"] for g in changed_groups}
                before_changed_only = {
                    gid: {**before_groups[gid], "name": group_name_map.get(gid, f"Group {gid}")}
                    for gid in changed_group_ids
                    if gid in before_groups
                }

                journal.log_entry(
                    category="m3u",
                    action_type="update",
                    entity_id=account_id,
                    entity_name=account_name,
                    description=f"Updated group settings - {'; '.join(changes)}",
                    before_value=before_changed_only,
                    after_value=changed_groups,
                )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------------------
# Server Groups
# -------------------------------------------------------------------------

@app.get("/api/m3u/server-groups")
async def get_server_groups():
    """Get all server groups."""
    client = get_client()
    try:
        return await client.get_server_groups()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/m3u/server-groups")
async def create_server_group(request: Request):
    """Create a new server group."""
    client = get_client()
    try:
        data = await request.json()
        result = await client.create_server_group(data)

        # Log to journal
        group_name = data.get("name", "Unknown")
        account_ids = data.get("account_ids", [])
        journal.log_entry(
            category="m3u",
            action_type="create",
            entity_id=result.get("id"),
            entity_name=group_name,
            description=f"Created server group '{group_name}' linking {len(account_ids)} M3U account(s)",
            after_value={"name": group_name, "account_ids": account_ids},
        )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/m3u/server-groups/{group_id}")
async def update_server_group(group_id: int, request: Request):
    """Update a server group."""
    client = get_client()
    try:
        # Get current group info
        groups = await client.get_server_groups()
        before_group = next((g for g in groups if g.get("id") == group_id), {})
        before_name = before_group.get("name", "Unknown")

        data = await request.json()
        result = await client.update_server_group(group_id, data)

        # Log to journal
        new_name = data.get("name", before_name)
        account_ids = data.get("account_ids", [])

        changes = []
        if "name" in data and data["name"] != before_name:
            changes.append(f"renamed to '{new_name}'")
        if "account_ids" in data:
            changes.append(f"updated to {len(account_ids)} M3U account(s)")

        if changes:
            journal.log_entry(
                category="m3u",
                action_type="update",
                entity_id=group_id,
                entity_name=new_name,
                description=f"Updated server group: {', '.join(changes)}",
                before_value={"name": before_name, "account_ids": before_group.get("account_ids", [])},
                after_value=data,
            )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/m3u/server-groups/{group_id}")
async def delete_server_group(group_id: int):
    """Delete a server group."""
    client = get_client()
    try:
        # Get group info before deleting
        groups = await client.get_server_groups()
        group = next((g for g in groups if g.get("id") == group_id), {})
        group_name = group.get("name", "Unknown")

        await client.delete_server_group(group_id)

        # Log to journal
        journal.log_entry(
            category="m3u",
            action_type="delete",
            entity_id=group_id,
            entity_name=group_name,
            description=f"Deleted server group '{group_name}'",
            before_value={"name": group_name, "account_ids": group.get("account_ids", [])},
        )

        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Journal API
@app.get("/api/journal")
async def get_journal_entries(
    page: int = 1,
    page_size: int = 50,
    category: Optional[str] = None,
    action_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    user_initiated: Optional[bool] = None,
):
    """Query journal entries with filtering and pagination."""
    from datetime import datetime

    # Parse date strings to datetime
    date_from_dt = None
    date_to_dt = None
    if date_from:
        try:
            date_from_dt = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
        except ValueError:
            pass
    if date_to:
        try:
            date_to_dt = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
        except ValueError:
            pass

    # Validate page_size
    page_size = min(max(page_size, 1), 200)

    return journal.get_entries(
        page=page,
        page_size=page_size,
        category=category,
        action_type=action_type,
        date_from=date_from_dt,
        date_to=date_to_dt,
        search=search,
        user_initiated=user_initiated,
    )


@app.get("/api/journal/stats")
async def get_journal_stats():
    """Get summary statistics for the journal."""
    return journal.get_stats()


@app.delete("/api/journal/purge")
async def purge_journal_entries(days: int = 90):
    """Delete journal entries older than the specified number of days."""
    deleted_count = journal.purge_old_entries(days=days)
    return {"deleted": deleted_count, "days": days}


# =============================================================================
# Stats & Monitoring
# =============================================================================


@app.get("/api/stats/channels")
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


@app.get("/api/stats/channels/{channel_id}")
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


@app.get("/api/stats/events")
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


@app.post("/api/stats/channels/{channel_id}/stop")
async def stop_channel(channel_id: str):
    """Stop a channel and release all associated resources."""
    client = get_client()
    try:
        return await client.stop_channel(channel_id)
    except Exception as e:
        logger.error(f"Failed to stop channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stats/channels/{channel_id}/stop-client")
async def stop_client(channel_id: str):
    """Stop a specific client connection."""
    client = get_client()
    try:
        return await client.stop_client(channel_id)
    except Exception as e:
        logger.error(f"Failed to stop client for channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Serve static files in production
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount(
        "/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets"
    )

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Serve index.html for all non-API routes (SPA routing)
        index_path = os.path.join(static_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"error": "Frontend not built"}
