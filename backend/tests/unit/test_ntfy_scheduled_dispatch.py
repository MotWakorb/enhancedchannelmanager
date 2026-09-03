"""Scheduled-task integration contract for ntfy alert dispatch."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from task_engine import TaskEngine


def _scheduled_task(**overrides):
    values = {
        "send_alerts": True,
        "alert_on_success": True,
        "alert_on_warning": True,
        "alert_on_error": True,
        "alert_on_info": False,
        "send_to_email": False,
        "send_to_discord": False,
        "send_to_telegram": False,
        "show_notifications": True,
    }
    values.update(overrides)
    return MagicMock(**values)


@pytest.mark.asyncio
async def test_scheduled_task_passes_legacy_channels_without_an_ntfy_toggle():
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = _scheduled_task()
    create_notification = AsyncMock(return_value={"id": 1})

    with patch("task_engine.get_session", return_value=session), patch(
        "services.notification_service.create_notification_internal", create_notification
    ):
        await TaskEngine()._notify_task_result(
            task_name="EPG Refresh",
            task_id="epg_refresh",
            notification_type="success",
            title="Task complete",
            message="Done",
        )

    create_notification.assert_awaited_once()
    assert create_notification.await_args.kwargs["send_alerts"] is True
    assert create_notification.await_args.kwargs["channel_settings"] == {
        "send_to_email": False,
        "send_to_discord": False,
        "send_to_telegram": False,
    }


@pytest.mark.asyncio
async def test_notification_exception_does_not_escape_task_completion_path():
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = _scheduled_task()

    with patch("task_engine.get_session", return_value=session), patch(
        "services.notification_service.create_notification_internal",
        new=AsyncMock(side_effect=RuntimeError("ntfy delivery failed")),
    ):
        await TaskEngine()._notify_task_result(
            task_name="EPG Refresh",
            task_id="epg_refresh",
            notification_type="success",
            title="Task complete",
            message="Done",
        )
