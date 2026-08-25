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

task_id / overlap (ADR-013 S6) — ONE task_id PER SyncTarget (bead ``7ipq2.3``)
------------------------------------------------------------------------------
ADR-013 S6: one ``task_id`` per ``SyncTarget`` so distinct targets run
concurrently while the engine's ``ALREADY_RUNNING`` guard (``task_engine.py``,
keyed on ``task_id``) excludes a second run of the SAME target. v1 (bead
``5gzg5``) shipped a single parameterized ``task_id="dbas_sync"`` — safe but
serializing (one slow/unreachable B starved every other target), and two
same-tick due schedules under the shared id silently SWALLOWED the second
target's run (the engine groups due schedules by task_id, runs the FIRST
schedule's parameters, and advances ``next_run_at`` for ALL of them).

Now each target owns a registered task ``dbas_sync_<target_id>`` (a dynamic
:func:`make_sync_task_class` subclass bound to that target):

* **Different targets run concurrently** — distinct task_ids, so the
  scheduler fires them as separate asyncio tasks and the manual /run endpoint
  accepts them independently.
* **Same target never runs twice concurrently** — the engine's per-task_id
  ``ALREADY_RUNNING`` guard IS the per-target lock (refusal is non-silent:
  an explicit failed TaskResult at the API, a retry-next-tick for schedules).
* **No cross-target parameter leakage, by construction** — each target id
  has its own registry singleton; ad-hoc parameters merge into THAT instance
  only. The 7ipq2.2 one-shot disarm still guards run-to-run leakage within a
  target (reset lands on the BOUND target id, see ``bound_sync_target_id``).
* **Bounded concurrency** — a module-level semaphore caps simultaneous sync
  runs across ALL targets (``ECM_SYNC_MAX_CONCURRENT``, default 3; excess
  runs queue, they are not dropped). The task engine's own global
  ``MAX_CONCURRENT_TASKS`` additionally bounds scheduled fires.

Lifecycle: :func:`register_sync_target_tasks` (startup, from ``main.py``)
registers every existing target, migrates legacy ``dbas_sync`` schedule rows
to their per-target id, and prunes rows for deleted targets;
:func:`ensure_sync_target_task` / :func:`remove_sync_target_task` are called
from the SyncTarget CRUD router. The base class is NOT statically registered.

Metric attribution — all three sync signals are keyed on the SAME target
identifier (the ``SyncTarget`` pk), so a responder can pivot between them:

* ``ecm_sync_runs_total{result, sync_target_id}`` — tri-state run outcome
  per target (the runbook's "unreachable vs half-applying" triage step).
* ``ecm_sync_last_full_success_timestamp{sync_target_id}`` — freshness of the
  last FULL APPLIED sync; the SLI ``ECMSyncStalledTargetDrift`` keys on.
  Deliberately NOT the generic per-task gauge: the task engine stamps that
  one on any success, and a dry-run PREVIEW succeeds without writing B, so a
  recurring preview would reset the drift clock (PR #752 review).
* ``ecm_task_schedule_last_success_timestamp{task_id="dbas_sync_<id>"}`` —
  generic task health ("did this task run at all"), previews included.

Trigger: MANUAL by default (the operator opts into an interval schedule).
Manual force-sync uses the generic ``POST /api/tasks/dbas_sync_<id>/run``
endpoint with ``parameters={confirm_apply}`` (``sync_target_id`` is optional
and must match the bound target when present). Per-target sync ids are
admin-gated via ``routers.tasks.is_privileged_task_id`` (outbound-write op).

Conventions (``docs/style_guide.md``): ``snake_case``; Google-style docstrings;
lazy ``%``-formatted logging; no secrets in any log/journal/notification field.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import NamedTuple, Optional, Type

import journal
import observability
from services.notification_service import create_notification_internal
from task_scheduler import ScheduleConfig, ScheduleType, TaskResult, TaskScheduler

from tasks.dbas_sync_engine import run_sync

logger = logging.getLogger(__name__)

# Legacy v1 shared task id (bead 5gzg5). No longer registered; kept as the
# migration source key for pre-7ipq2.3 ``scheduled_tasks``/``task_schedules``
# rows (see register_sync_target_tasks) and as the PRIVILEGED_TASK_IDS
# defence-in-depth entry in routers/tasks.py.
LEGACY_SYNC_TASK_ID = "dbas_sync"

# Per-target task ids: ``dbas_sync_<sync_target_id>`` (ADR-013 S6 — one
# task_id per SyncTarget). routers.tasks.is_privileged_task_id matches on
# this prefix, so every per-target id inherits the admin gate.
SYNC_TASK_ID_PREFIX = "dbas_sync_"


def sync_task_id_for(target_id: int) -> str:
    """The registered task id for one SyncTarget (``dbas_sync_<id>``)."""
    return "%s%d" % (SYNC_TASK_ID_PREFIX, target_id)


# ---------------------------------------------------------------------------
# Bounded sync concurrency (7ipq2.3): a small module-level cap across ALL
# sync targets. Distinct targets may run concurrently (that is the point of
# per-target task ids), but each in-flight run holds a remote Dispatcharr-B
# session + full-category reads, so an uncapped fan-out over many targets is
# an operator foot-gun. Excess runs QUEUE on the semaphore (bounded, never
# dropped); same-target exclusion is the engine's per-task_id guard, not this.
# Config-only: ECM_SYNC_MAX_CONCURRENT env var, default 3, floor 1.
# ---------------------------------------------------------------------------

_SYNC_MAX_CONCURRENT_ENV = "ECM_SYNC_MAX_CONCURRENT"
_SYNC_MAX_CONCURRENT_DEFAULT = 3

_sync_semaphore: Optional[asyncio.Semaphore] = None
_sync_semaphore_loop: Optional[asyncio.AbstractEventLoop] = None


def _sync_max_concurrent() -> int:
    """Read the sync concurrency cap from the environment (validated).

    Invalid or out-of-range values (non-integer, < 1) fall back to the safe
    default (3) with a WARN — a zero/negative cap would deadlock every run.
    """
    raw = os.environ.get(_SYNC_MAX_CONCURRENT_ENV)
    if raw is None:
        return _SYNC_MAX_CONCURRENT_DEFAULT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "[DBAS_SYNC] Invalid %s=%r — using default %d",
            _SYNC_MAX_CONCURRENT_ENV, raw, _SYNC_MAX_CONCURRENT_DEFAULT,
        )
        return _SYNC_MAX_CONCURRENT_DEFAULT
    if value < 1:
        logger.warning(
            "[DBAS_SYNC] %s=%d is below the floor of 1 — using default %d",
            _SYNC_MAX_CONCURRENT_ENV, value, _SYNC_MAX_CONCURRENT_DEFAULT,
        )
        return _SYNC_MAX_CONCURRENT_DEFAULT
    return value


def _get_sync_semaphore() -> asyncio.Semaphore:
    """Lazily create the shared cap semaphore, re-created per event loop.

    Production has one long-lived loop, so this is created once. The
    loop-identity check exists for test harnesses (a fresh loop per test):
    an asyncio primitive bound to a closed previous loop raises on use.
    """
    global _sync_semaphore, _sync_semaphore_loop
    loop = asyncio.get_running_loop()
    if _sync_semaphore is None or _sync_semaphore_loop is not loop:
        _sync_semaphore = asyncio.Semaphore(_sync_max_concurrent())
        _sync_semaphore_loop = loop
    return _sync_semaphore


def reset_sync_concurrency_for_tests() -> None:
    """Drop the cached semaphore so the next run re-reads the cap env var."""
    global _sync_semaphore, _sync_semaphore_loop
    _sync_semaphore = None
    _sync_semaphore_loop = None


def _bump_sync_metric(result: str, sync_target_id: Optional[int]) -> None:
    """Increment ecm_sync_runs_total for a result label, best-effort.

    Mirrors :func:`tasks.dbas_backup._bump_metric`. ``result`` is the bounded
    tri-state {success, partial, failed}.

    ``sync_target_id`` attributes the run to ONE target. Without it the
    counter was an aggregate across every target, so the runbook's triage
    step ("is this 'B unreachable' or 'applies but half-fails'?") could not
    say WHICH replica was broken once targets began running concurrently —
    while the freshness gauge was already per-target, leaving the two signals
    disagreeing in granularity.

    The label key is the SyncTarget row **pk**, not its name: the pk is
    immutable, so a rename cannot fork the series and break the continuity
    that ``rate()``/``increase()`` depend on. It is also the key the
    freshness gauge uses and the one the per-target task id is derived from
    — one identifier for a target across all three surfaces.

    ``None`` (only reachable on the unbound base class, where no target was
    ever selected) renders as the literal ``"unknown"`` rather than dropping
    the increment: losing a failure signal entirely is strictly worse than
    parking it on a clearly-named catch-all series.
    """
    try:
        observability.get_metric("sync_runs_total").labels(
            result=result,
            sync_target_id=str(sync_target_id) if sync_target_id is not None
            else "unknown",
        ).inc()
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


class DbasSyncTask(TaskScheduler):
    """Run one cross-instance config sync cycle (A -> B) on a schedule or on demand.

    NOT statically registered (7ipq2.3 / ADR-013 S6): each ``SyncTarget`` gets
    its own registered subclass via :func:`make_sync_task_class`, bound through
    ``bound_sync_target_id`` so the engine's per-task_id ``ALREADY_RUNNING``
    guard is the per-target lock. This base class carries all behavior and is
    instantiated directly only by tests.

    Configuration options (per-invocation run parameters — the /run endpoint
    passes them ad hoc, and a schedule persists them in
    ``task_schedules.parameters``):

    - ``sync_target_id``: Optional[int] — on a BOUND subclass this is implied
      by the task identity; when present it must MATCH the bound target (a
      mismatch is a hard, non-silent failure — silently syncing another target
      under this task id would run it outside its own lock and misattribute
      the run history). On the unbound base class it selects the target and
      is required.
    - ``confirm_apply``: bool — ``False`` (default) is a counts-only DRY-RUN
      preview (zero writes to B); ``True`` APPLIES source-wins (A overwrites B).
    - ``cloud_credential_version``: Optional[int] — the target's
      ``credential_version`` captured when the schedule was configured. Re-checked
      FRESH against the DB at fire time; a mismatch aborts the run.
    """

    task_id = LEGACY_SYNC_TASK_ID
    task_name = "Cross-Instance Sync"
    # The SyncTarget this task class is bound to (None on the unbound base).
    # Set by make_sync_task_class; the one-shot disarm resets sync_target_id
    # back to this value so a bare re-run still syncs ITS OWN target while
    # never replaying confirm_apply / a captured credential version.
    bound_sync_target_id: Optional[int] = None
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

        # A bound subclass arms its own target by construction — a schedule
        # needs no sync_target_id parameter at all (7ipq2.3).
        self.sync_target_id: Optional[int] = self.bound_sync_target_id
        # Dry-run preview is the safe default; apply (source-wins) is opt-in.
        self.confirm_apply: bool = False
        # The SyncTarget.credential_version captured when the schedule was
        # configured (task-config JSON, NO new DB column — mirrors
        # DbasBackupTask.cloud_credential_version). Re-checked FRESH at fire time.
        self.cloud_credential_version: Optional[int] = None
        # A sync_target_id parameter that CONFLICTED with the bound target.
        # Recorded (not applied) by update_config; execute() fails fast on it.
        self._bound_target_conflict: Optional[int] = None

    def get_config(self) -> dict:
        return {
            "sync_target_id": self.sync_target_id,
            "confirm_apply": self.confirm_apply,
            "cloud_credential_version": self.cloud_credential_version,
        }

    def update_config(self, config: dict) -> None:
        if "sync_target_id" in config:
            val = config["sync_target_id"]
            requested = int(val) if val is not None else None
            if (
                self.bound_sync_target_id is not None
                and requested is not None
                and requested != self.bound_sync_target_id
            ):
                # NEVER retarget a bound task: running target X under target
                # Y's task id would bypass X's own ALREADY_RUNNING lock (two
                # concurrent runs against the same B via two ids) and
                # misattribute run history/journal/gauge. Recorded here,
                # failed non-silently in execute() — update_config must not
                # raise (the engine logs-and-continues on parameter errors,
                # which WOULD silently run the bound target instead).
                self._bound_target_conflict = requested
            elif requested is not None:
                self.sync_target_id = requested
            elif self.bound_sync_target_id is None:
                # Explicit null on the unbound base clears the selection.
                self.sync_target_id = None
        if "confirm_apply" in config:
            self.confirm_apply = bool(config["confirm_apply"])
        if "cloud_credential_version" in config:
            val = config["cloud_credential_version"]
            self.cloud_credential_version = int(val) if val is not None else None

    async def execute(self) -> TaskResult:
        # Bounded sync-wide concurrency (see module docstring): queue politely
        # when the cap is reached — the engine's per-task_id guard has already
        # excluded a same-target overlap before we get here.
        async with _get_sync_semaphore():
            try:
                if self._bound_target_conflict is not None:
                    return self._fail(
                        datetime.now(timezone.utc),
                        "Sync refused: run parameters requested sync_target_id=%d "
                        "but this task is bound to sync target %d (task %s). "
                        "Use that target's own sync task instead." % (
                            self._bound_target_conflict,
                            self.bound_sync_target_id,
                            self.task_id,
                        ),
                        error="BOUND_TARGET_MISMATCH",
                    )
                return await self._execute_once()
            finally:
                # ONE-SHOT ARMING (live-validation finding, bead 7ipq2.2): the
                # task engine merges ad-hoc /run parameters into this PER-TARGET
                # singleton (update_config) and never restores them — and a bare
                # re-run (parameters absent/empty) skips update_config entirely,
                # running the instance as-is. Without this disarm, a prior run's
                # state leaked forward: a stale captured cloud_credential_version
                # aborted an unrelated later run (observed live), and a retained
                # confirm_apply=True would silently turn a later intended dry-run
                # into a source-wins APPLY. Every run must bring its own full
                # parameters (schedules always do); the fail-safe resting state
                # is disarmed — mirrors the persist_config=False rationale
                # (gjb01). On a BOUND task the reset lands on the bound target
                # id (7ipq2.3): the task keeps syncing ITS OWN target, while the
                # destructive/staleness-prone knobs always reset.
                self.sync_target_id = self.bound_sync_target_id
                self.confirm_apply = False
                self.cloud_credential_version = None
                self._bound_target_conflict = None

    async def _execute_once(self) -> TaskResult:
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
        _bump_sync_metric("failed", self.sync_target_id)
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
        - EITHER mode carrying ``destination_unreadable``     -> ``success=False``
          (bead ``…-jqfxm``), whatever the counts say.

        That last rule is why the dry-run branch above is not simply "a preview
        always succeeded". A preview's counts are claims ABOUT the destination —
        "would create 24" means "B does not have these 24" — and a run that
        could not read B produces exactly the shape of a run against an EMPTY B,
        because every importer degrades a failed read to ``existing = []``. Live
        validation measured that against a wrong password: ``outcome=success,
        would create 24, failed 0`` while B logged seven 401/429s, and the
        Settings card duly offered Apply (it gates on ``result.success``). A
        preview that never read the destination is a failed preview.

        ON A REALIZED APPLY THE MARKER RULE IS NOW REDUNDANT HERE, and
        deliberately kept: ``compute_outcome`` folds it into the OUTCOME (bead
        ``…-bj442`` — :func:`dbas.restore_orchestrator.outcome_for_unread_destination`),
        so the apply arrives already carrying ``FAILED_ROLLBACK_INCOMPLETE`` and
        the clause below cannot be what makes it fail. What the clause still
        covers on its own is the case an outcome cannot express: a DRY RUN, whose
        realized outcome is ``None`` by contract — both the preview whose
        destination read failed and the cycle the freshness / readback gate
        aborted before ``run_restore`` ever ran. That is why it stays a check on
        the marker rather than on the outcome.

        ``success=False`` is not one severity. A run that FINISHED and left real,
        kept state is DEGRADED, and is declared as such through
        ``completed_degraded`` so the task engine alerts it as a ``warning``
        rather than the red "Task Failed" (bead ``…-daziw``, PO decision
        2026-08-19). See :meth:`_degraded_not_failed`.
        """
        from dbas.restore_contracts import RestoreOutcome

        is_dry_run = bool(report.is_dry_run)
        outcome = report.outcome.value if report.outcome else "dry_run"
        unreadable = getattr(report, "destination_unreadable", None)
        succeeded = unreadable is None and (
            is_dry_run or report.outcome == RestoreOutcome.SUCCESS
        )

        self._set_progress(
            current=1, total=1,
            status="completed" if succeeded else "failed",
        )

        # Tri-state metric (mirror ecm_backup_runs_total): a clean
        # success/dry-run is 'success'; a mixed/rolled-back apply is 'partial'
        # (target B drifting — never reported as a clean success). Only the
        # 'success' increment coincides with the task_engine stamping
        # ecm_task_schedule_last_success_timestamp.
        _bump_sync_metric(
            "success" if succeeded else "partial", self.sync_target_id
        )

        # FULL-APPLY freshness (PR #752 review, Block 2). The task engine
        # stamps the GENERIC per-task success gauge on any success — and a
        # dry-run PREVIEW is a success (it produced a plan) — so that gauge
        # cannot answer "when was B last actually converged": a recurring
        # preview would reset the drift clock without ever writing B. This
        # dedicated gauge is stamped ONLY here, on an APPLY that returned a
        # clean SUCCESS, and it is what ECMSyncStalledTargetDrift keys on.
        if succeeded and not is_dry_run and self.sync_target_id is not None:
            observability.record_sync_full_success(self.sync_target_id)

        message = self._summary_message(report, is_dry_run, outcome)
        if unreadable is not None:
            # Replace the counts entirely rather than append to them: "would
            # create 24" is not a true sentence with a caveat attached, it is a
            # sentence about the SOURCE, and leaving it in the operator's line
            # is how a false green survives a fix.
            message = (
                "Cross-instance sync %s could not read the destination it "
                "describes — %s. No counts are reported because they would "
                "describe this instance, not the sync target." % (
                    "preview" if is_dry_run else "run", unreadable,
                )
            )
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
            completed_degraded=self._degraded_not_failed(report, is_dry_run),
            message=message,
            error=(
                None if succeeded
                else "SYNC_DESTINATION_UNREADABLE" if unreadable is not None
                else "SYNC_%s" % outcome.upper()
            ),
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
    def _degraded_not_failed(report, is_dry_run: bool) -> bool:
        """True when the sync RAN TO COMPLETION and rolled nothing back.

        Bead ``…-daziw``, PO decision 2026-08-19. Such a run is a WARNING, not a
        red "Task Failed": target B carries the applied state, and the summary
        names the shortfall. The task declares the state here; ``task_engine``
        maps state to alert severity
        (:attr:`task_scheduler.TaskResult.completed_degraded`).

        WHY SYNC SHARES THE RESTORE'S RULE INSTEAD OF DEFINING ITS OWN. Sync
        already shared the outcome downgrade — it runs the same orchestrator, so
        an apply that leaves a channel with no playable stream has ALWAYS come
        back ``COMPLETED_WITH_FAILURES`` here. What it did not share was the
        severity, because this method did not exist: the same degraded run
        alerted ``warning`` from a restore and ``error`` / "Task Failed" from a
        sync. A sync-specific definition of success was explicitly NOT chosen —
        it would recreate the bug in the path that runs unattended and
        repeatedly, which is where nobody is watching to catch it by hand.
        Alert VOLUME is handled at the alert layer instead: ``warning`` carries a
        per-task opt-out (``ScheduledTask.alert_on_warning``), so an operator
        with a known, accepted shortfall can silence the alert WITHOUT the code
        reporting success for an instance that cannot play a channel.

        The condition is the OUTCOME
        (:attr:`~dbas.restore_contracts.RestoreOutcome.is_degraded_not_failed`),
        never the particular shortfall — see that property for what keying on
        the shortfall instead cost the restore path (bead ``…-cwmid``).

        A DRY RUN is never degraded: it has no realized outcome, and a preview
        that PREDICTS a shortfall predicted it — nothing was applied to be
        unplayable. A run that failed before reaching the engine returns through
        :meth:`_fail` and never arrives here, so it keeps the error branch.

        NOR is a run that could not read the destination (bead ``…-jqfxm``).
        "Degraded" means the run finished and B carries real, kept state the
        operator can reason about. A run that never got an answer out of B knows
        neither what B carries nor what it applied — that is the error branch,
        not a warning an operator can opt out of. That rule USED TO BE A SECOND
        CONDITION READ HERE, and bead ``…-bj442`` removed it rather than leaving
        it beside the outcome: ``compute_outcome`` now resolves an unread
        destination to ``FAILED_ROLLBACK_INCOMPLETE``
        (:func:`dbas.restore_orchestrator.outcome_for_unread_destination`), which
        ``is_degraded_not_failed`` already answers ``False`` for. Keeping the
        condition here would have re-created exactly what ``…-cwmid`` measured
        and undid — a severity keyed on a condition rather than on the outcome —
        and the point of moving the decision was that ONE decision feeds every
        surface. Pinned by
        ``tests/tasks/test_bj442_unread_destination_outcome.py``
        ::``test_severity_is_still_read_off_the_outcome_alone``.
        """
        if is_dry_run or report.outcome is None:
            return False
        return report.outcome.is_degraded_not_failed

    @staticmethod
    def _account_field_convergence_suffix(report, *, is_preview: bool = False) -> str:
        """Name converged M3U account fields without exposing their values."""
        parts: list[str] = []
        for detail in getattr(report, "account_field_drift_details", None) or []:
            fields = sorted({str(field) for field in (detail.fields or []) if field})
            if not fields:
                continue
            if is_preview:
                action = "would converge"
            elif detail.applied:
                action = "converged"
            else:
                action = "could not converge"
            parts.append(
                "M3U account '%s' %s field(s): %s"
                % (detail.name, action, ", ".join(fields))
            )
        return ("; " + "; ".join(parts)) if parts else ""

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
            from tasks.dbas_restore import DbasRestoreTask

            return (
                "Sync dry-run complete: would create %d, update %d, skip %d, "
                "%d conflict(s) across %d categories" % (
                    total_create, total_update, total_skip, total_conflict,
                    len(report.categories),
                )
            ) + DbasRestoreTask._credential_reentry_suffix(
                report, is_preview=True
            ) + DbasSyncTask._account_field_convergence_suffix(
                report, is_preview=True
            )
        total_created = sum(c.created for c in report.categories)
        total_updated = sum(c.updated for c in report.categories)
        total_failed = sum(c.failed for c in report.categories)
        summary = (
            "Sync %s: created %d, updated %d, failed %d across %d categories" % (
                outcome, total_created, total_updated, total_failed,
                len(report.categories),
            )
        )
        # EVERY post-restore action item, not just the placeholder populations:
        # the counts above cannot express any of them, because every row
        # succeeded. A degraded sync would otherwise alert "failed 0" and name
        # nothing an operator can act on (…-daziw). Rendered by the RESTORE
        # task's builder rather than a second copy of it, so the two surfaces
        # cannot describe the same counters differently. Imported locally —
        # module scope would make the two task modules import-time circular.
        #
        # THIS USED TO CALL ``stream_reattach_phrases`` DIRECTLY, which is the
        # narrower of the two builders, and that is bead
        # ``enhancedchannelmanager-v7d37``. The restore path has named credential
        # re-entry, unreinstated logos, EPG-link losses and channel-group drift
        # since ``…-6pilh``/``…-dfkbn``; the sync path rendered only the
        # placeholder clause, so every OTHER shortfall fell off the one line an
        # unattended scheduled run produces. Measured on Dispatcharr 0.29.0: an
        # apply that stripped an Xtream Codes guide URL (the credentials are IN
        # the URL, so redaction takes the whole address) left 53 of 59 replica
        # channels with no EPG link and reported "Sync success: created 133,
        # updated 0, failed 0 across 9 categories" (re-measured live against the
        # doc environment on 2026-08-20) — while the same report
        # already carried ``epg_links_unrestored: 53`` and a
        # ``credential_reentry_details`` row naming the source and its ``url``.
        #
        # The OUTCOME is deliberately not touched here: whether a lost EPG link
        # should downgrade a run past SUCCESS is bead ``…-posm1``'s decision
        # (…-cwmid measured a severity inversion from keying that narrowly).
        # This makes the success QUALIFIED; posm1 decides whether it stays one.
        from tasks.dbas_restore import DbasRestoreTask

        return (
            summary
            + DbasRestoreTask._credential_reentry_suffix(report)
            + DbasSyncTask._account_field_convergence_suffix(report)
        )

    def _fail(
        self, started_at: datetime, message: str, *, error: Optional[str] = None
    ) -> TaskResult:
        """Build a failed TaskResult and mark progress failed (sanitized message)."""
        logger.warning("[DBAS_SYNC] %s", message)
        _bump_sync_metric("failed", self.sync_target_id)
        self._set_progress(status="failed", current_item="finalize")
        return TaskResult(
            success=False,
            message=message,
            error=error or message,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            failed_count=1,
        )


# ---------------------------------------------------------------------------
# Per-target task registration lifecycle (7ipq2.3 / ADR-013 S6).
# ---------------------------------------------------------------------------


def make_sync_task_class(target_id: int, target_name: str) -> Type[DbasSyncTask]:
    """Build the bound per-target task class for one ``SyncTarget``.

    A dynamic subclass, not an instance: the task registry stores CLASSES and
    instantiates its own singleton per task_id (one instance per target — the
    structural fix for cross-target parameter leakage). Everything except the
    identity/binding is inherited from :class:`DbasSyncTask`.
    """
    return type(
        "DbasSyncTargetTask%d" % target_id,
        (DbasSyncTask,),
        {
            "task_id": sync_task_id_for(target_id),
            "task_name": "Cross-Instance Sync: %s" % target_name,
            "task_description": (
                "One-way push of this instance's config (and channels) to the "
                "'%s' Dispatcharr-B sync target. Dry-run preview by default; "
                "apply is opt-in. Scheduled (operator opts into an interval) "
                "or manual." % target_name
            ),
            "bound_sync_target_id": target_id,
        },
    )


def ensure_sync_target_task(target_id: int, target_name: str) -> None:
    """Register (or refresh) the bound task for one target + persist its row.

    Called from the SyncTarget CRUD router on create AND update (a rename
    refreshes the display name under the same task id) and from the startup
    reconcile. Best-effort by contract: a registration failure must never fail
    the CRUD operation — it is logged loudly instead (the target simply has no
    schedulable task until the next startup reconcile).
    """
    from task_registry import get_registry

    task_id = sync_task_id_for(target_id)
    registry = get_registry()
    registry.register(make_sync_task_class(target_id, target_name))
    instance = registry.get_task_instance(task_id)
    if instance is not None:
        # The registry caches one instance per task_id; a rename must reach
        # the cached instance too (instance attributes shadow class attrs).
        instance.task_name = "Cross-Instance Sync: %s" % target_name
    # Persist/update the scheduled_tasks row so the task appears in the
    # Scheduled Tasks UI immediately (sync_from_database only runs at boot).
    _persist_sync_task_row(registry, task_id)


def _persist_sync_task_row(
    registry, task_id: str, *, seed_enabled: Optional[bool] = None
) -> bool:
    """Persist ONE per-target ``scheduled_tasks`` row without touching its gate.

    INVARIANT: **registration never changes the ``enabled`` state of an
    existing per-target parent row.** The row is the authority; registration
    only chooses the initial value for a row that does not exist yet.

    Why this has to be a property of registration rather than of any one
    caller: ``registry.sync_to_database`` writes ``instance._enabled`` straight
    over whatever row it finds (``task_registry._save_task_to_db``), and the
    per-target sync classes are created dynamically — so on EVERY startup the
    registry hands back a freshly constructed instance still carrying
    ``default_enabled = False``, with ``sync_from_database`` only hydrating it
    afterwards. Persisting before that hydration therefore switched an
    operator's ENABLED sync off on each ordinary restart, and the engine's
    parent gate (``task_engine._check_and_run_due_tasks``) then silently
    stopped the enabled child schedule from firing.

    Hydrating the instance from the row first makes the ``enabled`` write a
    no-op while the rest of the save (display name after a rename, schedule
    fields, run bookkeeping) still lands.

    ``seed_enabled`` is consulted ONLY when no row exists — that is where the
    legacy-migration's preserved firing state enters, as a seed for a row being
    created, never as an override of a row an operator already owns. ``None``
    means "keep whatever the instance already carries".

    Returns the gate state now persisted for ``task_id``.
    """
    from database import get_session
    from models import ScheduledTask

    instance = registry.get_task_instance(task_id)
    if instance is None:  # pragma: no cover — callers register first
        return False
    existing_enabled: Optional[bool] = None
    session = get_session()
    try:
        row = session.query(ScheduledTask).filter(
            ScheduledTask.task_id == task_id
        ).first()
        if row is not None:
            existing_enabled = bool(row.enabled)
    finally:
        session.close()

    if existing_enabled is not None:
        instance._enabled = existing_enabled
    elif seed_enabled is not None:
        instance._enabled = seed_enabled
    registry.sync_to_database(task_id)
    return bool(instance._enabled)


def remove_sync_target_task(target_id: int) -> None:
    """Unregister a deleted target's task and prune its DB rows.

    Deletes BOTH row kinds (``scheduled_tasks`` parent + ``task_schedules``
    children) — a surviving child schedule would trip the engine's
    "due-but-never-runnable" WARN every tick forever. An in-flight run is not
    interrupted (it completes under its own instance reference; idempotent
    per ADR-013 S8 and its freshness gate re-reads the now-deleted target on
    the next fire anyway — which can no longer happen, the schedule is gone).
    """
    from database import get_session
    from models import ScheduledTask, TaskSchedule
    from task_registry import get_registry

    task_id = sync_task_id_for(target_id)
    get_registry().unregister(task_id)
    session = get_session()
    try:
        session.query(TaskSchedule).filter(
            TaskSchedule.task_id == task_id
        ).delete(synchronize_session=False)
        session.query(ScheduledTask).filter(
            ScheduledTask.task_id == task_id
        ).delete(synchronize_session=False)
        session.commit()
        logger.info("[DBAS_SYNC] Removed sync task %s (target deleted)", task_id)
    except Exception as e:
        session.rollback()
        logger.warning("[DBAS_SYNC] Failed to prune rows for %s: %s", task_id, e)
    finally:
        session.close()


def register_sync_target_tasks() -> None:
    """Startup reconcile: one registered task per existing SyncTarget.

    Called from ``main.py`` after the task modules import and BEFORE the task
    engine starts (``sync_from_database`` then creates any missing
    ``scheduled_tasks`` rows for the freshly registered ids). Four concerns:

    1. **Register** a bound class for every ``sync_targets`` row.
    2. **Migrate legacy v1 rows** (single shared ``dbas_sync`` id, bead
       5gzg5): each ``task_schedules`` row keyed ``dbas_sync`` is re-keyed to
       the per-target id carried in its ``parameters.sync_target_id``; a row
       whose parameter is missing or points at a deleted target is DISABLED
       (non-silent WARN) rather than re-keyed to an id that can never run.
       The legacy ``scheduled_tasks`` parent row is deleted (its alert/
       notification preferences do NOT carry over to per-target rows).
    3. **Never move an existing per-target parent's enabled gate** (PR #752
       review, Block 1 + its delta). Firing needs BOTH the parent
       ``scheduled_tasks`` gate AND an enabled child row
       (``task_engine._check_and_run_due_tasks``), and the registry
       materialises every per-target instance from ``default_enabled = False``
       on EVERY boot — so persisting a fresh instance is what silently stops a
       working operator schedule. ``_persist_sync_task_row`` holds the
       invariant: an existing row is authoritative and registration only
       chooses the value for a row it creates. That covers both cases with one
       rule — the steady-state restart (row exists: enabled stays enabled,
       disabled stays disabled) and the upgrade (no row yet: seeded from the
       EFFECTIVE pre-upgrade state, i.e. legacy parent enabled — or absent,
       which never blocked — AND at least one migrated child enabled). The
       seed is preserve-only: it never starts a setup that was not firing (a
       disabled parent is the documented kill switch; a disabled child stayed
       off).
    4. **Prune stale per-target rows** for targets deleted while the
       container was down (the CRUD-hook path can't have seen them).

    Defensive: any failure here is logged and swallowed — sync registration
    must never break startup (the engine + every other task still run).
    """
    from database import get_session
    from export_models import SyncTarget
    from models import ScheduledTask, TaskSchedule
    from task_registry import get_registry

    registry = get_registry()
    try:
        session = get_session()
        try:
            targets = session.query(SyncTarget).all()
            valid_task_ids = set()
            for target in targets:
                registry.register(make_sync_task_class(target.id, target.name))
                valid_task_ids.add(sync_task_id_for(target.id))
            if targets:
                logger.info(
                    "[DBAS_SYNC] Registered %d per-target sync task(s)",
                    len(targets),
                )

            # --- Legacy v1 schedule migration (shared 'dbas_sync' id) -----
            legacy_parent = session.query(ScheduledTask).filter(
                ScheduledTask.task_id == LEGACY_SYNC_TASK_ID
            ).first()
            # An ABSENT legacy parent never blocked firing — the engine's gate
            # is ``if parent_task and not parent_task.enabled`` — so absence
            # maps to "not blocking", not to "disabled".
            legacy_parent_enabled = (
                bool(legacy_parent.enabled) if legacy_parent is not None else True
            )
            # Per-target task ids whose pre-upgrade state was actually FIRING.
            was_firing: set[str] = set()

            legacy_schedules = session.query(TaskSchedule).filter(
                TaskSchedule.task_id == LEGACY_SYNC_TASK_ID
            ).all()
            for sched in legacy_schedules:
                params = sched.get_parameters() or {}
                raw_target = params.get("sync_target_id")
                new_task_id = None
                if raw_target is not None:
                    try:
                        new_task_id = sync_task_id_for(int(raw_target))
                    except (TypeError, ValueError):
                        new_task_id = None
                if new_task_id in valid_task_ids:
                    sched.task_id = new_task_id
                    if legacy_parent_enabled and sched.enabled:
                        was_firing.add(new_task_id)
                    logger.info(
                        "[DBAS_SYNC] Migrated legacy sync schedule %s -> %s "
                        "(was_firing=%s)",
                        sched.id, new_task_id, new_task_id in was_firing,
                    )
                else:
                    sched.enabled = False
                    logger.warning(
                        "[DBAS_SYNC] Legacy sync schedule %s references a "
                        "missing/deleted sync target (%r) — disabled, not "
                        "migrated; delete it or recreate the target",
                        sched.id, raw_target,
                    )
            session.query(ScheduledTask).filter(
                ScheduledTask.task_id == LEGACY_SYNC_TASK_ID
            ).delete(synchronize_session=False)

            # --- Prune per-target rows for targets deleted while down -----
            for row in session.query(ScheduledTask).filter(
                ScheduledTask.task_id.like(SYNC_TASK_ID_PREFIX + "%")
            ).all():
                if row.task_id not in valid_task_ids:
                    session.query(TaskSchedule).filter(
                        TaskSchedule.task_id == row.task_id
                    ).delete(synchronize_session=False)
                    session.delete(row)
                    logger.info(
                        "[DBAS_SYNC] Pruned stale sync task row %s "
                        "(target no longer exists)", row.task_id,
                    )

            session.commit()
        finally:
            session.close()

        # --- Persist the per-target parent rows (after the migration txn
        #     commits, so the registry's own session sees the re-keyed
        #     children). Writing every registered target's row here — rather
        #     than leaving it to sync_from_database — is what lets the
        #     preserved enabled state land: sync_from_database only creates
        #     MISSING rows, and it creates them from default_enabled=False.
        #
        #     ``_persist_sync_task_row`` holds the invariant that makes this
        #     safe on EVERY boot, not just the upgrade one: an existing row's
        #     gate is never rewritten by registration, so the migration's
        #     ``was_firing`` state is a seed for the row being created and the
        #     steady-state restart leaves an operator's enabled (or
        #     deliberately disabled) parent exactly as it found it.
        for task_id in sorted(valid_task_ids):
            enabled_now = _persist_sync_task_row(
                registry, task_id, seed_enabled=task_id in was_firing
            )
            if task_id in was_firing and enabled_now:
                logger.info(
                    "[DBAS_SYNC] Preserved firing state for %s across the "
                    "legacy schedule migration (parent gate enabled)", task_id,
                )
            elif task_id in was_firing:
                logger.warning(
                    "[DBAS_SYNC] Legacy schedule for %s was firing, but the "
                    "per-target task is already DISABLED — leaving it off "
                    "(the existing row wins; enable the task to resume)",
                    task_id,
                )
    except Exception as e:  # pragma: no cover — must never break startup
        logger.exception("[DBAS_SYNC] Failed to register sync target tasks: %s", e)
