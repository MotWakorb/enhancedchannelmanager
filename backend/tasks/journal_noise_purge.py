"""
Journal Noise Purge Task (enhancedchannelmanager-uliyr;
extended by enhancedchannelmanager-gjb01).

PO policy decision 2026-07-17 (uliyr): auto-purge journal entries older than
3 DAYS for two automated-noise buckets:

- Watch start/stop events (``category="watch"``, written only by the
  bandwidth tracker with ``user_initiated=False``).
- Channel Pipeline rule create/delete pairs (``category="auto_creation"``,
  ``action_type`` "create"/"delete" — dominated by E2E test-rule churn like
  'E2E event_sync happy path'). PO follow-up decision 2026-07-19: only rows
  self-declared automated (``automated_client=True``, via the harness's
  ``X-ECM-Automated-Client`` header) and pre-marker legacy rows
  (``automated_client`` NULL) age out; operator-initiated rows
  (``automated_client=False``) are KEPT.

PO decision 2026-07-19 (gjb01): two further buckets on the same settings
surface, defaulting to enabled at the same shared retention:

- Run-on-refresh suppression notices (``category="auto_creation"``,
  ``action_type="run_on_refresh_skipped"``, written only by the
  circuit-breaker/break-glass suppression path with ``user_initiated=False``).
- Scheduled-task start/complete lifecycle rows (``category="task"``,
  ``action_type`` "start"/"complete", written only by ``task_engine``).
  Manually-triggered runs and the anomaly rows (cancel/fail/error) are
  KEPT — the per-run forensic record also survives independently in the
  ``task_executions`` table under CleanupTask's ``task_history_days``.

ALL other categories are untouched — the manual Purge control on the Journal
tab (and ``CleanupTask``'s general ``journal_days`` retention) remain the
tools for everything else. The exact filter predicates and the provenance
rationale live with the delete itself in ``journal.purge_noise_entries``.

This is a SEPARATE task from ``CleanupTask`` (not folded in) because the
retention windows demand different cadences: CleanupTask runs weekly (Sunday
02:00 UTC), which against a 3-day window would let noise live up to 10 days.
A daily run keeps the worst case ~4 days. The default slot (03:45 UTC) is
offset from both CleanupTask (Sunday 02:00) and StatsV2RollupTask (daily
03:30) so the maintenance passes never contend for the SQLite write lock.
"""
import logging
from datetime import datetime
from typing import Optional

import journal
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)


@register_task
class JournalNoisePurgeTask(TaskScheduler):
    """
    Task to purge automated-noise journal entries.

    Configuration options (stored in task config JSON, surfaced in the
    Scheduled Tasks settings UI — TaskEditorModal):
    - retention_days: Delete noise entries older than this many days
      (default: 3 — PO-decided; minimum 1; shared by all four buckets).
    - purge_watch_events: Include the watch start/stop bucket (default: True).
    - purge_pipeline_rule_pairs: Include the Channel Pipeline rule
      create/delete bucket (default: True).
    - purge_run_on_refresh_skipped: Include the run-on-refresh
      suppression-notice bucket (default: True).
    - purge_task_start_complete: Include the scheduled-task start/complete
      lifecycle bucket (default: True).
    """

    task_id = "journal_noise_purge"
    task_name = "Journal Noise Purge"
    task_description = (
        "Purge automated-noise journal entries (watch start/stop events, "
        "automated Channel Pipeline rule create/delete pairs, run-on-refresh "
        "suppression notices, and scheduled-task start/complete rows) older "
        "than the retention window. Operator-initiated entries, task "
        "cancel/fail/error rows, and all other journal categories are "
        "untouched."
    )

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        # Fresh installs default to a daily CRON run. Existing operators who
        # persisted their own ScheduleConfig are not clobbered — the registry
        # rehydrates the DB row and passes it here (same contract as
        # CleanupTask / bd-ygoqr).
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.CRON,
                cron_expression="45 3 * * *",  # Daily at 03:45 UTC
            )
        super().__init__(schedule_config)

        self.retention_days: int = journal.NOISE_RETENTION_DEFAULT_DAYS
        self.purge_watch_events: bool = True
        self.purge_pipeline_rule_pairs: bool = True
        self.purge_run_on_refresh_skipped: bool = True
        self.purge_task_start_complete: bool = True

    def get_config(self) -> dict:
        """Get journal noise purge configuration."""
        return {
            "retention_days": self.retention_days,
            "purge_watch_events": self.purge_watch_events,
            "purge_pipeline_rule_pairs": self.purge_pipeline_rule_pairs,
            "purge_run_on_refresh_skipped": self.purge_run_on_refresh_skipped,
            "purge_task_start_complete": self.purge_task_start_complete,
        }

    def update_config(self, config: dict) -> None:
        """Update journal noise purge configuration.

        ``retention_days`` below 1 is rejected (kept at its current value):
        a scheduled deleter must never be misconfigured into sweeping fresh
        rows — matching the min=1 constraint the settings UI enforces.
        """
        if "retention_days" in config:
            try:
                days = int(config["retention_days"])
            except (TypeError, ValueError):
                days = 0
            if days >= 1:
                self.retention_days = days
            else:
                logger.warning(
                    "[%s] Ignoring invalid retention_days=%r (must be >= 1)",
                    self.task_id, config["retention_days"],
                )
        if "purge_watch_events" in config:
            self.purge_watch_events = bool(config["purge_watch_events"])
        if "purge_pipeline_rule_pairs" in config:
            self.purge_pipeline_rule_pairs = bool(config["purge_pipeline_rule_pairs"])
        if "purge_run_on_refresh_skipped" in config:
            self.purge_run_on_refresh_skipped = bool(
                config["purge_run_on_refresh_skipped"]
            )
        if "purge_task_start_complete" in config:
            self.purge_task_start_complete = bool(
                config["purge_task_start_complete"]
            )

    async def execute(self) -> TaskResult:
        """Execute the noise purge."""
        started_at = datetime.utcnow()
        self._set_progress(
            total=1,
            current=0,
            status="purging",
            current_item="Purging automated-noise journal entries",
        )

        try:
            counts = journal.purge_noise_entries(
                days=self.retention_days,
                purge_watch_events=self.purge_watch_events,
                purge_pipeline_rule_pairs=self.purge_pipeline_rule_pairs,
                purge_run_on_refresh_skipped=self.purge_run_on_refresh_skipped,
                purge_task_start_complete=self.purge_task_start_complete,
            )
        except Exception as e:
            logger.exception("[%s] Noise purge failed: %s", self.task_id, e)
            return TaskResult(
                success=False,
                message=f"Journal noise purge failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        total_deleted = sum(counts.values())
        self._set_progress(
            current=1,
            success_count=total_deleted,
            failed_count=0,
            status="completed",
        )
        logger.info(
            "[%s] Purged %s watch event, %s pipeline rule create/delete, "
            "%s run-on-refresh-skipped, and %s task start/complete journal "
            "entries older than %s days",
            self.task_id,
            counts["watch_events"],
            counts["pipeline_rule_pairs"],
            counts["run_on_refresh_skipped"],
            counts["task_start_complete"],
            self.retention_days,
        )
        return TaskResult(
            success=True,
            message=(
                f"Purged {counts['watch_events']} watch event, "
                f"{counts['pipeline_rule_pairs']} Channel Pipeline rule "
                f"create/delete, {counts['run_on_refresh_skipped']} "
                f"run-on-refresh-skipped, and "
                f"{counts['task_start_complete']} task start/complete "
                f"journal entries older than {self.retention_days} days."
            ),
            started_at=started_at,
            completed_at=datetime.utcnow(),
            total_items=total_deleted,
            success_count=total_deleted,
            failed_count=0,
            details={
                "deleted": counts,
                "retention_days": self.retention_days,
            },
        )
