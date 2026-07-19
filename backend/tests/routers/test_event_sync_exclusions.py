"""
Tests for the Event Sync operator-exclusions API (bead ti939.3.5).

Covers:

  list (GET /api/event-sync-exclusions):
    * empty table → empty list, total=0
    * rule_id filter, evidence JSON parsed, newest-first ordering
    * pagination + invalid-input 400s

  create (POST /api/event-sync-exclusions):
    * happy path: row persisted with the four fingerprint components,
      journal entry (category event_sync, action_type exclusion_create)
    * idempotent on the fingerprint: second POST returns the existing row
      (already_existed=true), never a duplicate; a supplied note refreshes
    * unknown rule → 404; malformed body → 422

  delete (DELETE /api/event-sync-exclusions/{id}):
    * happy path: row removed + exclusion_delete journal entry
    * unknown id → 404
"""
from __future__ import annotations

import json

import pytest

from models import ChannelPipelineRule, EventSyncExclusion, JournalEntry
from services.event_sync_matcher import parse_event_name
from services.event_sync_review import master_event_key, stream_name_hash

MASTER_NAME = "PPV 02: Fury vs. Usyk Prelims @ 11 Jul 08:00 PM ET"
STREAM_NAME = "BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET"


def _event_key(master_name: str = MASTER_NAME) -> str:
    return master_event_key(parse_event_name(master_name, None))


def _make_rule(test_session, *, rule_id: int = 1) -> ChannelPipelineRule:
    rule = ChannelPipelineRule(
        id=rule_id,
        name=f"Event Sync {rule_id}",
        conditions="[]",
        actions="[]",
    )
    rule.set_event_sync_config({
        "master_group_id": 10,
        "secondary_group_ids": [20],
        "enabled": True,
    })
    test_session.add(rule)
    test_session.commit()
    return rule


def _create_body(rule_id: int = 1, **overrides) -> dict:
    body = {
        "rule_id": rule_id,
        "provider_id": 7,
        "stream_name_hash": stream_name_hash(STREAM_NAME),
        "event_key": _event_key(),
        "evidence": {
            "rule_name": f"Event Sync {rule_id}",
            "stream_name": STREAM_NAME,
            "master_channel_name": MASTER_NAME,
            "provider": "BoxProvider",
        },
    }
    body.update(overrides)
    return body


def _make_exclusion(test_session, *, rule_id: int = 1,
                    provider_id: int = 7,
                    stream_name: str = STREAM_NAME,
                    master_name: str = MASTER_NAME,
                    created_at: int = 1_752_800_000_000) -> EventSyncExclusion:
    row = EventSyncExclusion(
        rule_id=rule_id,
        provider_id=provider_id,
        stream_name_hash=stream_name_hash(stream_name),
        event_key=_event_key(master_name),
        created_at=created_at,
        evidence=json.dumps({
            "stream_name": stream_name,
            "master_channel_name": master_name,
        }),
    )
    test_session.add(row)
    test_session.commit()
    test_session.refresh(row)
    return row


class TestListEventSyncExclusions:
    @pytest.mark.asyncio
    async def test_empty_table_returns_empty_list(
        self, async_client, test_session
    ):
        resp = await async_client.get("/api/event-sync-exclusions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exclusions"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_lists_rows_with_parsed_evidence_newest_first(
        self, async_client, test_session
    ):
        _make_rule(test_session)
        older = _make_exclusion(test_session, created_at=1_000)
        newer = _make_exclusion(
            test_session, master_name="PPV 01: Fury vs. Usyk @ 11 Jul 08:00 PM ET",
            created_at=2_000,
        )

        resp = await async_client.get("/api/event-sync-exclusions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert [r["id"] for r in data["exclusions"]] == [newer.id, older.id]
        first = data["exclusions"][0]
        assert first["evidence"]["stream_name"] == STREAM_NAME
        assert first["stream_name_hash"] == stream_name_hash(STREAM_NAME)

    @pytest.mark.asyncio
    async def test_rule_filter(self, async_client, test_session):
        _make_rule(test_session, rule_id=1)
        _make_rule(test_session, rule_id=2)
        _make_exclusion(test_session, rule_id=1)
        _make_exclusion(test_session, rule_id=2)

        resp = await async_client.get("/api/event-sync-exclusions?rule_id=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["exclusions"][0]["rule_id"] == 2

    @pytest.mark.asyncio
    async def test_invalid_pagination_returns_400(
        self, async_client, test_session
    ):
        resp = await async_client.get("/api/event-sync-exclusions?page=0")
        assert resp.status_code == 400
        resp = await async_client.get(
            "/api/event-sync-exclusions?page_size=1000"
        )
        assert resp.status_code == 400


class TestCreateEventSyncExclusion:
    @pytest.mark.asyncio
    async def test_happy_path_persists_and_journals(
        self, async_client, test_session
    ):
        _make_rule(test_session)
        resp = await async_client.post(
            "/api/event-sync-exclusions", json=_create_body(note="bad feed")
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["already_existed"] is False
        assert data["note"] == "bad feed"
        assert data["stream_name_hash"] == stream_name_hash(STREAM_NAME)
        assert data["event_key"] == _event_key()

        row = test_session.query(EventSyncExclusion).one()
        assert row.rule_id == 1
        assert row.provider_id == 7

        entry = (
            test_session.query(JournalEntry)
            .filter(JournalEntry.action_type == "exclusion_create")
            .one()
        )
        assert entry.category == "event_sync"
        after = json.loads(entry.after_value)
        assert after["fingerprint"]["stream_name_hash"] \
            == stream_name_hash(STREAM_NAME)

    @pytest.mark.asyncio
    async def test_idempotent_on_fingerprint(
        self, async_client, test_session
    ):
        _make_rule(test_session)
        first = await async_client.post(
            "/api/event-sync-exclusions", json=_create_body()
        )
        assert first.status_code == 200
        second = await async_client.post(
            "/api/event-sync-exclusions", json=_create_body(note="updated")
        )
        assert second.status_code == 200
        data = second.json()
        assert data["already_existed"] is True
        assert data["id"] == first.json()["id"]
        assert data["note"] == "updated"
        assert test_session.query(EventSyncExclusion).count() == 1

    @pytest.mark.asyncio
    async def test_unknown_rule_returns_404(
        self, async_client, test_session
    ):
        resp = await async_client.post(
            "/api/event-sync-exclusions", json=_create_body(rule_id=999)
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_malformed_body_returns_422(
        self, async_client, test_session
    ):
        _make_rule(test_session)
        resp = await async_client.post(
            "/api/event-sync-exclusions",
            json=_create_body(stream_name_hash=""),
        )
        assert resp.status_code == 422
        resp = await async_client.post(
            "/api/event-sync-exclusions",
            json={"rule_id": 1},
        )
        assert resp.status_code == 422


class TestDeleteEventSyncExclusion:
    @pytest.mark.asyncio
    async def test_happy_path_removes_and_journals(
        self, async_client, test_session
    ):
        _make_rule(test_session)
        row = _make_exclusion(test_session)

        resp = await async_client.delete(
            f"/api/event-sync-exclusions/{row.id}"
        )
        assert resp.status_code == 204
        assert test_session.query(EventSyncExclusion).count() == 0

        entry = (
            test_session.query(JournalEntry)
            .filter(JournalEntry.action_type == "exclusion_delete")
            .one()
        )
        assert entry.category == "event_sync"
        before = json.loads(entry.before_value)
        assert before["fingerprint"]["event_key"] == _event_key()

    @pytest.mark.asyncio
    async def test_unknown_id_returns_404(
        self, async_client, test_session
    ):
        resp = await async_client.delete("/api/event-sync-exclusions/12345")
        assert resp.status_code == 404
