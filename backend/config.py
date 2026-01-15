from pydantic import BaseModel
from pydantic_settings import BaseSettings
import json
import os
import logging
from pathlib import Path

# Set up logging
logger = logging.getLogger(__name__)

# Config file location
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
CONFIG_FILE = CONFIG_DIR / "settings.json"


class DispatcharrSettings(BaseModel):
    """User-configurable Dispatcharr connection settings."""
    url: str = ""
    username: str = ""
    password: str = ""
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
    stream_probe_enabled: bool = True  # Enable scheduled background probing
    stream_probe_interval_hours: int = 24  # How often to auto-probe (hours)
    stream_probe_batch_size: int = 10  # Streams to probe per scheduled cycle
    stream_probe_timeout: int = 30  # Timeout in seconds for each probe
    stream_probe_schedule_time: str = "03:00"  # Time of day to run probes (HH:MM, 24h format, user's local time)
    probe_channel_groups: list[str] = []  # Channel groups to probe (empty = all groups with streams)
    bitrate_sample_duration: int = 10  # Duration in seconds to sample stream for bitrate measurement (10, 20, or 30)
    # Parallel probing - probe streams from different M3U accounts simultaneously
    parallel_probing_enabled: bool = True
    # Stream sort priority order for "Smart Sort" feature
    # Order determines priority: first element is primary sort key, subsequent elements are tie-breakers
    # Valid values: "resolution", "bitrate", "framerate"
    stream_sort_priority: list[str] = ["resolution", "bitrate", "framerate"]
    # Which sort criteria are enabled (users can disable criteria they don't want to use)
    # Only enabled criteria appear in sort dropdown and are used by Smart Sort
    stream_sort_enabled: dict[str, bool] = {"resolution": True, "bitrate": True, "framerate": True}

    def is_configured(self) -> bool:
        return bool(self.url and self.username and self.password)


class Settings(BaseSettings):
    """App settings from environment (for container config)."""
    config_dir: str = "/config"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# In-memory cache of settings
_cached_settings: DispatcharrSettings | None = None


def ensure_config_dir():
    """Ensure config directory exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Ensured config directory exists: {CONFIG_DIR}")


def load_settings() -> DispatcharrSettings:
    """Load settings from file or return defaults."""
    global _cached_settings

    if _cached_settings is not None:
        return _cached_settings

    logger.info(f"Loading settings from {CONFIG_FILE}")
    logger.info(f"Config file exists: {CONFIG_FILE.exists()}")

    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            _cached_settings = DispatcharrSettings(**data)
            logger.info(f"Loaded settings successfully, configured: {_cached_settings.is_configured()}")
            return _cached_settings
        except Exception as e:
            logger.error(f"Failed to load settings from {CONFIG_FILE}: {e}")

    logger.info("Using default settings (no config file found or failed to parse)")
    _cached_settings = DispatcharrSettings()
    return _cached_settings


def save_settings(settings: DispatcharrSettings) -> None:
    """Save settings to file."""
    global _cached_settings

    ensure_config_dir()

    try:
        settings_json = json.dumps(settings.model_dump(), indent=2)
        CONFIG_FILE.write_text(settings_json)
        _cached_settings = settings
        logger.info(f"Settings saved successfully to {CONFIG_FILE}")

        # Verify the save worked
        if CONFIG_FILE.exists():
            saved_data = CONFIG_FILE.read_text()
            logger.info(f"Verified settings file exists, size: {len(saved_data)} bytes")
        else:
            logger.error(f"Settings file does not exist after save!")
    except Exception as e:
        logger.error(f"Failed to save settings to {CONFIG_FILE}: {e}")
        raise


def clear_settings_cache() -> None:
    """Clear the cached settings (forces reload)."""
    global _cached_settings
    _cached_settings = None
    logger.info("Settings cache cleared")


def get_settings() -> DispatcharrSettings:
    """Get the current Dispatcharr settings."""
    return load_settings()


def log_config_status():
    """Log the current configuration status for debugging."""
    logger.info(f"CONFIG_DIR: {CONFIG_DIR}")
    logger.info(f"CONFIG_FILE: {CONFIG_FILE}")
    logger.info(f"CONFIG_DIR exists: {CONFIG_DIR.exists()}")
    logger.info(f"CONFIG_FILE exists: {CONFIG_FILE.exists()}")
    if CONFIG_DIR.exists():
        try:
            contents = list(CONFIG_DIR.iterdir())
            logger.info(f"CONFIG_DIR contents: {contents}")
        except Exception as e:
            logger.error(f"Failed to list CONFIG_DIR: {e}")


def get_log_level_from_env() -> str:
    """Get log level from environment variable or default to INFO."""
    return os.environ.get("LOG_LEVEL", "INFO").upper()


def set_log_level(level: str) -> None:
    """Set the logging level for all loggers dynamically."""
    level_upper = level.upper()

    # Validate log level
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if level_upper not in valid_levels:
        logger.warning(f"Invalid log level '{level}', using INFO")
        level_upper = "INFO"

    # Get numeric level
    numeric_level = getattr(logging, level_upper)

    # Set root logger level
    logging.getLogger().setLevel(numeric_level)

    # Set level for all existing loggers
    for logger_name in logging.root.manager.loggerDict:
        logger_obj = logging.getLogger(logger_name)
        logger_obj.setLevel(numeric_level)

    logger.info(f"Log level set to {level_upper}")
