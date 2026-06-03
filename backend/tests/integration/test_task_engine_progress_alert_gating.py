"""Integration test: the start/progress notification's external alert is gated
on ``alert_on_info``, not the master ``send_alerts`` toggle (GH #462 / bd-on4sr).

``task_scheduler._create_progress_notification`` emits an "info"-level
"<task> starting..." notification and dispatches it externally (Telegram/Discord)
when ``self._send_alerts`` is True. Before bd-on4sr, ``task_engine._execute_task``
wired that flag straight from the task's master ``send_alerts``, so a task with
only warning/error alerts enabled (``alert_on_info=False``) still alerted on every
start. The fix passes ``send_alerts AND alert_on_info`` into
``set_notification_callbacks``; we assert the resulting ``instance._send_alerts``.
"""
from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker
from unittest.mock import AsyncMock

import database
import task_registry
from models import ScheduledTask
from task_engine import TaskEngine
from task_scheduler import ScheduleConfig, ScheduleType, TaskResult, TaskScheduler


class _NoopTask(TaskScheduler):
    task_id = "test_on4sr_progress"
    task_name = "ON4SR Progress Test"
    task_description = "Synthetic task — used by bd-on4sr progress-alert gating test."
    default_enabled = False

    async def execute(self) -> TaskResult:
        now = datetime.utcnow()
        return TaskResult(success=True, message="ok", started_at=now, completed_at=now)


@pytest.fixture
def _registered_task():
    registry = task_registry.get_registry()
    registry.register(_NoopTask)
    registry._instances[_NoopTask.task_id] = _NoopTask(
        ScheduleConfig(schedule_type=ScheduleType.MANUAL)
    )
    yield registry
    registry.unregister(_NoopTask.task_id)
    registry._instances.pop(_NoopTask.task_id, None)


async def _run_with_alert_on_info(test_engine, monkeypatch, registry, *, alert_on_info: bool) -> bool:
    """Seed a ScheduledTask with the given alert_on_info, run _execute_task,
    and return the effective ``instance._send_alerts`` the engine wired."""
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", SessionLocal)

    s = SessionLocal()
    try:
        s.query(ScheduledTask).filter(ScheduledTask.task_id == _NoopTask.task_id).delete()
        s.add(ScheduledTask(
            task_id=_NoopTask.task_id,
            task_name=_NoopTask.task_name,
            description=_NoopTask.task_description,
            enabled=True,
            schedule_type="manual",
            send_alerts=True,          # master ON (user has warning/error enabled)
            alert_on_info=alert_on_info,
            show_notifications=True,
        ))
        s.commit()
    finally:
        s.close()

    engine = TaskEngine()
    # Truthy callbacks so _execute_task takes the set_notification_callbacks path.
    engine._create_notification_callback = AsyncMock(return_value={"id": 1})
    engine._update_notification_callback = AsyncMock()
    engine._delete_notification_callback = AsyncMock()

    await engine._execute_task(task_id=_NoopTask.task_id, triggered_by="test")
    return registry._instances[_NoopTask.task_id]._send_alerts


@pytest.mark.asyncio
async def test_start_notification_not_alerted_when_alert_on_info_false(
    test_engine, monkeypatch, _registered_task
):
    """Master send_alerts=True but alert_on_info=False -> no external start alert."""
    effective = await _run_with_alert_on_info(
        test_engine, monkeypatch, _registered_task, alert_on_info=False
    )
    assert effective is False


@pytest.mark.asyncio
async def test_start_notification_alerted_when_alert_on_info_true(
    test_engine, monkeypatch, _registered_task
):
    """alert_on_info=True -> start notification still alerts externally."""
    effective = await _run_with_alert_on_info(
        test_engine, monkeypatch, _registered_task, alert_on_info=True
    )
    assert effective is True
