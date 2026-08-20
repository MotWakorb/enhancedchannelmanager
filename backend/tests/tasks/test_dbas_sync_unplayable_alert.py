"""A cross-instance sync that leaves a channel unplayable is a WARNING, not a failure.

Bead ``enhancedchannelmanager-daziw``, PO decision 2026-08-19 (the residual
question the bead left open). The restore half shipped in v0.18.1-0027: an apply
that finishes with :attr:`RestoreReport.channels_with_no_playable_stream` above
zero returns ``COMPLETED_WITH_FAILURES``, and
:mod:`tasks.dbas_restore` declares that run degraded so the task engine alerts it
as ``warning`` instead of the red "Task Failed".

Cross-instance sync runs on the SAME orchestrator, so it already SHARED the
outcome downgrade — but it builds its OWN ``TaskResult``, which never set
``completed_degraded``. The consequence measured on the shipped build: a sync
whose only shortfall was an unplayable channel alerted ``type="error"`` /
"Task Failed: Cross-Instance Sync", and its history row read ``failed`` beside
``failed_count: 0``.

THE PO DECISION, VERBATIM: "cross-instance sync SHARES the downgrade, and fires
it as notification_type='warning', not 'error'." Sync is scheduled and
unattended, so an unfixed shortfall would otherwise fire a red alert on every
run — which is how operators are trained to ignore alerts. ``warning`` is a
first-class severity with a PER-TASK opt-out (``ScheduledTask.alert_on_warning``)
already wired, so volume is controlled at the alert layer WITHOUT the code
reporting success for an instance that cannot play a channel. A sync-specific
definition of success was explicitly NOT chosen.

Four layers, because a green assertion at one of them proves nothing about the
next:

1. **The report** — which structure actually carries the signal. Not
   ``skip_details`` and not ``failure_details``: the unplayable population lives
   in the top-level aggregates ``channels_needing_stream_reattach`` /
   ``channels_with_no_playable_stream`` with a per-row drill-down in
   ``stream_reattach_details``, all three written only by
   ``RestoreReport.record_stream_reattach_needed``.
2. **The engine** (:mod:`tasks.dbas_sync_engine`) — the outcome the shared
   ``compute_outcome`` derives survives to the caller, and the persisted
   ``last_full_sync_at`` still refuses to advance on it.
3. **The task** (:mod:`tasks.dbas_sync`) — the ``TaskResult`` it builds declares
   the run degraded, and its one-line summary NAMES the shortfall.
4. **The alert** (:mod:`task_engine`) — an operator receives ``warning`` /
   "Task Completed with Warnings", and turning ``alert_on_warning`` off
   suppresses the external alert without suppressing the outcome.

Conventions: ``docs/pytest_conventions.md``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

import database
import task_registry
from dbas.restore_contracts import (
    EntityCategoryReport,
    EntityType,
    RestoreOutcome,
    RestoreReport,
)
from dbas.restore_orchestrator import compute_outcome
from models import ScheduledTask
from task_engine import TaskEngine
from task_scheduler import ScheduleConfig, ScheduleType, completion_notification_type

from tests.tasks.test_dbas_sync_task import _make_target


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


@pytest.fixture
def _wire_db(test_engine, monkeypatch):
    """Point database._SessionLocal at the in-memory test engine (same harness
    as ``test_dbas_sync_task.py``)."""
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", TestSessionLocal)
    return TestSessionLocal


def _unplayable_apply_report(*, unplayable: int = 1, holding: int = 1) -> RestoreReport:
    """An APPLIED sync report: ``holding`` channels hold a placeholder,
    ``unplayable`` of them have no URL-bearing stream left at all.

    Every category count is CLEAN — this is the drill-run-3 shape, where
    ``created 32, failed 0`` described an instance that returned HTTP 500 with
    0 bytes on the one channel it had just restored.
    """
    report = RestoreReport(is_dry_run=False)
    report.category(EntityType.CHANNEL).created = 32
    for index in range(holding):
        report.record_stream_reattach_needed(
            name="Drill Channel %d" % index,
            channel_id=12 + index,
            placeholder_streams=["ECM placeholder %d" % index],
            has_playable_stream=index >= unplayable,
        )
    report.outcome = compute_outcome(
        report=report, failure_occurred=False, rollback=None
    )
    return report


def _clean_apply_report() -> RestoreReport:
    report = RestoreReport(is_dry_run=False)
    report.category(EntityType.CHANNEL).created = 32
    report.outcome = compute_outcome(
        report=report, failure_occurred=False, rollback=None
    )
    return report


def _non_fatal_failure_report() -> RestoreReport:
    """The ``…-cwmid`` shape: a non-fatal category failed, every channel plays."""
    report = RestoreReport(is_dry_run=False, outcome=RestoreOutcome.COMPLETED_WITH_FAILURES)
    report.category(EntityType.CHANNEL).created = 32
    report.category(EntityType.LOGO).created = 11
    report.category(EntityType.LOGO).failed = 1
    return report


def _rolled_back_report(outcome: RestoreOutcome) -> RestoreReport:
    report = RestoreReport(is_dry_run=False, outcome=outcome)
    report.category(EntityType.CHANNEL).created = 4
    report.category(EntityType.CHANNEL).failed = 1
    return report


def _dry_run_report_predicting_a_shortfall() -> RestoreReport:
    """A PREVIEW that names a channel it expects to strand.

    A prediction is not a failure, and nothing has been applied to be
    unplayable — the preview must stay a clean success.
    """
    report = RestoreReport(is_dry_run=True)
    report.record_stream_reattach_needed(
        name="Drill Channel 0",
        channel_id=12,
        placeholder_streams=["ECM placeholder 0"],
        has_playable_stream=False,
    )
    return report


async def _run_sync_task(target_id: int, report, *, confirm_apply: bool = True):
    """Run the UNBOUND task against a patched ``run_sync`` returning ``report``."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    async def _fake_run_sync(sync_target, *, confirm_apply=False, session=None, **_kw):
        return report

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        task = DbasSyncTask()
        task.update_config(
            {"sync_target_id": target_id, "confirm_apply": confirm_apply}
        )
        return await task.execute()


# ---------------------------------------------------------------------------
# 1. WHICH STRUCTURE CARRIES THE SIGNAL
# ---------------------------------------------------------------------------


def test_the_unplayable_signal_is_an_aggregate_not_a_skip_or_failure_detail():
    """Guards the false-green trap: asserting on the wrong structure passes
    against broken code.

    ``skip_details`` (``SkipReason``) and ``failure_details`` (``FailureReason``)
    are per-ENTITY records the importers write. The placeholder-rebind shortfall
    is neither — the entity was created successfully. It is recorded as a
    top-level aggregate plus its own drill-down list, which is exactly why the
    outcome decision could not see it before this bead.
    """
    report = _unplayable_apply_report()

    assert report.channels_needing_stream_reattach == 1
    assert report.channels_with_no_playable_stream == 1
    assert len(report.stream_reattach_details) == 1
    assert report.stream_reattach_details[0].has_playable_stream is False
    # Nothing FAILED and nothing was SKIPPED — that is the whole defect.
    channel = report.category(EntityType.CHANNEL)
    assert channel.failed == 0
    assert isinstance(channel, EntityCategoryReport)
    assert all(not cat.failure_details for cat in report.categories)
    assert all(not cat.skip_details for cat in report.categories)


def test_a_leftover_placeholder_beside_a_real_stream_is_not_the_signal():
    """The ``…-ixdaw`` shape. ``channels_needing_stream_reattach`` counts it and
    the channel plays fine, so it must NOT downgrade anything."""
    report = _unplayable_apply_report(unplayable=0, holding=1)

    assert report.channels_needing_stream_reattach == 1
    assert report.channels_with_no_playable_stream == 0
    assert report.outcome == RestoreOutcome.SUCCESS


# ---------------------------------------------------------------------------
# 2. ENGINE LAYER — the downgrade survives, and B is not marked current
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_sync_engine_never_upgrades_an_unplayable_apply_back_to_success(
    tmp_path,
):
    """Blast-radius call sites 3 and 4 (``dbas_sync_engine``).

    The engine re-checks the outcome after attaching name-conflict details and
    stamps the persisted per-target state. Neither may turn an apply that left a
    channel unplayable back into a clean success, and ``last_full_sync_at`` —
    "B was current as of this time" — must not advance on it.
    """
    from tasks import dbas_sync_engine as engine
    from routers import backup as backup_mod
    from tests.tasks.test_dbas_sync_engine import (
        _empty_dest_client,
        _source_client,
        _sync_target,
    )

    async def _fake_run_restore(*, report, **_kw):
        report.record_stream_reattach_needed(
            name="Drill KERA Dallas",
            channel_id=12,
            placeholder_streams=["ECM placeholder"],
            has_playable_stream=False,
        )
        report.outcome = compute_outcome(
            report=report, failure_occurred=False, rollback=None
        )
        return report

    target = _sync_target()
    target.last_outcome = None
    target.last_full_sync_at = None
    session = MagicMock()

    with patch.object(backup_mod, "get_client", return_value=_source_client()), \
         patch.object(engine, "make_remote_client", return_value=_empty_dest_client()), \
         patch.object(engine, "sync_freshness_reason", return_value=None), \
         patch.object(engine, "run_restore", side_effect=_fake_run_restore):
        report = await engine.run_sync(
            target, confirm_apply=True, session=session, ledger_dir=tmp_path,
        )

    assert report.channels_with_no_playable_stream == 1
    assert report.outcome == RestoreOutcome.COMPLETED_WITH_FAILURES
    assert target.last_outcome == "completed_with_failures"
    assert target.last_full_sync_at is None


# ---------------------------------------------------------------------------
# 3. TASK LAYER — degraded, not failed; and the summary says why
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSyncDegradedNotFailed:
    async def test_an_unplayable_channel_is_degraded_not_failed(self, _wire_db):
        session = _wire_db()
        target_id = _make_target(session).id
        session.close()

        result = await _run_sync_task(target_id, _unplayable_apply_report())

        # Still NOT a success — the outcome is completed_with_failures.
        assert result.success is False
        assert result.details["outcome"] == "completed_with_failures"
        assert result.completed_degraded is True
        assert completion_notification_type(result) == "warning"

    async def test_the_summary_names_the_unplayable_channels(self, _wire_db):
        """A warning an operator cannot act on is not a warning.

        The counts read ``failed 0``, so without this clause the alert says
        nothing at all about why the run was not clean.
        """
        session = _wire_db()
        target_id = _make_target(session).id
        session.close()

        result = await _run_sync_task(target_id, _unplayable_apply_report())

        assert "1 channel(s) have NO playable stream" in result.message

    async def test_a_leftover_placeholder_is_not_called_unplayable(self, _wire_db):
        session = _wire_db()
        target_id = _make_target(session).id
        session.close()

        result = await _run_sync_task(
            target_id, _unplayable_apply_report(unplayable=0, holding=1)
        )

        assert result.success is True
        assert result.completed_degraded is False
        assert "NO playable stream" not in result.message

    async def test_a_non_fatal_category_failure_is_also_degraded(self, _wire_db):
        """``…-cwmid`` parity: the rule is the OUTCOME, not which category
        degraded. Sync must not re-introduce the severity inversion the restore
        path already removed — a cosmetic logo failure shouting "Task Failed"
        while an unplayable channel whispers."""
        session = _wire_db()
        target_id = _make_target(session).id
        session.close()

        result = await _run_sync_task(target_id, _non_fatal_failure_report())

        assert result.success is False
        assert result.completed_degraded is True
        assert completion_notification_type(result) == "warning"

    @pytest.mark.parametrize(
        "outcome",
        [
            RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK,
            RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE,
        ],
    )
    async def test_a_rolled_back_sync_is_still_a_hard_failure(self, _wire_db, outcome):
        """The control: ``error`` still means something. Both rolled-back /
        indeterminate outcomes keep the red alert."""
        session = _wire_db()
        target_id = _make_target(session).id
        session.close()

        result = await _run_sync_task(target_id, _rolled_back_report(outcome))

        assert result.success is False
        assert result.completed_degraded is False
        assert completion_notification_type(result) == "error"

    async def test_a_clean_apply_is_not_degraded(self, _wire_db):
        session = _wire_db()
        target_id = _make_target(session).id
        session.close()

        result = await _run_sync_task(target_id, _clean_apply_report())

        assert result.success is True
        assert result.completed_degraded is False
        assert completion_notification_type(result) == "success"

    async def test_a_dry_run_predicting_a_shortfall_is_not_degraded(self, _wire_db):
        """A preview is a prediction, not a failure — and it applied nothing."""
        session = _wire_db()
        target_id = _make_target(session).id
        session.close()

        result = await _run_sync_task(
            target_id, _dry_run_report_predicting_a_shortfall(), confirm_apply=False
        )

        assert result.success is True
        assert result.completed_degraded is False
        assert completion_notification_type(result) == "success"

    async def test_a_run_that_never_reached_the_engine_is_not_degraded(self, _wire_db):
        """An exception rolled nothing back only because it applied nothing —
        there is no kept state to report, so this stays a hard failure."""
        from tasks import dbas_sync
        from tasks.dbas_sync import DbasSyncTask

        session = _wire_db()
        target_id = _make_target(session).id
        session.close()

        async def _boom(sync_target, **_kw):
            raise RuntimeError("remote-b unreachable")

        with patch.object(dbas_sync, "run_sync", side_effect=_boom):
            task = DbasSyncTask()
            task.update_config({"sync_target_id": target_id, "confirm_apply": True})
            result = await task.execute()

        assert result.success is False
        assert result.completed_degraded is False
        assert completion_notification_type(result) == "error"


# ---------------------------------------------------------------------------
# 4. ALERT LAYER — warning by default, per-task opt-out honoured
# ---------------------------------------------------------------------------


async def _capture_sync_completion_notification(
    _wire_db, *, report, alert_on_warning: bool
):
    """Register the REAL per-target sync task, run it through the task engine,
    and return the completion notification's kwargs."""
    from tasks import dbas_sync
    from tasks.dbas_sync import make_sync_task_class, sync_task_id_for

    session = _wire_db()
    target = _make_target(session)
    target_id = target.id
    session.close()

    task_id = sync_task_id_for(target_id)
    task_cls = make_sync_task_class(target_id, "dispatcharr-b")
    registry = task_registry.get_registry()
    registry.register(task_cls)
    registry._instances[task_id] = task_cls(
        ScheduleConfig(schedule_type=ScheduleType.MANUAL)
    )

    session = _wire_db()
    try:
        session.query(ScheduledTask).filter(
            ScheduledTask.task_id == task_id
        ).delete()
        session.add(ScheduledTask(
            task_id=task_id,
            task_name=task_cls.task_name,
            description=task_cls.task_description,
            enabled=True,
            schedule_type="manual",
            send_alerts=True,
            alert_on_warning=alert_on_warning,
            alert_on_error=True,
            show_notifications=True,
        ))
        session.commit()
    finally:
        session.close()

    engine = TaskEngine()
    engine._create_notification_callback = AsyncMock(return_value={"id": 1})
    engine._update_notification_callback = AsyncMock()
    engine._delete_notification_callback = AsyncMock()

    async def _fake_run_sync(sync_target, **_kw):
        return report

    notify = AsyncMock(return_value={"id": 2})
    try:
        with patch("services.notification_service.create_notification_internal", new=notify), \
             patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
            await engine._execute_task(
                task_id=task_id,
                triggered_by="test",
                parameters={"sync_target_id": target_id, "confirm_apply": True},
            )
    finally:
        registry.unregister(task_id)
        registry._instances.pop(task_id, None)

    assert notify.await_count == 1
    return notify.await_args.kwargs


@pytest.mark.asyncio
async def test_a_degraded_sync_alerts_as_a_warning(_wire_db):
    """PO decision 2026-08-19: warning, NOT error, on the unattended path."""
    kwargs = await _capture_sync_completion_notification(
        _wire_db, report=_unplayable_apply_report(), alert_on_warning=True
    )

    assert kwargs["notification_type"] == "warning"
    assert kwargs["title"].startswith("Task Completed with Warnings")
    assert "NO playable stream" in kwargs["message"]
    # The external alert still fires for an operator who wants it.
    assert kwargs["send_alerts"] is True


@pytest.mark.asyncio
async def test_the_operator_can_silence_the_alert_without_silencing_the_outcome(
    _wire_db,
):
    """``alert_on_warning=False`` is the volume control the PO decision names.

    It suppresses the EXTERNAL alert only — the notification is still recorded
    and the run is still not reported as a success."""
    kwargs = await _capture_sync_completion_notification(
        _wire_db, report=_unplayable_apply_report(), alert_on_warning=False
    )

    assert kwargs["notification_type"] == "warning"
    assert kwargs["send_alerts"] is False


@pytest.mark.asyncio
async def test_a_rolled_back_sync_still_alerts_as_a_task_failure(_wire_db):
    """The control at the alert layer, not just at the flag."""
    kwargs = await _capture_sync_completion_notification(
        _wire_db,
        report=_rolled_back_report(RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK),
        alert_on_warning=True,
    )

    assert kwargs["notification_type"] == "error"
    assert kwargs["title"].startswith("Task Failed")
