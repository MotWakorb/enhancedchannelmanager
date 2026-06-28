"""
Journal service layer for logging and querying change entries.
"""
import contextvars
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Any
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from database import get_session
from models import JournalEntry

logger = logging.getLogger(__name__)


# =========================================================================
# Mutation-source (actor/origin) plumbing — enhancedchannelmanager-vp1rx / W3
# =========================================================================
# WHO/WHAT initiated a journaled mutation. Resolved ONCE per HTTP request by
# the actor-source middleware (``main.py``) from the auth principal — a request
# bearing the static MCP key is ``"mcp_ai"``, a JWT is ``"ui"`` — and stashed
# in a contextvar so the ~40 ``journal.log_entry`` call sites pick it up
# automatically without each one having to thread the actor through. (A missed
# call site would silently mislabel an audit row, which is worse than no audit
# at all — so the default has to flow implicitly, not depend on every author
# remembering to pass it.) A contextvar (not thread-local) is used so the value
# survives ``await`` boundaries in async handlers, mirroring the trace-id
# contextvar in ``observability.py``.
#
# Internal, non-HTTP paths (the scheduler, the auto-creation pipeline) carry no
# request context, so they stamp ``mutation_source`` EXPLICITLY at their call
# sites instead of relying on the contextvar.
MUTATION_SOURCE_UI = "ui"
MUTATION_SOURCE_MCP_AI = "mcp_ai"
MUTATION_SOURCE_SCHEDULER = "scheduler"
MUTATION_SOURCE_AUTO_CREATION = "auto_creation"

_mutation_source_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "ecm_mutation_source", default=None
)

# Request-scoped correlation id for a multi-call bulk operation. The MCP
# ``bulk_delete_channels`` tool loops the single-channel DELETE endpoint, so the
# only way N deletes share one ``batch_id`` is for the client to send a single
# ``X-ECM-Batch-Id`` header for the whole loop, which the middleware stashes
# here. ``log_entry`` then folds it in as the default ``batch_id`` so the N rows
# are one correlatable batch via ``get_journal(batch_id=...)`` — reusing the
# existing batch_id primitive rather than inventing a new correlation store.
_batch_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "ecm_journal_batch_id", default=None
)


def get_request_batch_id() -> Optional[str]:
    """Return the current request's correlation batch id, or ``None``."""
    return _batch_id_var.get()


def set_request_batch_id(batch_id: Optional[str]) -> contextvars.Token:
    """Set the request-scoped correlation batch id. Returns a reset token."""
    return _batch_id_var.set(batch_id)


def reset_request_batch_id(token: contextvars.Token) -> None:
    """Reset the request-scoped batch id using a token from :func:`set_request_batch_id`."""
    _batch_id_var.reset(token)


def _resolve_batch_id(explicit: Optional[str]) -> Optional[str]:
    """Pick the batch_id to persist: an explicit value wins, else the request var."""
    if explicit is not None:
        return explicit
    return _batch_id_var.get()


def get_mutation_source() -> Optional[str]:
    """Return the current request's actor/origin, or ``None`` outside a request."""
    return _mutation_source_var.get()


def set_mutation_source(source: Optional[str]) -> contextvars.Token:
    """Set the actor/origin for the current context. Returns a reset token."""
    return _mutation_source_var.set(source)


def reset_mutation_source(token: contextvars.Token) -> None:
    """Reset the actor/origin using a token from :func:`set_mutation_source`."""
    _mutation_source_var.reset(token)


def _resolve_mutation_source(explicit: Optional[str]) -> Optional[str]:
    """Pick the actor/origin to persist on a journal row.

    An ``explicit`` value (passed by an internal scheduler/auto-creation call
    site, or a test) always wins. Otherwise fall back to the request-scoped
    contextvar set by the middleware. ``None`` when neither is present — a
    legacy/unknown actor, which reads back NULL.
    """
    if explicit is not None:
        return explicit
    return _mutation_source_var.get()


def _build_journal_entry(
    category: str,
    action_type: str,
    entity_name: str,
    description: str,
    entity_id: Optional[int] = None,
    before_value: Optional[dict] = None,
    after_value: Optional[dict] = None,
    user_initiated: bool = True,
    batch_id: Optional[str] = None,
    mutation_source: Optional[str] = None,
) -> JournalEntry:
    return JournalEntry(
        timestamp=datetime.utcnow(),
        category=category,
        action_type=action_type,
        entity_id=entity_id,
        entity_name=entity_name,
        description=description,
        before_value=json.dumps(before_value) if before_value else None,
        after_value=json.dumps(after_value) if after_value else None,
        user_initiated=user_initiated,
        batch_id=_resolve_batch_id(batch_id),
        mutation_source=_resolve_mutation_source(mutation_source),
    )


def log_entries(entries: list[dict[str, Any]]) -> bool:
    """Log multiple change entries in one transaction."""
    if not entries:
        return True

    session: Session = get_session()
    try:
        journal_entries = [
            _build_journal_entry(
                category=entry.get("category"),
                action_type=entry.get("action_type"),
                entity_name=entry.get("entity_name"),
                description=entry.get("description"),
                entity_id=entry.get("entity_id"),
                before_value=entry.get("before_value"),
                after_value=entry.get("after_value"),
                user_initiated=entry.get("user_initiated", True),
                batch_id=entry.get("batch_id"),
                mutation_source=entry.get("mutation_source"),
            )
            for entry in entries
        ]
        logger.debug("[JOURNAL] Creating batch entries: count=%s", len(journal_entries))
        session.add_all(journal_entries)
        session.commit()
        logger.debug("[JOURNAL] Batch logged: count=%s", len(journal_entries))
        return True
    except Exception as e:
        logger.exception("[JOURNAL] Failed to log journal entries batch: %s", e)
        session.rollback()
        return False
    finally:
        session.close()


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
    mutation_source: Optional[str] = None,
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
        mutation_source: Actor/origin ("ui", "mcp_ai", "scheduler",
            "auto_creation"). When omitted, falls back to the request-scoped
            actor resolved by the middleware (``None`` outside a request).

    Returns:
        The created JournalEntry or None if failed
    """
    try:
        session: Session = get_session()
        logger.debug(
            "[JOURNAL] Creating entry: category=%s action=%s entity=%r (id=%s) user_initiated=%s%s",
            category, action_type, entity_name, entity_id, user_initiated,
            (" batch_id=%s" % batch_id) if batch_id else ""
        )
        entry = _build_journal_entry(
            category=category,
            action_type=action_type,
            entity_name=entity_name,
            description=description,
            entity_id=entity_id,
            before_value=before_value,
            after_value=after_value,
            user_initiated=user_initiated,
            batch_id=batch_id,
            mutation_source=mutation_source,
        )
        session.add(entry)
        session.commit()
        logger.debug("[JOURNAL] Entry logged: %s/%s - %s (id=%s)", category, action_type, entity_name, entry.id)
        session.close()
        return entry
    except Exception as e:
        logger.exception("[JOURNAL] Failed to log journal entry: %s", e)
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
    batch_id: Optional[str] = None,
    mutation_source: Optional[str] = None,
) -> dict[str, Any]:
    """
    Query journal entries with filtering and pagination.

    Returns:
        Dict with count, page, page_size, total_pages, and results
    """
    filters_applied = []
    if category:
        filters_applied.append(f"category={category}")
    if action_type:
        filters_applied.append(f"action={action_type}")
    if date_from:
        filters_applied.append(f"from={date_from.isoformat()}")
    if date_to:
        filters_applied.append(f"to={date_to.isoformat()}")
    if search:
        filters_applied.append(f"search={search!r}")
    if user_initiated is not None:
        filters_applied.append(f"user_initiated={user_initiated}")
    if batch_id:
        filters_applied.append(f"batch_id={batch_id}")
    if mutation_source:
        filters_applied.append(f"mutation_source={mutation_source}")

    logger.debug(
        "[JOURNAL] Querying entries: page=%s page_size=%s filters=[%s]",
        page, page_size, ', '.join(filters_applied) if filters_applied else 'none'
    )
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
        if batch_id:
            # Indexed filter on batch_id (idx_journal_batch_id, bd-dmu8w).
            # Surfaces the bulk-operation correlation primitive (bd-91mcq) via the API (bd-s4sph).
            query = query.filter(JournalEntry.batch_id == batch_id)
        if mutation_source:
            # Indexed filter on mutation_source (idx_journal_mutation_source,
            # enhancedchannelmanager-vp1rx / W3). Forensic "show me everything
            # the AI did" — e.g. ?mutation_source=mcp_ai.
            query = query.filter(JournalEntry.mutation_source == mutation_source)

        # Get total count
        total_count = query.count()

        # Calculate pagination
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
        offset = (page - 1) * page_size

        # Get paginated results (newest first)
        entries = query.order_by(desc(JournalEntry.timestamp)).offset(offset).limit(page_size).all()

        logger.info("[JOURNAL] Retrieved %s journal entries (total: %s, page %s/%s)", len(entries), total_count, page, total_pages)
        return {
            "count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "results": [entry.to_dict() for entry in entries],
        }
    except Exception as e:
        logger.exception("[JOURNAL] Failed to query journal entries: %s", e)
        raise
    finally:
        session.close()


def get_stats() -> dict[str, Any]:
    """
    Get summary statistics for the journal.

    Returns:
        Dict with total_entries, by_category, by_action_type, and date_range
    """
    logger.debug("[JOURNAL] Calculating journal statistics")
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

        logger.info("[JOURNAL] Journal stats: %s total entries, %s categories, %s action types", total_count, len(by_category), len(by_action_type))
        return {
            "total_entries": total_count,
            "by_category": by_category,
            "by_action_type": by_action_type,
            "date_range": {
                "oldest": oldest.isoformat() + "Z" if oldest else None,
                "newest": newest.isoformat() + "Z" if newest else None,
            },
        }
    except Exception as e:
        logger.exception("[JOURNAL] Failed to calculate journal stats: %s", e)
        raise
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
    logger.debug("[JOURNAL] Purging journal entries older than %s days", days)
    session: Session = get_session()
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        deleted_count = (
            session.query(JournalEntry)
            .filter(JournalEntry.timestamp < cutoff_date)
            .delete()
        )
        session.commit()
        logger.info("[JOURNAL] Purged %s journal entries older than %s days", deleted_count, days)
        return deleted_count
    except Exception as e:
        logger.exception("[JOURNAL] Failed to purge journal entries: %s", e)
        session.rollback()
        raise
    finally:
        session.close()
