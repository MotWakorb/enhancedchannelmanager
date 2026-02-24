"""
Settings router — Dispatcharr connection, preferences, and service management endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import get_settings, save_settings, clear_settings_cache, set_log_level, DispatcharrSettings
from dispatcharr_client import get_client, reset_client
from cache import get_cache
from database import get_session
from stream_prober import StreamProber, get_prober, set_prober
from bandwidth_tracker import BandwidthTracker, get_tracker, set_tracker
from services.notification_service import create_notification_internal, update_notification_internal, delete_notifications_by_source_internal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["Settings"])


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
    stream_probe_timeout: int = 30
    stream_probe_schedule_time: str = "03:00"  # HH:MM format, 24h
    bitrate_sample_duration: int = 10  # Duration in seconds to sample stream for bitrate (10, 20, or 30)
    parallel_probing_enabled: bool = True  # Probe multiple streams from different M3Us simultaneously
    max_concurrent_probes: int = 8  # Max simultaneous probes when parallel probing is enabled (1-16)
    profile_distribution_strategy: str = "fill_first"  # How to distribute probes across M3U profiles: fill_first, round_robin, least_loaded
    skip_recently_probed_hours: int = 0  # Skip streams successfully probed within last N hours (0 = always probe)
    refresh_m3us_before_probe: bool = True  # Refresh all M3U accounts before starting probe
    auto_reorder_after_probe: bool = False  # Automatically reorder streams in channels after probe completes
    probe_retry_count: int = 1  # Retries on transient ffprobe failure (0 = no retry, max 5)
    probe_retry_delay: int = 2  # Seconds between retries (1-30)
    stream_fetch_page_limit: int = 200  # Max pages when fetching streams (200 pages * 500 = 100K streams)
    stream_sort_priority: list[str] = ["resolution", "bitrate", "framerate", "m3u_priority", "audio_channels"]  # Priority order for Smart Sort
    stream_sort_enabled: dict[str, bool] = {"resolution": True, "bitrate": True, "framerate": True, "m3u_priority": False, "audio_channels": False}  # Which criteria are enabled
    m3u_account_priorities: dict[str, int] = {}  # M3U account priorities (account_id -> priority value)
    deprioritize_failed_streams: bool = True  # When enabled, failed/timeout/pending streams sort to bottom
    strike_threshold: int = 3  # Consecutive failures before flagging stream (0 = disabled)
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
    # Auto-creation pipeline exclusion settings
    auto_creation_excluded_terms: list[str] = []
    auto_creation_excluded_groups: list[str] = []
    auto_creation_exclude_auto_sync_groups: bool = False


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
    stream_probe_timeout: int
    stream_probe_schedule_time: str  # HH:MM format, 24h
    bitrate_sample_duration: int
    parallel_probing_enabled: bool  # Probe multiple streams from different M3Us simultaneously
    max_concurrent_probes: int  # Max simultaneous probes when parallel probing is enabled (1-16)
    profile_distribution_strategy: str  # How to distribute probes across M3U profiles: fill_first, round_robin, least_loaded
    skip_recently_probed_hours: int  # Skip streams successfully probed within last N hours (0 = always probe)
    refresh_m3us_before_probe: bool  # Refresh all M3U accounts before starting probe
    auto_reorder_after_probe: bool  # Automatically reorder streams in channels after probe completes
    probe_retry_count: int  # Retries on transient ffprobe failure (0 = no retry, max 5)
    probe_retry_delay: int  # Seconds between retries (1-30)
    stream_fetch_page_limit: int  # Max pages when fetching streams (200 pages * 500 = 100K streams)
    stream_sort_priority: list[str]  # Priority order for Smart Sort
    stream_sort_enabled: dict[str, bool]  # Which criteria are enabled
    m3u_account_priorities: dict[str, int]  # M3U account priorities (account_id -> priority value)
    deprioritize_failed_streams: bool  # When enabled, failed/timeout/pending streams sort to bottom
    strike_threshold: int  # Consecutive failures before flagging stream (0 = disabled)
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
    # Auto-creation pipeline exclusion settings
    auto_creation_excluded_terms: list[str]
    auto_creation_excluded_groups: list[str]
    auto_creation_exclude_auto_sync_groups: bool


class TestConnectionRequest(BaseModel):
    url: str
    username: str
    password: str


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


class DiscordTestRequest(BaseModel):
    webhook_url: str


class TelegramTestRequest(BaseModel):
    bot_token: str
    chat_id: str


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


@router.get("")
async def get_current_settings():
    """Get current settings (password masked)."""
    logger.debug("[SETTINGS] GET /api/settings")
    settings = get_settings()
    logger.debug("[SETTINGS] Settings retrieved - configured: %s, log level: %s", settings.is_configured(), settings.backend_log_level)
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
        stream_probe_timeout=settings.stream_probe_timeout,
        stream_probe_schedule_time=settings.stream_probe_schedule_time,
        bitrate_sample_duration=settings.bitrate_sample_duration,
        parallel_probing_enabled=settings.parallel_probing_enabled,
        max_concurrent_probes=settings.max_concurrent_probes,
        profile_distribution_strategy=settings.profile_distribution_strategy,
        skip_recently_probed_hours=settings.skip_recently_probed_hours,
        refresh_m3us_before_probe=settings.refresh_m3us_before_probe,
        auto_reorder_after_probe=settings.auto_reorder_after_probe,
        probe_retry_count=settings.probe_retry_count,
        probe_retry_delay=settings.probe_retry_delay,
        stream_fetch_page_limit=settings.stream_fetch_page_limit,
        stream_sort_priority=settings.stream_sort_priority,
        stream_sort_enabled=settings.stream_sort_enabled,
        m3u_account_priorities=settings.m3u_account_priorities,
        deprioritize_failed_streams=settings.deprioritize_failed_streams,
        strike_threshold=settings.strike_threshold,
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
        auto_creation_excluded_terms=settings.auto_creation_excluded_terms,
        auto_creation_excluded_groups=settings.auto_creation_excluded_groups,
        auto_creation_exclude_auto_sync_groups=settings.auto_creation_exclude_auto_sync_groups,
    )


@router.post("")
async def update_settings(request: SettingsRequest):
    """Update Dispatcharr connection settings."""
    logger.debug("[SETTINGS] POST /api/settings - URL: %s, username: %s", request.url, request.username)
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
        logger.warning("[SETTINGS] Settings update failed: password required when changing URL or username")
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
        stream_probe_timeout=request.stream_probe_timeout,
        stream_probe_schedule_time=request.stream_probe_schedule_time,
        bitrate_sample_duration=request.bitrate_sample_duration,
        parallel_probing_enabled=request.parallel_probing_enabled,
        max_concurrent_probes=request.max_concurrent_probes,
        profile_distribution_strategy=request.profile_distribution_strategy,
        skip_recently_probed_hours=request.skip_recently_probed_hours,
        refresh_m3us_before_probe=request.refresh_m3us_before_probe,
        auto_reorder_after_probe=request.auto_reorder_after_probe,
        probe_retry_count=request.probe_retry_count,
        probe_retry_delay=request.probe_retry_delay,
        stream_fetch_page_limit=request.stream_fetch_page_limit,
        stream_sort_priority=request.stream_sort_priority,
        stream_sort_enabled=request.stream_sort_enabled,
        m3u_account_priorities=request.m3u_account_priorities,
        deprioritize_failed_streams=request.deprioritize_failed_streams,
        strike_threshold=request.strike_threshold,
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
        auto_creation_excluded_terms=request.auto_creation_excluded_terms,
        auto_creation_excluded_groups=request.auto_creation_excluded_groups,
        auto_creation_exclude_auto_sync_groups=request.auto_creation_exclude_auto_sync_groups,
    )
    save_settings(new_settings)
    clear_settings_cache()
    reset_client()

    # If the Dispatcharr URL changed, invalidate all cached data from the old server
    server_changed = request.url != current_settings.url
    if server_changed:
        cache = get_cache()
        cache.clear()
        logger.info("[SETTINGS] Dispatcharr URL changed - cleared all cache entries")

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
                "[SETTINGS] Dispatcharr URL changed - cleared all server-specific data: "
                "%s M3U changes, %s snapshots, "
                "%s watch stats, %s hidden groups, "
                "%s bandwidth records, %s popularity scores, "
                "%s client connections",
                changes_deleted, snapshots_deleted,
                watch_stats_deleted, hidden_groups_deleted,
                bandwidth_deleted, popularity_deleted,
                connections_deleted
            )

    # Apply backend log level immediately
    if new_settings.backend_log_level != current_settings.backend_log_level:
        logger.info("[SETTINGS] Applying new backend log level: %s", new_settings.backend_log_level)
        set_log_level(new_settings.backend_log_level)

    # Update prober's parallel probing settings without requiring restart
    if (new_settings.parallel_probing_enabled != current_settings.parallel_probing_enabled or
            new_settings.max_concurrent_probes != current_settings.max_concurrent_probes or
            new_settings.profile_distribution_strategy != current_settings.profile_distribution_strategy):
        prober = get_prober()
        if prober:
            prober.update_probing_settings(
                new_settings.parallel_probing_enabled,
                new_settings.max_concurrent_probes,
                new_settings.profile_distribution_strategy
            )
            logger.info("[SETTINGS] Updated prober parallel probing settings from settings")

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
            logger.info("[SETTINGS] Updated prober sort settings from settings")

    logger.info("[SETTINGS] Settings saved successfully - configured: %s, auth_changed: %s, server_changed: %s", new_settings.is_configured(), auth_changed, server_changed)
    return {"status": "saved", "configured": new_settings.is_configured(), "server_changed": server_changed}


@router.post("/test")
async def test_connection(request: TestConnectionRequest):
    """Test connection to Dispatcharr with provided credentials."""
    import httpx

    logger.debug("[SETTINGS-TEST] POST /api/settings/test")
    # Validate and reconstruct URL from parsed components to prevent SSRF
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(request.url)
    if parsed.scheme not in ("http", "https"):
        return {"success": False, "message": "Invalid URL scheme - must be http or https"}
    if not parsed.hostname:
        return {"success": False, "message": "Invalid URL - no hostname provided"}
    # Reconstruct URL from validated components (scheme + netloc only)
    base_url = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            target_url = f"{base_url}/api/accounts/token/"
            response = await client.post(
                target_url,
                json={
                    "username": request.username,
                    "password": request.password,
                },
            )
            if response.status_code == 200:
                logger.info("[SETTINGS-TEST] Connection test successful - %s", parsed.hostname)
                return {"success": True, "message": "Connection successful"}
            else:
                logger.warning("[SETTINGS-TEST] Connection test failed - %s - status: %s", parsed.hostname, response.status_code)
                return {
                    "success": False,
                    "message": f"Authentication failed: {response.status_code}",
                }
    except httpx.ConnectError as e:
        logger.error("[SETTINGS-TEST] Connection test failed - could not connect to %s: %s", parsed.hostname, e)
        return {"success": False, "message": "Could not connect to server"}
    except httpx.TimeoutException as e:
        logger.error("[SETTINGS-TEST] Connection test failed - timeout connecting to %s: %s", parsed.hostname, e)
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        logger.exception("[SETTINGS-TEST] Connection test failed - unexpected error: %s", e)
        return {"success": False, "message": "Unexpected error during connection test"}


@router.post("/test-smtp")
async def test_smtp_connection(request: SMTPTestRequest):
    """Test SMTP connection by sending a test email."""
    import smtplib
    import ssl
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    logger.debug("[SETTINGS-TEST] POST /api/settings/test-smtp - host=%s:%s", request.smtp_host, request.smtp_port)

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
            logger.info("[SETTINGS-TEST] SMTP test email sent successfully to %s", request.to_email)
            return {"success": True, "message": f"Test email sent to {request.to_email}"}

        finally:
            server.quit()

    except smtplib.SMTPAuthenticationError as e:
        logger.error("[SETTINGS-TEST] SMTP test failed - authentication error: %s", e)
        return {"success": False, "message": "Authentication failed - check username and password"}
    except smtplib.SMTPConnectError as e:
        logger.error("[SETTINGS-TEST] SMTP test failed - connection error: %s", e)
        return {"success": False, "message": f"Could not connect to {request.smtp_host}:{request.smtp_port}"}
    except smtplib.SMTPRecipientsRefused as e:
        logger.error("[SETTINGS-TEST] SMTP test failed - recipient refused: %s", e)
        return {"success": False, "message": "Recipient email was refused by the server"}
    except TimeoutError:
        logger.error("[SETTINGS-TEST] SMTP test failed - timeout connecting to %s", request.smtp_host)
        return {"success": False, "message": f"Connection timed out to {request.smtp_host}:{request.smtp_port}"}
    except Exception as e:
        logger.exception("[SETTINGS-TEST] SMTP test failed - unexpected error: %s", e)
        return {"success": False, "message": "Unexpected error during SMTP test"}


@router.post("/test-discord")
async def test_discord_webhook(request: DiscordTestRequest):
    """Test Discord webhook by sending a test message."""
    import aiohttp

    webhook_url = request.webhook_url
    logger.debug("[SETTINGS-TEST] POST /api/settings/test-discord")

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
                    logger.info("[SETTINGS-TEST] Discord webhook test successful")
                    return {"success": True, "message": "Test message sent successfully"}
                elif response.status == 401:
                    return {"success": False, "message": "Invalid webhook - unauthorized"}
                elif response.status == 404:
                    return {"success": False, "message": "Webhook not found - may have been deleted"}
                elif response.status == 429:
                    return {"success": False, "message": "Rate limited - try again later"}
                else:
                    text = await response.text()
                    logger.error("[SETTINGS-TEST] Discord test failed: %s - %s", response.status, text)
                    return {"success": False, "message": f"Discord returned error: {response.status}"}

    except aiohttp.ClientError as e:
        logger.error("[SETTINGS-TEST] Discord test failed - connection error: %s", e)
        return {"success": False, "message": "Connection error during Discord test"}
    except Exception as e:
        logger.exception("[SETTINGS-TEST] Discord test failed - unexpected error: %s", e)
        return {"success": False, "message": "Unexpected error during Discord test"}


@router.post("/test-telegram")
async def test_telegram_bot(request: TelegramTestRequest):
    """Test Telegram bot by sending a test message."""
    import aiohttp

    bot_token = request.bot_token
    chat_id = request.chat_id
    logger.debug("[SETTINGS-TEST] POST /api/settings/test-telegram")

    # Validate bot token format to prevent SSRF via URL manipulation
    import re as _re
    if not bot_token or not _re.match(r'^\d+:[A-Za-z0-9_-]+$', bot_token):
        return {"success": False, "message": "Invalid bot token format"}
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
                    logger.info("[SETTINGS-TEST] Telegram bot test successful")
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
                    logger.error("[SETTINGS-TEST] Telegram test failed: %s", error_desc)
                    return {"success": False, "message": f"Telegram returned error: {error_desc}"}

    except aiohttp.ClientError as e:
        logger.error("[SETTINGS-TEST] Telegram test failed - connection error: %s", e)
        return {"success": False, "message": "Connection error during Telegram test"}
    except Exception as e:
        logger.exception("[SETTINGS-TEST] Telegram test failed - unexpected error: %s", e)
        return {"success": False, "message": "Unexpected error during Telegram test"}


@router.post("/restart-services")
async def restart_services():
    """Restart background services (bandwidth tracker and stream prober) to apply new settings."""
    logger.debug("[SETTINGS] POST /api/settings/restart-services")
    settings = get_settings()

    # Stop existing tracker
    tracker = get_tracker()
    if tracker:
        await tracker.stop()
        logger.info("[SETTINGS] Stopped existing bandwidth tracker")

    # Stop existing stream prober
    prober = get_prober()
    if prober:
        await prober.stop()
        logger.info("[SETTINGS] Stopped existing stream prober")

    # Start new tracker and prober with current settings
    if settings.is_configured():
        try:
            # Restart bandwidth tracker
            new_tracker = BandwidthTracker(get_client(), poll_interval=settings.stats_poll_interval)
            set_tracker(new_tracker)
            await new_tracker.start()
            logger.info("[SETTINGS] Restarted bandwidth tracker with %ss poll interval, timezone: %s", settings.stats_poll_interval, settings.user_timezone or 'UTC')

            # Restart stream prober (scheduled probing is controlled by Task Engine)
            new_prober = StreamProber(
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
            logger.info("[SETTINGS] Notification callbacks configured for stream prober")
            set_prober(new_prober)

            # Connect the new prober to the StreamProbeTask
            try:
                from task_registry import get_registry
                registry = get_registry()
                stream_probe_task = registry.get_task_instance("stream_probe")
                if stream_probe_task:
                    stream_probe_task.set_prober(new_prober)
                    logger.info("[SETTINGS] Connected new StreamProber to StreamProbeTask")
            except Exception as e:
                logger.warning("[SETTINGS] Failed to connect prober to task: %s", e)

            await new_prober.start()
            logger.info("[SETTINGS] Restarted stream prober with updated settings")

            return {"success": True, "message": "Services restarted with new settings"}
        except Exception as e:
            logger.exception("[SETTINGS] Failed to restart services: %s", e)
            return {"success": False, "message": "Failed to restart services"}
    else:
        return {"success": False, "message": "Settings not configured"}


@router.post("/reset-stats")
async def reset_stats():
    """Reset all channel/stream statistics. Use when switching Dispatcharr servers."""
    logger.debug("[SETTINGS] POST /api/settings/reset-stats")
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
            logger.info("[SETTINGS] Reset stats: %s hidden groups, %s watch stats, %s bandwidth, %s stream stats, %s popularity", hidden, watch, bandwidth, streams, popularity)

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
        logger.exception("[SETTINGS] Failed to reset stats: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")
