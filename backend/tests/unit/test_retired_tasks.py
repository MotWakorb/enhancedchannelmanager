"""Startup retirement against an isolated SQLite DB, with synthetic rows only."""
from datetime import datetime
import logging

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from models import ScheduledTask, TaskSchedule, TaskExecution
from task_registry import TaskRegistry
from task_scheduler import TaskScheduler, TaskResult

STAMP = datetime(2026, 1, 1)


@pytest.fixture
def store(test_engine, monkeypatch):
    factory = sessionmaker(bind=test_engine)
    monkeypatch.setattr("task_registry.get_session", factory)
    monkeypatch.setattr("observability.update_task_schedule_null_count", lambda: None)
    return factory


def snapshot(store):
    with store() as session:
        return {
            model.__tablename__: [
                {column.name: getattr(row, column.name) for column in model.__table__.columns}
                for row in session.query(model).order_by(model.id).all()
            ] for model in (ScheduledTask, TaskSchedule, TaskExecution)
        }


def seed(store, parent=True):
    with store() as session:
        for task in ("export_publish", "synthetic_unknown"):
            if parent or task != "export_publish":
                session.add(ScheduledTask(task_id=task, task_name=f"Saved {task}", enabled=True,
                    next_run_at=STAMP, last_run_at=STAMP, config='{"saved": true}',
                    schedule_type="interval", interval_seconds=123, send_alerts=False))
            for index in range(3):
                session.add(TaskSchedule(task_id=task, name=f"Cadence {index}", enabled=index != 1,
                    schedule_type="interval", interval_seconds=120 + index, next_run_at=STAMP,
                    last_run_at=STAMP, parameters='{"synthetic": true}', timezone="UTC"))
            session.add(TaskExecution(task_id=task, started_at=STAMP, completed_at=STAMP,
                status="completed", success=True, message="Synthetic history", details='{"keep": true}'))
        session.commit()


@pytest.mark.parametrize("parent", [True, False])
def test_retirement_disables_all_schedules_preserves_data_and_is_idempotent(store, caplog, parent):
    seed(store, parent)
    before = snapshot(store)
    caplog.set_level(logging.INFO)
    TaskRegistry().sync_from_database()
    after = snapshot(store)
    for table, rows in after.items():
        for row, original in zip(rows, before[table]):
            expected = dict(original)
            if row["task_id"] == "export_publish" and table != "task_executions":
                expected.update(enabled=False, next_run_at=None, updated_at=row["updated_at"])
            assert row == expected
    assert "Task export_publish in DB but not registered" not in caplog.text
    assert "Task synthetic_unknown in DB but not registered" in caplog.text
    assert caplog.text.count("Disabled retired task export_publish") == 1
    caplog.clear()
    TaskRegistry().sync_from_database()
    assert snapshot(store) == after
    assert "Disabled retired task" not in caplog.text


def test_transaction_failure_rolls_back_and_does_not_report_retirement_success(store, caplog):
    seed(store)
    before = snapshot(store)
    caplog.set_level(logging.INFO)

    def fail_commit(session):
        session.flush()  # Prove rollback even after both sets of writes reach SQLite.
        raise RuntimeError("synthetic transaction failure")

    event.listen(store, "before_commit", fail_commit)
    try:
        TaskRegistry().sync_from_database()
    finally:
        event.remove(store, "before_commit", fail_commit)
    assert snapshot(store) == before
    assert "Failed to sync tasks" in caplog.text
    assert "Disabled retired task" not in caplog.text
    assert "Synced 0 tasks" not in caplog.text


def test_registered_identifier_wins_over_retired_set(store, caplog):
    class RestoredExport(TaskScheduler):
        task_id = "export_publish"
        task_name = "Restored export"

        async def execute(self):
            return TaskResult(success=True)

    seed(store)
    before = snapshot(store)
    registry = TaskRegistry()
    registry.register(RestoredExport)
    registry.sync_from_database()
    assert snapshot(store) == before
    assert registry.get_task_instance("export_publish") is not None
    assert "Disabled retired task" not in caplog.text
