"""
Auto-Creation Pipeline Task.

Scheduled task to run the auto-creation pipeline, creating channels
from streams based on configured rules.
"""
import logging
import os
from datetime import datetime
from typing import Optional

import journal
from config import get_settings, save_settings
from dispatcharr_client import get_client
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)

# ADR-011 (bd-ka7j9): the ChannelPipelineTask self-fires on an INTERVAL schedule
# at the same cadence the engine ticks (DEFAULT_CHECK_INTERVAL == 60s). Imported
# lazily inside __init__ to avoid a module-import cycle with task_engine (which
# pulls in task_registry, which imports the task modules).


def _run_on_refresh_suppressed() -> tuple[bool, str]:
    """bd-exo4j: decide whether the post-refresh auto-fire chain is suppressed.

    Two independent suppressors, checked at run time (the breaker scenario is a
    container restart, so the state must be read fresh, never cached):

    - ``ECM_DISABLE_RUN_ON_REFRESH`` env var (break-glass): honored regardless
      of the persisted flag so an operator can stop the chain before the app
      even reads settings.
    - ``auto_creation_run_on_refresh_disabled`` persisted setting (THE
      breaker): set True by the startup crash-sentinel when it abandons a run
      left 'running' by an OOM SIGKILL. NEVER auto-reset — cleared only by the
      operator via POST /api/auto-creation/reset-circuit-breaker.

    Returns ``(suppressed, reason)``. ``reason`` is a short machine-stable tag
    used in the notification/journal text.
    """
    env_val = (os.environ.get("ECM_DISABLE_RUN_ON_REFRESH") or "").strip().lower()
    if env_val in ("1", "true", "yes", "on"):
        return True, "break_glass_env"
    try:
        if get_settings().auto_creation_run_on_refresh_disabled:
            return True, "circuit_breaker"
    except Exception as e:  # pragma: no cover — settings must never block the gate
        logger.warning("[AUTO-CREATION] Failed to read circuit-breaker flag: %s", e)
    return False, ""


@register_task
class ChannelPipelineTask(TaskScheduler):
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

    # enhancedchannelmanager-i2xad (production incident): scheduled auto-creation
    # is OPT-IN — disabled by default. ADR-011 Phase 2 (bd-ka7j9) seeded this
    # task ENABLED by default (and a startup migration flipped already-installed
    # instances enabled), so auto-creation began firing autonomously on every
    # instance after upgrade — unwanted. The INTERVAL/60s schedule + AUTO-FIRE
    # GUARD architecture is preserved; only the default-enabled flag changed.
    # ``default_enabled = False`` seeds the PARENT ``scheduled_tasks`` row
    # disabled (the master switch); the child 60s cadence schedule stays enabled,
    # so an operator opts in with the single task "Enabled" toggle in the UI
    # (which persists). See ADR-011 "Rollout amendment" and
    # _migrate_disable_auto_creation_schedule.
    default_enabled = False

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        # ADR-011 (bd-ka7j9): default to an INTERVAL schedule (~60s, the engine's
        # check cadence) so the task ticks regularly. The AUTO-FIRE GUARD at the
        # top of execute() then decides whether the tick actually runs the
        # post-refresh pipeline (refresh watermark newer than consumed, breaker
        # clear, >=1 enabled run_on_refresh rule). A ~60s trigger latency after a
        # refresh is accepted (Q3). Previously MANUAL — auto-creation was driven
        # as a side-effect of the M3U refresh task (the GH #473 coupling).
        if schedule_config is None:
            from task_engine import DEFAULT_CHECK_INTERVAL
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.INTERVAL,
                interval_seconds=DEFAULT_CHECK_INTERVAL,
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
        """Run the post-refresh auto-creation pipeline, behind the AUTO-FIRE GUARD.

        ADR-011 (bd-ka7j9): this is now the SINGLE auto-creation entry point for
        the unattended path. It ticks on an INTERVAL schedule (~60s); the guard
        below decides whether the tick actually runs anything. The manual
        pipeline ``POST /api/auto-creation/run`` path goes straight to
        ``engine.run_pipeline`` and is deliberately NOT gated here.

        AUTO-FIRE GUARD — the pipeline runs only when ALL of these hold:
          (a) the task is enabled (the engine already filters disabled tasks,
              re-checked here so a direct execute() call is also safe);
          (b) ``_run_on_refresh_suppressed()`` is False — the exo4j breaker is
              clear AND ``ECM_DISABLE_RUN_ON_REFRESH`` is unset (read FRESH every
              tick: the breaker scenario is a restart, so a cached value is wrong);
          (c) at least one ``enabled AND run_on_refresh=True`` rule exists (Q2 —
              only that rule set ever runs on this path);
          (d) the refresh watermark is newer than the consumed watermark
              (``last_m3u_refresh_completed_at > last_auto_creation_consumed_refresh_at``)
              — i.e. an M3U refresh has completed since we last consumed one.

        When it runs, it advances ``last_auto_creation_consumed_refresh_at`` to
        the consumed refresh value BEFORE running the pipeline (so an overlapping
        tick — already guarded by the engine's "already running" check — and a
        crash mid-run both leave the watermark consumed, preventing a re-fire
        loop against the same refresh).
        """
        from channel_pipeline_engine import get_channel_pipeline_engine, init_channel_pipeline_engine
        from database import get_session
        from models import ChannelPipelineRule

        started_at = datetime.utcnow()
        self._set_progress(status="initializing")

        # (a) Enabled. The engine already skips disabled tasks; re-check so a
        # direct execute() invocation honors the same contract.
        if not self._enabled:
            logger.debug("[%s] Task disabled — skipping auto-fire", self.task_id)
            return TaskResult(
                success=True, message="Auto-creation task disabled",
                started_at=started_at, completed_at=datetime.utcnow(), total_items=0,
            )

        # (b) Breaker / break-glass — read FRESH (never cached).
        suppressed, reason = _run_on_refresh_suppressed()
        if suppressed:
            return await self._handle_suppressed(reason, started_at)

        # Read the watermarks FRESH alongside the breaker (same settings read).
        settings = get_settings()
        refresh_at = getattr(settings, "last_m3u_refresh_completed_at", "") or ""
        consumed_at = getattr(settings, "last_auto_creation_consumed_refresh_at", "") or ""

        # (d) A new refresh must have completed since we last consumed one.
        # Empty refresh watermark == "never refreshed" -> nothing to do. Empty
        # consumed watermark with a real refresh watermark == first-ever refresh
        # -> fire once. String compare is correct for ISO-8601 UTC timestamps.
        if not refresh_at or not (refresh_at > consumed_at):
            logger.debug(
                "[%s] No new M3U refresh to consume (refresh=%s consumed=%s) — skipping",
                self.task_id, refresh_at or "never", consumed_at or "never",
            )
            return TaskResult(
                success=True, message="No new M3U refresh to process",
                started_at=started_at, completed_at=datetime.utcnow(), total_items=0,
            )

        # (c) At least one enabled run_on_refresh rule. Only this rule set runs.
        # event_sync rules are EXPLICITLY excluded (ti939.2.1): they are
        # manual-run-only in this phase and must NEVER fire from this
        # unattended watermark path, even if an operator sets run_on_refresh
        # on one. The engine's triggered_by gate ("m3u_refresh" is not an
        # allowed event_sync trigger) is the second, independent layer.
        self._set_progress(status="loading_rules")
        session = get_session()
        try:
            rules_to_run = session.query(ChannelPipelineRule).filter(
                ChannelPipelineRule.enabled == True,
                ChannelPipelineRule.run_on_refresh == True,
                ChannelPipelineRule.event_sync_config.is_(None),
            ).all()
            rule_ids = [r.id for r in rules_to_run]
            rule_names = [r.name for r in rules_to_run]
        finally:
            session.close()

        if not rule_ids:
            logger.debug("[%s] No enabled run_on_refresh rules — skipping auto-fire", self.task_id)
            return TaskResult(
                success=True, message="No auto-creation rules with run_on_refresh enabled",
                started_at=started_at, completed_at=datetime.utcnow(), total_items=0,
            )

        # All guard conditions hold — CONSUME the watermark BEFORE running, so a
        # crash or an overlapping tick cannot re-fire against the same refresh.
        try:
            settings.last_auto_creation_consumed_refresh_at = refresh_at
            save_settings(settings)
        except Exception as e:  # pragma: no cover — best-effort, must not block the run
            logger.warning("[%s] Failed to advance consumed-refresh watermark: %s", self.task_id, e)

        logger.info(
            "[%s] Auto-firing %s run_on_refresh rule(s) for refresh watermark %s",
            self.task_id, len(rule_ids), refresh_at,
        )
        return await self._run_post_refresh_pipeline(rule_ids, rule_names, started_at)

    async def _handle_suppressed(self, reason: str, started_at: datetime) -> TaskResult:
        """Breaker / break-glass suppression path (migrated from the old
        run_auto_creation_after_refresh). Notifies + journals, then no-ops."""
        from services.notification_service import create_notification_internal

        logger.warning(
            "[%s] run_on_refresh SUPPRESSED (%s) — skipping auto-fire. "
            "Operator must clear the circuit breaker to re-enable.",
            self.task_id, reason,
        )
        if reason == "circuit_breaker":
            msg = (
                "Auto-creation after M3U refresh is DISABLED because a previous run "
                "was abandoned (likely an out-of-memory crash). Review your rules, "
                "then re-enable via POST /api/auto-creation/reset-circuit-breaker."
            )
        else:
            msg = (
                "Auto-creation after M3U refresh is suppressed by the "
                "ECM_DISABLE_RUN_ON_REFRESH environment break-glass switch."
            )
        try:
            await create_notification_internal(
                notification_type="warning",
                title="Auto-Creation: Skipped (run-on-refresh disabled)",
                message=msg,
                source="auto_creation",
                source_id="circuit_breaker",
                send_alerts=True,
            )
        except Exception as e:  # pragma: no cover — notification best-effort
            logger.warning("[%s] Failed to emit suppression notification: %s", self.task_id, e)
        try:
            journal.log_entry(
                category="auto_creation",
                action_type="run_on_refresh_skipped",
                entity_name="Auto-Creation",
                description=msg,
                user_initiated=False,
            )
        except Exception as e:  # pragma: no cover — journal best-effort
            logger.warning("[%s] Failed to journal suppression: %s", self.task_id, e)
        return TaskResult(
            success=True,
            message="Auto-creation run-on-refresh suppressed",
            started_at=started_at,
            completed_at=datetime.utcnow(),
            total_items=0,
            details={"skipped": True, "reason": reason},
        )

    async def _run_post_refresh_pipeline(
        self, rule_ids: list, rule_names: list, started_at: datetime
    ) -> TaskResult:
        """Run the run_on_refresh rule set and emit the start/completion/cap
        notifications (migrated from the old run_auto_creation_after_refresh so
        there is ONE notification style and ONE created-channel cap path)."""
        from channel_pipeline_engine import get_channel_pipeline_engine, init_channel_pipeline_engine
        from services.notification_service import create_notification_internal

        self._set_progress(
            status="running_pipeline",
            current_item=f"Processing {len(rule_ids)} rule(s)...",
        )

        # Notify: starting
        await create_notification_internal(
            notification_type="info",
            title="Auto-Creation: Starting",
            message=f"Running {len(rule_ids)} rule{'s' if len(rule_ids) != 1 else ''} "
                    f"after M3U refresh: {', '.join(rule_names)}",
            source="auto_creation",
            source_id="m3u_refresh",
            send_alerts=False,
        )

        client = get_client()
        engine = get_channel_pipeline_engine()
        if not engine:
            logger.debug("[%s] No existing engine for post-refresh, initializing new one", self.task_id)
            engine = await init_channel_pipeline_engine(client)

        try:
            result = await engine.run_pipeline(
                dry_run=False,
                triggered_by="m3u_refresh",
                m3u_account_ids=self.m3u_account_ids if self.m3u_account_ids else None,
                rule_ids=rule_ids,
            )

            if self._cancel_requested:
                logger.info("[%s] Pipeline cancelled by user", self.task_id)
                return TaskResult(
                    success=False, message="Auto-creation cancelled", error="CANCELLED",
                    started_at=started_at, completed_at=datetime.utcnow(),
                )

            created = result.get("channels_created", 0)
            updated = result.get("channels_updated", 0)
            matched = result.get("streams_matched", 0)
            evaluated = result.get("streams_evaluated", 0)
            # BD-F (bd-a5lb2): per-refresh count of pending_merges rows enqueued
            # by the bulk-M3U dedup hook (ADR-008 §D1), surfaced in the toast.
            pending_merges = result.get("pending_merges_added", 0)

            duration = (datetime.utcnow() - started_at).total_seconds()
            logger.info(
                "[%s] Post-refresh pipeline completed in %.1fs: %s created, %s updated, "
                "%s pending merges queued (%s/%s matched)",
                self.task_id, duration, created, updated, pending_merges, matched, evaluated,
            )

            # Notify: completed
            parts = []
            if created:
                parts.append(f"{created} created")
            if updated:
                parts.append(f"{updated} updated")
            if pending_merges:
                parts.append(f"{pending_merges} pending merge{'s' if pending_merges != 1 else ''} queued")
            title = f"Auto-Creation: {', '.join(parts)}" if parts else "Auto-Creation: No changes"
            ntype = "success" if parts else "info"

            await create_notification_internal(
                notification_type=ntype,
                title=title,
                message=f"Ran {len(rule_ids)} rule(s) after M3U refresh. "
                        f"{matched}/{evaluated} streams matched.",
                source="auto_creation",
                source_id="m3u_refresh",
                send_alerts=False,
            )

            # bd-h2xnl: a capped run must NOT be silent — warn with the N-of-M.
            if result.get("capped"):
                would = created + result.get("cap_would_create", 0)
                await create_notification_internal(
                    notification_type="warning",
                    title="Auto-Creation: Capped",
                    message=(
                        f"Auto-creation capped at {created} of ~{would} would-create "
                        f"channels. It is idempotent — run auto-creation again to "
                        f"continue (created channels persist), or raise the cap in "
                        f"Settings > Auto Creation."
                    ),
                    source="auto_creation",
                    source_id="capped",
                    send_alerts=True,
                )

            self._set_progress(
                status="completed",
                total=evaluated,
                success_count=created,
            )

            return TaskResult(
                success=True,
                message=f"Auto-creation after M3U refresh: {evaluated} streams evaluated, "
                        f"{matched} matched, {created} channels created, {updated} updated",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=evaluated,
                success_count=created + updated,
                details={
                    "execution_id": result.get("execution_id"),
                    "mode": "execute",
                    "triggered_by": "m3u_refresh",
                    "streams_evaluated": evaluated,
                    "streams_matched": matched,
                    "channels_created": created,
                    "channels_updated": updated,
                    "groups_created": result.get("groups_created", 0),
                    "streams_merged": result.get("streams_merged", 0),
                    "pending_merges_added": pending_merges,
                    "conflicts": len(result.get("conflicts", [])),
                    "capped": bool(result.get("capped")),
                },
            )

        except Exception as e:
            logger.exception("[%s] Post-refresh pipeline failed: %s", self.task_id, e)
            await create_notification_internal(
                notification_type="error",
                title="Auto-Creation: Failed",
                message=f"Auto-creation after M3U refresh failed: {e}",
                source="auto_creation",
                source_id="m3u_refresh",
                send_alerts=False,
            )
            return TaskResult(
                success=False,
                message=f"Auto-creation failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
