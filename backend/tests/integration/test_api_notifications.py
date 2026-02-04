"""
Integration tests for the Notifications API endpoints.

These tests use the FastAPI test client with database session overrides
to test the notification endpoints.
"""
import pytest
from datetime import datetime


class TestListNotifications:
    """Tests for GET /api/notifications endpoint."""

    @pytest.mark.asyncio
    async def test_list_returns_notifications(self, async_client):
        """GET /api/notifications returns notification list."""
        response = await async_client.get("/api/notifications")
        assert response.status_code == 200

        data = response.json()
        assert "notifications" in data
        assert "total" in data
        assert "unread_count" in data

    @pytest.mark.asyncio
    async def test_list_includes_created_notifications(self, async_client, test_session):
        """GET /api/notifications includes created notifications."""
        from tests.fixtures.factories import create_notification

        create_notification(test_session, title="Test 1", message="Message 1")
        create_notification(test_session, title="Test 2", message="Message 2")

        response = await async_client.get("/api/notifications")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] >= 2

    @pytest.mark.asyncio
    async def test_list_filters_unread_only(self, async_client, test_session):
        """GET /api/notifications?unread_only=true filters unread."""
        from tests.fixtures.factories import create_notification

        create_notification(test_session, title="Unread", read=False)
        create_notification(test_session, title="Read", read=True)

        response = await async_client.get("/api/notifications?unread_only=true")
        assert response.status_code == 200

        data = response.json()
        for notification in data["notifications"]:
            assert notification["read"] is False

    @pytest.mark.asyncio
    async def test_list_filters_by_type(self, async_client, test_session):
        """GET /api/notifications?notification_type=error filters by type."""
        from tests.fixtures.factories import create_notification

        create_notification(test_session, type="info", message="Info")
        create_notification(test_session, type="error", message="Error")

        response = await async_client.get("/api/notifications?notification_type=error")
        assert response.status_code == 200

        data = response.json()
        for notification in data["notifications"]:
            assert notification["type"] == "error"

    @pytest.mark.asyncio
    async def test_list_paginates(self, async_client, test_session):
        """GET /api/notifications supports pagination."""
        from tests.fixtures.factories import create_notification

        # Create 15 notifications
        for i in range(15):
            create_notification(test_session, title=f"Notification {i}", message=f"Message {i}")

        # Get first page
        response = await async_client.get("/api/notifications?page=1&page_size=10")
        assert response.status_code == 200

        data = response.json()
        assert len(data["notifications"]) == 10
        assert data["total"] >= 15

        # Get second page
        response = await async_client.get("/api/notifications?page=2&page_size=10")
        assert response.status_code == 200

        data = response.json()
        assert len(data["notifications"]) >= 5

    @pytest.mark.asyncio
    async def test_list_returns_unread_count(self, async_client, test_session):
        """GET /api/notifications returns accurate unread_count."""
        from tests.fixtures.factories import create_notification

        create_notification(test_session, read=False)
        create_notification(test_session, read=False)
        create_notification(test_session, read=True)

        response = await async_client.get("/api/notifications")
        assert response.status_code == 200

        data = response.json()
        assert data["unread_count"] >= 2


class TestCreateNotification:
    """Tests for POST /api/notifications endpoint."""

    @pytest.mark.asyncio
    async def test_create_notification(self, async_client):
        """POST /api/notifications creates notification."""
        response = await async_client.post(
            "/api/notifications",
            json={
                "notification_type": "info",
                "title": "Test Notification",
                "message": "This is a test message",
            },
        )
        assert response.status_code in (200, 201)

        data = response.json()
        assert data["title"] == "Test Notification"
        assert data["message"] == "This is a test message"

    @pytest.mark.asyncio
    async def test_create_notification_requires_message(self, async_client):
        """POST /api/notifications requires message field."""
        response = await async_client.post(
            "/api/notifications",
            json={
                "notification_type": "info",
                "title": "No Message",
                "message": "",  # Empty message should fail
            },
        )
        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_create_notification_validates_type(self, async_client):
        """POST /api/notifications validates notification type."""
        response = await async_client.post(
            "/api/notifications",
            json={
                "notification_type": "invalid_type",
                "message": "Test",
            },
        )
        # Should reject invalid type
        assert response.status_code in (400, 422)


class TestMarkAllRead:
    """Tests for PATCH /api/notifications/mark-all-read endpoint."""

    @pytest.mark.asyncio
    async def test_mark_all_read(self, async_client, test_session):
        """PATCH /api/notifications/mark-all-read marks all as read."""
        from tests.fixtures.factories import create_notification

        create_notification(test_session, read=False)
        create_notification(test_session, read=False)
        create_notification(test_session, read=False)

        response = await async_client.patch("/api/notifications/mark-all-read")
        assert response.status_code == 200

        data = response.json()
        # API returns {"marked_read": count}
        assert "marked_read" in data
        assert data["marked_read"] >= 3

    @pytest.mark.asyncio
    async def test_mark_all_read_updates_count(self, async_client, test_session):
        """PATCH /api/notifications/mark-all-read updates unread_count."""
        from tests.fixtures.factories import create_notification

        create_notification(test_session, read=False)
        create_notification(test_session, read=False)

        # Mark all read
        await async_client.patch("/api/notifications/mark-all-read")

        # Check unread count is 0
        response = await async_client.get("/api/notifications")
        data = response.json()
        assert data["unread_count"] == 0


class TestUpdateNotification:
    """Tests for PATCH /api/notifications/{notification_id} endpoint."""

    @pytest.mark.asyncio
    async def test_mark_single_as_read(self, async_client, test_session):
        """PATCH /api/notifications/{id} marks single notification as read."""
        from tests.fixtures.factories import create_notification

        notification = create_notification(test_session, read=False)

        # API uses query params, not JSON body
        response = await async_client.patch(
            f"/api/notifications/{notification.id}",
            params={"read": "true"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["read"] is True

    @pytest.mark.asyncio
    async def test_mark_single_as_unread(self, async_client, test_session):
        """PATCH /api/notifications/{id} can mark as unread."""
        from tests.fixtures.factories import create_notification

        notification = create_notification(test_session, read=True)

        # API uses query params, not JSON body
        response = await async_client.patch(
            f"/api/notifications/{notification.id}",
            params={"read": "false"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["read"] is False

    @pytest.mark.asyncio
    async def test_update_notification_not_found(self, async_client):
        """PATCH /api/notifications/{id} returns 404 for unknown ID."""
        response = await async_client.patch(
            "/api/notifications/99999",
            params={"read": "true"},
        )
        assert response.status_code == 404


class TestDeleteNotification:
    """Tests for DELETE /api/notifications/{notification_id} endpoint."""

    @pytest.mark.asyncio
    async def test_delete_single_notification(self, async_client, test_session):
        """DELETE /api/notifications/{id} deletes notification."""
        from tests.fixtures.factories import create_notification

        notification = create_notification(test_session, title="To Delete")
        notification_id = notification.id

        response = await async_client.delete(f"/api/notifications/{notification_id}")
        assert response.status_code in (200, 204)

        # Verify deleted
        response = await async_client.patch(
            f"/api/notifications/{notification_id}",
            json={"read": True},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_notification_not_found(self, async_client):
        """DELETE /api/notifications/{id} returns 404 for unknown ID."""
        response = await async_client.delete("/api/notifications/99999")
        assert response.status_code == 404


class TestDeleteAllNotifications:
    """Tests for DELETE /api/notifications endpoint."""

    @pytest.mark.asyncio
    async def test_delete_all_notifications(self, async_client, test_session):
        """DELETE /api/notifications deletes all notifications."""
        from tests.fixtures.factories import create_notification

        create_notification(test_session)
        create_notification(test_session)
        create_notification(test_session)

        # API defaults to read_only=True, pass read_only=false to delete all
        response = await async_client.delete("/api/notifications", params={"read_only": "false"})
        assert response.status_code in (200, 204)

        # Verify all deleted
        response = await async_client.get("/api/notifications")
        data = response.json()
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_delete_all_returns_count(self, async_client, test_session):
        """DELETE /api/notifications returns deleted count."""
        from tests.fixtures.factories import create_notification

        create_notification(test_session)
        create_notification(test_session)

        # API defaults to read_only=True, pass read_only=false to delete all
        response = await async_client.delete("/api/notifications", params={"read_only": "false"})
        assert response.status_code in (200, 204)

        if response.status_code == 200:
            data = response.json()
            # API returns {"deleted": count, "read_only": bool}
            assert "deleted" in data
            assert data["deleted"] >= 2
