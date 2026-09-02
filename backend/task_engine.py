"""
Task Execution Engine.

Background service that manages and executes scheduled tasks:
- Runs a scheduler loop to check for due tasks
- Executes tasks based on their schedules
- Enforces concurrent task limits
- Records execution history to database
- Provides error handling and retry logic
"""
import asyncio
import copy
import contextlib
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, or_

import journal
from database import get_session
from log_throttle import should_log
from models import TaskExecution
from task_registry import get_registry
from task_scheduler import (
    TaskOutcome,
    TaskResult,
    completion_notification_type,
    execution_status,
    execution_succeeded,
    task_outcome,
)
from journal import log_entry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Execution history is scoped to the target INSTANCE, not the id (bead …-5dp92)
# ---------------------------------------------------------------------------
#
# A per-target cross-instance-sync task is keyed ``dbas_sync_<target_id>``
# (``tasks/dbas_sync.py`` → ``sync_task_id_for``), and ``SyncTarget.id`` is a
# SQLite autoincrement primary key, so it is REUSED after a delete. Deleting a
# target prunes its ``scheduled_tasks`` and ``task_schedules`` rows
# (``remove_sync_target_task``) but not its ``task_executions`` rows — so the
# next target handed the same id opened carrying its predecessor's runs,
# including failures that were not its own, on the surface an operator uses to
# judge whether the target is healthy.
#
# THE INVARIANT these helpers enforce (the specification — delete-then-recreate
# is one example of it, not the definition): a ``task_executions`` row is
# attributed to a per-target sync task only when the live ``SyncTarget`` behind
# that id already existed when the run STARTED; an id with no live target
# behind it has no history at all.
#
# ``started_at`` and not ``completed_at``: a run in flight when its target is
# deleted is deliberately not interrupted, so it can finish after the
# replacement is created. It still is not the replacement's run.
#
# Enforced at the READ rather than by purging on delete. Purging would be a
# cleanup step, not a guarantee — ``remove_sync_target_task`` is best-effort by
# contract, runs after the delete has already committed, and would do nothing
# for installs already carrying orphaned rows. The rows are left in place (the
# ordinary 30-day ``purge_old_history`` retires them) so the audit trail of what
# a since-deleted target actually did is not destroyed.


def _sync_target_id_from_task_id(task_id: Optional[str]) -> Optional[int]:
    """The ``SyncTarget`` id inside ``dbas_sync_<n>``, or None if not one.

    Deliberately NOT a SQL ``LIKE``: in SQL ``_`` is a single-character
    wildcard, so ``LIKE 'dbas_sync_%'`` also matches ids like
    ``dbas_syncx_report`` that this rule has no business touching. The suffix of
    a real per-target id is an integer, and nothing else is.
    """
    from tasks.dbas_sync import SYNC_TASK_ID_PREFIX

    if not task_id or not task_id.startswith(SYNC_TASK_ID_PREFIX):
        return None
    suffix = task_id[len(SYNC_TASK_ID_PREFIX):]
    return int(suffix) if suffix.isdigit() else None


def _live_sync_target_lifetimes(session) -> dict[str, Optional[datetime]]:
    """Per-target sync task id → the instant its live target came into being.

    Key PRESENCE means "a live target owns this id"; a present-but-None value
    means the row carries no creation instant and therefore imposes no time
    restriction (``SyncTarget.created_at`` is NOT NULL, so this is a
    degradation path, and it degrades toward showing a live target's real
    history rather than hiding it).
    """
    from export_models import SyncTarget
    from tasks.dbas_sync import sync_task_id_for

    return {
        sync_task_id_for(target_id): created_at
        for target_id, created_at in session.query(
            SyncTarget.id, SyncTarget.created_at
        ).all()
    }


def _scope_to_sync_target_lifetimes(session, query, task_id: Optional[str]):
    """Apply the instance scope to a ``TaskExecution`` query.

    Returns ``(query, empty)``. ``empty`` is True when the requested id is a
    per-target sync id that no live target owns — there is nothing to show and
    no filter can express "nothing" as cleanly.

    Tasks whose ids are release constants (``epg_refresh``, the legacy shared
    ``dbas_sync`` from bead …-5gzg5) are not touched.
    """
    if task_id is not None:
        # Ordinary tasks are the overwhelming majority of these reads and must
        # not pay for a lookup that cannot apply to them.
        if _sync_target_id_from_task_id(task_id) is None:
            return query, False
        lifetimes = _live_sync_target_lifetimes(session)
        if task_id not in lifetimes:
            return query, True
        started_at_floor = lifetimes[task_id]
        if started_at_floor is None:
            return query, False
        return query.filter(TaskExecution.started_at >= started_at_floor), False

    # The all-tasks feed resolves the same ids to the same target names, so the
    # violation would simply be reachable one screen over. Constrain every
    # per-target sync id PRESENT in the table rather than pattern-matching in
    # SQL (see `_sync_target_id_from_task_id` for why LIKE is wrong here).
    lifetimes = _live_sync_target_lifetimes(session)
    clauses = []
    for (present_id,) in session.query(TaskExecution.task_id).distinct().all():
        if _sync_target_id_from_task_id(present_id) is None:
            continue
        if present_id not in lifetimes:
            clauses.append(TaskExecution.task_id != present_id)
            continue
        started_at_floor = lifetimes[present_id]
        if started_at_floor is None:
            continue
        clauses.append(
            or_(
                TaskExecution.task_id != present_id,
                TaskExecution.started_at >= started_at_floor,
            )
        )
    if clauses:
        query = query.filter(and_(*clauses))
    return query, False


@contextlib.contextmanager
def _scheduler_mutation_source_scope(triggered_by: str):
    """Stamp ``mutation_source="scheduler"`` for the duration of a scheduled run.

    W3-follow-up A (enhancedchannelmanager-t44t2): a scheduled/background task
    carries NO HTTP request, so the actor-source middleware (``main.py``) never
    fires and every ``journal.log_entry`` it makes would land with
    ``mutation_source=NULL``. This is the single execution chokepoint
    (``TaskEngine._execute_task`` runs EVERY task through here), so setting the
    journal contextvar once around the run attributes all journal calls inside
    it to ``"scheduler"`` without instrumenting each ``tasks/*`` job.

    Scope is limited to ``triggered_by == "scheduled"``. Manual/API runs reach
    the engine from an HTTP request whose middleware already stamped ``"ui"`` /
    ``"mcp_ai"`` — overriding that with ``"scheduler"`` would mislabel the audit
    row — so for those this is a no-op and the existing value is left intact.

    Explicit-vs-contextvar precedence is owned by
    ``journal._resolve_mutation_source``: an explicit ``mutation_source=`` passed
    at a call site (e.g. the auto-creation pipeline's ``"auto_creation"`` calls)
    always wins over this contextvar, so an auto-creation run TRIGGERED by the
    scheduler still journals its channel mutations as ``"auto_creation"`` while
    the surrounding task start/complete rows become ``"scheduler"``.

    The token is reset in ``finally`` (even on exception) so the actor never
    leaks past this job into a sibling async task.
    """
    if triggered_by != "scheduled":
        yield
        return

    token = journal.set_mutation_source(journal.MUTATION_SOURCE_SCHEDULER)
    try:
        yield
    finally:
        journal.reset_mutation_source(token)

# Task-parameter keys whose VALUE is a secret and must never be logged or written
# to the journal audit row (which itself is included in backups). A manual
# encrypted DBAS backup/restore (ADR-012 D12 / u81kh) passes an operator
# ``passphrase`` as an ad-hoc parameter; without this scrub it would land in
# cleartext in journal.db. Matched case-insensitively; additive.
_SECRET_PARAM_KEYS = frozenset({
    "passphrase", "password", "passwd", "token", "secret", "api_key",
    "access_token", "refresh_token", "client_secret", "private_key",
})
_REDACTED_PARAM = "***REDACTED***"


def _redact_task_parameters(parameters):
    """Return a shallow copy of a task-parameters dict with secret VALUES masked.

    Used for the journal audit ``after_value`` so a secret param (e.g. an
    encrypted-backup passphrase) never enters the journal row in clear text. The
    UNREDACTED dict still flows to ``update_config`` — only the journaled view is
    masked.

    NOTE: do NOT use this for ``logger`` calls. Logging sites use
    :func:`_param_keys` (keys only, no values) instead — a static taint analyser
    (CodeQL ``py/clear-text-logging-sensitive-data``) cannot see through this
    value-level sanitiser and would flag the passphrase as reaching the log sink.
    Logging only the key names removes the tainted values from the sink entirely.
    """
    if not isinstance(parameters, dict):
        return parameters
    return {
        k: (_REDACTED_PARAM if isinstance(k, str) and k.lower() in _SECRET_PARAM_KEYS
            and v not in (None, "") else v)
        for k, v in parameters.items()
    }


def _param_keys(parameters):
    """Return the sorted KEY names of a task-parameters dict — never the values.

    Logging the parameter NAMES (e.g. ``['artifact_path', 'passphrase']``) tells
    an operator which parameters a run used without ever putting a secret VALUE
    in a log line, which keeps the generic engine log sites free of the
    ``clear-text-logging-sensitive-data`` taint sink regardless of which task is
    run or which params it carries.
    """
    if parameters is None:
        # Falsy — never reaches a log site (all callers guard `if parameters:`),
        # and preserving None keeps the logged shape honest where it could.
        return None
    if not isinstance(parameters, dict):
        # NEVER return the raw object: a non-dict value could itself be (or
        # contain) a secret, and passing it through verbatim keeps the
        # clear-text-logging taint path alive (CodeQL alerts #1770/#1771).
        # A type-name placeholder tells the operator what shape arrived
        # without ever putting the value in a log line.
        return f"<non-dict:{type(parameters).__name__}>"
    return sorted(parameters.keys())

# Configuration
DEFAULT_CHECK_INTERVAL = 60  # Check for due tasks every 60 seconds
MAX_CONCURRENT_TASKS = 3  # Maximum tasks running simultaneously


def _abandon_orphaned_auto_creation_executions(session=None) -> int:
    """bd-exo4j crash-sentinel: reconcile ChannelPipelineExecution rows orphaned
    by a hard restart, and trip the circuit breaker if any were found.

    A kernel OOM-kill is a SIGKILL — no Python ``finally`` runs — so an
    in-flight auto-creation run leaves its ``ChannelPipelineExecution`` row at
    ``status='running'`` forever. ``task_engine`` already reconciles stale
    ``TaskExecution`` rows (``_cleanup_stale_executions``) but NOT
    ``ChannelPipelineExecution``; this closes that gap.

    Idempotent: only ``status='running'`` rows are touched (``WHERE`` filter),
    so ``completed`` / ``failed`` / ``capped`` rows are left alone and a second
    boot finds nothing to do. The abandoned status is the DISTINCT value
    ``'abandoned'`` (NOT ``'failed'`` — an OOM crash is operationally different
    from a rule that errored and we want the UI/alerts to say so).

    When at least one row is abandoned we set the PERSISTED circuit-breaker
    flag (``auto_creation_run_on_refresh_disabled=True``) so the post-refresh
    auto-fire chain stays disabled across the restart until the operator
    deliberately clears it. NEVER auto-resets.

    MUST run before the task scheduler arms — it is called from
    ``TaskEngine.start()`` ahead of the scheduler loop creation.

    Args:
        session: optional SQLAlchemy session (tests inject the in-memory one).
            When None, a fresh session is opened and closed here.

    Returns:
        Count of rows transitioned 'running' -> 'abandoned'.
    """
    from models import ChannelPipelineExecution

    owns_session = session is None
    if owns_session:
        session = get_session()
    try:
        orphaned = session.query(ChannelPipelineExecution).filter(
            ChannelPipelineExecution.status == "running"
        ).all()
        abandoned_count = len(orphaned)
        for execution in orphaned:
            execution.status = "abandoned"
            execution.completed_at = datetime.utcnow()
            execution.error_message = (
                "Abandoned: run was interrupted by a system restart "
                "(likely an out-of-memory kill). See GH #473."
            )
            selected_state = execution.get_selected_rule_outcome_state()
            if selected_state["integrity"] == "valid":
                terminal = {
                    "completed", "completed_with_errors", "skipped", "capped",
                    "failed", "interrupted", "not_run", "abandoned",
                }
                execution.set_selected_rule_outcomes([
                    outcome if outcome["status"] in terminal else {
                        **outcome, "status": "abandoned",
                    }
                    for outcome in selected_state["outcomes"]
                ])
        session.commit()

        if abandoned_count > 0:
            logger.warning(
                "[TASK-ENGINE] Abandoned %s orphaned auto-creation execution(s) "
                "left 'running' by a hard restart — tripping the run-on-refresh "
                "circuit breaker (bd-exo4j / GH #473)",
                abandoned_count,
            )
            _trip_run_on_refresh_circuit_breaker()

        return abandoned_count
    except Exception as e:
        logger.exception(
            "[TASK-ENGINE] Failed to reconcile orphaned auto-creation executions: %s", e
        )
        return 0
    finally:
        if owns_session:
            session.close()


def _trip_run_on_refresh_circuit_breaker() -> None:
    """Persist the run-on-refresh breaker flag (bd-exo4j).

    Persisted in settings.json so it survives the restart that motivated the
    breaker. Idempotent — re-tripping an already-tripped breaker is a no-op
    write. Best-effort: a settings failure is logged but never aborts startup.
    """
    try:
        from config import (
            get_settings,
            save_settings,
            settings_file_allows_startup_writes,
        )
        if not settings_file_allows_startup_writes():
            logger.warning(
                "[TASK-ENGINE] Run-on-refresh circuit breaker was not persisted "
                "because settings.json is valid JSON but not an object"
            )
            return
        settings = get_settings()
        if getattr(settings, "auto_creation_run_on_refresh_disabled", False):
            return
        settings.auto_creation_run_on_refresh_disabled = True
        save_settings(settings)
        logger.warning(
            "[TASK-ENGINE] Run-on-refresh circuit breaker TRIPPED — auto-creation "
            "will NOT auto-fire after M3U refresh until an operator clears it via "
            "POST /api/auto-creation/reset-circuit-breaker."
        )
    except Exception as e:  # pragma: no cover — best-effort, must not block boot
        logger.warning("[TASK-ENGINE] Failed to trip run-on-refresh circuit breaker: %s", e)


def _task_execution_metadata_extra(task_id: str, result: TaskResult) -> dict:
    """Counter-style fields for task alerts (email/Discord), aligned with each task's UI semantics."""
    extra: dict = {}
    if result.duration_seconds is not None:
        extra["duration_seconds"] = result.duration_seconds

    details = result.details if isinstance(result.details, dict) else {}

    if task_id == "auto_creation" and details:
        extra.update({
            "streams_evaluated": details.get("streams_evaluated", result.total_items),
            "streams_matched": details.get("streams_matched", 0),
            "channels_created": details.get("channels_created", 0),
            "channels_updated": details.get("channels_updated", 0),
            "groups_created": details.get("groups_created", 0),
            "conflict_streams": details.get("conflicts", 0),
        })
        if details.get("streams_merged", 0):
            extra["streams_merged"] = details["streams_merged"]
        if details.get("execution_id") is not None:
            extra["execution_id"] = details["execution_id"]
        if details.get("mode"):
            extra["pipeline_mode"] = details["mode"]
        return extra

    if task_id == "stream_probe":
        failed = result.failed_count
        extra.update({
            "streams_scheduled": result.total_items,
            "streams_ok": result.success_count,
            "streams_failed": failed,
            "streams_skipped": result.skipped_count,
            "black_screen_detections": details.get("black_screen_count", 0),
            "low_fps_detections": details.get("low_fps_count", 0),
            # Legacy key — alert_methods probe_failures min_failures threshold reads failed_count
            "failed_count": failed,
        })
        return extra

    extra.update({
        "total_items": result.total_items,
        "success_count": result.success_count,
        "failed_count": result.failed_count,
        "skipped_count": result.skipped_count,
    })
    return extra


def _success_task_completion_message(task_id: str, result: TaskResult) -> str:
    """Human-readable full-success line for scheduled tasks (matches execution modal where applicable)."""
    duration = result.duration_seconds
    dur = f" in {duration:.1f}s" if duration is not None else ""
    details = result.details if isinstance(result.details, dict) else {}

    if task_id == "auto_creation":
        if details:
            return f"{result.message}{dur}"
        if result.message:
            return f"{result.message}{dur}"

    if task_id == "stream_probe":
        total = result.total_items
        ok = result.success_count
        failed = result.failed_count
        skipped = result.skipped_count
        black = details.get("black_screen_count", 0)
        low = details.get("low_fps_count", 0)
        msg = f"Probed {total} stream(s){dur}: {ok} ok, {failed} failed, {skipped} skipped"
        if black or low:
            msg += f" ({black} black screen, {low} low FPS)"
        return msg

    if task_id == "dbas_backup":
        # …-fexq1: this task's counts are CATEGORIES, not opaque "items" (see
        # tasks.dbas_backup._counts_from_artifact). The generic line below left
        # the unit invisible, and before the counts meant anything it rendered
        # the boolean as "1 items processed". The task's WARNING message
        # already names categories; this is its clean-run counterpart. Only a
        # run with zero degraded categories reaches here, so "all" is exact.
        total = result.total_items
        return "Backup artifact built. All %d categor%s archived%s" % (
            total, "y" if total == 1 else "ies", dur,
        )

    skip_note = f", {result.skipped_count} skipped" if result.skipped_count else ""
    return f"Successfully completed. {result.success_count} items processed{skip_note}{dur}"


def _warning_task_completion_message(task_id: str, result: TaskResult) -> str:
    """Human-readable partial-success ('Completed with Warnings') line.

    Mirrors :func:`_success_task_completion_message`'s per-task_id branching so
    a task that needs a richer partial-success message (rather than the
    generic "N failures out of M items" phrasing) gets one WITHOUT a new
    notification path — this still feeds the single existing "Completed with
    Warnings" branch in the scheduler loop below (enhancedchannelmanager-zt3kf:
    reuse the existing branch, invent no new copy).
    """
    details = result.details if isinstance(result.details, dict) else {}

    if task_id == "stream_probe":
        return (
            f"Completed with {result.failed_count} failures out of {result.total_items} streams. "
            f"({result.success_count} ok, {result.skipped_count} skipped)"
        )

    if task_id == "dbas_backup":
        # zt3kf — a DBAS backup whose gather stubbed one or more Dispatcharr
        # categories (upstream fetch failure) is a WARNING, not a clean
        # success: the artifact was built but is missing real data for the
        # named category/categories. Name them here so the operator does not
        # have to open the artifact to discover what is missing.
        degraded = details.get("degraded_categories") or []
        if degraded:
            plural = len(degraded) != 1
            return (
                "Backup artifact built, but %d Dispatcharr categor%s could not "
                "be fetched from Dispatcharr and %s degraded in this backup: "
                "%s. Check Dispatcharr connectivity and re-run to fill the "
                "gap." % (
                    len(degraded),
                    "ies" if plural else "y",
                    "are" if plural else "is",
                    ", ".join(degraded),
                )
            )

    return (
        f"Completed with {result.failed_count} failures out of {result.total_items} items. "
        f"({result.success_count} succeeded, {result.skipped_count} skipped)"
    )


class TaskEngine:
    """
    Background execution engine for scheduled tasks.

    Manages task scheduling, execution, and history recording.
    """

    def __init__(
        self,
        check_interval: int = DEFAULT_CHECK_INTERVAL,
        max_concurrent: int = MAX_CONCURRENT_TASKS,
    ):
        self.check_interval = check_interval
        self.max_concurrent = max_concurrent
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._active_tasks: set[str] = set()  # Currently running task IDs
        self._lock = asyncio.Lock()
        # Notification callbacks for task progress
        self._create_notification_callback = None
        self._update_notification_callback = None
        self._delete_notification_callback = None

    def set_notification_callbacks(
        self,
        create_callback=None,
        update_callback=None,
        delete_callback=None,
    ):
        """Set notification callbacks for task progress updates."""
        self._create_notification_callback = create_callback
        self._update_notification_callback = update_callback
        self._delete_notification_callback = delete_callback
        logger.info("[TASK-ENGINE] Task engine notification callbacks configured")

    async def start(self) -> None:
        """Start the task execution engine."""
        if self._running:
            logger.warning("[TASK-ENGINE] Task engine already running")
            return

        logger.info("[TASK-ENGINE] Starting task execution engine")
        self._running = True

        # Cleanup any stale "running" executions from previous runs. Runs
        # BEFORE the scheduler loop arms (below) so a doomed auto-creation run
        # cannot be re-fired before reconciliation.
        self._cleanup_stale_executions()
        # bd-exo4j (GH #473): reconcile orphaned ChannelPipelineExecution rows the
        # OOM SIGKILL left 'running', and trip the run-on-refresh breaker if
        # any were found. MUST be before the scheduler loop starts.
        _abandon_orphaned_auto_creation_executions()

        # Initialize registry from database
        registry = get_registry()
        registry.sync_from_database()

        # Start the scheduler loop
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info("[TASK-ENGINE] Task engine started (check_interval=%ss, max_concurrent=%s)", self.check_interval, self.max_concurrent)

    def _cleanup_stale_executions(self) -> None:
        """Mark any 'running' task executions as 'terminated'.

        Called on startup to clean up executions that were interrupted
        by a container restart or crash.
        """
        try:
            session = get_session()
            stale_count = session.query(TaskExecution).filter(
                TaskExecution.status == "running"
            ).update({
                "status": "terminated",
                "completed_at": datetime.utcnow(),
                "success": False,
                "message": "Terminated: execution was interrupted by system restart",
            })
            session.commit()
            session.close()

            if stale_count > 0:
                logger.info("[TASK-ENGINE] Cleaned up %s stale 'running' task execution(s) - marked as terminated", stale_count)
        except Exception as e:
            logger.exception("[TASK-ENGINE] Failed to cleanup stale executions: %s", e)

    async def stop(self) -> None:
        """Stop the task execution engine."""
        if not self._running:
            return

        logger.info("[TASK-ENGINE] Stopping task execution engine")
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.debug("[TASK-ENGINE] Task loop cancelled during shutdown")
            self._task = None

        # Wait for active tasks to complete (with timeout)
        if self._active_tasks:
            logger.info("[TASK-ENGINE] Waiting for %s active tasks to complete...", len(self._active_tasks))
            timeout = 30  # 30 second timeout
            start = datetime.utcnow()
            while self._active_tasks and (datetime.utcnow() - start).total_seconds() < timeout:
                await asyncio.sleep(1)

        logger.info("[TASK-ENGINE] Task engine stopped")

    @property
    def is_running(self) -> bool:
        """Check if engine is running."""
        return self._running

    @property
    def active_task_count(self) -> int:
        """Get number of currently running tasks."""
        return len(self._active_tasks)

    @property
    def active_task_ids(self) -> list[str]:
        """Get list of currently running task IDs."""
        return list(self._active_tasks)

    async def _notify_task_result(
        self,
        task_name: str,
        task_id: str,
        notification_type: str,
        title: str,
        message: str,
        result: Optional[TaskResult] = None,
        alert_category: Optional[str] = None,
    ) -> None:
        """
        Send a notification about task execution result.

        This creates a notification in the database and dispatches
        to configured alert channels (Discord, Telegram, etc.)

        Args:
            task_name: Human-readable task name
            task_id: Task identifier
            notification_type: One of "success", "warning", "error"
            title: Notification title
            message: Notification message
            result: Optional TaskResult with execution details
            alert_category: Category for granular filtering (e.g., "probe_failures")
        """
        try:
            # Import here to avoid circular imports
            from services.notification_service import create_notification_internal
            from models import ScheduledTask

            # Check task-level notification and alert settings
            should_send_alert = True
            should_show_notification = True
            channel_settings = None  # Per-task channel settings (email, discord, telegram)
            try:
                session = get_session()
                try:
                    scheduled_task = session.query(ScheduledTask).filter(
                        ScheduledTask.task_id == task_id
                    ).first()
                    if scheduled_task:
                        logger.info(
                            "[%s] Alert settings: type=%s send_alerts=%s on_success=%s on_warning=%s on_error=%s on_info=%s "
                            "to_email=%s to_discord=%s to_telegram=%s show_notifications=%s",
                            task_id, notification_type,
                            scheduled_task.send_alerts,
                            scheduled_task.alert_on_success,
                            scheduled_task.alert_on_warning,
                            scheduled_task.alert_on_error,
                            scheduled_task.alert_on_info,
                            scheduled_task.send_to_email,
                            scheduled_task.send_to_discord,
                            scheduled_task.send_to_telegram,
                            scheduled_task.show_notifications,
                        )
                        # Check if notifications should appear in NotificationCenter
                        if not scheduled_task.show_notifications:
                            should_show_notification = False
                            logger.debug("[%s] NotificationCenter notifications disabled for this task", task_id)
                        # Check master toggle for external alerts
                        if not scheduled_task.send_alerts:
                            should_send_alert = False
                            logger.debug("[%s] External alerts disabled for this task (send_alerts=False)", task_id)
                        # Check type-specific toggle
                        elif notification_type == "success" and not scheduled_task.alert_on_success:
                            should_send_alert = False
                            logger.debug("[%s] Success alerts disabled for this task", task_id)
                        elif notification_type == "warning" and not scheduled_task.alert_on_warning:
                            should_send_alert = False
                            logger.debug("[%s] Warning alerts disabled for this task", task_id)
                        elif notification_type == "error" and not scheduled_task.alert_on_error:
                            should_send_alert = False
                            logger.debug("[%s] Error alerts disabled for this task", task_id)
                        elif notification_type == "info" and not scheduled_task.alert_on_info:
                            should_send_alert = False
                            logger.debug("[%s] Info alerts disabled for this task", task_id)

                        # Get per-task channel settings
                        if should_send_alert:
                            channel_settings = {
                                "send_to_email": scheduled_task.send_to_email,
                                "send_to_discord": scheduled_task.send_to_discord,
                                "send_to_telegram": scheduled_task.send_to_telegram,
                            }
                finally:
                    session.close()
            except Exception as e:
                logger.warning("[%s] Failed to check task alert settings, defaulting to send: %s", task_id, e)

            metadata = {
                "task_id": task_id,
                "task_name": task_name,
            }
            if result:
                metadata.update(_task_execution_metadata_extra(task_id, result))
                # Include failed item names if available in result details
                if result.details and result.failed_count > 0:
                    failed_streams = result.details.get("failed_streams", [])
                    if failed_streams:
                        # Extract just the names, limit to first 10 to avoid huge messages
                        failed_names = [s.get("name", f"ID:{s.get('id', '?')}") for s in failed_streams[:10]]
                        if len(failed_streams) > 10:
                            failed_names.append(f"... and {len(failed_streams) - 10} more")
                        metadata["failed_items"] = ", ".join(failed_names)

            # Dispatch external alerts even if in-app notifications are disabled
            if not should_show_notification and should_send_alert:
                logger.info("[%s] In-app notifications disabled, dispatching external alerts only", task_id)
                from services.notification_service import _dispatch_to_alert_channels
                asyncio.create_task(
                    _dispatch_to_alert_channels(
                        title=title,
                        message=message,
                        notification_type=notification_type,
                        source="task",
                        metadata=metadata,
                        alert_category=alert_category,
                        channel_settings=channel_settings,
                    )
                )
                return
            elif not should_show_notification:
                logger.debug("[%s] Skipping notification and alerts (both disabled)", task_id)
                return

            await create_notification_internal(
                notification_type=notification_type,
                title=title,
                message=message,
                source="task",
                source_id=task_id,
                metadata=metadata,
                send_alerts=should_send_alert,
                alert_category=alert_category,
                channel_settings=channel_settings,
            )
        except Exception as e:
            # Don't let notification failures affect task execution
            logger.exception("[TASK-ENGINE] Failed to send task notification: %s", e)

    async def _scheduler_loop(self) -> None:
        """Main scheduler loop - checks for due tasks and executes them."""
        logger.info("[TASK-ENGINE] Scheduler loop started")

        # Initial wait for system to stabilize
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            return

        while self._running:
            try:
                await self._check_and_run_due_tasks()
            except Exception as e:
                logger.exception("[TASK-ENGINE] Error in scheduler loop: %s", e)

            # bd-qxi02 P1 fix: refresh the
            # ecm_task_schedule_next_run_null_count gauge on every
            # scheduler tick so the 5m alert window in
            # prometheus_rules.yaml sees per-scrape freshness. Without
            # this, the gauge was only updated at init_db and
            # TaskRegistry.sync_from_database (both boot-only), so a
            # mid-life regression that caused next_run_at to drift to
            # NULL would not be detected until the next container
            # restart. The COUNT(*) query is cheap (~one indexed
            # lookup against task_schedules, <10 rows in practice).
            # Defensive wrapping mirrors the boot-time call site —
            # observability must never break the scheduler loop.
            try:
                from observability import update_task_schedule_null_count
                update_task_schedule_null_count()
            except Exception as obs_err:  # pragma: no cover — best-effort
                logger.debug("[TASK-ENGINE] task_schedule null-count refresh failed: %s", obs_err)

            # Wait for next check
            try:
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break

        logger.info("[TASK-ENGINE] Scheduler loop stopped")

    async def _check_and_run_due_tasks(self) -> None:
        """Check for tasks that are due and start them based on their schedules."""
        registry = get_registry()
        now = datetime.utcnow()

        # Check schedules from the database
        try:
            from database import get_session
            from models import TaskSchedule, ScheduledTask
            from schedule_calculator import calculate_next_run

            session = get_session()
            try:
                # Get all enabled schedules that are due
                due_schedules = session.query(TaskSchedule).filter(
                    TaskSchedule.enabled == True,
                    TaskSchedule.next_run_at != None,
                    TaskSchedule.next_run_at <= now
                ).all()

                # Group schedules by task_id
                tasks_to_run = {}
                for schedule in due_schedules:
                    if schedule.task_id not in tasks_to_run:
                        tasks_to_run[schedule.task_id] = []
                    tasks_to_run[schedule.task_id].append(schedule)

                for task_id, triggered_schedules in tasks_to_run.items():
                    # Skip if at max concurrency
                    if len(self._active_tasks) >= self.max_concurrent:
                        logger.debug("[TASK-ENGINE] Max concurrent tasks reached (%s), skipping check", self.max_concurrent)
                        break

                    # Skip if already running
                    if task_id in self._active_tasks:
                        continue

                    instance = registry.get_task_instance(task_id)
                    if not instance:
                        # A child schedule is due but NO task instance is
                        # registered in the engine — this schedule can NEVER
                        # run. It is a real misconfiguration (an orphaned
                        # ``task_schedules`` row, or a task removed from the
                        # registry while its schedule survived), not a transient
                        # skip, so surface it at WARNING. Throttled per task_id
                        # so a permanently-orphaned schedule cannot spam every
                        # ~60s tick (vkktd.1).
                        if should_log("task_not_registered:%s" % task_id):
                            logger.warning(
                                "[TASK-ENGINE] Schedule for %s is due but no task "
                                "instance is registered — it can NEVER run "
                                "(orphaned task_schedules row / unregistered task)",
                                task_id,
                            )
                        continue

                    # Check if the parent task is enabled
                    parent_task = session.query(ScheduledTask).filter(
                        ScheduledTask.task_id == task_id
                    ).first()
                    if parent_task and not parent_task.enabled:
                        # The child schedule is enabled and due, but the PARENT
                        # ``scheduled_tasks.enabled`` gate is off — firing needs
                        # BOTH (see epic vkktd). This was otherwise a silent
                        # ``continue`` every tick: the exact trap that made a
                        # gated-off auto_creation task invisible in the logs.
                        # Throttled DEBUG (once per task per window) names the
                        # cause without per-tick spam (vkktd.1).
                        if should_log("parent_disabled:%s" % task_id):
                            logger.debug(
                                "[TASK-ENGINE] Child schedule for %s is due but the "
                                "parent task is disabled — not firing (enable the "
                                "task to run)", task_id,
                            )
                        continue

                    # Found a due schedule - run the task
                    logger.info("[TASK-ENGINE] Task %s is due (via schedule), scheduling execution", task_id)
                    asyncio.create_task(self._execute_task_with_schedules(
                        task_id, triggered_schedules, triggered_by="scheduled"
                    ))
            finally:
                session.close()
        except Exception as e:
            logger.exception("[TASK-ENGINE] Error checking schedules from database: %s", e)

            # Fallback to legacy behavior (check instance._next_run)
            for task_id in registry.list_task_ids():
                if len(self._active_tasks) >= self.max_concurrent:
                    break

                if task_id in self._active_tasks:
                    continue

                instance = registry.get_task_instance(task_id)
                if not instance:
                    continue

                if not instance._enabled:
                    continue

                if instance._next_run and instance._next_run <= now:
                    logger.info("[TASK-ENGINE] Task %s is due (legacy), scheduling execution", task_id)
                    asyncio.create_task(self._execute_task(task_id, triggered_by="scheduled"))

    async def _execute_task_with_schedules(
        self, task_id: str, triggered_schedules: list, triggered_by: str = "scheduled"
    ) -> Optional[TaskResult]:
        """
        Execute a task and update the triggered schedules after completion.

        Args:
            task_id: ID of task to execute
            triggered_schedules: List of TaskSchedule objects that triggered this execution
            triggered_by: Who triggered the task ("scheduled", "manual", "api")

        Returns:
            TaskResult or None if task not found
        """
        results = []
        for schedule in triggered_schedules:
            schedule_parameters = schedule.get_parameters()
            if schedule_parameters:
                logger.info(
                    "[%s] Using parameters from schedule %s: %s",
                    task_id,
                    schedule.id,
                    _param_keys(schedule_parameters),
                )
            result = await self._execute_task(
                task_id,
                triggered_by,
                parameters=schedule_parameters,
                schedule_id=schedule.id,
            )
            if result:
                results.append((schedule.id, result))

        # Update next_run_at for triggered schedules
        if results:
            try:
                from database import get_session
                from models import TaskSchedule, ScheduledTask
                from schedule_calculator import calculate_next_run

                session = get_session()
                try:
                    for schedule_id, schedule_result in results:
                        # Update last_run_at and recalculate next run time
                        db_schedule = session.query(TaskSchedule).get(schedule_id)
                        if db_schedule:
                            db_schedule.last_run_at = schedule_result.completed_at
                            if db_schedule.enabled:
                                db_schedule.next_run_at = calculate_next_run(
                                    schedule_type=db_schedule.schedule_type,
                                    interval_seconds=db_schedule.interval_seconds,
                                    schedule_time=db_schedule.schedule_time,
                                    timezone=db_schedule.timezone,
                                    days_of_week=db_schedule.get_days_of_week_list(),
                                    day_of_month=db_schedule.day_of_month,
                                    last_run=schedule_result.completed_at,
                                )
                            logger.debug("[%s] Updated schedule %s last_run_at=%s, next_run_at=%s", task_id, db_schedule.id, schedule_result.completed_at, db_schedule.next_run_at)

                    # Update parent task's next_run_at (earliest of all enabled schedules)
                    all_schedules = session.query(TaskSchedule).filter(
                        TaskSchedule.task_id == task_id,
                        TaskSchedule.enabled == True,
                        TaskSchedule.next_run_at != None
                    ).order_by(TaskSchedule.next_run_at).all()

                    parent_task = session.query(ScheduledTask).filter(
                        ScheduledTask.task_id == task_id
                    ).first()
                    if parent_task:
                        if all_schedules:
                            parent_task.next_run_at = all_schedules[0].next_run_at
                        else:
                            parent_task.next_run_at = None

                    session.commit()
                finally:
                    session.close()
            except Exception as e:
                logger.exception("[%s] Failed to update schedule next_run_at: %s", task_id, e)

        return results[-1][1] if results else None

    async def _execute_task(
        self,
        task_id: str,
        triggered_by: str = "manual",
        parameters: Optional[dict] = None,
        schedule_id: Optional[int] = None,
    ) -> Optional[TaskResult]:
        """
        Execute a task and record the result.

        Args:
            task_id: ID of task to execute
            triggered_by: Who triggered the task ("scheduled", "manual", "api")
            parameters: Task-specific parameters from the schedule (e.g., channel_groups, batch_size)
            schedule_id: ID of the schedule that triggered this execution (for tracking)

        Returns:
            TaskResult or None if task not found
        """
        registry = get_registry()
        instance = registry.get_task_instance(task_id)

        if not instance:
            logger.error("[%s] Task not found", task_id)
            return None

        # Check if already running
        async with self._lock:
            if task_id in self._active_tasks:
                logger.warning("[%s] Task is already running", task_id)
                return TaskResult(
                    success=False,
                    message="Task is already running",
                    error="ALREADY_RUNNING",
                )
            self._active_tasks.add(task_id)

        # Create execution record
        execution = TaskExecution(
            task_id=task_id,
            started_at=datetime.utcnow(),
            status="running",
            triggered_by=triggered_by,
        )

        try:
            session = get_session()
            session.add(execution)
            session.commit()
            execution_id = execution.id
            session.close()
        except Exception as e:
            logger.exception("[%s] Failed to create execution record: %s", task_id, e)
            execution_id = None

        # Attribute every journal row made by this run to "scheduler" when the
        # run was triggered by the scheduler (no HTTP request, so the actor-source
        # middleware never fired). No-op for manual/API runs — see
        # _scheduler_mutation_source_scope. Explicit call-site sources (e.g. the
        # auto-creation pipeline's "auto_creation") still win via
        # journal._resolve_mutation_source. Reset in the active-task ``finally``
        # below so the actor never leaks into a sibling async task.
        _scheduler_source_token = (
            journal.set_mutation_source(journal.MUTATION_SOURCE_SCHEDULER)
            if triggered_by == "scheduled"
            else None
        )
        invocation_config = None
        validation_result = None

        try:
            # Registry instances are long-lived singletons. Treat run parameters
            # as an overlay on their hydrated persisted/default configuration,
            # then restore that baseline in ``finally`` before another schedule
            # or manual run can observe it.
            invocation_config = copy.deepcopy(instance.get_config())

            prepare_invocation = getattr(instance, "prepare_invocation", None)
            if prepare_invocation:
                prepare_invocation(triggered_by)

            prepare_parameters = getattr(instance, "prepare_invocation_parameters", None)
            if prepare_parameters:
                parameters = prepare_parameters(triggered_by, schedule_id, parameters)

            if triggered_by == "scheduled":
                validator = getattr(instance, "validate_schedule_parameters", None)
                if validator:
                    try:
                        validator(parameters)
                    except ValueError as error:
                        handler = getattr(
                            instance, "handle_schedule_validation_error", None
                        )
                        if handler is None:
                            raise
                        validation_result = await handler(parameters, error)

            # Apply schedule parameters to the task instance
            if parameters and hasattr(instance, 'update_config'):
                instance.update_config(parameters)
                logger.info("[%s] Applied schedule parameters: %s", task_id,
                            _param_keys(parameters))

            logger.info("[%s] Starting task execution (triggered_by=%s)", task_id, triggered_by)

            # Get notification settings from database
            show_notifications = True  # Default to true
            send_alerts = True  # Default to true
            # alert_on_info defaults to False to match the ScheduledTask column
            # default — info-level external alerts are opt-in.
            alert_on_info = False
            try:
                session = get_session()
                try:
                    from models import ScheduledTask
                    scheduled_task = session.query(ScheduledTask).filter(
                        ScheduledTask.task_id == task_id
                    ).first()
                    if scheduled_task:
                        show_notifications = scheduled_task.show_notifications
                        send_alerts = scheduled_task.send_alerts
                        alert_on_info = scheduled_task.alert_on_info
                finally:
                    session.close()
            except Exception as e:
                logger.warning("[%s] Failed to get notification settings: %s", task_id, e)

            # Set notification callbacks on the task instance.
            #
            # The start/progress notification (_create_progress_notification) is
            # always an "info"-level message ("<task> starting..."). Its external
            # alert dispatch must therefore respect alert_on_info — NOT just the
            # master send_alerts toggle. Without this gate, a task with only
            # warning/error alerts enabled still Telegram-spammed a "starting"
            # message on every run (GH #462 / bd-on4sr). The completion alert is
            # dispatched separately with full per-level gating (see
            # check_and_run_tasks), so this only narrows the start notification.
            progress_send_alerts = send_alerts and alert_on_info
            if self._create_notification_callback:
                instance.set_notification_callbacks(
                    create_callback=self._create_notification_callback,
                    update_callback=self._update_notification_callback,
                    delete_callback=self._delete_notification_callback,
                    show_notifications=show_notifications,
                    send_alerts=progress_send_alerts,
                )

            # Log task start to journal
            log_entry(
                category="task",
                action_type="start",
                entity_name=instance.task_name,
                description=f"Started {instance.task_name} ({triggered_by})",
                entity_id=execution_id,
                after_value={"task_id": task_id, "triggered_by": triggered_by,
                             "parameters": _redact_task_parameters(parameters)},
                user_initiated=(triggered_by == "manual"),
            )

            instance.set_run_trigger(triggered_by)
            result = validation_result or await instance.run()

            # Update execution record
            if execution_id:
                try:
                    session = get_session()
                    execution = session.query(TaskExecution).get(execution_id)
                    if execution:
                        execution.completed_at = result.completed_at
                        execution.duration_seconds = result.duration_seconds
                        # Severity comes from the ONE derivation (bead …-fexq1),
                        # so the stored row cannot contradict the alert emitted
                        # for the same run a few lines below. Previously both
                        # fields were read off result.success alone, which
                        # stored a degraded-but-completed run as "failed" while
                        # its own notification said "Completed with Warnings".
                        execution.status = execution_status(result)
                        execution.success = execution_succeeded(result)
                        execution.message = result.message
                        execution.error = result.error
                        execution.total_items = result.total_items
                        execution.success_count = result.success_count
                        execution.failed_count = result.failed_count
                        execution.skipped_count = result.skipped_count
                        if result.details:
                            import json
                            execution.details = json.dumps(result.details)
                        session.commit()
                    session.close()
                except Exception as e:
                    logger.exception("[%s] Failed to update execution record: %s", task_id, e)

            # Update registry
            registry.sync_to_database(task_id)

            # Determine alert category for granular filtering
            # For stream_probe tasks, use "probe_failures" to allow min_failures threshold
            alert_category = "probe_failures" if task_id == "stream_probe" else None

            # ONE branch per TaskOutcome (bead …-fexq1). Severity is derived
            # once, in task_scheduler.task_outcome, and the Journal row, the
            # completion notification and the TaskExecution row written above all
            # map from it. Before this, each surface re-derived severity from
            # ``result.success`` plus ``failed_count > 0`` and they disagreed:
            # the drill's degraded restore alerted "Completed with Warnings"
            # while its history row said ``failed``.
            outcome = task_outcome(result)
            if outcome is TaskOutcome.CANCELLED:
                log_entry(
                    category="task",
                    action_type="cancel",
                    entity_name=instance.task_name,
                    description=f"Cancelled {instance.task_name}: {result.success_count} completed before cancellation",
                    entity_id=execution_id,
                    after_value={
                        "task_id": task_id,
                        "success": False,
                        "cancelled": True,
                        "duration_seconds": result.duration_seconds,
                        "total_items": result.total_items,
                        "success_count": result.success_count,
                        "failed_count": result.failed_count,
                        "skipped_count": result.skipped_count,
                    },
                    user_initiated=(triggered_by == "manual"),
                )

                # Send warning notification for cancellation (warning type triggers alerts)
                if task_id == "stream_probe":
                    cancel_msg = (
                        f"Task was cancelled. {result.success_count} streams finished before cancellation"
                        + (f", {result.failed_count} failed" if result.failed_count > 0 else "")
                        + (f", {result.skipped_count} skipped" if result.skipped_count > 0 else "")
                        + f" (of {result.total_items} scheduled)"
                    )
                else:
                    cancel_msg = (
                        f"Task was cancelled. {result.success_count} items completed before cancellation"
                        + (f", {result.failed_count} failed" if result.failed_count > 0 else "")
                        + (f", {result.skipped_count} skipped" if result.skipped_count > 0 else "")
                        + f" (out of {result.total_items} total)"
                    )
                await self._notify_task_result(
                    task_name=instance.task_name,
                    task_id=task_id,
                    notification_type=completion_notification_type(result),
                    title=f"Task Cancelled: {instance.task_name}",
                    message=cancel_msg,
                    result=result,
                    alert_category=alert_category,
                )
            elif outcome is TaskOutcome.ERROR:
                logger.error("[%s] Task failed: %s (error=%s)", task_id, result.message, result.error)
                log_entry(
                    category="task",
                    action_type="fail",
                    entity_name=instance.task_name,
                    description=f"Failed {instance.task_name}: {result.error or result.message}",
                    entity_id=execution_id,
                    after_value={
                        "task_id": task_id,
                        "success": False,
                        "severity": outcome.value,
                        "error": result.error,
                        "message": result.message,
                    },
                    user_initiated=(triggered_by == "manual"),
                )
                await self._notify_task_result(
                    task_name=instance.task_name,
                    task_id=task_id,
                    notification_type=completion_notification_type(result),
                    title=f"Task Failed: {instance.task_name}",
                    message=result.error or result.message or "Unknown error",
                    result=result,
                    alert_category=alert_category,
                )
            else:
                # SUCCESS or WARNING — the run reached the end and left real,
                # kept state. Both were previously reachable only through
                # ``result.success``, which excluded the degraded runs of bead
                # ``…-daziw`` (a DBAS restore whose only shortfall is a channel
                # with no playable stream) and pushed them into the failure
                # branch above. They belong here: nothing was rolled back.
                warned = outcome is TaskOutcome.WARNING

                # bd-qxi02 (SRE recommendation from bd-p5b8i spike):
                # stamp the per-task success gauge so the
                # ECMTaskScheduleStale* alerts in prometheus_rules.yaml
                # can distinguish "task hasn't been scheduled in days"
                # (the disease bd-p5b8i hid for 39+ days) from "task
                # is healthy."  Wrapped defensively in observability —
                # this MUST NOT break the task completion path.
                #
                # THE TRIGGER IS DELIBERATELY ``result.success``, NOT THE
                # OUTCOME (bead …-fexq1). The gauge answers a NARROWER question
                # than the history row: "when did this task last run CLEANLY".
                # Widening it to every warning-level run would let a task that
                # degrades on every run keep resetting the staleness clock and
                # mask exactly the alert this gauge exists to raise; narrowing
                # it to clean runs only would start a stale-schedule alert storm
                # for the tasks that always report some failed items. So the
                # condition is left exactly where bd-qxi02 put it, and a
                # degraded run does not stamp.
                #
                # CANCELLED is excluded structurally: the branch above is part
                # of the same if/elif chain, so a cancelled result never reaches
                # here even if a task returns success=True alongside
                # error="CANCELLED".
                if result.success:
                    try:
                        from observability import record_task_success
                        record_task_success(task_id)
                    except Exception as obs_err:  # pragma: no cover — best-effort
                        logger.debug(
                            "[%s] Failed to record task success gauge: %s",
                            task_id, obs_err,
                        )

                if getattr(result, "completed_degraded", False):
                    # Kept as narrow as it was: only a task that DECLARED itself
                    # degraded logs here. Widening it to every partial-failure
                    # run would put a WARNING line in the log for each routine
                    # probe that could not reach two streams.
                    logger.warning(
                        "[%s] Task completed in a degraded state: %s", task_id, result.message
                    )

                # fexq1: the Journal row said "Completed X: 0 ok, 1 failed"
                # beside ``success: true`` — self-contradictory at a glance for
                # an operator scanning the Journal rather than the notification.
                # The severity now LEADS the line, and the row carries it
                # explicitly instead of leaving it to be inferred from counts.
                log_entry(
                    category="task",
                    action_type="complete",
                    entity_name=instance.task_name,
                    description=(
                        f"Completed with warnings — {instance.task_name}: "
                        f"{result.success_count} ok, {result.failed_count} failed"
                        if warned else
                        f"Completed {instance.task_name}: {result.success_count} ok, {result.failed_count} failed"
                    ),
                    entity_id=execution_id,
                    after_value={
                        "task_id": task_id,
                        "success": True,
                        "severity": outcome.value,
                        "message": result.message,
                        "duration_seconds": result.duration_seconds,
                        "total_items": result.total_items,
                        "success_count": result.success_count,
                        "failed_count": result.failed_count,
                        "skipped_count": result.skipped_count,
                    },
                    user_initiated=(triggered_by == "manual"),
                )

                # y3m6o.1 review (Finding 2): a task that already emitted ONE
                # coherent completion notification (e.g. a channel-pipeline run
                # that was BOTH capped and had failed actions) sets
                # suppress_completion_notification so we do NOT emit a second,
                # competing warning here. Journal + gauge above still ran.
                if result.suppress_completion_notification:
                    logger.debug(
                        "[%s] Completion notification suppressed by task "
                        "(single coherent notification already emitted)", task_id,
                    )
                elif warned:
                    # Two shapes of warning message, both preserved exactly:
                    # a run with failed ITEMS gets the per-task count summary,
                    # and a degraded run with clean counts gets its own message,
                    # which is the only place the shortfall is named.
                    await self._notify_task_result(
                        task_name=instance.task_name,
                        task_id=task_id,
                        notification_type=completion_notification_type(result),
                        title=f"Task Completed with Warnings: {instance.task_name}",
                        message=(
                            _warning_task_completion_message(task_id, result)
                            if result.failed_count > 0
                            else result.message or result.error or "Completed with warnings"
                        ),
                        result=result,
                        alert_category=alert_category,
                    )
                else:
                    await self._notify_task_result(
                        task_name=instance.task_name,
                        task_id=task_id,
                        notification_type=completion_notification_type(result),
                        title=f"Task Completed: {instance.task_name}",
                        message=_success_task_completion_message(task_id, result),
                        result=result,
                        alert_category=alert_category,
                    )

            return result

        except Exception as e:
            logger.exception("[%s] Task execution failed: %s", task_id, e)
            failure_error = getattr(e, "error_code", str(e))

            # Determine alert category for granular filtering
            alert_category = "probe_failures" if task_id == "stream_probe" else None

            # Log exception to journal
            log_entry(
                category="task",
                action_type="error",
                entity_name=instance.task_name,
                description=f"Error in {instance.task_name}: {str(e)}",
                entity_id=execution_id,
                after_value={
                    "task_id": task_id,
                    "error": str(e),
                    "triggered_by": triggered_by,
                },
                user_initiated=(triggered_by == "manual"),
            )

            # Send error notification for exception
            await self._notify_task_result(
                task_name=instance.task_name,
                task_id=task_id,
                notification_type="error",
                title=f"Task Error: {instance.task_name}",
                message=f"Task failed with exception: {str(e)}",
                result=None,
                alert_category=alert_category,
            )

            # Update execution record with error
            if execution_id:
                try:
                    session = get_session()
                    execution = session.query(TaskExecution).get(execution_id)
                    if execution:
                        execution.completed_at = datetime.utcnow()
                        execution.status = "failed"
                        execution.success = False
                        execution.error = failure_error
                        session.commit()
                    session.close()
                except Exception as db_err:
                    logger.exception("[%s] Failed to update execution record: %s", task_id, db_err)

            return TaskResult(
                success=False,
                message=f"Task execution failed: {str(e)}",
                error=failure_error,
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
            )

        finally:
            try:
                if invocation_config is not None:
                    instance.restore_invocation_config(invocation_config)
            except Exception as e:
                logger.exception("[%s] Failed to restore invocation config: %s", task_id, e)
            finally:
                instance.set_run_trigger("manual")
                if _scheduler_source_token is not None:
                    journal.reset_mutation_source(_scheduler_source_token)
                async with self._lock:
                    self._active_tasks.discard(task_id)

    async def run_task(self, task_id: str, schedule_id: Optional[int] = None, parameters: Optional[dict] = None) -> Optional[TaskResult]:
        """
        Manually run a task (API entry point).

        Args:
            task_id: ID of task to run
            schedule_id: Optional schedule ID to use parameters from
            parameters: Optional ad-hoc parameters (takes priority over schedule)

        Returns:
            TaskResult or None if task not found
        """
        if parameters:
            logger.info("[%s] Manual run with ad-hoc parameters: %s", task_id,
                        _param_keys(parameters))
            return await self._execute_task(task_id, triggered_by="manual", parameters=parameters)

        if schedule_id:
            # Load parameters from the specified schedule
            try:
                from database import get_session
                from models import TaskSchedule
                session = get_session()
                try:
                    schedule = session.query(TaskSchedule).filter(
                        TaskSchedule.id == schedule_id,
                        TaskSchedule.task_id == task_id
                    ).first()
                    if not schedule:
                        logger.warning(
                            "[%s] Manual run requested missing schedule %s",
                            task_id,
                            schedule_id,
                        )
                        return None
                    parameters = schedule.get_parameters()
                    logger.info("[%s] Manual run using schedule %s parameters: %s", task_id, schedule_id,
                                _param_keys(parameters))
                finally:
                    session.close()
            except Exception as e:
                logger.exception("[%s] Failed to load schedule parameters: %s", task_id, e)

        return await self._execute_task(task_id, triggered_by="manual", parameters=parameters, schedule_id=schedule_id)

    async def cancel_task(self, task_id: str) -> dict:
        """
        Cancel a running task.

        Args:
            task_id: ID of task to cancel

        Returns:
            Status dict with result
        """
        registry = get_registry()
        instance = registry.get_task_instance(task_id)

        if not instance:
            return {"status": "not_found", "message": f"Task {task_id} not found"}

        if task_id not in self._active_tasks:
            return {"status": "not_running", "message": f"Task {task_id} is not running"}

        return instance.cancel()

    def get_status(self) -> dict:
        """Get engine status."""
        registry = get_registry()
        return {
            "running": self._running,
            "check_interval": self.check_interval,
            "max_concurrent": self.max_concurrent,
            "active_tasks": list(self._active_tasks),
            "active_task_count": len(self._active_tasks),
            "registered_task_count": len(registry.list_task_ids()),
        }

    def get_task_history(
        self,
        task_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """
        Get execution history from database.

        Args:
            task_id: Filter by task ID (None for all tasks)
            limit: Maximum records to return
            offset: Number of records to skip

        Returns:
            List of execution records as dicts
        """
        try:
            session = get_session()
            try:
                query = session.query(TaskExecution).order_by(TaskExecution.started_at.desc())

                if task_id:
                    query = query.filter(TaskExecution.task_id == task_id)

                query, empty = _scope_to_sync_target_lifetimes(session, query, task_id)
                if empty:
                    return []

                executions = query.offset(offset).limit(limit).all()
                return [e.to_dict() for e in executions]
            finally:
                session.close()
        except Exception as e:
            logger.exception("[TASK-ENGINE] Failed to get task history: %s", e)
            return []

    def purge_old_history(self, days: int = 30) -> int:
        """
        Purge execution history older than specified days.

        Args:
            days: Delete records older than this many days

        Returns:
            Number of records deleted
        """
        from datetime import timedelta

        try:
            session = get_session()
            try:
                cutoff = datetime.utcnow() - timedelta(days=days)
                result = session.query(TaskExecution).filter(
                    TaskExecution.started_at < cutoff
                ).delete()
                session.commit()
                logger.info("[TASK-ENGINE] Purged %s task execution records older than %s days", result, days)
                return result
            finally:
                session.close()
        except Exception as e:
            logger.exception("[TASK-ENGINE] Failed to purge history: %s", e)
            return 0


# Global engine instance
_engine: Optional[TaskEngine] = None


def get_engine() -> TaskEngine:
    """Get the global task engine instance."""
    global _engine
    if _engine is None:
        _engine = TaskEngine()
    return _engine


async def start_engine() -> None:
    """Start the global task engine."""
    await get_engine().start()


async def stop_engine() -> None:
    """Stop the global task engine."""
    await get_engine().stop()
