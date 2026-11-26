"""A DBAS backup that fails its cloud upload AND has degraded categories.

``success=False`` routes the run to the task engine's error branch, whose alert
body used to be ``result.error`` alone (``CLOUD_UPLOAD_FAILED``). The degraded
category names lived only in ``result.message`` / ``details``, so an operator
paged for the upload fixed it, re-ran, and never learned the artifact was
missing data. These tests drive the REAL engine and assert the exact body handed
to the notification dispatch.
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
from task_scheduler import ScheduleConfig, ScheduleType, TaskResult, TaskScheduler
from tasks.dbas_backup import DbasBackupTask


@pytest.fixture
def _wire_db(test_engine, monkeypatch):
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", TestSessionLocal)
    return TestSessionLocal


def _result(*, success, error=None, message="built", degraded=()):
    details = {}
    if degraded:
        details["degraded_categories"] = list(degraded)
    return TaskResult(
        success=success,
        message=message,
        error=error,
        started_at=datetime(2026, 8, 3, 3, 0, 0),
        completed_at=datetime(2026, 8, 3, 3, 0, 5),
        total_items=6,
        success_count=6 - len(degraded),
        failed_count=len(degraded),
        details=details,
    )


async def _run(_wire_db, task_cls, result):
    """Run ``task_cls`` through the real engine; return the notification kwargs."""
    task_id = task_cls.task_id
    registry = task_registry.get_registry()
    saved_cls = registry._tasks.pop(task_id, None)
    saved_inst = registry._instances.pop(task_id, None)

    async def _execute(self):
        return result

    task_cls.execute = _execute
    registry.register(task_cls)
    registry._instances[task_id] = task_cls(
        ScheduleConfig(schedule_type=ScheduleType.MANUAL)
    )

    session = _wire_db()
    try:
        session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).delete()
        session.add(ScheduledTask(
            task_id=task_id,
            task_name=task_cls.task_name,
            description="d",
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
    try:
        with patch("services.notification_service.create_notification_internal", new=notify):
            await engine._execute_task(task_id=task_id, triggered_by="test", parameters={})
    finally:
        registry.unregister(task_id)
        if saved_cls is not None:
            registry._tasks[task_id] = saved_cls
        if saved_inst is not None:
            registry._instances[task_id] = saved_inst

    assert notify.await_count == 1
    return notify.await_args.kwargs


class _Backup(DbasBackupTask):
    pass


class _OtherTask(TaskScheduler):
    task_id = "m3u_refresh"
    task_name = "Other Task"
    task_description = "stub"

    def get_config(self):
        return {}

    async def execute(self):  # replaced per run
        raise NotImplementedError


@pytest.mark.asyncio
async def test_dual_failure_body_carries_cloud_error_and_degraded_categories(_wire_db):
    kwargs = await _run(
        _wire_db, _Backup,
        _result(success=False, error="CLOUD_UPLOAD_FAILED",
                degraded=["dvr_rules", "core_settings"]),
    )
    assert kwargs["notification_type"] == "error"
    assert kwargs["title"] == "Task Failed: DBAS Backup"
    assert kwargs["message"] == (
        "CLOUD_UPLOAD_FAILED; also: 2 categories degraded - dvr_rules, core_settings"
    )


@pytest.mark.asyncio
async def test_dual_failure_singular_wording(_wire_db):
    kwargs = await _run(
        _wire_db, _Backup,
        _result(success=False, error="CLOUD_UPLOAD_PARTIAL", degraded=["dvr_rules"]),
    )
    assert kwargs["message"] == (
        "CLOUD_UPLOAD_PARTIAL; also: 1 category degraded - dvr_rules"
    )


@pytest.mark.asyncio
async def test_dual_failure_caps_the_name_list(_wire_db):
    names = ["c%d" % i for i in range(9)]
    kwargs = await _run(
        _wire_db, _Backup,
        _result(success=False, error="CLOUD_UPLOAD_FAILED", degraded=names),
    )
    assert kwargs["message"] == (
        "CLOUD_UPLOAD_FAILED; also: 9 categories degraded - c0, c1, c2, c3, c4, +4 more"
    )


@pytest.mark.asyncio
async def test_cloud_failure_only_body_is_unchanged(_wire_db):
    kwargs = await _run(
        _wire_db, _Backup, _result(success=False, error="CLOUD_UPLOAD_FAILED"),
    )
    assert kwargs["notification_type"] == "error"
    assert kwargs["message"] == "CLOUD_UPLOAD_FAILED"


@pytest.mark.asyncio
async def test_degraded_only_warning_body_is_unchanged(_wire_db):
    kwargs = await _run(
        _wire_db, _Backup, _result(success=True, degraded=["dvr_rules"]),
    )
    assert kwargs["notification_type"] == "warning"
    assert kwargs["message"] == (
        "Backup artifact built, but 1 Dispatcharr category could not be fetched "
        "from Dispatcharr and is degraded in this backup: dvr_rules. Check "
        "Dispatcharr connectivity and re-run to fill the gap."
    )


@pytest.mark.asyncio
async def test_another_tasks_error_alert_is_unchanged(_wire_db):
    kwargs = await _run(
        _wire_db, _OtherTask, _result(success=False, error="BOOM"),
    )
    assert kwargs["notification_type"] == "error"
    assert kwargs["message"] == "BOOM"


@pytest.mark.asyncio
async def test_another_task_carrying_the_same_details_key_is_unchanged(_wire_db):
    kwargs = await _run(
        _wire_db, _OtherTask,
        _result(success=False, error="BOOM", degraded=["dvr_rules"]),
    )
    assert kwargs["message"] == "BOOM"
