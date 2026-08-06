"""The history row, the Journal line and the alert agree about the same run.

Beads ``enhancedchannelmanager-fexq1`` (Journal) and the drill's P-2b (task
history), recorded on ``enhancedchannelmanager-cwmid``.

THE DEFECT
----------
A run's severity was re-derived independently at four points, from two fields
that do not carry it:

* ``task_engine`` chose the completion notification from ``result.success`` and
  then ``result.failed_count > 0``;
* the Journal row was written from the same two, so a degraded-but-real run got
  ``action_type="fail"`` and ``description="Failed …"``;
* the ``TaskExecution`` row got ``status="failed"``, ``success=False`` from
  ``result.success`` alone;
* ``TaskHistoryPanel`` re-derived "Completed with warnings" in the BROWSER from
  ``success === true && failed_count > 0``.

Drill run ``2026-08-06-run9`` measured the contradiction: a redacted DBAS
restore alerted correctly as ``type=warning`` / "Task Completed with Warnings"
while its task-history row read ``status: "failed"``, ``success: false`` for the
same run. And with ``dbas_backup``'s binary counts model, a degraded-but-usable
backup wrote ``"Completed DBAS Backup: 0 ok, 1 failed"`` beside
``success: true`` in one Journal row (``fexq1``).

THE FIX THIS FILE PINS
----------------------
One derivation — :func:`task_scheduler.task_outcome` — produces a
:class:`task_scheduler.TaskOutcome`, and every terminal surface maps from THAT:
the notification severity, the Journal row, and the persisted history row.
``failed_count > 0`` stops being the proxy for severity.

THE DAZIW GUARD
---------------
:class:`_DegradedNoFailedItemsTask` is the case a naive
``TaskResult.success = True`` flip breaks: it has ``failed_count == 0``, so the
old success branch would have chosen a PLAIN "Task Completed" / ``success``
alert for a restore in which not one channel could play. That regression is
bead ``…-daziw``, and
:func:`test_a_degraded_run_with_no_failed_items_is_never_a_plain_success` is
what would catch it.

Conventions: ``docs/pytest_conventions.md``. The harness is modelled on
``tests/integration/test_task_run_notification_severity.py``.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

import database
import task_registry
from models import ScheduledTask, TaskExecution
from task_engine import TaskEngine
from task_scheduler import (
    ScheduleConfig,
    ScheduleType,
    TaskResult,
    TaskScheduler,
)


def _result(**overrides) -> TaskResult:
    now = datetime.utcnow()
    kwargs = dict(
        success=True,
        message="ok",
        started_at=now,
        completed_at=now,
        total_items=420,
        success_count=420,
        failed_count=0,
    )
    kwargs.update(overrides)
    return TaskResult(**kwargs)


class _DegradedNoFailedItemsTask(TaskScheduler):
    """Drill case A: the redacted restore. Nothing failed; nothing can play."""

    task_id = "test_fexq1_degraded_clean_counts"
    task_name = "Fexq1 Degraded Clean Counts"
    task_description = "success=False, completed_degraded=True, failed_count=0."
    default_enabled = False

    async def execute(self) -> TaskResult:
        return _result(
            success=False,
            message=(
                "Restore completed_with_failures: created 423, failed 0 across 13 "
                "categories; 12 channel(s) have NO playable stream"
            ),
            completed_degraded=True,
        )


class _DegradedWithFailedItemsTask(TaskScheduler):
    """Drill case B: the logo-failure restore. One non-fatal row failed."""

    task_id = "test_fexq1_degraded_failed_row"
    task_name = "Fexq1 Degraded Failed Row"
    task_description = "success=False, completed_degraded=True, failed_count=1."
    default_enabled = False

    async def execute(self) -> TaskResult:
        return _result(
            success=False,
            message=(
                "Restore completed_with_failures: created 47, failed 1 across 13 "
                "categories; 1 logo(s) could not be reinstated"
            ),
            success_count=47,
            failed_count=1,
            completed_degraded=True,
        )


class _PartialFailureTask(TaskScheduler):
    """The pre-existing warning shape: a successful run with failed items."""

    task_id = "test_fexq1_partial"
    task_name = "Fexq1 Partial"
    task_description = "success=True with failed items."
    default_enabled = False

    async def execute(self) -> TaskResult:
        return _result(success_count=418, failed_count=2)


class _HardFailureTask(TaskScheduler):
    task_id = "test_fexq1_hard_failure"
    task_name = "Fexq1 Hard Failure"
    task_description = "success=False, not degraded — a real failure."
    default_enabled = False

    async def execute(self) -> TaskResult:
        return _result(
            success=False,
            message="Restore rolled back",
            error="Restore failed during orchestration",
            success_count=0,
            failed_count=3,
        )


class _SuccessTask(TaskScheduler):
    task_id = "test_fexq1_success"
    task_name = "Fexq1 Success"
    task_description = "Clean success."
    default_enabled = False

    async def execute(self) -> TaskResult:
        return _result(message="Restore success: created 420, failed 0")


class _CancelledTask(TaskScheduler):
    task_id = "test_fexq1_cancelled"
    task_name = "Fexq1 Cancelled"
    task_description = "Cancelled mid-run."
    default_enabled = False

    async def execute(self) -> TaskResult:
        return _result(
            success=False,
            message="Task was cancelled",
            error="CANCELLED",
            success_count=12,
        )


@pytest.fixture
def registered_task(request):
    """Register the task class passed via ``indirect`` parametrization."""
    task_cls = request.param
    registry = task_registry.get_registry()
    registry.register(task_cls)
    registry._instances[task_cls.task_id] = task_cls(
        ScheduleConfig(schedule_type=ScheduleType.MANUAL)
    )
    yield task_cls
    registry.unregister(task_cls.task_id)
    registry._instances.pop(task_cls.task_id, None)


class _RunRecord:
    """Everything one finished run persisted or announced."""

    def __init__(self, execution: dict, completion: dict | None, journal: list[dict]):
        self.execution = execution
        self.completion = completion
        self.journal = journal

    @property
    def terminal_journal(self) -> dict:
        """The row written when the run finished (the start row is 'start')."""
        return [row for row in self.journal if row["action_type"] != "start"][-1]


async def _run_task(test_engine, monkeypatch, task_cls) -> _RunRecord:
    """Execute ``task_cls`` through the engine; return row + alert + Journal."""
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", SessionLocal)

    session = SessionLocal()
    try:
        session.query(ScheduledTask).filter(
            ScheduledTask.task_id == task_cls.task_id
        ).delete()
        session.query(TaskExecution).filter(
            TaskExecution.task_id == task_cls.task_id
        ).delete()
        session.add(ScheduledTask(
            task_id=task_cls.task_id,
            task_name=task_cls.task_name,
            description=task_cls.task_description,
            enabled=True,
            schedule_type="manual",
            send_alerts=True,
            alert_on_success=True,
            alert_on_warning=True,
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

    journal_rows: list[dict] = []

    def _capture_journal(**kwargs):
        journal_rows.append(kwargs)

    notify = AsyncMock(return_value={"id": 2})
    with patch(
        "services.notification_service.create_notification_internal", new=notify
    ), patch("task_engine.log_entry", new=_capture_journal):
        await engine._execute_task(task_id=task_cls.task_id, triggered_by="test")

    session = SessionLocal()
    try:
        row = (
            session.query(TaskExecution)
            .filter(TaskExecution.task_id == task_cls.task_id)
            .order_by(TaskExecution.id.desc())
            .first()
        )
        execution = row.to_dict() if row else {}
    finally:
        session.close()

    completion = notify.await_args.kwargs if notify.await_count else None
    return _RunRecord(execution=execution, completion=completion, journal=journal_rows)


# ---------------------------------------------------------------------------
# 1. THE daziw GUARD — the test a naive success-flip would fail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "registered_task", [_DegradedNoFailedItemsTask], indirect=True
)
async def test_a_degraded_run_with_no_failed_items_is_never_a_plain_success(
    test_engine, monkeypatch, registered_task
):
    """``failed_count == 0`` must NOT downgrade the alert to a plain success.

    This is the exact regression a ``TaskResult.success = True`` flip produces:
    the old engine chose the notification from ``failed_count``, so a restore in
    which not one channel could play would have announced "Task Completed".
    """
    run = await _run_task(test_engine, monkeypatch, registered_task)

    assert run.completion["notification_type"] == "warning"
    assert run.completion["title"] == (
        "Task Completed with Warnings: Fexq1 Degraded Clean Counts"
    )
    # The shortfall is still named — the operator's only clue to what degraded.
    assert "12 channel(s) have NO playable stream" in run.completion["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "registered_task", [_DegradedNoFailedItemsTask], indirect=True
)
async def test_the_history_row_does_not_call_a_degraded_run_failed(
    test_engine, monkeypatch, registered_task
):
    """P-2b: the row and the alert described the same run differently."""
    run = await _run_task(test_engine, monkeypatch, registered_task)

    assert run.execution["status"] == "completed_with_warnings"
    assert run.execution["success"] is True
    assert run.execution["error"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "registered_task", [_DegradedNoFailedItemsTask], indirect=True
)
async def test_the_journal_row_does_not_call_a_degraded_run_failed(
    test_engine, monkeypatch, registered_task
):
    """fexq1: the Journal said "Failed" for a run that produced kept state."""
    run = await _run_task(test_engine, monkeypatch, registered_task)
    row = run.terminal_journal

    assert row["action_type"] == "complete"
    assert row["description"].startswith("Completed with warnings")
    assert row["after_value"]["success"] is True
    assert row["after_value"]["severity"] == "warning"


# ---------------------------------------------------------------------------
# 2. The four required cases, on every surface at once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "registered_task", [_DegradedWithFailedItemsTask], indirect=True
)
async def test_a_logo_only_degradation_agrees_across_surfaces(
    test_engine, monkeypatch, registered_task
):
    """cwmid: one non-fatal row failed, nothing was rolled back."""
    run = await _run_task(test_engine, monkeypatch, registered_task)

    assert run.completion["notification_type"] == "warning"
    assert run.execution["status"] == "completed_with_warnings"
    assert run.execution["success"] is True
    assert run.terminal_journal["action_type"] == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize("registered_task", [_PartialFailureTask], indirect=True)
async def test_a_successful_run_with_failed_items_is_recorded_as_a_warning(
    test_engine, monkeypatch, registered_task
):
    """The shape that made ``failed_count > 0`` the de-facto severity proxy.

    Its ALERT is unchanged. What changes is that the history row now SAYS
    warning instead of leaving the browser to infer it from the counts.
    """
    run = await _run_task(test_engine, monkeypatch, registered_task)

    assert run.completion["notification_type"] == "warning"
    assert run.execution["status"] == "completed_with_warnings"
    assert run.execution["success"] is True
    assert run.terminal_journal["description"].startswith("Completed with warnings")


@pytest.mark.asyncio
@pytest.mark.parametrize("registered_task", [_HardFailureTask], indirect=True)
async def test_a_genuine_failure_is_unchanged_everywhere(
    test_engine, monkeypatch, registered_task
):
    """The control. ``error`` still means something."""
    run = await _run_task(test_engine, monkeypatch, registered_task)

    assert run.completion["notification_type"] == "error"
    assert run.completion["title"] == "Task Failed: Fexq1 Hard Failure"
    assert run.execution["status"] == "failed"
    assert run.execution["success"] is False
    row = run.terminal_journal
    assert row["action_type"] == "fail"
    assert row["after_value"]["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("registered_task", [_SuccessTask], indirect=True)
async def test_a_clean_run_is_unchanged_everywhere(
    test_engine, monkeypatch, registered_task
):
    run = await _run_task(test_engine, monkeypatch, registered_task)

    assert run.completion["notification_type"] == "success"
    assert run.completion["title"] == "Task Completed: Fexq1 Success"
    assert run.execution["status"] == "completed"
    assert run.execution["success"] is True
    row = run.terminal_journal
    assert row["action_type"] == "complete"
    assert row["description"] == "Completed Fexq1 Success: 420 ok, 0 failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("registered_task", [_CancelledTask], indirect=True)
async def test_a_cancelled_run_is_unchanged_everywhere(
    test_engine, monkeypatch, registered_task
):
    """A cancelled run stopped; it did not fail and it did not complete."""
    run = await _run_task(test_engine, monkeypatch, registered_task)

    assert run.completion["notification_type"] == "warning"
    assert run.completion["title"] == "Task Cancelled: Fexq1 Cancelled"
    assert run.execution["status"] == "cancelled"
    assert run.execution["success"] is False
    assert run.terminal_journal["action_type"] == "cancel"


# ---------------------------------------------------------------------------
# 3. The SRE gauge must not start counting a degraded run as healthy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "registered_task", [_DegradedNoFailedItemsTask], indirect=True
)
async def test_a_degraded_run_does_not_stamp_the_success_gauge(
    test_engine, monkeypatch, registered_task
):
    """``ecm_task_schedule_last_success_timestamp`` answers a NARROWER question.

    It is the SLI behind the ``ECMTaskScheduleStale*`` alerts (bd-qxi02), and it
    means "when did this task last run CLEANLY". A degraded run must not reset
    that clock: a task that degrades on every run would otherwise look healthy
    forever. Its trigger is deliberately left exactly where it was.
    """
    stamped: list[str] = []
    monkeypatch.setattr(
        "observability.record_task_success", lambda task_id, *a, **k: stamped.append(task_id)
    )

    await _run_task(test_engine, monkeypatch, registered_task)

    assert stamped == []


@pytest.mark.asyncio
@pytest.mark.parametrize("registered_task", [_PartialFailureTask], indirect=True)
async def test_a_partially_failed_run_still_stamps_the_success_gauge(
    test_engine, monkeypatch, registered_task
):
    """Control: the gauge's trigger is unchanged, so this one still stamps.

    Tasks that report some failed items every run (stream probes, pipeline
    runs) have always reset the staleness clock. Narrowing the gauge to clean
    runs only would start a stale-schedule alert storm for them, so the
    severity work deliberately does not touch it.
    """
    stamped: list[str] = []
    monkeypatch.setattr(
        "observability.record_task_success", lambda task_id, *a, **k: stamped.append(task_id)
    )

    await _run_task(test_engine, monkeypatch, registered_task)

    assert stamped == [_PartialFailureTask.task_id]
