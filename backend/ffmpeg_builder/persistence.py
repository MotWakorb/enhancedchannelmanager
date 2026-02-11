"""
FFMPEG Builder persistence module.

Provides CRUD operations for saved FFMPEG configurations with SQLAlchemy,
including search, pagination, and JSON export/import.
"""
import json
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Column, Integer, String, Text, DateTime, func
from sqlalchemy.orm import Session

from database import Base


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConfigNotFoundError(Exception):
    """Raised when a saved configuration is not found."""


class ConfigValidationError(Exception):
    """Raised when a saved configuration fails validation."""


# ---------------------------------------------------------------------------
# SQLAlchemy Model
# ---------------------------------------------------------------------------

MAX_NAME_LENGTH = 255

REQUIRED_STATE_KEYS = {"input", "output", "videoCodec", "audioCodec"}


class SavedConfig(Base):
    """A saved FFMPEG builder configuration."""

    __tablename__ = "ffmpeg_saved_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(MAX_NAME_LENGTH), nullable=False)
    description = Column(Text, nullable=True)
    config_json = Column(Text, nullable=False)  # JSON-encoded builder state
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    @property
    def config(self) -> dict:
        """Deserialize the stored JSON config."""
        return json.loads(self.config_json) if self.config_json else {}

    @config.setter
    def config(self, value: dict) -> None:
        """Serialize and store the config dict as JSON."""
        self.config_json = json.dumps(value)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_name(name: str) -> None:
    if not name or not name.strip():
        raise ConfigValidationError("Configuration name is required")
    if len(name) > MAX_NAME_LENGTH:
        raise ConfigValidationError(
            f"Configuration name exceeds maximum length ({MAX_NAME_LENGTH})"
        )


def _validate_config_structure(config: dict) -> None:
    if not isinstance(config, dict):
        raise ConfigValidationError("Config must be a dict")
    missing = REQUIRED_STATE_KEYS - set(config.keys())
    if missing:
        raise ConfigValidationError(
            f"Config is missing required keys: {', '.join(sorted(missing))}"
        )


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------

def create_config(
    session: Session,
    name: str,
    config: dict,
    description: Optional[str] = None,
) -> SavedConfig:
    """Create and persist a new saved configuration.

    Args:
        session: SQLAlchemy session.
        name: Configuration name.
        config: Builder state dict.
        description: Optional description.

    Returns:
        The newly created SavedConfig.

    Raises:
        ConfigValidationError: If name or config is invalid.
    """
    if config is None:
        raise ConfigValidationError("Config is required")
    _validate_name(name)
    _validate_config_structure(config)

    saved = SavedConfig(
        name=name,
        description=description,
        config_json=json.dumps(config),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(saved)
    session.commit()
    session.refresh(saved)
    return saved


def get_config(session: Session, config_id: int) -> SavedConfig:
    """Retrieve a saved configuration by ID.

    Raises:
        ConfigNotFoundError: If no config exists with the given ID.
    """
    config = session.query(SavedConfig).filter(SavedConfig.id == config_id).first()
    if config is None:
        raise ConfigNotFoundError(f"Config with id {config_id} not found")
    return config


def update_config(
    session: Session,
    config_id: int,
    name: Optional[str] = None,
    config: Optional[dict] = None,
    description: Optional[str] = None,
) -> SavedConfig:
    """Update a saved configuration.

    Args:
        session: SQLAlchemy session.
        config_id: ID of the config to update.
        name: New name (optional).
        config: New builder state dict (optional).
        description: New description (optional).

    Returns:
        The updated SavedConfig.

    Raises:
        ConfigNotFoundError: If the config doesn't exist.
    """
    saved = get_config(session, config_id)

    if name is not None:
        _validate_name(name)
        saved.name = name
    if config is not None:
        _validate_config_structure(config)
        saved.config_json = json.dumps(config)
    if description is not None:
        saved.description = description

    saved.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(saved)
    return saved


def delete_config(session: Session, config_id: int) -> None:
    """Delete a saved configuration.

    Raises:
        ConfigNotFoundError: If the config doesn't exist.
    """
    saved = get_config(session, config_id)
    session.delete(saved)
    session.commit()


def list_configs(
    session: Session,
    offset: int = 0,
    limit: int = 100,
) -> List[SavedConfig]:
    """List saved configurations with pagination.

    Args:
        session: SQLAlchemy session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        List of SavedConfig instances.
    """
    return (
        session.query(SavedConfig)
        .order_by(SavedConfig.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_configs(
    session: Session,
    query: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[SavedConfig]:
    """Search saved configurations by name/description and date range.

    Args:
        session: SQLAlchemy session.
        query: Text to search for in name and description.
        date_from: Filter configs created on or after this date.
        date_to: Filter configs created on or before this date.

    Returns:
        List of matching SavedConfig instances.
    """
    q = session.query(SavedConfig)

    if query:
        pattern = f"%{query}%"
        q = q.filter(
            SavedConfig.name.ilike(pattern)
            | SavedConfig.description.ilike(pattern)
        )

    if date_from:
        q = q.filter(SavedConfig.created_at >= date_from)
    if date_to:
        q = q.filter(SavedConfig.created_at <= date_to)

    return q.order_by(SavedConfig.created_at.desc()).all()


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------

def export_config_json(session: Session, config_id: int) -> str:
    """Export a saved configuration as a JSON string.

    Args:
        session: SQLAlchemy session.
        config_id: ID of the config to export.

    Returns:
        JSON string representation of the config.

    Raises:
        ConfigNotFoundError: If the config doesn't exist.
    """
    saved = get_config(session, config_id)
    return json.dumps({
        "name": saved.name,
        "description": saved.description,
        "config": saved.config,
    })


def import_config_json(session: Session, json_str: str) -> SavedConfig:
    """Import a configuration from a JSON string.

    Args:
        session: SQLAlchemy session.
        json_str: JSON string containing name, description, and config.

    Returns:
        The newly created SavedConfig.

    Raises:
        ConfigValidationError: If the JSON structure is invalid.
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ConfigValidationError(f"Invalid JSON: {exc}")

    if not isinstance(data, dict):
        raise ConfigValidationError("Imported data must be a JSON object")

    name = data.get("name", "")
    config = data.get("config")
    description = data.get("description")

    if not name:
        raise ConfigValidationError("Imported config must have a 'name' field")
    if not config:
        raise ConfigValidationError("Imported config must have a 'config' field")

    return create_config(
        session,
        name=name,
        config=config,
        description=description,
    )
