import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.pipeline_write_plan import (
    PIPELINE_INTERNAL_SIDE_EFFECTS, PIPELINE_WRITE_METHODS, PlanningDispatcharrClient, PipelineWritePlan,
    PartialReplayError, PlannedWrite, ReplayOutcome, journal_entries_for_plan, replay_write_plan,
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
# Logo writes are cosmetic: a failed create_logo must not abort the replay.
# Dispatcharr answers 400 for a duplicate logo URL and logo rows outlive their
# channels, so a stale row from an earlier event cycle used to turn the whole
# commit into a PartialReplayError at write 0 with zero writes landed.
# ---------------------------------------------------------------------------


def _logo_then_channel_plan():
    return PipelineWritePlan(
        writes=[
            PlannedWrite("create_logo", [{"name": "Snooker", "url": "http://l/x.png"}], {}),
            PlannedWrite("create_channel", [{"name": "Snooker 1", "logo_id": -1, "streams": [5]}], {}),
            PlannedWrite("update_channel", [-2, {"streams": [5, 6]}], {}),
        ],
    )


@pytest.mark.asyncio
async def test_replay_continues_past_a_failed_logo_create_and_lands_the_channel():
    live = AsyncMock()
    live.create_logo.side_effect = Exception("Logo creation failed: 400 - duplicate url")
    live.create_channel.return_value = {"id": 101}
    outcome = ReplayOutcome()
    results, remap = await replay_write_plan(live, _logo_then_channel_plan(), outcome=outcome)
    assert remap == {-1: None, -2: 101}
    # The field that referenced the skipped logo is OMITTED, not sent as null.
    live.create_channel.assert_awaited_once_with({"name": "Snooker 1", "streams": [5]})
    live.update_channel.assert_awaited_once_with(101, {"streams": [5, 6]})
    assert results[0] is None
    live.delete_channel.assert_not_awaited()
    assert outcome.skipped == [
        {"index": 0, "method": "create_logo", "reason": "soft_failure", "error_type": "Exception"},
    ]


@pytest.mark.asyncio
async def test_replay_still_aborts_and_compensates_when_a_channel_create_fails():
    live = AsyncMock()
    live.create_logo.return_value = {"id": 765}
    live.create_channel.side_effect = Exception("boom")
    with pytest.raises(PartialReplayError) as info:
        await replay_write_plan(live, _logo_then_channel_plan())
    assert info.value.failed_index == 1
    assert info.value.completed == ["create_logo:{'name': 'Snooker', 'url': 'http://l/x.png'}"]
    live.update_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_uses_the_existing_logo_id_when_create_logo_resolves_a_duplicate():
    live = AsyncMock()
    live.create_logo.return_value = {"id": 765, "url": "http://l/x.png"}
    live.create_channel.return_value = {"id": 101}
    _, remap = await replay_write_plan(live, _logo_then_channel_plan())
    assert remap == {-1: 765, -2: 101}
    live.create_channel.assert_awaited_once_with({"name": "Snooker 1", "logo_id": 765, "streams": [5]})


@pytest.mark.asyncio
async def test_replay_against_real_client_survives_duplicate_logo_400(monkeypatch):
    """End to end through DispatcharrClient.create_logo with a colliding row.

    The pre-check MISSES (the row is not there yet), the POST answers 400 and
    the post-400 lookup then finds the row: this exercises the race branch,
    not the pre-check branch, and the request sequence asserts it.
    """
    import httpx
    from config import DispatcharrSettings
    from dispatcharr_client import DispatcharrClient

    client = DispatcharrClient(DispatcharrSettings(
        url="http://dispatcharr:8000", auth_method="password", username="a", password="b",
    ))
    existing = {"id": 765, "name": "old", "url": "http://l/x.png"}
    posts: list[tuple[str, dict]] = []
    sequence: list[tuple[str, str]] = []
    logo_gets = {"n": 0}

    def resp(status, body):
        r = AsyncMock(spec=httpx.Response)
        r.status_code = status
        r.json = lambda: body
        r.text = str(body)
        r.raise_for_status = lambda: None
        return r

    async def fake_request(method, path, **kwargs):
        sequence.append((method, path))
        if method == "GET" and path == "/api/channels/logos/":
            logo_gets["n"] += 1
            if logo_gets["n"] == 1:
                return resp(200, {"count": 0, "next": None, "results": []})  # pre-check miss
            return resp(200, {"count": 1, "next": None, "results": [existing]})  # after the 400
        if method == "POST" and path == "/api/channels/logos/":
            return resp(400, {"url": ["logo with this url already exists."]})
        if method == "POST" and path == "/api/channels/channels/":
            posts.append((path, kwargs["json"]))
            return resp(201, {"id": 101, **kwargs["json"]})
        if method == "PATCH":
            return resp(200, {"id": 101, **kwargs["json"]})
        raise AssertionError(f"unexpected {method} {path}")

    monkeypatch.setattr(client, "_request", AsyncMock(side_effect=fake_request))
    outcome = ReplayOutcome()
    _, remap = await replay_write_plan(client, _logo_then_channel_plan(), outcome=outcome)
    assert remap == {-1: 765, -2: 101}
    assert sequence[:3] == [
        ("GET", "/api/channels/logos/"), ("POST", "/api/channels/logos/"), ("GET", "/api/channels/logos/"),
    ]
    assert posts == [("/api/channels/channels/", {"name": "Snooker 1", "logo_id": 765, "streams": [5]})]
    assert outcome.reused == [0]
    assert outcome.skipped == []


# ---------------------------------------------------------------------------
# PR #1014 review items 2, 3 and 4.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_replacement_logo_preserves_an_existing_channels_artwork():
    """Item 2: a planned assign_logo on an EXISTING channel whose replacement
    logo failed must not PATCH logo_id to null."""
    live = AsyncMock()
    live.create_logo.side_effect = Exception("Logo creation failed: 400 (duplicate_url)")
    live.get_channel.return_value = {"id": 7, "name": "Existing", "logo_id": 99, "streams": []}
    plan = PipelineWritePlan(
        writes=[
            PlannedWrite("create_logo", [{"name": "L", "url": "http://l/new.png"}], {}),
            PlannedWrite("update_channel", [7, {"logo_id": -1}], {}),
        ],
        channel_preconditions={"7": {"id": 7, "name": "Existing", "logo_id": 99, "streams": []}},
    )
    outcome = ReplayOutcome()
    results, remap = await replay_write_plan(live, plan, outcome=outcome)
    live.update_channel.assert_not_awaited()
    assert remap == {-1: None}
    assert results == [None, None]
    assert [(e["index"], e["reason"]) for e in outcome.skipped] == [
        (0, "soft_failure"), (1, "dependency_skipped"),
    ]
    # And the journal reconstructs neither a logo create nor a channel update.
    assert journal_entries_for_plan(plan, remap, 11, outcome=outcome) == []


@pytest.mark.asyncio
async def test_failed_logo_still_lets_independent_structural_fields_apply():
    live = AsyncMock()
    live.create_logo.side_effect = Exception("Logo creation failed: 400 (duplicate_url)")
    live.get_channel.return_value = {"id": 7, "name": "Existing", "logo_id": 99, "streams": []}
    plan = PipelineWritePlan(
        writes=[
            PlannedWrite("create_logo", [{"name": "L", "url": "http://l/new.png"}], {}),
            PlannedWrite("update_channel", [7, {"logo_id": -1, "name": "Renamed"}], {}),
        ],
        channel_preconditions={"7": {"id": 7, "name": "Existing", "logo_id": 99, "streams": []}},
    )
    outcome = ReplayOutcome()
    _, remap = await replay_write_plan(live, plan, outcome=outcome)
    live.update_channel.assert_awaited_once_with(7, {"name": "Renamed"})
    entries = journal_entries_for_plan(plan, remap, 11, outcome=outcome)
    assert [e["action_type"] for e in entries] == ["update_channel"]
    assert entries[0]["after_value"] == {"name": "Renamed"}  # skipped logo ref omitted


@pytest.mark.asyncio
async def test_journal_has_no_phantom_success_row_for_a_skipped_or_reused_logo():
    """Item 3: finalization must not reconstruct 'executed create_logo for None'."""
    plan = _logo_then_channel_plan()
    skipped = ReplayOutcome(skipped=[{"index": 0, "method": "create_logo", "reason": "soft_failure"}])
    entries = journal_entries_for_plan(plan, {-1: None, -2: 101}, 11, outcome=skipped)
    assert [e["action_type"] for e in entries if e["action_type"] == "create_logo"] == []
    create_channel = next(e for e in entries if e["action_type"] == "create_channel")
    assert create_channel["entity_id"] == 101

    reused = ReplayOutcome(reused=[0])
    entries = journal_entries_for_plan(plan, {-1: 765, -2: 101}, 11, outcome=reused)
    assert [e["action_type"] for e in entries if e["action_type"] == "create_logo"] == []

    # Control: a genuinely created logo still gets its row.
    entries = journal_entries_for_plan(plan, {-1: 765, -2: 101}, 11, outcome=ReplayOutcome())
    assert [e["entity_id"] for e in entries if e["action_type"] == "create_logo"] == [765]


@pytest.mark.asyncio
async def test_failed_index_is_the_true_plan_position_after_soft_skips():
    """Item 4: [create_logo skipped, create_channel ok, update_channel fails] -> index 2."""
    live = AsyncMock()
    live.create_logo.side_effect = Exception("Logo creation failed: 400 (duplicate_url)")
    live.create_channel.return_value = {"id": 101}
    live.update_channel.side_effect = Exception("boom")
    plan = PipelineWritePlan(writes=[
        PlannedWrite("create_logo", [{"name": "L", "url": "http://l/x.png"}], {}),
        PlannedWrite("create_channel", [{"name": "New", "streams": [5]}], {}),
        PlannedWrite("update_channel", [-2, {"streams": [5, 6]}], {}),
    ])
    with pytest.raises(PartialReplayError) as info:
        await replay_write_plan(live, plan)
    assert info.value.failed_index == 2
    assert info.value.completed == ["create_channel:{'name': 'New', 'streams': [5]}"]
    live.delete_channel.assert_awaited_once_with(101)  # compensation unchanged


@pytest.mark.asyncio
async def test_positional_dependency_on_a_skipped_create_still_aborts():
    """Only payload FIELDS are optional; a write that targets the skipped
    resource itself cannot proceed."""
    live = AsyncMock()
    live.create_logo.side_effect = Exception("Logo creation failed: 500 (server_error)")
    plan = PipelineWritePlan(writes=[
        PlannedWrite("create_logo", [{"name": "L", "url": "http://l/x.png"}], {}),
        PlannedWrite("update_logo", [-1, {"name": "Renamed"}], {}),
    ])
    with pytest.raises(PartialReplayError) as info:
        await replay_write_plan(live, plan)
    assert info.value.failed_index == 1
    live.update_logo.assert_not_awaited()


@pytest.mark.asyncio
async def test_planning_client_records_create_logo_kwargs_for_replay():
    live = AsyncMock()
    planner = PlanningDispatcharrClient(live)
    await planner.create_logo({"name": "L", "url": "http://l/x.png"}, precheck=False)
    write = planner.plan.writes[0]
    assert (write.method, write.kwargs) == ("create_logo", {"precheck": False})
    replay_client = AsyncMock()
    replay_client.create_logo.return_value = {"id": 5}
    await replay_write_plan(replay_client, planner.plan)
    replay_client.create_logo.assert_awaited_once_with({"name": "L", "url": "http://l/x.png"}, precheck=False)
