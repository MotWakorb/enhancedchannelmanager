"""
Task Registry System.

Provides a central registry for all scheduled tasks with:
- Task registration and discovery
- Task lookup and validation
- Database synchronization for task configurations
"""
import logging
from datetime import datetime
from typing import Optional, Type

from database import get_session
from models import ScheduledTask
from task_scheduler import TaskScheduler, ScheduleConfig, ScheduleType

logger = logging.getLogger(__name__)


class TaskRegistry:
    """
    Central registry for all scheduled task types.

    Tasks are registered by their task_id and can be looked up at runtime.
    The registry synchronizes with the database to persist task configurations.
    """

    def __init__(self):
        self._tasks: dict[str, Type[TaskScheduler]] = {}
        self._instances: dict[str, TaskScheduler] = {}
        self._initialized = False

    def register(self, task_class: Type[TaskScheduler]) -> None:
        """
        Register a task class with the registry.

        Args:
            task_class: A TaskScheduler subclass to register
        """
        if not task_class.task_id:
            raise ValueError(f"Task class {task_class.__name__} has no task_id defined")

        if task_class.task_id in self._tasks:
            logger.warning(f"Task {task_class.task_id} already registered, replacing")

        self._tasks[task_class.task_id] = task_class
        logger.debug(f"Registered task: {task_class.task_id} ({task_class.task_name})")

    def unregister(self, task_id: str) -> bool:
        """
        Unregister a task from the registry.

        Args:
            task_id: The task ID to unregister

        Returns:
            True if task was removed, False if not found
        """
        if task_id in self._tasks:
            del self._tasks[task_id]
            if task_id in self._instances:
                del self._instances[task_id]
            logger.debug(f"Unregistered task: {task_id}")
            return True
        return False

    def get_task_class(self, task_id: str) -> Optional[Type[TaskScheduler]]:
        """Get a registered task class by ID."""
        return self._tasks.get(task_id)

    def get_task_instance(self, task_id: str) -> Optional[TaskScheduler]:
        """Get a task instance by ID (creates if needed)."""
        if task_id not in self._instances and task_id in self._tasks:
            self._instances[task_id] = self._tasks[task_id]()
        return self._instances.get(task_id)

    def list_tasks(self) -> list[dict]:
        """
        List all registered tasks with their metadata.

        Returns:
            List of task info dictionaries
        """
        return [
            {
                "task_id": task_class.task_id,
                "task_name": task_class.task_name,
                "description": task_class.task_description,
            }
            for task_class in self._tasks.values()
        ]

    def list_task_ids(self) -> list[str]:
        """Get list of all registered task IDs."""
        return list(self._tasks.keys())

    def is_registered(self, task_id: str) -> bool:
        """Check if a task ID is registered."""
        return task_id in self._tasks

    # -------------------------------------------------------------------------
    # Database Synchronization
    # -------------------------------------------------------------------------

    def sync_from_database(self) -> None:
        """
        Load task configurations from database and create instances.

        This should be called on startup to restore saved configurations.
        """
        logger.info("Syncing tasks from database")

        try:
            session = get_session()
            try:
                db_tasks = session.query(ScheduledTask).all()

                for db_task in db_tasks:
                    if db_task.task_id in self._tasks:
                        # Create instance with saved configuration
                        schedule_config = ScheduleConfig(
                            schedule_type=ScheduleType(db_task.schedule_type),
                            interval_seconds=db_task.interval_seconds or 0,
                            cron_expression=db_task.cron_expression or "",
                            schedule_time=db_task.schedule_time or "",
                            timezone=db_task.timezone or "",
                        )
                        instance = self._tasks[db_task.task_id](schedule_config)
                        instance._enabled = db_task.enabled
                        instance._last_run = db_task.last_run_at
                        instance._next_run = db_task.next_run_at

                        self._instances[db_task.task_id] = instance
                        logger.debug(f"Loaded task config from DB: {db_task.task_id}")
                    else:
                        logger.warning(f"Task {db_task.task_id} in DB but not registered")

                # Create instances for registered tasks not in DB
                for task_id, task_class in self._tasks.items():
                    if task_id not in self._instances:
                        self._instances[task_id] = task_class()
                        # Save to database with defaults
                        self._save_task_to_db(session, self._instances[task_id])
                        logger.debug(f"Created default config for task: {task_id}")

                session.commit()
                self._initialized = True
                logger.info(f"Synced {len(self._instances)} tasks from database")
            finally:
                session.close()
        except Exception as e:
            logger.exception(f"Failed to sync tasks from database: {e}")
            # Create instances without DB config
            for task_id, task_class in self._tasks.items():
                if task_id not in self._instances:
                    self._instances[task_id] = task_class()
            self._initialized = True

    def sync_to_database(self, task_id: Optional[str] = None) -> None:
        """
        Save task configuration(s) to database.

        Args:
            task_id: Specific task to sync, or None to sync all
        """
        try:
            session = get_session()
            try:
                if task_id:
                    if task_id in self._instances:
                        self._save_task_to_db(session, self._instances[task_id])
                else:
                    for instance in self._instances.values():
                        self._save_task_to_db(session, instance)
                session.commit()
                logger.debug(f"Synced task(s) to database: {task_id or 'all'}")
            finally:
                session.close()
        except Exception as e:
            logger.exception(f"Failed to sync task to database: {e}")

    def _save_task_to_db(self, session, instance: TaskScheduler) -> None:
        """Save a single task instance to the database."""
        import json

        db_task = session.query(ScheduledTask).filter(
            ScheduledTask.task_id == instance.task_id
        ).first()

        if db_task:
            # Update existing
            db_task.task_name = instance.task_name
            db_task.description = instance.task_description
            db_task.enabled = instance._enabled
            db_task.schedule_type = instance.schedule_config.schedule_type.value
            db_task.interval_seconds = instance.schedule_config.interval_seconds or None
            db_task.cron_expression = instance.schedule_config.cron_expression or None
            db_task.schedule_time = instance.schedule_config.schedule_time or None
            db_task.timezone = instance.schedule_config.timezone or None
            db_task.last_run_at = instance._last_run
            db_task.next_run_at = instance._next_run
            db_task.updated_at = datetime.utcnow()
        else:
            # Create new
            db_task = ScheduledTask(
                task_id=instance.task_id,
                task_name=instance.task_name,
                description=instance.task_description,
                enabled=instance._enabled,
                schedule_type=instance.schedule_config.schedule_type.value,
                interval_seconds=instance.schedule_config.interval_seconds or None,
                cron_expression=instance.schedule_config.cron_expression or None,
                schedule_time=instance.schedule_config.schedule_time or None,
                timezone=instance.schedule_config.timezone or None,
                last_run_at=instance._last_run,
                next_run_at=instance._next_run,
            )
            session.add(db_task)

    # -------------------------------------------------------------------------
    # Task Configuration API
    # -------------------------------------------------------------------------

    def update_task_config(
        self,
        task_id: str,
        enabled: Optional[bool] = None,
        schedule_type: Optional[str] = None,
        interval_seconds: Optional[int] = None,
        cron_expression: Optional[str] = None,
        schedule_time: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Update configuration for a task.

        Args:
            task_id: Task to update
            enabled: Enable/disable the task
            schedule_type: "interval", "cron", or "manual"
            interval_seconds: Interval for interval scheduling
            cron_expression: Cron expression for cron scheduling
            schedule_time: HH:MM for daily scheduling
            timezone: IANA timezone name

        Returns:
            Updated task status dict, or None if task not found
        """
        instance = self.get_task_instance(task_id)
        if not instance:
            return None

        # Update enabled state
        if enabled is not None:
            if enabled:
                instance.enable()
            else:
                instance.disable()

        # Update schedule config
        if schedule_type is not None:
            instance.schedule_config.schedule_type = ScheduleType(schedule_type)
        if interval_seconds is not None:
            instance.schedule_config.interval_seconds = interval_seconds
        if cron_expression is not None:
            instance.schedule_config.cron_expression = cron_expression
        if schedule_time is not None:
            instance.schedule_config.schedule_time = schedule_time
        if timezone is not None:
            instance.schedule_config.timezone = timezone

        # Recalculate next run if needed
        if instance._enabled and instance.schedule_config.schedule_type != ScheduleType.MANUAL:
            instance._calculate_next_run()

        # Persist to database
        self.sync_to_database(task_id)

        return instance.get_status_dict()

    def get_task_status(self, task_id: str) -> Optional[dict]:
        """Get status for a specific task."""
        instance = self.get_task_instance(task_id)
        if instance:
            return instance.get_status_dict()
        return None

    def get_all_task_statuses(self) -> list[dict]:
        """Get status for all registered tasks."""
        return [
            instance.get_status_dict()
            for instance in self._instances.values()
        ]


# Global registry instance
_registry: Optional[TaskRegistry] = None


def get_registry() -> TaskRegistry:
    """Get the global task registry instance."""
    global _registry
    if _registry is None:
        _registry = TaskRegistry()
    return _registry


def register_task(task_class: Type[TaskScheduler]) -> Type[TaskScheduler]:
    """
    Decorator to register a task class with the global registry.

    Usage:
        @register_task
        class MyTask(TaskScheduler):
            task_id = "my_task"
            ...
    """
    get_registry().register(task_class)
    return task_class
