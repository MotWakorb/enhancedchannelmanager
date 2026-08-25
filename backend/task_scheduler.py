"""
Task Scheduler Framework.

Provides an abstract base class for scheduled tasks with support for:
- Interval-based scheduling (every N hours/minutes)
- Cron-based scheduling (for advanced use cases)
- Task lifecycle management (start, stop, pause, resume)
- Progress tracking and status reporting
- History persistence
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Status of a scheduled task."""
    IDLE = "idle"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class ScheduleType(str, Enum):
    """Type of schedule for a task."""
    INTERVAL = "interval"  # Run every N seconds/minutes/hours
    CRON = "cron"  # Cron expression
    MANUAL = "manual"  # Only run on demand


@dataclass
class TaskProgress:
    """Progress information for a running task."""
    total: int = 0
    current: int = 0
    status: str = "idle"
    current_item: str = ""
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    started_at: Optional[datetime] = None

    @property
    def percentage(self) -> float:
        """Get completion percentage (0-100)."""
        if self.total == 0:
            return 0.0
        return (self.current / self.total) * 100.0

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "total": self.total,
            "current": self.current,
            "percentage": round(self.percentage, 1),
            "status": self.status,
            "current_item": self.current_item,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "started_at": self.started_at.isoformat() + "Z" if self.started_at else None,
        }


@dataclass
class TaskResult:
    """Result of a task execution."""
    success: bool
    message: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_items: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    error: Optional[str] = None
    details: dict = field(default_factory=dict)
    # y3m6o.1 review (Finding 2): when True, the task engine SKIPS its generic
    # completion notification (the "Task Completed"/"...with Warnings" toast +
    # alert) because the task body already emitted ONE coherent notification for
    # this run. Journal + success-gauge stamping still happen. Used by the
    # channel-pipeline post-refresh path when a run is BOTH capped AND has
    # failed actions, to avoid emitting two separate warnings.
    suppress_completion_notification: bool = False
    # daziw (PO decision 2): the run did NOT succeed, but it ran to completion
    # and left real, kept state — it is DEGRADED, not failed. Set by the task,
    # read ONLY by the task engine's unsuccessful-result branch, where it selects
    # the "Task Completed with Warnings" / notification_type="warning" alert
    # instead of the red "Task Failed" / notification_type="error" one. Default
    # False, so every task that does not set it — and every other failure mode of
    # the tasks that do — keeps the error branch unchanged.
    #
    # Distinct from failed_count > 0, which describes a SUCCESSFUL run with some
    # failed items. This describes an UNSUCCESSFUL run with no failed items:
    # a DBAS restore that applied everything cleanly and still left a channel
    # with no playable stream.
    completed_degraded: bool = False

    @property
    def duration_seconds(self) -> Optional[float]:
        """Get execution duration in seconds."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "success": self.success,
            "message": self.message,
            "started_at": self.started_at.isoformat() + "Z" if self.started_at else None,
            "completed_at": self.completed_at.isoformat() + "Z" if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "total_items": self.total_items,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "error": self.error,
            "details": self.details,
        }


class TaskOutcome(str, Enum):
    """How a finished run ENDED — the run's severity, named once.

    Bead ``enhancedchannelmanager-fexq1``. Before this, four surfaces each
    re-derived severity from two fields that do not carry it
    (:attr:`TaskResult.success` and :attr:`TaskResult.failed_count`), and they
    disagreed: drill run ``2026-08-06-run9`` produced a DBAS restore whose alert
    was correctly ``warning`` / "Task Completed with Warnings" and whose
    task-history row read ``status: "failed"``, ``success: false`` — for the
    same run. ``failed_count > 0`` was doing the work of a severity field, which
    is why a degraded run with CLEAN counts (a restore where every row applied
    and not one channel could play) could not be expressed at all.

    Every terminal surface now maps from THIS: the notification severity
    (:func:`completion_notification_type`), the persisted history row
    (:func:`execution_status` / :func:`execution_succeeded`), and the Journal.

    * ``SUCCESS`` — every item did what it was asked to. Nothing to report.
    * ``WARNING`` — the run RAN TO COMPLETION and left real, kept state, but the
      result is not clean: some items failed, or the task declared itself
      degraded (:attr:`TaskResult.completed_degraded`). An action item, not a
      failure, and never announced as one.
    * ``ERROR`` — the run did not produce what it was asked for. This is the
      only outcome that says "failed" to an operator.
    * ``CANCELLED`` — an operator stopped it. Neither success nor failure.
    """

    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    CANCELLED = "cancelled"


def task_outcome(result: 'TaskResult') -> TaskOutcome:
    """Derive a finished run's :class:`TaskOutcome`. The ONE derivation.

    Deliberately computed from the fields tasks ALREADY set, so no task has to
    be changed and no existing run's severity moves: this is the same ladder
    ``completion_notification_type`` has always applied, given a name and a
    single home.

    * cancelled -> ``CANCELLED`` (the run stopped, it did not fail)
    * succeeded, some items failed -> ``WARNING``
    * succeeded cleanly -> ``SUCCESS``
    * unsuccessful but ``completed_degraded`` -> ``WARNING`` (bead
      ``…-daziw``: the applied state stands and the message names the shortfall)
    * unsuccessful -> ``ERROR``

    Note the third and fourth rules are the SAME severity reached two ways, and
    that is the point: a warning-level run is one that completed and left kept
    state, whether or not any individual item failed.
    """
    if result.error == "CANCELLED":
        return TaskOutcome.CANCELLED
    if result.success:
        return TaskOutcome.WARNING if result.failed_count > 0 else TaskOutcome.SUCCESS
    if getattr(result, "completed_degraded", False):
        return TaskOutcome.WARNING
    return TaskOutcome.ERROR


def completion_notification_type(result: 'TaskResult') -> str:
    """Map a finished :class:`TaskResult` to the severity the operator sees.

    Single source of truth for the NOTIFICATION severity (bead
    ``enhancedchannelmanager-asf3n``). A run touches TWO notification records —
    the completion notification the task engine creates, and the progress
    notification the scheduler created at start and re-types at the end — and
    each used to decide severity on its own. Only the engine learned the
    ``completed_degraded`` rule from bead ``enhancedchannelmanager-daziw``, so a
    degraded DBAS restore showed the operator a ``warning`` and an ``error``
    side by side for one event, with nothing to say which was authoritative.

    Now a thin projection of :func:`task_outcome` onto the notification
    vocabulary, which has no "cancelled" severity of its own: a cancelled run is
    shown as a ``warning`` and titled "Task Cancelled". Outputs are unchanged.
    """
    outcome = task_outcome(result)
    if outcome is TaskOutcome.CANCELLED:
        return "warning"
    return outcome.value


def execution_status(result: 'TaskResult') -> str:
    """Map a finished run to its TERMINAL STATUS (…-fexq1, …-bdmby).

    The status vocabulary is a closed set consumed by the Task History panel,
    the DBAS restore modals, and the MCP ``get_task_history`` tool:
    ``running`` / ``completed`` / ``completed_with_warnings`` / ``failed`` /
    ``cancelled``.

    ``completed_with_warnings`` is the member added by fexq1. Without it the row
    had to round a warning-level run to one of its neighbours, and it rounded
    the wrong way: a degraded restore was stored as ``failed`` while its own
    alert said "Task Completed with Warnings". The browser was left inferring
    the middle state from ``success && failed_count > 0``, which cannot see a
    degraded run with clean counts at all.

    Bead ``…-bdmby`` widened the callers rather than the vocabulary: the
    persisted ``TaskExecution.status``, the LIVE ``TaskProgress.status`` that
    ``GET /api/tasks/{id}`` publishes, and the finalized progress notification
    all read this one function. They used to end a run with three independently
    written strings, and drill run ``2026-08-08-run16`` caught them disagreeing —
    a restore that rolled nothing back published ``progress.status: "failed"``
    with ``failed_count: 0`` beside a history row reading
    ``completed_with_warnings``. One derivation, so they cannot drift again.
    """
    outcome = task_outcome(result)
    if outcome is TaskOutcome.CANCELLED:
        return "cancelled"
    if outcome is TaskOutcome.ERROR:
        return "failed"
    if outcome is TaskOutcome.WARNING:
        return "completed_with_warnings"
    return "completed"


def execution_succeeded(result: 'TaskResult') -> bool:
    """Map a finished run to its persisted ``TaskExecution.success`` (…-fexq1).

    Answers "did this run produce what it was asked for?" — TRUE for a
    warning-level run, whose applied state is real and kept. It is NOT the same
    question as :attr:`TaskResult.success`, which means "cleanly, with nothing
    to report", and it is NOT the question the
    ``ecm_task_schedule_last_success_timestamp`` gauge answers (that one is
    narrower still — "when did this task last run CLEANLY" — and deliberately
    keeps its own trigger; see ``task_engine``).
    """
    return task_outcome(result) in (TaskOutcome.SUCCESS, TaskOutcome.WARNING)


@dataclass
class ScheduleConfig:
    """Configuration for task scheduling."""
    schedule_type: ScheduleType = ScheduleType.MANUAL
    # For interval scheduling
    interval_seconds: int = 0
    # For cron scheduling (requires croniter)
    cron_expression: str = ""
    # For time-of-day scheduling
    schedule_time: str = ""  # HH:MM format
    # Timezone for schedule calculations
    timezone: str = ""  # IANA timezone name, empty = UTC

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "schedule_type": self.schedule_type.value,
            "interval_seconds": self.interval_seconds,
            "cron_expression": self.cron_expression,
            "schedule_time": self.schedule_time,
            "timezone": self.timezone,
        }


class TaskScheduler(ABC):
    """
    Abstract base class for scheduled tasks.

    Subclasses must implement:
    - task_id: Unique identifier for the task type
    - task_name: Human-readable name for the task
    - execute(): The actual task logic

    Optional overrides:
    - validate_config(): Validate task configuration
    - on_start(): Called when task starts running
    - on_complete(): Called when task completes successfully
    - on_error(): Called when task fails
    - on_cancel(): Called when task is cancelled
    """

    # Subclasses must define these
    task_id: str = ""
    task_name: str = ""
    task_description: str = ""
    default_enabled: bool = True  # Whether new installs start with this task enabled

    # Ad-hoc parameters this task honours in the ``parameters`` body of
    # POST /api/tasks/{task_id}/run but which are deliberately NOT
    # schedule-configurable (bead ``enhancedchannelmanager-sdpzy``). Declared on
    # the task itself so the discoverability endpoint cannot drift from what
    # ``update_config`` actually reads.
    #
    # Shape mirrors ``routers.tasks.TASK_PARAMETER_SCHEMAS`` entries:
    #   {"description": str,
    #    "parameters": [{"name", "type", "label", "description",
    #                    "required", "default"}, ...]}
    #
    # GET /api/tasks/{task_id}/parameter-schema surfaces these under a SEPARATE
    # ``run_parameters`` key. They are never merged into ``parameters``, which
    # the schedule editor renders and PERSISTS into ``task_schedules.parameters``
    # — the dbas_backup passphrase is a manual-run transient that must never be
    # written to a schedule.
    run_parameter_schema: Optional[dict] = None
    schedule_parameter_schema: Optional[dict] = None

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        """Initialize the task scheduler."""
        self.schedule_config = schedule_config or ScheduleConfig()
        self._status = TaskStatus.IDLE
        self._progress = TaskProgress()
        self._cancel_requested = False
        self._task: Optional[asyncio.Task] = None
        self._last_run: Optional[datetime] = None
        self._next_run: Optional[datetime] = None
        self._history: list[TaskResult] = []
        self._max_history = 50
        self._enabled = self.__class__.default_enabled
        # Notification callbacks (set by task_engine)
        self._notification_id: Optional[int] = None
        self._create_notification_callback = None
        self._update_notification_callback = None
        self._delete_notification_callback = None
        self._show_notifications: bool = True  # Whether to show in NotificationCenter
        self._send_alerts: bool = True  # Whether to send external alerts (Discord, Telegram, etc.)
        self._last_notification_update: float = 0
        self._notification_update_interval: float = 2.0  # Update every 2 seconds max
        self._run_trigger = "manual"

    def set_run_trigger(self, triggered_by: str) -> None:
        """Set whether the current invocation is manual or scheduler-driven."""
        self._run_trigger = triggered_by

    # -------------------------------------------------------------------------
    # Abstract methods (must be implemented by subclasses)
    # -------------------------------------------------------------------------

    @abstractmethod
    async def execute(self) -> TaskResult:
        """
        Execute the task logic.

        Subclasses must implement this method with their specific task logic.
        Should periodically check self._cancel_requested and exit early if True.

        Returns:
            TaskResult with execution outcome.
        """
        pass

    # -------------------------------------------------------------------------
    # Status and Progress
    # -------------------------------------------------------------------------

    @property
    def status(self) -> TaskStatus:
        """Get current task status."""
        return self._status

    @property
    def progress(self) -> TaskProgress:
        """Get current task progress."""
        return self._progress

    @property
    def is_running(self) -> bool:
        """Check if task is currently running."""
        return self._status == TaskStatus.RUNNING

    @property
    def is_enabled(self) -> bool:
        """Check if task is enabled."""
        return self._enabled

    @property
    def last_run(self) -> Optional[datetime]:
        """Get timestamp of last run."""
        return self._last_run

    @property
    def next_run(self) -> Optional[datetime]:
        """Get timestamp of next scheduled run."""
        return self._next_run

    @property
    def history(self) -> list[TaskResult]:
        """Get execution history."""
        return list(self._history)

    def get_status_dict(self) -> dict:
        """Get full status as dictionary for API responses."""
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "task_description": self.task_description,
            "status": self._status.value,
            "enabled": self._enabled,
            "progress": self._progress.to_dict(),
            "schedule": self.schedule_config.to_dict(),
            "last_run": self._last_run.isoformat() + "Z" if self._last_run else None,
            "next_run": self._next_run.isoformat() + "Z" if self._next_run else None,
            "config": self.get_config(),
        }

    # Whether the task-specific config surface (get_config/update_config) is
    # DURABLE operator settings: persisted to ScheduledTask.config by the
    # registry on save and re-applied (merge-over-defaults, via
    # update_config) on startup reconstruction (gjb01 review blocker).
    # Set False on tasks whose config is per-invocation/ephemeral state
    # (e.g. dbas_restore/dbas_sync destructive arming flags, which must
    # re-disarm on restart) or lives in its own store (m3u_digest settings
    # table) — those are neither persisted nor hydrated.
    persist_config: bool = True

    def get_config(self) -> dict:
        """
        Get task-specific configuration.
        Subclasses should override this to return their config options.
        """
        return {}

    def update_config(self, config: dict) -> None:
        """
        Update task-specific configuration.
        Subclasses should override this to apply config changes.

        Args:
            config: Dict with configuration values to update
        """
        pass

    def restore_invocation_config(self, config: dict) -> None:
        """Restore a config snapshot after an invocation-specific overlay.

        The task engine uses this after every run so schedule and ad-hoc
        parameters cannot become state on the registry singleton. Subclasses
        only need to override this when ``update_config(get_config())`` is not
        round-trippable.
        """
        self.update_config(config)

    # -------------------------------------------------------------------------
    # Notification Callbacks (set by task_engine for progress notifications)
    # -------------------------------------------------------------------------

    def set_notification_callbacks(
        self,
        create_callback=None,
        update_callback=None,
        delete_callback=None,
        show_notifications: bool = True,
        send_alerts: bool = True,
    ):
        """Set notification callbacks for progress updates."""
        self._create_notification_callback = create_callback
        self._update_notification_callback = update_callback
        self._delete_notification_callback = delete_callback
        self._show_notifications = show_notifications
        self._send_alerts = send_alerts

    async def _create_progress_notification(self):
        """Create a progress notification when task starts."""
        if not self._create_notification_callback:
            return

        # Respect the show_notifications setting
        if not self._show_notifications:
            logger.debug("[%s] Skipping progress notification (show_notifications=False)", self.task_id)
            return

        try:
            import time
            result = await self._create_notification_callback(
                notification_type="info",
                message=f"{self.task_name} starting...",
                title=self.task_name,
                source=f"task_{self.task_id}",
                source_id=f"progress_{int(time.time())}",
                send_alerts=self._send_alerts,
                metadata={
                    "progress": {
                        "current": 0,
                        "total": 0,
                        "success": 0,
                        "failed": 0,
                        "skipped": 0,
                        "status": "starting",
                        "current_stream": "",
                    }
                },
            )
            if result and "id" in result:
                self._notification_id = result["id"]
                logger.debug("[%s] Created progress notification %s", self.task_id, self._notification_id)
        except Exception as e:
            logger.warning("[%s] Failed to create progress notification: %s", self.task_id, e)

    async def _update_progress_notification(self, force: bool = False):
        """Update the progress notification (rate-limited unless force=True)."""
        if not self._notification_id or not self._update_notification_callback:
            return

        import time
        now = time.time()
        if not force and (now - self._last_notification_update) < self._notification_update_interval:
            return

        self._last_notification_update = now

        try:
            progress = self._progress
            percentage = round(progress.percentage) if progress.total > 0 else 0
            message = f"{progress.current}/{progress.total} ({percentage}%)"

            await self._update_notification_callback(
                notification_id=self._notification_id,
                message=message,
                metadata={
                    "progress": {
                        "current": progress.current,
                        "total": progress.total,
                        "success": progress.success_count,
                        "failed": progress.failed_count,
                        "skipped": progress.skipped_count,
                        "status": progress.status,
                        "current_stream": progress.current_item,
                    }
                },
            )
        except Exception as e:
            logger.warning("[%s] Failed to update progress notification: %s", self.task_id, e)

    async def _finalize_progress_notification(self, result: 'TaskResult'):
        """Finalize the progress notification when task completes."""
        if not self._notification_id or not self._update_notification_callback:
            return

        try:
            # Severity comes from the shared map so this notification can never
            # contradict the engine's completion notification for the same run
            # (asf3n), and ``status`` comes from the same closed set as the
            # history row (bdmby) so it cannot contradict that either. It used to
            # be written here by hand, which spelled a degraded run "failed" —
            # the one member that means the run did not produce what it was
            # asked for. The frontend reads this value as a terminal state
            # (notificationGrouping.FINAL_PROGRESS_STATUSES) and knows the whole
            # vocabulary.
            notification_type = completion_notification_type(result)
            status = execution_status(result)
            if result.error == "CANCELLED":
                message = f"Cancelled: {result.success_count} completed"
            elif result.success:
                if result.failed_count > 0:
                    message = f"Completed: {result.success_count} ok, {result.failed_count} failed"
                else:
                    message = f"Completed: {result.success_count} ok"
            else:
                message = result.message or "Task failed"

            await self._update_notification_callback(
                notification_id=self._notification_id,
                notification_type=notification_type,
                message=message,
                metadata={
                    "progress": {
                        "current": result.total_items,
                        "total": result.total_items,
                        "success": result.success_count,
                        "failed": result.failed_count,
                        "skipped": result.skipped_count,
                        "status": status,
                        "current_stream": "",
                    }
                },
            )
            self._notification_id = None
        except Exception as e:
            logger.warning("[%s] Failed to finalize progress notification: %s", self.task_id, e)

    # -------------------------------------------------------------------------
    # Progress Tracking (for use by subclasses)
    # -------------------------------------------------------------------------

    def _reset_progress(self):
        """Reset progress tracking for a new run."""
        self._progress = TaskProgress()
        self._cancel_requested = False

    def _set_progress(
        self,
        total: Optional[int] = None,
        current: Optional[int] = None,
        status: Optional[str] = None,
        current_item: Optional[str] = None,
        success_count: Optional[int] = None,
        failed_count: Optional[int] = None,
        skipped_count: Optional[int] = None,
    ):
        """Update progress values. Only provided values are updated."""
        if total is not None:
            self._progress.total = total
        if current is not None:
            self._progress.current = current
        if status is not None:
            self._progress.status = status
        if current_item is not None:
            self._progress.current_item = current_item
        if success_count is not None:
            self._progress.success_count = success_count
        if failed_count is not None:
            self._progress.failed_count = failed_count
        if skipped_count is not None:
            self._progress.skipped_count = skipped_count

        # Schedule notification update (rate-limited)
        self._schedule_notification_update()

    def _increment_progress(
        self,
        current: int = 0,
        success_count: int = 0,
        failed_count: int = 0,
        skipped_count: int = 0,
    ):
        """Increment progress counters."""
        self._progress.current += current
        self._progress.success_count += success_count
        self._progress.failed_count += failed_count
        self._progress.skipped_count += skipped_count

        # Schedule notification update (rate-limited)
        self._schedule_notification_update()

    def _schedule_notification_update(self):
        """Schedule a notification update if callbacks are set."""
        if self._notification_id and self._update_notification_callback:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._update_progress_notification())
            except RuntimeError:
                logger.debug("[TASK] No event loop available, skipping notification update")

    # -------------------------------------------------------------------------
    # Lifecycle Hooks (optional for subclasses to override)
    # -------------------------------------------------------------------------

    async def validate_config(self) -> tuple[bool, str]:
        """
        Validate task configuration before execution.

        Returns:
            Tuple of (is_valid, error_message). error_message is empty if valid.
        """
        return True, ""

    async def on_start(self):
        """Called when task starts running. Override for setup logic."""
        pass

    async def on_complete(self, result: TaskResult):
        """Called when task completes successfully. Override for cleanup logic."""
        pass

    async def on_error(self, error: Exception, result: TaskResult):
        """Called when task fails with an error. Override for error handling."""
        pass

    async def on_cancel(self):
        """Called when task is cancelled. Override for cancellation cleanup."""
        pass

    # -------------------------------------------------------------------------
    # Task Execution
    # -------------------------------------------------------------------------

    async def run(self) -> TaskResult:
        """
        Run the task immediately.

        This is the main entry point for task execution. It handles:
        - Status management
        - Progress tracking
        - History recording
        - Error handling
        - Lifecycle hooks

        Returns:
            TaskResult with execution outcome.
        """
        if self._status == TaskStatus.RUNNING:
            return TaskResult(
                success=False,
                message="Task is already running",
                error="ALREADY_RUNNING",
            )

        # Validate configuration
        is_valid, error_msg = await self.validate_config()
        if not is_valid:
            logger.error("[%s] Configuration validation failed: %s", self.task_id, error_msg)
            return TaskResult(
                success=False,
                message=f"Configuration validation failed: {error_msg}",
                error="CONFIG_INVALID",
            )

        # Initialize for this run
        self._reset_progress()
        self._status = TaskStatus.RUNNING
        self._progress.started_at = datetime.utcnow()
        self._progress.status = "starting"

        result = TaskResult(
            success=False,
            started_at=datetime.utcnow(),
        )

        try:
            logger.info("[%s] Starting task: %s", self.task_id, self.task_name)
            await self.on_start()

            # Create progress notification
            await self._create_progress_notification()

            # Execute the task
            result = await self.execute()
            result.started_at = self._progress.started_at
            result.completed_at = datetime.utcnow()

            if self._cancel_requested:
                self._status = TaskStatus.CANCELLED
                result.success = False
                result.message = "Task was cancelled"
                result.error = "CANCELLED"
                await self.on_cancel()
                logger.info("[%s] Task cancelled", self.task_id)
            elif result.success:
                self._status = TaskStatus.COMPLETED
                await self.on_complete(result)
                logger.info("[%s] Task completed successfully: %s", self.task_id, result.message)
            elif task_outcome(result) is TaskOutcome.WARNING:
                # The run did not succeed CLEANLY, but it ran to completion and
                # left real, kept state (bead …-daziw). Calling that "Task
                # failed" was the false positive bead …-bdmby closes:
                # ``docs/user_guide/troubleshooting/read-the-logs.md`` tells
                # operators to grep the log, and every degraded DBAS restore hit.
                # ``on_complete`` stays gated on ``result.success`` — a task's
                # own success hook is not this line's question.
                self._status = TaskStatus.COMPLETED
                logger.warning(
                    "[%s] Task completed with warnings: %s", self.task_id, result.message
                )
            else:
                self._status = TaskStatus.FAILED
                logger.warning("[%s] Task failed: %s", self.task_id, result.message)

        except Exception as e:
            self._status = TaskStatus.FAILED
            result.success = False
            result.message = f"Task failed with error: {str(e)}"
            result.error = str(e)
            result.completed_at = datetime.utcnow()
            logger.exception("[%s] Task error: %s", self.task_id, e)
            await self.on_error(e, result)
        finally:
            # Finalize progress notification
            await self._finalize_progress_notification(result)

            # Record history
            self._add_to_history(result)
            self._last_run = result.completed_at or datetime.utcnow()
            # The live terminal status is the history row's status (bdmby). It
            # used to be written from ``result.success`` alone, which spelled
            # BOTH a degraded run and a cancelled one "failed" — the field a
            # script polls to know a restore finished said the restore failed
            # while the row, the alert and the dialog all said it had not.
            self._progress.status = execution_status(result)

            # Calculate next run if scheduled
            if self._enabled and self.schedule_config.schedule_type != ScheduleType.MANUAL:
                self._calculate_next_run()

            # Reset to idle after a brief delay
            await asyncio.sleep(0.1)
            if self._status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                self._status = TaskStatus.IDLE

        return result

    def cancel(self) -> dict:
        """
        Request cancellation of the running task.

        The task's execute() method should periodically check self._cancel_requested
        and exit cleanly when True.

        Returns:
            Status dict with cancellation result.
        """
        if self._status != TaskStatus.RUNNING:
            return {
                "status": "not_running",
                "message": "Task is not currently running",
            }

        logger.info("[%s] Cancellation requested", self.task_id)
        self._cancel_requested = True
        self._progress.status = "cancelling"

        return {
            "status": "cancelling",
            "message": "Cancellation requested",
        }

    def enable(self):
        """Enable the task for scheduled execution."""
        self._enabled = True
        logger.info("[%s] Task enabled", self.task_id)
        if self.schedule_config.schedule_type != ScheduleType.MANUAL:
            self._calculate_next_run()

    def disable(self):
        """Disable the task (will not run on schedule)."""
        self._enabled = False
        self._next_run = None
        logger.info("[%s] Task disabled", self.task_id)

    # -------------------------------------------------------------------------
    # Schedule Calculation
    # -------------------------------------------------------------------------

    def _calculate_next_run(self):
        """Calculate the next scheduled run time."""
        now = datetime.utcnow()

        if self.schedule_config.schedule_type == ScheduleType.INTERVAL:
            self._next_run = now + timedelta(seconds=self.schedule_config.interval_seconds)

        elif self.schedule_config.schedule_type == ScheduleType.CRON:
            self._next_run = self._calculate_next_cron_run()

        elif self.schedule_config.schedule_time:
            # Time-of-day scheduling
            self._next_run = self._calculate_next_time_of_day_run()
        else:
            self._next_run = None

    def _calculate_next_time_of_day_run(self) -> Optional[datetime]:
        """Calculate next run time for time-of-day scheduling."""
        try:
            hour, minute = map(int, self.schedule_config.schedule_time.split(":"))
        except (ValueError, AttributeError):
            return None

        now = datetime.utcnow()

        # Handle timezone
        if self.schedule_config.timezone:
            try:
                tz = ZoneInfo(self.schedule_config.timezone)
                now_local = datetime.now(tz)
                next_run_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if next_run_local <= now_local:
                    next_run_local += timedelta(days=1)
                # Convert back to UTC
                next_run_utc = next_run_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
                return next_run_utc
            except Exception as e:
                logger.warning("[%s] Failed to use timezone %s: %s", self.task_id, self.schedule_config.timezone, e)

        # UTC fallback
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        return next_run

    def _calculate_next_cron_run(self) -> Optional[datetime]:
        """Calculate next run time from cron expression."""
        if not self.schedule_config.cron_expression:
            return None

        try:
            from croniter import croniter

            now = datetime.utcnow()
            if self.schedule_config.timezone:
                try:
                    tz = ZoneInfo(self.schedule_config.timezone)
                    now = datetime.now(tz)
                except Exception as e:
                    logger.debug("[TASK-SCHEDULER] Suppressed timezone parse error: %s", e)

            cron = croniter(self.schedule_config.cron_expression, now)
            next_time = cron.get_next(datetime)

            # Convert to UTC if we used a timezone
            if self.schedule_config.timezone and next_time.tzinfo:
                next_time = next_time.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

            return next_time
        except ImportError:
            logger.warning("[%s] croniter not installed, cron scheduling unavailable", self.task_id)
            return None
        except Exception as e:
            logger.error("[%s] Failed to parse cron expression: %s", self.task_id, e)
            return None

    def get_seconds_until_next_run(self) -> Optional[int]:
        """Get seconds until the next scheduled run."""
        if not self._next_run:
            return None

        now = datetime.utcnow()
        delta = (self._next_run - now).total_seconds()
        return max(0, int(delta))

    # -------------------------------------------------------------------------
    # History Management
    # -------------------------------------------------------------------------

    def _add_to_history(self, result: TaskResult):
        """Add a result to history, maintaining max size."""
        self._history.insert(0, result)
        if len(self._history) > self._max_history:
            self._history = self._history[:self._max_history]

    def get_history_dicts(self) -> list[dict]:
        """Get history as list of dictionaries."""
        return [r.to_dict() for r in self._history]

    def clear_history(self):
        """Clear execution history."""
        self._history = []
        logger.info("[%s] History cleared", self.task_id)

    # -------------------------------------------------------------------------
    # Configuration Update
    # -------------------------------------------------------------------------

    def update_schedule(self, schedule_config: ScheduleConfig):
        """Update the schedule configuration."""
        self.schedule_config = schedule_config
        if self._enabled and schedule_config.schedule_type != ScheduleType.MANUAL:
            self._calculate_next_run()
        logger.info("[%s] Schedule updated: %s", self.task_id, schedule_config.to_dict())
