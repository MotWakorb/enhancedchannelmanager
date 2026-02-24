"""
Auto-Creation Pipeline Task.

Scheduled task to run the auto-creation pipeline, creating channels
from streams based on configured rules.
"""
import logging
from datetime import datetime
from typing import Optional

from dispatcharr_client import get_client
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)


@register_task
class AutoCreationTask(TaskScheduler):
    """
    Task to run the auto-creation pipeline.

    Creates channels automatically from streams based on configured rules.
    Can be run manually, on schedule, or triggered after M3U refresh.

    Configuration options (stored in task config JSON):
    - dry_run: Only preview changes without applying (default: False)
    - m3u_account_ids: List of M3U account IDs to process (empty = all)
    - rule_ids: List of specific rule IDs to run (empty = all enabled rules)
    - run_on_refresh: Whether to run after M3U refresh tasks (default: False)
    """

    task_id = "auto_creation"
    task_name = "Auto-Create Channels"
    task_description = "Automatically create channels from streams based on rules"

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        # Default to manual only (user triggers via API or after M3U refresh)
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.MANUAL,
            )
        super().__init__(schedule_config)

        # Task-specific config
        self.dry_run: bool = False
        self.m3u_account_ids: list[int] = []  # Empty = all accounts
        self.rule_ids: list[int] = []  # Empty = all enabled rules
        self.run_on_refresh: bool = False

    def get_config(self) -> dict:
        """Get auto-creation configuration."""
        return {
            "dry_run": self.dry_run,
            "m3u_account_ids": self.m3u_account_ids,
            "rule_ids": self.rule_ids,
            "run_on_refresh": self.run_on_refresh,
        }

    def update_config(self, config: dict) -> None:
        """Update auto-creation configuration."""
        logger.debug("[%s] Updating config: %s", self.task_id, config)
        if "dry_run" in config:
            self.dry_run = config["dry_run"]
        if "m3u_account_ids" in config:
            self.m3u_account_ids = config["m3u_account_ids"] or []
        if "rule_ids" in config:
            self.rule_ids = config["rule_ids"] or []
        if "run_on_refresh" in config:
            self.run_on_refresh = config["run_on_refresh"]

    async def execute(self) -> TaskResult:
        """Execute the auto-creation pipeline."""
        from auto_creation_engine import get_auto_creation_engine, init_auto_creation_engine

        started_at = datetime.utcnow()
        self._set_progress(status="initializing")
        logger.info(
            "[%s] Starting auto-creation task: dry_run=%s, m3u_accounts=%s, rules=%s, run_on_refresh=%s",
            self.task_id, self.dry_run, self.m3u_account_ids or "all",
            self.rule_ids or "all enabled", self.run_on_refresh
        )

        try:
            # Get or initialize the engine
            client = get_client()
            engine = get_auto_creation_engine()
            if not engine:
                logger.debug("[%s] No existing engine, initializing new one", self.task_id)
                engine = await init_auto_creation_engine(client)
            else:
                logger.debug("[%s] Using existing engine instance", self.task_id)

            self._set_progress(status="loading_rules")

            # Check if there are any enabled rules
            from database import get_session
            from models import AutoCreationRule

            session = get_session()
            try:
                rule_count = session.query(AutoCreationRule).filter(
                    AutoCreationRule.enabled == True
                ).count()
            finally:
                session.close()

            if rule_count == 0:
                logger.info("[%s] No enabled rules found, skipping pipeline", self.task_id)
                return TaskResult(
                    success=True,
                    message="No enabled auto-creation rules to process",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=0,
                )

            logger.info("[%s] Found %s enabled rule(s)", self.task_id, rule_count)

            self._set_progress(
                status="running_pipeline",
                current_item=f"Processing {rule_count} rules...",
            )

            # Run the pipeline
            result = await engine.run_pipeline(
                dry_run=self.dry_run,
                triggered_by="scheduled",
                m3u_account_ids=self.m3u_account_ids if self.m3u_account_ids else None,
                rule_ids=self.rule_ids if self.rule_ids else None,
            )

            if self._cancel_requested:
                logger.info("[%s] Pipeline cancelled by user", self.task_id)
                return TaskResult(
                    success=False,
                    message="Auto-creation cancelled",
                    error="CANCELLED",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                )

            # Build result
            mode_str = "Dry-run" if self.dry_run else "Executed"
            stats = result
            duration = (datetime.utcnow() - started_at).total_seconds()
            logger.info(
                "[%s] %s pipeline completed in %.1fs: evaluated=%s matched=%s created=%s updated=%s groups=%s conflicts=%s",
                self.task_id, mode_str, duration,
                stats.get("streams_evaluated", 0), stats.get("streams_matched", 0),
                stats.get("channels_created", 0), stats.get("channels_updated", 0),
                stats.get("groups_created", 0), len(stats.get("conflicts", []))
            )

            self._set_progress(
                status="completed",
                total=stats.get("streams_evaluated", 0),
                success_count=stats.get("channels_created", 0),
            )

            message_parts = [
                f"{mode_str} auto-creation pipeline:",
                f"{stats.get('streams_evaluated', 0)} streams evaluated",
                f"{stats.get('streams_matched', 0)} matched",
            ]

            if not self.dry_run:
                message_parts.extend([
                    f"{stats.get('channels_created', 0)} channels created",
                    f"{stats.get('channels_updated', 0)} updated",
                    f"{stats.get('groups_created', 0)} groups created",
                ])

            return TaskResult(
                success=True,
                message=", ".join(message_parts),
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=stats.get("streams_evaluated", 0),
                success_count=stats.get("channels_created", 0) + stats.get("channels_updated", 0),
                details={
                    "execution_id": stats.get("execution_id"),
                    "mode": "dry_run" if self.dry_run else "execute",
                    "streams_evaluated": stats.get("streams_evaluated", 0),
                    "streams_matched": stats.get("streams_matched", 0),
                    "channels_created": stats.get("channels_created", 0),
                    "channels_updated": stats.get("channels_updated", 0),
                    "groups_created": stats.get("groups_created", 0),
                    "streams_merged": stats.get("streams_merged", 0),
                    "conflicts": len(stats.get("conflicts", [])),
                },
            )

        except Exception as e:
            logger.exception("[%s] Auto-creation pipeline failed: %s", self.task_id, e)
            return TaskResult(
                success=False,
                message=f"Auto-creation failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )


async def run_auto_creation_after_refresh(
    m3u_account_ids: list[int] = None,
    triggered_by: str = "m3u_refresh"
) -> dict:
    """
    Run auto-creation rules that have run_on_refresh=True.

    Called after M3U refresh completes to automatically create channels
    for newly discovered streams.

    Args:
        m3u_account_ids: Optional list of M3U account IDs that were refreshed
        triggered_by: How this was triggered

    Returns:
        Dict with execution results
    """
    from database import get_session
    from models import AutoCreationRule
    from auto_creation_engine import get_auto_creation_engine, init_auto_creation_engine
    from dispatcharr_client import get_client

    # Check if any rules have run_on_refresh enabled
    session = get_session()
    try:
        rules_to_run = session.query(AutoCreationRule).filter(
            AutoCreationRule.enabled == True,
            AutoCreationRule.run_on_refresh == True
        ).all()

        if not rules_to_run:
            logger.debug("[AUTO-CREATION] No rules with run_on_refresh=True")
            return {"success": True, "message": "No auto-creation rules to run on refresh"}

        rule_ids = [r.id for r in rules_to_run]
        rule_names = [r.name for r in rules_to_run]
        logger.info("[AUTO-CREATION] Running %s rules after M3U refresh", len(rule_ids))

    finally:
        session.close()

    from services.notification_service import create_notification_internal

    # Notify: starting
    await create_notification_internal(
        notification_type="info",
        title="Auto-Creation: Starting",
        message=f"Running {len(rule_ids)} rule{'s' if len(rule_ids) != 1 else ''} after M3U refresh: {', '.join(rule_names)}",
        source="auto_creation",
        source_id="m3u_refresh",
        send_alerts=False,
    )

    # Get or initialize engine
    client = get_client()
    engine = get_auto_creation_engine()
    if not engine:
        logger.debug("[AUTO-CREATION] No existing engine for post-refresh, initializing new one")
        engine = await init_auto_creation_engine(client)

    logger.debug(
        "[AUTO-CREATION] Running post-refresh pipeline: rule_ids=%s, m3u_accounts=%s, triggered_by=%s",
        rule_ids, m3u_account_ids, triggered_by
    )

    # Run the pipeline with only the run_on_refresh rules
    try:
        result = await engine.run_pipeline(
            dry_run=False,
            triggered_by=triggered_by,
            m3u_account_ids=m3u_account_ids,
            rule_ids=rule_ids,
        )

        created = result.get("channels_created", 0)
        updated = result.get("channels_updated", 0)
        matched = result.get("streams_matched", 0)
        evaluated = result.get("streams_evaluated", 0)

        logger.info(
            "[AUTO-CREATION] Post-refresh pipeline: %s channels created, %s updated",
            created, updated
        )

        # Notify: completed
        parts = []
        if created:
            parts.append(f"{created} created")
        if updated:
            parts.append(f"{updated} updated")
        title = f"Auto-Creation: {', '.join(parts)}" if parts else "Auto-Creation: No changes"
        ntype = "success" if parts else "info"

        await create_notification_internal(
            notification_type=ntype,
            title=title,
            message=f"Ran {len(rule_ids)} rules after M3U refresh. "
                    f"{matched}/{evaluated} streams matched.",
            source="auto_creation",
            source_id="m3u_refresh",
            send_alerts=False,
        )

        return result

    except Exception as e:
        logger.exception("[AUTO-CREATION] Post-refresh pipeline failed: %s", e)

        # Notify: failed
        await create_notification_internal(
            notification_type="error",
            title="Auto-Creation: Failed",
            message=f"Auto-creation after M3U refresh failed: {e}",
            source="auto_creation",
            source_id="m3u_refresh",
            send_alerts=False,
        )

        return {"success": False, "error": str(e)}
