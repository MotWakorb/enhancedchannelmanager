"""
Popularity Calculator Service (v0.11.0)

Calculates channel popularity scores based on multiple metrics:
- Watch count (number of viewing sessions)
- Watch time (total seconds watched)
- Unique viewers (distinct IP addresses)
- Bandwidth usage (bytes transferred)

Scores are normalized to a 0-100 scale and combined with configurable weights.
Trends are calculated by comparing current period to previous period.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, distinct

from database import get_session
from models import (
    ChannelWatchStats,
    ChannelBandwidth,
    UniqueClientConnection,
    ChannelPopularityScore,
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
            logger.warning(f"Weights sum to {weight_sum}, normalizing to 1.0")
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
        logger.info(f"Starting popularity calculation (period: {self.period_days} days)")

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
                logger.info("No channel data found for scoring")
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
                f"Popularity calculation complete: {len(scores)} channels scored, "
                f"{channels_updated} updated, {channels_created} created"
            )

            return {
                "channels_scored": len(scores),
                "channels_updated": channels_updated,
                "channels_created": channels_created,
                "top_channels": top_channels,
            }

        except Exception as e:
            logger.error(f"Popularity calculation failed: {e}")
            session.rollback()
            raise
        finally:
            session.close()

    def _gather_metrics(self, session, start_date, end_date) -> dict:
        """
        Gather metrics for all channels in the specified date range.

        Returns:
            dict mapping channel_id to metrics dict
        """
        metrics = {}

        # Get watch stats from ChannelWatchStats (lifetime stats, filter by last_watched)
        watch_stats = session.query(ChannelWatchStats).filter(
            ChannelWatchStats.last_watched >= datetime.combine(start_date, datetime.min.time())
        ).all()

        for ws in watch_stats:
            metrics[ws.channel_id] = {
                "channel_name": ws.channel_name,
                "watch_count": ws.watch_count,
                "watch_time": ws.total_watch_seconds,
                "unique_viewers": 0,
                "bandwidth": 0,
            }

        # Get unique viewer counts from UniqueClientConnection
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
            if uv.channel_id not in metrics:
                metrics[uv.channel_id] = {
                    "channel_name": uv.channel_name,
                    "watch_count": 0,
                    "watch_time": 0,
                    "unique_viewers": 0,
                    "bandwidth": 0,
                }
            metrics[uv.channel_id]["unique_viewers"] = uv.unique_viewers

        # Get bandwidth from ChannelBandwidth
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
            if bw.channel_id not in metrics:
                metrics[bw.channel_id] = {
                    "channel_name": bw.channel_name,
                    "watch_count": 0,
                    "watch_time": 0,
                    "unique_viewers": 0,
                    "bandwidth": 0,
                }
            metrics[bw.channel_id]["bandwidth"] = bw.total_bytes or 0

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
