"""
Popularity Calculation Task.

Scheduled task to calculate channel popularity rankings based on watch history.
"""
import logging
from datetime import datetime
from typing import Optional

from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)


@register_task
class PopularityCalculationTask(TaskScheduler):
    """
    Task to calculate channel popularity rankings.

    Analyzes watch history data to score and rank channels by popularity.
    Can optionally evaluate popularity rules after calculation.

    Configuration options (stored in task config JSON):
    - period_days: Number of days of history to analyze (default: 7)
    - evaluate_rules: Whether to evaluate popularity rules after calculation (default: False)
    - rules_dry_run: If evaluating rules, whether to run in dry-run mode (default: False)
    """

    task_id = "popularity_calculation"
    task_name = "Popularity Calculation"
    task_description = "Calculate channel popularity rankings from watch history"

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        # Default to manual (user can enable daily/weekly schedule)
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.MANUAL,
            )
        super().__init__(schedule_config)

        # Task-specific config
        self.period_days: int = 7
        self.evaluate_rules: bool = False
        self.rules_dry_run: bool = False

    def get_config(self) -> dict:
        """Get task configuration."""
        return {
            "period_days": self.period_days,
            "evaluate_rules": self.evaluate_rules,
            "rules_dry_run": self.rules_dry_run,
        }

    def update_config(self, config: dict) -> None:
        """Update task configuration."""
        if "period_days" in config:
            self.period_days = config["period_days"]
        if "evaluate_rules" in config:
            self.evaluate_rules = config["evaluate_rules"]
        if "rules_dry_run" in config:
            self.rules_dry_run = config["rules_dry_run"]

    async def execute(self) -> TaskResult:
        """Execute the popularity calculation task."""
        started_at = datetime.utcnow()

        self._set_progress(
            total=1,
            current=0,
            status="calculating",
            current_item="Calculating popularity scores",
        )

        try:
            # Import here to avoid circular imports
            from popularity_calculator import calculate_popularity

            # Run the calculation
            result = calculate_popularity(
                period_days=self.period_days,
                evaluate_rules=self.evaluate_rules,
                rules_dry_run=self.rules_dry_run,
            )

            channels_scored = result.get("channels_scored", 0)
            channels_created = result.get("channels_created", 0)
            channels_updated = result.get("channels_updated", 0)

            self._set_progress(
                current=1,
                success_count=channels_scored,
                status="completed",
            )

            message = f"Calculated popularity for {channels_scored} channels ({channels_created} new, {channels_updated} updated)"

            # Include rules result if evaluated
            details = {
                "channels_scored": channels_scored,
                "channels_created": channels_created,
                "channels_updated": channels_updated,
                "period_days": self.period_days,
            }

            if self.evaluate_rules and "rules_result" in result:
                rules_result = result["rules_result"]
                rules_evaluated = rules_result.get("rules_evaluated", 0)
                actions_executed = rules_result.get("total_actions_executed", 0)
                message += f", evaluated {rules_evaluated} rules with {actions_executed} actions"
                details["rules_result"] = rules_result

            logger.info("[%s] %s", self.task_id, message)

            return TaskResult(
                success=True,
                message=message,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=channels_scored,
                success_count=channels_scored,
                failed_count=0,
                details=details,
            )

        except Exception as e:
            logger.exception("[%s] Popularity calculation failed: %s", self.task_id, e)
            return TaskResult(
                success=False,
                message=f"Popularity calculation failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
