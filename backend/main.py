from fastapi import FastAPI, HTTPException, Request, Body, UploadFile, File, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, PlainTextResponse
import asyncio
import subprocess
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Literal, Union, List
import httpx
import os
import re
import logging
import time
from collections import defaultdict
from datetime import datetime

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
from database import init_db, get_session
import journal
from bandwidth_tracker import BandwidthTracker, set_tracker, get_tracker
from stream_prober import StreamProber, set_prober, get_prober
from alert_methods import get_alert_manager, get_method_types, create_method, send_alert
# Import method implementations to register them
import alert_methods_discord  # noqa: F401
import alert_methods_smtp  # noqa: F401
import alert_methods_telegram  # noqa: F401

# Import M3U digest for immediate notifications after refresh
from tasks.m3u_digest import send_immediate_digest

# Polling configuration for manual refresh endpoints
# These control background polling to detect when Dispatcharr completes refresh operations
REFRESH_POLL_INTERVAL_SECONDS = 5
M3U_REFRESH_MAX_WAIT_SECONDS = 300   # 5 minutes for M3U
EPG_REFRESH_MAX_WAIT_SECONDS = 900   # 15 minutes for EPG (larger files)

# Configure logging
# Start with environment variable, will be updated from settings in startup
initial_log_level = get_log_level_from_env()
logging.basicConfig(
    level=getattr(logging, initial_log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# OpenAPI tags for organizing endpoints in Swagger UI
tags_metadata = [
    {"name": "Health", "description": "Health check and debug endpoints"},
    {"name": "Settings", "description": "Application settings and configuration"},
    {"name": "Channels", "description": "Channel management - create, update, delete channels"},
    {"name": "Channel Groups", "description": "Organize channels into groups"},
    {"name": "Channel Profiles", "description": "Channel profile configurations"},
    {"name": "Streams", "description": "Stream management and statistics"},
    {"name": "Stream Profiles", "description": "Stream profile configurations"},
    {"name": "M3U", "description": "M3U account management, refresh, and VOD"},
    {"name": "M3U Digest", "description": "M3U change digest email notifications"},
    {"name": "EPG", "description": "Electronic Program Guide sources and data"},
    {"name": "Providers", "description": "Stream providers (M3U accounts)"},
    {"name": "Tasks", "description": "Scheduled tasks and task execution"},
    {"name": "Notifications", "description": "System notifications"},
    {"name": "Alert Methods", "description": "Alert delivery methods (Discord, Email, Telegram)"},
    {"name": "Journal", "description": "Activity journal and audit log"},
    {"name": "Stats", "description": "Statistics and analytics"},
    {"name": "Stream Stats", "description": "Stream health monitoring and statistics"},
    {"name": "Normalization", "description": "Channel name normalization rules"},
    {"name": "Tags", "description": "Tag management for channels"},
    {"name": "Cache", "description": "Cache management"},
    {"name": "Cron", "description": "Cron expression utilities"},
    {"name": "Authentication", "description": "User authentication and session management"},
    {"name": "TLS", "description": "TLS/SSL certificate management with Let's Encrypt"},
    {"name": "Auto-Creation", "description": "Automatic channel creation from streams based on rules"},
]

app = FastAPI(
    title="Enhanced Channel Manager API",
    description="""
## Overview
Enhanced Channel Manager (ECM) provides a powerful API for managing IPTV channels,
M3U playlists, EPG data, and more.

## Features
- **Channel Management**: Create, organize, and manage TV channels
- **M3U Integration**: Import and sync M3U playlists from multiple providers
- **EPG Support**: Manage Electronic Program Guide data sources
- **Stream Monitoring**: Track stream health and statistics
- **Scheduled Tasks**: Automate refresh and maintenance tasks
- **Notifications**: Get alerts via Discord, Email, or Telegram

## Authentication
Currently, the API does not require authentication for local access.

## Rate Limiting
No rate limiting is enforced, but rapid polling is logged for diagnostics.
    """,
    version="0.8.7",
    openapi_tags=tags_metadata,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include auth router
from auth.routes import router as auth_router
app.include_router(auth_router)

# Include admin router
from auth.admin_routes import router as admin_router
app.include_router(admin_router)

# Include TLS router
from tls.routes import router as tls_router
app.include_router(tls_router)


# ============================================================================
# Request Timing and Rate Tracking Middleware (for CPU diagnostics)
# ============================================================================
# Track request rates per endpoint to detect rapid polling
_request_rate_tracker: dict[str, list[float]] = defaultdict(list)
_rate_window_seconds = 10  # Track requests over 10-second window
_rate_alert_threshold = 20  # Warn if more than 20 requests in window

def _clean_old_timestamps(timestamps: list[float], window: float) -> list[float]:
    """Remove timestamps older than the window."""
    cutoff = time.time() - window
    return [t for t in timestamps if t > cutoff]


@app.middleware("http")
async def request_timing_middleware(request: Request, call_next):
    """Log request timing and detect rapid polling patterns."""
    start_time = time.time()
    path = request.url.path
    method = request.method

    # Skip static files and health checks for timing logs
    skip_timing = path.startswith("/assets") or path == "/api/health"

    # Process the request
    response = await call_next(request)

    # Calculate duration
    duration_ms = (time.time() - start_time) * 1000

    if not skip_timing:
        # Track request rate for this endpoint
        endpoint_key = f"{method} {path}"
        now = time.time()
        _request_rate_tracker[endpoint_key].append(now)
        _request_rate_tracker[endpoint_key] = _clean_old_timestamps(
            _request_rate_tracker[endpoint_key], _rate_window_seconds
        )
        request_count = len(_request_rate_tracker[endpoint_key])

        # Log timing at DEBUG level
        logger.debug(
            f"[REQUEST] {method} {path} - {duration_ms:.1f}ms - "
            f"status={response.status_code} - "
            f"rate={request_count}/{_rate_window_seconds}s"
        )

        # Warn if endpoint is being hit too frequently (possible runaway loop)
        if request_count >= _rate_alert_threshold:
            logger.warning(
                f"[RAPID-POLLING] {endpoint_key} hit {request_count} times in "
                f"{_rate_window_seconds}s - possible polling issue!"
            )

        # Log slow requests at INFO level
        if duration_ms > 1000:
            logger.info(
                f"[SLOW-REQUEST] {method} {path} took {duration_ms:.1f}ms"
            )

    return response


# ============================================================================
# Diagnostic Endpoint for Request Rate Stats
# ============================================================================
@app.get("/api/debug/request-rates", tags=["Health"])
async def get_request_rates():
    """Get current request rate statistics for all endpoints.

    Useful for diagnosing CPU issues - shows which endpoints are being
    hit most frequently.
    """
    now = time.time()
    stats = {}
    for endpoint, timestamps in _request_rate_tracker.items():
        clean_timestamps = _clean_old_timestamps(timestamps, _rate_window_seconds)
        if clean_timestamps:
            stats[endpoint] = {
                "count_last_10s": len(clean_timestamps),
                "requests_per_second": len(clean_timestamps) / _rate_window_seconds,
                "last_request_ago_ms": int((now - max(clean_timestamps)) * 1000),
            }

    # Sort by request count descending
    sorted_stats = dict(sorted(stats.items(), key=lambda x: x[1]["count_last_10s"], reverse=True))

    return {
        "window_seconds": _rate_window_seconds,
        "alert_threshold": _rate_alert_threshold,
        "timestamp": datetime.utcnow().isoformat(),
        "endpoints": sorted_stats,
    }


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

    # Remove directional suffixes from Timezone Tags (East/West affect EPG timing)
    try:
        from normalization_migration import fix_timezone_tags_remove_directional
        session = get_session()
        try:
            result = fix_timezone_tags_remove_directional(session)
            if result.get("rules_deleted", 0) > 0:
                logger.info(f"Removed {result['rules_deleted']} directional suffix rules from Timezone Tags")
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"Could not apply timezone tags fix: {e}")

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
        # Note: Scheduled probing is now controlled by the Task Engine (StreamProbeTask)
        try:
            logger.debug(
                f"Initializing stream prober (batch: {settings.stream_probe_batch_size}, timeout: {settings.stream_probe_timeout}s)"
            )
            prober = StreamProber(
                get_client(),
                probe_timeout=settings.stream_probe_timeout,
                probe_batch_size=settings.stream_probe_batch_size,
                user_timezone=settings.user_timezone,
                bitrate_sample_duration=settings.bitrate_sample_duration,
                parallel_probing_enabled=settings.parallel_probing_enabled,
                max_concurrent_probes=settings.max_concurrent_probes,
                skip_recently_probed_hours=settings.skip_recently_probed_hours,
                refresh_m3us_before_probe=settings.refresh_m3us_before_probe,
                auto_reorder_after_probe=settings.auto_reorder_after_probe,
                deprioritize_failed_streams=settings.deprioritize_failed_streams,
                stream_sort_priority=settings.stream_sort_priority,
                stream_sort_enabled=settings.stream_sort_enabled,
                stream_fetch_page_limit=settings.stream_fetch_page_limit,
                m3u_account_priorities=settings.m3u_account_priorities,
            )
            prober.set_notification_callbacks(
                create_callback=create_notification_internal,
                update_callback=update_notification_internal,
                delete_by_source_callback=delete_notifications_by_source_internal
            )
            logger.info("Notification callbacks configured for stream prober")
            logger.info(f"StreamProber instance created: {prober is not None}")

            set_prober(prober)
            logger.info("set_prober() called successfully")

            await prober.start()
            logger.info("prober.start() completed")

            # Verify prober is accessible via get_prober()
            test_prober = get_prober()
            logger.info(f"Verification: get_prober() returns: {test_prober is not None}")

            logger.info("Stream prober initialized (scheduled probing via Task Engine)")
        except Exception as e:
            logger.error(f"Failed to initialize stream prober: {e}", exc_info=True)
            logger.error("Stream probing will not be available!")

    # Start the task execution engine
    try:
        # Import tasks module to trigger @register_task decorators
        import tasks  # noqa: F401 - imported for side effects
        logger.info("Task modules loaded and registered")

        # Start the task engine
        from task_engine import start_engine, get_engine
        await start_engine()
        logger.info("Task execution engine started")

        # Set notification callbacks on the task engine for progress updates
        engine = get_engine()
        engine.set_notification_callbacks(
            create_callback=create_notification_internal,
            update_callback=update_notification_internal,
            delete_callback=delete_notifications_by_source_internal,
        )
        logger.info("Task engine notification callbacks configured")

        # Connect the prober to the StreamProbeTask AFTER tasks are registered
        prober = get_prober()
        if prober:
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
    except Exception as e:
        logger.error(f"Failed to start task engine: {e}", exc_info=True)
        logger.error("Scheduled tasks will not be available!")

    # Start TLS certificate renewal manager
    try:
        from tls.settings import get_tls_settings
        from tls.renewal import renewal_manager
        tls_settings = get_tls_settings()
        if tls_settings.enabled and tls_settings.mode == "letsencrypt" and tls_settings.auto_renew:
            renewal_manager.start(check_interval=86400)  # Check every 24 hours
            logger.info("TLS certificate renewal manager started")
        else:
            logger.info("TLS auto-renewal not enabled, skipping renewal manager")
    except Exception as e:
        logger.warning(f"Failed to start TLS renewal manager: {e}")

    # Start HTTPS server if TLS is configured
    try:
        from tls.https_server import start_https_if_configured
        await start_https_if_configured()
    except Exception as e:
        logger.warning(f"Failed to start HTTPS server: {e}")

    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on shutdown."""
    logger.info("Enhanced Channel Manager shutting down")

    # Stop HTTPS server
    try:
        from tls.https_server import stop_https_server
        await stop_https_server()
        logger.info("HTTPS server stopped")
    except Exception as e:
        logger.error(f"Error stopping HTTPS server: {e}")

    # Stop TLS renewal manager
    try:
        from tls.renewal import renewal_manager
        renewal_manager.stop()
        logger.info("TLS renewal manager stopped")
    except Exception as e:
        logger.error(f"Error stopping TLS renewal manager: {e}")

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
    tvcGuideStationId: Optional[str] = None  # Gracenote ID from M3U tvc-guide-stationid
    normalize: Optional[bool] = False  # Apply normalization rules to channel name


class BulkDeleteChannelOp(BaseModel):
    type: Literal["deleteChannel"] = "deleteChannel"
    channelId: int


class BulkCreateGroupOp(BaseModel):
    type: Literal["createGroup"] = "createGroup"
    name: str


class BulkDeleteGroupOp(BaseModel):
    type: Literal["deleteChannelGroup"] = "deleteChannelGroup"
    groupId: int


class BulkRenameGroupOp(BaseModel):
    type: Literal["renameChannelGroup"] = "renameChannelGroup"
    groupId: int
    newName: str


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
    BulkRenameGroupOp,
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
@app.get("/api/health", tags=["Health"])
async def health_check():
    # Get version info from environment (set at build time)
    version = os.environ.get("ECM_VERSION", "unknown")
    release_channel = os.environ.get("RELEASE_CHANNEL", "latest")
    git_commit = os.environ.get("GIT_COMMIT", "unknown")

    return {
        "status": "healthy",
        "service": "enhanced-channel-manager",
        "version": version,
        "release_channel": release_channel,
        "git_commit": git_commit,
    }


# Settings
class NormalizationTag(BaseModel):
    """A normalization tag with its matching mode."""
    value: str
    mode: str = "both"  # "prefix", "suffix", or "both"


class NormalizationSettings(BaseModel):
    """User-configurable normalization settings."""
    # Built-in tags that user has disabled (format: "group:value", e.g., "country:US")
    disabledBuiltinTags: list[str] = []
    # User-added custom tags
    customTags: list[NormalizationTag] = []


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
    # Stream probe settings (scheduled probing is controlled by Task Engine)
    stream_probe_batch_size: int = 10
    stream_probe_timeout: int = 30
    stream_probe_schedule_time: str = "03:00"  # HH:MM format, 24h
    bitrate_sample_duration: int = 10  # Duration in seconds to sample stream for bitrate (10, 20, or 30)
    parallel_probing_enabled: bool = True  # Probe multiple streams from different M3Us simultaneously
    max_concurrent_probes: int = 8  # Max simultaneous probes when parallel probing is enabled (1-16)
    skip_recently_probed_hours: int = 0  # Skip streams successfully probed within last N hours (0 = always probe)
    refresh_m3us_before_probe: bool = True  # Refresh all M3U accounts before starting probe
    auto_reorder_after_probe: bool = False  # Automatically reorder streams in channels after probe completes
    stream_fetch_page_limit: int = 200  # Max pages when fetching streams (200 pages * 500 = 100K streams)
    stream_sort_priority: list[str] = ["resolution", "bitrate", "framerate", "m3u_priority", "audio_channels"]  # Priority order for Smart Sort
    stream_sort_enabled: dict[str, bool] = {"resolution": True, "bitrate": True, "framerate": True, "m3u_priority": False, "audio_channels": False}  # Which criteria are enabled
    m3u_account_priorities: dict[str, int] = {}  # M3U account priorities (account_id -> priority value)
    deprioritize_failed_streams: bool = True  # When enabled, failed/timeout/pending streams sort to bottom
    normalization_settings: Optional[NormalizationSettings] = None  # User-configurable normalization tags
    normalize_on_channel_create: bool = False  # Default state for normalization toggle when creating channels
    # Shared SMTP settings
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: Optional[str] = None  # Optional - only required if changing SMTP auth
    smtp_from_email: str = ""
    smtp_from_name: str = "ECM Alerts"
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    # Shared Discord settings
    discord_webhook_url: str = ""
    # Shared Telegram settings
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Stream preview mode: "passthrough", "transcode", or "video_only"
    stream_preview_mode: str = "passthrough"


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
    # Stream probe settings (scheduled probing is controlled by Task Engine)
    stream_probe_batch_size: int
    stream_probe_timeout: int
    stream_probe_schedule_time: str  # HH:MM format, 24h
    bitrate_sample_duration: int
    parallel_probing_enabled: bool  # Probe multiple streams from different M3Us simultaneously
    max_concurrent_probes: int  # Max simultaneous probes when parallel probing is enabled (1-16)
    skip_recently_probed_hours: int  # Skip streams successfully probed within last N hours (0 = always probe)
    refresh_m3us_before_probe: bool  # Refresh all M3U accounts before starting probe
    auto_reorder_after_probe: bool  # Automatically reorder streams in channels after probe completes
    stream_fetch_page_limit: int  # Max pages when fetching streams (200 pages * 500 = 100K streams)
    stream_sort_priority: list[str]  # Priority order for Smart Sort
    stream_sort_enabled: dict[str, bool]  # Which criteria are enabled
    m3u_account_priorities: dict[str, int]  # M3U account priorities (account_id -> priority value)
    deprioritize_failed_streams: bool  # When enabled, failed/timeout/pending streams sort to bottom
    normalization_settings: NormalizationSettings  # User-configurable normalization tags
    normalize_on_channel_create: bool  # Default state for normalization toggle when creating channels
    # Shared SMTP settings
    smtp_configured: bool  # Whether shared SMTP is configured
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_from_email: str
    smtp_from_name: str
    smtp_use_tls: bool
    smtp_use_ssl: bool
    # Shared Discord settings
    discord_configured: bool  # Whether shared Discord webhook is configured
    discord_webhook_url: str
    # Shared Telegram settings
    telegram_configured: bool  # Whether shared Telegram bot is configured
    telegram_bot_token: str
    telegram_chat_id: str
    # Stream preview mode
    stream_preview_mode: str


class TestConnectionRequest(BaseModel):
    url: str
    username: str
    password: str


def _has_discord_alert_method() -> bool:
    """Check if any enabled Discord alert method exists."""
    try:
        from models import AlertMethod
        session = get_session()
        try:
            return session.query(AlertMethod).filter(
                AlertMethod.method_type == "discord",
                AlertMethod.enabled == True,
            ).first() is not None
        finally:
            session.close()
    except Exception:
        return False


@app.get("/api/settings", tags=["Settings"])
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
        stream_probe_batch_size=settings.stream_probe_batch_size,
        stream_probe_timeout=settings.stream_probe_timeout,
        stream_probe_schedule_time=settings.stream_probe_schedule_time,
        bitrate_sample_duration=settings.bitrate_sample_duration,
        parallel_probing_enabled=settings.parallel_probing_enabled,
        max_concurrent_probes=settings.max_concurrent_probes,
        skip_recently_probed_hours=settings.skip_recently_probed_hours,
        refresh_m3us_before_probe=settings.refresh_m3us_before_probe,
        auto_reorder_after_probe=settings.auto_reorder_after_probe,
        stream_fetch_page_limit=settings.stream_fetch_page_limit,
        stream_sort_priority=settings.stream_sort_priority,
        stream_sort_enabled=settings.stream_sort_enabled,
        m3u_account_priorities=settings.m3u_account_priorities,
        deprioritize_failed_streams=settings.deprioritize_failed_streams,
        normalization_settings=NormalizationSettings(
            disabledBuiltinTags=settings.disabled_builtin_tags,
            customTags=[
                NormalizationTag(value=tag["value"], mode=tag.get("mode", "both"))
                for tag in settings.custom_normalization_tags
            ]
        ),
        normalize_on_channel_create=settings.normalize_on_channel_create,
        # Shared SMTP settings (password not returned for security)
        smtp_configured=settings.is_smtp_configured(),
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_user=settings.smtp_user,
        smtp_from_email=settings.smtp_from_email,
        smtp_from_name=settings.smtp_from_name,
        smtp_use_tls=settings.smtp_use_tls,
        smtp_use_ssl=settings.smtp_use_ssl,
        # Shared Discord settings (also check alert methods for Discord webhook)
        discord_configured=settings.is_discord_configured() or _has_discord_alert_method(),
        discord_webhook_url=settings.discord_webhook_url,
        # Shared Telegram settings
        telegram_configured=settings.is_telegram_configured(),
        telegram_bot_token=settings.telegram_bot_token,
        telegram_chat_id=settings.telegram_chat_id,
        stream_preview_mode=settings.stream_preview_mode,
    )


@app.post("/api/settings", tags=["Settings"])
async def update_settings(request: SettingsRequest):
    """Update Dispatcharr connection settings."""
    logger.debug(f"POST /api/settings - Updating settings (URL: {request.url}, username: {request.username})")
    current_settings = get_settings()

    # If password is not provided, keep the existing password
    # This allows updating non-auth settings without re-entering password
    password = request.password if request.password else current_settings.password

    # Same for SMTP password - preserve existing if not provided
    smtp_password = request.smtp_password if request.smtp_password else current_settings.smtp_password

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
        stream_probe_batch_size=request.stream_probe_batch_size,
        stream_probe_timeout=request.stream_probe_timeout,
        stream_probe_schedule_time=request.stream_probe_schedule_time,
        bitrate_sample_duration=request.bitrate_sample_duration,
        parallel_probing_enabled=request.parallel_probing_enabled,
        max_concurrent_probes=request.max_concurrent_probes,
        skip_recently_probed_hours=request.skip_recently_probed_hours,
        refresh_m3us_before_probe=request.refresh_m3us_before_probe,
        auto_reorder_after_probe=request.auto_reorder_after_probe,
        stream_fetch_page_limit=request.stream_fetch_page_limit,
        stream_sort_priority=request.stream_sort_priority,
        stream_sort_enabled=request.stream_sort_enabled,
        m3u_account_priorities=request.m3u_account_priorities,
        deprioritize_failed_streams=request.deprioritize_failed_streams,
        # Convert normalization_settings from API format to backend format
        disabled_builtin_tags=(
            request.normalization_settings.disabledBuiltinTags
            if request.normalization_settings else current_settings.disabled_builtin_tags
        ),
        custom_normalization_tags=(
            [{"value": tag.value, "mode": tag.mode} for tag in request.normalization_settings.customTags]
            if request.normalization_settings else current_settings.custom_normalization_tags
        ),
        normalize_on_channel_create=request.normalize_on_channel_create,
        # Shared SMTP settings
        smtp_host=request.smtp_host,
        smtp_port=request.smtp_port,
        smtp_user=request.smtp_user,
        smtp_password=smtp_password,
        smtp_from_email=request.smtp_from_email,
        smtp_from_name=request.smtp_from_name,
        smtp_use_tls=request.smtp_use_tls,
        smtp_use_ssl=request.smtp_use_ssl,
        # Shared Discord settings
        discord_webhook_url=request.discord_webhook_url,
        # Shared Telegram settings
        telegram_bot_token=request.telegram_bot_token,
        telegram_chat_id=request.telegram_chat_id,
        stream_preview_mode=request.stream_preview_mode,
    )
    save_settings(new_settings)
    clear_settings_cache()
    reset_client()

    # If the Dispatcharr URL changed, invalidate all cached data from the old server
    server_changed = request.url != current_settings.url
    if server_changed:
        cache = get_cache()
        cache.clear()
        logger.info(f"Dispatcharr URL changed - cleared all cache entries")

        # Also clear all data tied to the old server
        from models import (
            M3UChangeLog, M3USnapshot, ChannelWatchStats, HiddenChannelGroup,
            ChannelBandwidth, ChannelPopularityScore, UniqueClientConnection
        )
        with get_session() as db:
            changes_deleted = db.query(M3UChangeLog).delete()
            snapshots_deleted = db.query(M3USnapshot).delete()
            watch_stats_deleted = db.query(ChannelWatchStats).delete()
            hidden_groups_deleted = db.query(HiddenChannelGroup).delete()
            bandwidth_deleted = db.query(ChannelBandwidth).delete()
            popularity_deleted = db.query(ChannelPopularityScore).delete()
            connections_deleted = db.query(UniqueClientConnection).delete()
            db.commit()
            logger.info(
                f"Dispatcharr URL changed - cleared all server-specific data: "
                f"{changes_deleted} M3U changes, {snapshots_deleted} snapshots, "
                f"{watch_stats_deleted} watch stats, {hidden_groups_deleted} hidden groups, "
                f"{bandwidth_deleted} bandwidth records, {popularity_deleted} popularity scores, "
                f"{connections_deleted} client connections"
            )

    # Apply backend log level immediately
    if new_settings.backend_log_level != current_settings.backend_log_level:
        logger.info(f"Applying new backend log level: {new_settings.backend_log_level}")
        set_log_level(new_settings.backend_log_level)

    # Update prober's parallel probing settings without requiring restart
    if (new_settings.parallel_probing_enabled != current_settings.parallel_probing_enabled or
            new_settings.max_concurrent_probes != current_settings.max_concurrent_probes):
        prober = get_prober()
        if prober:
            prober.update_probing_settings(
                new_settings.parallel_probing_enabled,
                new_settings.max_concurrent_probes
            )
            logger.info("Updated prober parallel probing settings from settings")

    # Update prober's sort settings without requiring restart
    if (new_settings.stream_sort_priority != current_settings.stream_sort_priority or
            new_settings.stream_sort_enabled != current_settings.stream_sort_enabled or
            new_settings.m3u_account_priorities != current_settings.m3u_account_priorities):
        prober = get_prober()
        if prober:
            prober.update_sort_settings(
                new_settings.stream_sort_priority,
                new_settings.stream_sort_enabled,
                new_settings.m3u_account_priorities
            )
            logger.info("Updated prober sort settings from settings")

    logger.info(f"Settings saved successfully - configured: {new_settings.is_configured()}, auth_changed: {auth_changed}, server_changed: {server_changed}")
    return {"status": "saved", "configured": new_settings.is_configured(), "server_changed": server_changed}


@app.post("/api/settings/test", tags=["Settings"])
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


class SMTPTestRequest(BaseModel):
    """Request model for testing SMTP settings."""
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str
    smtp_from_name: str = "ECM Alerts"
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    to_email: str  # Test recipient email


@app.post("/api/settings/test-smtp", tags=["Settings"])
async def test_smtp_connection(request: SMTPTestRequest):
    """Test SMTP connection by sending a test email."""
    import smtplib
    import ssl
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    logger.debug(f"POST /api/settings/test-smtp - Testing SMTP to {request.smtp_host}:{request.smtp_port}")

    if not request.smtp_host:
        return {"success": False, "message": "SMTP host is required"}
    if not request.smtp_from_email:
        return {"success": False, "message": "From email is required"}
    if not request.to_email:
        return {"success": False, "message": "Test recipient email is required"}

    try:
        # Build test email
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "ECM SMTP Test - Connection Successful"
        msg["From"] = f"{request.smtp_from_name} <{request.smtp_from_email}>"
        msg["To"] = request.to_email

        plain_text = """This is a test email from Enhanced Channel Manager.

If you're reading this, your SMTP settings are configured correctly!

You can now use email features like M3U Digest reports.

- Enhanced Channel Manager"""

        html_text = """
        <html>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px;">
            <div style="max-width: 500px; margin: 0 auto; background: #f8f9fa; border-radius: 8px; padding: 20px;">
                <h2 style="color: #22C55E; margin-top: 0;">✅ SMTP Test Successful</h2>
                <p>This is a test email from Enhanced Channel Manager.</p>
                <p>If you're reading this, your SMTP settings are configured correctly!</p>
                <p>You can now use email features like M3U Digest reports.</p>
                <hr style="border: none; border-top: 1px solid #e9ecef; margin: 20px 0;">
                <p style="color: #666; font-size: 12px;">- Enhanced Channel Manager</p>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(plain_text, "plain"))
        msg.attach(MIMEText(html_text, "html"))

        # Connect and send
        if request.smtp_use_ssl:
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(request.smtp_host, request.smtp_port, context=context, timeout=10)
        else:
            server = smtplib.SMTP(request.smtp_host, request.smtp_port, timeout=10)

        try:
            if request.smtp_use_tls and not request.smtp_use_ssl:
                server.starttls(context=ssl.create_default_context())

            if request.smtp_user and request.smtp_password:
                server.login(request.smtp_user, request.smtp_password)

            server.sendmail(request.smtp_from_email, [request.to_email], msg.as_string())
            logger.info(f"SMTP test email sent successfully to {request.to_email}")
            return {"success": True, "message": f"Test email sent to {request.to_email}"}

        finally:
            server.quit()

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP test failed - authentication error: {e}")
        return {"success": False, "message": "Authentication failed - check username and password"}
    except smtplib.SMTPConnectError as e:
        logger.error(f"SMTP test failed - connection error: {e}")
        return {"success": False, "message": f"Could not connect to {request.smtp_host}:{request.smtp_port}"}
    except smtplib.SMTPRecipientsRefused as e:
        logger.error(f"SMTP test failed - recipient refused: {e}")
        return {"success": False, "message": "Recipient email was refused by the server"}
    except TimeoutError:
        logger.error(f"SMTP test failed - timeout connecting to {request.smtp_host}")
        return {"success": False, "message": f"Connection timed out to {request.smtp_host}:{request.smtp_port}"}
    except Exception as e:
        logger.exception(f"SMTP test failed - unexpected error: {e}")
        return {"success": False, "message": str(e)}


class DiscordTestRequest(BaseModel):
    webhook_url: str


@app.post("/api/settings/test-discord", tags=["Settings"])
async def test_discord_webhook(request: DiscordTestRequest):
    """Test Discord webhook by sending a test message."""
    import aiohttp

    webhook_url = request.webhook_url
    logger.info(f"POST /api/settings/test-discord - Testing Discord webhook: {webhook_url[:50]}...")

    if not webhook_url:
        return {"success": False, "message": "Webhook URL is required"}

    # Validate URL format - accept discord.com, discordapp.com, and variants (canary, ptb)
    import re
    discord_pattern = r'^https://(discord\.com|discordapp\.com|canary\.discord\.com|ptb\.discord\.com)/api/webhooks/'
    if not re.match(discord_pattern, webhook_url):
        return {"success": False, "message": "Invalid Discord webhook URL format"}

    try:
        payload = {
            "content": (
                "**\u2713 ECM Discord Test**\n\n"
                "Your Discord webhook is configured correctly.\n"
                "You will receive notifications from Enhanced Channel Manager here."
            ),
            "username": "ECM Test",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 204:
                    logger.info("Discord webhook test successful")
                    return {"success": True, "message": "Test message sent successfully"}
                elif response.status == 401:
                    return {"success": False, "message": "Invalid webhook - unauthorized"}
                elif response.status == 404:
                    return {"success": False, "message": "Webhook not found - may have been deleted"}
                elif response.status == 429:
                    return {"success": False, "message": "Rate limited - try again later"}
                else:
                    text = await response.text()
                    logger.error(f"Discord test failed: {response.status} - {text}")
                    return {"success": False, "message": f"Discord returned error: {response.status}"}

    except aiohttp.ClientError as e:
        logger.error(f"Discord test failed - connection error: {e}")
        return {"success": False, "message": f"Connection error: {str(e)}"}
    except Exception as e:
        logger.exception(f"Discord test failed - unexpected error: {e}")
        return {"success": False, "message": str(e)}


class TelegramTestRequest(BaseModel):
    bot_token: str
    chat_id: str


@app.post("/api/settings/test-telegram", tags=["Settings"])
async def test_telegram_bot(request: TelegramTestRequest):
    """Test Telegram bot by sending a test message."""
    import aiohttp

    bot_token = request.bot_token
    chat_id = request.chat_id
    logger.debug("POST /api/settings/test-telegram - Testing Telegram bot")

    if not bot_token:
        return {"success": False, "message": "Bot token is required"}
    if not chat_id:
        return {"success": False, "message": "Chat ID is required"}

    try:
        # Telegram Bot API endpoint
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": (
                "✓ *ECM Telegram Test*\n\n"
                "Your Telegram bot is configured correctly\\.\n"
                "You will receive notifications from Enhanced Channel Manager here\\."
            ),
            "parse_mode": "MarkdownV2",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                data = await response.json()

                if response.status == 200 and data.get("ok"):
                    logger.info("Telegram bot test successful")
                    return {"success": True, "message": "Test message sent successfully"}
                elif response.status == 401:
                    return {"success": False, "message": "Invalid bot token - unauthorized"}
                elif response.status == 400:
                    error_desc = data.get("description", "Unknown error")
                    if "chat not found" in error_desc.lower():
                        return {"success": False, "message": "Chat not found - check your chat ID"}
                    return {"success": False, "message": f"Bad request: {error_desc}"}
                elif response.status == 429:
                    return {"success": False, "message": "Rate limited - try again later"}
                else:
                    error_desc = data.get("description", f"Status {response.status}")
                    logger.error(f"Telegram test failed: {error_desc}")
                    return {"success": False, "message": f"Telegram returned error: {error_desc}"}

    except aiohttp.ClientError as e:
        logger.error(f"Telegram test failed - connection error: {e}")
        return {"success": False, "message": f"Connection error: {str(e)}"}
    except Exception as e:
        logger.exception(f"Telegram test failed - unexpected error: {e}")
        return {"success": False, "message": str(e)}


@app.post("/api/settings/restart-services", tags=["Settings"])
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

            # Restart stream prober (scheduled probing is controlled by Task Engine)
            new_prober = StreamProber(
                get_client(),
                probe_timeout=settings.stream_probe_timeout,
                probe_batch_size=settings.stream_probe_batch_size,
                user_timezone=settings.user_timezone,
                bitrate_sample_duration=settings.bitrate_sample_duration,
                parallel_probing_enabled=settings.parallel_probing_enabled,
                max_concurrent_probes=settings.max_concurrent_probes,
                skip_recently_probed_hours=settings.skip_recently_probed_hours,
                refresh_m3us_before_probe=settings.refresh_m3us_before_probe,
                auto_reorder_after_probe=settings.auto_reorder_after_probe,
                deprioritize_failed_streams=settings.deprioritize_failed_streams,
                stream_sort_priority=settings.stream_sort_priority,
                stream_sort_enabled=settings.stream_sort_enabled,
                stream_fetch_page_limit=settings.stream_fetch_page_limit,
                m3u_account_priorities=settings.m3u_account_priorities,
            )
            new_prober.set_notification_callbacks(
                create_callback=create_notification_internal,
                update_callback=update_notification_internal,
                delete_by_source_callback=delete_notifications_by_source_internal
            )
            logger.info("Notification callbacks configured for stream prober")
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
            logger.info("Restarted stream prober with updated settings")

            return {"success": True, "message": "Services restarted with new settings"}
        except Exception as e:
            logger.error(f"Failed to restart services: {e}")
            return {"success": False, "message": str(e)}
    else:
        return {"success": False, "message": "Settings not configured"}


@app.post("/api/settings/reset-stats", tags=["Settings"])
async def reset_stats():
    """Reset all channel/stream statistics. Use when switching Dispatcharr servers."""
    from models import HiddenChannelGroup, ChannelWatchStats, ChannelBandwidth, StreamStats, ChannelPopularityScore

    try:
        with get_session() as db:
            hidden = db.query(HiddenChannelGroup).delete()
            watch = db.query(ChannelWatchStats).delete()
            bandwidth = db.query(ChannelBandwidth).delete()
            streams = db.query(StreamStats).delete()
            popularity = db.query(ChannelPopularityScore).delete()
            db.commit()

            total = hidden + watch + bandwidth + streams + popularity
            logger.info(f"Reset stats: {hidden} hidden groups, {watch} watch stats, {bandwidth} bandwidth, {streams} stream stats, {popularity} popularity")

            return {
                "success": True,
                "message": f"Cleared {total} records",
                "details": {
                    "hidden_groups": hidden,
                    "watch_stats": watch,
                    "bandwidth_records": bandwidth,
                    "stream_stats": streams,
                    "popularity_scores": popularity
                }
            }
    except Exception as e:
        logger.error(f"Failed to reset stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Channels
class CreateChannelRequest(BaseModel):
    name: str
    channel_number: Optional[float] = None
    channel_group_id: Optional[int] = None
    logo_id: Optional[int] = None
    tvg_id: Optional[str] = None
    normalize: Optional[bool] = False  # Apply normalization rules to channel name


@app.get("/api/channels")
async def get_channels(
    page: int = 1,
    page_size: int = 100,
    search: Optional[str] = None,
    channel_group: Optional[int] = None,
):
    start_time = time.time()
    logger.debug(
        f"[CHANNELS] Fetching channels - page={page}, page_size={page_size}, "
        f"search={search}, group={channel_group}"
    )
    client = get_client()
    try:
        fetch_start = time.time()
        result = await client.get_channels(
            page=page,
            page_size=page_size,
            search=search,
            channel_group=channel_group,
        )
        fetch_time = (time.time() - fetch_start) * 1000
        total_time = (time.time() - start_time) * 1000
        result_count = len(result.get('results', []))
        total_count = result.get('count', 0)

        # Debug logging: count channels per group_id on first page of unfiltered requests
        if page == 1 and not search and not channel_group:
            channels = result.get('results', [])
            group_counts: dict = {}
            for ch in channels:
                group_id = ch.get('channel_group_id')
                group_name = ch.get('channel_group_name', 'Unknown')
                key = f"{group_id}:{group_name}"
                if key not in group_counts:
                    group_counts[key] = {'count': 0, 'sample_channels': []}
                group_counts[key]['count'] += 1
                # Keep first 3 sample channel names per group for debugging
                if len(group_counts[key]['sample_channels']) < 3:
                    group_counts[key]['sample_channels'].append(
                        f"#{ch.get('channel_number')} {ch.get('name', 'unnamed')}"
                    )

            logger.info(f"[CHANNELS-DEBUG] Page 1 stats: {result_count} channels returned, API total={total_count}")
            logger.info(f"[CHANNELS-DEBUG] Channels per group_id (page 1 only):")
            for key, data in sorted(group_counts.items(), key=lambda x: -x[1]['count']):
                logger.info(f"  {key}: {data['count']} channels (samples: {data['sample_channels']})")

        logger.debug(
            f"[CHANNELS] Fetched {result_count} channels (total={total_count}, page={page}) "
            f"- fetch={fetch_time:.1f}ms, total={total_time:.1f}ms"
        )
        return result
    except Exception as e:
        logger.exception(f"[CHANNELS] Failed to retrieve channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels")
async def create_channel(request: CreateChannelRequest):
    logger.debug(f"POST /api/channels - Creating channel: {request.name}, number: {request.channel_number}, normalize: {request.normalize}")
    client = get_client()
    try:
        # Apply normalization if requested
        channel_name = request.name
        if request.normalize:
            try:
                from normalization_engine import get_normalization_engine
                with get_session() as db:
                    engine = get_normalization_engine(db)
                    norm_result = engine.normalize(request.name)
                    channel_name = norm_result.normalized
                    if channel_name != request.name:
                        logger.debug(f"Normalized channel name: '{request.name}' -> '{channel_name}'")
            except Exception as norm_err:
                logger.warning(f"Failed to normalize channel name '{request.name}': {norm_err}")
                # Continue with original name

        data = {"name": channel_name}
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


@app.get("/api/channels/logos", tags=["Channels"])
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


@app.get("/api/channels/logos/{logo_id}", tags=["Channels"])
async def get_logo(logo_id: int):
    client = get_client()
    try:
        return await client.get_logo(logo_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/logos", tags=["Channels"])
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


@app.patch("/api/channels/logos/{logo_id}", tags=["Channels"])
async def update_logo(logo_id: int, data: dict):
    client = get_client()
    try:
        return await client.update_logo(logo_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channels/logos/{logo_id}", tags=["Channels"])
async def delete_logo(logo_id: int):
    client = get_client()
    try:
        await client.delete_logo(logo_id)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# CSV Import/Export - MUST be defined before /api/channels/{channel_id} routes
from csv_handler import parse_csv, generate_csv, generate_template, CSVParseError
from datetime import date


@app.get("/api/channels/csv-template", tags=["Channels"])
async def get_csv_template():
    """Download CSV template for channel import."""
    template_content = generate_template()
    return Response(
        content=template_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=channel-import-template.csv"
        }
    )


@app.get("/api/channels/export-csv", tags=["Channels"])
async def export_channels_csv():
    """Export all channels to CSV format."""
    client = get_client()
    try:
        # Fetch channel groups to build ID -> name lookup
        groups = await client.get_channel_groups()
        group_lookup = {g.get("id"): g.get("name", "") for g in groups}

        # Fetch all channels (handle pagination)
        all_channels = []
        page = 1
        page_size = 100
        while True:
            result = await client.get_channels(page=page, page_size=page_size)
            channels = result.get("results", [])
            all_channels.extend(channels)
            if not result.get("next"):
                break
            page += 1

        # Filter out auto-created channels and sort by channel number ascending
        manual_channels = [ch for ch in all_channels if not ch.get("auto_created", False)]
        manual_channels.sort(key=lambda ch: ch.get("channel_number", 0) or 0)

        # Collect all stream IDs from channels
        all_stream_ids = set()
        for ch in manual_channels:
            stream_ids = ch.get("streams", [])
            all_stream_ids.update(stream_ids)

        # Fetch stream details to get URLs (batch by 100)
        stream_url_lookup = {}
        stream_ids_list = list(all_stream_ids)
        for i in range(0, len(stream_ids_list), 100):
            batch = stream_ids_list[i:i+100]
            if batch:
                try:
                    streams = await client.get_streams_by_ids(batch)
                    for s in streams:
                        stream_url_lookup[s.get("id")] = s.get("url", "")
                except Exception as e:
                    logger.warning(f"Failed to fetch stream batch: {e}")

        # Transform channel data for CSV export
        csv_channels = []
        for ch in manual_channels:
            group_id = ch.get("channel_group_id")
            group_name = group_lookup.get(group_id, "") if group_id else ""

            # Get stream URLs for this channel
            stream_ids = ch.get("streams", [])
            stream_urls = [stream_url_lookup.get(sid, "") for sid in stream_ids if stream_url_lookup.get(sid)]
            stream_urls_str = ";".join(stream_urls) if stream_urls else ""

            csv_channels.append({
                "channel_number": ch.get("channel_number"),
                "name": ch.get("name", ""),
                "group_name": group_name,
                "tvg_id": ch.get("tvg_id", ""),
                "gracenote_id": ch.get("tvc_guide_stationid", ""),
                "logo_url": ch.get("logo_url", ""),
                "stream_urls": stream_urls_str
            })

        csv_content = generate_csv(csv_channels)
        filename = f"channels-export-{date.today().isoformat()}.csv"

        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
    except Exception as e:
        logger.error(f"CSV export failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/import-csv", tags=["Channels"])
async def import_channels_csv(file: UploadFile = File(...)):
    """Import channels from CSV file."""
    client = get_client()

    # Read and decode the file
    try:
        content = await file.read()
        csv_content = content.decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")

    # Parse CSV
    try:
        rows, parse_errors = parse_csv(csv_content)
    except CSVParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not rows and not parse_errors:
        # Empty file or header only
        return {
            "success": True,
            "channels_created": 0,
            "groups_created": 0,
            "errors": [],
            "warnings": []
        }

    # Get existing channel groups for matching
    try:
        existing_groups = await client.get_channel_groups()
        group_map = {g["name"].lower(): g for g in existing_groups}
    except Exception as e:
        logger.error(f"Failed to fetch channel groups: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch channel groups: {e}")

    # Build URL -> stream ID lookup for stream linking
    stream_url_to_id = {}
    try:
        page = 1
        page_size = 500
        while True:
            result = await client.get_streams(page=page, page_size=page_size)
            streams = result.get("results", [])
            for s in streams:
                url = s.get("url", "")
                if url:
                    stream_url_to_id[url] = s.get("id")
            if not result.get("next"):
                break
            page += 1
        logger.info(f"Built stream URL lookup with {len(stream_url_to_id)} streams")
    except Exception as e:
        logger.warning(f"Failed to fetch streams for URL lookup: {e}")

    # Build EPG tvg_id -> icon_url lookup for logo assignment
    epg_tvg_id_to_icon = {}
    epg_name_to_icon = {}
    try:
        epg_data = await client.get_epg_data()
        for entry in epg_data:
            tvg_id = entry.get("tvg_id", "")
            icon_url = entry.get("icon_url", "")
            name = entry.get("name", "")
            if tvg_id and icon_url:
                epg_tvg_id_to_icon[tvg_id.lower()] = icon_url
            if name and icon_url:
                # Normalize name for matching (lowercase, strip common suffixes)
                normalized_name = name.lower().strip()
                for suffix in [" hd", " sd", " (hd)", " (sd)"]:
                    if normalized_name.endswith(suffix):
                        normalized_name = normalized_name[:-len(suffix)]
                epg_name_to_icon[normalized_name] = icon_url
        logger.info(f"Built EPG logo lookup with {len(epg_tvg_id_to_icon)} tvg_id entries and {len(epg_name_to_icon)} name entries")
    except Exception as e:
        logger.warning(f"Failed to fetch EPG data for logo lookup: {e}")

    channels_created = 0
    groups_created = 0
    streams_linked = 0
    logos_from_epg = 0
    errors = parse_errors.copy()
    warnings = []

    # Process each valid row
    for i, row in enumerate(rows):
        row_num = i + 2  # Account for header row

        try:
            # Handle group creation/lookup
            group_id = None
            group_name = row.get("group_name", "").strip()
            if group_name:
                group_key = group_name.lower()
                if group_key in group_map:
                    group_id = group_map[group_key]["id"]
                else:
                    # Create new group
                    try:
                        new_group = await client.create_channel_group(group_name)
                        group_id = new_group["id"]
                        group_map[group_key] = new_group
                        groups_created += 1
                        logger.info(f"Created channel group: {group_name}")
                    except Exception as ge:
                        warnings.append(f"Row {row_num}: Failed to create group '{group_name}': {ge}")

            # Build channel data
            channel_data = {
                "name": row["name"],
            }

            # Add optional fields
            channel_number = row.get("channel_number", "").strip()
            if channel_number:
                try:
                    channel_data["channel_number"] = float(channel_number)
                except ValueError:
                    pass  # Skip invalid numbers

            if group_id:
                channel_data["channel_group_id"] = group_id

            tvg_id = row.get("tvg_id", "").strip()
            if tvg_id:
                channel_data["tvg_id"] = tvg_id

            gracenote_id = row.get("gracenote_id", "").strip()
            if gracenote_id:
                channel_data["tvc_guide_stationid"] = gracenote_id

            logo_url = row.get("logo_url", "").strip()
            if logo_url:
                channel_data["logo_url"] = logo_url

            # Create the channel
            created_channel = await client.create_channel(channel_data)
            channels_created += 1

            # If no logo_url provided, try to get one from EPG data
            if not logo_url and created_channel:
                epg_icon_url = None
                # First try tvg_id match
                if tvg_id:
                    epg_icon_url = epg_tvg_id_to_icon.get(tvg_id.lower())
                # Fall back to name match
                if not epg_icon_url:
                    channel_name = row["name"].lower().strip()
                    # Try exact match first
                    epg_icon_url = epg_name_to_icon.get(channel_name)
                    # Try without HD/SD suffix
                    if not epg_icon_url:
                        for suffix in [" hd", " sd", " (hd)", " (sd)"]:
                            if channel_name.endswith(suffix):
                                channel_name = channel_name[:-len(suffix)]
                                epg_icon_url = epg_name_to_icon.get(channel_name)
                                break

                if epg_icon_url:
                    try:
                        channel_id = created_channel.get("id")
                        channel_name_for_logo = row["name"]
                        # Find existing logo by URL or create new one
                        existing_logo = await client.find_logo_by_url(epg_icon_url)
                        if existing_logo:
                            logo_id = existing_logo["id"]
                            logger.debug(f"Row {row_num}: Found existing logo ID {logo_id} for EPG icon")
                        else:
                            new_logo = await client.create_logo({"name": channel_name_for_logo, "url": epg_icon_url})
                            logo_id = new_logo["id"]
                            logger.debug(f"Row {row_num}: Created new logo ID {logo_id} for EPG icon")
                        # Update channel with logo_id
                        await client.update_channel(channel_id, {"logo_id": logo_id})
                        logos_from_epg += 1
                        logger.debug(f"Row {row_num}: Assigned EPG logo to channel '{row['name']}'")
                    except Exception as le:
                        warnings.append(f"Row {row_num}: Failed to assign EPG logo: {le}")

            # Handle stream linking if stream_urls provided
            stream_urls_str = row.get("stream_urls", "").strip()
            if stream_urls_str and created_channel:
                stream_urls = [url.strip() for url in stream_urls_str.split(";") if url.strip()]
                stream_ids = []
                for url in stream_urls:
                    stream_id = stream_url_to_id.get(url)
                    if stream_id:
                        stream_ids.append(stream_id)
                    else:
                        warnings.append(f"Row {row_num}: Stream URL not found: {url[:50]}...")

                if stream_ids:
                    try:
                        channel_id = created_channel.get("id")
                        await client.update_channel(channel_id, {"streams": stream_ids})
                        streams_linked += len(stream_ids)
                    except Exception as se:
                        warnings.append(f"Row {row_num}: Failed to link streams: {se}")

        except Exception as e:
            errors.append({"row": row_num, "error": str(e)})

    # Log the import
    logger.info(f"CSV import completed: {channels_created} channels created, {groups_created} groups created, {streams_linked} streams linked, {logos_from_epg} logos from EPG, {len(errors)} errors")

    return {
        "success": len(errors) == 0,
        "channels_created": channels_created,
        "groups_created": groups_created,
        "streams_linked": streams_linked,
        "logos_from_epg": logos_from_epg,
        "errors": errors,
        "warnings": warnings
    }


@app.post("/api/channels/preview-csv", tags=["Channels"])
async def preview_csv(data: dict):
    """Preview CSV content and validate before import."""
    content = data.get("content", "")
    if not content:
        return {"rows": [], "errors": []}

    try:
        rows, errors = parse_csv(content)
        # Convert rows to list of dicts for JSON response
        return {
            "rows": rows,
            "errors": errors
        }
    except CSVParseError as e:
        return {
            "rows": [],
            "errors": [{"row": 1, "error": str(e)}]
        }


# Channel by ID routes - must come after /api/channels/logos and CSV routes
@app.get("/api/channels/{channel_id}", tags=["Channels"])
async def get_channel(channel_id: int):
    client = get_client()
    try:
        return await client.get_channel(channel_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channels/{channel_id}/streams", tags=["Channels"])
async def get_channel_streams(channel_id: int):
    client = get_client()
    try:
        return await client.get_channel_streams(channel_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/channels/{channel_id}", tags=["Channels"])
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


@app.delete("/api/channels/{channel_id}", tags=["Channels"])
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


@app.post("/api/channels/{channel_id}/add-stream", tags=["Channels"])
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


@app.post("/api/channels/{channel_id}/remove-stream", tags=["Channels"])
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


@app.post("/api/channels/{channel_id}/reorder-streams", tags=["Channels"])
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


@app.post("/api/channels/assign-numbers", tags=["Channels"])
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


@app.post("/api/channels/bulk-commit", tags=["Channels"])
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
            # Log a sample of referenced channel IDs (first 20)
            sample_ids = sorted(referenced_channel_ids)[:20]
            logger.debug(f"[BULK-VALIDATE] Referenced channel IDs (sample): {sample_ids}{'...' if len(referenced_channel_ids) > 20 else ''}")

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
                # Check which referenced channels don't exist
                missing_channels = referenced_channel_ids - set(existing_channels.keys())
                if missing_channels:
                    logger.warning(f"[BULK-VALIDATE] Missing channels detected: {sorted(missing_channels)} ({len(missing_channels)} total)")
                else:
                    logger.debug(f"[BULK-VALIDATE] All {len(referenced_channel_ids)} referenced channels exist")
            except Exception as e:
                logger.warning(f"[BULK-VALIDATE] Failed to fetch channels for validation: {e}")

        if referenced_stream_ids:
            try:
                logger.debug(f"[BULK-VALIDATE] Fetching {len(referenced_stream_ids)} referenced streams for validation...")
                # Fetch only the specific streams that are referenced (not all streams)
                streams = await client.get_streams_by_ids(list(referenced_stream_ids))
                for s in streams:
                    existing_streams[s["id"]] = s
                logger.debug(f"[BULK-VALIDATE] Loaded {len(existing_streams)} of {len(referenced_stream_ids)} referenced streams")
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
        if result['validationIssues']:
            logger.warning(f"[BULK-VALIDATE] === VALIDATION ISSUES DETAIL ===")
            for i, issue in enumerate(result['validationIssues'][:10]):  # Show first 10
                op_idx = issue.get('operationIndex', '?')
                ch_id = issue.get('channelId', '?')
                stream_id = issue.get('streamId', '?')
                # Get the actual operation for more context
                if op_idx != '?' and op_idx < len(request.operations):
                    op = request.operations[op_idx]
                    logger.warning(f"[BULK-VALIDATE]   Issue {i+1}: {issue['type']} - {issue['message']}")
                    logger.warning(f"[BULK-VALIDATE]     Operation[{op_idx}]: type={op.type}, channelId={op.channelId}, streamId={getattr(op, 'streamId', None)}")
                    if op.type == "updateChannel" and op.data:
                        logger.warning(f"[BULK-VALIDATE]     Update data: name={op.data.get('name')}, number={op.data.get('channel_number')}")
                else:
                    logger.warning(f"[BULK-VALIDATE]   Issue {i+1}: {issue['type']} - {issue['message']} (channelId={ch_id}, streamId={stream_id})")
            if len(result['validationIssues']) > 10:
                logger.warning(f"[BULK-VALIDATE]   ... and {len(result['validationIssues']) - 10} more issues")
            logger.warning(f"[BULK-VALIDATE] === END VALIDATION ISSUES ===")

        # If validateOnly, return now without executing
        if request.validateOnly:
            logger.info(f"[BULK-COMMIT] Validation only mode: {len(result['validationIssues'])} issues found, returning without executing")
            result["success"] = result["validationPassed"]
            return result

        # If validation failed and continueOnError is false, return without executing
        if not result["validationPassed"] and not request.continueOnError:
            logger.warning(f"[BULK-COMMIT] Validation failed with {len(result['validationIssues'])} issues, aborting (continueOnError=false)")
            logger.warning(f"[BULK-COMMIT] No operations will be executed. Total operations that would have been attempted: {len(request.operations)}")
            # Log a hint about the issue
            if result['validationIssues']:
                first_issue = result['validationIssues'][0]
                logger.warning(f"[BULK-COMMIT] First issue: {first_issue.get('message', 'Unknown')}")
                if first_issue.get('type') == 'missing_channel':
                    logger.warning(f"[BULK-COMMIT] Hint: Channel {first_issue.get('channelId')} may have been deleted from Dispatcharr. Try refreshing the page to sync.")
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
                    logger.debug(f"[BULK-APPLY] [{idx+1}/{len(request.operations)}] createChannel: name='{op.name}', tempId={op.tempId}, groupId={op.groupId}, newGroupName={op.newGroupName}, normalize={op.normalize}")
                    # Resolve group ID
                    group_id = resolve_group_id(op.groupId, op.newGroupName)

                    # Apply normalization if requested
                    channel_name = op.name
                    if op.normalize:
                        try:
                            from normalization_engine import get_normalization_engine
                            with get_session() as db:
                                engine = get_normalization_engine(db)
                                norm_result = engine.normalize(op.name)
                                channel_name = norm_result.normalized
                                if channel_name != op.name:
                                    logger.debug(f"[BULK-APPLY] Normalized channel name: '{op.name}' -> '{channel_name}'")
                        except Exception as norm_err:
                            logger.warning(f"[BULK-APPLY] Failed to normalize channel name '{op.name}': {norm_err}")
                            # Continue with original name

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
                                new_logo = await client.create_logo({"name": channel_name, "url": op.logoUrl})
                                logo_id = new_logo["id"]
                                logger.debug(f"[BULK-APPLY] Created new logo ID {logo_id}")
                        except Exception as logo_err:
                            logger.warning(f"[BULK-APPLY] Failed to create/find logo for channel '{channel_name}': {logo_err}")
                            # Continue without logo

                    # Create the channel
                    channel_data = {"name": channel_name}
                    if op.channelNumber is not None:
                        channel_data["channel_number"] = op.channelNumber
                    if group_id is not None:
                        channel_data["channel_group_id"] = group_id
                    if logo_id is not None:
                        channel_data["logo_id"] = logo_id
                    if op.tvgId is not None:
                        channel_data["tvg_id"] = op.tvgId
                    if op.tvcGuideStationId is not None:
                        channel_data["tvc_guide_stationid"] = op.tvcGuideStationId

                    logger.debug(f"[BULK-APPLY] op.tvgId={op.tvgId}, op.tvcGuideStationId={op.tvcGuideStationId}")
                    logger.debug(f"[BULK-APPLY] Creating channel with data: {channel_data}")
                    new_channel = await client.create_channel(channel_data)

                    # Track temp ID -> real ID mapping
                    if op.tempId < 0:
                        result["tempIdMap"][op.tempId] = new_channel["id"]

                    result["operationsApplied"] += 1
                    logger.debug(f"[BULK-APPLY] Created channel '{channel_name}' (temp: {op.tempId} -> real: {new_channel['id']})")

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

                elif op.type == "renameChannelGroup":
                    logger.debug(f"[BULK-APPLY] [{idx+1}/{len(request.operations)}] renameChannelGroup: groupId={op.groupId}, newName='{op.newName}'")
                    await client.update_channel_group(op.groupId, {"name": op.newName})
                    result["operationsApplied"] += 1
                    logger.debug(f"[BULK-APPLY] Renamed group {op.groupId} to '{op.newName}'")

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
@app.get("/api/channel-groups", tags=["Channel Groups"])
async def get_channel_groups():
    client = get_client()
    try:
        groups = await client.get_channel_groups()

        # Filter out hidden groups
        from models import HiddenChannelGroup

        with get_session() as db:
            hidden_ids = {h.group_id for h in db.query(HiddenChannelGroup).all()}

        # Get M3U group settings to identify auto-sync groups
        m3u_group_settings = await client.get_all_m3u_group_settings()
        auto_sync_group_ids = {
            gid for gid, settings in m3u_group_settings.items()
            if settings.get("auto_channel_sync")
        }

        # Return groups with is_auto_sync flag, filtered by hidden status
        result = []
        for g in groups:
            if g.get("id") not in hidden_ids:
                group_data = dict(g)
                group_data["is_auto_sync"] = g.get("id") in auto_sync_group_ids
                result.append(group_data)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channel-groups", tags=["Channel Groups"])
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


@app.patch("/api/channel-groups/{group_id}", tags=["Channel Groups"])
async def update_channel_group(group_id: int, data: dict):
    client = get_client()
    try:
        return await client.update_channel_group(group_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channel-groups/orphaned", tags=["Channel Groups"])
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


@app.delete("/api/channel-groups/{group_id}", tags=["Channel Groups"])
async def delete_channel_group(group_id: int):
    client = get_client()
    try:
        # Check if this group has M3U sync settings
        m3u_settings = await client.get_all_m3u_group_settings()
        has_m3u_sync = group_id in m3u_settings

        if has_m3u_sync:
            # Hide the group instead of deleting to preserve M3U sync
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


@app.post("/api/channel-groups/{group_id}/restore", tags=["Channel Groups"])
async def restore_channel_group(group_id: int):
    """Restore a hidden channel group back to the visible list."""
    try:
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


@app.get("/api/channel-groups/hidden", tags=["Channel Groups"])
async def get_hidden_channel_groups():
    """Get list of all hidden channel groups."""
    try:
        from models import HiddenChannelGroup

        with get_session() as db:
            hidden_groups = db.query(HiddenChannelGroup).all()
            return [g.to_dict() for g in hidden_groups]
    except Exception as e:
        logger.error(f"Failed to get hidden channel groups: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channel-groups/orphaned", tags=["Channel Groups"])
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


@app.get("/api/channel-groups/auto-created", tags=["Channel Groups"])
async def get_groups_with_auto_created_channels():
    """Find channel groups that contain auto_created channels.

    Returns groups with at least one channel that has auto_created=True.
    """
    client = get_client()
    try:
        # Get all channel groups
        all_groups = await client.get_channel_groups()
        group_map = {g["id"]: g for g in all_groups}

        # Fetch all channels (paginated) and find auto_created ones
        auto_created_by_group: dict[int, list[dict]] = {}
        page = 1
        total_auto_created = 0

        while True:
            result = await client.get_channels(page=page, page_size=500)
            page_channels = result.get("results", [])

            for channel in page_channels:
                if channel.get("auto_created"):
                    total_auto_created += 1
                    group_id = channel.get("channel_group_id")
                    if group_id is not None:
                        if group_id not in auto_created_by_group:
                            auto_created_by_group[group_id] = []
                        auto_created_by_group[group_id].append({
                            "id": channel.get("id"),
                            "name": channel.get("name"),
                            "channel_number": channel.get("channel_number"),
                            "auto_created_by": channel.get("auto_created_by"),
                            "auto_created_by_name": channel.get("auto_created_by_name"),
                        })

            if not result.get("next"):
                break
            page += 1
            if page > 50:  # Safety limit
                break

        # Build result with group info
        groups_with_auto_created = []
        for group_id, channels in auto_created_by_group.items():
            group_info = group_map.get(group_id, {})
            groups_with_auto_created.append({
                "id": group_id,
                "name": group_info.get("name", f"Unknown Group {group_id}"),
                "auto_created_count": len(channels),
                "sample_channels": channels[:5],  # First 5 as samples
            })

        # Sort by name
        groups_with_auto_created.sort(key=lambda g: g["name"].lower())

        logger.info(f"Found {len(groups_with_auto_created)} groups with {total_auto_created} total auto_created channels")
        return {
            "groups": groups_with_auto_created,
            "total_auto_created_channels": total_auto_created,
        }
    except Exception as e:
        logger.error(f"Failed to find groups with auto_created channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ClearAutoCreatedRequest(BaseModel):
    group_ids: list[int]


@app.post("/api/channels/clear-auto-created", tags=["Channels"])
async def clear_auto_created_flag(request: ClearAutoCreatedRequest):
    """Clear the auto_created flag from all channels in the specified groups.

    This converts auto_created channels to manual channels by setting
    auto_created=False and auto_created_by=None.
    """
    client = get_client()
    group_ids = set(request.group_ids)

    if not group_ids:
        raise HTTPException(status_code=400, detail="No group IDs provided")

    try:
        # Fetch all channels and find auto_created ones in the specified groups
        channels_to_update = []
        page = 1

        while True:
            result = await client.get_channels(page=page, page_size=500)
            page_channels = result.get("results", [])

            for channel in page_channels:
                if channel.get("auto_created") and channel.get("channel_group_id") in group_ids:
                    channels_to_update.append({
                        "id": channel.get("id"),
                        "name": channel.get("name"),
                        "channel_number": channel.get("channel_number"),
                        "channel_group_id": channel.get("channel_group_id"),
                    })

            if not result.get("next"):
                break
            page += 1
            if page > 50:  # Safety limit
                break

        if not channels_to_update:
            return {
                "status": "ok",
                "message": "No auto_created channels found in the specified groups",
                "updated_count": 0,
                "updated_channels": [],
                "failed_channels": [],
            }

        logger.info(f"Clearing auto_created flag from {len(channels_to_update)} channels in groups {group_ids}")

        # Update each channel via Dispatcharr API
        updated_channels = []
        failed_channels = []

        for channel in channels_to_update:
            channel_id = channel["id"]
            try:
                await client.update_channel(channel_id, {
                    "auto_created": False,
                    "auto_created_by": None,
                })
                updated_channels.append(channel)
                logger.debug(f"Cleared auto_created flag from channel {channel_id} ({channel['name']})")
            except Exception as update_err:
                failed_channels.append({**channel, "error": str(update_err)})
                logger.error(f"Failed to clear auto_created flag from channel {channel_id}: {update_err}")

        # Log to journal
        journal.log_entry(
            category="channel",
            action_type="bulk_update",
            entity_id=None,
            entity_name="Clear Auto-Created Flag",
            description=f"Cleared auto_created flag from {len(updated_channels)} channels in {len(group_ids)} group(s)",
            after_value={
                "group_ids": list(group_ids),
                "updated_count": len(updated_channels),
                "failed_count": len(failed_channels),
            },
        )

        return {
            "status": "ok",
            "message": f"Cleared auto_created flag from {len(updated_channels)} channel(s)",
            "updated_count": len(updated_channels),
            "updated_channels": updated_channels[:20],  # Limit response size
            "failed_channels": failed_channels,
        }
    except Exception as e:
        logger.error(f"Failed to clear auto_created flags: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channel-groups/with-streams", tags=["Channel Groups"])
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
        channels_without_streams = 0
        auto_created_count = 0
        sample_channel_groups = []  # Track first 5 for debugging
        sample_channels_no_streams = []  # Track channels without streams
        channels_by_group_id: dict = {}  # Track channel count per group for debugging
        sample_auto_created = []  # Track auto-created channels for debugging

        while True:
            result = await client.get_channels(page=page, page_size=500)
            page_channels = result.get("results", [])
            total_channels += len(page_channels)

            for channel in page_channels:
                channel_group_id = channel.get("channel_group_id")
                channel_number = channel.get("channel_number")
                channel_name = channel.get("name")
                is_auto_created = channel.get("auto_created", False)

                # Track auto-created channels
                if is_auto_created:
                    auto_created_count += 1
                    if len(sample_auto_created) < 10:
                        group_name = group_map.get(channel_group_id, {}).get("name", "Unknown")
                        sample_auto_created.append({
                            "channel_id": channel.get("id"),
                            "channel_name": channel_name,
                            "channel_number": channel_number,
                            "channel_group_id": channel_group_id,
                            "group_name": group_name,
                            "auto_created_by": channel.get("auto_created_by"),
                            "auto_created_by_name": channel.get("auto_created_by_name")
                        })

                # Track channels per group
                if channel_group_id is not None:
                    if channel_group_id not in channels_by_group_id:
                        channels_by_group_id[channel_group_id] = {"count": 0, "with_streams": 0, "samples": []}
                    channels_by_group_id[channel_group_id]["count"] += 1
                    if len(channels_by_group_id[channel_group_id]["samples"]) < 3:
                        channels_by_group_id[channel_group_id]["samples"].append(f"#{channel_number} {channel_name}")

                # Check if channel has any streams
                stream_ids = channel.get("streams", [])
                if stream_ids:  # Has at least one stream
                    channels_with_streams += 1
                    if channel_group_id is not None:
                        channels_by_group_id[channel_group_id]["with_streams"] += 1

                    # Collect samples for debugging - dump first channel completely
                    if len(sample_channel_groups) == 0:
                        logger.info(f"First channel with streams (FULL DATA): {channel}")

                    if len(sample_channel_groups) < 5:
                        sample_channel_groups.append({
                            "channel_id": channel.get("id"),
                            "channel_name": channel_name,
                            "channel_number": channel_number,
                            "channel_group_id": channel_group_id,
                            "channel_group_type": type(channel_group_id).__name__,
                            "stream_count": len(stream_ids)
                        })

                    # IMPORTANT: Check for not None instead of truthy to handle group ID 0
                    if channel_group_id is not None:
                        groups_with_streams_ids.add(channel_group_id)
                else:
                    # Track channels WITHOUT streams for debugging
                    channels_without_streams += 1
                    if len(sample_channels_no_streams) < 10:
                        sample_channels_no_streams.append({
                            "channel_id": channel.get("id"),
                            "channel_name": channel_name,
                            "channel_number": channel_number,
                            "channel_group_id": channel_group_id,
                            "streams_field": stream_ids,
                            "streams_field_type": type(stream_ids).__name__
                        })

            if not result.get("next"):
                break
            page += 1
            if page > 50:  # Safety limit
                break

        # Log samples for debugging
        if sample_channel_groups:
            logger.info(f"Sample channels with streams (first 5): {sample_channel_groups}")

        # Log channels without streams
        if sample_channels_no_streams:
            logger.warning(f"[DEBUG] Found {channels_without_streams} channels WITHOUT streams. Samples: {sample_channels_no_streams}")

        # Log auto-created channels summary
        logger.info(f"[DEBUG] Auto-created channels: {auto_created_count} out of {total_channels} total")
        if sample_auto_created:
            logger.info(f"[DEBUG] Sample auto-created channels: {sample_auto_created}")

        # Log groups that have channels but NO streams
        groups_with_channels_no_streams = []
        for gid, data in channels_by_group_id.items():
            if data["with_streams"] == 0 and data["count"] > 0:
                group_name = group_map.get(gid, {}).get("name", "Unknown")
                groups_with_channels_no_streams.append({
                    "group_id": gid,
                    "group_name": group_name,
                    "channel_count": data["count"],
                    "samples": data["samples"]
                })

        if groups_with_channels_no_streams:
            logger.warning(f"[DEBUG] Groups with channels but NO streams ({len(groups_with_channels_no_streams)}): {groups_with_channels_no_streams[:20]}")

        logger.info(f"Scanned {total_channels} channels, found {channels_with_streams} with streams")
        logger.info(f"Found {len(groups_with_streams_ids)} groups with channels containing streams")
        logger.info(f"Group IDs found: {sorted(list(groups_with_streams_ids))}")

        # Log group names for groups with streams
        groups_with_streams_names = []
        for gid in sorted(groups_with_streams_ids):
            group_name = group_map.get(gid, {}).get("name", "Unknown")
            groups_with_streams_names.append(f"{gid}:{group_name}")
        logger.info(f"[DEBUG] Groups with streams (id:name): {groups_with_streams_names}")

        # Log any groups named "Entertainment" specifically
        entertainment_groups = [g for g in all_groups if "entertainment" in g.get("name", "").lower()]
        logger.info(f"[DEBUG] Groups containing 'Entertainment' in name: {entertainment_groups}")
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


@app.get("/api/stream-groups", tags=["Streams"])
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


@app.post("/api/cache/invalidate", tags=["Cache"])
async def invalidate_cache(prefix: Optional[str] = None):
    """Invalidate cached data. If prefix is provided, only invalidate matching keys."""
    cache = get_cache()
    if prefix:
        count = cache.invalidate_prefix(prefix)
        return {"message": f"Invalidated {count} cache entries with prefix '{prefix}'"}
    else:
        count = cache.clear()
        return {"message": f"Cleared entire cache ({count} entries)"}


@app.get("/api/cache/stats", tags=["Cache"])
async def cache_stats():
    """Get cache statistics."""
    cache = get_cache()
    return cache.stats()


# Providers (M3U Accounts)
@app.get("/api/providers", tags=["Providers"])
async def get_providers():
    client = get_client()
    try:
        return await client.get_m3u_accounts()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/providers/group-settings", tags=["Providers"])
async def get_all_provider_group_settings():
    """Get group settings from all M3U providers, mapped by channel_group_id."""
    client = get_client()
    try:
        return await client.get_all_m3u_group_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# EPG Sources
@app.get("/api/epg/sources", tags=["EPG"])
async def get_epg_sources():
    client = get_client()
    try:
        return await client.get_epg_sources()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/epg/sources/{source_id}", tags=["EPG"])
async def get_epg_source(source_id: int):
    client = get_client()
    try:
        return await client.get_epg_source(source_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/epg/sources", tags=["EPG"])
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


@app.patch("/api/epg/sources/{source_id}", tags=["EPG"])
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


@app.delete("/api/epg/sources/{source_id}", tags=["EPG"])
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


async def _poll_epg_refresh_completion(source_id: int, source_name: str, initial_updated):
    """
    Background task to poll Dispatcharr until EPG refresh completes.

    Polls every REFRESH_POLL_INTERVAL_SECONDS for up to EPG_REFRESH_MAX_WAIT_SECONDS.
    Sends success notification when updated_at changes, warning on timeout.
    Uses longer timeout than M3U since EPG files can be very large.
    """
    import asyncio
    from datetime import datetime

    client = get_client()
    wait_start = datetime.utcnow()

    try:
        while True:
            elapsed = (datetime.utcnow() - wait_start).total_seconds()
            if elapsed >= EPG_REFRESH_MAX_WAIT_SECONDS:
                logger.warning(f"[EPG-REFRESH] Timeout waiting for '{source_name}' refresh after {elapsed:.0f}s")
                await send_alert(
                    title=f"EPG Refresh: {source_name}",
                    message=f"EPG refresh for '{source_name}' timed out after {int(elapsed)}s - refresh may still be in progress",
                    notification_type="warning",
                    source="EPG Refresh",
                    metadata={"source_id": source_id, "source_name": source_name, "timeout": True},
                    alert_category="epg_refresh",
                    entity_id=source_id,
                )
                return

            await asyncio.sleep(REFRESH_POLL_INTERVAL_SECONDS)

            try:
                current_source = await client.get_epg_source(source_id)
            except Exception as e:
                # Source may have been deleted during refresh
                logger.warning(f"[EPG-REFRESH] Could not fetch source {source_id} during polling: {e}")
                return

            current_updated = current_source.get("updated_at") or current_source.get("last_updated")

            if current_updated and current_updated != initial_updated:
                wait_duration = (datetime.utcnow() - wait_start).total_seconds()
                logger.info(f"[EPG-REFRESH] '{source_name}' refresh complete in {wait_duration:.1f}s")

                journal.log_entry(
                    category="epg",
                    action_type="refresh",
                    entity_id=source_id,
                    entity_name=source_name,
                    description=f"Refreshed EPG source '{source_name}' in {wait_duration:.1f}s",
                )

                await send_alert(
                    title=f"EPG Refresh: {source_name}",
                    message=f"Successfully refreshed EPG source '{source_name}' in {wait_duration:.1f}s",
                    notification_type="success",
                    source="EPG Refresh",
                    metadata={"source_id": source_id, "source_name": source_name, "duration": wait_duration},
                    alert_category="epg_refresh",
                    entity_id=source_id,
                )
                return
            elif elapsed > 30 and not initial_updated:
                # After 30 seconds, assume complete if no timestamp field available
                wait_duration = (datetime.utcnow() - wait_start).total_seconds()
                logger.info(f"[EPG-REFRESH] '{source_name}' - assuming complete after {wait_duration:.0f}s (no timestamp field)")

                journal.log_entry(
                    category="epg",
                    action_type="refresh",
                    entity_id=source_id,
                    entity_name=source_name,
                    description=f"Refreshed EPG source '{source_name}'",
                )

                await send_alert(
                    title=f"EPG Refresh: {source_name}",
                    message=f"EPG source '{source_name}' refresh completed",
                    notification_type="success",
                    source="EPG Refresh",
                    metadata={"source_id": source_id, "source_name": source_name},
                    alert_category="epg_refresh",
                    entity_id=source_id,
                )
                return

    except Exception as e:
        logger.error(f"[EPG-REFRESH] Error polling for '{source_name}' completion: {e}")


@app.post("/api/epg/sources/{source_id}/refresh", tags=["EPG"])
async def refresh_epg_source(source_id: int):
    """Trigger refresh for a single EPG source.

    Triggers the refresh and spawns a background task to poll for completion.
    Success notification is sent only when refresh actually completes.
    """
    import asyncio

    client = get_client()
    try:
        # Get source info and capture initial state for polling
        source = await client.get_epg_source(source_id)
        source_name = source.get("name", "Unknown")
        initial_updated = source.get("updated_at") or source.get("last_updated")

        # Trigger the refresh (returns immediately, refresh happens in background)
        result = await client.refresh_epg_source(source_id)

        # Spawn background task to poll for completion and send notification
        asyncio.create_task(
            _poll_epg_refresh_completion(source_id, source_name, initial_updated)
        )

        logger.info(f"[EPG-REFRESH] Triggered refresh for '{source_name}', polling for completion in background")
        return result
    except Exception as e:
        # Send error notification for trigger failure
        try:
            await send_alert(
                title="EPG Refresh Failed",
                message=f"Failed to trigger EPG refresh for source (ID: {source_id}): {str(e)}",
                notification_type="error",
                source="EPG Refresh",
                metadata={"source_id": source_id, "error": str(e)},
                alert_category="epg_refresh",
                entity_id=source_id,
            )
        except Exception:
            pass  # Don't fail the request if notification fails
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/epg/import", tags=["EPG"])
async def trigger_epg_import():
    client = get_client()
    try:
        return await client.trigger_epg_import()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# EPG Data
@app.get("/api/epg/data", tags=["EPG"])
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


@app.get("/api/epg/data/{data_id}", tags=["EPG"])
async def get_epg_data_by_id(data_id: int):
    client = get_client()
    try:
        return await client.get_epg_data_by_id(data_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/epg/grid", tags=["EPG"])
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


@app.get("/api/epg/lcn", tags=["EPG"])
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


@app.post("/api/epg/lcn/batch", tags=["EPG"])
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
@app.get("/api/stream-profiles", tags=["Stream Profiles"])
async def get_stream_profiles():
    client = get_client()
    try:
        return await client.get_stream_profiles()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------------------
# Channel Profiles
# -------------------------------------------------------------------------

@app.get("/api/channel-profiles", tags=["Channel Profiles"])
async def get_channel_profiles():
    """Get all channel profiles."""
    client = get_client()
    try:
        return await client.get_channel_profiles()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channel-profiles", tags=["Channel Profiles"])
async def create_channel_profile(request: Request):
    """Create a new channel profile."""
    client = get_client()
    try:
        data = await request.json()
        return await client.create_channel_profile(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channel-profiles/{profile_id}", tags=["Channel Profiles"])
async def get_channel_profile(profile_id: int):
    """Get a single channel profile."""
    client = get_client()
    try:
        return await client.get_channel_profile(profile_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/channel-profiles/{profile_id}", tags=["Channel Profiles"])
async def update_channel_profile(profile_id: int, request: Request):
    """Update a channel profile."""
    client = get_client()
    try:
        data = await request.json()
        return await client.update_channel_profile(profile_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channel-profiles/{profile_id}", tags=["Channel Profiles"])
async def delete_channel_profile(profile_id: int):
    """Delete a channel profile."""
    client = get_client()
    try:
        await client.delete_channel_profile(profile_id)
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/channel-profiles/{profile_id}/channels/bulk-update", tags=["Channel Profiles"])
async def bulk_update_profile_channels(profile_id: int, request: Request):
    """Bulk enable/disable channels for a profile."""
    client = get_client()
    try:
        data = await request.json()
        return await client.bulk_update_profile_channels(profile_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/channel-profiles/{profile_id}/channels/{channel_id}", tags=["Channel Profiles"])
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

@app.get("/api/m3u/accounts/{account_id}", tags=["M3U"])
async def get_m3u_account(account_id: int):
    """Get a single M3U account by ID."""
    client = get_client()
    try:
        return await client.get_m3u_account(account_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/m3u/accounts/{account_id}/stream-metadata", tags=["M3U"])
async def get_m3u_stream_metadata(account_id: int):
    """Fetch and parse M3U file to extract stream metadata (tvg-id -> tvc-guide-stationid mapping).

    This parses the M3U file directly to get attributes like tvc-guide-stationid
    that Dispatcharr doesn't expose via its API.
    """
    client = get_client()
    try:
        # Get the M3U account details
        account = await client.get_m3u_account(account_id)

        # Construct the M3U URL based on account type
        account_type = account.get("account_type", "M3U")
        server_url = account.get("server_url")

        if not server_url:
            raise HTTPException(status_code=400, detail="M3U account has no server URL")

        if account_type == "XC":
            # XtreamCodes: construct M3U URL from credentials
            username = account.get("username", "")
            password = account.get("password", "")
            # Remove trailing slash from server_url if present
            base_url = server_url.rstrip("/")
            m3u_url = f"{base_url}/get.php?username={username}&password={password}&type=m3u_plus&output=ts"
        else:
            # Standard M3U: server_url is the direct URL
            m3u_url = server_url

        # Fetch the M3U file
        async with httpx.AsyncClient(timeout=60.0) as http_client:
            response = await http_client.get(m3u_url, follow_redirects=True)
            response.raise_for_status()
            m3u_content = response.text

        # Parse EXTINF lines to extract metadata
        # Format: #EXTINF:-1 tvg-id="ID" tvc-guide-stationid="12345" ...,Channel Name
        metadata = {}

        # Regex to match key="value" or key=value patterns in EXTINF lines
        attr_pattern = re.compile(r'([\w-]+)=["\']?([^"\'>\s,]+)["\']?')

        lines = m3u_content.split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith('#EXTINF:'):
                # Extract all attributes from the EXTINF line
                attrs = dict(attr_pattern.findall(line))

                tvg_id = attrs.get('tvg-id')
                tvc_station_id = attrs.get('tvc-guide-stationid')

                # Only include entries that have a tvg-id (needed for matching)
                if tvg_id:
                    entry = {}
                    if tvc_station_id:
                        entry['tvc-guide-stationid'] = tvc_station_id
                    # Include other useful attributes
                    if 'tvg-name' in attrs:
                        entry['tvg-name'] = attrs['tvg-name']
                    if 'tvg-logo' in attrs:
                        entry['tvg-logo'] = attrs['tvg-logo']
                    if 'group-title' in attrs:
                        entry['group-title'] = attrs['group-title']

                    if entry:  # Only add if we have at least one attribute
                        metadata[tvg_id] = entry

        logger.info(f"Parsed M3U metadata for account {account_id}: {len(metadata)} entries with tvg-id")
        return {"metadata": metadata, "count": len(metadata)}

    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch M3U file for account {account_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch M3U file: {str(e)}")
    except Exception as e:
        logger.error(f"Failed to parse M3U metadata for account {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/m3u/accounts", tags=["M3U"])
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


@app.post("/api/m3u/upload", tags=["M3U"])
async def upload_m3u_file(file: UploadFile = File(...)):
    """Upload an M3U file and return the path for use with M3U accounts.

    The file is saved to /config/m3u_uploads/ directory.
    Returns the full path that can be used as file_path when creating/updating M3U accounts.
    """
    import aiofiles
    from pathlib import Path
    import uuid

    # Create uploads directory if it doesn't exist
    uploads_dir = CONFIG_DIR / "m3u_uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    # Validate file extension
    original_name = file.filename or "upload.m3u"
    if not original_name.lower().endswith(('.m3u', '.m3u8')):
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Only .m3u and .m3u8 files are allowed."
        )

    # Create a unique filename to avoid collisions
    # Use original name with a short UUID prefix for uniqueness
    safe_name = re.sub(r'[^\w\-_\.]', '_', original_name)
    unique_prefix = str(uuid.uuid4())[:8]
    final_name = f"{unique_prefix}_{safe_name}"
    file_path = uploads_dir / final_name

    try:
        # Read and save the file
        content = await file.read()
        async with aiofiles.open(file_path, 'wb') as f:
            await f.write(content)

        logger.info(f"M3U file uploaded: {file_path} ({len(content)} bytes)")

        # Log to journal
        journal.log_entry(
            category="m3u",
            action_type="upload",
            entity_name=original_name,
            description=f"Uploaded M3U file '{original_name}' ({len(content)} bytes)",
        )

        return {
            "file_path": str(file_path),
            "original_name": original_name,
            "size": len(content)
        }
    except Exception as e:
        logger.error(f"Failed to upload M3U file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")


@app.put("/api/m3u/accounts/{account_id}", tags=["M3U"])
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


@app.patch("/api/m3u/accounts/{account_id}", tags=["M3U"])
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


@app.delete("/api/m3u/accounts/{account_id}", tags=["M3U"])
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

        # Invalidate caches - streams from this M3U are now gone
        cache = get_cache()
        streams_cleared = cache.invalidate_prefix("streams:")
        groups_cleared = cache.invalidate("channel_groups")
        logger.info(f"Invalidated cache after M3U deletion: {streams_cleared} stream entries, channel_groups={groups_cleared}")

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


async def _capture_m3u_changes_after_refresh(account_id: int, account_name: str):
    """
    Capture M3U state changes after a refresh.

    Fetches current groups/streams for the account, compares with previous
    snapshot, and persists any detected changes.

    IMPORTANT: Gets ALL groups from the M3U source (not just enabled ones) by:
    1. Getting the M3U account which has channel_groups with group IDs
    2. Getting all channel groups to build ID -> name mapping
    3. Getting actual stream counts per group (only available for enabled groups)
    4. Merging: all groups get names, stream counts where available
    """
    from m3u_change_detector import M3UChangeDetector

    try:
        api_client = get_client()

        # Get the M3U account - channel_groups contains ALL groups from this M3U source
        account_data = await api_client.get_m3u_account(account_id)
        account_channel_groups = account_data.get("channel_groups", [])

        # Get all channel groups to build ID -> name mapping
        all_channel_groups = await api_client.get_channel_groups()
        group_lookup = {
            g["id"]: g["name"]
            for g in all_channel_groups
        }

        # Get actual stream counts (only available for enabled groups with imported streams)
        stream_counts = await api_client.get_stream_groups_with_counts(m3u_account_id=account_id)
        stream_count_lookup = {
            g["name"]: g["count"]
            for g in stream_counts
        }

        # Build list of enabled group names to fetch stream names for
        enabled_group_names = []
        for acg in account_channel_groups:
            group_id = acg.get("channel_group")
            if group_id and group_id in group_lookup and acg.get("enabled", False):
                enabled_group_names.append(group_lookup[group_id])

        # Fetch stream names for enabled groups (limit to first 50 per group)
        stream_names_by_group = {}
        MAX_STREAM_NAMES = 500
        logger.info(f"[M3U-CHANGE] Fetching stream names for {len(enabled_group_names)} enabled groups: {enabled_group_names[:5]}{'...' if len(enabled_group_names) > 5 else ''}")
        for group_name in enabled_group_names:
            try:
                streams_response = await api_client.get_streams(
                    page=1,
                    page_size=MAX_STREAM_NAMES,
                    channel_group_name=group_name,
                    m3u_account=account_id,
                )
                results = streams_response.get("results", [])
                stream_names = [s.get("name", "") for s in results]
                logger.debug(f"[M3U-CHANGE] Group '{group_name}': got {len(results)} streams, {len(stream_names)} names")
                if stream_names:
                    stream_names_by_group[group_name] = stream_names
            except Exception as e:
                logger.warning(f"[M3U-CHANGE] Could not fetch streams for group '{group_name}': {e}")

        logger.info(f"[M3U-CHANGE] Captured stream names for {len(stream_names_by_group)} groups")

        # Match up: for each group in this M3U account, get name and stream count
        current_groups = []
        total_streams = 0

        for acg in account_channel_groups:
            group_id = acg.get("channel_group")
            if group_id and group_id in group_lookup:
                group_name = group_lookup[group_id]
                # Get stream count if available (only for enabled groups), otherwise 0
                stream_count = stream_count_lookup.get(group_name, 0)
                enabled = acg.get("enabled", False)
                current_groups.append({
                    "name": group_name,
                    "stream_count": stream_count,
                    "enabled": enabled,
                })
                total_streams += stream_count

        logger.info(
            f"[M3U-CHANGE] Capturing state for account {account_id} ({account_name}): "
            f"{len(current_groups)} groups, {total_streams} streams (all groups from M3U)"
        )

        # Use change detector to compare and persist
        db = get_session()
        try:
            detector = M3UChangeDetector(db)
            change_set = detector.detect_changes(
                m3u_account_id=account_id,
                current_groups=current_groups,
                current_total_streams=total_streams,
                stream_names_by_group=stream_names_by_group,
            )

            if change_set.has_changes:
                # Persist the changes
                detector.persist_changes(change_set)
                logger.info(
                    f"[M3U-CHANGE] Detected and persisted changes for {account_name}: "
                    f"+{len(change_set.groups_added)} groups, -{len(change_set.groups_removed)} groups, "
                    f"+{sum(s.count for s in change_set.streams_added)} streams, "
                    f"-{sum(s.count for s in change_set.streams_removed)} streams"
                )
            else:
                logger.debug(f"[M3U-CHANGE] No changes detected for {account_name}")
        finally:
            db.close()

    except Exception as e:
        logger.error(f"[M3U-CHANGE] Failed to capture changes for {account_name}: {e}")


async def _poll_m3u_refresh_completion(account_id: int, account_name: str, initial_updated):
    """
    Background task to poll Dispatcharr until M3U refresh completes.

    Polls every REFRESH_POLL_INTERVAL_SECONDS for up to M3U_REFRESH_MAX_WAIT_SECONDS.
    Sends success notification when updated_at changes, warning on timeout.
    """
    import asyncio
    from datetime import datetime

    client = get_client()
    wait_start = datetime.utcnow()

    try:
        while True:
            elapsed = (datetime.utcnow() - wait_start).total_seconds()
            if elapsed >= M3U_REFRESH_MAX_WAIT_SECONDS:
                logger.warning(f"[M3U-REFRESH] Timeout waiting for '{account_name}' refresh after {elapsed:.0f}s")
                await send_alert(
                    title=f"M3U Refresh: {account_name}",
                    message=f"M3U refresh for '{account_name}' timed out after {int(elapsed)}s - refresh may still be in progress",
                    notification_type="warning",
                    source="M3U Refresh",
                    metadata={"account_id": account_id, "account_name": account_name, "timeout": True},
                    alert_category="m3u_refresh",
                    entity_id=account_id,
                )
                return

            await asyncio.sleep(REFRESH_POLL_INTERVAL_SECONDS)

            try:
                current_account = await client.get_m3u_account(account_id)
            except Exception as e:
                # Account may have been deleted during refresh
                logger.warning(f"[M3U-REFRESH] Could not fetch account {account_id} during polling: {e}")
                return

            current_updated = current_account.get("updated_at") or current_account.get("last_refresh")

            if current_updated and current_updated != initial_updated:
                wait_duration = (datetime.utcnow() - wait_start).total_seconds()
                logger.info(f"[M3U-REFRESH] '{account_name}' refresh complete in {wait_duration:.1f}s")

                # Capture M3U changes after refresh
                await _capture_m3u_changes_after_refresh(account_id, account_name)

                # Send immediate digest if configured
                try:
                    await send_immediate_digest(account_id)
                except Exception as e:
                    logger.warning(f"[M3U-REFRESH] Failed to send immediate digest for '{account_name}': {e}")

                journal.log_entry(
                    category="m3u",
                    action_type="refresh",
                    entity_id=account_id,
                    entity_name=account_name,
                    description=f"Refreshed M3U account '{account_name}' in {wait_duration:.1f}s",
                )

                await send_alert(
                    title=f"M3U Refresh: {account_name}",
                    message=f"Successfully refreshed M3U account '{account_name}' in {wait_duration:.1f}s",
                    notification_type="success",
                    source="M3U Refresh",
                    metadata={"account_id": account_id, "account_name": account_name, "duration": wait_duration},
                    alert_category="m3u_refresh",
                    entity_id=account_id,
                )
                return
            elif elapsed > 30 and not initial_updated:
                # After 30 seconds, assume complete if no timestamp field available
                wait_duration = (datetime.utcnow() - wait_start).total_seconds()
                logger.info(f"[M3U-REFRESH] '{account_name}' - assuming complete after {wait_duration:.0f}s (no timestamp field)")

                # Capture M3U changes after refresh
                await _capture_m3u_changes_after_refresh(account_id, account_name)

                # Send immediate digest if configured
                try:
                    await send_immediate_digest(account_id)
                except Exception as e:
                    logger.warning(f"[M3U-REFRESH] Failed to send immediate digest for '{account_name}': {e}")

                journal.log_entry(
                    category="m3u",
                    action_type="refresh",
                    entity_id=account_id,
                    entity_name=account_name,
                    description=f"Refreshed M3U account '{account_name}'",
                )

                await send_alert(
                    title=f"M3U Refresh: {account_name}",
                    message=f"M3U account '{account_name}' refresh completed",
                    notification_type="success",
                    source="M3U Refresh",
                    metadata={"account_id": account_id, "account_name": account_name},
                    alert_category="m3u_refresh",
                    entity_id=account_id,
                )
                return

    except Exception as e:
        logger.error(f"[M3U-REFRESH] Error polling for '{account_name}' completion: {e}")


@app.post("/api/m3u/refresh", tags=["M3U"])
async def refresh_all_m3u_accounts():
    """Trigger refresh for all active M3U accounts."""
    client = get_client()
    try:
        return await client.refresh_all_m3u_accounts()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/m3u/refresh/{account_id}", tags=["M3U"])
async def refresh_m3u_account(account_id: int):
    """Trigger refresh for a single M3U account.

    Triggers the refresh and spawns a background task to poll for completion.
    Success notification is sent only when refresh actually completes.
    """
    import asyncio

    client = get_client()
    try:
        # Get account info and capture initial state for polling
        account = await client.get_m3u_account(account_id)
        account_name = account.get("name", "Unknown")
        initial_updated = account.get("updated_at") or account.get("last_refresh")

        # Trigger the refresh (returns immediately, refresh happens in background)
        result = await client.refresh_m3u_account(account_id)

        # Spawn background task to poll for completion and send notification
        asyncio.create_task(
            _poll_m3u_refresh_completion(account_id, account_name, initial_updated)
        )

        logger.info(f"[M3U-REFRESH] Triggered refresh for '{account_name}', polling for completion in background")
        return result
    except Exception as e:
        # Send error notification for trigger failure
        try:
            await send_alert(
                title="M3U Refresh Failed",
                message=f"Failed to trigger M3U refresh for account (ID: {account_id}): {str(e)}",
                notification_type="error",
                source="M3U Refresh",
                metadata={"account_id": account_id, "error": str(e)},
                alert_category="m3u_refresh",
                entity_id=account_id,
            )
        except Exception:
            pass  # Don't fail the request if notification fails
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/m3u/accounts/{account_id}/refresh-vod", tags=["M3U"])
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

@app.get("/api/m3u/accounts/{account_id}/filters", tags=["M3U"])
async def get_m3u_filters(account_id: int):
    """Get all filters for an M3U account."""
    client = get_client()
    try:
        return await client.get_m3u_filters(account_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/m3u/accounts/{account_id}/filters", tags=["M3U"])
async def create_m3u_filter(account_id: int, request: Request):
    """Create a new filter for an M3U account."""
    client = get_client()
    try:
        data = await request.json()
        return await client.create_m3u_filter(account_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/m3u/accounts/{account_id}/filters/{filter_id}", tags=["M3U"])
async def update_m3u_filter(account_id: int, filter_id: int, request: Request):
    """Update a filter for an M3U account."""
    client = get_client()
    try:
        data = await request.json()
        return await client.update_m3u_filter(account_id, filter_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/m3u/accounts/{account_id}/filters/{filter_id}", tags=["M3U"])
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

@app.get("/api/m3u/accounts/{account_id}/profiles/", tags=["M3U"])
async def get_m3u_profiles(account_id: int):
    """Get all profiles for an M3U account."""
    client = get_client()
    try:
        return await client.get_m3u_profiles(account_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/m3u/accounts/{account_id}/profiles/", tags=["M3U"])
async def create_m3u_profile(account_id: int, request: Request):
    """Create a new profile for an M3U account."""
    client = get_client()
    try:
        data = await request.json()
        return await client.create_m3u_profile(account_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/m3u/accounts/{account_id}/profiles/{profile_id}/", tags=["M3U"])
async def get_m3u_profile(account_id: int, profile_id: int):
    """Get a specific profile for an M3U account."""
    client = get_client()
    try:
        return await client.get_m3u_profile(account_id, profile_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/m3u/accounts/{account_id}/profiles/{profile_id}/", tags=["M3U"])
async def update_m3u_profile(account_id: int, profile_id: int, request: Request):
    """Update a profile for an M3U account."""
    client = get_client()
    try:
        data = await request.json()
        return await client.update_m3u_profile(account_id, profile_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/m3u/accounts/{account_id}/profiles/{profile_id}/", tags=["M3U"])
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

@app.patch("/api/m3u/accounts/{account_id}/group-settings", tags=["M3U"])
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

@app.get("/api/m3u/server-groups", tags=["M3U"])
async def get_server_groups():
    """Get all server groups."""
    client = get_client()
    try:
        return await client.get_server_groups()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/m3u/server-groups", tags=["M3U"])
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


@app.patch("/api/m3u/server-groups/{group_id}", tags=["M3U"])
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


@app.delete("/api/m3u/server-groups/{group_id}", tags=["M3U"])
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


# M3U Change Tracking API
@app.get("/api/m3u/changes", tags=["M3U Digest"])
async def get_m3u_changes(
    page: int = 1,
    page_size: int = 50,
    m3u_account_id: Optional[int] = None,
    change_type: Optional[str] = None,
    enabled: Optional[bool] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = "desc",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """
    Get paginated list of M3U change logs.

    Args:
        page: Page number (1-indexed)
        page_size: Number of items per page
        m3u_account_id: Filter by M3U account ID
        change_type: Filter by change type (group_added, group_removed, streams_added, streams_removed)
        enabled: Filter by enabled status (true/false)
        sort_by: Column to sort by (change_time, m3u_account_id, change_type, group_name, count, enabled)
        sort_order: Sort order (asc or desc, default: desc)
        date_from: Filter changes from this date (ISO format)
        date_to: Filter changes until this date (ISO format)
    """
    from datetime import datetime as dt
    from models import M3UChangeLog

    db = get_session()
    try:
        query = db.query(M3UChangeLog)

        # Apply filters
        if m3u_account_id:
            query = query.filter(M3UChangeLog.m3u_account_id == m3u_account_id)
        if change_type:
            query = query.filter(M3UChangeLog.change_type == change_type)
        if enabled is not None:
            query = query.filter(M3UChangeLog.enabled == enabled)
        if date_from:
            try:
                date_from_dt = dt.fromisoformat(date_from.replace("Z", "+00:00"))
                query = query.filter(M3UChangeLog.change_time >= date_from_dt)
            except ValueError:
                pass
        if date_to:
            try:
                date_to_dt = dt.fromisoformat(date_to.replace("Z", "+00:00"))
                query = query.filter(M3UChangeLog.change_time <= date_to_dt)
            except ValueError:
                pass

        # Get total count
        total = query.count()

        # Apply sorting
        sort_columns = {
            "change_time": M3UChangeLog.change_time,
            "m3u_account_id": M3UChangeLog.m3u_account_id,
            "change_type": M3UChangeLog.change_type,
            "group_name": M3UChangeLog.group_name,
            "count": M3UChangeLog.count,
            "enabled": M3UChangeLog.enabled,
        }
        sort_column = sort_columns.get(sort_by, M3UChangeLog.change_time)
        if sort_order == "asc":
            query = query.order_by(sort_column.asc())
        else:
            query = query.order_by(sort_column.desc())

        # Apply pagination
        changes = (
            query.offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        return {
            "results": [c.to_dict() for c in changes],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        }
    finally:
        db.close()


@app.get("/api/m3u/changes/summary", tags=["M3U Digest"])
async def get_m3u_changes_summary(
    hours: int = 24,
    m3u_account_id: Optional[int] = None,
):
    """
    Get aggregated summary of M3U changes.

    Args:
        hours: Look back this many hours (default: 24)
        m3u_account_id: Filter by M3U account ID
    """
    from datetime import datetime as dt, timedelta
    from m3u_change_detector import M3UChangeDetector

    db = get_session()
    try:
        detector = M3UChangeDetector(db)
        since = dt.utcnow() - timedelta(hours=hours)
        summary = detector.get_change_summary(since, m3u_account_id)
        return summary
    finally:
        db.close()


@app.get("/api/m3u/accounts/{account_id}/changes", tags=["M3U"])
async def get_m3u_account_changes(
    account_id: int,
    page: int = 1,
    page_size: int = 50,
    change_type: Optional[str] = None,
):
    """
    Get change history for a specific M3U account.

    Args:
        account_id: M3U account ID
        page: Page number (1-indexed)
        page_size: Number of items per page
        change_type: Filter by change type
    """
    from models import M3UChangeLog

    db = get_session()
    try:
        query = db.query(M3UChangeLog).filter(M3UChangeLog.m3u_account_id == account_id)

        if change_type:
            query = query.filter(M3UChangeLog.change_type == change_type)

        total = query.count()

        changes = (
            query.order_by(M3UChangeLog.change_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        return {
            "results": [c.to_dict() for c in changes],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "m3u_account_id": account_id,
        }
    finally:
        db.close()


@app.get("/api/m3u/snapshots", tags=["M3U"])
async def get_m3u_snapshots(
    m3u_account_id: Optional[int] = None,
    limit: int = 10,
):
    """
    Get recent M3U snapshots.

    Args:
        m3u_account_id: Filter by M3U account ID
        limit: Maximum number of snapshots to return
    """
    from models import M3USnapshot

    db = get_session()
    try:
        query = db.query(M3USnapshot)

        if m3u_account_id:
            query = query.filter(M3USnapshot.m3u_account_id == m3u_account_id)

        snapshots = query.order_by(M3USnapshot.snapshot_time.desc()).limit(limit).all()

        return [s.to_dict() for s in snapshots]
    finally:
        db.close()


# M3U Digest Settings API
@app.get("/api/m3u/digest/settings", tags=["M3U Digest"])
async def get_m3u_digest_settings():
    """Get M3U digest email settings."""
    from tasks.m3u_digest import get_or_create_digest_settings

    db = get_session()
    try:
        settings = get_or_create_digest_settings(db)
        return settings.to_dict()
    finally:
        db.close()


class M3UDigestSettingsUpdate(BaseModel):
    """Request model for updating M3U digest settings."""
    enabled: Optional[bool] = None
    frequency: Optional[str] = None  # immediate, hourly, daily, weekly
    email_recipients: Optional[List[str]] = None
    include_group_changes: Optional[bool] = None
    include_stream_changes: Optional[bool] = None
    show_detailed_list: Optional[bool] = None  # Show detailed list vs just summary
    min_changes_threshold: Optional[int] = None
    send_to_discord: Optional[bool] = None  # Send digest to Discord (uses shared webhook)


@app.put("/api/m3u/digest/settings", tags=["M3U Digest"])
async def update_m3u_digest_settings(request: M3UDigestSettingsUpdate):
    """Update M3U digest email settings."""
    from tasks.m3u_digest import get_or_create_digest_settings
    import re

    db = get_session()
    try:
        settings = get_or_create_digest_settings(db)

        # Validate and apply updates
        if request.enabled is not None:
            settings.enabled = request.enabled

        if request.frequency is not None:
            if request.frequency not in ("immediate", "hourly", "daily", "weekly"):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid frequency. Must be: immediate, hourly, daily, or weekly"
                )
            settings.frequency = request.frequency

        if request.email_recipients is not None:
            # Validate email addresses
            email_pattern = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
            for email in request.email_recipients:
                if not email_pattern.match(email):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid email address: {email}"
                    )
            settings.set_email_recipients(request.email_recipients)

        if request.include_group_changes is not None:
            settings.include_group_changes = request.include_group_changes

        if request.include_stream_changes is not None:
            settings.include_stream_changes = request.include_stream_changes

        if request.show_detailed_list is not None:
            settings.show_detailed_list = request.show_detailed_list

        if request.min_changes_threshold is not None:
            if request.min_changes_threshold < 1:
                raise HTTPException(
                    status_code=400,
                    detail="min_changes_threshold must be at least 1"
                )
            settings.min_changes_threshold = request.min_changes_threshold

        if request.send_to_discord is not None:
            settings.send_to_discord = request.send_to_discord

        db.commit()
        db.refresh(settings)

        # Log to journal
        journal.log_entry(
            category="m3u",
            action_type="update",
            entity_id=settings.id,
            entity_name="M3U Digest Settings",
            description="Updated M3U digest email settings",
            after_value=settings.to_dict(),
        )

        return settings.to_dict()
    finally:
        db.close()


@app.post("/api/m3u/digest/test", tags=["M3U Digest"])
async def send_test_m3u_digest():
    """Send a test M3U digest email."""
    from tasks.m3u_digest import M3UDigestTask, get_or_create_digest_settings

    db = get_session()
    try:
        settings = get_or_create_digest_settings(db)

        has_email = bool(settings.get_email_recipients())
        has_discord = bool(settings.send_to_discord)
        if not has_email and not has_discord:
            raise HTTPException(
                status_code=400,
                detail="No notification targets configured. Please add email recipients or enable Discord."
            )

        task = M3UDigestTask()
        result = await task.execute(force=True)

        return {
            "success": result.success,
            "message": result.message,
            "details": result.details,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to send test M3U digest: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# Journal API
@app.get("/api/journal", tags=["Journal"])
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


@app.get("/api/journal/stats", tags=["Journal"])
async def get_journal_stats():
    """Get summary statistics for the journal."""
    return journal.get_stats()


@app.delete("/api/journal/purge", tags=["Journal"])
async def purge_journal_entries(days: int = 90):
    """Delete journal entries older than the specified number of days."""
    deleted_count = journal.purge_old_entries(days=days)
    return {"deleted": deleted_count, "days": days}


# =============================================================================
# Notifications API
# =============================================================================


@app.get("/api/notifications", tags=["Notifications"])
async def get_notifications(
    page: int = 1,
    page_size: int = 50,
    unread_only: bool = False,
    notification_type: Optional[str] = None,
):
    """Get notifications with pagination and filtering."""
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


async def create_notification_internal(
    notification_type: str = "info",
    title: Optional[str] = None,
    message: str = "",
    source: Optional[str] = None,
    source_id: Optional[str] = None,
    action_label: Optional[str] = None,
    action_url: Optional[str] = None,
    metadata: Optional[dict] = None,
    send_alerts: bool = True,
    alert_category: Optional[str] = None,
    entity_id: Optional[int] = None,
    channel_settings: Optional[dict] = None,
) -> Optional[dict]:
    """Create a new notification (internal helper).

    Can be called from anywhere in the backend (task_engine, etc.)

    Args:
        notification_type: One of "info", "success", "warning", "error"
        title: Optional notification title
        message: Notification message (required)
        source: Source identifier (e.g., "task", "system")
        source_id: Source-specific ID (e.g., task_id)
        action_label: Optional action button label
        action_url: Optional action URL
        metadata: Optional additional data
        send_alerts: If True (default), also dispatch to configured alert channels.
        alert_category: Category for granular filtering ("epg_refresh", "m3u_refresh", "probe_failures")
        entity_id: Source/account ID for filtering (EPG source ID or M3U account ID)
        channel_settings: Per-task channel settings (send_to_email, send_to_discord, send_to_telegram)

    Returns:
        Notification dict or None if message is empty
    """
    import json
    import asyncio
    from models import Notification

    if not message:
        logger.warning("create_notification_internal called with empty message")
        return None

    if notification_type not in ("info", "success", "warning", "error"):
        logger.warning(f"Invalid notification type: {notification_type}, defaulting to info")
        notification_type = "info"

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
                    alert_category=alert_category,
                    entity_id=entity_id,
                    channel_settings=channel_settings,
                )
            )

        logger.debug(f"Created notification: {notification_type} - {title or message[:50]}")
        return result
    except Exception as e:
        logger.error(f"Failed to create notification: {e}")
        return None
    finally:
        session.close()


async def update_notification_internal(
    notification_id: int,
    notification_type: str = None,
    message: str = None,
    metadata: dict = None,
) -> Optional[dict]:
    """Update an existing notification's content.

    Used for updating progress notifications like stream probe status.

    Args:
        notification_id: ID of the notification to update
        notification_type: New type (info, success, warning, error) - optional
        message: New message - optional
        metadata: New metadata dict - optional (replaces existing metadata)

    Returns:
        Updated notification dict or None if not found
    """
    import json
    from models import Notification

    session = get_session()
    try:
        notification = session.query(Notification).filter(
            Notification.id == notification_id
        ).first()

        if not notification:
            logger.warning(f"Notification {notification_id} not found for update")
            return None

        if notification_type is not None and notification_type in ("info", "success", "warning", "error"):
            notification.type = notification_type

        if message is not None:
            notification.message = message

        if metadata is not None:
            notification.extra_data = json.dumps(metadata)

        session.commit()
        session.refresh(notification)
        result = notification.to_dict()

        logger.debug(f"Updated notification {notification_id}: {notification_type or 'same type'} - {message[:50] if message else 'same message'}")
        return result
    except Exception as e:
        logger.error(f"Failed to update notification {notification_id}: {e}")
        session.rollback()
        return None
    finally:
        session.close()


async def delete_notifications_by_source_internal(source: str) -> int:
    """Delete all notifications with a given source.

    Used for cleanup of progress notifications (e.g., old probe notifications).

    Args:
        source: The source identifier to match

    Returns:
        Number of notifications deleted
    """
    from models import Notification

    session = get_session()
    try:
        deleted = session.query(Notification).filter(
            Notification.source == source
        ).delete()
        session.commit()
        if deleted > 0:
            logger.debug(f"Deleted {deleted} notification(s) with source '{source}'")
        return deleted
    except Exception as e:
        logger.error(f"Failed to delete notifications by source '{source}': {e}")
        session.rollback()
        return 0
    finally:
        session.close()


class CreateNotificationRequest(BaseModel):
    notification_type: str = "info"
    title: Optional[str] = None
    message: str
    source: Optional[str] = None
    source_id: Optional[str] = None
    action_label: Optional[str] = None
    action_url: Optional[str] = None
    metadata: Optional[dict] = None
    send_alerts: bool = True


@app.post("/api/notifications", tags=["Notifications"])
async def create_notification(request: CreateNotificationRequest):
    """Create a new notification (API endpoint).

    Args:
        send_alerts: If True (default), also dispatch to configured alert channels.
    """
    if not request.message:
        raise HTTPException(status_code=400, detail="Message is required")

    if request.notification_type not in ("info", "success", "warning", "error"):
        raise HTTPException(status_code=400, detail="Invalid notification type")

    result = await create_notification_internal(
        notification_type=request.notification_type,
        title=request.title,
        message=request.message,
        source=request.source,
        source_id=request.source_id,
        action_label=request.action_label,
        action_url=request.action_url,
        metadata=request.metadata,
        send_alerts=request.send_alerts,
    )

    if result is None:
        raise HTTPException(status_code=500, detail="Failed to create notification")

    return result


async def _dispatch_to_alert_channels(
    title: Optional[str],
    message: str,
    notification_type: str,
    source: Optional[str],
    metadata: Optional[dict],
    alert_category: Optional[str] = None,
    entity_id: Optional[int] = None,
    channel_settings: Optional[dict] = None,
):
    """Dispatch notification to configured alert channels using shared settings.

    This sends directly to Discord/Telegram/Email using the shared notification
    settings configured in Settings > Notification Settings.

    Args:
        channel_settings: Per-task channel settings (send_to_email, send_to_discord, send_to_telegram).
                         If None, all channels are allowed.
    """
    import aiohttp

    settings = get_settings()
    results = {"email": None, "discord": None, "telegram": None}
    alert_title = title or "ECM Notification"

    # Determine which channels are enabled
    send_email = channel_settings.get("send_to_email", True) if channel_settings else True
    send_discord = channel_settings.get("send_to_discord", True) if channel_settings else True
    send_telegram = channel_settings.get("send_to_telegram", True) if channel_settings else True

    # Format message with type indicator
    type_emoji = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"}.get(notification_type, "📢")

    # Send to Discord if configured and enabled
    if send_discord and settings.is_discord_configured():
        try:
            discord_message = f"**{type_emoji} {alert_title}**\n\n{message}"
            if metadata:
                if "task_name" in metadata:
                    discord_message += f"\n\n**Task:** {metadata['task_name']}"
                if "duration_seconds" in metadata:
                    discord_message += f"\n**Duration:** {metadata['duration_seconds']:.1f}s"

            payload = {
                "content": discord_message,
                "username": "ECM Alerts",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    settings.discord_webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status == 204:
                        results["discord"] = True
                        logger.debug("Alert sent to Discord successfully")
                    else:
                        results["discord"] = False
                        logger.warning(f"Discord alert failed: {response.status}")
        except Exception as e:
            results["discord"] = False
            logger.error(f"Failed to send Discord alert: {e}")

    # Send to Telegram if configured and enabled
    if send_telegram and settings.is_telegram_configured():
        try:
            telegram_message = f"{type_emoji} <b>{alert_title}</b>\n\n{message}"
            if metadata:
                if "task_name" in metadata:
                    telegram_message += f"\n\n<b>Task:</b> {metadata['task_name']}"
                if "duration_seconds" in metadata:
                    telegram_message += f"\n<b>Duration:</b> {metadata['duration_seconds']:.1f}s"

            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
            payload = {
                "chat_id": settings.telegram_chat_id,
                "text": telegram_message,
                "parse_mode": "HTML",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status == 200:
                        results["telegram"] = True
                        logger.debug("Alert sent to Telegram successfully")
                    else:
                        results["telegram"] = False
                        text = await response.text()
                        logger.warning(f"Telegram alert failed: {response.status} - {text}")
        except Exception as e:
            results["telegram"] = False
            logger.error(f"Failed to send Telegram alert: {e}")

    # Send to Email if configured and enabled
    # Note: Email sending is more complex and would require SMTP setup
    # For now, we log that email is enabled but skip actual sending
    # (Email alerts for tasks can be implemented later if needed)
    if send_email and settings.is_smtp_configured():
        # TODO: Implement email alerts for task notifications
        # For now, just log that it would be sent
        logger.debug(f"Email alert would be sent (not yet implemented for task alerts)")
        results["email"] = None  # None means not attempted

    # Log summary
    sent = [k for k, v in results.items() if v is True]
    failed = [k for k, v in results.items() if v is False]
    if sent:
        logger.info(f"Alert dispatched to: {', '.join(sent)}")
    if failed:
        logger.warning(f"Alert failed for: {', '.join(failed)}")


@app.patch("/api/notifications/mark-all-read", tags=["Notifications"])
async def mark_all_notifications_read():
    """Mark all notifications as read."""
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


@app.patch("/api/notifications/{notification_id}", tags=["Notifications"])
async def update_notification(notification_id: int, read: Optional[bool] = None):
    """Update a notification (mark as read/unread)."""
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


@app.delete("/api/notifications/{notification_id}", tags=["Notifications"])
async def delete_notification(notification_id: int):
    """Delete a specific notification."""
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


@app.delete("/api/notifications", tags=["Notifications"])
async def clear_all_notifications(read_only: bool = True):
    """Clear notifications. By default only clears read notifications."""
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


@app.delete("/api/notifications/by-source", tags=["Notifications"])
async def delete_notifications_by_source(source: str, source_id: Optional[str] = None):
    """Delete notifications matching source and optionally source_id."""
    from models import Notification

    session = get_session()
    try:
        query = session.query(Notification).filter(Notification.source == source)
        if source_id is not None:
            query = query.filter(Notification.source_id == source_id)

        count = query.delete(synchronize_session=False)
        session.commit()
        return {"deleted": count, "source": source, "source_id": source_id}
    finally:
        session.close()


# =============================================================================
# Alert Methods
# =============================================================================


class AlertMethodCreate(BaseModel):
    name: str
    method_type: str
    config: dict
    enabled: bool = True
    notify_info: bool = False
    notify_success: bool = True
    notify_warning: bool = True
    notify_error: bool = True
    alert_sources: Optional[dict] = None  # Granular source filtering


class AlertMethodUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    enabled: Optional[bool] = None
    notify_info: Optional[bool] = None
    notify_success: Optional[bool] = None
    notify_warning: Optional[bool] = None
    notify_error: Optional[bool] = None
    alert_sources: Optional[dict] = None  # Granular source filtering


def validate_alert_sources(alert_sources: Optional[dict]) -> Optional[str]:
    """Validate alert_sources structure. Returns error message or None if valid."""
    if alert_sources is None:
        return None

    valid_filter_modes = {"all", "only_selected", "all_except"}

    # Validate EPG refresh section
    if "epg_refresh" in alert_sources:
        epg = alert_sources["epg_refresh"]
        if not isinstance(epg, dict):
            return "epg_refresh must be an object"
        if "filter_mode" in epg and epg["filter_mode"] not in valid_filter_modes:
            return f"epg_refresh.filter_mode must be one of: {valid_filter_modes}"
        if "source_ids" in epg and not isinstance(epg["source_ids"], list):
            return "epg_refresh.source_ids must be an array"

    # Validate M3U refresh section
    if "m3u_refresh" in alert_sources:
        m3u = alert_sources["m3u_refresh"]
        if not isinstance(m3u, dict):
            return "m3u_refresh must be an object"
        if "filter_mode" in m3u and m3u["filter_mode"] not in valid_filter_modes:
            return f"m3u_refresh.filter_mode must be one of: {valid_filter_modes}"
        if "account_ids" in m3u and not isinstance(m3u["account_ids"], list):
            return "m3u_refresh.account_ids must be an array"

    # Validate probe failures section
    if "probe_failures" in alert_sources:
        probe = alert_sources["probe_failures"]
        if not isinstance(probe, dict):
            return "probe_failures must be an object"
        if "min_failures" in probe:
            min_failures = probe["min_failures"]
            if not isinstance(min_failures, int) or min_failures < 0:
                return "probe_failures.min_failures must be a non-negative integer"

    return None


@app.get("/api/alert-methods/types", tags=["Alert Methods"])
async def get_alert_method_types():
    """Get available alert method types and their configuration fields."""
    logger.debug("Fetching alert method types")
    try:
        types = get_method_types()
        logger.debug(f"Found {len(types)} alert method types: {[t['type'] for t in types]}")
        return types
    except Exception as e:
        logger.exception(f"Error fetching alert method types: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/alert-methods", tags=["Alert Methods"])
async def list_alert_methods():
    """List all configured alert methods."""
    from models import AlertMethod as AlertMethodModel
    import json

    logger.debug("Listing alert methods")
    session = get_session()
    try:
        methods = session.query(AlertMethodModel).all()
        logger.debug(f"Found {len(methods)} alert methods in database")
        result = []
        for m in methods:
            alert_sources = None
            if m.alert_sources:
                try:
                    alert_sources = json.loads(m.alert_sources)
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append({
                "id": m.id,
                "name": m.name,
                "method_type": m.method_type,
                "enabled": m.enabled,
                "config": json.loads(m.config) if m.config else {},
                "notify_info": m.notify_info,
                "notify_success": m.notify_success,
                "notify_warning": m.notify_warning,
                "notify_error": m.notify_error,
                "alert_sources": alert_sources,
                "last_sent_at": m.last_sent_at.isoformat() + "Z" if m.last_sent_at else None,
                "created_at": m.created_at.isoformat() + "Z" if m.created_at else None,
            })
        return result
    except Exception as e:
        logger.exception(f"Error listing alert methods: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.post("/api/alert-methods", tags=["Alert Methods"])
async def create_alert_method(data: AlertMethodCreate):
    """Create a new alert method."""
    from models import AlertMethod as AlertMethodModel
    import json

    logger.debug(f"Creating alert method: name={data.name}, type={data.method_type}")

    session = None
    try:
        # Validate method type
        method_types = {mt["type"] for mt in get_method_types()}
        if data.method_type not in method_types:
            logger.warning(f"Unknown method type attempted: {data.method_type}")
            raise HTTPException(status_code=400, detail=f"Unknown method type: {data.method_type}")

        # Validate config
        method = create_method(data.method_type, 0, data.name, data.config)
        if method:
            is_valid, error = method.validate_config(data.config)
            if not is_valid:
                logger.warning(f"Invalid config for method {data.name}: {error}")
                raise HTTPException(status_code=400, detail=error)

        # Validate alert_sources if provided
        if data.alert_sources is not None:
            alert_sources_error = validate_alert_sources(data.alert_sources)
            if alert_sources_error:
                logger.warning(f"Invalid alert_sources for method {data.name}: {alert_sources_error}")
                raise HTTPException(status_code=400, detail=alert_sources_error)

        session = get_session()
        method_model = AlertMethodModel(
            name=data.name,
            method_type=data.method_type,
            config=json.dumps(data.config),
            enabled=data.enabled,
            notify_info=data.notify_info,
            notify_success=data.notify_success,
            notify_warning=data.notify_warning,
            notify_error=data.notify_error,
            alert_sources=json.dumps(data.alert_sources) if data.alert_sources else None,
        )
        session.add(method_model)
        session.commit()
        session.refresh(method_model)

        # Reload the manager to pick up the new method
        get_alert_manager().reload_method(method_model.id)

        logger.info(f"Created alert method: id={method_model.id}, name={method_model.name}, type={method_model.method_type}")
        return {
            "id": method_model.id,
            "name": method_model.name,
            "method_type": method_model.method_type,
            "enabled": method_model.enabled,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error creating alert method: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if session:
            session.close()


@app.get("/api/alert-methods/{method_id}", tags=["Alert Methods"])
async def get_alert_method(method_id: int):
    """Get a specific alert method."""
    from models import AlertMethod as AlertMethodModel
    import json

    logger.debug(f"Getting alert method: id={method_id}")
    session = get_session()
    try:
        method = session.query(AlertMethodModel).filter(
            AlertMethodModel.id == method_id
        ).first()

        if not method:
            logger.debug(f"Alert method not found: id={method_id}")
            raise HTTPException(status_code=404, detail="Alert method not found")

        logger.debug(f"Found alert method: id={method.id}, name={method.name}")
        alert_sources = None
        if method.alert_sources:
            try:
                alert_sources = json.loads(method.alert_sources)
            except (json.JSONDecodeError, TypeError):
                pass
        return {
            "id": method.id,
            "name": method.name,
            "method_type": method.method_type,
            "enabled": method.enabled,
            "config": json.loads(method.config) if method.config else {},
            "notify_info": method.notify_info,
            "notify_success": method.notify_success,
            "notify_warning": method.notify_warning,
            "notify_error": method.notify_error,
            "alert_sources": alert_sources,
            "last_sent_at": method.last_sent_at.isoformat() + "Z" if method.last_sent_at else None,
            "created_at": method.created_at.isoformat() + "Z" if method.created_at else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting alert method {method_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.patch("/api/alert-methods/{method_id}", tags=["Alert Methods"])
async def update_alert_method(method_id: int, data: AlertMethodUpdate):
    """Update an alert method."""
    from models import AlertMethod as AlertMethodModel
    import json

    logger.debug(f"Updating alert method: id={method_id}")
    session = get_session()
    try:
        method = session.query(AlertMethodModel).filter(
            AlertMethodModel.id == method_id
        ).first()

        if not method:
            logger.debug(f"Alert method not found for update: id={method_id}")
            raise HTTPException(status_code=404, detail="Alert method not found")

        if data.name is not None:
            method.name = data.name
        if data.config is not None:
            # Validate new config
            method_instance = create_method(method.method_type, method.id, method.name, data.config)
            if method_instance:
                is_valid, error = method_instance.validate_config(data.config)
                if not is_valid:
                    logger.warning(f"Invalid config for method {method_id}: {error}")
                    raise HTTPException(status_code=400, detail=error)
            method.config = json.dumps(data.config)
        if data.enabled is not None:
            method.enabled = data.enabled
        if data.notify_info is not None:
            method.notify_info = data.notify_info
        if data.notify_success is not None:
            method.notify_success = data.notify_success
        if data.notify_warning is not None:
            method.notify_warning = data.notify_warning
        if data.notify_error is not None:
            method.notify_error = data.notify_error
        if data.alert_sources is not None:
            # Validate alert_sources
            alert_sources_error = validate_alert_sources(data.alert_sources)
            if alert_sources_error:
                logger.warning(f"Invalid alert_sources for method {method_id}: {alert_sources_error}")
                raise HTTPException(status_code=400, detail=alert_sources_error)
            method.alert_sources = json.dumps(data.alert_sources) if data.alert_sources else None

        session.commit()

        # Reload the manager to pick up the changes
        get_alert_manager().reload_method(method_id)

        logger.info(f"Updated alert method: id={method_id}, name={method.name}")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating alert method {method_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.delete("/api/alert-methods/{method_id}", tags=["Alert Methods"])
async def delete_alert_method(method_id: int):
    """Delete an alert method."""
    from models import AlertMethod as AlertMethodModel

    logger.debug(f"Deleting alert method: id={method_id}")
    session = get_session()
    try:
        method = session.query(AlertMethodModel).filter(
            AlertMethodModel.id == method_id
        ).first()

        if not method:
            logger.debug(f"Alert method not found for deletion: id={method_id}")
            raise HTTPException(status_code=404, detail="Alert method not found")

        method_name = method.name
        session.delete(method)
        session.commit()

        # Remove from manager
        get_alert_manager().reload_method(method_id)

        logger.info(f"Deleted alert method: id={method_id}, name={method_name}")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error deleting alert method {method_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.post("/api/alert-methods/{method_id}/test", tags=["Alert Methods"])
async def test_alert_method(method_id: int):
    """Test an alert method by sending a test message."""
    from models import AlertMethod as AlertMethodModel
    import json

    logger.debug(f"Testing alert method: id={method_id}")
    session = get_session()
    try:
        method_model = session.query(AlertMethodModel).filter(
            AlertMethodModel.id == method_id
        ).first()

        if not method_model:
            logger.debug(f"Alert method not found for test: id={method_id}")
            raise HTTPException(status_code=404, detail="Alert method not found")

        config = json.loads(method_model.config) if method_model.config else {}
        method = create_method(
            method_model.method_type,
            method_model.id,
            method_model.name,
            config
        )

        if not method:
            logger.warning(f"Unknown method type for test: {method_model.method_type}")
            raise HTTPException(status_code=400, detail=f"Unknown method type: {method_model.method_type}")

        logger.debug(f"Sending test message to method: {method_model.name} ({method_model.method_type})")
        success, message = await method.test_connection()
        logger.info(f"Test result for method {method_model.name}: success={success}, message={message}")
        return {"success": success, "message": message}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error testing alert method {method_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


# =============================================================================
# Stats & Monitoring
# =============================================================================


@app.get("/api/stats/channels", tags=["Stats"])
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


@app.get("/api/stats/channels/{channel_id}", tags=["Stats"])
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


@app.get("/api/stats/activity", tags=["Stats"])
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


@app.post("/api/stats/channels/{channel_id}/stop", tags=["Stats"])
async def stop_channel(channel_id: str):
    """Stop a channel and release all associated resources."""
    client = get_client()
    try:
        return await client.stop_channel(channel_id)
    except Exception as e:
        logger.error(f"Failed to stop channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stats/channels/{channel_id}/stop-client", tags=["Stats"])
async def stop_client(channel_id: str):
    """Stop a specific client connection."""
    client = get_client()
    try:
        return await client.stop_client(channel_id)
    except Exception as e:
        logger.error(f"Failed to stop client for channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats/bandwidth", tags=["Stats"])
async def get_bandwidth_stats():
    """Get bandwidth usage summary for all time periods."""
    try:
        return BandwidthTracker.get_bandwidth_summary()
    except Exception as e:
        logger.error(f"Failed to get bandwidth stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats/top-watched", tags=["Stats"])
async def get_top_watched_channels(limit: int = 10, sort_by: str = "views"):
    """Get the top watched channels by watch count or watch time."""
    try:
        return BandwidthTracker.get_top_watched_channels(limit=limit, sort_by=sort_by)
    except Exception as e:
        logger.error(f"Failed to get top watched channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Enhanced Statistics Endpoints (v0.11.0)
# =============================================================================


@app.get("/api/stats/unique-viewers", tags=["Enhanced Stats"])
async def get_unique_viewers_summary(days: int = 7):
    """Get unique viewer statistics for the specified period."""
    try:
        return BandwidthTracker.get_unique_viewers_summary(days=days)
    except Exception as e:
        logger.error(f"Failed to get unique viewers summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats/channel-bandwidth", tags=["Enhanced Stats"])
async def get_channel_bandwidth_stats(days: int = 7, limit: int = 20, sort_by: str = "bytes"):
    """Get per-channel bandwidth statistics."""
    try:
        return BandwidthTracker.get_channel_bandwidth_stats(days=days, limit=limit, sort_by=sort_by)
    except Exception as e:
        logger.error(f"Failed to get channel bandwidth stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats/unique-viewers-by-channel", tags=["Enhanced Stats"])
async def get_unique_viewers_by_channel(days: int = 7, limit: int = 20):
    """Get unique viewer counts per channel."""
    try:
        return BandwidthTracker.get_unique_viewers_by_channel(days=days, limit=limit)
    except Exception as e:
        logger.error(f"Failed to get unique viewers by channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats/watch-history", tags=["Enhanced Stats"])
async def get_watch_history(
    page: int = 1,
    page_size: int = 50,
    channel_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    days: Optional[int] = None,
):
    """
    Get watch history log - all channel viewing sessions.

    Args:
        page: Page number (1-indexed)
        page_size: Number of records per page (max 100)
        channel_id: Filter by specific channel
        ip_address: Filter by specific IP address
        days: Filter to last N days (None = all time)
    """
    try:
        from models import UniqueClientConnection
        from sqlalchemy import func, desc
        from datetime import date, timedelta

        session = get_session()
        try:
            # Build query
            query = session.query(UniqueClientConnection)

            # Apply filters
            if channel_id:
                query = query.filter(UniqueClientConnection.channel_id == channel_id)
            if ip_address:
                query = query.filter(UniqueClientConnection.ip_address == ip_address)
            if days:
                cutoff_date = date.today() - timedelta(days=days)
                query = query.filter(UniqueClientConnection.date >= cutoff_date)

            # Get total count
            total = query.count()

            # Limit page_size
            page_size = min(page_size, 100)

            # Apply pagination and ordering (most recent first)
            offset = (page - 1) * page_size
            records = query.order_by(
                desc(UniqueClientConnection.connected_at)
            ).offset(offset).limit(page_size).all()

            # Get summary stats
            summary_query = session.query(
                func.count(func.distinct(UniqueClientConnection.channel_id)).label("unique_channels"),
                func.count(func.distinct(UniqueClientConnection.ip_address)).label("unique_ips"),
                func.sum(UniqueClientConnection.watch_seconds).label("total_watch_seconds"),
            )
            if channel_id:
                summary_query = summary_query.filter(UniqueClientConnection.channel_id == channel_id)
            if ip_address:
                summary_query = summary_query.filter(UniqueClientConnection.ip_address == ip_address)
            if days:
                summary_query = summary_query.filter(UniqueClientConnection.date >= cutoff_date)

            summary = summary_query.first()

            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if total > 0 else 1,
                "summary": {
                    "unique_channels": summary.unique_channels or 0,
                    "unique_ips": summary.unique_ips or 0,
                    "total_watch_seconds": summary.total_watch_seconds or 0,
                },
                "history": [
                    {
                        "id": r.id,
                        "channel_id": r.channel_id,
                        "channel_name": r.channel_name,
                        "ip_address": r.ip_address,
                        "date": r.date.isoformat() if r.date else None,
                        "connected_at": r.connected_at.isoformat() + "Z" if r.connected_at else None,
                        "disconnected_at": r.disconnected_at.isoformat() + "Z" if r.disconnected_at else None,
                        "watch_seconds": r.watch_seconds,
                    }
                    for r in records
                ],
            }
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to get watch history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Popularity Endpoints (v0.11.0)
# =============================================================================


@app.get("/api/stats/popularity/rankings", tags=["Popularity"])
async def get_popularity_rankings(limit: int = 50, offset: int = 0):
    """Get channel popularity rankings."""
    try:
        from popularity_calculator import PopularityCalculator
        return PopularityCalculator.get_rankings(limit=limit, offset=offset)
    except Exception as e:
        logger.error(f"Failed to get popularity rankings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats/popularity/channel/{channel_id}", tags=["Popularity"])
async def get_channel_popularity(channel_id: str):
    """Get popularity score for a specific channel."""
    try:
        from popularity_calculator import PopularityCalculator
        result = PopularityCalculator.get_channel_score(channel_id)
        if not result:
            raise HTTPException(status_code=404, detail="Channel popularity score not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get channel popularity: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats/popularity/trending", tags=["Popularity"])
async def get_trending_channels(direction: str = "up", limit: int = 10):
    """Get channels that are trending up or down."""
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be 'up' or 'down'")
    try:
        from popularity_calculator import PopularityCalculator
        return PopularityCalculator.get_trending_channels(direction=direction, limit=limit)
    except Exception as e:
        logger.error(f"Failed to get trending channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stats/popularity/calculate", tags=["Popularity"])
async def calculate_popularity_scores(
    period_days: int = 7,
):
    """
    Trigger popularity score calculation.

    Args:
        period_days: Number of days to consider for scoring
    """
    try:
        from popularity_calculator import calculate_popularity
        result = calculate_popularity(period_days=period_days)
        return result
    except Exception as e:
        logger.error(f"Failed to calculate popularity: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Stream Stats / Probing Endpoints
# =============================================================================


@app.get("/api/stream-stats", tags=["Stream Stats"])
async def get_all_stream_stats():
    """Get all stream probe statistics."""
    try:
        return StreamProber.get_all_stats()
    except Exception as e:
        logger.error(f"Failed to get stream stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stream-stats/summary", tags=["Stream Stats"])
async def get_stream_stats_summary():
    """Get summary of stream probe statistics."""
    try:
        return StreamProber.get_stats_summary()
    except Exception as e:
        logger.error(f"Failed to get stream stats summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stream-stats/{stream_id}", tags=["Stream Stats"])
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


@app.post("/api/stream-stats/by-ids", tags=["Stream Stats"])
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
    stream_ids: list[int] = []  # Optional list of specific stream IDs to probe (empty = all)


# NOTE: /probe/bulk and /probe/all MUST be defined BEFORE /probe/{stream_id}
# to avoid the path parameter matching "bulk" or "all" as a stream_id
@app.post("/api/stream-stats/probe/bulk", tags=["Stream Stats"])
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


@app.post("/api/stream-stats/probe/all", tags=["Stream Stats"])
async def probe_all_streams_endpoint(request: ProbeAllRequest = ProbeAllRequest()):
    """Trigger probe for all streams (background task).

    Optionally filter by channel groups or specific stream IDs.
    If channel_groups is empty, probes all groups.
    If stream_ids is provided, probes only those specific streams (useful for re-probing failed streams).
    """
    logger.info(f"Probe all streams request received with groups filter: {request.channel_groups}, stream_ids: {len(request.stream_ids) if request.stream_ids else 0}")

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
                skip_m3u_refresh=request.skip_m3u_refresh,
                stream_ids_filter=request.stream_ids or None
            )
            logger.info("[PROBE-TASK] Background probe task completed successfully")
        except Exception as e:
            logger.error(f"[PROBE-TASK] Background probe task failed with error: {e}", exc_info=True)

    # Start background task with optional group filter
    stream_ids_msg = f", stream_ids: {len(request.stream_ids)}" if request.stream_ids else ""
    logger.info(f"Starting background probe task (groups: {request.channel_groups or 'all'}, skip_m3u_refresh: {request.skip_m3u_refresh}{stream_ids_msg})")
    asyncio.create_task(run_probe_with_logging())
    logger.info("Background task created, returning response")
    return {"status": "started", "message": "Background probe started"}


@app.get("/api/stream-stats/probe/progress", tags=["Stream Stats"])
async def get_probe_progress():
    """Get current probe all streams progress."""
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.get_probe_progress()


@app.get("/api/stream-stats/probe/results", tags=["Stream Stats"])
async def get_probe_results():
    """Get detailed results of the last probe all streams operation."""
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.get_probe_results()


@app.get("/api/stream-stats/probe/history", tags=["Stream Stats"])
async def get_probe_history():
    """Get probe run history (last 5 runs)."""
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.get_probe_history()


@app.post("/api/stream-stats/probe/cancel", tags=["Stream Stats"])
async def cancel_probe():
    """Cancel an in-progress probe operation."""
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.cancel_probe()


@app.post("/api/stream-stats/probe/reset", tags=["Stream Stats"])
async def reset_probe_state():
    """Force reset the probe state if it gets stuck."""
    prober = get_prober()
    if not prober:
        raise HTTPException(status_code=503, detail="Stream prober not available")

    return prober.force_reset_probe_state()


class DismissStatsRequest(BaseModel):
    """Request model for dismissing stream probe stats."""
    stream_ids: list[int]


class ClearStatsRequest(BaseModel):
    """Request model for clearing stream probe stats."""
    stream_ids: list[int]


@app.post("/api/stream-stats/dismiss", tags=["Stream Stats"])
async def dismiss_stream_stats(request: DismissStatsRequest):
    """Dismiss probe failures for the specified streams.

    Marks the streams as 'dismissed' so they don't appear in failed lists.
    The dismissal is cleared automatically when the stream is re-probed.
    """
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
        logger.info(f"Dismissed {updated} stream stats for IDs: {request.stream_ids}")
        return {"dismissed": updated, "stream_ids": request.stream_ids}
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to dismiss stream stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.post("/api/stream-stats/clear", tags=["Stream Stats"])
async def clear_stream_stats(request: ClearStatsRequest):
    """Clear (delete) probe stats for the specified streams.

    Completely removes the probe history for these streams.
    They will appear as 'pending' (never probed) until re-probed.
    """
    from models import StreamStats

    if not request.stream_ids:
        raise HTTPException(status_code=400, detail="stream_ids is required")

    session = get_session()
    try:
        deleted = session.query(StreamStats).filter(
            StreamStats.stream_id.in_(request.stream_ids)
        ).delete(synchronize_session=False)
        session.commit()
        logger.info(f"Cleared {deleted} stream stats for IDs: {request.stream_ids}")
        return {"cleared": deleted, "stream_ids": request.stream_ids}
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to clear stream stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.post("/api/stream-stats/clear-all", tags=["Stream Stats"])
async def clear_all_stream_stats():
    """Clear (delete) all probe stats for all streams.

    Completely removes all probe history. All streams will appear as
    'pending' (never probed) until re-probed.
    """
    from models import StreamStats

    session = get_session()
    try:
        deleted = session.query(StreamStats).delete(synchronize_session=False)
        session.commit()
        logger.info(f"Cleared all stream stats ({deleted} records)")
        return {"cleared": deleted}
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to clear all stream stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.get("/api/stream-stats/dismissed", tags=["Stream Stats"])
async def get_dismissed_stream_stats():
    """Get list of dismissed stream IDs.

    Returns stream IDs that have been dismissed (failures acknowledged).
    Used by frontend to filter out dismissed streams from probe results display.
    """
    from models import StreamStats

    session = get_session()
    try:
        dismissed = session.query(StreamStats.stream_id).filter(
            StreamStats.dismissed_at.isnot(None)
        ).all()
        stream_ids = [s.stream_id for s in dismissed]
        return {"dismissed_stream_ids": stream_ids, "count": len(stream_ids)}
    except Exception as e:
        logger.error(f"Failed to get dismissed stream stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.post("/api/stream-stats/probe/{stream_id}", tags=["Stream Stats"])
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
    # Alert configuration
    send_alerts: Optional[bool] = None  # Master toggle for external alerts (email, etc.)
    alert_on_success: Optional[bool] = None  # Alert when task succeeds
    alert_on_warning: Optional[bool] = None  # Alert on partial failures
    alert_on_error: Optional[bool] = None  # Alert on complete failures
    alert_on_info: Optional[bool] = None  # Alert on info messages
    # Notification channels
    send_to_email: Optional[bool] = None  # Send alerts via email
    send_to_discord: Optional[bool] = None  # Send alerts via Discord
    send_to_telegram: Optional[bool] = None  # Send alerts via Telegram
    show_notifications: Optional[bool] = None  # Show in NotificationCenter (bell icon)


@app.get("/api/tasks", tags=["Tasks"])
async def list_tasks():
    """Get all registered tasks with their status, including schedules."""
    start_time = time.time()
    try:
        from task_registry import get_registry
        from models import TaskSchedule, ScheduledTask
        from schedule_calculator import describe_schedule

        registry = get_registry()
        tasks = registry.get_all_task_statuses()

        # Include schedules and alert config for each task
        session = get_session()
        try:
            for task in tasks:
                task_id = task.get('task_id')
                if task_id:
                    # Get alert configuration from ScheduledTask
                    db_task = session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()
                    if db_task:
                        task['send_alerts'] = db_task.send_alerts
                        task['alert_on_success'] = db_task.alert_on_success
                        task['alert_on_warning'] = db_task.alert_on_warning
                        task['alert_on_error'] = db_task.alert_on_error
                        task['alert_on_info'] = db_task.alert_on_info
                        task['send_to_email'] = db_task.send_to_email
                        task['send_to_discord'] = db_task.send_to_discord
                        task['send_to_telegram'] = db_task.send_to_telegram
                        task['show_notifications'] = db_task.show_notifications

                    # Get schedules
                    schedules = session.query(TaskSchedule).filter(TaskSchedule.task_id == task_id).all()
                    task['schedules'] = []
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
                        task['schedules'].append(schedule_dict)
        finally:
            session.close()

        duration_ms = (time.time() - start_time) * 1000
        running_tasks = [t.get('task_id') for t in tasks if t.get('status') == 'running']
        logger.debug(
            f"[TASKS] Listed {len(tasks)} tasks in {duration_ms:.1f}ms"
            + (f" - running: {running_tasks}" if running_tasks else "")
        )
        return {"tasks": tasks}
    except Exception as e:
        logger.error(f"[TASKS] Failed to list tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tasks/{task_id}", tags=["Tasks"])
async def get_task(task_id: str):
    """Get status for a specific task, including all schedules."""
    try:
        from task_registry import get_registry
        from models import TaskSchedule, ScheduledTask
        from schedule_calculator import describe_schedule

        registry = get_registry()
        status = registry.get_task_status(task_id)
        if status is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Include schedules and alert config in the response
        session = get_session()
        try:
            # Get alert configuration from ScheduledTask
            db_task = session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()
            if db_task:
                status['send_alerts'] = db_task.send_alerts
                status['alert_on_success'] = db_task.alert_on_success
                status['alert_on_warning'] = db_task.alert_on_warning
                status['alert_on_error'] = db_task.alert_on_error
                status['alert_on_info'] = db_task.alert_on_info
                status['send_to_email'] = db_task.send_to_email
                status['send_to_discord'] = db_task.send_to_discord
                status['send_to_telegram'] = db_task.send_to_telegram
                status['show_notifications'] = db_task.show_notifications

            # Get schedules
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


@app.patch("/api/tasks/{task_id}", tags=["Tasks"])
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
            send_alerts=config.send_alerts,
            alert_on_success=config.alert_on_success,
            alert_on_warning=config.alert_on_warning,
            alert_on_error=config.alert_on_error,
            alert_on_info=config.alert_on_info,
            send_to_email=config.send_to_email,
            send_to_discord=config.send_to_discord,
            send_to_telegram=config.send_to_telegram,
            show_notifications=config.show_notifications,
        )

        if result is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class TaskRunRequest(BaseModel):
    """Request body for running a task."""
    schedule_id: Optional[int] = None  # Run with parameters from a specific schedule


@app.post("/api/tasks/{task_id}/run", tags=["Tasks"])
async def run_task(task_id: str, request: Optional[TaskRunRequest] = None):
    """Manually trigger a task execution."""
    try:
        from task_engine import get_engine
        engine = get_engine()
        schedule_id = request.schedule_id if request else None
        result = await engine.run_task(task_id, schedule_id=schedule_id)

        if result is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        return result.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to run task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tasks/{task_id}/cancel", tags=["Tasks"])
async def cancel_task(task_id: str):
    """Cancel a running task."""
    try:
        from task_engine import get_engine
        engine = get_engine()
        result = await engine.cancel_task(task_id)
        if result.get("status") == "not_found":
            raise HTTPException(status_code=404, detail=result.get("message", f"Task {task_id} not found"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tasks/{task_id}/history", tags=["Tasks"])
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


@app.get("/api/tasks/engine/status", tags=["Tasks"])
async def get_engine_status():
    """Get task engine status."""
    try:
        from task_engine import get_engine
        engine = get_engine()
        return engine.get_status()
    except Exception as e:
        logger.error(f"Failed to get engine status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tasks/history/all", tags=["Tasks"])
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


@app.get("/api/cron/presets", tags=["Cron"])
async def get_cron_presets():
    """Get available cron presets for task scheduling."""
    try:
        from cron_parser import get_preset_list
        return {"presets": get_preset_list()}
    except Exception as e:
        logger.error(f"Failed to get cron presets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Normalization Rule request/response models
class CreateRuleGroupRequest(BaseModel):
    name: str
    description: Optional[str] = None
    enabled: bool = True
    priority: int = 0


class UpdateRuleGroupRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None


class CreateRuleRequest(BaseModel):
    group_id: int
    name: str
    description: Optional[str] = None
    enabled: bool = True
    priority: int = 0
    # Legacy single condition (optional if using compound conditions)
    condition_type: Optional[str] = None  # always, contains, starts_with, ends_with, regex, tag_group
    condition_value: Optional[str] = None
    case_sensitive: bool = False
    # Tag group condition (for condition_type='tag_group')
    tag_group_id: Optional[int] = None
    tag_match_position: Optional[str] = None  # 'prefix', 'suffix', or 'contains'
    # Compound conditions (takes precedence over legacy fields if set)
    conditions: Optional[List[dict]] = None  # [{type, value, negate, case_sensitive}]
    condition_logic: str = "AND"  # "AND" or "OR"
    # Action configuration
    action_type: str  # remove, replace, regex_replace, strip_prefix, strip_suffix, normalize_prefix
    action_value: Optional[str] = None
    # Else action (executed when condition doesn't match)
    else_action_type: Optional[str] = None
    else_action_value: Optional[str] = None
    stop_processing: bool = False


class UpdateRuleRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    # Legacy single condition
    condition_type: Optional[str] = None
    condition_value: Optional[str] = None
    case_sensitive: Optional[bool] = None
    # Tag group condition
    tag_group_id: Optional[int] = None
    tag_match_position: Optional[str] = None
    # Compound conditions
    conditions: Optional[List[dict]] = None
    condition_logic: Optional[str] = None
    # Action configuration
    action_type: Optional[str] = None
    action_value: Optional[str] = None
    # Else action
    else_action_type: Optional[str] = None
    else_action_value: Optional[str] = None
    stop_processing: Optional[bool] = None


class TestRuleRequest(BaseModel):
    text: str
    condition_type: str
    condition_value: Optional[str] = None
    case_sensitive: bool = False
    # Tag group condition
    tag_group_id: Optional[int] = None
    tag_match_position: Optional[str] = None  # 'prefix', 'suffix', or 'contains'
    # Compound conditions (takes precedence if set)
    conditions: Optional[List[dict]] = None  # [{type, value, negate, case_sensitive}]
    condition_logic: str = "AND"  # "AND" or "OR"
    action_type: str
    action_value: Optional[str] = None
    # Else action
    else_action_type: Optional[str] = None
    else_action_value: Optional[str] = None


class TestRulesBatchRequest(BaseModel):
    texts: list[str]


class ReorderRulesRequest(BaseModel):
    rule_ids: list[int]  # Rules in new priority order


class ReorderGroupsRequest(BaseModel):
    group_ids: list[int]  # Groups in new priority order


class CronValidateRequest(BaseModel):
    """Request to validate a cron expression."""
    expression: str


@app.post("/api/cron/validate", tags=["Cron"])
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
    parameters: Optional[dict] = None  # Task-specific parameters (e.g., channel_groups, batch_size)


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
    parameters: Optional[dict] = None  # Task-specific parameters


# Task parameter schemas - defines what parameters each task type accepts
# This is used by the frontend to render appropriate form fields
TASK_PARAMETER_SCHEMAS = {
    "stream_probe": {
        "description": "Stream health probing parameters",
        "parameters": [
            {
                "name": "channel_groups",
                "type": "string_array",
                "label": "Channel Groups",
                "description": "Which channel groups to probe (empty = all groups)",
                "default": [],
                "source": "channel_groups",  # Tells UI to fetch from channel groups API
            },
            {
                "name": "batch_size",
                "type": "number",
                "label": "Batch Size",
                "description": "Number of streams to probe per batch",
                "default": 10,
                "min": 1,
                "max": 100,
            },
            {
                "name": "timeout",
                "type": "number",
                "label": "Timeout (seconds)",
                "description": "Timeout per stream probe in seconds",
                "default": 30,
                "min": 5,
                "max": 300,
            },
            {
                "name": "max_concurrent",
                "type": "number",
                "label": "Max Concurrent",
                "description": "Maximum concurrent probe operations",
                "default": 3,
                "min": 1,
                "max": 20,
            },
        ],
    },
    "m3u_refresh": {
        "description": "M3U account refresh parameters",
        "parameters": [
            {
                "name": "account_ids",
                "type": "number_array",
                "label": "M3U Accounts",
                "description": "Which M3U accounts to refresh (empty = all accounts)",
                "default": [],
                "source": "m3u_accounts",  # Tells UI to fetch from M3U accounts API
            },
        ],
    },
    "epg_refresh": {
        "description": "EPG data refresh parameters",
        "parameters": [
            {
                "name": "source_ids",
                "type": "number_array",
                "label": "EPG Sources",
                "description": "Which EPG sources to refresh (empty = all sources)",
                "default": [],
                "source": "epg_sources",  # Tells UI to fetch from EPG sources API
            },
        ],
    },
    "cleanup": {
        "description": "Cleanup task parameters",
        "parameters": [
            {
                "name": "retention_days",
                "type": "number",
                "label": "Retention Days",
                "description": "Keep data for this many days (0 = use default)",
                "default": 0,
                "min": 0,
                "max": 365,
            },
        ],
    },
}


@app.get("/api/tasks/{task_id}/parameter-schema", tags=["Tasks"])
async def get_task_parameter_schema(task_id: str):
    """Get the parameter schema for a task type."""
    schema = TASK_PARAMETER_SCHEMAS.get(task_id)
    if not schema:
        # Return empty schema for tasks without special parameters
        return {"task_id": task_id, "description": "No configurable parameters", "parameters": []}
    return {"task_id": task_id, **schema}


@app.get("/api/tasks/parameter-schemas", tags=["Tasks"])
async def get_all_task_parameter_schemas():
    """Get parameter schemas for all task types."""
    return {"schemas": TASK_PARAMETER_SCHEMAS}


@app.get("/api/tasks/{task_id}/schedules", tags=["Tasks"])
async def list_task_schedules(task_id: str):
    """Get all schedules for a task."""
    try:
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


@app.post("/api/tasks/{task_id}/schedules", tags=["Tasks"])
async def create_task_schedule(task_id: str, data: TaskScheduleCreate):
    """Create a new schedule for a task."""
    try:
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

            # Set task-specific parameters if provided
            if data.parameters:
                schedule.set_parameters(data.parameters)

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


@app.patch("/api/tasks/{task_id}/schedules/{schedule_id}", tags=["Tasks"])
async def update_task_schedule(task_id: str, schedule_id: int, data: TaskScheduleUpdate):
    """Update a task schedule."""
    try:
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
            if data.parameters is not None:
                schedule.set_parameters(data.parameters)

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


@app.delete("/api/tasks/{task_id}/schedules/{schedule_id}", tags=["Tasks"])
async def delete_task_schedule(task_id: str, schedule_id: int):
    """Delete a task schedule."""
    try:
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


# =============================================================================
# Normalization Rules API
# =============================================================================

@app.get("/api/normalization/rules", tags=["Normalization"])
async def get_all_normalization_rules():
    """Get all normalization rules organized by group."""
    try:
        from normalization_engine import get_normalization_engine
        session = get_session()
        try:
            engine = get_normalization_engine(session)
            rules = engine.get_all_rules()
            return {"groups": rules}
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to get normalization rules: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/normalization/groups", tags=["Normalization"])
async def get_normalization_groups():
    """Get all normalization rule groups."""
    try:
        from models import NormalizationRuleGroup
        session = get_session()
        try:
            groups = session.query(NormalizationRuleGroup).order_by(
                NormalizationRuleGroup.priority
            ).all()
            return {"groups": [g.to_dict() for g in groups]}
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to get normalization groups: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/normalization/groups", tags=["Normalization"])
async def create_normalization_group(request: CreateRuleGroupRequest):
    """Create a new normalization rule group."""
    try:
        from models import NormalizationRuleGroup
        session = get_session()
        try:
            group = NormalizationRuleGroup(
                name=request.name,
                description=request.description,
                enabled=request.enabled,
                priority=request.priority,
                is_builtin=False
            )
            session.add(group)
            session.commit()
            session.refresh(group)
            return group.to_dict()
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to create normalization group: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/normalization/groups/{group_id}", tags=["Normalization"])
async def get_normalization_group(group_id: int):
    """Get a normalization rule group by ID."""
    try:
        from models import NormalizationRuleGroup, NormalizationRule
        session = get_session()
        try:
            group = session.query(NormalizationRuleGroup).filter(
                NormalizationRuleGroup.id == group_id
            ).first()
            if not group:
                raise HTTPException(status_code=404, detail="Group not found")

            # Include rules in response
            rules = session.query(NormalizationRule).filter(
                NormalizationRule.group_id == group_id
            ).order_by(NormalizationRule.priority).all()

            result = group.to_dict()
            result["rules"] = [r.to_dict() for r in rules]
            return result
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get normalization group {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/normalization/groups/{group_id}", tags=["Normalization"])
async def update_normalization_group(group_id: int, request: UpdateRuleGroupRequest):
    """Update a normalization rule group."""
    try:
        from models import NormalizationRuleGroup
        session = get_session()
        try:
            group = session.query(NormalizationRuleGroup).filter(
                NormalizationRuleGroup.id == group_id
            ).first()
            if not group:
                raise HTTPException(status_code=404, detail="Group not found")

            if request.name is not None:
                group.name = request.name
            if request.description is not None:
                group.description = request.description
            if request.enabled is not None:
                group.enabled = request.enabled
            if request.priority is not None:
                group.priority = request.priority

            session.commit()
            session.refresh(group)
            return group.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update normalization group {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/normalization/groups/{group_id}", tags=["Normalization"])
async def delete_normalization_group(group_id: int):
    """Delete a normalization rule group and all its rules."""
    try:
        from models import NormalizationRuleGroup, NormalizationRule
        session = get_session()
        try:
            group = session.query(NormalizationRuleGroup).filter(
                NormalizationRuleGroup.id == group_id
            ).first()
            if not group:
                raise HTTPException(status_code=404, detail="Group not found")

            # Delete all rules in this group first
            session.query(NormalizationRule).filter(
                NormalizationRule.group_id == group_id
            ).delete()

            # Delete the group
            session.delete(group)
            session.commit()
            return {"status": "deleted", "id": group_id}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete normalization group {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/normalization/groups/reorder", tags=["Normalization"])
async def reorder_normalization_groups(request: ReorderGroupsRequest):
    """Reorder normalization rule groups."""
    try:
        from models import NormalizationRuleGroup
        session = get_session()
        try:
            for priority, group_id in enumerate(request.group_ids):
                session.query(NormalizationRuleGroup).filter(
                    NormalizationRuleGroup.id == group_id
                ).update({"priority": priority})
            session.commit()
            return {"status": "reordered", "group_ids": request.group_ids}
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to reorder normalization groups: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/normalization/rules/{rule_id}", tags=["Normalization"])
async def get_normalization_rule(rule_id: int):
    """Get a normalization rule by ID."""
    try:
        from models import NormalizationRule
        session = get_session()
        try:
            rule = session.query(NormalizationRule).filter(
                NormalizationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")
            return rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get normalization rule {rule_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/normalization/rules", tags=["Normalization"])
async def create_normalization_rule(request: CreateRuleRequest):
    """Create a new normalization rule."""
    try:
        from models import NormalizationRule, NormalizationRuleGroup
        session = get_session()
        try:
            # Verify group exists
            group = session.query(NormalizationRuleGroup).filter(
                NormalizationRuleGroup.id == request.group_id
            ).first()
            if not group:
                raise HTTPException(status_code=404, detail="Group not found")

            # Serialize conditions to JSON if provided
            import json
            conditions_json = json.dumps(request.conditions) if request.conditions else None

            rule = NormalizationRule(
                group_id=request.group_id,
                name=request.name,
                description=request.description,
                enabled=request.enabled,
                priority=request.priority,
                condition_type=request.condition_type,
                condition_value=request.condition_value,
                case_sensitive=request.case_sensitive,
                tag_group_id=request.tag_group_id,
                tag_match_position=request.tag_match_position,
                conditions=conditions_json,
                condition_logic=request.condition_logic,
                action_type=request.action_type,
                action_value=request.action_value,
                else_action_type=request.else_action_type,
                else_action_value=request.else_action_value,
                stop_processing=request.stop_processing,
                is_builtin=False
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)
            return rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create normalization rule: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/normalization/rules/{rule_id}", tags=["Normalization"])
async def update_normalization_rule(rule_id: int, request: UpdateRuleRequest):
    """Update a normalization rule."""
    try:
        from models import NormalizationRule
        session = get_session()
        try:
            rule = session.query(NormalizationRule).filter(
                NormalizationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")

            import json

            if request.name is not None:
                rule.name = request.name
            if request.description is not None:
                rule.description = request.description
            if request.enabled is not None:
                rule.enabled = request.enabled
            if request.priority is not None:
                rule.priority = request.priority
            if request.condition_type is not None:
                rule.condition_type = request.condition_type
            if request.condition_value is not None:
                rule.condition_value = request.condition_value
            if request.case_sensitive is not None:
                rule.case_sensitive = request.case_sensitive
            if request.tag_group_id is not None:
                rule.tag_group_id = request.tag_group_id
            if request.tag_match_position is not None:
                rule.tag_match_position = request.tag_match_position
            if request.conditions is not None:
                rule.conditions = json.dumps(request.conditions) if request.conditions else None
            if request.condition_logic is not None:
                rule.condition_logic = request.condition_logic
            if request.action_type is not None:
                rule.action_type = request.action_type
            if request.action_value is not None:
                rule.action_value = request.action_value
            if request.else_action_type is not None:
                rule.else_action_type = request.else_action_type
            if request.else_action_value is not None:
                rule.else_action_value = request.else_action_value
            if request.stop_processing is not None:
                rule.stop_processing = request.stop_processing

            session.commit()
            session.refresh(rule)
            return rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update normalization rule {rule_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/normalization/rules/{rule_id}", tags=["Normalization"])
async def delete_normalization_rule(rule_id: int):
    """Delete a normalization rule."""
    try:
        from models import NormalizationRule
        session = get_session()
        try:
            rule = session.query(NormalizationRule).filter(
                NormalizationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")

            session.delete(rule)
            session.commit()
            return {"status": "deleted", "id": rule_id}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete normalization rule {rule_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/normalization/groups/{group_id}/rules/reorder", tags=["Normalization"])
async def reorder_normalization_rules(group_id: int, request: ReorderRulesRequest):
    """Reorder normalization rules within a group."""
    try:
        from models import NormalizationRule
        session = get_session()
        try:
            for priority, rule_id in enumerate(request.rule_ids):
                session.query(NormalizationRule).filter(
                    NormalizationRule.id == rule_id,
                    NormalizationRule.group_id == group_id
                ).update({"priority": priority})
            session.commit()
            return {"status": "reordered", "group_id": group_id, "rule_ids": request.rule_ids}
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to reorder rules in group {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/normalization/test", tags=["Normalization"])
async def test_normalization_rule(request: TestRuleRequest):
    """Test a rule configuration against sample text without saving."""
    try:
        from normalization_engine import get_normalization_engine
        session = get_session()
        try:
            engine = get_normalization_engine(session)
            result = engine.test_rule(
                text=request.text,
                condition_type=request.condition_type,
                condition_value=request.condition_value or "",
                case_sensitive=request.case_sensitive,
                action_type=request.action_type,
                action_value=request.action_value or "",
                conditions=request.conditions,
                condition_logic=request.condition_logic,
                tag_group_id=request.tag_group_id,
                tag_match_position=request.tag_match_position or "contains",
                else_action_type=request.else_action_type,
                else_action_value=request.else_action_value
            )
            return result
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to test normalization rule: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/normalization/test-batch", tags=["Normalization"])
async def test_normalization_batch(request: TestRulesBatchRequest):
    """Test all enabled rules against multiple sample texts."""
    try:
        from normalization_engine import get_normalization_engine
        session = get_session()
        try:
            engine = get_normalization_engine(session)
            results = engine.test_rules_batch(request.texts)
            return {
                "results": [
                    {
                        "original": r.original,
                        "normalized": r.normalized,
                        "rules_applied": r.rules_applied,
                        "transformations": [
                            {"rule_id": t[0], "before": t[1], "after": t[2]}
                            for t in r.transformations
                        ]
                    }
                    for r in results
                ]
            }
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to test normalization batch: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/normalization/normalize", tags=["Normalization"])
async def normalize_text(request: TestRulesBatchRequest):
    """Normalize one or more texts using all enabled rules."""
    try:
        from normalization_engine import get_normalization_engine
        session = get_session()
        try:
            engine = get_normalization_engine(session)
            results = engine.test_rules_batch(request.texts)
            return {
                "results": [
                    {"original": r.original, "normalized": r.normalized}
                    for r in results
                ]
            }
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to normalize texts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/normalization/rule-stats", tags=["Normalization"])
async def get_normalization_rule_stats(limit: int = 500):
    """Get statistics on how many streams each rule matches.

    Fetches streams from Dispatcharr and tests each enabled rule individually
    to count how many streams it would match.

    Args:
        limit: Maximum number of streams to test (default 500, max 2000)

    Returns:
        Dict with rule_stats (list of {rule_id, rule_name, group_name, match_count})
        and metadata (total_streams_tested, total_rules)
    """
    try:
        from models import NormalizationRule, NormalizationRuleGroup
        from normalization_engine import get_normalization_engine

        # Cap the limit to avoid performance issues
        limit = min(limit, 2000)

        # Fetch streams from Dispatcharr
        client = get_client()
        streams_result = await client.get_streams(page=1, page_size=limit)
        streams = streams_result.get("results", [])
        stream_names = [s.get("name", "") for s in streams if s.get("name")]

        if not stream_names:
            return {
                "rule_stats": [],
                "total_streams_tested": 0,
                "total_rules": 0
            }

        session = get_session()
        try:
            engine = get_normalization_engine(session)

            # Get all rules with their groups
            groups = session.query(NormalizationRuleGroup).order_by(
                NormalizationRuleGroup.priority
            ).all()
            group_map = {g.id: g.name for g in groups}

            rules = session.query(NormalizationRule).order_by(
                NormalizationRule.group_id,
                NormalizationRule.priority
            ).all()

            rule_stats = []
            for rule in rules:
                match_count = 0
                for name in stream_names:
                    match = engine._match_condition(name, rule)
                    if match.matched:
                        match_count += 1

                rule_stats.append({
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "group_id": rule.group_id,
                    "group_name": group_map.get(rule.group_id, "Unknown"),
                    "enabled": rule.enabled,
                    "match_count": match_count,
                    "match_percentage": round(match_count / len(stream_names) * 100, 1) if stream_names else 0
                })

            return {
                "rule_stats": rule_stats,
                "total_streams_tested": len(stream_names),
                "total_rules": len(rules)
            }
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to get rule stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/normalization/migration/status", tags=["Normalization"])
async def get_normalization_migration_status():
    """Get the status of the normalization rules migration."""
    try:
        from normalization_migration import get_migration_status
        session = get_session()
        try:
            status = get_migration_status(session)
            return status
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to get migration status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/normalization/migration/run", tags=["Normalization"])
async def run_normalization_migration(force: bool = False, migrate_settings: bool = True):
    """Create demo normalization rules.

    Creates editable demo rules that are disabled by default. Users can enable
    the rule groups they want to use.

    Args:
        force: If True, recreate rules even if they already exist
        migrate_settings: If True, also migrate user's custom_normalization_tags
    """
    try:
        from normalization_migration import create_demo_rules

        # Get user settings to migrate
        custom_normalization_tags = []

        if migrate_settings:
            settings = get_settings()
            custom_normalization_tags = settings.custom_normalization_tags or []

        session = get_session()
        try:
            result = create_demo_rules(
                session,
                force=force,
                custom_normalization_tags=custom_normalization_tags
            )
            return result
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to create demo rules: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Tag Engine API
# =============================================================================

# Tag Engine request/response models
class CreateTagGroupRequest(BaseModel):
    name: str
    description: Optional[str] = None


class UpdateTagGroupRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class CreateTagsRequest(BaseModel):
    tags: List[str]  # List of tag values to add
    case_sensitive: bool = False


class UpdateTagRequest(BaseModel):
    enabled: Optional[bool] = None
    case_sensitive: Optional[bool] = None


class TestTagsRequest(BaseModel):
    text: str
    group_id: int


@app.get("/api/tags/groups", tags=["Tags"])
async def list_tag_groups():
    """List all tag groups with tag counts."""
    try:
        from models import TagGroup, Tag
        from sqlalchemy import func
        session = get_session()
        try:
            # Get groups with tag counts
            groups = session.query(
                TagGroup,
                func.count(Tag.id).label("tag_count")
            ).outerjoin(Tag).group_by(TagGroup.id).order_by(TagGroup.name).all()

            result = []
            for group, tag_count in groups:
                group_dict = group.to_dict()
                group_dict["tag_count"] = tag_count
                result.append(group_dict)

            return {"groups": result}
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to list tag groups: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tags/groups", tags=["Tags"])
async def create_tag_group(request: CreateTagGroupRequest):
    """Create a new tag group."""
    try:
        from models import TagGroup
        session = get_session()
        try:
            # Check for duplicate name
            existing = session.query(TagGroup).filter(TagGroup.name == request.name).first()
            if existing:
                raise HTTPException(status_code=400, detail=f"Tag group '{request.name}' already exists")

            group = TagGroup(
                name=request.name,
                description=request.description,
                is_builtin=False
            )
            session.add(group)
            session.commit()
            session.refresh(group)
            logger.info(f"Created tag group: id={group.id}, name={group.name}")
            return group.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create tag group: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tags/groups/{group_id}", tags=["Tags"])
async def get_tag_group(group_id: int):
    """Get a tag group with all its tags."""
    try:
        from models import TagGroup
        session = get_session()
        try:
            group = session.query(TagGroup).filter(TagGroup.id == group_id).first()
            if not group:
                raise HTTPException(status_code=404, detail="Tag group not found")

            return group.to_dict(include_tags=True)
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get tag group {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/tags/groups/{group_id}", tags=["Tags"])
async def update_tag_group(group_id: int, request: UpdateTagGroupRequest):
    """Update a tag group name/description."""
    try:
        from models import TagGroup
        session = get_session()
        try:
            group = session.query(TagGroup).filter(TagGroup.id == group_id).first()
            if not group:
                raise HTTPException(status_code=404, detail="Tag group not found")

            # Prevent modifying built-in group name
            if group.is_builtin and request.name is not None and request.name != group.name:
                raise HTTPException(status_code=400, detail="Cannot rename built-in tag group")

            if request.name is not None:
                # Check for duplicate name
                existing = session.query(TagGroup).filter(
                    TagGroup.name == request.name,
                    TagGroup.id != group_id
                ).first()
                if existing:
                    raise HTTPException(status_code=400, detail=f"Tag group '{request.name}' already exists")
                group.name = request.name

            if request.description is not None:
                group.description = request.description

            session.commit()
            session.refresh(group)
            logger.info(f"Updated tag group: id={group.id}, name={group.name}")
            return group.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update tag group {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/tags/groups/{group_id}", tags=["Tags"])
async def delete_tag_group(group_id: int):
    """Delete a tag group and all its tags."""
    try:
        from models import TagGroup
        session = get_session()
        try:
            group = session.query(TagGroup).filter(TagGroup.id == group_id).first()
            if not group:
                raise HTTPException(status_code=404, detail="Tag group not found")

            if group.is_builtin:
                raise HTTPException(status_code=400, detail="Cannot delete built-in tag group")

            group_name = group.name
            session.delete(group)  # Cascade deletes all tags
            session.commit()
            logger.info(f"Deleted tag group: id={group_id}, name={group_name}")
            return {"status": "deleted", "id": group_id}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete tag group {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tags/groups/{group_id}/tags", tags=["Tags"])
async def add_tags_to_group(group_id: int, request: CreateTagsRequest):
    """Add one or more tags to a group."""
    try:
        from models import TagGroup, Tag
        session = get_session()
        try:
            group = session.query(TagGroup).filter(TagGroup.id == group_id).first()
            if not group:
                raise HTTPException(status_code=404, detail="Tag group not found")

            created_tags = []
            skipped_tags = []

            for tag_value in request.tags:
                tag_value = tag_value.strip()
                if not tag_value:
                    continue

                # Check if tag already exists in this group
                existing = session.query(Tag).filter(
                    Tag.group_id == group_id,
                    Tag.value == tag_value
                ).first()

                if existing:
                    skipped_tags.append(tag_value)
                    continue

                tag = Tag(
                    group_id=group_id,
                    value=tag_value,
                    case_sensitive=request.case_sensitive,
                    enabled=True,
                    is_builtin=False
                )
                session.add(tag)
                created_tags.append(tag_value)

            session.commit()
            logger.info(f"Added {len(created_tags)} tags to group {group_id}, skipped {len(skipped_tags)} duplicates")

            return {
                "created": created_tags,
                "skipped": skipped_tags,
                "group_id": group_id
            }
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add tags to group {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/tags/groups/{group_id}/tags/{tag_id}", tags=["Tags"])
async def update_tag(group_id: int, tag_id: int, request: UpdateTagRequest):
    """Update a tag's enabled or case_sensitive status."""
    try:
        from models import Tag
        session = get_session()
        try:
            tag = session.query(Tag).filter(
                Tag.id == tag_id,
                Tag.group_id == group_id
            ).first()
            if not tag:
                raise HTTPException(status_code=404, detail="Tag not found")

            if request.enabled is not None:
                tag.enabled = request.enabled
            if request.case_sensitive is not None:
                tag.case_sensitive = request.case_sensitive

            session.commit()
            session.refresh(tag)
            logger.info(f"Updated tag: id={tag.id}, value={tag.value}")
            return tag.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update tag {tag_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/tags/groups/{group_id}/tags/{tag_id}", tags=["Tags"])
async def delete_tag(group_id: int, tag_id: int):
    """Delete a tag from a group."""
    try:
        from models import Tag
        session = get_session()
        try:
            tag = session.query(Tag).filter(
                Tag.id == tag_id,
                Tag.group_id == group_id
            ).first()
            if not tag:
                raise HTTPException(status_code=404, detail="Tag not found")

            if tag.is_builtin:
                raise HTTPException(status_code=400, detail="Cannot delete built-in tag")

            tag_value = tag.value
            session.delete(tag)
            session.commit()
            logger.info(f"Deleted tag: id={tag_id}, value={tag_value}")
            return {"status": "deleted", "id": tag_id}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete tag {tag_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tags/test", tags=["Tags"])
async def test_tags(request: TestTagsRequest):
    """Test text against a tag group to find matches."""
    try:
        from models import TagGroup, Tag
        session = get_session()
        try:
            group = session.query(TagGroup).filter(TagGroup.id == request.group_id).first()
            if not group:
                raise HTTPException(status_code=404, detail="Tag group not found")

            # Get all enabled tags in the group
            tags = session.query(Tag).filter(
                Tag.group_id == request.group_id,
                Tag.enabled == True
            ).all()

            matches = []
            text = request.text

            for tag in tags:
                if tag.case_sensitive:
                    if tag.value in text:
                        matches.append({
                            "tag_id": tag.id,
                            "value": tag.value,
                            "case_sensitive": True
                        })
                else:
                    if tag.value.lower() in text.lower():
                        matches.append({
                            "tag_id": tag.id,
                            "value": tag.value,
                            "case_sensitive": False
                        })

            return {
                "text": request.text,
                "group_id": request.group_id,
                "group_name": group.name,
                "matches": matches,
                "match_count": len(matches)
            }
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to test tags: {e}")
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


# =============================================================================
# Stream Preview Proxy
# =============================================================================

async def stream_generator(process: subprocess.Popen, chunk_size: int = 65536):
    """Generator that yields chunks from FFmpeg process stdout."""
    try:
        while True:
            chunk = await asyncio.get_event_loop().run_in_executor(
                None, process.stdout.read, chunk_size
            )
            if not chunk:
                break
            yield chunk
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


@app.get("/api/stream-preview/{stream_id}", tags=["Stream Preview"])
async def stream_preview(stream_id: int):
    """
    Proxy endpoint for stream preview with optional transcoding.

    Based on stream_preview_mode setting:
    - passthrough: Direct proxy (may fail on AC-3/E-AC-3/DTS audio)
    - transcode: Transcode audio to AAC for browser compatibility
    - video_only: Strip audio for quick preview

    Returns MPEG-TS stream suitable for mpegts.js playback.
    """
    settings = get_settings()
    mode = settings.stream_preview_mode

    # Get stream URL from Dispatcharr
    client = get_client()
    if not client:
        raise HTTPException(status_code=503, detail="Not connected to Dispatcharr")

    try:
        stream = await client.get_stream(stream_id)
        if not stream or not stream.get("url"):
            raise HTTPException(status_code=404, detail="Stream not found or has no URL")
        stream_url = stream["url"]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get stream {stream_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get stream: {str(e)}")

    logger.info(f"Stream preview requested for stream {stream_id}, mode: {mode}")

    if mode == "passthrough":
        # Direct proxy - just fetch and forward
        # Use httpx to stream the content, following redirects
        async def passthrough_generator():
            async with httpx.AsyncClient(timeout=None, follow_redirects=True) as http_client:
                async with http_client.stream("GET", stream_url) as response:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        yield chunk

        return StreamingResponse(
            passthrough_generator(),
            media_type="video/mp2t",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            }
        )

    elif mode == "transcode":
        # Transcode audio to AAC for browser compatibility
        # FFmpeg: copy video, transcode audio to AAC
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "+genpts+discardcorrupt",  # Generate pts, handle corruption
            "-analyzeduration", "2000000",        # 2 seconds to analyze stream
            "-probesize", "2000000",              # 2MB probe size
            "-i", stream_url,
            "-c:v", "copy",           # Copy video as-is
            "-c:a", "aac",            # Transcode audio to AAC
            "-b:a", "192k",           # 192kbps audio bitrate
            "-ac", "2",               # Stereo output
            "-max_muxing_queue_size", "1024",     # Larger muxing buffer
            "-f", "mpegts",           # Output format
            "-"                       # Output to stdout
        ]

        try:
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=65536
            )

            return StreamingResponse(
                stream_generator(process),
                media_type="video/mp2t",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="FFmpeg not found. Please install FFmpeg for transcoding support."
            )
        except Exception as e:
            logger.error(f"FFmpeg transcode error: {e}")
            raise HTTPException(status_code=500, detail=f"Transcoding failed: {str(e)}")

    elif mode == "video_only":
        # Strip audio entirely for quick preview
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "+genpts+discardcorrupt",  # Generate pts, handle corruption
            "-analyzeduration", "2000000",        # 2 seconds to analyze stream
            "-probesize", "2000000",              # 2MB probe size
            "-i", stream_url,
            "-c:v", "copy",           # Copy video as-is
            "-an",                    # No audio
            "-max_muxing_queue_size", "1024",     # Larger muxing buffer
            "-f", "mpegts",           # Output format
            "-"                       # Output to stdout
        ]

        try:
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=65536
            )

            return StreamingResponse(
                stream_generator(process),
                media_type="video/mp2t",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="FFmpeg not found. Please install FFmpeg for video-only preview."
            )
        except Exception as e:
            logger.error(f"FFmpeg video-only error: {e}")
            raise HTTPException(status_code=500, detail=f"Video extraction failed: {str(e)}")

    else:
        raise HTTPException(status_code=400, detail=f"Invalid preview mode: {mode}")


@app.get("/api/channel-preview/{channel_id}", tags=["Stream Preview"])
async def channel_preview(channel_id: int):
    """
    Proxy endpoint for channel preview with optional transcoding.

    Previews the channel output from Dispatcharr's TS proxy. This tests the
    actual channel stream as it would be served to clients.

    Based on stream_preview_mode setting:
    - passthrough: Direct proxy (may fail on AC-3/E-AC-3/DTS audio)
    - transcode: Transcode audio to AAC for browser compatibility
    - video_only: Strip audio for quick preview

    Returns MPEG-TS stream suitable for mpegts.js playback.
    """
    settings = get_settings()
    mode = settings.stream_preview_mode

    # Get channel from Dispatcharr to get its UUID
    client = get_client()
    if not client:
        raise HTTPException(status_code=503, detail="Not connected to Dispatcharr")

    try:
        channel = await client.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")

        channel_uuid = channel.get("uuid")
        if not channel_uuid:
            raise HTTPException(status_code=404, detail="Channel has no UUID")

        # Construct Dispatcharr TS proxy URL using UUID
        dispatcharr_url = settings.url.rstrip("/")
        channel_url = f"{dispatcharr_url}/proxy/ts/stream/{channel_uuid}"

        # Get auth token for authenticated requests to Dispatcharr proxy
        await client._ensure_authenticated()
        auth_headers = {"Authorization": f"Bearer {client.access_token}"}

        logger.info(f"Channel preview: proxying Dispatcharr stream for channel {channel_id} (uuid={channel_uuid})")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get channel: {str(e)}")

    logger.info(f"Channel preview requested for channel {channel_id}, mode: {mode}")

    if mode == "passthrough":
        # Direct proxy with JWT auth - just fetch and forward
        async def passthrough_generator():
            async with httpx.AsyncClient(timeout=None, follow_redirects=True) as http_client:
                async with http_client.stream("GET", channel_url, headers=auth_headers) as response:
                    if response.status_code != 200:
                        logger.error(f"Dispatcharr proxy returned {response.status_code}")
                        return
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        yield chunk

        return StreamingResponse(
            passthrough_generator(),
            media_type="video/mp2t",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            }
        )

    elif mode == "transcode":
        # Transcode audio to AAC for browser compatibility
        # FFmpeg -headers option passes JWT auth to Dispatcharr proxy
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "+genpts+discardcorrupt",
            "-analyzeduration", "2000000",
            "-probesize", "2000000",
            "-headers", f"Authorization: Bearer {client.access_token}\r\n",
            "-i", channel_url,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ac", "2",
            "-max_muxing_queue_size", "1024",
            "-f", "mpegts",
            "-"
        ]

        try:
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=65536
            )

            return StreamingResponse(
                stream_generator(process),
                media_type="video/mp2t",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="FFmpeg not found. Please install FFmpeg for transcoding support."
            )
        except Exception as e:
            logger.error(f"FFmpeg transcode error: {e}")
            raise HTTPException(status_code=500, detail=f"Transcoding failed: {str(e)}")

    elif mode == "video_only":
        # Strip audio entirely for quick preview
        # FFmpeg -headers option passes JWT auth to Dispatcharr proxy
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "+genpts+discardcorrupt",
            "-analyzeduration", "2000000",
            "-probesize", "2000000",
            "-headers", f"Authorization: Bearer {client.access_token}\r\n",
            "-i", channel_url,
            "-c:v", "copy",
            "-an",
            "-max_muxing_queue_size", "1024",
            "-f", "mpegts",
            "-"
        ]

        try:
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=65536
            )

            return StreamingResponse(
                stream_generator(process),
                media_type="video/mp2t",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="FFmpeg not found. Please install FFmpeg for video-only preview."
            )
        except Exception as e:
            logger.error(f"FFmpeg video-only error: {e}")
            raise HTTPException(status_code=500, detail=f"Video extraction failed: {str(e)}")

    else:
        raise HTTPException(status_code=400, detail=f"Invalid preview mode: {mode}")


# =============================================================================
# Auto-Creation Pipeline API
# =============================================================================

class CreateAutoCreationRuleRequest(BaseModel):
    """Request to create an auto-creation rule."""
    name: str
    description: Optional[str] = None
    enabled: bool = True
    priority: int = 0
    m3u_account_id: Optional[int] = None
    target_group_id: Optional[int] = None
    conditions: list
    actions: list
    run_on_refresh: bool = False
    stop_on_first_match: bool = True
    sort_field: Optional[str] = None
    sort_order: str = "asc"
    probe_on_sort: bool = False
    normalize_names: bool = False
    orphan_action: str = "delete"


class UpdateAutoCreationRuleRequest(BaseModel):
    """Request to update an auto-creation rule."""
    name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    m3u_account_id: Optional[int] = None
    target_group_id: Optional[int] = None
    conditions: Optional[list] = None
    actions: Optional[list] = None
    run_on_refresh: Optional[bool] = None
    stop_on_first_match: Optional[bool] = None
    sort_field: Optional[str] = None
    sort_order: Optional[str] = None
    probe_on_sort: Optional[bool] = None
    normalize_names: Optional[bool] = None
    orphan_action: Optional[str] = None


class RunPipelineRequest(BaseModel):
    """Request to run the auto-creation pipeline."""
    dry_run: bool = False
    m3u_account_ids: Optional[List[int]] = None
    rule_ids: Optional[List[int]] = None


class ImportYAMLRequest(BaseModel):
    """Request to import rules from YAML."""
    yaml_content: str
    overwrite: bool = False


@app.get("/api/auto-creation/rules", tags=["Auto-Creation"])
async def get_auto_creation_rules():
    """Get all auto-creation rules sorted by priority."""
    try:
        from models import AutoCreationRule
        session = get_session()
        try:
            rules = session.query(AutoCreationRule).order_by(
                AutoCreationRule.priority
            ).all()
            return {"rules": [r.to_dict() for r in rules]}
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to get auto-creation rules: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/auto-creation/rules/{rule_id}", tags=["Auto-Creation"])
async def get_auto_creation_rule(rule_id: int):
    """Get a specific auto-creation rule by ID."""
    try:
        from models import AutoCreationRule
        session = get_session()
        try:
            rule = session.query(AutoCreationRule).filter(
                AutoCreationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")
            return rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get auto-creation rule {rule_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auto-creation/rules", tags=["Auto-Creation"])
async def create_auto_creation_rule(request: CreateAutoCreationRuleRequest):
    """Create a new auto-creation rule."""
    try:
        from models import AutoCreationRule
        from auto_creation_schema import validate_rule
        import json

        # Validate conditions and actions
        validation = validate_rule(request.conditions, request.actions)
        if not validation["valid"]:
            raise HTTPException(status_code=400, detail={
                "message": "Invalid rule configuration",
                "errors": validation["errors"]
            })

        session = get_session()
        try:
            rule = AutoCreationRule(
                name=request.name,
                description=request.description,
                enabled=request.enabled,
                priority=request.priority,
                m3u_account_id=request.m3u_account_id,
                target_group_id=request.target_group_id,
                conditions=json.dumps(request.conditions),
                actions=json.dumps(request.actions),
                run_on_refresh=request.run_on_refresh,
                stop_on_first_match=request.stop_on_first_match,
                sort_field=request.sort_field,
                sort_order=request.sort_order,
                probe_on_sort=request.probe_on_sort,
                normalize_names=request.normalize_names,
                orphan_action=request.orphan_action
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)

            # Log to journal
            journal.log_entry(
                category="auto_creation",
                action_type="create_rule",
                entity_id=rule.id,
                entity_name=rule.name,
                description=f"Created auto-creation rule '{rule.name}'"
            )

            return rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create auto-creation rule: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/auto-creation/rules/{rule_id}", tags=["Auto-Creation"])
async def update_auto_creation_rule(rule_id: int, request: UpdateAutoCreationRuleRequest):
    """Update an auto-creation rule."""
    try:
        from models import AutoCreationRule
        from auto_creation_schema import validate_rule
        import json

        session = get_session()
        try:
            rule = session.query(AutoCreationRule).filter(
                AutoCreationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")

            # Update fields if provided
            if request.name is not None:
                rule.name = request.name
            if request.description is not None:
                rule.description = request.description
            if request.enabled is not None:
                rule.enabled = request.enabled
            if request.priority is not None:
                rule.priority = request.priority
            if request.m3u_account_id is not None:
                rule.m3u_account_id = request.m3u_account_id
            if request.target_group_id is not None:
                rule.target_group_id = request.target_group_id
            if request.run_on_refresh is not None:
                rule.run_on_refresh = request.run_on_refresh
            if request.stop_on_first_match is not None:
                rule.stop_on_first_match = request.stop_on_first_match
            if request.sort_field is not None:
                rule.sort_field = request.sort_field or None
            if request.sort_order is not None:
                rule.sort_order = request.sort_order
            if request.probe_on_sort is not None:
                rule.probe_on_sort = request.probe_on_sort
            if request.normalize_names is not None:
                rule.normalize_names = request.normalize_names
            if request.orphan_action is not None:
                rule.orphan_action = request.orphan_action

            # Validate and update conditions/actions if provided
            conditions = request.conditions if request.conditions is not None else rule.get_conditions()
            actions = request.actions if request.actions is not None else rule.get_actions()

            validation = validate_rule(conditions, actions)
            if not validation["valid"]:
                raise HTTPException(status_code=400, detail={
                    "message": "Invalid rule configuration",
                    "errors": validation["errors"]
                })

            if request.conditions is not None:
                rule.conditions = json.dumps(request.conditions)
            if request.actions is not None:
                rule.actions = json.dumps(request.actions)

            session.commit()
            session.refresh(rule)

            # Log to journal
            journal.log_entry(
                category="auto_creation",
                action_type="update_rule",
                entity_id=rule.id,
                entity_name=rule.name,
                description=f"Updated auto-creation rule '{rule.name}'"
            )

            return rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update auto-creation rule {rule_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/auto-creation/rules/{rule_id}", tags=["Auto-Creation"])
async def delete_auto_creation_rule(rule_id: int):
    """Delete an auto-creation rule."""
    try:
        from models import AutoCreationRule
        session = get_session()
        try:
            rule = session.query(AutoCreationRule).filter(
                AutoCreationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")

            rule_name = rule.name
            session.delete(rule)
            session.commit()

            # Log to journal
            journal.log_entry(
                category="auto_creation",
                action_type="delete_rule",
                entity_id=rule_id,
                entity_name=rule_name,
                description=f"Deleted auto-creation rule '{rule_name}'"
            )

            return {"status": "deleted", "id": rule_id}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete auto-creation rule {rule_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auto-creation/rules/reorder", tags=["Auto-Creation"])
async def reorder_auto_creation_rules(rule_ids: List[int] = Body(...)):
    """Reorder auto-creation rules by setting priorities based on array order."""
    try:
        from models import AutoCreationRule
        session = get_session()
        try:
            for priority, rule_id in enumerate(rule_ids):
                rule = session.query(AutoCreationRule).filter(
                    AutoCreationRule.id == rule_id
                ).first()
                if rule:
                    rule.priority = priority
            session.commit()
            return {"status": "reordered", "rule_ids": rule_ids}
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to reorder auto-creation rules: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auto-creation/rules/{rule_id}/toggle", tags=["Auto-Creation"])
async def toggle_auto_creation_rule(rule_id: int):
    """Toggle the enabled state of an auto-creation rule."""
    try:
        from models import AutoCreationRule
        session = get_session()
        try:
            rule = session.query(AutoCreationRule).filter(
                AutoCreationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")

            rule.enabled = not rule.enabled
            session.commit()
            session.refresh(rule)

            return rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to toggle auto-creation rule {rule_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auto-creation/rules/{rule_id}/duplicate", tags=["Auto-Creation"])
async def duplicate_auto_creation_rule(rule_id: int):
    """Duplicate an auto-creation rule."""
    try:
        from models import AutoCreationRule
        session = get_session()
        try:
            rule = session.query(AutoCreationRule).filter(
                AutoCreationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")

            # Create a copy with a new name
            new_rule = AutoCreationRule(
                name=f"{rule.name} (Copy)",
                description=rule.description,
                enabled=False,  # Disabled by default
                priority=rule.priority + 1,
                m3u_account_id=rule.m3u_account_id,
                target_group_id=rule.target_group_id,
                conditions=rule.conditions,
                actions=rule.actions,
                run_on_refresh=rule.run_on_refresh,
                stop_on_first_match=rule.stop_on_first_match,
                sort_field=rule.sort_field,
                sort_order=rule.sort_order,
                normalize_names=rule.normalize_names
            )
            session.add(new_rule)
            session.commit()
            session.refresh(new_rule)

            return new_rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to duplicate auto-creation rule {rule_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Pipeline Execution Endpoints

@app.post("/api/auto-creation/run", tags=["Auto-Creation"])
async def run_auto_creation_pipeline(request: RunPipelineRequest):
    """Run the auto-creation pipeline."""
    try:
        from auto_creation_engine import get_auto_creation_engine, init_auto_creation_engine

        # Get or initialize engine
        engine = get_auto_creation_engine()
        if not engine:
            client = get_client()
            engine = await init_auto_creation_engine(client)

        result = await engine.run_pipeline(
            dry_run=request.dry_run,
            triggered_by="api",
            m3u_account_ids=request.m3u_account_ids,
            rule_ids=request.rule_ids
        )

        return result
    except Exception as e:
        logger.error(f"Failed to run auto-creation pipeline: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auto-creation/rules/{rule_id}/run", tags=["Auto-Creation"])
async def run_auto_creation_rule(rule_id: int, dry_run: bool = False):
    """Run a specific auto-creation rule."""
    try:
        from auto_creation_engine import get_auto_creation_engine, init_auto_creation_engine

        # Get or initialize engine
        engine = get_auto_creation_engine()
        if not engine:
            client = get_client()
            engine = await init_auto_creation_engine(client)

        result = await engine.run_rule(
            rule_id=rule_id,
            dry_run=dry_run,
            triggered_by="api"
        )

        return result
    except Exception as e:
        logger.error(f"Failed to run auto-creation rule {rule_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/auto-creation/executions", tags=["Auto-Creation"])
async def get_auto_creation_executions(
    limit: int = 50,
    offset: int = 0,
    rule_id: Optional[int] = None,
    status: Optional[str] = None
):
    """Get auto-creation execution history."""
    try:
        from models import AutoCreationExecution
        session = get_session()
        try:
            query = session.query(AutoCreationExecution)

            if rule_id is not None:
                query = query.filter(AutoCreationExecution.rule_id == rule_id)
            if status is not None:
                query = query.filter(AutoCreationExecution.status == status)

            total = query.count()
            executions = query.order_by(
                AutoCreationExecution.started_at.desc()
            ).offset(offset).limit(limit).all()

            return {
                "executions": [e.to_dict() for e in executions],
                "total": total,
                "limit": limit,
                "offset": offset
            }
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to get auto-creation executions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/auto-creation/executions/{execution_id}", tags=["Auto-Creation"])
async def get_auto_creation_execution(execution_id: int, include_entities: bool = False, include_log: bool = False):
    """Get details of a specific execution."""
    try:
        from models import AutoCreationExecution, AutoCreationConflict
        session = get_session()
        try:
            execution = session.query(AutoCreationExecution).filter(
                AutoCreationExecution.id == execution_id
            ).first()
            if not execution:
                raise HTTPException(status_code=404, detail="Execution not found")

            result = execution.to_dict(include_entities=include_entities, include_log=include_log)

            # Include conflicts
            conflicts = session.query(AutoCreationConflict).filter(
                AutoCreationConflict.execution_id == execution_id
            ).all()
            result["conflicts"] = [c.to_dict() for c in conflicts]

            return result
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get auto-creation execution {execution_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auto-creation/executions/{execution_id}/rollback", tags=["Auto-Creation"])
async def rollback_auto_creation_execution(execution_id: int):
    """Rollback an auto-creation execution."""
    try:
        from auto_creation_engine import get_auto_creation_engine, init_auto_creation_engine

        # Get or initialize engine
        engine = get_auto_creation_engine()
        if not engine:
            client = get_client()
            engine = await init_auto_creation_engine(client)

        result = await engine.rollback_execution(execution_id, rolled_back_by="api")

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result.get("error", "Rollback failed"))

        # Log to journal
        rule_name = result.get("rule_name", f"Execution {execution_id}")
        removed = result.get("entities_removed", 0)
        restored = result.get("entities_restored", 0)
        session = get_session()
        try:
            journal.log_entry(
                category="auto_creation",
                action_type="rollback",
                entity_id=execution_id,
                entity_name=rule_name,
                description=f"Rolled back '{rule_name}': removed {removed} channel(s), restored {restored} entit{'y' if restored == 1 else 'ies'}"
            )
        finally:
            session.close()

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to rollback auto-creation execution {execution_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# YAML Import/Export Endpoints

@app.get("/api/auto-creation/export/yaml", tags=["Auto-Creation"])
async def export_auto_creation_rules_yaml():
    """Export all auto-creation rules as YAML."""
    try:
        import yaml
        from models import AutoCreationRule
        session = get_session()
        try:
            rules = session.query(AutoCreationRule).order_by(
                AutoCreationRule.priority
            ).all()

            export_data = {
                "version": 1,
                "exported_at": datetime.utcnow().isoformat() + "Z",
                "rules": []
            }

            for rule in rules:
                export_data["rules"].append({
                    "name": rule.name,
                    "description": rule.description,
                    "enabled": rule.enabled,
                    "priority": rule.priority,
                    "m3u_account_id": rule.m3u_account_id,
                    "target_group_id": rule.target_group_id,
                    "conditions": rule.get_conditions(),
                    "actions": rule.get_actions(),
                    "run_on_refresh": rule.run_on_refresh,
                    "stop_on_first_match": rule.stop_on_first_match,
                    "sort_field": rule.sort_field,
                    "sort_order": rule.sort_order or "asc",
                    "normalize_names": rule.normalize_names or False
                })

            yaml_content = yaml.dump(export_data, default_flow_style=False, sort_keys=False)

            return PlainTextResponse(
                content=yaml_content,
                media_type="text/yaml",
                headers={
                    "Content-Disposition": "attachment; filename=auto-creation-rules.yaml"
                }
            )
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to export auto-creation rules: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auto-creation/import/yaml", tags=["Auto-Creation"])
async def import_auto_creation_rules_yaml(request: ImportYAMLRequest):
    """Import auto-creation rules from YAML."""
    try:
        import yaml
        from models import AutoCreationRule
        from auto_creation_schema import validate_rule
        import json

        # Parse YAML
        try:
            data = yaml.safe_load(request.yaml_content)
        except yaml.YAMLError as e:
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

        if not data or "rules" not in data:
            raise HTTPException(status_code=400, detail="YAML must contain a 'rules' array")

        session = get_session()
        try:
            imported = []
            errors = []

            for i, rule_data in enumerate(data["rules"]):
                # Validate rule
                conditions = rule_data.get("conditions", [])
                actions = rule_data.get("actions", [])
                validation = validate_rule(conditions, actions)

                if not validation["valid"]:
                    errors.append({
                        "rule_index": i,
                        "rule_name": rule_data.get("name", f"Rule {i}"),
                        "errors": validation["errors"]
                    })
                    continue

                # Check if rule with same name exists
                existing = session.query(AutoCreationRule).filter(
                    AutoCreationRule.name == rule_data.get("name")
                ).first()

                if existing:
                    if request.overwrite:
                        # Update existing rule
                        existing.description = rule_data.get("description")
                        existing.enabled = rule_data.get("enabled", True)
                        existing.priority = rule_data.get("priority", 0)
                        existing.m3u_account_id = rule_data.get("m3u_account_id")
                        existing.target_group_id = rule_data.get("target_group_id")
                        existing.conditions = json.dumps(conditions)
                        existing.actions = json.dumps(actions)
                        existing.run_on_refresh = rule_data.get("run_on_refresh", False)
                        existing.stop_on_first_match = rule_data.get("stop_on_first_match", True)
                        existing.sort_field = rule_data.get("sort_field")
                        existing.sort_order = rule_data.get("sort_order", "asc")
                        existing.normalize_names = rule_data.get("normalize_names", False)
                        imported.append({"name": existing.name, "action": "updated"})
                    else:
                        errors.append({
                            "rule_index": i,
                            "rule_name": rule_data.get("name"),
                            "errors": ["Rule with this name already exists"]
                        })
                        continue
                else:
                    # Create new rule
                    rule = AutoCreationRule(
                        name=rule_data.get("name", f"Imported Rule {i}"),
                        description=rule_data.get("description"),
                        enabled=rule_data.get("enabled", True),
                        priority=rule_data.get("priority", 0),
                        m3u_account_id=rule_data.get("m3u_account_id"),
                        target_group_id=rule_data.get("target_group_id"),
                        conditions=json.dumps(conditions),
                        actions=json.dumps(actions),
                        run_on_refresh=rule_data.get("run_on_refresh", False),
                        stop_on_first_match=rule_data.get("stop_on_first_match", True),
                        sort_field=rule_data.get("sort_field"),
                        sort_order=rule_data.get("sort_order", "asc"),
                        normalize_names=rule_data.get("normalize_names", False)
                    )
                    session.add(rule)
                    imported.append({"name": rule.name, "action": "created"})

            session.commit()

            # Log to journal
            if imported:
                journal.log_entry(
                    category="auto_creation",
                    action_type="import_rules",
                    entity_id=None,
                    entity_name="YAML Import",
                    description=f"Imported {len(imported)} auto-creation rules from YAML"
                )

            return {
                "success": True,
                "imported": imported,
                "errors": errors
            }
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to import auto-creation rules: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auto-creation/validate", tags=["Auto-Creation"])
async def validate_auto_creation_rule(
    conditions: list = Body(...),
    actions: list = Body(...)
):
    """Validate conditions and actions without creating a rule."""
    try:
        from auto_creation_schema import validate_rule
        result = validate_rule(conditions, actions)
        return result
    except Exception as e:
        logger.error(f"Failed to validate auto-creation rule: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/auto-creation/schema/conditions", tags=["Auto-Creation"])
async def get_auto_creation_condition_schema():
    """Get the schema for available condition types."""
    from auto_creation_schema import ConditionType

    conditions = []
    for ct in ConditionType:
        condition_info = {
            "type": ct.value,
            "category": "logical" if ct.value in ("and", "or", "not") else
                        "special" if ct.value in ("always", "never") else
                        "channel" if ct.value.startswith("channel_") or ct.value == "has_channel" else
                        "stream"
        }

        # Add value type hints
        if ct.value in ("stream_name_matches", "stream_name_contains", "stream_group_matches",
                        "tvg_id_matches", "channel_exists_with_name", "channel_exists_matching"):
            condition_info["value_type"] = "string"
            condition_info["description"] = f"Pattern to match"
        elif ct.value in ("quality_min", "quality_max"):
            condition_info["value_type"] = "integer"
            condition_info["description"] = "Resolution height (e.g., 720, 1080)"
        elif ct.value in ("tvg_id_exists", "logo_exists", "has_channel"):
            condition_info["value_type"] = "boolean"
            condition_info["description"] = "Whether the property exists"
        elif ct.value == "provider_is":
            condition_info["value_type"] = "integer|array"
            condition_info["description"] = "M3U account ID(s)"
        elif ct.value == "codec_is":
            condition_info["value_type"] = "string|array"
            condition_info["description"] = "Video codec (e.g., h264, hevc)"
        elif ct.value == "channel_in_group":
            condition_info["value_type"] = "integer"
            condition_info["description"] = "Channel group ID"
        elif ct.value in ("and", "or"):
            condition_info["value_type"] = "array"
            condition_info["description"] = "Array of sub-conditions"
        elif ct.value == "not":
            condition_info["value_type"] = "array"
            condition_info["description"] = "Single condition to negate"

        conditions.append(condition_info)

    return {"conditions": conditions}


@app.get("/api/auto-creation/schema/actions", tags=["Auto-Creation"])
async def get_auto_creation_action_schema():
    """Get the schema for available action types."""
    from auto_creation_schema import ActionType

    actions = [
        {
            "type": ActionType.CREATE_CHANNEL.value,
            "description": "Create a new channel",
            "params": {
                "name_template": {"type": "string", "default": "{stream_name}", "description": "Template for channel name"},
                "channel_number": {"type": "string|integer", "default": "auto", "description": "'auto', specific number, or 'min-max' range"},
                "group_id": {"type": "integer", "optional": True, "description": "Target channel group ID"},
                "if_exists": {"type": "string", "enum": ["skip", "merge", "update"], "default": "skip", "description": "Behavior if channel exists"}
            }
        },
        {
            "type": ActionType.CREATE_GROUP.value,
            "description": "Create a new channel group",
            "params": {
                "name_template": {"type": "string", "default": "{stream_group}", "description": "Template for group name"},
                "if_exists": {"type": "string", "enum": ["skip", "use_existing"], "default": "use_existing", "description": "Behavior if group exists"}
            }
        },
        {
            "type": ActionType.MERGE_STREAMS.value,
            "description": "Merge multiple streams into one channel",
            "params": {
                "target": {"type": "string", "enum": ["new_channel", "existing_channel", "auto"], "default": "auto"},
                "match_by": {"type": "string", "enum": ["tvg_id", "normalized_name", "stream_group"], "default": "tvg_id"},
                "find_channel_by": {"type": "string", "enum": ["name_exact", "name_regex", "tvg_id"], "optional": True},
                "find_channel_value": {"type": "string", "optional": True},
                "quality_preference": {"type": "array", "default": [1080, 720, 480], "description": "Quality order preference"},
                "max_streams": {"type": "integer", "default": 5}
            }
        },
        {
            "type": ActionType.ASSIGN_LOGO.value,
            "description": "Assign a logo to the channel",
            "params": {
                "value": {"type": "string", "description": "'from_stream' or URL"}
            }
        },
        {
            "type": ActionType.ASSIGN_TVG_ID.value,
            "description": "Assign a TVG ID (EPG ID) to the channel",
            "params": {
                "value": {"type": "string", "description": "'from_stream' or specific value"}
            }
        },
        {
            "type": ActionType.ASSIGN_EPG.value,
            "description": "Assign an EPG source to the channel",
            "params": {
                "epg_id": {"type": "integer", "description": "EPG source ID"}
            }
        },
        {
            "type": ActionType.ASSIGN_PROFILE.value,
            "description": "Assign a stream profile to the channel",
            "params": {
                "profile_id": {"type": "integer", "description": "Stream profile ID"}
            }
        },
        {
            "type": ActionType.SET_CHANNEL_NUMBER.value,
            "description": "Set the channel number",
            "params": {
                "value": {"type": "string|integer", "description": "'auto', specific number, or 'min-max' range"}
            }
        },
        {
            "type": ActionType.SKIP.value,
            "description": "Skip this stream (don't create channel)"
        },
        {
            "type": ActionType.STOP_PROCESSING.value,
            "description": "Stop processing further rules for this stream"
        },
        {
            "type": ActionType.LOG_MATCH.value,
            "description": "Log a debug message",
            "params": {
                "message": {"type": "string", "description": "Message to log (supports templates)"}
            }
        }
    ]

    return {"actions": actions}


@app.get("/api/auto-creation/schema/template-variables", tags=["Auto-Creation"])
async def get_auto_creation_template_variables():
    """Get available template variables for name templates."""
    from auto_creation_schema import TemplateVariables

    return {
        "variables": [
            {"name": "{stream_name}", "description": "Original stream name"},
            {"name": "{stream_group}", "description": "Stream's group name from M3U"},
            {"name": "{tvg_id}", "description": "Stream's EPG ID"},
            {"name": "{tvg_name}", "description": "Stream's EPG name"},
            {"name": "{quality}", "description": "Resolution as string (e.g., '1080p')"},
            {"name": "{quality_raw}", "description": "Resolution as number (e.g., 1080)"},
            {"name": "{provider}", "description": "M3U account name"},
            {"name": "{provider_id}", "description": "M3U account ID"},
            {"name": "{normalized_name}", "description": "Name after normalization rules"}
        ]
    }


# Serve static files in production
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount(
        "/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets"
    )
    # Serve downloadable scripts (VLC protocol handlers, etc.)
    scripts_dir = os.path.join(static_dir, "scripts")
    if os.path.exists(scripts_dir):
        app.mount(
            "/scripts", StaticFiles(directory=scripts_dir), name="scripts"
        )

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Serve index.html for all non-API routes (SPA routing)
        index_path = os.path.join(static_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"error": "Frontend not built"}
