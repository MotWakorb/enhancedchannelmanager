"""
Integration tests for the Enhanced Stats API endpoints (v0.11.0).

Tests the stats-related API endpoints including:
- Popularity rankings
- Popularity trending
- Popularity calculation

Note: Bandwidth/unique-viewer endpoints require BandwidthTracker which is
initialized at module load time. Those are better tested via E2E tests.
"""
import pytest
from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock

from models import (
    ChannelWatchStats,
    UniqueClientConnection,
    ChannelBandwidth,
    ChannelPopularityScore,
)


class TestPopularityRankings:
    """Tests for GET /api/stats/popularity/rankings endpoint."""

    @pytest.mark.asyncio
    async def test_get_popularity_rankings_empty(self, async_client, test_session):
        """GET /api/stats/popularity/rankings returns empty list when no data."""
        with patch("popularity_calculator.get_session", return_value=test_session):
            response = await async_client.get("/api/stats/popularity/rankings")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["rankings"] == []

    @pytest.mark.asyncio
    async def test_get_popularity_rankings_with_data(self, async_client, test_session):
        """GET /api/stats/popularity/rankings returns rankings when data exists."""
        now = datetime.utcnow()

        # Create popularity scores
        for i in range(5):
            score = ChannelPopularityScore(
                channel_id=f"channel-{i}",
                channel_name=f"Channel {i}",
                score=100 - i * 10,
                rank=i + 1,
                watch_count_7d=100 - i * 10,
                watch_time_7d=3600,
                unique_viewers_7d=10,
                bandwidth_7d=1000000,
                trend="stable",
                trend_percent=0.0,
                calculated_at=now,
            )
            test_session.add(score)
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            response = await async_client.get("/api/stats/popularity/rankings")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["rankings"]) == 5
        assert data["rankings"][0]["rank"] == 1

    @pytest.mark.asyncio
    async def test_get_popularity_rankings_pagination(self, async_client, test_session):
        """GET /api/stats/popularity/rankings supports pagination."""
        now = datetime.utcnow()

        for i in range(10):
            score = ChannelPopularityScore(
                channel_id=f"channel-{i}",
                channel_name=f"Channel {i}",
                score=100 - i * 5,
                rank=i + 1,
                trend="stable",
                trend_percent=0.0,
                calculated_at=now,
            )
            test_session.add(score)
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            response = await async_client.get(
                "/api/stats/popularity/rankings?limit=3&offset=2"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 10
        assert len(data["rankings"]) == 3
        assert data["rankings"][0]["rank"] == 3


class TestChannelPopularity:
    """Tests for GET /api/stats/popularity/channel/{channel_id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_channel_popularity_exists(self, async_client, test_session):
        """GET /api/stats/popularity/channel/{id} returns score when exists."""
        now = datetime.utcnow()

        score = ChannelPopularityScore(
            channel_id="test-channel-uuid",
            channel_name="Test Channel",
            score=75.5,
            rank=1,
            watch_count_7d=100,
            watch_time_7d=3600,
            unique_viewers_7d=10,
            bandwidth_7d=1000000,
            trend="up",
            trend_percent=15.5,
            calculated_at=now,
        )
        test_session.add(score)
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            response = await async_client.get(
                "/api/stats/popularity/channel/test-channel-uuid"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["channel_id"] == "test-channel-uuid"
        assert data["score"] == 75.5
        assert data["trend"] == "up"

    @pytest.mark.asyncio
    async def test_get_channel_popularity_not_found(self, async_client, test_session):
        """GET /api/stats/popularity/channel/{id} returns 404 when not found."""
        with patch("popularity_calculator.get_session", return_value=test_session):
            response = await async_client.get(
                "/api/stats/popularity/channel/nonexistent-channel"
            )

        assert response.status_code == 404


class TestTrendingChannels:
    """Tests for GET /api/stats/popularity/trending endpoint."""

    @pytest.mark.asyncio
    async def test_get_trending_up(self, async_client, test_session):
        """GET /api/stats/popularity/trending?direction=up returns trending up."""
        now = datetime.utcnow()

        # Create channels with different trends
        for i, (trend, percent) in enumerate([
            ("up", 50.0),
            ("up", 25.0),
            ("down", -30.0),
            ("stable", 2.0),
        ]):
            score = ChannelPopularityScore(
                channel_id=f"channel-{i}",
                channel_name=f"Channel {i}",
                score=50.0,
                rank=i + 1,
                trend=trend,
                trend_percent=percent,
                calculated_at=now,
            )
            test_session.add(score)
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            response = await async_client.get(
                "/api/stats/popularity/trending?direction=up"
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert all(item["trend"] == "up" for item in data)
        # Should be sorted by trend_percent descending
        assert data[0]["trend_percent"] >= data[1]["trend_percent"]

    @pytest.mark.asyncio
    async def test_get_trending_down(self, async_client, test_session):
        """GET /api/stats/popularity/trending?direction=down returns trending down."""
        now = datetime.utcnow()

        for i, (trend, percent) in enumerate([
            ("up", 50.0),
            ("down", -10.0),
            ("down", -30.0),
            ("stable", -2.0),
        ]):
            score = ChannelPopularityScore(
                channel_id=f"channel-{i}",
                channel_name=f"Channel {i}",
                score=50.0,
                rank=i + 1,
                trend=trend,
                trend_percent=percent,
                calculated_at=now,
            )
            test_session.add(score)
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            response = await async_client.get(
                "/api/stats/popularity/trending?direction=down"
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert all(item["trend"] == "down" for item in data)

    @pytest.mark.asyncio
    async def test_get_trending_with_limit(self, async_client, test_session):
        """GET /api/stats/popularity/trending respects limit parameter."""
        now = datetime.utcnow()

        for i in range(10):
            score = ChannelPopularityScore(
                channel_id=f"channel-{i}",
                channel_name=f"Channel {i}",
                score=50.0,
                rank=i + 1,
                trend="up",
                trend_percent=50.0 - i * 5,
                calculated_at=now,
            )
            test_session.add(score)
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            response = await async_client.get(
                "/api/stats/popularity/trending?direction=up&limit=3"
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3


class TestCalculatePopularity:
    """Tests for POST /api/stats/popularity/calculate endpoint."""

    @pytest.mark.asyncio
    async def test_calculate_popularity_empty_db(self, async_client, test_session):
        """POST /api/stats/popularity/calculate works with empty database."""
        today = date.today()

        with patch("popularity_calculator.get_session", return_value=test_session):
            with patch("popularity_calculator.get_current_date", return_value=today):
                response = await async_client.post(
                    "/api/stats/popularity/calculate",
                    json={"period_days": 7},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["channels_scored"] == 0

    @pytest.mark.asyncio
    async def test_calculate_popularity_with_data(self, async_client, test_session):
        """POST /api/stats/popularity/calculate calculates scores."""
        today = date.today()
        now = datetime.utcnow()

        # Create some watch stats
        stats = ChannelWatchStats(
            channel_id="test-channel",
            channel_name="Test Channel",
            watch_count=100,
            total_watch_seconds=3600,
            last_watched=now,
        )
        test_session.add(stats)
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            with patch("popularity_calculator.get_current_date", return_value=today):
                response = await async_client.post(
                    "/api/stats/popularity/calculate",
                    json={"period_days": 7},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["channels_scored"] >= 1
        assert data["channels_created"] >= 1

    @pytest.mark.asyncio
    async def test_calculate_popularity_custom_period(self, async_client, test_session):
        """POST /api/stats/popularity/calculate accepts custom period."""
        today = date.today()

        with patch("popularity_calculator.get_session", return_value=test_session):
            with patch("popularity_calculator.get_current_date", return_value=today):
                response = await async_client.post(
                    "/api/stats/popularity/calculate",
                    json={"period_days": 30},
                )

        assert response.status_code == 200
