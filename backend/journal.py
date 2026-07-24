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


# Automation marker (enhancedchannelmanager-uliyr follow-up). Request-scoped
# tri-state for ``JournalEntry.automated_client``:
#   True  — the request self-declared as an automated client via the
#           ``X-ECM-Automated-Client`` header (the backend E2E harness).
#   False — an /api/* request WITHOUT the header — a real UI/MCP operator.
#           An operator can NEVER be classified automated: classification
#           requires the client itself to send the header.
#   None  — outside any HTTP request (scheduler, pipelines, bandwidth
#           tracker) → the row's column stays NULL.
# Header-declared rather than principal-based (unlike ``mutation_source``)
# because the E2E harness deliberately authenticates as a real user — the
# bearer credential cannot distinguish it. The failure direction is safe:
# a forgotten header classifies rows as operator (kept), never as
# purgeable.
_automated_client_var: contextvars.ContextVar[Optional[bool]] = contextvars.ContextVar(
    "ecm_journal_automated_client", default=None
)


def set_automated_client(value: Optional[bool]) -> contextvars.Token:
    """Set the request-scoped automation marker. Returns a reset token."""
    return _automated_client_var.set(value)


def reset_automated_client(token: contextvars.Token) -> None:
    """Reset the automation marker using a token from :func:`set_automated_client`."""
    _automated_client_var.reset(token)


def _resolve_automated_client(explicit: Optional[bool]) -> Optional[bool]:
    """Pick the automation marker to persist: explicit wins, else the request var."""
    if explicit is not None:
        return explicit
    return _automated_client_var.get()


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
    automated_client: Optional[bool] = None,
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
        automated_client=_resolve_automated_client(automated_client),
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
    session: Session | None = None
    try:
        session = get_session()
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
        return entry
    except Exception as e:
        logger.exception("[JOURNAL] Failed to log journal entry: %s", e)
        if session is not None:
            session.rollback()
        return None
    finally:
        if session is not None:
            session.close()


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


# =========================================================================
# Automated-noise purge (enhancedchannelmanager-uliyr)
# =========================================================================
# PO policy decisions 2026-07-17 (uliyr: buckets a/b) and 2026-07-19
# (gjb01: buckets c/d): auto-purge entries older than 3 days for exactly
# FOUR automated-noise buckets. Everything else is untouched — the
# manual Purge control (``purge_old_entries`` via DELETE /api/journal/purge)
# remains the tool for all other categories.
#
# Bucket (a) — watch events. ``category="watch"`` is written ONLY by
# ``bandwidth_tracker`` (watch start / watch stop), always with
# ``user_initiated=False``. The action-type allowlist plus the
# ``user_initiated == False`` guard mean any future operator-initiated or
# unrecognized watch row survives the sweep.
#
# Bucket (b) — Channel Pipeline rule create/delete pairs.
# ``category="auto_creation"`` with ``action_type`` "create"/"delete" is
# written ONLY by the rule create/delete endpoints in
# ``routers/channel_pipeline.py``; in practice the bucket is dominated by
# E2E test-rule churn (e.g. 'E2E event_sync happy path'). PO follow-up
# decision 2026-07-19: operator-initiated rows are KEPT — the
# ``automated_client`` marker separates them (the E2E driver authenticates
# like an operator, so ``mutation_source``/``user_initiated`` cannot).
# The predicate is ``automated_client IS NOT FALSE``:
#   True  (self-declared automated harness traffic)      → purged
#   NULL  (pre-marker legacy rows — the PO's original
#          measured population; shrinks to zero)          → purged
#   False (a request without the automation header —
#          a real UI/MCP operator)                        → KEPT
# All other auto_creation action types (update, bulk_update, import,
# rollback, restore_snapshot, circuit_breaker_reset) survive regardless
# of marker.
#
# Bucket (c) — run-on-refresh suppression notices (gjb01).
# ``category="auto_creation"``, ``action_type="run_on_refresh_skipped"``
# is written ONLY by the circuit-breaker / break-glass suppression path in
# ``tasks/channel_pipeline.py`` (always ``user_initiated=False``). The
# ``user_initiated == False`` guard mirrors the watch bucket: any
# hypothetical operator-initiated row is not provably automated noise and
# survives.
#
# Bucket (d) — task start/complete lifecycle rows (gjb01).
# ``category="task"`` is written ONLY by ``task_engine.py``, with exactly
# five action types: start, complete, cancel, fail, error. Only the
# routine start/complete pair from SCHEDULED runs
# (``user_initiated=False`` — task_engine writes
# ``user_initiated=(triggered_by == "manual")``) ages out:
#   - cancel/fail/error are anomaly forensics and are NOT in the
#     allowlist — they survive regardless of age;
#   - manually-triggered runs (``user_initiated=True``) are operator
#     audit trail and are KEPT, the same fail-safe direction as bucket
#     (b)'s operator rows.
# The richer per-run forensic record (duration, counts, error, details)
# lives in the ``task_executions`` table (CleanupTask's
# ``task_history_days``, default 30), so purging these journal rows
# does not lose execution history.
NOISE_RETENTION_DEFAULT_DAYS = 3
NOISE_WATCH_CATEGORY = "watch"
NOISE_WATCH_ACTION_TYPES = ("start", "stop")
NOISE_PIPELINE_CATEGORY = "auto_creation"
NOISE_PIPELINE_ACTION_TYPES = ("create", "delete")
NOISE_REFRESH_SKIPPED_CATEGORY = "auto_creation"
NOISE_REFRESH_SKIPPED_ACTION_TYPE = "run_on_refresh_skipped"
NOISE_TASK_CATEGORY = "task"
NOISE_TASK_ACTION_TYPES = ("start", "complete")


def purge_noise_entries(
    days: int = NOISE_RETENTION_DEFAULT_DAYS,
    purge_watch_events: bool = True,
    purge_pipeline_rule_pairs: bool = True,
    purge_run_on_refresh_skipped: bool = True,
    purge_task_start_complete: bool = True,
) -> dict[str, int]:
    """
    Delete automated-noise journal entries older than ``days`` days.

    Scoped STRICTLY to the four PO-decided noise buckets (see module
    comment above); every other category/action combination survives.
    Cutoff math matches :func:`purge_old_entries`: UTC now minus ``days``,
    strict ``timestamp < cutoff``.

    Args:
        days: Retention window in days; must be >= 1 (a scheduled deleter
            must never be misconfigured into sweeping fresh rows).
        purge_watch_events: Include the watch start/stop bucket.
        purge_pipeline_rule_pairs: Include the Channel Pipeline rule
            create/delete bucket.
        purge_run_on_refresh_skipped: Include the run-on-refresh
            suppression-notice bucket.
        purge_task_start_complete: Include the scheduled-task
            start/complete lifecycle bucket.

    Returns:
        Per-bucket deleted counts:
        ``{"watch_events": n, "pipeline_rule_pairs": m,
        "run_on_refresh_skipped": k, "task_start_complete": j}``
    """
    if days < 1:
        raise ValueError(f"Noise purge retention must be >= 1 day, got {days}")

    cutoff_date = datetime.utcnow() - timedelta(days=days)
    counts = {
        "watch_events": 0,
        "pipeline_rule_pairs": 0,
        "run_on_refresh_skipped": 0,
        "task_start_complete": 0,
    }
    logger.debug(
        "[JOURNAL] Purging noise entries older than %s days (watch=%s "
        "pipeline_pairs=%s refresh_skipped=%s task_start_complete=%s)",
        days, purge_watch_events, purge_pipeline_rule_pairs,
        purge_run_on_refresh_skipped, purge_task_start_complete,
    )
    session: Session = get_session()
    try:
        if purge_watch_events:
            counts["watch_events"] = (
                session.query(JournalEntry)
                .filter(
                    JournalEntry.category == NOISE_WATCH_CATEGORY,
                    JournalEntry.action_type.in_(NOISE_WATCH_ACTION_TYPES),
                    JournalEntry.user_initiated.is_(False),
                    JournalEntry.timestamp < cutoff_date,
                )
                .delete(synchronize_session=False)
            )
        if purge_pipeline_rule_pairs:
            counts["pipeline_rule_pairs"] = (
                session.query(JournalEntry)
                .filter(
                    JournalEntry.category == NOISE_PIPELINE_CATEGORY,
                    JournalEntry.action_type.in_(NOISE_PIPELINE_ACTION_TYPES),
                    # Operator-marked rows (False) are kept; automated (True)
                    # and pre-marker legacy (NULL) rows age out. See the
                    # bucket (b) comment above.
                    JournalEntry.automated_client.isnot(False),
                    JournalEntry.timestamp < cutoff_date,
                )
                .delete(synchronize_session=False)
            )
        if purge_run_on_refresh_skipped:
            counts["run_on_refresh_skipped"] = (
                session.query(JournalEntry)
                .filter(
                    JournalEntry.category == NOISE_REFRESH_SKIPPED_CATEGORY,
                    JournalEntry.action_type == NOISE_REFRESH_SKIPPED_ACTION_TYPE,
                    JournalEntry.user_initiated.is_(False),
                    JournalEntry.timestamp < cutoff_date,
                )
                .delete(synchronize_session=False)
            )
        if purge_task_start_complete:
            counts["task_start_complete"] = (
                session.query(JournalEntry)
                .filter(
                    JournalEntry.category == NOISE_TASK_CATEGORY,
                    JournalEntry.action_type.in_(NOISE_TASK_ACTION_TYPES),
                    # Manually-triggered runs (user_initiated=True) are
                    # operator audit trail and are KEPT; cancel/fail/error
                    # rows never match the allowlist. See bucket (d) above.
                    JournalEntry.user_initiated.is_(False),
                    JournalEntry.timestamp < cutoff_date,
                )
                .delete(synchronize_session=False)
            )
        session.commit()
        logger.info(
            "[JOURNAL] Noise purge deleted %s watch event, %s pipeline rule "
            "create/delete, %s run-on-refresh-skipped, and %s task "
            "start/complete entries older than %s days",
            counts["watch_events"], counts["pipeline_rule_pairs"],
            counts["run_on_refresh_skipped"], counts["task_start_complete"],
            days,
        )
        return counts
    except Exception as e:
        logger.exception("[JOURNAL] Failed to purge noise journal entries: %s", e)
        session.rollback()
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
