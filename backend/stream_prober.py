"""
Stream Prober service.
Uses ffprobe to extract stream metadata and stores results in SQLite.
Supports both scheduled and on-demand probing.
"""
import asyncio
import json
import logging
import shutil
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx

from database import get_session
from models import StreamStats

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_PROBE_TIMEOUT = 30  # seconds
DEFAULT_PROBE_BATCH_SIZE = 10  # streams per cycle
DEFAULT_PROBE_INTERVAL_HOURS = 24  # daily
BITRATE_SAMPLE_DURATION = 8  # seconds to sample stream for bitrate measurement


def check_ffprobe_available() -> bool:
    """Check if ffprobe is available on the system."""
    return shutil.which("ffprobe") is not None


class StreamProber:
    """
    Background service that probes streams using ffprobe.
    Supports scheduled probing and on-demand single/batch probes.
    """

    def __init__(
        self,
        client,
        probe_timeout: int = DEFAULT_PROBE_TIMEOUT,
        probe_batch_size: int = DEFAULT_PROBE_BATCH_SIZE,
        probe_interval_hours: int = DEFAULT_PROBE_INTERVAL_HOURS,
        probe_enabled: bool = True,
        schedule_time: str = "03:00",  # HH:MM format, 24h
        user_timezone: str = "",  # IANA timezone name
        probe_channel_groups: list[str] = None,  # List of group names to probe (empty/None = all groups)
        bitrate_sample_duration: int = 10,  # Duration in seconds to sample stream for bitrate (10, 20, or 30)
        parallel_probing_enabled: bool = True,  # Probe streams from different M3Us simultaneously
    ):
        self.client = client
        self.probe_timeout = probe_timeout
        self.probe_batch_size = probe_batch_size
        self.probe_interval_hours = probe_interval_hours
        self.probe_enabled = probe_enabled
        self.schedule_time = schedule_time
        self.user_timezone = user_timezone
        self.probe_channel_groups = probe_channel_groups or []
        self.bitrate_sample_duration = bitrate_sample_duration
        self.parallel_probing_enabled = parallel_probing_enabled
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._probing_in_progress = False
        # Progress tracking for probe all streams
        self._probe_progress_total = 0
        self._probe_progress_current = 0
        self._probe_progress_status = "idle"
        self._probe_progress_current_stream = ""
        self._probe_progress_success_count = 0
        self._probe_progress_failed_count = 0
        self._probe_success_streams = []  # List of {id, name, url} for successful probes
        self._probe_failed_streams = []   # List of {id, name, url} for failed probes
        self._probe_skipped_streams = []  # List of {id, name, url, reason} for skipped probes (e.g., M3U at max connections)
        self._probe_progress_skipped_count = 0
        # Probe history - list of last 5 probe runs
        self._probe_history = []  # List of {timestamp, total, success_count, failed_count, status, success_streams, failed_streams}

    async def start(self):
        """Start the background scheduled probing task."""
        logger.info("StreamProber.start() called")

        if self._running:
            logger.warning("StreamProber already running")
            return

        # Check ffprobe availability
        ffprobe_available = check_ffprobe_available()
        logger.info(f"ffprobe availability check: {ffprobe_available}")

        if not ffprobe_available:
            logger.error("ffprobe not found - stream probing disabled (scheduled probing will not start)")
            logger.warning("On-demand probing will fail without ffprobe")
            return

        self._running = True
        self._task = asyncio.create_task(self._scheduled_probe_loop())
        logger.info(
            f"StreamProber started successfully (schedule: {self.schedule_time}, interval: {self.probe_interval_hours}h, "
            f"batch: {self.probe_batch_size}, timeout: {self.probe_timeout}s)"
        )

    async def stop(self):
        """Stop the background probing task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("StreamProber stopped")

    def _get_seconds_until_next_schedule(self) -> int:
        """Calculate seconds until the next scheduled probe time."""
        try:
            # Parse schedule time
            hour, minute = map(int, self.schedule_time.split(":"))
        except (ValueError, AttributeError):
            hour, minute = 3, 0  # Default to 3:00 AM

        now = datetime.utcnow()

        # If user has a timezone set, calculate in their local time
        if self.user_timezone:
            try:
                import zoneinfo
                tz = zoneinfo.ZoneInfo(self.user_timezone)
                now_local = datetime.now(tz)
                # Create next scheduled time in user's timezone
                next_run_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if next_run_local <= now_local:
                    # Already passed today, schedule for tomorrow
                    next_run_local += timedelta(days=1)
                # Convert to UTC for sleep calculation
                next_run_utc = next_run_local.astimezone(zoneinfo.ZoneInfo("UTC")).replace(tzinfo=None)
                seconds_until = (next_run_utc - now).total_seconds()
            except Exception as e:
                logger.warning(f"Failed to use timezone {self.user_timezone}, using UTC: {e}")
                # Fall back to UTC
                next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if next_run <= now:
                    next_run += timedelta(days=1)
                seconds_until = (next_run - now).total_seconds()
        else:
            # No timezone set, use UTC
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            seconds_until = (next_run - now).total_seconds()

        return max(60, int(seconds_until))  # At least 60 seconds

    async def _scheduled_probe_loop(self):
        """Main loop for scheduled probing."""
        # Wait a bit before first probe to let system stabilize
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

        while self._running and self.probe_enabled:
            # Calculate time until next scheduled probe
            seconds_until = self._get_seconds_until_next_schedule()
            hours_until = seconds_until / 3600
            logger.info(f"StreamProber: Next scheduled probe in {hours_until:.1f} hours (at {self.schedule_time})")

            # Sleep until scheduled time
            try:
                await asyncio.sleep(seconds_until)
            except asyncio.CancelledError:
                break

            # Run the probe
            try:
                await self._probe_stale_streams()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"StreamProber scheduled probe error: {e}")

            # Small delay before calculating next schedule (to avoid immediate re-triggering)
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break

    async def _probe_stale_streams(self):
        """Find and probe streams that haven't been probed recently."""
        if self._probing_in_progress:
            logger.debug("Probe already in progress, skipping")
            return

        self._probing_in_progress = True
        try:
            cutoff = datetime.utcnow() - timedelta(hours=self.probe_interval_hours)

            # Get all streams from Dispatcharr
            streams = await self._fetch_all_streams()
            if not streams:
                logger.debug("No streams found to probe")
                return

            # Find streams needing probe (never probed or stale)
            session = get_session()
            try:
                probed_recently = {
                    stat.stream_id
                    for stat in session.query(StreamStats).filter(
                        StreamStats.last_probed > cutoff
                    ).all()
                }

                to_probe = [s for s in streams if s["id"] not in probed_recently]
                to_probe = to_probe[: self.probe_batch_size]  # Limit batch size

                if not to_probe:
                    logger.debug("No streams need probing")
                    return

                logger.info(f"Scheduled probe: {len(to_probe)} streams to probe")

                for stream in to_probe:
                    if not self._running:
                        break
                    await self.probe_stream(
                        stream["id"], stream.get("url"), stream.get("name")
                    )
                    await asyncio.sleep(1)  # Rate limiting

            finally:
                session.close()
        finally:
            self._probing_in_progress = False

    async def probe_stream(
        self, stream_id: int, url: Optional[str], name: Optional[str] = None
    ) -> dict:
        """
        Probe a single stream using ffprobe.
        Returns the probe result dict.
        """
        logger.debug(f"probe_stream() called for stream_id={stream_id}, name={name}, url={'present' if url else 'missing'}")

        if not url:
            logger.warning(f"Stream {stream_id} has no URL, marking as failed")
            return self._save_probe_result(
                stream_id, name, None, "failed", "No URL available"
            )

        try:
            logger.debug(f"Running ffprobe for stream {stream_id}")
            result = await self._run_ffprobe(url)
            logger.info(f"Stream {stream_id} ffprobe succeeded")

            # Measure actual bitrate by downloading stream data
            logger.debug(f"Measuring bitrate for stream {stream_id}")
            measured_bitrate = await self._measure_stream_bitrate(url)

            # Save probe result with both ffprobe metadata and measured bitrate
            return self._save_probe_result(
                stream_id, name, result, "success", None, measured_bitrate
            )
        except asyncio.TimeoutError:
            logger.warning(f"Stream {stream_id} probe timed out after {self.probe_timeout}s")
            return self._save_probe_result(
                stream_id,
                name,
                None,
                "timeout",
                f"Probe timed out after {self.probe_timeout}s",
            )
        except Exception as e:
            error_msg = str(e)
            # Truncate very long error messages
            if len(error_msg) > 500:
                error_msg = error_msg[:500] + "..."
            logger.error(f"Stream {stream_id} probe failed: {error_msg}")
            return self._save_probe_result(stream_id, name, None, "failed", error_msg)

    async def _run_ffprobe(self, url: str) -> dict:
        """Run ffprobe and parse JSON output."""
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-timeout",
            str(self.probe_timeout * 1000000),  # microseconds
            url,
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.probe_timeout + 5
            )
        except asyncio.TimeoutError:
            process.kill()
            raise

        if process.returncode != 0:
            error_text = stderr.decode()[:500] if stderr else "Unknown error"
            raise RuntimeError(f"ffprobe failed: {error_text}")

        output = stdout.decode()
        if not output.strip():
            raise RuntimeError("ffprobe returned empty output")

        return json.loads(output)

    async def _measure_stream_bitrate(self, url: str) -> Optional[int]:
        """
        Measure actual stream bitrate by downloading data for a few seconds.
        This is how Dispatcharr gets real bitrate - by measuring throughput.

        Returns bitrate in bits per second, or None if measurement fails.
        """
        try:
            logger.debug(f"Starting bitrate measurement for {self.bitrate_sample_duration}s...")

            bytes_downloaded = 0
            start_time = time.time()

            # Stream download with timeout (all four parameters required by httpx.Timeout)
            timeout = httpx.Timeout(
                connect=10.0,
                read=self.bitrate_sample_duration + 5.0,
                write=10.0,
                pool=10.0
            )

            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()

                    # Download stream data for the sample duration
                    async for chunk in response.aiter_bytes(chunk_size=65536):  # 64KB chunks
                        bytes_downloaded += len(chunk)
                        elapsed = time.time() - start_time

                        # Stop after sample duration
                        if elapsed >= self.bitrate_sample_duration:
                            break

            elapsed = time.time() - start_time

            # Calculate bitrate (bits per second)
            if elapsed > 0:
                bitrate_bps = int((bytes_downloaded * 8) / elapsed)
                logger.info(f"Measured bitrate: {bytes_downloaded:,} bytes in {elapsed:.2f}s = {bitrate_bps:,} bps ({bitrate_bps/1000000:.2f} Mbps)")
                return bitrate_bps
            else:
                logger.warning("Bitrate measurement: elapsed time is zero")
                return None

        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP error during bitrate measurement: {e.response.status_code}")
            return None
        except httpx.TimeoutException:
            logger.warning(f"Timeout during bitrate measurement")
            return None
        except Exception as e:
            logger.warning(f"Failed to measure bitrate: {e}")
            return None

    def _save_probe_result(
        self,
        stream_id: int,
        stream_name: Optional[str],
        ffprobe_data: Optional[dict],
        status: str,
        error_message: Optional[str],
        measured_bitrate: Optional[int] = None,
    ) -> dict:
        """Parse ffprobe output and save to database."""
        session = get_session()
        try:
            # Get or create stats record
            stats = (
                session.query(StreamStats).filter_by(stream_id=stream_id).first()
            )
            if not stats:
                stats = StreamStats(stream_id=stream_id)
                session.add(stats)

            stats.stream_name = stream_name
            stats.probe_status = status
            stats.error_message = error_message
            stats.last_probed = datetime.utcnow()

            if ffprobe_data and status == "success":
                self._parse_ffprobe_data(stats, ffprobe_data)

            # Apply measured bitrate if available (overrides ffprobe metadata)
            if measured_bitrate is not None:
                stats.video_bitrate = measured_bitrate
                logger.debug(f"Applied measured bitrate: {measured_bitrate} bps")

            session.commit()
            result = stats.to_dict()
            logger.debug(f"Saved probe result for stream {stream_id}: {status}")
            return result
        except Exception as e:
            logger.error(f"Failed to save probe result: {e}")
            session.rollback()
            raise
        finally:
            session.close()

    def _parse_ffprobe_data(self, stats: StreamStats, data: dict):
        """Extract relevant fields from ffprobe JSON output."""
        streams = data.get("streams", [])
        format_info = data.get("format", {})

        # Find video stream
        video_stream = next(
            (s for s in streams if s.get("codec_type") == "video"), None
        )
        if video_stream:
            # Debug: Log available bitrate fields
            logger.debug(f"Video stream bitrate fields - bit_rate: {video_stream.get('bit_rate')}, "
                        f"tags.BPS: {video_stream.get('tags', {}).get('BPS')}, "
                        f"tags.DURATION: {video_stream.get('tags', {}).get('DURATION')}, "
                        f"format.bit_rate: {format_info.get('bit_rate')}")
            width = video_stream.get("width")
            height = video_stream.get("height")
            if width and height:
                stats.resolution = f"{width}x{height}"

            stats.video_codec = video_stream.get("codec_name")

            # Parse FPS from various fields
            fps = self._parse_fps(video_stream)
            if fps:
                stats.fps = str(fps)

            # Extract video bitrate (try multiple sources)
            video_bit_rate = video_stream.get("bit_rate")
            if not video_bit_rate:
                # Try tags.BPS as fallback (common in HLS/MPEG-TS)
                video_bit_rate = video_stream.get("tags", {}).get("BPS")
            if not video_bit_rate:
                # Try tags.BPS-eng (variant-BPS)
                video_bit_rate = video_stream.get("tags", {}).get("BPS-eng")

            if video_bit_rate:
                try:
                    stats.video_bitrate = int(video_bit_rate)
                    logger.debug(f"Extracted video bitrate: {stats.video_bitrate} bps")
                except (ValueError, TypeError):
                    logger.warning(f"Failed to parse video bitrate: {video_bit_rate}")

        # Find audio stream
        audio_stream = next(
            (s for s in streams if s.get("codec_type") == "audio"), None
        )
        if audio_stream:
            stats.audio_codec = audio_stream.get("codec_name")
            stats.audio_channels = audio_stream.get("channels")

        # Format info
        format_name = format_info.get("format_name", "")
        stats.stream_type = self._parse_stream_type(format_name)

        # Bitrate
        bit_rate = format_info.get("bit_rate")
        if bit_rate:
            try:
                stats.bitrate = int(bit_rate)
            except (ValueError, TypeError):
                pass

    def _parse_fps(self, video_stream: dict) -> Optional[float]:
        """Parse FPS from various ffprobe fields."""
        # Try r_frame_rate first (most reliable)
        r_frame_rate = video_stream.get("r_frame_rate")
        if r_frame_rate and "/" in r_frame_rate:
            try:
                num, den = r_frame_rate.split("/")
                if float(den) > 0:
                    return round(float(num) / float(den), 2)
            except (ValueError, ZeroDivisionError):
                pass

        # Try avg_frame_rate
        avg_frame_rate = video_stream.get("avg_frame_rate")
        if avg_frame_rate and "/" in avg_frame_rate:
            try:
                num, den = avg_frame_rate.split("/")
                if float(den) > 0:
                    return round(float(num) / float(den), 2)
            except (ValueError, ZeroDivisionError):
                pass

        return None

    def _parse_stream_type(self, format_name: str) -> Optional[str]:
        """Parse stream type from ffprobe format name."""
        format_lower = format_name.lower()
        if "hls" in format_lower or "m3u8" in format_lower or "applehttp" in format_lower:
            return "HLS"
        elif "mpegts" in format_lower:
            return "MPEG-TS"
        elif "mp4" in format_lower or "mov" in format_lower:
            return "MP4"
        elif "flv" in format_lower:
            return "FLV"
        elif "rtmp" in format_lower:
            return "RTMP"
        elif "dash" in format_lower:
            return "DASH"
        elif format_name:
            # Return first part if multiple formats listed (e.g., "hls,applehttp")
            return format_name.split(",")[0].upper()[:10]
        return None

    async def _fetch_all_streams(self) -> list:
        """Fetch all streams from Dispatcharr (paginated)."""
        all_streams = []
        page = 1
        while True:
            try:
                result = await self.client.get_streams(page=page, page_size=500)
                streams = result.get("results", [])
                all_streams.extend(streams)
                if not result.get("next"):
                    break
                page += 1
                if page > 50:  # Safety limit
                    break
            except Exception as e:
                logger.error(f"Failed to fetch streams page {page}: {e}")
                break
        return all_streams

    async def _fetch_channel_stream_ids(self, channel_groups_override: list[str] = None) -> tuple[set, dict, dict]:
        """
        Fetch all unique stream IDs from channels (paginated).
        Only fetches from selected groups if probe_channel_groups is set.
        Returns: (set of stream IDs, dict mapping stream_id -> list of channel names, dict mapping stream_id -> lowest channel number)

        Args:
            channel_groups_override: Optional list of channel group names to filter by.
                                    If None, uses self.probe_channel_groups.
        """
        channel_stream_ids = set()
        stream_to_channels = {}  # stream_id -> list of channel names
        stream_to_channel_number = {}  # stream_id -> lowest channel number (for sorting)

        # Determine which groups to filter by
        groups_to_filter = channel_groups_override if channel_groups_override is not None else self.probe_channel_groups

        # If specific groups are selected, fetch all groups first to filter
        selected_group_ids = set()
        if groups_to_filter:
            try:
                all_groups = await self.client.get_channel_groups()
                for group in all_groups:
                    if group.get("name") in groups_to_filter:
                        selected_group_ids.add(group["id"])
                logger.info(f"Filtering to {len(selected_group_ids)} selected groups: {groups_to_filter}")
            except Exception as e:
                logger.error(f"Failed to fetch channel groups for filtering: {e}")
                # Continue without filtering if we can't fetch groups

        page = 1
        while True:
            try:
                result = await self.client.get_channels(page=page, page_size=500)
                channels = result.get("results", [])
                for channel in channels:
                    # If groups are selected, filter by channel_group_id
                    if selected_group_ids:
                        channel_group_id = channel.get("channel_group_id")
                        if channel_group_id not in selected_group_ids:
                            continue  # Skip channels not in selected groups

                    channel_name = channel.get("name", f"Channel {channel.get('id', 'Unknown')}")
                    channel_number = channel.get("channel_number", 999999)  # Default high number for sorting
                    # Each channel has a "streams" field which is a list of stream IDs
                    stream_ids = channel.get("streams", [])
                    channel_stream_ids.update(stream_ids)
                    # Map each stream to its channel names and track lowest channel number
                    for stream_id in stream_ids:
                        if stream_id not in stream_to_channels:
                            stream_to_channels[stream_id] = []
                        stream_to_channels[stream_id].append(channel_name)
                        # Track the lowest channel number for this stream (for sorting)
                        if stream_id not in stream_to_channel_number or channel_number < stream_to_channel_number[stream_id]:
                            stream_to_channel_number[stream_id] = channel_number
                if not result.get("next"):
                    break
                page += 1
                if page > 50:  # Safety limit
                    break
            except Exception as e:
                logger.error(f"Failed to fetch channels page {page}: {e}")
                break
        return channel_stream_ids, stream_to_channels, stream_to_channel_number

    async def _get_all_m3u_active_connections(self) -> dict[int, int]:
        """
        Fetch current active connection counts for all M3U accounts.
        Makes a single API call to Dispatcharr to get real-time connection status.

        Returns:
            Dict mapping M3U account ID to active connection count.
        """
        try:
            channel_stats = await self.client.get_channel_stats()
            channels = channel_stats.get("channels", [])
            counts = {}
            for ch in channels:
                m3u_id = ch.get("m3u_profile_id")
                if m3u_id:
                    counts[m3u_id] = counts.get(m3u_id, 0) + 1
            return counts
        except Exception as e:
            logger.warning(f"Failed to fetch M3U connection counts: {e}")
            # Return empty dict on failure - allows probes to proceed (fail-open)
            return {}

    async def probe_all_streams(self, channel_groups_override: list[str] = None):
        """Probe all streams that are in channels (runs in background).

        Uses parallel probing - streams from different M3U accounts (or same M3U with
        available capacity) are probed concurrently for faster completion.

        Args:
            channel_groups_override: Optional list of channel group names to filter by.
                                    If None, uses self.probe_channel_groups.
                                    If empty list, probes all groups.
        """
        if self._probing_in_progress:
            logger.warning("Probe already in progress")
            return {"status": "already_running"}

        self._probing_in_progress = True
        self._probe_progress_current = 0
        self._probe_progress_total = 0
        self._probe_progress_status = "fetching"
        self._probe_progress_current_stream = ""
        self._probe_progress_success_count = 0
        self._probe_progress_failed_count = 0
        self._probe_progress_skipped_count = 0
        self._probe_success_streams = []
        self._probe_failed_streams = []
        self._probe_skipped_streams = []

        probed_count = 0
        start_time = datetime.utcnow()
        try:
            # Fetch all channel stream IDs and channel mappings
            logger.info(f"Fetching channel stream IDs (override groups: {channel_groups_override})...")
            channel_stream_ids, stream_to_channels, stream_to_channel_number = await self._fetch_channel_stream_ids(channel_groups_override)
            logger.info(f"Found {len(channel_stream_ids)} unique streams across all channels")

            # Fetch M3U accounts to map account IDs to names and max_streams
            logger.info("Fetching M3U accounts...")
            m3u_accounts_map = {}  # id -> name
            m3u_max_streams = {}   # id -> max_streams (considering profiles)
            try:
                m3u_accounts = await self.client.get_m3u_accounts()
                for account in m3u_accounts:
                    account_id = account["id"]
                    m3u_accounts_map[account_id] = account.get("name", f"M3U {account_id}")
                    # Calculate max_streams considering profiles (like Stats tab does)
                    profiles = account.get("profiles", [])
                    active_profiles = [p for p in profiles if p.get("is_active", True)]
                    if active_profiles:
                        # Sum max_streams from active profiles
                        profile_max = sum(p.get("max_streams", 0) for p in active_profiles)
                        m3u_max_streams[account_id] = profile_max if profile_max > 0 else account.get("max_streams", 0)
                    else:
                        m3u_max_streams[account_id] = account.get("max_streams", 0)
                logger.info(f"Found {len(m3u_accounts_map)} M3U accounts")
            except Exception as e:
                logger.warning(f"Failed to fetch M3U accounts: {e}")

            # Fetch all streams
            logger.info("Fetching stream details...")
            all_streams = await self._fetch_all_streams()

            # Filter to only streams that are in channels
            streams_to_probe = [s for s in all_streams if s["id"] in channel_stream_ids]

            # Sort streams by their lowest channel number (lowest first)
            streams_to_probe.sort(key=lambda s: stream_to_channel_number.get(s["id"], 999999))
            logger.info(f"Sorted {len(streams_to_probe)} streams by channel number")

            self._probe_progress_total = len(streams_to_probe)
            self._probe_progress_status = "probing"

            if self.parallel_probing_enabled:
                # ========== PARALLEL PROBING MODE ==========
                logger.info(f"Starting parallel probe of {len(streams_to_probe)} streams (filtered from {len(all_streams)} total)")

                # Track our own probe connections per M3U (separate from Dispatcharr's active connections)
                # This lets us know how many streams WE are currently probing per M3U
                probe_connections_lock = asyncio.Lock()
                probe_connections = {}  # m3u_id -> count of our active probes

                # Results lock for thread-safe updates
                results_lock = asyncio.Lock()

                async def probe_single_stream(stream: dict, display_string: str) -> tuple[str, dict]:
                    """Probe a single stream and return (status, stream_info)."""
                    stream_id = stream["id"]
                    stream_name = stream.get("name", f"Stream {stream_id}")
                    stream_url = stream.get("url", "")
                    m3u_account_id = stream.get("m3u_account")

                    try:
                        result = await self.probe_stream(stream_id, stream_url, stream_name)
                        probe_status = result.get("probe_status", "failed")
                        stream_info = {"id": stream_id, "name": stream_name, "url": stream_url}
                        return (probe_status, stream_info)
                    finally:
                        # Release our probe connection for this M3U
                        if m3u_account_id:
                            async with probe_connections_lock:
                                if m3u_account_id in probe_connections:
                                    probe_connections[m3u_account_id] = max(0, probe_connections[m3u_account_id] - 1)

                # Process streams with parallel probing
                pending_streams = list(streams_to_probe)  # Streams waiting to be probed
                active_tasks = {}  # task -> (stream, display_string)

                while pending_streams or active_tasks:
                    if not self._running:
                        self._probe_progress_status = "cancelled"
                        # Cancel active tasks
                        for task in active_tasks:
                            task.cancel()
                        break

                    # Get fresh M3U connection counts from Dispatcharr
                    dispatcharr_connections = await self._get_all_m3u_active_connections()

                    # Try to start new probes for streams that have available M3U capacity
                    streams_started_this_round = []
                    for stream in pending_streams:
                        m3u_account_id = stream.get("m3u_account")
                        stream_id = stream["id"]
                        stream_name = stream.get("name", f"Stream {stream_id}")
                        stream_url = stream.get("url", "")

                        # Build display string
                        display_parts = []
                        if stream_id in stream_to_channels and stream_to_channels[stream_id]:
                            channel_names = stream_to_channels[stream_id]
                            if len(channel_names) == 1:
                                display_parts.append(channel_names[0])
                            else:
                                display_parts.append(f"{channel_names[0]} (+{len(channel_names)-1})")
                        else:
                            display_parts.append("Unknown Channel")
                        display_parts.append(stream_name)

                        if m3u_account_id and m3u_account_id in m3u_accounts_map:
                            m3u_name = m3u_accounts_map[m3u_account_id]
                            display_string = f"{display_parts[0]}: {display_parts[1]} | {m3u_name}"
                        else:
                            display_string = f"{display_parts[0]}: {display_parts[1]}"

                        # Check M3U capacity
                        can_probe = True
                        skip_reason = None

                        if m3u_account_id:
                            max_streams = m3u_max_streams.get(m3u_account_id, 0)
                            if max_streams > 0:
                                # Total connections = Dispatcharr active + our active probes
                                dispatcharr_active = dispatcharr_connections.get(m3u_account_id, 0)
                                async with probe_connections_lock:
                                    our_probes = probe_connections.get(m3u_account_id, 0)
                                total_active = dispatcharr_active + our_probes

                                if total_active >= max_streams:
                                    # Check if we have any active probes for this M3U
                                    # If we do, wait for them to finish (don't skip yet)
                                    if our_probes > 0:
                                        can_probe = False  # Wait, don't skip
                                    else:
                                        # No probes running, Dispatcharr is using all connections
                                        m3u_name = m3u_accounts_map.get(m3u_account_id, f"M3U {m3u_account_id}")
                                        skip_reason = f"M3U '{m3u_name}' at max connections ({dispatcharr_active}/{max_streams})"
                                        logger.info(f"Skipping stream {stream_id} ({stream_name}): {skip_reason}")

                        if skip_reason:
                            # Skip this stream - M3U is at capacity with Dispatcharr connections
                            stream_info = {"id": stream_id, "name": stream_name, "url": stream_url, "reason": skip_reason}
                            async with results_lock:
                                self._probe_progress_skipped_count += 1
                                self._probe_skipped_streams.append(stream_info)
                            probed_count += 1
                            streams_started_this_round.append(stream)
                            self._probe_progress_current = probed_count
                            continue

                        if can_probe:
                            # Reserve a probe connection for this M3U
                            if m3u_account_id:
                                async with probe_connections_lock:
                                    probe_connections[m3u_account_id] = probe_connections.get(m3u_account_id, 0) + 1

                            # Start the probe task
                            task = asyncio.create_task(probe_single_stream(stream, display_string))
                            active_tasks[task] = (stream, display_string)
                            streams_started_this_round.append(stream)

                            # Update progress display with active streams
                            active_displays = [info[1] for info in active_tasks.values()]
                            if len(active_displays) == 1:
                                self._probe_progress_current_stream = active_displays[0]
                            else:
                                self._probe_progress_current_stream = f"[{len(active_displays)} parallel] {active_displays[0]}"

                    # Remove started streams from pending
                    for stream in streams_started_this_round:
                        pending_streams.remove(stream)

                    # If we have active tasks, wait for at least one to complete
                    if active_tasks:
                        done, _ = await asyncio.wait(active_tasks.keys(), return_when=asyncio.FIRST_COMPLETED)

                        for task in done:
                            stream, display_string = active_tasks.pop(task)
                            try:
                                probe_status, stream_info = task.result()
                                async with results_lock:
                                    if probe_status == "success":
                                        self._probe_progress_success_count += 1
                                        self._probe_success_streams.append(stream_info)
                                    else:
                                        self._probe_progress_failed_count += 1
                                        self._probe_failed_streams.append(stream_info)
                                probed_count += 1
                                self._probe_progress_current = probed_count
                            except asyncio.CancelledError:
                                pass
                            except Exception as e:
                                logger.error(f"Probe task failed: {e}")
                                probed_count += 1
                                self._probe_progress_current = probed_count
                    elif not pending_streams:
                        # No active tasks and no pending streams - we're done
                        break
                    else:
                        # All pending streams are waiting for M3U capacity - wait a bit and retry
                        await asyncio.sleep(0.5)
            else:
                # ========== SEQUENTIAL PROBING MODE ==========
                logger.info(f"Starting sequential probe of {len(streams_to_probe)} streams (filtered from {len(all_streams)} total)")

                for stream in streams_to_probe:
                    if not self._running:
                        self._probe_progress_status = "cancelled"
                        break

                    stream_id = stream["id"]
                    stream_name = stream.get("name", f"Stream {stream_id}")
                    stream_url = stream.get("url", "")

                    # Build display string: "channel(s): stream | M3U"
                    display_parts = []

                    # Add channel name(s)
                    if stream_id in stream_to_channels and stream_to_channels[stream_id]:
                        channel_names = stream_to_channels[stream_id]
                        if len(channel_names) == 1:
                            display_parts.append(channel_names[0])
                        else:
                            display_parts.append(f"{channel_names[0]} (+{len(channel_names)-1})")
                    else:
                        display_parts.append("Unknown Channel")

                    display_parts.append(stream_name)

                    m3u_account_id = stream.get("m3u_account")
                    if m3u_account_id and m3u_account_id in m3u_accounts_map:
                        m3u_name = m3u_accounts_map[m3u_account_id]
                        display_string = f"{display_parts[0]}: {display_parts[1]} | {m3u_name}"
                    else:
                        display_string = f"{display_parts[0]}: {display_parts[1]}"

                    self._probe_progress_current = probed_count + 1
                    self._probe_progress_current_stream = display_string

                    # Check if M3U is at max connections before probing (fresh check each time)
                    skip_reason = None
                    if m3u_account_id:
                        max_streams = m3u_max_streams.get(m3u_account_id, 0)
                        if max_streams > 0:
                            dispatcharr_connections = await self._get_all_m3u_active_connections()
                            current_streams = dispatcharr_connections.get(m3u_account_id, 0)
                            if current_streams >= max_streams:
                                m3u_name = m3u_accounts_map.get(m3u_account_id, f"M3U {m3u_account_id}")
                                skip_reason = f"M3U '{m3u_name}' at max connections ({current_streams}/{max_streams})"
                                logger.info(f"Skipping stream {stream_id} ({stream_name}): {skip_reason}")

                    if skip_reason:
                        # Skip this stream - M3U is at capacity
                        stream_info = {"id": stream_id, "name": stream_name, "url": stream_url, "reason": skip_reason}
                        self._probe_progress_skipped_count += 1
                        self._probe_skipped_streams.append(stream_info)
                        probed_count += 1
                        continue

                    result = await self.probe_stream(stream_id, stream_url, stream_name)

                    # Track success/failure
                    probe_status = result.get("probe_status", "failed")
                    stream_info = {"id": stream_id, "name": stream_name, "url": stream_url}
                    if probe_status == "success":
                        self._probe_progress_success_count += 1
                        self._probe_success_streams.append(stream_info)
                    else:
                        self._probe_progress_failed_count += 1
                        self._probe_failed_streams.append(stream_info)

                    probed_count += 1
                    await asyncio.sleep(0.5)  # Rate limiting

            logger.info(f"Completed probing {probed_count} streams")
            self._probe_progress_status = "completed"
            self._probe_progress_current_stream = ""

            # Save to probe history
            self._save_probe_history(start_time, probed_count)

            return {"status": "completed", "probed": probed_count}
        except Exception as e:
            logger.error(f"Probe all streams failed: {e}")
            self._probe_progress_status = "failed"
            self._probe_progress_current_stream = ""

            # Save failed run to history
            self._save_probe_history(start_time, probed_count, error=str(e))

            return {"status": "failed", "error": str(e), "probed": probed_count}
        finally:
            self._probing_in_progress = False

    def get_probe_progress(self) -> dict:
        """Get current probe all streams progress."""
        return {
            "in_progress": self._probing_in_progress,
            "total": self._probe_progress_total,
            "current": self._probe_progress_current,
            "status": self._probe_progress_status,
            "current_stream": self._probe_progress_current_stream,
            "success_count": self._probe_progress_success_count,
            "failed_count": self._probe_progress_failed_count,
            "skipped_count": self._probe_progress_skipped_count,
            "percentage": round((self._probe_progress_current / self._probe_progress_total * 100) if self._probe_progress_total > 0 else 0, 1)
        }

    def get_probe_results(self) -> dict:
        """Get detailed results of the last probe all streams operation."""
        return {
            "success_streams": self._probe_success_streams,
            "failed_streams": self._probe_failed_streams,
            "skipped_streams": self._probe_skipped_streams,
            "success_count": len(self._probe_success_streams),
            "failed_count": len(self._probe_failed_streams),
            "skipped_count": len(self._probe_skipped_streams)
        }

    def _save_probe_history(self, start_time: datetime, total: int, error: str = None):
        """Save a probe run to history (keeps last 5 runs)."""
        end_time = datetime.utcnow()
        duration_seconds = int((end_time - start_time).total_seconds())

        history_entry = {
            "timestamp": start_time.isoformat() + "Z",
            "end_timestamp": end_time.isoformat() + "Z",
            "duration_seconds": duration_seconds,
            "total": total,
            "success_count": self._probe_progress_success_count,
            "failed_count": self._probe_progress_failed_count,
            "skipped_count": self._probe_progress_skipped_count,
            "status": "failed" if error else ("completed" if self._probe_progress_status == "completed" else self._probe_progress_status),
            "error": error,
            "success_streams": list(self._probe_success_streams),  # Copy the list
            "failed_streams": list(self._probe_failed_streams),    # Copy the list
            "skipped_streams": list(self._probe_skipped_streams),  # Copy the list
        }

        # Add to history and keep only last 5
        self._probe_history.insert(0, history_entry)
        self._probe_history = self._probe_history[:5]

        logger.info(f"Saved probe history entry: {total} streams, {self._probe_progress_success_count} success, {self._probe_progress_failed_count} failed, {self._probe_progress_skipped_count} skipped")

    def get_probe_history(self) -> list:
        """Get probe run history (last 5 runs)."""
        return self._probe_history

    @staticmethod
    def get_all_stats() -> list:
        """Get all stream stats from database."""
        session = get_session()
        try:
            stats = session.query(StreamStats).all()
            return [s.to_dict() for s in stats]
        finally:
            session.close()

    @staticmethod
    def get_stats_by_stream_ids(stream_ids: list[int]) -> dict[int, dict]:
        """Get stats for multiple streams by their IDs."""
        if not stream_ids:
            return {}
        session = get_session()
        try:
            stats = session.query(StreamStats).filter(
                StreamStats.stream_id.in_(stream_ids)
            ).all()
            return {s.stream_id: s.to_dict() for s in stats}
        finally:
            session.close()

    @staticmethod
    def get_stats_by_stream_id(stream_id: int) -> Optional[dict]:
        """Get stats for a specific stream."""
        session = get_session()
        try:
            stats = (
                session.query(StreamStats).filter_by(stream_id=stream_id).first()
            )
            return stats.to_dict() if stats else None
        finally:
            session.close()

    @staticmethod
    def get_stats_summary() -> dict:
        """Get summary of probe statistics."""
        from sqlalchemy import func

        session = get_session()
        try:
            total = session.query(func.count(StreamStats.id)).scalar() or 0
            success = (
                session.query(func.count(StreamStats.id))
                .filter(StreamStats.probe_status == "success")
                .scalar()
                or 0
            )
            failed = (
                session.query(func.count(StreamStats.id))
                .filter(StreamStats.probe_status == "failed")
                .scalar()
                or 0
            )
            timeout = (
                session.query(func.count(StreamStats.id))
                .filter(StreamStats.probe_status == "timeout")
                .scalar()
                or 0
            )
            pending = (
                session.query(func.count(StreamStats.id))
                .filter(StreamStats.probe_status == "pending")
                .scalar()
                or 0
            )

            return {
                "total": total,
                "success": success,
                "failed": failed,
                "timeout": timeout,
                "pending": pending,
            }
        finally:
            session.close()

    @staticmethod
    def delete_stats(stream_id: int) -> bool:
        """Delete stats for a specific stream."""
        session = get_session()
        try:
            deleted = (
                session.query(StreamStats)
                .filter_by(stream_id=stream_id)
                .delete()
            )
            session.commit()
            return deleted > 0
        except Exception as e:
            logger.error(f"Failed to delete stats for stream {stream_id}: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    @staticmethod
    def purge_old_stats(days: int = 30):
        """Remove stats for streams not probed in specified days."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        session = get_session()
        try:
            deleted = (
                session.query(StreamStats)
                .filter(StreamStats.last_probed < cutoff)
                .delete()
            )
            session.commit()
            if deleted > 0:
                logger.info(f"Purged {deleted} old stream stats")
        except Exception as e:
            logger.error(f"Failed to purge old stats: {e}")
            session.rollback()
        finally:
            session.close()


# Global prober instance
_prober: Optional[StreamProber] = None


def get_prober() -> Optional[StreamProber]:
    """Get the global prober instance."""
    logger.debug(f"get_prober() called, returning: {_prober is not None} (instance exists: {_prober is not None})")
    return _prober


def set_prober(prober: StreamProber):
    """Set the global prober instance."""
    global _prober
    _prober = prober
    logger.info(f"Stream prober instance set: {prober is not None}")
