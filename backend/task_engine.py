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
import logging
from datetime import datetime
from typing import Optional

from database import get_session
from models import TaskExecution
from task_registry import get_registry
from task_scheduler import TaskResult, TaskStatus
from journal import log_entry

logger = logging.getLogger(__name__)

# Configuration
DEFAULT_CHECK_INTERVAL = 60  # Check for due tasks every 60 seconds
MAX_CONCURRENT_TASKS = 3  # Maximum tasks running simultaneously


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

    async def start(self) -> None:
        """Start the task execution engine."""
        if self._running:
            logger.warning("Task engine already running")
            return

        logger.info("Starting task execution engine")
        self._running = True

        # Initialize registry from database
        registry = get_registry()
        registry.sync_from_database()

        # Start the scheduler loop
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info(f"Task engine started (check_interval={self.check_interval}s, max_concurrent={self.max_concurrent})")

    async def stop(self) -> None:
        """Stop the task execution engine."""
        if not self._running:
            return

        logger.info("Stopping task execution engine")
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Wait for active tasks to complete (with timeout)
        if self._active_tasks:
            logger.info(f"Waiting for {len(self._active_tasks)} active tasks to complete...")
            timeout = 30  # 30 second timeout
            start = datetime.utcnow()
            while self._active_tasks and (datetime.utcnow() - start).total_seconds() < timeout:
                await asyncio.sleep(1)

        logger.info("Task engine stopped")

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
            from main import create_notification_internal

            metadata = {
                "task_id": task_id,
                "task_name": task_name,
            }
            if result:
                metadata.update({
                    "duration_seconds": result.duration_seconds,
                    "total_items": result.total_items,
                    "success_count": result.success_count,
                    "failed_count": result.failed_count,
                    "skipped_count": result.skipped_count,
                })
                # Include failed item names if available in result details
                if result.details and result.failed_count > 0:
                    failed_streams = result.details.get("failed_streams", [])
                    if failed_streams:
                        # Extract just the names, limit to first 10 to avoid huge messages
                        failed_names = [s.get("name", f"ID:{s.get('id', '?')}") for s in failed_streams[:10]]
                        if len(failed_streams) > 10:
                            failed_names.append(f"... and {len(failed_streams) - 10} more")
                        metadata["failed_items"] = ", ".join(failed_names)

            await create_notification_internal(
                notification_type=notification_type,
                title=title,
                message=message,
                source="task",
                source_id=task_id,
                metadata=metadata,
                send_alerts=True,
                alert_category=alert_category,
            )
        except Exception as e:
            # Don't let notification failures affect task execution
            logger.error(f"Failed to send task notification: {e}")

    async def _scheduler_loop(self) -> None:
        """Main scheduler loop - checks for due tasks and executes them."""
        logger.info("Scheduler loop started")

        # Initial wait for system to stabilize
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            return

        while self._running:
            try:
                await self._check_and_run_due_tasks()
            except Exception as e:
                logger.exception(f"Error in scheduler loop: {e}")

            # Wait for next check
            try:
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break

        logger.info("Scheduler loop stopped")

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
                        logger.debug(f"Max concurrent tasks reached ({self.max_concurrent}), skipping check")
                        break

                    # Skip if already running
                    if task_id in self._active_tasks:
                        continue

                    instance = registry.get_task_instance(task_id)
                    if not instance:
                        continue

                    # Check if the parent task is enabled
                    parent_task = session.query(ScheduledTask).filter(
                        ScheduledTask.task_id == task_id
                    ).first()
                    if parent_task and not parent_task.enabled:
                        continue

                    # Found a due schedule - run the task
                    logger.info(f"Task {task_id} is due (via schedule), scheduling execution")
                    asyncio.create_task(self._execute_task_with_schedules(
                        task_id, triggered_schedules, triggered_by="scheduled"
                    ))
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error checking schedules from database: {e}")

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
                    logger.info(f"Task {task_id} is due (legacy), scheduling execution")
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
        # Execute the task
        result = await self._execute_task(task_id, triggered_by)

        # Update next_run_at for triggered schedules
        if result:
            try:
                from database import get_session
                from models import TaskSchedule, ScheduledTask
                from schedule_calculator import calculate_next_run

                session = get_session()
                try:
                    for schedule in triggered_schedules:
                        # Recalculate next run time
                        db_schedule = session.query(TaskSchedule).get(schedule.id)
                        if db_schedule and db_schedule.enabled:
                            db_schedule.next_run_at = calculate_next_run(
                                schedule_type=db_schedule.schedule_type,
                                interval_seconds=db_schedule.interval_seconds,
                                schedule_time=db_schedule.schedule_time,
                                timezone=db_schedule.timezone,
                                days_of_week=db_schedule.get_days_of_week_list(),
                                day_of_month=db_schedule.day_of_month,
                                last_run=result.completed_at,
                            )
                            logger.debug(f"Updated schedule {db_schedule.id} next_run_at to {db_schedule.next_run_at}")

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
                logger.error(f"Failed to update schedule next_run_at: {e}")

        return result

    async def _execute_task(self, task_id: str, triggered_by: str = "manual") -> Optional[TaskResult]:
        """
        Execute a task and record the result.

        Args:
            task_id: ID of task to execute
            triggered_by: Who triggered the task ("scheduled", "manual", "api")

        Returns:
            TaskResult or None if task not found
        """
        registry = get_registry()
        instance = registry.get_task_instance(task_id)

        if not instance:
            logger.error(f"Task {task_id} not found")
            return None

        # Check if already running
        async with self._lock:
            if task_id in self._active_tasks:
                logger.warning(f"Task {task_id} is already running")
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
            logger.error(f"Failed to create execution record: {e}")
            execution_id = None

        try:
            logger.info(f"[{task_id}] Starting task execution (triggered_by={triggered_by})")

            # Log task start to journal
            log_entry(
                category="task",
                action_type="start",
                entity_name=instance.task_name,
                description=f"Started {instance.task_name} ({triggered_by})",
                entity_id=execution_id,
                after_value={"task_id": task_id, "triggered_by": triggered_by},
                user_initiated=(triggered_by == "manual"),
            )

            result = await instance.run()

            # Update execution record
            if execution_id:
                try:
                    session = get_session()
                    execution = session.query(TaskExecution).get(execution_id)
                    if execution:
                        execution.completed_at = result.completed_at
                        execution.duration_seconds = result.duration_seconds
                        # Set status based on result: completed, cancelled, or failed
                        if result.error == "CANCELLED":
                            execution.status = "cancelled"
                        elif result.success:
                            execution.status = "completed"
                        else:
                            execution.status = "failed"
                        execution.success = result.success
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
                    logger.error(f"Failed to update execution record: {e}")

            # Update registry
            registry.sync_to_database(task_id)

            # Determine alert category for granular filtering
            # For stream_probe tasks, use "probe_failures" to allow min_failures threshold
            alert_category = "probe_failures" if task_id == "stream_probe" else None

            # Log task completion to journal and send notifications
            # Check for cancellation first - cancelled tasks should show a distinct message
            if result.error == "CANCELLED":
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
                await self._notify_task_result(
                    task_name=instance.task_name,
                    task_id=task_id,
                    notification_type="warning",
                    title=f"Task Cancelled: {instance.task_name}",
                    message=f"Task was cancelled. {result.success_count} items completed before cancellation"
                            + (f", {result.failed_count} failed" if result.failed_count > 0 else "")
                            + (f", {result.skipped_count} skipped" if result.skipped_count > 0 else "")
                            + f" (out of {result.total_items} total)",
                    result=result,
                    alert_category=alert_category,
                )
            elif result.success:
                log_entry(
                    category="task",
                    action_type="complete",
                    entity_name=instance.task_name,
                    description=f"Completed {instance.task_name}: {result.success_count} ok, {result.failed_count} failed",
                    entity_id=execution_id,
                    after_value={
                        "task_id": task_id,
                        "success": True,
                        "duration_seconds": result.duration_seconds,
                        "total_items": result.total_items,
                        "success_count": result.success_count,
                        "failed_count": result.failed_count,
                        "skipped_count": result.skipped_count,
                    },
                    user_initiated=(triggered_by == "manual"),
                )

                # Send notification - warning if partial failure, success if all ok
                if result.failed_count > 0:
                    # Partial success - some items failed
                    await self._notify_task_result(
                        task_name=instance.task_name,
                        task_id=task_id,
                        notification_type="warning",
                        title=f"Task Completed with Warnings: {instance.task_name}",
                        message=f"Completed with {result.failed_count} failures out of {result.total_items} items. "
                                f"({result.success_count} succeeded, {result.skipped_count} skipped)",
                        result=result,
                        alert_category=alert_category,
                    )
                else:
                    # Full success
                    await self._notify_task_result(
                        task_name=instance.task_name,
                        task_id=task_id,
                        notification_type="success",
                        title=f"Task Completed: {instance.task_name}",
                        message=f"Successfully completed. {result.success_count} items processed"
                                + (f", {result.skipped_count} skipped" if result.skipped_count else "")
                                + f" in {result.duration_seconds:.1f}s",
                        result=result,
                        alert_category=alert_category,
                    )
            else:
                log_entry(
                    category="task",
                    action_type="fail",
                    entity_name=instance.task_name,
                    description=f"Failed {instance.task_name}: {result.error or result.message}",
                    entity_id=execution_id,
                    after_value={
                        "task_id": task_id,
                        "success": False,
                        "error": result.error,
                        "message": result.message,
                    },
                    user_initiated=(triggered_by == "manual"),
                )

                # Send error notification
                await self._notify_task_result(
                    task_name=instance.task_name,
                    task_id=task_id,
                    notification_type="error",
                    title=f"Task Failed: {instance.task_name}",
                    message=result.error or result.message or "Unknown error",
                    result=result,
                    alert_category=alert_category,
                )

            return result

        except Exception as e:
            logger.exception(f"[{task_id}] Task execution failed: {e}")

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
                        execution.error = str(e)
                        session.commit()
                    session.close()
                except Exception as db_err:
                    logger.error(f"Failed to update execution record: {db_err}")

            return TaskResult(
                success=False,
                message=f"Task execution failed: {str(e)}",
                error=str(e),
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
            )

        finally:
            async with self._lock:
                self._active_tasks.discard(task_id)

    async def run_task(self, task_id: str) -> Optional[TaskResult]:
        """
        Manually run a task (API entry point).

        Args:
            task_id: ID of task to run

        Returns:
            TaskResult or None if task not found
        """
        return await self._execute_task(task_id, triggered_by="manual")

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

                executions = query.offset(offset).limit(limit).all()
                return [e.to_dict() for e in executions]
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Failed to get task history: {e}")
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
                logger.info(f"Purged {result} task execution records older than {days} days")
                return result
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Failed to purge history: {e}")
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
