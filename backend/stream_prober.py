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
import re

import httpx

from database import get_session
from models import StreamStats

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_PROBE_TIMEOUT = 30  # seconds
DEFAULT_PROBE_BATCH_SIZE = 10  # streams per cycle
BITRATE_SAMPLE_DURATION = 8  # seconds to sample stream for bitrate measurement

# Per-account ramp-up configuration
RAMP_INITIAL_LIMIT = 1         # Start each account at 1 concurrent probe
RAMP_INCREMENT = 1             # Increase allowed concurrency by 1 after each successful window
RAMP_SUCCESS_WINDOW = 3        # Consecutive successes at current level before ramping up
RAMP_FAILURE_HOLD_SECONDS = 10 # Seconds to hold an account after a probe failure
RAMP_FAILURE_REDUCTION = 1     # Reduce current_limit by this on failure (min 1)
RAMP_UNLIMITED_CAP = 4         # For accounts with max_streams=0 (unlimited), cap ramp here

# Probe history persistence
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
PROBE_HISTORY_FILE = CONFIG_DIR / "probe_history.json"


def check_ffprobe_available() -> bool:
    """Check if ffprobe is available on the system."""
    return shutil.which("ffprobe") is not None


def extract_m3u_account_id(m3u_account):
    """Extract M3U account ID from stream data.

    Handles both formats:
    - Direct ID: m3u_account = 3
    - Nested object: m3u_account = {"id": 3, "name": "..."}

    Args:
        m3u_account: The m3u_account field from stream data

    Returns:
        The M3U account ID (int) or None
    """
    logger.debug("[STREAM-PROBE-M3U] Raw m3u_account value: %r (type: %s)", m3u_account, type(m3u_account).__name__)
    if m3u_account is None:
        return None
    if isinstance(m3u_account, dict):
        extracted_id = m3u_account.get("id")
        logger.debug("[STREAM-PROBE-M3U] Extracted ID from dict: %s", extracted_id)
        return extracted_id
    logger.debug("[STREAM-PROBE-M3U] Returning direct value: %s", m3u_account)
    return m3u_account


def smart_sort_streams(
    stream_ids: list[int],
    stats_map: dict,
    stream_m3u_map: dict[int, int] = None,
    stream_sort_priority: list[str] = None,
    stream_sort_enabled: dict[str, bool] = None,
    m3u_account_priorities: dict[str, int] = None,
    deprioritize_failed_streams: bool = True,
    channel_name: str = "unknown",
) -> list[int]:
    """
    Pure function — sort stream IDs by quality/priority criteria.

    Args:
        stream_ids: List of stream IDs to sort
        stats_map: Map of stream_id -> StreamStats
        stream_m3u_map: Map of stream_id -> m3u_account_id (for M3U priority sorting)
        stream_sort_priority: Priority order for sort criteria
        stream_sort_enabled: Which criteria are enabled
        m3u_account_priorities: M3U account priorities (account_id_str -> priority)
        deprioritize_failed_streams: Whether to push failed streams to bottom
        channel_name: Channel name for logging purposes
    """
    if stream_m3u_map is None:
        stream_m3u_map = {}
    if stream_sort_priority is None:
        stream_sort_priority = ["resolution", "bitrate", "framerate", "m3u_priority", "audio_channels"]
    if stream_sort_enabled is None:
        stream_sort_enabled = {"resolution": True, "bitrate": True, "framerate": True, "m3u_priority": False, "audio_channels": False}
    if m3u_account_priorities is None:
        m3u_account_priorities = {}

    # Get active sort criteria (enabled and in priority order)
    active_criteria = [
        criterion for criterion in stream_sort_priority
        if stream_sort_enabled.get(criterion, False)
    ]

    logger.info("[STREAM-PROBE-SORT] Channel '%s': Sorting %s streams", channel_name, len(stream_ids))
    logger.info("[STREAM-PROBE-SORT] Sort config: priority=%s, enabled=%s", stream_sort_priority, stream_sort_enabled)
    logger.info("[STREAM-PROBE-SORT] Active criteria (in order): %s", active_criteria)
    logger.info("[STREAM-PROBE-SORT] Deprioritize failed streams: %s", deprioritize_failed_streams)

    # Log each stream's stats before sorting
    for stream_id in stream_ids:
        stat = stats_map.get(stream_id)
        if stat:
            logger.debug("[STREAM-PROBE-SORT]   Stream %s (%s): "
                        "status=%s, res=%s, "
                        "bitrate=%s, fps=%s",
                        stream_id, stat.stream_name,
                        stat.probe_status, stat.resolution,
                        stat.bitrate, stat.fps)
        else:
            logger.debug("[STREAM-PROBE-SORT]   Stream %s: NO STATS AVAILABLE", stream_id)

    def get_sort_value(stream_id: int) -> tuple:
        stat = stats_map.get(stream_id)
        stream_name = stat.stream_name if stat else f"Stream {stream_id}"

        # Deprioritize failed streams if enabled
        if deprioritize_failed_streams:
            if not stat or stat.probe_status in ('failed', 'timeout', 'pending'):
                logger.debug("[STREAM-PROBE-SORT]   %s: DEPRIORITIZED (status=%s)", stream_name, stat.probe_status if stat else 'no_stats')
                # Return tuple with 1 as first element to sort to bottom
                return (1,) + tuple(0 for _ in active_criteria)

        if not stat or stat.probe_status != 'success':
            logger.debug("[STREAM-PROBE-SORT]   %s: No successful probe data", stream_name)
            # Still compute M3U priority for unprobed streams (M3U priority doesn't require probing)
            sort_values = [0]
            for criterion in active_criteria:
                if criterion == "m3u_priority":
                    m3u_priority_value = 0
                    m3u_account_id = stream_m3u_map.get(stream_id)
                    if m3u_account_id is not None:
                        m3u_priority_value = m3u_account_priorities.get(str(m3u_account_id), 0)
                    sort_values.append(-m3u_priority_value)
                else:
                    sort_values.append(0)
            return tuple(sort_values)

        # Build sort values based on active criteria in priority order
        sort_values = [0]  # First element: 0 = successful stream

        for criterion in active_criteria:
            if criterion == "resolution":
                # Parse resolution (e.g., "1920x1080" -> height only, matching frontend)
                resolution_value = 0
                if stat.resolution:
                    try:
                        parts = stat.resolution.split('x')
                        if len(parts) == 2:
                            resolution_value = int(parts[1])  # Use height only
                    except Exception as e:
                        logger.debug("[STREAM-PROBE] Suppressed resolution parse error: %s", e)
                # Negate for descending sort (higher values first)
                sort_values.append(-resolution_value)

            elif criterion == "bitrate":
                # Use video_bitrate first (from probe), fallback to overall bitrate
                bitrate_value = stat.video_bitrate or stat.bitrate or 0
                sort_values.append(-bitrate_value)

            elif criterion == "framerate":
                # Parse fps - could be string like "29.97" or "30"
                framerate_value = 0
                if stat.fps:
                    try:
                        framerate_value = float(stat.fps)
                    except (ValueError, TypeError) as e:
                        logger.debug("[STREAM-PROBE] Suppressed fps parse error: %s", e)
                sort_values.append(-framerate_value)

            elif criterion == "m3u_priority":
                # Get M3U account priority from settings (higher priority = sorted first)
                m3u_priority_value = 0
                m3u_account_id = stream_m3u_map.get(stream_id)
                if m3u_account_id is not None:
                    # Convert to string since JSON keys are strings
                    m3u_priority_value = m3u_account_priorities.get(str(m3u_account_id), 0)
                # Negate for descending sort (higher priority first)
                sort_values.append(-m3u_priority_value)

            elif criterion == "audio_channels":
                # Sort by audio channels: 5.1/6ch > stereo/2ch > mono/1ch
                audio_channels_value = stat.audio_channels or 0
                # Negate for descending sort (more channels first)
                sort_values.append(-audio_channels_value)

        m3u_account_id = stream_m3u_map.get(stream_id)
        logger.debug("[STREAM-PROBE-SORT]   %s: sort_tuple=%s "
                    "(res=%s, br=%s, fps=%s, m3u=%s, audio_ch=%s)",
                    stream_name, tuple(sort_values),
                    stat.resolution, stat.bitrate, stat.fps, m3u_account_id, stat.audio_channels)
        return tuple(sort_values)

    # Sort stream IDs by their stats
    sorted_ids = sorted(stream_ids, key=get_sort_value)

    # Log the final sorted order
    logger.info("[STREAM-PROBE-SORT] Channel '%s' sorted order:", channel_name)
    for idx, stream_id in enumerate(sorted_ids):
        stat = stats_map.get(stream_id)
        stream_name = stat.stream_name if stat else f"Stream {stream_id}"
        status = stat.probe_status if stat else "no_stats"
        res = stat.resolution if stat else "?"
        logger.info("[STREAM-PROBE-SORT]   #%s: %s (id=%s, status=%s, res=%s)", idx+1, stream_name, stream_id, status, res)

    return sorted_ids


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
        user_timezone: str = "",  # IANA timezone name
        bitrate_sample_duration: int = 10,  # Duration in seconds to sample stream for bitrate (10, 20, or 30)
        parallel_probing_enabled: bool = True,  # Probe streams from different M3Us simultaneously
        max_concurrent_probes: int = 8,  # Max simultaneous probes when parallel probing is enabled (1-16)
        profile_distribution_strategy: str = "fill_first",  # How to distribute probes across profiles: fill_first, round_robin, least_loaded
        skip_recently_probed_hours: int = 0,  # Skip streams probed within last N hours (0 = always probe)
        refresh_m3us_before_probe: bool = True,  # Refresh all M3U accounts before starting probe
        auto_reorder_after_probe: bool = False,  # Automatically reorder streams in channels after probe completes
        probe_retry_count: int = 1,   # Retries on transient ffprobe failure (0 = no retry)
        probe_retry_delay: int = 2,   # Seconds between retries
        deprioritize_failed_streams: bool = True,  # Deprioritize failed streams in smart sort
        stream_sort_priority: list[str] = None,  # Priority order for Smart Sort criteria
        stream_sort_enabled: dict[str, bool] = None,  # Which criteria are enabled for Smart Sort
        stream_fetch_page_limit: int = 200,  # Max pages when fetching streams (200 * 500 = 100K streams)
        m3u_account_priorities: dict[str, int] = None,  # M3U account priorities (account_id -> priority)
    ):
        self.client = client
        self.probe_timeout = probe_timeout
        self.probe_batch_size = probe_batch_size
        self.user_timezone = user_timezone
        self.bitrate_sample_duration = bitrate_sample_duration
        self.parallel_probing_enabled = parallel_probing_enabled
        self.max_concurrent_probes = max(1, min(16, max_concurrent_probes))  # Clamp to 1-16
        self.profile_distribution_strategy = profile_distribution_strategy
        self.skip_recently_probed_hours = skip_recently_probed_hours
        self.refresh_m3us_before_probe = refresh_m3us_before_probe
        self.auto_reorder_after_probe = auto_reorder_after_probe
        self.probe_retry_count = max(0, min(5, probe_retry_count))  # Clamp 0-5
        self.probe_retry_delay = max(1, min(30, probe_retry_delay))  # Clamp 1-30
        self.deprioritize_failed_streams = deprioritize_failed_streams
        self.stream_fetch_page_limit = stream_fetch_page_limit
        logger.info("[STREAM-PROBE] auto_reorder_after_probe=%s", auto_reorder_after_probe)
        # Smart Sort configuration
        self.stream_sort_priority = stream_sort_priority or ["resolution", "bitrate", "framerate", "m3u_priority", "audio_channels"]
        self.stream_sort_enabled = stream_sort_enabled or {"resolution": True, "bitrate": True, "framerate": True, "m3u_priority": False, "audio_channels": False}
        self.m3u_account_priorities = m3u_account_priorities or {}
        self._probe_cancelled = False  # Controls cancellation of in-progress probe
        self._probe_paused = False  # Controls pausing of in-progress probe
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

        # Profile-to-account mapping for connection tracking
        self._profile_to_account_map = {}  # profile_id -> account_id (built during probe_all_streams)
        self._account_profiles = {}      # account_id -> [sorted list of active profile dicts]
        self._profile_max_streams = {}   # profile_id -> max_streams
        self._round_robin_index = {}    # account_id -> last used profile index (for round_robin strategy)

        # Per-account ramp-up state (reset each probe run)
        self._account_ramp_state = {}  # account_id -> ramp state dict

        # Notification callbacks for progress updates
        self._notification_create_callback = None  # async fn(type, title, message, source, source_id, metadata) -> dict with id
        self._notification_update_callback = None  # async fn(notification_id, type, message, metadata) -> dict
        self._notification_delete_by_source_callback = None  # async fn(source) -> int (deleted count)
        self._probe_notification_id = None  # Current probe notification ID
        self._last_notification_update = 0  # Timestamp of last notification update

        # Load probe history from disk on initialization
        self._load_probe_history()

    def _extract_m3u_account_id(self, m3u_account):
        """Extract M3U account ID from stream data. Delegates to module-level function."""
        return extract_m3u_account_id(m3u_account)

    def _load_probe_history(self):
        """Load probe history from persistent storage."""
        try:
            if PROBE_HISTORY_FILE.exists():
                with open(PROBE_HISTORY_FILE, 'r') as f:
                    self._probe_history = json.load(f)
                logger.info("[STREAM-PROBE] Loaded %s probe history entries from %s", len(self._probe_history), PROBE_HISTORY_FILE)
            else:
                logger.info("[STREAM-PROBE] No probe history file found at %s, starting fresh", PROBE_HISTORY_FILE)
        except Exception as e:
            logger.error("[STREAM-PROBE] Failed to load probe history from %s: %s", PROBE_HISTORY_FILE, e)
            self._probe_history = []

    def update_probing_settings(self, parallel_probing_enabled: bool, max_concurrent_probes: int,
                                profile_distribution_strategy: str = "fill_first") -> None:
        """Update the parallel probing settings.

        This allows updating the prober's concurrency settings without restarting the service.
        Called when settings are saved to ensure probes use the latest limits.

        Args:
            parallel_probing_enabled: Whether to enable parallel probing.
            max_concurrent_probes: Max simultaneous probes (clamped to 1-16).
            profile_distribution_strategy: How to distribute probes across profiles.
        """
        old_parallel = self.parallel_probing_enabled
        old_concurrent = self.max_concurrent_probes
        old_strategy = self.profile_distribution_strategy
        self.parallel_probing_enabled = parallel_probing_enabled
        self.max_concurrent_probes = max(1, min(16, max_concurrent_probes))
        self.profile_distribution_strategy = profile_distribution_strategy
        logger.info("[STREAM-PROBE] Updated probing settings: parallel_probing_enabled=%s->%s, "
                    "max_concurrent_probes=%s->%s, "
                    "profile_distribution_strategy=%s->%s",
                    old_parallel, self.parallel_probing_enabled,
                    old_concurrent, self.max_concurrent_probes,
                    old_strategy, self.profile_distribution_strategy)

    def update_sort_settings(
        self,
        stream_sort_priority: list[str],
        stream_sort_enabled: dict[str, bool],
        m3u_account_priorities: dict[str, int]
    ) -> None:
        """Update the sort settings.

        This allows updating the prober's sort settings without restarting the service.
        Called when settings are saved to ensure smart sort uses the latest config.

        Args:
            stream_sort_priority: Priority order for sort criteria.
            stream_sort_enabled: Which criteria are enabled.
            m3u_account_priorities: M3U account priorities (account_id -> priority value).
        """
        old_priority = self.stream_sort_priority
        old_enabled = self.stream_sort_enabled
        old_m3u_priorities = self.m3u_account_priorities
        self.stream_sort_priority = stream_sort_priority
        self.stream_sort_enabled = stream_sort_enabled
        self.m3u_account_priorities = m3u_account_priorities
        logger.info("[STREAM-PROBE] Updated sort settings: priority=%s->%s, "
                    "enabled=%s->%s, "
                    "m3u_priorities=%s->%s",
                    old_priority, self.stream_sort_priority,
                    old_enabled, self.stream_sort_enabled,
                    old_m3u_priorities, self.m3u_account_priorities)

    def set_notification_callbacks(self, create_callback, update_callback, delete_by_source_callback=None):
        """Set notification callback functions for probe progress updates.

        Args:
            create_callback: async fn(type, title, message, source, source_id, metadata) -> dict with 'id' key
            update_callback: async fn(notification_id, type, message, metadata) -> dict
            delete_by_source_callback: async fn(source) -> int (deleted count) - optional, used to clean up old notifications
        """
        self._notification_create_callback = create_callback
        self._notification_update_callback = update_callback
        self._notification_delete_by_source_callback = delete_by_source_callback
        logger.info("[STREAM-PROBE] Notification callbacks configured for stream prober")

    async def _create_probe_notification(self, total_streams: int) -> Optional[int]:
        """Create a notification for probe progress.

        Deletes any existing probe notifications first to ensure only one exists.

        Returns:
            Notification ID or None if callbacks not configured
        """
        if not self._notification_create_callback:
            return None

        try:
            # Delete any existing probe notifications first (only one probe at a time)
            if self._notification_delete_by_source_callback:
                deleted = await self._notification_delete_by_source_callback("stream_probe")
                if deleted > 0:
                    logger.info("[STREAM-PROBE] Cleaned up %s existing probe notification(s)", deleted)

            metadata = {
                "progress": {
                    "current": 0,
                    "total": total_streams,
                    "success": 0,
                    "failed": 0,
                    "skipped": 0,
                    "status": "running",
                    "current_stream": ""
                }
            }
            result = await self._notification_create_callback(
                notification_type="info",
                title="Stream Probe",
                message=f"Stream probe started (0/{total_streams})",
                source="stream_probe",
                source_id=str(int(time.time())),
                metadata=metadata
            )
            if result and "id" in result:
                self._probe_notification_id = result["id"]
                self._last_notification_update = time.time()
                logger.debug("[STREAM-PROBE] Created probe notification: %s", self._probe_notification_id)
                return result["id"]
        except Exception as e:
            logger.error("[STREAM-PROBE] Failed to create probe notification: %s", e)
        return None

    async def _update_probe_notification(self, force: bool = False) -> None:
        """Update the probe progress notification.

        Only updates every 5 seconds or every 10 streams to avoid excessive updates,
        unless force=True.
        """
        if not self._notification_update_callback or not self._probe_notification_id:
            return

        current_time = time.time()
        streams_since_update = self._probe_progress_current % 10

        # Update every 10 streams or every 5 seconds, or when forced
        if not force and streams_since_update != 0 and (current_time - self._last_notification_update) < 5:
            return

        try:
            metadata = {
                "progress": {
                    "current": self._probe_progress_current,
                    "total": self._probe_progress_total,
                    "success": self._probe_progress_success_count,
                    "failed": self._probe_progress_failed_count,
                    "skipped": self._probe_progress_skipped_count,
                    "status": self._probe_progress_status,
                    "current_stream": self._probe_progress_current_stream
                }
            }

            message = f"Probing streams... ({self._probe_progress_current}/{self._probe_progress_total})"

            await self._notification_update_callback(
                notification_id=self._probe_notification_id,
                notification_type="info",
                message=message,
                metadata=metadata
            )
            self._last_notification_update = current_time
            logger.debug("[STREAM-PROBE] Updated probe notification: %s/%s", self._probe_progress_current, self._probe_progress_total)
        except Exception as e:
            logger.error("[STREAM-PROBE] Failed to update probe notification: %s", e)

    async def _finalize_probe_notification(self) -> None:
        """Update the notification with final probe results, or delete if cancelled."""
        if not self._probe_notification_id:
            return

        try:
            # If cancelled, delete the notification instead of updating it
            if self._probe_cancelled and self._notification_delete_by_source_callback:
                await self._notification_delete_by_source_callback("stream_probe")
                logger.info("[STREAM-PROBE] Deleted probe notification (probe was cancelled)")
                return

            if not self._notification_update_callback:
                return

            # Determine notification type based on results
            if self._probe_progress_failed_count > 0:
                notification_type = "warning"
            else:
                notification_type = "success"

            # Build message
            parts = []
            if self._probe_progress_success_count > 0:
                parts.append(f"{self._probe_progress_success_count} success")
            if self._probe_progress_failed_count > 0:
                parts.append(f"{self._probe_progress_failed_count} failed")
            if self._probe_progress_skipped_count > 0:
                parts.append(f"{self._probe_progress_skipped_count} skipped")

            message = f"Stream probe complete: {', '.join(parts)}" if parts else "Stream probe complete"

            metadata = {
                "progress": {
                    "current": self._probe_progress_total,
                    "total": self._probe_progress_total,
                    "success": self._probe_progress_success_count,
                    "failed": self._probe_progress_failed_count,
                    "skipped": self._probe_progress_skipped_count,
                    "status": "completed",
                    "current_stream": ""
                }
            }

            await self._notification_update_callback(
                notification_id=self._probe_notification_id,
                notification_type=notification_type,
                message=message,
                metadata=metadata
            )
            logger.info("[STREAM-PROBE] Finalized probe notification: %s", message)
        except Exception as e:
            logger.error("[STREAM-PROBE] Failed to finalize probe notification: %s", e)
        finally:
            self._probe_notification_id = None

    def _persist_probe_history(self):
        """Persist probe history to disk."""
        try:
            # Ensure config directory exists
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)

            with open(PROBE_HISTORY_FILE, 'w') as f:
                json.dump(self._probe_history, f, indent=2)
            logger.debug("[STREAM-PROBE] Persisted %s probe history entries to %s", len(self._probe_history), PROBE_HISTORY_FILE)
        except Exception as e:
            logger.error("[STREAM-PROBE] Failed to persist probe history to %s: %s", PROBE_HISTORY_FILE, e)

    def _init_account_ramp(self, account_id: int):
        """Initialize ramp-up state for an account if not already present."""
        if account_id not in self._account_ramp_state:
            self._account_ramp_state[account_id] = {
                "current_limit": RAMP_INITIAL_LIMIT,
                "consecutive_successes": 0,
                "hold_until": 0.0,
                "total_successes": 0,
                "total_failures": 0,
            }

    def _get_account_ramp_limit(self, account_id: int, account_max: int, dispatcharr_active: int) -> int:
        """Get current ramp-limited concurrent probe cap for an account.
        Cap = min(ramp_level, max_streams - dispatcharr_active).
        """
        state = self._account_ramp_state.get(account_id)
        if not state:
            return RAMP_INITIAL_LIMIT
        ramp_limit = state["current_limit"]
        if account_max > 0:
            dynamic_cap = max(0, account_max - dispatcharr_active)
        else:
            dynamic_cap = RAMP_UNLIMITED_CAP
        return min(ramp_limit, dynamic_cap)

    def _is_account_held(self, account_id: int) -> bool:
        """Check if an account is in a failure hold period."""
        state = self._account_ramp_state.get(account_id)
        if not state:
            return False
        return time.time() < state["hold_until"]

    def _get_account_hold_remaining(self, account_id: int) -> float:
        """Get remaining hold time in seconds."""
        state = self._account_ramp_state.get(account_id)
        if not state:
            return 0.0
        return max(0.0, state["hold_until"] - time.time())

    def _record_probe_success(self, account_id: int):
        """Record success. After RAMP_SUCCESS_WINDOW consecutive successes, ramp up by 1."""
        state = self._account_ramp_state.get(account_id)
        if not state:
            return
        state["total_successes"] += 1
        state["consecutive_successes"] += 1
        if state["consecutive_successes"] >= RAMP_SUCCESS_WINDOW:
            state["current_limit"] += RAMP_INCREMENT
            state["consecutive_successes"] = 0
            logger.info("[STREAM-PROBE] Account %s: ramped to %s concurrent probes", account_id, state['current_limit'])

    def _is_overload_error(self, error_message: str) -> bool:
        """Check if an error indicates server overload (should trigger ramp-down).

        Only 429 and 5XX errors suggest the server can't handle the load.
        Dead streams (404, connection timeout, invalid data) should NOT
        cause ramp-down because the server isn't overloaded — the stream
        is simply gone or unreachable.
        """
        overload_patterns = ("429", "Too Many Requests", "5XX", "500", "502", "503", "520")
        return any(p in error_message for p in overload_patterns)

    def _record_probe_failure(self, account_id: int, error_message: str):
        """Record failure. Only ramp-down/hold for overload errors (429/5XX).

        Dead streams (404, connection timeout, invalid data) reset the
        consecutive success counter but do NOT reduce concurrency or hold
        the account, since the server isn't overloaded.
        """
        state = self._account_ramp_state.get(account_id)
        if not state:
            return
        state["total_failures"] += 1
        state["consecutive_successes"] = 0

        if self._is_overload_error(error_message):
            old_limit = state["current_limit"]
            state["current_limit"] = max(1, old_limit - RAMP_FAILURE_REDUCTION)
            state["hold_until"] = time.time() + RAMP_FAILURE_HOLD_SECONDS
            logger.warning("[STREAM-PROBE] Account %s: overload detected, "
                           "limit %s->%s, "
                           "hold %ss — %s",
                           account_id, old_limit, state['current_limit'],
                           RAMP_FAILURE_HOLD_SECONDS, error_message[:100])
        else:
            logger.debug("[STREAM-PROBE] Account %s: non-overload failure, "
                         "no ramp-down — %s",
                         account_id, error_message[:100])

    async def start(self):
        """Initialize the stream prober (check ffprobe availability).

        Note: Scheduled probing is now handled by the task engine (StreamProbeTask).
        This method only validates that ffprobe is available for on-demand probing.
        """
        logger.info("[STREAM-PROBE] StreamProber.start() called")

        # Check ffprobe availability
        ffprobe_available = check_ffprobe_available()
        logger.info("[STREAM-PROBE] ffprobe availability check: %s", ffprobe_available)

        if not ffprobe_available:
            logger.error("[STREAM-PROBE] ffprobe not found - stream probing will not be available")
            logger.warning("[STREAM-PROBE] Install ffprobe (part of ffmpeg) to enable stream probing")
            return

        logger.info(
            "[STREAM-PROBE] StreamProber initialized (batch: %s, timeout: %ss)",
            self.probe_batch_size, self.probe_timeout
        )

    async def stop(self):
        """Stop the stream prober and cancel any in-progress probes."""
        logger.info("[STREAM-PROBE] StreamProber stopping...")
        self._probe_cancelled = True
        logger.info("[STREAM-PROBE] StreamProber stopped")

    def cancel_probe(self) -> dict:
        """Cancel an in-progress probe operation.

        Returns:
            Dict with status of the cancellation.
        """
        if not self._probing_in_progress:
            return {"status": "no_probe_running", "message": "No probe is currently running"}

        logger.info("[STREAM-PROBE] Cancelling in-progress probe...")
        self._probe_cancelled = True
        # The probe loop will detect _probe_cancelled=True and set status to "cancelled"
        return {"status": "cancelling", "message": "Probe cancellation requested"}

    def pause_probe(self) -> dict:
        """Pause an in-progress probe operation.

        Returns:
            Dict with status of the pause request.
        """
        if not self._probing_in_progress:
            return {"status": "no_probe_running", "message": "No probe is currently running"}

        if self._probe_paused:
            return {"status": "already_paused", "message": "Probe is already paused"}

        logger.info("[STREAM-PROBE] Pausing in-progress probe...")
        self._probe_paused = True
        return {"status": "paused", "message": "Probe paused"}

    def resume_probe(self) -> dict:
        """Resume a paused probe operation.

        Returns:
            Dict with status of the resume request.
        """
        if not self._probing_in_progress:
            return {"status": "no_probe_running", "message": "No probe is currently running"}

        if not self._probe_paused:
            return {"status": "not_paused", "message": "Probe is not paused"}

        logger.info("[STREAM-PROBE] Resuming paused probe...")
        self._probe_paused = False
        return {"status": "resumed", "message": "Probe resumed"}

    def force_reset_probe_state(self) -> dict:
        """Force reset the probe state. Use this if a probe got stuck.

        Returns:
            Dict with status of the reset.
        """
        was_in_progress = self._probing_in_progress
        logger.warning("[STREAM-PROBE] Force resetting probe state (was_in_progress=%s)", was_in_progress)

        self._probing_in_progress = False
        self._probe_cancelled = True  # Signal any running probe to stop
        self._probe_paused = False  # Reset paused state
        self._probe_progress_status = "idle"
        self._probe_progress_current_stream = ""

        return {
            "status": "reset",
            "message": f"Probe state forcibly reset (was_in_progress={was_in_progress})"
        }

    async def probe_stream(
        self, stream_id: int, url: Optional[str], name: Optional[str] = None
    ) -> dict:
        """
        Probe a single stream using ffprobe.
        Returns the probe result dict.
        """
        logger.debug("[STREAM-PROBE] probe_stream() called for stream_id=%s, name=%s, url=%s", stream_id, name, 'present' if url else 'missing')

        if not url:
            logger.warning("[STREAM-PROBE] Stream %s has no URL, marking as failed", stream_id)
            return self._save_probe_result(
                stream_id, name, None, "failed", "No URL available"
            )

        try:
            logger.debug("[STREAM-PROBE] Running ffprobe for stream %s", stream_id)
            result = await self._run_ffprobe(url)
            logger.info("[STREAM-PROBE] Stream %s ffprobe succeeded", stream_id)

            # Measure actual bitrate by downloading stream data
            logger.debug("[STREAM-PROBE] Measuring bitrate for stream %s", stream_id)
            measured_bitrate = await self._measure_stream_bitrate(url)

            # Save probe result with both ffprobe metadata and measured bitrate
            return self._save_probe_result(
                stream_id, name, result, "success", None, measured_bitrate
            )
        except asyncio.TimeoutError:
            logger.warning("[STREAM-PROBE] Stream %s probe timed out after %ss", stream_id, self.probe_timeout)
            return self._save_probe_result(
                stream_id,
                name,
                None,
                "timeout",
                f"Probe timed out after {self.probe_timeout}s"
            )
        except Exception as e:
            error_msg = str(e)
            # Truncate very long error messages
            if len(error_msg) > 500:
                error_msg = error_msg[:500] + "..."
            logger.error("[STREAM-PROBE] Stream %s probe failed: %s", stream_id, error_msg)
            return self._save_probe_result(stream_id, name, None, "failed", error_msg)

    async def _run_ffprobe(self, url: str, _retry_attempt: int = 0) -> dict:
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

            # Retry only on genuinely transient errors (server errors, connection drops).
            # Do NOT retry 404 (dead stream), connection timeouts (server down), or
            # invalid data (corrupt stream) — these won't succeed on retry and just
            # waste semaphore time.
            transient_patterns = ("5XX", "500", "502", "503", "520", "Input/output error", "Stream ends prematurely", "Connection reset", "Broken pipe")
            if any(p in error_text for p in transient_patterns) and "404" not in error_text and _retry_attempt < self.probe_retry_count:
                logger.info("[STREAM-PROBE] Transient error — retry %s/%s in %ss: %s...", _retry_attempt + 1, self.probe_retry_count, self.probe_retry_delay, url[:80])
                await asyncio.sleep(self.probe_retry_delay)
                return await self._run_ffprobe(url, _retry_attempt=_retry_attempt + 1)

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
            logger.debug("[STREAM-PROBE] Starting bitrate measurement for %ss...", self.bitrate_sample_duration)

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
                logger.info("[STREAM-PROBE] Measured bitrate: %, bytes in %.2fs = %, bps (%.2f Mbps)", bytes_downloaded, elapsed, bitrate_bps, bitrate_bps/1000000)
                return bitrate_bps
            else:
                logger.warning("[STREAM-PROBE] Bitrate measurement: elapsed time is zero")
                return None

        except httpx.HTTPStatusError as e:
            logger.warning("[STREAM-PROBE] HTTP error during bitrate measurement: %s", e.response.status_code)
            return None
        except httpx.TimeoutException:
            logger.warning("[STREAM-PROBE] Timeout during bitrate measurement")
            return None
        except Exception as e:
            logger.warning("[STREAM-PROBE] Failed to measure bitrate: %s", e)
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
            stats.dismissed_at = None  # Clear dismissal when re-probed

            # Track consecutive failures for strike rule
            if status in ("failed", "timeout"):
                stats.consecutive_failures = (stats.consecutive_failures or 0) + 1
            elif status == "success":
                stats.consecutive_failures = 0

            if ffprobe_data and status == "success":
                self._parse_ffprobe_data(stats, ffprobe_data)

            # Apply measured bitrate if available (overrides ffprobe metadata)
            if measured_bitrate is not None:
                stats.video_bitrate = measured_bitrate
                logger.debug("[STREAM-PROBE] Applied measured bitrate: %s bps", measured_bitrate)

            session.commit()
            result = stats.to_dict()
            logger.debug("[STREAM-PROBE] Saved probe result for stream %s: %s", stream_id, status)
            return result
        except Exception as e:
            logger.error("[STREAM-PROBE] Failed to save probe result: %s", e)
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
            logger.debug("[STREAM-PROBE] Video stream bitrate fields - bit_rate: %s, "
                        "tags.BPS: %s, "
                        "tags.DURATION: %s, "
                        "format.bit_rate: %s",
                        video_stream.get('bit_rate'),
                        video_stream.get('tags', {}).get('BPS'),
                        video_stream.get('tags', {}).get('DURATION'),
                        format_info.get('bit_rate'))
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
                    logger.debug("[STREAM-PROBE] Extracted video bitrate: %s bps", stats.video_bitrate)
                except (ValueError, TypeError):
                    logger.warning("[STREAM-PROBE] Failed to parse video bitrate: %s", video_bit_rate)

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
            except (ValueError, TypeError) as e:
                logger.debug("[STREAM-PROBE] Suppressed bitrate parse error: %s", e)

    def _parse_fps(self, video_stream: dict) -> Optional[float]:
        """Parse FPS from various ffprobe fields."""
        # Try r_frame_rate first (most reliable)
        r_frame_rate = video_stream.get("r_frame_rate")
        if r_frame_rate and "/" in r_frame_rate:
            try:
                num, den = r_frame_rate.split("/")
                if float(den) > 0:
                    return round(float(num) / float(den), 2)
            except (ValueError, ZeroDivisionError) as e:
                logger.debug("[STREAM-PROBE] Suppressed r_frame_rate parse error: %s", e)

        # Try avg_frame_rate
        avg_frame_rate = video_stream.get("avg_frame_rate")
        if avg_frame_rate and "/" in avg_frame_rate:
            try:
                num, den = avg_frame_rate.split("/")
                if float(den) > 0:
                    return round(float(num) / float(den), 2)
            except (ValueError, ZeroDivisionError) as e:
                logger.debug("[STREAM-PROBE] Suppressed avg_frame_rate parse error: %s", e)

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
                        "[STREAM-PROBE] Pagination limit reached (%s pages, %s streams). "
                        "Some streams may be missing. Increase 'Stream Fetch Page Limit' in settings if needed.",
                        page_limit, len(all_streams)
                    )
                    break
            except Exception as e:
                logger.error("[STREAM-PROBE] Failed to fetch streams page %s: %s", page, e)
                break
        return all_streams

    async def _fetch_channel_stream_ids(self, channel_groups_override: list[str] = None) -> tuple[set, dict, dict]:
        """
        Fetch all unique stream IDs from channels (paginated).
        Only fetches from selected groups if channel_groups_override is set.
        Returns: (set of stream IDs, dict mapping stream_id -> list of channel names, dict mapping stream_id -> lowest channel number)

        Args:
            channel_groups_override: Optional list of channel group names to filter by.
                                    If None or empty, probes all groups.
        """
        logger.debug("[STREAM-PROBE] _fetch_channel_stream_ids called with override=%s", channel_groups_override)

        channel_stream_ids = set()
        stream_to_channels = {}  # stream_id -> list of channel names
        stream_to_channel_number = {}  # stream_id -> lowest channel number (for sorting)

        # Determine which groups to filter by
        groups_to_filter = channel_groups_override or []
        logger.debug("[STREAM-PROBE] groups_to_filter=%s", groups_to_filter)

        # If specific groups are selected, fetch all groups first to filter
        selected_group_ids = set()
        if groups_to_filter:
            try:
                all_groups = await self.client.get_channel_groups()
                available_group_names = [g.get("name") for g in all_groups]
                logger.debug("[STREAM-PROBE] Requested groups: %s", groups_to_filter)
                logger.debug("[STREAM-PROBE] Available groups: %s", available_group_names)

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

                logger.debug("[STREAM-PROBE] Matched groups: %s", matched_groups)
                if unmatched_groups:
                    logger.warning("[STREAM-PROBE] Requested groups NOT FOUND: %s", unmatched_groups)
                logger.debug("[STREAM-PROBE] Filtering to %s groups", len(selected_group_ids))
            except Exception as e:
                logger.error("[STREAM-PROBE] Failed to fetch channel groups for filtering: %s", e)
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
                        logger.debug("[STREAM-PROBE] Channel '%s' has no streams, skipping", channel_name)
                        continue

                    channels_included += 1
                    channel_stream_ids.update(stream_ids)
                    logger.debug("[STREAM-PROBE] Including channel '%s' with %s stream(s)", channel_name, len(stream_ids))

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
                logger.error("[STREAM-PROBE] Failed to fetch channels page %s: %s", page, e)
                break

        # Log summary of channel filtering
        logger.debug("[STREAM-PROBE] Channel filtering summary:")
        logger.debug("[STREAM-PROBE]   Total channels seen: %s", total_channels_seen)
        logger.debug("[STREAM-PROBE]   Channels included: %s", channels_included)
        if selected_group_ids:
            logger.debug("[STREAM-PROBE]   Channels excluded (wrong group): %s", channels_excluded_wrong_group)
        if channels_with_no_streams > 0:
            logger.debug("[STREAM-PROBE]   Channels with no streams: %s", channels_with_no_streams)
        logger.debug("[STREAM-PROBE]   Unique streams to probe: %s", len(channel_stream_ids))

        # Log excluded channels if there are any (limit to first 20 to avoid log spam)
        if excluded_channel_names:
            sample = excluded_channel_names[:20]
            logger.debug("[STREAM-PROBE] Excluded channels (first 20): %s", sample)
            if len(excluded_channel_names) > 20:
                logger.debug("[STREAM-PROBE] ... and %s more", len(excluded_channel_names) - 20)

        return channel_stream_ids, stream_to_channels, stream_to_channel_number

    async def _get_all_m3u_active_connections(self) -> dict[int, int]:
        """
        Fetch current active connection counts for all M3U accounts.
        Makes a single API call to Dispatcharr to get real-time connection status.

        Channel stats report connections by m3u_profile_id (profile-level),
        so we aggregate them up to account-level using _profile_to_account_map.

        Returns:
            Dict mapping M3U account ID to active connection count.
        """
        try:
            channel_stats = await self.client.get_channel_stats()
            channels = channel_stats.get("channels", [])
            counts = {}
            for ch in channels:
                profile_id = ch.get("m3u_profile_id")
                if profile_id:
                    # Map profile ID back to parent account ID
                    account_id = self._profile_to_account_map.get(profile_id, profile_id)
                    counts[account_id] = counts.get(account_id, 0) + 1
            return counts
        except Exception as e:
            logger.warning("[STREAM-PROBE] Failed to fetch M3U connection counts: %s", e)
            # Return empty dict on failure - allows probes to proceed (fail-open)
            return {}

    async def _get_profile_active_connections(self) -> dict[int, int]:
        """
        Fetch current active connection counts per profile.
        Unlike _get_all_m3u_active_connections which aggregates to account level,
        this returns counts at the profile level for profile-aware probing.

        Uses a 5-second cache to avoid hammering the Dispatcharr API on every
        loop iteration (~660 calls per probe run without caching).

        Returns:
            Dict mapping profile_id to active connection count.
        """
        # Return cached result if fresh (within 5 seconds)
        now = time.time()
        cache_age = now - getattr(self, '_dispatcharr_conns_cache_time', 0.0)
        if cache_age < 5.0 and hasattr(self, '_dispatcharr_conns_cache'):
            return self._dispatcharr_conns_cache

        try:
            channel_stats = await self.client.get_channel_stats()
            channels = channel_stats.get("channels", [])
            counts = {}
            for ch in channels:
                profile_id = ch.get("m3u_profile_id")
                if profile_id:
                    counts[profile_id] = counts.get(profile_id, 0) + 1
            if channels:
                logger.info("[STREAM-PROBE] %s active channels, "
                            "profile connection counts: %s",
                            len(channels), counts)
                # Log channel keys if m3u_profile_id is missing — helps debug
                # data structure mismatches with different Dispatcharr versions
                if not counts:
                    sample_keys = list(channels[0].keys())
                    logger.warning("[STREAM-PROBE] Active channels found but no m3u_profile_id! "
                                   "Channel keys: %s", sample_keys)
            # Cache the result
            self._dispatcharr_conns_cache = counts
            self._dispatcharr_conns_cache_time = now
            return counts
        except Exception as e:
            logger.warning("[STREAM-PROBE] Failed to fetch profile connection counts: %s", e)
            return getattr(self, '_dispatcharr_conns_cache', {})

    def _profile_has_capacity(self, profile: dict, dispatcharr_profile_conns: dict,
                              our_profile_conns: dict) -> bool:
        """Check if a profile has capacity for another probe connection.

        Args:
            profile: Profile dict with 'id' key
            dispatcharr_profile_conns: {profile_id -> active connection count} from Dispatcharr
            our_profile_conns: {profile_id -> active probe count} from our concurrent probes

        Returns:
            True if the profile has capacity, False if at max
        """
        profile_id = profile["id"]
        profile_max = self._profile_max_streams.get(profile_id, 0)
        if profile_max == 0:
            return True  # Unlimited
        profile_total = dispatcharr_profile_conns.get(profile_id, 0) + our_profile_conns.get(profile_id, 0)
        return profile_total < profile_max

    def _select_probe_profile(self, account_id: int, dispatcharr_profile_conns: dict,
                               our_profile_conns: dict, account_max: int,
                               total_account_conns: int) -> Optional[dict]:
        """Select the best profile to use for probing a stream from this account.

        Distributes probes across profiles using the configured strategy:
        - fill_first: Use profiles in order, filling each to capacity before moving on
        - round_robin: Rotate across profiles evenly, cycling through each in turn
        - least_loaded: Pick the profile with the most available headroom

        Args:
            account_id: The M3U account ID
            dispatcharr_profile_conns: {profile_id -> active connection count} from Dispatcharr
            our_profile_conns: {profile_id -> active probe count} from our concurrent probes
            account_max: Account-level max_streams (0 = unlimited)
            total_account_conns: Total connections across all profiles for this account

        Returns:
            Profile dict if one has capacity, None if all at capacity
        """
        # Check account-level cap first
        if account_max > 0 and total_account_conns >= account_max:
            logger.debug("[STREAM-PROBE] Account %s: at account cap (%s/%s)", account_id, total_account_conns, account_max)
            return None

        profiles = self._account_profiles.get(account_id, [])
        if not profiles:
            return None

        if self.profile_distribution_strategy == "round_robin":
            # Rotate across profiles evenly, starting from the next one after last used
            last_idx = self._round_robin_index.get(account_id, -1)
            for i in range(len(profiles)):
                idx = (last_idx + 1 + i) % len(profiles)
                profile = profiles[idx]
                if self._profile_has_capacity(profile, dispatcharr_profile_conns, our_profile_conns):
                    self._round_robin_index[account_id] = idx
                    logger.debug("[STREAM-PROBE] Account %s: round_robin selected profile %s "
                               "('%s', idx=%s)",
                               account_id, profile['id'], profile.get('name', 'unnamed'), idx)
                    return profile
            logger.debug("[STREAM-PROBE] Account %s: round_robin - all profiles at capacity", account_id)
            return None

        elif self.profile_distribution_strategy == "least_loaded":
            # Pick profile with most available headroom
            best = None
            best_headroom = -1
            for profile in profiles:
                profile_id = profile["id"]
                profile_max = self._profile_max_streams.get(profile_id, 0)
                if profile_max == 0:
                    # Unlimited = always best
                    logger.debug("[STREAM-PROBE] Account %s: least_loaded selected profile %s "
                               "('%s', unlimited)",
                               account_id, profile_id, profile.get('name', 'unnamed'))
                    return profile
                current = dispatcharr_profile_conns.get(profile_id, 0) + our_profile_conns.get(profile_id, 0)
                headroom = profile_max - current
                if headroom > 0 and headroom > best_headroom:
                    best = profile
                    best_headroom = headroom
            if best:
                logger.debug("[STREAM-PROBE] Account %s: least_loaded selected profile %s "
                           "('%s', headroom=%s)",
                           account_id, best['id'], best.get('name', 'unnamed'), best_headroom)
            else:
                logger.debug("[STREAM-PROBE] Account %s: least_loaded - all profiles at capacity", account_id)
            return best

        else:
            # "fill_first" (default) — iterate in order, pick first with capacity
            for profile in profiles:
                if self._profile_has_capacity(profile, dispatcharr_profile_conns, our_profile_conns):
                    logger.debug("[STREAM-PROBE] Account %s: fill_first selected profile %s "
                               "('%s')",
                               account_id, profile['id'], profile.get('name', 'unnamed'))
                    return profile
            logger.debug("[STREAM-PROBE] Account %s: fill_first - all profiles at capacity", account_id)
            return None

    def _rewrite_url_for_profile(self, original_url: str, profile: dict) -> str:
        """Rewrite a stream URL for a specific profile using search/replace patterns.

        Args:
            original_url: The original stream URL
            profile: Profile dict with search_pattern and replace_pattern fields

        Returns:
            Rewritten URL, or original URL if no rewriting needed
        """
        if profile.get("is_default", False):
            return original_url

        search_pattern = profile.get("search_pattern", "")
        replace_pattern = profile.get("replace_pattern", "")

        if not search_pattern:
            return original_url

        try:
            rewritten = re.sub(search_pattern, replace_pattern, original_url)
            if rewritten != original_url:
                logger.debug("[STREAM-PROBE] Profile %s: rewrote URL "
                           "(pattern: %s -> %s)",
                           profile['id'], search_pattern, replace_pattern)
            return rewritten
        except re.error as e:
            logger.warning("[STREAM-PROBE] Invalid regex in profile %s: %s", profile['id'], e)
            return original_url

    async def _auto_reorder_channels(self, channel_groups_override: list[str] = None, stream_to_channels: dict = None) -> list[dict]:
        """
        Auto-reorder streams in all channels from the selected groups using smart sort.
        Returns a list of dicts with {channel_id, channel_name, stream_count} for channels that were reordered.
        """
        reordered = []

        try:
            # Determine which groups to filter by
            groups_to_filter = channel_groups_override or []
            logger.info("[STREAM-PROBE-SORT] groups_to_filter=%s", groups_to_filter)

            # Get selected group IDs
            selected_group_ids = set()
            if groups_to_filter:
                try:
                    all_groups = await self.client.get_channel_groups()
                    available_group_names = [g.get("name") for g in all_groups]
                    logger.info("[STREAM-PROBE-SORT] Available groups: %s... (total: %s)", available_group_names[:10], len(all_groups))
                    for group in all_groups:
                        if group.get("name") in groups_to_filter:
                            selected_group_ids.add(group["id"])
                    logger.info("[STREAM-PROBE-SORT] Filtering to %s selected groups (matched: %s)", len(selected_group_ids), selected_group_ids)
                except Exception as e:
                    logger.error("[STREAM-PROBE] Failed to fetch channel groups for auto-reorder: %s", e)
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
                    logger.error("[STREAM-PROBE] Failed to fetch channels page %s for auto-reorder: %s", page, e)
                    break

            logger.info("[STREAM-PROBE-SORT] Found %s channels to potentially reorder", len(channels_to_reorder))

            # For each channel, fetch full details, get stream stats, and reorder
            for channel in channels_to_reorder:
                try:
                    channel_id = channel["id"]
                    channel_name = channel.get("name", f"Channel {channel_id}")

                    # Fetch full channel details to get streams list
                    full_channel = await self.client.get_channel(channel_id)
                    stream_ids = full_channel.get("streams", [])

                    if len(stream_ids) <= 1:
                        logger.debug("[STREAM-PROBE-SORT] Channel %s (%s) - Skipping, only %s streams", channel_id, channel_name, len(stream_ids))
                        continue  # Skip if 0 or 1 streams

                    logger.info("[STREAM-PROBE-SORT] Processing channel %s (%s) with %s streams: %s", channel_id, channel_name, len(stream_ids), stream_ids)

                    # Fetch full stream data to get M3U account mapping
                    streams_data = await self.client.get_streams_by_ids(stream_ids)
                    # Log raw stream data for debugging
                    for s in streams_data:
                        logger.debug("[STREAM-PROBE-SORT] Channel %s: Stream %s ('%s') has raw m3u_account=%r", channel_id, s['id'], s.get('name', 'Unknown'), s.get('m3u_account'))
                    # Extract M3U account IDs (handles both direct ID and nested object formats)
                    stream_m3u_map = {s["id"]: self._extract_m3u_account_id(s.get("m3u_account")) for s in streams_data}
                    logger.debug("[STREAM-PROBE-SORT] Channel %s: Built M3U map for %s streams: %s", channel_id, len(stream_m3u_map), stream_m3u_map)

                    # Fetch stream stats for this channel's streams (uses get_session and StreamStats imported at top of file)
                    logger.info("[STREAM-PROBE-SORT] Channel %s: Opening database session...", channel_id)
                    with get_session() as session:
                        logger.info("[STREAM-PROBE-SORT] Channel %s: Querying stats for stream_ids: %s", channel_id, stream_ids)
                        stats_records = session.query(StreamStats).filter(
                            StreamStats.stream_id.in_(stream_ids)
                        ).all()
                        logger.info("[STREAM-PROBE-SORT] Channel %s: Query returned %s records", channel_id, len(stats_records))

                        # Build stats map
                        stats_map = {stat.stream_id: stat for stat in stats_records}
                        logger.info("[STREAM-PROBE-SORT] Channel %s: Found stats for %s/%s streams", channel_id, len(stats_map), len(stream_ids))

                        # Sort streams using smart sort logic (similar to frontend)
                        sorted_stream_ids = self._smart_sort_streams(stream_ids, stats_map, stream_m3u_map, channel_name)
                        logger.info("[STREAM-PROBE-SORT] Channel %s: Original order: %s", channel_id, stream_ids)
                        logger.info("[STREAM-PROBE-SORT] Channel %s: Sorted order:   %s", channel_id, sorted_stream_ids)
                        logger.info("[STREAM-PROBE-SORT] Channel %s: Order changed: %s", channel_id, sorted_stream_ids != stream_ids)

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
                            logger.debug("[STREAM-PROBE-SORT] Channel %s (%s) - Proposing reorder:", channel_id, channel_name)
                            before_str = [f"{s['name']} (pos={s['position']}, status={s['status']}, res={s['resolution']}, br={s['bitrate']})" for s in streams_before]
                            after_str = [f"{s['name']} (pos={s['position']}, status={s['status']}, res={s['resolution']}, br={s['bitrate']})" for s in streams_after]
                            logger.debug("[STREAM-PROBE-SORT]   Before: %s", before_str)
                            logger.debug("[STREAM-PROBE-SORT]   After:  %s", after_str)

                            # Execute the reorder
                            try:
                                await self.client.update_channel(channel_id, {"streams": sorted_stream_ids})
                                logger.debug("[STREAM-PROBE-SORT] Successfully reordered channel %s (%s)", channel_id, channel_name)
                            except Exception as update_err:
                                logger.error("[STREAM-PROBE-SORT] Failed to update channel %s (%s): %s", channel_id, channel_name, update_err)
                                raise  # Re-raise to be caught by outer exception handler

                            reordered.append({
                                "channel_id": channel_id,
                                "channel_name": channel_name,
                                "stream_count": len(stream_ids),
                                "streams_before": streams_before,
                                "streams_after": streams_after,
                            })
                        else:
                            logger.debug("[STREAM-PROBE-SORT] Channel %s (%s) - No reorder needed (already in correct order)", channel_id, channel_name)

                except Exception as e:
                    logger.error("[STREAM-PROBE] Failed to reorder channel %s: %s", channel.get('id', 'unknown'), e)
                    continue

        except Exception as e:
            logger.error("[STREAM-PROBE] Auto-reorder channels failed: %s", e)

        return reordered

    def _smart_sort_streams(
        self,
        stream_ids: list[int],
        stats_map: dict,
        stream_m3u_map: dict[int, int] = None,
        channel_name: str = "unknown"
    ) -> list[int]:
        """Sort stream IDs using smart sort logic. Delegates to module-level function."""
        return smart_sort_streams(
            stream_ids, stats_map, stream_m3u_map or {},
            self.stream_sort_priority, self.stream_sort_enabled,
            self.m3u_account_priorities, self.deprioritize_failed_streams,
            channel_name
        )

    async def probe_all_streams(self, channel_groups_override: list[str] = None, skip_m3u_refresh: bool = False, stream_ids_filter: list[int] = None):
        """Probe all streams that are in channels (runs in background).

        Uses parallel probing - streams from different M3U accounts (or same M3U with
        available capacity) are probed concurrently for faster completion.

        Args:
            channel_groups_override: Optional list of channel group names to filter by.
                                    If None or empty list, probes all groups.
            skip_m3u_refresh: If True, skip M3U refresh even if configured.
                             Use this for on-demand probes from the UI.
            stream_ids_filter: Optional list of specific stream IDs to probe.
                              If provided, only these streams will be probed (useful for re-probing failed streams).
        """
        logger.info("[STREAM-PROBE] probe_all_streams called with channel_groups_override=%s, skip_m3u_refresh=%s, stream_ids_filter=%s", channel_groups_override, skip_m3u_refresh, len(stream_ids_filter) if stream_ids_filter else 0)
        logger.info("[STREAM-PROBE] Settings: parallel_probing_enabled=%s, max_concurrent_probes=%s, "
                     "profile_distribution_strategy=%s",
                     self.parallel_probing_enabled, self.max_concurrent_probes,
                     self.profile_distribution_strategy)

        if self._probing_in_progress:
            logger.warning("[STREAM-PROBE] Probe already in progress")
            return {"status": "already_running"}

        self._probing_in_progress = True
        self._probe_cancelled = False  # Reset cancellation flag
        self._probe_paused = False  # Reset paused flag
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
        self._account_ramp_state = {}  # Fresh ramp state for each probe run

        probed_count = 0
        start_time = datetime.utcnow()
        try:
            # Refresh M3U accounts if configured AND not explicitly skipped
            # On-demand probes from UI should skip refresh; only scheduled probes refresh
            if self.refresh_m3us_before_probe and not skip_m3u_refresh:
                logger.info("[STREAM-PROBE] Refreshing all M3U accounts before probing...")
                self._probe_progress_status = "refreshing"
                self._probe_progress_current_stream = "Refreshing M3U accounts..."
                try:
                    await self.client.refresh_all_m3u_accounts()
                    logger.info("[STREAM-PROBE] M3U refresh triggered successfully")
                    # Wait a reasonable amount of time for refresh to complete
                    # Since Dispatcharr doesn't provide refresh status, we wait 60 seconds
                    await asyncio.sleep(60)
                    logger.info("[STREAM-PROBE] M3U refresh wait period completed")
                except Exception as e:
                    logger.warning("[STREAM-PROBE] Failed to refresh M3U accounts: %s", e)
                    logger.info("[STREAM-PROBE] Continuing with probe despite refresh failure")
            elif skip_m3u_refresh:
                logger.info("[STREAM-PROBE] Skipping M3U refresh (on-demand probe)")

            # Fetch all channel stream IDs and channel mappings
            self._probe_progress_status = "fetching"
            logger.info("[STREAM-PROBE] Fetching channel stream IDs (override groups: %s)...", channel_groups_override)
            channel_stream_ids, stream_to_channels, stream_to_channel_number = await self._fetch_channel_stream_ids(channel_groups_override)
            logger.info("[STREAM-PROBE] Found %s unique streams across all channels", len(channel_stream_ids))

            # Fetch M3U accounts to map account IDs to names and max_streams
            logger.info("[STREAM-PROBE] Fetching M3U accounts...")
            m3u_accounts_map = {}  # id -> name
            m3u_max_streams = {}   # id -> max_streams
            self._profile_to_account_map = {}  # profile_id -> account_id
            self._account_profiles = {}  # account_id -> [sorted list of active profile dicts]
            self._profile_max_streams = {}  # profile_id -> max_streams
            try:
                m3u_accounts = await self.client.get_m3u_accounts()
                for account in m3u_accounts:
                    account_id = account["id"]
                    m3u_accounts_map[account_id] = account.get("name", f"M3U {account_id}")
                    # Build profile-to-account map and profile lists
                    account_profiles = []
                    for profile in account.get("profiles", []):
                        self._profile_to_account_map[profile["id"]] = account_id
                        self._profile_max_streams[profile["id"]] = profile.get("max_streams", 0)
                        if profile.get("is_active", True):
                            account_profiles.append(profile)
                    # Sort profiles: default first, then by ID
                    account_profiles.sort(key=lambda p: (not p.get("is_default", False), p["id"]))
                    self._account_profiles[account_id] = account_profiles
                    # Use the account-level max_streams as the cap
                    m3u_max_streams[account_id] = account.get("max_streams", 0)
                logger.info("[STREAM-PROBE] Found %s M3U accounts, %s profiles mapped, "
                           "%s active profiles",
                           len(m3u_accounts_map), len(self._profile_to_account_map),
                           sum(len(v) for v in self._account_profiles.values()))
            except Exception as e:
                logger.warning("[STREAM-PROBE] Failed to fetch M3U accounts: %s", e)

            # Fetch all streams
            logger.info("[STREAM-PROBE] Fetching stream details...")
            all_streams = await self._fetch_all_streams()
            logger.debug("[STREAM-PROBE] Fetched %s total streams from Dispatcharr", len(all_streams))

            # Log the stream IDs we're looking for
            logger.debug("[STREAM-PROBE] Looking for %s channel stream IDs: %s", len(channel_stream_ids), sorted(channel_stream_ids))

            # Get all stream IDs from Dispatcharr
            all_stream_ids = {s["id"] for s in all_streams}
            logger.debug("[STREAM-PROBE] Dispatcharr returned %s unique stream IDs", len(all_stream_ids))

            # Find which channel stream IDs are missing from Dispatcharr's stream list
            missing_ids = channel_stream_ids - all_stream_ids
            if missing_ids:
                logger.warning("[STREAM-PROBE] %s channel stream IDs NOT FOUND in Dispatcharr streams: %s", len(missing_ids), sorted(missing_ids))
                # Log which channels reference these missing streams
                for missing_id in missing_ids:
                    channel_names = stream_to_channels.get(missing_id, ["Unknown"])
                    logger.warning("[STREAM-PROBE]   Missing stream %s is referenced by channels: %s", missing_id, channel_names)

            # Filter to only streams that are in channels
            streams_to_probe = [s for s in all_streams if s["id"] in channel_stream_ids]
            logger.debug("[STREAM-PROBE] Matched %s streams to probe", len(streams_to_probe))

            # If stream_ids_filter is provided, further filter to only those specific streams
            # This is used for re-probing specific failed streams
            if stream_ids_filter:
                stream_ids_filter_set = set(stream_ids_filter)
                original_count = len(streams_to_probe)
                streams_to_probe = [s for s in streams_to_probe if s["id"] in stream_ids_filter_set]
                logger.info("[STREAM-PROBE] Filtered to %s specific streams (from %s channel streams, requested %s)", len(streams_to_probe), original_count, len(stream_ids_filter))

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
                        logger.info("[STREAM-PROBE] Skipped %s streams that were successfully probed within the last %s hour(s)", skipped_count, self.skip_recently_probed_hours)

            # Sort streams by their lowest channel number (lowest first)
            streams_to_probe.sort(key=lambda s: stream_to_channel_number.get(s["id"], 999999))
            logger.info("[STREAM-PROBE] Sorted %s streams by channel number", len(streams_to_probe))

            self._probe_progress_total = len(streams_to_probe)
            self._probe_progress_status = "probing"

            # Create progress notification
            await self._create_probe_notification(len(streams_to_probe))

            # Log diagnostic info if no streams to probe
            if len(streams_to_probe) == 0:
                logger.warning("[STREAM-PROBE] No streams to probe! channel_stream_ids=%s, "
                              "all_streams=%s, stream_ids_filter=%s, "
                              "groups_override=%s",
                              len(channel_stream_ids), len(all_streams),
                              len(stream_ids_filter) if stream_ids_filter else 'None',
                              channel_groups_override)
            else:
                logger.info("[STREAM-PROBE] Starting probe of %s streams", len(streams_to_probe))

            if self.parallel_probing_enabled:
                # ========== PARALLEL PROBING MODE ==========
                logger.info("[STREAM-PROBE] Starting parallel probe of %s streams (filtered from %s total)", len(streams_to_probe), len(all_streams))
                logger.info("[STREAM-PROBE] Rate limit settings: max_concurrent_probes=%s", self.max_concurrent_probes)

                # Global concurrency limit - max simultaneous probes regardless of M3U account
                # This prevents system resource exhaustion when probing many streams
                global_probe_semaphore = asyncio.Semaphore(self.max_concurrent_probes)
                logger.info("[STREAM-PROBE] Semaphore created with limit=%s", self.max_concurrent_probes)

                # Track our own probe connections per M3U (separate from Dispatcharr's active connections)
                # This lets us know how many streams WE are currently probing per M3U
                probe_connections_lock = asyncio.Lock()
                probe_connections = {}  # profile_id (or m3u_id fallback) -> count of our active probes

                # Results lock for thread-safe updates
                results_lock = asyncio.Lock()

                # Track active concurrent probes for debugging
                active_probe_count = [0]  # Use list to allow modification in nested function
                active_probe_count_lock = asyncio.Lock()

                async def probe_single_stream(stream: dict, display_string: str) -> tuple[str, dict]:
                    """Probe a single stream and return (status, stream_info)."""
                    stream_id = stream["id"]
                    stream_name = stream.get("name", f"Stream {stream_id}")
                    stream_url = stream.get("url", "")
                    m3u_account_id = self._extract_m3u_account_id(stream.get("m3u_account"))

                    # Apply profile URL rewriting if a profile was selected
                    selected_profile = stream.get("_selected_profile")
                    if selected_profile:
                        stream_url = self._rewrite_url_for_profile(stream_url, selected_profile)

                    # Log probe details for traceability
                    if selected_profile:
                        logger.debug("[STREAM-PROBE] Stream %s (%s): "
                                     "strategy=%s, "
                                     "profile=%s ('%s'), "
                                     "url=%s",
                                     stream_id, stream_name,
                                     self.profile_distribution_strategy,
                                     selected_profile['id'], selected_profile.get('name', 'unnamed'),
                                     stream_url)
                    else:
                        logger.debug("[STREAM-PROBE] Stream %s (%s): "
                                     "no profile (direct URL), url=%s",
                                     stream_id, stream_name, stream_url)

                    # Acquire global semaphore to limit total concurrent probes
                    async with global_probe_semaphore:
                        # Track concurrent probe count
                        async with active_probe_count_lock:
                            active_probe_count[0] += 1
                            current_count = active_probe_count[0]
                            if current_count > self.max_concurrent_probes:
                                logger.error("[STREAM-PROBE] RATE LIMIT EXCEEDED! active=%s, limit=%s", current_count, self.max_concurrent_probes)
                            else:
                                logger.debug("[STREAM-PROBE] Acquired semaphore: active=%s/%s, stream=%s", current_count, self.max_concurrent_probes, stream_id)
                        try:
                            result = await self.probe_stream(stream_id, stream_url, stream_name)
                            probe_status = result.get("probe_status", "failed")
                            error_message = result.get("error_message", "")
                            stream_info = {"id": stream_id, "name": stream_name, "url": stream_url}

                            if probe_status != "success":
                                stream_info["error"] = error_message or "Unknown error"
                                if m3u_account_id:
                                    self._record_probe_failure(m3u_account_id, error_message)
                            else:
                                if m3u_account_id:
                                    self._record_probe_success(m3u_account_id)

                            return (probe_status, stream_info)
                        finally:
                            # Track concurrent probe count decrement
                            async with active_probe_count_lock:
                                active_probe_count[0] -= 1
                                logger.debug("[STREAM-PROBE] Released semaphore: active=%s/%s, stream=%s", active_probe_count[0], self.max_concurrent_probes, stream_id)
                            # Release our probe connection (by profile_id or m3u_account_id)
                            release_key = selected_profile["id"] if selected_profile else m3u_account_id
                            if release_key:
                                async with probe_connections_lock:
                                    if release_key in probe_connections:
                                        probe_connections[release_key] = max(0, probe_connections[release_key] - 1)

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

                    # Check for pause - wait while paused
                    while self._probe_paused and not self._probe_cancelled:
                        if self._probe_progress_status != "paused":
                            self._probe_progress_status = "paused"
                            self._probe_progress_current_stream = "Probe paused"
                            await self._update_probe_notification()
                        await asyncio.sleep(1)

                    # If cancelled while paused, break
                    if self._probe_cancelled:
                        self._probe_progress_status = "cancelled"
                        for task in active_tasks:
                            task.cancel()
                        break

                    # Restore status after unpause
                    if self._probe_progress_status == "paused":
                        self._probe_progress_status = "probing"

                    # Get fresh connection counts from Dispatcharr (profile and account level)
                    dispatcharr_profile_conns = await self._get_profile_active_connections()
                    # Derive account-level from profile-level
                    dispatcharr_connections = {}
                    for pid, cnt in dispatcharr_profile_conns.items():
                        aid = self._profile_to_account_map.get(pid, pid)
                        dispatcharr_connections[aid] = dispatcharr_connections.get(aid, 0) + cnt
                    if dispatcharr_connections:
                        logger.info("[STREAM-PROBE] Account-level active connections: %s", dispatcharr_connections)

                    # Try to start new probes for streams that have available M3U capacity
                    streams_started_this_round = []
                    for stream in pending_streams:
                        m3u_account_id = self._extract_m3u_account_id(stream.get("m3u_account"))
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
                                # Calculate total account connections (dispatcharr + our probes)
                                dispatcharr_active = dispatcharr_connections.get(m3u_account_id, 0)
                                async with probe_connections_lock:
                                    our_profile_conns_snapshot = dict(probe_connections)
                                # Sum our probes for this account across all profiles
                                profiles = self._account_profiles.get(m3u_account_id, [])
                                if profiles:
                                    our_account_total = sum(
                                        our_profile_conns_snapshot.get(p["id"], 0) for p in profiles
                                    )
                                else:
                                    our_account_total = our_profile_conns_snapshot.get(m3u_account_id, 0)
                                total_account_conns = dispatcharr_active + our_account_total

                                # Ramp-up gate: limit concurrent probes per account
                                self._init_account_ramp(m3u_account_id)
                                if self._is_account_held(m3u_account_id):
                                    can_probe = False
                                else:
                                    ramp_limit = self._get_account_ramp_limit(m3u_account_id, effective_max, dispatcharr_active)
                                    if our_account_total >= ramp_limit:
                                        can_probe = False

                                if can_probe:
                                    if not is_hdhomerun and profiles:
                                        # Profile-aware selection
                                        selected_profile = self._select_probe_profile(
                                            m3u_account_id, dispatcharr_profile_conns,
                                            our_profile_conns_snapshot, effective_max, total_account_conns
                                        )
                                        if selected_profile:
                                            stream["_selected_profile"] = selected_profile
                                        else:
                                            if our_account_total > 0:
                                                can_probe = False  # Wait for active probes to finish
                                            else:
                                                m3u_name = m3u_accounts_map.get(m3u_account_id, f"M3U {m3u_account_id}")
                                                skip_reason = f"M3U '{m3u_name}' at max connections ({dispatcharr_active}/{effective_max})"
                                                logger.info("[STREAM-PROBE] Skipping stream %s (%s): %s", stream_id, stream_name, skip_reason)
                                    else:
                                        # HDHomeRun or no profiles - use account-level logic
                                        if total_account_conns >= effective_max:
                                            if our_account_total > 0:
                                                can_probe = False  # Wait, don't skip
                                            else:
                                                m3u_name = m3u_accounts_map.get(m3u_account_id, f"M3U {m3u_account_id}")
                                                skip_reason = f"M3U '{m3u_name}' at max connections ({dispatcharr_active}/{effective_max})"
                                                logger.info("[STREAM-PROBE] Skipping stream %s (%s): %s", stream_id, stream_name, skip_reason)
                            else:
                                # Unlimited account — still apply ramp-up
                                self._init_account_ramp(m3u_account_id)
                                dispatcharr_active = dispatcharr_connections.get(m3u_account_id, 0)
                                if self._is_account_held(m3u_account_id):
                                    can_probe = False
                                else:
                                    async with probe_connections_lock:
                                        our_profile_conns_snapshot = dict(probe_connections)
                                    profiles = self._account_profiles.get(m3u_account_id, [])
                                    if profiles:
                                        our_account_total = sum(our_profile_conns_snapshot.get(p["id"], 0) for p in profiles)
                                    else:
                                        our_account_total = our_profile_conns_snapshot.get(m3u_account_id, 0)
                                    ramp_limit = self._get_account_ramp_limit(m3u_account_id, 0, dispatcharr_active)
                                    if our_account_total >= ramp_limit:
                                        can_probe = False

                        if skip_reason:
                            # Skip this stream - M3U is at capacity with Dispatcharr connections
                            stream_info = {"id": stream_id, "name": stream_name, "url": stream_url, "reason": skip_reason}
                            async with results_lock:
                                self._probe_progress_skipped_count += 1
                                self._probe_skipped_streams.append(stream_info)
                            probed_count += 1
                            streams_started_this_round.append(stream)
                            self._probe_progress_current = probed_count
                            await self._update_probe_notification()
                            continue

                        if can_probe:
                            # Reserve a probe connection (by profile_id or m3u_account_id)
                            selected_profile = stream.get("_selected_profile")
                            reserve_key = selected_profile["id"] if selected_profile else m3u_account_id
                            if reserve_key:
                                async with probe_connections_lock:
                                    probe_connections[reserve_key] = probe_connections.get(reserve_key, 0) + 1

                            # Start the probe task
                            task = asyncio.create_task(probe_single_stream(stream, display_string))
                            active_tasks[task] = (stream, display_string)
                            streams_started_this_round.append(stream)

                            # Update progress display with active streams
                            # Show actual concurrent probe count (inside semaphore), not queued tasks
                            active_displays = [info[1] for info in active_tasks.values()]
                            async with active_probe_count_lock:
                                actual_concurrent = active_probe_count[0]
                            if len(active_displays) == 1:
                                self._probe_progress_current_stream = active_displays[0]
                            elif actual_concurrent <= 1:
                                # Tasks queued but only 0-1 actually running
                                self._probe_progress_current_stream = f"[{len(active_displays)} queued] {active_displays[0]}"
                            else:
                                self._probe_progress_current_stream = f"[{actual_concurrent} parallel] {active_displays[0]}"

                    # Remove started streams from pending
                    for stream in streams_started_this_round:
                        pending_streams.remove(stream)

                    # If we have active tasks, wait for at least one to complete
                    if active_tasks:
                        done, _ = await asyncio.wait(active_tasks.keys(), return_when=asyncio.FIRST_COMPLETED)

                        completed_had_hdhomerun = False
                        for task in done:
                            stream, display_string = active_tasks.pop(task)
                            stream_url = stream.get("url", "")
                            if ':5004/' in stream_url or 'hdhomerun' in stream_url.lower():
                                completed_had_hdhomerun = True
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
                                await self._update_probe_notification()
                            except asyncio.CancelledError:
                                pass
                            except Exception as e:
                                logger.error("[STREAM-PROBE] Probe task failed: %s", e)
                                probed_count += 1
                                self._probe_progress_current = probed_count
                                await self._update_probe_notification()

                        # Small delay only for HDHomeRun devices to let tuners release
                        if completed_had_hdhomerun:
                            await asyncio.sleep(0.5)
                    elif not pending_streams:
                        # No active tasks and no pending streams - we're done
                        break
                    else:
                        # All pending streams are waiting for M3U capacity - wait a bit and retry
                        await asyncio.sleep(0.5)
            else:
                # ========== SEQUENTIAL PROBING MODE ==========
                logger.info("[STREAM-PROBE] Starting sequential probe of %s streams (filtered from %s total)", len(streams_to_probe), len(all_streams))

                for stream in streams_to_probe:
                    if self._probe_cancelled:
                        self._probe_progress_status = "cancelled"
                        break

                    # Check for pause - wait while paused
                    while self._probe_paused and not self._probe_cancelled:
                        if self._probe_progress_status != "paused":
                            self._probe_progress_status = "paused"
                            self._probe_progress_current_stream = "Probe paused"
                            await self._update_probe_notification()
                        await asyncio.sleep(1)

                    # If cancelled while paused, break
                    if self._probe_cancelled:
                        self._probe_progress_status = "cancelled"
                        break

                    # Restore status after unpause
                    if self._probe_progress_status == "paused":
                        self._probe_progress_status = "probing"

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

                    m3u_account_id = self._extract_m3u_account_id(stream.get("m3u_account"))
                    if m3u_account_id and m3u_account_id in m3u_accounts_map:
                        m3u_name = m3u_accounts_map[m3u_account_id]
                        display_string = f"{display_parts[0]}: {display_parts[1]} | {m3u_name}"
                    else:
                        display_string = f"{display_parts[0]}: {display_parts[1]}"

                    self._probe_progress_current = probed_count + 1
                    self._probe_progress_current_stream = display_string

                    # Check if M3U is at max connections before probing (fresh check each time)
                    skip_reason = None
                    selected_profile = None
                    if m3u_account_id:
                        max_streams = m3u_max_streams.get(m3u_account_id, 0)
                        if max_streams > 0:
                            dispatcharr_profile_conns = await self._get_profile_active_connections()
                            dispatcharr_connections = {}
                            for pid, cnt in dispatcharr_profile_conns.items():
                                aid = self._profile_to_account_map.get(pid, pid)
                                dispatcharr_connections[aid] = dispatcharr_connections.get(aid, 0) + cnt
                            total_account_conns = dispatcharr_connections.get(m3u_account_id, 0)

                            profiles = self._account_profiles.get(m3u_account_id, [])
                            if profiles:
                                # Profile-aware selection
                                selected_profile = self._select_probe_profile(
                                    m3u_account_id, dispatcharr_profile_conns, {},
                                    max_streams, total_account_conns
                                )
                                if not selected_profile:
                                    m3u_name = m3u_accounts_map.get(m3u_account_id, f"M3U {m3u_account_id}")
                                    skip_reason = f"M3U '{m3u_name}' at max connections ({total_account_conns}/{max_streams})"
                                    logger.info("[STREAM-PROBE] Skipping stream %s (%s): %s", stream_id, stream_name, skip_reason)
                            else:
                                # No profiles - use account-level logic
                                if total_account_conns >= max_streams:
                                    m3u_name = m3u_accounts_map.get(m3u_account_id, f"M3U {m3u_account_id}")
                                    skip_reason = f"M3U '{m3u_name}' at max connections ({total_account_conns}/{max_streams})"
                                    logger.info("[STREAM-PROBE] Skipping stream %s (%s): %s", stream_id, stream_name, skip_reason)

                    if skip_reason:
                        # Skip this stream - M3U is at capacity
                        stream_info = {"id": stream_id, "name": stream_name, "url": stream_url, "reason": skip_reason}
                        self._probe_progress_skipped_count += 1
                        self._probe_skipped_streams.append(stream_info)
                        probed_count += 1
                        await self._update_probe_notification()
                        continue

                    # Rewrite URL if a profile was selected
                    if selected_profile:
                        stream_url = self._rewrite_url_for_profile(stream_url, selected_profile)

                    # Account hold check (sequential mode)
                    if m3u_account_id:
                        self._init_account_ramp(m3u_account_id)
                        hold_remaining = self._get_account_hold_remaining(m3u_account_id)
                        if hold_remaining > 0:
                            logger.debug("[STREAM-PROBE] Account %s: waiting %.1fs", m3u_account_id, hold_remaining)
                            await asyncio.sleep(hold_remaining)

                    result = await self.probe_stream(stream_id, stream_url, stream_name)

                    # Track success/failure
                    probe_status = result.get("probe_status", "failed")
                    error_message = result.get("error_message", "")
                    stream_info = {"id": stream_id, "name": stream_name, "url": stream_url}
                    if probe_status == "success":
                        self._probe_progress_success_count += 1
                        self._probe_success_streams.append(stream_info)
                        if m3u_account_id:
                            self._record_probe_success(m3u_account_id)
                    else:
                        self._probe_progress_failed_count += 1
                        stream_info["error"] = error_message or "Unknown error"
                        self._probe_failed_streams.append(stream_info)
                        if m3u_account_id:
                            self._record_probe_failure(m3u_account_id, error_message)

                    probed_count += 1
                    await self._update_probe_notification()
                    await asyncio.sleep(0.5)  # Base rate limiting delay

            logger.info("[STREAM-PROBE] Completed probing %s streams", probed_count)
            logger.info("[STREAM-PROBE] Final counts: success=%s, "
                       "failed=%s, skipped=%s",
                       self._probe_progress_success_count,
                       self._probe_progress_failed_count,
                       self._probe_progress_skipped_count)
            self._probe_progress_status = "completed"
            self._probe_progress_current_stream = ""

            # Auto-reorder streams if configured
            reordered_channels = []
            logger.info("[STREAM-PROBE-SORT] Checking auto_reorder_after_probe setting: %s", self.auto_reorder_after_probe)
            if self.auto_reorder_after_probe:
                logger.info("[STREAM-PROBE] Auto-reorder is enabled, reordering streams in probed channels...")
                self._probe_progress_status = "reordering"
                self._probe_progress_current_stream = "Reordering streams..."
                try:
                    reordered_channels = await self._auto_reorder_channels(channel_groups_override, stream_to_channels)
                    logger.info("[STREAM-PROBE-SORT] Auto-reordered %s channels", len(reordered_channels))
                except Exception as e:
                    logger.error("[STREAM-PROBE] Auto-reorder failed: %s", e)

            # Save to probe history
            self._save_probe_history(start_time, probed_count, reordered_channels=reordered_channels)

            # Finalize notification with success/warning status
            await self._finalize_probe_notification()

            return {"status": "completed", "probed": probed_count, "reordered_channels": len(reordered_channels)}
        except Exception as e:
            logger.exception("[STREAM-PROBE] Probe all streams failed: %s", e)
            self._probe_progress_status = "failed"
            self._probe_progress_current_stream = ""

            # Save failed run to history
            self._save_probe_history(start_time, probed_count, error=str(e))

            # Finalize notification with error status
            await self._finalize_probe_notification()

            return {"status": "failed", "error": str(e), "probed": probed_count}
        finally:
            self._probing_in_progress = False

    def get_probe_progress(self) -> dict:
        """Get current probe all streams progress."""
        # Get ramp-up / hold summary
        rate_limit_info = self._get_ramp_summary()

        progress = {
            "in_progress": self._probing_in_progress,
            "total": self._probe_progress_total,
            "current": self._probe_progress_current,
            "status": self._probe_progress_status,
            "current_stream": self._probe_progress_current_stream,
            "success_count": self._probe_progress_success_count,
            "failed_count": self._probe_progress_failed_count,
            "skipped_count": self._probe_progress_skipped_count,
            "percentage": round((self._probe_progress_current / self._probe_progress_total * 100) if self._probe_progress_total > 0 else 0, 1),
            "rate_limited": rate_limit_info["is_rate_limited"],
            "rate_limited_hosts": rate_limit_info["hosts"],
            "max_backoff_remaining": rate_limit_info["max_backoff_remaining"]
        }
        # Log when probing is in progress for debugging
        if self._probing_in_progress:
            logger.debug("[STREAM-PROBE] in_progress=True, status=%s, %s/%s", self._probe_progress_status, self._probe_progress_current, self._probe_progress_total)
        return progress

    def _get_ramp_summary(self) -> dict:
        """Get a summary of current ramp-up / hold status for all accounts."""
        current_time = time.time()
        held_accounts = []
        max_hold = 0.0
        for account_id, state in self._account_ramp_state.items():
            remaining = state["hold_until"] - current_time
            if remaining > 0:
                held_accounts.append({
                    "host": f"Account {account_id}",
                    "backoff_remaining": round(remaining, 1),
                    "consecutive_429s": state["total_failures"],
                })
                max_hold = max(max_hold, remaining)
        return {
            "is_rate_limited": len(held_accounts) > 0,
            "hosts": held_accounts,
            "max_backoff_remaining": round(max_hold, 1) if max_hold > 0 else 0,
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
        logger.info("[STREAM-PROBE] Saved probe history entry: %s streams, %s success, %s failed, %s skipped%s", total, self._probe_progress_success_count, self._probe_progress_failed_count, self._probe_progress_skipped_count, reorder_msg)
        logger.info("[STREAM-PROBE] History entry stream lists: success_streams=%s, "
                   "failed_streams=%s, skipped_streams=%s",
                   len(history_entry['success_streams']),
                   len(history_entry['failed_streams']),
                   len(history_entry['skipped_streams']))

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
        """Get stats for multiple streams by their IDs.

        Uses batched queries to avoid massive IN clauses that can cause
        performance issues with large numbers of stream IDs.
        """
        if not stream_ids:
            return {}

        # Batch size of 500 to avoid massive IN clauses
        # SQLite handles this much better than 1900+ parameters
        BATCH_SIZE = 500
        result = {}

        session = get_session()
        try:
            # Process in batches to avoid huge IN clauses
            for i in range(0, len(stream_ids), BATCH_SIZE):
                batch = stream_ids[i:i + BATCH_SIZE]
                stats = session.query(StreamStats).filter(
                    StreamStats.stream_id.in_(batch)
                ).all()
                for s in stats:
                    result[s.stream_id] = s.to_dict()
            return result
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
            logger.error("[STREAM-PROBE] Failed to delete stats for stream %s: %s", stream_id, e)
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
                logger.info("[STREAM-PROBE] Purged %s old stream stats", deleted)
        except Exception as e:
            logger.error("[STREAM-PROBE] Failed to purge old stats: %s", e)
            session.rollback()
        finally:
            session.close()


# Global prober instance
_prober: Optional[StreamProber] = None


def get_prober() -> Optional[StreamProber]:
    """Get the global prober instance."""
    logger.debug("[STREAM-PROBE] get_prober() called, returning: %s (instance exists: %s)", _prober is not None, _prober is not None)
    return _prober


def set_prober(prober: StreamProber):
    """Set the global prober instance."""
    global _prober
    _prober = prober
    logger.info("[STREAM-PROBE] Stream prober instance set: %s", prober is not None)
