"""60r8c: file-backed SQLite and stateful upstream, never a live server."""
import copy
import json
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models import ChannelPipelineRule, JournalEntry
from services import event_sync_cleanup as cleanup


OLD = "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
NEW = "Peacock 11: IMSA CTMP Qualifying @ 12 Jul 03:55 PM ET"
CONFIG = {"master_group_id": 10, "secondary_group_ids": [20],
          "detach_stale_streams": True}


class Upstream:
    def __init__(self):
        self.channel = {"id": 100, "uuid": str(uuid4()), "name": OLD,
                        "channel_group_id": 10, "auto_created": True,
                        "auto_created_by": 17, "streams": [1]}
        self.streams = {
            1: {"id": 1, "name": OLD, "m3u_account": 17, "channel_group": 10},
            2: {"id": 2, "name": OLD, "m3u_account": 18, "channel_group": 20},
            3: {"id": 3, "name": NEW, "m3u_account": 18, "channel_group": 20},
        }
        self.writes = []
        self.fail = False
        self.malformed = False

    async def get_channel(self, cid):
        assert cid == 100
        return copy.deepcopy(self.channel)

    async def get_channels(self, **kwargs):
        return {"results": [copy.deepcopy(self.channel)], "next": None, "count": 1}

    async def get_streams(self, **kwargs):
        if self.malformed:
            return {"next": None}
        rows = [s for s in self.streams.values() if s["channel_group"] == 20]
        return {"results": copy.deepcopy(rows), "next": None, "count": len(rows)}

    async def get_streams_by_ids(self, ids):
        return [copy.deepcopy(self.streams[i]) for i in ids if i in self.streams]

    async def _channel_group_name_for_id(self, gid):
        return str(gid)

    async def get_all_m3u_group_settings(self):
        return {10: {"auto_channel_sync": True}, 20: {"auto_channel_sync": False}}

    async def get_m3u_accounts(self):
        return [{"id": 17}, {"id": 18}]

    async def update_channel(self, cid, payload):
        assert set(payload) == {"streams"}
        self.writes.append(copy.deepcopy(payload))
        self.channel.update(copy.deepcopy(payload))
        if self.fail:
            raise TimeoutError("response lost after upstream applied write")
        return copy.deepcopy(self.channel)


@pytest.fixture
def db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'cleanup.db'}"
    engine = create_engine(url)
    ChannelPipelineRule.__table__.create(engine)
    JournalEntry.__table__.create(engine)
    monkeypatch.setattr(cleanup, "get_database_url", lambda: url)
    with Session(engine) as session:
        rule = ChannelPipelineRule(id=7, name="Events", conditions="[]", actions="[]",
                                   event_sync_config=json.dumps(CONFIG), created_at=datetime(2026, 9, 5))
        session.add(rule)
        session.commit()
    monkeypatch.setattr(cleanup, "load_review_decisions", lambda *args: None)
    monkeypatch.setattr(cleanup, "load_exclusion_keys", lambda *args: frozenset())
    monkeypatch.setattr(cleanup.event_sync_staleness, "previous_day_names", lambda *args: {})
    yield engine
    engine.dispose()


async def attach(upstream, batch="1", sid=2):
    proof = cleanup.rule_identity(7)
    operation = {**proof, "channel_id": 100, "channel_uuid": upstream.channel["uuid"],
                 "stream_id": sid, "stream_account": upstream.streams[sid]["m3u_account"],
                 "action": "attach", "config": CONFIG}
    await cleanup.apply_change(upstream, operation, batch)


@pytest.mark.asyncio
async def test_rename_zero_replacement_and_surgical_rollback(db):
    upstream = Upstream()
    await attach(upstream)
    upstream.channel["name"] = NEW
    plan = await cleanup.plan_cleanup(upstream, 7, CONFIG)
    assert [(r["stream_id"], r["decision"]) for r in plan["decisions"]] == [(1, "preserve"), (2, "would_detach")]
    assert len(upstream.writes) == 1  # preview did not mutate
    await cleanup.apply_change(upstream, plan["operations"][0], "2")
    assert upstream.channel["streams"] == [1]
    assert not (await cleanup.plan_cleanup(upstream, 7, CONFIG))["operations"]
    await attach(upstream, "2", 3)
    upstream.channel["streams"].append(99)  # unrelated current membership
    result = await cleanup.rollback_batch(upstream, "2")
    assert result["success"] is True
    assert upstream.channel["streams"] == [1, 99, 2]
    with Session(db) as session:
        states = [json.loads(r.after_value)["state"] for r in session.query(JournalEntry)]
        assert "reverted" in states


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,reason", [
    ("auto_created", False, "native"), ("auto_created", 1, "native"),
    ("auto_created_by", None, "owner"), ("auto_created_by", True, "owner"),
    ("auto_created_by", 18, "same_account"), ("uuid", "bad", "identity"),
])
async def test_native_authority_preserves_unknown_and_same_account(db, field, value, reason):
    upstream = Upstream()
    await attach(upstream)
    upstream.channel.update({"name": NEW, field: value})
    plan = await cleanup.plan_cleanup(upstream, 7, CONFIG)
    assert not plan["operations"]
    assert any(reason in r["reason"] for r in plan["decisions"])


@pytest.mark.asyncio
async def test_noop_manual_and_expired_proof_are_not_adopted(db):
    upstream = Upstream()
    upstream.channel["streams"].append(2)
    await attach(upstream)
    assert not upstream.writes
    upstream.channel["name"] = NEW
    plan = await cleanup.plan_cleanup(upstream, 7, CONFIG)
    assert not plan["operations"]
    assert any("ownership" in r["reason"] for r in plan["decisions"])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["malformed", "parse", "rule_reused", "expired", "other_rule", "account", "missing"])
async def test_uncertainty_preserves_and_explains(db, failure):
    upstream = Upstream()
    await attach(upstream)
    upstream.channel["name"] = NEW
    if failure == "malformed":
        upstream.malformed = True
    elif failure == "parse":
        upstream.channel["name"] = "NO EVENT"
    elif failure == "account":
        upstream.streams[2]["m3u_account"] = None
    elif failure == "missing":
        del upstream.streams[2]
    else:
        with Session(db) as session:
            if failure == "rule_reused":
                session.get(ChannelPipelineRule, 7).created_at = datetime(2026, 9, 6)
            elif failure == "expired":
                session.query(JournalEntry).delete()
            else:
                row = session.query(JournalEntry).one()
                data = json.loads(row.after_value)
                data["operation"]["rule_id"] = 8
                row.after_value = json.dumps(data)
            session.commit()
    plan = await cleanup.plan_cleanup(upstream, 7, CONFIG)
    assert not plan["operations"]
    assert plan["decisions"] or plan["error"]


@pytest.mark.asyncio
async def test_intent_failure_and_uncertain_http_never_false_success(db, monkeypatch):
    upstream = Upstream()
    await attach(upstream)
    upstream.channel["name"] = NEW
    operation = (await cleanup.plan_cleanup(upstream, 7, CONFIG))["operations"][0]
    original = cleanup.write_intent
    monkeypatch.setattr(cleanup, "write_intent", lambda *args: (_ for _ in ()).throw(RuntimeError("disk full")))
    with pytest.raises(RuntimeError, match="disk full"):
        await cleanup.apply_change(upstream, operation, "2")
    assert upstream.channel["streams"] == [1, 2]
    monkeypatch.setattr(cleanup, "write_intent", original)
    upstream.fail = True
    with pytest.raises(RuntimeError, match="uncertain"):
        await cleanup.apply_change(upstream, operation, "2")
    assert (await cleanup.rollback_batch(upstream, "2"))["success"] is False
    assert not (await cleanup.plan_cleanup(upstream, 7, CONFIG))["operations"]


@pytest.mark.asyncio
async def test_off_does_not_read_or_mutate(db):
    assert await cleanup.plan_cleanup(None, 7, {}) == {"decisions": [], "operations": [], "error": None}


def test_recorded_channel_shape_is_not_a_singular_parent():
    # Captured 0.28.2 shape: test_channels_importer.py:361-396; representative IDs.
    recorded = json.loads('''{"id":12411,"channel_number":50.0,
      "name":"FloRacing 24/7 (FloRacing 247)","channel_group_id":3195,
      "streams":[],"uuid":"08f14609-a078-4af9-9af1-81b1c7d3fff3",
      "auto_created":true,"auto_created_by":17,"auto_created_by_name":"Infinity",
      "source_stream":{"id":39491,"name":"FloRacing 24/7 (FloRacing 247)",
      "account_id":17,"account_name":"Infinity"}}''')
    assert cleanup.native_guard(recorded, {"m3u_account": 17}) == "same_account_protected"
    assert cleanup.native_guard(recorded, {"m3u_account": 18}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [False, True])
async def test_executor_lifecycle_normal_and_prepared(db, prepared):
    from channel_pipeline_executor import ActionExecutor, ExecutionContext
    from services.event_sync_resolver import SecondaryStream
    from services.pipeline_write_plan import PlanningDispatcharrClient, replay_write_plan

    upstream = Upstream()

    async def run(batch, sid):
        client = PlanningDispatcharrClient(upstream) if prepared else upstream
        executor = ActionExecutor(client, existing_channels=[copy.deepcopy(upstream.channel)],
                                  existing_groups=[], execution_id=int(batch), plan_only=prepared)
        stream = upstream.streams[sid]
        context = ExecutionContext(dry_run=False)
        summary = await executor.execute_event_sync_rule(
            7, "Events", CONFIG, [SecondaryStream(name=stream["name"], group_id=20,
                                                 stream_id=sid, provider_id=18)], context,
        )
        if prepared:
            before = len(upstream.writes)
            assert upstream.channel["streams"] == ([1] if batch == "1" else [1, 2])
            with Session(db) as session:
                assert session.query(JournalEntry).count() == (0 if batch == "1" else 1)
            await replay_write_plan(upstream, client.plan, execution_id=int(batch))
            assert len(upstream.writes) > before
        return summary

    await run("1", 2)
    assert upstream.channel["streams"] == [1, 2]
    upstream.channel["name"] = NEW
    summary = await run("2", 3)
    assert upstream.channel["streams"] == [1, 3]
    assert summary["cleanup"]["decisions"][1]["decision"] == ("would_detach" if prepared else "detached")
    upstream.channel["streams"].append(99)
    assert (await cleanup.rollback_batch(upstream, "2"))["success"]
    assert upstream.channel["streams"] == [1, 99, 2]


def test_option_validation_is_strict_and_default_absent():
    from channel_pipeline_schema import validate_event_sync_config
    config = {"master_group_id": 10, "secondary_group_ids": [20]}
    assert not validate_event_sync_config(config)
    assert not config.get("detach_stale_streams", False)
    assert not validate_event_sync_config(dict(CONFIG))
    assert validate_event_sync_config({**CONFIG, "detach_stale_streams": "true"})


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [False, True])
@pytest.mark.parametrize("expired", [False, True])
async def test_engine_rollback_uses_surgical_journal_even_with_snapshot(db, monkeypatch, snapshot, expired):
    import channel_pipeline_engine as module
    from models import ChannelPipelineExecution, ChannelPipelineSnapshot
    ChannelPipelineExecution.__table__.create(db)
    ChannelPipelineSnapshot.__table__.create(db)
    monkeypatch.setattr(module, "get_session", lambda: Session(db))
    upstream = Upstream()
    await attach(upstream)
    upstream.channel["name"] = NEW
    plan = await cleanup.plan_cleanup(upstream, 7, CONFIG)
    await cleanup.apply_change(upstream, plan["operations"][0], "2")
    await attach(upstream, "2", 3)
    with Session(db) as session:
        execution = ChannelPipelineExecution(id=2, started_at=datetime.utcnow(), status="completed")
        execution.set_modified_entities([
            {"type": "channel", "id": 100, "previous": {"streams": [1, 2]}},
            {"type": "channel", "id": 100, "previous": {"streams": [1]}},
        ])
        execution.set_event_sync_summary([{"cleanup": {"decisions": []}}])
        session.add(execution)
        if snapshot:
            snap = ChannelPipelineSnapshot(execution_id=2, channel_count=1)
            snap.set_channels_data({"channels": [{**upstream.channel, "stream_ids": [1, 2]}]})
            session.add(snap)
        if expired:
            session.query(JournalEntry).filter(JournalEntry.batch_id == "2").delete()
        session.commit()
    upstream.channel["streams"].append(99)
    result = await module.ChannelPipelineEngine(upstream).rollback_execution(2, confirm=True)
    assert result["success"] is not expired
    assert upstream.channel["streams"] == ([1, 3, 99] if expired else [1, 99, 2])
    with Session(db) as session:
        assert session.get(ChannelPipelineExecution, 2).status == ("completed" if expired else "rolled_back")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["group", "settings", "missing_group_settings", "aliases", "reviews", "staleness", "truncated", "duplicate", "count_bool"])
async def test_failed_dependencies_and_incomplete_pages_cannot_authorize_detach(db, monkeypatch, failure):
    from unittest.mock import AsyncMock
    upstream = Upstream()
    await attach(upstream)
    upstream.channel["name"] = NEW
    if failure == "group":
        upstream._channel_group_name_for_id = AsyncMock(return_value=None)
    elif failure == "settings":
        upstream.get_all_m3u_group_settings = AsyncMock(side_effect=RuntimeError("unavailable"))
    elif failure == "missing_group_settings":
        upstream.get_all_m3u_group_settings = AsyncMock(return_value={})
    elif failure in {"aliases", "reviews", "staleness"}:
        def fail(*args):
            raise RuntimeError("unavailable")
        if failure == "aliases":
            monkeypatch.setattr(cleanup, "get_settings", fail)
        elif failure == "reviews":
            monkeypatch.setattr(cleanup, "load_review_decisions", fail)
        else:
            monkeypatch.setattr(cleanup.event_sync_staleness, "previous_day_names", fail)
    else:
        rows = [upstream.streams[2]]
        response = {"count": 2, "results": rows, "next": None}
        if failure == "duplicate":
            response["results"] = rows * 2
        elif failure == "count_bool":
            response["count"] = True
        upstream.get_streams = AsyncMock(return_value=response)
    plan = await cleanup.plan_cleanup(upstream, 7, CONFIG)
    assert not plan["operations"]
    assert plan["error"]
    assert len(upstream.writes) == 1


@pytest.mark.asyncio
async def test_fresh_read_after_intent_preserves_new_unrelated_membership(db, monkeypatch):
    upstream = Upstream()
    await attach(upstream)
    upstream.channel["name"] = NEW
    op = (await cleanup.plan_cleanup(upstream, 7, CONFIG))["operations"][0]
    original = cleanup.write_intent
    def interleave(*args):
        rid = original(*args)
        upstream.channel["streams"].insert(1, 99)
        return rid
    monkeypatch.setattr(cleanup, "write_intent", interleave)
    await cleanup.apply_change(upstream, op, "2")
    assert upstream.channel["streams"] == [1, 99]


@pytest.mark.asyncio
async def test_prepared_partial_failure_surgically_compensates_confirmed_detach(db):
    from services.pipeline_write_plan import PlanningDispatcharrClient, PlannedWrite, replay_write_plan, PartialReplayError
    upstream = Upstream()
    await attach(upstream)
    upstream.channel["name"] = NEW
    operation = (await cleanup.plan_cleanup(upstream, 7, CONFIG))["operations"][0]
    planned = PlanningDispatcharrClient(upstream)
    await planned.event_sync_change(operation)
    planned.plan.writes.append(PlannedWrite("missing_method", [], {}))
    with pytest.raises(PartialReplayError) as error:
        await replay_write_plan(upstream, planned.plan, execution_id=2)
    assert not error.value.compensation_errors
    assert upstream.channel["streams"] == [1, 2]


@pytest.mark.asyncio
async def test_attached_master_group_stream_is_explicitly_scored_not_dropped(db):
    upstream = Upstream()
    upstream.streams[2]["channel_group"] = 10
    await attach(upstream)
    upstream.channel["name"] = NEW
    from unittest.mock import AsyncMock
    upstream.get_streams = AsyncMock(return_value={"results": [upstream.streams[2]], "next": None, "count": 1})
    config = {**CONFIG, "secondary_group_ids": [], "include_master_group_streams": True}
    plan = await cleanup.plan_cleanup(upstream, 7, config)
    assert len(plan["operations"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
async def test_real_preview_router_returns_cleanup_without_mutation(db, monkeypatch, inline):
    from routers import channel_pipeline as router
    upstream = Upstream()
    await attach(upstream)
    upstream.channel["name"] = NEW
    monkeypatch.setattr(router, "get_client", lambda: upstream)
    monkeypatch.setattr(router, "get_session", lambda: Session(db))
    before = len(upstream.writes)
    request = (router.EventSyncPreviewRequest(event_sync_config=CONFIG, cleanup_rule_id=7)
               if inline else router.EventSyncPreviewRequest(rule_id=7))
    result = await router.preview_event_sync(request)
    assert result["cleanup"]["decisions"][1]["decision"] == "would_detach"
    assert len(upstream.writes) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("response_lost", [False, True])
async def test_prepared_commit_persists_confirmed_cleanup_and_rollback(db, monkeypatch, response_lost):
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    from channel_pipeline_executor import ActionExecutor, ExecutionContext
    from models import ChannelPipelineExecution, ChannelPipelineSnapshot
    from routers import channel_pipeline as router
    from services.event_sync_resolver import SecondaryStream
    from services.pipeline_write_plan import PlanningDispatcharrClient
    from services import mutation_plan_store as store
    import channel_pipeline_engine as engine_module

    ChannelPipelineExecution.__table__.create(db)
    ChannelPipelineSnapshot.__table__.create(db)
    monkeypatch.setattr(router, "get_session", lambda: Session(db))
    monkeypatch.setattr(engine_module, "get_session", lambda: Session(db))
    monkeypatch.setattr(store, "mutation_plan_store", store.MutationPlanStore())
    upstream = Upstream()
    await attach(upstream, "prior")
    upstream.channel["name"] = NEW
    with Session(db) as session:
        rule = session.get(ChannelPipelineRule, 7)
        session.expunge(rule)
    engine = SimpleNamespace(client=upstream, _load_rules=AsyncMock(return_value=[rule]),
                             _update_rule_stats=AsyncMock())
    monkeypatch.setattr(router, "_ensure_engine", AsyncMock(return_value=engine))

    async def compute(request):
        client = PlanningDispatcharrClient(upstream)
        executor = ActionExecutor(client, existing_channels=[copy.deepcopy(upstream.channel)],
                                  existing_groups=[], plan_only=True)
        context = ExecutionContext(dry_run=False)
        summary = await executor.execute_event_sync_rule(7, "Events", CONFIG,
            [SecondaryStream(name=NEW, group_id=20, stream_id=3, provider_id=18)], context)
        return {"request": request.model_dump(exclude={"dry_run"}), "snapshot": [],
                "write_plan": client.plan.as_dict(), "result": {
                    "event_sync": [summary], "modified_entities": context.modified_entities,
                    "created_entities": [], "execution_log": [],
                }}
    monkeypatch.setattr(router, "_compute_pipeline_plan_payload", compute)
    prepared = await router._materialize_pipeline_plan(router.RunPipelineRequest(rule_ids=[7]))
    assert upstream.channel["streams"] == [1, 2]
    if response_lost:
        from fastapi import HTTPException
        upstream.fail = True
        with pytest.raises(HTTPException) as error:
            await router.commit_auto_creation_pipeline(router.CommitPipelinePlanRequest(
                plan_id=prepared["plan_id"], plan_hash=prepared["plan_hash"], phase="execute"), _admin=None)
        assert error.value.status_code == 502
        assert "uncertain_cleanup_outcome" in error.value.detail["compensation_errors"]
        assert upstream.channel["streams"] == [1]
        with Session(db) as session:
            execution = session.query(ChannelPipelineExecution).one()
            assert execution.status == "failed"
            assert "PartialReplayError" in execution.error_message
            assert not execution.get_event_sync_summary()
            entry = session.query(JournalEntry).filter_by(batch_id=str(execution.id)).one()
            assert json.loads(entry.after_value)["state"] == "uncertain"
        return
    response = await router.commit_auto_creation_pipeline(router.CommitPipelinePlanRequest(
        plan_id=prepared["plan_id"], plan_hash=prepared["plan_hash"], phase="execute"), _admin=None)
    execution_id = json.loads(response.body)["execution_id"]
    assert upstream.channel["streams"] == [1, 3]
    with Session(db) as session:
        summary = session.get(ChannelPipelineExecution, execution_id).get_event_sync_summary()[0]
        assert summary["cleanup"]["decisions"][1]["decision"] == "detached"
        entries = session.query(JournalEntry).filter(JournalEntry.batch_id == str(execution_id)).all()
        assert {r.action_type for r in entries} == {"merge_stream", "detach_stream"}
        assert all(json.loads(r.after_value)["state"] == "confirmed" for r in entries)
    upstream.channel["streams"].append(99)
    result = await engine_module.ChannelPipelineEngine(upstream).rollback_execution(execution_id, confirm=True)
    assert result["success"]
    assert upstream.channel["streams"] == [1, 99, 2]


@pytest.mark.asyncio
async def test_partial_rollback_does_not_claim_success_when_detached_entity_disappears(db):
    upstream = Upstream()
    await attach(upstream)
    upstream.channel["name"] = NEW
    await cleanup.apply_change(upstream, (await cleanup.plan_cleanup(upstream, 7, CONFIG))["operations"][0], "2")
    await attach(upstream, "2", 3)
    del upstream.streams[2]
    upstream.channel["streams"].append(99)
    result = await cleanup.rollback_batch(upstream, "2", expected_count=2)
    assert result["success"] is False
    assert result["entities_restored"] == 1
    assert upstream.channel["streams"] == [1, 99]


@pytest.mark.asyncio
async def test_corrupt_history_is_preserved_not_adopted(db):
    upstream = Upstream()
    await attach(upstream)
    upstream.channel["name"] = NEW
    with Session(db) as session:
        session.query(JournalEntry).one().after_value = "{broken"
        session.commit()
    assert not (await cleanup.plan_cleanup(upstream, 7, CONFIG))["operations"]
    assert (await cleanup.rollback_batch(upstream, "1"))["success"] is False


@pytest.mark.asyncio
async def test_matching_and_attach_cap_do_not_prove_staleness(db):
    upstream = Upstream()
    await attach(upstream)
    plan = await cleanup.plan_cleanup(upstream, 7, {**CONFIG, "max_attach_per_run": 1})
    assert not plan["operations"]
    assert plan["decisions"][1]["reason"] == "still_matches"
    upstream.channel["name"] = "Peacock 11: IMSA CTMP Qualifying @ 11 Jul 03:55 PM ET"
    upstream.streams[2]["name"] = "IMSA TV 03 : IMSA VPRC at CTMP R2 @ 11 Jul 03:55 PM ET"
    plan = await cleanup.plan_cleanup(upstream, 7, CONFIG)
    assert not plan["operations"]
    assert plan["decisions"][1]["reason"] == "matching_ambiguous"


def test_cleanup_preview_identity_rejects_malformed_ids():
    from pydantic import ValidationError
    from routers.channel_pipeline import EventSyncPreviewRequest
    for bad in [True, 0, -1, "7", 7.0]:
        with pytest.raises(ValidationError):
            EventSyncPreviewRequest(event_sync_config=CONFIG, cleanup_rule_id=bad)


@pytest.mark.asyncio
async def test_last_read_rechecks_order_derived_matching_identity_without_using_it_as_parent(db, monkeypatch):
    upstream = Upstream()
    await attach(upstream)
    config = {**CONFIG, "parse_master_from_stream": True}
    with Session(db) as session:
        session.get(ChannelPipelineRule, 7).set_event_sync_config(config)
        session.commit()
    upstream.streams[1]["name"] = NEW
    operation = (await cleanup.plan_cleanup(upstream, 7, config))["operations"][0]
    original = cleanup.write_intent
    def reorder_after_intent(*args):
        entry_id = original(*args)
        upstream.channel["streams"] = [2, 1]
        return entry_id
    monkeypatch.setattr(cleanup, "write_intent", reorder_after_intent)
    with pytest.raises(RuntimeError, match="cancelled"):
        await cleanup.apply_change(upstream, operation, "2")
    assert upstream.channel["streams"] == [2, 1]
    assert len(upstream.writes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [False, True])
async def test_group_drift_after_durable_intent_cancels_detach(db, monkeypatch, prepared):
    from services.pipeline_write_plan import PlanningDispatcharrClient, replay_write_plan, PartialReplayError
    upstream = Upstream()
    await attach(upstream)
    upstream.streams[2]["channel_group"] = 30
    operation = (await cleanup.plan_cleanup(upstream, 7, CONFIG))["operations"][0]
    planned = PlanningDispatcharrClient(upstream)
    if prepared:
        await planned.event_sync_change(operation)
    original = cleanup.write_intent

    def drift(*args):
        entry_id = original(*args)
        upstream.streams[2]["channel_group"] = 20
        return entry_id

    monkeypatch.setattr(cleanup, "write_intent", drift)
    with pytest.raises((RuntimeError, PartialReplayError), match="replay failed" if prepared else "cancelled"):
        if prepared:
            await replay_write_plan(upstream, planned.plan, execution_id=2)
        else:
            await cleanup.apply_change(upstream, operation, "2")
    assert upstream.channel["streams"] == [1, 2]
    assert len(upstream.writes) == 1
    with Session(db) as session:
        row = session.query(JournalEntry).filter_by(batch_id="2").one()
        assert json.loads(row.after_value)["state"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("preimage", [None, "{}", '{"stream_ids":null}',
    '{"stream_ids":[true]}', '{"stream_ids":[1,1]}', '{"stream_ids":[0]}',
    '{"stream_ids":["1"]}', '{broken', '{"stream_ids":[1]}'])
async def test_ownership_requires_valid_explicit_preimage(db, prepared, preimage):
    from services.pipeline_write_plan import PlanningDispatcharrClient, replay_write_plan, PartialReplayError
    upstream = Upstream()
    await attach(upstream)
    upstream.channel["name"] = NEW
    operation = (await cleanup.plan_cleanup(upstream, 7, CONFIG))["operations"][0]
    planned = PlanningDispatcharrClient(upstream)
    if prepared:
        await planned.event_sync_change(operation)
    with Session(db) as session:
        session.query(JournalEntry).one().before_value = preimage
        session.commit()
    plan = await cleanup.plan_cleanup(upstream, 7, CONFIG)
    valid = preimage == '{"stream_ids":[1]}'
    assert bool(plan["operations"]) is valid
    if not valid:
        assert plan["error"] or all(r["reason"] for r in plan["decisions"])
        with pytest.raises((ValueError, PartialReplayError), match="replay failed" if prepared else "decision changed"):
            if prepared:
                await replay_write_plan(upstream, planned.plan, execution_id=2)
            else:
                await cleanup.apply_change(upstream, operation, "2")
        assert upstream.channel["streams"] == [1, 2]
        assert len(upstream.writes) == 1
    else:
        if prepared:
            await replay_write_plan(upstream, planned.plan, execution_id=2)
        else:
            await cleanup.apply_change(upstream, operation, "2")
        assert upstream.channel["streams"] == [1]


@pytest.mark.asyncio
async def test_executor_response_lost_history_does_not_claim_preserved(db):
    from channel_pipeline_executor import ActionExecutor, ExecutionContext
    from models import ChannelPipelineExecution
    ChannelPipelineExecution.__table__.create(db)
    upstream = Upstream()
    await attach(upstream)
    upstream.channel["name"] = NEW
    upstream.fail = True
    executor = ActionExecutor(upstream, existing_channels=[copy.deepcopy(upstream.channel)],
                              existing_groups=[], execution_id=2)
    summary = await executor.execute_event_sync_rule(7, "Events", CONFIG, [], ExecutionContext(dry_run=False))
    assert upstream.channel["streams"] == [1]
    with Session(db) as session:
        execution = ChannelPipelineExecution(id=2, started_at=datetime.utcnow(), status="completed_with_errors")
        execution.set_event_sync_summary([summary])
        session.add(execution)
        session.commit()
        persisted = session.get(ChannelPipelineExecution, 2).get_event_sync_summary()[0]
        assert persisted["cleanup"]["decisions"][1]["decision"] == "uncertain"
        assert persisted["cleanup"]["error"]
