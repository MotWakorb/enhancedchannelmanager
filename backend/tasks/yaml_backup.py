"""
YAML Backup Task.

Scheduled task to export ECM configuration as YAML and save to /config/backups/.
Supports selective section export and automatic retention cleanup.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from config import CONFIG_DIR
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)

BACKUPS_DIR = CONFIG_DIR / "backups"


@register_task
class YamlBackupTask(TaskScheduler):
    """
    Task to export ECM configuration as YAML and save to disk.

    Configuration options (stored in task config JSON):
    - sections: List of section keys to include (empty = all)
    - retention_count: Number of backup files to keep (default: 10)
    """

    task_id = "yaml_backup"
    task_name = "YAML Backup"
    task_description = "Export ECM configuration as YAML and save to /config/backups/"
    default_enabled = False

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.MANUAL,
            )
        super().__init__(schedule_config)

        self.sections: list[str] = []
        self.retention_count: int = 10

    def get_config(self) -> dict:
        return {
            "sections": self.sections,
            "retention_count": self.retention_count,
        }

    def update_config(self, config: dict) -> None:
        if "sections" in config:
            self.sections = config["sections"]
        if "retention_count" in config:
            self.retention_count = max(1, int(config["retention_count"]))

    async def execute(self) -> TaskResult:
        started_at = datetime.now(timezone.utc)
        self._set_progress(status="starting", current_item="Preparing YAML backup...")

        try:
            # Import here to avoid circular imports at module load time
            from routers.backup import build_yaml_export

            # Build export
            selected = set(self.sections) if self.sections else None
            self._set_progress(current_item="Generating YAML export...")
            yaml_str = await build_yaml_export(selected)

            # Write to disk
            BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
            filename = f"ecm-backup-{now}.yaml"
            filepath = BACKUPS_DIR / filename
            filepath.write_text(yaml_str)
            logger.info("[YAML_BACKUP] Saved backup: %s (%d bytes)", filename, len(yaml_str))

            # Retention cleanup
            self._set_progress(current_item="Cleaning up old backups...")
            deleted = self._cleanup_old_backups()

            sections_desc = ", ".join(sorted(self.sections)) if self.sections else "all"
            message = "Saved %s (%d bytes, sections: %s)" % (filename, len(yaml_str), sections_desc)
            if deleted > 0:
                message += ", deleted %d old backup(s)" % deleted

            self._set_progress(current=1, total=1, status="completed")
            return TaskResult(
                success=True,
                message=message,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                details={"filename": filename, "size_bytes": len(yaml_str), "deleted_old": deleted},
            )
        except Exception as e:
            logger.exception("[YAML_BACKUP] Backup failed: %s", e)
            return TaskResult(
                success=False,
                message="Backup failed: %s" % str(e),
                error=str(e),
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )

    def _cleanup_old_backups(self) -> int:
        """Delete oldest backup files beyond retention_count. Returns count deleted."""
        if not BACKUPS_DIR.exists():
            return 0
        files = sorted(BACKUPS_DIR.glob("ecm-backup-*.yaml"))
        to_delete = len(files) - self.retention_count
        if to_delete <= 0:
            return 0
        deleted = 0
        for f in files[:to_delete]:
            try:
                f.unlink()
                logger.info("[YAML_BACKUP] Deleted old backup: %s", f.name)
                deleted += 1
            except OSError as e:
                logger.warning("[YAML_BACKUP] Failed to delete %s: %s", f.name, e)
        return deleted
