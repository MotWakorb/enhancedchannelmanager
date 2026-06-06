"""_save_task_to_db must not clobber a DB-managed next_run_at (issue #468 / bd-a80u2).

For tasks driven by the multi-schedule system (``task_schedules`` rows), the
parent ``scheduled_tasks.next_run_at`` is maintained by the schedule endpoints
and the task engine from the child schedules. The registry's in-memory
``instance._next_run`` only refreshes at startup, so a routine
``sync_to_database`` (triggered e.g. by editing alert settings) used to write a
stale ``None`` over the correct value — leaving the UI stuck on "Never" across
restarts. The guard must skip the write when child schedules exist, while
preserving the legacy write path for tasks that have none.
"""
from datetime import datetime

from task_registry import TaskRegistry
from models import ScheduledTask, TaskSchedule
from tasks.stream_probe import StreamProbeTask


def _register_stale_instance(reg):
    inst = StreamProbeTask()
    inst._next_run = None  # the stale in-memory value (bug condition)
    reg.register(StreamProbeTask)
    reg._instances["stream_probe"] = inst
    return inst


def test_save_does_not_clobber_next_run_when_schedules_exist(test_session, monkeypatch):
    monkeypatch.setattr("task_registry.get_session", lambda: test_session)
    from tests.fixtures.factories import create_scheduled_task

    future = datetime(2026, 6, 3, 8, 0, 0)
    create_scheduled_task(
        test_session, task_id="stream_probe", task_name="Stream Probe",
        description="x", enabled=True, schedule_type="manual", next_run_at=future,
    )
    test_session.add(TaskSchedule(
        task_id="stream_probe", enabled=True, schedule_type="daily",
        schedule_time="03:00", timezone="UTC", next_run_at=future,
    ))
    test_session.commit()

    reg = TaskRegistry()
    _register_stale_instance(reg)

    reg.sync_to_database("stream_probe")

    row = test_session.query(ScheduledTask).filter_by(task_id="stream_probe").first()
    assert row.next_run_at == future  # preserved, NOT clobbered to None


def test_save_writes_next_run_when_no_schedules(test_session, monkeypatch):
    """Legacy path: with no child schedules, the in-memory value is still written."""
    monkeypatch.setattr("task_registry.get_session", lambda: test_session)
    from tests.fixtures.factories import create_scheduled_task

    create_scheduled_task(
        test_session, task_id="stream_probe", task_name="Stream Probe",
        description="x", enabled=True, schedule_type="manual", next_run_at=None,
    )

    reg = TaskRegistry()
    inst = StreamProbeTask()
    future = datetime(2026, 6, 3, 8, 0, 0)
    inst._next_run = future
    reg.register(StreamProbeTask)
    reg._instances["stream_probe"] = inst

    reg.sync_to_database("stream_probe")

    row = test_session.query(ScheduledTask).filter_by(task_id="stream_probe").first()
    assert row.next_run_at == future
