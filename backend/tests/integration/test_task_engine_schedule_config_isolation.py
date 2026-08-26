"""Scheduled parameters are per-invocation overlays, not singleton config."""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

import database
import task_registry
from models import ScheduledTask, TaskSchedule
from task_engine import TaskEngine
from task_scheduler import TaskResult, TaskScheduler
from tasks.black_screen_scan import BlackScreenScanTask
from tasks.stream_probe import StreamProbeTask


class _ConfigRecordingTask(TaskScheduler):
    task_id = "test_schedule_config_isolation"
    task_name = "Schedule Config Isolation Test"
    task_description = "Records the effective config for each invocation"

    seen: list[dict] = []

    def __init__(self, schedule_config=None):
        super().__init__(schedule_config)
        self.first = "default-first"
        self.second = "default-second"

    def get_config(self) -> dict:
        return {"first": self.first, "second": self.second}

    def update_config(self, config: dict) -> None:
        if "first" in config:
            self.first = config["first"]
        if "second" in config:
            self.second = config["second"]

    async def run(self) -> TaskResult:
        if self.first == "raise":
            self.seen.append(self.get_config().copy())
            raise RuntimeError("deliberate schedule failure")
        return await super().run()

    async def execute(self) -> TaskResult:
        effective = self.get_config().copy()
        self.seen.append(effective)
        now = datetime.utcnow()
        return TaskResult(
            success=True,
            message="recorded",
            started_at=now,
            completed_at=now,
        )


@pytest.fixture
def _scheduled_task_runtime(test_engine, monkeypatch):
    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_engine,
        expire_on_commit=False,
    )
    monkeypatch.setattr(database, "_SessionLocal", session_factory)

    registry = task_registry.get_registry()
    registry.register(_ConfigRecordingTask)
    instance = _ConfigRecordingTask()
    instance.update_config({"first": "persisted-first"})
    registry._instances[_ConfigRecordingTask.task_id] = instance
    _ConfigRecordingTask.seen = []

    session = session_factory()
    session.add(
        ScheduledTask(
            task_id=_ConfigRecordingTask.task_id,
            task_name=_ConfigRecordingTask.task_name,
            description=_ConfigRecordingTask.task_description,
            enabled=True,
            schedule_type="manual",
            config=json.dumps({"first": "persisted-first"}),
        )
    )
    session.commit()
    session.close()

    yield registry, instance, session_factory

    registry.unregister(_ConfigRecordingTask.task_id)
    registry._instances.pop(_ConfigRecordingTask.task_id, None)


def _schedule(session_factory, *, name: str, parameters: dict | None) -> TaskSchedule:
    due_at = datetime.utcnow() - timedelta(minutes=1)
    schedule = TaskSchedule(
        task_id=_ConfigRecordingTask.task_id,
        name=name,
        enabled=True,
        schedule_type="interval",
        interval_seconds=3600,
        parameters=json.dumps(parameters) if parameters is not None else None,
        next_run_at=due_at,
    )
    session = session_factory()
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    session.expunge(schedule)
    session.close()
    return schedule


def _assert_schedules_advanced(session_factory, *schedule_ids: int) -> None:
    session = session_factory()
    try:
        rows = session.query(TaskSchedule).filter(TaskSchedule.id.in_(schedule_ids)).all()
        assert len(rows) == len(schedule_ids)
        assert all(row.last_run_at is not None for row in rows)
        assert all(row.next_run_at > row.last_run_at for row in rows)
    finally:
        session.close()


def _assert_persisted_and_singleton_config_unchanged(
    instance: _ConfigRecordingTask, session_factory
) -> None:
    assert instance.get_config() == {
        "first": "persisted-first",
        "second": "default-second",
    }
    session = session_factory()
    try:
        row = session.query(ScheduledTask).filter_by(
            task_id=_ConfigRecordingTask.task_id
        ).one()
        assert json.loads(row.config) == {"first": "persisted-first"}
    finally:
        session.close()


@pytest.mark.parametrize("task_class", [StreamProbeTask, BlackScreenScanTask])
def test_exact_restore_preserves_none_as_all_groups(task_class):
    task = task_class()
    baseline = task.get_config().copy()

    task.update_config({"channel_groups": ["sports"]})
    task.restore_invocation_config(baseline)

    assert task.get_config()["channel_groups"] is None


@pytest.mark.asyncio
async def test_co_due_empty_schedule_uses_baseline_not_previous_override(
    _scheduled_task_runtime,
):
    registry, instance, session_factory = _scheduled_task_runtime
    first = _schedule(
        session_factory,
        name="overridden",
        parameters={"first": "schedule-first", "second": "schedule-second"},
    )
    empty = _schedule(session_factory, name="empty", parameters=None)

    await TaskEngine()._execute_task_with_schedules(
        _ConfigRecordingTask.task_id, [first, empty]
    )

    assert _ConfigRecordingTask.seen == [
        {"first": "schedule-first", "second": "schedule-second"},
        {"first": "persisted-first", "second": "default-second"},
    ]
    _assert_schedules_advanced(session_factory, first.id, empty.id)
    _assert_persisted_and_singleton_config_unchanged(instance, session_factory)


@pytest.mark.asyncio
async def test_co_due_partial_schedule_merges_only_its_own_override(
    _scheduled_task_runtime,
):
    registry, instance, session_factory = _scheduled_task_runtime
    first = _schedule(
        session_factory,
        name="full",
        parameters={"first": "schedule-first", "second": "schedule-second"},
    )
    partial = _schedule(
        session_factory, name="partial", parameters={"second": "partial-second"}
    )

    await TaskEngine()._execute_task_with_schedules(
        _ConfigRecordingTask.task_id, [first, partial]
    )

    assert _ConfigRecordingTask.seen == [
        {"first": "schedule-first", "second": "schedule-second"},
        {"first": "persisted-first", "second": "partial-second"},
    ]
    _assert_schedules_advanced(session_factory, first.id, partial.id)
    _assert_persisted_and_singleton_config_unchanged(instance, session_factory)


@pytest.mark.asyncio
async def test_schedule_config_is_restored_after_task_exception(
    _scheduled_task_runtime,
):
    registry, instance, session_factory = _scheduled_task_runtime
    failing = _schedule(
        session_factory,
        name="raising",
        parameters={"first": "raise", "second": "schedule-second"},
    )

    result = await TaskEngine()._execute_task_with_schedules(
        _ConfigRecordingTask.task_id, [failing]
    )

    assert result is not None
    assert result.success is False
    assert result.error == "deliberate schedule failure"
    _assert_schedules_advanced(session_factory, failing.id)
    _assert_persisted_and_singleton_config_unchanged(instance, session_factory)
