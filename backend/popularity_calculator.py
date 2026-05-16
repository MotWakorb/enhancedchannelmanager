"""
Popularity Calculator Service (v0.11.0; reader refactored in v0.17.0)

Calculates channel popularity scores based on multiple metrics:
- Watch count (DISTINCT session_id in session_telemetry — see below)
- Watch time (sum of distinct poll intervals — see below)
- Unique viewers (distinct IP addresses)
- Bandwidth usage (bytes transferred)

Scores are normalized to a 0-100 scale and combined with configurable weights.
Trends are calculated by comparing current period to previous period.

v0.17.0 reader refactor (bead enhancedchannelmanager-skqln.3 step (d)):

The "watch_count" and "watch_time" inputs used to read from the legacy
``channel_watch_stats`` lifetime aggregate (one row per channel, populated
by ``BandwidthTracker._update_watch_counts`` / ``_update_watch_time``).
That table is no longer written. The reader now derives both metrics from
the per-poll ``session_telemetry`` stream:

* ``watch_count`` (axis name preserved for weight-config compatibility) is
  ``COUNT(DISTINCT session_id)`` within the period — number of distinct
  viewing sessions on the channel. The legacy semantic was a
  state-transition counter (inactive→active edges); that semantic is not
  derivable from a per-poll observation stream. The DISTINCT-session_id
  substitute is the closest poll-derivable proxy and ranks similarly in
  practice. See ``tests/unit/test_popularity_formula_session_telemetry.py``
  for the regression test that locks the substitution in.
* ``watch_time`` is the sum of distinct ``poll_interval_ms`` values per
  channel (DISTINCT by ``(channel_id, observed_at)``) — matches the legacy
  ``_update_watch_time`` semantic of "one poll interval per active channel
  per still-active poll, regardless of client count." A channel with N
  concurrent clients in one poll still contributes only one interval.
* Channel name is side-loaded from ``UniqueClientConnection`` (the
  sibling table the BandwidthTracker still writes one row per
  connection into per poll). ``session_telemetry`` itself does not store
  the channel name; the ``channel_watch_stats_v`` view that exposes the
  scoped-down read-compat surface deliberately omits it.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, distinct

from database import get_session
from models import (
    ChannelBandwidth,
    ChannelPopularityScore,
    SessionTelemetry,
    UniqueClientConnection,
)
from bandwidth_tracker import get_current_date

logger = logging.getLogger(__name__)

# Default weights for score calculation (must sum to 1.0)
DEFAULT_WEIGHTS = {
    "watch_count": 0.25,      # Number of watch sessions
    "watch_time": 0.30,       # Total watch duration
    "unique_viewers": 0.30,   # Distinct viewers (IPs)
    "bandwidth": 0.15,        # Data transferred
}

# Trend thresholds
TREND_UP_THRESHOLD = 5.0      # Score increase >= 5% = trending up
TREND_DOWN_THRESHOLD = -5.0   # Score decrease <= -5% = trending down


class PopularityCalculator:
    """
    Calculates and updates channel popularity scores.

    Designed to be run periodically (e.g., hourly or daily) to update
    the ChannelPopularityScore table with fresh rankings.
    """

    def __init__(self, period_days: int = 7, weights: Optional[dict] = None):
        """
        Initialize the calculator.

        Args:
            period_days: Number of days to consider for scoring (default 7)
            weights: Custom weights for score components (default uses DEFAULT_WEIGHTS)
        """
        self.period_days = period_days
        self.weights = weights or DEFAULT_WEIGHTS.copy()

        # Validate weights sum to 1.0
        weight_sum = sum(self.weights.values())
        if abs(weight_sum - 1.0) > 0.001:
            logger.warning("[POPULARITY] Weights sum to %s, normalizing to 1.0", weight_sum)
            for key in self.weights:
                self.weights[key] /= weight_sum

    def calculate_all(self) -> dict:
        """
        Calculate popularity scores for all channels.

        Returns:
            dict with calculation results:
            - channels_scored: number of channels with scores
            - channels_updated: number of existing scores updated
            - channels_created: number of new score records created
            - top_channels: list of top 10 channels by score
        """
        logger.info("[POPULARITY] Starting popularity calculation (period: %s days)", self.period_days)

        # Gather metrics for current and previous periods
        today = get_current_date()
        current_start = today - timedelta(days=self.period_days)
        previous_start = current_start - timedelta(days=self.period_days)
        previous_end = current_start - timedelta(days=1)

        session = get_session()
        try:
            # Get current period metrics
            current_metrics = self._gather_metrics(session, current_start, today)

            # Get previous period metrics for trend calculation
            previous_metrics = self._gather_metrics(session, previous_start, previous_end)

            if not current_metrics:
                logger.info("[POPULARITY] No channel data found for scoring")
                return {
                    "channels_scored": 0,
                    "channels_updated": 0,
                    "channels_created": 0,
                    "top_channels": [],
                }

            # Calculate normalized scores
            scores = self._calculate_scores(current_metrics)

            # Calculate previous scores for trend comparison
            previous_scores = self._calculate_scores(previous_metrics) if previous_metrics else {}

            # Update database with scores and ranks
            now = datetime.utcnow()
            channels_updated = 0
            channels_created = 0

            # Sort by score descending to assign ranks
            sorted_channels = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)

            for rank, (channel_id, score_data) in enumerate(sorted_channels, start=1):
                metrics = current_metrics[channel_id]
                prev_score = previous_scores.get(channel_id, {}).get("score", 0)

                # Calculate trend
                if prev_score > 0:
                    trend_percent = ((score_data["score"] - prev_score) / prev_score) * 100
                else:
                    trend_percent = 100.0 if score_data["score"] > 0 else 0.0

                if trend_percent >= TREND_UP_THRESHOLD:
                    trend = "up"
                elif trend_percent <= TREND_DOWN_THRESHOLD:
                    trend = "down"
                else:
                    trend = "stable"

                # Get or create score record
                record = session.query(ChannelPopularityScore).filter(
                    ChannelPopularityScore.channel_id == channel_id
                ).first()

                if record is None:
                    record = ChannelPopularityScore(
                        channel_id=channel_id,
                        channel_name=metrics["channel_name"],
                        calculated_at=now,
                    )
                    session.add(record)
                    channels_created += 1
                else:
                    # Store previous values before updating
                    record.previous_score = record.score
                    record.previous_rank = record.rank
                    channels_updated += 1

                # Update record
                record.channel_name = metrics["channel_name"]
                record.score = round(score_data["score"], 2)
                record.rank = rank
                record.watch_count_7d = metrics["watch_count"]
                record.watch_time_7d = metrics["watch_time"]
                record.unique_viewers_7d = metrics["unique_viewers"]
                record.bandwidth_7d = metrics["bandwidth"]
                record.trend = trend
                record.trend_percent = round(trend_percent, 1)
                record.calculated_at = now

            session.commit()

            # Get top channels for return value
            top_channels = [
                {
                    "channel_id": channel_id,
                    "channel_name": current_metrics[channel_id]["channel_name"],
                    "score": scores[channel_id]["score"],
                    "rank": rank,
                }
                for rank, (channel_id, _) in enumerate(sorted_channels[:10], start=1)
            ]

            logger.info(
                "[POPULARITY] Popularity calculation complete: %s channels scored, "
                "%s updated, %s created",
                len(scores), channels_updated, channels_created
            )

            return {
                "channels_scored": len(scores),
                "channels_updated": channels_updated,
                "channels_created": channels_created,
                "top_channels": top_channels,
            }

        except Exception as e:
            logger.exception("[POPULARITY] Popularity calculation failed: %s", e)
            session.rollback()
            raise
        finally:
            session.close()

    def _gather_metrics(self, session, start_date, end_date) -> dict:
        """
        Gather metrics for all channels in the specified date range.

        Post step (d): the ``watch_count`` and ``watch_time`` axes read
        from ``session_telemetry`` (per-poll grain) rather than the legacy
        ``channel_watch_stats`` aggregate. Channel name is side-loaded
        from ``UniqueClientConnection`` (or ``ChannelBandwidth`` as a
        fallback if a channel has bandwidth rows but no connection rows
        within the window).

        Returns:
            dict mapping channel_id to metrics dict
        """
        metrics: dict = {}

        # session_telemetry uses ms-since-epoch for observed_at; convert
        # the calendar-date bounds to ms once.
        start_ms = int(
            datetime.combine(start_date, datetime.min.time()).timestamp() * 1000
        )
        # end_date is treated as INCLUSIVE on the day boundary — match
        # the legacy WHERE last_watched >= start_date semantic which had
        # no upper bound. We add 1 day so a same-day end_date still
        # captures rows observed any time on that day.
        end_ms = int(
            datetime.combine(end_date + timedelta(days=1), datetime.min.time())
            .timestamp() * 1000
        )

        # Distinct viewing sessions per channel (replaces legacy
        # state-transition watch_count). Bead skqln.3 step (d).
        watch_count_rows = session.query(
            SessionTelemetry.channel_id,
            func.count(distinct(SessionTelemetry.session_id)).label("watch_count"),
        ).filter(
            SessionTelemetry.observed_at >= start_ms,
            SessionTelemetry.observed_at < end_ms,
        ).group_by(SessionTelemetry.channel_id).all()

        for row in watch_count_rows:
            metrics.setdefault(row.channel_id, {
                "channel_name": None,
                "watch_count": 0,
                "watch_time": 0,
                "unique_viewers": 0,
                "bandwidth": 0,
            })
            metrics[row.channel_id]["watch_count"] = row.watch_count

        # Watch time per channel: sum of distinct-by-(channel, observed_at)
        # poll intervals. Mirrors the channel_watch_stats_v view shape —
        # a channel with N concurrent clients in one poll contributes one
        # interval, not N. This is the same DISTINCT-by-observed_at
        # collapse the migration 0008 view performs.
        per_poll = session.query(
            SessionTelemetry.channel_id.label("channel_id"),
            SessionTelemetry.observed_at.label("observed_at"),
            func.max(SessionTelemetry.poll_interval_ms).label("poll_interval_ms"),
        ).filter(
            SessionTelemetry.observed_at >= start_ms,
            SessionTelemetry.observed_at < end_ms,
        ).group_by(
            SessionTelemetry.channel_id,
            SessionTelemetry.observed_at,
        ).subquery()

        watch_time_rows = session.query(
            per_poll.c.channel_id,
            func.coalesce(
                func.sum(per_poll.c.poll_interval_ms) / 1000, 0
            ).label("watch_time"),
        ).group_by(per_poll.c.channel_id).all()

        for row in watch_time_rows:
            metrics.setdefault(row.channel_id, {
                "channel_name": None,
                "watch_count": 0,
                "watch_time": 0,
                "unique_viewers": 0,
                "bandwidth": 0,
            })
            metrics[row.channel_id]["watch_time"] = int(row.watch_time or 0)

        # Unique viewer counts and channel-name side-load from
        # UniqueClientConnection. Same query the legacy reader used; the
        # channel_name picked up here is the post-step-(d) source of
        # truth for that field (session_telemetry doesn't store it).
        unique_viewer_data = session.query(
            UniqueClientConnection.channel_id,
            UniqueClientConnection.channel_name,
            func.count(distinct(UniqueClientConnection.ip_address)).label("unique_viewers"),
        ).filter(
            UniqueClientConnection.date >= start_date,
            UniqueClientConnection.date <= end_date,
        ).group_by(
            UniqueClientConnection.channel_id,
            UniqueClientConnection.channel_name,
        ).all()

        for uv in unique_viewer_data:
            metrics.setdefault(uv.channel_id, {
                "channel_name": None,
                "watch_count": 0,
                "watch_time": 0,
                "unique_viewers": 0,
                "bandwidth": 0,
            })
            metrics[uv.channel_id]["unique_viewers"] = uv.unique_viewers
            # Side-load channel name only if not already set (avoid stomping
            # on a name from a different connection row if the connection
            # table has stale denormalized values for the same channel).
            if not metrics[uv.channel_id]["channel_name"]:
                metrics[uv.channel_id]["channel_name"] = uv.channel_name

        # Get bandwidth from ChannelBandwidth — same query as the legacy
        # reader; the table is still written by BandwidthTracker.
        bandwidth_data = session.query(
            ChannelBandwidth.channel_id,
            ChannelBandwidth.channel_name,
            func.sum(ChannelBandwidth.bytes_transferred).label("total_bytes"),
        ).filter(
            ChannelBandwidth.date >= start_date,
            ChannelBandwidth.date <= end_date,
        ).group_by(
            ChannelBandwidth.channel_id,
            ChannelBandwidth.channel_name,
        ).all()

        for bw in bandwidth_data:
            metrics.setdefault(bw.channel_id, {
                "channel_name": None,
                "watch_count": 0,
                "watch_time": 0,
                "unique_viewers": 0,
                "bandwidth": 0,
            })
            metrics[bw.channel_id]["bandwidth"] = bw.total_bytes or 0
            # Fallback channel-name source if UniqueClientConnection
            # didn't supply one for this channel.
            if not metrics[bw.channel_id]["channel_name"]:
                metrics[bw.channel_id]["channel_name"] = bw.channel_name

        # Last-resort fallback so the scoring loop doesn't crash on a
        # NULL channel_name. Shouldn't happen in production — the writer
        # always populates UniqueClientConnection.channel_name — but
        # belt-and-suspenders for the popularity record's NOT NULL field.
        for channel_id, m in metrics.items():
            if not m["channel_name"]:
                m["channel_name"] = f"Channel {channel_id[:8]}..."

        return metrics

    def _calculate_scores(self, metrics: dict) -> dict:
        """
        Calculate normalized popularity scores for all channels.

        Uses min-max normalization to scale each metric to 0-100,
        then applies weights to create composite score.

        Returns:
            dict mapping channel_id to score dict with component scores
        """
        if not metrics:
            return {}

        # Find max values for normalization
        max_watch_count = max((m["watch_count"] for m in metrics.values()), default=1) or 1
        max_watch_time = max((m["watch_time"] for m in metrics.values()), default=1) or 1
        max_unique_viewers = max((m["unique_viewers"] for m in metrics.values()), default=1) or 1
        max_bandwidth = max((m["bandwidth"] for m in metrics.values()), default=1) or 1

        scores = {}
        for channel_id, m in metrics.items():
            # Normalize each metric to 0-100
            norm_watch_count = (m["watch_count"] / max_watch_count) * 100
            norm_watch_time = (m["watch_time"] / max_watch_time) * 100
            norm_unique_viewers = (m["unique_viewers"] / max_unique_viewers) * 100
            norm_bandwidth = (m["bandwidth"] / max_bandwidth) * 100

            # Calculate weighted composite score
            composite = (
                norm_watch_count * self.weights["watch_count"] +
                norm_watch_time * self.weights["watch_time"] +
                norm_unique_viewers * self.weights["unique_viewers"] +
                norm_bandwidth * self.weights["bandwidth"]
            )

            scores[channel_id] = {
                "score": composite,
                "watch_count_score": norm_watch_count,
                "watch_time_score": norm_watch_time,
                "unique_viewers_score": norm_unique_viewers,
                "bandwidth_score": norm_bandwidth,
            }

        return scores

    @staticmethod
    def get_rankings(limit: int = 50, offset: int = 0) -> dict:
        """
        Get current popularity rankings.

        Args:
            limit: Maximum number of channels to return
            offset: Number of channels to skip (for pagination)

        Returns:
            dict with rankings list and total count
        """
        session = get_session()
        try:
            total = session.query(func.count(ChannelPopularityScore.id)).scalar() or 0

            records = session.query(ChannelPopularityScore).order_by(
                ChannelPopularityScore.rank.asc()
            ).offset(offset).limit(limit).all()

            return {
                "total": total,
                "rankings": [r.to_dict() for r in records],
            }
        finally:
            session.close()

    @staticmethod
    def get_channel_score(channel_id: str) -> Optional[dict]:
        """
        Get popularity score for a specific channel.

        Args:
            channel_id: The channel UUID

        Returns:
            Score dict or None if not found
        """
        session = get_session()
        try:
            record = session.query(ChannelPopularityScore).filter(
                ChannelPopularityScore.channel_id == channel_id
            ).first()

            return record.to_dict() if record else None
        finally:
            session.close()

    @staticmethod
    def get_trending_channels(direction: str = "up", limit: int = 10) -> list[dict]:
        """
        Get channels that are trending up or down.

        Args:
            direction: "up" or "down"
            limit: Maximum number to return

        Returns:
            List of channel score dicts
        """
        session = get_session()
        try:
            query = session.query(ChannelPopularityScore).filter(
                ChannelPopularityScore.trend == direction
            )

            if direction == "up":
                query = query.order_by(ChannelPopularityScore.trend_percent.desc())
            else:
                query = query.order_by(ChannelPopularityScore.trend_percent.asc())

            records = query.limit(limit).all()
            return [r.to_dict() for r in records]
        finally:
            session.close()


# Convenience function for running calculation
def calculate_popularity(
    period_days: int = 7,
    weights: Optional[dict] = None,
    evaluate_rules: bool = False,
    rules_dry_run: bool = False,
) -> dict:
    """
    Run popularity calculation with specified parameters.

    Args:
        period_days: Number of days to consider
        weights: Optional custom weights
        evaluate_rules: Whether to evaluate popularity rules after calculation
        rules_dry_run: If evaluating rules, whether to run in dry-run mode

    Returns:
        Calculation results dict
    """
    calculator = PopularityCalculator(period_days=period_days, weights=weights)
    return calculator.calculate_all()
