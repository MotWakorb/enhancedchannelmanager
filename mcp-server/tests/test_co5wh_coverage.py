"""Tests for co5wh epic — MCP coverage parity for Stats v2 (0.17.0/0.17.1).

Five beads implemented here:
  co5wh.1 — get_channel_stats attribution rendering (emby/plex/jellyfin/client_ip/provider)
  co5wh.2 — get_provider_stats (buffering/watch_time/channel_heatmap/bitrate)
  co5wh.3 — get_user_watch_time + get_user_channel_breakdown
  co5wh.4 — get_trending + get_channel_popularity
  co5wh.5 — get_activity + get_channel_bandwidth

All responses are mocked with the REAL shapes read from backend/routers/stats.py
and backend/bandwidth_tracker.py.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_mock(return_value):
    """Make a mock ECMClient whose call_endpoint always returns return_value."""
    mock = AsyncMock()
    mock.call_endpoint.return_value = return_value
    return mock


def _register_stats(mcp):
    from tools.stats import register
    register(mcp)


# =============================================================================
# co5wh.1 — get_channel_stats attribution rendering
# =============================================================================


class TestGetChannelStatsAttribution:
    """Tests for v0.17.1 attribution fields in get_channel_stats."""

    @pytest.mark.asyncio
    async def test_emby_user_name_rendered(self):
        """emby_user_name on a channel appears in output."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        # Real response shape from backend/routers/stats.py GET /api/stats/channels
        # → client.get_channel_stats() → Dispatcharr /proxy/ts/status enriched by
        # _enrich_channels_with_attribution.
        stats_response = {
            "channels": [
                {
                    "channel_id": "abc123",
                    "channel_name": "ESPN HD",
                    "active_connections": 1,
                    "emby_user_name": "alice",
                    "plex_user_name": None,
                    "jellyfin_user_name": None,
                    "emby_viewers": [{"user_id": "u1", "user_name": "alice", "client_ip": "10.0.0.5"}],
                    "plex_viewers": [],
                    "jellyfin_viewers": [],
                    "provider_name": "StreamCo",
                    "clients": [
                        {
                            "ip_address": "10.0.0.1",
                            "emby_user_name": "alice",
                            "plex_user_name": None,
                            "jellyfin_user_name": None,
                            "client_ip": "10.0.0.5",
                            "client_ips": [],
                        }
                    ],
                }
            ]
        }

        mock_client = _make_mock(stats_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_channel_stats", {})

        text = result[0][0].text
        assert "ESPN HD" in text
        assert "Emby: alice" in text
        assert "provider: StreamCo" in text
        assert "client_ip: 10.0.0.5" in text

    @pytest.mark.asyncio
    async def test_plex_and_jellyfin_user_names_rendered(self):
        """plex_user_name and jellyfin_user_name appear when present."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        stats_response = {
            "channels": [
                {
                    "channel_id": "def456",
                    "channel_name": "CNN",
                    "active_connections": 2,
                    "emby_user_name": None,
                    "plex_user_name": "bob_plex",
                    "jellyfin_user_name": "carol_jf",
                    "emby_viewers": [],
                    "plex_viewers": [{"user_id": "p1", "user_name": "bob_plex", "client_ip": None}],
                    "jellyfin_viewers": [{"user_id": "j1", "user_name": "carol_jf", "client_ip": None}],
                    "provider_name": None,
                    "clients": [],
                }
            ]
        }

        mock_client = _make_mock(stats_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_channel_stats", {})

        text = result[0][0].text
        assert "Plex: bob_plex" in text
        assert "Jellyfin: carol_jf" in text
        # multi-viewer lists
        assert "Plex viewers: bob_plex" in text
        assert "Jellyfin viewers: carol_jf" in text

    @pytest.mark.asyncio
    async def test_no_attribution_fields_still_works(self):
        """Legacy response (no attribution keys) still renders correctly."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        stats_response = {
            "channels": [
                {
                    "channel_id": "ghi789",
                    "channel_name": "ESPN 2",
                    "active_connections": 1,
                    "clients": [],
                }
            ]
        }

        mock_client = _make_mock(stats_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_channel_stats", {})

        text = result[0][0].text
        assert "ESPN 2" in text
        assert "1 viewer(s)" in text
        # No attribution noise in output
        assert "Emby:" not in text
        assert "Plex:" not in text

    @pytest.mark.asyncio
    async def test_client_ips_rollup_rendered(self):
        """client_ips list rendered when client_ip absent (Option-B rollup path)."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        stats_response = {
            "channels": [
                {
                    "channel_id": "xyz",
                    "channel_name": "Fox",
                    "active_connections": 1,
                    "emby_user_name": "dave",
                    "plex_user_name": None,
                    "jellyfin_user_name": None,
                    "emby_viewers": [],
                    "plex_viewers": [],
                    "jellyfin_viewers": [],
                    "provider_name": None,
                    "clients": [
                        {
                            "ip_address": "10.0.0.10",
                            "emby_user_name": "dave",
                            "plex_user_name": None,
                            "jellyfin_user_name": None,
                            "client_ip": None,
                            "client_ips": ["192.168.1.1", "192.168.1.2"],
                        }
                    ],
                }
            ]
        }

        mock_client = _make_mock(stats_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_channel_stats", {})

        text = result[0][0].text
        assert "client_ips: 192.168.1.1, 192.168.1.2" in text


# =============================================================================
# co5wh.2 — get_provider_stats
# =============================================================================


class TestGetProviderStats:
    """Tests for the get_provider_stats MCP tool (all four metrics)."""

    @pytest.mark.asyncio
    async def test_buffering_metric_rendered(self):
        """get_provider_stats metric=buffering renders event counts."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        # Real shape: _build_provider_envelope({data:[...], meta:{...}, pagination:null})
        # Each row: {provider_id, time_bucket, buffer_event_count, reconnect_event_count,
        #            error_event_count, switch_event_count, total_event_count}
        buffering_response = {
            "data": [
                {
                    "provider_id": 1,
                    "time_bucket": "2026-05-22T10:00:00Z",
                    "buffer_event_count": 2,
                    "reconnect_event_count": 5,
                    "error_event_count": 1,
                    "switch_event_count": 3,
                    "total_event_count": 11,
                }
            ],
            "meta": {"from_iso": "2026-05-15T00:00:00Z", "to_iso": "2026-05-22T00:00:00Z",
                     "total_rows": 1, "window": "7d", "bucket": "hour"},
            "pagination": None,
        }

        mock_client = _make_mock(buffering_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_provider_stats", {"metric": "buffering"})

        text = result[0][0].text
        assert "Buffering Events" in text
        assert "provider=1" in text
        assert "total=11" in text
        assert "reconnect=5" in text

    @pytest.mark.asyncio
    async def test_watch_time_metric_rendered(self):
        """get_provider_stats metric=watch_time renders total hours."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        # Real shape: {data: [{provider_id, total_watch_seconds}], meta: {...}, pagination: null}
        watch_time_response = {
            "data": [
                {"provider_id": 1, "total_watch_seconds": 7200},
                {"provider_id": None, "total_watch_seconds": 3600},
            ],
            "meta": {"from_iso": "2026-05-15T00:00:00Z", "to_iso": "2026-05-22T00:00:00Z",
                     "total_rows": 2, "window": "7d"},
            "pagination": None,
        }

        mock_client = _make_mock(watch_time_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_provider_stats", {"metric": "watch_time"})

        text = result[0][0].text
        assert "Watch-Time" in text
        assert "2.0h" in text
        assert "Unknown" in text  # provider_id=None rendered as Unknown

    @pytest.mark.asyncio
    async def test_channel_heatmap_metric_rendered(self):
        """get_provider_stats metric=channel_heatmap renders bytes cells."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        # Real shape: {data: [{provider_id, channel_id, channel_name, bytes,
        #              latest_stream_id, latest_stream_name}], meta: {...}, pagination: null}
        heatmap_response = {
            "data": [
                {
                    "provider_id": 2,
                    "channel_id": "ch-abc",
                    "channel_name": "ESPN",
                    "bytes": 1073741824,  # 1 GB
                    "latest_stream_id": 42,
                    "latest_stream_name": "ESPN_HD_Stream",
                }
            ],
            "meta": {"from_iso": "2026-05-15T00:00:00Z", "to_iso": "2026-05-22T00:00:00Z",
                     "total_rows": 1, "window": "7d", "top_n": 50},
            "pagination": None,
        }

        mock_client = _make_mock(heatmap_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_provider_stats", {"metric": "channel_heatmap"})

        text = result[0][0].text
        assert "Heatmap" in text
        assert "ESPN" in text
        assert "1.0 GB" in text
        assert "ESPN_HD_Stream" in text

    @pytest.mark.asyncio
    async def test_bitrate_metric_rendered(self):
        """get_provider_stats metric=bitrate renders Mbps."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        # Real shape: {data: [{provider_id, time_bucket, bitrate_bps}], meta: {...}, pagination: null}
        bitrate_response = {
            "data": [
                {
                    "provider_id": 3,
                    "time_bucket": "2026-05-22T09:00:00Z",
                    "bitrate_bps": 5_000_000,
                }
            ],
            "meta": {"from_iso": "2026-05-15T00:00:00Z", "to_iso": "2026-05-22T00:00:00Z",
                     "total_rows": 1, "window": "7d", "bucket": "hour"},
            "pagination": None,
        }

        mock_client = _make_mock(bitrate_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_provider_stats", {"metric": "bitrate"})

        text = result[0][0].text
        assert "Bitrate" in text
        assert "5.00 Mbps" in text
        assert "provider=3" in text

    @pytest.mark.asyncio
    async def test_invalid_metric_returns_error(self):
        """Invalid metric parameter returns a helpful error."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        mock_client = _make_mock({})
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_provider_stats", {"metric": "bogus"})

        text = result[0][0].text
        assert "Invalid metric" in text
        assert "bogus" in text

    @pytest.mark.asyncio
    async def test_empty_data_returns_no_data_message(self):
        """Empty data list returns informative message."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        mock_client = _make_mock({"data": [], "meta": {"total_rows": 0}, "pagination": None})
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_provider_stats", {"metric": "watch_time"})

        text = result[0][0].text
        assert "No watch-time data" in text


# =============================================================================
# co5wh.3 — get_user_watch_time + get_user_channel_breakdown
# =============================================================================


class TestGetUserWatchTime:
    """Tests for the get_user_watch_time MCP tool."""

    @pytest.mark.asyncio
    async def test_total_group_by_renders_users(self):
        """get_user_watch_time group_by=total renders per-user rows."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        # Real shape from /api/stats/watch-time?group_by=total:
        # {data: [{user_id, username, attribution_source, total_watch_seconds, last_watched}],
        #  meta: {from_iso, to_iso, group_by, total_rows}, pagination: null}
        watch_time_response = {
            "data": [
                {
                    "user_id": 5,
                    "username": "alice",
                    "attribution_source": "emby",
                    "total_watch_seconds": 10800,
                    "last_watched": "2026-05-22T08:00:00Z",
                },
                {
                    "user_id": 7,
                    "username": "bob",
                    "attribution_source": "dispatcharr",
                    "total_watch_seconds": 3600,
                    "last_watched": "2026-05-21T22:00:00Z",
                },
            ],
            "meta": {
                "from_iso": None,
                "to_iso": None,
                "group_by": "total",
                "total_rows": 2,
            },
            "pagination": None,
        }

        mock_client = _make_mock(watch_time_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_user_watch_time", {"group_by": "total"})

        text = result[0][0].text
        assert "alice" in text
        assert "via emby" in text
        assert "3.0h" in text
        assert "bob" in text

    @pytest.mark.asyncio
    async def test_day_group_by_renders_per_day(self):
        """get_user_watch_time group_by=day renders day+minutes rows."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        # Real shape for group_by=day:
        # {user_id, username, attribution_source, day, watch_seconds}
        watch_time_response = {
            "data": [
                {
                    "user_id": 5,
                    "username": "alice",
                    "attribution_source": "emby",
                    "day": "2026-05-22",
                    "watch_seconds": 1800,
                }
            ],
            "meta": {
                "from_iso": None,
                "to_iso": None,
                "group_by": "day",
                "total_rows": 1,
            },
            "pagination": None,
        }

        mock_client = _make_mock(watch_time_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_user_watch_time", {"group_by": "day"})

        text = result[0][0].text
        assert "2026-05-22" in text
        assert "30min" in text
        assert "alice" in text

    @pytest.mark.asyncio
    async def test_invalid_group_by_returns_error(self):
        """Invalid group_by returns helpful error."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        mock_client = _make_mock({})
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_user_watch_time", {"group_by": "month"})

        assert "Invalid group_by" in result[0][0].text

    @pytest.mark.asyncio
    async def test_empty_data_returns_message(self):
        """Empty response returns informative message."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        mock_client = _make_mock({"data": [], "meta": {"total_rows": 0}, "pagination": None})
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_user_watch_time", {})

        assert "No watch-time data" in result[0][0].text


class TestGetUserChannelBreakdown:
    """Tests for the get_user_channel_breakdown MCP tool."""

    @pytest.mark.asyncio
    async def test_dispatcharr_source_renders_channels(self):
        """get_user_channel_breakdown source=dispatcharr renders per-channel rows."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        # Real shape from /api/stats/users/dispatcharr/{user_id} (bd-fm23o):
        # {data: [{channel_id, channel_name, total_watch_seconds, session_count,
        #          last_watched, latest_stream_id, latest_stream_name}],
        #  meta: {from_iso, to_iso, group_by:"channel", total_rows}, pagination: null}
        breakdown_response = {
            "data": [
                {
                    "channel_id": "ch-abc",
                    "channel_name": "ESPN",
                    "total_watch_seconds": 7200,
                    "session_count": 3,
                    "last_watched": "2026-05-22T10:00:00Z",
                    "latest_stream_id": 42,
                    "latest_stream_name": "ESPN_HD_Feed",
                }
            ],
            "meta": {
                "from_iso": None,
                "to_iso": None,
                "group_by": "channel",
                "total_rows": 1,
            },
            "pagination": None,
        }

        mock_client = _make_mock(breakdown_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "get_user_channel_breakdown",
                {"user_id": "5", "source": "dispatcharr"},
            )

        text = result[0][0].text
        assert "ESPN" in text
        assert "2.0h" in text
        assert "3 sessions" in text
        assert "ESPN_HD_Feed" in text

    @pytest.mark.asyncio
    async def test_emby_source_uses_emby_endpoint(self):
        """get_user_channel_breakdown source=emby calls the emby endpoint."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        # Same shape as dispatcharr — same _per_user_breakdown_by_channel helper.
        breakdown_response = {
            "data": [
                {
                    "channel_id": "ch-xyz",
                    "channel_name": "CNN",
                    "total_watch_seconds": 3600,
                    "session_count": 1,
                    "last_watched": "2026-05-20T15:00:00Z",
                    "latest_stream_id": None,
                    "latest_stream_name": None,
                }
            ],
            "meta": {"from_iso": None, "to_iso": None, "group_by": "channel", "total_rows": 1},
            "pagination": None,
        }

        mock_client = _make_mock(breakdown_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "get_user_channel_breakdown",
                {"user_id": "emby-guid-abc", "source": "emby"},
            )

        text = result[0][0].text
        assert "CNN" in text
        assert "emby" in text
        assert "1.0h" in text

    @pytest.mark.asyncio
    async def test_invalid_source_returns_error(self):
        """Invalid source returns helpful error."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        mock_client = _make_mock({})
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "get_user_channel_breakdown",
                {"user_id": "5", "source": "plex"},
            )

        assert "Invalid source" in result[0][0].text


# =============================================================================
# co5wh.4 — get_trending + get_channel_popularity
# =============================================================================


class TestGetTrending:
    """Tests for the get_trending MCP tool."""

    @pytest.mark.asyncio
    async def test_up_direction_renders_trending_channels(self):
        """get_trending direction=up renders channels with trend data."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        # Real shape: list of ChannelPopularityScore.to_dict() (models.py line 1217)
        trending_response = [
            {
                "id": 1,
                "channel_id": "ch-abc",
                "channel_name": "ESPN",
                "score": 82.5,
                "rank": 3,
                "watch_count_7d": 150,
                "watch_time_7d": 54000,
                "unique_viewers_7d": 45,
                "bandwidth_7d": 5_000_000_000,
                "trend": "up",
                "trend_percent": 12.3,
                "previous_score": 73.5,
                "previous_rank": 5,
                "calculated_at": "2026-05-22T06:00:00Z",
                "created_at": "2026-05-01T00:00:00Z",
                "updated_at": "2026-05-22T06:00:00Z",
            }
        ]

        mock_client = _make_mock(trending_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_trending", {"direction": "up"})

        text = result[0][0].text
        assert "Trending UP" in text
        assert "ESPN" in text
        assert "score=82.5" in text
        assert "+12.3%" in text
        assert "rank #3" in text

    @pytest.mark.asyncio
    async def test_down_direction_renders_down_arrow(self):
        """get_trending direction=down renders ↓ arrow."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        trending_response = [
            {
                "id": 2,
                "channel_id": "ch-def",
                "channel_name": "Fox News",
                "score": 30.1,
                "rank": 20,
                "trend": "down",
                "trend_percent": -8.7,
                "watch_count_7d": 40,
                "watch_time_7d": 14400,
                "unique_viewers_7d": 12,
                "bandwidth_7d": 1_000_000_000,
                "previous_score": 32.9,
                "previous_rank": 18,
                "calculated_at": "2026-05-22T06:00:00Z",
                "created_at": "2026-05-01T00:00:00Z",
                "updated_at": "2026-05-22T06:00:00Z",
            }
        ]

        mock_client = _make_mock(trending_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_trending", {"direction": "down"})

        text = result[0][0].text
        assert "Trending DOWN" in text
        assert "↓" in text
        assert "Fox News" in text
        assert "-8.7%" in text

    @pytest.mark.asyncio
    async def test_invalid_direction_returns_error(self):
        """Invalid direction returns helpful error."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        mock_client = _make_mock([])
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_trending", {"direction": "sideways"})

        assert "Invalid direction" in result[0][0].text

    @pytest.mark.asyncio
    async def test_empty_result_returns_no_data_message(self):
        """Empty trending list returns informative message."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        mock_client = _make_mock([])
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_trending", {})

        text = result[0][0].text
        assert "No channels trending" in text or "popularity calculation" in text.lower()


class TestGetChannelPopularity:
    """Tests for the get_channel_popularity MCP tool."""

    @pytest.mark.asyncio
    async def test_renders_score_and_metrics(self):
        """get_channel_popularity renders score, rank, trend, and 7-day metrics."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        # Real shape: ChannelPopularityScore.to_dict() (models.py line 1217)
        score_response = {
            "id": 1,
            "channel_id": "ch-abc",
            "channel_name": "ESPN",
            "score": 82.5,
            "rank": 3,
            "watch_count_7d": 150,
            "watch_time_7d": 54000,
            "unique_viewers_7d": 45,
            "bandwidth_7d": 5_368_709_120,  # ~5 GB
            "trend": "up",
            "trend_percent": 12.3,
            "previous_score": 73.5,
            "previous_rank": 5,
            "calculated_at": "2026-05-22T06:00:00Z",
            "created_at": "2026-05-01T00:00:00Z",
            "updated_at": "2026-05-22T06:00:00Z",
        }

        mock_client = _make_mock(score_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_channel_popularity", {"channel_id": "ch-abc"})

        text = result[0][0].text
        assert "ESPN" in text
        assert "82.5" in text
        assert "#3" in text
        assert "up" in text
        assert "+12.3%" in text
        assert "150 sessions" in text
        assert "45 unique viewers" in text
        assert "2026-05-22" in text

    @pytest.mark.asyncio
    async def test_none_response_returns_no_data(self):
        """None/empty response when channel not found returns message."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        mock_client = _make_mock(None)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_channel_popularity", {"channel_id": "not-found"})

        text = result[0][0].text
        assert "No popularity data" in text


# =============================================================================
# co5wh.5 — get_activity + get_channel_bandwidth
# =============================================================================


class TestGetActivity:
    """Tests for the get_activity MCP tool."""

    @pytest.mark.asyncio
    async def test_renders_event_list(self):
        """get_activity renders system events from Dispatcharr."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        # Real shape: Dispatcharr paginated response
        # {count: N, next: url|null, previous: url|null, results: [...events]}
        # Each event is a Dispatcharr SystemEvent dict.
        activity_response = {
            "count": 2,
            "next": None,
            "previous": None,
            "results": [
                {
                    "event_type": "channel_start",
                    "timestamp": "2026-05-22T10:00:00Z",
                    "channel_name": "ESPN",
                    "detail": "Stream started",
                },
                {
                    "event_type": "client_connected",
                    "timestamp": "2026-05-22T10:00:05Z",
                    "channel_name": "ESPN",
                    "detail": "IP 10.0.0.1",
                },
            ],
        }

        mock_client = _make_mock(activity_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_activity", {"limit": 50})

        text = result[0][0].text
        assert "Activity Events" in text
        assert "channel_start" in text
        assert "ESPN" in text
        assert "client_connected" in text
        assert "10:00:05" in text

    @pytest.mark.asyncio
    async def test_event_type_filter_noted_in_output(self):
        """When event_type is passed, the filter is noted in output."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        activity_response = {
            "count": 1,
            "next": None,
            "previous": None,
            "results": [
                {
                    "event_type": "channel_buffering",
                    "timestamp": "2026-05-22T10:00:00Z",
                    "channel_name": "CNN",
                    "detail": None,
                }
            ],
        }

        mock_client = _make_mock(activity_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "get_activity", {"event_type": "channel_buffering"}
            )

        text = result[0][0].text
        assert "filtered: channel_buffering" in text
        assert "channel_buffering" in text

    @pytest.mark.asyncio
    async def test_empty_results_returns_message(self):
        """Empty results returns informative message."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        mock_client = _make_mock({"count": 0, "next": None, "previous": None, "results": []})
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_activity", {})

        assert "No activity events" in result[0][0].text


class TestGetChannelBandwidth:
    """Tests for the get_channel_bandwidth MCP tool."""

    @pytest.mark.asyncio
    async def test_renders_channel_bandwidth_list(self):
        """get_channel_bandwidth renders per-channel stats."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        # Real shape: BandwidthTracker.get_channel_bandwidth_stats returns list[dict]
        # Each dict: {channel_id, channel_name, total_bytes, total_connections,
        #             total_watch_seconds, peak_clients}
        bandwidth_response = [
            {
                "channel_id": "ch-abc",
                "channel_name": "ESPN",
                "total_bytes": 1_073_741_824,  # 1 GB
                "total_connections": 42,
                "total_watch_seconds": 36000,
                "peak_clients": 5,
            },
            {
                "channel_id": "ch-def",
                "channel_name": "CNN",
                "total_bytes": 536_870_912,  # 512 MB
                "total_connections": 18,
                "total_watch_seconds": 14400,
                "peak_clients": 2,
            },
        ]

        mock_client = _make_mock(bandwidth_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_channel_bandwidth", {"days": 7})

        text = result[0][0].text
        assert "Channel Bandwidth" in text
        assert "ESPN" in text
        assert "1.0 GB" in text
        assert "42 connections" in text
        assert "peak 5 clients" in text
        assert "CNN" in text
        assert "512.0 MB" in text

    @pytest.mark.asyncio
    async def test_sort_by_connections_param_accepted(self):
        """sort_by=connections parameter is accepted and forwarded."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        bandwidth_response = [
            {
                "channel_id": "ch-abc",
                "channel_name": "ESPN",
                "total_bytes": 500_000_000,
                "total_connections": 100,
                "total_watch_seconds": 7200,
                "peak_clients": 8,
            }
        ]

        mock_client = _make_mock(bandwidth_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "get_channel_bandwidth", {"sort_by": "connections", "days": 30}
            )

        text = result[0][0].text
        assert "ESPN" in text
        assert "100 connections" in text

    @pytest.mark.asyncio
    async def test_invalid_sort_by_returns_error(self):
        """Invalid sort_by returns helpful error."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        mock_client = _make_mock([])
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "get_channel_bandwidth", {"sort_by": "popularity"}
            )

        assert "Invalid sort_by" in result[0][0].text

    @pytest.mark.asyncio
    async def test_empty_list_returns_message(self):
        """Empty bandwidth list returns informative message."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        mock_client = _make_mock([])
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_channel_bandwidth", {})

        assert "No channel bandwidth data" in result[0][0].text
