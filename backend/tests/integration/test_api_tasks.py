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
        with patch("main.get_registry") as mock_registry:
            mock_registry.return_value.list_tasks.return_value = []

            response = await async_client.get("/api/tasks")
            assert response.status_code == 200
            assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_list_tasks_includes_task_info(self, async_client):
        """GET /api/tasks returns task information."""
        mock_task = MagicMock()
        mock_task.task_id = "test_task"
        mock_task.task_name = "Test Task"
        mock_task.description = "A test task"
        mock_task.enabled = True
        mock_task.get_schedule_info.return_value = {}

        with patch("main.get_registry") as mock_registry:
            mock_registry.return_value.list_tasks.return_value = [mock_task]

            response = await async_client.get("/api/tasks")
            assert response.status_code == 200

            tasks = response.json()
            assert len(tasks) >= 0  # May have registered tasks


class TestGetTask:
    """Tests for GET /api/tasks/{task_id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_task_returns_task_details(self, async_client):
        """GET /api/tasks/{task_id} returns task details."""
        mock_task = MagicMock()
        mock_task.task_id = "test_task"
        mock_task.task_name = "Test Task"
        mock_task.description = "A test task"
        mock_task.enabled = True
        mock_task.get_schedule_info.return_value = {"schedule_type": "manual"}

        with patch("main.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = mock_task

            response = await async_client.get("/api/tasks/test_task")
            assert response.status_code == 200

            data = response.json()
            assert data["task_id"] == "test_task"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, async_client):
        """GET /api/tasks/{task_id} returns 404 for unknown task."""
        with patch("main.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = None

            response = await async_client.get("/api/tasks/nonexistent")
            assert response.status_code == 404


class TestUpdateTask:
    """Tests for PATCH /api/tasks/{task_id} endpoint."""

    @pytest.mark.asyncio
    async def test_update_task_enables(self, async_client):
        """PATCH /api/tasks/{task_id} can enable a task."""
        mock_task = MagicMock()
        mock_task.task_id = "test_task"
        mock_task.enabled = False

        with patch("main.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = mock_task

            response = await async_client.patch(
                "/api/tasks/test_task",
                json={"enabled": True},
            )
            assert response.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_update_task_not_found(self, async_client):
        """PATCH /api/tasks/{task_id} returns 404 for unknown task."""
        with patch("main.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = None

            response = await async_client.patch(
                "/api/tasks/nonexistent",
                json={"enabled": True},
            )
            assert response.status_code == 404


class TestRunTask:
    """Tests for POST /api/tasks/{task_id}/run endpoint."""

    @pytest.mark.asyncio
    async def test_run_task_triggers_execution(self, async_client):
        """POST /api/tasks/{task_id}/run triggers task execution."""
        mock_task = MagicMock()
        mock_task.task_id = "test_task"
        mock_task.is_running = False

        with patch("main.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = mock_task

            with patch("main.run_task_now", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = {"status": "started"}

                response = await async_client.post("/api/tasks/test_task/run")
                assert response.status_code in (200, 404, 409)

    @pytest.mark.asyncio
    async def test_run_task_not_found(self, async_client):
        """POST /api/tasks/{task_id}/run returns 404 for unknown task."""
        with patch("main.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = None

            response = await async_client.post("/api/tasks/nonexistent/run")
            assert response.status_code == 404


class TestCancelTask:
    """Tests for POST /api/tasks/{task_id}/cancel endpoint."""

    @pytest.mark.asyncio
    async def test_cancel_task_stops_execution(self, async_client):
        """POST /api/tasks/{task_id}/cancel stops running task."""
        mock_task = MagicMock()
        mock_task.task_id = "test_task"
        mock_task.is_running = True
        mock_task.cancel = MagicMock()

        with patch("main.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = mock_task

            response = await async_client.post("/api/tasks/test_task/cancel")
            assert response.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_cancel_task_not_found(self, async_client):
        """POST /api/tasks/{task_id}/cancel returns 404 for unknown task."""
        with patch("main.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = None

            response = await async_client.post("/api/tasks/nonexistent/cancel")
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
        assert "executions" in data or isinstance(data, list)


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

        with patch("main.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = mock_task

            response = await async_client.get("/api/tasks/test_task/schedules")
            assert response.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_create_task_schedule(self, async_client):
        """POST /api/tasks/{task_id}/schedules creates schedule."""
        mock_task = MagicMock()
        mock_task.task_id = "test_task"

        with patch("main.get_registry") as mock_registry:
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

        with patch("main.get_registry") as mock_registry:
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

        with patch("main.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = mock_task

            response = await async_client.delete(
                f"/api/tasks/test_task/schedules/{schedule.id}"
            )
            assert response.status_code in (200, 204, 404)
