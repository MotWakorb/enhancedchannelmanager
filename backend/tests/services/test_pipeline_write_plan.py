import ast
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from services.pipeline_write_plan import (
    PIPELINE_INTERNAL_SIDE_EFFECTS, PIPELINE_WRITE_METHODS, PlanningDispatcharrClient, PipelineWritePlan,
    PartialReplayError, PlannedWrite, replay_write_plan,
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


# ---------------------------------------------------------------------------
# GH #1009: a partial replay must say which write failed, what was and was
# not applied, and whether the failure provably happened before any mutation.
# ---------------------------------------------------------------------------


def _rate_limited() -> httpx.HTTPStatusError:
    response = httpx.Response(429, request=httpx.Request("PATCH", "http://dispatcharr/x"))
    return httpx.HTTPStatusError("429", request=response.request, response=response)


def _two_write_plan() -> PipelineWritePlan:
    return PipelineWritePlan(
        writes=[
            PlannedWrite("update_channel", [7, {"name": "A"}], {}),
            PlannedWrite("delete_channel", [8], {}),
        ],
    )


@pytest.mark.asyncio
async def test_first_write_rejected_with_429_is_reported_as_pre_mutation():
    live = AsyncMock()
    live.update_channel.side_effect = _rate_limited()
    with pytest.raises(PartialReplayError) as error:
        await replay_write_plan(live, _two_write_plan())
    exc = error.value
    assert exc.failed_index == 0
    assert exc.failed_write == "update_channel:7"
    assert exc.failed_outcome == "rejected"
    assert exc.completed == []
    assert exc.not_applied == ["delete_channel:8"]
    assert exc.pre_mutation is True
    live.delete_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_after_a_landed_write_is_not_pre_mutation():
    live = AsyncMock()
    live.update_channel.return_value = {"id": 7}
    live.delete_channel.side_effect = _rate_limited()
    with pytest.raises(PartialReplayError) as error:
        await replay_write_plan(live, _two_write_plan())
    exc = error.value
    assert exc.failed_index == 1
    assert exc.failed_write == "delete_channel:8"
    assert exc.failed_outcome == "rejected"
    assert exc.completed == ["update_channel:7"]
    assert exc.not_applied == []
    assert exc.pre_mutation is False


@pytest.mark.asyncio
async def test_first_write_timeout_is_not_claimed_pre_mutation():
    """A lost response may have landed upstream; never claim retry is safe."""
    live = AsyncMock()
    live.update_channel.side_effect = httpx.ReadTimeout("slow", request=httpx.Request("PATCH", "http://d/x"))
    with pytest.raises(PartialReplayError) as error:
        await replay_write_plan(live, _two_write_plan())
    assert error.value.failed_index == 0
    assert error.value.completed == []
    assert error.value.pre_mutation is False
    assert error.value.failed_outcome == "unknown"


# ---------------------------------------------------------------------------
# PR #1010 review items 1-3: outcome classes, resolved ids, safe descriptors.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_landed_write_with_lost_response_is_unknown_not_not_applied():
    """Item 1: a PATCH that reached upstream but whose response was lost may
    have landed. It must be reported as failed with an unknown outcome and
    must NOT appear in ``not_applied``; only the never-attempted delete is."""
    live = AsyncMock()
    live.update_channel.side_effect = httpx.RemoteProtocolError(
        "peer closed connection", request=httpx.Request("PATCH", "http://d/x"),
    )
    with pytest.raises(PartialReplayError) as error:
        await replay_write_plan(live, _two_write_plan())
    exc = error.value
    assert exc.failed_write == "update_channel:7"
    assert exc.failed_outcome == "unknown"
    assert exc.not_applied == ["delete_channel:8"]
    assert "update_channel:7" not in exc.not_applied
    assert exc.pre_mutation is False
    live.delete_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmed_rejection_control_is_rejected_and_pre_mutation():
    live = AsyncMock()
    live.update_channel.side_effect = httpx.HTTPStatusError(
        "400", request=httpx.Request("PATCH", "http://d/x"),
        response=httpx.Response(400, request=httpx.Request("PATCH", "http://d/x")),
    )
    with pytest.raises(PartialReplayError) as error:
        await replay_write_plan(live, _two_write_plan())
    assert error.value.failed_outcome == "rejected"
    assert error.value.pre_mutation is True
    assert error.value.not_applied == ["delete_channel:8"]


@pytest.mark.asyncio
async def test_dependent_update_failure_reports_the_resolved_upstream_id():
    """Item 2: the plan recorded the update against temp id -1; replay created
    channel 101 and PATCHed 101, so the failure must name 101, and the
    completed create must name the resource it produced."""
    live = AsyncMock()
    live.create_channel.return_value = {"id": 101}
    live.update_channel.side_effect = _rate_limited()
    plan = PipelineWritePlan(writes=[
        PlannedWrite("create_channel", [{"name": "New"}], {}),
        PlannedWrite("update_channel", [-1, {"streams": [5]}], {}),
        PlannedWrite("update_channel", [-1, {"name": "Renamed"}], {}),
    ])
    with pytest.raises(PartialReplayError) as error:
        await replay_write_plan(live, plan)
    exc = error.value
    assert exc.failed_index == 1
    assert exc.completed == ["create_channel#0->101"]
    assert exc.failed_write == "update_channel:101"
    assert exc.not_applied == ["update_channel:101"]
    assert "-1" not in exc.failed_write and "-1" not in "".join(exc.not_applied)
    live.delete_channel.assert_awaited_once_with(101)  # compensation targeted the real id


@pytest.mark.asyncio
async def test_compensation_failure_names_the_created_resource():
    live = AsyncMock()
    live.create_channel.return_value = {"id": 101}
    live.update_channel.side_effect = _rate_limited()
    live.delete_channel.side_effect = RuntimeError("upstream unavailable")
    plan = PipelineWritePlan(writes=[
        PlannedWrite("create_channel", [{"name": "New"}], {}),
        PlannedWrite("update_channel", [-1, {"streams": [5]}], {}),
    ])
    with pytest.raises(PartialReplayError) as error:
        await replay_write_plan(live, plan)
    assert error.value.completed == ["create_channel#0->101"]
    assert error.value.compensation_errors == ["create_channel: upstream unavailable"]
    assert error.value.pre_mutation is False


@pytest.mark.asyncio
async def test_unresolved_future_target_renders_as_pending_without_failing():
    live = AsyncMock()
    live.update_channel.side_effect = _rate_limited()
    plan = PipelineWritePlan(writes=[
        PlannedWrite("update_channel", [7, {"name": "A"}], {}),
        PlannedWrite("create_channel_group", ["Sports"], {}),
        PlannedWrite("create_channel", [{"name": "New"}], {}),
        PlannedWrite("update_channel", [-2, {"streams": [5]}], {}),
        PlannedWrite("assign_channel_numbers", [[7, -2], 100], {}),
    ])
    with pytest.raises(PartialReplayError) as error:
        await replay_write_plan(live, plan)
    assert error.value.not_applied == [
        "create_channel_group#1", "create_channel#2",
        "update_channel:pending(-2)", "assign_channel_numbers:[7,pending(-2)]",
    ]


@pytest.mark.asyncio
async def test_credential_bearing_create_payload_never_reaches_diagnostics():
    """Item 3: a create_logo payload URL can carry a provider token. No
    diagnostic field, and not str(exc), may contain it."""
    token = "SECRET-TOKEN-8f3a9c"
    live = AsyncMock()
    live.create_logo.side_effect = _rate_limited()
    plan = PipelineWritePlan(writes=[
        PlannedWrite("create_logo", [{"name": "L", "url": f"http://p.example/logo.png?token={token}"}], {}),
        PlannedWrite("create_channel", [{"name": "New", "tvg_id": token}], {}),
        PlannedWrite("update_channel", [-2, {"logo_id": -1}], {}),
    ])
    with pytest.raises(PartialReplayError) as error:
        await replay_write_plan(live, plan)
    exc = error.value
    assert exc.failed_write == "create_logo#0"
    assert exc.not_applied == ["create_channel#1", "update_channel:pending(-2)"]
    assert exc.pre_mutation is True
    rendered = " ".join([exc.failed_write, *exc.not_applied, *exc.completed, str(exc), repr(exc)])
    assert token not in rendered
    assert "http" not in rendered


@pytest.mark.asyncio
async def test_completed_create_descriptor_is_payload_free():
    token = "SECRET-TOKEN-8f3a9c"
    live = AsyncMock()
    live.create_logo.return_value = {"id": 55}
    live.create_channel.side_effect = _rate_limited()
    plan = PipelineWritePlan(writes=[
        PlannedWrite("create_logo", [{"name": "L", "url": f"http://p.example/logo.png?token={token}"}], {}),
        PlannedWrite("create_channel", [{"name": "New"}], {}),
    ])
    with pytest.raises(PartialReplayError) as error:
        await replay_write_plan(live, plan)
    assert error.value.completed == ["create_logo#0->55"]
    assert token not in " ".join(error.value.completed)


@pytest.mark.asyncio
async def test_mapping_error_before_the_call_is_a_rejection_not_unknown():
    """An unresolved temp id fails inside replay before upstream is contacted."""
    live = AsyncMock()
    plan = PipelineWritePlan(writes=[PlannedWrite("update_channel", [-9, {"name": "A"}], {})])
    with pytest.raises(PartialReplayError) as error:
        await replay_write_plan(live, plan)
    assert error.value.failed_outcome == "rejected"
    assert error.value.failed_write == "update_channel:pending(-9)"
    assert error.value.pre_mutation is True
    live.update_channel.assert_not_awaited()
