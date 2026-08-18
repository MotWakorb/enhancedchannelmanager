import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.pipeline_write_plan import (
    PIPELINE_INTERNAL_SIDE_EFFECTS, PIPELINE_WRITE_METHODS, PlanningDispatcharrClient, PipelineWritePlan,
    PlannedWrite, replay_write_plan,
)


def test_authoritative_accounting_counts_nested_operations_and_unique_targets():
    plan = PipelineWritePlan(writes=[
        PlannedWrite("assign_channel_numbers", [[1, 2, 3], 10], {}),
        PlannedWrite("update_channel", [1, {"name": "x"}], {}),
        PlannedWrite("update_profile_channel", [9, 2, {"enabled": True}], {}),
        PlannedWrite("create_channel", [{"name": "new"}], {}),
    ])
    assert plan.accounting() == {"write_count": 4, "unique_target_count": 4}


def test_every_pipeline_dispatcharr_write_chokepoint_is_recorded():
    root = Path(__file__).parents[2]
    discovered = set()
    for filename in (root / "channel_pipeline_engine.py", root / "channel_pipeline_executor.py"):
        tree = ast.parse(filename.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                if isinstance(owner, ast.Attribute) and owner.attr == "client":
                    if node.func.attr.startswith(("create_", "update_", "delete_", "assign_")):
                        discovered.add(node.func.attr)
    assert discovered <= PIPELINE_WRITE_METHODS


def test_pipeline_side_effect_ast_inventory_is_not_limited_to_client_calls():
    root = Path(__file__).parents[2]
    sink_names = {
        "commit", "log_entries", "probe_stream", "_batch_probe_streams",
        "_probe_unprobed_streams", "_capture_snapshot", "_save_execution",
        "_update_rule_stats", "_record_conflict", "_refresh_dummy_epg_and_retry",
        "_prerefresh_event_sync_providers",
    }
    discovered = set()
    for filename in (root / "channel_pipeline_engine.py", root / "channel_pipeline_executor.py"):
        tree = ast.parse(filename.read_text())
        discovered.update(
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in sink_names
        )
    # Dangerous mutant: removing the broad commit/journal/prober inventory
    # makes this fail even if every ``self.client`` write remains wrapped.
    assert {"commit", "log_entries", "probe_stream"} <= discovered


def test_internal_side_effect_parity_inventory_is_explicit_and_complete():
    assert PIPELINE_INTERNAL_SIDE_EFFECTS == {
        "execution_record", "rollback_snapshot", "journal_entries",
        "event_review_candidates", "rule_statistics", "conflict_records",
        "database_commit", "managed_channel_ledger", "stream_probe",
        "provider_refresh", "dummy_epg_refresh", "xmltv_cache",
        "notification", "live_data_refresh",
    }


@pytest.mark.asyncio
async def test_recorder_uses_deterministic_temp_ids_and_never_writes():
    live = AsyncMock()
    planner = PlanningDispatcharrClient(live)
    one = await planner.create_channel({"name": "One"})
    two = await planner.create_channel_group("Two")
    assert (one["id"], two["id"]) == (-1, -2)
    live.create_channel.assert_not_awaited()
    live.create_channel_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_validates_all_preconditions_before_first_write_and_remaps_ids():
    live = AsyncMock()
    live.get_channel.return_value = {"id": 7, "name": "Old", "streams": []}
    live.create_channel.return_value = {"id": 101}
    plan = PipelineWritePlan(
        writes=[
            PlannedWrite("create_channel", [{"name": "New"}], {}),
            PlannedWrite("update_channel", [-1, {"streams": [5]}], {}),
        ],
        channel_preconditions={"7": {"id": 7, "name": "Old", "streams": []}},
    )
    _, remap = await replay_write_plan(live, plan)
    assert remap == {-1: 101}
    live.update_channel.assert_awaited_once_with(101, {"streams": [5]})


@pytest.mark.asyncio
async def test_drift_rejects_before_any_replay_write():
    live = AsyncMock()
    live.get_channel.return_value = {"id": 7, "name": "Changed", "streams": []}
    plan = PipelineWritePlan(
        writes=[PlannedWrite("delete_channel", [7], {})],
        channel_preconditions={"7": {"id": 7, "name": "Old", "streams": []}},
    )
    with pytest.raises(ValueError, match="drifted"):
        await replay_write_plan(live, plan)
    live.delete_channel.assert_not_awaited()
