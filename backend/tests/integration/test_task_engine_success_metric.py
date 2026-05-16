"""Integration test: task_engine wires the success-timestamp gauge (bd-qxi02).

Confirms the wiring added in ``task_engine._execute_task`` actually
calls ``observability.record_task_success`` when a TaskResult reports
``success=True`` (and only then). The unit tests in
``test_observability_task_scheduler.py`` lock the gauge contract; this
test locks the integration — that the success path in the real
``TaskEngine`` invokes the helper for the right task_id.

We exercise the path through ``_execute_task`` directly with a
synthetic task class registered into the real ``TaskRegistry``, so the
test costs ~milliseconds and doesn't require the scheduler loop.
"""
from datetime import datetime

import pytest

import observability
import task_registry
from task_engine import TaskEngine
from task_scheduler import ScheduleConfig, ScheduleType, TaskResult, TaskScheduler


class _AlwaysSucceedsTask(TaskScheduler):
    task_id = "test_qxi02_success"
    task_name = "QXI02 Success Test"
    task_description = "Synthetic task that always succeeds — used by bd-qxi02 wiring test."
    default_enabled = False  # don't get picked up by any real scheduler

    async def execute(self) -> TaskResult:
        now = datetime.utcnow()
        return TaskResult(
            success=True,
            message="ok",
            started_at=now,
            completed_at=now,
            total_items=1,
            success_count=1,
        )


class _AlwaysFailsTask(TaskScheduler):
    task_id = "test_qxi02_failure"
    task_name = "QXI02 Failure Test"
    task_description = "Synthetic task that always fails — used by bd-qxi02 wiring test."
    default_enabled = False

    async def execute(self) -> TaskResult:
        now = datetime.utcnow()
        return TaskResult(
            success=False,
            message="boom",
            error="DELIBERATE_FAILURE",
            started_at=now,
            completed_at=now,
        )


@pytest.fixture(autouse=True)
def _reset_observability_state():
    observability.reset_for_tests()
    yield
    observability.reset_for_tests()


@pytest.fixture
def _registered_tasks():
    """Register the two synthetic tasks; unregister after the test."""
    registry = task_registry.get_registry()
    registry.register(_AlwaysSucceedsTask)
    registry.register(_AlwaysFailsTask)
    # The registry needs an instance to be createable via get_task_instance.
    # Match production: the registry's _instances dict is populated by
    # sync_from_database normally; here we shortcut and seed directly.
    registry._instances[_AlwaysSucceedsTask.task_id] = _AlwaysSucceedsTask(
        ScheduleConfig(schedule_type=ScheduleType.MANUAL)
    )
    registry._instances[_AlwaysFailsTask.task_id] = _AlwaysFailsTask(
        ScheduleConfig(schedule_type=ScheduleType.MANUAL)
    )
    yield registry
    registry.unregister(_AlwaysSucceedsTask.task_id)
    registry.unregister(_AlwaysFailsTask.task_id)
    registry._instances.pop(_AlwaysSucceedsTask.task_id, None)
    registry._instances.pop(_AlwaysFailsTask.task_id, None)


@pytest.mark.asyncio
async def test_success_path_stamps_task_schedule_gauge(
    test_engine, monkeypatch, _registered_tasks
):
    """A successful TaskResult triggers record_task_success for the task_id."""
    observability.install_metrics()

    # Wire the test engine into the database module so task_engine's
    # get_session calls reach our in-memory SQLite.
    import database
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(
        database, "_SessionLocal",
        sessionmaker(autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False),
    )

    engine = TaskEngine()
    result = await engine._execute_task(
        task_id=_AlwaysSucceedsTask.task_id, triggered_by="test"
    )

    assert result is not None
    assert result.success is True

    # The success path wired in task_engine._execute_task should have
    # called observability.record_task_success("test_qxi02_success"),
    # which stamps the per-task gauge to time.time().
    gauge = observability.get_metric("task_schedule_last_success_timestamp")
    sample = gauge.labels(task_id=_AlwaysSucceedsTask.task_id)
    # Any non-zero stamp proves the helper was invoked. We don't pin
    # an exact value because the engine uses real time.time().
    assert sample._value.get() > 0.0


@pytest.mark.asyncio
async def test_failure_path_does_not_stamp_task_schedule_gauge(
    test_engine, monkeypatch, _registered_tasks
):
    """A failed TaskResult must NOT stamp the success gauge — that's the contract."""
    observability.install_metrics()

    import database
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(
        database, "_SessionLocal",
        sessionmaker(autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False),
    )

    engine = TaskEngine()
    result = await engine._execute_task(
        task_id=_AlwaysFailsTask.task_id, triggered_by="test"
    )

    assert result is not None
    assert result.success is False

    # No labeled value should exist for the failure task_id — the
    # success branch was not taken. Reading the unlabeled
    # gauge family doesn't expose per-label state, but if we never
    # called .labels() the metric family has no children for this
    # task_id. We assert by rendering the metrics and checking the
    # exposition does NOT contain a stamped sample for this task_id.
    body = observability.render_metrics().decode("utf-8")
    assert _AlwaysFailsTask.task_id not in body or (
        f'ecm_task_schedule_last_success_timestamp{{task_id="{_AlwaysFailsTask.task_id}"}}'
        not in body
    )
