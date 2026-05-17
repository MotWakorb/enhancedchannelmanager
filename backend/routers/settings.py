"""
Settings router — Dispatcharr connection, preferences, and service management endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import logging
import re
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from urllib.parse import urlparse, urlunparse

from auth import RequireAdminIfEnabled
from config import get_settings, save_settings, clear_settings_cache, set_log_level, DispatcharrSettings
from dispatcharr_client import get_client, reset_client
from emby_client import EmbyClient, EmbyClientError
from jellyfin_client import JellyfinClient, JellyfinClientError
from plex_client import PlexClient, PlexClientError
from cache import get_cache
from database import get_session
from stream_prober import StreamProber, get_prober, set_prober
from bandwidth_tracker import BandwidthTracker, get_tracker, set_tracker
from services.notification_service import create_notification_internal, update_notification_internal, delete_notifications_by_source_internal

logger = logging.getLogger(__name__)

# Discord webhook URL prefix — accepts the canonical discord.com host, the legacy
# discordapp.com host, and the canary/ptb subdomains. Anchored to the start so
# only well-formed webhook URLs are admitted by the test-discord endpoint.
_DISCORD_WEBHOOK_RE = re.compile(
    r"^https://(discord\.com|discordapp\.com|canary\.discord\.com|ptb\.discord\.com)/api/webhooks/"
)

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
    auth_method: str = "password"  # "password" or "api_key"
    username: str = ""
    password: Optional[str] = None  # Optional - only required if changing auth settings
    # bd-jmi1c (GH #273): canonical Dispatcharr REST token field. The legacy
    # ``api_key`` field below is accepted as a back-compat alias for one
    # release. New clients should send ``dispatcharr_api_key``.
    dispatcharr_api_key: Optional[str] = None  # Optional - only required if (re)setting Dispatcharr API key
    api_key: Optional[str] = None  # DEPRECATED — legacy alias for dispatcharr_api_key. Remove in v0.19.0 (bd-jmi1c, bd-ewm4h).
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
    # bd-ugzn4 (BD-K): dedup epic operator settings. Defaults match
    # config.DispatcharrSettings so an older frontend bundle that doesn't
    # send these fields persists the current value rather than getting
    # nudged back to a hardcoded default on every save. Pydantic validator
    # on the canonical field (bd-0b6xj / BD-B in backend/config.py) clamps
    # to [CONFIDENCE_FLOOR, 1.00] per ADR-008 §D2.
    dedup_threshold: float = 0.80
    dedup_m3u_toast_suppressed: bool = False
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
    push_stream_stats_to_dispatcharr: bool = False  # Reflect probe stats back to Dispatcharr after each probe
    probe_retry_count: int = 1  # Retries on transient ffprobe failure (0 = no retry, max 5)
    probe_retry_delay: int = 2  # Seconds between retries (1-30)
    stream_fetch_page_limit: int = 200  # Max pages when fetching streams (200 pages * 500 = 100K streams)
    stream_sort_priority: list[str] = ["resolution", "bitrate", "framerate", "m3u_priority", "audio_channels"]  # Priority order for Smart Sort
    stream_sort_enabled: dict[str, bool] = {"resolution": True, "bitrate": True, "framerate": True, "m3u_priority": False, "audio_channels": False}  # Which criteria are enabled
    m3u_account_priorities: dict[str, int] = {}  # M3U account priorities (account_id -> priority value)
    black_screen_detection_enabled: bool = False  # Run ffmpeg blackdetect after successful probe
    black_screen_sample_duration: int = 5  # Seconds to sample for black screen detection (3-30)
    low_fps_threshold: int = 20  # FPS below this value is considered "low FPS" (5, 10, 15, or 20)
    deprioritize_failed_streams: bool = True  # When enabled, failed/timeout/pending streams sort to bottom
    deprioritize_black_screen: bool = True  # When disabled, black screen streams sort by quality stats
    deprioritize_low_fps: bool = True  # When disabled, low FPS streams sort by quality stats
    failed_stream_sort_order: list[str] = ["failed", "black_screen", "low_fps"]  # Order of deprioritized categories (first = sorted higher)
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
    # Frontend error telemetry toggle (ADR-006 §10, bd-i6a1m).
    # Default ON; honored by both the backend /api/client-errors endpoint
    # and the frontend clientErrorReporter.
    telemetry_client_errors_enabled: bool = True
    # Emby integration (bd-8wc6q, epic bd-2cenq). Defaults match
    # config.DispatcharrSettings so an older frontend bundle that doesn't
    # send these fields persists the current value rather than getting
    # nudged back to a hardcoded default on every save.
    emby_enabled: bool = False
    emby_base_url: str = ""
    # ``emby_api_key`` is Optional so a partial POST that omits it preserves
    # the stored value (same preserve-on-omit contract as ``smtp_password``
    # and ``mcp_api_key`` — bd-vj8n9).
    emby_api_key: Optional[str] = None
    # Plex integration (bd-r5f0c.4, epic bd-r5f0c). Mirror the Emby
    # contract: an older frontend bundle that omits these fields must
    # NOT clobber stored values to defaults, so the saver applies the
    # preserve-on-omit pattern for ``plex_token`` and the existing
    # values for the toggle + base URL when the request key is absent.
    plex_enabled: bool = False
    plex_base_url: str = ""
    # ``plex_token`` is Optional so a partial POST that omits it preserves
    # the stored value (same posture as ``emby_api_key``).
    plex_token: Optional[str] = None
    # Jellyfin integration (bd-r5f0c.4, epic bd-r5f0c). Same posture
    # as Emby + Plex.
    jellyfin_enabled: bool = False
    jellyfin_base_url: str = ""
    jellyfin_api_key: Optional[str] = None


class SettingsResponse(BaseModel):
    url: str
    auth_method: str
    username: str
    # Non-empty signal that a Dispatcharr REST API key is stored, without
    # returning the secret. bd-jmi1c (GH #273): both field names are
    # surfaced — ``dispatcharr_api_key_configured`` is canonical; the legacy
    # ``api_key_configured`` is kept for one release so older frontend
    # bundles (cached browser tabs) keep showing the indicator correctly.
    dispatcharr_api_key_configured: bool
    api_key_configured: bool  # DEPRECATED — alias for dispatcharr_api_key_configured. Remove in v0.19.0 (bd-jmi1c, bd-ewm4h).
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
    dedup_threshold: float
    dedup_m3u_toast_suppressed: bool
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
    push_stream_stats_to_dispatcharr: bool  # Reflect probe stats back to Dispatcharr after each probe
    probe_retry_count: int  # Retries on transient ffprobe failure (0 = no retry, max 5)
    probe_retry_delay: int  # Seconds between retries (1-30)
    stream_fetch_page_limit: int  # Max pages when fetching streams (200 pages * 500 = 100K streams)
    stream_sort_priority: list[str]  # Priority order for Smart Sort
    stream_sort_enabled: dict[str, bool]  # Which criteria are enabled
    m3u_account_priorities: dict[str, int]  # M3U account priorities (account_id -> priority value)
    black_screen_detection_enabled: bool  # Run ffmpeg blackdetect after successful probe
    black_screen_sample_duration: int  # Seconds to sample for black screen detection (3-30)
    low_fps_threshold: int  # FPS below this value is considered "low FPS"
    deprioritize_failed_streams: bool  # When enabled, failed/timeout/pending streams sort to bottom
    deprioritize_black_screen: bool = True  # When disabled, black screen streams sort by quality stats
    deprioritize_low_fps: bool = True  # When disabled, low FPS streams sort by quality stats
    failed_stream_sort_order: list[str]  # Order of deprioritized categories (first = sorted higher)
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
    # MCP integration
    mcp_api_key_configured: bool  # Whether an MCP API key has been generated
    # Frontend error telemetry toggle (ADR-006 §10, bd-i6a1m)
    telemetry_client_errors_enabled: bool
    # Emby integration (bd-8wc6q, epic bd-2cenq). The API key itself is NOT
    # returned — only a boolean indicator that one is stored, mirroring
    # ``dispatcharr_api_key_configured`` and ``mcp_api_key_configured``.
    emby_enabled: bool
    emby_base_url: str
    emby_api_key_configured: bool
    # Plex integration (bd-r5f0c.4, epic bd-r5f0c). The token itself is
    # NOT returned — only a boolean indicator that one is stored, mirroring
    # ``emby_api_key_configured``.
    plex_enabled: bool
    plex_base_url: str
    plex_token_configured: bool
    # Jellyfin integration (bd-r5f0c.4, epic bd-r5f0c). Same posture as
    # Emby + Plex.
    jellyfin_enabled: bool
    jellyfin_base_url: str
    jellyfin_api_key_configured: bool


class EmbyTestConnectionRequest(BaseModel):
    """Inline credentials for the Emby test-connection endpoint (bd-8wc6q).

    The operator may be testing values BEFORE saving them, so we accept the
    base URL and API key in the request body rather than reading from saved
    settings. Mirrors the Dispatcharr ``TestConnectionRequest`` shape.
    """
    base_url: str
    api_key: str


class PlexTestConnectionRequest(BaseModel):
    """Inline credentials for the Plex test-connection endpoint (bd-r5f0c.4).

    Mirrors :class:`EmbyTestConnectionRequest`. The token field is named
    ``token`` rather than ``api_key`` to match Plex ecosystem nomenclature
    operators are used to (``X-Plex-Token``).
    """
    base_url: str
    token: str


class JellyfinTestConnectionRequest(BaseModel):
    """Inline credentials for the Jellyfin test-connection endpoint (bd-r5f0c.4).

    Mirrors :class:`EmbyTestConnectionRequest` exactly — Jellyfin uses a
    server-issued API key (Dashboard > API Keys), same posture as Emby.
    """
    base_url: str
    api_key: str


class TestConnectionRequest(BaseModel):
    url: str
    auth_method: str = "password"  # "password" or "api_key"
    username: str = ""
    password: str = ""
    # bd-jmi1c (GH #273): canonical field; legacy ``api_key`` accepted below
    # for one release of back-compat. The handler reads
    # ``dispatcharr_api_key or api_key`` so either populates the X-API-Key
    # header on the connection probe.
    dispatcharr_api_key: str = ""
    api_key: str = ""  # DEPRECATED — legacy alias for dispatcharr_api_key. Remove in v0.19.0 (bd-jmi1c, bd-ewm4h).


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
        auth_method=settings.auth_method,
        username=settings.username,
        # bd-jmi1c (GH #273): both indicators reflect the same underlying
        # state — whether ECM has a Dispatcharr REST token configured. The
        # ``or`` covers the migration window where legacy is populated but
        # ``load_settings()`` hasn't yet copied it into the canonical field
        # (won't happen in practice — load always migrates — but defensive).
        # Back-compat: drop ``api_key_configured`` mirror in v0.19.0 (bd-ewm4h).
        dispatcharr_api_key_configured=bool(settings.dispatcharr_api_key or settings.api_key),
        api_key_configured=bool(settings.dispatcharr_api_key or settings.api_key),
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
        dedup_threshold=settings.dedup_threshold,
        dedup_m3u_toast_suppressed=settings.dedup_m3u_toast_suppressed,
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
        push_stream_stats_to_dispatcharr=settings.push_stream_stats_to_dispatcharr,
        probe_retry_count=settings.probe_retry_count,
        probe_retry_delay=settings.probe_retry_delay,
        stream_fetch_page_limit=settings.stream_fetch_page_limit,
        stream_sort_priority=settings.stream_sort_priority,
        stream_sort_enabled=settings.stream_sort_enabled,
        m3u_account_priorities=settings.m3u_account_priorities,
        black_screen_detection_enabled=settings.black_screen_detection_enabled,
        black_screen_sample_duration=settings.black_screen_sample_duration,
        low_fps_threshold=settings.low_fps_threshold,
        deprioritize_failed_streams=settings.deprioritize_failed_streams,
        deprioritize_black_screen=settings.deprioritize_black_screen,
        deprioritize_low_fps=settings.deprioritize_low_fps,
        failed_stream_sort_order=settings.failed_stream_sort_order,
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
        mcp_api_key_configured=bool(settings.mcp_api_key),
        telemetry_client_errors_enabled=settings.telemetry_client_errors_enabled,
        # Emby integration (bd-8wc6q). Surface the toggle + base URL so the
        # operator sees what's configured; the API key itself is masked —
        # only a boolean indicator is returned.
        emby_enabled=settings.emby_enabled,
        emby_base_url=settings.emby_base_url,
        emby_api_key_configured=bool(settings.emby_api_key),
        # Plex integration (bd-r5f0c.4). Same mask-secret posture as Emby.
        plex_enabled=settings.plex_enabled,
        plex_base_url=settings.plex_base_url,
        plex_token_configured=bool(settings.plex_token),
        # Jellyfin integration (bd-r5f0c.4). Same mask-secret posture.
        jellyfin_enabled=settings.jellyfin_enabled,
        jellyfin_base_url=settings.jellyfin_base_url,
        jellyfin_api_key_configured=bool(settings.jellyfin_api_key),
    )


@router.post("")
async def update_settings(request: SettingsRequest):
    """Update Dispatcharr connection settings."""
    logger.debug("[SETTINGS] POST /api/settings - URL: %s, username: %s", request.url, request.username)
    current_settings = get_settings()

    # If password is not provided, keep the existing password (preserve-on-omit
    # lets the UI update non-auth fields without re-asking for the secret).
    password = request.password if request.password else current_settings.password
    # bd-jmi1c (GH #273): accept either ``dispatcharr_api_key`` (canonical)
    # or legacy ``api_key`` in the request body. Canonical wins when both
    # are provided. The preserved value also prefers canonical so an older
    # frontend bundle sending only ``api_key`` doesn't unconditionally clobber
    # a freshly-rotated canonical value with the legacy mirror.
    # Back-compat: drop ``or request.api_key`` and the conflict-WARN block in v0.19.0 (bd-ewm4h).
    request_dispatcharr_key = request.dispatcharr_api_key or request.api_key
    # bd-jmi1c P1-1: warn (per request — POST is rare enough that flag-gating
    # isn't worth it) when both fields are present in the body and differ.
    # The canonical wins silently otherwise; logging only the conflict case
    # avoids spam from clients that double-send for back-compat.
    if (
        request.dispatcharr_api_key
        and request.api_key
        and request.dispatcharr_api_key != request.api_key
    ):
        logger.warning(
            "[SETTINGS] POST body has differing 'dispatcharr_api_key' and "
            "'api_key' values; using canonical 'dispatcharr_api_key' and "
            "ignoring 'api_key'. (bd-jmi1c, GH #273)"
        )
    dispatcharr_api_key = (
        request_dispatcharr_key
        if request_dispatcharr_key
        else (current_settings.dispatcharr_api_key or current_settings.api_key)
    )

    # Same for SMTP password - preserve existing if not provided
    smtp_password = request.smtp_password if request.smtp_password else current_settings.smtp_password

    # Emby API key: preserve-on-omit (bd-8wc6q). A partial POST that doesn't
    # send ``emby_api_key`` must keep the stored value — same contract as
    # ``smtp_password`` and ``mcp_api_key`` so the Settings UI can save
    # non-secret fields (toggle, base URL) without re-asking for the key.
    emby_api_key = request.emby_api_key if request.emby_api_key else current_settings.emby_api_key

    # Plex token + Jellyfin API key: same preserve-on-omit posture
    # (bd-r5f0c.4). A partial POST (e.g. older frontend bundle that doesn't
    # know about Plex / Jellyfin yet) must NOT silently clear stored secrets
    # or flip toggles back to defaults.
    plex_token = request.plex_token if request.plex_token else current_settings.plex_token
    jellyfin_api_key = (
        request.jellyfin_api_key
        if request.jellyfin_api_key
        else current_settings.jellyfin_api_key
    )

    # MCP API key is never accepted on this endpoint (it has dedicated
    # generate/revoke endpoints) — always preserve the stored value so a
    # partial POST cannot silently revoke it (bd-vj8n9).
    mcp_api_key = current_settings.mcp_api_key

    if request.auth_method not in ("password", "api_key"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid auth_method: {request.auth_method!r} (expected 'password' or 'api_key')",
        )

    mode_changed = request.auth_method != current_settings.auth_method
    # auth_changed tracks whether any credential-relevant field changed,
    # used downstream for the save-success log line. Default False so it's
    # always defined even in api_key mode.
    auth_changed = mode_changed or request.url != current_settings.url
    if request.auth_method == "api_key":
        # api_key mode: url + api_key required; ignore password entirely.
        # Require a new key when switching modes or rotating an empty key.
        # bd-jmi1c: accept either field name on the request body.
        if mode_changed and not request_dispatcharr_key:
            raise HTTPException(
                status_code=400,
                detail="API key is required when switching to API key authentication",
            )
        if not dispatcharr_api_key:
            raise HTTPException(
                status_code=400,
                detail="API key is required when auth_method is 'api_key'",
            )
    else:
        # password mode: url + username + password required. Ask for the password
        # again if url/username/mode changed, to avoid silently reusing an old one.
        auth_changed = (
            auth_changed
            or request.username != current_settings.username
        )
        if auth_changed and not request.password:
            logger.warning("[SETTINGS] Settings update failed: password required when changing auth mode, URL or username")
            raise HTTPException(
                status_code=400,
                detail="Password is required when changing auth method, URL or username",
            )

    new_settings = DispatcharrSettings(
        url=request.url,
        auth_method=request.auth_method,
        username=request.username,
        password=password,
        # bd-jmi1c (GH #273): canonical field; ``save_settings()`` mirrors
        # this value into the legacy ``api_key`` field on disk for one
        # release of back-compat with external readers.
        dispatcharr_api_key=dispatcharr_api_key,
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
        dedup_threshold=request.dedup_threshold,
        dedup_m3u_toast_suppressed=request.dedup_m3u_toast_suppressed,
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
        push_stream_stats_to_dispatcharr=request.push_stream_stats_to_dispatcharr,
        probe_retry_count=request.probe_retry_count,
        probe_retry_delay=request.probe_retry_delay,
        stream_fetch_page_limit=request.stream_fetch_page_limit,
        stream_sort_priority=request.stream_sort_priority,
        stream_sort_enabled=request.stream_sort_enabled,
        m3u_account_priorities=request.m3u_account_priorities,
        black_screen_detection_enabled=request.black_screen_detection_enabled,
        black_screen_sample_duration=request.black_screen_sample_duration,
        low_fps_threshold=request.low_fps_threshold,
        deprioritize_failed_streams=request.deprioritize_failed_streams,
        deprioritize_black_screen=request.deprioritize_black_screen,
        deprioritize_low_fps=request.deprioritize_low_fps,
        failed_stream_sort_order=request.failed_stream_sort_order,
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
        # MCP API key is preserved from current settings — see comment above
        # where mcp_api_key is captured (bd-vj8n9).
        mcp_api_key=mcp_api_key,
        telemetry_client_errors_enabled=request.telemetry_client_errors_enabled,
        # Emby integration (bd-8wc6q). emby_api_key uses the preserve-on-omit
        # pattern resolved above so a partial POST cannot silently clear the
        # stored key.
        emby_enabled=request.emby_enabled,
        emby_base_url=request.emby_base_url,
        emby_api_key=emby_api_key,
        # Plex integration (bd-r5f0c.4). plex_token preserved above.
        plex_enabled=request.plex_enabled,
        plex_base_url=request.plex_base_url,
        plex_token=plex_token,
        # Jellyfin integration (bd-r5f0c.4). jellyfin_api_key preserved above.
        jellyfin_enabled=request.jellyfin_enabled,
        jellyfin_base_url=request.jellyfin_base_url,
        jellyfin_api_key=jellyfin_api_key,
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
            ChannelBandwidth, ChannelPopularityScore, UniqueClientConnection,
            SessionTelemetry,
        )
        with get_session() as db:
            changes_deleted = db.query(M3UChangeLog).delete()
            snapshots_deleted = db.query(M3USnapshot).delete()
            # Legacy aggregate (no longer written post bd-skqln.3 step (d))
            # — kept here so reset semantics still purge any pre-cutover rows.
            watch_stats_deleted = db.query(ChannelWatchStats).delete()
            hidden_groups_deleted = db.query(HiddenChannelGroup).delete()
            bandwidth_deleted = db.query(ChannelBandwidth).delete()
            popularity_deleted = db.query(ChannelPopularityScore).delete()
            connections_deleted = db.query(UniqueClientConnection).delete()
            telemetry_deleted = db.query(SessionTelemetry).delete()
            db.commit()
            logger.info(
                "[SETTINGS] Dispatcharr URL changed - cleared all server-specific data: "
                "%s M3U changes, %s snapshots, "
                "%s watch stats, %s hidden groups, "
                "%s bandwidth records, %s popularity scores, "
                "%s client connections, %s session_telemetry rows",
                changes_deleted, snapshots_deleted,
                watch_stats_deleted, hidden_groups_deleted,
                bandwidth_deleted, popularity_deleted,
                connections_deleted, telemetry_deleted
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
            new_settings.m3u_account_priorities != current_settings.m3u_account_priorities or
            new_settings.failed_stream_sort_order != current_settings.failed_stream_sort_order or
            new_settings.deprioritize_black_screen != current_settings.deprioritize_black_screen or
            new_settings.deprioritize_low_fps != current_settings.deprioritize_low_fps):
        prober = get_prober()
        if prober:
            prober.update_sort_settings(
                new_settings.stream_sort_priority,
                new_settings.stream_sort_enabled,
                new_settings.m3u_account_priorities,
                failed_stream_sort_order=new_settings.failed_stream_sort_order,
                deprioritize_black_screen=new_settings.deprioritize_black_screen,
                deprioritize_low_fps=new_settings.deprioritize_low_fps,
            )
            logger.info("[SETTINGS] Updated prober sort settings from settings")

    # Update prober's black screen detection settings without requiring restart
    if (new_settings.black_screen_detection_enabled != current_settings.black_screen_detection_enabled or
            new_settings.black_screen_sample_duration != current_settings.black_screen_sample_duration):
        prober = get_prober()
        if prober:
            prober.black_screen_detection_enabled = new_settings.black_screen_detection_enabled
            prober.black_screen_sample_duration = max(3, min(30, new_settings.black_screen_sample_duration))
            logger.info("[SETTINGS] Updated prober black screen settings: enabled=%s, duration=%ss",
                        new_settings.black_screen_detection_enabled, new_settings.black_screen_sample_duration)

    # Update prober's low FPS threshold without requiring restart
    if new_settings.low_fps_threshold != current_settings.low_fps_threshold:
        prober = get_prober()
        if prober:
            prober.low_fps_threshold = max(1, min(60, new_settings.low_fps_threshold))
            logger.info("[SETTINGS] Updated prober low FPS threshold: %s", prober.low_fps_threshold)

    # Update remaining prober settings without requiring restart
    prober = get_prober()
    if prober:
        changed = []
        if new_settings.auto_reorder_after_probe != current_settings.auto_reorder_after_probe:
            prober.auto_reorder_after_probe = new_settings.auto_reorder_after_probe
            changed.append(f"auto_reorder_after_probe={new_settings.auto_reorder_after_probe}")
        if new_settings.stream_probe_timeout != current_settings.stream_probe_timeout:
            prober.probe_timeout = new_settings.stream_probe_timeout
            changed.append(f"probe_timeout={new_settings.stream_probe_timeout}")
        if new_settings.bitrate_sample_duration != current_settings.bitrate_sample_duration:
            prober.bitrate_sample_duration = new_settings.bitrate_sample_duration
            changed.append(f"bitrate_sample_duration={new_settings.bitrate_sample_duration}")
        if new_settings.skip_recently_probed_hours != current_settings.skip_recently_probed_hours:
            prober.skip_recently_probed_hours = new_settings.skip_recently_probed_hours
            changed.append(f"skip_recently_probed_hours={new_settings.skip_recently_probed_hours}")
        if new_settings.refresh_m3us_before_probe != current_settings.refresh_m3us_before_probe:
            prober.refresh_m3us_before_probe = new_settings.refresh_m3us_before_probe
            changed.append(f"refresh_m3us_before_probe={new_settings.refresh_m3us_before_probe}")
        if new_settings.probe_retry_count != current_settings.probe_retry_count:
            prober.probe_retry_count = max(0, min(5, new_settings.probe_retry_count))
            changed.append(f"probe_retry_count={prober.probe_retry_count}")
        if new_settings.probe_retry_delay != current_settings.probe_retry_delay:
            prober.probe_retry_delay = max(1, min(30, new_settings.probe_retry_delay))
            changed.append(f"probe_retry_delay={prober.probe_retry_delay}")
        if new_settings.deprioritize_failed_streams != current_settings.deprioritize_failed_streams:
            prober.deprioritize_failed_streams = new_settings.deprioritize_failed_streams
            changed.append(f"deprioritize_failed_streams={new_settings.deprioritize_failed_streams}")
        if new_settings.deprioritize_black_screen != current_settings.deprioritize_black_screen:
            prober.deprioritize_black_screen = new_settings.deprioritize_black_screen
            changed.append(f"deprioritize_black_screen={new_settings.deprioritize_black_screen}")
        if new_settings.deprioritize_low_fps != current_settings.deprioritize_low_fps:
            prober.deprioritize_low_fps = new_settings.deprioritize_low_fps
            changed.append(f"deprioritize_low_fps={new_settings.deprioritize_low_fps}")
        if new_settings.stream_fetch_page_limit != current_settings.stream_fetch_page_limit:
            prober.stream_fetch_page_limit = new_settings.stream_fetch_page_limit
            changed.append(f"stream_fetch_page_limit={new_settings.stream_fetch_page_limit}")
        if changed:
            logger.info("[SETTINGS] Updated prober settings: %s", ", ".join(changed))

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
            if request.auth_method == "api_key":
                # API key auth: probe /api/accounts/users/me/ with X-API-Key.
                # A 2xx means the key authenticates to an active user.
                # bd-jmi1c (GH #273): accept either field name; canonical wins.
                test_key = request.dispatcharr_api_key or request.api_key
                if not test_key:
                    return {"success": False, "message": "API key is required"}
                target_url = f"{base_url}/api/accounts/users/me/"
                response = await client.get(
                    target_url,
                    headers={"X-API-Key": test_key},
                )
                if 200 <= response.status_code < 300:
                    logger.info("[SETTINGS-TEST] API key connection test successful - %s", parsed.hostname)
                    return {"success": True, "message": "Connection successful"}
                if response.status_code == 401:
                    logger.warning("[SETTINGS-TEST] API key rejected - %s", parsed.hostname)
                    return {"success": False, "message": "Invalid API key"}
                if response.status_code == 403:
                    logger.warning("[SETTINGS-TEST] API key denied by network policy - %s", parsed.hostname)
                    return {"success": False, "message": "Dispatcharr rejected this server by network policy"}
                logger.warning("[SETTINGS-TEST] API key test failed - %s - status: %s", parsed.hostname, response.status_code)
                return {"success": False, "message": f"Authentication failed: {response.status_code}"}

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
            if response.status_code == 429:
                logger.warning("[SETTINGS-TEST] Login throttled by Dispatcharr - %s", parsed.hostname)
                return {
                    "success": False,
                    "message": "Dispatcharr is rate-limiting login (3/min per IP). Wait a minute or switch to API key auth.",
                }
            if response.status_code == 403:
                logger.warning("[SETTINGS-TEST] Login denied by network policy - %s", parsed.hostname)
                return {"success": False, "message": "Dispatcharr rejected this server by network policy"}
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
    if not _DISCORD_WEBHOOK_RE.match(webhook_url):
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


def _sanitize_base_url(raw_url: str) -> tuple[Optional[str], Optional[str]]:
    """Sanitize an operator-supplied base URL for media-server test endpoints.

    SSRF mitigation (security finding SEC-2 — bd-r5f0c.4 backfill).
    Mirrors the Dispatcharr ``/test`` endpoint pattern (routers.settings.
    test_connection, around the ``urlparse`` + scheme allowlist +
    ``urlunparse((scheme, netloc, '', '', '', ''))`` reconstruction):

    1. Reject any scheme outside {http, https}. ``file://`` /
       ``gopher://`` / ``ftp://`` / etc. let an attacker pivot the
       proxy-server request through unintended protocols (file
       exfiltration, internal protocol smuggling).
    2. Reject when no hostname is present — without a hostname the
       client would either bind to a default loopback or raise late;
       fail-closed at the entry edge instead.
    3. Reconstruct the URL from scheme + netloc ONLY, stripping any
       path / params / query / fragment the operator typed (or an
       attacker tried to embed). The downstream client builds its own
       paths off the base URL — preserving the operator's path would
       let a crafted ``http://attacker.com/legit/path?bypass`` survive
       to the HTTP probe.

    Returns:
        ``(sanitized_url, None)`` on success; ``(None, error_message)``
        on rejection. Callers route the error message into the
        ``{ok: False, error: <msg>}`` envelope so the UI surfaces an
        inline banner rather than a 500.
    """
    if not raw_url:
        return None, "Base URL is required"
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return None, "Invalid base URL — could not parse"
    if parsed.scheme.lower() not in ("http", "https"):
        return None, "Invalid URL scheme — must be http or https"
    if not parsed.hostname:
        return None, "Invalid base URL — no hostname provided"
    # Reconstruct from (scheme, netloc, path='', params='', query='',
    # fragment=''). netloc carries hostname + optional port + optional
    # userinfo — the operator's port stays attached, but everything past
    # the authority is dropped.
    sanitized = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    return sanitized, None


@router.post("/emby/test-connection")
async def test_emby_connection(
    request: EmbyTestConnectionRequest,
    _admin=RequireAdminIfEnabled,
):
    """Test connectivity to an Emby server using operator-supplied credentials.

    Wired into the Settings UI 'Test Connection' button (bd-8wc6q). The
    operator may be testing values BEFORE saving them, so this endpoint
    reads ``base_url`` + ``api_key`` from the request body — it does NOT
    read from saved settings. Admin-only because the operator is providing
    a secret on the wire (same posture as MCP key generation /
    backup-restore writes).

    Returns ``{ok: True}`` on success and ``{ok: False, error: <msg>}`` on
    any auth / network / non-2xx failure. The endpoint deliberately does
    NOT raise HTTPException on connection failure — the operator wants to
    SEE the error message inline in the UI, not get a generic 500.

    bd-r5f0c.4: SSRF mitigation via :func:`_sanitize_base_url`. Backfilled
    on this previously-unsafe endpoint at the same time the new Plex +
    Jellyfin endpoints landed with the helper from day one (security
    finding SEC-2).
    """
    logger.debug(
        "[SETTINGS-TEST] POST /api/settings/emby/test-connection - base_url=%s",
        request.base_url,
    )
    base_url, err = _sanitize_base_url(request.base_url)
    if err is not None:
        logger.info("[SETTINGS-TEST] Emby test rejected by SSRF guard: %s", err)
        return {"ok": False, "error": err}
    client = EmbyClient(base_url, request.api_key)
    try:
        # ``test_connection()`` already swallows EmbyClientError → False, but
        # we want the operator-actionable error STRING for the UI banner.
        # Calling ``get_sessions()`` directly lets us surface that string.
        await client.get_sessions()
    except EmbyClientError as exc:
        logger.info("[SETTINGS-TEST] Emby connection test failed: %s", exc)
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # pylint: disable=broad-except
        # Defensive: EmbyClient should wrap all known failures in
        # EmbyClientError, but an unexpected exception class still needs
        # to render inline rather than 500.
        logger.exception("[SETTINGS-TEST] Emby connection test unexpected error: %s", exc)
        return {"ok": False, "error": f"Unexpected error: {type(exc).__name__}"}
    finally:
        # Release the underlying httpx connection pool so the per-request
        # client doesn't leak sockets.
        await client.close()
    logger.info(
        "[SETTINGS-TEST] Emby connection test successful - base_url=%s",
        base_url,
    )
    return {"ok": True}


@router.post("/plex/test-connection")
async def test_plex_connection(
    request: PlexTestConnectionRequest,
    _admin=RequireAdminIfEnabled,
):
    """Test connectivity to a Plex server using operator-supplied credentials.

    Wired into the Settings UI 'Test Connection' button (bd-r5f0c.4). Mirrors
    :func:`test_emby_connection` exactly — operator may be testing values
    BEFORE saving so request body credentials win over saved settings;
    admin-only; inline ``{ok: False, error: <msg>}`` on failure (never a 500).

    SSRF mitigation (security finding SEC-2): scheme allowlist
    (``http`` / ``https`` only) + netloc-only URL reconstruction via
    :func:`_sanitize_base_url`. ``file://`` / ``gopher://`` / paths /
    queries / fragments are rejected or stripped before the HTTP probe.
    """
    logger.debug(
        "[SETTINGS-TEST] POST /api/settings/plex/test-connection - base_url=%s",
        request.base_url,
    )
    base_url, err = _sanitize_base_url(request.base_url)
    if err is not None:
        logger.info("[SETTINGS-TEST] Plex test rejected by SSRF guard: %s", err)
        return {"ok": False, "error": err}
    client = PlexClient(base_url, request.token)
    try:
        await client.get_sessions()
    except PlexClientError as exc:
        logger.info("[SETTINGS-TEST] Plex connection test failed: %s", exc)
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception(
            "[SETTINGS-TEST] Plex connection test unexpected error: %s", exc,
        )
        return {"ok": False, "error": f"Unexpected error: {type(exc).__name__}"}
    finally:
        await client.close()
    logger.info(
        "[SETTINGS-TEST] Plex connection test successful - base_url=%s",
        base_url,
    )
    return {"ok": True}


@router.post("/jellyfin/test-connection")
async def test_jellyfin_connection(
    request: JellyfinTestConnectionRequest,
    _admin=RequireAdminIfEnabled,
):
    """Test connectivity to a Jellyfin server using operator-supplied credentials.

    Wired into the Settings UI 'Test Connection' button (bd-r5f0c.4). Mirrors
    :func:`test_emby_connection` exactly — operator may be testing values
    BEFORE saving so request body credentials win over saved settings;
    admin-only; inline ``{ok: False, error: <msg>}`` on failure (never a 500).

    SSRF mitigation (security finding SEC-2): scheme allowlist
    (``http`` / ``https`` only) + netloc-only URL reconstruction via
    :func:`_sanitize_base_url`.
    """
    logger.debug(
        "[SETTINGS-TEST] POST /api/settings/jellyfin/test-connection - base_url=%s",
        request.base_url,
    )
    base_url, err = _sanitize_base_url(request.base_url)
    if err is not None:
        logger.info("[SETTINGS-TEST] Jellyfin test rejected by SSRF guard: %s", err)
        return {"ok": False, "error": err}
    client = JellyfinClient(base_url, request.api_key)
    try:
        await client.get_sessions()
    except JellyfinClientError as exc:
        logger.info("[SETTINGS-TEST] Jellyfin connection test failed: %s", exc)
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception(
            "[SETTINGS-TEST] Jellyfin connection test unexpected error: %s", exc,
        )
        return {"ok": False, "error": f"Unexpected error: {type(exc).__name__}"}
    finally:
        await client.close()
    logger.info(
        "[SETTINGS-TEST] Jellyfin connection test successful - base_url=%s",
        base_url,
    )
    return {"ok": True}


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
                deprioritize_black_screen=settings.deprioritize_black_screen,
                deprioritize_low_fps=settings.deprioritize_low_fps,
                black_screen_detection_enabled=settings.black_screen_detection_enabled,
                black_screen_sample_duration=settings.black_screen_sample_duration,
                low_fps_threshold=settings.low_fps_threshold,
                stream_sort_priority=settings.stream_sort_priority,
                stream_sort_enabled=settings.stream_sort_enabled,
                stream_fetch_page_limit=settings.stream_fetch_page_limit,
                m3u_account_priorities=settings.m3u_account_priorities,
                failed_stream_sort_order=settings.failed_stream_sort_order,
            )
            new_prober.set_notification_callbacks(
                create_callback=create_notification_internal,
                update_callback=update_notification_internal,
                delete_by_source_callback=delete_notifications_by_source_internal
            )
            logger.info("[SETTINGS] Notification callbacks configured for stream prober")
            set_prober(new_prober)

            # Connect the new prober to all prober-dependent tasks
            try:
                from task_registry import get_registry
                registry = get_registry()
                for tid in ("stream_probe", "failed_stream_reprobe", "black_screen_scan"):
                    task_instance = registry.get_task_instance(tid)
                    if task_instance:
                        task_instance.set_prober(new_prober)
                        logger.info("[SETTINGS] Connected new StreamProber to %s", tid)
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
    from models import (
        HiddenChannelGroup,
        ChannelWatchStats,
        ChannelBandwidth,
        StreamStats,
        ChannelPopularityScore,
        SessionTelemetry,
        UniqueClientConnection,
    )

    try:
        with get_session() as db:
            hidden = db.query(HiddenChannelGroup).delete()
            # Legacy aggregate (no longer written post bd-skqln.3 step (d))
            # — still purged so any pre-cutover rows leave with the reset.
            watch = db.query(ChannelWatchStats).delete()
            bandwidth = db.query(ChannelBandwidth).delete()
            streams = db.query(StreamStats).delete()
            popularity = db.query(ChannelPopularityScore).delete()
            connections = db.query(UniqueClientConnection).delete()
            telemetry = db.query(SessionTelemetry).delete()
            db.commit()

            total = hidden + watch + bandwidth + streams + popularity + connections + telemetry
            logger.info(
                "[SETTINGS] Reset stats: %s hidden groups, %s watch stats, "
                "%s bandwidth, %s stream stats, %s popularity, "
                "%s client connections, %s session_telemetry rows",
                hidden, watch, bandwidth, streams, popularity,
                connections, telemetry,
            )

            return {
                "success": True,
                "message": f"Cleared {total} records",
                "details": {
                    "hidden_groups": hidden,
                    "watch_stats": watch,
                    "bandwidth_records": bandwidth,
                    "stream_stats": streams,
                    "popularity_scores": popularity,
                    "client_connections": connections,
                    "session_telemetry": telemetry,
                }
            }
    except Exception as e:
        logger.exception("[SETTINGS] Failed to reset stats: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================================
# MCP API Key Management
# ============================================================================

@router.post("/mcp-api-key")
async def generate_mcp_api_key():
    """Generate a new MCP API key (replaces any existing key)."""
    settings = get_settings()
    settings.mcp_api_key = secrets.token_urlsafe(32)
    save_settings(settings)
    clear_settings_cache()
    logger.info("[SETTINGS] MCP API key generated")
    return {"mcp_api_key": settings.mcp_api_key}


@router.delete("/mcp-api-key")
async def revoke_mcp_api_key():
    """Revoke the current MCP API key."""
    settings = get_settings()
    settings.mcp_api_key = ""
    save_settings(settings)
    clear_settings_cache()
    logger.info("[SETTINGS] MCP API key revoked")
    return {"status": "revoked"}


@router.get("/mcp-status")
async def get_mcp_status():
    """Check MCP server health by calling its /health endpoint."""
    import os
    import httpx

    mcp_port = os.environ.get("MCP_PORT", "6101")
    mcp_url = f"http://localhost:{mcp_port}/health"

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(mcp_url)
            r.raise_for_status()
            return {"reachable": True, **r.json()}
    except Exception as e:  # noqa: F841 — exception class accessed via type(e)
        # CodeQL py/stack-trace-exposure (#1415, bd-m8i9q): log the full
        # exception for operator diagnosis but only return the exception
        # class to the client. ADR-005 disallows "won't fix" dismissal.
        # Trailing "return {'status': 'revoked'}" was unreachable and
        # removed (bd-kdsn3 py/unreachable-statement at original L1058).
        logger.exception("[SETTINGS] MCP health check failed")
        return {"reachable": False, "error": type(e).__name__}
