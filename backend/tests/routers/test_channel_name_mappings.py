"""Frozen u0ko6 contract: persisted literal aliases, not regex rules."""
import pytest

from normalization_engine import NormalizationEngine
from models import NormalizationRule, NormalizationRuleGroup


@pytest.fixture(autouse=True)
def isolated_tag_caches():
    from normalization_engine import invalidate_tag_cache

    invalidate_tag_cache()
    yield
    invalidate_tag_cache()


@pytest.fixture
def test_engine(tmp_path, monkeypatch):
    import database
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    monkeypatch.setattr(database, "JOURNAL_DB_FILE", tmp_path / "mappings.db")
    engine = create_engine(database.get_database_url(),
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    database.Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.mark.asyncio
async def test_persisted_crud_and_literal_matching(async_client, test_session):
    examples = {
        "Polonia": ["Polonia", "Polonia 1", "Polonia1", "Polonia.1"],
        "Stars TV": ["Stars TV", "Stars.TV", "Stars-TV"],
        "TVN": ["TVN", "TVN HD", "TVN-HD"],
        "Literal HD": ["A.+[1](TV)?$", "Literal HD"],
    }
    ids = []
    for preferred, aliases in examples.items():
        response = await async_client.post("/api/normalization/mappings", json={
            "preferred_name": preferred, "aliases": aliases,
        })
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    listed = await async_client.get("/api/normalization/mappings")
    assert len(listed.json()["mappings"]) == 4

    group = NormalizationRuleGroup(name="Destructive later pass", enabled=True)
    test_session.add(group)
    test_session.flush()
    test_session.add(NormalizationRule(
        group_id=group.id, name="Strip HD", enabled=True,
        condition_type="contains", condition_value=" HD", action_type="remove",
    ))
    test_session.commit()
    engine = NormalizationEngine(test_session)
    for preferred, aliases in examples.items():
        for alias in aliases:
            assert engine.normalize(alias.swapcase()).normalized == preferred
            assert engine.normalize(preferred).normalized == preferred
    for negative in ["TVN24", "Polonia 2", "StarsXTV", "Axxx1TV", " TVN", "TVN\n"]:
        assert engine.resolve_preferred_name(negative) is None
    assert engine.normalize("Unmapped HD").normalized == "Unmapped"

    response = await async_client.put(f"/api/normalization/mappings/{ids[2]}", json={
        "preferred_name": "TVN", "aliases": ["TVN", "TVN-HD", "TVN SD"],
    })
    assert response.status_code == 200
    fresh = NormalizationEngine(test_session)
    assert fresh.resolve_preferred_name("tvn sd") == "TVN"
    assert fresh.resolve_preferred_name("TVN HD") is None
    assert (await async_client.delete(f"/api/normalization/mappings/{ids[2]}")).status_code == 204
    assert NormalizationEngine(test_session).resolve_preferred_name("TVN-HD") is None
    assert (await async_client.delete(f"/api/normalization/mappings/{ids[2]}")).status_code == 404


@pytest.mark.asyncio
async def test_duplicate_aliases_deduplicated_and_conflicts_atomic(async_client):
    created = await async_client.post("/api/normalization/mappings", json={
        "preferred_name": "TVN", "aliases": ["TVN", "tvn", "TVN-HD", "TVN-HD"],
    })
    assert created.status_code == 201
    assert created.json()["aliases"] == ["TVN", "TVN-HD"]
    for preferred, aliases in [("Other", ["tvn-hd"]), ("tvn", []), ("Other", ["TVN"])]:
        conflict = await async_client.post("/api/normalization/mappings", json={
            "preferred_name": preferred, "aliases": aliases,
        })
        assert conflict.status_code == 409
        assert "owned" in conflict.json()["detail"]
    other = await async_client.post("/api/normalization/mappings", json={
        "preferred_name": "Other", "aliases": ["Other.1"],
    })
    rejected = await async_client.put(f"/api/normalization/mappings/{other.json()['id']}", json={
        "preferred_name": "Changed", "aliases": ["tvn-hd"],
    })
    assert rejected.status_code == 409
    listed = (await async_client.get("/api/normalization/mappings")).json()["mappings"]
    assert {item["preferred_name"] for item in listed} == {"TVN", "Other"}
    assert next(item for item in listed if item["preferred_name"] == "Other")["aliases"] == ["Other", "Other.1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"preferred_name": "", "aliases": []},
    {"preferred_name": "TVN", "aliases": [""]},
    {"preferred_name": "TVN", "aliases": ["TVN\n"]},
])
async def test_invalid_mapping_is_visible_validation_error(async_client, payload):
    response = await async_client.post("/api/normalization/mappings", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_pipeline_repeated_runs_group_aliases_without_duplicate_attachments(async_client, test_session):
    from unittest.mock import AsyncMock, MagicMock
    from copy import deepcopy
    from channel_pipeline_executor import ActionExecutor, ExecutionContext
    from channel_pipeline_evaluator import StreamContext

    await async_client.post("/api/normalization/mappings", json={
        "preferred_name": "Stars TV HD", "aliases": ["Stars.TV", "Stars-TV"],
    })
    channels = []

    async def create(data):
        channel = {"id": len(channels) + 1, **deepcopy(data)}
        channels.append(channel)
        return deepcopy(channel)

    async def update(channel_id, data):
        channel = next(c for c in channels if c["id"] == channel_id)
        channel.update(deepcopy(data))
        return deepcopy(channel)

    client = MagicMock()
    client.create_channel = AsyncMock(side_effect=create)
    client.update_channel = AsyncMock(side_effect=update)
    client.get_channel = AsyncMock(side_effect=lambda channel_id: deepcopy(next(c for c in channels if c["id"] == channel_id)))
    for _run in range(2):
        executor = ActionExecutor(client, existing_channels=deepcopy(channels),
                                  managed_channel_ids=[c["id"] for c in channels],
                                  normalization_engine=NormalizationEngine(test_session))
        for stream_id, name in [(1, "Stars.TV"), (2, "Stars-TV")]:
            result = await executor.execute(
                {"type": "create_channel", "if_exists": "merge", "group_id": 5},
                StreamContext.from_dispatcharr_stream({"id": stream_id, "name": name, "m3u_account": stream_id}),
                ExecutionContext(), normalization_group_ids=[],
            )
            assert result.success, result.error
    assert len(channels) == 1
    assert channels[0]["name"] == "Stars TV HD"
    assert sorted(channels[0]["streams"]) == [1, 2]
    assert client.create_channel.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("managed,scope,expected_streams", [
    (True, 5, [1, 2]), (False, 5, [1]), (True, 6, [1]),
])
async def test_mapped_auto_merge_keeps_manual_and_group_protections(
    async_client, test_session, managed, scope, expected_streams,
):
    from unittest.mock import AsyncMock, MagicMock
    from channel_pipeline_executor import ActionExecutor, ExecutionContext
    from channel_pipeline_evaluator import StreamContext

    await async_client.post("/api/normalization/mappings", json={
        "preferred_name": "TVN", "aliases": ["TVN-HD"],
    })
    channel = {"id": 1, "name": "TVN", "channel_group_id": 5, "streams": [1]}
    client = MagicMock()
    client.update_channel = AsyncMock(side_effect=lambda _id, data: channel.update(data))
    client.get_channel = AsyncMock(return_value=channel)
    executor = ActionExecutor(client, existing_channels=[channel],
                              managed_channel_ids=[1] if managed else [],
                              normalization_engine=NormalizationEngine(test_session))
    for _run in range(2):
        await executor.execute(
            {"type": "merge_streams", "target": "auto"},
            StreamContext.from_dispatcharr_stream({"id": 2, "name": "TVN-HD"}),
            ExecutionContext(), rule_scope_group_id=scope, normalization_group_ids=[],
        )
    assert sorted(channel["streams"]) == expected_streams
    assert client.update_channel.await_count == (1 if expected_streams == [1, 2] else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("groups", [False, True])
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("original,pattern,replacement,expected", [
    ("Stars.TV", r"\.", "-", "Stars TV HD"),
    ("StarsXTV", "X", ".", "Stars.TV"),
])
async def test_create_mapping_ownership_precedes_transform(
    async_client, test_session, groups, existing, original, pattern, replacement, expected,
):
    from unittest.mock import AsyncMock, MagicMock
    from channel_pipeline_executor import ActionExecutor, ExecutionContext
    from channel_pipeline_evaluator import StreamContext

    await async_client.post("/api/normalization/mappings", json={
        "preferred_name": "Stars TV HD", "aliases": ["Stars.TV"],
    })
    group = NormalizationRuleGroup(name="Selected", enabled=True)
    test_session.add(group)
    test_session.commit()
    client = MagicMock()
    client.create_channel = AsyncMock(side_effect=lambda data: {"id": 10, **data})
    channel = {"id": 1, "name": "Stars TV HD", "streams": [1]}
    client.get_channel = AsyncMock(return_value=channel)
    client.update_channel = AsyncMock()
    executor = ActionExecutor(client, existing_channels=[channel] if existing else [],
                              managed_channel_ids=[1], normalization_engine=NormalizationEngine(test_session))
    result = await executor.execute(
        {"type": "create_channel", "if_exists": "merge", "name_transform_pattern": pattern,
         "name_transform_replacement": replacement},
        StreamContext.from_dispatcharr_stream({"id": 2, "name": original}),
        ExecutionContext(), normalization_group_ids=[group.id] if groups else [],
    )
    assert result.success, result.error
    if existing and original == "Stars.TV":
        client.create_channel.assert_not_awaited()
        client.update_channel.assert_awaited_once()
    else:
        assert client.create_channel.call_args.args[0]["name"] == expected
        client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create_channel", "merge_streams"])
async def test_mapped_identity_does_not_fall_back_to_quality_stripped_channel(
    async_client, test_session, action,
):
    from unittest.mock import AsyncMock, MagicMock
    from channel_pipeline_executor import ActionExecutor, ExecutionContext
    from channel_pipeline_evaluator import StreamContext
    from models import Tag, TagGroup

    await async_client.post("/api/normalization/mappings", json={
        "preferred_name": "Stars TV HD", "aliases": ["Stars.TV"],
    })
    test_session.add(TagGroup(name="Quality Tags", tags=[Tag(value="HD")]))
    group = NormalizationRuleGroup(name="Selected", enabled=True)
    test_session.add(group)
    test_session.commit()
    channel = {"id": 1, "name": "Stars TV", "channel_group_id": 5, "streams": [1]}
    client = MagicMock()
    client.get_channel = AsyncMock(return_value=channel)
    client.update_channel = AsyncMock()
    client.create_channel = AsyncMock(side_effect=lambda data: {"id": 10, **data})
    executor = ActionExecutor(client, existing_channels=[channel], managed_channel_ids=[1],
                              normalization_engine=NormalizationEngine(test_session))
    assert NormalizationEngine(test_session).extract_core_name("Stars TV HD", for_matching=True) == "Stars TV"
    await executor.execute(
        {"type": action, "if_exists": "merge", "target": "auto", "loose_name_match": True},
        StreamContext.from_dispatcharr_stream({"id": 2, "name": "Stars.TV" if action == "create_channel" else "Stars TV HD"}),
        ExecutionContext(), normalization_group_ids=[] if action == "create_channel" else [group.id],
    )
    client.update_channel.assert_not_awaited()
    if action == "create_channel":
        assert client.create_channel.call_args.args[0]["name"] == "Stars TV HD"


@pytest.mark.parametrize("delete_second", [False, True])
def test_overlapping_mapping_replacement_uses_current_state(test_engine, monkeypatch, delete_second):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    from sqlalchemy import event, text
    from sqlalchemy.orm import Session, sessionmaker
    from routers import normalization as router

    engine = test_engine
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode=WAL")).scalar() == "wal"
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(router, "get_session", factory)

    def save(name, aliases, mapping_id=None):
        return router._save_channel_name_mapping(
            router.ChannelNameMappingRequest(preferred_name=name, aliases=aliases), mapping_id)

    original = save("Old", ["Old alias"])
    first_loaded, second_loaded, release_first, first_done, second_attempt = (Event() for _ in range(5))

    def before_flush(session, context, instances):
        second = current_thread().name.endswith("_1")
        if session.info.get("waited"):
            return
        session.info["waited"] = True
        if second:
            second_loaded.set()
            assert first_done.wait(10)
        else:
            first_loaded.set()
            assert release_first.wait(10)

    event.listen(Session, "before_flush", before_flush)

    def first():
        try:
            return save("First", ["First x", "First y", "First z"], original["id"])
        finally:
            first_done.set()

    def second():
        second_attempt.set()
        if delete_second:
            return router.delete_channel_name_mapping(original["id"])
        return save("Second", ["Final"], original["id"])

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(first)
            assert first_loaded.wait(10)
            b = pool.submit(second)
            assert second_attempt.wait(10)
            # Old code loads both stale collections; serialized code waits outside the session.
            second_loaded.wait(0.2)
            release_first.set()
            assert a.result(timeout=10)["preferred_name"] == "First"
            b.result(timeout=10)
        monkeypatch.setattr(router, "get_session", factory)
        mappings = router.get_channel_name_mappings()["mappings"]
        if delete_second:
            assert mappings == []
        else:
            assert mappings == [{"id": original["id"], "preferred_name": "Second",
                                 "aliases": ["Second", "Final"]}]
        with factory() as session:
            assert NormalizationEngine(session).resolve_preferred_name("First y") is None
            from models import ChannelNameAlias
            assert session.query(ChannelNameAlias).count() == (0 if delete_second else 2)
    finally:
        release_first.set()
        event.remove(Session, "before_flush", before_flush)
        engine.dispose()


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_mapping_write_isolated_from_production_readers(tmp_path, monkeypatch, operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    from sqlalchemy import event, text
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    import database
    from models import ChannelNameAlias, ChannelNameMapping
    from routers import normalization as router

    monkeypatch.setattr(database, "JOURNAL_DB_FILE", tmp_path / "production.db")
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    database.init_db()
    ready, release = Event(), Event()

    def save(name, aliases, mapping_id=None):
        return router._save_channel_name_mapping(
            router.ChannelNameMappingRequest(preferred_name=name, aliases=aliases), mapping_id)

    old = save("Old", ["Old alias"])

    def pause_writer(session, context):
        if current_thread().name.startswith("mapping-writer"):
            session.info["flush_count"] = session.info.get("flush_count", 0) + 1
            if session.info["flush_count"] == (1 if operation == "delete" else 2):
                ready.set()
                assert release.wait(10)

    event.listen(Session, "after_flush_postexec", pause_writer)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="mapping-writer") as pool:
            if operation == "delete":
                writer = pool.submit(router.delete_channel_name_mapping, old["id"])
            else:
                writer = pool.submit(save, "Second", ["Final"], old["id"] if operation == "update" else None)
            try:
                assert ready.wait(10)
                resolved = router.resolve_channel_name_mappings(
                    router.TestRulesBatchRequest(texts=["Final", "Old alias"]))
                with database.get_session() as session:
                    assert isinstance(session.bind.pool, StaticPool)
                    assert session.execute(text("PRAGMA journal_mode")).scalar() == "wal"
                    snapshot = [m.to_dict() for m in session.query(ChannelNameMapping).all()]
            finally:
                release.set()
            result = writer.result(timeout=10)
        assert resolved["results"] == [
            {"original": "Final", "preferred_name": None},
            {"original": "Old alias", "preferred_name": "Old"},
        ]
        assert snapshot == [old]
        expected = [] if operation == "delete" else ([old, result] if operation == "create" else [result])
        assert router.get_channel_name_mappings()["mappings"] == expected
        with database.get_session() as session:
            assert session.query(ChannelNameAlias).count() == (0 if operation == "delete" else 4 if operation == "create" else 2)
            assert NormalizationEngine(session).resolve_preferred_name("Final") == (None if operation == "delete" else "Second")
        if operation != "delete":
            assert result == {"id": result["id"], "preferred_name": "Second", "aliases": ["Second", "Final"]}
    finally:
        release.set()
        event.remove(Session, "after_flush_postexec", pause_writer)
        database.close_db()
