"""
Tests for the Event Sync review-queue API (bead ti939.3.2).

Covers:

  list (GET /api/event-sync-reviews):
    * empty queue → empty list, total=0
    * status + rule_id filters, evidence JSON parsed
    * pagination + invalid-input 400s

  accept (POST /{id}/accept):
    * happy path: decision recorded FIRST, snapshot ids re-verified
      against live Dispatcharr, stream attached, merge_stream journal
      entry carries attach_source="review_queue" (the bead's
      journal-distinction acceptance criterion)
    * sibling pending pairings for the same stream fingerprint are
      superseded
    * snapshot channel vanished / event identity mismatch / stream hash
      mismatch → accept still succeeds with attach_deferred_reason (the
      fingerprint-keyed decision self-applies next run)
    * idempotent double-accept; 409 on rejected/superseded; 404 missing

  reject (POST /{id}/reject):
    * happy path: durable suppression + review_reject journal entry
    * idempotent double-reject; 409 on accepted; 404 missing
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from models import ChannelPipelineRule, EventSyncReview, JournalEntry
from services.event_sync_matcher import parse_event_name
from services.event_sync_review import (
    master_event_key,
    stream_name_hash,
)

MASTER_NAME = "PPV 02: Fury vs. Usyk Prelims @ 11 Jul 08:00 PM ET"
STREAM_NAME = "BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET"


def _event_key(master_name: str = MASTER_NAME) -> str:
    """Same computation the enqueue path uses — content identity only."""
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


def _make_review(
    test_session,
    *,
    rule_id: int = 1,
    provider_id: int = 7,
    stream_name: str = STREAM_NAME,
    master_name: str = MASTER_NAME,
    status: str = "pending",
    master_channel_id: int | None = 501,
    stream_id: int | None = 4242,
    created_at: int = 1_752_300_000_000,
    last_seen_at: int = 1_752_300_000_000,
) -> EventSyncReview:
    row = EventSyncReview(
        rule_id=rule_id,
        provider_id=provider_id,
        stream_name_hash=stream_name_hash(stream_name),
        event_key=_event_key(master_name),
        status=status,
        created_at=created_at,
        last_seen_at=last_seen_at,
        evidence=json.dumps({
            "rule_name": f"Event Sync {rule_id}",
            "stream_name": stream_name,
            "provider": "BoxProvider",
            "secondary_group_id": 20,
            "stream_id": stream_id,
            "stream_parsed_title": "Fury vs. Usyk",
            "master_channel_name": master_name,
            "master_channel_id": master_channel_id,
            "master_parsed_title": "Fury vs. Usyk Prelims",
            "score": 1.0,
            "band": "attach",
            "team_verdict": "agree",
            "time_delta_minutes": 0.0,
            "ambiguous_reason": "contested_top_candidates",
        }),
    )
    test_session.add(row)
    test_session.commit()
    test_session.refresh(row)
    return row


def _mock_client_for_attach(row_master_name: str = MASTER_NAME,
                            channel_streams: list | None = None):
    """Dispatcharr mock whose snapshot ids re-verify successfully."""
    client = AsyncMock()
    client.get_channel.return_value = {
        "id": 501, "name": row_master_name,
        "streams": channel_streams if channel_streams is not None else [],
    }
    client.get_stream.return_value = {"id": 4242, "name": STREAM_NAME}
    client.update_channel.return_value = {"id": 501}
    return client


class TestListEventSyncReviews:
    @pytest.mark.asyncio
    async def test_empty_queue_returns_empty_list(self, async_client, test_session):
        response = await async_client.get("/api/event-sync-reviews")
        assert response.status_code == 200
        data = response.json()
        assert data["reviews"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_lists_pending_rows_with_parsed_evidence(
        self, async_client, test_session
    ):
        _make_rule(test_session)
        row = _make_review(test_session)
        response = await async_client.get("/api/event-sync-reviews")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        record = data["reviews"][0]
        assert record["id"] == row.id
        assert record["status"] == "pending"
        assert record["stream_name_hash"] == stream_name_hash(STREAM_NAME)
        assert record["event_key"] == _event_key()
        # Evidence arrives PARSED for the review UI's per-candidate display.
        assert record["evidence"]["stream_name"] == STREAM_NAME
        assert record["evidence"]["master_channel_name"] == MASTER_NAME
        assert record["evidence"]["score"] == 1.0
        assert record["evidence"]["team_verdict"] == "agree"

    @pytest.mark.asyncio
    async def test_status_and_rule_filters(self, async_client, test_session):
        _make_rule(test_session, rule_id=1)
        _make_rule(test_session, rule_id=2)
        _make_review(test_session, rule_id=1, status="pending")
        _make_review(test_session, rule_id=1, provider_id=8, status="rejected")
        _make_review(test_session, rule_id=2, status="pending")

        pending = await async_client.get(
            "/api/event-sync-reviews?status=pending&rule_id=1"
        )
        assert pending.json()["total"] == 1

        rejected = await async_client.get(
            "/api/event-sync-reviews?status=rejected"
        )
        assert rejected.json()["total"] == 1

    @pytest.mark.asyncio
    async def test_invalid_status_returns_400(self, async_client, test_session):
        response = await async_client.get("/api/event-sync-reviews?status=bogus")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_pagination_returns_400(self, async_client, test_session):
        assert (
            await async_client.get("/api/event-sync-reviews?page=0")
        ).status_code == 400
        assert (
            await async_client.get("/api/event-sync-reviews?page_size=9999")
        ).status_code == 400


class TestAcceptEventSyncReview:
    @pytest.mark.asyncio
    async def test_happy_path_attaches_and_journals_distinctly(
        self, async_client, test_session
    ):
        _make_rule(test_session)
        row = _make_review(test_session)
        mock_client = _mock_client_for_attach()

        with patch("routers.event_sync_reviews.get_client",
                   return_value=mock_client):
            response = await async_client.post(
                f"/api/event-sync-reviews/{row.id}/accept"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["attached"] is True
        assert data["already_attached"] is False
        assert data["attach_deferred_reason"] is None

        # Attach used the existing update-channel internals (idempotent
        # append of the stream id).
        mock_client.update_channel.assert_called_once()
        args = mock_client.update_channel.call_args
        assert args[0][0] == 501
        assert args[0][1] == {"streams": [4242]}

        # Decision persisted.
        test_session.expire_all()
        refreshed = test_session.query(EventSyncReview).filter(
            EventSyncReview.id == row.id
        ).one()
        assert refreshed.status == "accepted"
        assert refreshed.resolved_at is not None
        assert refreshed.resolution_source == "operator"

        # Journal: a review_accept decision entry AND a merge_stream entry
        # whose match.attach_source distinguishes the queue-driven attach
        # from threshold attaches (acceptance criterion).
        entries = test_session.query(JournalEntry).filter(
            JournalEntry.category == "event_sync"
        ).all()
        actions = {e.action_type for e in entries}
        assert "review_accept" in actions
        merge_entries = [e for e in entries if e.action_type == "merge_stream"]
        assert len(merge_entries) == 1
        after = json.loads(merge_entries[0].after_value)
        assert after["match"]["attach_source"] == "review_queue"
        assert after["match"]["review_id"] == row.id
        assert after["stream_ids"] == [4242]

    @pytest.mark.asyncio
    async def test_accept_supersedes_sibling_pending_pairings(
        self, async_client, test_session
    ):
        _make_rule(test_session)
        row = _make_review(test_session)
        sibling = _make_review(
            test_session,
            master_name="PPV 01: Fury vs. Usyk @ 11 Jul 08:00 PM ET",
        )
        # Different stream fingerprint — must NOT be touched.
        unrelated = _make_review(
            test_session,
            stream_name="BOX HD: Canelo vs. Crawford @ 11 Jul 08:00 PM ET",
            master_name="PPV 03: Canelo vs. Crawford @ 11 Jul 08:00 PM ET",
        )

        with patch("routers.event_sync_reviews.get_client",
                   return_value=_mock_client_for_attach()):
            response = await async_client.post(
                f"/api/event-sync-reviews/{row.id}/accept"
            )

        assert response.status_code == 200
        assert response.json()["superseded_siblings"] == 1

        test_session.expire_all()
        assert test_session.query(EventSyncReview).get(sibling.id).status \
            == "superseded"
        assert test_session.query(EventSyncReview).get(unrelated.id).status \
            == "pending"

    @pytest.mark.asyncio
    async def test_vanished_channel_defers_attach_but_records_decision(
        self, async_client, test_session
    ):
        import httpx as _httpx

        _make_rule(test_session)
        row = _make_review(test_session)
        mock_client = AsyncMock()
        request = _httpx.Request("GET", "http://dispatcharr/api/channels/501")
        mock_client.get_channel.side_effect = _httpx.HTTPStatusError(
            "404", request=request,
            response=_httpx.Response(404, request=request),
        )

        with patch("routers.event_sync_reviews.get_client",
                   return_value=mock_client):
            response = await async_client.post(
                f"/api/event-sync-reviews/{row.id}/accept"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["attached"] is False
        assert data["attach_deferred_reason"]
        mock_client.update_channel.assert_not_called()

        test_session.expire_all()
        assert test_session.query(EventSyncReview).get(row.id).status \
            == "accepted"

    @pytest.mark.asyncio
    async def test_recycled_channel_id_never_receives_the_stream(
        self, async_client, test_session
    ):
        """Snapshot channel id now carries a DIFFERENT event — the event-key
        re-verification must refuse the attach (security: fingerprints are
        the identity, snapshot ids are only a fast path)."""
        _make_rule(test_session)
        row = _make_review(test_session)
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {
            "id": 501,
            "name": "PPV 09: Canelo vs. Crawford @ 11 Jul 09:00 PM ET",
            "streams": [],
        }

        with patch("routers.event_sync_reviews.get_client",
                   return_value=mock_client):
            response = await async_client.post(
                f"/api/event-sync-reviews/{row.id}/accept"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["attached"] is False
        assert "identity" in data["attach_deferred_reason"]
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_hash_mismatch_defers_attach(
        self, async_client, test_session
    ):
        _make_rule(test_session)
        row = _make_review(test_session)
        mock_client = _mock_client_for_attach()
        mock_client.get_stream.return_value = {
            "id": 4242, "name": "TOTALLY DIFFERENT STREAM",
        }

        with patch("routers.event_sync_reviews.get_client",
                   return_value=mock_client):
            response = await async_client.post(
                f"/api/event-sync-reviews/{row.id}/accept"
            )

        assert response.status_code == 200
        assert response.json()["attached"] is False
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_attached_stream_is_a_noop(
        self, async_client, test_session
    ):
        _make_rule(test_session)
        row = _make_review(test_session)
        mock_client = _mock_client_for_attach(channel_streams=[4242])

        with patch("routers.event_sync_reviews.get_client",
                   return_value=mock_client):
            response = await async_client.post(
                f"/api/event-sync-reviews/{row.id}/accept"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["already_attached"] is True
        assert data["attached"] is False
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_idempotent_double_accept(self, async_client, test_session):
        _make_rule(test_session)
        row = _make_review(test_session, status="accepted")
        mock_client = AsyncMock()

        with patch("routers.event_sync_reviews.get_client",
                   return_value=mock_client):
            response = await async_client.post(
                f"/api/event-sync-reviews/{row.id}/accept"
            )

        assert response.status_code == 200
        assert response.json()["status"] == "accepted"
        # No re-attach on idempotent replay — runs are the applier.
        mock_client.get_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_accept_on_rejected_returns_409(self, async_client, test_session):
        _make_rule(test_session)
        row = _make_review(test_session, status="rejected")
        response = await async_client.post(
            f"/api/event-sync-reviews/{row.id}/accept"
        )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_accept_nonexistent_returns_404(self, async_client, test_session):
        response = await async_client.post("/api/event-sync-reviews/9999/accept")
        assert response.status_code == 404


class TestRejectEventSyncReview:
    @pytest.mark.asyncio
    async def test_happy_path_records_suppression(self, async_client, test_session):
        _make_rule(test_session)
        row = _make_review(test_session)

        response = await async_client.post(
            f"/api/event-sync-reviews/{row.id}/reject"
        )

        assert response.status_code == 200
        assert response.json()["status"] == "rejected"

        test_session.expire_all()
        refreshed = test_session.query(EventSyncReview).get(row.id)
        assert refreshed.status == "rejected"
        assert refreshed.resolved_at is not None
        assert refreshed.resolution_source == "operator"

        entries = test_session.query(JournalEntry).filter(
            JournalEntry.category == "event_sync",
            JournalEntry.action_type == "review_reject",
        ).all()
        assert len(entries) == 1
        after = json.loads(entries[0].after_value)
        assert after["fingerprint"]["stream_name_hash"] \
            == stream_name_hash(STREAM_NAME)

    @pytest.mark.asyncio
    async def test_idempotent_double_reject(self, async_client, test_session):
        _make_rule(test_session)
        row = _make_review(test_session, status="rejected")
        response = await async_client.post(
            f"/api/event-sync-reviews/{row.id}/reject"
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_reject_on_accepted_returns_409(self, async_client, test_session):
        _make_rule(test_session)
        row = _make_review(test_session, status="accepted")
        response = await async_client.post(
            f"/api/event-sync-reviews/{row.id}/reject"
        )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_reject_nonexistent_returns_404(self, async_client, test_session):
        response = await async_client.post("/api/event-sync-reviews/9999/reject")
        assert response.status_code == 404
