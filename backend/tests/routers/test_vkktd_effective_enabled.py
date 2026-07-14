"""Effective-enabled surfacing in the API + debug bundle (epic vkktd .2/.3).

vkktd.3 — GET /api/tasks (+ /{id}) expose ``effective_enabled`` = parent gate
          AND >=1 enabled child schedule, and ``enabled`` reflects the parent
          scheduled_tasks gate. A task can read enabled=True yet never fire
          (child disabled), which ``effective_enabled`` makes visible.
vkktd.2 — the debug bundle's task_schedules.json annotates every child schedule
          with ``parent_task_enabled`` and ``effective_enabled`` so a gated-OFF
          task can never read a bare "enabled: true".
"""
import io
import json
import tarfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import ScheduledTask, TaskSchedule


def _seed_scheduled_task(session, task_id, enabled):
    session.add(ScheduledTask(
        task_id=task_id, task_name=task_id, enabled=enabled, schedule_type="interval",
    ))
    session.commit()


def _seed_child(session, task_id, enabled, next_run_at=None):
    session.add(TaskSchedule(
        task_id=task_id, name="Default Schedule", enabled=enabled,
        schedule_type="interval", interval_seconds=60, next_run_at=next_run_at,
    ))
    session.commit()


# ---------------------------------------------------------------------------
# vkktd.3 — GET /api/tasks effective_enabled
# ---------------------------------------------------------------------------
class TestListTasksEffectiveEnabled:
    def _registry(self, task_id, enabled):
        reg = MagicMock()
        reg.get_all_task_statuses.return_value = [
            {"task_id": task_id, "status": "idle", "task_name": task_id, "enabled": enabled},
        ]
        return reg

    @pytest.mark.asyncio
    async def test_enabled_reflects_parent_and_effective_true_when_child_enabled(
        self, async_client, test_session
    ):
        _seed_scheduled_task(test_session, "auto_creation", enabled=True)
        _seed_child(test_session, "auto_creation", enabled=True, next_run_at=datetime(2026, 6, 3, 8, 0))
        with patch("task_registry.get_registry", return_value=self._registry("auto_creation", True)), \
             patch("schedule_calculator.describe_schedule", MagicMock(return_value="x")):
            resp = await async_client.get("/api/tasks")
        task = resp.json()["tasks"][0]
        assert task["enabled"] is True          # parent gate
        assert task["effective_enabled"] is True

    @pytest.mark.asyncio
    async def test_effective_false_when_parent_enabled_but_child_disabled(
        self, async_client, test_session
    ):
        """The sharp vkktd trap: enabled=True yet the task can never fire."""
        _seed_scheduled_task(test_session, "auto_creation", enabled=True)
        _seed_child(test_session, "auto_creation", enabled=False, next_run_at=None)
        with patch("task_registry.get_registry", return_value=self._registry("auto_creation", True)), \
             patch("schedule_calculator.describe_schedule", MagicMock(return_value="x")):
            resp = await async_client.get("/api/tasks")
        task = resp.json()["tasks"][0]
        assert task["enabled"] is True
        assert task["effective_enabled"] is False

    @pytest.mark.asyncio
    async def test_effective_false_when_parent_disabled(self, async_client, test_session):
        _seed_scheduled_task(test_session, "auto_creation", enabled=False)
        _seed_child(test_session, "auto_creation", enabled=True, next_run_at=datetime(2026, 6, 3, 8, 0))
        with patch("task_registry.get_registry", return_value=self._registry("auto_creation", False)), \
             patch("schedule_calculator.describe_schedule", MagicMock(return_value="x")):
            resp = await async_client.get("/api/tasks")
        task = resp.json()["tasks"][0]
        assert task["enabled"] is False
        assert task["effective_enabled"] is False

    @pytest.mark.asyncio
    async def test_effective_mirrors_enabled_when_no_schedules(self, async_client, test_session):
        """A task with no child schedules (manual/legacy) mirrors ``enabled`` —
        never flagged as 'won't fire' just for lacking a child row."""
        _seed_scheduled_task(test_session, "cleanup", enabled=True)
        with patch("task_registry.get_registry", return_value=self._registry("cleanup", True)), \
             patch("schedule_calculator.describe_schedule", MagicMock(return_value="x")):
            resp = await async_client.get("/api/tasks")
        task = resp.json()["tasks"][0]
        assert task["effective_enabled"] is True


# ---------------------------------------------------------------------------
# vkktd.2 — debug bundle task_schedules.json effective-enabled annotation
# ---------------------------------------------------------------------------
class TestDebugBundleEffectiveEnabled:
    @pytest.mark.asyncio
    async def test_task_schedules_json_annotates_parent_and_effective_enabled(
        self, async_client, test_session
    ):
        # auto_creation: parent DISABLED, child enabled (the reported trap).
        _seed_scheduled_task(test_session, "auto_creation", enabled=False)
        _seed_child(test_session, "auto_creation", enabled=True,
                    next_run_at=datetime(2026, 7, 4, 0, 0))
        # stream_probe: parent enabled, child enabled (the healthy case).
        _seed_scheduled_task(test_session, "stream_probe", enabled=True)
        _seed_child(test_session, "stream_probe", enabled=True,
                    next_run_at=datetime(2026, 7, 14, 3, 0))

        mock_client = AsyncMock()
        mock_client.get_channels = AsyncMock(return_value={"results": [], "next": None, "count": 0})
        mock_client.get_channel_groups = AsyncMock(return_value=[])
        mock_client.get_streams_by_ids = AsyncMock(return_value=[])
        mock_client.get_m3u_accounts = AsyncMock(return_value=[])

        with patch("routers.channel_pipeline.get_client", return_value=mock_client), \
             patch("log_utils.get_recent_logs", return_value=[]):
            enqueue = await async_client.post("/api/auto-creation/debug-bundle")
            assert enqueue.status_code == 202
            job_id = enqueue.json()["job_id"]
            import asyncio as _asyncio
            for _ in range(80):
                await _asyncio.sleep(0)
            response = await async_client.get(f"/api/auto-creation/debug-bundle/{job_id}")
            assert response.status_code == 200

        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as tf:
            schedules = json.loads(tf.extractfile("task_schedules.json").read())

        by_task = {s["task_id"]: s for s in schedules}
        ac = by_task["auto_creation"]
        # The gated-OFF task must NOT read a bare enabled:true firing gate.
        assert ac["child_schedule_enabled"] is True
        assert ac["parent_task_enabled"] is False
        assert ac["effective_enabled"] is False
        # Corroborating "never fired" signal remains visible.
        assert ac["next_run_at"] is not None
        assert ac["last_run_at"] is None

        sp = by_task["stream_probe"]
        assert sp["parent_task_enabled"] is True
        assert sp["effective_enabled"] is True
