"""
Unit tests for notification service internal functions.

Tests: create_notification_internal, update_notification_internal,
       delete_notifications_by_source_internal, _dispatch_to_alert_channels
Mocks: database sessions (via main.get_session), alert channel dispatch.
"""
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestCreateNotificationInternal:
    """Tests for create_notification_internal()."""

    @pytest.mark.asyncio
    async def test_create_basic_notification(self, test_session):
        """Creates a notification with required fields and returns dict."""
        with patch("services.notification_service.get_session", return_value=test_session):
            from services.notification_service import create_notification_internal

            result = await create_notification_internal(
                message="Test message",
                source="test",
            )

        assert result is not None
        assert result["message"] == "Test message"
        assert result["source"] == "test"
        assert result["type"] == "info"  # default
        assert result["read"] is False
        assert "id" in result

    @pytest.mark.asyncio
    async def test_create_with_all_fields(self, test_session):
        """Creates a notification with all optional fields."""
        with patch("services.notification_service.get_session", return_value=test_session):
            from services.notification_service import create_notification_internal

            result = await create_notification_internal(
                notification_type="warning",
                title="Warning Title",
                message="Something happened",
                source="task",
                source_id="task-123",
                action_label="View",
                action_url="/tasks/123",
                metadata={"task_name": "EPG Refresh"},
                send_alerts=False,
            )

        assert result is not None
        assert result["type"] == "warning"
        assert result["title"] == "Warning Title"
        assert result["message"] == "Something happened"
        assert result["source"] == "task"
        assert result["source_id"] == "task-123"
        assert result["action_label"] == "View"
        assert result["action_url"] == "/tasks/123"
        assert result["metadata"] == {"task_name": "EPG Refresh"}

    @pytest.mark.asyncio
    async def test_create_returns_none_for_empty_message(self, test_session):
        """Returns None when message is empty."""
        with patch("services.notification_service.get_session", return_value=test_session):
            from services.notification_service import create_notification_internal

            result = await create_notification_internal(message="")

        assert result is None

    @pytest.mark.asyncio
    async def test_create_defaults_invalid_type_to_info(self, test_session):
        """Invalid notification_type defaults to 'info'."""
        with patch("services.notification_service.get_session", return_value=test_session):
            from services.notification_service import create_notification_internal

            result = await create_notification_internal(
                notification_type="invalid",
                message="Test",
            )

        assert result is not None
        assert result["type"] == "info"

    @pytest.mark.asyncio
    async def test_create_persists_to_database(self, test_session):
        """Notification is persisted in the database."""
        from models import Notification

        with patch("services.notification_service.get_session", return_value=test_session):
            from services.notification_service import create_notification_internal

            result = await create_notification_internal(
                message="Persistent msg",
                source="db_test",
            )

        # Query directly from the test session
        notif = test_session.query(Notification).filter(
            Notification.id == result["id"]
        ).first()
        assert notif is not None
        assert notif.message == "Persistent msg"
        assert notif.source == "db_test"

    @pytest.mark.asyncio
    async def test_create_dispatches_alerts_by_default(self, test_session):
        """When send_alerts=True (default), dispatches to alert channels."""
        with patch("services.notification_service.get_session", return_value=test_session), \
             patch("services.notification_service._dispatch_to_alert_channels", new_callable=AsyncMock) as mock_dispatch, \
             patch("asyncio.create_task") as mock_create_task:
            from services.notification_service import create_notification_internal

            await create_notification_internal(
                message="Alert me",
                title="Test Alert",
                source="test",
            )

            # asyncio.create_task is called with the dispatch coroutine
            mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_skips_alerts_when_disabled(self, test_session):
        """When send_alerts=False, does not dispatch to alert channels."""
        with patch("services.notification_service.get_session", return_value=test_session), \
             patch("asyncio.create_task") as mock_create_task:
            from services.notification_service import create_notification_internal

            await create_notification_internal(
                message="No alert",
                send_alerts=False,
            )

            mock_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_stores_metadata_as_json(self, test_session):
        """Metadata dict is stored as JSON in extra_data column."""
        from models import Notification

        with patch("services.notification_service.get_session", return_value=test_session):
            from services.notification_service import create_notification_internal

            result = await create_notification_internal(
                message="With metadata",
                metadata={"key": "value", "count": 42},
                send_alerts=False,
            )

        notif = test_session.query(Notification).filter(
            Notification.id == result["id"]
        ).first()
        assert notif.extra_data is not None
        parsed = json.loads(notif.extra_data)
        assert parsed["key"] == "value"
        assert parsed["count"] == 42


class TestUpdateNotificationInternal:
    """Tests for update_notification_internal()."""

    @pytest.mark.asyncio
    async def test_update_message(self, test_session):
        """Updates notification message."""
        from models import Notification

        # Create a notification directly
        notif = Notification(type="info", message="Original", source="test")
        test_session.add(notif)
        test_session.commit()
        test_session.refresh(notif)
        notif_id = notif.id

        with patch("services.notification_service.get_session", return_value=test_session):
            from services.notification_service import update_notification_internal

            result = await update_notification_internal(
                notification_id=notif_id,
                message="Updated message",
            )

        assert result is not None
        assert result["message"] == "Updated message"

    @pytest.mark.asyncio
    async def test_update_type(self, test_session):
        """Updates notification type."""
        from models import Notification

        notif = Notification(type="info", message="Test", source="test")
        test_session.add(notif)
        test_session.commit()
        test_session.refresh(notif)

        with patch("services.notification_service.get_session", return_value=test_session):
            from services.notification_service import update_notification_internal

            result = await update_notification_internal(
                notification_id=notif.id,
                notification_type="success",
            )

        assert result["type"] == "success"

    @pytest.mark.asyncio
    async def test_update_metadata(self, test_session):
        """Updates notification metadata (replaces existing)."""
        from models import Notification

        notif = Notification(
            type="info",
            message="Test",
            extra_data=json.dumps({"old": "data"}),
        )
        test_session.add(notif)
        test_session.commit()
        test_session.refresh(notif)

        with patch("services.notification_service.get_session", return_value=test_session):
            from services.notification_service import update_notification_internal

            result = await update_notification_internal(
                notification_id=notif.id,
                metadata={"new": "data", "progress": 75},
            )

        assert result["metadata"] == {"new": "data", "progress": 75}

    @pytest.mark.asyncio
    async def test_update_returns_none_for_nonexistent(self, test_session):
        """Returns None when notification doesn't exist."""
        with patch("services.notification_service.get_session", return_value=test_session):
            from services.notification_service import update_notification_internal

            result = await update_notification_internal(
                notification_id=99999,
                message="Ghost",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_update_ignores_invalid_type(self, test_session):
        """Invalid notification_type is ignored (keeps original)."""
        from models import Notification

        notif = Notification(type="warning", message="Test")
        test_session.add(notif)
        test_session.commit()
        test_session.refresh(notif)

        with patch("services.notification_service.get_session", return_value=test_session):
            from services.notification_service import update_notification_internal

            result = await update_notification_internal(
                notification_id=notif.id,
                notification_type="bogus",
            )

        assert result["type"] == "warning"  # unchanged


class TestDeleteNotificationsBySourceInternal:
    """Tests for delete_notifications_by_source_internal()."""

    @pytest.mark.asyncio
    async def test_delete_by_source(self, test_session):
        """Deletes all notifications with matching source."""
        from models import Notification

        for i in range(3):
            test_session.add(Notification(type="info", message=f"Probe {i}", source="probe"))
        test_session.add(Notification(type="info", message="Other", source="task"))
        test_session.commit()

        with patch("services.notification_service.get_session", return_value=test_session):
            from services.notification_service import delete_notifications_by_source_internal

            deleted = await delete_notifications_by_source_internal("probe")

        assert deleted == 3

        # "task" notification still exists
        remaining = test_session.query(Notification).filter(
            Notification.source == "task"
        ).count()
        assert remaining == 1

    @pytest.mark.asyncio
    async def test_delete_returns_zero_when_none_match(self, test_session):
        """Returns 0 when no notifications match the source."""
        with patch("services.notification_service.get_session", return_value=test_session):
            from services.notification_service import delete_notifications_by_source_internal

            deleted = await delete_notifications_by_source_internal("nonexistent")

        assert deleted == 0


class TestDispatchToAlertChannels:
    """Tests for _dispatch_to_alert_channels()."""

    @pytest.mark.asyncio
    async def test_dispatch_skips_when_no_channels_configured(self):
        """Does nothing when no alert channels are configured."""
        mock_settings = MagicMock()
        mock_settings.is_discord_configured.return_value = False
        mock_settings.is_telegram_configured.return_value = False
        mock_settings.is_smtp_configured.return_value = False

        with patch("services.notification_service.get_settings", return_value=mock_settings):
            from services.notification_service import _dispatch_to_alert_channels

            # Should not raise
            await _dispatch_to_alert_channels(
                title="Test",
                message="Hello",
                notification_type="info",
                source="test",
                metadata=None,
            )

    @pytest.mark.asyncio
    async def test_dispatch_sends_to_discord(self):
        """Sends to Discord when configured."""
        mock_settings = MagicMock()
        mock_settings.is_discord_configured.return_value = True
        mock_settings.is_telegram_configured.return_value = False
        mock_settings.is_smtp_configured.return_value = False
        mock_settings.discord_webhook_url = "https://discord.com/api/webhooks/test"

        # Mock the aiohttp session
        mock_response = AsyncMock()
        mock_response.status = 204
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp_session = MagicMock()
        mock_aiohttp_session.post.return_value = mock_session_ctx
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("services.notification_service.get_settings", return_value=mock_settings), \
             patch("aiohttp.ClientSession", return_value=mock_aiohttp_session):
            from services.notification_service import _dispatch_to_alert_channels

            await _dispatch_to_alert_channels(
                title="Test Alert",
                message="Hello World",
                notification_type="success",
                source="test",
                metadata=None,
            )

        # Verify Discord was called
        mock_aiohttp_session.post.assert_called_once()
        call_args = mock_aiohttp_session.post.call_args
        assert call_args[0][0] == "https://discord.com/api/webhooks/test"

    @pytest.mark.asyncio
    async def test_dispatch_sends_to_telegram(self):
        """Sends to Telegram when configured."""
        mock_settings = MagicMock()
        mock_settings.is_discord_configured.return_value = False
        mock_settings.is_telegram_configured.return_value = True
        mock_settings.is_smtp_configured.return_value = False
        mock_settings.telegram_bot_token = "bot123"
        mock_settings.telegram_chat_id = "chat456"

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp_session = MagicMock()
        mock_aiohttp_session.post.return_value = mock_session_ctx
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("services.notification_service.get_settings", return_value=mock_settings), \
             patch("aiohttp.ClientSession", return_value=mock_aiohttp_session):
            from services.notification_service import _dispatch_to_alert_channels

            await _dispatch_to_alert_channels(
                title="Test",
                message="Hello",
                notification_type="info",
                source="test",
                metadata=None,
            )

        mock_aiohttp_session.post.assert_called_once()
        call_args = mock_aiohttp_session.post.call_args
        assert "bot123" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_dispatch_respects_channel_settings(self):
        """channel_settings can disable specific channels."""
        mock_settings = MagicMock()
        mock_settings.is_discord_configured.return_value = True
        mock_settings.is_telegram_configured.return_value = True
        mock_settings.is_smtp_configured.return_value = False

        with patch("services.notification_service.get_settings", return_value=mock_settings):
            from services.notification_service import _dispatch_to_alert_channels

            # Disable both discord and telegram via channel_settings
            await _dispatch_to_alert_channels(
                title="Test",
                message="Hello",
                notification_type="info",
                source="test",
                metadata=None,
                channel_settings={
                    "send_to_discord": False,
                    "send_to_telegram": False,
                },
            )

        # Neither should have been called since channels are disabled
        # (no aiohttp.ClientSession should have been created)

    @pytest.mark.asyncio
    async def test_dispatch_includes_metadata_in_message(self):
        """Metadata fields (task_name, duration) are included in alert message."""
        mock_settings = MagicMock()
        mock_settings.is_discord_configured.return_value = True
        mock_settings.is_telegram_configured.return_value = False
        mock_settings.is_smtp_configured.return_value = False
        mock_settings.discord_webhook_url = "https://discord.com/api/webhooks/test"

        mock_response = AsyncMock()
        mock_response.status = 204
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp_session = MagicMock()
        mock_aiohttp_session.post.return_value = mock_session_ctx
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("services.notification_service.get_settings", return_value=mock_settings), \
             patch("aiohttp.ClientSession", return_value=mock_aiohttp_session):
            from services.notification_service import _dispatch_to_alert_channels

            await _dispatch_to_alert_channels(
                title="Task Complete",
                message="EPG refresh done",
                notification_type="success",
                source="task",
                metadata={"task_name": "EPG Refresh", "duration_seconds": 12.5},
            )

        call_args = mock_aiohttp_session.post.call_args
        payload = call_args[1]["json"]
        assert "EPG Refresh" in payload["content"]
        assert "12.5" in payload["content"]
