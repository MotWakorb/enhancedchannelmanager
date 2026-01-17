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
        """Check for tasks that are due and start them."""
        registry = get_registry()
        now = datetime.utcnow()

        for task_id in registry.list_task_ids():
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

            # Check if task is enabled and due
            if not instance._enabled:
                continue

            if instance._next_run and instance._next_run <= now:
                logger.info(f"Task {task_id} is due, scheduling execution")
                asyncio.create_task(self._execute_task(task_id, triggered_by="scheduled"))

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
            result = await instance.run()

            # Update execution record
            if execution_id:
                try:
                    session = get_session()
                    execution = session.query(TaskExecution).get(execution_id)
                    if execution:
                        execution.completed_at = result.completed_at
                        execution.duration_seconds = result.duration_seconds
                        execution.status = "completed" if result.success else "failed"
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

            return result

        except Exception as e:
            logger.exception(f"[{task_id}] Task execution failed: {e}")

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
