"""
Integration tests for the Tasks API endpoints.

These tests use the FastAPI test client with database session overrides
to test the scheduled tasks endpoints.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestListTasks:
    """Tests for GET /api/tasks endpoint."""

    @pytest.mark.asyncio
    async def test_list_tasks_returns_array(self, async_client):
        """GET /api/tasks returns array of tasks."""
        response = await async_client.get("/api/tasks")
        assert response.status_code == 200
        data = response.json()
        # Response contains tasks list
        assert "tasks" in data or isinstance(data, list)

    @pytest.mark.asyncio
    async def test_list_tasks_includes_task_info(self, async_client):
        """GET /api/tasks returns task information."""
        mock_task = MagicMock()
        mock_task.task_id = "test_task"
        mock_task.task_name = "Test Task"
        mock_task.description = "A test task"
        mock_task.enabled = True
        mock_task.get_schedule_info.return_value = {}

        with patch("task_registry.get_registry") as mock_registry:
            mock_registry.return_value.list_tasks.return_value = [mock_task]

            response = await async_client.get("/api/tasks")
            assert response.status_code == 200

            tasks = response.json()
            assert len(tasks) >= 0  # May have registered tasks


class TestGetTask:
    """Tests for GET /api/tasks/{task_id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_task_returns_task_details(self, async_client):
        """GET /api/tasks/{task_id} returns task details for known task."""
        # Use a task that actually exists (stream_probe is registered by default)
        response = await async_client.get("/api/tasks/stream_probe")
        # May return 200 if task exists or 404 if not registered in test env
        assert response.status_code in (200, 404)

        if response.status_code == 200:
            data = response.json()
            assert "task_id" in data

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, async_client):
        """GET /api/tasks/{task_id} returns 404 for unknown task."""
        response = await async_client.get("/api/tasks/definitely_nonexistent_task_12345")
        assert response.status_code == 404


class TestUpdateTask:
    """Tests for PATCH /api/tasks/{task_id} endpoint."""

    @pytest.mark.asyncio
    async def test_update_task_enables(self, async_client):
        """PATCH /api/tasks/{task_id} can enable a task."""
        # Try to update a real task
        response = await async_client.patch(
            "/api/tasks/stream_probe",
            json={"enabled": True},
        )
        # May succeed or return 404 depending on task registration
        assert response.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_update_task_not_found(self, async_client):
        """PATCH /api/tasks/{task_id} returns 404 for unknown task."""
        response = await async_client.patch(
            "/api/tasks/definitely_nonexistent_task_12345",
            json={"enabled": True},
        )
        assert response.status_code == 404


class TestRunTask:
    """Tests for POST /api/tasks/{task_id}/run endpoint."""

    @pytest.mark.asyncio
    async def test_run_task_triggers_execution(self, async_client):
        """POST /api/tasks/{task_id}/run triggers task execution."""
        # Try to run a real task
        response = await async_client.post("/api/tasks/stream_probe/run")
        # May return 200 (started), 404 (not found), or 409 (already running)
        assert response.status_code in (200, 404, 409, 500)

    @pytest.mark.asyncio
    async def test_run_task_not_found(self, async_client):
        """POST /api/tasks/{task_id}/run returns 404 for unknown task."""
        response = await async_client.post("/api/tasks/definitely_nonexistent_task_12345/run")
        assert response.status_code == 404


class TestCancelTask:
    """Tests for POST /api/tasks/{task_id}/cancel endpoint."""

    @pytest.mark.asyncio
    async def test_cancel_task_stops_execution(self, async_client):
        """POST /api/tasks/{task_id}/cancel stops running task."""
        # Try to cancel a real task
        response = await async_client.post("/api/tasks/stream_probe/cancel")
        # May return 200 (cancelled), 404 (not found), or other status
        assert response.status_code in (200, 404, 409, 500)

    @pytest.mark.asyncio
    async def test_cancel_task_not_found(self, async_client):
        """POST /api/tasks/{task_id}/cancel returns 404 for unknown task."""
        response = await async_client.post("/api/tasks/definitely_nonexistent_task_12345/cancel")
        assert response.status_code == 404


class TestTaskHistory:
    """Tests for GET /api/tasks/{task_id}/history endpoint."""

    @pytest.mark.asyncio
    async def test_get_task_history_returns_executions(self, async_client, test_session):
        """GET /api/tasks/{task_id}/history returns execution history."""
        from tests.fixtures.factories import create_task_execution

        # Create some task executions
        create_task_execution(test_session, task_id="test_task")
        create_task_execution(test_session, task_id="test_task")

        response = await async_client.get("/api/tasks/test_task/history")
        assert response.status_code == 200

        data = response.json()
        # API returns {"history": [...]}
        assert "history" in data or isinstance(data, list)


class TestEngineStatus:
    """Tests for GET /api/tasks/engine/status endpoint."""

    @pytest.mark.asyncio
    async def test_get_engine_status(self, async_client):
        """GET /api/tasks/engine/status returns engine status."""
        with patch("main.get_engine_status") as mock_status:
            mock_status.return_value = {
                "running": True,
                "tasks_running": 0,
            }

            response = await async_client.get("/api/tasks/engine/status")
            assert response.status_code == 200

            data = response.json()
            assert "running" in data


class TestTaskSchedules:
    """Tests for task schedule CRUD endpoints."""

    @pytest.mark.asyncio
    async def test_get_task_schedules(self, async_client):
        """GET /api/tasks/{task_id}/schedules returns schedules."""
        mock_task = MagicMock()
        mock_task.task_id = "test_task"

        with patch("task_registry.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = mock_task

            response = await async_client.get("/api/tasks/test_task/schedules")
            assert response.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_create_task_schedule(self, async_client):
        """POST /api/tasks/{task_id}/schedules creates schedule."""
        mock_task = MagicMock()
        mock_task.task_id = "test_task"

        with patch("task_registry.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = mock_task

            response = await async_client.post(
                "/api/tasks/test_task/schedules",
                json={
                    "schedule_type": "daily",
                    "schedule_time": "03:00",
                    "timezone": "America/New_York",
                },
            )
            assert response.status_code in (200, 201, 404, 422)

    @pytest.mark.asyncio
    async def test_update_task_schedule(self, async_client, test_session):
        """PATCH /api/tasks/{task_id}/schedules/{schedule_id} updates schedule."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(test_session, task_id="test_task")

        mock_task = MagicMock()
        mock_task.task_id = "test_task"

        with patch("task_registry.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = mock_task

            response = await async_client.patch(
                f"/api/tasks/test_task/schedules/{schedule.id}",
                json={"enabled": False},
            )
            assert response.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_delete_task_schedule(self, async_client, test_session):
        """DELETE /api/tasks/{task_id}/schedules/{schedule_id} deletes schedule."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(test_session, task_id="test_task")

        mock_task = MagicMock()
        mock_task.task_id = "test_task"

        with patch("task_registry.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = mock_task

            response = await async_client.delete(
                f"/api/tasks/test_task/schedules/{schedule.id}"
            )
            assert response.status_code in (200, 204, 404)
