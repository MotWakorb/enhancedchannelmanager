"""Scheduled-task integration contract for ntfy alert dispatch."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import alert_methods
from alert_methods import AlertMethodManager
from alert_methods_ntfy import NtfyMethod
from models import AlertMethod, ScheduledTask
from task_engine import TaskEngine
from tasks.stream_probe import StreamProbeTask


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


def _completed_prober():
    prober = MagicMock()
    prober._probing_in_progress = False
    prober.probe_timeout = 10
    prober.max_concurrent_probes = 2
    prober._probe_progress_total = 1
    prober._probe_progress_success_count = 1
    prober._probe_progress_failed_count = 0
    prober._probe_progress_skipped_count = 0
    prober._probe_progress_black_screen_count = 0
    prober._probe_progress_low_fps_count = 0
    prober._probe_success_streams = []
    prober._probe_failed_streams = []
    prober._probe_skipped_streams = []
    prober._probe_black_screen_streams = []
    prober._probe_low_fps_streams = []
    prober._failure_breakdown.return_value = []

    async def finish(**_kwargs):
        return {"status": "completed"}

    prober.probe_all_streams = AsyncMock(side_effect=finish)
    return prober


@pytest.mark.parametrize("send_alerts,expected_queues", [(False, 0), (True, 1)])
@pytest.mark.asyncio
async def test_scheduled_stream_probe_has_one_gated_completion_alert_owner(
    send_alerts, expected_queues, test_session, async_client
):
    prober = _completed_prober()
    task = StreamProbeTask()
    task.set_prober(prober)
    task._send_alerts = send_alerts

    result = await task.execute()

    assert result.success is True
    assert prober.probe_all_streams.await_args.kwargs["completion_send_alerts"] is False

    scheduled = ScheduledTask(
        task_id="stream_probe",
        task_name="Stream Probe",
        send_alerts=send_alerts,
        alert_on_success=True,
        alert_on_warning=True,
        alert_on_error=True,
        alert_on_info=False,
        send_to_email=False,
        send_to_discord=False,
        send_to_telegram=False,
        show_notifications=True,
    )
    method_row = AlertMethod(
        name="ntfy",
        method_type="ntfy",
        config='{"server_url":"https://ntfy.example.test","topic":"private"}',
        enabled=True,
        notify_info=False,
        notify_success=True,
        notify_warning=True,
        notify_error=True,
    )
    test_session.add_all([scheduled, method_row])
    test_session.commit()
    test_session.refresh(method_row)

    manager = AlertMethodManager()
    manager._methods[method_row.id] = NtfyMethod(
        method_row.id,
        method_row.name,
        {"server_url": "https://ntfy.example.test", "topic": "private"},
    )
    settings = MagicMock()
    settings.is_smtp_configured.return_value = False
    settings.is_discord_configured.return_value = False
    settings.is_telegram_configured.return_value = False

    with patch.object(alert_methods, "_manager", manager), patch(
        "services.notification_service.get_settings", return_value=settings
    ):
        await TaskEngine()._notify_task_result(
            task_name="Stream Probe",
            task_id="stream_probe",
            notification_type="success",
            title="Task complete",
            message="Done",
            result=result,
            alert_category="probe_failures",
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    queued = manager._alert_buffer.get(method_row.id, [])
    assert len(queued) == expected_queues
    if manager._flush_task:
        manager._flush_task.cancel()
        await asyncio.sleep(0)
