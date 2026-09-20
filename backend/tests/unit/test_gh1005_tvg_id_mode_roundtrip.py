"""GH #1005 / PR #1006 review item 4: ``tvg_id_mode: "none"`` survives the
whole path an operator actually exercises: save the rule, reload it from the
database, run the action under the planning client (prepare), replay the
recorded plan against Dispatcharr (commit). The same action's
``if_exists: update`` path must not back-fill the field.

The per-branch behaviour is covered in ``test_channel_pipeline_executor.py``;
this file pins the boundaries between persistence, planning and replay.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock

from channel_pipeline_evaluator import StreamContext
from channel_pipeline_executor import ActionExecutor, ExecutionContext
from channel_pipeline_schema import Action
from models import ChannelPipelineRule
from services.pipeline_write_plan import PlanningDispatcharrClient, replay_write_plan

STREAM_TVG_ID = "BossSports.HBO Max UK.017"


def _save_and_reload_rule(test_session, actions: list[dict]) -> list[dict]:
    rule = ChannelPipelineRule(
        name="GH1005 roundtrip",
        enabled=True,
        priority=0,
        conditions=json.dumps([{"type": "stream_name_contains", "value": "Snooker"}]),
        actions="[]",
        run_on_refresh=False,
        stop_on_first_match=True,
        sort_order="asc",
        orphan_action="delete",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    rule.set_actions(actions)
    test_session.add(rule)
    test_session.commit()
    rule_id = rule.id
    test_session.expire_all()
    reloaded = test_session.get(ChannelPipelineRule, rule_id)
    return reloaded.get_actions()


def _stream_ctx(name: str = "Snooker 1") -> StreamContext:
    return StreamContext(
        stream_id=301,
        stream_name=name,
        m3u_account_id=1,
        m3u_account_name="Provider A",
        group_name="Sports",
        tvg_id=STREAM_TVG_ID,
        resolution_height=1080,
        logo_url=None,
    )


def _plan_action(action: dict, *, existing_channels: list[dict]) -> tuple[PlanningDispatcharrClient, AsyncMock]:
    live = AsyncMock()
    if existing_channels:
        # PlanningDispatcharrClient.update_channel reads the channel through
        # the live client to record its precondition before recording the write.
        live.get_channel.return_value = dict(existing_channels[0])
    planner = PlanningDispatcharrClient(live)
    executor = ActionExecutor(
        planner,
        existing_channels=existing_channels,
        existing_groups=[{"id": 1, "name": "Sports"}],
    )
    result = asyncio.get_event_loop().run_until_complete(
        executor.execute(action, _stream_ctx(), ExecutionContext(dry_run=False))
    )
    assert result.success is True, result.error
    return planner, live


def _replay(plan) -> AsyncMock:
    commit_client = AsyncMock()
    commit_client.create_channel.return_value = {"id": 500, "name": "Snooker 1"}
    asyncio.get_event_loop().run_until_complete(
        replay_write_plan(commit_client, plan, read_set_validated=True)
    )
    return commit_client


class TestTvgIdModeNoneSurvivesPersistenceAndPlanning:
    def test_saved_rule_reloads_validates_and_plans_a_create_without_tvg_id(self, test_session):
        actions = _save_and_reload_rule(test_session, [
            {"type": "create_channel", "name_template": "{stream_name}",
             "group_id": 1, "tvg_id_mode": "none"},
        ])
        assert actions[0]["tvg_id_mode"] == "none"
        assert Action.from_dict(actions[0]).validate() == []

        planner, live = _plan_action(actions[0], existing_channels=[])

        creates = [w for w in planner.plan.writes if w.method == "create_channel"]
        assert len(creates) == 1
        recorded_payload = creates[0].args[0]
        assert "tvg_id" not in recorded_payload
        assert recorded_payload["name"] == "Snooker 1"
        live.create_channel.assert_not_awaited()  # planning never writes

        commit_client = _replay(planner.plan)
        commit_client.create_channel.assert_awaited_once()
        replayed_payload = commit_client.create_channel.await_args[0][0]
        assert "tvg_id" not in replayed_payload
        assert replayed_payload["name"] == "Snooker 1"

    def test_saved_rule_update_path_records_no_tvg_id_backfill(self, test_session):
        actions = _save_and_reload_rule(test_session, [
            {"type": "create_channel", "name_template": "{stream_name}",
             "if_exists": "update", "tvg_id_mode": "none"},
        ])
        existing = [{"id": 1, "name": "Snooker 1", "tvg_id": None, "channel_number": 100,
                     "streams": [301], "auto_created": True, "logo_url": None}]
        planner, _live = _plan_action(actions[0], existing_channels=existing)

        assert not any(w.method == "create_channel" for w in planner.plan.writes)
        backfills = [
            w for w in planner.plan.writes
            if w.method == "update_channel" and "tvg_id" in (w.args[1] or {})
        ]
        assert backfills == []
        assert existing[0]["tvg_id"] is None

    def test_inherit_control_plans_and_replays_the_stream_tvg_id(self, test_session):
        """Control: the fixture really carries a tvg_id, and a saved rule that
        omits the field still inherits it through the same boundaries."""
        actions = _save_and_reload_rule(test_session, [
            {"type": "create_channel", "name_template": "{stream_name}", "group_id": 1},
        ])
        assert "tvg_id_mode" not in actions[0]
        planner, _live = _plan_action(actions[0], existing_channels=[])
        recorded_payload = next(w for w in planner.plan.writes if w.method == "create_channel").args[0]
        assert recorded_payload["tvg_id"] == STREAM_TVG_ID

        commit_client = _replay(planner.plan)
        assert commit_client.create_channel.await_args[0][0]["tvg_id"] == STREAM_TVG_ID

    def test_inherit_control_update_path_backfills_through_planning(self, test_session):
        actions = _save_and_reload_rule(test_session, [
            {"type": "create_channel", "name_template": "{stream_name}", "if_exists": "update"},
        ])
        existing = [{"id": 1, "name": "Snooker 1", "tvg_id": None, "channel_number": 100,
                     "streams": [301], "auto_created": True, "logo_url": None}]
        planner, _live = _plan_action(actions[0], existing_channels=existing)
        backfills = [
            w for w in planner.plan.writes
            if w.method == "update_channel" and "tvg_id" in (w.args[1] or {})
        ]
        assert [(w.args[0], w.args[1]["tvg_id"]) for w in backfills] == [(1, STREAM_TVG_ID)]

    def test_saved_rule_with_explicit_null_reads_as_inherit(self, test_session):
        """JSON null in a stored rule is 'inherit', not 'none' (review item 5)."""
        actions = _save_and_reload_rule(test_session, [
            {"type": "create_channel", "name_template": "{stream_name}",
             "group_id": 1, "tvg_id_mode": None},
        ])
        assert actions[0]["tvg_id_mode"] is None
        assert Action.from_dict(actions[0]).validate() == []
        planner, _live = _plan_action(actions[0], existing_channels=[])
        recorded_payload = next(w for w in planner.plan.writes if w.method == "create_channel").args[0]
        assert recorded_payload["tvg_id"] == STREAM_TVG_ID
