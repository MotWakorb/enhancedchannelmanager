from pydantic import BaseModel
from pydantic_settings import BaseSettings
import json
import os
from pathlib import Path

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
    # Timezone preference: "east", "west", or "both"
    timezone_preference: str = "both"

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


def load_settings() -> DispatcharrSettings:
    """Load settings from file or return defaults."""
    global _cached_settings

    if _cached_settings is not None:
        return _cached_settings

    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            _cached_settings = DispatcharrSettings(**data)
            return _cached_settings
        except Exception:
            pass

    _cached_settings = DispatcharrSettings()
    return _cached_settings


def save_settings(settings: DispatcharrSettings) -> None:
    """Save settings to file."""
    global _cached_settings

    ensure_config_dir()
    CONFIG_FILE.write_text(json.dumps(settings.model_dump(), indent=2))
    _cached_settings = settings


def clear_settings_cache() -> None:
    """Clear the cached settings (forces reload)."""
    global _cached_settings
    _cached_settings = None


def get_settings() -> DispatcharrSettings:
    """Get the current Dispatcharr settings."""
    return load_settings()
