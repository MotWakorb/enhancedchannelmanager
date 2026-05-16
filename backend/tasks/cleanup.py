"""
Cleanup Task.

Scheduled task to clean up old data:
- Probe history
- Task execution history
- Journal entries
- AutoCreationExecution BLOB columns (bd-ia28g)
- health_checks rows (bd-ia28g)
- Notifications (bd-ia28g)
- Orphaned data
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import inspect, text, update

from database import get_session
from models import (
    AutoCreationExecution,
    JournalEntry,
    Notification,
    StreamStats,
    TaskExecution,
)
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
    - journal_days: Keep journal entries for this many days (default: 90)
    - auto_creation_blob_days: NULL out AutoCreationExecution BLOB columns
      older than this many days (default: 30) — see bd-ia28g
    - health_checks_days: Delete health_checks rows older than this many days
      (default: 7) — see bd-ia28g
    - notifications_days: Delete notifications older than this many days
      (default: 30) — see bd-ia28g; uses ``expires_at`` if set, else ``created_at``
    - vacuum_db: Run VACUUM after cleanup (default: True)

    Retention rationale (bd-dmu8w): the journal default is 90 days because
    bulk auto-creation now produces per-entity rows (bd-91mcq), amplifying
    journal growth N-fold per run. 90 days is the documented hot-retention
    window and matches ``journal.purge_old_entries``' own default. Operators
    who need a longer audit trail should override this via the task config.

    Retention rationale (bd-ia28g, per bd-p5b8i DBA spike re-attribution):
    auto_creation_executions BLOBs were 77% of the operator DB (127MB / 165MB);
    health_checks was 14% (20MB / 53k rows); notifications 2.2MB. None had
    retention. The bd-f9gd8 spike's session_telemetry headline was the wrong
    target — session_telemetry has its own retention via StatsV2RollupTask
    (bd-7i2vv). This task now covers the three actual large tables.

    The auto_creation_executions prune NULLs out only the four large BLOB
    columns (execution_log, dry_run_results, created_entities,
    modified_entities) — the summary row (id, task_id, status, started_at,
    completed_at, statistics counts) is preserved for audit history per DBA
    recommendation. Operators keep their "what ran when, what was the result"
    audit trail while the multi-MB per-row payloads age out.
    """

    task_id = "cleanup"
    task_name = "Database Cleanup"
    task_description = "Clean up old probe history, task execution history, and journal entries"

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        # bd-ygoqr (follow-up to bd-f9gd8 DBA spike): fresh installs now default
        # the cleanup task to CRON (Sunday 02:00 UTC), not MANUAL. Without this
        # default, a long-running install never runs the journal/task-execution
        # prune unless the operator explicitly schedules it from the UI — and
        # the journal grows unboundedly (bd-dmu8w noted the 90d hot window but
        # could not enforce it without a default schedule).
        #
        # Existing operators who have explicitly set their own ScheduleConfig
        # (persisted in the ScheduledTask DB row) are NOT clobbered: see
        # `task_registry.TaskRegistry.sync_from_database` — it reconstructs
        # `ScheduleConfig` from the DB row and passes it as `schedule_config`
        # here, so this `if schedule_config is None` branch is hit ONLY on
        # fresh installs that have no DB row yet.
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.CRON,
                cron_expression="0 2 * * 0",  # Weekly on Sunday at 02:00 UTC
            )
        super().__init__(schedule_config)

        # Task-specific config - retention periods in days
        self.probe_history_days: int = 30
        self.task_history_days: int = 30
        # bd-dmu8w: 90 days hot retention for journal entries.
        # See class docstring for rationale.
        self.journal_days: int = 90
        # bd-ia28g: see class docstring for the DBA spike re-attribution
        # that motivated these three new retention blocks.
        self.auto_creation_blob_days: int = 30
        self.health_checks_days: int = 7
        self.notifications_days: int = 30
        self.vacuum_db: bool = True

    def get_config(self) -> dict:
        """Get cleanup task configuration."""
        return {
            "probe_history_days": self.probe_history_days,
            "task_history_days": self.task_history_days,
            "journal_days": self.journal_days,
            "auto_creation_blob_days": self.auto_creation_blob_days,
            "health_checks_days": self.health_checks_days,
            "notifications_days": self.notifications_days,
            "vacuum_db": self.vacuum_db,
        }

    def update_config(self, config: dict) -> None:
        """Update cleanup task configuration."""
        if "probe_history_days" in config:
            self.probe_history_days = config["probe_history_days"]
        if "task_history_days" in config:
            self.task_history_days = config["task_history_days"]
        if "journal_days" in config:
            self.journal_days = config["journal_days"]
        if "auto_creation_blob_days" in config:
            self.auto_creation_blob_days = config["auto_creation_blob_days"]
        if "health_checks_days" in config:
            self.health_checks_days = config["health_checks_days"]
        if "notifications_days" in config:
            self.notifications_days = config["notifications_days"]
        if "vacuum_db" in config:
            self.vacuum_db = config["vacuum_db"]

    async def execute(self) -> TaskResult:
        """Execute the cleanup task."""
        started_at = datetime.utcnow()
        deleted_counts = {}
        errors = []

        # 7 prune operations total:
        # 1. stream_stats failed/pending probes
        # 2. task_executions
        # 3. journal_entries
        # 4. auto_creation_executions BLOB null-out (bd-ia28g)
        # 5. health_checks (bd-ia28g)
        # 6. notifications (bd-ia28g)
        # 7. VACUUM
        total_steps = 7

        self._set_progress(
            total=total_steps,
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
                    logger.info("[%s] Deleted %s old failed/pending probe entries", self.task_id, result)
                except Exception as e:
                    logger.error("[%s] Failed to clean probe history: %s", self.task_id, e)
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
                    logger.info("[%s] Deleted %s old task execution records", self.task_id, result)
                except Exception as e:
                    logger.error("[%s] Failed to clean task history: %s", self.task_id, e)
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
                    logger.info("[%s] Deleted %s old journal entries", self.task_id, result)
                except Exception as e:
                    logger.error("[%s] Failed to clean journal: %s", self.task_id, e)
                    errors.append(f"Journal cleanup: {str(e)}")
                    session.rollback()

                if self._cancel_requested:
                    session.close()
                    return self._cancelled_result(started_at, deleted_counts)

                # 4. bd-ia28g: NULL out BLOB columns in old auto_creation_executions
                # rows. KEEP the summary row (id, status, started_at, counts, etc.)
                # for audit history per DBA recommendation — only the multi-MB
                # JSON BLOB payloads age out. The idempotency gate ORs across
                # all four BLOB columns: a row is eligible if ANY of them is
                # non-NULL. Filtering on execution_log alone would miss rows
                # whose per-stream log was empty (auto_creation_engine writes
                # ``execution_log = json.dumps(log) if log else None`` — see
                # ``models.AutoCreationExecution.set_execution_log``) but whose
                # created_entities / modified_entities / dry_run_results
                # payloads were populated. After one prune all four columns are
                # NULL, so the next pass naturally returns 0 rows (idempotent)
                # and the log message reflects only rows actually pruned this
                # run.
                self._set_progress(
                    current=4,
                    current_item="Pruning auto_creation_executions BLOB columns",
                )
                auto_creation_blob_cutoff = datetime.utcnow() - timedelta(
                    days=self.auto_creation_blob_days
                )

                try:
                    result = session.execute(
                        update(AutoCreationExecution)
                        .where(AutoCreationExecution.started_at < auto_creation_blob_cutoff)
                        .where(
                            AutoCreationExecution.execution_log.isnot(None)
                            | AutoCreationExecution.dry_run_results.isnot(None)
                            | AutoCreationExecution.created_entities.isnot(None)
                            | AutoCreationExecution.modified_entities.isnot(None)
                        )
                        .values(
                            execution_log=None,
                            dry_run_results=None,
                            created_entities=None,
                            modified_entities=None,
                        )
                    )
                    deleted_counts["auto_creation_blobs_pruned"] = result.rowcount
                    session.commit()
                    logger.info(
                        "[%s] Pruned BLOB columns from %s auto_creation_executions row(s) older than %s days",
                        self.task_id,
                        result.rowcount,
                        self.auto_creation_blob_days,
                    )
                except Exception as e:
                    logger.error(
                        "[%s] Failed to prune auto_creation_executions BLOBs: %s",
                        self.task_id,
                        e,
                    )
                    errors.append(f"AutoCreation BLOB prune: {str(e)}")
                    session.rollback()

                if self._cancel_requested:
                    session.close()
                    return self._cancelled_result(started_at, deleted_counts)

                # 5. bd-ia28g: delete old health_checks rows. The health_checks
                # table is NOT defined in models.py — it's a legacy table
                # created by the v0.11.6 Background Health Check Service spec
                # (bd-lq8dq.1.13). The service itself is no longer in the
                # codebase, but the table persists on long-running installs
                # (53k rows / 20MB on the GH #243 operator). Use raw SQL with
                # an inspect()-based existence check so fresh installs (which
                # never had the table) are silent no-ops, not error noise.
                self._set_progress(
                    current=5,
                    current_item="Cleaning health_checks history",
                )
                health_cutoff = datetime.utcnow() - timedelta(days=self.health_checks_days)

                try:
                    inspector = inspect(session.bind)
                    if "health_checks" in inspector.get_table_names():
                        # Legacy schema didn't standardize a timestamp column
                        # name. Try the common variants in order; fall back to
                        # skipping the prune (and logging) if none match.
                        columns = {c["name"] for c in inspector.get_columns("health_checks")}
                        ts_column = next(
                            (c for c in ("created_at", "checked_at", "timestamp") if c in columns),
                            None,
                        )
                        if ts_column is None:
                            logger.warning(
                                "[%s] health_checks table present but no recognized timestamp column (created_at/checked_at/timestamp); skipping prune",
                                self.task_id,
                            )
                            deleted_counts["health_checks"] = 0
                        else:
                            # Safe: ts_column is drawn from a hardcoded allowlist tuple above, not user input.
                            result = session.execute(
                                text(
                                    f"DELETE FROM health_checks WHERE {ts_column} < :cutoff"
                                ),
                                {"cutoff": health_cutoff},
                            )
                            deleted_counts["health_checks"] = result.rowcount
                            session.commit()
                            logger.info(
                                "[%s] Deleted %s old health_checks rows (older than %s days, by %s)",
                                self.task_id,
                                result.rowcount,
                                self.health_checks_days,
                                ts_column,
                            )
                    else:
                        # Fresh installs never had this table — silent no-op.
                        deleted_counts["health_checks"] = 0
                except Exception as e:
                    logger.error("[%s] Failed to clean health_checks: %s", self.task_id, e)
                    errors.append(f"Health checks cleanup: {str(e)}")
                    session.rollback()

                if self._cancel_requested:
                    session.close()
                    return self._cancelled_result(started_at, deleted_counts)

                # 6. bd-ia28g: delete old notifications. Per DBA recommendation,
                # use the row's ``expires_at`` if set (auto-expiring notifications
                # honor their own TTL); otherwise fall back to ``created_at`` +
                # notifications_days. This avoids deleting a notification that
                # was deliberately given a long ``expires_at`` ("keep this for 90
                # days") just because it happens to be older than 30 days by
                # created_at.
                self._set_progress(
                    current=6,
                    current_item="Cleaning notifications",
                )
                notifications_cutoff = datetime.utcnow() - timedelta(
                    days=self.notifications_days
                )

                try:
                    now = datetime.utcnow()
                    result = session.query(Notification).filter(
                        # Row qualifies for deletion when:
                        # - expires_at IS NOT NULL AND expires_at < now (TTL expired), OR
                        # - expires_at IS NULL AND created_at < cutoff (age-based fallback)
                        (
                            (Notification.expires_at.isnot(None) & (Notification.expires_at < now))
                            | (Notification.expires_at.is_(None) & (Notification.created_at < notifications_cutoff))
                        )
                    ).delete(synchronize_session=False)
                    deleted_counts["notifications"] = result
                    session.commit()
                    logger.info(
                        "[%s] Deleted %s old notifications (expired or older than %s days)",
                        self.task_id,
                        result,
                        self.notifications_days,
                    )
                except Exception as e:
                    logger.error("[%s] Failed to clean notifications: %s", self.task_id, e)
                    errors.append(f"Notifications cleanup: {str(e)}")
                    session.rollback()

                if self._cancel_requested:
                    session.close()
                    return self._cancelled_result(started_at, deleted_counts)

                # 7. VACUUM the database
                self._set_progress(current=7, current_item="Vacuuming database")

                if self.vacuum_db:
                    try:
                        # VACUUM must run OUTSIDE any transaction. SQLAlchemy
                        # 2.0 sessions use implicit transactions: even after
                        # ``session.commit()``, the next ``session.execute()``
                        # opens a NEW transaction and SQLite raises
                        # ``OperationalError: cannot VACUUM from within a
                        # transaction``. The fix is to acquire a raw DBAPI
                        # connection from the engine (``session.bind`` is the
                        # bound ``Engine``) — its ``connect()`` context yields
                        # a Connection in autocommit-ish mode that does NOT
                        # auto-open a transaction around bare statements,
                        # matching the established pattern in
                        # ``database._perform_maintenance`` (see
                        # ``backend/database.py``'s startup VACUUM call site).
                        # The explicit ``session.commit()`` above is kept so
                        # any pending session-level writes are flushed before
                        # we hand off to the raw connection.
                        session.commit()
                        with session.bind.connect() as conn:
                            conn.execute(text("VACUUM"))
                        deleted_counts["vacuum"] = "completed"
                        logger.info("[%s] Database vacuum completed", self.task_id)
                    except Exception as e:
                        logger.error("[%s] Failed to vacuum database: %s", self.task_id, e)
                        errors.append(f"Vacuum: {str(e)}")

                    # bd-ygoqr: publish post-VACUUM file size onto the
                    # ecm_database_size_bytes / ecm_database_wal_size_bytes
                    # gauges. Done unconditionally after the VACUUM block
                    # (success OR failure) so a failed VACUUM still gets
                    # the current size onto the gauge — operators care more
                    # about "what is it now?" than "did the most recent
                    # VACUUM succeed?" (the latter is in the task result).
                    try:
                        from observability import update_database_size_metrics
                        update_database_size_metrics()
                    except Exception as exc:  # pragma: no cover — observability is best-effort
                        logger.debug("[%s] DB size metric publish failed: %s", self.task_id, exc)

            finally:
                session.close()

            # Calculate totals. ``auto_creation_blobs_pruned`` is intentionally
            # excluded from ``total_deleted`` and reported separately — those
            # rows are BLOB-nulled (summary rows preserved), not deleted, so
            # bundling them into the "deleted N records" headline would
            # mislead an operator reading the log into thinking summary rows
            # were removed.
            total_deleted = sum(
                v for k, v in deleted_counts.items()
                if isinstance(v, int) and k != "auto_creation_blobs_pruned"
            )
            total_blobs_nulled = deleted_counts.get("auto_creation_blobs_pruned", 0)
            if not isinstance(total_blobs_nulled, int):
                total_blobs_nulled = 0

            self._set_progress(
                success_count=total_deleted + total_blobs_nulled,
                failed_count=len(errors),
                status="completed",
            )

            summary_line = (
                f"deleted {total_deleted} records, "
                f"nulled {total_blobs_nulled} auto-creation BLOBs"
            )

            if errors:
                return TaskResult(
                    success=len(errors) < total_steps,  # Partial success if some operations worked
                    message=f"Cleanup completed with {len(errors)} errors. {summary_line}.",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=total_steps,
                    success_count=total_steps - len(errors),
                    failed_count=len(errors),
                    details={"deleted": deleted_counts, "errors": errors},
                )

            return TaskResult(
                success=True,
                message=f"Cleanup completed. {summary_line.capitalize()}.",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=total_steps,
                success_count=total_steps,
                failed_count=0,
                details={"deleted": deleted_counts},
            )

        except Exception as e:
            logger.exception("[%s] Cleanup failed: %s", self.task_id, e)
            return TaskResult(
                success=False,
                message=f"Cleanup failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

    def _cancelled_result(self, started_at: datetime, deleted_counts: dict) -> TaskResult:
        """Create a cancelled result with partial progress.

        Mirrors the main execute() reporting: ``auto_creation_blobs_pruned`` is
        a NULL-out, not a delete, so it gets its own counter rather than being
        folded into the "deleted N" headline.
        """
        total_deleted = sum(
            v for k, v in deleted_counts.items()
            if isinstance(v, int) and k != "auto_creation_blobs_pruned"
        )
        total_blobs_nulled = deleted_counts.get("auto_creation_blobs_pruned", 0)
        if not isinstance(total_blobs_nulled, int):
            total_blobs_nulled = 0
        return TaskResult(
            success=False,
            message=(
                f"Cleanup cancelled. Deleted {total_deleted} records, "
                f"nulled {total_blobs_nulled} auto-creation BLOBs before cancellation."
            ),
            error="CANCELLED",
            started_at=started_at,
            completed_at=datetime.utcnow(),
            details={"deleted": deleted_counts},
        )
