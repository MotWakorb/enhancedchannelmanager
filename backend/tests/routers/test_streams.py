"""
Unit tests for stream and provider endpoints.

Tests: GET /api/streams, GET /api/stream-groups, GET /api/providers,
       GET /api/providers/group-settings, POST /api/streams/by-ids
Mocks: get_client(), get_cache() to isolate from Dispatcharr and cache.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestGetStreams:
    """Tests for GET /api/streams endpoint."""

    @pytest.mark.asyncio
    async def test_returns_streams_from_client(self, async_client):
        """Returns paginated streams from Dispatcharr client."""
        mock_client = AsyncMock()
        mock_client.get_streams.return_value = {
            "count": 2,
            "results": [
                {"id": 1, "name": "Stream A", "channel_group": 10},
                {"id": 2, "name": "Stream B", "channel_group": 20},
            ],
        }
        mock_client.get_channel_groups.return_value = [
            {"id": 10, "name": "Sports"},
            {"id": 20, "name": "News"},
        ]
        mock_cache = MagicMock()
        mock_cache.get.return_value = None  # Cache miss

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.get("/api/streams")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert len(data["results"]) == 2
        # Verify group names were enriched
        assert data["results"][0]["channel_group_name"] == "Sports"
        assert data["results"][1]["channel_group_name"] == "News"

    @pytest.mark.asyncio
    async def test_returns_cached_result(self, async_client):
        """Returns cached result when available."""
        cached_data = {
            "count": 1,
            "results": [{"id": 1, "name": "Cached Stream"}],
        }
        mock_cache = MagicMock()
        mock_cache.get.return_value = cached_data

        with patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.get("/api/streams")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["results"][0]["name"] == "Cached Stream"

    @pytest.mark.asyncio
    async def test_bypass_cache(self, async_client):
        """bypass_cache=true skips cache lookup."""
        mock_client = AsyncMock()
        mock_client.get_streams.return_value = {"count": 0, "results": []}
        mock_client.get_channel_groups.return_value = []
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.get("/api/streams", params={"bypass_cache": True})

        assert response.status_code == 200
        # Client should have been called even if cache had data
        mock_client.get_streams.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_filter_params(self, async_client):
        """Passes search, group, and m3u_account filters to client."""
        mock_client = AsyncMock()
        mock_client.get_streams.return_value = {"count": 0, "results": []}
        mock_client.get_channel_groups.return_value = []
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.get("/api/streams", params={
                "search": "ESPN",
                "channel_group_name": "Sports",
                "m3u_account": 5,
                "bypass_cache": True,
            })

        assert response.status_code == 200
        mock_client.get_streams.assert_called_once_with(
            page=1,
            page_size=100,
            search="ESPN",
            channel_group_name="Sports",
            m3u_account=5,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("page", [0, -1])
    async def test_invalid_page_returns_422_not_500(self, async_client, page):
        """page < 1 is rejected by validation (422), never passed upstream to
        become a 500 (bead enhancedchannelmanager-g4z2h, systemic sibling of
        1a5mf)."""
        mock_client = AsyncMock()
        mock_client.get_streams.return_value = {"count": 0, "results": []}

        with patch("routers.streams.get_client", return_value=mock_client):
            response = await async_client.get("/api/streams", params={"page": page})

        assert response.status_code == 422
        mock_client.get_streams.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("page_size", [0, -5, 1001])
    async def test_invalid_page_size_returns_422_not_500(self, async_client, page_size):
        """page_size out of [1, 1000] is rejected by validation (422)."""
        mock_client = AsyncMock()
        mock_client.get_streams.return_value = {"count": 0, "results": []}

        with patch("routers.streams.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/streams", params={"page_size": page_size}
            )

        assert response.status_code == 422
        mock_client.get_streams.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_max_page_size_still_works(self, async_client):
        """page_size=500 (App.tsx's searchStreams/loadStreamGroup) passes
        through unchanged."""
        mock_client = AsyncMock()
        mock_client.get_streams.return_value = {"count": 0, "results": []}
        mock_client.get_channel_groups.return_value = []
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.get(
                "/api/streams", params={"page_size": 500, "bypass_cache": True}
            )

        assert response.status_code == 200
        mock_client.get_streams.assert_called_once_with(
            page=1, page_size=500, search=None,
            channel_group_name=None, m3u_account=None,
        )

    @pytest.mark.asyncio
    async def test_client_error_returns_500(self, async_client):
        """Returns 500 when Dispatcharr client raises."""
        mock_client = AsyncMock()
        mock_client.get_streams.side_effect = Exception("Connection refused")
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.get(
                "/api/streams",
                params={"bypass_cache": True},
            )

        assert response.status_code == 500


class TestGetStreamGroups:
    """Tests for GET /api/stream-groups endpoint."""

    @pytest.mark.asyncio
    async def test_returns_groups(self, async_client):
        """Returns stream groups with counts."""
        mock_client = AsyncMock()
        mock_client.get_stream_groups_with_counts.return_value = [
            {"name": "Sports", "count": 42},
            {"name": "News", "count": 15},
        ]
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.get("/api/stream-groups")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["name"] == "Sports"

    @pytest.mark.asyncio
    async def test_returns_cached_groups(self, async_client):
        """Returns cached groups when available."""
        cached = [{"name": "Cached", "count": 1}]
        mock_cache = MagicMock()
        mock_cache.get.return_value = cached

        with patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.get("/api/stream-groups")

        assert response.status_code == 200
        assert response.json()[0]["name"] == "Cached"


class TestGetProviders:
    """Tests for GET /api/providers endpoint."""

    @pytest.mark.asyncio
    async def test_returns_providers(self, async_client):
        """Returns list of M3U providers."""
        mock_client = AsyncMock()
        mock_client.get_m3u_accounts.return_value = [
            {"id": 1, "name": "Provider A"},
            {"id": 2, "name": "Provider B"},
        ]

        with patch("routers.streams.get_client", return_value=mock_client):
            response = await async_client.get("/api/providers")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["name"] == "Provider A"

    @pytest.mark.asyncio
    async def test_client_error_returns_500(self, async_client):
        """Returns 500 when client fails."""
        mock_client = AsyncMock()
        mock_client.get_m3u_accounts.side_effect = Exception("Timeout")

        with patch("routers.streams.get_client", return_value=mock_client):
            response = await async_client.get("/api/providers")

        assert response.status_code == 500


class TestGetProviderGroupSettings:
    """Tests for GET /api/providers/group-settings endpoint."""

    @pytest.mark.asyncio
    async def test_returns_group_settings(self, async_client):
        """Returns group settings mapped by channel_group_id."""
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {
            "10": {"enabled": True},
            "20": {"enabled": False},
        }

        with patch("routers.streams.get_client", return_value=mock_client):
            response = await async_client.get("/api/providers/group-settings")

        assert response.status_code == 200
        data = response.json()
        assert data["10"]["enabled"] is True


class TestGetProviderGroupSettingsByProvider:
    """GET /api/providers/group-settings/by-provider — non-collapsed (bead 38dzi)."""

    @pytest.mark.asyncio
    async def test_returns_one_row_per_provider_group_pair(self, async_client):
        mock_client = AsyncMock()
        # Same channel group (99) carried by TWO providers — the case the
        # collapsed endpoint hides.
        mock_client.get_m3u_group_settings_by_provider.return_value = {
            (3, 99): {
                "channel_group": 99, "auto_channel_sync": True,
                "enabled": True, "stream_count": 40,
                "m3u_account_id": 3, "m3u_account_name": "Provider A",
            },
            (11, 99): {
                "channel_group": 99, "auto_channel_sync": False,
                "enabled": True, "stream_count": 12,
                "m3u_account_id": 11, "m3u_account_name": "Provider B",
            },
        }

        with patch("routers.streams.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/providers/group-settings/by-provider"
            )

        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 2
        by_provider = {r["m3u_account_id"]: r for r in rows}
        assert by_provider[3]["channel_group_id"] == 99
        assert by_provider[3]["auto_channel_sync"] is True
        assert by_provider[11]["auto_channel_sync"] is False
        assert by_provider[3]["m3u_account_name"] == "Provider A"


class TestGetStreamsByIds:
    """Tests for POST /api/streams/by-ids endpoint."""

    @pytest.mark.asyncio
    async def test_returns_streams_by_ids(self, async_client):
        """Returns streams matching the given IDs."""
        mock_client = AsyncMock()
        mock_client.get_streams_by_ids.return_value = [
            {"id": 1, "name": "Stream 1"},
            {"id": 5, "name": "Stream 5"},
        ]

        with patch("routers.streams.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/streams/by-ids",
                json={"stream_ids": [1, 5]},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        mock_client.get_streams_by_ids.assert_called_once_with([1, 5])

    @pytest.mark.asyncio
    async def test_client_error_returns_500(self, async_client):
        """Returns 500 when client fails."""
        mock_client = AsyncMock()
        mock_client.get_streams_by_ids.side_effect = Exception("Error")

        with patch("routers.streams.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/streams/by-ids",
                json={"stream_ids": [1]},
            )

        assert response.status_code == 500


def _paged_get_streams(pages):
    """Build an AsyncMock get_streams side_effect that serves `pages` in order.

    Each entry in `pages` is a list of stream dicts for that page. `count` is
    derived from the total across all pages, mirroring how Dispatcharr reports
    pagination totals.
    """
    total = sum(len(p) for p in pages)

    async def _get_streams(page=1, page_size=1000, **_kwargs):
        idx = page - 1
        results = pages[idx] if idx < len(pages) else []
        return {"results": results, "count": total}

    return _get_streams


class TestGetStaleStreamIds:
    """Tests for GET /api/streams/stale-ids."""

    @pytest.mark.asyncio
    async def test_returns_only_stale_stream_ids(self, async_client):
        """Filters to streams with is_stale truthy, carrying last_seen."""
        mock_client = AsyncMock()
        mock_client.get_streams.side_effect = _paged_get_streams([
            [
                {"id": 1, "name": "A", "is_stale": True, "last_seen": "2026-07-01T00:00:00Z"},
                {"id": 2, "name": "B", "is_stale": False, "last_seen": None},
                {"id": 3, "name": "C", "is_stale": True, "last_seen": None},
            ],
        ])
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.get("/api/streams/stale-ids")

        assert response.status_code == 200
        data = response.json()
        assert sorted(data["stale_stream_ids"]) == [1, 3]
        assert data["count"] == 2
        assert data["last_seen"]["1"] == "2026-07-01T00:00:00Z"
        assert data["last_seen"]["3"] is None
        assert "2" not in data["last_seen"]

    @pytest.mark.asyncio
    async def test_paginates_past_1000(self, async_client):
        """Scans every page until the reported count is reached."""
        page1 = [{"id": i, "name": f"S{i}", "is_stale": False} for i in range(1000)]
        page2 = [
            {"id": 1000, "name": "Stale One", "is_stale": True, "last_seen": "2026-06-01T00:00:00Z"},
            {"id": 1001, "name": "Not Stale", "is_stale": False},
        ]
        mock_client = AsyncMock()
        mock_client.get_streams.side_effect = _paged_get_streams([page1, page2])
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.get("/api/streams/stale-ids")

        assert response.status_code == 200
        data = response.json()
        assert data["stale_stream_ids"] == [1000]
        assert data["count"] == 1
        assert mock_client.get_streams.call_count == 2

    @pytest.mark.asyncio
    async def test_caches_result_second_call_no_client_hit(self, async_client):
        """A second call within TTL is served from cache, not the client."""
        mock_client = AsyncMock()
        mock_client.get_streams.side_effect = _paged_get_streams([
            [{"id": 1, "name": "A", "is_stale": True, "last_seen": None}],
        ])
        real_cache = {}

        class FakeCache:
            def get(self, key, ttl=None):
                return real_cache.get(key)

            def set(self, key, value):
                real_cache[key] = value

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=FakeCache()):
            first = await async_client.get("/api/streams/stale-ids")
            second = await async_client.get("/api/streams/stale-ids")

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()
        # Only the first call should have hit the upstream client.
        assert mock_client.get_streams.call_count == 1

    @pytest.mark.asyncio
    async def test_bypass_cache_refetches(self, async_client):
        """bypass_cache=True skips the cache and re-scans the client."""
        mock_client = AsyncMock()
        mock_client.get_streams.side_effect = _paged_get_streams([
            [{"id": 1, "name": "A", "is_stale": True, "last_seen": None}],
        ])
        real_cache = {}

        class FakeCache:
            def get(self, key, ttl=None):
                return real_cache.get(key)

            def set(self, key, value):
                real_cache[key] = value

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=FakeCache()):
            await async_client.get("/api/streams/stale-ids")
            response = await async_client.get("/api/streams/stale-ids?bypass_cache=true")

        assert response.status_code == 200
        assert mock_client.get_streams.call_count == 2

    @pytest.mark.asyncio
    async def test_no_stale_streams_returns_empty(self, async_client):
        """Empty-state: no provider-stale streams returns an empty list, not an error."""
        mock_client = AsyncMock()
        mock_client.get_streams.side_effect = _paged_get_streams([
            [{"id": 1, "name": "A", "is_stale": False}],
        ])
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.get("/api/streams/stale-ids")

        assert response.status_code == 200
        data = response.json()
        assert data["stale_stream_ids"] == []
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_client_error_returns_500(self, async_client):
        """Upstream failure surfaces as a 500, not a silent empty result."""
        mock_client = AsyncMock()
        mock_client.get_streams.side_effect = Exception("Dispatcharr unreachable")
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        with patch("routers.streams.get_client", return_value=mock_client), \
             patch("routers.streams.get_cache", return_value=mock_cache):
            response = await async_client.get("/api/streams/stale-ids")

        assert response.status_code == 500
