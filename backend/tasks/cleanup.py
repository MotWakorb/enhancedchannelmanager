"""
Cleanup Task.

Scheduled task to clean up old data:
- Probe history
- Task execution history
- Journal entries
- Orphaned data
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from database import get_session
from models import StreamStats, TaskExecution, JournalEntry
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)


@register_task
class CleanupTask(TaskScheduler):
    """
    Task to clean up old data from the database.

    Configuration options (stored in task config JSON):
    - probe_history_days: Keep probe history for this many days (default: 30)
    - task_history_days: Keep task execution history for this many days (default: 30)
    - journal_days: Keep journal entries for this many days (default: 30)
    - vacuum_db: Run VACUUM after cleanup (default: True)
    """

    task_id = "cleanup"
    task_name = "Database Cleanup"
    task_description = "Clean up old probe history, task execution history, and journal entries"

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        # Default to weekly on Sunday at 2 AM
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.MANUAL,
                cron_expression="0 2 * * 0",  # Weekly on Sunday at 2 AM
            )
        super().__init__(schedule_config)

        # Task-specific config - retention periods in days
        self.probe_history_days: int = 30
        self.task_history_days: int = 30
        self.journal_days: int = 30
        self.vacuum_db: bool = True

    async def execute(self) -> TaskResult:
        """Execute the cleanup task."""
        started_at = datetime.utcnow()
        deleted_counts = {}
        errors = []

        self._set_progress(
            total=4,  # 4 cleanup operations
            current=0,
            status="cleaning",
        )

        try:
            session = get_session()
            try:
                # 1. Clean up old failed/pending probe entries
                self._set_progress(current=1, current_item="Cleaning old probe data")
                probe_cutoff = datetime.utcnow() - timedelta(days=self.probe_history_days)

                try:
                    # Delete stream stats that haven't been probed in a while
                    # and have failed/pending status (keep successful probes)
                    result = session.query(StreamStats).filter(
                        StreamStats.last_probed < probe_cutoff,
                        StreamStats.probe_status.in_(["failed", "timeout", "pending"]),
                    ).delete(synchronize_session=False)
                    deleted_counts["probe_failed"] = result
                    session.commit()
                    logger.info(f"[{self.task_id}] Deleted {result} old failed/pending probe entries")
                except Exception as e:
                    logger.error(f"[{self.task_id}] Failed to clean probe history: {e}")
                    errors.append(f"Probe cleanup: {str(e)}")
                    session.rollback()

                if self._cancel_requested:
                    session.close()
                    return self._cancelled_result(started_at, deleted_counts)

                # 2. Clean up old task execution history
                self._set_progress(current=2, current_item="Cleaning task execution history")
                task_cutoff = datetime.utcnow() - timedelta(days=self.task_history_days)

                try:
                    result = session.query(TaskExecution).filter(
                        TaskExecution.started_at < task_cutoff,
                    ).delete(synchronize_session=False)
                    deleted_counts["task_executions"] = result
                    session.commit()
                    logger.info(f"[{self.task_id}] Deleted {result} old task execution records")
                except Exception as e:
                    logger.error(f"[{self.task_id}] Failed to clean task history: {e}")
                    errors.append(f"Task history cleanup: {str(e)}")
                    session.rollback()

                if self._cancel_requested:
                    session.close()
                    return self._cancelled_result(started_at, deleted_counts)

                # 3. Clean up old journal entries
                self._set_progress(current=3, current_item="Cleaning journal entries")
                journal_cutoff = datetime.utcnow() - timedelta(days=self.journal_days)

                try:
                    result = session.query(JournalEntry).filter(
                        JournalEntry.timestamp < journal_cutoff,
                    ).delete(synchronize_session=False)
                    deleted_counts["journal_entries"] = result
                    session.commit()
                    logger.info(f"[{self.task_id}] Deleted {result} old journal entries")
                except Exception as e:
                    logger.error(f"[{self.task_id}] Failed to clean journal: {e}")
                    errors.append(f"Journal cleanup: {str(e)}")
                    session.rollback()

                if self._cancel_requested:
                    session.close()
                    return self._cancelled_result(started_at, deleted_counts)

                # 4. VACUUM the database
                self._set_progress(current=4, current_item="Vacuuming database")

                if self.vacuum_db:
                    try:
                        from sqlalchemy import text
                        # VACUUM must be outside transaction
                        session.commit()
                        session.execute(text("VACUUM"))
                        deleted_counts["vacuum"] = "completed"
                        logger.info(f"[{self.task_id}] Database vacuum completed")
                    except Exception as e:
                        logger.error(f"[{self.task_id}] Failed to vacuum database: {e}")
                        errors.append(f"Vacuum: {str(e)}")

            finally:
                session.close()

            # Calculate totals
            total_deleted = sum(
                v for k, v in deleted_counts.items()
                if isinstance(v, int)
            )

            self._set_progress(
                success_count=total_deleted,
                failed_count=len(errors),
                status="completed",
            )

            if errors:
                return TaskResult(
                    success=len(errors) < 4,  # Partial success if some operations worked
                    message=f"Cleanup completed with {len(errors)} errors. Deleted {total_deleted} records.",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=4,
                    success_count=4 - len(errors),
                    failed_count=len(errors),
                    details={"deleted": deleted_counts, "errors": errors},
                )

            return TaskResult(
                success=True,
                message=f"Cleanup completed. Deleted {total_deleted} old records.",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=4,
                success_count=4,
                failed_count=0,
                details={"deleted": deleted_counts},
            )

        except Exception as e:
            logger.exception(f"[{self.task_id}] Cleanup failed: {e}")
            return TaskResult(
                success=False,
                message=f"Cleanup failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

    def _cancelled_result(self, started_at: datetime, deleted_counts: dict) -> TaskResult:
        """Create a cancelled result with partial progress."""
        total_deleted = sum(
            v for k, v in deleted_counts.items()
            if isinstance(v, int)
        )
        return TaskResult(
            success=False,
            message=f"Cleanup cancelled. Deleted {total_deleted} records before cancellation.",
            error="CANCELLED",
            started_at=started_at,
            completed_at=datetime.utcnow(),
            details={"deleted": deleted_counts},
        )
