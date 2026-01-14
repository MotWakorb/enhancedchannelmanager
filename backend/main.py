from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import httpx
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
    get_log_level_from_env,
    set_log_level,
)
from cache import get_cache
from database import init_db
import journal
from bandwidth_tracker import BandwidthTracker, set_tracker, get_tracker
from stream_prober import StreamProber, set_prober, get_prober

# Configure logging
# Start with environment variable, will be updated from settings in startup
initial_log_level = get_log_level_from_env()
logging.basicConfig(
    level=getattr(logging, initial_log_level),
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
    logger.info(f"Initial log level from environment: {initial_log_level}")

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

    # Load settings to log status and apply log level from settings
    settings = get_settings()
    logger.info(f"Settings configured: {settings.is_configured()}")
    if settings.url:
        logger.info(f"Dispatcharr URL: {settings.url}")

    # Apply log level from settings (overrides environment variable)
    if settings.backend_log_level:
        set_log_level(settings.backend_log_level)
        logger.info(f"Applied log level from settings: {settings.backend_log_level}")

    # Start bandwidth tracker if configured
    if settings.is_configured():
        try:
            logger.debug(f"Starting bandwidth tracker with poll interval {settings.stats_poll_interval}s")
            tracker = BandwidthTracker(get_client(), poll_interval=settings.stats_poll_interval)
            set_tracker(tracker)
            await tracker.start()
            logger.info("Bandwidth tracker started successfully")
        except Exception as e:
            logger.error(f"Failed to start bandwidth tracker: {e}", exc_info=True)

        # Always create stream prober for on-demand probing support
        # Scheduled probing is controlled by probe_enabled flag
        try:
            logger.debug(
                f"Initializing stream prober (scheduled: {settings.stream_probe_enabled}, "
                f"schedule: {settings.stream_probe_schedule_time}, "
                f"interval: {settings.stream_probe_interval_hours}h, "
                f"batch: {settings.stream_probe_batch_size}, timeout: {settings.stream_probe_timeout}s)"
            )
            prober = StreamProber(
                get_client(),
                probe_timeout=settings.stream_probe_timeout,
                probe_batch_size=settings.stream_probe_batch_size,
                probe_interval_hours=settings.stream_probe_interval_hours,
                probe_enabled=settings.stream_probe_enabled,
                schedule_time=settings.stream_probe_schedule_time,
                user_timezone=settings.user_timezone,
                probe_channel_groups=settings.probe_channel_groups,
            )
            logger.info(f"StreamProber instance created: {prober is not None}")

            set_prober(prober)
            logger.info("set_prober() called successfully")

            await prober.start()
            logger.info("prober.start() completed")

            # Verify prober is accessible via get_prober()
            test_prober = get_prober()
            logger.info(f"Verification: get_prober() returns: {test_prober is not None}")

            if settings.stream_probe_enabled:
                logger.info("Stream prober initialized with scheduled probing enabled")
            else:
                logger.info("Stream prober initialized (scheduled probing disabled, on-demand available)")
        except Exception as e:
            logger.error(f"Failed to initialize stream prober: {e}", exc_info=True)
            logger.error("Stream probing will not be available!")

    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on shutdown."""
    logger.info("Enhanced Channel Manager shutting down")

    # Stop bandwidth tracker
    tracker = get_tracker()
    if tracker:
        await tracker.stop()

    # Stop stream prober
    prober = get_prober()
    if prober:
        await prober.stop()


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


class DeleteOrphanedGroupsRequest(BaseModel):
    group_ids: list[int] | None = None  # Optional list of group IDs to delete


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
    stats_poll_interval: int = 10
    user_timezone: str = ""
    backend_log_level: str = "INFO"
    frontend_log_level: str = "INFO"
    vlc_open_behavior: str = "m3u_fallback"
    # Stream probe settings
    stream_probe_enabled: bool = True
    stream_probe_interval_hours: int = 24
    stream_probe_batch_size: int = 10
    stream_probe_timeout: int = 30
    stream_probe_schedule_time: str = "03:00"  # HH:MM format, 24h
    probe_channel_groups: list[str] = []  # Channel groups to probe


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
    stats_poll_interval: int
    user_timezone: str
    backend_log_level: str
    frontend_log_level: str
    vlc_open_behavior: str
    # Stream probe settings
    stream_probe_enabled: bool
    stream_probe_interval_hours: int
    stream_probe_batch_size: int
    stream_probe_timeout: int
    stream_probe_schedule_time: str  # HH:MM format, 24h
    probe_channel_groups: list[str]


class TestConnectionRequest(BaseModel):
    url: str
    username: str
    password: str


@app.get("/api/settings")
async def get_current_settings():
    """Get current settings (password masked)."""
    logger.debug("GET /api/settings - Retrieving current settings")
    settings = get_settings()
    logger.info(f"Settings retrieved - configured: {settings.is_configured()}, log level: {settings.backend_log_level}")
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
        stats_poll_interval=settings.stats_poll_interval,
        user_timezone=settings.user_timezone,
        backend_log_level=settings.backend_log_level,
        frontend_log_level=settings.frontend_log_level,
        vlc_open_behavior=settings.vlc_open_behavior,
        stream_probe_enabled=settings.stream_probe_enabled,
        stream_probe_interval_hours=settings.stream_probe_interval_hours,
        stream_probe_batch_size=settings.stream_probe_batch_size,
        stream_probe_timeout=settings.stream_probe_timeout,
        stream_probe_schedule_time=settings.stream_probe_schedule_time,
        probe_channel_groups=settings.probe_channel_groups,
    )


@app.post("/api/settings")
async def update_settings(request: SettingsRequest):
    """Update Dispatcharr connection settings."""
    logger.debug(f"POST /api/settings - Updating settings (URL: {request.url}, username: {request.username})")
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
        logger.warning("Settings update failed: password required when changing URL or username")
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
        stats_poll_interval=request.stats_poll_interval,
        user_timezone=request.user_timezone,
        backend_log_level=request.backend_log_level,
        frontend_log_level=request.frontend_log_level,
        vlc_open_behavior=request.vlc_open_behavior,
        stream_probe_enabled=request.stream_probe_enabled,
        stream_probe_interval_hours=request.stream_probe_interval_hours,
        stream_probe_batch_size=request.stream_probe_batch_size,
        stream_probe_timeout=request.stream_probe_timeout,
        stream_probe_schedule_time=request.stream_probe_schedule_time,
        probe_channel_groups=request.probe_channel_groups,
    )
    save_settings(new_settings)
    clear_settings_cache()
    reset_client()

    # Apply backend log level immediately
    if new_settings.backend_log_level != current_settings.backend_log_level:
        logger.info(f"Applying new backend log level: {new_settings.backend_log_level}")
        set_log_level(new_settings.backend_log_level)

    logger.info(f"Settings saved successfully - configured: {new_settings.is_configured()}, auth_changed: {auth_changed}")
    return {"status": "saved", "configured": new_settings.is_configured()}


@app.post("/api/settings/test")
async def test_connection(request: TestConnectionRequest):
    """Test connection to Dispatcharr with provided credentials."""
    import httpx

    logger.debug(f"POST /api/settings/test - Testing connection to {request.url}")
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
                logger.info(f"Connection test successful - {url}")
                return {"success": True, "message": "Connection successful"}
            else:
                logger.warning(f"Connection test failed - {url} - status: {response.status_code}")
                return {
                    "success": False,
                    "message": f"Authentication failed: {response.status_code}",
                }
    except httpx.ConnectError as e:
        logger.error(f"Connection test failed - could not connect to {request.url}: {e}")
        return {"success": False, "message": "Could not connect to server"}
    except httpx.TimeoutException as e:
        logger.error(f"Connection test failed - timeout connecting to {request.url}: {e}")
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        logger.exception(f"Connection test failed - unexpected error: {e}")
        return {"success": False, "message": str(e)}


@app.post("/api/settings/restart-services")
async def restart_services():
    """Restart background services (bandwidth tracker and stream prober) to apply new settings."""
    settings = get_settings()

    # Stop existing tracker
    tracker = get_tracker()
    if tracker:
        await tracker.stop()
        logger.info("Stopped existing bandwidth tracker")

    # Stop existing stream prober
    prober = get_prober()
    if prober:
        await prober.stop()
        logger.info("Stopped existing stream prober")

    # Start new tracker and prober with current settings
    if settings.is_configured():
        try:
            # Restart bandwidth tracker
            new_tracker = BandwidthTracker(get_client(), poll_interval=settings.stats_poll_interval)
            set_tracker(new_tracker)
            await new_tracker.start()
            logger.info(f"Restarted bandwidth tracker with {settings.stats_poll_interval}s poll interval, timezone: {settings.user_timezone or 'UTC'}")

            # Restart stream prober
            new_prober = StreamProber(
                get_client(),
                probe_timeout=settings.stream_probe_timeout,
                probe_batch_size=settings.stream_probe_batch_size,
                probe_interval_hours=settings.stream_probe_interval_hours,
                probe_enabled=settings.stream_probe_enabled,
                schedule_time=settings.stream_probe_schedule_time,
                user_timezone=settings.user_timezone,
                probe_channel_groups=settings.probe_channel_groups,
            )
            set_prober(new_prober)
            await new_prober.start()
            logger.info(f"Restarted stream prober with updated settings (groups: {len(settings.probe_channel_groups)} selected)")

            return {"success": True, "message": "Services restarted with new settings"}
        except Exception as e:
            logger.error(f"Failed to restart services: {e}")
            return {"success": False, "message": str(e)}
    else:
        return {"success": False, "message": "Settings not configured"}


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
    logger.debug(f"GET /api/channels - page: {page}, page_size: {page_size}, search: {search}, channel_group: {channel_group}")
    client = get_client()
    try:
        result = await client.get_channels(
            page=page,
            page_size=page_size,
            search=search,
            channel_group=channel_group,
        )
        logger.info(f"Retrieved {len(result.get('results', []))} channels (page {page}, total: {result.get('count', 0)})")
        return result
    except Exception as e:
        logger.exception(f"Failed to retrieve channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels")
async def create_channel(request: CreateChannelRequest):
    logger.debug(f"POST /api/channels - Creating channel: {request.name}, number: {request.channel_number}")
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
    logger.debug(f"PATCH /api/channels/{channel_id} - Updating channel with data: {data}")
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
            logger.info(f"Updated channel {channel_id}: {', '.join(changes)}")
            journal.log_entry(
                category="channel",
                action_type="update",
                entity_id=channel_id,
                entity_name=result.get("name", before_channel.get("name", "Unknown")),
                description=f"Updated channel: {', '.join(changes)}",
                before_value=before_value,
                after_value=after_value,
            )
        else:
            logger.debug(f"No changes detected for channel {channel_id}")

        return result
    except Exception as e:
        logger.exception(f"Failed to update channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channels/{channel_id}")
async def delete_channel(channel_id: int):
    logger.debug(f"DELETE /api/channels/{channel_id} - Deleting channel")
    client = get_client()
    try:
        # Get channel info before deleting for logging
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")

        await client.delete_channel(channel_id)
        logger.info(f"Deleted channel {channel_id}: {channel_name}")

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
        logger.exception(f"Failed to delete channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/{channel_id}/add-stream")
async def add_stream_to_channel(channel_id: int, request: AddStreamRequest):
    logger.debug(f"POST /api/channels/{channel_id}/add-stream - Adding stream {request.stream_id}")
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
            logger.info(f"Added stream {request.stream_id} to channel {channel_id} ({channel_name})")

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
        logger.debug(f"Stream {request.stream_id} already in channel {channel_id}")
        return channel
    except Exception as e:
        logger.exception(f"Failed to add stream {request.stream_id} to channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/{channel_id}/remove-stream")
async def remove_stream_from_channel(channel_id: int, request: RemoveStreamRequest):
    logger.debug(f"POST /api/channels/{channel_id}/remove-stream - Removing stream {request.stream_id}")
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
            logger.info(f"Removed stream {request.stream_id} from channel {channel_id} ({channel_name})")

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
        logger.debug(f"Stream {request.stream_id} not in channel {channel_id}")
        return channel
    except Exception as e:
        logger.exception(f"Failed to remove stream {request.stream_id} from channel {channel_id}: {e}")
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
        groups = await client.get_channel_groups()

        # Filter out hidden groups
        from database import get_session
        from models import HiddenChannelGroup

        with get_session() as db:
            hidden_ids = {h.group_id for h in db.query(HiddenChannelGroup).all()}

        # Return only groups that aren't hidden
        return [g for g in groups if g.get("id") not in hidden_ids]
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
        # Check if this group has M3U sync settings
        m3u_settings = await client.get_all_m3u_group_settings()
        has_m3u_sync = group_id in m3u_settings

        if has_m3u_sync:
            # Hide the group instead of deleting to preserve M3U sync
            from database import get_session
            from models import HiddenChannelGroup

            # Get the group name before hiding
            groups = await client.get_channel_groups()
            group_name = next((g.get("name") for g in groups if g.get("id") == group_id), f"Group {group_id}")

            with get_session() as db:
                # Check if already hidden
                existing = db.query(HiddenChannelGroup).filter_by(group_id=group_id).first()
                if not existing:
                    hidden_group = HiddenChannelGroup(group_id=group_id, group_name=group_name)
                    db.add(hidden_group)
                    db.commit()
                    logger.info(f"Hidden channel group {group_id} ({group_name}) due to M3U sync settings")

            return {"status": "hidden", "message": "Group hidden (M3U sync active)"}
        else:
            # No M3U sync, safe to delete
            await client.delete_channel_group(group_id)
            logger.info(f"Deleted channel group {group_id}")
            return {"status": "deleted"}
    except Exception as e:
        logger.error(f"Failed to delete/hide channel group {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channel-groups/{group_id}/restore")
async def restore_channel_group(group_id: int):
    """Restore a hidden channel group back to the visible list."""
    try:
        from database import get_session
        from models import HiddenChannelGroup

        with get_session() as db:
            hidden_group = db.query(HiddenChannelGroup).filter_by(group_id=group_id).first()
            if hidden_group:
                db.delete(hidden_group)
                db.commit()
                logger.info(f"Restored channel group {group_id} ({hidden_group.group_name})")
                return {"status": "restored", "message": f"Group '{hidden_group.group_name}' restored"}
            else:
                raise HTTPException(status_code=404, detail="Group not found in hidden list")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to restore channel group {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channel-groups/hidden")
async def get_hidden_channel_groups():
    """Get list of all hidden channel groups."""
    try:
        from database import get_session
        from models import HiddenChannelGroup

        with get_session() as db:
            hidden_groups = db.query(HiddenChannelGroup).all()
            return [g.to_dict() for g in hidden_groups]
    except Exception as e:
        logger.error(f"Failed to get hidden channel groups: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channel-groups/orphaned")
async def get_orphaned_channel_groups():
    """Find channel groups that are truly orphaned.

    A group is considered orphaned if it has no streams AND no channels.
    M3U groups contain streams, manual groups contain channels.
    """
    client = get_client()
    try:
        # Get all channel groups from Dispatcharr
        all_groups = await client.get_channel_groups()

        # Get M3U group settings to see which M3U accounts groups were associated with
        m3u_group_settings = await client.get_all_m3u_group_settings()

        # Get all streams (paginated) to check which groups have streams
        streams = []
        page = 1
        while True:
            result = await client.get_streams(page=page, page_size=500)
            page_streams = result.get("results", [])
            streams.extend(page_streams)

            # Check if there are more pages
            if len(page_streams) < 500:
                break
            page += 1

        # Get all channels (paginated) to check which groups have channels
        channels = []
        page = 1
        while True:
            result = await client.get_channels(page=page, page_size=500)
            page_channels = result.get("results", [])
            channels.extend(page_channels)

            # Check if there are more pages
            if len(page_channels) < 500:
                break
            page += 1

        # Build map of group_id -> stream count (streams use group ID, not name)
        group_stream_count = {}
        for stream in streams:
            group_id = stream.get("channel_group")
            if group_id:
                group_stream_count[group_id] = group_stream_count.get(group_id, 0) + 1

        # Build map of group_id -> channel count
        group_channel_count = {}
        for channel in channels:
            group_id = channel.get("channel_group_id")
            if group_id:
                group_channel_count[group_id] = group_channel_count.get(group_id, 0) + 1

        # Build a set of group IDs that are targets of group_override from auto_channel_sync M3U groups
        # These groups may be empty now but will be populated by Auto Channel Sync
        group_override_targets = set()
        for group_id, m3u_info in m3u_group_settings.items():
            if m3u_info.get("auto_channel_sync"):
                custom_props = m3u_info.get("custom_properties", {})
                if custom_props and isinstance(custom_props, dict):
                    group_override = custom_props.get("group_override")
                    if group_override:
                        group_override_targets.add(group_override)

        logger.info(f"Total streams fetched: {len(streams)}")
        logger.info(f"Total channels fetched: {len(channels)}")
        logger.info(f"Groups with streams: {len(group_stream_count)}")
        logger.info(f"Groups with channels: {len(group_channel_count)}")
        logger.info(f"Groups that are group_override targets: {len(group_override_targets)}")

        # Find orphaned groups
        # A group is orphaned if it has no streams AND no channels AND is NOT in any M3U account
        # AND is NOT a target of group_override from an auto_channel_sync M3U group
        orphaned_groups = []
        for group in all_groups:
            group_id = group["id"]
            group_name = group["name"]

            stream_count = group_stream_count.get(group_id, 0)
            channel_count = group_channel_count.get(group_id, 0)

            # Check if this group is associated with any M3U account
            m3u_info = m3u_group_settings.get(group_id)

            # Check if this group is a target of group_override (will be populated by Auto Channel Sync)
            is_override_target = group_id in group_override_targets

            # Only consider it orphaned if:
            # 1. It has no streams AND no channels
            # 2. AND it's not in any M3U account (truly orphaned from deleted M3U)
            # 3. AND it's not a target of group_override from an auto_channel_sync M3U group
            if stream_count == 0 and channel_count == 0 and m3u_info is None and not is_override_target:
                # Group is truly orphaned - not in any M3U and has no content
                orphaned_groups.append({
                    "id": group_id,
                    "name": group_name,
                    "reason": "No streams, channels, or M3U association",
                })

        # Sort by name for consistent display
        orphaned_groups.sort(key=lambda g: g["name"].lower())

        logger.info(f"Found {len(orphaned_groups)} orphaned channel groups out of {len(all_groups)} total")
        return {
            "orphaned_groups": orphaned_groups,
            "total_groups": len(all_groups),
            "groups_with_content": len(set(list(group_stream_count.keys()) + list(str(gid) for gid in group_channel_count.keys()))),
        }
    except Exception as e:
        logger.error(f"Failed to find orphaned channel groups: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channel-groups/with-streams")
async def get_channel_groups_with_streams():
    """Get all channel groups that have channels with streams.

    Returns groups that have at least one channel containing at least one stream.
    These are the groups that can be probed.
    """
    client = get_client()
    try:
        # Get all channel groups
        all_groups = await client.get_channel_groups()

        # Get all channels (paginated) to check which have streams
        channels_with_streams = set()
        page = 1
        while True:
            result = await client.get_channels(page=page, page_size=500)
            page_channels = result.get("results", [])

            for channel in page_channels:
                # Check if channel has any streams
                stream_ids = channel.get("streams", [])
                if stream_ids:  # Has at least one stream
                    channels_with_streams.add(channel["id"])

            if not result.get("next"):
                break
            page += 1
            if page > 50:  # Safety limit
                break

        logger.info(f"Found {len(channels_with_streams)} channels with streams")

        # Now check which groups have these channels
        groups_with_streams = []
        for group in all_groups:
            group_id = group["id"]
            group_name = group["name"]

            # Get channels in this group
            try:
                group_channels_result = await client.get_channels(page=1, page_size=1, channel_group_id=group_id)
                group_channels = group_channels_result.get("results", [])

                # Check if any of these channels have streams
                has_streams = any(ch["id"] in channels_with_streams for ch in group_channels)

                if has_streams:
                    groups_with_streams.append({
                        "id": group_id,
                        "name": group_name
                    })
            except Exception as e:
                logger.warning(f"Failed to check group {group_id} ({group_name}): {e}")
                continue

        # Sort by name for consistent display
        groups_with_streams.sort(key=lambda g: g["name"].lower())

        logger.info(f"Found {len(groups_with_streams)} groups with streams out of {len(all_groups)} total")
        return {
            "groups": groups_with_streams,
            "total_groups": len(all_groups)
        }
    except Exception as e:
        logger.error(f"Failed to get channel groups with streams: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channel-groups/orphaned")
async def delete_orphaned_channel_groups(request: DeleteOrphanedGroupsRequest = None):
    """Delete channel groups that are truly orphaned.

    A group is deleted if it has no streams AND no channels.
    M3U groups contain streams, manual groups contain channels.

    Args:
        request: Optional request body with group_ids list. If None or empty, all orphaned groups are deleted.
    """
    client = get_client()
    group_ids = request.group_ids if request else None
    try:
        # Use the same logic as GET to find orphaned groups
        all_groups = await client.get_channel_groups()

        # Get M3U group settings to see which groups are still in M3U accounts
        m3u_group_settings = await client.get_all_m3u_group_settings()

        # Get all streams (paginated)
        streams = []
        page = 1
        while True:
            result = await client.get_streams(page=page, page_size=500)
            page_streams = result.get("results", [])
            streams.extend(page_streams)

            # Check if there are more pages
            if len(page_streams) < 500:
                break
            page += 1

        # Get all channels (paginated)
        channels = []
        page = 1
        while True:
            result = await client.get_channels(page=page, page_size=500)
            page_channels = result.get("results", [])
            channels.extend(page_channels)

            # Check if there are more pages
            if len(page_channels) < 500:
                break
            page += 1

        # Build map of group_id -> stream count (streams use group ID, not name)
        group_stream_count = {}
        for stream in streams:
            group_id = stream.get("channel_group")
            if group_id:
                group_stream_count[group_id] = group_stream_count.get(group_id, 0) + 1

        # Build map of group_id -> channel count
        group_channel_count = {}
        for channel in channels:
            group_id = channel.get("channel_group_id")
            if group_id:
                group_channel_count[group_id] = group_channel_count.get(group_id, 0) + 1

        # Build a set of group IDs that are targets of group_override from auto_channel_sync M3U groups
        # These groups may be empty now but will be populated by Auto Channel Sync
        group_override_targets = set()
        for group_id, m3u_info in m3u_group_settings.items():
            if m3u_info.get("auto_channel_sync"):
                custom_props = m3u_info.get("custom_properties", {})
                if custom_props and isinstance(custom_props, dict):
                    group_override = custom_props.get("group_override")
                    if group_override:
                        group_override_targets.add(group_override)

        # Find orphaned groups
        # A group is orphaned if it has no streams AND no channels AND is NOT in any M3U account
        # AND is NOT a target of group_override from an auto_channel_sync M3U group
        orphaned_groups = []
        for group in all_groups:
            group_id = group["id"]
            group_name = group["name"]

            stream_count = group_stream_count.get(group_id, 0)
            channel_count = group_channel_count.get(group_id, 0)

            # Check if this group is associated with any M3U account
            m3u_info = m3u_group_settings.get(group_id)

            # Check if this group is a target of group_override (will be populated by Auto Channel Sync)
            is_override_target = group_id in group_override_targets

            # Only consider it orphaned if:
            # 1. It has no streams AND no channels
            # 2. AND it's not in any M3U account (truly orphaned from deleted M3U)
            # 3. AND it's not a target of group_override from an auto_channel_sync M3U group
            if stream_count == 0 and channel_count == 0 and m3u_info is None and not is_override_target:
                # Group is truly orphaned - not in any M3U and has no content
                orphaned_groups.append({
                    "id": group_id,
                    "name": group_name,
                    "reason": "No streams, channels, or M3U association",
                })

        if not orphaned_groups:
            return {
                "status": "ok",
                "message": "No orphaned channel groups found",
                "deleted_groups": [],
                "failed_groups": [],
            }

        # Filter to only the specified group IDs if provided
        groups_to_delete = orphaned_groups
        if group_ids is not None:
            groups_to_delete = [g for g in orphaned_groups if g["id"] in group_ids]
            if not groups_to_delete:
                return {
                    "status": "ok",
                    "message": "No matching orphaned groups to delete",
                    "deleted_groups": [],
                    "failed_groups": [],
                }

        # Delete each orphaned group
        deleted_groups = []
        failed_groups = []
        for orphan in groups_to_delete:
            group_id = orphan["id"]
            group_name = orphan["name"]
            try:
                await client.delete_channel_group(group_id)
                deleted_groups.append({"id": group_id, "name": group_name, "reason": orphan["reason"]})
                logger.info(f"Deleted orphaned channel group: {group_id} ({group_name}) - {orphan['reason']}")
            except Exception as group_err:
                failed_groups.append({"id": group_id, "name": group_name, "error": str(group_err)})
                logger.warning(f"Failed to delete orphaned channel group {group_id} ({group_name}): {group_err}")

        # Log to journal
        if deleted_groups:
            journal.log_entry(
                category="channel",
                action_type="cleanup",
                entity_id=None,
                entity_name="Orphaned Groups Cleanup",
                description=f"Deleted {len(deleted_groups)} orphaned channel groups",
                after_value={
                    "deleted_groups": deleted_groups,
                    "failed_groups": failed_groups,
                },
            )

        return {
            "status": "ok",
            "message": f"Deleted {len(deleted_groups)} orphaned channel groups",
            "deleted_groups": deleted_groups,
            "failed_groups": failed_groups,
        }
    except Exception as e:
        logger.error(f"Failed to delete orphaned channel groups: {e}")
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
    except httpx.ReadTimeout:
        raise HTTPException(
            status_code=504,
            detail="EPG data request timed out. This usually happens with very large EPG datasets. Try again or contact your Dispatcharr administrator to optimize EPG data size."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/epg/lcn")
async def get_epg_lcn_by_tvg_id(tvg_id: str):
    """Get LCN (Logical Channel Number) for a TVG-ID from EPG XML sources.

    Fetches EPG XML from source URLs and extracts the <lcn> value for the given tvg_id.
    Returns the first LCN found across all XMLTV sources.

    Args:
        tvg_id: The TVG-ID to search for (as a query parameter)
    """
    import xml.etree.ElementTree as ET
    import gzip
    import io
    import httpx

    client = get_client()
    try:
        # Get all EPG sources
        sources = await client.get_epg_sources()

        # Filter to XMLTV sources that have URLs
        xmltv_sources = [
            s for s in sources
            if s.get("source_type") == "xmltv" and s.get("url")
        ]

        if not xmltv_sources:
            raise HTTPException(status_code=404, detail="No XMLTV EPG sources found")

        # Fetch and parse each XML source looking for the tvg_id
        # For large files, use streaming decompression to only read channel metadata
        MAX_SMALL_FILE = 50 * 1024 * 1024  # 50MB - download fully
        MAX_STREAM_BYTES = 20 * 1024 * 1024  # 20MB - max to stream from large files

        async def parse_xml_for_lcn(content: bytes, source_name: str) -> dict | None:
            """Parse XML content looking for LCN matching tvg_id."""
            xml_stream = io.BytesIO(content)
            root = None
            for event, elem in ET.iterparse(xml_stream, events=["start", "end"]):
                if event == "start" and root is None:
                    root = elem
                if event == "end" and elem.tag == "channel":
                    channel_id = elem.get("id", "")
                    if channel_id == tvg_id:
                        lcn = None
                        for child in elem:
                            if child.tag == "lcn":
                                lcn = child.text
                                break
                        if lcn:
                            logger.info(f"Found LCN {lcn} for {tvg_id} in {source_name}")
                            return {"tvg_id": tvg_id, "lcn": lcn, "source": source_name}
                    if root is not None:
                        root.clear()
                if event == "end" and elem.tag == "programme":
                    break
            return None

        async with httpx.AsyncClient(timeout=120.0) as http_client:
            for source in xmltv_sources:
                url = source.get("url")
                if not url:
                    continue

                try:
                    logger.info(f"Checking EPG XML from {url} for LCN lookup...")

                    # Check file size first
                    head_response = await http_client.head(url)
                    content_length = head_response.headers.get('content-length')
                    file_size = int(content_length) if content_length else 0

                    if file_size == 0 or file_size <= MAX_SMALL_FILE:
                        # Small file - download fully
                        response = await http_client.get(url)
                        response.raise_for_status()
                        content = response.content
                        logger.info(f"Downloaded {len(content)} bytes from {url}")

                        # Decompress if gzipped
                        if url.endswith('.gz') or response.headers.get('content-encoding') == 'gzip':
                            try:
                                content = gzip.decompress(content)
                                logger.info(f"Decompressed to {len(content)} bytes")
                            except gzip.BadGzipFile:
                                pass

                        result = await parse_xml_for_lcn(content, source.get("name"))
                        if result:
                            return result
                    else:
                        # Large file - stream download first portion and decompress incrementally
                        logger.info(f"Large file ({file_size} bytes) - streaming first {MAX_STREAM_BYTES//1024//1024}MB...")

                        if url.endswith('.gz'):
                            # For gzipped files, download partial and try to decompress
                            # Channel data is typically in first 1-2% of large EPG files
                            download_size = min(file_size, MAX_STREAM_BYTES)
                            headers = {"Range": f"bytes=0-{download_size}"}

                            # Try range request
                            response = await http_client.get(url, headers=headers)
                            partial_content = response.content
                            logger.info(f"Downloaded {len(partial_content)} bytes (partial)")

                            # Decompress with decompobj to handle truncated data
                            import zlib
                            decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)
                            try:
                                decompressed = decompressor.decompress(partial_content)
                                logger.info(f"Partially decompressed to {len(decompressed)} bytes")

                                # Try to parse what we have - look for channel data
                                # Add closing tag to make it parseable
                                xml_partial = decompressed
                                if b'<programme' in xml_partial:
                                    # Truncate at first programme to avoid XML parse errors
                                    idx = xml_partial.find(b'<programme')
                                    xml_partial = xml_partial[:idx] + b'</tv>'

                                result = await parse_xml_for_lcn(xml_partial, source.get("name"))
                                if result:
                                    return result
                            except Exception as e:
                                logger.warning(f"Failed to decompress partial {url}: {e}")
                        else:
                            # Non-gzipped large file - just download first portion
                            headers = {"Range": f"bytes=0-{MAX_STREAM_BYTES}"}
                            response = await http_client.get(url, headers=headers)
                            content = response.content
                            logger.info(f"Downloaded {len(content)} bytes (partial)")

                            if b'<programme' in content:
                                idx = content.find(b'<programme')
                                content = content[:idx] + b'</tv>'

                            result = await parse_xml_for_lcn(content, source.get("name"))
                            if result:
                                return result

                except httpx.HTTPError as e:
                    logger.warning(f"Failed to fetch EPG XML from {url}: {e}")
                    continue
                except ET.ParseError as e:
                    logger.warning(f"Failed to parse EPG XML from {url}: {e}")
                    continue
                except Exception as e:
                    logger.warning(f"Error processing EPG XML from {url}: {e}")
                    continue

        # Not found in any source
        raise HTTPException(
            status_code=404,
            detail=f"No LCN found for TVG-ID '{tvg_id}' in any EPG source"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching LCN for {tvg_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class BatchLCNRequest(BaseModel):
    """Request body for batch LCN lookup."""
    tvg_ids: list[str]


@app.post("/api/epg/lcn/batch")
async def get_epg_lcn_batch(request: BatchLCNRequest):
    """Get LCN (Logical Channel Number) for multiple TVG-IDs from EPG XML sources.

    This is more efficient than calling the single endpoint multiple times
    because it fetches and parses each EPG XML source only once.

    Returns a dict mapping tvg_id -> {lcn, source} for found entries.
    """
    import xml.etree.ElementTree as ET
    import gzip
    import io
    import httpx

    tvg_ids_to_find = set(request.tvg_ids)
    if not tvg_ids_to_find:
        return {"results": {}}

    client = get_client()
    try:
        # Get all EPG sources
        sources = await client.get_epg_sources()

        # Filter to XMLTV sources that have URLs
        xmltv_sources = [
            s for s in sources
            if s.get("source_type") == "xmltv" and s.get("url")
        ]

        if not xmltv_sources:
            return {"results": {}}

        results: dict[str, dict] = {}
        MAX_SMALL_FILE = 50 * 1024 * 1024  # 50MB
        MAX_STREAM_BYTES = 20 * 1024 * 1024  # 20MB

        def parse_xml_for_lcns(content: bytes, source_name: str, tvg_ids: set[str]) -> dict[str, dict]:
            """Parse XML content and extract LCN for all matching tvg_ids."""
            found: dict[str, dict] = {}
            xml_stream = io.BytesIO(content)
            root = None
            for event, elem in ET.iterparse(xml_stream, events=["start", "end"]):
                if event == "start" and root is None:
                    root = elem
                if event == "end" and elem.tag == "channel":
                    channel_id = elem.get("id", "")
                    if channel_id in tvg_ids and channel_id not in found:
                        lcn = None
                        for child in elem:
                            if child.tag == "lcn":
                                lcn = child.text
                                break
                        if lcn:
                            found[channel_id] = {"lcn": lcn, "source": source_name}
                    if root is not None:
                        root.clear()
                if event == "end" and elem.tag == "programme":
                    break
            return found

        logger.info(f"Batch LCN lookup for {len(tvg_ids_to_find)} TVG-IDs")

        async with httpx.AsyncClient(timeout=120.0) as http_client:
            for source in xmltv_sources:
                url = source.get("url")
                if not url:
                    continue

                # Stop early if all found
                remaining = tvg_ids_to_find - set(results.keys())
                if not remaining:
                    break

                try:
                    # Check file size
                    head_response = await http_client.head(url)
                    content_length = head_response.headers.get('content-length')
                    file_size = int(content_length) if content_length else 0

                    if file_size == 0 or file_size <= MAX_SMALL_FILE:
                        # Small file - download fully
                        response = await http_client.get(url)
                        response.raise_for_status()
                        content = response.content
                        logger.info(f"Batch LCN: Downloaded {len(content)} bytes from {url}")

                        if url.endswith('.gz') or response.headers.get('content-encoding') == 'gzip':
                            try:
                                content = gzip.decompress(content)
                            except gzip.BadGzipFile:
                                pass

                        found = parse_xml_for_lcns(content, source.get("name"), remaining)
                        results.update(found)
                        if found:
                            logger.info(f"Batch LCN: Found {len(found)} LCNs in {source.get('name')}")
                    else:
                        # Large file - stream first portion
                        logger.info(f"Batch LCN: Large file ({file_size} bytes) - streaming...")

                        if url.endswith('.gz'):
                            download_size = min(file_size, MAX_STREAM_BYTES)
                            headers = {"Range": f"bytes=0-{download_size}"}
                            response = await http_client.get(url, headers=headers)
                            partial_content = response.content

                            import zlib
                            decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)
                            try:
                                decompressed = decompressor.decompress(partial_content)
                                xml_partial = decompressed
                                if b'<programme' in xml_partial:
                                    idx = xml_partial.find(b'<programme')
                                    xml_partial = xml_partial[:idx] + b'</tv>'

                                found = parse_xml_for_lcns(xml_partial, source.get("name"), remaining)
                                results.update(found)
                                if found:
                                    logger.info(f"Batch LCN: Found {len(found)} LCNs in {source.get('name')} (partial)")
                            except Exception as e:
                                logger.warning(f"Batch LCN: Failed to decompress partial {url}: {e}")
                        else:
                            headers = {"Range": f"bytes=0-{MAX_STREAM_BYTES}"}
                            response = await http_client.get(url, headers=headers)
                            content = response.content

                            if b'<programme' in content:
                                idx = content.find(b'<programme')
                                content = content[:idx] + b'</tv>'

                            found = parse_xml_for_lcns(content, source.get("name"), remaining)
                            results.update(found)
                            if found:
                                logger.info(f"Batch LCN: Found {len(found)} LCNs in {source.get('name')} (partial)")

                except httpx.HTTPError as e:
                    logger.warning(f"Batch LCN: Failed to fetch {url}: {e}")
                    continue
                except ET.ParseError as e:
                    logger.warning(f"Batch LCN: Failed to parse {url}: {e}")
                    continue
                except Exception as e:
                    logger.warning(f"Batch LCN: Error processing {url}: {e}")
                    continue

        logger.info(f"Batch LCN lookup complete: {len(results)}/{len(tvg_ids_to_find)} found")
        return {"results": results}

    except Exception as e:
        logger.error(f"Batch LCN error: {e}")
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
async def delete_m3u_account(account_id: int, delete_groups: bool = True):
    """Delete an M3U account and optionally its associated channel groups.

    Args:
        account_id: The M3U account ID to delete
        delete_groups: If True (default), also delete channel groups associated with this account
    """
    client = get_client()
    try:
        # Get account info before deleting (includes channel_groups)
        account = await client.get_m3u_account(account_id)
        account_name = account.get("name", "Unknown")

        # Extract channel group IDs associated with this M3U account
        channel_group_ids = []
        if delete_groups:
            for group_setting in account.get("channel_groups", []):
                group_id = group_setting.get("channel_group")
                if group_id:
                    channel_group_ids.append(group_id)
            logger.info(f"M3U account '{account_name}' has {len(channel_group_ids)} associated channel groups")

        # Delete the M3U account first
        await client.delete_m3u_account(account_id)

        # Now delete associated channel groups
        deleted_groups = []
        failed_groups = []
        if delete_groups and channel_group_ids:
            for group_id in channel_group_ids:
                try:
                    await client.delete_channel_group(group_id)
                    deleted_groups.append(group_id)
                    logger.info(f"Deleted channel group {group_id} (was associated with M3U '{account_name}')")
                except Exception as group_err:
                    # Group might have channels or other issues - log but don't fail
                    failed_groups.append({"id": group_id, "error": str(group_err)})
                    logger.warning(f"Failed to delete channel group {group_id}: {group_err}")

        # Log to journal
        journal.log_entry(
            category="m3u",
            action_type="delete",
            entity_id=account_id,
            entity_name=account_name,
            description=f"Deleted M3U account '{account_name}'" +
                       (f" and {len(deleted_groups)} channel groups" if deleted_groups else ""),
            before_value={
                "name": account_name,
                "channel_groups": channel_group_ids,
            },
            after_value={
                "deleted_groups": deleted_groups,
                "failed_groups": failed_groups,
            } if channel_group_ids else None,
        )

        return {
            "status": "deleted",
            "deleted_groups": deleted_groups,
            "failed_groups": failed_groups,
        }
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
# M3U Profiles
# -------------------------------------------------------------------------

@app.get("/api/m3u/accounts/{account_id}/profiles/")
async def get_m3u_profiles(account_id: int):
    """Get all profiles for an M3U account."""
    client = get_client()
    try:
        return await client.get_m3u_profiles(account_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/m3u/accounts/{account_id}/profiles/")
async def create_m3u_profile(account_id: int, request: Request):
    """Create a new profile for an M3U account."""
    client = get_client()
    try:
        data = await request.json()
        return await client.create_m3u_profile(account_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/m3u/accounts/{account_id}/profiles/{profile_id}/")
async def get_m3u_profile(account_id: int, profile_id: int):
    """Get a specific profile for an M3U account."""
    client = get_client()
    try:
        return await client.get_m3u_profile(account_id, profile_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/m3u/accounts/{account_id}/profiles/{profile_id}/")
async def update_m3u_profile(account_id: int, profile_id: int, request: Request):
    """Update a profile for an M3U account."""
    client = get_client()
    try:
        data = await request.json()
        return await client.update_m3u_profile(account_id, profile_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/m3u/accounts/{account_id}/profiles/{profile_id}/")
async def delete_m3u_profile(account_id: int, profile_id: int):
    """Delete a profile from an M3U account."""
    client = get_client()
    try:
        await client.delete_m3u_profile(account_id, profile_id)
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


@app.get("/api/stats/activity")
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


@app.get("/api/stats/bandwidth")
async def get_bandwidth_stats():
    """Get bandwidth usage summary for all time periods."""
    try:
        return BandwidthTracker.get_bandwidth_summary()
    except Exception as e:
        logger.error(f"Failed to get bandwidth stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats/top-watched")
async def get_top_watched_channels(limit: int = 10, sort_by: str = "views"):
    """Get the top watched channels by watch count or watch time."""
    try:
        return BandwidthTracker.get_top_watched_channels(limit=limit, sort_by=sort_by)
    except Exception as e:
        logger.error(f"Failed to get top watched channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Stream Stats / Probing Endpoints
# =============================================================================


@app.get("/api/stream-stats")
async def get_all_stream_stats():
    """Get all stream probe statistics."""
    try:
        return StreamProber.get_all_stats()
    except Exception as e:
        logger.error(f"Failed to get stream stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stream-stats/summary")
async def get_stream_stats_summary():
    """Get summary of stream probe statistics."""
    try:
        return StreamProber.get_stats_summary()
    except Exception as e:
        logger.error(f"Failed to get stream stats summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stream-stats/{stream_id}")
async def get_stream_stats_by_id(stream_id: int):
    """Get probe stats for a specific stream."""
    try:
        stats = StreamProber.get_stats_by_stream_id(stream_id)
        if not stats:
            raise HTTPException(status_code=404, detail="Stream stats not found")
        return stats
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get stream stats for {stream_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class BulkStreamStatsRequest(BaseModel):
    stream_ids: list[int]


@app.post("/api/stream-stats/by-ids")
async def get_stream_stats_by_ids(request: BulkStreamStatsRequest):
    """Get probe stats for multiple streams by their IDs."""
    try:
        return StreamProber.get_stats_by_stream_ids(request.stream_ids)
    except Exception as e:
        logger.error(f"Failed to get stream stats by IDs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class BulkProbeRequest(BaseModel):
    stream_ids: list[int]


# NOTE: /probe/bulk and /probe/all MUST be defined BEFORE /probe/{stream_id}
# to avoid the path parameter matching "bulk" or "all" as a stream_id
@app.post("/api/stream-stats/probe/bulk")
async def probe_bulk_streams(request: BulkProbeRequest):
    """Trigger on-demand probe for multiple streams."""
    logger.info(f"Bulk probe request received for {len(request.stream_ids)} streams: {request.stream_ids}")

    prober = get_prober()
    logger.info(f"get_prober() returned: {prober is not None}")

    if not prober:
        logger.error("Stream prober not available - returning 503")
        raise HTTPException(status_code=503, detail="Stream prober not available")

    try:
        import asyncio

        logger.debug("Fetching all streams for bulk probe")
        all_streams = await prober._fetch_all_streams()
        logger.info(f"Fetched {len(all_streams)} total streams")

        stream_map = {s["id"]: s for s in all_streams}

        results = []
        for stream_id in request.stream_ids:
            stream = stream_map.get(stream_id)
            if stream:
                logger.debug(f"Probing stream {stream_id}")
                result = await prober.probe_stream(
                    stream_id, stream.get("url"), stream.get("name")
                )
                results.append(result)
                await asyncio.sleep(0.5)  # Rate limiting
            else:
                logger.warning(f"Stream {stream_id} not found in stream list")

        logger.info(f"Bulk probe completed: {len(results)} streams probed")
        return {"probed": len(results), "results": results}
    except Exception as e:
        logger.error(f"Bulk probe failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stream-stats/probe/all")
async def probe_all_streams_endpoint():
    """Trigger probe for all streams (background task)."""
    logger.info("Probe all streams request received")

    prober = get_prober()
    logger.info(f"get_prober() returned: {prober is not None}")

    if not prober:
        logger.error("Stream prober not available - returning 503")
        raise HTTPException(status_code=503, detail="Stream prober not available")

    import asyncio

    # Start background task
    logger.info("Starting background probe task for all streams")
    asyncio.create_task(prober.probe_all_streams())
    return {"status": "started", "message": "Background probe started"}


@app.get("/api/stream-stats/probe/progress")
async def get_probe_progress():
    """Get current probe all streams progress."""
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.get_probe_progress()


@app.get("/api/stream-stats/probe/results")
async def get_probe_results():
    """Get detailed results of the last probe all streams operation."""
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.get_probe_results()


@app.post("/api/stream-stats/probe/{stream_id}")
async def probe_single_stream(stream_id: int):
    """Trigger on-demand probe for a single stream."""
    logger.info(f"Single stream probe request received for stream_id={stream_id}")

    prober = get_prober()
    logger.info(f"get_prober() returned: {prober is not None}")

    if not prober:
        logger.error("Stream prober not available - returning 503")
        raise HTTPException(status_code=503, detail="Stream prober not available")

    try:
        # Get all streams and find the one we want
        logger.debug(f"Fetching all streams to find stream {stream_id}")
        all_streams = await prober._fetch_all_streams()
        stream = next((s for s in all_streams if s["id"] == stream_id), None)

        if not stream:
            logger.warning(f"Stream {stream_id} not found")
            raise HTTPException(status_code=404, detail="Stream not found")

        logger.info(f"Probing single stream {stream_id}")
        result = await prober.probe_stream(
            stream_id, stream.get("url"), stream.get("name")
        )
        logger.info(f"Single stream probe completed for {stream_id}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to probe stream {stream_id}: {e}")
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
