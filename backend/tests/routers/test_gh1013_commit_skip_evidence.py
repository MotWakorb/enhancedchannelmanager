"""PR #1014 review item 3: a planned commit whose replay skipped a cosmetic
write persists truthful evidence, not a reconstruction in which every planned
operation succeeded.

The skipped writes, the warnings and the journal rows asserted here come from
the PRODUCTION replay path: ``commit_auto_creation_pipeline`` runs the real
``replay_write_plan`` against the real ``DispatcharrClient`` over a controlled
``httpx.MockTransport`` whose logo endpoint rejects the create and whose
catalog lookups cannot reconcile it. Nothing synthesises a ``ReplayOutcome``.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient
from models import ChannelPipelineExecution


def _upstream(record: list[tuple[str, str, dict | None]]):
    channels = {7: {"id": 7, "name": "Existing", "logo_id": 99, "streams": []}}

    def handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else None
        record.append((method, path, body))
        if path == "/api/channels/logos/" and method == "GET":
            return httpx.Response(200, json={"count": 0, "next": None, "results": []})
        if path == "/api/channels/logos/" and method == "POST":
            return httpx.Response(400, json={"url": ["logo with this url already exists."]})
        if path == "/api/channels/channels/" and method == "POST":
            channels[101] = {"id": 101, **body}
            return httpx.Response(201, json=channels[101])
        if path == "/api/channels/channels/7/" and method == "GET":
            return httpx.Response(200, json=channels[7])
        if path == "/api/channels/channels/7/" and method == "PATCH":
            channels[7].update(body)
            return httpx.Response(200, json=channels[7])
        raise AssertionError(f"unexpected upstream call: {method} {path}")

    client = DispatcharrClient(
        DispatcharrSettings(url="http://dispatcharr", auth_method="api_key", api_key="k")
    )
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client, channels


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

    record: list = []
    client, channels = _upstream(record)
    engine = SimpleNamespace(
        client=client, _load_rules=AsyncMock(return_value=[]), _update_rule_stats=AsyncMock(),
    )
    logged: list = []

    try:
        with patch.object(store, "mutation_plan_store", fresh_store), \
             patch.object(router, "_ensure_engine", AsyncMock(return_value=engine)), \
             patch.object(router, "_compute_pipeline_plan_payload", AsyncMock(return_value=payload)), \
             patch.object(router.journal, "log_entries", side_effect=lambda entries: logged.extend(entries)), \
             patch.object(router, "get_session", sessionmaker(bind=test_engine)):
            response = await router.commit_auto_creation_pipeline(
                router.CommitPipelinePlanRequest(
                    plan_id=plan.plan_id, plan_hash=plan.payload_hash, phase="execute",
                ),
                _admin=None,
            )
    finally:
        await client._client.aclose()

    assert response.status_code == 202
    execution_id = json.loads(response.body)["execution_id"]

    # What upstream actually received: one rejected logo POST, one channel
    # create WITHOUT a logo field, and no PATCH to channel 7 at all.
    assert ("POST", "/api/channels/logos/", {"name": "L", "url": "http://l/x.png"}) in record
    channel_posts = [b for m, p, b in record if m == "POST" and p == "/api/channels/channels/"]
    assert channel_posts == [{"name": "New", "streams": [5]}]
    assert not any(m == "PATCH" for m, _, _ in record)
    assert channels[7]["logo_id"] == 99

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
