"""One run, one severity — the progress notification agrees with the completion one.

Bead ``enhancedchannelmanager-asf3n``. A single task run touches TWO
notification records:

1. the **completion** notification created by
   :meth:`task_engine.TaskEngine._notify_task_result` (title
   ``"Task Completed with Warnings: <task>"`` and friends), and
2. the **progress** notification created at start by
   :meth:`task_scheduler.TaskScheduler._create_progress_notification` (title is
   the bare ``task_name``) and re-typed at the end by
   ``_finalize_progress_notification``.

Only (1) knew about ``TaskResult.completed_degraded`` (bead
``enhancedchannelmanager-daziw``), so a degraded DBAS restore left the operator
with a ``warning`` and an ``error`` side by side for the same event and no way
to tell which was authoritative. Both emitters now read the same map
(:func:`task_scheduler.completion_notification_type`), so a run has ONE severity.

The daziw wording and severity are asserted here as well as in
``tests/tasks/test_dbas_restore_unplayable_alert.py`` — this file is what would
go red if a future change re-typed the degraded completion alert.

Conventions: ``docs/pytest_conventions.md``. The harness is modelled on
``tests/integration/test_task_engine_progress_alert_gating.py``.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

import database
import task_registry
from models import ScheduledTask
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


class _DegradedTask(TaskScheduler):
    task_id = "test_asf3n_degraded"
    task_name = "Asf3n Degraded Test"
    task_description = "Synthetic task — success=False, completed_degraded=True."
    default_enabled = False

    async def execute(self) -> TaskResult:
        return _result(
            success=False,
            message=(
                "Restore completed_with_failures: created 420, failed 0 across 13 "
                "categories; 12 channel(s) have NO playable stream"
            ),
            completed_degraded=True,
        )


class _HardFailureTask(TaskScheduler):
    task_id = "test_asf3n_hard_failure"
    task_name = "Asf3n Hard Failure Test"
    task_description = "Synthetic task — success=False, not degraded."
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
    task_id = "test_asf3n_success"
    task_name = "Asf3n Success Test"
    task_description = "Synthetic task — clean success."
    default_enabled = False

    async def execute(self) -> TaskResult:
        return _result(message="Restore completed: created 420, failed 0")


class _PartialFailureTask(TaskScheduler):
    task_id = "test_asf3n_partial"
    task_name = "Asf3n Partial Test"
    task_description = "Synthetic task — success=True with failed items."
    default_enabled = False

    async def execute(self) -> TaskResult:
        return _result(success_count=418, failed_count=2)


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


class _RunNotifications:
    """Everything a single run told the operator, by emitter."""

    def __init__(self, completion: list[dict], progress_final: list[dict]):
        self.completion = completion
        self.progress_final = progress_final

    @property
    def severities(self) -> list[str]:
        """Every severity the finished run assigned, across both emitters."""
        return [n["notification_type"] for n in self.completion] + [
            n["notification_type"] for n in self.progress_final
        ]


async def _run_task(test_engine, monkeypatch, task_cls) -> _RunNotifications:
    """Execute ``task_cls`` through the engine, capturing both notification paths."""
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

    notify = AsyncMock(return_value={"id": 2})
    with patch("services.notification_service.create_notification_internal", new=notify):
        await engine._execute_task(task_id=task_cls.task_id, triggered_by="test")

    # Progress updates that carry a severity are the finalize call; the
    # rate-limited in-flight updates pass message/metadata only.
    progress_final = [
        call.kwargs
        for call in engine._update_notification_callback.await_args_list
        if call.kwargs.get("notification_type") is not None
    ]
    return _RunNotifications(
        completion=[call.kwargs for call in notify.await_args_list],
        progress_final=progress_final,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("registered_task", [_DegradedTask], indirect=True)
async def test_a_degraded_run_never_reports_an_error(
    test_engine, monkeypatch, registered_task
):
    """The asf3n defect: warning + error for one event, and no way to tell which is right."""
    emitted = await _run_task(test_engine, monkeypatch, registered_task)
    assert emitted.severities == ["warning", "warning"]


@pytest.mark.asyncio
@pytest.mark.parametrize("registered_task", [_DegradedTask], indirect=True)
async def test_a_degraded_run_emits_one_completion_notification(
    test_engine, monkeypatch, registered_task
):
    """daziw's alert is untouched: still exactly one, still worded the same."""
    emitted = await _run_task(test_engine, monkeypatch, registered_task)
    assert len(emitted.completion) == 1
    assert emitted.completion[0]["title"] == (
        "Task Completed with Warnings: Asf3n Degraded Test"
    )
    assert emitted.completion[0]["notification_type"] == "warning"
    assert "12 channel(s) have NO playable stream" in emitted.completion[0]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("registered_task", [_HardFailureTask], indirect=True)
async def test_a_genuine_failure_still_reports_an_error(
    test_engine, monkeypatch, registered_task
):
    """Control: without ``completed_degraded`` an unsuccessful run is still red."""
    emitted = await _run_task(test_engine, monkeypatch, registered_task)
    assert emitted.severities == ["error", "error"]
    assert emitted.completion[0]["title"] == "Task Failed: Asf3n Hard Failure Test"


@pytest.mark.asyncio
@pytest.mark.parametrize("registered_task", [_SuccessTask], indirect=True)
async def test_a_successful_run_still_reports_success(
    test_engine, monkeypatch, registered_task
):
    emitted = await _run_task(test_engine, monkeypatch, registered_task)
    assert emitted.severities == ["success", "success"]
    assert emitted.completion[0]["title"] == "Task Completed: Asf3n Success Test"


@pytest.mark.asyncio
@pytest.mark.parametrize("registered_task", [_PartialFailureTask], indirect=True)
async def test_a_run_with_failed_items_still_reports_a_warning(
    test_engine, monkeypatch, registered_task
):
    """A successful run with failed items was already a warning on both paths."""
    emitted = await _run_task(test_engine, monkeypatch, registered_task)
    assert emitted.severities == ["warning", "warning"]
    assert emitted.completion[0]["title"] == (
        "Task Completed with Warnings: Asf3n Partial Test"
    )
