"""PR #1014 review item 3: a planned commit whose replay skipped a cosmetic
write persists truthful evidence, not a reconstruction in which every planned
operation succeeded.

Drives ``commit_auto_creation_pipeline`` directly (as the other planned-run
tests do) with a stored plan and a replay double that reports one skipped
``create_logo`` and one dependent ``update_channel`` through ``ReplayOutcome``.
Asserts the durable execution row and the journal rows handed to
``journal.log_entries``.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

from models import ChannelPipelineExecution


@pytest.mark.asyncio
async def test_skipped_replay_writes_leave_a_durable_warning_and_no_phantom_journal_row(test_engine):
    from routers import channel_pipeline as router
    from services import mutation_plan_store as store
    from services.mutation_plan_store import canonical_hash

    payload = {
        "request": {"m3u_account_ids": None, "rule_ids": [7]},
        "result": {
            "event_sync": [], "planned_review_candidates": [], "execution_log": [],
            "failed_actions": [], "channels_created": 1,
        },
        "write_plan": {
            "writes": [
                {"method": "create_logo", "args": [{"name": "L", "url": "http://l/x.png"}], "kwargs": {}, "event_sync": None},
                {"method": "create_channel", "args": [{"name": "New", "logo_id": -1, "streams": [5]}], "kwargs": {}, "event_sync": None},
                {"method": "update_channel", "args": [7, {"logo_id": -1}], "kwargs": {}, "event_sync": None},
            ],
            "channel_preconditions": {"7": {"id": 7, "name": "Existing", "logo_id": 99, "streams": []}},
            "group_preconditions": {}, "profile_preconditions": {},
        },
        "snapshot": [],
    }
    fresh_store = store.MutationPlanStore()
    plan = fresh_store.create(
        "channel_pipeline", payload, canonical_hash(router._canonical_pipeline_decision(payload)),
    )

    async def fake_replay(client, write_plan, *, outcome=None, **_kwargs):
        # What the real replay reports when the logo create fails upstream and
        # the update that only carried that logo is therefore not sent.
        outcome.skipped.append({"index": 0, "method": "create_logo", "reason": "soft_failure", "error_type": "Exception"})
        outcome.skipped.append({"index": 2, "method": "update_channel", "reason": "dependency_skipped"})
        return [None, {"id": 101}, None], {-1: None, -2: 101}

    engine = MagicMock(client=object())
    engine._load_rules = AsyncMock(return_value=[])
    engine._update_rule_stats = AsyncMock()
    logged: list = []

    with patch.object(store, "mutation_plan_store", fresh_store), \
         patch.object(router, "_ensure_engine", AsyncMock(return_value=engine)), \
         patch.object(router, "_compute_pipeline_plan_payload", AsyncMock(return_value=payload)), \
         patch("services.pipeline_write_plan.validate_read_set", AsyncMock()), \
         patch("services.pipeline_write_plan.replay_write_plan", AsyncMock(side_effect=fake_replay)), \
         patch.object(router.journal, "log_entries", side_effect=lambda entries: logged.extend(entries)), \
         patch.object(router, "get_session", sessionmaker(bind=test_engine)):
        response = await router.commit_auto_creation_pipeline(
            router.CommitPipelinePlanRequest(
                plan_id=plan.plan_id, plan_hash=plan.payload_hash, phase="execute",
            ),
            _admin=None,
        )

    assert response.status_code == 202
    import json
    execution_id = json.loads(response.body)["execution_id"]

    session = sessionmaker(bind=test_engine)()
    try:
        execution = session.get(ChannelPipelineExecution, execution_id)
        assert execution.status == "completed"  # the channel work did complete
        warnings = [w for w in execution.get_warnings() if w["type"] == "replay_write_skipped"]
        assert [(w["index"], w["method"], w["reason"]) for w in warnings] == [
            (0, "create_logo", "soft_failure"),
            (2, "update_channel", "dependency_skipped"),
        ]
        assert warnings[0]["error_type"] == "Exception"
        assert warnings[1]["channel_id"] == 7
        assert "existing values were left unchanged" in warnings[1]["message"]
        log_types = [entry.get("type") for entry in execution.get_execution_log()]
        assert log_types.count("replay_write_skipped") == 2
    finally:
        session.close()

    # Journal: the created channel is recorded; neither the skipped logo create
    # nor the unsent update gets a success row, and no row says "for None".
    assert [e["action_type"] for e in logged] == ["create_channel"]
    assert logged[0]["entity_id"] == 101
    assert not any("None" in (e.get("description") or "") for e in logged)
