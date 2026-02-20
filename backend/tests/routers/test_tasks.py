"""
Unit tests for task/cron/schedule endpoints.

Tests: 16 task endpoints covering task listing, get, update, run, cancel,
       history, engine status, parameter schemas, cron presets/validation,
       and schedule CRUD.
Mocks: task_registry, task_engine, cron_parser, schedule_calculator,
       get_session() (via conftest) for ScheduledTask/TaskSchedule models.

NOTE: Routes /api/tasks/engine/status, /api/tasks/history/all, and
/api/tasks/parameter-schemas are shadowed by /api/tasks/{task_id} in the
monolith (they're defined after the parameterized route).
"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from models import ScheduledTask, TaskSchedule


def _create_scheduled_task(session, task_id="stream_probe", **overrides):
    """Insert a ScheduledTask record for testing."""
    defaults = {
        "task_id": task_id,
        "task_name": "Stream Probe",
        "description": "Probe stream health",
        "enabled": True,
        "schedule_type": "manual",
    }
    defaults.update(overrides)
    record = ScheduledTask(**defaults)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def _create_task_schedule(session, task_id="stream_probe", **overrides):
    """Insert a TaskSchedule record for testing."""
    defaults = {
        "task_id": task_id,
        "enabled": True,
        "schedule_type": "daily",
        "schedule_time": "03:00",
        "timezone": "UTC",
    }
    defaults.update(overrides)
    record = TaskSchedule(**defaults)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


class TestListTasks:
    """Tests for GET /api/tasks."""

    @pytest.mark.asyncio
    async def test_returns_tasks(self, async_client, test_session):
        """Returns all registered tasks with schedules."""
        _create_scheduled_task(test_session, task_id="stream_probe")

        mock_registry = MagicMock()
        mock_registry.get_all_task_statuses.return_value = [
            {"task_id": "stream_probe", "status": "idle", "task_name": "Stream Probe"},
        ]

        mock_describe = MagicMock(return_value="Every day at 03:00 UTC")

        with patch("task_registry.get_registry", return_value=mock_registry), \
             patch("schedule_calculator.describe_schedule", mock_describe):
            response = await async_client.get("/api/tasks")

        assert response.status_code == 200
        data = response.json()
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["task_id"] == "stream_probe"


class TestGetTask:
    """Tests for GET /api/tasks/{task_id}."""

    @pytest.mark.asyncio
    async def test_returns_task(self, async_client, test_session):
        """Returns status for a specific task."""
        _create_scheduled_task(test_session, task_id="stream_probe")

        mock_registry = MagicMock()
        mock_registry.get_task_status.return_value = {
            "task_id": "stream_probe", "status": "idle",
        }

        mock_describe = MagicMock(return_value="Daily at 03:00 UTC")

        with patch("task_registry.get_registry", return_value=mock_registry), \
             patch("schedule_calculator.describe_schedule", mock_describe):
            response = await async_client.get("/api/tasks/stream_probe")

        assert response.status_code == 200
        assert response.json()["task_id"] == "stream_probe"

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown(self, async_client):
        """Returns 404 for unknown task."""
        mock_registry = MagicMock()
        mock_registry.get_task_status.return_value = None

        with patch("task_registry.get_registry", return_value=mock_registry):
            response = await async_client.get("/api/tasks/nonexistent")

        assert response.status_code == 404


class TestUpdateTask:
    """Tests for PATCH /api/tasks/{task_id}."""

    @pytest.mark.asyncio
    async def test_updates_task(self, async_client):
        """Updates task configuration."""
        mock_registry = MagicMock()
        mock_registry.update_task_config.return_value = {
            "task_id": "stream_probe", "enabled": False,
        }

        with patch("task_registry.get_registry", return_value=mock_registry):
            response = await async_client.patch("/api/tasks/stream_probe", json={
                "enabled": False,
            })

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown(self, async_client):
        """Returns 404 when task not found."""
        mock_registry = MagicMock()
        mock_registry.update_task_config.return_value = None

        with patch("task_registry.get_registry", return_value=mock_registry):
            response = await async_client.patch("/api/tasks/nonexistent", json={
                "enabled": False,
            })

        assert response.status_code == 404


class TestRunTask:
    """Tests for POST /api/tasks/{task_id}/run."""

    @pytest.mark.asyncio
    async def test_runs_task(self, async_client):
        """Manually triggers a task execution."""
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "task_id": "stream_probe", "status": "completed",
            "success": True, "message": "Done",
        }

        mock_engine = MagicMock()
        mock_engine.run_task = AsyncMock(return_value=mock_result)

        with patch("task_engine.get_engine", return_value=mock_engine):
            response = await async_client.post("/api/tasks/stream_probe/run")

        assert response.status_code == 200
        mock_engine.run_task.assert_called_once_with("stream_probe", schedule_id=None)

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown(self, async_client):
        """Returns 404 when task not found."""
        mock_engine = MagicMock()
        mock_engine.run_task = AsyncMock(return_value=None)

        with patch("task_engine.get_engine", return_value=mock_engine):
            response = await async_client.post("/api/tasks/nonexistent/run")

        assert response.status_code == 404


class TestCancelTask:
    """Tests for POST /api/tasks/{task_id}/cancel."""

    @pytest.mark.asyncio
    async def test_cancels_task(self, async_client):
        """Cancels a running task."""
        mock_engine = MagicMock()
        mock_engine.cancel_task = AsyncMock(return_value={"status": "cancelled"})

        with patch("task_engine.get_engine", return_value=mock_engine):
            response = await async_client.post("/api/tasks/stream_probe/cancel")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(self, async_client):
        """Returns 404 when task not found."""
        mock_engine = MagicMock()
        mock_engine.cancel_task = AsyncMock(return_value={
            "status": "not_found", "message": "Task not found",
        })

        with patch("task_engine.get_engine", return_value=mock_engine):
            response = await async_client.post("/api/tasks/nonexistent/cancel")

        assert response.status_code == 404


class TestGetTaskHistory:
    """Tests for GET /api/tasks/{task_id}/history."""

    @pytest.mark.asyncio
    async def test_returns_history(self, async_client):
        """Returns execution history for a task."""
        mock_engine = MagicMock()
        mock_engine.get_task_history.return_value = [
            {"task_id": "stream_probe", "status": "completed"},
        ]

        with patch("task_engine.get_engine", return_value=mock_engine):
            response = await async_client.get("/api/tasks/stream_probe/history")

        assert response.status_code == 200
        data = response.json()
        assert len(data["history"]) == 1


class TestEngineStatus:
    """Tests for GET /api/tasks/engine/status."""

    @pytest.mark.asyncio
    async def test_returns_status(self, async_client):
        """Returns task engine status."""
        mock_engine = MagicMock()
        mock_engine.get_status.return_value = {"running": True, "tasks": 5}

        with patch("task_engine.get_engine", return_value=mock_engine):
            response = await async_client.get("/api/tasks/engine/status")

        assert response.status_code == 200
        assert response.json()["running"] is True


class TestAllTaskHistory:
    """Tests for GET /api/tasks/history/all."""

    @pytest.mark.asyncio
    async def test_returns_all_history(self, async_client):
        """Returns execution history for all tasks."""
        mock_engine = MagicMock()
        mock_engine.get_task_history.return_value = [
            {"task_id": "stream_probe", "status": "completed"},
            {"task_id": "epg_refresh", "status": "completed"},
        ]

        with patch("task_engine.get_engine", return_value=mock_engine):
            response = await async_client.get("/api/tasks/history/all")

        assert response.status_code == 200
        data = response.json()
        assert len(data["history"]) == 2


class TestGetParameterSchema:
    """Tests for GET /api/tasks/{task_id}/parameter-schema."""

    @pytest.mark.asyncio
    async def test_returns_schema(self, async_client):
        """Returns parameter schema for a known task type."""
        response = await async_client.get("/api/tasks/stream_probe/parameter-schema")

        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "stream_probe"
        assert len(data["parameters"]) > 0

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown(self, async_client):
        """Returns empty schema for unknown task type."""
        response = await async_client.get("/api/tasks/unknown_task/parameter-schema")

        assert response.status_code == 200
        data = response.json()
        assert data["parameters"] == []


class TestGetAllParameterSchemas:
    """Tests for GET /api/tasks/parameter-schemas.

    NOTE: Route ordering was fixed during extraction to routers/tasks.py.
    Non-parameterized routes are now defined before /{task_id}, so
    /api/tasks/parameter-schemas is no longer shadowed.
    """

    @pytest.mark.asyncio
    async def test_returns_all_schemas(self, async_client):
        """Returns all parameter schemas."""
        response = await async_client.get("/api/tasks/parameter-schemas")

        assert response.status_code == 200
        data = response.json()
        assert "schemas" in data
        assert "stream_probe" in data["schemas"]


class TestCronPresets:
    """Tests for GET /api/cron/presets."""

    @pytest.mark.asyncio
    async def test_returns_presets(self, async_client):
        """Returns cron presets."""
        mock_presets = [
            {"name": "Every hour", "expression": "0 * * * *"},
        ]

        with patch("cron_parser.get_preset_list", return_value=mock_presets):
            response = await async_client.get("/api/cron/presets")

        assert response.status_code == 200
        data = response.json()
        assert len(data["presets"]) == 1


class TestCronValidate:
    """Tests for POST /api/cron/validate."""

    @pytest.mark.asyncio
    async def test_validates_valid_expression(self, async_client):
        """Validates a valid cron expression."""
        with patch("cron_parser.validate_cron_expression", return_value=(True, None)), \
             patch("cron_parser.describe_cron_expression", return_value="Every hour"), \
             patch("cron_parser.get_next_n_run_times", return_value=[
                 datetime(2024, 6, 15, 13, 0, 0),
             ]):
            response = await async_client.post("/api/cron/validate", json={
                "expression": "0 * * * *",
            })

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["description"] == "Every hour"

    @pytest.mark.asyncio
    async def test_rejects_invalid_expression(self, async_client):
        """Rejects an invalid cron expression."""
        with patch("cron_parser.validate_cron_expression", return_value=(False, "Bad format")):
            response = await async_client.post("/api/cron/validate", json={
                "expression": "invalid",
            })

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert data["error"] == "Bad format"


class TestListTaskSchedules:
    """Tests for GET /api/tasks/{task_id}/schedules."""

    @pytest.mark.asyncio
    async def test_returns_schedules(self, async_client, test_session):
        """Returns schedules for a task."""
        _create_scheduled_task(test_session, task_id="stream_probe")
        _create_task_schedule(test_session, task_id="stream_probe", name="Morning Probe")

        mock_describe = MagicMock(return_value="Daily at 03:00 UTC")

        with patch("schedule_calculator.describe_schedule", mock_describe), \
             patch("routers.tasks.get_client", return_value=None):
            response = await async_client.get("/api/tasks/stream_probe/schedules")

        assert response.status_code == 200
        data = response.json()
        assert len(data["schedules"]) == 1

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_task(self, async_client):
        """Returns 404 when task not found."""
        response = await async_client.get("/api/tasks/nonexistent/schedules")

        assert response.status_code == 404


class TestCreateTaskSchedule:
    """Tests for POST /api/tasks/{task_id}/schedules."""

    @pytest.mark.asyncio
    async def test_creates_schedule(self, async_client, test_session):
        """Creates a new schedule for a task."""
        _create_scheduled_task(test_session, task_id="stream_probe")

        mock_describe = MagicMock(return_value="Daily at 06:00 UTC")
        mock_calc = MagicMock(return_value=datetime(2024, 6, 16, 6, 0, 0))

        with patch("schedule_calculator.describe_schedule", mock_describe), \
             patch("schedule_calculator.calculate_next_run", mock_calc):
            response = await async_client.post("/api/tasks/stream_probe/schedules", json={
                "schedule_type": "daily",
                "schedule_time": "06:00",
            })

        assert response.status_code == 200
        data = response.json()
        assert data["schedule_type"] == "daily"
        assert data["schedule_time"] == "06:00"

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_task(self, async_client):
        """Returns 404 when task not found."""
        response = await async_client.post("/api/tasks/nonexistent/schedules", json={
            "schedule_type": "daily",
            "schedule_time": "06:00",
        })

        assert response.status_code == 404


class TestUpdateTaskSchedule:
    """Tests for PATCH /api/tasks/{task_id}/schedules/{schedule_id}."""

    @pytest.mark.asyncio
    async def test_updates_schedule(self, async_client, test_session):
        """Updates a task schedule."""
        _create_scheduled_task(test_session, task_id="stream_probe")
        schedule = _create_task_schedule(test_session, task_id="stream_probe")

        mock_describe = MagicMock(return_value="Daily at 09:00 UTC")
        mock_calc = MagicMock(return_value=datetime(2024, 6, 16, 9, 0, 0))

        with patch("schedule_calculator.describe_schedule", mock_describe), \
             patch("schedule_calculator.calculate_next_run", mock_calc):
            response = await async_client.patch(
                f"/api/tasks/stream_probe/schedules/{schedule.id}",
                json={"schedule_time": "09:00"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["schedule_time"] == "09:00"

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown(self, async_client, test_session):
        """Returns 404 when schedule not found."""
        _create_scheduled_task(test_session, task_id="stream_probe")

        response = await async_client.patch(
            "/api/tasks/stream_probe/schedules/999",
            json={"schedule_time": "09:00"},
        )

        assert response.status_code == 404


class TestDeleteTaskSchedule:
    """Tests for DELETE /api/tasks/{task_id}/schedules/{schedule_id}."""

    @pytest.mark.asyncio
    async def test_deletes_schedule(self, async_client, test_session):
        """Deletes a task schedule."""
        _create_scheduled_task(test_session, task_id="stream_probe")
        schedule = _create_task_schedule(test_session, task_id="stream_probe")

        response = await async_client.delete(
            f"/api/tasks/stream_probe/schedules/{schedule.id}",
        )

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

        # Verify deleted from DB
        remaining = test_session.query(TaskSchedule).filter_by(id=schedule.id).first()
        assert remaining is None

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown(self, async_client, test_session):
        """Returns 404 when schedule not found."""
        _create_scheduled_task(test_session, task_id="stream_probe")

        response = await async_client.delete("/api/tasks/stream_probe/schedules/999")

        assert response.status_code == 404
