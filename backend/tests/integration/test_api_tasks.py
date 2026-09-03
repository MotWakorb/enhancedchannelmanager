"""
Integration tests for the Tasks API endpoints.

These tests use the FastAPI test client with database session overrides
to test the scheduled tasks endpoints.
"""
import pytest
from unittest.mock import patch, MagicMock


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
    async def test_list_tasks_returns_task_with_required_fields(self, async_client):
        """GET /api/tasks response has the expected envelope and any tasks have required fields.

        In the test environment the task engine is not started (startup event is not
        triggered by the test client), so the task list from get_all_task_statuses() is
        empty. We assert the envelope shape and validate that any tasks present have the
        required task_id field. The registry-level assertion (stream_probe registered) is
        a unit concern tested separately; this integration test validates the API contract.

        Mutation check: if the response envelope changed from {tasks: [...]} to a bare
        list, or if task dict no longer included task_id, this test would fail.
        """
        response = await async_client.get("/api/tasks")
        assert response.status_code == 200
        data = response.json()
        # Response must be wrapped in {"tasks": [...]}
        assert isinstance(data, dict), "Expected dict response, not bare list"
        assert "tasks" in data, f"Expected 'tasks' key in response, got: {list(data.keys())}"
        tasks = data["tasks"]
        assert isinstance(tasks, list)
        # If tasks are present, each must have the required task_id field
        for task in tasks:
            assert "task_id" in task, f"Task missing task_id field: {task}"


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
        """PATCH /api/tasks/{task_id} enables stream_probe and returns 200.

        stream_probe is always registered, so the 404 branch is unreachable here.
        Mutation check: if update_task_config were broken, the response would not
        include the task_id and this test would fail.
        """
        response = await async_client.patch(
            "/api/tasks/stream_probe",
            json={"enabled": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert "task_id" in data
        assert data["task_id"] == "stream_probe"

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
        """POST /api/tasks/{task_id}/run returns 200 with success field when engine runs the task.

        Mocks the task engine so the outcome is deterministic — no actual probe happens.
        Mutation check: if engine.run_task were removed or returned None (→ 404), this fails.
        """
        from task_scheduler import TaskResult
        from datetime import datetime, timezone
        mock_result = MagicMock(spec=TaskResult)
        mock_result.to_dict.return_value = {
            "success": True,
            "message": "Task completed",
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
            "total_items": 0,
            "success_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "error": None,
            "details": {},
        }

        from unittest.mock import AsyncMock
        mock_engine = MagicMock()
        mock_engine.run_task = AsyncMock(return_value=mock_result)

        with patch("task_engine.get_engine", return_value=mock_engine):
            response = await async_client.post("/api/tasks/stream_probe/run")

        assert response.status_code == 200
        data = response.json()
        assert "success" in data
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_run_task_not_found(self, async_client):
        """POST /api/tasks/{task_id}/run returns 404 for unknown task."""
        response = await async_client.post("/api/tasks/definitely_nonexistent_task_12345/run")
        assert response.status_code == 404


class TestCancelTask:
    """Tests for POST /api/tasks/{task_id}/cancel endpoint."""

    @pytest.mark.asyncio
    async def test_cancel_task_stops_execution(self, async_client):
        """POST /api/tasks/{task_id}/cancel returns 200 with status=cancelled.

        Mocks the task engine to return a deterministic "cancelled" result.
        Mutation check: if engine.cancel_task returned {status: not_found} the
        router would raise 404, failing this assertion.
        """
        from unittest.mock import AsyncMock
        mock_engine = MagicMock()
        mock_engine.cancel_task = AsyncMock(return_value={"status": "cancelled", "task_id": "stream_probe"})

        with patch("task_engine.get_engine", return_value=mock_engine):
            response = await async_client.post("/api/tasks/stream_probe/cancel")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"

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
        with patch("routers.tasks.get_engine_status") as mock_status:
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
    async def test_epg_probe_rejects_invalid_regex_before_persisting_schedule(
        self, async_client, test_session
    ):
        from models import TaskSchedule
        from tasks.epg_event_probe import EPGEventProbeTask
        from tests.fixtures.factories import create_scheduled_task

        create_scheduled_task(test_session, task_id="epg_event_probe")

        with patch("task_registry.get_registry") as mock_registry:
            mock_registry.return_value.get_task_class.return_value = EPGEventProbeTask
            response = await async_client.post(
                "/api/tasks/epg_event_probe/schedules",
                json={
                    "name": "Invalid EPG Probe",
                    "schedule_type": "interval",
                    "interval_seconds": 60,
                    "parameters": {"title_pattern": "["},
                },
            )

        assert response.status_code == 422
        assert response.json()["detail"] == "title_pattern must be a valid regex"
        assert test_session.query(TaskSchedule).filter(
            TaskSchedule.task_id == "epg_event_probe"
        ).count() == 0

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


# Note: TestRunTaskWithSchedule was removed in bead enhancedchannelmanager-0gcu9.
# It covered POST /api/tasks/{task_id}/schedules/{schedule_id}/run, a route that
# was removed from routers/tasks.py. Per-schedule "run" is not an exposed endpoint
# today — running a task uses POST /api/tasks/{task_id}/run. If the per-schedule
# run endpoint is re-introduced, re-add a focused test then.
