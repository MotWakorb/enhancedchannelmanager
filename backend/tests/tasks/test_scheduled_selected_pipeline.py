"""Scheduled Channel Pipeline selections execute exactly or fail closed."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from selected_pipeline_rules import SelectedRuleValidationError
from task_engine import TaskEngine
from tasks.channel_pipeline import ChannelPipelineTask


def _rule(rule_id: int, name: str, priority: int, *, event_sync: bool = False):
    rule = MagicMock()
    rule.id = rule_id
    rule.name = name
    rule.priority = priority
    rule.is_event_sync.return_value = event_sync
    return rule


def test_schedule_parameters_require_unique_nonempty_rule_ids():
    for parameters in ({"rule_ids": []}, {"rule_ids": [1, 1]}, {"rule_ids": [True]}):
        with pytest.raises(ValueError):
            ChannelPipelineTask.validate_schedule_parameters(parameters)


def test_schedule_parameters_validate_the_complete_selection():
    rules = [_rule(2, "Later", 20), _rule(1, "First", 10)]
    with patch(
        "selected_pipeline_rules.load_selected_rule_snapshots",
        return_value=rules,
    ) as load:
        ChannelPipelineTask.validate_schedule_parameters({"rule_ids": [2, 1]})

    load.assert_called_once_with([2, 1])


@pytest.mark.asyncio
async def test_scheduled_selection_dispatches_canonical_snapshot_with_origin():
    first = _rule(3, "First", 5)
    event = _rule(8, "Event", 10, event_sync=True)
    engine = AsyncMock()
    engine.run_pipeline.return_value = {
        "success": True,
        "status": "completed",
        "execution_id": 44,
        "streams_evaluated": 4,
        "streams_matched": 2,
        "channels_created": 1,
        "channels_updated": 0,
        "groups_created": 0,
        "streams_merged": 0,
        "pending_merges_added": 0,
        "conflicts": [],
        "event_sync": [{"attached": 1}],
    }
    task = ChannelPipelineTask()
    task.set_run_trigger("scheduled")
    task.prepare_invocation_parameters("scheduled", 17, {"rule_ids": [8, 3]})
    task.update_config({"rule_ids": [8, 3]})

    with patch(
        "selected_pipeline_rules.load_selected_rule_snapshots",
        return_value=[first, event],
    ), patch(
        "channel_pipeline_engine.get_channel_pipeline_engine",
        return_value=engine,
    ):
        result = await task.execute()

    assert result.success is True
    assert result.details["scheduled_task_id"] == "auto_creation"
    assert result.details["schedule_id"] == 17
    assert result.details["selected_rule_ids"] == [3, 8]
    engine.run_pipeline.assert_awaited_once_with(
        dry_run=False,
        triggered_by="scheduled_selected",
        m3u_account_ids=None,
        rule_ids=[3, 8],
        require_all_rule_ids=True,
        execution_origin={"scheduled_task_id": "auto_creation", "schedule_id": 17},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["deleted", "disabled", "invalid"])
async def test_stale_scheduled_selection_fails_without_run_all_fallback(reason):
    task = ChannelPipelineTask()
    task.set_run_trigger("scheduled")
    task.prepare_invocation_parameters("scheduled", 9, {"rule_ids": [4, 7]})
    task.update_config({"rule_ids": [4, 7]})
    pipeline_engine = AsyncMock()
    error = SelectedRuleValidationError(
        "selected_rules_not_runnable",
        "Every selected rule must be runnable",
        issues=[{"rule_id": 7, "reason": reason}],
    )

    with patch(
        "selected_pipeline_rules.load_selected_rule_snapshots",
        side_effect=error,
    ), patch(
        "channel_pipeline_engine.get_channel_pipeline_engine",
        return_value=pipeline_engine,
    ):
        result = await task.execute()

    assert result.success is False
    assert result.details["selected_rule_ids"] == [4, 7]
    assert result.details["schedule_id"] == 9
    assert reason in result.message
    pipeline_engine.run_pipeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_now_uses_disabled_schedule_selection_by_existing_convention(monkeypatch):
    schedule = SimpleNamespace(
        id=23,
        task_id="auto_creation",
        enabled=False,
        get_parameters=lambda: {"rule_ids": [5]},
    )
    query = MagicMock()
    query.filter.return_value.first.return_value = schedule
    session = MagicMock()
    session.query.return_value = query
    monkeypatch.setattr("database.get_session", lambda: session)
    task_engine = TaskEngine()
    task_engine._execute_task = AsyncMock(return_value=SimpleNamespace(completed_at=datetime.utcnow()))

    await task_engine.run_task("auto_creation", schedule_id=23)

    task_engine._execute_task.assert_awaited_once_with(
        "auto_creation",
        triggered_by="manual",
        parameters={"rule_ids": [5]},
        schedule_id=23,
    )


def test_pipeline_history_exposes_schedule_origin_and_exact_scope():
    from models import ChannelPipelineExecution

    execution = ChannelPipelineExecution(
        mode="execute",
        triggered_by="scheduled_selected",
        started_at=datetime.utcnow(),
        status="completed",
    )
    execution.set_execution_log([{
        "type": "scheduled_task_origin",
        "scheduled_task_id": "auto_creation",
        "schedule_id": 17,
    }])
    execution.set_selected_rule_outcomes([
        {"rule_id": 3, "rule_name": "First", "rule_kind": "standard", "status": "completed"},
        {"rule_id": 8, "rule_name": "Event", "rule_kind": "event_sync", "status": "completed"},
    ])

    payload = execution.to_dict()

    assert payload["scheduled_task_id"] == "auto_creation"
    assert payload["schedule_id"] == 17
    assert payload["selected_rule_ids"] == [3, 8]
    assert payload["run_scope"] == "selected"
