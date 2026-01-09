"""
SQLAlchemy ORM models for the Journal feature.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Index
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
