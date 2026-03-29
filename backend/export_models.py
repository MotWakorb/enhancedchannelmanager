"""
SQLAlchemy ORM models for the Export feature.
Tables: playlist_profiles, cloud_storage_targets, publish_configurations, publish_history.
"""
import json
import logging
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Index
from database import Base

logger = logging.getLogger(__name__)


class PlaylistProfile(Base):
    """Named export profile with channel/group selection and output options."""
    __tablename__ = "playlist_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    # Selection: "all", "groups", or "channels"
    selection_mode = Column(String(20), nullable=False, default="all")
    # JSON arrays of selected group IDs or channel IDs
    selected_groups = Column(Text, nullable=False, default="[]")
    selected_channels = Column(Text, nullable=False, default="[]")
    # Output options
    stream_url_mode = Column(String(20), nullable=False, default="direct")  # "direct" or "proxy"
    include_logos = Column(Boolean, nullable=False, default=True)
    include_epg_ids = Column(Boolean, nullable=False, default=True)
    include_channel_numbers = Column(Boolean, nullable=False, default=True)
    sort_order = Column(String(20), nullable=False, default="number")  # "name", "number", "group"
    filename_prefix = Column(String(255), nullable=False, default="playlist")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def get_selected_groups(self) -> list[int]:
        return json.loads(self.selected_groups) if self.selected_groups else []

    def get_selected_channels(self) -> list[int]:
        return json.loads(self.selected_channels) if self.selected_channels else []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "selection_mode": self.selection_mode,
            "selected_groups": self.get_selected_groups(),
            "selected_channels": self.get_selected_channels(),
            "stream_url_mode": self.stream_url_mode,
            "include_logos": self.include_logos,
            "include_epg_ids": self.include_epg_ids,
            "include_channel_numbers": self.include_channel_numbers,
            "sort_order": self.sort_order,
            "filename_prefix": self.filename_prefix,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<PlaylistProfile(id={self.id}, name={self.name})>"


class CloudStorageTarget(Base):
    """Cloud storage destination for published exports."""
    __tablename__ = "cloud_storage_targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    # Provider: "s3", "gdrive", "onedrive", "dropbox"
    provider_type = Column(String(20), nullable=False)
    # Encrypted JSON with provider-specific credentials
    credentials = Column(Text, nullable=False, default="{}")
    upload_path = Column(String(500), nullable=False, default="/")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self, mask_credentials: bool = True) -> dict:
        # Credentials are stored encrypted — don't try to parse here.
        # The router layer handles decryption and masking.
        return {
            "id": self.id,
            "name": self.name,
            "provider_type": self.provider_type,
            "credentials": {} if mask_credentials else self.credentials,
            "upload_path": self.upload_path,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<CloudStorageTarget(id={self.id}, name={self.name}, provider={self.provider_type})>"


class PublishConfiguration(Base):
    """Links a playlist profile to a cloud target with scheduling."""
    __tablename__ = "publish_configurations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    profile_id = Column(Integer, ForeignKey("playlist_profiles.id", ondelete="CASCADE"), nullable=False)
    # Nullable target — None means local-only export
    target_id = Column(Integer, ForeignKey("cloud_storage_targets.id", ondelete="SET NULL"), nullable=True)
    # Schedule: "manual", "cron", "event"
    schedule_type = Column(String(20), nullable=False, default="manual")
    cron_expression = Column(String(100), nullable=True)
    # JSON array of event trigger types (e.g. ["m3u_refresh", "channel_edit", "epg_refresh"])
    event_triggers = Column(Text, nullable=False, default="[]")
    enabled = Column(Boolean, nullable=False, default=True)
    # Optional webhook URL for publish completion notifications
    webhook_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_publish_config_profile", profile_id),
    )

    def get_event_triggers(self) -> list[str]:
        return json.loads(self.event_triggers) if self.event_triggers else []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "profile_id": self.profile_id,
            "target_id": self.target_id,
            "schedule_type": self.schedule_type,
            "cron_expression": self.cron_expression,
            "event_triggers": self.get_event_triggers(),
            "enabled": self.enabled,
            "webhook_url": self.webhook_url,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<PublishConfiguration(id={self.id}, name={self.name})>"


class PublishHistory(Base):
    """Audit log for publish operations."""
    __tablename__ = "publish_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_id = Column(Integer, ForeignKey("publish_configurations.id", ondelete="CASCADE"), nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    # Status: "running", "success", "failed"
    status = Column(String(20), nullable=False, default="running")
    channels_count = Column(Integer, nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    # JSON with extra details (file paths, upload info, etc.)
    details = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_publish_history_config", config_id),
        Index("idx_publish_history_started", started_at.desc()),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "config_id": self.config_id,
            "started_at": self.started_at.isoformat() + "Z" if self.started_at else None,
            "completed_at": self.completed_at.isoformat() + "Z" if self.completed_at else None,
            "status": self.status,
            "channels_count": self.channels_count,
            "file_size_bytes": self.file_size_bytes,
            "error_message": self.error_message,
            "details": json.loads(self.details) if self.details else None,
        }

    def __repr__(self):
        return f"<PublishHistory(id={self.id}, config_id={self.config_id}, status={self.status})>"
