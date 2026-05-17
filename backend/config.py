from urllib.parse import urlparse

from pydantic import BaseModel, field_validator
import json
import os
import logging

# Single source of truth for the dedup confidence floor per ADR-008 §D2.
# Imported from BD-A's matcher so this validator (layer 2) cannot drift from
# the matcher's clamp (layer 1).
from services.dedup_matcher import CONFIDENCE_FLOOR
from pathlib import Path

# Set up logging
logger = logging.getLogger(__name__)

# Config file location
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
CONFIG_FILE = CONFIG_DIR / "settings.json"


ALLOWED_URL_SCHEMES = {"http", "https"}


def validate_url_scheme(url: str, field_name: str = "URL") -> None:
    """Validate that a URL uses an allowed scheme (http/https only).

    Raises HTTPException 400 if the scheme is not allowed.
    """
    from fastapi import HTTPException
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name}: only http and https URLs are allowed",
        )


class DispatcharrSettings(BaseModel):
    """User-configurable Dispatcharr connection settings."""
    url: str = ""
    # Outbound auth method for service-to-service calls:
    #   "password" — legacy flow: username + password → JWT token (subject to
    #                Dispatcharr 0.23.0+ 3/min IP-shared login throttle).
    #   "api_key"  — X-API-Key header on every request, no token refresh.
    auth_method: str = "password"
    username: str = ""
    password: str = ""
    # Personal API key generated in Dispatcharr (Account → API Keys). Stored
    # plaintext at rest, same as password. ``api_key`` is the legacy alias
    # retained for one release of back-compat (bd-jmi1c, GH #273); new code
    # MUST read ``dispatcharr_api_key`` — it is the canonical field. The
    # ``load_settings()`` migration copies the legacy value into the canonical
    # field on first read, so callers reading ``dispatcharr_api_key`` always
    # see the value regardless of which field is populated on disk.
    dispatcharr_api_key: str = ""
    # Back-compat: legacy 'api_key' field. Remove in v0.19.0 (bd-ewm4h).
    api_key: str = ""
    # Channel naming defaults
    auto_rename_channel_number: bool = False
    include_channel_number_in_name: bool = False
    channel_number_separator: str = "-"  # "-", ":", or "|"
    remove_country_prefix: bool = False
    include_country_in_name: bool = False  # Keep country prefix normalized in channel name
    country_separator: str = "|"  # Separator for country prefix: "-", ":", or "|"
    # Timezone preference: "east", "west", or "both"
    timezone_preference: str = "both"
    # Appearance settings
    show_stream_urls: bool = True  # Show stream URLs in the UI (can hide for screenshots)
    hide_auto_sync_groups: bool = False  # Hide auto-sync channel groups by default
    hide_ungrouped_streams: bool = True  # Hide ungrouped streams in the streams pane
    hide_epg_urls: bool = False  # Hide EPG URLs in EPG Manager tab
    hide_m3u_urls: bool = False  # Hide M3U URLs in M3U Manager tab
    gracenote_conflict_mode: str = "ask"  # Gracenote ID conflict handling: "ask", "skip", or "overwrite"
    theme: str = "dark"  # Theme: "dark", "light", or "high-contrast"
    # Default channel profiles for new channels (empty list means no defaults)
    default_channel_profile_ids: list[int] = []
    # Linked M3U accounts - groups of account IDs that should sync group settings
    # Each inner list is a group of linked account IDs, e.g. [[1, 2], [3, 4, 5]]
    linked_m3u_accounts: list[list[int]] = []
    # EPG auto-match confidence threshold (0-100)
    # Matches with confidence >= this value are considered "auto-matched"
    # Set to 0 to disable auto-matching (all matches need review)
    # Set to 100 to require perfect confidence for auto-match
    epg_auto_match_threshold: int = 80
    # Custom network prefixes to strip during bulk channel creation
    # These are merged with the built-in list (CHAMP, PPV, NFL, etc.)
    custom_network_prefixes: list[str] = []
    # Custom network suffixes to strip during bulk channel creation
    # These are merged with the built-in list (ENGLISH, LIVE, BACKUP, etc.)
    custom_network_suffixes: list[str] = []
    # Stats polling interval in seconds (how often to check Dispatcharr for channel stats)
    stats_poll_interval: int = 10
    # User timezone for stats display (IANA timezone name, e.g. "America/Los_Angeles")
    # Empty string means use UTC
    user_timezone: str = ""
    # Backend log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
    backend_log_level: str = "INFO"
    # Frontend log level: DEBUG, INFO, WARN, ERROR
    frontend_log_level: str = "INFO"
    # VLC open behavior: "protocol_only", "m3u_fallback", or "m3u_only"
    # protocol_only: Try vlc:// protocol, show helper modal if it fails
    # m3u_fallback: Try vlc:// protocol, download M3U if it fails (current default)
    # m3u_only: Always download M3U file without trying protocol
    vlc_open_behavior: str = "m3u_fallback"
    # Stream probe settings - uses ffprobe to gather stream metadata
    # Note: Scheduled probing is now controlled by the Task Engine (StreamProbeTask)
    stream_probe_timeout: int = 30  # Timeout in seconds for each probe
    stream_probe_schedule_time: str = "03:00"  # Time of day to run probes (HH:MM, 24h format, user's local time)
    bitrate_sample_duration: int = 10  # Duration in seconds to sample stream for bitrate measurement (10, 20, or 30)
    # Parallel probing - probe streams from different M3U accounts simultaneously
    parallel_probing_enabled: bool = True
    # Max simultaneous probes when parallel probing is enabled (1-16)
    max_concurrent_probes: int = 8
    # How to distribute probes across M3U profiles: fill_first, round_robin, least_loaded
    profile_distribution_strategy: str = "fill_first"
    # Skip streams that were successfully probed within the last N hours (0 = always probe)
    skip_recently_probed_hours: int = 0
    # Refresh all M3U accounts before starting probe
    refresh_m3us_before_probe: bool = True
    # Automatically reorder streams in channels after probe completes
    auto_reorder_after_probe: bool = False
    # Reflect probe stats back to Dispatcharr via PATCH /api/channels/streams/{id}/
    # so Dispatcharr's UI shows resolution/codec/fps without requiring playback.
    # Uses GET-then-merge-then-PATCH to avoid clobbering keys Dispatcharr wrote itself.
    push_stream_stats_to_dispatcharr: bool = False
    # Probe retry settings for transient ffprobe failures
    probe_retry_count: int = 1  # Number of retries when ffprobe fails but HTTP returns 200 (0 = no retry)
    probe_retry_delay: int = 2  # Seconds to wait between retries
    # Maximum pages to fetch when retrieving streams from Dispatcharr (page_size=500)
    # 200 pages = 100,000 streams max. Increase if you have more than 100K streams.
    stream_fetch_page_limit: int = 200
    # Stream sort priority order for "Smart Sort" feature
    # Order determines priority: first element is primary sort key, subsequent elements are tie-breakers
    # Valid values: "resolution", "bitrate", "framerate", "m3u_priority", "audio_channels"
    stream_sort_priority: list[str] = ["resolution", "bitrate", "framerate", "video_codec", "m3u_priority", "audio_channels"]
    # Which sort criteria are enabled (users can disable criteria they don't want to use)
    # Only enabled criteria appear in sort dropdown and are used by Smart Sort
    stream_sort_enabled: dict[str, bool] = {"resolution": True, "bitrate": True, "framerate": True, "video_codec": False, "m3u_priority": False, "audio_channels": False}
    # M3U account priorities for sorting - maps M3U account ID (as string) to priority value
    # Higher priority value = preferred (sorted first). Accounts not in this map get priority 0.
    # Example: {"1": 100, "2": 50} means M3U account 1 is preferred over account 2
    # Special key "custom": priority assigned to operator-added (non-M3U) custom streams
    # when the m3u_priority sort criterion is active (bd-sgtmx / GH #244).
    # Example: {"1": 100, "custom": 200} places custom streams above M3U account 1.
    m3u_account_priorities: dict[str, int] = {}
    # Deprioritize failed streams - when enabled, failed/timeout/pending streams sort to bottom
    # Black screen detection - run ffmpeg blackdetect after successful probe
    black_screen_detection_enabled: bool = False
    black_screen_sample_duration: int = 5  # Seconds to sample for black screen detection (3-30)
    low_fps_threshold: int = 20  # FPS below this value is considered "low FPS" (5, 10, 15, or 20)
    deprioritize_failed_streams: bool = True
    # Per-category deprioritization overrides.  When False the category's
    # streams are sorted by their actual quality stats instead of being
    # pushed to the bottom.  Only relevant when deprioritize_failed_streams
    # is True (if the master toggle is False, nothing is deprioritized).
    deprioritize_black_screen: bool = True
    deprioritize_low_fps: bool = True
    # Order of deprioritized stream categories (first = sorted higher among deprioritized)
    # Valid values: "failed", "black_screen", "low_fps"
    failed_stream_sort_order: list[str] = ["failed", "black_screen", "low_fps"]
    # Strike rule - flag streams with consecutive probe failures (0 = disabled)
    strike_threshold: int = 3
    # Normalization settings - user-configurable tags for stream name normalization
    # disabled_builtin_tags: Tags to exclude from normalization (format: "group:value", e.g., "country:US")
    disabled_builtin_tags: list[str] = []
    # custom_normalization_tags: User-added custom tags
    # Each dict has "value" (str) and "mode" (prefix/suffix/both)
    custom_normalization_tags: list[dict] = []
    # normalize_on_channel_create: Default state for normalization toggle when creating channels
    # When true, the "Apply normalization" checkbox will be checked by default
    normalize_on_channel_create: bool = False
    # Shared SMTP settings for email features (M3U Digest, etc.)
    # These provide a centralized email configuration that can be used by various features
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "ECM Alerts"
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    # Shared Discord webhook for notifications (M3U Digest, etc.)
    discord_webhook_url: str = ""
    # Shared Telegram bot for notifications (M3U Digest, etc.)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Stream preview mode: how to handle audio codecs in browser preview
    # "passthrough" - Direct playback, may fail on AC-3/E-AC-3/DTS codecs
    # "transcode" - FFmpeg transcodes unsupported audio to AAC (CPU intensive)
    # "video_only" - Strip audio for quick preview (fast, no audio)
    stream_preview_mode: str = "passthrough"
    # Auto-creation pipeline exclusion settings
    auto_creation_excluded_terms: list[str] = []  # Terms that exclude streams by name (case-insensitive substring)
    auto_creation_excluded_groups: list[str] = []  # M3U group names to exclude (case-insensitive exact match)
    auto_creation_exclude_auto_sync_groups: bool = False  # Exclude streams in Dispatcharr auto-sync groups
    # MCP server API key for Claude integration (empty = not configured)
    mcp_api_key: str = ""
    # Frontend error telemetry toggle (ADR-006 §10, bd-i6a1m).
    # Default ON — Phase 1 data never leaves the container. When False,
    # the backend /api/client-errors endpoint returns 204 without logging
    # or incrementing counters, and the frontend reporter short-circuits
    # before building the payload.
    telemetry_client_errors_enabled: bool = True
    # Stream dedup settings (ADR-008 §D2, bd-0b6xj / BD-B).
    # dedup_threshold: operator-configurable confidence threshold (0.0–1.0).
    # Default 0.80; clamped to CONFIDENCE_FLOOR (0.60) at the Pydantic validator
    # (layer 2 of three-layer enforcement per ADR-008 §D2 — the matcher service
    # BD-A clamps at the same floor as the load-bearing enforcement; this validator
    # is the settings-persistence boundary guard).
    # Settings UI (BD-K) constrains the input control to the same range; this
    # validator is the source of truth so API-direct or settings.json-edited
    # bypasses also land at the floor.
    dedup_threshold: float = 0.80
    # dedup_m3u_toast_suppressed: when True, the "N pending merges queued" toast
    # after M3U refresh is not shown to the operator.
    # Default False — the toast is shown by default.
    dedup_m3u_toast_suppressed: bool = False
    # Emby integration settings (bd-8wc6q, epic bd-2cenq). When ``emby_enabled``
    # is True and ``emby_base_url`` + ``emby_api_key`` are configured, the
    # Stats v2 / BandwidthTracker pipeline cross-references active streams
    # against the operator's Emby /Sessions feed to attribute real Emby
    # usernames instead of collapsing every Emby-mediated pull to the proxy
    # IP. ``emby_api_key`` is stored PLAINTEXT at rest — same approach as
    # ``dispatcharr_api_key`` (no encryption-at-rest in this release).
    emby_enabled: bool = False
    # Base URL of the operator's Emby server, e.g. ``http://emby.local:8096``
    # or ``http://proxy/emby`` for reverse-proxy setups. No validation —
    # operator's responsibility to enter a reachable URL; the bd-8wc6q
    # Settings UI 'Test Connection' button surfaces unreachable URLs.
    emby_base_url: str = ""
    # Emby API key (X-Emby-Token header value). Plaintext at rest, same
    # approach as ``dispatcharr_api_key``.
    emby_api_key: str = ""
    # Jellyfin integration settings (bd-r5f0c.3, epic bd-r5f0c). When
    # ``jellyfin_enabled`` is True and ``jellyfin_base_url`` +
    # ``jellyfin_api_key`` are configured, the Stats v2 / BandwidthTracker
    # pipeline cross-references active streams against the operator's Jellyfin
    # /Sessions feed to attribute real Jellyfin usernames. W4 wires the
    # Settings UI 'Test Connection' button and the stats endpoint.
    # ``jellyfin_api_key`` is stored PLAINTEXT at rest — same approach as
    # ``emby_api_key`` (no encryption-at-rest in this release).
    # Auth uses ``Authorization: MediaBrowser Token="<key>"`` (Jellyfin's
    # header format — differs from Emby's X-Emby-Token).
    jellyfin_enabled: bool = False
    # Base URL of the operator's Jellyfin server, e.g.
    # ``http://jellyfin.local:8096``. No validation — operator's
    # responsibility to enter a reachable URL; W4's Settings UI 'Test
    # Connection' button surfaces unreachable URLs.
    jellyfin_base_url: str = ""
    # Jellyfin API key. Server-issued via Dashboard > API Keys. Plaintext
    # at rest, same approach as ``emby_api_key``.
    jellyfin_api_key: str = ""

    @field_validator("dedup_threshold")
    @classmethod
    def clamp_dedup_threshold(cls, v: float) -> float:
        """Clamp dedup_threshold to [CONFIDENCE_FLOOR, 1.00] per ADR-008 §D2.

        CONFIDENCE_FLOOR (imported from services.dedup_matcher) is the
        defense-in-depth integrity constraint (Security Engineer veto-class per
        ADR-008 §D2). A below-floor value triggers a one-time-per-process WARN
        so operators are informed of the clamp; the upper-bound clamp (> 1.00)
        is silent. Negative values hit the lower-bound branch and are clamped
        to the floor with the same WARN.

        The matcher service (BD-A) ALSO clamps to CONFIDENCE_FLOOR — this
        validator is layer 2 of three-layer enforcement. Changing the floor
        value requires an ADR addendum (not a runtime config change).
        """
        global _dedup_threshold_floor_warned

        # Upper-bound clamp (silent)
        if v > 1.00:
            v = 1.00

        # Lower-bound clamp (one-time WARN per process)
        if v < CONFIDENCE_FLOOR:
            if not _dedup_threshold_floor_warned:
                logger.warning(
                    "[CONFIG] dedup_threshold=%s is below the integrity floor (%s); "
                    "clamping to %s. See ADR-008 §D2.",
                    v, CONFIDENCE_FLOOR, CONFIDENCE_FLOOR,
                )
                _dedup_threshold_floor_warned = True
            v = CONFIDENCE_FLOOR

        return v

    def is_configured(self) -> bool:
        if not self.url:
            return False
        if self.auth_method == "api_key":
            # Prefer the canonical ``dispatcharr_api_key`` field; fall back to
            # the legacy ``api_key`` for callers that constructed the model
            # directly without going through ``load_settings()`` (bd-jmi1c).
            # As of 2026-05-16 grep, production code never constructs
            # ``DispatcharrSettings(api_key=...)`` without ``dispatcharr_api_key=``
            # — every site reads from ``load_settings()`` first (which migrates
            # legacy → canonical) or writes via the settings router (which
            # always passes canonical). The fallback is kept defensively only
            # because the legacy field exists on the model until v0.19.0 per
            # bd-ewm4h; remove with that bead.
            return bool(self.dispatcharr_api_key or self.api_key)
        return bool(self.username and self.password)

    def is_smtp_configured(self) -> bool:
        """Check if shared SMTP settings are configured."""
        return bool(self.smtp_host and self.smtp_from_email)

    def is_discord_configured(self) -> bool:
        """Check if shared Discord webhook is configured."""
        return bool(self.discord_webhook_url)

    def is_telegram_configured(self) -> bool:
        """Check if shared Telegram bot is configured."""
        return bool(self.telegram_bot_token and self.telegram_chat_id)


# In-memory cache of settings
_cached_settings: DispatcharrSettings | None = None

# One-shot flag so the legacy ``api_key`` deprecation WARN only fires once
# per process startup, not on every settings reload (bd-jmi1c). Cleared by
# ``clear_settings_cache()`` so test isolation works.
_legacy_api_key_warned: bool = False

# One-shot flag so the "both fields populated and differ" WARN only fires
# once per process startup (bd-jmi1c P1-1). Cleared by
# ``clear_settings_cache()`` alongside ``_legacy_api_key_warned``.
_legacy_api_key_conflict_warned: bool = False

# One-shot flag so the dedup_threshold below-floor WARN only fires once per
# process startup, not on every settings reload (bd-0b6xj / BD-B, ADR-008 §D2).
# Cleared by ``clear_settings_cache()`` so test isolation works.
_dedup_threshold_floor_warned: bool = False


def ensure_config_dir():
    """Ensure config directory exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("[CONFIG] Ensured config directory exists: %s", CONFIG_DIR)


def _migrate_normalization_settings(data: dict) -> dict:
    """Migrate old custom_network_prefixes/suffixes to new normalization format.

    If custom_network_prefixes or custom_network_suffixes exist but
    custom_normalization_tags is empty, convert them to the new format.
    """
    # Only migrate if we have old settings but no new ones
    old_prefixes = data.get("custom_network_prefixes", [])
    old_suffixes = data.get("custom_network_suffixes", [])
    new_tags = data.get("custom_normalization_tags", [])

    if (old_prefixes or old_suffixes) and not new_tags:
        logger.info("[CONFIG] Migrating %s prefixes and %s suffixes to normalization_tags", len(old_prefixes), len(old_suffixes))
        migrated_tags = []

        # Convert prefixes to new format
        for prefix in old_prefixes:
            if prefix and isinstance(prefix, str):
                migrated_tags.append({"value": prefix.strip().upper(), "mode": "prefix"})

        # Convert suffixes to new format
        for suffix in old_suffixes:
            if suffix and isinstance(suffix, str):
                migrated_tags.append({"value": suffix.strip().upper(), "mode": "suffix"})

        if migrated_tags:
            data["custom_normalization_tags"] = migrated_tags
            logger.info("[CONFIG] Migrated %s tags to custom_normalization_tags", len(migrated_tags))

    return data


# Back-compat: legacy 'api_key' field migration helper. Remove in v0.19.0 (bd-ewm4h).
def _migrate_dispatcharr_api_key(data: dict) -> dict:
    """Migrate legacy ``api_key`` field to ``dispatcharr_api_key`` (bd-jmi1c, GH #273).

    Until v0.17.1, the Dispatcharr REST API token was stored in
    ``settings.json:api_key``. That field name collides lexically with the
    MCP integration's ``mcp_api_key`` field; operators rotating the MCP key
    were copying the new MCP key into ``api_key`` (since the UI labels the
    Dispatcharr token "API Key"), which caused ECM to send the MCP key to
    Dispatcharr and break every channel/stream operation with 401.

    The canonical field is now ``dispatcharr_api_key``. This migration runs
    on every settings load so existing operators don't have to touch their
    config files:

      - If ``dispatcharr_api_key`` is already populated → no-op (idempotent).
      - If only legacy ``api_key`` is populated → copy into
        ``dispatcharr_api_key`` and emit ONE WARN per process startup
        pointing the operator at the rename.
      - If both are populated and disagree → ``dispatcharr_api_key`` wins
        (the legacy field is treated as stale).

    The legacy ``api_key`` field is *not* deleted from the in-memory dict
    or from settings.json — external tools that read the file directly
    (the workaround in GH #273's issue body, ad-hoc operator scripts) keep
    working. ``save_settings()`` also mirrors the canonical value back into
    the legacy field on write so the two stay in sync until the legacy
    field is removed in a future release.
    """
    global _legacy_api_key_warned, _legacy_api_key_conflict_warned

    new_key = (data.get("dispatcharr_api_key") or "").strip()
    legacy_key = (data.get("api_key") or "").strip()

    if new_key:
        # Canonical field wins — operator likely rotated the Dispatcharr token
        # via the UI and the legacy field never got updated by an external
        # script. When the two are populated AND differ we emit one WARN per
        # process so operators editing the file directly can see they're about
        # to lose the legacy value on next save (the canonical wins and
        # save_settings() mirrors canonical → legacy, silently overwriting any
        # divergent legacy value). bd-jmi1c P1-1.
        if legacy_key and legacy_key != new_key:
            if not _legacy_api_key_conflict_warned:
                logger.warning(
                    "[CONFIG] Both 'dispatcharr_api_key' and 'api_key' are populated "
                    "with differing values in settings.json; using canonical "
                    "'dispatcharr_api_key' and overwriting 'api_key' on next save. "
                    "If you intend to update the Dispatcharr token via direct file "
                    "edits, write to 'dispatcharr_api_key'. (bd-jmi1c, GH #273)"
                )
                _legacy_api_key_conflict_warned = True
        return data

    if legacy_key:
        # One-time deprecation WARN per process. The flag is cleared by
        # ``clear_settings_cache()`` so tests that exercise the load path
        # multiple times can observe the warning each time.
        if not _legacy_api_key_warned:
            logger.warning(
                "[CONFIG] Reading deprecated 'api_key' field as Dispatcharr token "
                "— please rename to 'dispatcharr_api_key' in settings.json. "
                "The legacy field will continue to be read for v0.17.x and removed "
                "in a future release. (bd-jmi1c, GH #273)"
            )
            _legacy_api_key_warned = True
        data["dispatcharr_api_key"] = legacy_key

    return data


def _sanitize_settings_data(data: dict) -> dict:
    """Replace null values with field defaults to prevent Pydantic validation failures.

    When settings.json contains null for non-Optional fields (e.g., from manual edits,
    older versions, or corrupted backups), Pydantic v2 raises ValidationError, causing
    a silent fallback to empty defaults — effectively "clearing" user settings on restart.
    """
    defaults = DispatcharrSettings()
    for field_name, field_info in DispatcharrSettings.model_fields.items():
        if field_name in data and data[field_name] is None:
            default_val = getattr(defaults, field_name)
            logger.warning("[CONFIG] Field '%s' is null in settings file, using default: %s", field_name, default_val)
            data[field_name] = default_val
    return data


def load_settings() -> DispatcharrSettings:
    """Load settings from file or return defaults."""
    global _cached_settings

    if _cached_settings is not None:
        return _cached_settings

    logger.info("[CONFIG] Loading settings from %s", CONFIG_FILE)
    logger.info("[CONFIG] Config file exists: %s", CONFIG_FILE.exists())

    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            # Apply migrations
            data = _migrate_normalization_settings(data)
            # bd-jmi1c (GH #273) — rename legacy ``api_key`` to
            # ``dispatcharr_api_key``. Must run before _sanitize so the WARN
            # log fires on the actual legacy value, not on a sanitized "".
            data = _migrate_dispatcharr_api_key(data)
            # Sanitize nulls to prevent Pydantic validation failures
            data = _sanitize_settings_data(data)
            _cached_settings = DispatcharrSettings(**data)
            logger.info("[CONFIG] Loaded settings successfully, configured: %s", _cached_settings.is_configured())
            return _cached_settings
        except json.JSONDecodeError as e:
            logger.error("[CONFIG] Settings file is not valid JSON: %s", e)
        except Exception as e:
            logger.exception("[CONFIG] Failed to load settings from %s: %s", CONFIG_FILE, e)

    logger.info("[CONFIG] Using default settings (no config file found or failed to parse)")
    _cached_settings = DispatcharrSettings()
    return _cached_settings


def save_settings(settings: DispatcharrSettings) -> None:
    """Save settings to file.

    bd-jmi1c (GH #273): if ``dispatcharr_api_key`` is populated but the legacy
    ``api_key`` is not, mirror the canonical value into the legacy field on
    write. This keeps external tools that read settings.json directly (the
    workaround in the GH #273 issue body, ad-hoc operator scripts) functional
    until the legacy field is removed in a future release. The reverse mirror
    (legacy → canonical) is the loader's job, not the saver's.
    """
    global _cached_settings

    ensure_config_dir()

    try:
        # Mirror canonical → legacy on write so external readers stay
        # current. The legacy field is the documented surface that operators
        # and ad-hoc scripts touch directly; keeping it in lockstep with the
        # canonical field avoids the trap where a UI rotation makes the file
        # look stale to those readers. Only mirror when the canonical field
        # is populated — an explicit clear (both empty) stays cleared.
        # Back-compat: legacy 'api_key' mirror. Remove in v0.19.0 (bd-ewm4h).
        if settings.dispatcharr_api_key:
            settings.api_key = settings.dispatcharr_api_key
        settings_json = json.dumps(settings.model_dump(), indent=2)
        CONFIG_FILE.write_text(settings_json)
        _cached_settings = settings
        logger.info("[CONFIG] Settings saved successfully to %s", CONFIG_FILE)

        # Verify the save worked
        if CONFIG_FILE.exists():
            saved_data = CONFIG_FILE.read_text()
            logger.info("[CONFIG] Verified settings file exists, size: %s bytes", len(saved_data))
        else:
            logger.error("[CONFIG] Settings file does not exist after save!")
    except Exception as e:
        logger.exception("[CONFIG] Failed to save settings to %s: %s", CONFIG_FILE, e)
        raise


def clear_settings_cache() -> None:
    """Clear the cached settings (forces reload).

    Also resets the legacy ``api_key`` deprecation WARN flag, the
    legacy/canonical conflict WARN flag (bd-jmi1c), and the dedup_threshold
    below-floor WARN flag (bd-0b6xj) so subsequent calls surface all warnings
    again. Without this, tests that exercise the load/validation path multiple
    times in one process would see each WARN fire once and then be silent —
    making it impossible to assert on the warnings per test.
    """
    global _cached_settings, _legacy_api_key_warned, _legacy_api_key_conflict_warned, _dedup_threshold_floor_warned
    _cached_settings = None
    _legacy_api_key_warned = False
    _legacy_api_key_conflict_warned = False
    _dedup_threshold_floor_warned = False
    logger.info("[CONFIG] Settings cache cleared")


def get_settings() -> DispatcharrSettings:
    """Get the current Dispatcharr settings."""
    return load_settings()


def get_http_port() -> int:
    """Get the HTTP port from environment variable (ECM_PORT).
    
    This is an app-level runtime configuration and is not persisted to settings.json.
    Default: 6100
    """
    try:
        return int(os.environ.get("ECM_PORT", 6100))
    except ValueError:
        logger.warning("[CONFIG] Invalid ECM_PORT '%s', using default 6100", os.environ.get("ECM_PORT"))
        return 6100


def get_log_level_from_env() -> str:
    """Get log level from environment variable or default to INFO."""
    return os.environ.get("LOG_LEVEL", "INFO").upper()


def set_log_level(level: str) -> None:
    """Set the logging level for all loggers dynamically."""
    level_upper = level.upper()

    # Validate log level
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if level_upper not in valid_levels:
        logger.warning("[CONFIG] Invalid log level '%s', using INFO", level)
        level_upper = "INFO"

    # Get numeric level
    numeric_level = getattr(logging, level_upper)

    # Set root logger level
    logging.getLogger().setLevel(numeric_level)

    # Set level for all existing loggers, but keep noisy third-party
    # loggers (e.g. sqlalchemy.engine) at WARNING to avoid flooding
    # the console and ring buffer with SQL dumps.
    _NOISY_LOGGERS = {"sqlalchemy", "httpcore"}
    for logger_name in logging.root.manager.loggerDict:
        if any(logger_name.startswith(prefix) for prefix in _NOISY_LOGGERS):
            continue
        logger_obj = logging.getLogger(logger_name)
        logger_obj.setLevel(numeric_level)

    logger.info("[CONFIG] Log level set to %s", level_upper)
