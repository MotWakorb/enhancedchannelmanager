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
from models import BandwidthDaily, ChannelWatchStats, UniqueClientConnection, ChannelBandwidth

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
        self._ecm_channel_number_map: dict[int, str] = {}  # channel_number -> name mapping from ECM
        self._channel_map_refresh_interval = 300  # Refresh channel map every 5 minutes
        self._last_channel_map_refresh = 0.0
        # Enhanced stats tracking (v0.11.0)
        # Maps (channel_id, ip_address) -> connection_id in UniqueClientConnection table
        self._active_connections: dict[tuple[str, str], int] = {}
        # Track last known clients per channel for detecting new/disconnected clients
        self._last_channel_clients: dict[str, set[str]] = {}  # channel_id -> set of IPs

    async def start(self):
        """Start the background polling task."""
        if self._running:
            logger.warning("BandwidthTracker already running")
            return

        # Initialize channel maps on startup
        await self._initialize_channel_maps()

        # Clean up stale connections from previous runs
        self._cleanup_stale_connections()

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

    async def _initialize_channel_maps(self):
        """
        Initialize the ECM channel maps on startup.
        This fetches all channels from ECM and builds maps for UUID->name and channel_number->name lookups.
        """
        try:
            # Fetch all channels from ECM (paginated)
            uuid_map: dict[str, str] = {}
            number_map: dict[int, str] = {}
            page = 1
            page_size = 500
            while True:
                result = await self.client.get_channels(page=page, page_size=page_size)
                channels = result.get("results", [])
                for ch in channels:
                    uuid = ch.get("uuid")
                    name = ch.get("name")
                    channel_number = ch.get("channel_number")
                    if uuid and name:
                        uuid_map[uuid] = name
                    if channel_number is not None and name:
                        number_map[int(channel_number)] = name

                if not result.get("next"):
                    break
                page += 1
                if page > 20:
                    break

            self._ecm_channel_map = uuid_map
            self._ecm_channel_number_map = number_map
            self._last_channel_map_refresh = time.time()

            logger.info(f"Loaded channel maps: {len(uuid_map)} by UUID, {len(number_map)} by channel number")

        except Exception as e:
            logger.error(f"Failed to initialize channel maps: {e}")

    def _cleanup_stale_connections(self):
        """
        Clean up stale connections from previous runs.
        Marks any connections with null disconnected_at as completed.
        Also updates channel names that look like UUIDs if we can resolve them.
        """
        import re
        from models import ChannelPopularityScore, ChannelWatchStats
        uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
        truncated_pattern = re.compile(r'^Channel [0-9a-f]{8}\.\.\.$', re.I)

        def needs_name_fix(name: str) -> bool:
            """Check if a channel name looks like a UUID or truncated UUID."""
            return bool(uuid_pattern.match(name) or truncated_pattern.match(name))

        session = get_session()
        try:
            now = datetime.utcnow()

            # Find all connections with null disconnected_at (stale "watching" entries)
            stale_connections = session.query(UniqueClientConnection).filter(
                UniqueClientConnection.disconnected_at.is_(None)
            ).all()

            stale_count = 0
            name_updates = 0

            for conn in stale_connections:
                # Mark as disconnected - use connected_at + watch_seconds as approximate end time
                if conn.watch_seconds > 0:
                    conn.disconnected_at = conn.connected_at + timedelta(seconds=conn.watch_seconds)
                else:
                    conn.disconnected_at = conn.connected_at
                stale_count += 1

            # Fix channel names in UniqueClientConnection
            all_connections = session.query(UniqueClientConnection).all()
            for conn in all_connections:
                if needs_name_fix(conn.channel_name):
                    real_name = self._ecm_channel_map.get(conn.channel_id)
                    if real_name and real_name != conn.channel_name:
                        conn.channel_name = real_name
                        name_updates += 1

            # Fix channel names in ChannelPopularityScore
            popularity_scores = session.query(ChannelPopularityScore).all()
            for score in popularity_scores:
                if needs_name_fix(score.channel_name):
                    real_name = self._ecm_channel_map.get(score.channel_id)
                    if real_name and real_name != score.channel_name:
                        score.channel_name = real_name
                        name_updates += 1

            # Fix channel names in ChannelWatchStats
            watch_stats = session.query(ChannelWatchStats).all()
            for stats in watch_stats:
                if needs_name_fix(stats.channel_name):
                    real_name = self._ecm_channel_map.get(stats.channel_id)
                    if real_name and real_name != stats.channel_name:
                        stats.channel_name = real_name
                        name_updates += 1

            # Fix channel names in ChannelBandwidth
            bandwidth_records = session.query(ChannelBandwidth).all()
            for bw in bandwidth_records:
                if needs_name_fix(bw.channel_name):
                    real_name = self._ecm_channel_map.get(bw.channel_id)
                    if real_name and real_name != bw.channel_name:
                        bw.channel_name = real_name
                        name_updates += 1

            session.commit()
            if stale_count > 0 or name_updates > 0:
                logger.info(f"Cleaned up {stale_count} stale connections, updated {name_updates} channel names")
        except Exception as e:
            logger.error(f"Failed to cleanup stale connections: {e}")
            session.rollback()
        finally:
            session.close()

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
        """Refresh the ECM channel maps (UUID->name and channel_number->name)."""
        now = time.time()
        if now - self._last_channel_map_refresh < self._channel_map_refresh_interval:
            return

        try:
            # Fetch all channels from ECM (paginated)
            uuid_map: dict[str, str] = {}
            number_map: dict[int, str] = {}
            page = 1
            page_size = 500
            while True:
                result = await self.client.get_channels(page=page, page_size=page_size)
                channels = result.get("results", [])
                for ch in channels:
                    uuid = ch.get("uuid")
                    name = ch.get("name")
                    channel_number = ch.get("channel_number")
                    if uuid and name:
                        uuid_map[uuid] = name
                    if channel_number is not None and name:
                        # channel_number can be float or int, convert to int for lookup
                        number_map[int(channel_number)] = name

                # Check if there are more pages
                if not result.get("next"):
                    break
                page += 1
                # Safety limit
                if page > 20:
                    break

            self._ecm_channel_map = uuid_map
            self._ecm_channel_number_map = number_map
            self._last_channel_map_refresh = now
            logger.debug(f"Refreshed channel maps: {len(uuid_map)} by UUID, {len(number_map)} by number")
        except Exception as e:
            logger.debug(f"Failed to refresh channel map: {e}")

    async def _collect_stats(self):
        """Fetch stats from Dispatcharr and update daily totals."""
        try:
            stats = await self.client.get_channel_stats()
        except Exception as e:
            logger.warning(f"Failed to fetch stats from Dispatcharr: {e}")
            return

        channels = stats.get("channels", [])
        logger.debug(f"Collected stats for {len(channels)} active channels")

        # Calculate totals from all active channels
        total_bytes_delta = 0
        total_bytes_in_delta = 0  # Inbound from providers
        total_bytes_out_delta = 0  # Outbound to clients
        current_bitrate_in = 0  # Current inbound bitrate (bps)
        current_bitrate_out = 0  # Current outbound bitrate (bps)
        active_channels = len(channels)
        total_clients = 0

        current_bytes: dict[str, int] = {}
        current_active_channels: set[str] = set()
        current_channel_clients: dict[str, set[str]] = {}  # channel_id -> set of IPs
        newly_active_channels: list[dict] = []
        still_active_channels: list[dict] = []
        # Per-channel bandwidth tracking (v0.11.0)
        channel_bandwidth_updates: list[dict] = []

        for channel in channels:
            channel_id = str(channel.get("channel_id", ""))
            channel_number = channel.get("channel_number")
            # Get channel name - prefer ECM lookup by channel_number or UUID, fall back to Dispatcharr's response
            channel_name = None
            # Try channel_number lookup first (most reliable)
            if channel_number is not None:
                channel_name = self._ecm_channel_number_map.get(int(channel_number))
            # Fall back to UUID lookup
            if not channel_name:
                channel_name = self._ecm_channel_map.get(channel_id)
            # Fall back to Dispatcharr's response
            if not channel_name:
                channel_name = channel.get("channel_name") or channel.get("name")
            # Last resort: use partial UUID
            if not channel_name:
                channel_name = f"Channel {channel_id[:8]}..."

            bytes_now = channel.get("total_bytes", 0) or 0
            client_count = channel.get("client_count", 0) or 0
            avg_bitrate_kbps = channel.get("avg_bitrate_kbps", 0) or 0

            # Extract client IP addresses
            clients = channel.get("clients", [])
            client_ips = [c.get("ip_address") for c in clients if c.get("ip_address")]
            current_channel_clients[channel_id] = set(client_ips)

            current_bytes[channel_id] = bytes_now
            total_clients += client_count

            # Track current bitrate (for peak calculation)
            # Inbound: one stream per channel from provider
            # Outbound: stream × number of clients
            channel_bitrate_bps = int(avg_bitrate_kbps * 1000)  # Convert kbps to bps
            current_bitrate_in += channel_bitrate_bps  # One stream per channel
            current_bitrate_out += channel_bitrate_bps * max(client_count, 1)  # Stream × clients

            # Calculate per-channel byte delta
            channel_bytes_delta = 0
            if channel_id in self._last_bytes:
                prev_bytes = self._last_bytes[channel_id]
                if bytes_now > prev_bytes:
                    channel_bytes_delta = bytes_now - prev_bytes
                    total_bytes_delta += channel_bytes_delta
                    # Calculate in/out bytes
                    # Inbound = bytes from provider (one stream per channel)
                    total_bytes_in_delta += channel_bytes_delta
                    # Outbound = bytes fanned out to all clients (stream × clients)
                    total_bytes_out_delta += channel_bytes_delta * max(client_count, 1)

            # Track active channels for watch counting (use string ID for UUID support)
            if channel_id:
                current_active_channels.add(channel_id)
                self._channel_names[channel_id] = channel_name  # Cache name for stop events

                # Detect new and continuing client connections
                last_clients = self._last_channel_clients.get(channel_id, set())
                new_clients = set(client_ips) - last_clients
                continuing_clients = set(client_ips) & last_clients

                # Check if this channel just became active (wasn't in last poll)
                if channel_id not in self._last_active_channels:
                    newly_active_channels.append({
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "client_ips": client_ips,
                        "client_count": client_count,
                    })
                else:
                    # Channel was active last poll and still is - accumulate watch time
                    still_active_channels.append({
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "client_ips": client_ips,
                        "new_clients": list(new_clients),
                        "continuing_clients": list(continuing_clients),
                        "client_count": client_count,
                    })

                # Track per-channel bandwidth data
                if channel_bytes_delta > 0 or client_count > 0:
                    channel_bandwidth_updates.append({
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "bytes_delta": channel_bytes_delta,
                        "client_count": client_count,
                    })

        # Check for channels that stopped being watched
        stopped_channels = self._last_active_channels - current_active_channels
        if stopped_channels:
            self._log_watch_stop_events(stopped_channels)
            self._close_client_connections(stopped_channels)

        # Update last bytes tracking
        self._last_bytes = current_bytes
        self._last_active_channels = current_active_channels
        self._last_channel_clients = current_channel_clients

        # Only record if there's actual data transfer
        if total_bytes_delta > 0 or active_channels > 0:
            self._update_daily_record(
                total_bytes_delta,
                active_channels,
                total_clients,
                bytes_in_delta=total_bytes_in_delta,
                bytes_out_delta=total_bytes_out_delta,
                current_bitrate_in=current_bitrate_in,
                current_bitrate_out=current_bitrate_out,
            )
            if total_bytes_delta > 0:
                bytes_mb = total_bytes_delta / (1024 * 1024)
                logger.debug(f"Bandwidth delta: {bytes_mb:.2f} MB (in: {total_bytes_in_delta / (1024*1024):.2f}, out: {total_bytes_out_delta / (1024*1024):.2f}), active channels: {active_channels}, clients: {total_clients}")

        # Update per-channel bandwidth (v0.11.0)
        if channel_bandwidth_updates:
            self._update_channel_bandwidth(channel_bandwidth_updates)

        # Update watch counts for newly active channels (and log start events)
        if newly_active_channels:
            logger.info(f"{len(newly_active_channels)} channel(s) started streaming")
            self._update_watch_counts(newly_active_channels)

        # Accumulate watch time for still-active channels
        if still_active_channels:
            self._update_watch_time(still_active_channels)

        # Log stopped channels
        if stopped_channels:
            logger.info(f"{len(stopped_channels)} channel(s) stopped streaming")

    def _update_daily_record(
        self,
        bytes_delta: int,
        active_channels: int,
        total_clients: int,
        bytes_in_delta: int = 0,
        bytes_out_delta: int = 0,
        current_bitrate_in: int = 0,
        current_bitrate_out: int = 0,
    ):
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
                    bytes_in=0,
                    bytes_out=0,
                    peak_channels=0,
                    peak_clients=0,
                    peak_bitrate_in=0,
                    peak_bitrate_out=0,
                )
                session.add(record)

            # Update totals
            record.bytes_transferred += bytes_delta
            record.bytes_in += bytes_in_delta
            record.bytes_out += bytes_out_delta
            record.peak_channels = max(record.peak_channels, active_channels)
            record.peak_clients = max(record.peak_clients, total_clients)
            # Update peak bitrates (track highest seen during the day)
            record.peak_bitrate_in = max(record.peak_bitrate_in, current_bitrate_in)
            record.peak_bitrate_out = max(record.peak_bitrate_out, current_bitrate_out)

            session.commit()
        except Exception as e:
            logger.error(f"Failed to update bandwidth record: {e}")
            session.rollback()
        finally:
            session.close()

    def _update_channel_bandwidth(self, updates: list[dict]):
        """Update per-channel bandwidth records (v0.11.0)."""
        today = get_current_date()
        session = get_session()
        try:
            for upd in updates:
                channel_id = upd["channel_id"]
                channel_name = upd["channel_name"]
                bytes_delta = upd["bytes_delta"]
                client_count = upd["client_count"]

                # Get or create today's record for this channel
                record = session.query(ChannelBandwidth).filter(
                    ChannelBandwidth.channel_id == channel_id,
                    ChannelBandwidth.date == today
                ).first()

                if record is None:
                    record = ChannelBandwidth(
                        channel_id=channel_id,
                        channel_name=channel_name,
                        date=today,
                        bytes_transferred=0,
                        peak_clients=0,
                        total_watch_seconds=0,
                        connection_count=0,
                    )
                    session.add(record)

                # Update record
                record.bytes_transferred += bytes_delta
                record.peak_clients = max(record.peak_clients, client_count)
                record.total_watch_seconds += self.poll_interval * client_count  # Each client adds poll_interval seconds
                record.channel_name = channel_name  # Update name in case it changed

            session.commit()
            logger.debug(f"Updated channel bandwidth for {len(updates)} channels")
        except Exception as e:
            logger.error(f"Failed to update channel bandwidth: {e}")
            session.rollback()
        finally:
            session.close()

    def _update_watch_counts(self, channels: list[dict]):
        """Update watch counts for channels that just became active and log journal events."""
        from journal import log_entry

        session = get_session()
        today = get_current_date()
        try:
            now = datetime.now(get_user_timezone())
            for ch in channels:
                channel_id = ch["channel_id"]
                channel_name = ch["channel_name"]
                client_ips = ch.get("client_ips", [])
                client_count = ch.get("client_count", len(client_ips))

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

                # Create UniqueClientConnection records for each client (v0.11.0)
                for ip in client_ips:
                    connection = UniqueClientConnection(
                        ip_address=ip,
                        channel_id=channel_id,
                        channel_name=channel_name,
                        date=today,
                        connected_at=now,
                        watch_seconds=0,
                    )
                    session.add(connection)
                    session.flush()  # Get the ID
                    # Track active connection
                    self._active_connections[(channel_id, ip)] = connection.id

                # Update ChannelBandwidth connection count
                bw_record = session.query(ChannelBandwidth).filter(
                    ChannelBandwidth.channel_id == channel_id,
                    ChannelBandwidth.date == today
                ).first()
                if bw_record:
                    bw_record.connection_count += len(client_ips)

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
        today = get_current_date()
        try:
            now = datetime.now(get_user_timezone())
            for ch in channels:
                channel_id = ch["channel_id"]
                channel_name = ch["channel_name"]
                new_clients = ch.get("new_clients", [])
                continuing_clients = ch.get("continuing_clients", [])
                client_count = ch.get("client_count", 0)

                record = session.query(ChannelWatchStats).filter(
                    ChannelWatchStats.channel_id == channel_id
                ).first()

                if record:
                    # Add poll interval seconds to watch time
                    record.total_watch_seconds += self.poll_interval
                    record.last_watched = now
                    record.channel_name = channel_name

                # Handle new clients that joined mid-stream (v0.11.0)
                for ip in new_clients:
                    connection = UniqueClientConnection(
                        ip_address=ip,
                        channel_id=channel_id,
                        channel_name=channel_name,
                        date=today,
                        connected_at=now,
                        watch_seconds=0,
                    )
                    session.add(connection)
                    session.flush()
                    self._active_connections[(channel_id, ip)] = connection.id

                    # Update connection count in ChannelBandwidth
                    bw_record = session.query(ChannelBandwidth).filter(
                        ChannelBandwidth.channel_id == channel_id,
                        ChannelBandwidth.date == today
                    ).first()
                    if bw_record:
                        bw_record.connection_count += 1

                # Update watch_seconds for continuing connections
                for ip in continuing_clients:
                    conn_key = (channel_id, ip)
                    if conn_key in self._active_connections:
                        conn_id = self._active_connections[conn_key]
                        connection = session.query(UniqueClientConnection).filter(
                            UniqueClientConnection.id == conn_id
                        ).first()
                        if connection:
                            connection.watch_seconds += self.poll_interval

                # Handle clients that disconnected from this still-active channel
                last_clients = self._last_channel_clients.get(channel_id, set())
                current_clients = set(ch.get("client_ips", []))
                disconnected_clients = last_clients - current_clients
                for ip in disconnected_clients:
                    conn_key = (channel_id, ip)
                    if conn_key in self._active_connections:
                        conn_id = self._active_connections.pop(conn_key)
                        connection = session.query(UniqueClientConnection).filter(
                            UniqueClientConnection.id == conn_id
                        ).first()
                        if connection:
                            connection.disconnected_at = now

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

    def _close_client_connections(self, channel_ids: set[str]):
        """Mark all client connections as disconnected when channels stop (v0.11.0)."""
        session = get_session()
        try:
            now = datetime.now(get_user_timezone())
            closed_count = 0

            for channel_id in channel_ids:
                # Find all active connections for this channel
                keys_to_remove = [
                    key for key in self._active_connections
                    if key[0] == channel_id
                ]

                for key in keys_to_remove:
                    conn_id = self._active_connections.pop(key)
                    connection = session.query(UniqueClientConnection).filter(
                        UniqueClientConnection.id == conn_id
                    ).first()
                    if connection and connection.disconnected_at is None:
                        connection.disconnected_at = now
                        closed_count += 1

            session.commit()
            if closed_count > 0:
                logger.debug(f"Closed {closed_count} client connections for stopped channels")
        except Exception as e:
            logger.error(f"Failed to close client connections: {e}")
            session.rollback()
        finally:
            session.close()

    @staticmethod
    def get_bandwidth_summary() -> dict:
        """
        Get bandwidth summary for all time periods (using user's timezone).

        Returns:
            dict with today, this_week, this_month, this_year, all_time bytes,
            in/out breakdowns, peak bitrates, and daily_history for last 7 days
        """
        from sqlalchemy import func

        today = get_current_date()
        week_ago = today - timedelta(days=7)
        month_start = today.replace(day=1)
        year_start = today.replace(month=1, day=1)

        session = get_session()
        try:
            # Use SQL aggregation for efficient calculations
            # Today's bytes (total, in, out)
            today_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_in), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_out), 0),
                func.coalesce(func.max(BandwidthDaily.peak_bitrate_in), 0),
                func.coalesce(func.max(BandwidthDaily.peak_bitrate_out), 0),
            ).filter(BandwidthDaily.date == today).first()
            today_bytes = today_result[0] or 0
            today_bytes_in = today_result[1] or 0
            today_bytes_out = today_result[2] or 0
            today_peak_bitrate_in = today_result[3] or 0
            today_peak_bitrate_out = today_result[4] or 0

            # This week's bytes
            week_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_in), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_out), 0),
                func.coalesce(func.max(BandwidthDaily.peak_bitrate_in), 0),
                func.coalesce(func.max(BandwidthDaily.peak_bitrate_out), 0),
            ).filter(BandwidthDaily.date >= week_ago).first()
            week_bytes = week_result[0] or 0
            week_bytes_in = week_result[1] or 0
            week_bytes_out = week_result[2] or 0
            week_peak_bitrate_in = week_result[3] or 0
            week_peak_bitrate_out = week_result[4] or 0

            # This month's bytes
            month_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_in), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_out), 0),
            ).filter(BandwidthDaily.date >= month_start).first()
            month_bytes = month_result[0] or 0
            month_bytes_in = month_result[1] or 0
            month_bytes_out = month_result[2] or 0

            # This year's bytes
            year_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_in), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_out), 0),
            ).filter(BandwidthDaily.date >= year_start).first()
            year_bytes = year_result[0] or 0
            year_bytes_in = year_result[1] or 0
            year_bytes_out = year_result[2] or 0

            # All time bytes
            all_time_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_in), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_out), 0),
            ).first()
            all_time_bytes = all_time_result[0] or 0
            all_time_bytes_in = all_time_result[1] or 0
            all_time_bytes_out = all_time_result[2] or 0

            # Get last 7 days for chart
            week_records = session.query(BandwidthDaily).filter(
                BandwidthDaily.date >= week_ago
            ).order_by(BandwidthDaily.date.asc()).all()

            daily_history = [record.to_dict() for record in week_records]

            return {
                # Legacy fields (backwards compatible)
                "today": today_bytes,
                "this_week": week_bytes,
                "this_month": month_bytes,
                "this_year": year_bytes,
                "all_time": all_time_bytes,
                # Inbound/Outbound breakdown
                "today_in": today_bytes_in,
                "today_out": today_bytes_out,
                "week_in": week_bytes_in,
                "week_out": week_bytes_out,
                "month_in": month_bytes_in,
                "month_out": month_bytes_out,
                "year_in": year_bytes_in,
                "year_out": year_bytes_out,
                "all_time_in": all_time_bytes_in,
                "all_time_out": all_time_bytes_out,
                # Peak bitrates (today and week)
                "today_peak_bitrate_in": today_peak_bitrate_in,
                "today_peak_bitrate_out": today_peak_bitrate_out,
                "week_peak_bitrate_in": week_peak_bitrate_in,
                "week_peak_bitrate_out": week_peak_bitrate_out,
                # Daily history for charts
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

    # =========================================================================
    # Enhanced Statistics Query Methods (v0.11.0)
    # =========================================================================

    @staticmethod
    def get_unique_viewers_summary(days: int = 7) -> dict:
        """
        Get unique viewer statistics for the specified period.

        Args:
            days: Number of days to look back (default 7)

        Returns:
            dict with unique viewer counts and breakdown
        """
        from sqlalchemy import func, distinct

        cutoff = get_current_date() - timedelta(days=days)
        today = get_current_date()

        session = get_session()
        try:
            # Total unique IPs in period
            total_unique = session.query(
                func.count(distinct(UniqueClientConnection.ip_address))
            ).filter(UniqueClientConnection.date >= cutoff).scalar() or 0

            # Unique IPs today
            today_unique = session.query(
                func.count(distinct(UniqueClientConnection.ip_address))
            ).filter(UniqueClientConnection.date == today).scalar() or 0

            # Total connections in period
            total_connections = session.query(
                func.count(UniqueClientConnection.id)
            ).filter(UniqueClientConnection.date >= cutoff).scalar() or 0

            # Average watch time per connection
            avg_watch_time = session.query(
                func.avg(UniqueClientConnection.watch_seconds)
            ).filter(
                UniqueClientConnection.date >= cutoff,
                UniqueClientConnection.watch_seconds > 0
            ).scalar() or 0

            # Top viewers by connection count
            top_viewers = session.query(
                UniqueClientConnection.ip_address,
                func.count(UniqueClientConnection.id).label("connection_count"),
                func.sum(UniqueClientConnection.watch_seconds).label("total_watch_seconds")
            ).filter(
                UniqueClientConnection.date >= cutoff
            ).group_by(
                UniqueClientConnection.ip_address
            ).order_by(
                func.count(UniqueClientConnection.id).desc()
            ).limit(10).all()

            # Daily unique viewer counts for chart
            daily_unique = session.query(
                UniqueClientConnection.date,
                func.count(distinct(UniqueClientConnection.ip_address)).label("unique_count")
            ).filter(
                UniqueClientConnection.date >= cutoff
            ).group_by(
                UniqueClientConnection.date
            ).order_by(
                UniqueClientConnection.date.asc()
            ).all()

            return {
                "period_days": days,
                "total_unique_viewers": total_unique,
                "today_unique_viewers": today_unique,
                "total_connections": total_connections,
                "avg_watch_seconds": round(avg_watch_time, 1),
                "top_viewers": [
                    {
                        "ip_address": v.ip_address,
                        "connection_count": v.connection_count,
                        "total_watch_seconds": v.total_watch_seconds or 0,
                    }
                    for v in top_viewers
                ],
                "daily_unique": [
                    {"date": d.date.isoformat(), "unique_count": d.unique_count}
                    for d in daily_unique
                ],
            }
        finally:
            session.close()

    @staticmethod
    def get_channel_bandwidth_stats(days: int = 7, limit: int = 20, sort_by: str = "bytes") -> list[dict]:
        """
        Get per-channel bandwidth statistics.

        Args:
            days: Number of days to aggregate (default 7)
            limit: Maximum channels to return (default 20)
            sort_by: "bytes", "connections", or "watch_time" (default "bytes")

        Returns:
            List of channel bandwidth stats, sorted by specified metric
        """
        from sqlalchemy import func

        cutoff = get_current_date() - timedelta(days=days)

        session = get_session()
        try:
            # Aggregate per-channel data
            query = session.query(
                ChannelBandwidth.channel_id,
                ChannelBandwidth.channel_name,
                func.sum(ChannelBandwidth.bytes_transferred).label("total_bytes"),
                func.sum(ChannelBandwidth.connection_count).label("total_connections"),
                func.sum(ChannelBandwidth.total_watch_seconds).label("total_watch_seconds"),
                func.max(ChannelBandwidth.peak_clients).label("peak_clients"),
            ).filter(
                ChannelBandwidth.date >= cutoff
            ).group_by(
                ChannelBandwidth.channel_id,
                ChannelBandwidth.channel_name
            )

            # Apply sorting
            if sort_by == "connections":
                query = query.order_by(func.sum(ChannelBandwidth.connection_count).desc())
            elif sort_by == "watch_time":
                query = query.order_by(func.sum(ChannelBandwidth.total_watch_seconds).desc())
            else:  # bytes
                query = query.order_by(func.sum(ChannelBandwidth.bytes_transferred).desc())

            results = query.limit(limit).all()

            return [
                {
                    "channel_id": r.channel_id,
                    "channel_name": r.channel_name,
                    "total_bytes": r.total_bytes or 0,
                    "total_connections": r.total_connections or 0,
                    "total_watch_seconds": r.total_watch_seconds or 0,
                    "peak_clients": r.peak_clients or 0,
                }
                for r in results
            ]
        finally:
            session.close()

    @staticmethod
    def get_unique_viewers_by_channel(days: int = 7, limit: int = 20) -> list[dict]:
        """
        Get unique viewer counts per channel.

        Args:
            days: Number of days to look back (default 7)
            limit: Maximum channels to return (default 20)

        Returns:
            List of channels with their unique viewer counts
        """
        from sqlalchemy import func, distinct

        cutoff = get_current_date() - timedelta(days=days)

        session = get_session()
        try:
            results = session.query(
                UniqueClientConnection.channel_id,
                UniqueClientConnection.channel_name,
                func.count(distinct(UniqueClientConnection.ip_address)).label("unique_viewers"),
                func.count(UniqueClientConnection.id).label("total_connections"),
                func.sum(UniqueClientConnection.watch_seconds).label("total_watch_seconds"),
            ).filter(
                UniqueClientConnection.date >= cutoff
            ).group_by(
                UniqueClientConnection.channel_id,
                UniqueClientConnection.channel_name
            ).order_by(
                func.count(distinct(UniqueClientConnection.ip_address)).desc()
            ).limit(limit).all()

            return [
                {
                    "channel_id": r.channel_id,
                    "channel_name": r.channel_name,
                    "unique_viewers": r.unique_viewers,
                    "total_connections": r.total_connections,
                    "total_watch_seconds": r.total_watch_seconds or 0,
                }
                for r in results
            ]
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
