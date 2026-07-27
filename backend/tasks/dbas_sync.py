"""Scheduled + manual cross-instance SyncTask (bead ``enhancedchannelmanager-5gzg5``).

Epic ``i39wu``. Architecture: [ADR-013](../../docs/adr/
ADR-013-cross-instance-live-sync.md) S6 (trigger / overlap guard) and S8
(operational posture). The sync ENGINE (``tasks.dbas_sync_engine.run_sync``) is
built; this module is the THIN ``TaskScheduler`` wrapper that makes it
OPERATOR-TRIGGERABLE — schedulable on an interval AND manually force-triggerable.

What this is
------------
A :class:`~task_scheduler.TaskScheduler` subclass whose ``execute()``:

1. resolves the configured ``SyncTarget`` (``sync_target_id``),
2. runs the FIRE-TIME credential-freshness gate (capture ``credential_version``
   at enqueue; re-check FRESH at execute — abort+WARN+journal+notification on a
   stale/revoked/disabled target, NEVER calling :func:`run_sync`), then
3. calls :func:`tasks.dbas_sync_engine.run_sync` (dry-run default; source-wins
   apply when ``confirm_apply=True``) and maps the returned
   :class:`~dbas.restore_contracts.RestoreReport` to a TRI-STATE ``TaskResult``.

The freshness gate here mirrors ``DbasBackupTask._check_credential_freshness``.
The engine's :func:`run_sync` ALSO re-runs the same gate internally (defence in
depth) when handed a ``session``; this task's pre-call gate is the layer that
guarantees the bead's contract: a stale target ABORTS without a remote client
ever being built and without :func:`run_sync` being invoked.

Source snapshot posture (bead / ADR-013 S6)
-------------------------------------------
The source set is NOT frozen at enqueue: :func:`run_sync` reads the LOCAL source
config at EXECUTE time. That is the correct converge-over-cycles posture — each
cycle pushes the source's then-current state, so the system converges across runs
rather than replaying a stale snapshot. Only the credential-freshness inputs
(``credential_version``) are captured-at-enqueue + re-checked-at-execute.

task_id / overlap (ADR-013 S6 + SRE spike ``xp6mp``) — DELIBERATE v1 SIMPLIFICATION
----------------------------------------------------------------------------------
ADR-013 S6 PREFERS one ``task_id`` per ``SyncTarget`` so distinct targets run
concurrently while the engine's ``ALREADY_RUNNING`` guard (``task_engine.py``,
keyed on ``task_id``) excludes a second run of the SAME target. Implementing
per-target DYNAMIC task registration is out of scope for v1.

v1 implements a SINGLE registered ``task_id="dbas_sync"`` PARAMETERIZED by
``sync_target_id`` (exactly how ``DbasBackupTask`` handles its targets). The
consequence: concurrent runs of DIFFERENT targets SERIALIZE under the shared
``task_id`` (the second is rejected ``ALREADY_RUNNING`` until the first finishes).
This is SAFE (each cycle is idempotent — re-run-to-convergence; ADR-013 S8) and
SLOWER, never incorrect. Per-target concurrency is a small, isolated follow-up
(dynamic registration) — NOT a re-architecture.

Metric: this bead uses the EXISTING task success/failure metric path (the
task-engine run history + notifications). The dedicated
``ecm_sync_runs_total{result}`` metric is a separate bead (``k78ja``).

Trigger: MANUAL by default (the operator opts into an interval schedule). Manual
force-sync uses the generic ``POST /api/tasks/dbas_sync/run`` endpoint with
``parameters={sync_target_id, confirm_apply}`` — no bespoke endpoint. ``dbas_sync``
is in ``routers.tasks.PRIVILEGED_TASK_IDS`` (admin-gated outbound-write op).

Conventions (``docs/style_guide.md``): ``snake_case``; Google-style docstrings;
lazy ``%``-formatted logging; no secrets in any log/journal/notification field.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import NamedTuple, Optional

import journal
import observability
from services.notification_service import create_notification_internal
from task_registry import register_task
from task_scheduler import ScheduleConfig, ScheduleType, TaskResult, TaskScheduler

from tasks.dbas_sync_engine import run_sync

logger = logging.getLogger(__name__)


def _bump_sync_metric(result: str) -> None:
    """Increment ecm_sync_runs_total for a result label, best-effort.

    Mirrors :func:`tasks.dbas_backup._bump_metric`. ``result`` is the bounded
    tri-state {success, partial, failed}; a 'success' increment is the run that
    ALSO stamps ecm_task_schedule_last_success_timestamp (via the task_engine),
    so a sustained absence of 'success' is what the ECMSyncStalledTargetDrift
    staleness alert detects.
    """
    try:
        observability.get_metric("sync_runs_total").labels(result=result).inc()
    except Exception as e:  # pragma: no cover — metrics best-effort
        logger.warning("[DBAS_SYNC] Failed to increment sync_runs_total: %s", e)


class SyncCounts(NamedTuple):
    """Per-run item counts summed from ``RestoreReport.categories``.

    Named fields instead of a bare tuple so ``_counts_from_report``'s callers
    use attribute access (``counts.failed_count``) rather than positional
    unpacking — a future reorder of this tuple's construction vs. a
    destructuring assignment at the call site would otherwise silently swap
    fields (e.g. failed shown as skipped) with no type-checker catch. Mirrors
    the house pattern in ``bandwidth_tracker.py``'s ``ProviderResolution``:
    zero runtime overhead vs. a plain tuple (``typing.NamedTuple`` is a tuple
    subclass), field access is callsite documentation.

    * ``total_items`` — success_count + skipped_count + failed_count.
    * ``success_count`` — dry-run: would_create + would_update; apply:
      created + updated.
    * ``failed_count`` — sum of ``category.failed`` on BOTH dry-run and apply.
      A dry-run plan cannot FAIL an apply attempt, but ``cat.failed`` also
      carries per-item CONFLICTs the sync engine surfaces unconditionally (a
      source-side duplicate name, an ambiguous null-channel-number collision)
      — those are facts about the source data, populated on a dry-run preview
      too, so this bucket is identical in both branches.
    * ``skipped_count`` — dry-run: would_skip; apply: skipped.
    """

    total_items: int
    success_count: int
    failed_count: int
    skipped_count: int


@register_task
class DbasSyncTask(TaskScheduler):
    """Run one cross-instance config sync cycle (A -> B) on a schedule or on demand.

    Configuration options (stored in task config JSON via update_config — the
    /run endpoint passes them as ad-hoc run parameters, and a schedule persists
    them in ``task_schedules.parameters``):

    - ``sync_target_id``: int — which :class:`~export_models.SyncTarget` to sync
      to. Required; an absent id is a hard failure (nothing to sync).
    - ``confirm_apply``: bool — ``False`` (default) is a counts-only DRY-RUN
      preview (zero writes to B); ``True`` APPLIES source-wins (A overwrites B).
    - ``cloud_credential_version``: Optional[int] — the target's
      ``credential_version`` captured when the schedule was configured. Re-checked
      FRESH against the DB at fire time; a mismatch aborts the run.
    """

    task_id = "dbas_sync"
    task_name = "Cross-Instance Sync"
    task_description = (
        "One-way push of this instance's config (and channels) to a remote "
        "Dispatcharr-B SyncTarget. Dry-run preview by default; apply is opt-in. "
        "Scheduled (operator opts into an interval) or manual."
    )
    default_enabled = False
    # Per-invocation state, not durable settings: this task runs off
    # schedule/run parameters (sync_target_id, confirm_apply — see module
    # docstring), and confirm_apply arms a DESTRUCTIVE source-wins apply.
    # The fail-safe direction on restart is disarmed, so the registry must
    # neither persist nor rehydrate this surface (gjb01).
    persist_config = False

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        if schedule_config is None:
            schedule_config = ScheduleConfig(schedule_type=ScheduleType.MANUAL)
        super().__init__(schedule_config)

        self.sync_target_id: Optional[int] = None
        # Dry-run preview is the safe default; apply (source-wins) is opt-in.
        self.confirm_apply: bool = False
        # The SyncTarget.credential_version captured when the schedule was
        # configured (task-config JSON, NO new DB column — mirrors
        # DbasBackupTask.cloud_credential_version). Re-checked FRESH at fire time.
        self.cloud_credential_version: Optional[int] = None

    def get_config(self) -> dict:
        return {
            "sync_target_id": self.sync_target_id,
            "confirm_apply": self.confirm_apply,
            "cloud_credential_version": self.cloud_credential_version,
        }

    def update_config(self, config: dict) -> None:
        if "sync_target_id" in config:
            val = config["sync_target_id"]
            self.sync_target_id = int(val) if val is not None else None
        if "confirm_apply" in config:
            self.confirm_apply = bool(config["confirm_apply"])
        if "cloud_credential_version" in config:
            val = config["cloud_credential_version"]
            self.cloud_credential_version = int(val) if val is not None else None

    async def execute(self) -> TaskResult:
        started_at = datetime.now(timezone.utc)
        self._set_progress(
            total=1, current=0, status="starting",
            current_item="Preparing cross-instance sync...",
        )

        if self.sync_target_id is None:
            return self._fail(started_at, "No sync_target_id configured for sync")

        # Open ONE session for the lifetime of the run: the fire-time freshness
        # gate re-reads the target through it, and run_sync re-uses it for its
        # own (defence-in-depth) freshness re-read. The caller owns its lifecycle.
        from database import get_session

        session = get_session()
        try:
            # --- Fire-time credential-freshness gate (ADR-013 S7, mirror
            #     DbasBackupTask). A stale/revoked/disabled target ABORTS the run
            #     WITHOUT building a remote client or calling run_sync. ----------
            target, skip = self._check_credential_freshness(session, started_at)
            if skip is not None:
                await self._emit_abort(skip.message)
                return skip

            self._set_progress(
                current_item="Syncing to '%s'..." % (
                    getattr(target, "name", None) or self.sync_target_id
                ),
                status="running",
            )

            report = await run_sync(
                target,
                confirm_apply=self.confirm_apply,
                session=session,
                captured_version=self.cloud_credential_version,
            )
        except Exception as e:  # noqa: BLE001 - any sync error is a sanitized failure
            logger.exception("[DBAS_SYNC] Sync run failed: %s", e)
            return self._fail(started_at, "Sync failed during execution", error=str(e))
        finally:
            session.close()

        return self._result_from_report(started_at, report)

    # ------------------------------------------------------------------
    # Fire-time credential-freshness gate (mirror DbasBackupTask).
    # ------------------------------------------------------------------
    def _check_credential_freshness(
        self, session, started_at: datetime
    ) -> tuple[Optional[object], Optional[TaskResult]]:
        """Re-read the bound SyncTarget FRESH and decide whether to proceed.

        Reuses :func:`tasks.dbas_sync_client.sync_freshness_reason` (the same
        check ``run_sync`` runs internally) so the abort criteria are defined in
        exactly one place. Returns ``(target, None)`` to proceed, or
        ``(None, skip_result)`` to abort. The abort ``TaskResult`` is non-silent:
        the caller emits the WARN/journal/notification via :meth:`_emit_abort`.
        """
        from export_models import SyncTarget
        from tasks.dbas_sync_client import sync_freshness_reason

        reason = sync_freshness_reason(
            session, self.sync_target_id, self.cloud_credential_version
        )
        if reason is not None:
            return None, self._abort_skip(started_at, reason)

        target = (
            session.query(SyncTarget)
            .filter(SyncTarget.id == self.sync_target_id)
            .first()
        )
        # sync_freshness_reason already proved the target exists + is usable, so
        # `target` is non-None here; the query just re-materializes the row to
        # hand to run_sync (single source of the freshness verdict above).
        return target, None

    def _abort_skip(self, started_at: datetime, reason: str) -> TaskResult:
        """Build the non-silent SKIP TaskResult for a freshness-gate abort.

        A scheduled sync that silently stops = false safety, so the run records a
        sanitized, operator-facing reason; the WARN/journal/notification side
        effects are emitted by :meth:`_emit_abort` (kept off this builder so the
        builder stays pure/testable)."""
        message = (
            "Cross-instance sync skipped — %s. No sync was performed. Review the "
            "sync target configuration or update the sync schedule." % reason
        )
        # A freshness-gate abort is a non-clean terminal run — count it as
        # 'failed' (the target drifted because no sync was applied). The
        # WARN/journal/notification side effects are emitted by _emit_abort.
        _bump_sync_metric("failed")
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

    async def _emit_abort(self, message: str) -> None:
        """Emit the NON-SILENT side effects of a freshness-gate abort:
        WARN log + journal (``sync_outbound``) + NotificationCenter (best-effort)."""
        logger.warning("[DBAS_SYNC] %s", message)
        try:
            journal.log_entry(
                category="sync_outbound",
                action_type="scheduled_sync_skipped",
                entity_name="Cross-Instance Sync",
                entity_id=self.sync_target_id,
                description=message,
                user_initiated=False,
            )
        except Exception as e:  # pragma: no cover — journal best-effort
            logger.warning("[DBAS_SYNC] Failed to journal skip: %s", e)
        try:
            await create_notification_internal(
                notification_type="warning",
                title="Cross-Instance Sync: Skipped",
                message=message,
                source="task_dbas_sync",
                source_id="credential_freshness",
                send_alerts=True,
            )
        except Exception as e:  # pragma: no cover — notification best-effort
            logger.warning("[DBAS_SYNC] Failed to emit skip notification: %s", e)

    # ------------------------------------------------------------------
    # Report -> tri-state TaskResult (mirror DbasRestoreTask result shaping).
    # ------------------------------------------------------------------
    def _result_from_report(self, started_at: datetime, report) -> TaskResult:
        """Map a :class:`RestoreReport` to a TRI-STATE TaskResult.

        - DRY-RUN (``is_dry_run=True``, no realized outcome) -> ``success=True``
          (a preview that produced a plan succeeded).
        - APPLY with a clean ``SUCCESS`` outcome              -> ``success=True``.
        - APPLY with a mixed/rolled-back outcome              -> ``success=False``
          (partial — tri-state discipline: NEVER success on mixed state).
        """
        from dbas.restore_contracts import RestoreOutcome

        is_dry_run = bool(report.is_dry_run)
        outcome = report.outcome.value if report.outcome else "dry_run"
        succeeded = is_dry_run or report.outcome == RestoreOutcome.SUCCESS

        self._set_progress(
            current=1, total=1,
            status="completed" if succeeded else "failed",
        )

        # Tri-state metric (mirror ecm_backup_runs_total): a clean
        # success/dry-run is 'success'; a mixed/rolled-back apply is 'partial'
        # (target B drifting — never reported as a clean success). Only the
        # 'success' increment coincides with the task_engine stamping
        # ecm_task_schedule_last_success_timestamp.
        _bump_sync_metric("success" if succeeded else "partial")

        message = self._summary_message(report, is_dry_run, outcome)
        logger.info(
            "[DBAS_SYNC] Sync task complete (mode=%s, outcome=%s, %d categories)",
            "dry-run" if is_dry_run else "apply", outcome, len(report.categories),
        )
        # NOTE: task_engine.py's generic "if result.failed_count > 0" warning
        # branch (~line 1014) now depends on an invariant enforced upstream in
        # dbas/restore_orchestrator.py's compute_outcome(): outcome is NEVER
        # SUCCESS while any category.failed > 0 (compute_outcome re-scans every
        # category via _report_has_failures at outcome-decision time). So
        # succeeded=True (dry-run, or apply with outcome==SUCCESS) implies
        # counts.failed_count == 0 here by construction, not by convention.
        counts = self._counts_from_report(report, is_dry_run)
        return TaskResult(
            success=succeeded,
            message=message,
            error=None if succeeded else "SYNC_%s" % outcome.upper(),
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            total_items=counts.total_items,
            success_count=counts.success_count,
            failed_count=counts.failed_count,
            skipped_count=counts.skipped_count,
            details={
                "is_dry_run": is_dry_run,
                "outcome": outcome,
                "sync_report": report.model_dump(mode="json"),
            },
        )

    @staticmethod
    def _counts_from_report(report, is_dry_run: bool) -> "SyncCounts":
        """Sum the REAL per-category item counts across ``report.categories``.

        The task-level ``TaskResult`` badges (Task History UI) previously
        hardcoded ``total_items=1`` / ``success_count=1`` — "the whole run
        counted as one unit" — which reads as "1 of 1" even when a sync
        dry-run/apply touches dozens of channels/groups/profiles. This mirrors
        :meth:`_summary_message`'s sums (same underlying counts, so the
        human-readable message and the numeric badges never disagree).

        - **Dry-run**: success = would_create + would_update (items that WOULD
          change), skipped = would_skip. ``EntityCategoryReport`` has no
          ``would_fail`` field, but ``failed`` (the apply-flavour field) is NOT
          exclusively an apply-time signal here: the sync engine's per-item
          name-conflict tolerance (``dbas_sync_engine._split_name_conflicts`` /
          ``_apply_name_conflict_details``) and the channels importer's
          ambiguous-null-key collision (``dbas/importers/channels.py``) both
          populate ``cat.failed`` UNCONDITIONALLY — including on a dry-run
          preview — because a conflict is a fact about the source data, not
          about whether the run applied. So failed = sum of ``category.failed``
          on BOTH branches.
        - **Apply**: success = created + updated, skipped = skipped,
          failed = failed (same sum as dry-run for that one bucket).

        Returns a :class:`SyncCounts` (named fields, not a positional tuple —
        see its docstring for why).
        """
        failed_count = sum(c.failed for c in report.categories)
        if is_dry_run:
            success_count = sum(
                c.would_create + c.would_update for c in report.categories
            )
            skipped_count = sum(c.would_skip for c in report.categories)
        else:
            success_count = sum(c.created + c.updated for c in report.categories)
            skipped_count = sum(c.skipped for c in report.categories)
        total_items = success_count + skipped_count + failed_count
        return SyncCounts(
            total_items=total_items,
            success_count=success_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
        )

    @staticmethod
    def _summary_message(report, is_dry_run: bool, outcome: str) -> str:
        if is_dry_run:
            total_create = sum(c.would_create for c in report.categories)
            total_update = sum(c.would_update for c in report.categories)
            total_skip = sum(c.would_skip for c in report.categories)
            # `failed` is populated on a dry-run preview by the per-item
            # conflict paths (source-side duplicate name, ambiguous
            # null-channel-number collision) — surface it here too so this
            # message never disagrees with the numeric failed_count badge.
            total_conflict = sum(c.failed for c in report.categories)
            return (
                "Sync dry-run complete: would create %d, update %d, skip %d, "
                "%d conflict(s) across %d categories" % (
                    total_create, total_update, total_skip, total_conflict,
                    len(report.categories),
                )
            )
        total_created = sum(c.created for c in report.categories)
        total_updated = sum(c.updated for c in report.categories)
        total_failed = sum(c.failed for c in report.categories)
        return (
            "Sync %s: created %d, updated %d, failed %d across %d categories" % (
                outcome, total_created, total_updated, total_failed,
                len(report.categories),
            )
        )

    def _fail(
        self, started_at: datetime, message: str, *, error: Optional[str] = None
    ) -> TaskResult:
        """Build a failed TaskResult and mark progress failed (sanitized message)."""
        logger.warning("[DBAS_SYNC] %s", message)
        _bump_sync_metric("failed")
        self._set_progress(status="failed", current_item="finalize")
        return TaskResult(
            success=False,
            message=message,
            error=error or message,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            failed_count=1,
        )
