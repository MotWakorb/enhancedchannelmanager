"""
Journal service layer for logging and querying change entries.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Any
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from database import get_session
from models import JournalEntry

logger = logging.getLogger(__name__)


def log_entry(
    category: str,
    action_type: str,
    entity_name: str,
    description: str,
    entity_id: Optional[int] = None,
    before_value: Optional[dict] = None,
    after_value: Optional[dict] = None,
    user_initiated: bool = True,
    batch_id: Optional[str] = None,
) -> Optional[JournalEntry]:
    """
    Log a change entry to the journal.

    Args:
        category: Type of entity ("channel", "epg", "m3u")
        action_type: Type of action ("create", "update", "delete", etc.)
        entity_name: Human-readable name of the entity
        description: Human-readable description of the change
        entity_id: ID of the affected entity (optional)
        before_value: Previous state as dict (optional)
        after_value: New state as dict (optional)
        user_initiated: True if manual action, False if automatic
        batch_id: ID to group related changes (optional)

    Returns:
        The created JournalEntry or None if failed
    """
    try:
        session: Session = get_session()
        entry = JournalEntry(
            timestamp=datetime.utcnow(),
            category=category,
            action_type=action_type,
            entity_id=entity_id,
            entity_name=entity_name,
            description=description,
            before_value=json.dumps(before_value) if before_value else None,
            after_value=json.dumps(after_value) if after_value else None,
            user_initiated=user_initiated,
            batch_id=batch_id,
        )
        session.add(entry)
        session.commit()
        logger.debug(f"Journal entry logged: {category}/{action_type} - {entity_name}")
        session.close()
        return entry
    except Exception as e:
        logger.error(f"Failed to log journal entry: {e}")
        return None


def get_entries(
    page: int = 1,
    page_size: int = 50,
    category: Optional[str] = None,
    action_type: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    search: Optional[str] = None,
    user_initiated: Optional[bool] = None,
) -> dict[str, Any]:
    """
    Query journal entries with filtering and pagination.

    Returns:
        Dict with count, page, page_size, total_pages, and results
    """
    session: Session = get_session()
    try:
        query = session.query(JournalEntry)

        # Apply filters
        if category:
            query = query.filter(JournalEntry.category == category)
        if action_type:
            query = query.filter(JournalEntry.action_type == action_type)
        if date_from:
            query = query.filter(JournalEntry.timestamp >= date_from)
        if date_to:
            query = query.filter(JournalEntry.timestamp <= date_to)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (JournalEntry.entity_name.ilike(search_pattern)) |
                (JournalEntry.description.ilike(search_pattern))
            )
        if user_initiated is not None:
            query = query.filter(JournalEntry.user_initiated == user_initiated)

        # Get total count
        total_count = query.count()

        # Calculate pagination
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
        offset = (page - 1) * page_size

        # Get paginated results (newest first)
        entries = query.order_by(desc(JournalEntry.timestamp)).offset(offset).limit(page_size).all()

        return {
            "count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "results": [entry.to_dict() for entry in entries],
        }
    finally:
        session.close()


def get_stats() -> dict[str, Any]:
    """
    Get summary statistics for the journal.

    Returns:
        Dict with total_entries, by_category, by_action_type, and date_range
    """
    session: Session = get_session()
    try:
        # Total count
        total_count = session.query(func.count(JournalEntry.id)).scalar() or 0

        # Count by category
        category_counts = (
            session.query(JournalEntry.category, func.count(JournalEntry.id))
            .group_by(JournalEntry.category)
            .all()
        )
        by_category = {cat: count for cat, count in category_counts}

        # Count by action type
        action_counts = (
            session.query(JournalEntry.action_type, func.count(JournalEntry.id))
            .group_by(JournalEntry.action_type)
            .all()
        )
        by_action_type = {action: count for action, count in action_counts}

        # Date range
        oldest = session.query(func.min(JournalEntry.timestamp)).scalar()
        newest = session.query(func.max(JournalEntry.timestamp)).scalar()

        return {
            "total_entries": total_count,
            "by_category": by_category,
            "by_action_type": by_action_type,
            "date_range": {
                "oldest": oldest.isoformat() + "Z" if oldest else None,
                "newest": newest.isoformat() + "Z" if newest else None,
            },
        }
    finally:
        session.close()


def purge_old_entries(days: int = 90) -> int:
    """
    Delete journal entries older than the specified number of days.

    Args:
        days: Delete entries older than this many days

    Returns:
        Number of entries deleted
    """
    session: Session = get_session()
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        deleted_count = (
            session.query(JournalEntry)
            .filter(JournalEntry.timestamp < cutoff_date)
            .delete()
        )
        session.commit()
        logger.info(f"Purged {deleted_count} journal entries older than {days} days")
        return deleted_count
    finally:
        session.close()
