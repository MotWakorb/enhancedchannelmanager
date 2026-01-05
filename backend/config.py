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
    # Default channel profile for new channels (None means no default)
    default_channel_profile_id: int | None = None
    # Linked M3U accounts - groups of account IDs that should sync group settings
    # Each inner list is a group of linked account IDs, e.g. [[1, 2], [3, 4, 5]]
    linked_m3u_accounts: list[list[int]] = []

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
