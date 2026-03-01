from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import asyncio
from fastapi.exceptions import RequestValidationError
import os
import logging
import time
from collections import defaultdict
from datetime import datetime

from dispatcharr_client import get_client
from config import (
    get_settings,
    CONFIG_DIR,
    CONFIG_FILE,
    get_log_level_from_env,
    set_log_level,
)
from database import init_db, get_session
from bandwidth_tracker import BandwidthTracker, set_tracker, get_tracker
from stream_prober import StreamProber, set_prober, get_prober
from services.notification_service import (
    create_notification_internal,
    update_notification_internal,
    delete_notifications_by_source_internal,
)
# Import alert method implementations to register their handlers (side-effect imports)
import alert_methods_discord  # noqa: F401
import alert_methods_smtp  # noqa: F401
import alert_methods_telegram  # noqa: F401

# Configure logging
# Start with environment variable, will be updated from settings in startup
initial_log_level = get_log_level_from_env()
logging.basicConfig(
    level=getattr(logging, initial_log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Sanitize all log arguments to prevent log injection (CWE-117)
from log_utils import install_safe_logging  # noqa: E402
install_safe_logging()
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
    {"name": "Enhanced Stats", "description": "Advanced analytics: unique viewers, channel bandwidth, watch history"},
    {"name": "Popularity", "description": "Channel popularity scores, rankings, and trending analysis"},
    {"name": "Stream Preview", "description": "Live stream and channel preview endpoints"},
    {"name": "Admin", "description": "User management (admin only)"},
    {"name": "FFMPEG Profiles", "description": "Save and load FFMPEG Builder profiles"},
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
All API endpoints require JWT authentication. Obtain a token via `POST /api/auth/login`
and include it as a Bearer token or session cookie. The interactive docs at `/api/docs`
handle authentication automatically when accessed through the web UI.

## Rate Limiting
No rate limiting is enforced, but rapid polling is logged for diagnostics.
    """,
    version="0.13.0",
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

# Include domain routers
from routers import all_routers
for _router in all_routers:
    app.include_router(_router)


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
            "[REQUEST] %s %s - %.1fms - status=%s - rate=%s/%ss",
            method, path, duration_ms, response.status_code,
            request_count, _rate_window_seconds
        )

        # Warn if endpoint is being hit too frequently (possible runaway loop)
        if request_count >= _rate_alert_threshold:
            logger.warning(
                "[RAPID-POLLING] %s hit %s times in %ss - possible polling issue!",
                endpoint_key, request_count, _rate_window_seconds
            )

        # Log slow requests at INFO level
        if duration_ms > 1000:
            logger.info(
                "[SLOW-REQUEST] %s %s took %.1fms",
                method, path, duration_ms
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
    logger.error("[VALIDATION-ERROR] Request path: %s", request.url.path)
    logger.error("[VALIDATION-ERROR] Request method: %s", request.method)
    logger.error("[VALIDATION-ERROR] Request headers: %s", dict(request.headers))

    # Try to read the body
    try:
        body = await request.body()
        logger.error("[VALIDATION-ERROR] Request body (raw): %s", body)
        logger.error("[VALIDATION-ERROR] Request body (decoded): %s", body.decode())
    except Exception as e:
        logger.error("[VALIDATION-ERROR] Could not read body: %s", e)

    logger.error("[VALIDATION-ERROR] Validation errors: %s", exc.errors())
    logger.error("[VALIDATION-ERROR] Validation body: %s", exc.body)

    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": str(exc.body)},
    )


@app.on_event("startup")
async def startup_event():
    """Log configuration status on startup."""
    from tls.https_server import is_https_subprocess
    _is_https_subprocess = is_https_subprocess()

    logger.info("=" * 60)
    logger.info("[MAIN] Enhanced Channel Manager starting up%s", " (HTTPS subprocess)" if _is_https_subprocess else "")
    logger.info("[MAIN] Initial log level from environment: %s", initial_log_level)

    # Initialize journal database
    init_db()

    # Remove directional suffixes from Timezone Tags (East/West affect EPG timing)
    try:
        from normalization_migration import fix_timezone_tags_remove_directional
        session = get_session()
        try:
            result = fix_timezone_tags_remove_directional(session)
            if result.get("tags_added", 0) > 0:
                logger.info("[MAIN] Added %s missing tags to Timezone Tags", result["tags_added"])
        finally:
            session.close()
    except Exception as e:
        logger.warning("[MAIN] Could not apply timezone tags fix: %s", e)

    # Ensure Provider Tags normalization rule exists for existing installations
    try:
        from normalization_migration import ensure_provider_tags_rule
        session = get_session()
        try:
            result = ensure_provider_tags_rule(session)
            if result.get("created"):
                logger.info("[MAIN] Created Provider Tags normalization rule for existing installation")
        finally:
            session.close()
    except Exception as e:
        logger.warning("[MAIN] Could not ensure Provider Tags rule: %s", e)

    logger.info("[MAIN] CONFIG_DIR: %s", CONFIG_DIR)
    logger.info("[MAIN] CONFIG_FILE: %s", CONFIG_FILE)
    logger.info("[MAIN] CONFIG_DIR exists: %s", CONFIG_DIR.exists())
    logger.info("[MAIN] CONFIG_FILE exists: %s", CONFIG_FILE.exists())

    if CONFIG_DIR.exists():
        try:
            contents = list(CONFIG_DIR.iterdir())
            logger.info("[MAIN] CONFIG_DIR contents: %s", [str(p) for p in contents])
        except Exception as e:
            logger.exception("[MAIN] Failed to list CONFIG_DIR: %s", e)

    # Load settings to log status and apply log level from settings
    settings = get_settings()
    logger.info("[MAIN] Settings configured: %s", settings.is_configured())
    if settings.url:
        logger.info("[MAIN] Dispatcharr URL: %s", settings.url)

    # Apply log level from settings (overrides environment variable)
    if settings.backend_log_level:
        set_log_level(settings.backend_log_level)
        logger.info("[MAIN] Applied log level from settings: %s", settings.backend_log_level)

    # Skip background services in HTTPS subprocess — only the main process
    # should run schedulers, probers, and trackers to avoid duplicate execution
    if _is_https_subprocess:
        logger.info("[MAIN] HTTPS subprocess: skipping background services (task engine, prober, tracker)")
        return

    # Start bandwidth tracker if configured
    if settings.is_configured():
        try:
            logger.debug("[MAIN] Starting bandwidth tracker with poll interval %ss", settings.stats_poll_interval)
            tracker = BandwidthTracker(get_client(), poll_interval=settings.stats_poll_interval)
            set_tracker(tracker)
            await tracker.start()
            logger.info("[MAIN] Bandwidth tracker started successfully")
        except Exception as e:
            logger.error("[MAIN] Failed to start bandwidth tracker: %s", e, exc_info=True)

        # Always create stream prober for on-demand probing support
        # Note: Scheduled probing is now controlled by the Task Engine (StreamProbeTask)
        try:
            logger.debug(
                "[MAIN] Initializing stream prober (timeout: %ss, max_concurrent: %s)",
                settings.stream_probe_timeout, settings.max_concurrent_probes
            )
            prober = StreamProber(
                get_client(),
                probe_timeout=settings.stream_probe_timeout,
                user_timezone=settings.user_timezone,
                bitrate_sample_duration=settings.bitrate_sample_duration,
                parallel_probing_enabled=settings.parallel_probing_enabled,
                max_concurrent_probes=settings.max_concurrent_probes,
                profile_distribution_strategy=settings.profile_distribution_strategy,
                skip_recently_probed_hours=settings.skip_recently_probed_hours,
                refresh_m3us_before_probe=settings.refresh_m3us_before_probe,
                auto_reorder_after_probe=settings.auto_reorder_after_probe,
                probe_retry_count=settings.probe_retry_count,
                probe_retry_delay=settings.probe_retry_delay,
                deprioritize_failed_streams=settings.deprioritize_failed_streams,
                black_screen_detection_enabled=settings.black_screen_detection_enabled,
                black_screen_sample_duration=settings.black_screen_sample_duration,
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
            logger.info("[MAIN] Notification callbacks configured for stream prober")
            logger.info("[MAIN] StreamProber instance created: %s", prober is not None)

            set_prober(prober)
            logger.info("[MAIN] set_prober() called successfully")

            await prober.start()
            logger.info("[MAIN] prober.start() completed")

            # Verify prober is accessible via get_prober()
            test_prober = get_prober()
            logger.info("[MAIN] Verification: get_prober() returns: %s", test_prober is not None)

            logger.info("[MAIN] Stream prober initialized (scheduled probing via Task Engine)")
        except Exception as e:
            logger.error("[MAIN] Failed to initialize stream prober: %s", e, exc_info=True)
            logger.error("[MAIN] Stream probing will not be available!")

    # Start the task execution engine
    try:
        # Import tasks module to trigger @register_task decorators
        import tasks  # noqa: F401 - imported for side effects
        logger.info("[MAIN] Task modules loaded and registered")

        # Start the task engine
        from task_engine import start_engine, get_engine
        await start_engine()
        logger.info("[MAIN] Task execution engine started")

        # Set notification callbacks on the task engine for progress updates
        engine = get_engine()
        engine.set_notification_callbacks(
            create_callback=create_notification_internal,
            update_callback=update_notification_internal,
            delete_callback=delete_notifications_by_source_internal,
        )
        logger.info("[MAIN] Task engine notification callbacks configured")

        # Connect the prober to the StreamProbeTask AFTER tasks are registered
        prober = get_prober()
        if prober:
            try:
                from task_registry import get_registry
                registry = get_registry()
                stream_probe_task = registry.get_task_instance("stream_probe")
                if stream_probe_task:
                    stream_probe_task.set_prober(prober)
                    logger.info("[MAIN] Connected StreamProber to StreamProbeTask")
                else:
                    logger.warning("[MAIN] StreamProbeTask not found in registry")

                failed_reprobe_task = registry.get_task_instance("failed_stream_reprobe")
                if failed_reprobe_task:
                    failed_reprobe_task.set_prober(prober)
                    logger.info("[MAIN] Connected StreamProber to FailedStreamReprobeTask")
                else:
                    logger.warning("[MAIN] FailedStreamReprobeTask not found in registry")
            except Exception as e:
                logger.warning("[MAIN] Failed to connect prober to task: %s", e)
    except Exception as e:
        logger.error("[MAIN] Failed to start task engine: %s", e, exc_info=True)
        logger.error("[MAIN] Scheduled tasks will not be available!")

    # Schedule a background auto-sync for channel groups in probe schedules
    # Removes stale groups and adds new groups automatically
    async def _check_stale_groups_on_startup():
        await asyncio.sleep(15)  # Wait for services to be ready
        try:
            from models import TaskSchedule as TaskScheduleModel, Notification as NotificationModel
            client = get_client()
            current_groups = await client.get_channel_groups()
            current_by_id = {g["id"]: g.get("name") for g in current_groups}

            sess = get_session()
            try:
                schedules = sess.query(TaskScheduleModel).filter(
                    TaskScheduleModel.task_id == "stream_probe"
                ).all()
                total_stale = 0
                for sched in schedules:
                    params = sched.get_parameters()
                    stored = params.get("channel_groups", [])
                    if not stored:
                        continue

                    if isinstance(stored[0], int):
                        valid = [gid for gid in stored if gid in current_by_id]
                        stale = [gid for gid in stored if gid not in current_by_id]
                    else:
                        current_by_name = {g.get("name"): g["id"] for g in current_groups}
                        valid = [current_by_name[n] for n in stored if n in current_by_name]
                        stale = [n for n in stored if n not in current_by_name]

                    # Only remove stale groups — do NOT auto-add new groups.
                    # Users control which groups to probe via the schedule editor.
                    # The auto_sync_groups parameter handles "probe all groups" when enabled.
                    if stale:
                        params["channel_groups"] = valid
                        params.pop("_stale_groups", None)
                        sched.set_parameters(params)
                        sess.add(sched)
                        total_stale += len(stale)

                        logger.info("[MAIN] Startup: auto-removed %s stale group(s) from probe schedule %s", len(stale), sched.id)

                if total_stale:
                    sess.commit()
                    logger.info("[MAIN] Startup: auto-removed %s stale group(s) from probe schedules", total_stale)

                # Clean up any stale group notifications since we auto-fix
                stale_notifs = sess.query(NotificationModel).filter(
                    NotificationModel.source_id == "stream_probe_stale_groups",
                ).all()
                for n in stale_notifs:
                    sess.delete(n)
                if stale_notifs:
                    sess.commit()
            finally:
                sess.close()
        except Exception as e:
            logger.debug("[MAIN] Stale groups startup check skipped: %s", e)

    asyncio.create_task(_check_stale_groups_on_startup())

    # Start TLS certificate renewal manager
    try:
        from tls.settings import get_tls_settings
        from tls.renewal import renewal_manager
        tls_settings = get_tls_settings()
        if tls_settings.enabled and tls_settings.mode == "letsencrypt" and tls_settings.auto_renew:
            renewal_manager.start(check_interval=86400)  # Check every 24 hours
            logger.info("[MAIN] TLS certificate renewal manager started")
        else:
            logger.info("[MAIN] TLS auto-renewal not enabled, skipping renewal manager")
    except Exception as e:
        logger.warning("[MAIN] Failed to start TLS renewal manager: %s", e)

    # Start HTTPS server if TLS is configured
    try:
        from tls.https_server import start_https_if_configured
        await start_https_if_configured()
    except Exception as e:
        logger.warning("[MAIN] Failed to start HTTPS server: %s", e)

    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on shutdown."""
    logger.info("[MAIN] Enhanced Channel Manager shutting down")

    # Stop HTTPS server
    try:
        from tls.https_server import stop_https_server
        await stop_https_server()
        logger.info("[MAIN] HTTPS server stopped")
    except Exception as e:
        logger.error("[MAIN] Error stopping HTTPS server: %s", e)

    # Stop TLS renewal manager
    try:
        from tls.renewal import renewal_manager
        renewal_manager.stop()
        logger.info("[MAIN] TLS renewal manager stopped")
    except Exception as e:
        logger.error("[MAIN] Error stopping TLS renewal manager: %s", e)

    # Stop task engine
    try:
        from task_engine import stop_engine
        await stop_engine()
        logger.info("[MAIN] Task execution engine stopped")
    except Exception as e:
        logger.error("[MAIN] Error stopping task engine: %s", e)

    # Stop bandwidth tracker
    tracker = get_tracker()
    if tracker:
        await tracker.stop()

    # Stop stream prober
    prober = get_prober()
    if prober:
        await prober.stop()


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
    # Serve bundled documentation (ffmpeg reference, etc.)
    docs_dir = os.path.join(static_dir, "docs")
    if os.path.exists(docs_dir):
        app.mount(
            "/docs", StaticFiles(directory=docs_dir, html=True), name="docs"
        )

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Serve index.html for all non-API routes (SPA routing)
        index_path = os.path.join(static_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"detail": "Frontend not built"}
