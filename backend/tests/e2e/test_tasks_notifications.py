"""
E2E tests for task, cron, notification, and alert method endpoints.

Endpoints: /api/tasks/*, /api/cron/*, /api/notifications/*, /api/alert-methods/*
"""
from tests.e2e.conftest import skip_if_not_api


class TestTasks:
    """Tests for /api/tasks endpoints."""

    def test_list_tasks(self, e2e_client):
        """GET /api/tasks returns task list."""
        response = e2e_client.get("/api/tasks")
        assert response.status_code == 200
        data = response.json()
        assert "tasks" in data

    def test_engine_status(self, e2e_client):
        """GET /api/tasks/engine/status returns engine status."""
        response = e2e_client.get("/api/tasks/engine/status")
        assert response.status_code == 200

    def test_history_all(self, e2e_client):
        """GET /api/tasks/history/all returns execution history."""
        response = e2e_client.get("/api/tasks/history/all")
        assert response.status_code == 200


class TestCron:
    """Tests for /api/cron endpoints."""

    def test_validate_cron(self, e2e_client):
        """POST /api/cron/validate validates a cron expression."""
        response = e2e_client.post("/api/cron/validate", json={
            "expression": "0 */6 * * *",
        })
        assert response.status_code == 200

    def test_cron_presets(self, e2e_client):
        """GET /api/cron/presets returns cron presets."""
        response = e2e_client.get("/api/cron/presets")
        skip_if_not_api(response)
        assert response.status_code == 200


class TestNotifications:
    """Tests for /api/notifications endpoints."""

    def test_list_notifications(self, e2e_client):
        """GET /api/notifications returns notifications."""
        response = e2e_client.get("/api/notifications")
        skip_if_not_api(response)
        assert response.status_code == 200

    def test_mark_all_read(self, e2e_client):
        """PATCH /api/notifications/mark-all-read marks all as read."""
        response = e2e_client.patch("/api/notifications/mark-all-read")
        skip_if_not_api(response)
        assert response.status_code == 200


class TestAlertMethods:
    """Tests for /api/alert-methods endpoints."""

    def test_list_alert_methods(self, e2e_client):
        """GET /api/alert-methods returns alert methods."""
        response = e2e_client.get("/api/alert-methods")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_alert_types(self, e2e_client):
        """GET /api/alert-methods/types returns alert types."""
        response = e2e_client.get("/api/alert-methods/types")
        assert response.status_code == 200
