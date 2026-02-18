"""
Integration tests for notification service cross-module usage.

Tests: Verify notification_service CRUD works through real DB,
       create_notification_internal stores in DB, notification callbacks
       can be called from different modules.
"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from models import Notification


def _create_notification(session, **overrides):
    """Insert a notification record for testing."""
    defaults = {
        "created_at": datetime.utcnow(),
        "title": "Test Notification",
        "message": "Test message",
        "type": "info",
        "read": False,
    }
    defaults.update(overrides)
    record = Notification(**defaults)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


class TestNotificationCRUDIntegration:
    """Verify notification CRUD through the API with real DB."""

    @pytest.mark.asyncio
    async def test_create_notification_via_api(self, async_client, test_session):
        """POST /api/notifications creates a notification in the DB."""
        response = await async_client.post("/api/notifications", json={
            "title": "Integration Test",
            "message": "Created via API",
            "notification_type": "info",
            "send_alerts": False,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Integration Test"

        # Verify it was stored in DB
        stored = test_session.query(Notification).filter_by(
            title="Integration Test"
        ).first()
        assert stored is not None
        assert stored.message == "Created via API"

    @pytest.mark.asyncio
    async def test_list_after_create(self, async_client, test_session):
        """GET /api/notifications returns created notifications."""
        _create_notification(test_session, title="First")
        _create_notification(test_session, title="Second")

        response = await async_client.get("/api/notifications")
        assert response.status_code == 200
        data = response.json()
        titles = [n["title"] for n in data["notifications"]]
        assert "First" in titles
        assert "Second" in titles

    @pytest.mark.asyncio
    async def test_mark_read_updates_db(self, async_client, test_session):
        """PATCH /api/notifications/{id} updates read flag in DB."""
        notif = _create_notification(test_session, title="Unread")

        response = await async_client.patch(
            f"/api/notifications/{notif.id}",
            params={"read": True},
        )
        assert response.status_code == 200

        test_session.refresh(notif)
        assert notif.read is True

    @pytest.mark.asyncio
    async def test_delete_notification_removes_from_db(self, async_client, test_session):
        """DELETE /api/notifications/{id} removes from DB."""
        notif = _create_notification(test_session, title="To Delete")

        response = await async_client.delete(f"/api/notifications/{notif.id}")
        assert response.status_code == 200

        remaining = test_session.query(Notification).filter_by(id=notif.id).first()
        assert remaining is None


class TestCreateNotificationInternal:
    """Verify create_notification_internal works with real DB."""

    @pytest.mark.asyncio
    async def test_internal_creates_notification(self, async_client, test_session):
        """create_notification_internal should store notification in DB."""
        from services.notification_service import create_notification_internal

        result = await create_notification_internal(
            notification_type="warning",
            title="Internal Test",
            message="Created internally",
            source="test",
            send_alerts=False,
        )

        assert result is not None
        assert result["title"] == "Internal Test"

        # Verify stored in DB
        stored = test_session.query(Notification).filter_by(
            title="Internal Test"
        ).first()
        assert stored is not None
        assert stored.type == "warning"

    @pytest.mark.asyncio
    async def test_internal_skips_empty_message(self, async_client, test_session):
        """create_notification_internal should skip empty messages."""
        from services.notification_service import create_notification_internal

        result = await create_notification_internal(message="")
        assert result is None


class TestNotificationCallbackChain:
    """Verify notification callbacks work when called from task engine context."""

    @pytest.mark.asyncio
    async def test_create_and_update_chain(self, async_client, test_session):
        """Create then update a notification via internal helpers."""
        from services.notification_service import create_notification_internal, update_notification_internal

        created = await create_notification_internal(
            notification_type="info",
            title="Chain Test",
            message="Step 1",
            source="test_chain",
            source_id="chain-1",
            send_alerts=False,
        )

        assert created is not None
        notif_id = created["id"]

        # Update the notification
        updated = await update_notification_internal(
            notification_id=notif_id,
            message="Step 2 - Updated",
        )

        assert updated is not None
        assert updated["message"] == "Step 2 - Updated"

        # Verify DB reflects update
        stored = test_session.query(Notification).filter_by(id=notif_id).first()
        assert stored.message == "Step 2 - Updated"

    @pytest.mark.asyncio
    async def test_delete_by_source(self, async_client, test_session):
        """delete_notifications_by_source_internal removes matching records."""
        from services.notification_service import (
            create_notification_internal,
            delete_notifications_by_source_internal,
        )

        await create_notification_internal(
            message="Source A",
            source="cleanup_test",
            source_id="a",
            send_alerts=False,
        )
        await create_notification_internal(
            message="Source B",
            source="cleanup_test",
            source_id="b",
            send_alerts=False,
        )

        count = await delete_notifications_by_source_internal("cleanup_test")
        assert count >= 2

        remaining = test_session.query(Notification).filter_by(
            source="cleanup_test"
        ).all()
        assert len(remaining) == 0
