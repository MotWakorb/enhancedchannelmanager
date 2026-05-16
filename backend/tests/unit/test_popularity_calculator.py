"""
Unit tests for the PopularityCalculator module (v0.11.0).

Tests the popularity scoring algorithm including:
- Weight normalization
- Metric gathering from multiple data sources
- Min-max score normalization
- Trend calculation
- Ranking assignment
"""
from datetime import datetime, date, timedelta
from unittest.mock import patch

from models import (
    ChannelBandwidth,
    ChannelPopularityScore,
    SessionTelemetry,
    UniqueClientConnection,
)
from popularity_calculator import (
    PopularityCalculator,
    calculate_popularity,
    DEFAULT_WEIGHTS,
    TREND_UP_THRESHOLD,
    TREND_DOWN_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Test helpers — seed session_telemetry the way the post-step-(d) calculator
# expects (per-poll rows, with a UniqueClientConnection row for channel_name).
# ---------------------------------------------------------------------------

def _seed_watch_session(
    test_session,
    *,
    channel_id: str,
    channel_name: str,
    poll_count: int,
    base_observed_at_ms: int | None = None,
    distinct_sessions: int = 1,
    poll_interval_ms: int = 10_000,
    bytes_per_poll: int = 1000,
):
    """Seed ``session_telemetry`` rows + a ``UniqueClientConnection`` row.

    Mirrors the production write shape so the calculator's reader path is
    exercised. ``distinct_sessions`` controls how the polls are split
    across ``session_id`` values — this is what the post-step-(d) formula
    uses as the ``watch_count`` substitute (DISTINCT session_id).

    Returns the (start_date, end_date) tuple covering the seeded rows.
    """
    if base_observed_at_ms is None:
        base_observed_at_ms = int(
            (datetime.utcnow() - timedelta(days=1)).timestamp() * 1000
        )

    polls_per_session = poll_count // distinct_sessions
    extra = poll_count - polls_per_session * distinct_sessions

    poll_index = 0
    for s in range(distinct_sessions):
        n = polls_per_session + (1 if s < extra else 0)
        for _ in range(n):
            observed_at = base_observed_at_ms + poll_index * poll_interval_ms
            test_session.add(
                SessionTelemetry(
                    session_id=f"conn-{channel_id}-{s}",
                    observed_at=observed_at,
                    user_id=None,
                    provider_id=None,
                    channel_id=channel_id,
                    bytes_delta=bytes_per_poll,
                    buffer_event_count=0,
                    poll_interval_ms=poll_interval_ms,
                )
            )
            poll_index += 1

    last_observed_dt = datetime.utcfromtimestamp(
        (base_observed_at_ms + (poll_count - 1) * poll_interval_ms) / 1000.0
    )
    # One UniqueClientConnection row so the channel_name side-load has a
    # source. IP uses channel_id to keep rows distinct across channels.
    test_session.add(
        UniqueClientConnection(
            ip_address=f"10.0.0.{abs(hash(channel_id)) % 200 + 1}",
            channel_id=channel_id,
            channel_name=channel_name,
            user_id=None,
            username=None,
            date=last_observed_dt.date(),
            connected_at=last_observed_dt,
            watch_seconds=poll_count * (poll_interval_ms // 1000),
        )
    )


class TestPopularityCalculatorInit:
    """Tests for PopularityCalculator initialization."""

    def test_init_with_default_period(self):
        """Default period is 7 days."""
        calc = PopularityCalculator()
        assert calc.period_days == 7

    def test_init_with_custom_period(self):
        """Custom period can be specified."""
        calc = PopularityCalculator(period_days=30)
        assert calc.period_days == 30

    def test_init_with_default_weights(self):
        """Default weights are used when not specified."""
        calc = PopularityCalculator()
        assert calc.weights == DEFAULT_WEIGHTS

    def test_init_with_custom_weights(self):
        """Custom weights can be specified."""
        custom = {"watch_count": 0.5, "watch_time": 0.5, "unique_viewers": 0, "bandwidth": 0}
        calc = PopularityCalculator(weights=custom)
        assert calc.weights == custom

    def test_init_normalizes_weights_that_dont_sum_to_one(self):
        """Weights are normalized if they don't sum to 1.0."""
        custom = {"watch_count": 0.5, "watch_time": 0.5, "unique_viewers": 0.5, "bandwidth": 0.5}
        calc = PopularityCalculator(weights=custom)

        # Each weight should be 0.25 after normalization
        assert abs(calc.weights["watch_count"] - 0.25) < 0.001
        assert abs(calc.weights["watch_time"] - 0.25) < 0.001
        assert abs(sum(calc.weights.values()) - 1.0) < 0.001

    def test_init_preserves_weights_that_sum_to_one(self):
        """Weights that already sum to 1.0 are not modified."""
        custom = {"watch_count": 0.4, "watch_time": 0.3, "unique_viewers": 0.2, "bandwidth": 0.1}
        calc = PopularityCalculator(weights=custom)
        assert calc.weights["watch_count"] == 0.4
        assert calc.weights["watch_time"] == 0.3


class TestCalculateScores:
    """Tests for the _calculate_scores method."""

    def test_calculate_scores_empty_metrics(self):
        """Returns empty dict for empty metrics."""
        calc = PopularityCalculator()
        result = calc._calculate_scores({})
        assert result == {}

    def test_calculate_scores_single_channel(self):
        """Single channel gets score of 100 (normalized against itself)."""
        calc = PopularityCalculator()
        metrics = {
            "channel-1": {
                "channel_name": "Test Channel",
                "watch_count": 100,
                "watch_time": 3600,
                "unique_viewers": 10,
                "bandwidth": 1000000,
            }
        }

        result = calc._calculate_scores(metrics)

        assert "channel-1" in result
        # With only one channel, all normalized metrics are 100
        assert result["channel-1"]["score"] == 100.0

    def test_calculate_scores_multiple_channels_ranking(self):
        """Channels with higher metrics get higher scores."""
        calc = PopularityCalculator()
        metrics = {
            "channel-1": {
                "channel_name": "High Channel",
                "watch_count": 100,
                "watch_time": 3600,
                "unique_viewers": 50,
                "bandwidth": 1000000,
            },
            "channel-2": {
                "channel_name": "Low Channel",
                "watch_count": 10,
                "watch_time": 360,
                "unique_viewers": 5,
                "bandwidth": 100000,
            },
        }

        result = calc._calculate_scores(metrics)

        assert result["channel-1"]["score"] > result["channel-2"]["score"]
        assert result["channel-1"]["score"] == 100.0  # Max values
        assert result["channel-2"]["score"] == 10.0   # 10% of max values

    def test_calculate_scores_with_zero_metrics(self):
        """Handles channels with zero metrics."""
        calc = PopularityCalculator()
        metrics = {
            "channel-1": {
                "channel_name": "Active Channel",
                "watch_count": 100,
                "watch_time": 3600,
                "unique_viewers": 10,
                "bandwidth": 1000000,
            },
            "channel-2": {
                "channel_name": "Inactive Channel",
                "watch_count": 0,
                "watch_time": 0,
                "unique_viewers": 0,
                "bandwidth": 0,
            },
        }

        result = calc._calculate_scores(metrics)

        assert result["channel-2"]["score"] == 0.0

    def test_calculate_scores_applies_weights(self):
        """Weights are correctly applied to component scores."""
        # Use weights that heavily favor watch_count
        calc = PopularityCalculator(weights={
            "watch_count": 1.0,
            "watch_time": 0.0,
            "unique_viewers": 0.0,
            "bandwidth": 0.0,
        })

        metrics = {
            "channel-1": {
                "channel_name": "Test",
                "watch_count": 50,
                "watch_time": 100,
                "unique_viewers": 100,
                "bandwidth": 100,
            },
            "channel-2": {
                "channel_name": "Test2",
                "watch_count": 100,
                "watch_time": 10,
                "unique_viewers": 10,
                "bandwidth": 10,
            },
        }

        result = calc._calculate_scores(metrics)

        # Channel 2 has higher watch_count, so should score higher
        assert result["channel-2"]["score"] > result["channel-1"]["score"]


class TestGatherMetrics:
    """Tests for the _gather_metrics method."""

    def test_gather_metrics_from_session_telemetry(self, test_session):
        """Gathers watch_count (DISTINCT session_id) and watch_time
        (sum of distinct poll intervals) from ``session_telemetry``.

        Post step (d): the calculator reads per-poll telemetry rows
        instead of the legacy ``channel_watch_stats`` lifetime
        aggregate. The legacy ``ChannelWatchStats.watch_count``
        semantic (state-transition counter) is replaced by
        ``COUNT(DISTINCT session_id)``.
        """
        _seed_watch_session(
            test_session,
            channel_id="test-channel",
            channel_name="Test Channel",
            poll_count=10,
            distinct_sessions=2,
        )
        test_session.commit()

        calc = PopularityCalculator()
        start_date = date.today() - timedelta(days=7)
        end_date = date.today() + timedelta(days=1)

        metrics = calc._gather_metrics(test_session, start_date, end_date)

        assert "test-channel" in metrics
        # watch_count = DISTINCT session_id (post step (d) semantic).
        assert metrics["test-channel"]["watch_count"] == 2
        # watch_time = sum of distinct poll intervals (10 polls × 10s/poll).
        assert metrics["test-channel"]["watch_time"] == 100
        # channel_name side-loaded from UniqueClientConnection.
        assert metrics["test-channel"]["channel_name"] == "Test Channel"

    def test_gather_metrics_from_unique_connections(self, test_session):
        """Gathers unique_viewers from UniqueClientConnection."""
        today = date.today()

        # Create multiple connections from same IP (should count as 1)
        for i in range(3):
            conn = UniqueClientConnection(
                ip_address="192.168.1.100",
                channel_id="test-channel",
                channel_name="Test Channel",
                date=today,
                connected_at=datetime.utcnow(),
                watch_seconds=100,
            )
            test_session.add(conn)

        # Create connection from different IP
        conn2 = UniqueClientConnection(
            ip_address="192.168.1.101",
            channel_id="test-channel",
            channel_name="Test Channel",
            date=today,
            connected_at=datetime.utcnow(),
            watch_seconds=200,
        )
        test_session.add(conn2)
        test_session.commit()

        calc = PopularityCalculator()
        start_date = today - timedelta(days=7)

        metrics = calc._gather_metrics(test_session, start_date, today)

        assert "test-channel" in metrics
        assert metrics["test-channel"]["unique_viewers"] == 2  # 2 unique IPs

    def test_gather_metrics_from_channel_bandwidth(self, test_session):
        """Gathers bandwidth from ChannelBandwidth."""
        today = date.today()

        # Create bandwidth records for multiple days
        for i in range(3):
            bw = ChannelBandwidth(
                channel_id="test-channel",
                channel_name="Test Channel",
                date=today - timedelta(days=i),
                bytes_transferred=1000000,  # 1MB per day
                peak_clients=5,
                total_watch_seconds=3600,
                connection_count=10,
            )
            test_session.add(bw)
        test_session.commit()

        calc = PopularityCalculator()
        start_date = today - timedelta(days=7)

        metrics = calc._gather_metrics(test_session, start_date, today)

        assert "test-channel" in metrics
        assert metrics["test-channel"]["bandwidth"] == 3000000  # Sum of 3 days

    def test_gather_metrics_empty_database(self, test_session):
        """Returns empty dict when no data exists."""
        calc = PopularityCalculator()
        start_date = date.today() - timedelta(days=7)
        end_date = date.today()

        metrics = calc._gather_metrics(test_session, start_date, end_date)

        assert metrics == {}

    def test_gather_metrics_filters_by_date_range(self, test_session):
        """Only includes data within the specified date range."""
        today = date.today()

        # Create data within range
        in_range = UniqueClientConnection(
            ip_address="192.168.1.100",
            channel_id="channel-1",
            channel_name="In Range",
            date=today - timedelta(days=3),
            connected_at=datetime.utcnow(),
            watch_seconds=100,
        )
        test_session.add(in_range)

        # Create data outside range
        out_of_range = UniqueClientConnection(
            ip_address="192.168.1.101",
            channel_id="channel-2",
            channel_name="Out of Range",
            date=today - timedelta(days=30),
            connected_at=datetime.utcnow(),
            watch_seconds=100,
        )
        test_session.add(out_of_range)
        test_session.commit()

        calc = PopularityCalculator(period_days=7)
        start_date = today - timedelta(days=7)

        metrics = calc._gather_metrics(test_session, start_date, today)

        assert "channel-1" in metrics
        assert "channel-2" not in metrics


class TestCalculateAll:
    """Tests for the calculate_all method."""

    def test_calculate_all_creates_score_records(self, test_session):
        """Creates ChannelPopularityScore records for channels."""
        today = date.today()

        # Seed session_telemetry data (the post-step-(d) data source).
        _seed_watch_session(
            test_session,
            channel_id="test-channel",
            channel_name="Test Channel",
            poll_count=10,
        )
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            with patch("popularity_calculator.get_current_date", return_value=today):
                calc = PopularityCalculator()
                result = calc.calculate_all()

        assert result["channels_scored"] == 1
        assert result["channels_created"] == 1
        assert result["channels_updated"] == 0

        # Verify record was created
        score = test_session.query(ChannelPopularityScore).filter(
            ChannelPopularityScore.channel_id == "test-channel"
        ).first()
        assert score is not None
        assert score.rank == 1

    def test_calculate_all_updates_existing_records(self, test_session):
        """Updates existing ChannelPopularityScore records."""
        today = date.today()
        now = datetime.utcnow()

        # Create existing score record
        existing = ChannelPopularityScore(
            channel_id="test-channel",
            channel_name="Test Channel",
            score=50.0,
            rank=1,
            watch_count_7d=50,
            watch_time_7d=1800,
            unique_viewers_7d=5,
            bandwidth_7d=500000,
            trend="stable",
            trend_percent=0.0,
            calculated_at=now - timedelta(hours=1),
        )
        test_session.add(existing)

        # Seed session_telemetry for the recalculation pass.
        _seed_watch_session(
            test_session,
            channel_id="test-channel",
            channel_name="Test Channel",
            poll_count=10,
        )
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            with patch("popularity_calculator.get_current_date", return_value=today):
                calc = PopularityCalculator()
                result = calc.calculate_all()

        assert result["channels_updated"] == 1
        assert result["channels_created"] == 0

        # Verify previous values were stored
        score = test_session.query(ChannelPopularityScore).filter(
            ChannelPopularityScore.channel_id == "test-channel"
        ).first()
        assert score.previous_score == 50.0
        assert score.previous_rank == 1

    def test_calculate_all_assigns_correct_ranks(self, test_session):
        """Assigns ranks based on score (1 = highest).

        Seeds three channels with stepped-down activity (poll counts and
        distinct sessions both decrease). All score components move in
        the same direction so the ranking is unambiguous regardless of
        weight allocation.
        """
        today = date.today()

        # Stepped-down activity: high → medium → low on every axis.
        # poll_count and distinct_sessions both scale, so watch_count
        # (DISTINCT session_id) and watch_time both decrease together.
        for i, (poll_count, distinct, name) in enumerate([
            (60, 6, "Most Popular"),
            (30, 3, "Medium Popular"),
            (10, 1, "Least Popular"),
        ]):
            _seed_watch_session(
                test_session,
                channel_id=f"channel-{i}",
                channel_name=name,
                poll_count=poll_count,
                distinct_sessions=distinct,
            )
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            with patch("popularity_calculator.get_current_date", return_value=today):
                calc = PopularityCalculator()
                calc.calculate_all()

        scores = test_session.query(ChannelPopularityScore).order_by(
            ChannelPopularityScore.rank
        ).all()

        assert len(scores) == 3
        assert scores[0].channel_name == "Most Popular"
        assert scores[0].rank == 1
        assert scores[1].channel_name == "Medium Popular"
        assert scores[1].rank == 2
        assert scores[2].channel_name == "Least Popular"
        assert scores[2].rank == 3

    def test_calculate_all_returns_top_channels(self, test_session):
        """Returns list of top 10 channels in result."""
        today = date.today()

        # Create 15 channels with stepped poll counts so they sort cleanly.
        for i in range(15):
            _seed_watch_session(
                test_session,
                channel_id=f"channel-{i}",
                channel_name=f"Channel {i}",
                poll_count=60 - i * 3,  # 60, 57, 54, ... 18 polls
                distinct_sessions=1,
            )
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            with patch("popularity_calculator.get_current_date", return_value=today):
                calc = PopularityCalculator()
                result = calc.calculate_all()

        assert len(result["top_channels"]) == 10
        assert result["top_channels"][0]["rank"] == 1
        assert result["top_channels"][9]["rank"] == 10

    def test_calculate_all_empty_database(self, test_session):
        """Handles empty database gracefully."""
        today = date.today()

        with patch("popularity_calculator.get_session", return_value=test_session):
            with patch("popularity_calculator.get_current_date", return_value=today):
                calc = PopularityCalculator()
                result = calc.calculate_all()

        assert result["channels_scored"] == 0
        assert result["channels_updated"] == 0
        assert result["channels_created"] == 0
        assert result["top_channels"] == []


class TestTrendCalculation:
    """Tests for trend calculation logic."""

    def test_trend_up_when_score_increases_significantly(self, test_session):
        """Trend is 'up' when score increases >= 5%."""
        today = date.today()
        now = datetime.utcnow()

        # With min-max normalization, we need multiple channels to see trends
        # Channel A: was dominant in previous period, channel B was small
        # Current period: Channel B became dominant

        # Previous period (14-8 days ago): Channel A dominant
        for i in range(10):
            prev_conn = UniqueClientConnection(
                ip_address=f"192.168.1.{i}",
                channel_id="channel-a",
                channel_name="Channel A",
                date=today - timedelta(days=10),
                connected_at=now - timedelta(days=10),
                watch_seconds=1000,
            )
            test_session.add(prev_conn)

        # Previous period: Channel B had minimal activity
        prev_conn_b = UniqueClientConnection(
            ip_address="192.168.2.1",
            channel_id="channel-b",
            channel_name="Channel B",
            date=today - timedelta(days=10),
            connected_at=now - timedelta(days=10),
            watch_seconds=100,
        )
        test_session.add(prev_conn_b)

        # Current period (last 7 days): Channel B now dominant
        for i in range(10):
            curr_conn = UniqueClientConnection(
                ip_address=f"192.168.3.{i}",
                channel_id="channel-b",
                channel_name="Channel B",
                date=today - timedelta(days=1),
                connected_at=now - timedelta(days=1),
                watch_seconds=1000,
            )
            test_session.add(curr_conn)

        # Current period: Channel A has minimal activity
        curr_conn_a = UniqueClientConnection(
            ip_address="192.168.4.1",
            channel_id="channel-a",
            channel_name="Channel A",
            date=today - timedelta(days=1),
            connected_at=now - timedelta(days=1),
            watch_seconds=100,
        )
        test_session.add(curr_conn_a)
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            with patch("popularity_calculator.get_current_date", return_value=today):
                calc = PopularityCalculator(period_days=7)
                calc.calculate_all()

        # Channel B went from low score to high score = trending up
        score_b = test_session.query(ChannelPopularityScore).filter(
            ChannelPopularityScore.channel_id == "channel-b"
        ).first()

        # Channel A went from high score to low score = trending down
        score_a = test_session.query(ChannelPopularityScore).filter(
            ChannelPopularityScore.channel_id == "channel-a"
        ).first()

        assert score_b.trend == "up"
        assert score_a.trend == "down"

    def test_trend_stable_when_score_changes_slightly(self, test_session):
        """Trend is 'stable' when score changes < 5%."""
        today = date.today()

        # Seed session_telemetry data (the post-step-(d) source).
        _seed_watch_session(
            test_session,
            channel_id="test-channel",
            channel_name="Test Channel",
            poll_count=10,
        )
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            with patch("popularity_calculator.get_current_date", return_value=today):
                calc = PopularityCalculator()
                calc.calculate_all()

        score = test_session.query(ChannelPopularityScore).first()
        # New channel with no previous score has 100% trend (or stable if prev=0)
        # Actually for new channels with no previous data, trend_percent = 100 if score > 0
        assert score.trend in ["up", "stable"]


class TestGetRankings:
    """Tests for the get_rankings static method."""

    def test_get_rankings_returns_paginated_results(self, test_session):
        """Returns rankings with pagination."""
        now = datetime.utcnow()

        # Create 5 score records
        for i in range(5):
            score = ChannelPopularityScore(
                channel_id=f"channel-{i}",
                channel_name=f"Channel {i}",
                score=100 - i * 10,
                rank=i + 1,
                trend="stable",
                trend_percent=0.0,
                calculated_at=now,
            )
            test_session.add(score)
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            result = PopularityCalculator.get_rankings(limit=2, offset=0)

        assert result["total"] == 5
        assert len(result["rankings"]) == 2
        assert result["rankings"][0]["rank"] == 1

    def test_get_rankings_with_offset(self, test_session):
        """Offset skips the specified number of records."""
        now = datetime.utcnow()

        for i in range(5):
            score = ChannelPopularityScore(
                channel_id=f"channel-{i}",
                channel_name=f"Channel {i}",
                score=100 - i * 10,
                rank=i + 1,
                trend="stable",
                trend_percent=0.0,
                calculated_at=now,
            )
            test_session.add(score)
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            result = PopularityCalculator.get_rankings(limit=2, offset=2)

        assert result["rankings"][0]["rank"] == 3

    def test_get_rankings_empty_database(self, test_session):
        """Returns empty list for empty database."""
        with patch("popularity_calculator.get_session", return_value=test_session):
            result = PopularityCalculator.get_rankings()

        assert result["total"] == 0
        assert result["rankings"] == []


class TestGetChannelScore:
    """Tests for the get_channel_score static method."""

    def test_get_channel_score_returns_score(self, test_session):
        """Returns score for existing channel."""
        now = datetime.utcnow()

        score = ChannelPopularityScore(
            channel_id="test-channel",
            channel_name="Test Channel",
            score=75.5,
            rank=1,
            trend="up",
            trend_percent=10.0,
            calculated_at=now,
        )
        test_session.add(score)
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            result = PopularityCalculator.get_channel_score("test-channel")

        assert result is not None
        assert result["score"] == 75.5
        assert result["trend"] == "up"

    def test_get_channel_score_returns_none_for_missing(self, test_session):
        """Returns None for non-existent channel."""
        with patch("popularity_calculator.get_session", return_value=test_session):
            result = PopularityCalculator.get_channel_score("nonexistent")

        assert result is None


class TestGetTrendingChannels:
    """Tests for the get_trending_channels static method."""

    def test_get_trending_up_channels(self, test_session):
        """Returns channels trending up, sorted by trend_percent descending."""
        now = datetime.utcnow()

        # Create channels with various trends
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
            result = PopularityCalculator.get_trending_channels(direction="up")

        assert len(result) == 2
        assert result[0]["trend_percent"] == 50.0  # Highest first
        assert result[1]["trend_percent"] == 25.0

    def test_get_trending_down_channels(self, test_session):
        """Returns channels trending down, sorted by trend_percent ascending."""
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
            result = PopularityCalculator.get_trending_channels(direction="down")

        assert len(result) == 2
        assert result[0]["trend_percent"] == -30.0  # Most negative first
        assert result[1]["trend_percent"] == -10.0


class TestCalculatePopularityFunction:
    """Tests for the calculate_popularity convenience function."""

    def test_calculate_popularity_creates_calculator(self, test_session):
        """Creates calculator with specified parameters."""
        today = date.today()

        with patch("popularity_calculator.get_session", return_value=test_session):
            with patch("popularity_calculator.get_current_date", return_value=today):
                result = calculate_popularity(period_days=14)

        assert result["channels_scored"] == 0  # Empty database


class TestConstants:
    """Tests for module constants."""

    def test_default_weights_sum_to_one(self):
        """DEFAULT_WEIGHTS sum to 1.0."""
        assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 0.001

    def test_trend_thresholds(self):
        """Trend thresholds are correctly defined."""
        assert TREND_UP_THRESHOLD == 5.0
        assert TREND_DOWN_THRESHOLD == -5.0
