from urllib.parse import urlparse

from pydantic import BaseModel, field_validator
import json
import os
import logging
import secrets

# Single source of truth for the dedup confidence floor per ADR-008 §D2.
# Imported from the ``confidence_constants`` leaf module (NOT from
# services.dedup_matcher) so this validator (layer 2) cannot drift from the
# matcher's clamp (layer 1) — both read the same constant — while keeping
# ``config`` out of the dedup_matcher import cycle (bd-0nabr).
from confidence_constants import CONFIDENCE_FLOOR
# ``credential_sentinel`` is a leaf module (no ECM imports) so this stays out of
# any cycle. It makes ``is_configured`` immune to the backup pipeline's own
# ``***REDACTED***`` placeholder — a truthiness check reports a placeholder as a
# configured credential (bead …-6pilh).
from credential_sentinel import credential_is_present
from pathlib import Path

# Set up logging
logger = logging.getLogger(__name__)

# Config file location
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
CONFIG_FILE = CONFIG_DIR / "settings.json"
# Sidecar credential projection (enhancedchannelmanager-04c0u.8). The AI-facing
# MCP process mounts only this directory, never the full ``/config`` volume, so
# it can reach MCP credential material and nothing else — no settings.json, no
# auth_settings.json, no journal, no TLS keys, no backups.
#
# The fallback is ``CONFIG_DIR``, NOT "no projection". That is deliberate: a
# 0.18.1+ backend running under an older compose file (no ``MCP_SECRETS_DIR``,
# sidecar still mounting ``/config``) has to keep publishing the credentials
# the sidecar reads, or upgrading the backend alone would break MCP. The
# consequence is that EVERY deployment writes ``<CONFIG_DIR>/api-key`` and
# ``<CONFIG_DIR>/mcp-service.json``, overlay or not — both 0600 and owned by
# ECM. Operators who bind-mount a host directory at ``/config`` should know
# that ``api-key`` is a credential file sitting at its top level; see
# docs/user_guide/integrations/mcp.md § "Where the MCP credentials live".
#
# ``or`` rather than a ``get`` default so an explicitly empty ``MCP_SECRETS_DIR=``
# in an ``.env`` resolves to CONFIG_DIR instead of ``Path("")`` → the process
# CWD; mcp-server/config.py resolves the same variable the same way.
MCP_SECRETS_DIR = Path(os.environ.get("MCP_SECRETS_DIR") or CONFIG_DIR)
# The bare filenames are kept as their own constants, separate from the resolved
# paths, so log lines can name the file an operator has to repair without
# interpolating anything derived from the ``MCP_SECRETS_DIR`` environment read.
# CodeQL treats that read as a sensitive source (the identifier matches its
# heuristic), so a resolved path reaching a logger is a
# ``py/clear-text-logging-sensitive-data`` finding on every scan — see
# ``backend/tests/test_04c0u8_projection_paths_are_not_logged.py``, and
# ``mcp-server/config.py`` for the sidecar's copy of the same rule.
MCP_KEY_FILENAME = "api-key"
MCP_SERVICE_FILENAME = "mcp-service.json"
# Public client credential the operator hands to MCP clients.
MCP_KEY_FILE = MCP_SECRETS_DIR / MCP_KEY_FILENAME
# Private sidecar-to-backend credentials (enhancedchannelmanager-04c0u.7): a
# distinct backend principal key plus a distinct destructive-confirmation
# signing key. Never derived from, and never merged with, the public key above.
MCP_SERVICE_FILE = MCP_SECRETS_DIR / MCP_SERVICE_FILENAME


ALLOWED_URL_SCHEMES = {"http", "https"}

# GH #473 OOM cluster — named, settings-overridable safety-valve defaults.
#
# MAX_AUTO_CREATION_LOG_ENTRIES (bd-sjdsq): hard cap on the number of
# per-stream execution_log entries RETAINED in memory (and therefore
# serialized to the auto_creation_executions.execution_log TEXT column) for a
# single non-dry-run pipeline run. The dominant accumulator on a runaway run is
# this log — it holds the full per-stream rule/condition trace — so bounding it
# incrementally during the run is what keeps peak RSS flat regardless of how
# many streams match. Dry-run is exempt (it mutates nothing and the operator
# wants the full trace for debugging). Default 500.
#
# MAX_AUTO_CREATED_CHANNELS_PER_RUN (bd-h2xnl, shared with bd-exo4j as the
# run-size lever): hard cap on the number of channels a single run will
# CREATE. When a run reaches it the engine soft-aborts — it stops creating
# further channels, leaves the already-created channels consistent, marks the
# execution status='capped', and alerts. This is the systemic safety valve for
# the PPV/event expansion blast radius (186 -> 2400+); it is NOT the root-cause
# fix. Default 500.
DEFAULT_MAX_AUTO_CREATION_LOG_ENTRIES = 500
DEFAULT_MAX_AUTO_CREATED_CHANNELS_PER_RUN = 500

# bd-p8fx9 (W4): batch-size caps for the destructive MCP bulk tools. Surfaced on
# GET /api/settings so the MCP guardrails (mcp-server/tools/_guardrails.py) can
# read them; conservative defaults mirror the mcp-server module defaults. SOFT
# cap forces the confirm-token; HARD cap refuses outright. Raisable here for a
# deliberate large migration.
DEFAULT_MCP_BULK_DELETE_SOFT_CAP = 25
DEFAULT_MCP_BULK_DELETE_HARD_CAP = 500
DEFAULT_MCP_CLEAR_AUTO_CREATED_GROUP_SOFT_CAP = 10
DEFAULT_MCP_BULK_MERGE_SOFT_CAP = 20
DEFAULT_MCP_BULK_MERGE_HARD_CAP = 200

# CANONICAL: the :class:`DispatcharrSettings` fields whose VALUES are withheld
# from a non-admin caller on READ (bead …-9ej7f). Outbound notification
# credentials — a Discord webhook URL is a bearer capability to post into a
# server, and a Telegram bot token plus chat id is a bearer capability to post
# into a chat.
#
# WHY IT LIVES HERE and not in the router that enforces it (bead
# …-9kwzp.9). This partition has two independent enforcement points, and while
# it was defined in ``routers/settings.py`` only ONE of them knew about it:
#
#   * ``routers.settings`` withholds these on GET /api/settings.
#   * ``routers.backup`` must redact them out of every backup artifact,
#     because GET /api/backup/create, /export and /saved/{filename} carry
#     ``RequireAdminIfEnabled``, which ADMITS the MCP service principal — the
#     exact principal ``_resolve_settings_admin`` classifies as non-admin. Two
#     of the three fields were readable straight out of a standard backup by
#     the caller the settings endpoint had just refused.
#
# ``config`` is the leaf both consumers already import and is where the model
# these names partition is defined, so deriving from here cannot cycle and
# cannot drift. Adding a field here closes it on BOTH surfaces at once; that
# one-edit property is the whole point, so do not re-inline a literal copy in
# either router. ``routers.settings`` re-exports it under its historical
# private name and carries the read-gate rationale;
# ``routers.backup._SETTINGS_CREDENTIAL_FIELDS`` folds it in.
ADMIN_ONLY_READ_REDACTED_FIELDS: frozenset[str] = frozenset({
    "discord_webhook_url",
    "telegram_bot_token",
    "telegram_chat_id",
})


def normalize_public_base_url(raw_url: str) -> tuple[str, str | None]:
    """Validate + normalize ECM's canonical public base URL (bead ...-qsqfv).

    This is the ONE place the shape of ``public_base_url`` is decided, so the
    save path (``routers.settings``, which turns an error into a 400) and the
    read path (:func:`get_public_base_url`, which treats an error as "unset")
    can never disagree about what a usable value looks like.

    A valid value is an ORIGIN and nothing else: ``scheme://host[:port]``.

    * Scheme is required and must be http or https. Requiring it is what makes
      the value unambiguous; a bare ``ecm.example.com`` would parse as a path.
    * A trailing slash is accepted and stripped, because every caller appends
      its own leading-slash path (``{base}/reset-password?...``) and a stored
      slash would emit a double slash into a link an operator has to trust.
    * A path is REJECTED rather than stripped. ECM's frontend is built with
      Vite's default ``base`` of ``/`` and its router serves ``/reset-password``
      from the origin root, so a sub-path origin cannot produce a working link;
      silently stripping it would hide the operator's mistake instead of
      reporting it.
    * Query string, fragment and userinfo (``user:pass@``) are rejected: none
      of them can be meaningful in an origin, and userinfo in particular is a
      classic way to make a link's real destination hard to read.
    * Whitespace anywhere is rejected, which also keeps stray newlines out of a
      value that gets interpolated into outbound email bodies.

    Host is lower-cased (host names are case-insensitive) and an IPv6 literal
    keeps its brackets. Returns ``(normalized, error)``; ``("", None)`` means
    the operator has not configured a value, which is a legitimate state and
    NOT an error.
    """
    if not raw_url:
        return "", None
    candidate = raw_url.strip()
    if not candidate:
        return "", None
    if any(char.isspace() for char in candidate):
        return "", "must not contain whitespace"

    try:
        parsed = urlparse(candidate)
    except ValueError as exc:
        return "", f"could not be parsed as a URL ({exc})"

    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        return "", "must start with http:// or https://"

    try:
        # Both raise ValueError on a malformed netloc (bad IPv6 literal, a
        # non-numeric port), so they are read before anything else touches it.
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        return "", f"has an invalid host or port ({exc})"

    if parsed.username or parsed.password:
        return "", "must not contain credentials (user:password@host)"
    if parsed.query:
        return "", "must not contain a query string"
    if parsed.fragment:
        return "", "must not contain a fragment"
    if parsed.path not in ("", "/"):
        return "", (
            "must not contain a path (ECM is served from the root of its "
            "origin, so only scheme://host[:port] can produce a working link)"
        )
    if not hostname:
        return "", "must include a host"

    host = hostname.lower()
    if ":" in host:
        # ``parsed.hostname`` unwraps an IPv6 literal; put the brackets back.
        host = f"[{host}]"
    if port is not None:
        host = f"{host}:{port}"
    return f"{scheme}://{host}", None


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
    # bd-dgs64 (GH #591): Dispatcharr channel groups are global entities — a
    # group with the same name on two M3U providers shares one channel_group
    # ID. The M3UGroupsModal frontend guard (commit 030c1ef8) therefore locks
    # a group's Auto-Sync toggle/Start#/Settings to a single "owning" account
    # once any OTHER account has auto_channel_sync enabled for that group ID,
    # to prevent two providers silently double-creating channels for the same
    # group. This flag lets an operator opt OUT of that guard so the same
    # group CAN be auto-synced from multiple providers at once, as Dispatcharr
    # itself allows. Admin-gated (routers/settings.py
    # _ADMIN_ONLY_SETTINGS_FIELDS) because enabling it is an install-wide
    # duplicate-channel risk, not a per-user display preference. Default False
    # preserves today's single-owner lock.
    allow_multi_provider_auto_sync: bool = False
    hide_ungrouped_streams: bool = True  # Hide ungrouped streams in the streams pane
    hide_epg_urls: bool = False  # Hide EPG URLs in EPG Manager tab
    hide_m3u_urls: bool = False  # Hide M3U URLs in M3U Manager tab
    gracenote_conflict_mode: str = "ask"  # Gracenote ID conflict handling: "ask", "skip", or "overwrite"
    theme: str = "dark"  # Theme: "dark", "light", or "high-contrast"
    # Global date-format preference for the UI (bd-8j47e). Applies to all
    # users since settings are instance-wide. "auto" defers to each viewer's
    # browser locale; "mdy"/"dmy"/"iso" pin the date ordering app-wide.
    date_format: str = "auto"  # Date format: "auto", "mdy", "dmy", or "iso"
    # Internal bookkeeping (not a user setting): records that the one-time league
    # strip require_delimiter heal (bd-0emgo.2) has run. Gates the startup heal so
    # it applies once for upgrading operators and NEVER re-flips a value the user
    # later changed (GH #484). A persistent marker is required because the heal
    # cannot be an Alembic data migration — ECM's smart-bootstrap stamps forward
    # past data-only migrations when the schema already matches (bd-5w6jz).
    league_delimiter_heal_applied: bool = False
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
    # ADR-013 §D2 (bead 312nk.3): steady-state ``session_telemetry`` write
    # cadence in seconds. Observation freshness (byte-delta bandwidth,
    # ChannelBandwidth/BandwidthDaily, in-memory active-channel/client tracking)
    # is decoupled from this — it updates on EVERY observation (~2s under the WS
    # driver). The heavy write path (provider resolution, system-events ingest,
    # media-server attribution, session_telemetry insert) only runs once per
    # this interval. Edge-triggered writes on session start/stop (a channel
    # becomes newly active, or a client appears/leaves) still fire IMMEDIATELY
    # even mid-interval, so session boundaries are captured at WS latency.
    # PO-LOCKED DEFAULT 10s — preserves today's session_telemetry row cadence
    # (matches the default stats_poll_interval). No migration (settings.json).
    telemetry_write_interval: int = 10
    # ADR-013: WebSocket channel_stats subscriber (bead 312nk.2). Master enable
    # for the WS driver that feeds Dispatcharr's channel_stats broadcast into
    # the bandwidth tracker as a drop-in for the /proxy/ts/status poll. Default
    # OFF — the poll remains the permanent fallback. The settings-restart path
    # (_restart_background_services) reconstructs the tracker, so toggling this
    # re-reads it on the next start.
    use_ws_channel_stats: bool = False
    # ADR-013 §D5 / PO decision #3. When the WS is healthy: if True, the poll
    # skips its get_channel_stats() fetch entirely (WS is the sole driver); if
    # False (the soak default), the poll STILL fetches but cross-validates
    # against the last WS snapshot instead of double-processing telemetry. Flip
    # to True once the feature defaults ON.
    ws_suppress_poll_when_healthy: bool = False
    # ADR-013 §D3 (bead 312nk.4): coarse safety TTL (seconds) for the
    # process-lived stream_id -> (m3u_account_id, provider_name) cache. The
    # cache is event-invalidated by the WS stream_rehash / channels_created
    # broadcasts while the WS is healthy; this TTL bounds staleness if an
    # invalidation event is missed during a WS gap, and is the ONLY invalidation
    # on the poll-fallback path (degraded mode). Default 300s matches the
    # bd-1qmn0 M3U-accounts snapshot cache. Operator-driven via settings.json
    # (not surfaced in the UI). No migration.
    stream_provider_cache_ttl: int = 300
    # ADR-013 §D4 (bead 312nk.4): TTL (seconds) for the user_id -> username
    # cache that replaces the per-write get_users() fetch. Dispatcharr usernames
    # change rarely, so minutes of staleness on a display name is harmless.
    # Default 300s. Operator-driven via settings.json (not surfaced in the UI).
    # No migration.
    user_username_cache_ttl: int = 300
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
    # Valid values: "resolution", "bitrate", "framerate", "video_codec", "m3u_priority", "audio_channels", "custom_streams", "catchup"
    stream_sort_priority: list[str] = ["resolution", "bitrate", "framerate", "video_codec", "m3u_priority", "audio_channels", "custom_streams", "catchup"]
    # Which sort criteria are enabled (users can disable criteria they don't want to use)
    # Only enabled criteria appear in sort dropdown and are used by Smart Sort
    stream_sort_enabled: dict[str, bool] = {"resolution": True, "bitrate": True, "framerate": True, "video_codec": False, "m3u_priority": False, "audio_channels": False, "custom_streams": False, "catchup": False}
    # M3U account priorities for sorting - maps M3U account ID (as string) to priority value
    # Higher priority value = preferred (sorted first). Accounts not in this map get priority 0.
    # Example: {"1": 100, "2": 50} means M3U account 1 is preferred over account 2
    # Special key "custom": a vestigial defensive fallback applied by the m3u_priority
    # criterion to streams that carry NO M3U account (m3u_account_id is None). Operator-added
    # custom streams belong to the real Dispatcharr "custom" M3U account and are now ranked
    # by the dedicated "custom_streams" Smart Sort criterion (bead ap1ud / GH #244), not by
    # this key. Example: {"1": 100, "custom": 200} only affects account-less streams.
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
    # public_base_url: the canonical origin (scheme://host[:port]) operators
    # reach ECM at, used VERBATIM to build links ECM sends OUT of the process,
    # today the password-reset link in the forgot-password email.
    #
    # Bead ...-qsqfv (P1): that link used to be built from X-Forwarded-Host /
    # X-Forwarded-Proto, falling back to the request's own Host header. All
    # three are supplied by whoever sent the request, so an unauthenticated
    # caller who knew a victim's email address could make ECM mail that victim
    # a genuine reset email, from ECM's own SMTP, whose link pointed at the
    # attacker's host and carried a live reset token.
    #
    # Empty (the default) preserves the old header-derived behaviour so no
    # existing install's reset email stops working on upgrade; that install
    # stays exposed, which is why get_public_base_url() warns when it is unset.
    # Shape is decided in exactly one place, normalize_public_base_url().
    public_base_url: str = ""
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
    # Event Sync operator team-alias dictionary (bead ti939.4.2). Each entry
    # is {"terms": [str, ...], "note": str|None} — one group of KNOWN-
    # equivalent team spellings ("Man Utd" == "Manchester United" == "MUFC")
    # consulted by the event matcher's team-token layer
    # (services/event_sync_matcher.py). Written ONLY through the dedicated
    # PUT /api/event-sync/team-aliases endpoint (routers/event_sync_aliases.py
    # — validated + journaled there), never by the general settings form.
    # Ships EMPTY by design: aliases are corpus-gated (docs/event_sync.md).
    event_sync_team_aliases: list[dict] = []
    # Auto-creation pre-run snapshot retention (ADR-010 §D7 / uc51o.3). Two
    # bounds, whichever fires first, pruned by CleanupTask BEFORE the VACUUM
    # step. Without these, a per-run ~570-channel snapshot captured on every
    # execute (incl. hourly run_on_refresh) is an unbounded SQLite-growth bomb.
    # Naming + 30-day default match the auto_creation_blob_days retention
    # cadence already in tasks/cleanup.py; the count cap is modeled on the
    # M3USnapshot newest-N precedent (ADR-010 §D7).
    auto_creation_snapshot_days: int = 30  # Age window — prune snapshots older than this many days (by snapshot_time).
    auto_creation_snapshot_max: int = 50  # Count cap — keep at most this many newest snapshots; older ones pruned regardless of age.
    # GH #473 OOM cluster safety valves (settings-overridable; module defaults
    # in DEFAULT_MAX_* above). See those constants for the full rationale.
    # bd-sjdsq: max per-stream execution_log entries retained in memory per
    # non-dry-run run. Dry-run keeps the full trace. <= 0 disables the cap.
    max_auto_creation_log_entries: int = DEFAULT_MAX_AUTO_CREATION_LOG_ENTRIES
    # bd-h2xnl / bd-exo4j: max channels a single run will create before
    # soft-aborting (status='capped'). <= 0 disables the cap.
    max_auto_created_channels_per_run: int = DEFAULT_MAX_AUTO_CREATED_CHANNELS_PER_RUN
    # bd-p8fx9 (W4): MCP destructive-bulk batch-size caps (read by the MCP
    # guardrails over GET /api/settings). SOFT forces the confirm-token; HARD
    # refuses outright. Raise deliberately for a large planned migration.
    mcp_bulk_delete_soft_cap: int = DEFAULT_MCP_BULK_DELETE_SOFT_CAP
    mcp_bulk_delete_hard_cap: int = DEFAULT_MCP_BULK_DELETE_HARD_CAP
    mcp_clear_auto_created_group_soft_cap: int = DEFAULT_MCP_CLEAR_AUTO_CREATED_GROUP_SOFT_CAP
    mcp_bulk_merge_soft_cap: int = DEFAULT_MCP_BULK_MERGE_SOFT_CAP
    mcp_bulk_merge_hard_cap: int = DEFAULT_MCP_BULK_MERGE_HARD_CAP
    # bd-exo4j circuit breaker (THE breaker, persisted across restarts): when
    # the startup crash-sentinel abandons a run left 'running' by an OOM
    # SIGKILL, it sets this flag True. While True, run_auto_creation_after_refresh
    # SKIPS the auto-fire chain (manual "Run Now" is NOT gated). NEVER
    # auto-reset — the operator must deliberately clear it via
    # POST /api/auto-creation/reset-circuit-breaker. Internal bookkeeping, not a
    # user-facing preference.
    auto_creation_run_on_refresh_disabled: bool = False
    # ADR-011 (bd-ka7j9): refresh watermark decoupling M3U refresh from
    # auto-creation. M3U refresh no longer hard-chains auto-creation as a
    # side-effect; instead it advances ``last_m3u_refresh_completed_at`` on
    # EVERY successful refresh (Q1: NOT change-gated — preserves today's
    # "runs after every refresh" behavior). The interval-scheduled
    # ChannelPipelineTask auto-fires only when the refresh watermark is newer than
    # ``last_auto_creation_consumed_refresh_at`` (which it advances to the
    # consumed value when it runs). Both are ISO-8601 UTC strings (matching the
    # other timestamp fields); empty string == "never" (sorts before any real
    # timestamp, so a fresh install with at least one refresh fires once).
    last_m3u_refresh_completed_at: str = ""
    last_auto_creation_consumed_refresh_at: str = ""
    # M3U change-tracking retention (bd-wehek / bd-f9gd8 DBA spike). Both tables
    # grow with every Dispatcharr upstream change (every 5-min poll if upstream
    # churns): m3u_snapshots stores ~1-10 kB groups_data JSON per row;
    # m3u_change_logs stores ~500 B per detected change.  Neither had retention.
    # Prune both by age, BEFORE the VACUUM step, mirroring the established
    # age-window pattern.  90-day default matches the journal hot-retention window
    # (bd-dmu8w) and gives operators a comfortable M3U change history without
    # unbounded growth.
    m3u_snapshot_days: int = 90  # Delete m3u_snapshots rows older than this many days (by snapshot_time).
    m3u_change_log_days: int = 90  # Delete m3u_change_logs rows older than this many days (by change_time).
    # unique_client_connections retention (bd-1wi3y / bd-f9gd8 DBA spike). High
    # write rate (one row per (channel, IP) connection start) + 6 indexes makes
    # this table grow quickly.  Currently no retention beyond manual stats reset.
    # Prune by age, BEFORE the VACUUM step, mirroring the established
    # age-window pattern.  90-day default mirrors the M3U retention window above.
    unique_client_connection_days: int = 90  # Delete unique_client_connections rows older than this many days (by connected_at).
    # DBAS outbound SSRF mode (bead 0i2vt.5, threat model §9.4 item 7 / ADR-012
    # D4). The SINGLE wizard knob governing the outbound-destination policy for
    # cloud upload (S3/WebDAV/OneDrive/Dropbox/GDrive). "lan_friendly" (DEFAULT)
    # allows RFC1918 private, RFC 6598 shared, and 127/8 loopback destinations
    # (operators backing up to a LAN/VPN peer); "public_only" blocks those. The
    # ALWAYS-ON denylist (metadata/link-local/IPv6-special/non-http(s)) is enforced
    # unconditionally in code (security/ssrf.py) regardless of this value — this
    # key can ONLY move the RFC1918/RFC6598/loopback band, never the always-on denylist
    # (threat model B6). The first-run wizard that records this choice is a
    # separate frontend bead; this field is the persistence seam.
    ssrf_outbound_mode: str = "lan_friendly"
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
    # Plex integration settings (bd-r5f0c.4, epic bd-r5f0c). When
    # ``plex_enabled`` is True and ``plex_base_url`` + ``plex_token`` are
    # configured, the Stats v2 / BandwidthTracker pipeline
    # cross-references active streams against the operator's Plex
    # ``/status/sessions`` feed to attribute real Plex usernames. Auth
    # uses ``X-Plex-Token: <token>`` — issued via Plex Web (Account >
    # Authorized Devices > Get Token). Plaintext at rest, same approach
    # as ``emby_api_key`` / ``jellyfin_api_key`` (no encryption-at-rest
    # in this release). The field is named ``plex_token`` rather than
    # ``plex_api_key`` to match the Plex ecosystem nomenclature operators
    # are used to.
    plex_enabled: bool = False
    # Base URL of the operator's Plex server, e.g.
    # ``http://plex.local:32400``. No validation — operator's
    # responsibility to enter a reachable URL; W4's Settings UI 'Test
    # Connection' button surfaces unreachable URLs.
    plex_base_url: str = ""
    # Plex auth token (``X-Plex-Token`` header value). Plaintext at rest,
    # same approach as ``emby_api_key``.
    plex_token: str = ""
    # bd-mlcla: operator-configured trusted media/proxy networks used ONLY
    # to RANK media-server attribution candidates, never to gate. Each entry
    # is a CIDR (``"172.16.0.0/24"``) or a bare IP (``"172.16.0.19"``,
    # treated as a host). Connections whose source IP falls inside any entry
    # sort first when pairing media-server users to Dispatcharr connections
    # (most-likely media-mediated). Getting this list wrong can only change
    # tie-break ORDER, never which users attribute — attribution is a
    # per-channel set reconciliation, not an IP join. Default empty: pure
    # ``connected_at`` ordering, still safe (the reconciler does the
    # anti-collapse work; IP only breaks ties). See
    # ``services.attribution_reconciler``.
    trusted_media_networks: list[str] = []

    @field_validator("dedup_threshold")
    @classmethod
    def clamp_dedup_threshold(cls, v: float) -> float:
        """Clamp dedup_threshold to [CONFIDENCE_FLOOR, 1.00] per ADR-008 §D2.

        CONFIDENCE_FLOOR (imported from the confidence_constants leaf module,
        the single source of truth shared with the matcher) is the
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

    @field_validator(
        "max_auto_created_channels_per_run",
        "max_auto_creation_log_entries",
    )
    @classmethod
    def normalize_auto_creation_cap(cls, v: int) -> int:
        """Normalize the GH #473 auto-creation safety-valve caps (skg35).

        Both caps share the same ``<= 0`` disable sentinel, surfaced to
        operators via the settings API/UI. Any value at or below zero means
        "disabled" (no cap). A negative is just another way of saying disabled,
        so we normalize it to ``0`` for a single canonical disabled value —
        keeping the stored settings.json tidy and the GET response unambiguous.

        Deliberately permissive on the upper bound: an operator running a large
        deliberate expansion may raise the cap arbitrarily high, and the engine
        already treats the cap as a soft-abort threshold (no allocation tied to
        the value). No upper clamp here would only invite a footgun without a
        real failure mode, so we leave positive values untouched.
        """
        if v < 0:
            return 0
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
            return credential_is_present(self.dispatcharr_api_key) or credential_is_present(self.api_key)
        return credential_is_present(self.username) and credential_is_present(self.password)

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

# One-shot flags for the two ``public_base_url`` WARNs (bead ...-qsqfv), same
# convention as the three above: fire once per process, cleared by
# ``clear_settings_cache()`` so a settings save re-arms them and so tests can
# assert on each warning. get_public_base_url() runs on every forgot-password
# request, so an unguarded WARN there would be per-request log spam.
_public_base_url_unset_warned: bool = False
_public_base_url_invalid_warned: bool = False
_session_cookie_transport_warned: bool = False


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
            try:
                _project_mcp_api_key(_cached_settings.mcp_api_key)
            except OSError:
                # A broken optional sidecar projection must not make ECM
                # discard otherwise-valid settings and boot with defaults.
                # Rotation/save remains fail-closed because save_settings does
                # not suppress this error.
                logger.exception("[CONFIG] Failed to project MCP API key")
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
        # Credential rotation must never expose a partially-written JSON file
        # to the live MCP sidecar. Write, sync, then atomically project it.
        temporary = CONFIG_FILE.with_name(
            f".{CONFIG_FILE.name}.{secrets.token_hex(8)}.tmp"
        )
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w") as output:
                output.write(settings_json)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, CONFIG_FILE)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        # Adopt the cache the instant the settings file is durable, BEFORE
        # projecting. The projection can fail for reasons unrelated to the
        # settings write — a full disk, a read-only or relabelled mount — and
        # the old ordering left the new key durably on disk while this process
        # kept serving the previous one from cache and returned 500 to the
        # caller. For a credential rotation that is the worst possible split:
        # the operator is handed a key ECM itself no longer believes in.
        # Rotation stays fail-closed because the projection error below is
        # still raised, not suppressed (see _project_mcp_api_key), and it is
        # now raised over a cache that agrees with disk.
        #
        # MERGE NOTE for the save_settings lock landing in
        # enhancedchannelmanager-04c0u.10: put BOTH of these statements inside
        # that lock, in THIS order. Serializing them fixes the interleaving
        # that lets one save project a key while another owns the cache. It
        # does not fix the failure ordering, which is a separate defect: if
        # the projection runs first and raises, this process keeps serving the
        # superseded key out of a cache that no longer matches the settings
        # file it just wrote. Swapping these two lines back reintroduces that
        # and fails
        # tests/test_04c0u8_mcp_key_projection.py::
        # test_a_failed_projection_never_leaves_the_cache_behind_the_saved_file.
        _cached_settings = settings
        _project_mcp_api_key(settings.mcp_api_key)
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


def _project_mcp_api_key(key: str) -> None:
    """Atomically publish only the public MCP credential to the sidecar volume.

    The full settings document contains unrelated credentials and must never be
    mounted into the AI-facing process.  Only the operator-disclosed client key
    is projected here; the private backend and destructive-confirmation keys
    stay in the separate owner-only ``mcp-service.json`` projection written by
    ``backend.auth.mcp_service`` (enhancedchannelmanager-04c0u.7).

    The projection is owner-only (0600) and readable by the sidecar solely
    because the sidecar runs under the same PUID/PGID as the backend.  Widening
    the mode, or granting group/other read, would hand the credential to every
    other process sharing the volume.

    This runs on EVERY deployment, not only overlay deployments.
    ``MCP_SECRETS_DIR`` defaults to ``CONFIG_DIR`` (see the module header for
    why), so without the overlay the projection is written to
    ``<CONFIG_DIR>/api-key`` rather than skipped.  The ``parent.exists()``
    guard below therefore fires only when the configured directory is genuinely
    absent — an ``MCP_SECRETS_DIR`` pointing at an unmounted path — not as the
    normal no-overlay path.  Enforced by
    ``backend/tests/test_04c0u8_mcp_key_projection.py``
    ``test_the_default_projection_directory_is_config_dir``.
    """
    parent = MCP_KEY_FILE.parent
    if not parent.exists():
        return
    temporary = MCP_KEY_FILE.with_name(
        f".{MCP_KEY_FILE.name}.{secrets.token_hex(8)}.tmp"
    )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(f"{key}\n")
            handle.flush()
            # Fix the mode on the descriptor, not on the path after the
            # rename: a path-based chmod follows symlinks, so it would widen
            # the mode of a link target rather than of the file just written.
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
        os.replace(temporary, MCP_KEY_FILE)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def superseded_mcp_service_projection() -> Path | None:
    """Return the pre-…-04c0u.8 private projection if it was left behind.

    Deployments that ran …-04c0u.7 have a live-format ``mcp-service.json``
    (backend principal key + destructive-confirmation signing key) at
    ``CONFIG_DIR``. Moving the projection to ``MCP_SECRETS_DIR`` does not
    remove it, so it becomes stale secret material sitting in the config
    volume, where host-side sweep-everything backup tools will capture it.

    It is inert — this backend accepts only the projection at
    ``MCP_SERVICE_FILE``, so the superseded pair authenticates nothing — but
    inert is not gone. ECM deliberately does NOT delete it: removing
    credential material on an operator's behalf is destructive and
    irreversible, and ``CONFIG_DIR`` is a path ECM shares rather than owns.
    The caller reports it instead, on every start, until the operator removes
    it. See docs/user_guide/integrations/mcp.md § "Where the MCP credentials
    live".
    """
    if MCP_SECRETS_DIR == CONFIG_DIR:
        return None
    # ``CONFIG_DIR / MCP_SERVICE_FILENAME``, never ``MCP_SERVICE_FILE.name``:
    # the returned path is a CONFIG_DIR path with a constant filename and never
    # depended on ``MCP_SECRETS_DIR``, but taking the name off the resolved
    # path made it MCP_SECRETS_DIR-derived anyway — which put the caller's
    # startup warning on the clear-text-logging alert list for a value that is
    # not sensitive. The caller needs to name the exact file to delete, so the
    # value is de-tainted at the source rather than dropped from the message.
    superseded = CONFIG_DIR / MCP_SERVICE_FILENAME
    return superseded if superseded.is_file() else None


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
    global _public_base_url_unset_warned, _public_base_url_invalid_warned
    global _session_cookie_transport_warned
    _cached_settings = None
    _legacy_api_key_warned = False
    _legacy_api_key_conflict_warned = False
    _dedup_threshold_floor_warned = False
    _public_base_url_unset_warned = False
    _public_base_url_invalid_warned = False
    _session_cookie_transport_warned = False
    logger.info("[CONFIG] Settings cache cleared")


def get_settings() -> DispatcharrSettings:
    """Get the current Dispatcharr settings."""
    return load_settings()


def _session_cookies_travel_in_cleartext() -> bool:
    """True when auth is on and no transport signal protects session cookies.

    Imported lazily: ``auth.settings`` and ``tls.settings`` are higher layers
    than ``config``, and only this runtime check needs them. Any failure here
    degrades to "say nothing" — a diagnostic must never break configuration
    loading.
    """
    try:
        from auth.settings import get_auth_settings
        from tls.settings import get_tls_settings, TLS_DIR
        from tls.storage import CertificateStorage

        if not get_auth_settings().require_auth:
            return False
        tls_settings = get_tls_settings()
        return not (
            tls_settings.enabled and CertificateStorage(TLS_DIR).has_certificate()
        )
    except Exception:
        return False


def get_public_base_url() -> str:
    """Canonical public origin for links ECM sends out, or "" when unset.

    Callers that build a user-visible URL should use this and fall back only
    deliberately: a falsy return means the operator has configured nothing, so
    the caller is on its own with request-derived (caller-controlled) data.

    The stored value is re-validated here rather than trusted, because
    settings.json is also written by hand and by backup restores; an invalid
    stored value degrades to "unset" instead of emitting a malformed link.

    WARN cadence (bead ...-qsqfv): once per process for each condition, re-armed
    by ``clear_settings_cache()`` (so a settings save warns again). ECM also
    calls this during startup, so an operator who never configures it finds the
    warning at the top of the log rather than only after someone happens to
    request a password reset.
    """
    global _public_base_url_unset_warned, _public_base_url_invalid_warned
    global _session_cookie_transport_warned

    raw = get_settings().public_base_url
    normalized, err = normalize_public_base_url(raw)
    if err is not None:
        if not _public_base_url_invalid_warned:
            # The value is operator-entered configuration, not a credential,
            # and the operator needs to see what was rejected to fix it.
            logger.warning(
                "[CONFIG] Stored public_base_url %r is not usable (%s); "
                "treating it as unset. Outbound links will fall back to "
                "caller-supplied request headers until it is corrected in "
                "Settings > Email.",
                raw, err,
            )
            _public_base_url_invalid_warned = True
        normalized = ""

    if not normalized and not _public_base_url_unset_warned:
        logger.warning(
            "[CONFIG] public_base_url is not set, so password-reset links are "
            "built from the caller-supplied Host / X-Forwarded-Host header. An "
            "unauthenticated caller who knows a user's email address can make "
            "that link point at a host they control (bead qsqfv). Set the "
            "public base URL under Settings > Email to close this."
        )
        _public_base_url_unset_warned = True

    if (
        not normalized
        and not _session_cookie_transport_warned
        and _session_cookies_travel_in_cleartext()
    ):
        # Second, separate WARN on purpose (bead 04c0u.9 remediation). The one
        # above is about password-reset links, and an operator reading it has
        # no reason to connect it to session-cookie policy — but it is the SAME
        # unset value that leaves both open. Naming the consequence is what
        # turns the known residual into an operator-visible signal.
        logger.warning(
            "[CONFIG] Session cookies are UNPROTECTED: no public base URL is "
            "configured and ECM is not terminating TLS, so browser session "
            "cookies are issued without Secure and anyone who can observe this "
            "network can capture a live session (bead 04c0u.9). Set the public "
            "base URL to your https:// origin under Settings > Email if a "
            "reverse proxy terminates TLS, or enable TLS under Settings > TLS."
        )
        _session_cookie_transport_warned = True

    return normalized


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


def detect_local_bridge_gateways() -> list[str]:
    """Best-effort auto-detect local Docker bridge-gateway IPs (bd-mlcla).

    When ECM runs inside a container, browser-direct media-server traffic
    is frequently NAT'd through the media box's Docker bridge gateway, so
    ECM observes the gateway IP (e.g. ``172.18.0.1``) instead of the
    configured media-server IP. Including those gateway IPs in the
    attribution RANKING (never the gate) lets such connections sort first
    as most-likely media-mediated.

    This reads ``/proc/net/route`` to find the default-gateway IP(s) for
    every interface. It is intentionally a HINT only: getting detection
    wrong can change tie-break order but never which users attribute
    (asserted by ``test_attribution_reconciler`` /
    ``test_bandwidth_tracker_*attribution*``). Never raises — any parse or
    I/O failure returns an empty list so the hot path degrades to
    ``connected_at`` ordering.

    Returns a list of IPv4 dotted-quad strings (deduplicated, order
    preserved). Empty when no gateways could be read.
    """
    gateways: list[str] = []
    seen: set[str] = set()
    route_path = Path("/proc/net/route")
    try:
        if not route_path.exists():
            return []
        for line in route_path.read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) < 3:
                continue
            # /proc/net/route gateway is a little-endian hex IPv4. A
            # non-zero gateway with the default-route destination
            # (0.0.0.0) is the interface's default gateway.
            destination_hex = fields[1]
            gateway_hex = fields[2]
            if gateway_hex == "00000000":
                continue
            if destination_hex != "00000000":
                # Only default routes give us the egress gateway; per-subnet
                # routes are not what we want for the NAT-source hint.
                continue
            try:
                gw_int = int(gateway_hex, 16)
            except ValueError:
                continue
            # Little-endian: reverse the four bytes.
            octets = [
                (gw_int >> 0) & 0xFF,
                (gw_int >> 8) & 0xFF,
                (gw_int >> 16) & 0xFF,
                (gw_int >> 24) & 0xFF,
            ]
            ip = ".".join(str(o) for o in octets)
            if ip != "0.0.0.0" and ip not in seen:
                seen.add(ip)
                gateways.append(ip)
    except Exception as exc:  # noqa: BLE001 — hint-only, must never raise
        logger.debug("[CONFIG] Bridge-gateway auto-detect failed (hint only): %s", exc)
        return []
    return gateways


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
