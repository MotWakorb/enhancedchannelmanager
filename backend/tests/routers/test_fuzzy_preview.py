"""Component B — no-write scored fuzzy preview endpoint (jnzst).

GET /api/auto-creation/fuzzy-preview returns scored (stream, channel) triples
for a group-scoped set with REAL pagination, DoS caps, and ZERO writes. The
mandatory invariant: no mutating Dispatcharr method is ever called.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


CHANNEL_TVG_ID = "WBAY-DT(ABC)(WBAYDT).us"


def _client_with(channels, streams, group_name="Locals"):
    """Build a mock Dispatcharr client. Mutating methods are AsyncMocks so a
    test can assert they were never awaited."""
    client = MagicMock()
    client.get_channels = AsyncMock(return_value={"results": channels, "next": None})
    client.get_streams = AsyncMock(return_value={"results": streams, "next": None})
    client._channel_group_name_for_id = AsyncMock(return_value=group_name)
    # Mutating methods — must NEVER be called on the preview path.
    client.update_channel = AsyncMock()
    client.channels_add_stream = AsyncMock()
    client.add_stream_to_channel = AsyncMock()
    client.create_channel = AsyncMock()
    client.delete_channel = AsyncMock()
    return client


def _wbay_channel(group_id=42):
    return {"id": "1", "name": "ABC: WBAY Green Bay", "tvg_id": CHANNEL_TVG_ID,
            "channel_group_id": group_id}


class TestFuzzyPreview:
    @pytest.mark.asyncio
    async def test_returns_scored_triples_with_no_writes(self, async_client):
        channels = [_wbay_channel(), {"id": "2", "name": "NBC WGBA Green Bay",
                                      "tvg_id": None, "channel_group_id": 42}]
        streams = [
            {"id": 201, "name": "WI | Green Bay | ABC 2 WBAY", "tvg_id": None},
            {"id": 202, "name": "WI | Green Bay | NBC WGBA", "tvg_id": None},
        ]
        client = _client_with(channels, streams)
        with patch("routers.channel_pipeline.get_client", return_value=client):
            resp = await async_client.get(
                "/api/auto-creation/fuzzy-preview",
                params={"group_ids": [42], "min_score": 0.6},
            )
        assert resp.status_code == 200
        data = resp.json()
        # Triples present with the expected shape.
        assert data["min_score"] == 0.6
        assert all(
            {"stream_id", "channel_id", "score", "callsign_verdict", "signal"}
            <= set(t) for t in data["triples"]
        )
        # WBAY stream -> WBAY channel admitted; WGBA->WBAY is a conflict (0.0,
        # below min_score) and excluded.
        pairs = {(t["stream_id"], t["channel_id"]) for t in data["triples"]}
        assert (201, "1") in pairs
        assert (202, "1") not in pairs  # M1 conflict, score 0.0 < 0.6

        # MANDATORY: zero writes.
        client.update_channel.assert_not_called()
        client.channels_add_stream.assert_not_called()
        client.add_stream_to_channel.assert_not_called()
        client.create_channel.assert_not_called()
        client.delete_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_can_expose_sub_floor_scores(self, async_client):
        # min_score below the 0.60 floor is allowed here (preview never writes).
        channels = [_wbay_channel()]
        streams = [{"id": 201, "name": "ABC something only loosely related",
                    "tvg_id": None}]
        client = _client_with(channels, streams)
        with patch("routers.channel_pipeline.get_client", return_value=client):
            resp = await async_client.get(
                "/api/auto-creation/fuzzy-preview",
                params={"group_ids": [42], "min_score": 0.1},
            )
        assert resp.status_code == 200
        assert resp.json()["min_score"] == 0.1

    @pytest.mark.asyncio
    async def test_real_pagination(self, async_client):
        # 3 ADMISSIBLE WBAY 'match' triples + 1 WBAY stream → 3 triples;
        # page_size=2. (Callsign-absent channels are no longer admissible by
        # default — FIX 2 — so pagination is exercised with match-verdict pairs
        # that the shared policy admits.)
        channels = [
            {"id": str(i), "name": f"ABC {i} WBAY Green Bay",
             "tvg_id": CHANNEL_TVG_ID, "channel_group_id": 42}
            for i in range(3)
        ]
        streams = [{"id": 201, "name": "WI | Green Bay | ABC 2 WBAY",
                    "tvg_id": None}]
        client = _client_with(channels, streams)
        with patch("routers.channel_pipeline.get_client", return_value=client):
            resp = await async_client.get(
                "/api/auto-creation/fuzzy-preview",
                params={"group_ids": [42], "min_score": 0.1,
                        "page": 1, "page_size": 2},
            )
        data = resp.json()
        assert data["total"] == 3
        assert data["total_pages"] == 2  # NOT a degenerate total_pages=1 stub
        assert len(data["triples"]) == 2
        assert data["page"] == 1
        assert all(t["callsign_verdict"] == "match" for t in data["triples"])

    @pytest.mark.asyncio
    async def test_empty_group_ids_rejected(self, async_client):
        client = _client_with([], [])
        with patch("routers.channel_pipeline.get_client", return_value=client):
            resp = await async_client.get(
                "/api/auto-creation/fuzzy-preview", params={"min_score": 0.6},
            )
        # No group_ids at all → 422 (required query param).
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_duplicate_group_ids_rejected(self, async_client):
        client = _client_with([], [])
        with patch("routers.channel_pipeline.get_client", return_value=client):
            resp = await async_client.get(
                "/api/auto-creation/fuzzy-preview",
                params={"group_ids": [42, 42], "min_score": 0.6},
            )
        assert resp.status_code == 400
        assert "duplicate" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_too_many_group_ids_rejected(self, async_client):
        client = _client_with([], [])
        many = list(range(100))
        with patch("routers.channel_pipeline.get_client", return_value=client):
            resp = await async_client.get(
                "/api/auto-creation/fuzzy-preview",
                params={"group_ids": many, "min_score": 0.6},
            )
        assert resp.status_code == 400
        assert "cap" in resp.json()["detail"].lower()


class TestPreviewAdmissionPolicy:
    """FIX 2 — the preview returns ADMISSIBLE-only triples via the shared
    ``is_admissible`` policy, so the MCP write tools inherit M1/M2.

    * An ``absent`` (no-callsign) pair is NOT returned by default, even when
      its score clears ``min_score`` (default policy requires a callsign).
    * A ``conflict`` (M1) pair is NEVER returned, even at ``min_score == 0``.
    * An ``absent`` pair at score >= 0.90 IS returned only with
      ``allow_no_callsign=True``.
    """

    @pytest.mark.asyncio
    async def test_absent_pair_not_returned_by_default(self, async_client):
        # "Travel Channel" vs "Travel Time" → score 0.7059, verdict absent.
        # Above min_score=0.6, but absent → not admissible by default.
        channels = [{"id": "1", "name": "Travel Time", "tvg_id": None,
                     "channel_group_id": 42}]
        streams = [{"id": 201, "name": "Travel Channel", "tvg_id": None}]
        client = _client_with(channels, streams)
        with patch("routers.channel_pipeline.get_client", return_value=client):
            resp = await async_client.get(
                "/api/auto-creation/fuzzy-preview",
                params={"group_ids": [42], "min_score": 0.6},
            )
        assert resp.status_code == 200
        # No admissible triples — the absent pair is excluded.
        assert resp.json()["triples"] == []

    @pytest.mark.asyncio
    async def test_conflict_pair_never_returned_even_at_min_score_zero(
        self, async_client
    ):
        # WGBA stream vs WBAY channel → M1 conflict (score 0.0). Even with
        # min_score=0 (which would admit a 0.0-scoring match), a conflict is
        # never admissible.
        channels = [_wbay_channel()]
        streams = [{"id": 202, "name": "WI | Green Bay | NBC WGBA",
                    "tvg_id": None}]
        client = _client_with(channels, streams)
        with patch("routers.channel_pipeline.get_client", return_value=client):
            resp = await async_client.get(
                "/api/auto-creation/fuzzy-preview",
                params={"group_ids": [42], "min_score": 0.0},
            )
        assert resp.status_code == 200
        pairs = {(t["stream_id"], t["channel_id"]) for t in resp.json()["triples"]}
        assert (202, "1") not in pairs

    @pytest.mark.asyncio
    async def test_high_absent_pair_returned_only_with_opt_in(self, async_client):
        # "Travel Channel" vs "Travel Channel" → score 1.0, verdict absent
        # (>= NO_CALLSIGN_FLOOR 0.90). Default: excluded. allow_no_callsign:
        # included.
        channels = [{"id": "1", "name": "Travel Channel", "tvg_id": None,
                     "channel_group_id": 42}]
        streams = [{"id": 201, "name": "Travel Channel", "tvg_id": None}]
        client = _client_with(channels, streams)

        with patch("routers.channel_pipeline.get_client", return_value=client):
            resp_default = await async_client.get(
                "/api/auto-creation/fuzzy-preview",
                params={"group_ids": [42], "min_score": 0.6},
            )
        assert resp_default.json()["triples"] == []  # absent, opt-in off

        with patch("routers.channel_pipeline.get_client", return_value=client):
            resp_optin = await async_client.get(
                "/api/auto-creation/fuzzy-preview",
                params={"group_ids": [42], "min_score": 0.6,
                        "allow_no_callsign": True},
            )
        triples = resp_optin.json()["triples"]
        assert len(triples) == 1
        assert triples[0]["callsign_verdict"] == "absent"
        assert triples[0]["score"] >= 0.90

    @pytest.mark.asyncio
    async def test_mid_absent_pair_excluded_even_with_opt_in(self, async_client):
        # "Travel Channel" vs "Travel Time" → 0.7059 absent: BELOW the 0.90
        # no-callsign floor, so even allow_no_callsign=True excludes it.
        channels = [{"id": "1", "name": "Travel Time", "tvg_id": None,
                     "channel_group_id": 42}]
        streams = [{"id": 201, "name": "Travel Channel", "tvg_id": None}]
        client = _client_with(channels, streams)
        with patch("routers.channel_pipeline.get_client", return_value=client):
            resp = await async_client.get(
                "/api/auto-creation/fuzzy-preview",
                params={"group_ids": [42], "min_score": 0.6,
                        "allow_no_callsign": True},
            )
        assert resp.json()["triples"] == []
