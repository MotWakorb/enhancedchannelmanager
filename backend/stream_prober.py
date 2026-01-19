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
from pathlib import Path
import os

import httpx

from database import get_session
from models import StreamStats

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_PROBE_TIMEOUT = 30  # seconds
DEFAULT_PROBE_BATCH_SIZE = 10  # streams per cycle
DEFAULT_PROBE_INTERVAL_HOURS = 24  # daily
BITRATE_SAMPLE_DURATION = 8  # seconds to sample stream for bitrate measurement

# Probe history persistence
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
PROBE_HISTORY_FILE = CONFIG_DIR / "probe_history.json"


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
        user_timezone: str = "",  # IANA timezone name
        probe_channel_groups: list[str] = None,  # List of group names to probe (empty/None = all groups)
        bitrate_sample_duration: int = 10,  # Duration in seconds to sample stream for bitrate (10, 20, or 30)
        parallel_probing_enabled: bool = True,  # Probe streams from different M3Us simultaneously
        skip_recently_probed_hours: int = 0,  # Skip streams probed within last N hours (0 = always probe)
        refresh_m3us_before_probe: bool = True,  # Refresh all M3U accounts before starting probe
        auto_reorder_after_probe: bool = False,  # Automatically reorder streams in channels after probe completes
        deprioritize_failed_streams: bool = True,  # Deprioritize failed streams in smart sort
        stream_sort_priority: list[str] = None,  # Priority order for Smart Sort criteria
        stream_sort_enabled: dict[str, bool] = None,  # Which criteria are enabled for Smart Sort
        stream_fetch_page_limit: int = 200,  # Max pages when fetching streams (200 * 500 = 100K streams)
    ):
        self.client = client
        self.probe_timeout = probe_timeout
        self.probe_batch_size = probe_batch_size
        self.probe_interval_hours = probe_interval_hours
        self.user_timezone = user_timezone
        self.probe_channel_groups = probe_channel_groups or []
        self.bitrate_sample_duration = bitrate_sample_duration
        self.parallel_probing_enabled = parallel_probing_enabled
        self.skip_recently_probed_hours = skip_recently_probed_hours
        self.refresh_m3us_before_probe = refresh_m3us_before_probe
        self.auto_reorder_after_probe = auto_reorder_after_probe
        self.deprioritize_failed_streams = deprioritize_failed_streams
        self.stream_fetch_page_limit = stream_fetch_page_limit
        logger.info(f"[PROBER-INIT] auto_reorder_after_probe={auto_reorder_after_probe}")
        # Smart Sort configuration
        self.stream_sort_priority = stream_sort_priority or ["resolution", "bitrate", "framerate"]
        self.stream_sort_enabled = stream_sort_enabled or {"resolution": True, "bitrate": True, "framerate": True}
        self._probe_cancelled = False  # Controls cancellation of in-progress probe
        self._probing_in_progress = False
        # Progress tracking for probe all streams
        self._probe_progress_total = 0
        self._probe_progress_current = 0
        self._probe_progress_status = "idle"
        self._probe_progress_current_stream = ""
        self._probe_progress_success_count = 0
        self._probe_progress_failed_count = 0
        self._probe_success_streams = []  # List of {id, name, url} for successful probes
        self._probe_failed_streams = []   # List of {id, name, url, error} for failed probes
        self._probe_skipped_streams = []  # List of {id, name, url, reason} for skipped probes (e.g., M3U at max connections)
        self._probe_progress_skipped_count = 0
        # Probe history - list of last 5 probe runs
        self._probe_history = []  # List of {timestamp, total, success_count, failed_count, status, success_streams, failed_streams}

        # Load probe history from disk on initialization
        self._load_probe_history()

    def _load_probe_history(self):
        """Load probe history from persistent storage."""
        try:
            if PROBE_HISTORY_FILE.exists():
                with open(PROBE_HISTORY_FILE, 'r') as f:
                    self._probe_history = json.load(f)
                logger.info(f"Loaded {len(self._probe_history)} probe history entries from {PROBE_HISTORY_FILE}")
            else:
                logger.info(f"No probe history file found at {PROBE_HISTORY_FILE}, starting fresh")
        except Exception as e:
            logger.error(f"Failed to load probe history from {PROBE_HISTORY_FILE}: {e}")
            self._probe_history = []

    def _persist_probe_history(self):
        """Persist probe history to disk."""
        try:
            # Ensure config directory exists
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)

            with open(PROBE_HISTORY_FILE, 'w') as f:
                json.dump(self._probe_history, f, indent=2)
            logger.debug(f"Persisted {len(self._probe_history)} probe history entries to {PROBE_HISTORY_FILE}")
        except Exception as e:
            logger.error(f"Failed to persist probe history to {PROBE_HISTORY_FILE}: {e}")

    async def start(self):
        """Initialize the stream prober (check ffprobe availability).

        Note: Scheduled probing is now handled by the task engine (StreamProbeTask).
        This method only validates that ffprobe is available for on-demand probing.
        """
        logger.info("StreamProber.start() called")

        # Check ffprobe availability
        ffprobe_available = check_ffprobe_available()
        logger.info(f"ffprobe availability check: {ffprobe_available}")

        if not ffprobe_available:
            logger.error("ffprobe not found - stream probing will not be available")
            logger.warning("Install ffprobe (part of ffmpeg) to enable stream probing")
            return

        logger.info(
            f"StreamProber initialized (batch: {self.probe_batch_size}, timeout: {self.probe_timeout}s)"
        )

    async def stop(self):
        """Stop the stream prober and cancel any in-progress probes."""
        logger.info("StreamProber stopping...")
        self._probe_cancelled = True
        logger.info("StreamProber stopped")

    def cancel_probe(self) -> dict:
        """Cancel an in-progress probe operation.

        Returns:
            Dict with status of the cancellation.
        """
        if not self._probing_in_progress:
            return {"status": "no_probe_running", "message": "No probe is currently running"}

        logger.info("Cancelling in-progress probe...")
        self._probe_cancelled = True
        # The probe loop will detect _probe_cancelled=True and set status to "cancelled"
        return {"status": "cancelling", "message": "Probe cancellation requested"}

    def force_reset_probe_state(self) -> dict:
        """Force reset the probe state. Use this if a probe got stuck.

        Returns:
            Dict with status of the reset.
        """
        was_in_progress = self._probing_in_progress
        logger.warning(f"Force resetting probe state (was_in_progress={was_in_progress})")

        self._probing_in_progress = False
        self._probe_cancelled = True  # Signal any running probe to stop
        self._probe_progress_status = "idle"
        self._probe_progress_current_stream = ""

        return {
            "status": "reset",
            "message": f"Probe state forcibly reset (was_in_progress={was_in_progress})"
        }

    async def _probe_stale_streams(self):
        """Find and probe streams that haven't been probed recently."""
        if self._probing_in_progress:
            logger.debug("Probe already in progress, skipping")
            return

        self._probing_in_progress = True
        self._probe_cancelled = False  # Reset cancellation flag
        start_time = datetime.utcnow()
        probed_count = 0

        # Reset tracking variables
        self._probe_success_streams = []
        self._probe_failed_streams = []
        self._probe_skipped_streams = []
        self._probe_progress_total = 0
        self._probe_progress_current = 0
        self._probe_progress_success_count = 0
        self._probe_progress_failed_count = 0
        self._probe_progress_skipped_count = 0
        self._probe_progress_status = "probing"
        self._probe_progress_current_stream = ""

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

                # Set progress tracking
                self._probe_progress_total = len(to_probe)

                # Build stream_id -> channel mapping for auto-reorder
                stream_to_channels = {}
                for stream in streams:
                    stream_id = stream["id"]
                    channel_id = stream.get("channel")
                    if channel_id:
                        if stream_id not in stream_to_channels:
                            stream_to_channels[stream_id] = []
                        stream_to_channels[stream_id].append(channel_id)

                for stream in to_probe:
                    if self._probe_cancelled:
                        break

                    stream_id = stream["id"]
                    stream_url = stream.get("url")
                    stream_name = stream.get("name")

                    self._probe_progress_current = probed_count + 1
                    self._probe_progress_current_stream = stream_name or f"Stream {stream_id}"

                    result = await self.probe_stream(stream_id, stream_url, stream_name)

                    # Track success/failure
                    probe_status = result.get("probe_status", "failed")
                    stream_info = {"id": stream_id, "name": stream_name, "url": stream_url}
                    if probe_status == "success":
                        self._probe_progress_success_count += 1
                        self._probe_success_streams.append(stream_info)
                    else:
                        self._probe_progress_failed_count += 1
                        # Include error message for failed streams
                        stream_info["error"] = result.get("error_message", "Unknown error")
                        self._probe_failed_streams.append(stream_info)

                    probed_count += 1
                    await asyncio.sleep(1)  # Rate limiting

                logger.info(f"Scheduled probe completed: {probed_count} streams probed")
                self._probe_progress_status = "completed"
                self._probe_progress_current_stream = ""

                # Auto-reorder streams if configured
                reordered_channels = []
                if self.auto_reorder_after_probe:
                    logger.info("=" * 60)
                    logger.info("[AUTO-REORDER] Starting automatic stream reorder after probe")
                    logger.info(f"[AUTO-REORDER] Configuration:")
                    logger.info(f"[AUTO-REORDER]   Sort priority: {self.stream_sort_priority}")
                    logger.info(f"[AUTO-REORDER]   Sort enabled: {self.stream_sort_enabled}")
                    logger.info(f"[AUTO-REORDER]   Deprioritize failed: {self.deprioritize_failed_streams}")
                    logger.info(f"[AUTO-REORDER]   Channel groups filter: {self.probe_channel_groups or 'ALL GROUPS'}")
                    logger.info("=" * 60)
                    self._probe_progress_status = "reordering"
                    self._probe_progress_current_stream = "Reordering streams..."
                    try:
                        # Use the same channel groups filter as configured
                        channel_groups_override = self.probe_channel_groups if self.probe_channel_groups else None
                        reordered_channels = await self._auto_reorder_channels(channel_groups_override, stream_to_channels)
                        logger.info("=" * 60)
                        logger.info(f"[AUTO-REORDER] Completed: {len(reordered_channels)} channels reordered")
                        for ch in reordered_channels:
                            logger.info(f"[AUTO-REORDER]   - {ch['channel_name']} (id={ch['channel_id']}): {ch['stream_count']} streams")
                        logger.info("=" * 60)
                    except Exception as e:
                        logger.error(f"[AUTO-REORDER] Failed: {e}")

                # Save to probe history
                self._save_probe_history(start_time, probed_count, reordered_channels=reordered_channels)

            finally:
                session.close()
        except Exception as e:
            logger.error(f"Scheduled probe failed: {e}")
            self._probe_progress_status = "failed"
            self._probe_progress_current_stream = ""

            # Save failed run to history
            self._save_probe_history(start_time, probed_count, error=str(e))
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
            "error",  # Show errors in stderr (was "quiet" which suppressed everything)
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-user_agent", "VLC/3.0.20 LibVLC/3.0.20",  # Mimic VLC to avoid server rejections
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
            error_text = stderr.decode().strip()[:500] if stderr else ""
            if not error_text:
                error_text = f"Exit code {process.returncode} (no stderr output)"
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

            headers = {"User-Agent": "VLC/3.0.20 LibVLC/3.0.20"}
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
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
        page_limit = self.stream_fetch_page_limit  # Configurable: pages * 500 = max streams
        while True:
            try:
                result = await self.client.get_streams(page=page, page_size=500)
                streams = result.get("results", [])
                all_streams.extend(streams)
                if not result.get("next"):
                    break
                page += 1
                if page > page_limit:
                    logger.warning(
                        f"[PROBE-MATCH] Pagination limit reached ({page_limit} pages, {len(all_streams)} streams). "
                        f"Some streams may be missing. Increase 'Stream Fetch Page Limit' in settings if needed."
                    )
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
        logger.debug(f"[PROBE-FILTER] _fetch_channel_stream_ids called with override={channel_groups_override}")
        logger.debug(f"[PROBE-FILTER] self.probe_channel_groups={self.probe_channel_groups}")

        channel_stream_ids = set()
        stream_to_channels = {}  # stream_id -> list of channel names
        stream_to_channel_number = {}  # stream_id -> lowest channel number (for sorting)

        # Determine which groups to filter by
        groups_to_filter = channel_groups_override if channel_groups_override is not None else self.probe_channel_groups
        logger.debug(f"[PROBE-FILTER] groups_to_filter (after resolution)={groups_to_filter}")

        # If specific groups are selected, fetch all groups first to filter
        selected_group_ids = set()
        if groups_to_filter:
            try:
                all_groups = await self.client.get_channel_groups()
                available_group_names = [g.get("name") for g in all_groups]
                logger.debug(f"[PROBE-FILTER] Requested groups: {groups_to_filter}")
                logger.debug(f"[PROBE-FILTER] Available groups: {available_group_names}")

                matched_groups = []
                unmatched_groups = []
                for group in all_groups:
                    group_name = group.get("name")
                    if group_name in groups_to_filter:
                        selected_group_ids.add(group["id"])
                        matched_groups.append(f"{group_name} (id={group['id']})")

                for requested in groups_to_filter:
                    if requested not in [g.get("name") for g in all_groups]:
                        unmatched_groups.append(requested)

                logger.debug(f"[PROBE-FILTER] Matched groups: {matched_groups}")
                if unmatched_groups:
                    logger.warning(f"[PROBE-FILTER] Requested groups NOT FOUND: {unmatched_groups}")
                logger.debug(f"[PROBE-FILTER] Filtering to {len(selected_group_ids)} groups")
            except Exception as e:
                logger.error(f"Failed to fetch channel groups for filtering: {e}")
                # Continue without filtering if we can't fetch groups

        page = 1
        total_channels_seen = 0
        channels_included = 0
        channels_excluded_wrong_group = 0
        channels_with_no_streams = 0
        excluded_channel_names = []  # Track names for debug logging

        while True:
            try:
                result = await self.client.get_channels(page=page, page_size=500)
                channels = result.get("results", [])
                for channel in channels:
                    total_channels_seen += 1
                    channel_name = channel.get("name", f"Channel {channel.get('id', 'Unknown')}")
                    channel_group_id = channel.get("channel_group_id")

                    # If groups are selected, filter by channel_group_id
                    if selected_group_ids:
                        if channel_group_id not in selected_group_ids:
                            channels_excluded_wrong_group += 1
                            excluded_channel_names.append(channel_name)
                            continue  # Skip channels not in selected groups

                    channel_number = channel.get("channel_number", 999999)  # Default high number for sorting
                    # Each channel has a "streams" field which is a list of stream IDs
                    stream_ids = channel.get("streams", [])

                    if not stream_ids:
                        channels_with_no_streams += 1
                        logger.debug(f"[PROBE-FILTER] Channel '{channel_name}' has no streams, skipping")
                        continue

                    channels_included += 1
                    channel_stream_ids.update(stream_ids)
                    logger.debug(f"[PROBE-FILTER] Including channel '{channel_name}' with {len(stream_ids)} stream(s)")

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

        # Log summary of channel filtering
        logger.debug(f"[PROBE-FILTER] Channel filtering summary:")
        logger.debug(f"[PROBE-FILTER]   Total channels seen: {total_channels_seen}")
        logger.debug(f"[PROBE-FILTER]   Channels included: {channels_included}")
        if selected_group_ids:
            logger.debug(f"[PROBE-FILTER]   Channels excluded (wrong group): {channels_excluded_wrong_group}")
        if channels_with_no_streams > 0:
            logger.debug(f"[PROBE-FILTER]   Channels with no streams: {channels_with_no_streams}")
        logger.debug(f"[PROBE-FILTER]   Unique streams to probe: {len(channel_stream_ids)}")

        # Log excluded channels if there are any (limit to first 20 to avoid log spam)
        if excluded_channel_names:
            sample = excluded_channel_names[:20]
            logger.debug(f"[PROBE-FILTER] Excluded channels (first 20): {sample}")
            if len(excluded_channel_names) > 20:
                logger.debug(f"[PROBE-FILTER] ... and {len(excluded_channel_names) - 20} more")

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

    async def _auto_reorder_channels(self, channel_groups_override: list[str] = None, stream_to_channels: dict = None) -> list[dict]:
        """
        Auto-reorder streams in all channels from the selected groups using smart sort.
        Returns a list of dicts with {channel_id, channel_name, stream_count} for channels that were reordered.
        """
        reordered = []

        try:
            # Determine which groups to filter by
            groups_to_filter = channel_groups_override if channel_groups_override is not None else self.probe_channel_groups
            logger.info(f"[AUTO-REORDER] groups_to_filter={groups_to_filter}, channel_groups_override={channel_groups_override}, self.probe_channel_groups={self.probe_channel_groups}")

            # Get selected group IDs
            selected_group_ids = set()
            if groups_to_filter:
                try:
                    all_groups = await self.client.get_channel_groups()
                    available_group_names = [g.get("name") for g in all_groups]
                    logger.info(f"[AUTO-REORDER] Available groups: {available_group_names[:10]}... (total: {len(all_groups)})")
                    for group in all_groups:
                        if group.get("name") in groups_to_filter:
                            selected_group_ids.add(group["id"])
                    logger.info(f"[AUTO-REORDER] Filtering to {len(selected_group_ids)} selected groups (matched: {selected_group_ids})")
                except Exception as e:
                    logger.error(f"Failed to fetch channel groups for auto-reorder: {e}")
                    return []

            # Fetch all channels and filter by selected groups
            page = 1
            channels_to_reorder = []
            while True:
                try:
                    result = await self.client.get_channels(page=page, page_size=500)
                    channels = result.get("results", [])
                    for channel in channels:
                        # Filter by channel_group_id if groups selected
                        if selected_group_ids:
                            channel_group_id = channel.get("channel_group_id")
                            if channel_group_id not in selected_group_ids:
                                continue

                        # Add all channels - we'll check stream count later when we fetch full details
                        # The paginated list might not include full stream data
                        channels_to_reorder.append(channel)

                    if not result.get("next"):
                        break
                    page += 1
                    if page > 50:  # Safety limit
                        break
                except Exception as e:
                    logger.error(f"Failed to fetch channels page {page} for auto-reorder: {e}")
                    break

            logger.info(f"[AUTO-REORDER] Found {len(channels_to_reorder)} channels to potentially reorder")

            # For each channel, fetch full details, get stream stats, and reorder
            for channel in channels_to_reorder:
                try:
                    channel_id = channel["id"]
                    channel_name = channel.get("name", f"Channel {channel_id}")

                    # Fetch full channel details to get streams list
                    full_channel = await self.client.get_channel(channel_id)
                    stream_ids = full_channel.get("streams", [])

                    if len(stream_ids) <= 1:
                        logger.debug(f"[AUTO-REORDER] Channel {channel_id} ({channel_name}) - Skipping, only {len(stream_ids)} streams")
                        continue  # Skip if 0 or 1 streams

                    logger.info(f"[AUTO-REORDER] Processing channel {channel_id} ({channel_name}) with {len(stream_ids)} streams: {stream_ids}")

                    # Fetch stream stats for this channel's streams (uses get_session and StreamStats imported at top of file)
                    logger.info(f"[AUTO-REORDER] Channel {channel_id}: Opening database session...")
                    with get_session() as session:
                        logger.info(f"[AUTO-REORDER] Channel {channel_id}: Querying stats for stream_ids: {stream_ids}")
                        stats_records = session.query(StreamStats).filter(
                            StreamStats.stream_id.in_(stream_ids)
                        ).all()
                        logger.info(f"[AUTO-REORDER] Channel {channel_id}: Query returned {len(stats_records)} records")

                        # Build stats map
                        stats_map = {stat.stream_id: stat for stat in stats_records}
                        logger.info(f"[AUTO-REORDER] Channel {channel_id}: Found stats for {len(stats_map)}/{len(stream_ids)} streams")

                        # Sort streams using smart sort logic (similar to frontend)
                        sorted_stream_ids = self._smart_sort_streams(stream_ids, stats_map, channel_name)
                        logger.info(f"[AUTO-REORDER] Channel {channel_id}: Original order: {stream_ids}")
                        logger.info(f"[AUTO-REORDER] Channel {channel_id}: Sorted order:   {sorted_stream_ids}")
                        logger.info(f"[AUTO-REORDER] Channel {channel_id}: Order changed: {sorted_stream_ids != stream_ids}")

                        # Only update if order changed
                        if sorted_stream_ids != stream_ids:
                            # Build detailed stream info for before/after
                            streams_before = []
                            streams_after = []
                            for idx, stream_id in enumerate(stream_ids):
                                stat = stats_map.get(stream_id)
                                streams_before.append({
                                    "id": stream_id,
                                    "name": stat.stream_name if stat else f"Stream {stream_id}",
                                    "position": idx + 1,
                                    "status": stat.probe_status if stat else "unknown",
                                    "resolution": stat.resolution if stat else None,
                                    "bitrate": stat.bitrate if stat else None,
                                })

                            for idx, stream_id in enumerate(sorted_stream_ids):
                                stat = stats_map.get(stream_id)
                                streams_after.append({
                                    "id": stream_id,
                                    "name": stat.stream_name if stat else f"Stream {stream_id}",
                                    "position": idx + 1,
                                    "status": stat.probe_status if stat else "unknown",
                                    "resolution": stat.resolution if stat else None,
                                    "bitrate": stat.bitrate if stat else None,
                                })

                            # Debug logging: log the proposed changes
                            logger.debug(f"[AUTO-REORDER] Channel {channel_id} ({channel_name}) - Proposing reorder:")
                            before_str = [f"{s['name']} (pos={s['position']}, status={s['status']}, res={s['resolution']}, br={s['bitrate']})" for s in streams_before]
                            after_str = [f"{s['name']} (pos={s['position']}, status={s['status']}, res={s['resolution']}, br={s['bitrate']})" for s in streams_after]
                            logger.debug(f"[AUTO-REORDER]   Before: {before_str}")
                            logger.debug(f"[AUTO-REORDER]   After:  {after_str}")

                            # Execute the reorder
                            try:
                                await self.client.update_channel(channel_id, {"streams": sorted_stream_ids})
                                logger.debug(f"[AUTO-REORDER] Successfully reordered channel {channel_id} ({channel_name})")
                            except Exception as update_err:
                                logger.error(f"[AUTO-REORDER] Failed to update channel {channel_id} ({channel_name}): {update_err}")
                                raise  # Re-raise to be caught by outer exception handler

                            reordered.append({
                                "channel_id": channel_id,
                                "channel_name": channel_name,
                                "stream_count": len(stream_ids),
                                "streams_before": streams_before,
                                "streams_after": streams_after,
                            })
                        else:
                            logger.debug(f"[AUTO-REORDER] Channel {channel_id} ({channel_name}) - No reorder needed (already in correct order)")

                except Exception as e:
                    logger.error(f"Failed to reorder channel {channel.get('id', 'unknown')}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Auto-reorder channels failed: {e}")

        return reordered

    def _smart_sort_streams(self, stream_ids: list[int], stats_map: dict, channel_name: str = "unknown") -> list[int]:
        """
        Sort stream IDs using smart sort logic based on stream stats.
        Uses configurable sort priority and enabled criteria from settings.
        Prioritizes working streams and sorts by enabled criteria in priority order.

        Args:
            stream_ids: List of stream IDs to sort
            stats_map: Map of stream_id -> StreamStats
            channel_name: Channel name for logging purposes
        """
        # Get active sort criteria (enabled and in priority order)
        active_criteria = [
            criterion for criterion in self.stream_sort_priority
            if self.stream_sort_enabled.get(criterion, False)
        ]

        logger.info(f"[SMART-SORT] Channel '{channel_name}': Sorting {len(stream_ids)} streams")
        logger.info(f"[SMART-SORT] Sort config: priority={self.stream_sort_priority}, enabled={self.stream_sort_enabled}")
        logger.info(f"[SMART-SORT] Active criteria (in order): {active_criteria}")
        logger.info(f"[SMART-SORT] Deprioritize failed streams: {self.deprioritize_failed_streams}")

        # Log each stream's stats before sorting
        for stream_id in stream_ids:
            stat = stats_map.get(stream_id)
            if stat:
                logger.debug(f"[SMART-SORT]   Stream {stream_id} ({stat.stream_name}): "
                            f"status={stat.probe_status}, res={stat.resolution}, "
                            f"bitrate={stat.bitrate}, fps={stat.fps}")
            else:
                logger.debug(f"[SMART-SORT]   Stream {stream_id}: NO STATS AVAILABLE")

        def get_sort_value(stream_id: int) -> tuple:
            stat = stats_map.get(stream_id)
            stream_name = stat.stream_name if stat else f"Stream {stream_id}"

            # Deprioritize failed streams if enabled
            if self.deprioritize_failed_streams:
                if not stat or stat.probe_status in ('failed', 'timeout', 'pending'):
                    logger.debug(f"[SMART-SORT]   {stream_name}: DEPRIORITIZED (status={stat.probe_status if stat else 'no_stats'})")
                    # Return tuple with 1 as first element to sort to bottom
                    return (1,) + tuple(0 for _ in active_criteria)

            if not stat or stat.probe_status != 'success':
                logger.debug(f"[SMART-SORT]   {stream_name}: No successful probe data")
                return (0,) + tuple(0 for _ in active_criteria)

            # Build sort values based on active criteria in priority order
            sort_values = [0]  # First element: 0 = successful stream

            for criterion in active_criteria:
                if criterion == "resolution":
                    # Parse resolution (e.g., "1920x1080" -> width * height)
                    resolution_value = 0
                    if stat.resolution:
                        try:
                            parts = stat.resolution.split('x')
                            if len(parts) == 2:
                                resolution_value = int(parts[0]) * int(parts[1])
                        except:
                            pass
                    # Negate for descending sort (higher values first)
                    sort_values.append(-resolution_value)

                elif criterion == "bitrate":
                    bitrate_value = stat.bitrate or 0
                    sort_values.append(-bitrate_value)

                elif criterion == "framerate":
                    # Parse fps - could be string like "29.97" or "30"
                    framerate_value = 0
                    if stat.fps:
                        try:
                            framerate_value = float(stat.fps)
                        except:
                            pass
                    sort_values.append(-framerate_value)

            logger.debug(f"[SMART-SORT]   {stream_name}: sort_tuple={tuple(sort_values)} "
                        f"(res={stat.resolution}, br={stat.bitrate}, fps={stat.fps})")
            return tuple(sort_values)

        # Sort stream IDs by their stats
        sorted_ids = sorted(stream_ids, key=get_sort_value)

        # Log the final sorted order
        logger.info(f"[SMART-SORT] Channel '{channel_name}' sorted order:")
        for idx, stream_id in enumerate(sorted_ids):
            stat = stats_map.get(stream_id)
            stream_name = stat.stream_name if stat else f"Stream {stream_id}"
            status = stat.probe_status if stat else "no_stats"
            res = stat.resolution if stat else "?"
            logger.info(f"[SMART-SORT]   #{idx+1}: {stream_name} (id={stream_id}, status={status}, res={res})")

        return sorted_ids

    async def probe_all_streams(self, channel_groups_override: list[str] = None, skip_m3u_refresh: bool = False):
        """Probe all streams that are in channels (runs in background).

        Uses parallel probing - streams from different M3U accounts (or same M3U with
        available capacity) are probed concurrently for faster completion.

        Args:
            channel_groups_override: Optional list of channel group names to filter by.
                                    If None, uses self.probe_channel_groups.
                                    If empty list, probes all groups.
            skip_m3u_refresh: If True, skip M3U refresh even if configured.
                             Use this for on-demand probes from the UI.
        """
        logger.debug(f"[PROBE] probe_all_streams called with channel_groups_override={channel_groups_override}, skip_m3u_refresh={skip_m3u_refresh}")
        logger.debug(f"[PROBE] self.probe_channel_groups={self.probe_channel_groups}")

        if self._probing_in_progress:
            logger.warning("Probe already in progress")
            return {"status": "already_running"}

        self._probing_in_progress = True
        self._probe_cancelled = False  # Reset cancellation flag
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
            # Refresh M3U accounts if configured AND not explicitly skipped
            # On-demand probes from UI should skip refresh; only scheduled probes refresh
            if self.refresh_m3us_before_probe and not skip_m3u_refresh:
                logger.info("Refreshing all M3U accounts before probing...")
                self._probe_progress_status = "refreshing"
                self._probe_progress_current_stream = "Refreshing M3U accounts..."
                try:
                    await self.client.refresh_all_m3u_accounts()
                    logger.info("M3U refresh triggered successfully")
                    # Wait a reasonable amount of time for refresh to complete
                    # Since Dispatcharr doesn't provide refresh status, we wait 60 seconds
                    await asyncio.sleep(60)
                    logger.info("M3U refresh wait period completed")
                except Exception as e:
                    logger.warning(f"Failed to refresh M3U accounts: {e}")
                    logger.info("Continuing with probe despite refresh failure")
            elif skip_m3u_refresh:
                logger.info("Skipping M3U refresh (on-demand probe)")

            # Fetch all channel stream IDs and channel mappings
            self._probe_progress_status = "fetching"
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
            logger.debug(f"[PROBE-MATCH] Fetched {len(all_streams)} total streams from Dispatcharr")

            # Log the stream IDs we're looking for
            logger.debug(f"[PROBE-MATCH] Looking for {len(channel_stream_ids)} channel stream IDs: {sorted(channel_stream_ids)}")

            # Get all stream IDs from Dispatcharr
            all_stream_ids = {s["id"] for s in all_streams}
            logger.debug(f"[PROBE-MATCH] Dispatcharr returned {len(all_stream_ids)} unique stream IDs")

            # Find which channel stream IDs are missing from Dispatcharr's stream list
            missing_ids = channel_stream_ids - all_stream_ids
            if missing_ids:
                logger.warning(f"[PROBE-MATCH] {len(missing_ids)} channel stream IDs NOT FOUND in Dispatcharr streams: {sorted(missing_ids)}")
                # Log which channels reference these missing streams
                for missing_id in missing_ids:
                    channel_names = stream_to_channels.get(missing_id, ["Unknown"])
                    logger.warning(f"[PROBE-MATCH]   Missing stream {missing_id} is referenced by channels: {channel_names}")

            # Filter to only streams that are in channels
            streams_to_probe = [s for s in all_streams if s["id"] in channel_stream_ids]
            logger.debug(f"[PROBE-MATCH] Matched {len(streams_to_probe)} streams to probe")

            # Skip recently probed streams if configured
            if self.skip_recently_probed_hours > 0:
                from datetime import timedelta
                skip_threshold = datetime.utcnow() - timedelta(hours=self.skip_recently_probed_hours)

                # Query StreamStats for recently probed streams (only successful probes)
                # get_session and StreamStats already imported at top of file
                with get_session() as session:
                    recent_probes = session.query(StreamStats).filter(
                        StreamStats.stream_id.in_([s["id"] for s in streams_to_probe]),
                        StreamStats.probe_status == "success",
                        StreamStats.last_probed >= skip_threshold
                    ).all()

                    recently_probed_ids = {stat.stream_id for stat in recent_probes}
                    original_count = len(streams_to_probe)
                    streams_to_probe = [s for s in streams_to_probe if s["id"] not in recently_probed_ids]
                    skipped_count = original_count - len(streams_to_probe)

                    if skipped_count > 0:
                        logger.info(f"Skipped {skipped_count} streams that were successfully probed within the last {self.skip_recently_probed_hours} hour(s)")

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
                        # Include error message for failed streams
                        if probe_status != "success":
                            stream_info["error"] = result.get("error_message", "Unknown error")
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
                    if self._probe_cancelled:
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

                        # Detect HDHomeRun-style URLs (local tuner devices)
                        # These need limited parallelism because each probe locks a tuner
                        is_hdhomerun = False
                        if stream_url:
                            # HDHomeRun URLs: http://192.168.x.x:5004/auto/... or http://IP:5004/...
                            if ':5004/' in stream_url or 'hdhomerun' in stream_url.lower():
                                is_hdhomerun = True

                        if m3u_account_id:
                            max_streams = m3u_max_streams.get(m3u_account_id, 0)

                            # For HDHomeRun devices, limit to 2 concurrent probes regardless of max_streams
                            # This prevents 5XX errors from overwhelming the tuner while still allowing some parallelism
                            effective_max = 2 if is_hdhomerun else max_streams

                            if effective_max > 0:
                                # Total connections = Dispatcharr active + our active probes
                                dispatcharr_active = dispatcharr_connections.get(m3u_account_id, 0)
                                async with probe_connections_lock:
                                    our_probes = probe_connections.get(m3u_account_id, 0)
                                total_active = dispatcharr_active + our_probes

                                if total_active >= effective_max:
                                    # Check if we have any active probes for this M3U
                                    # If we do, wait for them to finish (don't skip yet)
                                    if our_probes > 0:
                                        can_probe = False  # Wait, don't skip
                                    else:
                                        # No probes running, Dispatcharr is using all connections
                                        m3u_name = m3u_accounts_map.get(m3u_account_id, f"M3U {m3u_account_id}")
                                        skip_reason = f"M3U '{m3u_name}' at max connections ({dispatcharr_active}/{effective_max})"
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

                        # Small delay after probes complete to let devices (like HDHomeRun) release tuners
                        # This prevents rapid-fire requests that can cause 5XX errors
                        await asyncio.sleep(0.5)
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
                    if self._probe_cancelled:
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
                        # Include error message for failed streams
                        stream_info["error"] = result.get("error_message", "Unknown error")
                        self._probe_failed_streams.append(stream_info)

                    probed_count += 1
                    await asyncio.sleep(0.5)  # Rate limiting

            logger.info(f"Completed probing {probed_count} streams")
            self._probe_progress_status = "completed"
            self._probe_progress_current_stream = ""

            # Auto-reorder streams if configured
            reordered_channels = []
            logger.info(f"[AUTO-REORDER] Checking auto_reorder_after_probe setting: {self.auto_reorder_after_probe}")
            if self.auto_reorder_after_probe:
                logger.info("Auto-reorder is enabled, reordering streams in probed channels...")
                self._probe_progress_status = "reordering"
                self._probe_progress_current_stream = "Reordering streams..."
                try:
                    reordered_channels = await self._auto_reorder_channels(channel_groups_override, stream_to_channels)
                    logger.info(f"[AUTO-REORDER] Auto-reordered {len(reordered_channels)} channels")
                except Exception as e:
                    logger.error(f"Auto-reorder failed: {e}")

            # Save to probe history
            self._save_probe_history(start_time, probed_count, reordered_channels=reordered_channels)

            return {"status": "completed", "probed": probed_count, "reordered_channels": len(reordered_channels)}
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
        progress = {
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
        # Log when probing is in progress for debugging
        if self._probing_in_progress:
            logger.debug(f"[PROBE-PROGRESS] in_progress=True, status={self._probe_progress_status}, {self._probe_progress_current}/{self._probe_progress_total}")
        return progress

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

    def _save_probe_history(self, start_time: datetime, total: int, error: str = None, reordered_channels: list = None):
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
            "reordered_channels": reordered_channels or [],  # List of channels that were reordered
            # Include sort configuration used for this run (for UI display)
            "sort_config": {
                "priority": list(self.stream_sort_priority),
                "enabled": dict(self.stream_sort_enabled),
                "deprioritize_failed": self.deprioritize_failed_streams,
            } if reordered_channels else None,
        }

        # Add to history and keep only last 5
        self._probe_history.insert(0, history_entry)
        self._probe_history = self._probe_history[:5]

        reorder_msg = f", {len(reordered_channels or [])} channels reordered" if reordered_channels else ""
        logger.info(f"Saved probe history entry: {total} streams, {self._probe_progress_success_count} success, {self._probe_progress_failed_count} failed, {self._probe_progress_skipped_count} skipped{reorder_msg}")

        # Persist to disk
        self._persist_probe_history()

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
