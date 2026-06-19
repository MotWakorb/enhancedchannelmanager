"""DBAS Backup Task (bead 0i2vt.6).

Scheduled + manually-triggerable producer of the new-format DBAS backup
artifact (the ``.7`` builder, :func:`routers.backup.build_backup_artifact`).
The artifact is a redacted, sealed ZIP plus a ``.sha256`` sidecar written into
``CONFIG_DIR / "backups"`` (same dir convention as ``YamlBackupTask``).

Schedules: MANUAL (default), CRON and INTERVAL, via the existing
``ScheduleConfig`` mechanism. Manual triggering uses the existing
``POST /api/tasks/{task_id}/run`` endpoint — no bespoke endpoint here.

Ships OFF by default (``default_enabled = False``) per the PO decision: a
less-engaged operator can't silently end up with zero backups, so the
one-time "set one up" banner (a separate frontend sub-task) covers discovery.

Fire-time credential-freshness gate (ADR-008 Security Mandatory #5)
------------------------------------------------------------------
The task config may carry an optional ``cloud_target_id`` plus the
``cloud_credential_version`` captured when the schedule was configured. There
is NO owner/user_id model (ECM is single-admin) and NO new DB column — the
two fields live in the task config JSON (``task_schedules.parameters``).

At fire time, when ``cloud_target_id`` is set the task re-reads the
``CloudStorageTarget`` FRESH from the DB and ABORTS the run (no artifact
produced) if ANY of:

  * the target is missing,
  * ``enabled`` is False,
  * ``token_revoked_at`` is not None (hard stop), or
  * ``credential_version`` no longer matches the captured version.

The actual cloud UPLOAD is bead 0i2vt.8 — not this bead. The validated seam
is marked ``TODO(0i2vt.8)`` below.

Silent-skip MUST notify
-----------------------
A freshness-gate abort is NOT silent: it emits a WARN log (``[DBAS_BACKUP]``
prefix), a ``journal`` entry, AND a NotificationCenter notification, and
returns ``TaskResult(success=False, ...)``. A scheduled backup that silently
stops = false safety.

Metric: ``ecm_backup_runs_total{result="success"|"skipped"|"failed"}``.

Concurrency: the engine's ``TaskScheduler.run()`` already rejects a second
concurrent run of the same ``task_id`` (ALREADY_RUNNING guard), so no extra
self-exclusion is needed here.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from config import CONFIG_DIR
from services.notification_service import create_notification_internal
from task_registry import register_task
from task_scheduler import ScheduleConfig, ScheduleType, TaskResult, TaskScheduler

import journal
import observability
from routers.backup import build_backup_artifact

logger = logging.getLogger(__name__)

BACKUPS_DIR = CONFIG_DIR / "backups"


def _bump_metric(result: str) -> None:
    """Increment ecm_backup_runs_total for a result label, best-effort."""
    try:
        observability.get_metric("backup_runs_total").labels(result=result).inc()
    except Exception as e:  # pragma: no cover — metrics best-effort
        logger.warning("[DBAS_BACKUP] Failed to increment backup_runs_total: %s", e)


@register_task
class DbasBackupTask(TaskScheduler):
    """Build the new-format DBAS backup artifact on a schedule or on demand.

    Configuration options (stored in task config JSON):
    - cloud_target_id: Optional[int] — the CloudStorageTarget this schedule is
      bound to. None = local-only backup (freshness gate is a no-op).
    - cloud_credential_version: Optional[int] — the target's
      ``credential_version`` captured when the schedule was configured. Checked
      fresh against the DB at fire time.
    """

    task_id = "dbas_backup"
    task_name = "DBAS Backup"
    task_description = (
        "Build a redacted, sealed DBAS backup artifact (ZIP + SHA-256 sidecar) "
        "in /config/backups/. Scheduled or manual."
    )
    default_enabled = False

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        if schedule_config is None:
            schedule_config = ScheduleConfig(schedule_type=ScheduleType.MANUAL)
        super().__init__(schedule_config)

        self.cloud_target_id: Optional[int] = None
        self.cloud_credential_version: Optional[int] = None

    def get_config(self) -> dict:
        return {
            "cloud_target_id": self.cloud_target_id,
            "cloud_credential_version": self.cloud_credential_version,
        }

    def update_config(self, config: dict) -> None:
        if "cloud_target_id" in config:
            val = config["cloud_target_id"]
            self.cloud_target_id = int(val) if val is not None else None
        if "cloud_credential_version" in config:
            val = config["cloud_credential_version"]
            self.cloud_credential_version = int(val) if val is not None else None

    async def execute(self) -> TaskResult:
        started_at = datetime.now(timezone.utc)
        self._set_progress(
            total=1, current=0, status="starting",
            current_item="Preparing DBAS backup...",
        )

        # Fire-time credential-freshness gate. Returns a SKIP TaskResult when
        # the bound target is no longer usable; None means "proceed".
        if self.cloud_target_id is not None:
            skip = await self._check_credential_freshness(started_at)
            if skip is not None:
                return skip

        # TODO(0i2vt.8): once the gate passes and a cloud_target_id is set,
        # the cloud-upload step goes HERE (after the artifact is built below),
        # streaming the sealed ZIP to the validated target. The validation
        # above is fully built + tested now; only the upload is deferred.

        try:
            self._set_progress(
                current_item="Building backup artifact...", status="running",
            )
            artifact = await build_backup_artifact(dest_dir=BACKUPS_DIR)

            filename = artifact.zip_path.name
            logger.info(
                "[DBAS_BACKUP] Built artifact %s "
                "(schema_version=%d, %d members, sha256=%s)",
                filename, artifact.schema_version, artifact.file_count,
                artifact.sha256,
            )

            _bump_metric("success")
            self._set_progress(current=1, total=1, status="completed")
            return TaskResult(
                success=True,
                message="Built DBAS backup %s (schema v%d, %d files)" % (
                    filename, artifact.schema_version, artifact.file_count,
                ),
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                total_items=1,
                success_count=1,
                details={
                    "filename": filename,
                    "schema_version": artifact.schema_version,
                    "sha256": artifact.sha256,
                    "file_count": artifact.file_count,
                },
            )
        except Exception as e:
            logger.exception("[DBAS_BACKUP] Backup build failed: %s", e)
            _bump_metric("failed")
            return TaskResult(
                success=False,
                message="DBAS backup build failed: %s" % str(e),
                error=str(e),
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                failed_count=1,
            )

    async def _check_credential_freshness(
        self, started_at: datetime
    ) -> Optional[TaskResult]:
        """Re-read the bound CloudStorageTarget FRESH and abort if stale.

        Returns a SKIP ``TaskResult`` (and emits WARN + journal + notification
        + metric) when the target is missing/disabled/revoked/rotated, or
        ``None`` when the run may proceed.
        """
        from database import get_session
        from export_models import CloudStorageTarget

        reason: Optional[str] = None
        session = get_session()
        try:
            target = (
                session.query(CloudStorageTarget)
                .filter(CloudStorageTarget.id == self.cloud_target_id)
                .first()
            )
            if target is None:
                reason = "target %s no longer exists" % self.cloud_target_id
            elif not target.enabled:
                reason = "target '%s' (id=%s) is disabled" % (
                    target.name, target.id,
                )
            elif target.token_revoked_at is not None:
                reason = "credentials for target '%s' (id=%s) were revoked" % (
                    target.name, target.id,
                )
            elif target.credential_version != self.cloud_credential_version:
                reason = (
                    "credentials for target '%s' (id=%s) were rotated "
                    "(configured v%s, current v%s)" % (
                        target.name, target.id,
                        self.cloud_credential_version, target.credential_version,
                    )
                )
        finally:
            session.close()

        if reason is None:
            return None

        return await self._abort_skip(started_at, reason)

    async def _abort_skip(
        self, started_at: datetime, reason: str
    ) -> TaskResult:
        """Emit a NON-SILENT skip (WARN + journal + notification + metric)
        and return a failed TaskResult. A scheduled backup that silently
        stops = false safety."""
        message = (
            "DBAS backup skipped — %s. No backup was produced. Review the "
            "cloud target configuration or update the backup schedule." % reason
        )
        logger.warning("[DBAS_BACKUP] %s", message)

        _bump_metric("skipped")

        # Journal entry (audit trail).
        try:
            journal.log_entry(
                category="backup",
                action_type="scheduled_backup_skipped",
                entity_name="DBAS Backup",
                description=message,
                user_initiated=False,
            )
        except Exception as e:  # pragma: no cover — journal best-effort
            logger.warning("[DBAS_BACKUP] Failed to journal skip: %s", e)

        # NotificationCenter notification (operator-visible).
        try:
            await create_notification_internal(
                notification_type="warning",
                title="DBAS Backup: Skipped",
                message=message,
                source="task_dbas_backup",
                source_id="credential_freshness",
                send_alerts=True,
            )
        except Exception as e:  # pragma: no cover — notification best-effort
            logger.warning("[DBAS_BACKUP] Failed to emit skip notification: %s", e)

        self._set_progress(current=0, total=1, status="failed", skipped_count=1)
        return TaskResult(
            success=False,
            message=message,
            error="CREDENTIAL_FRESHNESS_ABORT",
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            total_items=1,
            skipped_count=1,
            details={"skipped": True, "reason": reason},
        )
