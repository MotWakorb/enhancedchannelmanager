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


class TestChannelStatsStreamEnrichment:
    """Tests for stream identity enrichment on GET /api/stats/channels (bd-ox5q8).

    The endpoint feeds the Stats v2 Active Channels live view. Each
    active channel must surface ``stream_name`` and ``m3u_account_id``
    so the UI can render the ``[<provider>] - <stream_name>`` badge.
    Source of truth is the live resolver (same logic as
    ``BandwidthTracker._resolve_provider_ids``) — not the persisted
    ``session_telemetry`` row, because operators expect the live view
    to be immediately accurate (no poll-cycle lag).
    """

    @pytest.mark.asyncio
    async def test_enriches_direct_stream_id_channel(self, async_client):
        """Channel surfaces ``stream_id`` directly → endpoint populates
        ``stream_name`` and ``m3u_account_id`` from the streams batch."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-1",
                    "channel_name": "300 | TNT",
                    "stream_id": 555,
                    "clients": [],
                },
            ],
        }
        mock_client.get_streams_by_ids.return_value = [
            {"id": 555, "name": "US: TNT", "m3u_account": 6},
        ]

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        body = response.json()
        ch = body["channels"][0]
        assert ch["stream_name"] == "US: TNT"
        assert ch["m3u_account_id"] == 6

    @pytest.mark.asyncio
    async def test_enriches_url_derived_stream_id_channel(self, async_client):
        """Channel has no ``stream_id`` but the URL's trailing path
        segment is a Dispatcharr stream id — resolver's URL fallback
        (bd-kbgey) kicks in and enrichment completes."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-2",
                    "channel_name": "Channel 2",
                    "url": "https://example.gives/live/x/y/777.ts",
                    "clients": [],
                },
            ],
        }
        mock_client.get_streams_by_ids.return_value = [
            {"id": 777, "name": "Discovery", "m3u_account": 6},
        ]

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        ch = response.json()["channels"][0]
        assert ch["stream_name"] == "Discovery"
        assert ch["m3u_account_id"] == 6

    @pytest.mark.asyncio
    async def test_enriches_channel_streams_fallback_channel(self, async_client):
        """URL-derived id misses the batched lookup → resolver falls back
        to ``/channels/<uuid>/streams`` URL matching (bd-5g7kx). The
        endpoint surfaces the matched stream's identity."""
        mock_client = AsyncMock()
        active_url = "https://infinity.gives/live/mot/16118141/85796.ts"
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-3",
                    "channel_name": "300 | TNT",
                    "url": active_url,
                    "clients": [],
                },
            ],
        }
        # URL-derived id is the upstream provider id, not in the batch
        # response. The channel-streams fallback finds the matching URL.
        mock_client.get_streams_by_ids.return_value = []
        mock_client.get_channel_streams.return_value = [
            {
                "id": 97205,
                "name": "US: TNT",
                "m3u_account": 6,
                "url": active_url,
            },
        ]

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        ch = response.json()["channels"][0]
        assert ch["stream_name"] == "US: TNT"
        assert ch["m3u_account_id"] == 6

    @pytest.mark.asyncio
    async def test_enriches_multiple_channels_in_one_response(self, async_client):
        """All three resolver paths in one endpoint response (the
        operator's reality: heterogeneous active channels). Each row's
        identity is populated correctly without cross-contamination."""
        mock_client = AsyncMock()
        active_url_c3 = "https://infinity.gives/live/mot/16118141/85796.ts"
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-1",
                    "stream_id": 555,
                    "clients": [],
                },
                {
                    "channel_id": "uuid-2",
                    "url": "https://example.gives/live/x/y/777.ts",
                    "clients": [],
                },
                {
                    "channel_id": "uuid-3",
                    "url": active_url_c3,
                    "clients": [],
                },
            ],
        }
        mock_client.get_streams_by_ids.return_value = [
            {"id": 555, "name": "ESPN", "m3u_account": 1},
            {"id": 777, "name": "Discovery", "m3u_account": 2},
        ]
        mock_client.get_channel_streams.return_value = [
            {"id": 97205, "name": "US: TNT", "m3u_account": 6, "url": active_url_c3},
        ]

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        by_uuid = {c["channel_id"]: c for c in response.json()["channels"]}
        assert by_uuid["uuid-1"]["stream_name"] == "ESPN"
        assert by_uuid["uuid-1"]["m3u_account_id"] == 1
        assert by_uuid["uuid-2"]["stream_name"] == "Discovery"
        assert by_uuid["uuid-2"]["m3u_account_id"] == 2
        assert by_uuid["uuid-3"]["stream_name"] == "US: TNT"
        assert by_uuid["uuid-3"]["m3u_account_id"] == 6

    @pytest.mark.asyncio
    async def test_unresolvable_channel_writes_nulls(self, async_client):
        """A channel with no stream_id and no URL is unresolvable. The
        endpoint still returns the row, with ``stream_name`` and
        ``m3u_account_id`` set to ``None``. The frontend renders the
        bare channel name with no badge."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {"channel_id": "uuid-1", "channel_name": "Unknown", "clients": []},
            ],
        }
        mock_client.get_streams_by_ids.return_value = []

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        ch = response.json()["channels"][0]
        assert ch["stream_name"] is None
        assert ch["m3u_account_id"] is None

    @pytest.mark.asyncio
    async def test_resolver_failure_does_not_break_endpoint(self, async_client):
        """If the resolver raises (Dispatcharr lookup error), the
        endpoint still returns successfully — enrichment is best-effort.
        Active Channels rendering must not depend on resolver success."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {"channel_id": "uuid-1", "stream_id": 555, "clients": []},
            ],
        }
        mock_client.get_streams_by_ids.side_effect = Exception("Dispatcharr timeout")

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        ch = response.json()["channels"][0]
        # Resolver raised → identity columns NULL but row still present.
        assert ch.get("stream_name") is None
        assert ch.get("m3u_account_id") is None

    @pytest.mark.asyncio
    async def test_skips_resolver_round_trip_when_no_channels(self, async_client):
        """No active channels → no Dispatcharr round-trip for stream
        resolution. Avoids one wasted HTTP call per poll-equivalent
        endpoint hit when nothing is streaming."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {"channels": []}

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        mock_client.get_streams_by_ids.assert_not_called()


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
