"""
Unit tests for notification endpoints.

Tests: GET /api/notifications, POST /api/notifications,
       PATCH /api/notifications/mark-all-read, PATCH /api/notifications/{id},
       DELETE /api/notifications/{id}, DELETE /api/notifications,
       DELETE /api/notifications/by-source
Uses async_client fixture which patches database session.
"""
import pytest
from unittest.mock import patch, AsyncMock

from models import Notification


def _create_notification(session, **overrides):
    """Helper to create a Notification with sensible defaults."""
    defaults = {
        "type": "info",
        "title": "Test Notification",
        "message": "Test message",
        "read": False,
        "source": "test",
    }
    defaults.update(overrides)
    notif = Notification(**defaults)
    session.add(notif)
    session.commit()
    session.refresh(notif)
    return notif


class TestGetNotifications:
    """Tests for GET /api/notifications."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_none(self, async_client):
        """Returns empty list with pagination info."""
        response = await async_client.get("/api/notifications")
        assert response.status_code == 200
        data = response.json()
        assert data["notifications"] == []
        assert data["total"] == 0
        assert data["unread_count"] == 0

    @pytest.mark.asyncio
    async def test_returns_notifications(self, async_client, test_session):
        """Returns created notifications."""
        _create_notification(test_session, title="First")
        _create_notification(test_session, title="Second")

        response = await async_client.get("/api/notifications")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["notifications"]) == 2

    @pytest.mark.asyncio
    async def test_pagination(self, async_client, test_session):
        """Pagination works with page and page_size."""
        for i in range(5):
            _create_notification(test_session, title=f"Notif {i}")

        response = await async_client.get("/api/notifications", params={"page": 2, "page_size": 2})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert data["page"] == 2
        assert data["page_size"] == 2
        assert len(data["notifications"]) == 2

    @pytest.mark.asyncio
    async def test_filter_unread_only(self, async_client, test_session):
        """Filters to only unread notifications."""
        _create_notification(test_session, title="Unread", read=False)
        _create_notification(test_session, title="Read", read=True)

        response = await async_client.get("/api/notifications", params={"unread_only": True})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["notifications"][0]["title"] == "Unread"

    @pytest.mark.asyncio
    async def test_filter_by_type(self, async_client, test_session):
        """Filters by notification type."""
        _create_notification(test_session, type="error", title="Error")
        _create_notification(test_session, type="info", title="Info")

        response = await async_client.get(
            "/api/notifications", params={"notification_type": "error"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["notifications"][0]["type"] == "error"

    @pytest.mark.asyncio
    async def test_unread_count(self, async_client, test_session):
        """Response includes correct unread count."""
        _create_notification(test_session, read=False)
        _create_notification(test_session, read=False)
        _create_notification(test_session, read=True)

        response = await async_client.get("/api/notifications")
        data = response.json()
        assert data["unread_count"] == 2


class TestCreateNotification:
    """Tests for POST /api/notifications."""

    @pytest.mark.asyncio
    async def test_creates_notification(self, async_client, test_session):
        """Creates a new notification via API."""
        with patch("routers.notifications.create_notification_internal", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = {
                "id": 1,
                "type": "info",
                "title": "New Alert",
                "message": "Something happened",
                "read": False,
            }
            response = await async_client.post("/api/notifications", json={
                "message": "Something happened",
                "title": "New Alert",
            })

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "New Alert"

    @pytest.mark.asyncio
    async def test_rejects_empty_message(self, async_client):
        """Returns 400 when message is empty."""
        response = await async_client.post("/api/notifications", json={
            "message": "",
        })
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_invalid_type(self, async_client):
        """Returns 400 for invalid notification type."""
        response = await async_client.post("/api/notifications", json={
            "message": "Test",
            "notification_type": "invalid",
        })
        assert response.status_code == 400


class TestMarkAllNotificationsRead:
    """Tests for PATCH /api/notifications/mark-all-read."""

    @pytest.mark.asyncio
    async def test_marks_all_read(self, async_client, test_session):
        """Marks all unread notifications as read."""
        _create_notification(test_session, read=False)
        _create_notification(test_session, read=False)
        _create_notification(test_session, read=True)

        response = await async_client.patch("/api/notifications/mark-all-read")
        assert response.status_code == 200
        data = response.json()
        assert data["marked_read"] == 2

        # Verify all are now read
        unread = test_session.query(Notification).filter(Notification.read == False).count()
        assert unread == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_all_read(self, async_client, test_session):
        """Returns 0 when no unread notifications exist."""
        _create_notification(test_session, read=True)

        response = await async_client.patch("/api/notifications/mark-all-read")
        assert response.status_code == 200
        assert response.json()["marked_read"] == 0


class TestUpdateNotification:
    """Tests for PATCH /api/notifications/{notification_id}."""

    @pytest.mark.asyncio
    async def test_mark_as_read(self, async_client, test_session):
        """Marks a notification as read."""
        notif = _create_notification(test_session, read=False)

        response = await async_client.patch(
            f"/api/notifications/{notif.id}",
            params={"read": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["read"] is True
        assert data["read_at"] is not None

    @pytest.mark.asyncio
    async def test_mark_as_unread(self, async_client, test_session):
        """Marks a notification as unread."""
        notif = _create_notification(test_session, read=True)

        response = await async_client.patch(
            f"/api/notifications/{notif.id}",
            params={"read": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["read"] is False
        assert data["read_at"] is None

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 for nonexistent notification."""
        response = await async_client.patch(
            "/api/notifications/99999",
            params={"read": True},
        )
        assert response.status_code == 404


class TestDeleteNotification:
    """Tests for DELETE /api/notifications/{notification_id}."""

    @pytest.mark.asyncio
    async def test_deletes_notification(self, async_client, test_session):
        """Deletes a specific notification."""
        notif = _create_notification(test_session)
        notif_id = notif.id

        response = await async_client.delete(f"/api/notifications/{notif_id}")
        assert response.status_code == 200
        assert response.json()["deleted"] is True

        result = test_session.query(Notification).filter(Notification.id == notif_id).first()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 for nonexistent notification."""
        response = await async_client.delete("/api/notifications/99999")
        assert response.status_code == 404


class TestClearAllNotifications:
    """Tests for DELETE /api/notifications."""

    @pytest.mark.asyncio
    async def test_clears_read_only_by_default(self, async_client, test_session):
        """Clears only read notifications by default."""
        _create_notification(test_session, read=True, title="Read1")
        _create_notification(test_session, read=True, title="Read2")
        _create_notification(test_session, read=False, title="Unread")

        response = await async_client.delete("/api/notifications")
        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] == 2
        assert data["read_only"] is True

        remaining = test_session.query(Notification).count()
        assert remaining == 1

    @pytest.mark.asyncio
    async def test_clears_all_when_read_only_false(self, async_client, test_session):
        """Clears all notifications when read_only=false."""
        _create_notification(test_session, read=True)
        _create_notification(test_session, read=False)

        response = await async_client.delete(
            "/api/notifications", params={"read_only": False}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] == 2
        assert data["read_only"] is False


class TestDeleteNotificationsBySource:
    """Tests for DELETE /api/notifications/by-source.

    NOTE: In the monolith, this route is shadowed by DELETE /api/notifications/{notification_id}
    because the parameterized route is defined first. FastAPI matches routes in definition order,
    so "by-source" is parsed as notification_id (and fails validation).
    This will be fixed during router extraction by reordering static routes before dynamic ones.
    """

    @pytest.mark.asyncio
    async def test_route_shadowed_by_notification_id(self, async_client):
        """Route is currently shadowed — returns 422 because 'by-source' isn't a valid int."""
        response = await async_client.delete(
            "/api/notifications/by-source", params={"source": "probe"}
        )
        # 422 because FastAPI tries to parse "by-source" as notification_id (int)
        assert response.status_code == 422
