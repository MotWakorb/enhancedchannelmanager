"""Scheduled-task integration contract for ntfy alert dispatch."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

import alert_methods
import database
from alert_methods import AlertMethodManager
from alert_methods_ntfy import NtfyMethod
from models import AlertMethod, ScheduledTask, StreamStats
from task_engine import TaskEngine
from task_registry import get_registry
from tasks.failed_stream_reprobe import FailedStreamReprobeTask  # noqa: F401
from tasks.stream_probe import StreamProbeTask  # noqa: F401


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


def _completed_prober(*, failed_count=0):
    prober = MagicMock()
    prober._probing_in_progress = False
    prober.probe_timeout = 10
    prober.max_concurrent_probes = 2
    prober._probe_progress_total = 1
    prober._probe_progress_current = 1
    prober._probe_progress_status = "completed"
    prober._probe_progress_current_stream = ""
    prober._probe_progress_success_count = 1 - failed_count
    prober._probe_progress_failed_count = failed_count
    prober._probe_progress_skipped_count = 0
    prober._probe_progress_black_screen_count = 0
    prober._probe_progress_low_fps_count = 0
    prober._probe_success_streams = []
    prober._probe_failed_streams = []
    prober._probe_skipped_streams = []
    prober._probe_black_screen_streams = []
    prober._probe_low_fps_streams = []
    prober._last_probe_scope_kind = None
    prober._last_probe_channel_stream_ids = set()
    prober._failure_breakdown.return_value = []

    async def finish(**_kwargs):
        return {"status": "completed"}

    prober.probe_all_streams = AsyncMock(side_effect=finish)
    return prober


async def _run_probe_lifecycle(
    test_engine,
    monkeypatch,
    *,
    task_id,
    triggered_by="scheduled",
    send_alerts=True,
    probe_source_enabled=True,
):
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", SessionLocal)

    task = get_registry().get_task_instance(task_id)
    prober = _completed_prober()
    task.set_prober(prober)

    session = SessionLocal()
    scheduled = ScheduledTask(
        task_id=task_id,
        task_name=task.task_name,
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
        alert_sources=(
            '{"version":1,"probe_failures":{"enabled":true,"min_failures":1}}'
            if probe_source_enabled
            else '{"version":1,"probe_failures":{"enabled":false,"min_failures":1}}'
        ),
    )
    session.add_all([scheduled, method_row])
    if task_id == "failed_stream_reprobe":
        session.add(StreamStats(stream_id=42, probe_status="failed"))
    session.commit()
    session.refresh(method_row)
    session.close()

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
        result = await TaskEngine()._execute_task(task_id, triggered_by=triggered_by)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    queued = manager._alert_buffer.get(method_row.id, [])
    if manager._flush_task:
        manager._flush_task.cancel()
        await asyncio.sleep(0)
    return result, prober, queued


@pytest.mark.parametrize(
    "triggered_by,send_alerts,expected_queues",
    [
        ("scheduled", False, 0),
        ("scheduled", True, 1),
        ("manual", True, 1),
    ],
)
@pytest.mark.asyncio
async def test_stream_probe_completion_alert_runs_through_task_engine_lifecycle(
    test_engine, monkeypatch, triggered_by, send_alerts, expected_queues
):
    result, prober, queued = await _run_probe_lifecycle(
        test_engine,
        monkeypatch,
        task_id="stream_probe",
        triggered_by=triggered_by,
        send_alerts=send_alerts,
    )

    assert result.success is True
    assert prober.probe_all_streams.await_args.kwargs["completion_send_alerts"] is False
    assert len(queued) == expected_queues


@pytest.mark.asyncio
async def test_failed_stream_reprobe_completion_respects_probe_failure_source_filter(
    test_engine, monkeypatch
):
    result, prober, queued = await _run_probe_lifecycle(
        test_engine,
        monkeypatch,
        task_id="failed_stream_reprobe",
        probe_source_enabled=False,
    )

    assert result.success is True
    assert prober.probe_all_streams.await_args.kwargs["completion_send_alerts"] is False
    assert queued == []
