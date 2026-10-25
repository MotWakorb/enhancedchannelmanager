"""Mapper -> real task engine -> SQLite execution -> history HTTP response."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from dbas.restore_contracts import EntityType, RestoreReport
from task_engine import TaskEngine
from task_scheduler import TaskScheduler
from tasks.dbas_sync import DbasSyncTask

from tests.integration.test_degraded_run_history_agrees_with_alert import (
    _run_task,
)
from tests.integration.test_degraded_run_history_agrees_with_alert import (
    registered_task as _registered_task,
)

registered_task = _registered_task


class _ApplyWithoutOutcome(TaskScheduler):
    task_id = "p3_apply_without_outcome"
    task_name = "P3 apply"
    default_enabled = False

    async def execute(self):
        return DbasSyncTask()._result_from_report(
            datetime.now(UTC), RestoreReport(is_dry_run=False)
        )


class _UnpredictedPreview(TaskScheduler):
    task_id = "p3_unpredicted_preview"
    task_name = "P3 preview"
    default_enabled = False

    async def execute(self):
        report = RestoreReport(is_dry_run=True)
        report.category(EntityType.CHANNEL).would_create = 2
        streams = report.category(EntityType.STREAM)
        streams.predicted = False
        streams.caveat = "Streams require destination refresh before prediction."
        return DbasSyncTask()._result_from_report(datetime.now(UTC), report)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "registered_task", [_ApplyWithoutOutcome, _UnpredictedPreview], indirect=True
)
async def test_history_keeps_mode_and_prediction_contract(
    async_client, test_engine, monkeypatch, registered_task
):
    persisted = await _run_task(test_engine, monkeypatch, registered_task)
    with patch("task_engine.get_engine", return_value=TaskEngine()):
        response = await async_client.get(
            f"/api/tasks/{registered_task.task_id}/history"
        )
    assert response.status_code == 200
    history = response.json()["history"]
    assert len(history) == 1
    record = history[0]
    assert record["message"] == persisted.execution["message"]
    if registered_task is _ApplyWithoutOutcome:
        assert record["status"] == "failed"
        assert record["details"]["outcome"] == "unknown"
        assert record["details"]["is_dry_run"] is False
    else:
        assert record["status"] == "completed"
        assert "stream: not predicted" in record["message"]
        assert "Streams require destination refresh" in record["message"]
        assert record["details"]["sync_report"]["categories"][1]["predicted"] is False
