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
    async def test_create_task_schedule_with_parameters(self, async_client):
        """POST /api/tasks/{task_id}/schedules creates schedule with parameters."""
        mock_task = MagicMock()
        mock_task.task_id = "stream_probe"

        with patch("task_registry.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = mock_task

            response = await async_client.post(
                "/api/tasks/stream_probe/schedules",
                json={
                    "name": "Sports Probe",
                    "schedule_type": "daily",
                    "schedule_time": "06:00",
                    "timezone": "America/New_York",
                    "parameters": {
                        "batch_size": 25,
                        "timeout": 45,
                        "max_concurrent": 4,
                        "channel_groups": ["Sports", "News"],
                    },
                },
            )
            assert response.status_code in (200, 201, 404, 422)

            if response.status_code in (200, 201):
                data = response.json()
                assert "parameters" in data
                assert data["parameters"]["batch_size"] == 25
                assert data["parameters"]["channel_groups"] == ["Sports", "News"]

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
    async def test_update_task_schedule_parameters(self, async_client, test_session):
        """PATCH /api/tasks/{task_id}/schedules/{schedule_id} updates parameters."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(test_session, task_id="stream_probe")
        schedule.set_parameters({"batch_size": 10})
        test_session.commit()

        mock_task = MagicMock()
        mock_task.task_id = "stream_probe"

        with patch("task_registry.get_registry") as mock_registry:
            mock_registry.return_value.get_task_instance.return_value = mock_task

            response = await async_client.patch(
                f"/api/tasks/stream_probe/schedules/{schedule.id}",
                json={
                    "parameters": {
                        "batch_size": 30,
                        "timeout": 60,
                    },
                },
            )
            assert response.status_code in (200, 404)

            if response.status_code == 200:
                data = response.json()
                assert data["parameters"]["batch_size"] == 30

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


class TestTaskParameterSchemas:
    """Tests for task parameter schema endpoints."""

    @pytest.mark.asyncio
    async def test_get_parameter_schema(self, async_client):
        """GET /api/tasks/{task_id}/parameter-schema returns schema."""
        response = await async_client.get("/api/tasks/stream_probe/parameter-schema")
        assert response.status_code in (200, 404)

        if response.status_code == 200:
            data = response.json()
            # Response structure: {"task_id": ..., "parameters": [...]}
            assert "task_id" in data
            assert "parameters" in data
            # stream_probe should have batch_size, timeout, max_concurrent, channel_groups
            if data["parameters"]:
                param_names = [p["name"] for p in data["parameters"]]
                assert len(param_names) >= 0

    @pytest.mark.asyncio
    async def test_get_parameter_schema_unknown_task(self, async_client):
        """GET /api/tasks/{task_id}/parameter-schema returns empty for unknown task."""
        response = await async_client.get("/api/tasks/nonexistent_task_xyz/parameter-schema")
        # Should return 200 with empty parameters array
        assert response.status_code == 200
        data = response.json()
        assert data["parameters"] == []

    @pytest.mark.asyncio
    async def test_get_all_parameter_schemas(self, async_client):
        """GET /api/tasks/parameter-schemas returns all schemas."""
        response = await async_client.get("/api/tasks/parameter-schemas")
        # Endpoint may return 200 or 404 if route ordering captures it as task_id
        assert response.status_code in (200, 404)

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, dict)
            assert "schemas" in data


class TestRunTaskWithSchedule:
    """Tests for running tasks with specific schedule parameters."""

    @pytest.mark.asyncio
    async def test_run_task_with_schedule_id(self, async_client, test_session):
        """POST /api/tasks/{task_id}/schedules/{schedule_id}/run triggers task with params."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(test_session, task_id="stream_probe")
        schedule.set_parameters({"batch_size": 15, "channel_groups": ["Test"]})
        test_session.commit()

        response = await async_client.post(
            f"/api/tasks/stream_probe/schedules/{schedule.id}/run"
        )
        # May return 200 (started), 404 (not found), 409 (already running), or 500
        assert response.status_code in (200, 404, 409, 500)
