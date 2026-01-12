"""
Background bandwidth tracking service.
Polls Dispatcharr stats periodically and accumulates bandwidth data.
"""
import asyncio
import logging
import time
from datetime import datetime, date, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from database import get_session
from models import BandwidthDaily, ChannelWatchStats

logger = logging.getLogger(__name__)


def get_user_timezone() -> timezone:
    """Get the user's configured timezone, or UTC if not set/invalid."""
    try:
        from config import get_settings
        settings = get_settings()
        if settings.user_timezone:
            return ZoneInfo(settings.user_timezone)
    except Exception as e:
        logger.debug(f"Could not get user timezone: {e}")
    return timezone.utc


def get_current_date() -> date:
    """Get current date in user's timezone."""
    tz = get_user_timezone()
    return datetime.now(tz).date()

# Default polling interval in seconds (used if not configured)
DEFAULT_POLL_INTERVAL = 10


class BandwidthTracker:
    """
    Background service that tracks bandwidth usage over time.
    Polls Dispatcharr's stats endpoint and stores daily aggregates.
    """

    def __init__(self, client, poll_interval: int = DEFAULT_POLL_INTERVAL):
        """
        Initialize the tracker.

        Args:
            client: DispatcharrClient instance for API calls
            poll_interval: Seconds between polls (default 10)
        """
        self.client = client
        self.poll_interval = poll_interval
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_bytes: dict[str, int] = {}  # Track per-channel bytes to compute deltas
        self._last_active_channels: set[str] = set()  # Track which channels were active last poll (UUIDs)
        self._channel_names: dict[str, str] = {}  # Cache channel names for stop events
        self._ecm_channel_map: dict[str, str] = {}  # UUID -> name mapping from ECM channels
        self._channel_map_refresh_interval = 300  # Refresh channel map every 5 minutes
        self._last_channel_map_refresh = 0.0

    async def start(self):
        """Start the background polling task."""
        if self._running:
            logger.warning("BandwidthTracker already running")
            return

        # Run one-time migration to fix UUID channel names in database
        await self._migrate_uuid_channel_names()

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"BandwidthTracker started (polling every {self.poll_interval}s)")

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

    async def _migrate_uuid_channel_names(self):
        """
        One-time migration to fix channel names that were stored as UUIDs.
        Fetches channel data from ECM and updates records where the name looks like a UUID.
        """
        import re
        from models import JournalEntry

        # UUID pattern: 8-4-4-4-12 hex characters
        uuid_pattern = re.compile(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
            re.IGNORECASE
        )

        try:
            # Fetch channel map from ECM
            channel_map: dict[str, str] = {}
            page = 1
            page_size = 500
            while True:
                result = await self.client.get_channels(page=page, page_size=page_size)
                channels = result.get("results", [])
                for ch in channels:
                    uuid = ch.get("uuid")
                    name = ch.get("name")
                    if uuid and name:
                        channel_map[uuid] = name

                if not result.get("next"):
                    break
                page += 1
                if page > 20:
                    break

            if not channel_map:
                logger.debug("No channels found for UUID migration")
                return

            # Store for later use
            self._ecm_channel_map = channel_map
            self._last_channel_map_refresh = time.time()

            session = get_session()
            try:
                # Fix channel_watch_stats records with UUID names
                watch_stats = session.query(ChannelWatchStats).all()
                watch_fixed = 0
                for record in watch_stats:
                    # Check if channel_name looks like a UUID
                    if uuid_pattern.match(record.channel_name):
                        # Try to look up the real name
                        real_name = channel_map.get(record.channel_id)
                        if real_name:
                            record.channel_name = real_name
                            watch_fixed += 1

                if watch_fixed > 0:
                    session.commit()
                    logger.info(f"Migration: Fixed {watch_fixed} channel watch stats records with UUID names")

                # Fix journal entries with UUID entity_name (for watch category)
                journal_entries = session.query(JournalEntry).filter(
                    JournalEntry.category == "watch"
                ).all()
                journal_fixed = 0
                for entry in journal_entries:
                    # Check if entity_name looks like a UUID
                    if uuid_pattern.match(entry.entity_name):
                        old_name = entry.entity_name
                        # Try to look up from after_value which has channel_id
                        if entry.after_value:
                            import json
                            try:
                                after = json.loads(entry.after_value)
                                channel_id = after.get("channel_id")
                                if channel_id:
                                    real_name = channel_map.get(channel_id)
                                    if real_name:
                                        entry.entity_name = real_name
                                        # Also fix description if it contains the UUID
                                        if entry.description and old_name in entry.description:
                                            entry.description = entry.description.replace(
                                                old_name, real_name
                                            )
                                        journal_fixed += 1
                            except json.JSONDecodeError:
                                pass

                if journal_fixed > 0:
                    session.commit()
                    logger.info(f"Migration: Fixed {journal_fixed} journal entries with UUID entity names")

            finally:
                session.close()

        except Exception as e:
            logger.error(f"UUID channel name migration failed: {e}")

    async def _poll_loop(self):
        """Main polling loop - runs until stopped."""
        while self._running:
            try:
                # Refresh channel name map periodically
                await self._maybe_refresh_channel_map()
                await self._collect_stats()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"BandwidthTracker error: {e}")

            # Wait for next poll interval
            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break

    async def _maybe_refresh_channel_map(self):
        """Refresh the ECM channel UUID->name map if needed."""
        now = time.time()
        if now - self._last_channel_map_refresh < self._channel_map_refresh_interval:
            return

        try:
            # Fetch all channels from ECM (paginated)
            new_map: dict[str, str] = {}
            page = 1
            page_size = 500
            while True:
                result = await self.client.get_channels(page=page, page_size=page_size)
                channels = result.get("results", [])
                for ch in channels:
                    uuid = ch.get("uuid")
                    name = ch.get("name")
                    if uuid and name:
                        new_map[uuid] = name

                # Check if there are more pages
                if not result.get("next"):
                    break
                page += 1
                # Safety limit
                if page > 20:
                    break

            self._ecm_channel_map = new_map
            self._last_channel_map_refresh = now
            logger.debug(f"Refreshed channel map with {len(new_map)} channels")
        except Exception as e:
            logger.debug(f"Failed to refresh channel map: {e}")

    async def _collect_stats(self):
        """Fetch stats from Dispatcharr and update daily totals."""
        try:
            stats = await self.client.get_channel_stats()
        except Exception as e:
            logger.debug(f"Failed to fetch stats: {e}")
            return

        channels = stats.get("channels", [])

        # Calculate totals from all active channels
        total_bytes_delta = 0
        active_channels = len(channels)
        total_clients = 0

        current_bytes: dict[str, int] = {}
        current_active_channels: set[str] = set()
        newly_active_channels: list[dict] = []
        still_active_channels: list[dict] = []

        for channel in channels:
            channel_id = str(channel.get("channel_id", ""))
            # Get channel name - prefer ECM lookup by UUID, fall back to Dispatcharr's response
            channel_name = (
                self._ecm_channel_map.get(channel_id)
                or channel.get("channel_name")
                or channel.get("name")
                or f"Channel {channel_id[:8]}..."
            )
            bytes_now = channel.get("total_bytes", 0) or 0
            client_count = channel.get("client_count", 0) or 0

            # Extract client IP addresses
            clients = channel.get("clients", [])
            client_ips = [c.get("ip_address") for c in clients if c.get("ip_address")]

            current_bytes[channel_id] = bytes_now
            total_clients += client_count

            # Track active channels for watch counting (use string ID for UUID support)
            if channel_id:
                current_active_channels.add(channel_id)
                self._channel_names[channel_id] = channel_name  # Cache name for stop events

                # Check if this channel just became active (wasn't in last poll)
                if channel_id not in self._last_active_channels:
                    newly_active_channels.append({
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "client_ips": client_ips,
                    })
                else:
                    # Channel was active last poll and still is - accumulate watch time
                    still_active_channels.append({
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                    })

            # Calculate delta if we have previous value for this channel
            if channel_id in self._last_bytes:
                prev_bytes = self._last_bytes[channel_id]
                if bytes_now > prev_bytes:
                    total_bytes_delta += bytes_now - prev_bytes

        # Check for channels that stopped being watched
        stopped_channels = self._last_active_channels - current_active_channels
        if stopped_channels:
            self._log_watch_stop_events(stopped_channels)

        # Update last bytes tracking
        self._last_bytes = current_bytes
        self._last_active_channels = current_active_channels

        # Only record if there's actual data transfer
        if total_bytes_delta > 0 or active_channels > 0:
            self._update_daily_record(total_bytes_delta, active_channels, total_clients)

        # Update watch counts for newly active channels (and log start events)
        if newly_active_channels:
            self._update_watch_counts(newly_active_channels)

        # Accumulate watch time for still-active channels
        if still_active_channels:
            self._update_watch_time(still_active_channels)

    def _update_daily_record(self, bytes_delta: int, active_channels: int, total_clients: int):
        """Update today's bandwidth record in the database (using user's timezone)."""
        today = get_current_date()

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

    def _update_watch_counts(self, channels: list[dict]):
        """Update watch counts for channels that just became active and log journal events."""
        from journal import log_entry

        session = get_session()
        try:
            now = datetime.now(get_user_timezone())
            for ch in channels:
                channel_id = ch["channel_id"]
                channel_name = ch["channel_name"]
                client_ips = ch.get("client_ips", [])

                # Get or create watch stats record
                record = session.query(ChannelWatchStats).filter(
                    ChannelWatchStats.channel_id == channel_id
                ).first()

                if record is None:
                    record = ChannelWatchStats(
                        channel_id=channel_id,
                        channel_name=channel_name,
                        watch_count=0,
                        total_watch_seconds=0,
                    )
                    session.add(record)

                # Update record
                record.watch_count += 1
                record.last_watched = now
                # Update channel name in case it changed
                record.channel_name = channel_name

                # Build description with IP addresses
                ip_str = ", ".join(client_ips) if client_ips else "unknown"
                description = f"Started watching {channel_name} from {ip_str}"

                # Log journal entry for watch start
                log_entry(
                    category="watch",
                    action_type="start",
                    entity_name=channel_name,
                    description=description,
                    user_initiated=False,
                    after_value={
                        "channel_id": channel_id,
                        "watch_count": record.watch_count,
                        "client_ips": client_ips,
                    },
                )

            session.commit()
            logger.debug(f"Updated watch counts for {len(channels)} channels")
        except Exception as e:
            logger.error(f"Failed to update watch counts: {e}")
            session.rollback()
        finally:
            session.close()

    def _update_watch_time(self, channels: list[dict]):
        """Accumulate watch time for channels that are still active."""
        session = get_session()
        try:
            now = datetime.now(get_user_timezone())
            for ch in channels:
                channel_id = ch["channel_id"]
                channel_name = ch["channel_name"]

                record = session.query(ChannelWatchStats).filter(
                    ChannelWatchStats.channel_id == channel_id
                ).first()

                if record:
                    # Add poll interval seconds to watch time
                    record.total_watch_seconds += self.poll_interval
                    record.last_watched = now
                    record.channel_name = channel_name

            session.commit()
        except Exception as e:
            logger.error(f"Failed to update watch time: {e}")
            session.rollback()
        finally:
            session.close()

    def _log_watch_stop_events(self, channel_ids: set[str]):
        """Log journal entries when channels stop being watched."""
        from journal import log_entry

        session = get_session()
        try:
            for channel_id in channel_ids:
                # Get channel name - prefer ECM map, then cache, then database
                channel_name = (
                    self._ecm_channel_map.get(channel_id)
                    or self._channel_names.get(channel_id)
                )
                if not channel_name:
                    record = session.query(ChannelWatchStats).filter(
                        ChannelWatchStats.channel_id == channel_id
                    ).first()
                    channel_name = record.channel_name if record else f"Channel {channel_id[:8]}..."

                # Get current stats for the log
                record = session.query(ChannelWatchStats).filter(
                    ChannelWatchStats.channel_id == channel_id
                ).first()

                watch_time = record.total_watch_seconds if record else 0

                # Log journal entry for watch stop
                log_entry(
                    category="watch",
                    action_type="stop",
                    entity_name=channel_name,
                    description=f"Stopped watching {channel_name}",
                    user_initiated=False,
                    after_value={
                        "channel_id": channel_id,
                        "total_watch_seconds": watch_time,
                    },
                )

            logger.debug(f"Logged watch stop events for {len(channel_ids)} channels")
        except Exception as e:
            logger.error(f"Failed to log watch stop events: {e}")
        finally:
            session.close()

    @staticmethod
    def get_bandwidth_summary() -> dict:
        """
        Get bandwidth summary for all time periods (using user's timezone).

        Returns:
            dict with today, this_week, this_month, this_year, all_time bytes,
            and daily_history for last 7 days
        """
        from sqlalchemy import func

        today = get_current_date()
        week_ago = today - timedelta(days=7)
        month_start = today.replace(day=1)
        year_start = today.replace(month=1, day=1)

        session = get_session()
        try:
            # Use SQL aggregation for efficient calculations
            # Today's bytes
            today_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0)
            ).filter(BandwidthDaily.date == today).scalar()
            today_bytes = today_result or 0

            # This week's bytes
            week_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0)
            ).filter(BandwidthDaily.date >= week_ago).scalar()
            week_bytes = week_result or 0

            # This month's bytes
            month_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0)
            ).filter(BandwidthDaily.date >= month_start).scalar()
            month_bytes = month_result or 0

            # This year's bytes
            year_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0)
            ).filter(BandwidthDaily.date >= year_start).scalar()
            year_bytes = year_result or 0

            # All time bytes
            all_time_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0)
            ).scalar()
            all_time_bytes = all_time_result or 0

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
    def get_top_watched_channels(limit: int = 10, sort_by: str = "views") -> list[dict]:
        """
        Get the top watched channels by watch count or watch time.

        Args:
            limit: Maximum number of channels to return (default 10)
            sort_by: "views" for watch count, "time" for total watch time (default "views")

        Returns:
            List of channel watch stats dicts, ordered by selected metric desc
        """
        session = get_session()
        try:
            if sort_by == "time":
                records = session.query(ChannelWatchStats).order_by(
                    ChannelWatchStats.total_watch_seconds.desc()
                ).limit(limit).all()
            else:
                records = session.query(ChannelWatchStats).order_by(
                    ChannelWatchStats.watch_count.desc()
                ).limit(limit).all()

            return [record.to_dict() for record in records]
        finally:
            session.close()

    @staticmethod
    def purge_old_records(days: int = 90):
        """Remove records older than specified days (using user's timezone)."""
        cutoff = get_current_date() - timedelta(days=days)

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
