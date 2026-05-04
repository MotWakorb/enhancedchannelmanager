"""TaskRegistry must persist `enabled` when saving an existing ScheduledTask row."""

import pytest

from task_registry import TaskRegistry
from models import ScheduledTask
from tasks.yaml_backup import YamlBackupTask


def test_sync_to_database_persists_enabled_for_existing_task(test_session, monkeypatch):
    """
    Enabling a task in memory and calling sync_to_database must update the DB.
    Regression: _save_task_to_db used to reset instance._enabled from the old row
    and never wrote enabled back, so tasks with default_enabled=False (e.g. yaml_backup)
    could not be enabled from the UI.
    """
    monkeypatch.setattr("task_registry.get_session", lambda: test_session)

    from tests.fixtures.factories import create_scheduled_task

    create_scheduled_task(
        test_session,
        task_id="yaml_backup",
        task_name="YAML Backup",
        description="x",
        enabled=False,
        schedule_type="manual",
    )

    reg = TaskRegistry()
    reg.register(YamlBackupTask)
    inst = YamlBackupTask()
    inst.enable()
    reg._instances["yaml_backup"] = inst

    reg.sync_to_database("yaml_backup")

    row = test_session.query(ScheduledTask).filter_by(task_id="yaml_backup").first()
    assert row is not None
    assert row.enabled is True
    assert inst._enabled is True
