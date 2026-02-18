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
