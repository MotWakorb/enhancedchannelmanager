"""
Background bandwidth tracking service.
Polls Dispatcharr stats periodically and accumulates bandwidth data.

v0.17.0 Stats v2 (bd-skqln.3 step (d)):
``_collect_stats`` writes one row per active viewing connection into
``session_telemetry`` unconditionally. The legacy ``channel_watch_stats``
write inside this module is gone — its readers (popularity calculator,
top-watched API) now derive their inputs from ``session_telemetry`` and
``unique_client_connections``. The transitional
``ECM_SESSION_TELEMETRY_WRITE_ENABLED`` kill-switch is retired; its job
was the (a)→(d) transition gate and there is no off-state once legacy
writes are gone.
"""
import asyncio
import logging
import os
import re
import time
from collections import OrderedDict
from datetime import datetime, date, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func

from database import get_session
from models import (
    BandwidthDaily,
    ChannelBandwidth,
    SessionTelemetry,
    UniqueClientConnection,
)

logger = logging.getLogger(__name__)


# Dispatcharr stream-URL convention: the last path segment before ``.ts`` is
# the integer Dispatcharr stream row id — the same value that ``stream_id``
# would have carried on the ``/proxy/ts/status`` payload had it been
# populated. The resolver falls back to this when ``stream_id`` is missing
# (bd-kbgey — 214 of 235 dev polls observed with stream_id=None on active
# channels). One capture group, defensive against query-string suffixes
# like ``.ts?session=abc123`` so the fallback survives Dispatcharr URL
# annotation conventions that the proxy may add for transcoding hints.
_STREAM_ID_URL_PATTERN = re.compile(r"/(\d+)\.ts(?:\?|$)")


def _extract_stream_id_from_url(url: Optional[str]) -> Optional[int]:
    """Pull the Dispatcharr stream row id from a stream URL.

    Returns the integer stream id if the URL matches the
    ``.../<id>.ts[?...]`` convention, otherwise ``None``. Defensive
    against malformed input (non-string, empty, no path, no integer
    segment); the caller treats ``None`` the same as a missing
    ``stream_id`` field and falls through to the NULL-write path.

    Validates the captured value parses as a positive int — guards
    against pathological inputs like ``/00000.ts`` mapping to id 0.
    """
    if not url or not isinstance(url, str):
        return None
    match = _STREAM_ID_URL_PATTERN.search(url)
    if match is None:
        return None
    try:
        parsed = int(match.group(1))
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _telemetry_opt_out_enabled() -> bool:
    """Parse ``ECM_STATS_TELEMETRY_OPT_OUT``. Default-OFF (writes happen).

    Operator-facing opt-out for the Stats v2 data path
    (bead ``enhancedchannelmanager-tp1pd``). When the env var is set to
    a truthy value, ``_collect_stats`` short-circuits AFTER the legacy
    sibling writes (``ChannelBandwidth``, ``BandwidthDaily``,
    ``UniqueClientConnection``) and BEFORE the Stats v2 path
    (``_resolve_provider_ids`` + ``_collect_buffer_events`` +
    ``_write_session_telemetry``). Net effect:

    * Zero rows land in ``session_telemetry``.
    * No Dispatcharr ``get_streams_by_ids`` round-trip per poll.
    * No Dispatcharr ``get_system_events`` round-trip per poll.
    * Legacy stats (existing since v0.11.0) continue to record.

    Read per-poll (not cached at import) so an operator who flips the
    env var at runtime (export + container restart) sees the new value
    on the next poll cycle without code awareness of when the flip
    happened. The string-compare cost is microseconds; the latency-
    sensitive surface is the network round-trip the flag elides.

    Truthy-value enum mirrors ``_confusables_fold_enabled`` in
    ``normalization_engine.py`` — ``true``, ``1``, ``yes``, ``on``
    (case-insensitive, whitespace-tolerant). Everything else (including
    empty string and unset) is OFF.
    """
    raw = os.environ.get("ECM_STATS_TELEMETRY_OPT_OUT", "false")
    return raw.strip().lower() in {"true", "1", "yes", "on"}


def get_user_timezone() -> timezone:
    """Get the user's configured timezone, or UTC if not set/invalid."""
    try:
        from config import get_settings
        settings = get_settings()
        if settings.user_timezone:
            return ZoneInfo(settings.user_timezone)
    except Exception as e:
        logger.debug("[BANDWIDTH] Could not get user timezone: %s", e)
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
        # Buffer-event ingest (bd-skqln.15). Bounded LRU of Dispatcharr
        # ``system_event.id`` values already counted. Persists across polls
        # because Dispatcharr's ``/api/core/system-events/`` feed re-delivers
        # recent events on every fetch — without cross-poll dedup the same
        # event would be counted N times. The cap is intentionally generous
        # (10x the per-poll fetch limit) so a stable working set fits without
        # eviction; the LRU sheds the oldest entries first.
        self._seen_buffer_event_ids: OrderedDict[int, None] = OrderedDict()
        self._seen_buffer_event_ids_cap = 10_000

    async def start(self):
        """Start the background polling task."""
        if self._running:
            logger.warning("[BANDWIDTH] BandwidthTracker already running")
            return

        # Initialize channel maps on startup
        await self._initialize_channel_maps()

        # Clean up stale connections from previous runs
        self._cleanup_stale_connections()

        # Operator-facing Stats v2 opt-out (bd-tp1pd). When the env var
        # is set at process start, announce it ONCE — operators reading
        # ``docker logs`` need a visible signal that Stats v2 is
        # silenced. Per-poll re-emission would be log spam (one line
        # every ``poll_interval`` seconds). Read at start() time
        # because the start log is the operator's single-pane-of-glass
        # check; ``_collect_stats`` rechecks per-poll so a runtime flip
        # still takes effect.
        if _telemetry_opt_out_enabled():
            logger.info(
                "[STATS_V2] telemetry opt-out is ENABLED — no session_telemetry data will be collected"
            )

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("[BANDWIDTH] BandwidthTracker started (polling every %ss)", self.poll_interval)

    async def stop(self):
        """Stop the background polling task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.debug("[BANDWIDTH] Polling task cancelled during shutdown")
            self._task = None
        logger.info("[BANDWIDTH] BandwidthTracker stopped")

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

            logger.info("[BANDWIDTH] Loaded channel maps: %s by UUID, %s by channel number", len(uuid_map), len(number_map))

        except Exception as e:
            logger.exception("[BANDWIDTH] Failed to initialize channel maps: %s", e)

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
                logger.info("[BANDWIDTH] Cleaned up %s stale connections, updated %s channel names", stale_count, name_updates)
        except Exception as e:
            logger.error("[BANDWIDTH] Failed to cleanup stale connections: %s", e)
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
                logger.exception("[BANDWIDTH] BandwidthTracker error: %s", e)

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
            logger.debug("[BANDWIDTH] Refreshed channel maps: %s by UUID, %s by number", len(uuid_map), len(number_map))
        except Exception as e:
            logger.debug("[BANDWIDTH] Failed to refresh channel map: %s", e)

    async def _collect_stats(self):
        """Fetch stats from Dispatcharr and update daily totals."""
        try:
            stats = await self.client.get_channel_stats()
        except Exception as e:
            logger.warning("[BANDWIDTH] Failed to fetch stats from Dispatcharr: %s", e)
            return

        # Stamp the moment we observed this poll. Used by the Stats v2
        # session_telemetry write (skqln.3 step (a)). Held outside the
        # feature-flag check so the value is identical whether or not the
        # additive write is enabled — keeps observed_at semantics stable
        # if/when the flag flips at runtime mid-cycle.
        observed_at_ms = int(time.time() * 1000)

        channels = stats.get("channels", [])
        logger.debug("[BANDWIDTH] Collected stats for %s active channels", len(channels))

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
        # Per-channel snapshot for the Stats v2 session_telemetry helper
        # (skqln.3 step (a)). One entry per active channel this poll.
        telemetry_channel_snapshot: list[dict] = []

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

            # Extract client IP addresses and user_id mappings
            clients = channel.get("clients", [])
            client_ips = [c.get("ip_address") for c in clients if c.get("ip_address")]
            client_user_map = {}
            for c in clients:
                ip = c.get("ip_address")
                uid = c.get("user_id")
                if ip and uid:
                    client_user_map[ip] = uid
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
                        "client_user_map": client_user_map,
                        "client_count": client_count,
                    })
                else:
                    # Channel was active last poll and still is - accumulate watch time
                    still_active_channels.append({
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "client_ips": client_ips,
                        "client_user_map": client_user_map,
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

                # Capture the snapshot the Stats v2 session_telemetry helper
                # needs (skqln.3 step (a)). Built unconditionally so the data
                # shape is stable; the helper is a no-op when the feature
                # flag is OFF. ``stream_id`` is the Dispatcharr integer ID
                # of the stream currently being served — surfaced by
                # ``/proxy/ts/status``, consumed by the provider resolver
                # in ``_resolve_provider_ids`` (bd-skqln.14). May be missing
                # if Dispatcharr serves a degraded stats payload; the
                # resolver tolerates that and falls back to URL parsing
                # (bd-kbgey) when ``url`` carries the same id as the trailing
                # ``.../<stream_id>.ts`` path segment, then NULL.
                telemetry_channel_snapshot.append({
                    "channel_uuid": channel_id,
                    "channel_number": channel_number,
                    "client_ips": list(client_ips),
                    "client_user_map": dict(client_user_map),
                    "channel_bytes_delta": channel_bytes_delta,
                    "stream_id": channel.get("stream_id"),
                    "url": channel.get("url"),
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
                logger.debug("[BANDWIDTH] Bandwidth delta: %.2f MB (in: %.2f, out: %.2f), active channels: %s, clients: %s", bytes_mb, total_bytes_in_delta / (1024*1024), total_bytes_out_delta / (1024*1024), active_channels, total_clients)

        # Update per-channel bandwidth (v0.11.0)
        if channel_bandwidth_updates:
            self._update_channel_bandwidth(channel_bandwidth_updates)

        # Resolve user_id → username for any channels with user IDs
        all_channel_data = newly_active_channels + still_active_channels
        has_user_ids = any(
            ch.get("client_user_map") for ch in all_channel_data
        )
        if has_user_ids:
            try:
                users = await self.client.get_users()
                user_map = {str(u["id"]): u.get("username", "") for u in users}
                for ch in all_channel_data:
                    client_user_map = ch.get("client_user_map", {})
                    ch["client_username_map"] = {
                        ip: user_map.get(str(uid), "")
                        for ip, uid in client_user_map.items()
                    }
            except Exception as e:
                logger.warning("[BANDWIDTH] Failed to resolve usernames for watch history: %s", e)

        # Update watch counts for newly active channels (and log start events)
        if newly_active_channels:
            logger.info("[BANDWIDTH] %s channel(s) started streaming", len(newly_active_channels))
            self._update_watch_counts(newly_active_channels)

        # Accumulate watch time for still-active channels
        if still_active_channels:
            self._update_watch_time(still_active_channels)

        # Log stopped channels
        if stopped_channels:
            logger.info("[BANDWIDTH] %s channel(s) stopped streaming", len(stopped_channels))

        # Operator-facing Stats v2 telemetry opt-out (bd-tp1pd). When the
        # ``ECM_STATS_TELEMETRY_OPT_OUT`` env var is truthy, the entire
        # Stats v2 path is short-circuited: the provider resolver is not
        # called (skips the Dispatcharr ``get_streams_by_ids`` round-trip),
        # the buffer-event ingest is not called (skips the
        # ``get_system_events`` round-trip), and no ``session_telemetry``
        # rows are written. The legacy sibling writes above
        # (``ChannelBandwidth``, ``BandwidthDaily``,
        # ``UniqueClientConnection``) are NOT affected — they pre-date
        # Stats v2 and are not part of the opt-out surface.
        #
        # The env var is read per-poll so a runtime flip takes effect on
        # the next cycle. Cost: one os.environ lookup + string compare
        # per poll (microseconds). The latency-sensitive surface is the
        # network round-trips this flag elides, not the parse itself.
        if _telemetry_opt_out_enabled():
            return

        # Stats v2 write (bd-skqln.3 step (d)). Runs LAST and is wrapped in
        # a defensive try/except inside the helper so a failure in this
        # path cannot disturb the legacy sibling writes above. Step (d)
        # made this unconditional — the ECM_SESSION_TELEMETRY_WRITE_ENABLED
        # kill-switch was retired along with the legacy
        # ``ChannelWatchStats`` writes that the gate used to protect.
        # (bd-tp1pd re-introduces an env var, but as an operator-facing
        # opt-out, not a transition gate — see the short-circuit above.)
        #
        # Provider resolution (bd-skqln.14): the snapshot already carries
        # the ``stream_id`` Dispatcharr surfaced per-channel. The resolver
        # batches those stream IDs into ONE ``get_streams_by_ids`` call
        # per poll and returns a ``{channel_uuid: provider_id}`` map. The
        # cache lives only for the duration of this invocation — next
        # poll re-resolves so a stream's failover hop is picked up
        # immediately. NULL on failure (network, missing stream, deleted
        # provider); the row still gets written.
        provider_by_channel = await self._resolve_provider_ids(
            telemetry_channel_snapshot
        )
        # Buffer-event ingest (bd-skqln.15). Fetches the buffering subset of
        # Dispatcharr's ``/api/core/system-events/`` feed, de-duplicates
        # against the cross-poll LRU, and produces a
        # ``{channel_uuid: deduped_count}`` map. Failure is non-fatal — the
        # helper returns ``{}`` and the session_telemetry rows still write
        # with ``buffer_event_count=0`` as they have since skqln.3 step (a).
        buffer_events_by_channel = await self._collect_buffer_events(
            telemetry_channel_snapshot
        )
        self._write_session_telemetry(
            telemetry_channel_snapshot,
            observed_at_ms,
            provider_by_channel,
            buffer_events_by_channel,
        )

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
            logger.error("[BANDWIDTH] Failed to update bandwidth record: %s", e)
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
            logger.debug("[BANDWIDTH] Updated channel bandwidth for %s channels", len(updates))
        except Exception as e:
            logger.error("[BANDWIDTH] Failed to update channel bandwidth: %s", e)
            session.rollback()
        finally:
            session.close()

    def _update_watch_counts(self, channels: list[dict]):
        """Record newly active channels: create UniqueClientConnection rows,
        bump per-channel connection_count, and emit the journal start event.

        bd-skqln.3 step (d): the legacy ``ChannelWatchStats`` write that
        used to live in this method is gone — the popularity calculator
        and the top-watched API now derive their inputs from
        ``session_telemetry`` and ``unique_client_connections``.
        """
        from journal import log_entry

        session = get_session()
        today = get_current_date()
        try:
            now = datetime.now(get_user_timezone())
            for ch in channels:
                channel_id = ch["channel_id"]
                channel_name = ch["channel_name"]
                client_ips = ch.get("client_ips", [])
                client_user_map = ch.get("client_user_map", {})
                client_username_map = ch.get("client_username_map", {})

                # Create UniqueClientConnection records for each client (v0.11.0)
                for ip in client_ips:
                    connection = UniqueClientConnection(
                        ip_address=ip,
                        channel_id=channel_id,
                        channel_name=channel_name,
                        user_id=client_user_map.get(ip),
                        username=client_username_map.get(ip) or None,
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
                        "client_ips": client_ips,
                    },
                )

            session.commit()
            logger.debug("[BANDWIDTH] Updated watch counts for %s channels", len(channels))
        except Exception as e:
            logger.error("[BANDWIDTH] Failed to update watch counts: %s", e)
            session.rollback()
        finally:
            session.close()

    def _update_watch_time(self, channels: list[dict]):
        """Accumulate watch time for channels that are still active.

        bd-skqln.3 step (d): the legacy ``ChannelWatchStats`` write that
        used to live in this method is gone — watch time is now derived
        from ``session_telemetry`` (one row per poll per client) on read.
        The per-client ``UniqueClientConnection.watch_seconds`` write
        below stays — it's a per-connection accumulator, not the
        per-channel aggregate that was retired.
        """
        session = get_session()
        today = get_current_date()
        try:
            now = datetime.now(get_user_timezone())
            for ch in channels:
                channel_id = ch["channel_id"]
                channel_name = ch["channel_name"]
                new_clients = ch.get("new_clients", [])
                continuing_clients = ch.get("continuing_clients", [])
                client_user_map = ch.get("client_user_map", {})
                client_username_map = ch.get("client_username_map", {})

                # Handle new clients that joined mid-stream (v0.11.0)
                for ip in new_clients:
                    connection = UniqueClientConnection(
                        ip_address=ip,
                        channel_id=channel_id,
                        channel_name=channel_name,
                        user_id=client_user_map.get(ip),
                        username=client_username_map.get(ip) or None,
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
            logger.error("[BANDWIDTH] Failed to update watch time: %s", e)
            session.rollback()
        finally:
            session.close()

    def _log_watch_stop_events(self, channel_ids: set[str]):
        """Log journal entries when channels stop being watched.

        bd-skqln.3 step (d): the channel-name fallback no longer reads
        from ``ChannelWatchStats`` (which is no longer written) — it
        falls through to a UUID-derived placeholder if neither the ECM
        channel map nor the in-memory cache has a name. The
        ``total_watch_seconds`` figure that was previously fetched from
        ``ChannelWatchStats`` is replaced by a session_telemetry
        aggregate using the same DISTINCT-by-(channel, observed_at)
        collapse the view (migration 0008) and popularity calculator
        use, so per-channel watch-time semantics stay consistent
        across surfaces.
        """
        from journal import log_entry

        session = get_session()
        try:
            for channel_id in channel_ids:
                # Get channel name - prefer ECM map, then cache, then placeholder.
                channel_name = (
                    self._ecm_channel_map.get(channel_id)
                    or self._channel_names.get(channel_id)
                    or f"Channel {channel_id[:8]}..."
                )

                # Derive lifetime watch_seconds for this channel from
                # session_telemetry. Same DISTINCT-by-(channel,
                # observed_at) collapse as the channel_watch_stats_v
                # view: a channel with N concurrent clients in one poll
                # contributes one interval, not N.
                per_poll = session.query(
                    SessionTelemetry.observed_at.label("observed_at"),
                    func.max(SessionTelemetry.poll_interval_ms).label("poll_interval_ms"),
                ).filter(
                    SessionTelemetry.channel_id == channel_id,
                ).group_by(
                    SessionTelemetry.observed_at,
                ).subquery()
                total_ms = session.query(
                    func.coalesce(func.sum(per_poll.c.poll_interval_ms), 0)
                ).scalar() or 0
                watch_time = int(total_ms) // 1000

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

            logger.debug("[BANDWIDTH] Logged watch stop events for %s channels", len(channel_ids))
        except Exception as e:
            logger.error("[BANDWIDTH] Failed to log watch stop events: %s", e)
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
                logger.debug("[BANDWIDTH] Closed %s client connections for stopped channels", closed_count)
        except Exception as e:
            logger.error("[BANDWIDTH] Failed to close client connections: %s", e)
            session.rollback()
        finally:
            session.close()

    async def _resolve_provider_ids(
        self,
        channel_snapshot: list[dict],
    ) -> dict[str, Optional[int]]:
        """Resolve each channel's active-stream → provider mapping for one poll.

        Bead: ``enhancedchannelmanager-skqln.14``.

        Returns a ``{channel_uuid: provider_id_or_None}`` map. The mapping
        is built from a SINGLE batched ``DispatcharrClient.get_streams_by_ids``
        call covering every unique ``stream_id`` the snapshot surfaced —
        not N calls per-channel. The cache lives only for the duration of
        the call (the function builds it locally each invocation); a
        stream's failover hop between polls is picked up on the next
        ``_collect_stats`` cycle without staleness.

        Failure modes — every one returns ``None`` for the affected
        channel, emits a structured ``[STATS_V2] provider_resolution_failed``
        log line, and lets the ``session_telemetry`` row write with
        ``provider_id=NULL``:

        * Channel reports no ``stream_id`` (degraded Dispatcharr payload).
        * ``get_streams_by_ids`` raises (network error, 5xx, timeout).
        * Returned stream list omits an expected ``stream_id``
          (Dispatcharr 404 for one of the IDs).
        * Stream record has ``m3u_account=None`` (orphaned — the
          provider was deleted).

        A per-poll SLI line is emitted at INFO: ``[STATS_V2]
        provider_resolution resolved=X unresolved=Y``. skqln.12 derives a
        Prometheus ``stats_v2_provider_resolution_total{result=...}``
        counter from this without plumbing a new code path.

        Performance: ONE additional Dispatcharr round-trip per poll cycle,
        skipped entirely when no channel surfaced a ``stream_id``. The
        bead's "no p95 regression on _collect_stats" constraint is met
        because the cost scales as O(1) calls, not O(channels).
        """
        # Local import keeps the resolver self-contained — the helper at
        # ``stream_prober.extract_m3u_account_id`` normalizes Dispatcharr's
        # two historical shapes for the ``m3u_account`` field (bare int
        # vs. nested object) and is the single owner of that contract.
        from stream_prober import extract_m3u_account_id

        provider_by_channel: dict[str, Optional[int]] = {}
        # Channels that surfaced no stream_id at all — directly unresolvable.
        unresolvable_channels: list[str] = []
        # {channel_uuid: stream_id} for channels we'll attempt to resolve.
        stream_id_by_channel: dict[str, int] = {}
        # Channels whose stream_id came from URL parsing (bd-kbgey fallback)
        # rather than the direct ``stream_id`` field. Used only to
        # differentiate the ``stream_not_found`` failure log so operators
        # can tell a Dispatcharr-direct miss from a URL-derived miss when
        # we triage; the resolved-success path is identical.
        url_derived_channels: set[str] = set()
        for entry in channel_snapshot:
            channel_uuid = entry["channel_uuid"]
            stream_id = entry.get("stream_id")
            if stream_id is None:
                # Fallback (bd-kbgey): Dispatcharr inconsistently surfaces
                # ``stream_id`` on the ``/proxy/ts/status`` payload, but the
                # ``url`` field carries the same id as the trailing path
                # segment before ``.ts``. Parse it and feed it into the
                # same batched lookup below — no extra API call.
                url = entry.get("url")
                derived = _extract_stream_id_from_url(url) if url else None
                if derived is None:
                    unresolvable_channels.append(channel_uuid)
                    provider_by_channel[channel_uuid] = None
                    logger.warning(
                        "[STATS_V2] provider_resolution_failed channel=%s reason=no_stream_id",
                        channel_uuid,
                    )
                    continue
                stream_id_by_channel[channel_uuid] = derived
                url_derived_channels.add(channel_uuid)
                continue
            stream_id_by_channel[channel_uuid] = int(stream_id)

        # Skip the Dispatcharr round-trip entirely when nothing is resolvable.
        if not stream_id_by_channel:
            self._log_provider_resolution_sli(0, len(unresolvable_channels))
            return provider_by_channel

        unique_stream_ids = sorted(set(stream_id_by_channel.values()))
        try:
            streams = await self.client.get_streams_by_ids(unique_stream_ids)
        except Exception as e:
            # Whole batch failed — every attempted channel falls back to
            # NULL with one log per channel so skqln.12's metric can
            # still attribute the failure.
            logger.warning(
                "[STATS_V2] provider_resolution_failed reason=lookup_raised error=%s",
                e,
            )
            for channel_uuid in stream_id_by_channel:
                provider_by_channel[channel_uuid] = None
                logger.warning(
                    "[STATS_V2] provider_resolution_failed channel=%s stream=%s reason=lookup_raised",
                    channel_uuid,
                    stream_id_by_channel[channel_uuid],
                )
            self._log_provider_resolution_sli(
                0, len(unresolvable_channels) + len(stream_id_by_channel)
            )
            return provider_by_channel

        # Build {stream_id: provider_id} from the batch response.
        provider_by_stream: dict[int, Optional[int]] = {}
        for stream in streams:
            sid = stream.get("id", stream.get("stream_id"))
            if sid is None:
                continue
            provider_by_stream[int(sid)] = extract_m3u_account_id(
                stream.get("m3u_account")
            )

        resolved_count = 0
        unresolved_count = len(unresolvable_channels)
        for channel_uuid, stream_id in stream_id_by_channel.items():
            provider_id = provider_by_stream.get(stream_id)
            if provider_id is None:
                # Either the stream was not in the batch response (404)
                # or its m3u_account was None. Both surface as NULL.
                provider_by_channel[channel_uuid] = None
                unresolved_count += 1
                if stream_id not in provider_by_stream:
                    # Distinguish "Dispatcharr didn't find the id we
                    # extracted from the URL" from "Dispatcharr didn't
                    # find the id it gave us directly" — helps observability
                    # if the URL convention shifts (bd-kbgey).
                    reason = (
                        "stream_not_found_url_derived"
                        if channel_uuid in url_derived_channels
                        else "stream_not_found"
                    )
                else:
                    reason = "stream_has_no_provider"
                logger.warning(
                    "[STATS_V2] provider_resolution_failed channel=%s stream=%s reason=%s",
                    channel_uuid,
                    stream_id,
                    reason,
                )
            else:
                provider_by_channel[channel_uuid] = provider_id
                resolved_count += 1

        self._log_provider_resolution_sli(resolved_count, unresolved_count)
        return provider_by_channel

    def _log_provider_resolution_sli(
        self,
        resolved_count: int,
        unresolved_count: int,
    ) -> None:
        """Emit the per-poll provider-resolution SLI line + metric.

        Format of the log line: ``[STATS_V2] provider_resolution
        resolved=X unresolved=Y``. Stable substring shape — kept for
        backwards-compat with operator log greps even though bd-skqln.12
        now also increments a Prometheus counter inline (see below).

        bd-skqln.12: the resolved-rate is the modernized data-consistency
        SLI for Stats v2 — the original dual-write divergence is no
        longer measurable after skqln.3 step (d) removed the legacy
        writer, so the resolver's success-rate is the surviving SLI.
        Target ≥95% steady state per skqln.14 acceptance.

        Cardinality: increments ``ecm_provider_resolution_total`` with
        the bounded ``result`` label (resolved/unresolved) by the
        per-poll counts. NO provider_id label here — that's a follow-up
        bead's decision (provider_id is allowed but would change the
        SLI's aggregation shape).
        """
        logger.info(
            "[STATS_V2] provider_resolution resolved=%s unresolved=%s",
            resolved_count,
            unresolved_count,
        )
        try:
            from observability import get_metric

            counter = get_metric("provider_resolution_total")
            if resolved_count:
                counter.labels(result="resolved").inc(int(resolved_count))
            if unresolved_count:
                counter.labels(result="unresolved").inc(int(unresolved_count))
        except Exception:  # pragma: no cover — never break the resolver
            logger.debug(
                "[STATS_V2] failed to emit provider_resolution_total metric",
                exc_info=True,
            )

    async def _collect_buffer_events(
        self,
        channel_snapshot: list[dict],
    ) -> dict[str, int]:
        """Fetch the buffering subset of Dispatcharr's system-events feed and
        return ``{channel_uuid: deduped_event_count}`` for the current poll.

        Bead: ``enhancedchannelmanager-skqln.15``.

        Dispatcharr exposes recent buffer events at
        ``GET /api/core/system-events/?event_type=buffering`` (already wired
        on ``DispatcharrClient`` as ``get_system_events``). The feed re-
        delivers recent events on every fetch — without dedup, the same
        event would be counted N times across successive polls. The
        ``self._seen_buffer_event_ids`` LRU is the cross-poll dedup state:
        an event's integer ``id`` is the dedup key, capped at
        ``self._seen_buffer_event_ids_cap`` with LRU eviction.

        Channel-id reconciliation: ``ChannelStats`` (``/proxy/ts/status``)
        and ``SystemEvent`` (``/api/core/system-events/``) can disagree on
        the ``channel_id`` field shape — the former is the Dispatcharr UUID
        string, the latter has historically been a numeric channel id.
        This helper normalizes both to ``str(channel_id)`` and tries match
        against the snapshot's channel_uuids; events whose channel cannot
        be mapped to a snapshot row are dropped (logged at WARNING).

        Failure modes are non-fatal — the helper never raises:
        * ``get_system_events`` raises → ``{}`` returned + structured
          ``[STATS_V2] buffer_event_fetch_failed`` log.
        * An event surfaces with no ``id`` → skipped (we cannot dedup it).
        * An event's channel doesn't match any snapshot row → dropped,
          logged once per occurrence as
          ``[STATS_V2] buffer_event_unmapped_channel``.

        A per-poll SLI line is emitted at INFO:
        ``[STATS_V2] buffer_event_ingest fetched=X deduped=Y attributed=Z``.
        skqln.12 derives a Prometheus counter from this line.
        """
        # No channels active this poll → no rows will be written, so skip
        # the Dispatcharr round-trip entirely.
        if not channel_snapshot:
            return {}

        # Build the (str(channel_id) → channel_uuid) lookup so we can
        # reconcile Dispatcharr's two channel_id shapes (numeric on
        # system-events, UUID on /proxy/ts/status). Snapshot rows always
        # use UUID strings; we map numeric-id events through the
        # channel_number map if needed.
        snapshot_uuids = {entry["channel_uuid"] for entry in channel_snapshot}
        snapshot_uuids_str = {str(u) for u in snapshot_uuids}

        try:
            response = await self.client.get_system_events(
                limit=1000,
                offset=0,
                event_type="buffering",
            )
        except Exception as e:
            logger.warning(
                "[STATS_V2] buffer_event_fetch_failed reason=request_raised error=%s",
                e,
            )
            self._log_buffer_event_ingest_sli(
                fetched=0, deduped=0, attributed=0
            )
            return {}

        events = (response or {}).get("events", []) or []
        fetched = len(events)
        attributed_count = 0
        deduped_count = 0
        counts_by_channel: dict[str, int] = {}

        for event in events:
            event_id = event.get("id")
            if event_id is None:
                # Cannot dedup without a stable id — skip rather than
                # double-count on the next poll.
                logger.warning(
                    "[STATS_V2] buffer_event_skipped reason=no_event_id"
                )
                continue
            event_id = int(event_id)
            if event_id in self._seen_buffer_event_ids:
                # Already counted in an earlier poll — bump it to MRU so the
                # LRU eviction prefers genuinely stale entries.
                self._seen_buffer_event_ids.move_to_end(event_id)
                deduped_count += 1
                continue

            event_channel = event.get("channel_id")
            event_channel_str = str(event_channel) if event_channel is not None else None
            if event_channel_str not in snapshot_uuids_str:
                # Dispatcharr surfaced an event for a channel that's not in
                # our snapshot — either the channel stopped between the
                # stats and system-events fetches, or the channel_id shape
                # mismatch documented above. Drop it; record the dedup
                # entry so we don't keep re-evaluating the same id every
                # poll.
                self._seen_buffer_event_ids[event_id] = None
                self._evict_buffer_event_ids_if_over_cap()
                logger.warning(
                    "[STATS_V2] buffer_event_unmapped_channel event_id=%s channel_id=%s",
                    event_id,
                    event_channel,
                )
                continue

            # Attribute the event to the snapshot's UUID-keyed map. Reverse
            # the str() lookup back to the original UUID value so the
            # caller's keys match.
            attributed_uuid = next(
                (u for u in snapshot_uuids if str(u) == event_channel_str),
                event_channel_str,
            )
            counts_by_channel[attributed_uuid] = counts_by_channel.get(attributed_uuid, 0) + 1
            attributed_count += 1
            self._seen_buffer_event_ids[event_id] = None

        # Evict any LRU overflow once per poll — cheaper than per-event.
        self._evict_buffer_event_ids_if_over_cap()

        self._log_buffer_event_ingest_sli(
            fetched=fetched,
            deduped=deduped_count,
            attributed=attributed_count,
        )
        return counts_by_channel

    def _evict_buffer_event_ids_if_over_cap(self) -> None:
        """LRU eviction for the buffer-event dedup set. Keeps memory
        bounded; entries are popped from the front (oldest insertion).
        """
        while len(self._seen_buffer_event_ids) > self._seen_buffer_event_ids_cap:
            self._seen_buffer_event_ids.popitem(last=False)

    def _log_buffer_event_ingest_sli(
        self,
        *,
        fetched: int,
        deduped: int,
        attributed: int,
    ) -> None:
        """Emit the per-poll buffer-event-ingest SLI line.

        Format: ``[STATS_V2] buffer_event_ingest fetched=X deduped=Y
        attributed=Z``. Stable substring shape; skqln.12 derives a
        Prometheus counter from this without coupling a metric library
        into the ingest path.
        """
        logger.info(
            "[STATS_V2] buffer_event_ingest fetched=%s deduped=%s attributed=%s",
            fetched,
            deduped,
            attributed,
        )

    def _record_session_telemetry_metrics(
        self,
        *,
        result: str,
        duration_seconds: float,
        rows_written: int,
    ) -> None:
        """Emit the Stats v2 session_telemetry write metrics (bd-skqln.12).

        Wrapped so a metric-side failure (registry not installed in some
        edge-case test, prometheus-client missing) can never propagate into
        the observation path it is instrumenting. The exception swallow
        is deliberate — observability must not break the writer.
        """
        try:
            from observability import get_metric

            get_metric("session_telemetry_writes_total").labels(
                result=result
            ).inc()
            get_metric("session_telemetry_write_duration_seconds").observe(
                max(0.0, float(duration_seconds))
            )
            if result == "success":
                # Gauge reflects the most recent successful batch's size —
                # on failure the previous value remains, which is the
                # behavior SRE wants for storage-growth alerting.
                get_metric("session_telemetry_row_count").set(int(rows_written))
        except Exception:  # pragma: no cover — never break the writer
            logger.debug(
                "[STATS_V2] failed to emit session_telemetry write metrics",
                exc_info=True,
            )

    def _write_session_telemetry(
        self,
        channel_snapshot: list[dict],
        observed_at_ms: int,
        provider_by_channel: Optional[dict[str, Optional[int]]] = None,
        buffer_events_by_channel: Optional[dict[str, int]] = None,
    ) -> None:
        """Write one row per active viewing connection into ``session_telemetry``.

        Stats v2 additive write path — bead ``enhancedchannelmanager-skqln.3``
        step (a). Called from ``_collect_stats`` AFTER the four legacy writes
        and ONLY when ``ECM_SESSION_TELEMETRY_WRITE_ENABLED`` is on. No
        consumers of ``session_telemetry`` exist yet, so this is observation-
        only — the row shape is what later beads (skqln.5 read API, skqln.14
        provider resolver, skqln.15 buffer ingest) will populate further.

        Row population (step (a), conservative):

        * ``session_id`` — synthesized from the active-connection id we track
          in ``self._active_connections``; namespaced ``conn-<id>`` so it does
          not collide with future session-id sources. Stable for the life of
          the connection.
        * ``observed_at`` — ms since epoch stamped at the start of the poll
          (passed in so all rows in this cycle share the same value).
        * ``user_id`` — from the per-channel ``client_user_map`` if present;
          NULL when Dispatcharr did not surface a user id for that ip.
        * ``provider_id`` — populated from ``provider_by_channel`` (built
          upstream by ``_resolve_provider_ids``, bd-skqln.14). NULL when the
          resolver couldn't map the active stream to an M3U account.
        * ``channel_id`` — Dispatcharr channel UUID (``String(64)``). Same
          shape the snapshot loop in ``_collect_stats`` already keys on, and
          matches every other channel-keyed table in the schema
          (``channel_watch_stats`` etc.). Migration 0007 corrected the
          column type from INTEGER to VARCHAR(64) NOT NULL after the
          step-(a) commit was first drafted with NULL writes; see the
          bead body for the schema-mismatch correction.
        * ``bytes_delta`` — per-channel byte delta divided equally across
          active clients (integer floor; remainder dropped). Acceptable for
          observation-only; refined when consumers exist.
        * ``buffer_event_count`` — deduplicated count of buffering events
          surfaced for this channel during this poll (bd-skqln.15). Buffer
          events are channel-level (a stall on the upstream pipeline
          affects every viewer), so the count attributes to EXACTLY ONE
          row per ``(channel_uuid, observed_at)`` bucket — the first row
          emitted for that channel, with sibling rows writing 0. This
          keeps ``SUM(buffer_event_count) GROUP BY provider, time_bucket``
          well-defined for skqln.16 query 1 ("buffering events by
          provider") without per-client double-counting.
        * ``poll_interval_ms`` — ``self.poll_interval`` (seconds) × 1000.

        The write is wrapped in a defensive try/except so any failure here
        cannot disturb the legacy writes that already committed. This is
        the keystone of "single-write refactor that can't break what
        already works" — step (a) is dual-write under a flag, but ONLY for
        the duration needed to prove the new path; legacy-write removal is
        step (d) in this bead, behind a separate commit and PR.
        """
        if provider_by_channel is None:
            provider_by_channel = {}
        if buffer_events_by_channel is None:
            buffer_events_by_channel = {}
        # bd-skqln.12: time the entire write attempt and record success /
        # failure on the way out. ``rows_written`` is hoisted here so it is
        # visible to the metric-emission block in the ``finally`` (it is 0
        # if the helper raises before the inner counter increments).
        write_start = time.perf_counter()
        rows_written = 0
        write_result = "failure"
        try:
            poll_interval_ms = max(int(self.poll_interval * 1000), 0)
            session = get_session()
            try:
                # Track which channels have already received their buffer-
                # event-count attribution this poll. Channel-level counts
                # land on the FIRST emitted row per channel (sorted by
                # client_ip for determinism); subsequent rows for the same
                # channel write 0 so SUM aggregates do not double-count.
                buffer_attributed: set[str] = set()
                # Channels with buffer events but no eligible row (no active
                # client connection recorded) — count is dropped and logged
                # below.
                channels_with_buffer = set(buffer_events_by_channel.keys())
                for entry in channel_snapshot:
                    channel_uuid = entry["channel_uuid"]
                    client_ips = entry["client_ips"]
                    client_user_map = entry["client_user_map"]
                    channel_bytes_delta = max(int(entry["channel_bytes_delta"]), 0)
                    provider_id = provider_by_channel.get(channel_uuid)

                    # No active clients on this channel this poll → nothing
                    # to attribute to a session.
                    if not client_ips:
                        continue

                    per_client_bytes = channel_bytes_delta // len(client_ips)
                    # Deterministic emission order so the buffer-event count
                    # consistently lands on the same row across runs (test
                    # parity + reproducible aggregates).
                    sorted_ips = sorted(client_ips)

                    for ip in sorted_ips:
                        conn_key = (channel_uuid, ip)
                        conn_id = self._active_connections.get(conn_key)
                        # Tracker has not yet recorded a connection row for
                        # this (channel, ip) — happens on the first poll of
                        # a brand-new viewer because _update_watch_counts
                        # has already run by the time we reach here, so this
                        # should be rare. Skip rather than synthesize a
                        # session id we cannot correlate later.
                        if conn_id is None:
                            continue

                        # Attribute the channel's buffer-event count to the
                        # FIRST eligible row only (bd-skqln.15). Sibling
                        # rows write 0.
                        if channel_uuid not in buffer_attributed:
                            row_buffer_count = buffer_events_by_channel.get(channel_uuid, 0)
                            buffer_attributed.add(channel_uuid)
                        else:
                            row_buffer_count = 0

                        session.add(
                            SessionTelemetry(
                                session_id=f"conn-{conn_id}",
                                observed_at=observed_at_ms,
                                user_id=client_user_map.get(ip),
                                provider_id=provider_id,
                                channel_id=channel_uuid,
                                bytes_delta=per_client_bytes,
                                buffer_event_count=row_buffer_count,
                                poll_interval_ms=poll_interval_ms,
                            )
                        )
                        rows_written += 1

                # Buffer events were surfaced for channels with no eligible
                # row this poll (rare — between client disconnect and the
                # channel stopping). Log per channel so skqln.12's metric
                # can surface the gap; the count is dropped.
                unattributed = channels_with_buffer - buffer_attributed
                for channel_uuid in unattributed:
                    logger.warning(
                        "[STATS_V2] buffer_event_dropped channel=%s count=%s reason=no_active_session_row",
                        channel_uuid,
                        buffer_events_by_channel.get(channel_uuid, 0),
                    )

                if rows_written:
                    session.commit()
                    logger.debug(
                        "[STATS_V2] Wrote %s session_telemetry row(s) (observed_at=%s)",
                        rows_written,
                        observed_at_ms,
                    )
                else:
                    # Nothing to write; release the (empty) transaction.
                    session.rollback()
                # Reaching this line means the write attempt completed
                # without raising (rows_written may be 0). Record success
                # so the bd-skqln.12 metric reflects "the helper did what
                # it was asked to do" rather than overstating failures
                # whenever a poll had no active connections.
                write_result = "success"
            finally:
                session.close()
        except Exception as e:
            # Observation-only path — failures must never propagate. The
            # legacy writes already committed before we got here.
            #
            # bd-skqln.12: WARN-level log carries trace_id (via the
            # observability filter) + observed_at (poll-scoped correlator)
            # + the count of channels we attempted, so SRE can correlate
            # this failure with the poll that produced it. Privacy 11a:
            # we deliberately do NOT enumerate per-row user_id+channel_id
            # pairs — those are aggregated away by the time we get here.
            logger.warning(
                "[STATS_V2] session_telemetry write failed observed_at=%s channels_attempted=%s error=%s",
                observed_at_ms,
                len(channel_snapshot),
                e,
                exc_info=True,
            )
        finally:
            # Always emit the write-health metrics — success or failure
            # paths both observe duration and increment the result-keyed
            # counter. Wrapped helper so a metric-emission failure cannot
            # propagate out of the observation-only writer.
            duration = time.perf_counter() - write_start
            self._record_session_telemetry_metrics(
                result=write_result,
                duration_seconds=duration,
                rows_written=rows_written,
            )

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
            List of channel watch stats dicts (channel_id, channel_name,
            watch_count, total_watch_seconds, last_watched), ordered by
            selected metric desc.

        bd-skqln.3 step (d): reads from ``session_telemetry`` (DISTINCT
        session_id and DISTINCT-by-observed_at poll-interval sum) and
        side-loads channel_name from ``UniqueClientConnection``. Returns
        the same dict shape the legacy ``ChannelWatchStats.to_dict()``
        produced, so API consumers don't need to change.
        """
        session = get_session()
        try:
            # DISTINCT-by-(channel_id, observed_at) collapse so concurrent
            # clients in one poll contribute one interval each — matches
            # the channel_watch_stats_v view + popularity calculator.
            per_poll = session.query(
                SessionTelemetry.channel_id.label("channel_id"),
                SessionTelemetry.observed_at.label("observed_at"),
                func.max(SessionTelemetry.poll_interval_ms).label("poll_interval_ms"),
            ).group_by(
                SessionTelemetry.channel_id,
                SessionTelemetry.observed_at,
            ).subquery()

            per_channel = session.query(
                per_poll.c.channel_id.label("channel_id"),
                func.coalesce(
                    func.sum(per_poll.c.poll_interval_ms) / 1000, 0
                ).label("total_watch_seconds"),
                func.max(per_poll.c.observed_at).label("last_observed_at_ms"),
            ).group_by(per_poll.c.channel_id).subquery()

            session_counts = session.query(
                SessionTelemetry.channel_id.label("channel_id"),
                func.count(distinct(SessionTelemetry.session_id)).label("watch_count"),
            ).group_by(SessionTelemetry.channel_id).subquery()

            query = session.query(
                per_channel.c.channel_id,
                per_channel.c.total_watch_seconds,
                per_channel.c.last_observed_at_ms,
                func.coalesce(session_counts.c.watch_count, 0).label("watch_count"),
            ).outerjoin(
                session_counts,
                session_counts.c.channel_id == per_channel.c.channel_id,
            )

            if sort_by == "time":
                query = query.order_by(per_channel.c.total_watch_seconds.desc())
            else:
                query = query.order_by(func.coalesce(session_counts.c.watch_count, 0).desc())

            rows = query.limit(limit).all()
            if not rows:
                return []

            # Side-load channel_name from UniqueClientConnection. One
            # round-trip for the candidate channel set rather than per-row.
            channel_ids = [r.channel_id for r in rows]
            name_rows = session.query(
                UniqueClientConnection.channel_id,
                UniqueClientConnection.channel_name,
            ).filter(
                UniqueClientConnection.channel_id.in_(channel_ids),
            ).all()
            name_lookup: dict[str, str] = {}
            for cn_row in name_rows:
                # Take the first non-empty name we see per channel; the
                # writer keeps names consistent across rows on the same
                # channel but a stale row may still exist.
                if cn_row.channel_id not in name_lookup and cn_row.channel_name:
                    name_lookup[cn_row.channel_id] = cn_row.channel_name

            results = []
            for row in rows:
                last_watched_dt = (
                    datetime.utcfromtimestamp(row.last_observed_at_ms / 1000.0)
                    if row.last_observed_at_ms is not None
                    else None
                )
                results.append({
                    "channel_id": row.channel_id,
                    "channel_name": name_lookup.get(
                        row.channel_id, f"Channel {row.channel_id[:8]}..."
                    ),
                    "watch_count": int(row.watch_count or 0),
                    "total_watch_seconds": int(row.total_watch_seconds or 0),
                    "last_watched": (
                        last_watched_dt.isoformat() + "Z"
                        if last_watched_dt is not None
                        else None
                    ),
                })
            return results
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
                logger.info("[BANDWIDTH] Purged %s old bandwidth records", deleted)
        except Exception as e:
            logger.error("[BANDWIDTH] Failed to purge old records: %s", e)
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
