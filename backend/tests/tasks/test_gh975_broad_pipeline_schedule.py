"""GH975: broad schedule scope must not inherit the refresh-only filter."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.orm import sessionmaker

from models import ChannelPipelineRule
from tasks.channel_pipeline import ChannelPipelineTask
from channel_pipeline_engine import ChannelPipelineEngine
from task_engine import TaskEngine
from task_registry import TaskRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("triggered_by", ["scheduled", "manual"])
@pytest.mark.parametrize("refresh_at", ["", "2026-09-05T12:00:00"])
async def test_broad_schedule_runs_without_refresh_opt_in(
    test_engine, monkeypatch, triggered_by, refresh_at
):
    session_factory = sessionmaker(bind=test_engine)
    with session_factory() as session:
        session.add_all([
            ChannelPipelineRule(
                id=1, name="Enabled", enabled=True, run_on_refresh=False,
                conditions='{"type":"and","children":[]}', actions='[]',
            ),
            ChannelPipelineRule(
                id=2, name="Disabled", enabled=False, run_on_refresh=False,
                conditions='{"type":"and","children":[]}', actions='[]',
            ),
        ])
        session.commit()

    settings = SimpleNamespace(
        auto_creation_run_on_refresh_disabled=False,
        last_m3u_refresh_completed_at=refresh_at,
        last_auto_creation_consumed_refresh_at="",
    )
    pipeline = AsyncMock()
    pipeline.run_pipeline.return_value = {
        "success": True, "status": "completed", "streams_evaluated": 0,
    }
    monkeypatch.setattr("database.get_session", session_factory)
    monkeypatch.setattr("tasks.channel_pipeline.get_settings", lambda: settings)
    monkeypatch.setattr("tasks.channel_pipeline.save_settings", lambda settings: None)
    monkeypatch.setattr("tasks.channel_pipeline.get_client", lambda: None)
    monkeypatch.setattr("channel_pipeline_engine.get_channel_pipeline_engine", lambda: pipeline)
    monkeypatch.setattr("services.notification_service.create_notification_internal", AsyncMock())
    monkeypatch.delenv("ECM_DISABLE_RUN_ON_REFRESH", raising=False)

    task = ChannelPipelineTask()
    task._enabled = True
    task.set_run_trigger(triggered_by)
    task.prepare_invocation_parameters(triggered_by, 17, {"run_all_rules": True})
    result = await task.execute()

    assert result.success is True
    pipeline.run_pipeline.assert_awaited_once()
    kwargs = pipeline.run_pipeline.await_args.kwargs
    assert kwargs["triggered_by"] == ("scheduled_all" if triggered_by == "scheduled" else triggered_by)
    # None delegates enabled-rule selection to the engine, as the page run does.
    assert kwargs["rule_ids"] is None or kwargs["rule_ids"] == [1]
    assert settings.last_auto_creation_consumed_refresh_at == ""


@pytest.mark.parametrize("parameters", [
    {"run_all_rules": "true"},
    {"run_all_rules": 1},
    {"run_all_rules": True, "rule_ids": [1]},
    {"run_all_rules": True, "rule_ids": []},
    {"run_all_rules": True, "rule_ids": None},
])
def test_broad_scope_rejects_invalid_or_conflicting_parameters(parameters):
    with pytest.raises(ValueError):
        ChannelPipelineTask.validate_schedule_parameters(parameters)


def test_broad_scope_is_valid_without_selected_rules():
    ChannelPipelineTask.validate_schedule_parameters({"run_all_rules": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("parameters", [None, {}])
async def test_installed_parameterless_poll_still_uses_refresh_guard(monkeypatch, parameters):
    settings = SimpleNamespace(
        auto_creation_run_on_refresh_disabled=False,
        last_m3u_refresh_completed_at="",
        last_auto_creation_consumed_refresh_at="",
    )
    monkeypatch.setattr("tasks.channel_pipeline.get_settings", lambda: settings)
    monkeypatch.delenv("ECM_DISABLE_RUN_ON_REFRESH", raising=False)
    task = ChannelPipelineTask()
    task._enabled = True
    task.set_run_trigger("scheduled")
    # Same singleton can first run a broad schedule, then the built-in poll.
    config = task.get_config()
    task.prepare_invocation_parameters("scheduled", 18, {"run_all_rules": True})
    task.restore_invocation_config(config)
    task.prepare_invocation_parameters("scheduled", 17, parameters)
    result = await task.execute()
    assert result.message == "No new M3U refresh to process"


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["scheduled", "manual"])
@pytest.mark.parametrize("scope", ["broad", "selected", "refresh"])
async def test_persisted_schedule_dispatch_preserves_each_scope(
    test_session, monkeypatch, trigger, scope
):
    from models import ScheduledTask, TaskSchedule, TaskExecution

    monkeypatch.setattr("database._SessionLocal", sessionmaker(
        bind=test_session.get_bind(), expire_on_commit=False,
    ))
    test_session.add(ScheduledTask(
        task_id="auto_creation", task_name="Pipeline", enabled=True, schedule_type="interval",
    ))
    for rule_id, enabled, refresh, priority in [
        (1, True, False, 20), (2, False, True, 0),
        (3, True, True, 10), (4, True, False, 30),
    ]:
        test_session.add(ChannelPipelineRule(
            id=rule_id, name=f"Rule {rule_id}", enabled=enabled,
            run_on_refresh=refresh, priority=priority,
            conditions='[{"type":"always"}]', actions='[{"type":"skip"}]',
        ))
    test_session.commit()
    task = ChannelPipelineTask()
    task._enabled = True
    # Exercise actual seed persistence. Existing installs have this same null scope.
    registry = TaskRegistry()
    registry._create_default_task_schedule(test_session, task)
    test_session.commit()
    schedule = test_session.query(TaskSchedule).one()
    assert schedule.interval_seconds == 60
    assert not schedule.get_parameters()
    if scope != "refresh":
        schedule.set_parameters(
            {"run_all_rules": True} if scope == "broad" else {"rule_ids": [1, 3]}
        )
        test_session.commit()
    # Registration must not rewrite an installed operator schedule.
    stored_parameters = schedule.parameters
    registry._create_default_task_schedule(test_session, task)
    assert schedule.parameters == stored_parameters

    settings = SimpleNamespace(
        auto_creation_run_on_refresh_disabled=False,
        last_m3u_refresh_completed_at="2026-09-05T12:00:00",
        last_auto_creation_consumed_refresh_at="",
    )
    monkeypatch.setattr("tasks.channel_pipeline.get_settings", lambda: settings)
    monkeypatch.setattr("tasks.channel_pipeline.save_settings", lambda settings: None)
    monkeypatch.setattr("tasks.channel_pipeline.get_client", lambda: None)
    monkeypatch.setattr("services.notification_service.create_notification_internal", AsyncMock())
    monkeypatch.delenv("ECM_DISABLE_RUN_ON_REFRESH", raising=False)
    loader = ChannelPipelineEngine(MagicMock())
    loaded_ids = []

    async def run_pipeline(**kwargs):
        # Keep the real enabled/date/priority selection at the engine boundary;
        # replace processing and external writes only.
        rules = await loader._load_rules(kwargs["rule_ids"])
        loaded_ids.extend(rule.id for rule in rules)
        return {"success": True, "status": "completed", "streams_evaluated": 0}

    pipeline = AsyncMock()
    pipeline.run_pipeline.side_effect = run_pipeline
    monkeypatch.setattr("channel_pipeline_engine.get_channel_pipeline_engine", lambda: pipeline)
    registry_mock = MagicMock()
    registry_mock.get_task_instance.return_value = task
    monkeypatch.setattr("task_engine.get_registry", lambda: registry_mock)
    monkeypatch.setattr("task_engine.log_entry", MagicMock())
    task_engine = TaskEngine()
    monkeypatch.setattr(task_engine, "_notify_task_result", AsyncMock())

    if trigger == "scheduled":
        result = await task_engine._execute_task_with_schedules("auto_creation", [schedule])
    else:
        result = await task_engine.run_task("auto_creation", schedule_id=schedule.id)

    assert result.success is True
    assert loaded_ids == {"broad": [3, 1, 4], "selected": [3, 1], "refresh": [3]}[scope]
    kwargs = pipeline.run_pipeline.await_args.kwargs
    assert kwargs.get("require_all_rule_ids", False) is (scope == "selected")
    assert kwargs["triggered_by"] == (
        "m3u_refresh" if scope == "refresh" else
        "scheduled_all" if scope == "broad" and trigger == "scheduled" else
        "scheduled_selected" if scope == "selected" and trigger == "scheduled" else trigger
    )
    test_session.expire_all()
    history = test_session.query(TaskExecution).one()
    assert history.status == "completed"
    assert history.triggered_by == trigger
    assert task._invocation_run_all_rules is False
    assert task._invocation_schedule_id is None
    assert task.rule_ids == []
    if scope == "broad":
        page_rules = await loader._load_rules(None)
        assert loaded_ids == [rule.id for rule in page_rules]
        assert settings.last_auto_creation_consumed_refresh_at == ""
    elif scope == "refresh":
        assert settings.last_auto_creation_consumed_refresh_at == settings.last_m3u_refresh_completed_at


@pytest.mark.parametrize("config", [None, {}, {"auto_run": False}, {"auto_run": True}])
def test_explicit_broad_event_sync_scope_matches_manual_without_widening_refresh(config):
    from channel_pipeline_engine import event_sync_trigger_allowed

    assert event_sync_trigger_allowed("scheduled_all", config) is True
    assert event_sync_trigger_allowed("manual", config) is True
    assert event_sync_trigger_allowed("scheduled", config) is False
    assert event_sync_trigger_allowed("m3u_refresh", config) is bool(config and config.get("auto_run"))
