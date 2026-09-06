"""GH971 collection editing against file SQLite, not mocked ORM writes."""

import asyncio
import copy
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from auth import RequireAdminIfEnabled
from models import ChannelPipelineRule
from routers import channel_pipeline

PATH = "/api/channel-pipeline/rules/yaml"


@pytest.fixture
def editor(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'editor.db'}"
    engine = create_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    database.Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "get_database_url", lambda: url)
    monkeypatch.setattr(database, "get_session", sessions)
    monkeypatch.setattr(channel_pipeline, "get_session", sessions)
    upstream = AsyncMock()
    upstream.get_channel_groups.return_value = [{"id": 10}, {"id": 20}]
    upstream.get_m3u_accounts.return_value = [{"id": 1}, {"id": 2}]
    upstream.get_stream_profiles.return_value = [{"id": 5}]
    upstream.get_channel_profiles.return_value = [{"id": 6}]
    upstream.get_epg_data.return_value = [{"id": 7}]
    monkeypatch.setattr(channel_pipeline, "get_client", lambda: upstream)
    app = FastAPI()
    app.include_router(channel_pipeline.router, prefix="/api/channel-pipeline")
    app.dependency_overrides[RequireAdminIfEnabled.dependency] = lambda: None
    with sessions() as session:
        for index in range(4):
            rule = ChannelPipelineRule(
                name=f"Rule {index}", enabled=index % 2 == 0, priority=index,
                conditions='[{"type":"always"}]',
                actions='[{"type":"skip"}]', match_scope_target_group=False,
                match_count=7, managed_channel_ids="[88]",
                last_run_at=datetime(2026, 1, 1), last_run_stats='{"matched":7}',
                event_sync_config=(json.dumps({
                    "master_group_id": 10, "secondary_group_ids": [20],
                }) if index > 1 else None),
            )
            session.add(rule)
        session.commit()
    with TestClient(app) as client:
        yield client, sessions, upstream, engine
    engine.dispose()


def load(client):
    response = client.get(PATH)
    assert response.status_code == 200, response.text
    snapshot = response.json()
    return snapshot, yaml.safe_load(snapshot["yaml_content"])


def save(client, snapshot, document, **options):
    return client.put(PATH, json={
        **snapshot, "yaml_content": yaml.safe_dump(document), **options,
    })


def rows(sessions):
    with sessions() as session:
        return [dict(row._mapping) for row in session.execute(
            text("SELECT * FROM auto_creation_rules ORDER BY id")
        )]


def test_complete_collection_rename_copy_preserves_runtime(editor):
    client, sessions, upstream, _ = editor
    snapshot, doc = load(client)
    assert len(doc["rules"]) == 4
    assert [rule["enabled"] for rule in doc["rules"]] == [True, False, True, False]
    assert [rule["kind"] for rule in doc["rules"]] == [
        "standard", "standard", "event_sync", "event_sync",
    ]
    assert not ({"last_run_at", "match_count", "managed_channel_ids", "created_at"}
                & doc["rules"][0].keys())
    before = rows(sessions)
    doc["rules"][0]["name"] = "Renamed"
    duplicate = copy.deepcopy(doc["rules"][0])
    duplicate.pop("id")
    doc["rules"].append(duplicate)
    response = save(client, snapshot, doc)
    assert response.status_code == 200, response.text
    after = rows(sessions)
    assert after[0]["id"] == before[0]["id"]
    assert after[0]["name"] == "Renamed"
    for field in ("match_count", "managed_channel_ids", "last_run_at", "last_run_stats", "created_at"):
        assert after[0][field] == before[0][field]
    assert after[0]["match_scope_target_group"] == 0
    assert after[-1]["id"] > before[-1]["id"]
    assert after[-1]["managed_channel_ids"] is None
    assert after[-1]["last_run_at"] is None
    assert after[-1]["match_count"] == 0
    assert not [call for call in upstream.mock_calls if not call[0].startswith("get_")]


@pytest.mark.parametrize("field,value", [
    ("id", 9999), ("id", True), ("id", "1"), ("id", None), ("id", 1.5),
    ("id", -1), ("id", 1), ("last_run_at", "2026-01-01"),
    ("kind", "unknown"), ("enabled", "false"), ("priority", True),
    ("conditions", [4]), ("actions", [{"type": "unknown"}]),
    ("sort_regex", "["), ("target_group_id", 999),
    ("sort_field", "not-a-sort"), ("stream_sort_field", "not-a-sort"),
    ("normalization_group_ids", [999]), ("required_provider_ids", [1, 999]),
])
def test_invalid_last_rule_rejects_every_edit(editor, field, value):
    client, sessions, _, _ = editor
    snapshot, doc = load(client)
    before = rows(sessions)
    doc["rules"][0]["name"] = "Must not persist"
    doc["rules"][-1][field] = value
    response = save(client, snapshot, doc)
    assert response.status_code in (400, 422), response.text
    assert "rule_index" in response.text, response.text
    assert rows(sessions) == before


@pytest.mark.parametrize("content", [
    "version: 1\nversion: 1\nrules: []",
    "version: 1\nrules: &rules [*rules]",
    "version: 1\nrules: " + "[" * 80 + "0" + "]" * 80,
    "version: 1\nrules: [",
    "version: 1\nrules: !!python/object:object {}",
])
def test_unsafe_yaml_is_actionable_and_atomic(editor, content):
    client, sessions, _, _ = editor
    snapshot, _ = load(client)
    before = rows(sessions)
    response = client.put(PATH, json={**snapshot, "yaml_content": content})
    assert response.status_code in (400, 422), response.text
    assert "line" in response.text and "column" in response.text
    assert rows(sessions) == before


@pytest.mark.parametrize("field,scalar,message", [
    ("active_from", "2026-02-30", "day is out of range"),
    ("id", "9" * 5000, "integer string conversion"),
], ids=["invalid-date", "oversized-integer"])
def test_invalid_yaml_scalar_is_actionable_before_io(editor, monkeypatch, field, scalar, message):
    import pipeline_yaml_editor

    client, sessions, upstream, _ = editor
    snapshot, doc = load(client)
    before = rows(sessions)
    doc["rules"][0]["name"] = "Must not persist"
    doc["rules"][-1][field] = "SCALAR_PLACEHOLDER"
    content = yaml.safe_dump(doc).replace("SCALAR_PLACEHOLDER", scalar)
    line = next(index for index, value in enumerate(content.splitlines(), 1) if scalar in value)
    column = content.splitlines()[line - 1].index(scalar) + 1
    save_session = MagicMock(side_effect=AssertionError("Invalid scalar must not open a DB session"))
    monkeypatch.setattr(pipeline_yaml_editor, "_session", save_session)
    with TestClient(client.app, raise_server_exceptions=False) as route_client:
        response = route_client.put(PATH, json={**snapshot, "yaml_content": content})
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert message in detail["message"]
    assert (detail["line"], detail["column"]) == (line, column)
    assert upstream.mock_calls == []
    save_session.assert_not_called()
    assert rows(sessions) == before


def test_valid_yaml_date_and_integer_scalars_save(editor):
    client, sessions, upstream, _ = editor
    snapshot, doc = load(client)
    doc["rules"][0]["active_from"] = "SCALAR_PLACEHOLDER"
    content = yaml.safe_dump(doc).replace("SCALAR_PLACEHOLDER", "2026-02-28")
    response = client.put(PATH, json={**snapshot, "yaml_content": content})
    assert response.status_code == 200, response.text
    after = rows(sessions)
    assert after[0]["active_from"] == "2026-02-28"
    assert [row["id"] for row in after] == [1, 2, 3, 4]
    upstream.get_channel_groups.assert_awaited_once()


def test_delete_all_requires_confirmation_and_retains_draft_revision(editor):
    client, sessions, upstream, _ = editor
    snapshot, _ = load(client)
    before = rows(sessions)
    doc = {"version": 1, "rules": []}
    response = save(client, snapshot, doc)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "deletion_confirmation_required"
    assert [item["name"] for item in detail["deletions"]] == [row["name"] for row in before]
    assert "schedule" in str(detail).lower()
    assert rows(sessions) == before
    assert load(client)[0] == snapshot
    response = save(client, snapshot, doc, confirm_deletions=True)
    assert response.status_code == 200, response.text
    assert rows(sessions) == []
    assert not [call for call in upstream.mock_calls if not call[0].startswith("get_")]


def test_two_editors_conflict_but_runtime_does_not(editor):
    client, sessions, _, _ = editor
    snapshot, doc = load(client)
    with sessions() as session:
        session.query(ChannelPipelineRule).filter_by(id=1).update({"match_count": 99})
        session.commit()
    assert load(client)[0]["revision"] == snapshot["revision"]
    doc["rules"][0]["name"] = "Winner"
    assert save(client, snapshot, doc).status_code == 200
    before = rows(sessions)
    doc["rules"][0]["name"] = "Loser"
    response = save(client, snapshot, doc)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "stale_revision"
    assert rows(sessions) == before


def test_mid_transaction_failure_rolls_back_all_changes(editor):
    client, sessions, _, _ = editor
    snapshot, doc = load(client)
    before = rows(sessions)
    doc["rules"][0]["name"] = "Changed"
    doc["rules"].pop()
    duplicate = copy.deepcopy(doc["rules"][0])
    duplicate.pop("id")
    doc["rules"].append(duplicate)

    def fail_delete(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM auto_creation_rules"):
            raise RuntimeError("injected deletion failure")

    from sqlalchemy.engine import Engine
    event.listen(Engine, "before_cursor_execute", fail_delete)
    try:
        response = save(client, snapshot, doc, confirm_deletions=True)
    finally:
        event.remove(Engine, "before_cursor_execute", fail_delete)
    assert response.status_code == 500
    assert rows(sessions) == before


def test_actual_stats_completion_cannot_overwrite_or_resurrect(editor, monkeypatch):
    from channel_pipeline_engine import ChannelPipelineEngine
    import channel_pipeline_engine

    client, sessions, _, _ = editor
    monkeypatch.setattr(channel_pipeline_engine, "get_session", sessions)
    with sessions() as session:
        detached = session.query(ChannelPipelineRule).order_by(ChannelPipelineRule.id).all()
        session.expunge_all()
    snapshot, doc = load(client)
    doc["rules"][0]["name"] = "Saved while running"
    doc["rules"].pop()
    assert save(client, snapshot, doc, confirm_deletions=True).status_code == 200
    saved_revision = load(client)[0]["revision"]
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(ChannelPipelineEngine._update_rule_stats(
            object(), detached, {"rule_match_counts": {1: 42}},
        ))
    finally:
        loop.close()
    after = rows(sessions)
    assert len(after) == 3
    assert after[0]["name"] == "Saved while running"
    assert after[0]["match_count"] == 42
    assert load(client)[0]["revision"] == saved_revision


@pytest.mark.parametrize("config", [
    {"master_group_id": 999, "secondary_group_ids": [20]},
    {"master": {"group_id": 10, "m3u_account_id": 999}, "secondary": [20]},
    {"master_group_id": 10, "secondary_group_ids": [999]},
])
def test_event_sync_reference_errors_are_atomic(editor, config):
    client, sessions, _, _ = editor
    snapshot, doc = load(client)
    before = rows(sessions)
    doc["rules"][0]["name"] = "Must roll back"
    doc["rules"][-1]["event_sync_config"] = config
    response = save(client, snapshot, doc)
    assert response.status_code == 422, response.text
    assert "event_sync_config" in response.text
    assert rows(sessions) == before


@pytest.mark.parametrize("operation", ["create", "update", "delete", "reorder", "import", "bulk", "toggle"])
def test_existing_writes_invalidate_editor_revision(editor, monkeypatch, operation):
    client, sessions, _, _ = editor
    monkeypatch.setattr(channel_pipeline.journal, "log_entry", lambda **kwargs: None)
    snapshot, doc = load(client)
    base = "/api/channel-pipeline"
    payload = {"name": "Concurrent", "conditions": [{"type": "always"}], "actions": [{"type": "skip"}]}
    if operation == "create":
        result = client.post(f"{base}/rules", json=payload)
    elif operation == "update":
        result = client.put(f"{base}/rules/1", json={"name": "Concurrent"})
    elif operation == "delete":
        result = client.delete(f"{base}/rules/1")
    elif operation == "reorder":
        result = client.post(f"{base}/rules/reorder", json=[4, 3, 2, 1])
    elif operation == "bulk":
        result = client.post(f"{base}/rules/bulk-update", json={"rule_ids": [1, 2], "name": "Concurrent"})
    elif operation == "toggle":
        result = client.post(f"{base}/rules/1/toggle")
    else:
        result = client.post(f"{base}/import/yaml", json={"yaml_content": yaml.safe_dump({"rules": [payload]})})
    assert result.status_code == 200, result.text
    before = rows(sessions)
    result = save(client, snapshot, doc)
    assert result.status_code == 409, result.text
    assert result.json()["detail"]["code"] == "stale_revision"
    assert rows(sessions) == before


def test_private_save_survives_generic_staticpool_reader_rollback(editor):
    client, sessions, _, _ = editor
    snapshot, doc = load(client)
    doc["rules"][0]["name"] = "Private write"
    observed = []

    def generic_reader(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE auto_creation_rules"):
            with sessions() as session:
                observed.append(session.get(ChannelPipelineRule, 1).name)
                session.rollback()

    from sqlalchemy.engine import Engine
    event.listen(Engine, "after_cursor_execute", generic_reader)
    try:
        response = save(client, snapshot, doc)
    finally:
        event.remove(Engine, "after_cursor_execute", generic_reader)
    assert response.status_code == 200, response.text
    assert observed == ["Rule 0"]
    assert rows(sessions)[0]["name"] == "Private write"


@pytest.mark.parametrize("fail_after_delete", [False, True])
def test_delete_fk_effects_and_history_share_transaction(editor, fail_after_delete):
    from models import EventSyncReview, EventSyncExclusion, ChannelPipelineExecution
    from sqlalchemy.engine import Engine

    client, sessions, _, _ = editor
    with sessions() as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        session.add(EventSyncReview(rule_id=4, provider_id=1, stream_name_hash="hash", event_key="key", created_at=1, last_seen_at=1, evidence="{}"))
        session.add(EventSyncExclusion(rule_id=4, provider_id=1, stream_name_hash="hash", event_key="key", created_at=1, evidence="{}"))
        execution = ChannelPipelineExecution(rule_id=4, mode="execute", status="completed", started_at=datetime(2026, 1, 1))
        session.add(execution)
        session.commit()
        execution_id = execution.id
    snapshot, doc = load(client)
    doc["rules"].pop()
    duplicate = copy.deepcopy(doc["rules"][0])
    duplicate.pop("id")
    doc["rules"].append(duplicate)

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM auto_creation_rules"):
            raise RuntimeError("Failure after FK cascade")

    if fail_after_delete:
        event.listen(Engine, "after_cursor_execute", fail)
    try:
        response = save(client, snapshot, doc, confirm_deletions=True)
    finally:
        if fail_after_delete:
            event.remove(Engine, "after_cursor_execute", fail)
    assert response.status_code == (500 if fail_after_delete else 200), response.text
    with sessions() as session:
        assert session.query(EventSyncReview).count() == int(fail_after_delete)
        assert session.query(EventSyncExclusion).count() == int(fail_after_delete)
        execution = session.get(ChannelPipelineExecution, execution_id)
        assert execution is not None
        assert execution.rule_id == (4 if fail_after_delete else None)
        assert session.get(ChannelPipelineRule, 4) is not None if fail_after_delete else session.get(ChannelPipelineRule, 4) is None
        assert session.query(ChannelPipelineRule.id).order_by(ChannelPipelineRule.id.desc()).first()[0] == (4 if fail_after_delete else 5)


def test_simultaneous_editors_have_one_winner(editor, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi import HTTPException
    from pipeline_yaml_editor import save_collection

    client, sessions, upstream, _ = editor
    snapshot, doc = load(client)
    barrier = Barrier(2)

    async def providers():
        barrier.wait(timeout=10)
        return [{"id": 1}, {"id": 2}]

    upstream.get_m3u_accounts.side_effect = providers

    def contender(name):
        document = copy.deepcopy(doc)
        document["rules"][0]["name"] = name
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(save_collection(yaml.safe_dump(document), snapshot["revision"]))
            return 200
        except HTTPException as exc:
            return exc.status_code
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contender, ["Writer A", "Writer B"]))
    assert sorted(results) == [200, 409]
    assert rows(sessions)[0]["name"] in ("Writer A", "Writer B")


def test_busy_existing_writer_rejects_without_losing_draft_revision(editor, monkeypatch):
    import pipeline_yaml_editor

    client, sessions, _, engine = editor
    snapshot, doc = load(client)
    before = rows(sessions)
    monkeypatch.setattr(pipeline_yaml_editor, "create_engine", lambda url, **kwargs: create_engine(
        url, **kwargs, connect_args={"timeout": 0.01},
    ))
    with engine.connect() as connection:
        connection.execute(text("BEGIN IMMEDIATE"))
        try:
            response = save(client, snapshot, doc)
        finally:
            connection.rollback()
    assert response.status_code == 409, response.text
    assert rows(sessions) == before
    assert load(client)[0]["revision"] == snapshot["revision"]


@pytest.mark.parametrize("orphan_action,managed", [("none", "[88]"), ("delete", None), ("delete", "[88]")])
def test_actual_reconciliation_completion_cannot_undo_save(editor, monkeypatch, orphan_action, managed):
    import channel_pipeline_engine
    from channel_pipeline_engine import ChannelPipelineEngine

    client, sessions, _, _ = editor
    monkeypatch.setattr(channel_pipeline_engine, "get_session", sessions)
    with sessions() as session:
        for rule_id in (1, 2):
            rule = session.get(ChannelPipelineRule, rule_id)
            rule.orphan_action = orphan_action
            rule.managed_channel_ids = managed
        session.commit()
        detached = session.query(ChannelPipelineRule).filter(ChannelPipelineRule.id.in_([1, 2])).all()
        session.expunge_all()
    snapshot, doc = load(client)
    doc["rules"][0]["name"] = "Saved config"
    doc["rules"] = [rule for rule in doc["rules"] if rule["id"] != 2]
    assert save(client, snapshot, doc, confirm_deletions=True).status_code == 200
    revision = load(client)[0]["revision"]
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(ChannelPipelineEngine._reconcile_orphans(
            object(), detached, {1: [88, 99], 2: [88, 99]}, MagicMock(), MagicMock(), {}, False,
        ))
    finally:
        loop.close()
    with sessions() as session:
        assert session.get(ChannelPipelineRule, 2) is None
        rule = session.get(ChannelPipelineRule, 1)
        assert rule.name == "Saved config"
        assert rule.get_managed_channel_ids() == [88, 99]
    assert load(client)[0]["revision"] == revision


def test_yaml_write_requires_existing_admin_authority(editor):
    from fastapi import HTTPException

    client, sessions, _, _ = editor
    snapshot, doc = load(client)
    before = rows(sessions)

    def deny():
        raise HTTPException(403, detail="Admin access required")

    client.app.dependency_overrides[RequireAdminIfEnabled.dependency] = deny
    assert save(client, snapshot, doc).status_code == 403
    assert rows(sessions) == before


@pytest.mark.parametrize("action", [
    {"type": "assign_profile", "profile_id": 999},
    {"type": "assign_channel_profile", "channel_profile_ids": [999]},
    {"type": "assign_epg", "epg_id": 999},
])
def test_missing_action_reference_rejects_collection(editor, action):
    client, sessions, _, _ = editor
    snapshot, doc = load(client)
    before = rows(sessions)
    doc["rules"][0]["name"] = "Must not persist"
    doc["rules"][-1]["actions"] = [action]
    response = save(client, snapshot, doc)
    assert response.status_code == 422, response.text
    assert rows(sessions) == before


def test_rename_and_deletion_do_not_rewrite_selected_schedule(editor):
    from models import TaskSchedule

    client, sessions, _, _ = editor
    with sessions() as session:
        schedule = TaskSchedule(task_id="auto_creation", name="Selected fixture", enabled=True,
                                schedule_type="interval", interval_seconds=3600,
                                parameters=json.dumps({"rule_ids": [1, 4]}))
        session.add(schedule)
        session.commit()
        schedule_id = schedule.id
        parameters = schedule.parameters
    snapshot, doc = load(client)
    doc["rules"][0]["name"] = "Same identity"
    assert save(client, snapshot, doc).status_code == 200
    with sessions() as session:
        assert session.get(TaskSchedule, schedule_id).parameters == parameters
    snapshot, doc = load(client)
    doc["rules"].pop()
    response = save(client, snapshot, doc)
    assert "schedules" in response.text
    assert save(client, snapshot, doc, confirm_deletions=True).status_code == 200
    with sessions() as session:
        assert session.get(TaskSchedule, schedule_id).parameters == parameters
