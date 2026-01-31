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
from typing import Any, Optional
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
        self._enabled = True
        # Notification callbacks (set by task_engine)
        self._notification_id: Optional[int] = None
        self._create_notification_callback = None
        self._update_notification_callback = None
        self._delete_notification_callback = None
        self._show_notifications: bool = True  # Whether to show in NotificationCenter
        self._last_notification_update: float = 0
        self._notification_update_interval: float = 2.0  # Update every 2 seconds max

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

    # -------------------------------------------------------------------------
    # Notification Callbacks (set by task_engine for progress notifications)
    # -------------------------------------------------------------------------

    def set_notification_callbacks(
        self,
        create_callback=None,
        update_callback=None,
        delete_callback=None,
        show_notifications: bool = True,
    ):
        """Set notification callbacks for progress updates."""
        self._create_notification_callback = create_callback
        self._update_notification_callback = update_callback
        self._delete_notification_callback = delete_callback
        self._show_notifications = show_notifications

    async def _create_progress_notification(self):
        """Create a progress notification when task starts."""
        if not self._create_notification_callback:
            return

        # Respect the show_notifications setting
        if not self._show_notifications:
            logger.debug(f"[{self.task_id}] Skipping progress notification (show_notifications=False)")
            return

        try:
            import time
            result = await self._create_notification_callback(
                notification_type="info",
                message=f"{self.task_name} starting...",
                title=self.task_name,
                source=f"task_{self.task_id}",
                source_id=f"progress_{int(time.time())}",
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
                logger.debug(f"[{self.task_id}] Created progress notification {self._notification_id}")
        except Exception as e:
            logger.warning(f"[{self.task_id}] Failed to create progress notification: {e}")

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
            logger.warning(f"[{self.task_id}] Failed to update progress notification: {e}")

    async def _finalize_progress_notification(self, result: 'TaskResult'):
        """Finalize the progress notification when task completes."""
        if not self._notification_id or not self._update_notification_callback:
            return

        try:
            # Determine final type and message
            if result.error == "CANCELLED":
                notification_type = "warning"
                message = f"Cancelled: {result.success_count} completed"
                status = "cancelled"
            elif result.success:
                notification_type = "success"
                if result.failed_count > 0:
                    notification_type = "warning"
                    message = f"Completed: {result.success_count} ok, {result.failed_count} failed"
                else:
                    message = f"Completed: {result.success_count} ok"
                status = "completed"
            else:
                notification_type = "error"
                message = result.message or "Task failed"
                status = "failed"

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
            logger.warning(f"[{self.task_id}] Failed to finalize progress notification: {e}")

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
                pass  # No event loop, skip notification

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
            logger.info(f"[{self.task_id}] Starting task: {self.task_name}")
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
                logger.info(f"[{self.task_id}] Task cancelled")
            elif result.success:
                self._status = TaskStatus.COMPLETED
                await self.on_complete(result)
                logger.info(f"[{self.task_id}] Task completed successfully: {result.message}")
            else:
                self._status = TaskStatus.FAILED
                logger.warning(f"[{self.task_id}] Task failed: {result.message}")

        except Exception as e:
            self._status = TaskStatus.FAILED
            result.success = False
            result.message = f"Task failed with error: {str(e)}"
            result.error = str(e)
            result.completed_at = datetime.utcnow()
            logger.exception(f"[{self.task_id}] Task error: {e}")
            await self.on_error(e, result)
        finally:
            # Finalize progress notification
            await self._finalize_progress_notification(result)

            # Record history
            self._add_to_history(result)
            self._last_run = result.completed_at or datetime.utcnow()
            self._progress.status = "completed" if result.success else "failed"

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

        logger.info(f"[{self.task_id}] Cancellation requested")
        self._cancel_requested = True
        self._progress.status = "cancelling"

        return {
            "status": "cancelling",
            "message": "Cancellation requested",
        }

    def enable(self):
        """Enable the task for scheduled execution."""
        self._enabled = True
        logger.info(f"[{self.task_id}] Task enabled")
        if self.schedule_config.schedule_type != ScheduleType.MANUAL:
            self._calculate_next_run()

    def disable(self):
        """Disable the task (will not run on schedule)."""
        self._enabled = False
        self._next_run = None
        logger.info(f"[{self.task_id}] Task disabled")

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
                logger.warning(f"[{self.task_id}] Failed to use timezone {self.schedule_config.timezone}: {e}")

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
                except Exception:
                    pass

            cron = croniter(self.schedule_config.cron_expression, now)
            next_time = cron.get_next(datetime)

            # Convert to UTC if we used a timezone
            if self.schedule_config.timezone and next_time.tzinfo:
                next_time = next_time.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

            return next_time
        except ImportError:
            logger.warning(f"[{self.task_id}] croniter not installed, cron scheduling unavailable")
            return None
        except Exception as e:
            logger.error(f"[{self.task_id}] Failed to parse cron expression: {e}")
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
        logger.info(f"[{self.task_id}] History cleared")

    # -------------------------------------------------------------------------
    # Configuration Update
    # -------------------------------------------------------------------------

    def update_schedule(self, schedule_config: ScheduleConfig):
        """Update the schedule configuration."""
        self.schedule_config = schedule_config
        if self._enabled and schedule_config.schedule_type != ScheduleType.MANUAL:
            self._calculate_next_run()
        logger.info(f"[{self.task_id}] Schedule updated: {schedule_config.to_dict()}")
