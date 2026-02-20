"""
Unit tests for stats, enhanced stats, and popularity endpoints.

Tests: 15 endpoints covering channel stats, activity, bandwidth,
       watch history, unique viewers, popularity rankings.
Mocks: get_client(), BandwidthTracker, PopularityCalculator.
"""
import pytest
from unittest.mock import AsyncMock, patch


class TestChannelStats:
    """Tests for GET /api/stats/channels."""

    @pytest.mark.asyncio
    async def test_returns_stats(self, async_client):
        """Returns channel stats from client."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "active_channels": 5, "total_clients": 3,
        }

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        assert response.json()["active_channels"] == 5

    @pytest.mark.asyncio
    async def test_client_error(self, async_client):
        """Returns 500 on client error."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.side_effect = Exception("Timeout")

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 500


class TestChannelStatsDetail:
    """Tests for GET /api/stats/channels/{channel_id}."""

    @pytest.mark.asyncio
    async def test_returns_detail(self, async_client):
        """Returns detailed stats for a channel."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats_detail.return_value = {
            "channel_id": 42, "clients": [],
        }

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels/42")

        assert response.status_code == 200
        mock_client.get_channel_stats_detail.assert_called_once_with(42)


class TestSystemEvents:
    """Tests for GET /api/stats/activity."""

    @pytest.mark.asyncio
    async def test_returns_events(self, async_client):
        """Returns system events."""
        mock_client = AsyncMock()
        mock_client.get_system_events.return_value = {"events": []}

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/activity")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_passes_filters(self, async_client):
        """Passes limit, offset, event_type filters."""
        mock_client = AsyncMock()
        mock_client.get_system_events.return_value = {"events": []}

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/activity", params={
                "limit": 50, "offset": 10, "event_type": "channel_start",
            })

        assert response.status_code == 200
        mock_client.get_system_events.assert_called_once_with(
            limit=50, offset=10, event_type="channel_start",
        )

    @pytest.mark.asyncio
    async def test_clamps_limit(self, async_client):
        """Limit is clamped to 1000."""
        mock_client = AsyncMock()
        mock_client.get_system_events.return_value = {"events": []}

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/activity", params={"limit": 5000})

        assert response.status_code == 200
        call_args = mock_client.get_system_events.call_args
        assert call_args[1]["limit"] == 1000


class TestStopChannel:
    """Tests for POST /api/stats/channels/{channel_id}/stop."""

    @pytest.mark.asyncio
    async def test_stops_channel(self, async_client):
        """Stops a channel."""
        mock_client = AsyncMock()
        mock_client.stop_channel.return_value = {"status": "stopped"}

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.post("/api/stats/channels/42/stop")

        assert response.status_code == 200


class TestStopClient:
    """Tests for POST /api/stats/channels/{channel_id}/stop-client."""

    @pytest.mark.asyncio
    async def test_stops_client(self, async_client):
        """Stops a client connection."""
        mock_client = AsyncMock()
        mock_client.stop_client.return_value = {"status": "stopped"}

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.post("/api/stats/channels/42/stop-client")

        assert response.status_code == 200


class TestBandwidthStats:
    """Tests for GET /api/stats/bandwidth."""

    @pytest.mark.asyncio
    async def test_returns_bandwidth(self, async_client):
        """Returns bandwidth summary."""
        with patch("routers.stats.BandwidthTracker.get_bandwidth_summary", return_value={
            "today": {"bytes_in": 1000},
        }):
            response = await async_client.get("/api/stats/bandwidth")

        assert response.status_code == 200
        assert "today" in response.json()


class TestTopWatched:
    """Tests for GET /api/stats/top-watched."""

    @pytest.mark.asyncio
    async def test_returns_top_channels(self, async_client):
        """Returns top watched channels."""
        with patch("routers.stats.BandwidthTracker.get_top_watched_channels", return_value=[
            {"channel_name": "ESPN", "watch_count": 100},
        ]):
            response = await async_client.get("/api/stats/top-watched")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_passes_params(self, async_client):
        """Passes limit and sort_by params."""
        with patch("routers.stats.BandwidthTracker.get_top_watched_channels", return_value=[]) as mock:
            response = await async_client.get("/api/stats/top-watched", params={
                "limit": 5, "sort_by": "time",
            })

        assert response.status_code == 200
        mock.assert_called_once_with(limit=5, sort_by="time")


class TestUniqueViewers:
    """Tests for GET /api/stats/unique-viewers."""

    @pytest.mark.asyncio
    async def test_returns_summary(self, async_client):
        """Returns unique viewers summary."""
        with patch("routers.stats.BandwidthTracker.get_unique_viewers_summary", return_value={
            "total_unique": 42,
        }):
            response = await async_client.get("/api/stats/unique-viewers")

        assert response.status_code == 200


class TestChannelBandwidth:
    """Tests for GET /api/stats/channel-bandwidth."""

    @pytest.mark.asyncio
    async def test_returns_stats(self, async_client):
        """Returns per-channel bandwidth stats."""
        with patch("routers.stats.BandwidthTracker.get_channel_bandwidth_stats", return_value=[]):
            response = await async_client.get("/api/stats/channel-bandwidth")

        assert response.status_code == 200


class TestUniqueViewersByChannel:
    """Tests for GET /api/stats/unique-viewers-by-channel."""

    @pytest.mark.asyncio
    async def test_returns_data(self, async_client):
        """Returns unique viewers per channel."""
        with patch("routers.stats.BandwidthTracker.get_unique_viewers_by_channel", return_value=[]):
            response = await async_client.get("/api/stats/unique-viewers-by-channel")

        assert response.status_code == 200


class TestWatchHistory:
    """Tests for GET /api/stats/watch-history."""

    @pytest.mark.asyncio
    async def test_returns_empty_history(self, async_client):
        """Returns empty history with pagination."""
        response = await async_client.get("/api/stats/watch-history")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["history"] == []
        assert "summary" in data


class TestPopularityRankings:
    """Tests for GET /api/stats/popularity/rankings."""

    @pytest.mark.asyncio
    async def test_returns_rankings(self, async_client):
        """Returns popularity rankings."""
        with patch("popularity_calculator.PopularityCalculator.get_rankings", return_value={
            "rankings": [], "total": 0,
        }):
            response = await async_client.get("/api/stats/popularity/rankings")

        assert response.status_code == 200


class TestChannelPopularity:
    """Tests for GET /api/stats/popularity/channel/{channel_id}."""

    @pytest.mark.asyncio
    async def test_returns_score(self, async_client):
        """Returns popularity score for a channel."""
        with patch("popularity_calculator.PopularityCalculator.get_channel_score", return_value={
            "channel_id": "abc", "score": 85.5,
        }):
            response = await async_client.get("/api/stats/popularity/channel/abc")

        assert response.status_code == 200
        assert response.json()["score"] == 85.5

    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(self, async_client):
        """Returns 404 when no score exists."""
        with patch("popularity_calculator.PopularityCalculator.get_channel_score", return_value=None):
            response = await async_client.get("/api/stats/popularity/channel/unknown")

        assert response.status_code == 404


class TestTrendingChannels:
    """Tests for GET /api/stats/popularity/trending."""

    @pytest.mark.asyncio
    async def test_returns_trending(self, async_client):
        """Returns trending channels."""
        with patch("popularity_calculator.PopularityCalculator.get_trending_channels", return_value=[]):
            response = await async_client.get("/api/stats/popularity/trending")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_rejects_invalid_direction(self, async_client):
        """Returns 400 for invalid direction."""
        response = await async_client.get("/api/stats/popularity/trending", params={
            "direction": "sideways",
        })
        assert response.status_code == 400


class TestCalculatePopularity:
    """Tests for POST /api/stats/popularity/calculate."""

    @pytest.mark.asyncio
    async def test_triggers_calculation(self, async_client):
        """Triggers popularity calculation."""
        with patch("popularity_calculator.calculate_popularity", return_value={
            "calculated": 50, "period_days": 7,
        }):
            response = await async_client.post("/api/stats/popularity/calculate")

        assert response.status_code == 200
        assert response.json()["calculated"] == 50
