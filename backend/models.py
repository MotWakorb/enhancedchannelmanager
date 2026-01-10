"""
SQLAlchemy ORM models for the Journal and Bandwidth tracking features.
"""
from datetime import datetime, date
from sqlalchemy import Column, Integer, BigInteger, String, Text, Boolean, DateTime, Date, Index
from database import Base


class JournalEntry(Base):
    """
    Represents a single change entry in the journal.
    Tracks all modifications to channels, EPG sources, and M3U accounts.
    """
    __tablename__ = "journal_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    category = Column(String(20), nullable=False)  # "channel", "epg", "m3u"
    action_type = Column(String(30), nullable=False)  # "create", "update", "delete", etc.
    entity_id = Column(Integer, nullable=True)  # ID of the affected entity
    entity_name = Column(String(255), nullable=False)  # Human-readable name
    description = Column(Text, nullable=False)  # Human-readable change description
    before_value = Column(Text, nullable=True)  # JSON of previous state
    after_value = Column(Text, nullable=True)  # JSON of new state
    user_initiated = Column(Boolean, default=True, nullable=False)  # Manual vs automatic
    batch_id = Column(String(50), nullable=True)  # Groups related changes

    # Indexes for common queries
    __table_args__ = (
        Index("idx_journal_timestamp", timestamp.desc()),
        Index("idx_journal_category", category),
        Index("idx_journal_action_type", action_type),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        import json
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() + "Z" if self.timestamp else None,
            "category": self.category,
            "action_type": self.action_type,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "description": self.description,
            "before_value": json.loads(self.before_value) if self.before_value else None,
            "after_value": json.loads(self.after_value) if self.after_value else None,
            "user_initiated": self.user_initiated,
            "batch_id": self.batch_id,
        }

    def __repr__(self):
        return f"<JournalEntry(id={self.id}, category={self.category}, action={self.action_type}, entity={self.entity_name})>"


class BandwidthDaily(Base):
    """
    Daily aggregated bandwidth statistics.
    One row per day with totals and peaks.
    """
    __tablename__ = "bandwidth_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, unique=True)
    bytes_transferred = Column(BigInteger, default=0, nullable=False)
    peak_channels = Column(Integer, default=0, nullable=False)
    peak_clients = Column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("idx_bandwidth_daily_date", date.desc()),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "date": self.date.isoformat() if self.date else None,
            "bytes_transferred": self.bytes_transferred,
            "peak_channels": self.peak_channels,
            "peak_clients": self.peak_clients,
        }

    def __repr__(self):
        return f"<BandwidthDaily(date={self.date}, bytes={self.bytes_transferred})>"


class ChannelWatchStats(Base):
    """
    Tracks watch counts per channel.
    Each time a channel is seen active in stats, we increment its watch count.
    """
    __tablename__ = "channel_watch_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(String(64), nullable=False, unique=True)  # Dispatcharr channel UUID
    channel_name = Column(String(255), nullable=False)  # Channel name (for display)
    watch_count = Column(Integer, default=0, nullable=False)  # Number of times seen watching
    last_watched = Column(DateTime, nullable=True)  # Last time this channel was active

    __table_args__ = (
        Index("idx_channel_watch_count", watch_count.desc()),
        Index("idx_channel_watch_channel_id", channel_id),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "watch_count": self.watch_count,
            "last_watched": self.last_watched.isoformat() + "Z" if self.last_watched else None,
        }

    def __repr__(self):
        return f"<ChannelWatchStats(channel_id={self.channel_id}, name={self.channel_name}, count={self.watch_count})>"
