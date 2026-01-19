from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Literal, Union
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
from alert_channels import get_alert_manager, get_channel_types, create_channel
# Import channel implementations to register them
import alert_channels_discord  # noqa: F401
import alert_channels_smtp  # noqa: F401
import alert_channels_telegram  # noqa: F401

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


# Custom validation error handler to log details
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log detailed validation errors for debugging."""
    logger.error(f"[VALIDATION-ERROR] Request path: {request.url.path}")
    logger.error(f"[VALIDATION-ERROR] Request method: {request.method}")
    logger.error(f"[VALIDATION-ERROR] Request headers: {dict(request.headers)}")

    # Try to read the body
    try:
        body = await request.body()
        logger.error(f"[VALIDATION-ERROR] Request body (raw): {body}")
        logger.error(f"[VALIDATION-ERROR] Request body (decoded): {body.decode()}")
    except Exception as e:
        logger.error(f"[VALIDATION-ERROR] Could not read body: {e}")

    logger.error(f"[VALIDATION-ERROR] Validation errors: {exc.errors()}")
    logger.error(f"[VALIDATION-ERROR] Validation body: {exc.body}")

    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": str(exc.body)},
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
                bitrate_sample_duration=settings.bitrate_sample_duration,
                parallel_probing_enabled=settings.parallel_probing_enabled,
                skip_recently_probed_hours=settings.skip_recently_probed_hours,
                refresh_m3us_before_probe=settings.refresh_m3us_before_probe,
                auto_reorder_after_probe=settings.auto_reorder_after_probe,
                deprioritize_failed_streams=settings.deprioritize_failed_streams,
                stream_sort_priority=settings.stream_sort_priority,
                stream_sort_enabled=settings.stream_sort_enabled,
                stream_fetch_page_limit=settings.stream_fetch_page_limit,
            )
            logger.info(f"StreamProber instance created: {prober is not None}")

            set_prober(prober)
            logger.info("set_prober() called successfully")

            # Connect the prober to the StreamProbeTask in the task registry
            try:
                from task_registry import get_registry
                registry = get_registry()
                stream_probe_task = registry.get_task_instance("stream_probe")
                if stream_probe_task:
                    stream_probe_task.set_prober(prober)
                    logger.info("Connected StreamProber to StreamProbeTask")
                else:
                    logger.warning("StreamProbeTask not found in registry")
            except Exception as e:
                logger.warning(f"Failed to connect prober to task: {e}")

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

    # Start the task execution engine
    try:
        # Import tasks module to trigger @register_task decorators
        import tasks  # noqa: F401 - imported for side effects
        logger.info("Task modules loaded and registered")

        # Start the task engine
        from task_engine import start_engine
        await start_engine()
        logger.info("Task execution engine started")
    except Exception as e:
        logger.error(f"Failed to start task engine: {e}", exc_info=True)
        logger.error("Scheduled tasks will not be available!")

    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on shutdown."""
    logger.info("Enhanced Channel Manager shutting down")

    # Stop task engine
    try:
        from task_engine import stop_engine
        await stop_engine()
        logger.info("Task execution engine stopped")
    except Exception as e:
        logger.error(f"Error stopping task engine: {e}")

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

    class Config:
        # Allow extra fields to be ignored (for future compatibility)
        extra = "ignore"


# Bulk commit operation types
class BulkUpdateChannelOp(BaseModel):
    type: Literal["updateChannel"] = "updateChannel"
    channelId: int
    data: dict


class BulkAddStreamOp(BaseModel):
    type: Literal["addStreamToChannel"] = "addStreamToChannel"
    channelId: int
    streamId: int


class BulkRemoveStreamOp(BaseModel):
    type: Literal["removeStreamFromChannel"] = "removeStreamFromChannel"
    channelId: int
    streamId: int


class BulkReorderStreamsOp(BaseModel):
    type: Literal["reorderChannelStreams"] = "reorderChannelStreams"
    channelId: int
    streamIds: list[int]


class BulkAssignNumbersOp(BaseModel):
    type: Literal["bulkAssignChannelNumbers"] = "bulkAssignChannelNumbers"
    channelIds: list[int]
    startingNumber: Optional[float] = None


class BulkCreateChannelOp(BaseModel):
    type: Literal["createChannel"] = "createChannel"
    tempId: int  # Negative temp ID from frontend
    name: str
    channelNumber: Optional[float] = None
    groupId: Optional[int] = None
    newGroupName: Optional[str] = None
    logoId: Optional[int] = None
    logoUrl: Optional[str] = None
    tvgId: Optional[str] = None


class BulkDeleteChannelOp(BaseModel):
    type: Literal["deleteChannel"] = "deleteChannel"
    channelId: int


class BulkCreateGroupOp(BaseModel):
    type: Literal["createGroup"] = "createGroup"
    name: str


class BulkDeleteGroupOp(BaseModel):
    type: Literal["deleteChannelGroup"] = "deleteChannelGroup"
    groupId: int


# Union type for all bulk operations
BulkOperation = Union[
    BulkUpdateChannelOp,
    BulkAddStreamOp,
    BulkRemoveStreamOp,
    BulkReorderStreamsOp,
    BulkAssignNumbersOp,
    BulkCreateChannelOp,
    BulkDeleteChannelOp,
    BulkCreateGroupOp,
    BulkDeleteGroupOp,
]


class BulkCommitRequest(BaseModel):
    operations: list[BulkOperation]
    # Groups to create before processing operations (name -> temp group ID mapping)
    groupsToCreate: Optional[list[dict]] = None
    # If true, only validate without executing (returns validation issues)
    validateOnly: Optional[bool] = False
    # If true, continue processing even when individual operations fail
    continueOnError: Optional[bool] = False


class ValidationIssue(BaseModel):
    """Represents a validation issue found during pre-validation"""
    type: str  # 'missing_channel', 'missing_stream', 'invalid_operation', etc.
    severity: str  # 'error', 'warning'
    message: str
    operationIndex: Optional[int] = None
    channelId: Optional[int] = None
    channelName: Optional[str] = None
    streamId: Optional[int] = None
    streamName: Optional[str] = None


class BulkCommitResponse(BaseModel):
    success: bool
    operationsApplied: int
    operationsFailed: int
    errors: list[dict]
    # Map of temp channel IDs to real IDs
    tempIdMap: dict[int, int]
    # Map of group names to real IDs
    groupIdMap: dict[str, int]
    # Validation issues found during pre-validation
    validationIssues: Optional[list[dict]] = None
    # Whether validation passed (no errors, may have warnings)
    validationPassed: Optional[bool] = None


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
    hide_epg_urls: bool = False
    hide_m3u_urls: bool = False
    gracenote_conflict_mode: str = "ask"
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
    bitrate_sample_duration: int = 10  # Duration in seconds to sample stream for bitrate (10, 20, or 30)
    parallel_probing_enabled: bool = True  # Probe multiple streams from different M3Us simultaneously
    skip_recently_probed_hours: int = 0  # Skip streams successfully probed within last N hours (0 = always probe)
    refresh_m3us_before_probe: bool = True  # Refresh all M3U accounts before starting probe
    auto_reorder_after_probe: bool = False  # Automatically reorder streams in channels after probe completes
    stream_fetch_page_limit: int = 200  # Max pages when fetching streams (200 pages * 500 = 100K streams)
    stream_sort_priority: list[str] = ["resolution", "bitrate", "framerate"]  # Priority order for Smart Sort
    stream_sort_enabled: dict[str, bool] = {"resolution": True, "bitrate": True, "framerate": True}  # Which criteria are enabled
    deprioritize_failed_streams: bool = True  # When enabled, failed/timeout/pending streams sort to bottom


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
    hide_epg_urls: bool
    hide_m3u_urls: bool
    gracenote_conflict_mode: str
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
    bitrate_sample_duration: int
    parallel_probing_enabled: bool  # Probe multiple streams from different M3Us simultaneously
    skip_recently_probed_hours: int  # Skip streams successfully probed within last N hours (0 = always probe)
    refresh_m3us_before_probe: bool  # Refresh all M3U accounts before starting probe
    auto_reorder_after_probe: bool  # Automatically reorder streams in channels after probe completes
    stream_fetch_page_limit: int  # Max pages when fetching streams (200 pages * 500 = 100K streams)
    stream_sort_priority: list[str]  # Priority order for Smart Sort
    stream_sort_enabled: dict[str, bool]  # Which criteria are enabled
    deprioritize_failed_streams: bool  # When enabled, failed/timeout/pending streams sort to bottom


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
        hide_epg_urls=settings.hide_epg_urls,
        hide_m3u_urls=settings.hide_m3u_urls,
        gracenote_conflict_mode=settings.gracenote_conflict_mode,
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
        bitrate_sample_duration=settings.bitrate_sample_duration,
        parallel_probing_enabled=settings.parallel_probing_enabled,
        skip_recently_probed_hours=settings.skip_recently_probed_hours,
        refresh_m3us_before_probe=settings.refresh_m3us_before_probe,
        auto_reorder_after_probe=settings.auto_reorder_after_probe,
        stream_fetch_page_limit=settings.stream_fetch_page_limit,
        stream_sort_priority=settings.stream_sort_priority,
        stream_sort_enabled=settings.stream_sort_enabled,
        deprioritize_failed_streams=settings.deprioritize_failed_streams,
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
        hide_epg_urls=request.hide_epg_urls,
        hide_m3u_urls=request.hide_m3u_urls,
        gracenote_conflict_mode=request.gracenote_conflict_mode,
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
        bitrate_sample_duration=request.bitrate_sample_duration,
        parallel_probing_enabled=request.parallel_probing_enabled,
        skip_recently_probed_hours=request.skip_recently_probed_hours,
        refresh_m3us_before_probe=request.refresh_m3us_before_probe,
        auto_reorder_after_probe=request.auto_reorder_after_probe,
        stream_sort_priority=request.stream_sort_priority,
        stream_sort_enabled=request.stream_sort_enabled,
        deprioritize_failed_streams=request.deprioritize_failed_streams,
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
                bitrate_sample_duration=settings.bitrate_sample_duration,
                parallel_probing_enabled=settings.parallel_probing_enabled,
                skip_recently_probed_hours=settings.skip_recently_probed_hours,
                refresh_m3us_before_probe=settings.refresh_m3us_before_probe,
                auto_reorder_after_probe=settings.auto_reorder_after_probe,
                deprioritize_failed_streams=settings.deprioritize_failed_streams,
                stream_sort_priority=settings.stream_sort_priority,
                stream_sort_enabled=settings.stream_sort_enabled,
                stream_fetch_page_limit=settings.stream_fetch_page_limit,
            )
            set_prober(new_prober)

            # Connect the new prober to the StreamProbeTask
            try:
                from task_registry import get_registry
                registry = get_registry()
                stream_probe_task = registry.get_task_instance("stream_probe")
                if stream_probe_task:
                    stream_probe_task.set_prober(new_prober)
                    logger.info("Connected new StreamProber to StreamProbeTask")
            except Exception as e:
                logger.warning(f"Failed to connect prober to task: {e}")

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


@app.post("/api/channels/bulk-commit")
async def bulk_commit_operations(request: BulkCommitRequest):
    """
    Process multiple channel operations in a single request.

    This endpoint is optimized for bulk changes (1000+ operations) by:
    - Processing all operations in a single HTTP request
    - Tracking temp ID -> real ID mappings for newly created channels
    - Creating groups before processing channel operations that reference them
    - Pre-validating that referenced channels/streams exist

    Options:
    - validateOnly: If true, only validate without executing
    - continueOnError: If true, continue processing even when operations fail

    Returns a response with success status, ID mappings, and validation issues.
    """
    import uuid

    client = get_client()
    batch_id = str(uuid.uuid4())[:8]

    # Count operation types for logging
    op_counts = {}
    for op in request.operations:
        op_counts[op.type] = op_counts.get(op.type, 0) + 1
    op_summary = ", ".join(f"{count} {op_type}" for op_type, count in sorted(op_counts.items()))

    logger.debug(f"[BULK-COMMIT] Starting bulk commit (batch={batch_id}): {len(request.operations)} operations ({op_summary})")
    logger.debug(f"[BULK-COMMIT] Options: validateOnly={request.validateOnly}, continueOnError={request.continueOnError}")
    if request.groupsToCreate:
        logger.debug(f"[BULK-COMMIT] Groups to create: {[g.get('name') for g in request.groupsToCreate]}")

    result = {
        "success": True,
        "operationsApplied": 0,
        "operationsFailed": 0,
        "errors": [],
        "tempIdMap": {},  # temp channel ID -> real ID
        "groupIdMap": {},  # group name -> real ID
        "validationIssues": [],
        "validationPassed": True,
    }

    # Helper to resolve temp IDs to real IDs
    def resolve_id(channel_id: int) -> int:
        return result["tempIdMap"].get(channel_id, channel_id)

    # Helper to resolve group ID (could be temp or real, or from new group name)
    def resolve_group_id(group_id: Optional[int], new_group_name: Optional[str]) -> Optional[int]:
        if new_group_name and new_group_name in result["groupIdMap"]:
            return result["groupIdMap"][new_group_name]
        return group_id

    try:
        # Phase 0: Pre-validation - check that referenced entities exist
        logger.debug(f"[BULK-VALIDATE] Phase 0: Starting pre-validation")

        # Collect all channel IDs that are referenced (not created) in operations
        referenced_channel_ids = set()
        referenced_stream_ids = set()
        channels_to_create = set()  # Temp IDs that will be created

        for idx, op in enumerate(request.operations):
            if op.type == "createChannel":
                # This creates a channel, track its temp ID
                channels_to_create.add(op.tempId)
            elif op.type in ("updateChannel", "deleteChannel"):
                if op.channelId >= 0:  # Only real IDs need validation
                    referenced_channel_ids.add(op.channelId)
            elif op.type == "addStreamToChannel":
                if op.channelId >= 0:
                    referenced_channel_ids.add(op.channelId)
                referenced_stream_ids.add(op.streamId)
            elif op.type == "removeStreamFromChannel":
                if op.channelId >= 0:
                    referenced_channel_ids.add(op.channelId)
                referenced_stream_ids.add(op.streamId)
            elif op.type == "reorderChannelStreams":
                if op.channelId >= 0:
                    referenced_channel_ids.add(op.channelId)
                for sid in op.streamIds:
                    referenced_stream_ids.add(sid)
            elif op.type == "bulkAssignChannelNumbers":
                for cid in op.channelIds:
                    if cid >= 0:
                        referenced_channel_ids.add(cid)

        # Fetch existing channels and streams to validate
        existing_channels = {}  # id -> channel dict
        existing_streams = {}   # id -> stream dict

        logger.debug(f"[BULK-VALIDATE] Referenced entities: {len(referenced_channel_ids)} channels, {len(referenced_stream_ids)} streams")
        logger.debug(f"[BULK-VALIDATE] Channels to create: {len(channels_to_create)} (temp IDs: {sorted(channels_to_create)})")

        if referenced_channel_ids:
            try:
                logger.debug(f"[BULK-VALIDATE] Fetching existing channels for validation...")
                # Fetch all pages of channels to build lookup
                page = 1
                while True:
                    response = await client.get_channels(page=page, page_size=500)
                    for ch in response.get("results", []):
                        existing_channels[ch["id"]] = ch
                    if not response.get("next"):
                        break
                    page += 1
                logger.debug(f"[BULK-VALIDATE] Loaded {len(existing_channels)} existing channels")
            except Exception as e:
                logger.warning(f"[BULK-VALIDATE] Failed to fetch channels for validation: {e}")

        if referenced_stream_ids:
            try:
                logger.debug(f"[BULK-VALIDATE] Fetching existing streams for validation...")
                # Fetch all pages of streams to build lookup
                page = 1
                while True:
                    response = await client.get_streams(page=page, page_size=500)
                    for s in response.get("results", []):
                        existing_streams[s["id"]] = s
                    if not response.get("next"):
                        break
                    page += 1
                logger.debug(f"[BULK-VALIDATE] Loaded {len(existing_streams)} existing streams")
            except Exception as e:
                logger.warning(f"[BULK-VALIDATE] Failed to fetch streams for validation: {e}")

        # Validate each operation
        for idx, op in enumerate(request.operations):
            if op.type in ("updateChannel", "deleteChannel"):
                if op.channelId >= 0 and op.channelId not in existing_channels:
                    ch_name = f"Channel {op.channelId}"
                    result["validationIssues"].append({
                        "type": "missing_channel",
                        "severity": "error",
                        "message": f"Channel {op.channelId} does not exist in Dispatcharr",
                        "operationIndex": idx,
                        "channelId": op.channelId,
                        "channelName": ch_name,
                    })
                    result["validationPassed"] = False

            elif op.type == "addStreamToChannel":
                if op.channelId >= 0 and op.channelId not in existing_channels:
                    ch_name = f"Channel {op.channelId}"
                    result["validationIssues"].append({
                        "type": "missing_channel",
                        "severity": "error",
                        "message": f"Cannot add stream to channel {op.channelId}: channel does not exist",
                        "operationIndex": idx,
                        "channelId": op.channelId,
                        "channelName": ch_name,
                        "streamId": op.streamId,
                    })
                    result["validationPassed"] = False
                elif op.channelId >= 0:
                    ch_name = existing_channels[op.channelId].get("name", f"Channel {op.channelId}")
                    # Check stream exists
                    if op.streamId not in existing_streams:
                        result["validationIssues"].append({
                            "type": "missing_stream",
                            "severity": "error",
                            "message": f"Stream {op.streamId} does not exist",
                            "operationIndex": idx,
                            "channelId": op.channelId,
                            "channelName": ch_name,
                            "streamId": op.streamId,
                        })
                        result["validationPassed"] = False

            elif op.type == "removeStreamFromChannel":
                if op.channelId >= 0 and op.channelId not in existing_channels:
                    result["validationIssues"].append({
                        "type": "missing_channel",
                        "severity": "error",
                        "message": f"Cannot remove stream from channel {op.channelId}: channel does not exist",
                        "operationIndex": idx,
                        "channelId": op.channelId,
                        "streamId": op.streamId,
                    })
                    result["validationPassed"] = False

            elif op.type == "reorderChannelStreams":
                if op.channelId >= 0 and op.channelId not in existing_channels:
                    result["validationIssues"].append({
                        "type": "missing_channel",
                        "severity": "error",
                        "message": f"Cannot reorder streams for channel {op.channelId}: channel does not exist",
                        "operationIndex": idx,
                        "channelId": op.channelId,
                    })
                    result["validationPassed"] = False

            elif op.type == "bulkAssignChannelNumbers":
                for cid in op.channelIds:
                    if cid >= 0 and cid not in existing_channels:
                        result["validationIssues"].append({
                            "type": "missing_channel",
                            "severity": "error",
                            "message": f"Cannot assign number to channel {cid}: channel does not exist",
                            "operationIndex": idx,
                            "channelId": cid,
                        })
                        result["validationPassed"] = False

        # Log validation summary
        logger.debug(f"[BULK-VALIDATE] Validation complete: passed={result['validationPassed']}, issues={len(result['validationIssues'])}")
        for issue in result['validationIssues']:
            logger.debug(f"[BULK-VALIDATE] Issue: {issue['type']} - {issue['message']}")

        # If validateOnly, return now without executing
        if request.validateOnly:
            logger.info(f"[BULK-COMMIT] Validation only mode: {len(result['validationIssues'])} issues found, returning without executing")
            result["success"] = result["validationPassed"]
            return result

        # If validation failed and continueOnError is false, return without executing
        if not result["validationPassed"] and not request.continueOnError:
            logger.warning(f"[BULK-COMMIT] Validation failed with {len(result['validationIssues'])} issues, aborting (continueOnError=false)")
            result["success"] = False
            return result

        # Log if continuing despite validation issues
        if not result["validationPassed"] and request.continueOnError:
            logger.warning(f"[BULK-COMMIT] Continuing despite {len(result['validationIssues'])} validation issues (continueOnError=true)")

        # Phase 1: Create groups first (if any)
        if request.groupsToCreate:
            logger.debug(f"[BULK-GROUP] Phase 1: Creating {len(request.groupsToCreate)} groups")
            for group_info in request.groupsToCreate:
                group_name = group_info.get("name")
                if not group_name:
                    logger.debug(f"[BULK-GROUP] Skipping group with no name")
                    continue
                try:
                    logger.debug(f"[BULK-GROUP] Creating group: '{group_name}'")
                    # Try to create the group
                    new_group = await client.create_channel_group(group_name)
                    result["groupIdMap"][group_name] = new_group["id"]
                    logger.debug(f"[BULK-GROUP] Created group '{group_name}' -> ID {new_group['id']}")
                except Exception as e:
                    error_str = str(e)
                    # If group already exists, try to find it
                    if "400" in error_str or "already exists" in error_str.lower():
                        logger.debug(f"[BULK-GROUP] Group '{group_name}' may already exist, searching...")
                        try:
                            groups = await client.get_channel_groups()
                            for g in groups:
                                if g.get("name") == group_name:
                                    result["groupIdMap"][group_name] = g["id"]
                                    logger.debug(f"[BULK-GROUP] Found existing group '{group_name}' -> ID {g['id']}")
                                    break
                        except Exception as find_err:
                            logger.debug(f"[BULK-GROUP] Failed to search for existing group: {find_err}")
                    else:
                        # Non-duplicate error - fail the whole operation
                        logger.error(f"[BULK-GROUP] Failed to create group '{group_name}': {e}")
                        result["success"] = False
                        result["errors"].append({
                            "operationId": f"create-group-{group_name}",
                            "error": str(e)
                        })
                        return result
            logger.debug(f"[BULK-GROUP] Group creation complete: {len(result['groupIdMap'])} groups mapped")

        # Phase 2: Process operations sequentially
        logger.debug(f"[BULK-APPLY] Phase 2: Processing {len(request.operations)} operations")
        for idx, op in enumerate(request.operations):
            op_id = f"op-{idx}-{op.type}"
            try:
                if op.type == "updateChannel":
                    channel_id = resolve_id(op.channelId)
                    logger.debug(f"[BULK-APPLY] [{idx+1}/{len(request.operations)}] updateChannel: channel_id={channel_id}, data={op.data}")
                    await client.update_channel(channel_id, op.data)
                    result["operationsApplied"] += 1

                elif op.type == "addStreamToChannel":
                    channel_id = resolve_id(op.channelId)
                    logger.debug(f"[BULK-APPLY] [{idx+1}/{len(request.operations)}] addStreamToChannel: channel_id={channel_id}, stream_id={op.streamId}")
                    channel = await client.get_channel(channel_id)
                    current_streams = channel.get("streams", [])
                    if op.streamId not in current_streams:
                        current_streams.append(op.streamId)
                        await client.update_channel(channel_id, {"streams": current_streams})
                        logger.debug(f"[BULK-APPLY] Added stream {op.streamId} to channel {channel_id}")
                    else:
                        logger.debug(f"[BULK-APPLY] Stream {op.streamId} already in channel {channel_id}, skipping")
                    result["operationsApplied"] += 1

                elif op.type == "removeStreamFromChannel":
                    channel_id = resolve_id(op.channelId)
                    logger.debug(f"[BULK-APPLY] [{idx+1}/{len(request.operations)}] removeStreamFromChannel: channel_id={channel_id}, stream_id={op.streamId}")
                    channel = await client.get_channel(channel_id)
                    current_streams = channel.get("streams", [])
                    if op.streamId in current_streams:
                        current_streams.remove(op.streamId)
                        await client.update_channel(channel_id, {"streams": current_streams})
                        logger.debug(f"[BULK-APPLY] Removed stream {op.streamId} from channel {channel_id}")
                    else:
                        logger.debug(f"[BULK-APPLY] Stream {op.streamId} not in channel {channel_id}, skipping")
                    result["operationsApplied"] += 1

                elif op.type == "reorderChannelStreams":
                    channel_id = resolve_id(op.channelId)
                    logger.debug(f"[BULK-APPLY] [{idx+1}/{len(request.operations)}] reorderChannelStreams: channel_id={channel_id}, streams={op.streamIds}")
                    await client.update_channel(channel_id, {"streams": op.streamIds})
                    result["operationsApplied"] += 1

                elif op.type == "bulkAssignChannelNumbers":
                    resolved_ids = [resolve_id(cid) for cid in op.channelIds]
                    logger.debug(f"[BULK-APPLY] [{idx+1}/{len(request.operations)}] bulkAssignChannelNumbers: {len(resolved_ids)} channels starting at {op.startingNumber}")
                    await client.assign_channel_numbers(resolved_ids, op.startingNumber)
                    result["operationsApplied"] += 1

                elif op.type == "createChannel":
                    logger.debug(f"[BULK-APPLY] [{idx+1}/{len(request.operations)}] createChannel: name='{op.name}', tempId={op.tempId}, groupId={op.groupId}, newGroupName={op.newGroupName}")
                    # Resolve group ID
                    group_id = resolve_group_id(op.groupId, op.newGroupName)

                    # Handle logo - if logoUrl provided but no logoId, try to find/create logo
                    logo_id = op.logoId
                    if not logo_id and op.logoUrl:
                        try:
                            logger.debug(f"[BULK-APPLY] Looking for logo by URL for channel '{op.name}'")
                            # Try to find existing logo by URL
                            existing_logo = await client.find_logo_by_url(op.logoUrl)
                            if existing_logo:
                                logo_id = existing_logo["id"]
                                logger.debug(f"[BULK-APPLY] Found existing logo ID {logo_id}")
                            else:
                                # Create new logo
                                new_logo = await client.create_logo({"name": op.name, "url": op.logoUrl})
                                logo_id = new_logo["id"]
                                logger.debug(f"[BULK-APPLY] Created new logo ID {logo_id}")
                        except Exception as logo_err:
                            logger.warning(f"[BULK-APPLY] Failed to create/find logo for channel '{op.name}': {logo_err}")
                            # Continue without logo

                    # Create the channel
                    channel_data = {"name": op.name}
                    if op.channelNumber is not None:
                        channel_data["channel_number"] = op.channelNumber
                    if group_id is not None:
                        channel_data["channel_group_id"] = group_id
                    if logo_id is not None:
                        channel_data["logo_id"] = logo_id
                    if op.tvgId is not None:
                        channel_data["tvg_id"] = op.tvgId

                    logger.debug(f"[BULK-APPLY] Creating channel with data: {channel_data}")
                    new_channel = await client.create_channel(channel_data)

                    # Track temp ID -> real ID mapping
                    if op.tempId < 0:
                        result["tempIdMap"][op.tempId] = new_channel["id"]

                    result["operationsApplied"] += 1
                    logger.debug(f"[BULK-APPLY] Created channel '{op.name}' (temp: {op.tempId} -> real: {new_channel['id']})")

                elif op.type == "deleteChannel":
                    channel_id = resolve_id(op.channelId)
                    logger.debug(f"[BULK-APPLY] [{idx+1}/{len(request.operations)}] deleteChannel: channel_id={channel_id}")
                    await client.delete_channel(channel_id)
                    result["operationsApplied"] += 1
                    logger.debug(f"[BULK-APPLY] Deleted channel {channel_id}")

                elif op.type == "createGroup":
                    logger.debug(f"[BULK-APPLY] [{idx+1}/{len(request.operations)}] createGroup: name='{op.name}'")
                    # Groups should be created in Phase 1, but handle here if needed
                    if op.name not in result["groupIdMap"]:
                        new_group = await client.create_channel_group(op.name)
                        result["groupIdMap"][op.name] = new_group["id"]
                        logger.debug(f"[BULK-APPLY] Created group '{op.name}' -> ID {new_group['id']}")
                    else:
                        logger.debug(f"[BULK-APPLY] Group '{op.name}' already exists with ID {result['groupIdMap'][op.name]}")
                    result["operationsApplied"] += 1

                elif op.type == "deleteChannelGroup":
                    logger.debug(f"[BULK-APPLY] [{idx+1}/{len(request.operations)}] deleteChannelGroup: groupId={op.groupId}")
                    await client.delete_channel_group(op.groupId)
                    result["operationsApplied"] += 1
                    logger.debug(f"[BULK-APPLY] Deleted group {op.groupId}")

            except Exception as e:
                # Build detailed error info with channel/stream names
                error_details = {
                    "operationId": op_id,
                    "operationType": op.type,
                    "error": str(e),
                }

                # Add context based on operation type
                if hasattr(op, 'channelId'):
                    error_details["channelId"] = op.channelId
                    # Try to get channel name from our lookup
                    if op.channelId in existing_channels:
                        error_details["channelName"] = existing_channels[op.channelId].get("name", f"Channel {op.channelId}")
                    else:
                        error_details["channelName"] = f"Channel {op.channelId}"

                if hasattr(op, 'streamId'):
                    error_details["streamId"] = op.streamId
                    # Try to get stream name from our lookup
                    if op.streamId in existing_streams:
                        error_details["streamName"] = existing_streams[op.streamId].get("name", f"Stream {op.streamId}")
                    else:
                        error_details["streamName"] = f"Stream {op.streamId}"

                if hasattr(op, 'name'):
                    error_details["entityName"] = op.name

                # Log with detailed context
                channel_info = f" (channel: {error_details.get('channelName', 'N/A')})" if 'channelName' in error_details else ""
                stream_info = f" (stream: {error_details.get('streamName', 'N/A')})" if 'streamName' in error_details else ""
                logger.error(f"[BULK-APPLY] Operation {op_id} failed{channel_info}{stream_info}: {e}")

                result["operationsFailed"] += 1
                result["errors"].append(error_details)

                # If continueOnError, keep processing; otherwise stop
                if not request.continueOnError:
                    logger.debug(f"[BULK-APPLY] Stopping due to error (continueOnError=false)")
                    result["success"] = False
                    break
                else:
                    logger.debug(f"[BULK-APPLY] Continuing despite error (continueOnError=true)")
                # If continuing, mark as partial failure but keep going
                # success will be determined at the end based on whether any ops succeeded

        # Determine final success status
        # If continueOnError was used, success means at least some operations succeeded
        if request.continueOnError:
            result["success"] = result["operationsFailed"] == 0 or result["operationsApplied"] > 0
        else:
            result["success"] = result["operationsFailed"] == 0

        # Log summary
        logger.debug(f"[BULK-COMMIT] Phase 2 complete: {result['operationsApplied']} applied, {result['operationsFailed']} failed")
        logger.debug(f"[BULK-COMMIT] ID mappings: {len(result['tempIdMap'])} channels, {len(result['groupIdMap'])} groups")

        # Log summary to journal
        journal.log_entry(
            category="channel",
            action_type="bulk_commit",
            entity_id=None,
            entity_name="Bulk Commit",
            description=f"Applied {result['operationsApplied']} operations in bulk commit" +
                        (f" ({result['operationsFailed']} failed)" if result["operationsFailed"] > 0 else ""),
            after_value={
                "operations_applied": result["operationsApplied"],
                "operations_failed": result["operationsFailed"],
                "channels_created": len(result["tempIdMap"]),
                "groups_created": len(result["groupIdMap"]),
                "validation_issues": len(result["validationIssues"]),
                "continue_on_error": request.continueOnError,
            },
            batch_id=batch_id,
        )

        logger.info(f"[BULK-COMMIT] Completed (batch={batch_id}): success={result['success']}, applied={result['operationsApplied']}, failed={result['operationsFailed']}" +
                   (f", validation_issues={len(result['validationIssues'])}" if result["validationIssues"] else ""))
        return result

    except Exception as e:
        logger.exception(f"[BULK-COMMIT] Unexpected error (batch={batch_id}): {e}")
        result["success"] = False
        result["errors"].append({
            "operationId": "bulk-commit",
            "error": str(e)
        })
        return result


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


@app.delete("/api/channel-groups/orphaned")
async def delete_orphaned_channel_groups(request: DeleteOrphanedGroupsRequest | None = Body(None)):
    """Delete channel groups that are truly orphaned.

    A group is deleted if it has no streams AND no channels.
    M3U groups contain streams, manual groups contain channels.

    Args:
        request: Optional request body with group_ids list. If None or empty, all orphaned groups are deleted.
    """
    logger.debug(f"[DELETE-ORPHANED] Request received: {request}")
    logger.debug(f"[DELETE-ORPHANED] Request type: {type(request)}")

    client = get_client()
    group_ids = request.group_ids if request else None
    logger.debug(f"[DELETE-ORPHANED] Extracted group_ids: {group_ids}")

    try:
        # Use the same logic as GET to find orphaned groups
        logger.debug(f"[DELETE-ORPHANED] Fetching all channel groups...")
        all_groups = await client.get_channel_groups()
        logger.debug(f"[DELETE-ORPHANED] Found {len(all_groups)} total channel groups")

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
        logger.debug(f"[DELETE-ORPHANED] Identifying orphaned groups...")
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
                logger.debug(f"[DELETE-ORPHANED] Group {group_id} ({group_name}) is orphaned: streams={stream_count}, channels={channel_count}, m3u={m3u_info is not None}, override_target={is_override_target}")

        logger.debug(f"[DELETE-ORPHANED] Found {len(orphaned_groups)} orphaned groups")

        if not orphaned_groups:
            logger.debug(f"[DELETE-ORPHANED] No orphaned groups found, returning early")
            return {
                "status": "ok",
                "message": "No orphaned channel groups found",
                "deleted_groups": [],
                "failed_groups": [],
            }

        # Filter to only the specified group IDs if provided
        groups_to_delete = orphaned_groups
        if group_ids is not None:
            logger.debug(f"[DELETE-ORPHANED] Filtering to specified group IDs: {group_ids}")
            groups_to_delete = [g for g in orphaned_groups if g["id"] in group_ids]
            logger.debug(f"[DELETE-ORPHANED] After filtering: {len(groups_to_delete)} groups to delete")
            if not groups_to_delete:
                logger.debug(f"[DELETE-ORPHANED] No matching groups to delete, returning early")
                return {
                    "status": "ok",
                    "message": "No matching orphaned groups to delete",
                    "deleted_groups": [],
                    "failed_groups": [],
                }

        # Delete each orphaned group
        logger.debug(f"[DELETE-ORPHANED] Deleting {len(groups_to_delete)} orphaned groups...")
        deleted_groups = []
        failed_groups = []
        for orphan in groups_to_delete:
            group_id = orphan["id"]
            group_name = orphan["name"]
            try:
                logger.debug(f"[DELETE-ORPHANED] Attempting to delete group {group_id} ({group_name})...")
                await client.delete_channel_group(group_id)
                deleted_groups.append({"id": group_id, "name": group_name, "reason": orphan["reason"]})
                logger.info(f"[DELETE-ORPHANED] Successfully deleted orphaned channel group: {group_id} ({group_name}) - {orphan['reason']}")
            except Exception as group_err:
                failed_groups.append({"id": group_id, "name": group_name, "error": str(group_err)})
                logger.error(f"[DELETE-ORPHANED] Failed to delete orphaned channel group {group_id} ({group_name}): {group_err}")

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
        # Get all channel groups first
        all_groups = await client.get_channel_groups()
        logger.info(f"Found {len(all_groups)} total channel groups")

        # Build a map of group_id -> group info for easy lookup
        group_map = {g["id"]: g for g in all_groups}

        # Track which groups have channels with streams
        groups_with_streams_ids = set()

        # Fetch all channels and check which groups have channels with streams
        page = 1
        total_channels = 0
        channels_with_streams = 0
        sample_channel_groups = []  # Track first 5 for debugging

        while True:
            result = await client.get_channels(page=page, page_size=500)
            page_channels = result.get("results", [])
            total_channels += len(page_channels)

            for channel in page_channels:
                # Check if channel has any streams
                stream_ids = channel.get("streams", [])
                if stream_ids:  # Has at least one stream
                    channels_with_streams += 1
                    # Record this group as having a channel with streams
                    channel_group_id = channel.get("channel_group_id")

                    # Collect samples for debugging - dump first channel completely
                    if len(sample_channel_groups) == 0:
                        logger.info(f"First channel with streams (FULL DATA): {channel}")

                    if len(sample_channel_groups) < 5:
                        sample_channel_groups.append({
                            "channel_id": channel.get("id"),
                            "channel_name": channel.get("name"),
                            "channel_group_id": channel_group_id,
                            "channel_group_type": type(channel_group_id).__name__,
                            "stream_count": len(stream_ids)
                        })

                    # IMPORTANT: Check for not None instead of truthy to handle group ID 0
                    if channel_group_id is not None:
                        groups_with_streams_ids.add(channel_group_id)

            if not result.get("next"):
                break
            page += 1
            if page > 50:  # Safety limit
                break

        # Log samples for debugging
        if sample_channel_groups:
            logger.info(f"Sample channels with streams (first 5): {sample_channel_groups}")

        logger.info(f"Scanned {total_channels} channels, found {channels_with_streams} with streams")
        logger.info(f"Found {len(groups_with_streams_ids)} groups with channels containing streams")
        logger.info(f"Group IDs found: {sorted(list(groups_with_streams_ids))}")
        logger.info(f"Group IDs in group_map: {sorted(list(group_map.keys()))}")

        # Build the result list
        groups_with_streams = []
        not_in_map = []
        for group_id in groups_with_streams_ids:
            if group_id in group_map:
                group = group_map[group_id]
                groups_with_streams.append({
                    "id": group["id"],
                    "name": group["name"]
                })
            else:
                not_in_map.append(group_id)

        if not_in_map:
            logger.warning(f"Found {len(not_in_map)} group IDs in channels but not in group_map: {not_in_map}")

        # Sort by name for consistent display
        groups_with_streams.sort(key=lambda g: g["name"].lower())

        logger.info(f"Returning {len(groups_with_streams)} groups with streams")
        return {
            "groups": groups_with_streams,
            "total_groups": len(all_groups)
        }
    except Exception as e:
        logger.error(f"Failed to get channel groups with streams: {e}")
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
    Time filtering significantly reduces data size and prevents timeouts.
    """
    client = get_client()
    try:
        return await client.get_epg_grid(start=start, end=end)
    except httpx.ReadTimeout:
        raise HTTPException(
            status_code=504,
            detail="EPG data request timed out. This usually happens with very large EPG datasets. Try reducing the time range or contact your Dispatcharr administrator to optimize EPG data size."
        )
    except httpx.HTTPStatusError as e:
        # Handle upstream 504 from Dispatcharr
        if e.response.status_code == 504:
            raise HTTPException(
                status_code=504,
                detail="Dispatcharr EPG service timed out. This usually happens with very large channel counts (~2000+). The time range has been reduced to help, but you may need to optimize your EPG sources or reduce the number of channels."
            )
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        logger.exception(f"Error fetching EPG grid: {e}")
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


class LCNLookupItem(BaseModel):
    """Single item for LCN lookup."""
    tvg_id: str
    epg_source_id: int | None = None  # If provided, only search this EPG source


class BatchLCNRequest(BaseModel):
    """Request body for batch LCN lookup."""
    items: list[LCNLookupItem]


@app.post("/api/epg/lcn/batch")
async def get_epg_lcn_batch(request: BatchLCNRequest):
    """Get LCN (Logical Channel Number) for multiple TVG-IDs from EPG XML sources.

    Each item can specify an EPG source ID. If provided, only that source is searched.
    If not provided, all XMLTV sources are searched (fallback behavior).

    This is more efficient than calling the single endpoint multiple times
    because it fetches and parses each EPG XML source only once.

    Returns a dict mapping tvg_id -> {lcn, source} for found entries.
    """
    import xml.etree.ElementTree as ET
    import gzip
    import io
    import httpx

    if not request.items:
        return {"results": {}}

    # Group items by EPG source
    # Map of epg_source_id -> set of tvg_ids to find in that source
    source_to_tvg_ids: dict[int | None, set[str]] = {}
    for item in request.items:
        if item.epg_source_id not in source_to_tvg_ids:
            source_to_tvg_ids[item.epg_source_id] = set()
        source_to_tvg_ids[item.epg_source_id].add(item.tvg_id)

    client = get_client()
    try:
        # Get all EPG sources
        all_sources = await client.get_epg_sources()

        # Filter to XMLTV sources that have URLs
        all_xmltv_sources = [
            s for s in all_sources
            if s.get("source_type") == "xmltv" and s.get("url")
        ]

        if not all_xmltv_sources:
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

        logger.info(f"Batch LCN lookup for {len(request.items)} items across {len(source_to_tvg_ids)} EPG source(s)")

        async with httpx.AsyncClient(timeout=120.0) as http_client:
            # Process each EPG source group
            for epg_source_id, tvg_ids_for_source in source_to_tvg_ids.items():
                # Determine which sources to search
                if epg_source_id is None:
                    # No EPG source specified - search all sources (fallback)
                    sources_to_search = all_xmltv_sources
                    logger.info(f"Searching all EPG sources for {len(tvg_ids_for_source)} TVG-ID(s) with no EPG source")
                else:
                    # Search only the specified EPG source
                    sources_to_search = [s for s in all_xmltv_sources if s.get("id") == epg_source_id]
                    if not sources_to_search:
                        logger.warning(f"EPG source {epg_source_id} not found or not XMLTV")
                        continue
                    logger.info(f"Searching EPG source {epg_source_id} for {len(tvg_ids_for_source)} TVG-ID(s)")

                # Track what we still need to find for this source group
                remaining = tvg_ids_for_source.copy()

                for source in sources_to_search:
                    url = source.get("url")
                    if not url:
                        continue

                    # Stop early if all found for this source group
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
                        remaining -= set(found.keys())
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
                                remaining -= set(found.keys())
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
                            remaining -= set(found.keys())
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

        logger.info(f"Batch LCN lookup complete: {len(results)}/{len(request.items)} found")
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
# Notifications API
# =============================================================================


@app.get("/api/notifications")
async def get_notifications(
    page: int = 1,
    page_size: int = 50,
    unread_only: bool = False,
    notification_type: Optional[str] = None,
):
    """Get notifications with pagination and filtering."""
    from database import get_session
    from models import Notification

    session = get_session()
    try:
        query = session.query(Notification)

        # Filter by read status
        if unread_only:
            query = query.filter(Notification.read == False)

        # Filter by type
        if notification_type:
            query = query.filter(Notification.type == notification_type)

        # Order by most recent first
        query = query.order_by(Notification.created_at.desc())

        # Get total count
        total = query.count()

        # Apply pagination
        offset = (page - 1) * page_size
        notifications = query.offset(offset).limit(page_size).all()

        # Get unread count
        unread_count = session.query(Notification).filter(Notification.read == False).count()

        return {
            "notifications": [n.to_dict() for n in notifications],
            "total": total,
            "unread_count": unread_count,
            "page": page,
            "page_size": page_size,
        }
    finally:
        session.close()


@app.post("/api/notifications")
async def create_notification(
    notification_type: str = "info",
    title: Optional[str] = None,
    message: str = "",
    source: Optional[str] = None,
    source_id: Optional[str] = None,
    action_label: Optional[str] = None,
    action_url: Optional[str] = None,
    metadata: Optional[dict] = None,
    send_alerts: bool = True,
):
    """Create a new notification.

    Args:
        send_alerts: If True (default), also dispatch to configured alert channels.
    """
    from database import get_session
    import json
    import asyncio
    from models import Notification
    from alert_channels import send_alert

    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    if notification_type not in ("info", "success", "warning", "error"):
        raise HTTPException(status_code=400, detail="Invalid notification type")

    session = get_session()
    try:
        notification = Notification(
            type=notification_type,
            title=title,
            message=message,
            source=source,
            source_id=source_id,
            action_label=action_label,
            action_url=action_url,
            extra_data=json.dumps(metadata) if metadata else None,
        )
        session.add(notification)
        session.commit()
        session.refresh(notification)
        result = notification.to_dict()

        # Dispatch to alert channels asynchronously (non-blocking)
        if send_alerts:
            asyncio.create_task(
                _dispatch_to_alert_channels(
                    title=title,
                    message=message,
                    notification_type=notification_type,
                    source=source,
                    metadata=metadata,
                )
            )

        return result
    finally:
        session.close()


async def _dispatch_to_alert_channels(
    title: Optional[str],
    message: str,
    notification_type: str,
    source: Optional[str],
    metadata: Optional[dict],
):
    """Dispatch notification to all configured alert channels.

    This runs asynchronously and won't block the notification creation.
    Failures are logged but don't affect the original notification.
    """
    from alert_channels import send_alert

    try:
        results = await send_alert(
            title=title or "Notification",
            message=message,
            notification_type=notification_type,
            source=source,
            metadata=metadata,
        )
        if results:
            success_count = sum(1 for v in results.values() if v)
            fail_count = sum(1 for v in results.values() if not v)
            if fail_count > 0:
                logger.warning(
                    f"Alert dispatch: {success_count} succeeded, {fail_count} failed"
                )
            else:
                logger.debug(f"Alert dispatch: sent to {success_count} channel(s)")
    except Exception as e:
        logger.error(f"Failed to dispatch alerts: {e}")


@app.patch("/api/notifications/{notification_id}")
async def update_notification(notification_id: int, read: Optional[bool] = None):
    """Update a notification (mark as read/unread)."""
    from database import get_session
    from datetime import datetime
    from models import Notification

    session = get_session()
    try:
        notification = session.query(Notification).filter(Notification.id == notification_id).first()
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")

        if read is not None:
            notification.read = read
            notification.read_at = datetime.utcnow() if read else None

        session.commit()
        session.refresh(notification)
        return notification.to_dict()
    finally:
        session.close()


@app.patch("/api/notifications/mark-all-read")
async def mark_all_notifications_read():
    """Mark all notifications as read."""
    from database import get_session
    from datetime import datetime
    from models import Notification

    session = get_session()
    try:
        count = session.query(Notification).filter(Notification.read == False).update(
            {"read": True, "read_at": datetime.utcnow()},
            synchronize_session=False
        )
        session.commit()
        return {"marked_read": count}
    finally:
        session.close()


@app.delete("/api/notifications/{notification_id}")
async def delete_notification(notification_id: int):
    """Delete a specific notification."""
    from database import get_session
    from models import Notification

    session = get_session()
    try:
        notification = session.query(Notification).filter(Notification.id == notification_id).first()
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")

        session.delete(notification)
        session.commit()
        return {"deleted": True}
    finally:
        session.close()


@app.delete("/api/notifications")
async def clear_all_notifications(read_only: bool = True):
    """Clear notifications. By default only clears read notifications."""
    from database import get_session
    from models import Notification

    session = get_session()
    try:
        query = session.query(Notification)
        if read_only:
            query = query.filter(Notification.read == True)

        count = query.delete(synchronize_session=False)
        session.commit()
        return {"deleted": count, "read_only": read_only}
    finally:
        session.close()


# =============================================================================
# Alert Channels
# =============================================================================


class AlertChannelCreate(BaseModel):
    name: str
    channel_type: str
    config: dict
    enabled: bool = True
    notify_info: bool = False
    notify_success: bool = True
    notify_warning: bool = True
    notify_error: bool = True
    min_interval_seconds: int = 60


class AlertChannelUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    enabled: Optional[bool] = None
    notify_info: Optional[bool] = None
    notify_success: Optional[bool] = None
    notify_warning: Optional[bool] = None
    notify_error: Optional[bool] = None
    min_interval_seconds: Optional[int] = None


@app.get("/api/alert-channels/types")
async def get_alert_channel_types():
    """Get available alert channel types and their configuration fields."""
    logger.debug("Fetching alert channel types")
    try:
        types = get_channel_types()
        logger.debug(f"Found {len(types)} alert channel types: {[t['type'] for t in types]}")
        return types
    except Exception as e:
        logger.exception(f"Error fetching alert channel types: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/alert-channels")
async def list_alert_channels():
    """List all configured alert channels."""
    from database import get_session
    from models import AlertChannel as AlertChannelModel
    import json

    logger.debug("Listing alert channels")
    session = get_session()
    try:
        channels = session.query(AlertChannelModel).all()
        logger.debug(f"Found {len(channels)} alert channels in database")
        result = [
            {
                "id": ch.id,
                "name": ch.name,
                "channel_type": ch.channel_type,
                "enabled": ch.enabled,
                "config": json.loads(ch.config) if ch.config else {},
                "notify_info": ch.notify_info,
                "notify_success": ch.notify_success,
                "notify_warning": ch.notify_warning,
                "notify_error": ch.notify_error,
                "min_interval_seconds": ch.min_interval_seconds,
                "last_sent_at": ch.last_sent_at.isoformat() + "Z" if ch.last_sent_at else None,
                "created_at": ch.created_at.isoformat() + "Z" if ch.created_at else None,
            }
            for ch in channels
        ]
        return result
    except Exception as e:
        logger.exception(f"Error listing alert channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.post("/api/alert-channels")
async def create_alert_channel(data: AlertChannelCreate):
    """Create a new alert channel."""
    from database import get_session
    from models import AlertChannel as AlertChannelModel
    import json

    logger.debug(f"Creating alert channel: name={data.name}, type={data.channel_type}")

    # Validate channel type
    channel_types = {ct["type"] for ct in get_channel_types()}
    if data.channel_type not in channel_types:
        logger.warning(f"Unknown channel type attempted: {data.channel_type}")
        raise HTTPException(status_code=400, detail=f"Unknown channel type: {data.channel_type}")

    # Validate config
    channel = create_channel(data.channel_type, 0, data.name, data.config)
    if channel:
        is_valid, error = channel.validate_config(data.config)
        if not is_valid:
            logger.warning(f"Invalid config for channel {data.name}: {error}")
            raise HTTPException(status_code=400, detail=error)

    session = get_session()
    try:
        channel_model = AlertChannelModel(
            name=data.name,
            channel_type=data.channel_type,
            config=json.dumps(data.config),
            enabled=data.enabled,
            notify_info=data.notify_info,
            notify_success=data.notify_success,
            notify_warning=data.notify_warning,
            notify_error=data.notify_error,
            min_interval_seconds=data.min_interval_seconds,
        )
        session.add(channel_model)
        session.commit()
        session.refresh(channel_model)

        # Reload the manager to pick up the new channel
        get_alert_manager().reload_channel(channel_model.id)

        logger.info(f"Created alert channel: id={channel_model.id}, name={channel_model.name}, type={channel_model.channel_type}")
        return {
            "id": channel_model.id,
            "name": channel_model.name,
            "channel_type": channel_model.channel_type,
            "enabled": channel_model.enabled,
        }
    except Exception as e:
        logger.exception(f"Error creating alert channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.get("/api/alert-channels/{channel_id}")
async def get_alert_channel(channel_id: int):
    """Get a specific alert channel."""
    from database import get_session
    from models import AlertChannel as AlertChannelModel
    import json

    logger.debug(f"Getting alert channel: id={channel_id}")
    session = get_session()
    try:
        channel = session.query(AlertChannelModel).filter(
            AlertChannelModel.id == channel_id
        ).first()

        if not channel:
            logger.debug(f"Alert channel not found: id={channel_id}")
            raise HTTPException(status_code=404, detail="Alert channel not found")

        logger.debug(f"Found alert channel: id={channel.id}, name={channel.name}")
        return {
            "id": channel.id,
            "name": channel.name,
            "channel_type": channel.channel_type,
            "enabled": channel.enabled,
            "config": json.loads(channel.config) if channel.config else {},
            "notify_info": channel.notify_info,
            "notify_success": channel.notify_success,
            "notify_warning": channel.notify_warning,
            "notify_error": channel.notify_error,
            "min_interval_seconds": channel.min_interval_seconds,
            "last_sent_at": channel.last_sent_at.isoformat() + "Z" if channel.last_sent_at else None,
            "created_at": channel.created_at.isoformat() + "Z" if channel.created_at else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting alert channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.patch("/api/alert-channels/{channel_id}")
async def update_alert_channel(channel_id: int, data: AlertChannelUpdate):
    """Update an alert channel."""
    from database import get_session
    from models import AlertChannel as AlertChannelModel
    import json

    logger.debug(f"Updating alert channel: id={channel_id}")
    session = get_session()
    try:
        channel = session.query(AlertChannelModel).filter(
            AlertChannelModel.id == channel_id
        ).first()

        if not channel:
            logger.debug(f"Alert channel not found for update: id={channel_id}")
            raise HTTPException(status_code=404, detail="Alert channel not found")

        if data.name is not None:
            channel.name = data.name
        if data.config is not None:
            # Validate new config
            channel_instance = create_channel(channel.channel_type, channel.id, channel.name, data.config)
            if channel_instance:
                is_valid, error = channel_instance.validate_config(data.config)
                if not is_valid:
                    logger.warning(f"Invalid config for channel {channel_id}: {error}")
                    raise HTTPException(status_code=400, detail=error)
            channel.config = json.dumps(data.config)
        if data.enabled is not None:
            channel.enabled = data.enabled
        if data.notify_info is not None:
            channel.notify_info = data.notify_info
        if data.notify_success is not None:
            channel.notify_success = data.notify_success
        if data.notify_warning is not None:
            channel.notify_warning = data.notify_warning
        if data.notify_error is not None:
            channel.notify_error = data.notify_error
        if data.min_interval_seconds is not None:
            channel.min_interval_seconds = data.min_interval_seconds

        session.commit()

        # Reload the manager to pick up the changes
        get_alert_manager().reload_channel(channel_id)

        logger.info(f"Updated alert channel: id={channel_id}, name={channel.name}")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating alert channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.delete("/api/alert-channels/{channel_id}")
async def delete_alert_channel(channel_id: int):
    """Delete an alert channel."""
    from database import get_session
    from models import AlertChannel as AlertChannelModel

    logger.debug(f"Deleting alert channel: id={channel_id}")
    session = get_session()
    try:
        channel = session.query(AlertChannelModel).filter(
            AlertChannelModel.id == channel_id
        ).first()

        if not channel:
            logger.debug(f"Alert channel not found for deletion: id={channel_id}")
            raise HTTPException(status_code=404, detail="Alert channel not found")

        channel_name = channel.name
        session.delete(channel)
        session.commit()

        # Remove from manager
        get_alert_manager().reload_channel(channel_id)

        logger.info(f"Deleted alert channel: id={channel_id}, name={channel_name}")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error deleting alert channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.post("/api/alert-channels/{channel_id}/test")
async def test_alert_channel(channel_id: int):
    """Test an alert channel by sending a test message."""
    from database import get_session
    from models import AlertChannel as AlertChannelModel
    import json

    logger.debug(f"Testing alert channel: id={channel_id}")
    session = get_session()
    try:
        channel_model = session.query(AlertChannelModel).filter(
            AlertChannelModel.id == channel_id
        ).first()

        if not channel_model:
            logger.debug(f"Alert channel not found for test: id={channel_id}")
            raise HTTPException(status_code=404, detail="Alert channel not found")

        config = json.loads(channel_model.config) if channel_model.config else {}
        channel = create_channel(
            channel_model.channel_type,
            channel_model.id,
            channel_model.name,
            config
        )

        if not channel:
            logger.warning(f"Unknown channel type for test: {channel_model.channel_type}")
            raise HTTPException(status_code=400, detail=f"Unknown channel type: {channel_model.channel_type}")

        logger.debug(f"Sending test message to channel: {channel_model.name} ({channel_model.channel_type})")
        success, message = await channel.test_connection()
        logger.info(f"Test result for channel {channel_model.name}: success={success}, message={message}")
        return {"success": success, "message": message}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error testing alert channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


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


class ProbeAllRequest(BaseModel):
    """Request for probe all streams endpoint with optional group filtering."""
    channel_groups: list[str] = []  # Empty list means all groups
    skip_m3u_refresh: bool = False  # Skip M3U refresh for on-demand probes


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
async def probe_all_streams_endpoint(request: ProbeAllRequest = ProbeAllRequest()):
    """Trigger probe for all streams (background task).

    Optionally filter by channel groups. If channel_groups is empty, probes all groups.
    """
    logger.info(f"Probe all streams request received with groups filter: {request.channel_groups}")

    prober = get_prober()
    logger.info(f"get_prober() returned: {prober is not None}")

    if not prober:
        logger.error("Stream prober not available - returning 503")
        raise HTTPException(status_code=503, detail="Stream prober not available")

    # If a probe is already "in progress" (possibly stuck), reset it first
    if prober._probing_in_progress:
        logger.warning("Probe state shows in_progress - resetting before starting new probe")
        prober.force_reset_probe_state()

    import asyncio

    async def run_probe_with_logging():
        """Wrapper to catch and log any errors from the probe task."""
        try:
            logger.info("[PROBE-TASK] Background probe task starting...")
            await prober.probe_all_streams(
                channel_groups_override=request.channel_groups or None,
                skip_m3u_refresh=request.skip_m3u_refresh
            )
            logger.info("[PROBE-TASK] Background probe task completed successfully")
        except Exception as e:
            logger.error(f"[PROBE-TASK] Background probe task failed with error: {e}", exc_info=True)

    # Start background task with optional group filter
    logger.info(f"Starting background probe task (groups: {request.channel_groups or 'all'}, skip_m3u_refresh: {request.skip_m3u_refresh})")
    asyncio.create_task(run_probe_with_logging())
    logger.info("Background task created, returning response")
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


@app.get("/api/stream-stats/probe/history")
async def get_probe_history():
    """Get probe run history (last 5 runs)."""
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.get_probe_history()


@app.post("/api/stream-stats/probe/cancel")
async def cancel_probe():
    """Cancel an in-progress probe operation."""
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.cancel_probe()


@app.post("/api/stream-stats/probe/reset")
async def reset_probe_state():
    """Force reset the probe state if it gets stuck."""
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.force_reset_probe_state()


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


# -------------------------------------------------------------------------
# Scheduled Tasks API
# -------------------------------------------------------------------------

class TaskConfigUpdate(BaseModel):
    """Request model for updating task configuration."""
    enabled: Optional[bool] = None
    schedule_type: Optional[str] = None
    interval_seconds: Optional[int] = None
    cron_expression: Optional[str] = None
    schedule_time: Optional[str] = None
    timezone: Optional[str] = None
    config: Optional[dict] = None  # Task-specific configuration (source_ids, account_ids, etc.)


@app.get("/api/tasks")
async def list_tasks():
    """Get all registered tasks with their status."""
    try:
        from task_registry import get_registry
        registry = get_registry()
        return {"tasks": registry.get_all_task_statuses()}
    except Exception as e:
        logger.error(f"Failed to list tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """Get status for a specific task, including all schedules."""
    try:
        from task_registry import get_registry
        from database import get_session
        from models import TaskSchedule
        from schedule_calculator import describe_schedule

        registry = get_registry()
        status = registry.get_task_status(task_id)
        if status is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Include schedules in the response
        session = get_session()
        try:
            schedules = session.query(TaskSchedule).filter(TaskSchedule.task_id == task_id).all()
            status['schedules'] = []
            for schedule in schedules:
                schedule_dict = schedule.to_dict()
                schedule_dict['description'] = describe_schedule(
                    schedule_type=schedule.schedule_type,
                    interval_seconds=schedule.interval_seconds,
                    schedule_time=schedule.schedule_time,
                    timezone=schedule.timezone,
                    days_of_week=schedule.get_days_of_week_list(),
                    day_of_month=schedule.day_of_month,
                )
                status['schedules'].append(schedule_dict)
        finally:
            session.close()

        return status
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/tasks/{task_id}")
async def update_task(task_id: str, config: TaskConfigUpdate):
    """Update task configuration."""
    try:
        from task_registry import get_registry
        registry = get_registry()

        result = registry.update_task_config(
            task_id=task_id,
            enabled=config.enabled,
            schedule_type=config.schedule_type,
            interval_seconds=config.interval_seconds,
            cron_expression=config.cron_expression,
            schedule_time=config.schedule_time,
            timezone=config.timezone,
            task_config=config.config,
        )

        if result is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tasks/{task_id}/run")
async def run_task(task_id: str):
    """Manually trigger a task execution."""
    try:
        from task_engine import get_engine
        engine = get_engine()
        result = await engine.run_task(task_id)

        if result is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        return result.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to run task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a running task."""
    try:
        from task_engine import get_engine
        engine = get_engine()
        return engine.cancel_task(task_id)
    except Exception as e:
        logger.error(f"Failed to cancel task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tasks/{task_id}/history")
async def get_task_history(task_id: str, limit: int = 50, offset: int = 0):
    """Get execution history for a task."""
    try:
        from task_engine import get_engine
        engine = get_engine()
        history = engine.get_task_history(task_id=task_id, limit=limit, offset=offset)
        return {"history": history}
    except Exception as e:
        logger.error(f"Failed to get history for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tasks/engine/status")
async def get_engine_status():
    """Get task engine status."""
    try:
        from task_engine import get_engine
        engine = get_engine()
        return engine.get_status()
    except Exception as e:
        logger.error(f"Failed to get engine status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tasks/history/all")
async def get_all_task_history(limit: int = 100, offset: int = 0):
    """Get execution history for all tasks."""
    try:
        from task_engine import get_engine
        engine = get_engine()
        history = engine.get_task_history(task_id=None, limit=limit, offset=offset)
        return {"history": history}
    except Exception as e:
        logger.error(f"Failed to get all task history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cron/presets")
async def get_cron_presets():
    """Get available cron presets for task scheduling."""
    try:
        from cron_parser import get_preset_list
        return {"presets": get_preset_list()}
    except Exception as e:
        logger.error(f"Failed to get cron presets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class CronValidateRequest(BaseModel):
    """Request to validate a cron expression."""
    expression: str


@app.post("/api/cron/validate")
async def validate_cron(request: CronValidateRequest):
    """Validate a cron expression."""
    try:
        from cron_parser import validate_cron_expression, describe_cron_expression, get_next_n_run_times

        is_valid, error = validate_cron_expression(request.expression)

        if not is_valid:
            return {
                "valid": False,
                "error": error,
            }

        # Get next run times for valid expressions
        next_times = get_next_n_run_times(request.expression, n=5)

        return {
            "valid": True,
            "description": describe_cron_expression(request.expression),
            "next_runs": [t.isoformat() + "Z" for t in next_times],
        }
    except Exception as e:
        logger.error(f"Failed to validate cron expression: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# Task Schedule API - Multiple schedules per task
# =========================================================================

class TaskScheduleCreate(BaseModel):
    """Request body for creating a task schedule."""
    name: Optional[str] = None
    enabled: bool = True
    schedule_type: Literal['interval', 'daily', 'weekly', 'biweekly', 'monthly']
    interval_seconds: Optional[int] = None
    schedule_time: Optional[str] = None  # HH:MM format
    timezone: Optional[str] = None
    days_of_week: Optional[list] = None  # List of day numbers (0=Sunday, 6=Saturday)
    day_of_month: Optional[int] = None  # 1-31, or -1 for last day


class TaskScheduleUpdate(BaseModel):
    """Request body for updating a task schedule."""
    name: Optional[str] = None
    enabled: Optional[bool] = None
    schedule_type: Optional[Literal['interval', 'daily', 'weekly', 'biweekly', 'monthly']] = None
    interval_seconds: Optional[int] = None
    schedule_time: Optional[str] = None
    timezone: Optional[str] = None
    days_of_week: Optional[list] = None
    day_of_month: Optional[int] = None


@app.get("/api/tasks/{task_id}/schedules")
async def list_task_schedules(task_id: str):
    """Get all schedules for a task."""
    try:
        from database import get_session
        from models import TaskSchedule, ScheduledTask
        from schedule_calculator import describe_schedule

        session = get_session()
        try:
            # Verify task exists
            task = session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()
            if not task:
                raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

            # Get all schedules for this task
            schedules = session.query(TaskSchedule).filter(TaskSchedule.task_id == task_id).all()

            result = []
            for schedule in schedules:
                schedule_dict = schedule.to_dict()
                # Add human-readable description
                schedule_dict['description'] = describe_schedule(
                    schedule_type=schedule.schedule_type,
                    interval_seconds=schedule.interval_seconds,
                    schedule_time=schedule.schedule_time,
                    timezone=schedule.timezone,
                    days_of_week=schedule.get_days_of_week_list(),
                    day_of_month=schedule.day_of_month,
                )
                result.append(schedule_dict)

            return {"schedules": result}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list schedules for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tasks/{task_id}/schedules")
async def create_task_schedule(task_id: str, data: TaskScheduleCreate):
    """Create a new schedule for a task."""
    try:
        from database import get_session
        from models import TaskSchedule, ScheduledTask
        from schedule_calculator import calculate_next_run, describe_schedule

        session = get_session()
        try:
            # Verify task exists
            task = session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()
            if not task:
                raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

            # Create the schedule
            schedule = TaskSchedule(
                task_id=task_id,
                name=data.name,
                enabled=data.enabled,
                schedule_type=data.schedule_type,
                interval_seconds=data.interval_seconds,
                schedule_time=data.schedule_time,
                timezone=data.timezone or "UTC",
                day_of_month=data.day_of_month,
            )

            # Set days_of_week if provided
            if data.days_of_week:
                schedule.set_days_of_week_list(data.days_of_week)

            # Calculate next run time
            if data.enabled:
                schedule.next_run_at = calculate_next_run(
                    schedule_type=data.schedule_type,
                    interval_seconds=data.interval_seconds,
                    schedule_time=data.schedule_time,
                    timezone=data.timezone or "UTC",
                    days_of_week=data.days_of_week,
                    day_of_month=data.day_of_month,
                )

            session.add(schedule)
            session.commit()
            session.refresh(schedule)

            # Build response
            result = schedule.to_dict()
            result['description'] = describe_schedule(
                schedule_type=schedule.schedule_type,
                interval_seconds=schedule.interval_seconds,
                schedule_time=schedule.schedule_time,
                timezone=schedule.timezone,
                days_of_week=schedule.get_days_of_week_list(),
                day_of_month=schedule.day_of_month,
            )

            # Update the parent task's next_run_at to be the earliest of all schedules
            _update_task_next_run(session, task_id)
            session.commit()

            return result
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create schedule for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/tasks/{task_id}/schedules/{schedule_id}")
async def update_task_schedule(task_id: str, schedule_id: int, data: TaskScheduleUpdate):
    """Update a task schedule."""
    try:
        from database import get_session
        from models import TaskSchedule, ScheduledTask
        from schedule_calculator import calculate_next_run, describe_schedule

        session = get_session()
        try:
            # Verify schedule exists and belongs to task
            schedule = session.query(TaskSchedule).filter(
                TaskSchedule.id == schedule_id,
                TaskSchedule.task_id == task_id
            ).first()

            if not schedule:
                raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found for task {task_id}")

            # Update fields if provided
            if data.name is not None:
                schedule.name = data.name
            if data.enabled is not None:
                schedule.enabled = data.enabled
            if data.schedule_type is not None:
                schedule.schedule_type = data.schedule_type
            if data.interval_seconds is not None:
                schedule.interval_seconds = data.interval_seconds
            if data.schedule_time is not None:
                schedule.schedule_time = data.schedule_time
            if data.timezone is not None:
                schedule.timezone = data.timezone
            if data.days_of_week is not None:
                schedule.set_days_of_week_list(data.days_of_week)
            if data.day_of_month is not None:
                schedule.day_of_month = data.day_of_month

            # Recalculate next run time
            if schedule.enabled:
                schedule.next_run_at = calculate_next_run(
                    schedule_type=schedule.schedule_type,
                    interval_seconds=schedule.interval_seconds,
                    schedule_time=schedule.schedule_time,
                    timezone=schedule.timezone,
                    days_of_week=schedule.get_days_of_week_list(),
                    day_of_month=schedule.day_of_month,
                )
            else:
                schedule.next_run_at = None

            session.commit()
            session.refresh(schedule)

            # Build response
            result = schedule.to_dict()
            result['description'] = describe_schedule(
                schedule_type=schedule.schedule_type,
                interval_seconds=schedule.interval_seconds,
                schedule_time=schedule.schedule_time,
                timezone=schedule.timezone,
                days_of_week=schedule.get_days_of_week_list(),
                day_of_month=schedule.day_of_month,
            )

            # Update the parent task's next_run_at
            _update_task_next_run(session, task_id)
            session.commit()

            return result
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update schedule {schedule_id} for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/tasks/{task_id}/schedules/{schedule_id}")
async def delete_task_schedule(task_id: str, schedule_id: int):
    """Delete a task schedule."""
    try:
        from database import get_session
        from models import TaskSchedule

        session = get_session()
        try:
            # Verify schedule exists and belongs to task
            schedule = session.query(TaskSchedule).filter(
                TaskSchedule.id == schedule_id,
                TaskSchedule.task_id == task_id
            ).first()

            if not schedule:
                raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found for task {task_id}")

            session.delete(schedule)
            session.commit()

            # Update the parent task's next_run_at
            _update_task_next_run(session, task_id)
            session.commit()

            return {"status": "deleted", "id": schedule_id}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete schedule {schedule_id} for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _update_task_next_run(session, task_id: str) -> None:
    """Update a task's next_run_at based on its schedules."""
    from models import TaskSchedule, ScheduledTask

    # Get the earliest next_run_at from all enabled schedules
    schedules = session.query(TaskSchedule).filter(
        TaskSchedule.task_id == task_id,
        TaskSchedule.enabled == True,
        TaskSchedule.next_run_at != None
    ).order_by(TaskSchedule.next_run_at).all()

    task = session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()
    if task:
        if schedules:
            task.next_run_at = schedules[0].next_run_at
        else:
            task.next_run_at = None


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
