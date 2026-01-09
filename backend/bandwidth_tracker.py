"""
Background bandwidth tracking service.
Polls Dispatcharr stats periodically and accumulates bandwidth data.
"""
import asyncio
import logging
from datetime import datetime, date, timedelta
from typing import Optional

from database import get_session
from models import BandwidthDaily

logger = logging.getLogger(__name__)

# Polling interval in seconds
POLL_INTERVAL = 30


class BandwidthTracker:
    """
    Background service that tracks bandwidth usage over time.
    Polls Dispatcharr's stats endpoint and stores daily aggregates.
    """

    def __init__(self, client):
        """
        Initialize the tracker.

        Args:
            client: DispatcharrClient instance for API calls
        """
        self.client = client
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_bytes: dict[str, int] = {}  # Track per-channel bytes to compute deltas

    async def start(self):
        """Start the background polling task."""
        if self._running:
            logger.warning("BandwidthTracker already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"BandwidthTracker started (polling every {POLL_INTERVAL}s)")

    async def stop(self):
        """Stop the background polling task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("BandwidthTracker stopped")

    async def _poll_loop(self):
        """Main polling loop - runs until stopped."""
        while self._running:
            try:
                await self._collect_stats()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"BandwidthTracker error: {e}")

            # Wait for next poll interval
            try:
                await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                break

    async def _collect_stats(self):
        """Fetch stats from Dispatcharr and update daily totals."""
        try:
            stats = await self.client.get_channel_stats()
        except Exception as e:
            logger.debug(f"Failed to fetch stats: {e}")
            return

        channels = stats.get("channels", [])
        if not channels:
            return

        # Calculate totals from all active channels
        total_bytes_delta = 0
        active_channels = len(channels)
        total_clients = 0

        current_bytes: dict[str, int] = {}

        for channel in channels:
            channel_id = str(channel.get("channel_id", ""))
            bytes_now = channel.get("total_bytes", 0) or 0
            client_count = channel.get("client_count", 0) or 0

            current_bytes[channel_id] = bytes_now
            total_clients += client_count

            # Calculate delta if we have previous value for this channel
            if channel_id in self._last_bytes:
                prev_bytes = self._last_bytes[channel_id]
                if bytes_now > prev_bytes:
                    total_bytes_delta += bytes_now - prev_bytes

        # Update last bytes tracking
        self._last_bytes = current_bytes

        # Only record if there's actual data transfer
        if total_bytes_delta > 0 or active_channels > 0:
            self._update_daily_record(total_bytes_delta, active_channels, total_clients)

    def _update_daily_record(self, bytes_delta: int, active_channels: int, total_clients: int):
        """Update today's bandwidth record in the database."""
        today = date.today()

        session = get_session()
        try:
            # Get or create today's record
            record = session.query(BandwidthDaily).filter(
                BandwidthDaily.date == today
            ).first()

            if record is None:
                record = BandwidthDaily(
                    date=today,
                    bytes_transferred=0,
                    peak_channels=0,
                    peak_clients=0,
                )
                session.add(record)

            # Update totals
            record.bytes_transferred += bytes_delta
            record.peak_channels = max(record.peak_channels, active_channels)
            record.peak_clients = max(record.peak_clients, total_clients)

            session.commit()
        except Exception as e:
            logger.error(f"Failed to update bandwidth record: {e}")
            session.rollback()
        finally:
            session.close()

    @staticmethod
    def get_bandwidth_summary() -> dict:
        """
        Get bandwidth summary for all time periods.

        Returns:
            dict with today, this_week, this_month, this_year, all_time bytes,
            and daily_history for last 7 days
        """
        today = date.today()
        week_ago = today - timedelta(days=7)
        month_start = today.replace(day=1)
        year_start = today.replace(month=1, day=1)

        session = get_session()
        try:
            # Get all records for calculations
            all_records = session.query(BandwidthDaily).all()

            # Calculate totals for each period
            today_bytes = 0
            week_bytes = 0
            month_bytes = 0
            year_bytes = 0
            all_time_bytes = 0
            daily_history = []

            for record in all_records:
                bytes_val = record.bytes_transferred or 0
                all_time_bytes += bytes_val

                if record.date == today:
                    today_bytes = bytes_val

                if record.date >= week_ago:
                    week_bytes += bytes_val

                if record.date >= month_start:
                    month_bytes += bytes_val

                if record.date >= year_start:
                    year_bytes += bytes_val

            # Get last 7 days for chart
            week_records = session.query(BandwidthDaily).filter(
                BandwidthDaily.date >= week_ago
            ).order_by(BandwidthDaily.date.asc()).all()

            daily_history = [record.to_dict() for record in week_records]

            return {
                "today": today_bytes,
                "this_week": week_bytes,
                "this_month": month_bytes,
                "this_year": year_bytes,
                "all_time": all_time_bytes,
                "daily_history": daily_history,
            }

        finally:
            session.close()

    @staticmethod
    def purge_old_records(days: int = 90):
        """Remove records older than specified days."""
        cutoff = date.today() - timedelta(days=days)

        session = get_session()
        try:
            deleted = session.query(BandwidthDaily).filter(
                BandwidthDaily.date < cutoff
            ).delete()
            session.commit()
            if deleted > 0:
                logger.info(f"Purged {deleted} old bandwidth records")
        except Exception as e:
            logger.error(f"Failed to purge old records: {e}")
            session.rollback()
        finally:
            session.close()


# Global tracker instance
_tracker: Optional[BandwidthTracker] = None


def get_tracker() -> Optional[BandwidthTracker]:
    """Get the global tracker instance."""
    return _tracker


def set_tracker(tracker: BandwidthTracker):
    """Set the global tracker instance."""
    global _tracker
    _tracker = tracker
