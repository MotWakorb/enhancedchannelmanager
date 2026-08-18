from services.pipeline_write_plan import PipelineWritePlan, PlannedWrite, journal_entries_for_plan
from datetime import datetime
from database import get_session
from models import ChannelPipelineExecution, ChannelPipelineSnapshot
from routers.channel_pipeline import _mark_execution_failed, _planned_run_warnings
import pytest


def test_sequential_channel_journal_uses_evolving_shadow_state():
    plan = PipelineWritePlan(
        channel_preconditions={"7": {"id": 7, "name": "old", "streams": [1], "channel_number": 10}},
        writes=[
            PlannedWrite("update_channel", [7, {"name": "new"}], {}),
            PlannedWrite("update_channel", [7, {"streams": [1, 2]}], {}),
        ],
    )
    entries = journal_entries_for_plan(plan, {}, 11)
    second = next(item for item in entries if item["action_type"] == "merge_stream")
    assert second["entity_name"] == "new"
    assert second["before_value"] == {"stream_ids": [1]}


def test_number_assignment_journal_records_each_exact_old_and_new_value():
    plan = PipelineWritePlan(
        channel_preconditions={
            "7": {"id": 7, "channel_number": 40},
            "8": {"id": 8, "channel_number": 50},
        },
        writes=[PlannedWrite("assign_channel_numbers", [[7, 8], 100], {})],
    )
    entries = journal_entries_for_plan(plan, {}, 11)
    assert [(entry["before_value"], entry["after_value"]) for entry in entries] == [
        ({"channel_number": 40}, {"channel_number": 100}),
        ({"channel_number": 50}, {"channel_number": 101}),
    ]


@pytest.mark.asyncio
async def test_partial_replay_evidence_is_durable_on_execution_and_snapshot(async_client):
    session = get_session()
    try:
        execution = ChannelPipelineExecution(
            mode="execute", triggered_by="api", started_at=datetime.utcnow(), status="running"
        )
        session.add(execution)
        session.flush()
        snapshot = ChannelPipelineSnapshot(execution_id=execution.id, channel_count=1)
        snapshot.set_channels_data({"channels": [{"id": 7}]})
        session.add(snapshot)
        session.commit()
        execution_id = execution.id
    finally:
        session.close()

    evidence = {
        "failed_index": 1,
        "completed_targets": ["update_channel:7"],
        "compensation_errors": ["update_channel: upstream unavailable"],
    }
    _mark_execution_failed(execution_id, RuntimeError("replay stopped"), partial_replay=evidence)

    session = get_session()
    try:
        execution = session.get(ChannelPipelineExecution, execution_id)
        snapshot = session.query(ChannelPipelineSnapshot).filter_by(execution_id=execution_id).one()
        assert execution.status == "failed"
        assert execution.get_execution_log()[0]["completed_targets"] == ["update_channel:7"]
        assert snapshot.get_channels_data()["partial_replay"] == evidence
    finally:
        session.close()


def test_planned_warning_variants_survive_into_durable_execution_surface():
    warnings = _planned_run_warnings({
        "normalization_warnings": [{"type": "normalization", "message": "disabled"}],
        "event_sync_warnings": [{"type": "event_sync", "message": "capped"}],
        "non_reversible_channel_ids": {9, 7},
        "profile_ownership_unestablished_channel_ids": {11},
    })
    assert [item["type"] for item in warnings] == [
        "normalization", "event_sync", "non_reversible_profile_changes",
        "profile_ownership_not_established",
    ]
    assert warnings[2]["channel_ids"] == [7, 9]
