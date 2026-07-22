"""y3m6o.1 (0152): the UNATTENDED auto-fire path (ChannelPipelineTask) must
surface a failed-action run as "Completed with Warnings" — a single WARNING,
never a green "success" toast (the GH #720 hidden-failure class on the 3 AM
M3U-refresh path) and never a red "Task Failed" error (PO decision: a partial
failure is warnings, not a hard failure).

The engine finalizes a run in which any executed action failed as
``completed_with_errors`` and reports ``status`` + ``failed_action_count`` on
the pipeline result. The task envelope reports it as COMPLETED-WITH-WARNINGS:

  - failed-action run  -> TaskResult.success True + failed_count=N, and the
    auto-creation-specific completion toast is SUPPRESSED (deferred to the task
    engine's single "Task Completed with Warnings" warning). No green toast.
  - clean run          -> TaskResult.success True, failed_count 0, the green
    "Auto-Creation: N created" completion toast fires (unchanged control).

The final class here is an engine-level proof that a ``TaskResult(success=True,
failed_count>0)`` produces exactly the "Task Completed with Warnings" warning
WITH external alerts (requirement: alerts must still fire on the 3 AM path).
"""
import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Task-layer: ChannelPipelineTask.execute() envelope + auto-creation toast
# ---------------------------------------------------------------------------
def _settings(**kwargs):
    base = dict(
        auto_creation_run_on_refresh_disabled=False,
        last_m3u_refresh_completed_at="2026-01-02T00:00:00+00:00",
        last_auto_creation_consumed_refresh_at="2026-01-01T00:00:00+00:00",
    )
    base.update(kwargs)
    return MagicMock(**base)


def _rule(rid=1, name="R1"):
    r = MagicMock(id=rid)
    r.name = name
    return r


async def _run_autofire_capture(engine_result):
    """Drive ChannelPipelineTask.execute() through the auto-fire guard with a
    single run_on_refresh rule, returning (task_result, notify_mock).

    ``notify`` captures the task's OWN (Layer-A) create_notification_internal
    calls; the task-engine completion notification is a separate layer proven
    by the engine-level test class below.
    """
    from tasks.channel_pipeline import ChannelPipelineTask

    fake_engine = MagicMock()
    fake_engine.run_pipeline = AsyncMock(return_value=engine_result)

    rules = [_rule()]
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = rules
    session.query.return_value.filter.return_value.count.return_value = len(rules)

    notify = AsyncMock()
    with patch("tasks.channel_pipeline.get_settings", return_value=_settings()), \
         patch("tasks.channel_pipeline.save_settings"), \
         patch("services.notification_service.create_notification_internal", new=notify), \
         patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=fake_engine), \
         patch("channel_pipeline_engine.init_channel_pipeline_engine",
               new=AsyncMock(return_value=fake_engine)), \
         patch("dispatcharr_client.get_client", return_value=MagicMock()), \
         patch("tasks.channel_pipeline.get_client", return_value=MagicMock()), \
         patch("database.get_session", return_value=session), \
         patch("journal.log_entry"):
        os.environ.pop("ECM_DISABLE_RUN_ON_REFRESH", None)
        task = ChannelPipelineTask()
        # i2xad: scheduled auto-creation is opt-in; the guard's other conditions
        # only apply once "enabled" holds — simulate the operator opt-in.
        task._enabled = True
        result = await task.execute()
    return result, notify


def _completion_notification(notify):
    """The run-completion toast (an 'Auto-Creation: ...' notification that is
    NOT the 'Starting' one), or None if it was suppressed/deferred."""
    for call in notify.await_args_list:
        kwargs = call.kwargs
        title = kwargs.get("title", "")
        if title.startswith("Auto-Creation:") and "Starting" not in title:
            return kwargs
    return None


_FAILED_RESULT = {
    "status": "completed_with_errors",
    "failed_action_count": 2,
    "failed_actions": [
        {"action": "assign_channel_profile"},
        {"action": "assign_channel_profile"},
    ],
    "channels_created": 3,
    "channels_updated": 1,
    "streams_matched": 4,
    "streams_evaluated": 6,
    "execution_id": 99,
}

_CLEAN_RESULT = {
    "status": "completed",
    "failed_action_count": 0,
    "channels_created": 3,
    "channels_updated": 1,
    "streams_matched": 4,
    "streams_evaluated": 6,
    "execution_id": 100,
}


@pytest.mark.asyncio
async def test_failed_action_run_is_completed_with_warnings_envelope():
    """success STAYS True (completed-with-warnings, not a hard failure), with
    the action-failure count carried on failed_count so the task engine emits a
    warning rather than a red error."""
    result, _ = await _run_autofire_capture(_FAILED_RESULT)
    assert result.success is True
    assert result.failed_count == 2
    # No hard-error envelope.
    assert result.error is None


@pytest.mark.asyncio
async def test_failed_action_run_suppresses_green_completion_toast():
    """The auto-creation-specific completion toast is deferred to the task
    engine on a failed-action run — so there is NO green 'success' toast and no
    competing second toast from this layer."""
    _, notify = await _run_autofire_capture(_FAILED_RESULT)
    completion = _completion_notification(notify)
    assert completion is None, (
        "failed-action run must NOT emit its own completion toast — the single "
        "warning is deferred to the task engine's 'Completed with Warnings'"
    )
    # And nothing this layer emitted was a green success toast.
    types = [c.kwargs.get("notification_type") for c in notify.await_args_list]
    assert "success" not in types


_STATUS_ONLY_RESULT = {
    # Defensive edge: the engine signals completed_with_errors but (contrary to
    # today's coupling) reports NO count / empty failed_actions. The task layer
    # must still route non-green — never trust the count alone.
    "status": "completed_with_errors",
    "failed_action_count": 0,
    "failed_actions": [],
    "channels_created": 3,
    "channels_updated": 1,
    "streams_matched": 4,
    "streams_evaluated": 6,
    "execution_id": 101,
}


@pytest.mark.asyncio
async def test_completed_with_errors_status_without_count_still_non_green():
    """A completed_with_errors run with failed_action_count==0 / empty
    failed_actions must STILL be non-green: failed_count >= 1 (so the task
    engine takes its warning branch) and the green completion toast is
    suppressed. Guards against the cross-module invariant ever drifting."""
    result, notify = await _run_autofire_capture(_STATUS_ONLY_RESULT)
    assert result.success is True
    assert result.failed_count >= 1
    completion = _completion_notification(notify)
    assert completion is None
    types = [c.kwargs.get("notification_type") for c in notify.await_args_list]
    assert "success" not in types


_CAPPED_FAILED_RESULT = {
    # y3m6o.1 review (Finding 2): a run that was BOTH capped AND had failed
    # actions. The task must emit ONE coherent warning (cap + failed-action
    # guidance) and suppress the task-engine's generic warning.
    "status": "capped",
    "capped": True,
    "cap_would_create": 5,
    "failed_action_count": 2,
    "failed_actions": [
        {"action": "assign_channel_profile"},
        {"action": "event_sync_attach"},
    ],
    "channels_created": 3,
    "channels_updated": 1,
    "streams_matched": 4,
    "streams_evaluated": 6,
    "execution_id": 102,
}


@pytest.mark.asyncio
async def test_capped_plus_failed_emits_one_combined_warning():
    """y3m6o.1 review (Finding 2): a compound capped + failed-action run emits
    ONE coherent warning carrying BOTH the cap info and the failed-action
    recovery guidance — not a cap warning plus a separate engine warning."""
    result, notify = await _run_autofire_capture(_CAPPED_FAILED_RESULT)
    completion = _completion_notification(notify)
    assert completion is not None
    assert completion["notification_type"] == "warning"
    # ONE message carries BOTH conditions.
    msg = completion["message"].lower()
    assert "cap" in msg
    assert "action" in msg and "failed" in msg
    # And the returned envelope tells the task engine to skip its own warning.
    assert result.suppress_completion_notification is True


@pytest.mark.asyncio
async def test_capped_without_failed_does_not_suppress_engine_notification():
    """Control: a capped run with NO failed actions keeps the standard behavior
    — it does not suppress the task engine's completion path."""
    capped_only = dict(_CAPPED_FAILED_RESULT)
    capped_only["failed_action_count"] = 0
    capped_only["failed_actions"] = []
    result, _ = await _run_autofire_capture(capped_only)
    assert result.suppress_completion_notification is False


@pytest.mark.asyncio
async def test_clean_run_emits_green_completion_and_stays_success():
    result, notify = await _run_autofire_capture(_CLEAN_RESULT)
    assert result.success is True
    assert result.failed_count == 0
    completion = _completion_notification(notify)
    assert completion is not None
    assert completion["notification_type"] == "success"
    assert completion["send_alerts"] is False


# ---------------------------------------------------------------------------
# Engine-layer: TaskResult(success=True, failed_count>0) -> ONE "Task Completed
# with Warnings" warning WITH external alerts (requirement: alerts fire).
# Modeled on tests/integration/test_task_engine_progress_alert_gating.py.
# ---------------------------------------------------------------------------
from sqlalchemy.orm import sessionmaker  # noqa: E402

import database  # noqa: E402
import task_registry  # noqa: E402
from models import ScheduledTask  # noqa: E402
from task_engine import TaskEngine  # noqa: E402
from task_scheduler import ScheduleConfig, ScheduleType, TaskResult, TaskScheduler  # noqa: E402


class _PartialFailureTask(TaskScheduler):
    task_id = "test_y3m6o1_partial_failure"
    task_name = "Y3M6O1 Partial Failure Test"
    task_description = "Synthetic task — returns success=True with failed_count>0."
    default_enabled = False

    async def execute(self) -> TaskResult:
        now = datetime.utcnow()
        # The exact envelope ChannelPipelineTask now returns on a failed-action
        # run: completed-with-warnings, not a hard failure.
        return TaskResult(
            success=True,
            message="Auto-creation after M3U refresh: 6 streams evaluated, "
                    "4 matched, 3 channels created, 1 updated; 2 actions failed",
            started_at=now,
            completed_at=now,
            total_items=6,
            success_count=4,
            failed_count=2,
        )


@pytest.fixture
def _registered_partial_failure_task():
    registry = task_registry.get_registry()
    registry.register(_PartialFailureTask)
    registry._instances[_PartialFailureTask.task_id] = _PartialFailureTask(
        ScheduleConfig(schedule_type=ScheduleType.MANUAL)
    )
    yield registry
    registry.unregister(_PartialFailureTask.task_id)
    registry._instances.pop(_PartialFailureTask.task_id, None)


@pytest.mark.asyncio
async def test_partial_failure_result_emits_warning_completion_with_alerts(
    test_engine, monkeypatch, _registered_partial_failure_task
):
    """A completed-with-warnings TaskResult produces the task engine's single
    'Task Completed with Warnings' warning AND dispatches external alerts
    (send_alerts True, gated on alert_on_warning which defaults ON)."""
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", SessionLocal)

    s = SessionLocal()
    try:
        s.query(ScheduledTask).filter(
            ScheduledTask.task_id == _PartialFailureTask.task_id
        ).delete()
        s.add(ScheduledTask(
            task_id=_PartialFailureTask.task_id,
            task_name=_PartialFailureTask.task_name,
            description=_PartialFailureTask.task_description,
            enabled=True,
            schedule_type="manual",
            send_alerts=True,          # master ON
            alert_on_warning=True,     # warnings alert (default)
            show_notifications=True,
        ))
        s.commit()
    finally:
        s.close()

    engine = TaskEngine()
    engine._create_notification_callback = AsyncMock(return_value={"id": 1})
    engine._update_notification_callback = AsyncMock()
    engine._delete_notification_callback = AsyncMock()

    notify = AsyncMock(return_value={"id": 2})
    with patch("services.notification_service.create_notification_internal", new=notify):
        await engine._execute_task(
            task_id=_PartialFailureTask.task_id, triggered_by="test"
        )

    # Exactly one completion notification, and it is a WARNING (not error/success).
    assert notify.await_count == 1
    kwargs = notify.await_args.kwargs
    assert kwargs["notification_type"] == "warning"
    assert kwargs["title"].startswith("Task Completed with Warnings")
    # External alerts fire on the 3 AM path.
    assert kwargs["send_alerts"] is True


class _SuppressedCompletionTask(TaskScheduler):
    task_id = "test_y3m6o1_suppressed_completion"
    task_name = "Y3M6O1 Suppressed Completion Test"
    task_description = "Synthetic task — success=True, failed_count>0, suppressed."
    default_enabled = False

    async def execute(self) -> TaskResult:
        now = datetime.utcnow()
        return TaskResult(
            success=True,
            message="Capped and had failures; one combined warning already sent.",
            started_at=now,
            completed_at=now,
            total_items=6,
            success_count=4,
            failed_count=2,
            # y3m6o.1 review (Finding 2): the task already emitted ONE coherent
            # warning, so the engine must NOT emit a second.
            suppress_completion_notification=True,
        )


@pytest.fixture
def _registered_suppressed_completion_task():
    registry = task_registry.get_registry()
    registry.register(_SuppressedCompletionTask)
    registry._instances[_SuppressedCompletionTask.task_id] = _SuppressedCompletionTask(
        ScheduleConfig(schedule_type=ScheduleType.MANUAL)
    )
    yield registry
    registry.unregister(_SuppressedCompletionTask.task_id)
    registry._instances.pop(_SuppressedCompletionTask.task_id, None)


@pytest.mark.asyncio
async def test_suppress_flag_skips_engine_completion_notification(
    test_engine, monkeypatch, _registered_suppressed_completion_task
):
    """y3m6o.1 review (Finding 2): suppress_completion_notification makes the
    task engine emit NO completion notification (the task already sent one
    combined warning), avoiding two separate warnings for one run."""
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", SessionLocal)

    s = SessionLocal()
    try:
        s.query(ScheduledTask).filter(
            ScheduledTask.task_id == _SuppressedCompletionTask.task_id
        ).delete()
        s.add(ScheduledTask(
            task_id=_SuppressedCompletionTask.task_id,
            task_name=_SuppressedCompletionTask.task_name,
            description=_SuppressedCompletionTask.task_description,
            enabled=True,
            schedule_type="manual",
            send_alerts=True,
            alert_on_warning=True,
            show_notifications=True,
        ))
        s.commit()
    finally:
        s.close()

    engine = TaskEngine()
    engine._create_notification_callback = AsyncMock(return_value={"id": 1})
    engine._update_notification_callback = AsyncMock()
    engine._delete_notification_callback = AsyncMock()

    notify = AsyncMock(return_value={"id": 2})
    with patch("services.notification_service.create_notification_internal", new=notify):
        result = await engine._execute_task(
            task_id=_SuppressedCompletionTask.task_id, triggered_by="test"
        )

    # No completion notification emitted by the engine layer.
    assert notify.await_count == 0
    # The run still persisted its result (success stays True).
    assert result.success is True
