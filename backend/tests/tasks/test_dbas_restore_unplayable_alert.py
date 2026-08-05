"""An unplayable-channel downgrade is a WARNING, and it says what it means.

Bead ``enhancedchannelmanager-daziw``, PO decision 2. Two layers:

1. **The task layer** (:mod:`tasks.dbas_restore`) — the one-line summary an
   operator who restored via MCP or reads task history ever sees. It used to
   render ``channels_needing_stream_reattach`` as
   "N channel(s) have NO playable stream", a claim that counter cannot support
   (it counts channels holding a placeholder, most of which still play). The
   "NO playable stream" wording now belongs to
   ``channels_with_no_playable_stream``; the placeholder counter gets wording
   that is true of it.

2. **The engine layer** (:mod:`task_engine`) — an apply downgraded SOLELY by
   unplayable channels alerts as ``Task Completed with Warnings`` /
   ``notification_type="warning"``, not as the red ``Task Failed`` error. Every
   other unsuccessful result — a real category failure, a rollback, any
   non-DBAS task — keeps the error branch byte-for-byte. The task declares the
   state (``TaskResult.completed_degraded``); the engine maps state to severity.

Conventions: ``docs/pytest_conventions.md``. The engine-layer class is modelled
on ``tests/unit/test_channel_pipeline_task_failed_action_notify.py``.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from dbas.restore_contracts import EntityType, RestoreOutcome, RestoreReport
from tasks.dbas_restore import DbasRestoreTask

from tests.tasks.test_dbas_restore_task import _make_task, _write_artifact


def _apply_report(
    *, unplayable: int = 0, holding: int = 0, failed: int = 0
) -> RestoreReport:
    """An applied report holding ``holding`` placeholder channels, ``unplayable`` of them dead."""
    report = RestoreReport(is_dry_run=False, outcome=RestoreOutcome.SUCCESS)
    cat = report.category(EntityType.M3U_ACCOUNT)
    cat.created = 1
    for index in range(holding):
        report.record_stream_reattach_needed(
            name="Channel %d" % index,
            channel_id=200 + index,
            placeholder_streams=["placeholder %d" % index],
            has_playable_stream=index >= unplayable,
        )
    if failed:
        cat.failed = failed
        report.outcome = RestoreOutcome.COMPLETED_WITH_FAILURES
    elif unplayable:
        report.outcome = RestoreOutcome.COMPLETED_WITH_FAILURES
    return report


async def _run_apply(tmp_path, report) -> object:
    art = _write_artifact(tmp_path)
    task = _make_task(art, confirm_apply=True)
    with patch(
        "dbas.restore_orchestrator.run_restore", AsyncMock(return_value=report)
    ), patch("dispatcharr_client.get_client", return_value=AsyncMock()):
        return await task.execute()


# ---------------------------------------------------------------------------
# 1. The operator-facing summary says what is actually true
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSummaryMessageWording:
    async def test_a_leftover_placeholder_is_not_called_unplayable(self, tmp_path):
        """The ixdaw shape: a channel holds a placeholder and still plays.

        Calling that "NO playable stream" is the mislabel this bead removes.
        """
        result = await _run_apply(tmp_path, _apply_report(holding=1, unplayable=0))
        assert "1 channel(s) still hold a placeholder stream" in result.message
        assert "NO playable stream" not in result.message

    async def test_an_unplayable_channel_is_named_as_such(self, tmp_path):
        result = await _run_apply(tmp_path, _apply_report(holding=1, unplayable=1))
        assert "1 channel(s) have NO playable stream" in result.message
        # No redundant second clause when every holder is unplayable.
        assert "still hold a placeholder stream" not in result.message

    async def test_both_populations_read_clearly_together(self, tmp_path):
        result = await _run_apply(tmp_path, _apply_report(holding=3, unplayable=1))
        assert (
            "1 channel(s) have NO playable stream (of 3 still holding a "
            "placeholder stream)" in result.message
        )

    async def test_a_clean_restore_mentions_neither(self, tmp_path):
        result = await _run_apply(tmp_path, _apply_report())
        assert "playable stream" not in result.message
        assert "placeholder stream" not in result.message


# ---------------------------------------------------------------------------
# 2. Which runs are "degraded, not failed"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDegradedNotFailed:
    async def test_an_unplayable_only_downgrade_is_degraded(self, tmp_path):
        result = await _run_apply(tmp_path, _apply_report(holding=1, unplayable=1))
        # Still not a success — the outcome is completed_with_failures.
        assert result.success is False
        assert result.completed_degraded is True

    async def test_a_real_category_failure_is_a_hard_failure(self, tmp_path):
        """A failed row is a failure even when channels are also unplayable."""
        result = await _run_apply(
            tmp_path, _apply_report(holding=1, unplayable=1, failed=1)
        )
        assert result.success is False
        assert result.completed_degraded is False

    async def test_a_rolled_back_run_is_a_hard_failure(self, tmp_path):
        report = _apply_report(holding=1, unplayable=1)
        report.outcome = RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE
        result = await _run_apply(tmp_path, report)
        assert result.success is False
        assert result.completed_degraded is False

    async def test_a_clean_apply_is_not_degraded(self, tmp_path):
        result = await _run_apply(tmp_path, _apply_report())
        assert result.success is True
        assert result.completed_degraded is False

    async def test_a_dry_run_is_not_degraded(self, tmp_path):
        report = RestoreReport(is_dry_run=True)
        report.record_stream_reattach_needed(
            name="Obscure", placeholder_streams=["p"], has_playable_stream=False
        )
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        with patch(
            "dbas.restore_orchestrator.run_dry_run", AsyncMock(return_value=report)
        ), patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()
        assert result.success is True
        assert result.completed_degraded is False


# ---------------------------------------------------------------------------
# 3. Engine layer: degraded -> warning, everything else -> unchanged
# ---------------------------------------------------------------------------
from sqlalchemy.orm import sessionmaker  # noqa: E402

import database  # noqa: E402
import task_registry  # noqa: E402
from models import ScheduledTask  # noqa: E402
from task_engine import TaskEngine  # noqa: E402
from task_scheduler import (  # noqa: E402
    ScheduleConfig,
    ScheduleType,
    TaskResult,
    TaskScheduler,
)


class _DegradedTask(TaskScheduler):
    task_id = "test_daziw_degraded"
    task_name = "Daziw Degraded Test"
    task_description = "Synthetic task — success=False, completed_degraded=True."
    default_enabled = False

    async def execute(self) -> TaskResult:
        now = datetime.utcnow()
        return TaskResult(
            success=False,
            message="Restore applied: 32 created, 0 failed; 1 channel(s) have NO playable stream",
            started_at=now,
            completed_at=now,
            total_items=32,
            success_count=32,
            failed_count=0,
            completed_degraded=True,
        )


class _HardFailureTask(TaskScheduler):
    task_id = "test_daziw_hard_failure"
    task_name = "Daziw Hard Failure Test"
    task_description = "Synthetic task — success=False, no degraded flag (control)."
    default_enabled = False

    async def execute(self) -> TaskResult:
        now = datetime.utcnow()
        return TaskResult(
            success=False,
            message="Restore rolled back",
            error="Restore failed during orchestration",
            started_at=now,
            completed_at=now,
            total_items=32,
            success_count=0,
            failed_count=3,
        )


def _register(task_cls):
    registry = task_registry.get_registry()
    registry.register(task_cls)
    registry._instances[task_cls.task_id] = task_cls(
        ScheduleConfig(schedule_type=ScheduleType.MANUAL)
    )
    return registry


@pytest.fixture
def _registered_degraded_task():
    registry = _register(_DegradedTask)
    yield registry
    registry.unregister(_DegradedTask.task_id)
    registry._instances.pop(_DegradedTask.task_id, None)


@pytest.fixture
def _registered_hard_failure_task():
    registry = _register(_HardFailureTask)
    yield registry
    registry.unregister(_HardFailureTask.task_id)
    registry._instances.pop(_HardFailureTask.task_id, None)


async def _capture_completion_notification(test_engine, monkeypatch, task_cls):
    """Run ``task_cls`` through the engine and return the completion notification kwargs."""
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", SessionLocal)

    session = SessionLocal()
    try:
        session.query(ScheduledTask).filter(
            ScheduledTask.task_id == task_cls.task_id
        ).delete()
        session.add(ScheduledTask(
            task_id=task_cls.task_id,
            task_name=task_cls.task_name,
            description=task_cls.task_description,
            enabled=True,
            schedule_type="manual",
            send_alerts=True,
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

    notify = AsyncMock(return_value={"id": 2})
    with patch("services.notification_service.create_notification_internal", new=notify):
        await engine._execute_task(task_id=task_cls.task_id, triggered_by="test")

    assert notify.await_count == 1
    return notify.await_args.kwargs


@pytest.mark.asyncio
async def test_a_degraded_result_alerts_as_a_warning(
    test_engine, monkeypatch, _registered_degraded_task
):
    """PO decision 2: unplayable channels are a warning, not "Task Failed"."""
    kwargs = await _capture_completion_notification(
        test_engine, monkeypatch, _DegradedTask
    )
    assert kwargs["notification_type"] == "warning"
    assert kwargs["title"].startswith("Task Completed with Warnings")
    # The operator still gets the detail that made it a warning.
    assert "NO playable stream" in kwargs["message"]
    # External alerts still fire on the unattended path.
    assert kwargs["send_alerts"] is True


@pytest.mark.asyncio
async def test_a_hard_failure_still_alerts_as_an_error(
    test_engine, monkeypatch, _registered_hard_failure_task
):
    """Control: without the flag, an unsuccessful result is unchanged."""
    kwargs = await _capture_completion_notification(
        test_engine, monkeypatch, _HardFailureTask
    )
    assert kwargs["notification_type"] == "error"
    assert kwargs["title"].startswith("Task Failed")
