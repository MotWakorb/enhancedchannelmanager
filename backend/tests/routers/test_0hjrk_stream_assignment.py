"""
TDD tests for enhancedchannelmanager-0hjrk.6 — stream assignment status.

Dispatcharr's POST /api/channels/streams/by-ids/ and GET /api/streams responses
carry NO per-stream channel linkage (channels/channel_id/channel are all None
even for an assigned stream — verified live 2026-05-24). The authoritative
source of assignment is each CHANNEL's `streams` ID list. So assignment is
derived by paginating get_channels() and building a reverse
stream_id -> [channel_id] index.

These tests cover:
  * POST /api/streams/by-ids with include_assignment=True derives
    has_channel / assigned_channel_id / assigned_channel_ids correctly for
    assigned AND unassigned streams.
  * include_assignment=False (the default) does NOT fetch channels and adds
    no assignment fields — the default path stays cheap.
  * The reverse index is cached: a second include_assignment call within the
    TTL does not re-paginate get_channels().
  * GET /api/streams supports include_assignment for the list path enrichment.

Mocks: get_client(), get_cache() at the router module level
(per backend/CLAUDE.md).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _channels_page(channels, count=None):
    """Build a single-page get_channels() response envelope."""
    return {"count": count if count is not None else len(channels), "results": channels}


def _passthrough_cache():
    """A MagicMock cache whose get() always misses (forces a fresh fetch)."""
    cache = MagicMock()
    cache.get.return_value = None
    return cache


class TestByIdsIncludeAssignment:
    """POST /api/streams/by-ids with include_assignment=True."""

    @pytest.mark.asyncio
    async def test_assigned_and_unassigned_streams(self, async_client):
        """Assigned streams get has_channel/assigned_channel_id(s); unassigned do not."""
        mock_client = AsyncMock()
        # Stream 1 is in channel 100; stream 2 is in channels 100 and 200;
        # stream 3 is assigned to nothing.
        mock_client.get_streams_by_ids.return_value = [
            {"id": 1, "name": "Stream 1"},
            {"id": 2, "name": "Stream 2"},
            {"id": 3, "name": "Stream 3"},
        ]
        mock_client.get_channels.return_value = _channels_page([
            {"id": 100, "name": "Channel A", "streams": [1, 2]},
            {"id": 200, "name": "Channel B", "streams": [2]},
        ])
        mock_cache = _passthrough_cache()

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.post(
                "/api/streams/by-ids",
                json={"stream_ids": [1, 2, 3], "include_assignment": True},
            )

        assert response.status_code == 200
        data = response.json()
        by_id = {s["id"]: s for s in data}

        # Stream 1: assigned to channel 100 only.
        assert by_id[1]["has_channel"] is True
        assert by_id[1]["assigned_channel_id"] == 100
        assert by_id[1]["assigned_channel_ids"] == [100]

        # Stream 2: assigned to two channels — first wins for assigned_channel_id.
        assert by_id[2]["has_channel"] is True
        assert by_id[2]["assigned_channel_id"] in (100, 200)
        assert sorted(by_id[2]["assigned_channel_ids"]) == [100, 200]

        # Stream 3: unassigned.
        assert by_id[3]["has_channel"] is False
        assert by_id[3]["assigned_channel_id"] is None
        assert by_id[3]["assigned_channel_ids"] == []

        # The channel reverse index was fetched once.
        assert mock_client.get_channels.called

    @pytest.mark.asyncio
    async def test_default_does_not_fetch_channels(self, async_client):
        """include_assignment defaults to False: no channel fetch, no assignment fields."""
        mock_client = AsyncMock()
        mock_client.get_streams_by_ids.return_value = [
            {"id": 1, "name": "Stream 1"},
        ]
        mock_cache = _passthrough_cache()

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.post(
                "/api/streams/by-ids",
                json={"stream_ids": [1]},
            )

        assert response.status_code == 200
        data = response.json()
        # No channel fetch on the default path.
        mock_client.get_channels.assert_not_called()
        # No assignment fields added.
        assert "has_channel" not in data[0]
        assert "assigned_channel_id" not in data[0]
        assert "assigned_channel_ids" not in data[0]

    @pytest.mark.asyncio
    async def test_explicit_false_does_not_fetch_channels(self, async_client):
        """include_assignment=False explicitly: still no channel fetch."""
        mock_client = AsyncMock()
        mock_client.get_streams_by_ids.return_value = [{"id": 1, "name": "Stream 1"}]
        mock_cache = _passthrough_cache()

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.post(
                "/api/streams/by-ids",
                json={"stream_ids": [1], "include_assignment": False},
            )

        assert response.status_code == 200
        mock_client.get_channels.assert_not_called()

    @pytest.mark.asyncio
    async def test_paginates_all_channel_pages(self, async_client):
        """The reverse index spans all channel pages, not just the first."""
        mock_client = AsyncMock()
        mock_client.get_streams_by_ids.return_value = [
            {"id": 1, "name": "Stream 1"},
            {"id": 2, "name": "Stream 2"},
        ]
        # Two pages: stream 1 in channel on page 1, stream 2 on page 2.
        pages = [
            _channels_page([{"id": 100, "name": "A", "streams": [1]}], count=2),
            _channels_page([{"id": 200, "name": "B", "streams": [2]}], count=2),
        ]
        mock_client.get_channels.side_effect = pages
        mock_cache = _passthrough_cache()

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.post(
                "/api/streams/by-ids",
                json={"stream_ids": [1, 2], "include_assignment": True},
            )

        assert response.status_code == 200
        by_id = {s["id"]: s for s in response.json()}
        assert by_id[1]["assigned_channel_ids"] == [100]
        assert by_id[2]["assigned_channel_ids"] == [200]
        assert mock_client.get_channels.call_count == 2


class TestReverseIndexCache:
    """The reverse stream->channel index is cached with a short TTL."""

    @pytest.mark.asyncio
    async def test_second_call_within_ttl_does_not_refetch(self, async_client):
        """Two include_assignment calls share one channel fetch via the cache."""
        mock_client = AsyncMock()
        mock_client.get_streams_by_ids.return_value = [{"id": 1, "name": "Stream 1"}]
        mock_client.get_channels.return_value = _channels_page([
            {"id": 100, "name": "A", "streams": [1]},
        ])

        # A real-ish cache: stores on set, returns the stored value on get.
        store = {}

        def cache_get(key, ttl=None):
            return store.get(key)

        def cache_set(key, value):
            store[key] = value

        mock_cache = MagicMock()
        mock_cache.get.side_effect = cache_get
        mock_cache.set.side_effect = cache_set

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            r1 = await async_client.post(
                "/api/streams/by-ids",
                json={"stream_ids": [1], "include_assignment": True},
            )
            r2 = await async_client.post(
                "/api/streams/by-ids",
                json={"stream_ids": [1], "include_assignment": True},
            )

        assert r1.status_code == 200
        assert r2.status_code == 200
        # Channels fetched exactly once across two assignment calls.
        assert mock_client.get_channels.call_count == 1
        # Both responses still correctly assigned.
        assert r1.json()[0]["assigned_channel_ids"] == [100]
        assert r2.json()[0]["assigned_channel_ids"] == [100]

    @pytest.mark.asyncio
    async def test_index_read_uses_short_ttl(self, async_client):
        """The index is read from cache with the documented short TTL, not the default."""
        from routers.streams import ASSIGNMENT_INDEX_TTL_SECONDS

        mock_client = AsyncMock()
        mock_client.get_streams_by_ids.return_value = [{"id": 1, "name": "Stream 1"}]
        mock_client.get_channels.return_value = _channels_page([
            {"id": 100, "name": "A", "streams": [1]},
        ])
        mock_cache = MagicMock()
        mock_cache.get.return_value = None  # force a fetch

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            await async_client.post(
                "/api/streams/by-ids",
                json={"stream_ids": [1], "include_assignment": True},
            )

        # The index cache read must pass the short TTL.
        ttl_calls = [
            c for c in mock_cache.get.call_args_list
            if c.kwargs.get("ttl") == ASSIGNMENT_INDEX_TTL_SECONDS
            or (len(c.args) > 1 and c.args[1] == ASSIGNMENT_INDEX_TTL_SECONDS)
        ]
        assert ttl_calls, (
            "Reverse index should be read from cache with the short "
            f"ASSIGNMENT_INDEX_TTL_SECONDS={ASSIGNMENT_INDEX_TTL_SECONDS}s TTL"
        )
        # TTL is in the documented short range (30-60s).
        assert 30 <= ASSIGNMENT_INDEX_TTL_SECONDS <= 60


class TestListIncludeAssignment:
    """GET /api/streams supports include_assignment enrichment."""

    @pytest.mark.asyncio
    async def test_list_enriches_with_assignment(self, async_client):
        """include_assignment=True adds has_channel/assigned_channel_ids to list results."""
        mock_client = AsyncMock()
        mock_client.get_streams.return_value = {
            "count": 2,
            "results": [
                {"id": 1, "name": "Stream 1", "channel_group": 10},
                {"id": 2, "name": "Stream 2", "channel_group": 10},
            ],
        }
        mock_client.get_channel_groups.return_value = [{"id": 10, "name": "Sports"}]
        mock_client.get_channels.return_value = _channels_page([
            {"id": 100, "name": "A", "streams": [1]},
        ])
        mock_cache = _passthrough_cache()

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.get(
                "/api/streams",
                params={"include_assignment": True, "bypass_cache": True},
            )

        assert response.status_code == 200
        by_id = {s["id"]: s for s in response.json()["results"]}
        assert by_id[1]["has_channel"] is True
        assert by_id[1]["assigned_channel_ids"] == [100]
        assert by_id[2]["has_channel"] is False
        assert by_id[2]["assigned_channel_ids"] == []

    @pytest.mark.asyncio
    async def test_list_default_no_channel_fetch(self, async_client):
        """Default list path does not fetch channels and adds no assignment fields."""
        mock_client = AsyncMock()
        mock_client.get_streams.return_value = {
            "count": 1,
            "results": [{"id": 1, "name": "Stream 1", "channel_group": 10}],
        }
        mock_client.get_channel_groups.return_value = [{"id": 10, "name": "Sports"}]
        mock_cache = _passthrough_cache()

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.get(
                "/api/streams",
                params={"bypass_cache": True},
            )

        assert response.status_code == 200
        mock_client.get_channels.assert_not_called()
        result = response.json()["results"][0]
        assert "has_channel" not in result
