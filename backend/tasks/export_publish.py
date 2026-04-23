"""
Export Publish Task.

Scheduled task that evaluates cron-based publish configurations
and triggers the publish pipeline for matching ones.
"""
import logging
from datetime import datetime

from croniter import croniter

from database import get_session
from export_models import PublishConfiguration
from publish_pipeline import execute_publish
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)


class ExportPublishTask(TaskScheduler):
    task_id = "export_publish"
    task_name = "Export Publish"
    task_description = "Evaluates cron-based publish schedules and triggers export generation + cloud upload"
    default_enabled = False  # Opt-in: only users with publish configs need this

    def __init__(self, schedule_config: ScheduleConfig | None = None):
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.INTERVAL,
                interval_seconds=60,  # Check every minute
            )
        super().__init__(schedule_config)

    async def execute(self, **kwargs) -> TaskResult:
        started = datetime.utcnow()
        db = get_session()
        try:
            configs = db.query(PublishConfiguration).filter(
                PublishConfiguration.enabled == True,
                PublishConfiguration.schedule_type == "cron",
                PublishConfiguration.cron_expression.isnot(None),
            ).all()

            config_list = [(c.id, c.name, c.cron_expression) for c in configs]
        finally:
            db.close()

        if not config_list:
            return TaskResult(
                success=True, message="No cron publish configs",
                started_at=started, completed_at=datetime.utcnow(),
            )

        now = datetime.utcnow()
        triggered = 0
        errors = 0

        for config_id, name, cron_expr in config_list:
            try:
                cron = croniter(cron_expr, now)
                prev = cron.get_prev(datetime)
                # If the previous scheduled time is within the last 60 seconds, trigger
                diff = (now - prev).total_seconds()
                if diff < 60:
                    logger.info("[EXPORT-TASK] Triggering cron publish for config %s (%s)", config_id, name)
                    result = await execute_publish(config_id)
                    if result.success:
                        triggered += 1
                    else:
                        errors += 1
                        logger.warning("[EXPORT-TASK] Publish failed for config %s: %s", config_id, result.error)
            except Exception as e:
                errors += 1
                logger.warning("[EXPORT-TASK] Error evaluating config %s: %s", config_id, e)

        return TaskResult(
            success=errors == 0,
            message=f"Triggered {triggered}, errors {errors}",
            started_at=started,
            completed_at=datetime.utcnow(),
            total_items=len(config_list),
            success_count=triggered,
            failed_count=errors,
        )


register_task(ExportPublishTask)
