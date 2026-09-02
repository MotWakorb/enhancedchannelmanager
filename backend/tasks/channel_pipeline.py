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
from sqlalchemy import or_
from config import get_settings, save_settings
from dispatcharr_client import get_client
from log_throttle import should_log
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

    schedule_parameter_schema = {
        "description": "Run an exact selection of Channel Pipeline rules",
        "parameters": [{
            "name": "rule_ids",
            "type": "number_array",
            "label": "Rules",
            "description": (
                "The exact rules to run. Every selected rule must remain enabled, "
                "active, and valid; an empty or stale selection will not run."
            ),
            "required": True,
            "source": "auto_creation_rules",
        }],
    }

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
        self._invocation_schedule_id: Optional[int] = None

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

    @classmethod
    def validate_schedule_parameters(cls, parameters: Optional[dict]) -> None:
        """Require a complete, runnable rule selection before persistence."""
        # The code-seeded Default Schedule has no selected scope: it is the
        # legacy refresh-watermark cadence and must remain distinct from #873
        # selected-rule schedules. API creation enforces a selection separately.
        if parameters is None or parameters == {}:
            return
        if not isinstance(parameters, dict):
            raise ValueError("Channel Pipeline schedule parameters must be an object")
        rule_ids = parameters.get("rule_ids")
        if (
            not isinstance(rule_ids, list)
            or not rule_ids
            or len(rule_ids) > 500
            or any(
                not isinstance(rule_id, int)
                or isinstance(rule_id, bool)
                or rule_id <= 0
                for rule_id in rule_ids
            )
        ):
            raise ValueError("rule_ids must be a non-empty list of positive integers")
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("rule_ids must be unique")

        from selected_pipeline_rules import load_selected_rule_snapshots
        load_selected_rule_snapshots(rule_ids)

    def prepare_invocation_parameters(
        self,
        triggered_by: str,
        schedule_id: Optional[int],
        parameters: Optional[dict],
    ) -> Optional[dict]:
        self._invocation_schedule_id = (
            schedule_id
            if isinstance(parameters, dict) and "rule_ids" in parameters
            else None
        )
        return parameters

    def restore_invocation_config(self, config: dict) -> None:
        super().restore_invocation_config(config)
        self._invocation_schedule_id = None


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
        if self._invocation_schedule_id is not None:
            return await self._run_selected_schedule()

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

        # (c) At least one rule eligible for the unattended path. Two
        # DISJOINT rule sets ride it:
        #
        # * Standard rules: enabled AND run_on_refresh=True (Q2 — unchanged).
        #   The query still excludes every event_sync rule, even one with
        #   run_on_refresh set — that flag is not the event_sync opt-in.
        # * event_sync rules (ti939.3.1): enabled AND the config carries the
        #   EXPLICIT auto_run=true opt-in. The flag lives inside the JSON
        #   config column, so candidates are selected by SQL
        #   (event_sync_config IS NOT NULL) and the flag is read in Python —
        #   only the literal True opts in (absent == false, the
        #   backward-compat rail for stored configs). The engine's per-rule
        #   trigger gate (event_sync_trigger_allowed) is the second,
        #   independent layer, and the attach phase re-checks the breaker +
        #   pre-flight as the third.
        self._set_progress(status="loading_rules")
        session = get_session()
        try:
            today = datetime.utcnow().date()
            active_window = (
                or_(ChannelPipelineRule.active_from.is_(None),
                    ChannelPipelineRule.active_from <= today),
                or_(ChannelPipelineRule.active_until.is_(None),
                    ChannelPipelineRule.active_until >= today),
            )
            rules_to_run = session.query(ChannelPipelineRule).filter(
                ChannelPipelineRule.enabled == True,
                ChannelPipelineRule.run_on_refresh == True,
                ChannelPipelineRule.event_sync_config.is_(None),
                *active_window,
            ).all()
            event_sync_candidates = session.query(ChannelPipelineRule).filter(
                ChannelPipelineRule.enabled == True,
                ChannelPipelineRule.event_sync_config.isnot(None),
                *active_window,
            ).all()
            event_sync_to_run = [
                r for r in event_sync_candidates
                if (r.get_event_sync_config() or {}).get("auto_run") is True
            ]
            rule_ids = (
                [r.id for r in rules_to_run]
                + [r.id for r in event_sync_to_run]
            )
            rule_names = (
                [r.name for r in rules_to_run]
                + [r.name for r in event_sync_to_run]
            )
            # These extra queries exist only to explain an otherwise-empty
            # eligible set. Keep them off the happy path (and legacy mock
            # paths) where their results are never consumed.
            date_gated_standard = False
            date_gated_event_sync = False
            if not rule_ids:
                standard_candidate_count = session.query(ChannelPipelineRule).filter(
                    ChannelPipelineRule.enabled == True,
                    ChannelPipelineRule.run_on_refresh == True,
                    ChannelPipelineRule.event_sync_config.is_(None),
                ).count()
                date_gated_standard = (
                    isinstance(standard_candidate_count, int)
                    and standard_candidate_count > len(rules_to_run)
                )
                all_event_sync_candidates = session.query(ChannelPipelineRule).filter(
                    ChannelPipelineRule.enabled == True,
                    ChannelPipelineRule.event_sync_config.isnot(None),
                ).all()
                date_gated_event_sync = any(
                    (r.get_event_sync_config() or {}).get("auto_run") is True
                    for r in all_event_sync_candidates
                    if r not in event_sync_candidates
                )
        finally:
            session.close()

        if not rule_ids:
            date_gated = date_gated_standard or date_gated_event_sync
            # A refresh watermark IS pending (we passed the ``refresh_at >
            # consumed_at`` gate above) but no rule is eligible for the
            # unattended path, so matching will NOT run for this refresh.
            # Surface at INFO (throttled per task) so "why didn't matching run
            # after the refresh?" is answerable from the logs instead of being
            # DEBUG-only (vkktd.1). The throttled-off ticks keep the original
            # DEBUG so debug-level readers still see every tick.
            if date_gated and should_log(
                "date_gated_refresh:%s" % self.task_id
            ):
                logger.info(
                    "[%s] Refresh watermark %s is pending, but all enabled "
                    "run_on_refresh/auto_run rules are outside their active UTC "
                    "date windows — matching will resume when a rule is in-window",
                    self.task_id, refresh_at,
                )
            elif not date_gated and should_log(
                "no_run_on_refresh_rule:%s" % self.task_id
            ):
                logger.info(
                    "[%s] Refresh watermark %s is pending but NO enabled "
                    "run_on_refresh rule (or auto_run event_sync rule) exists — "
                    "matching will not run for this refresh (enable run_on_refresh "
                    "on a rule to have it fire)", self.task_id, refresh_at,
                )
            else:
                logger.debug(
                    "[%s] No enabled run_on_refresh or auto_run event_sync rules "
                    "— skipping auto-fire", self.task_id,
                )
            return TaskResult(
                success=True, message=(
                    "No run_on_refresh rules are active in their UTC date windows"
                    if date_gated else
                    "No auto-creation rules with run_on_refresh enabled"
                ),
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

    async def _run_selected_schedule(self) -> TaskResult:
        """Execute one stored schedule's exact rule scope without widening."""
        from channel_pipeline_engine import (
            get_channel_pipeline_engine,
            init_channel_pipeline_engine,
        )
        from selected_pipeline_rules import (
            SelectedRuleValidationError,
            load_selected_rule_snapshots,
        )

        started_at = datetime.utcnow()
        requested_ids = list(self.rule_ids)
        origin = {
            "scheduled_task_id": self.task_id,
            "schedule_id": self._invocation_schedule_id,
        }
        try:
            # Establish canonical order and fail before any external reads. The
            # engine repeats this at its worker boundary to preserve #874's
            # validation-to-worker race protection and detached snapshots.
            rules = load_selected_rule_snapshots(requested_ids)
            canonical_ids = [rule.id for rule in rules]
            engine = get_channel_pipeline_engine()
            if not engine:
                engine = await init_channel_pipeline_engine(get_client())
            result = await engine.run_pipeline(
                dry_run=False,
                triggered_by=(
                    "scheduled_selected"
                    if self._run_trigger == "scheduled"
                    else self._run_trigger
                ),
                m3u_account_ids=self.m3u_account_ids or None,
                rule_ids=canonical_ids,
                require_all_rule_ids=True,
                execution_origin=origin,
            )
        except SelectedRuleValidationError as error:
            reasons = [issue.get("reason", "stale") for issue in error.issues]
            if error.rule_ids:
                reasons.append("deleted")
            reason_text = ", ".join(dict.fromkeys(reasons)) or error.code
            return TaskResult(
                success=False,
                message=f"Selected Channel Pipeline rules are stale: {reason_text}",
                error=error.code,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                details={
                    **origin,
                    "selected_rule_ids": requested_ids,
                    "selection_issues": error.issues,
                    "missing_rule_ids": error.rule_ids,
                },
            )

        failed_count = result.get("failed_action_count", 0)
        return TaskResult(
            success=bool(result.get("success")),
            message=(
                f"Channel Pipeline schedule ran {len(canonical_ids)} selected rule(s)"
            ),
            error=None if result.get("success") else result.get("status", "pipeline_failed"),
            started_at=started_at,
            completed_at=datetime.utcnow(),
            total_items=result.get("streams_evaluated", 0),
            success_count=result.get("channels_created", 0) + result.get("channels_updated", 0),
            failed_count=failed_count,
            details={
                **origin,
                "selected_rule_ids": canonical_ids,
                "execution_id": result.get("execution_id"),
                "mode": "execute",
                "status": result.get("status"),
                "streams_evaluated": result.get("streams_evaluated", 0),
                "streams_matched": result.get("streams_matched", 0),
                "channels_created": result.get("channels_created", 0),
                "channels_updated": result.get("channels_updated", 0),
                "groups_created": result.get("groups_created", 0),
                "streams_merged": result.get("streams_merged", 0),
                "conflicts": len(result.get("conflicts", [])),
                "failed_action_count": failed_count,
            },
        )

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

            # ti939.3.1: unattended event_sync attach count (structured
            # per-rule summaries ride the run result).
            event_sync_attached = sum(
                s.get("attached", 0) for s in result.get("event_sync", [])
            )
            # ti939.3.2: ambiguous matches queued for operator review on
            # this unattended run — no operator was watching, so the count
            # must reach the notification surface.
            event_sync_review_queued = sum(
                s.get("review_enqueued", 0)
                for s in result.get("event_sync", [])
            )

            # y3m6o.1 (0152): an UNATTENDED run in which any executed action
            # FAILED must NOT notify green nor return a green success TaskResult
            # (the GH #720 hidden-failure class on the 3 AM path). The engine
            # finalizes such a run as ``completed_with_errors`` and reports
            # ``status`` + ``failed_action_count`` on the result (its top-level
            # ``success`` is ``not failed_actions``); the persisted
            # ``execution.status`` stays the honest ``completed_with_errors``.
            #
            # Per PO decision, the task-layer envelope reports COMPLETED WITH
            # WARNINGS (not a hard failure): we return ``TaskResult(success=True,
            # failed_count=N)`` below, which makes the task engine emit a single
            # "Task Completed with Warnings" warning — with external alerts,
            # gated on the task's ``alert_on_warning`` (default ON) — exactly the
            # established stream_probe partial-failure path. To keep it to ONE
            # coherent warning (no green success toast, no competing second
            # toast), the auto-creation-specific completion notification below is
            # SUPPRESSED on a failed-action run; the per-run failed-action
            # summary lives on the execution record (Execution History).
            failed_action_count = result.get("failed_action_count", 0) or len(
                result.get("failed_actions") or []
            )
            has_failed_actions = (
                result.get("status") == "completed_with_errors"
                or bool(failed_action_count)
            )

            # Notify: completed — clean / partial-info runs only. A failed-action
            # run defers its single warning to the task-engine layer (above).
            if not has_failed_actions:
                parts = []
                if created:
                    parts.append(f"{created} created")
                if updated:
                    parts.append(f"{updated} updated")
                if pending_merges:
                    parts.append(f"{pending_merges} pending merge{'s' if pending_merges != 1 else ''} queued")
                if event_sync_attached:
                    parts.append(
                        f"{event_sync_attached} event stream"
                        f"{'s' if event_sync_attached != 1 else ''} attached"
                    )
                if event_sync_review_queued:
                    parts.append(
                        f"{event_sync_review_queued} event match"
                        f"{'es' if event_sync_review_queued != 1 else ''} "
                        f"queued for review"
                    )
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

            # ti939.3.1: event_sync warnings from an unattended run (attach
            # cap overage, pre-flight failures) must notify — no operator is
            # watching the run surface at 3 AM. Best-effort like every other
            # notification here.
            try:
                await self._notify_event_sync_warnings(result)
            except Exception as e:  # pragma: no cover — notification best-effort
                logger.warning(
                    "[%s] Failed to emit event_sync warning notifications: %s",
                    self.task_id, e,
                )

            # bd-h2xnl: a capped run must NOT be silent — warn with the N-of-M.
            # y3m6o.1 review (Finding 2): a compound capped + failed-action run
            # must emit ONE coherent warning carrying BOTH the cap info and the
            # failed-action recovery guidance — not the cap warning here plus a
            # separate "Task Completed with Warnings" from the task engine. When
            # both conditions hold, this notification carries both and the
            # returned TaskResult suppresses the engine's generic warning.
            if result.get("capped"):
                would = created + result.get("cap_would_create", 0)
                cap_msg = (
                    f"Auto-creation capped at {created} of ~{would} would-create "
                    f"channels. It is idempotent — run auto-creation again to "
                    f"continue (created channels persist), or raise the cap in "
                    f"Settings > Auto Creation."
                )
                if has_failed_actions:
                    cap_msg += (
                        f" Additionally, {failed_action_count} action"
                        f"{'s' if failed_action_count != 1 else ''} failed this "
                        f"run — see the run's Execution History for details; "
                        f"rerunning the pipeline is safe (every action path is "
                        f"idempotent)."
                    )
                await create_notification_internal(
                    notification_type="warning",
                    title=(
                        "Auto-Creation: Capped, with errors"
                        if has_failed_actions else "Auto-Creation: Capped"
                    ),
                    message=cap_msg,
                    source="auto_creation",
                    source_id="capped",
                    send_alerts=True,
                )

            self._set_progress(
                status="completed_with_errors" if has_failed_actions else "completed",
                total=evaluated,
                success_count=created,
                failed_count=failed_action_count,
            )

            summary = (
                f"Auto-creation after M3U refresh: {evaluated} streams evaluated, "
                f"{matched} matched, {created} channels created, {updated} updated"
            )
            if has_failed_actions:
                summary += (
                    f"; {failed_action_count} action"
                    f"{'s' if failed_action_count != 1 else ''} failed"
                )

            return TaskResult(
                # y3m6o.1 (0152) / PO decision: a failed-action run is COMPLETED
                # WITH WARNINGS, not a hard failure. ``success`` stays True and
                # ``failed_count`` carries the action-failure count, so the task
                # engine emits ONE "Task Completed with Warnings" warning (with
                # alerts) rather than a red "Task Failed" error. The engine
                # pipeline result (``success=not failed_actions``) and the
                # persisted ``execution.status='completed_with_errors'`` remain
                # the honest record; only this task envelope is warnings-not-fail.
                success=True,
                message=summary,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=evaluated,
                success_count=created + updated,
                # Defensive: a completed_with_errors run ALWAYS reports at least
                # one failure so the task engine takes its warning branch, never
                # green. This decouples the task layer from the engine invariant
                # that status="completed_with_errors" implies failed_action_count
                # > 0 — if that coupling ever broke, a count of 0 must not let a
                # failed run report green (the exact class this bead kills).
                failed_count=max(failed_action_count, 1) if has_failed_actions else 0,
                # y3m6o.1 review (Finding 2): a compound capped + failed-action
                # run already emitted ONE combined warning above (cap info +
                # failed-action recovery guidance), so tell the task engine to
                # skip its generic "Task Completed with Warnings" — otherwise the
                # unattended path emits two separate warnings for one run.
                suppress_completion_notification=bool(
                    result.get("capped") and has_failed_actions
                ),
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
                    "status": result.get("status"),
                    "failed_action_count": failed_action_count,
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

    # Warning types an UNATTENDED event_sync run must never leave silent
    # (bead ti939.3.1): attach-cap overage and pre-flight failures. Other
    # event_sync warnings (invalid config, fetch failure) stay run-surface
    # only, same as manual runs.
    _EVENT_SYNC_NOTIFY_WARNING_TITLES = {
        "event_sync_attach_capped": "Event Sync: Attach cap reached",
        "event_sync_preflight_failed": "Event Sync: Pre-flight failed (rule skipped)",
    }

    async def _notify_event_sync_warnings(self, result: dict) -> None:
        """Turn persisted event_sync run warnings into notifications.

        The engine pops ``event_sync_warnings`` off the transient results
        before returning (they are persisted on the execution record so the
        API/UI is not double-fed), so they are read back here by execution
        id. One warning notification per cap-overage / pre-flight failure,
        with alerts ON — an unattended misconfiguration must surface via the
        notification channel rather than silence.
        """
        from database import get_session
        from models import ChannelPipelineExecution
        from services.notification_service import create_notification_internal

        execution_id = result.get("execution_id")
        if execution_id is None:
            return
        session = get_session()
        try:
            execution = session.query(ChannelPipelineExecution).filter(
                ChannelPipelineExecution.id == execution_id
            ).first()
            warnings = execution.get_warnings() if execution else []
        finally:
            session.close()

        for warning in warnings or []:
            title = self._EVENT_SYNC_NOTIFY_WARNING_TITLES.get(
                warning.get("type")
            )
            if not title:
                continue
            await create_notification_internal(
                notification_type="warning",
                title=title,
                message=warning.get("message", ""),
                source="auto_creation",
                source_id="event_sync_auto_run",
                send_alerts=True,
            )
