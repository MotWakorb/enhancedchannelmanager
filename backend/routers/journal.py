"""
Journal router — execution log endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import logging
from typing import Optional

from fastapi import APIRouter

import journal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/journal", tags=["Journal"])


@router.get("")
async def get_journal_entries(
    page: int = 1,
    page_size: int = 50,
    category: Optional[str] = None,
    action_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    user_initiated: Optional[bool] = None,
):
    """Query journal entries with filtering and pagination."""
    logger.debug("[JOURNAL] GET /journal - page=%s category=%s action_type=%s search=%s", page, category, action_type, search)
    from datetime import datetime

    # Parse date strings to datetime
    date_from_dt = None
    date_to_dt = None
    if date_from:
        try:
            date_from_dt = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
        except ValueError:
            pass  # Invalid date format from client; ignore filter
    if date_to:
        try:
            date_to_dt = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
        except ValueError:
            pass  # Invalid date format from client; ignore filter

    # Validate page_size
    page_size = min(max(page_size, 1), 200)

    return journal.get_entries(
        page=page,
        page_size=page_size,
        category=category,
        action_type=action_type,
        date_from=date_from_dt,
        date_to=date_to_dt,
        search=search,
        user_initiated=user_initiated,
    )


@router.get("/stats")
async def get_journal_stats():
    """Get summary statistics for the journal."""
    logger.debug("[JOURNAL] GET /journal/stats")
    return journal.get_stats()


@router.delete("/purge")
async def purge_journal_entries(days: int = 90):
    """Delete journal entries older than the specified number of days."""
    logger.debug("[JOURNAL] DELETE /journal/purge - days=%s", days)
    deleted_count = journal.purge_old_entries(days=days)
    logger.info("[JOURNAL] Purged journal entries older than %s days count=%s", days, deleted_count)
    return {"deleted": deleted_count, "days": days}
