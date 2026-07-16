"""
Journal router — execution log endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Query

import journal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/journal", tags=["Journal"])


@router.get("")
async def get_journal_entries(
    # Bounds enforced here (bead enhancedchannelmanager-g4z2h, systemic sibling
    # of 1a5mf): page<1 previously produced a negative SQL OFFSET instead of a
    # clean 422. Upper bound matches the largest page-size option the Journal
    # tab's UI offers (JournalTab.tsx CustomSelect: 25/50/100/250) — replaces
    # the old silent `min(page_size, 200)` clamp, which would have quietly
    # truncated that 250 option to 200 results.
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(50, ge=1, le=250, description="Results per page"),
    category: Optional[str] = None,
    action_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    user_initiated: Optional[bool] = None,
    batch_id: Optional[str] = Query(
        None,
        description="Filter to a single batched operation's journal rows. Either an 8-char batch_id from bulk handlers (e.g. POST /api/auto-creation/rules/bulk-update) or an auto-creation run's execution_id (as a string) for per-merge audit rows written by the executor (bd-0emgo.5). Indexed lookup via idx_journal_batch_id.",
    ),
    mutation_source: Optional[str] = Query(
        None,
        description="Filter by actor/origin of the mutation (enhancedchannelmanager-vp1rx / W3): 'ui' (operator via the web UI), 'mcp_ai' (AI agent via the static MCP key), 'scheduler' (a scheduled task), or 'auto_creation' (the auto-creation pipeline). Indexed lookup via idx_journal_mutation_source. Legacy/unknown rows have a NULL source and are excluded by any value filter.",
    ),
):
    """Query journal entries with filtering and pagination."""
    logger.debug(
        "[JOURNAL] GET /journal - page=%s category=%s action_type=%s search=%s batch_id=%s mutation_source=%s",
        page, category, action_type, search, batch_id, mutation_source,
    )
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

    return journal.get_entries(
        page=page,
        page_size=page_size,
        category=category,
        action_type=action_type,
        date_from=date_from_dt,
        date_to=date_to_dt,
        search=search,
        user_initiated=user_initiated,
        batch_id=batch_id,
        mutation_source=mutation_source,
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
