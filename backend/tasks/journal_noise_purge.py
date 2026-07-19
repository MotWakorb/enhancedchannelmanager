"""
Journal Noise Purge Task (enhancedchannelmanager-uliyr).

PO policy decision 2026-07-17: auto-purge journal entries older than 3 DAYS
for exactly TWO automated-noise buckets:

- Watch start/stop events (``category="watch"``, written only by the
  bandwidth tracker with ``user_initiated=False``).
- Channel Pipeline rule create/delete pairs (``category="auto_creation"``,
  ``action_type`` "create"/"delete" — dominated by E2E test-rule churn like
  'E2E event_sync happy path'). PO follow-up decision 2026-07-19: only rows
  self-declared automated (``automated_client=True``, via the harness's
  ``X-ECM-Automated-Client`` header) and pre-marker legacy rows
  (``automated_client`` NULL) age out; operator-initiated rows
  (``automated_client=False``) are KEPT.

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
      (default: 3 — PO-decided; minimum 1).
    - purge_watch_events: Include the watch start/stop bucket (default: True).
    - purge_pipeline_rule_pairs: Include the Channel Pipeline rule
      create/delete bucket (default: True).
    """

    task_id = "journal_noise_purge"
    task_name = "Journal Noise Purge"
    task_description = (
        "Purge automated-noise journal entries (watch start/stop events and "
        "automated Channel Pipeline rule create/delete pairs) older than the "
        "retention window. Operator-initiated rule changes and all other "
        "journal categories are untouched."
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

    def get_config(self) -> dict:
        """Get journal noise purge configuration."""
        return {
            "retention_days": self.retention_days,
            "purge_watch_events": self.purge_watch_events,
            "purge_pipeline_rule_pairs": self.purge_pipeline_rule_pairs,
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

        total_deleted = counts["watch_events"] + counts["pipeline_rule_pairs"]
        self._set_progress(
            current=1,
            success_count=total_deleted,
            failed_count=0,
            status="completed",
        )
        logger.info(
            "[%s] Purged %s watch event and %s pipeline rule create/delete "
            "journal entries older than %s days",
            self.task_id,
            counts["watch_events"],
            counts["pipeline_rule_pairs"],
            self.retention_days,
        )
        return TaskResult(
            success=True,
            message=(
                f"Purged {counts['watch_events']} watch event and "
                f"{counts['pipeline_rule_pairs']} Channel Pipeline rule "
                f"create/delete journal entries older than "
                f"{self.retention_days} days."
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
