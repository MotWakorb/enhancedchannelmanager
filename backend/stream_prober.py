"""
Stream Prober service.
Uses ffprobe to extract stream metadata and stores results in SQLite.
Supports both scheduled and on-demand probing.
"""
import asyncio
import json
import logging
import math
import shutil
import tempfile
import time
from datetime import datetime, timedelta
from numbers import Real
from typing import Optional
from pathlib import Path
import os
import re

import journal
import safe_regex

import httpx
from security.ssrf import SchemeDowngrade, SSRFError
from security.stream_outbound import (
    stream_request,
    validated_subprocess_input,
)

from database import get_session
from models import StreamStats
from resdet_lock import RESDET_PIPELINE_LOCK_PATH, ResdetLockError, ResdetPipelineLock
from smart_sort_evaluator import (
    PointRule,
    StreamFacts,
    health_deprioritization_category,
    health_deprioritization_label,
    health_deprioritization_reason,
    sort_streams,
    stream_metadata_criteria,
)

logger = logging.getLogger(__name__)

PROBE_NETWORK_ROUTE_GUIDANCE = (
    "Provider connection failed from the ECM container. Raw stream probes do "
    "not use Dispatcharr's proxy; give ECM the same VPN or network route."
)


NETWORK_FAILURE_MARKERS = (
    "timed out", "timeout", "connection refused", "connection to",
    "network is unreachable", "no route to host", "name or service not known",
)


class ProbeNetworkRouteError(RuntimeError):
    """An upstream connection failure whose raw diagnostic must stay private."""


class ResolutionDetectionError(RuntimeError):
    """A resdet failure with a fixed operator-safe message."""


# Bead enhancedchannelmanager-iyvl9. XC providers 302 an
# https://<portal>/live/<user>/<pass>/<id>.ts request onto a plain-HTTP edge
# node and serve the video over HTTP there regardless, so the media is already
# unencrypted in transit and the redirect target's opaque-token path carries no
# credentials. Refusing the hop cost the operator every probe against such a
# provider and bought no confidentiality, so the PROBE PATH -- and only the
# probe path -- waives the scheme-downgrade clause. Everything else in the SSRF
# guard (denylist, resolve-then-connect-by-IP, redirect depth cap, origin
# pinning) still applies here unchanged.
#
# This constant is the single place the prober names the waiver; the three probe
# call sites below reference it, and no other module in the backend may pass
# SchemeDowngrade.ALLOW_STREAM_PROBE (enforced by
# tests/security/test_probe_scheme_downgrade.py).
PROBE_SCHEME_DOWNGRADE = SchemeDowngrade.ALLOW_STREAM_PROBE


# Exception types ECM RAISES ITSELF whose message is a fixed string we wrote and
# is therefore safe to show an operator (bead enhancedchannelmanager-3dn59).
#
# Classification is by exception ORIGIN, never by string-matching or scrubbing a
# message: subprocess diagnostics from ffmpeg/ffprobe can embed the provider URL
# with its credentials, so anything not on this list is reported by type only.
# Membership is tested by EXACT type rather than isinstance, so a subclass
# defined elsewhere cannot inherit the allowance on the strength of its base
# class. If a guard message ever needs to name a host, that is a deliberate
# decision at the raise site -- tests/security/test_probe_failure_diagnostics.py
# fails if one starts interpolating a URL.
OPERATOR_SAFE_EXCEPTION_TYPES: tuple = (
    SSRFError,               # ECM's own SSRF chokepoint -- fixed guard messages
    ProbeNetworkRouteError,  # raised here, with a fixed message
    ResolutionDetectionError,
)


def operator_safe_detail(exc: BaseException) -> Optional[str]:
    """Return ``exc``'s message iff its EXACT type is operator-safe, else None.

    See :data:`OPERATOR_SAFE_EXCEPTION_TYPES`. Returning ``None`` means the
    caller must log the exception CLASS only.
    """
    if type(exc) in OPERATOR_SAFE_EXCEPTION_TYPES:
        return str(exc).strip() or None
    return None


# Default configuration
DEFAULT_PROBE_TIMEOUT = 30  # seconds
BITRATE_SAMPLE_DURATION = 8  # seconds to sample stream for bitrate measurement

# Restrict ffprobe/ffmpeg to safe network protocols only — blocks file://, data://,
# concat:, subfile:, etc. URLs fed to these invocations come from Dispatcharr stream
# rows, which an attacker who can write a stream URL could otherwise weaponise for
# local-file exfiltration or DoS on the ECM host. Matches the whitelist used in
# backend/ffmpeg_builder/probe.py and backend/routers/stream_preview.py.
#
# tls and crypto are required internal protocols: HTTPS chains to tls for the TLS
# handshake, and HLS AES-128-encrypted segments chain to crypto. Without them,
# ffprobe fails on every HTTPS stream with "Protocol 'tls' not on whitelist"
# (GH-106). Neither is a URL scheme an attacker can specify directly — they are
# internal demuxers activated by https:// / hls variants.
FFPROBE_PROTOCOL_WHITELIST = "http,https,tls,crypto,tcp,udp,rtp,rtmp,pipe"
RELAY_PROTOCOL_WHITELIST = "http,tcp,crypto"
RESDET_MAX_WIDTH = 4096
RESDET_MAX_HEIGHT = 2160
RESDET_MAX_PIXELS = 8_847_360
RESDET_FRAME_MAX_BYTES = RESDET_MAX_PIXELS * 3 // 2 + 8192
RESDET_OUTPUT_MAX_BYTES = 64

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


def _normalized_number(value, *, parse_string: bool = False) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Real):
        return value if math.isfinite(value) else None
    if parse_string and isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _resolution_height(resolution) -> int | None:
    if not isinstance(resolution, str):
        return None
    try:
        parts = resolution.split("x")
        return int(parts[1]) if len(parts) == 2 else None
    except (ValueError, IndexError) as exc:
        logger.debug("[STREAM-PROBE] Suppressed resolution parse error: %s", exc)
        return None


def _prober_stream_facts(
    stream_id: int,
    stat,
    stream_m3u_map: dict[int, int],
    m3u_account_priorities: dict[str, int],
    custom_stream_ids: set[int],
    catchup_stream_ids: set[int],
    stream_metadata_known_ids: set[int] | None = None,
) -> StreamFacts:
    stats_available = stat is not None
    m3u_account_id = stream_m3u_map.get(stream_id)
    metadata_known = (
        stream_metadata_known_ids is None
        or stream_id in stream_metadata_known_ids
    )
    m3u_known = stream_id in stream_m3u_map or metadata_known
    priority_key = str(m3u_account_id) if m3u_account_id is not None else "custom"
    status = getattr(stat, "probe_status", None) if stats_available else None
    black_screen = (
        getattr(stat, "is_black_screen", None) if stats_available else None
    )
    low_fps = getattr(stat, "is_low_fps", None) if stats_available else None
    return StreamFacts(
        stream_id=stream_id,
        probe_succeeded=status == "success",
        probe_stats_available=stats_available,
        resolution_height=_resolution_height(getattr(stat, "resolution", None)),
        video_bitrate=_normalized_number(getattr(stat, "video_bitrate", None)),
        bitrate=_normalized_number(getattr(stat, "bitrate", None)),
        framerate=_normalized_number(getattr(stat, "fps", None), parse_string=True),
        video_codec=getattr(stat, "video_codec", None),
        m3u_priority=(
            _normalized_number(m3u_account_priorities.get(priority_key, 0))
            if m3u_known
            else None
        ),
        audio_channels=_normalized_number(getattr(stat, "audio_channels", None)),
        is_custom=(
            True
            if stream_id in custom_stream_ids
            else False if metadata_known else None
        ),
        is_catchup=(
            True
            if stream_id in catchup_stream_ids
            else False if metadata_known else None
        ),
        failed=(
            status in ("failed", "timeout", "pending")
            if isinstance(status, str)
            else None
        ),
        black_screen=black_screen if type(black_screen) is bool else None,
        low_fps=low_fps if type(low_fps) is bool else None,
    )


def smart_sort_streams(
    stream_ids: list[int],
    stats_map: dict,
    stream_m3u_map: dict[int, int] = None,
    stream_sort_priority: list[str] = None,
    stream_sort_enabled: dict[str, bool] = None,
    m3u_account_priorities: dict[str, int] = None,
    deprioritize_failed_streams: bool = True,
    deprioritize_black_screen: bool = True,
    deprioritize_low_fps: bool = True,
    failed_stream_sort_order: list[str] = None,
    channel_name: str = "unknown",
    custom_stream_ids: set[int] | None = None,
    catchup_stream_ids: set[int] | None = None,
    stream_sort_strategy: str = "priority",
    stream_sort_point_rules: tuple[PointRule, ...] = (),
    stream_metadata_known_ids: set[int] | None = None,
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
        deprioritize_black_screen: Whether to push black screen streams to bottom
            (only effective when deprioritize_failed_streams is also True)
        deprioritize_low_fps: Whether to push low FPS streams to bottom
            (only effective when deprioritize_failed_streams is also True)
        failed_stream_sort_order: Order of deprioritized categories (first = sorted higher)
        channel_name: Channel name for logging purposes
        custom_stream_ids: Set of stream IDs that are operator-added custom streams
            (Dispatcharr ``is_custom == True``). Drives the ``custom_streams``
            criterion. When None/omitted the criterion is inert (scores 0
            everywhere) so callers that don't supply it degrade gracefully.
    """
    if stream_m3u_map is None:
        stream_m3u_map = {}
    if custom_stream_ids is None:
        custom_stream_ids = set()
    if catchup_stream_ids is None:
        catchup_stream_ids = set()
    if stream_sort_priority is None:
        stream_sort_priority = [
            "resolution", "bitrate", "framerate", "video_codec", "m3u_priority",
            "audio_channels", "custom_streams", "catchup",
        ]
    if stream_sort_enabled is None:
        stream_sort_enabled = {
            "resolution": True,
            "bitrate": True,
            "framerate": True,
            "video_codec": False,
            "m3u_priority": False,
            "audio_channels": False,
            "custom_streams": False,
            "catchup": False,
        }
    if m3u_account_priorities is None:
        m3u_account_priorities = {}
    if failed_stream_sort_order is None:
        failed_stream_sort_order = ["failed", "black_screen", "low_fps"]

    active_criteria = [
        criterion
        for criterion in stream_sort_priority
        if stream_sort_enabled.get(criterion, False)
    ]
    safe_name = str(channel_name).replace("\n", "").replace("\r", "")
    if stream_sort_strategy == "priority" and not active_criteria:
        logger.warning(
            "[STREAM-PROBE-SORT] Channel '%s': no criteria enabled in Settings → Smart Sort; "
            "sorting will only use stream id as a tiebreaker",
            safe_name,
        )
    logger.info("[STREAM-PROBE-SORT] Channel '%s': Sorting %s streams", safe_name, len(stream_ids))
    logger.info("[STREAM-PROBE-SORT] Sort config: priority=%s, enabled=%s", stream_sort_priority, stream_sort_enabled)
    logger.info("[STREAM-PROBE-SORT] Active criteria (in order): %s", active_criteria)
    logger.info("[STREAM-PROBE-SORT] Deprioritize failed streams: %s", deprioritize_failed_streams)
    if deprioritize_failed_streams:
        logger.info("[STREAM-PROBE-SORT] Failed stream sort order: %s", failed_stream_sort_order)

    facts = [
        _prober_stream_facts(
            stream_id,
            stats_map.get(stream_id),
            stream_m3u_map,
            m3u_account_priorities,
            custom_stream_ids,
            catchup_stream_ids,
            stream_metadata_known_ids if stream_sort_strategy == "points" else None,
        )
        for stream_id in stream_ids
    ]
    sorted_ids = sort_streams(
        facts,
        strategy=stream_sort_strategy,
        priority_criteria=active_criteria,
        point_rules=stream_sort_point_rules,
        deprioritize_failed=deprioritize_failed_streams,
        deprioritize_black_screen=deprioritize_black_screen,
        deprioritize_low_fps=deprioritize_low_fps,
        failed_stream_sort_order=failed_stream_sort_order,
    )

    # Log the final sorted order
    logger.info("[STREAM-PROBE-SORT] Channel '%s' sorted order:", channel_name)
    for idx, stream_id in enumerate(sorted_ids):
        stat = stats_map.get(stream_id)
        stream_name = stat.stream_name if stat else f"Stream {stream_id}"
        status = stat.probe_status if stat else "no_stats"
        res = stat.resolution if stat else "?"
        logger.info("[STREAM-PROBE-SORT]   #%s: %s (id=%s, status=%s, res=%s)", idx+1, stream_name, stream_id, status, res)

    return sorted_ids


def _priority_deprioritized_streams(
    sorted_stream_ids: list[int],
    stats_map: dict,
    sort_settings: dict,
    stream_m3u_map: dict[int, int],
    custom_stream_ids: set[int],
    catchup_stream_ids: set[int],
    stream_metadata_known_ids: set[int],
) -> list[dict]:
    deprioritized = []
    for stream_id in sorted_stream_ids:
        stat = stats_map.get(stream_id)
        facts = _prober_stream_facts(
            stream_id,
            stat,
            stream_m3u_map,
            sort_settings["m3u_account_priorities"],
            custom_stream_ids,
            catchup_stream_ids,
            (
                stream_metadata_known_ids
                if sort_settings["stream_sort_strategy"] == "points"
                else None
            ),
        )
        category = health_deprioritization_category(
            facts,
            strategy=sort_settings["stream_sort_strategy"],
            deprioritize_failed=sort_settings["deprioritize_failed_streams"],
            deprioritize_black_screen=sort_settings["deprioritize_black_screen"],
            deprioritize_low_fps=sort_settings["deprioritize_low_fps"],
        )
        reason = health_deprioritization_reason(
            facts, category, getattr(stat, "probe_status", None)
        )
        if reason is None:
            continue
        deprioritized.append({
            "id": stream_id,
            "name": stat.stream_name if stat else f"Stream {stream_id}",
            "reason": reason,
        })
    return deprioritized


class StreamProber:
    """
    Background service that probes streams using ffprobe.
    Supports scheduled probing and on-demand single/batch probes.
    """

    def __init__(
        self,
        client,
        probe_timeout: int = DEFAULT_PROBE_TIMEOUT,
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
        deprioritize_black_screen: bool = True,  # Deprioritize black screen streams in smart sort
        deprioritize_low_fps: bool = True,  # Deprioritize low FPS streams in smart sort
        black_screen_detection_enabled: bool = False,  # Run ffmpeg blackdetect after successful probe
        black_screen_sample_duration: int = 5,  # Seconds to sample for black screen detection (3-30)
        low_fps_threshold: int = 20,  # FPS below this value is considered "low FPS"
        stream_sort_priority: list[str] = None,  # Priority order for Smart Sort criteria
        stream_sort_enabled: dict[str, bool] = None,  # Which criteria are enabled for Smart Sort
        stream_fetch_page_limit: int = 200,  # Max pages when fetching streams (200 * 500 = 100K streams)
        m3u_account_priorities: dict[str, int] = None,  # M3U account priorities (account_id -> priority)
        failed_stream_sort_order: list[str] = None,  # Order of deprioritized categories (first = sorted higher)
        stream_sort_strategy: str = "priority",
        stream_sort_point_rules: tuple[PointRule, ...] = (),
        use_resdet_for_resolution: bool = False,
        _resdet_lock_path: Path = RESDET_PIPELINE_LOCK_PATH,
    ):
        self.client = client
        self.probe_timeout = probe_timeout
        self.use_resdet_for_resolution = use_resdet_for_resolution
        self._resdet_lock_path = _resdet_lock_path
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
        self.deprioritize_black_screen = deprioritize_black_screen
        self.deprioritize_low_fps = deprioritize_low_fps
        self.black_screen_detection_enabled = black_screen_detection_enabled
        self.low_fps_threshold = max(1, min(60, low_fps_threshold))  # Clamp 1-60
        self.black_screen_sample_duration = max(3, min(30, black_screen_sample_duration))  # Clamp 3-30
        self.stream_fetch_page_limit = stream_fetch_page_limit
        logger.info("[STREAM-PROBE] auto_reorder_after_probe=%s", auto_reorder_after_probe)
        # Smart Sort configuration
        self.stream_sort_priority = (
            stream_sort_priority
            if stream_sort_priority is not None
            else [
                "resolution", "bitrate", "framerate", "video_codec",
                "m3u_priority", "audio_channels", "custom_streams", "catchup",
            ]
        )
        self.stream_sort_enabled = (
            stream_sort_enabled
            if stream_sort_enabled is not None
            else {"resolution": True, "bitrate": True, "framerate": True, "m3u_priority": False, "audio_channels": False}
        )
        self.m3u_account_priorities = (
            m3u_account_priorities
            if m3u_account_priorities is not None
            else {}
        )
        self.failed_stream_sort_order = (
            failed_stream_sort_order
            if failed_stream_sort_order is not None
            else ["failed", "black_screen", "low_fps"]
        )
        self.stream_sort_strategy = stream_sort_strategy
        self.stream_sort_point_rules = tuple(stream_sort_point_rules)
        self._probe_cancelled = False  # Controls cancellation of in-progress probe
        self._probe_paused = False  # Controls pausing of in-progress probe
        self._probing_in_progress = False
        self._bulk_probe_tasks: set[asyncio.Task] = set()
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
        self._probe_black_screen_streams = []  # List of {id, name, url} for black screen probes
        self._probe_progress_black_screen_count = 0
        self._probe_low_fps_streams = []  # List of {id, name, url} for low FPS probes
        self._probe_progress_low_fps_count = 0
        # Probe history - list of last 5 probe runs
        self._probe_history = []  # List of {timestamp, total, success_count, failed_count, status, success_streams, failed_streams}
        # Scope from the last successful scheduled probe (used to scope reprobes).
        # None = no prior probe, "all" = all groups, "scoped" = selected groups.
        self._last_probe_scope_kind: str | None = None
        self._last_probe_channel_stream_ids: set = set()

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
        m3u_account_priorities: dict[str, int],
        failed_stream_sort_order: list[str] = None,
        deprioritize_black_screen: bool = None,
        deprioritize_low_fps: bool = None,
        stream_sort_strategy: str = None,
        stream_sort_point_rules: tuple[PointRule, ...] = None,
    ) -> None:
        """Update the sort settings.

        This allows updating the prober's sort settings without restarting the service.
        Called when settings are saved to ensure smart sort uses the latest config.

        Args:
            stream_sort_priority: Priority order for sort criteria.
            stream_sort_enabled: Which criteria are enabled.
            m3u_account_priorities: M3U account priorities (account_id -> priority value).
            deprioritize_black_screen: Per-category override for black screen streams.
            deprioritize_low_fps: Per-category override for low FPS streams.
        """
        old_priority = self.stream_sort_priority
        old_enabled = self.stream_sort_enabled
        old_m3u_priorities = self.m3u_account_priorities
        old_failed_order = self.failed_stream_sort_order
        old_strategy = self.stream_sort_strategy
        self.stream_sort_priority = stream_sort_priority
        self.stream_sort_enabled = stream_sort_enabled
        self.m3u_account_priorities = m3u_account_priorities
        if failed_stream_sort_order is not None:
            self.failed_stream_sort_order = failed_stream_sort_order
        if deprioritize_black_screen is not None:
            self.deprioritize_black_screen = deprioritize_black_screen
        if deprioritize_low_fps is not None:
            self.deprioritize_low_fps = deprioritize_low_fps
        if stream_sort_strategy is not None:
            self.stream_sort_strategy = stream_sort_strategy
        if stream_sort_point_rules is not None:
            self.stream_sort_point_rules = tuple(stream_sort_point_rules)
        logger.info("[STREAM-PROBE] Updated sort settings: priority=%s->%s, "
                    "enabled=%s->%s, "
                    "m3u_priorities=%s->%s, "
                    "failed_order=%s->%s, strategy=%s->%s",
                    old_priority, self.stream_sort_priority,
                    old_enabled, self.stream_sort_enabled,
                    old_m3u_priorities, self.m3u_account_priorities,
                    old_failed_order, self.failed_stream_sort_order,
                    old_strategy, self.stream_sort_strategy)

    def _stream_metadata_criteria(self) -> frozenset[str]:
        return self._stream_metadata_criteria_for(
            self._sort_settings_snapshot()
        )

    def _sort_settings_snapshot(self) -> dict:
        return {
            "stream_sort_priority": tuple(self.stream_sort_priority),
            "stream_sort_enabled": dict(self.stream_sort_enabled),
            "m3u_account_priorities": dict(self.m3u_account_priorities),
            "deprioritize_failed_streams": self.deprioritize_failed_streams,
            "deprioritize_black_screen": self.deprioritize_black_screen,
            "deprioritize_low_fps": self.deprioritize_low_fps,
            "failed_stream_sort_order": tuple(self.failed_stream_sort_order),
            "stream_sort_strategy": self.stream_sort_strategy,
            "stream_sort_point_rules": tuple(self.stream_sort_point_rules),
        }

    @staticmethod
    def _stream_metadata_criteria_for(sort_settings: dict) -> frozenset[str]:
        active_priority = (
            criterion
            for criterion in sort_settings["stream_sort_priority"]
            if sort_settings["stream_sort_enabled"].get(criterion, False)
        )
        return stream_metadata_criteria(
            sort_settings["stream_sort_strategy"],
            priority_criteria=active_priority,
            point_rules=sort_settings["stream_sort_point_rules"],
        )

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

    async def _create_probe_notification(
        self, total_streams: int, send_alerts: bool = True
    ) -> Optional[int]:
        """Create a notification for probe progress.

        Deletes any existing probe notifications first to ensure only one exists.

        Args:
            total_streams: Total number of streams in this probe run.
            send_alerts: Whether the "probe started" message should dispatch an
                external alert (Telegram/Discord/email). This is an info-level
                notification, so external dispatch must respect the stream-probe
                task's ``alert_on_info`` gate — the caller passes the already-gated
                value (GH #462). The in-app notification is created regardless.

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
                metadata=metadata,
                send_alerts=send_alerts,
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
                    "black_screen": self._probe_progress_black_screen_count,
                    "low_fps": self._probe_progress_low_fps_count,
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

    async def _finalize_probe_notification(self, send_alerts: bool = True) -> None:
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
            elif self._probe_progress_black_screen_count > 0:
                notification_type = "warning"
            elif self._probe_progress_low_fps_count > 0:
                notification_type = "warning"
            else:
                notification_type = "success"

            # Build message
            total_streams = self._probe_progress_total
            ok = self._probe_progress_success_count
            failed = self._probe_progress_failed_count
            skipped = self._probe_progress_skipped_count
            black = self._probe_progress_black_screen_count
            low = self._probe_progress_low_fps_count

            message = (
                f"Stream probe complete: {total_streams} stream(s) — {ok} ok, {failed} failed, {skipped} skipped"
            )
            if black or low:
                message += f" ({black} black screen, {low} low FPS)"

            # Name the dominant failure cause instead of leaving the operator a
            # bare count (bead enhancedchannelmanager-3dn59). Reasons are the
            # operator-safe strings chosen in probe_stream, never subprocess
            # text.
            failure_breakdown = self._failure_breakdown()
            if failed and failure_breakdown:
                top = failure_breakdown[0]
                message += f" — most common failure: {top['reason']} ({top['count']})"

            metadata = {
                "failure_breakdown": failure_breakdown,
                "progress": {
                    "current": self._probe_progress_total,
                    "total": self._probe_progress_total,
                    "success": self._probe_progress_success_count,
                    "failed": self._probe_progress_failed_count,
                    "skipped": self._probe_progress_skipped_count,
                    "black_screen": self._probe_progress_black_screen_count,
                    "low_fps": self._probe_progress_low_fps_count,
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

            if send_alerts:
                # Manual/non-scheduled probes retain their direct completion alert.
                try:
                    from alert_methods import send_alert
                    failed = self._probe_progress_failed_count
                    alert_metadata = {
                        "streams_scheduled": self._probe_progress_total,
                        "streams_ok": self._probe_progress_success_count,
                        "streams_failed": failed,
                        "streams_skipped": self._probe_progress_skipped_count,
                        "black_screen_detections": self._probe_progress_black_screen_count,
                        "low_fps_detections": self._probe_progress_low_fps_count,
                        # Legacy key — alert_methods probe_failures min_failures threshold reads failed_count
                        "failed_count": failed,
                        "failure_breakdown": failure_breakdown,
                    }
                    await send_alert(
                        title="Stream Probe",
                        message=message,
                        notification_type=notification_type,
                        source="stream_probe",
                        metadata=alert_metadata,
                        alert_category="probe_failures",
                    )
                except Exception as alert_err:
                    logger.error("[STREAM-PROBE] Failed to dispatch probe alert: %s", alert_err)
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
            "[STREAM-PROBE] StreamProber initialized (timeout: %ss, max_concurrent: %s)",
            self.probe_timeout, self.max_concurrent_probes
        )

    async def stop(self):
        """Stop the stream prober and cancel any in-progress probes."""
        logger.info("[STREAM-PROBE] StreamProber stopping...")
        self._probe_cancelled = True
        for task in tuple(self._bulk_probe_tasks):
            task.cancel()
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
        for task in tuple(self._bulk_probe_tasks):
            task.cancel()
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

            if self.use_resdet_for_resolution:
                width, height = await self._run_resdet(url)
                for stream in result.get("streams", []):
                    if stream.get("codec_type") == "video":
                        stream["width"] = width
                        stream["height"] = height
                        break

            # Measure actual bitrate by downloading stream data
            logger.debug("[STREAM-PROBE] Measuring bitrate for stream %s", stream_id)
            measured_bitrate = await self._measure_stream_bitrate(url)

            # Black screen detection (opt-in). `is_black` stays None when
            # detection is disabled OR when it returned indeterminate (timeout
            # / no YAVG data), so _save_probe_result knows not to overwrite
            # any prior is_black_screen value.
            is_black: Optional[bool] = None
            if self.black_screen_detection_enabled:
                logger.debug("[STREAM-PROBE] Running black screen detection for stream %s", stream_id)
                is_black = await self._detect_black_screen(url)

            # Save probe result with both ffprobe metadata and measured bitrate
            saved = self._save_probe_result(
                stream_id, name, result, "success", None, measured_bitrate, is_black
            )
            # Optionally reflect stats back to Dispatcharr. Fire-and-forget semantics:
            # any failure is logged but never fails the probe.
            await self._push_stats_to_dispatcharr(stream_id, saved)
            return saved
        except asyncio.TimeoutError:
            logger.warning("[STREAM-PROBE] Stream %s probe timed out after %ss", stream_id, self.probe_timeout)
            return self._save_probe_result(
                stream_id,
                name,
                None,
                "timeout",
                PROBE_NETWORK_ROUTE_GUIDANCE
            )
        except Exception as e:
            # FFmpeg/ffprobe diagnostics can contain the provider URL (including
            # embedded credentials) or a redirect target, so subprocess text is
            # never copied into logs or persisted state. An exception ECM's OWN
            # guard raised is a different kind of value: its message is a fixed
            # string we wrote, and it is exactly the one line the operator needs.
            # Classify by ORIGIN (operator_safe_detail), not by inspecting the
            # message. Bead enhancedchannelmanager-3dn59.
            detail = operator_safe_detail(e)
            if detail is not None:
                logger.error(
                    "[STREAM-PROBE] Stream %s probe failed (%s): %s",
                    stream_id,
                    type(e).__name__,
                    detail,
                )
            else:
                logger.error(
                    "[STREAM-PROBE] Stream %s probe failed (%s)",
                    stream_id,
                    type(e).__name__,
                )
            if isinstance(e, ProbeNetworkRouteError):
                public_error = PROBE_NETWORK_ROUTE_GUIDANCE
            elif detail is not None:
                # Surfaced in the run report's failure breakdown, so a wholesale
                # guard rejection reads as one named cause instead of N nameless
                # failures.
                public_error = detail
            else:
                public_error = "Probe failed"
            return self._save_probe_result(stream_id, name, None, "failed", public_error)

    async def _run_ffprobe(self, url: str, _retry_attempt: int = 0) -> dict:
        """Run ffprobe and parse JSON output."""
        headers = {"User-Agent": "VLC/3.0.20 LibVLC/3.0.20"}
        async with validated_subprocess_input(
            url, headers=headers, scheme_downgrade=PROBE_SCHEME_DOWNGRADE
        ) as subprocess_input:
            cmd = [
                "ffprobe",
                "-v",
                "error",  # Show errors in stderr (was "quiet" which suppressed everything)
                "-protocol_whitelist", (
                    RELAY_PROTOCOL_WHITELIST
                    if subprocess_input.is_http_relay
                    else FFPROBE_PROTOCOL_WHITELIST
                ),
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                "-timeout",
                str(self.probe_timeout * 1000000),  # microseconds
                subprocess_input.argument,
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
                await process.wait()
                raise

        if process.returncode != 0:
            # Classify the complete diagnostic only after removing the exact
            # input URL. URLs may themselves contain words such as
            # "connection to", and truncating before redaction can both create
            # false network classifications and hide a real marker that comes
            # after a long credential-bearing URL.
            error_text = stderr.decode(errors="replace").strip() if stderr else ""
            error_text = error_text.replace(url, "[REDACTED stream URL]")
            if not error_text:
                error_text = f"Exit code {process.returncode} (no stderr output)"

            # Retry only on genuinely transient errors (server errors, connection drops).
            # Do NOT retry 404 (dead stream), connection timeouts (server down), or
            # invalid data (corrupt stream) — these won't succeed on retry and just
            # waste semaphore time.
            transient_patterns = ("5XX", "500", "502", "503", "520", "Input/output error", "Stream ends prematurely", "Connection reset", "Broken pipe")
            if any(p in error_text for p in transient_patterns) and "404" not in error_text and _retry_attempt < self.probe_retry_count:
                logger.info("[STREAM-PROBE] Transient provider error — retry %s/%s in %ss", _retry_attempt + 1, self.probe_retry_count, self.probe_retry_delay)
                await asyncio.sleep(self.probe_retry_delay)
                return await self._run_ffprobe(url, _retry_attempt=_retry_attempt + 1)

            if any(marker in error_text.lower() for marker in NETWORK_FAILURE_MARKERS):
                raise ProbeNetworkRouteError("Provider connection failed")
            raise RuntimeError("ffprobe failed: [REDACTED diagnostic]")

        output = stdout.decode()
        if not output.strip():
            raise RuntimeError("ffprobe returned empty output")

        return json.loads(output)

    async def _run_resdet(self, url: str) -> tuple[int, int]:
        """Serialize the complete native frame-extract and analysis pipeline."""
        try:
            async with ResdetPipelineLock(self._resdet_lock_path) as pipeline_lock:
                return await self._run_resdet_pipeline(url, pipeline_lock.fileno())
        except ResdetLockError:
            raise ResolutionDetectionError("resdet pipeline lock is unavailable") from None

    async def _run_resdet_pipeline(self, url: str, lock_fd: int) -> tuple[int, int]:
        """Decode one bounded Y4M frame, then analyze that local file with resdet."""

        async def kill_and_reap(process) -> None:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, 9)
                except ProcessLookupError:
                    pass
            await asyncio.shield(process.wait())

        async def read_bounded_output(
            process, limit: int, invalid_message: str
        ) -> bytes:
            async def read_and_wait() -> bytes:
                try:
                    output = await process.stdout.readexactly(limit + 1)
                except asyncio.IncompleteReadError as exc:
                    output = exc.partial
                if len(output) > limit:
                    await kill_and_reap(process)
                    raise ResolutionDetectionError(invalid_message)
                await process.wait()
                return output

            try:
                return await asyncio.wait_for(
                    read_and_wait(), timeout=self.probe_timeout + 7
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                await kill_and_reap(process)
                raise

        headers = {"User-Agent": "VLC/3.0.20 LibVLC/3.0.20"}
        with tempfile.TemporaryDirectory(prefix="ecm-resdet-") as directory:
            frame_path = Path(directory) / "frame.y4m"
            async with validated_subprocess_input(
                url, headers=headers, scheme_downgrade=PROBE_SCHEME_DOWNGRADE
            ) as subprocess_input:
                ffmpeg = await asyncio.create_subprocess_exec(
                    "/usr/bin/timeout",
                    "--signal=KILL",
                    f"{self.probe_timeout + 5}s",
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    (
                        RELAY_PROTOCOL_WHITELIST
                        if subprocess_input.is_http_relay
                        else FFPROBE_PROTOCOL_WHITELIST
                    ),
                    "-timeout",
                    str(self.probe_timeout * 1000000),
                    "-i",
                    subprocess_input.argument,
                    "-frames:v",
                    "1",
                    "-an",
                    "-sn",
                    "-dn",
                    "-pix_fmt",
                    "yuv420p",
                    "-f",
                    "yuv4mpegpipe",
                    "-fs",
                    str(RESDET_FRAME_MAX_BYTES),
                    "-y",
                    "pipe:1",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                    pass_fds=(lock_fd,),
                )
                try:
                    frame_data = await read_bounded_output(
                        ffmpeg,
                        RESDET_FRAME_MAX_BYTES,
                        "resdet returned an invalid frame",
                    )
                except asyncio.TimeoutError:
                    raise ResolutionDetectionError(
                        "resdet resolution detection timed out"
                    ) from None

            if ffmpeg.returncode in (124, 137):
                raise ResolutionDetectionError("resdet resolution detection timed out")
            if ffmpeg.returncode != 0:
                raise ResolutionDetectionError(
                    "resdet frame extraction failed"
                )
            self._validate_resdet_y4m_frame(frame_data)
            try:
                frame_path.write_bytes(frame_data)
            except OSError:
                raise ResolutionDetectionError(
                    "resdet frame extraction failed"
                ) from None
            process = await asyncio.create_subprocess_exec(
                "/usr/bin/timeout",
                "--signal=KILL",
                f"{self.probe_timeout + 5}s",
                "/usr/local/bin/resdet",
                "-R",
                "Y4M",
                "-v",
                "1",
                "-n",
                "1",
                str(frame_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
                pass_fds=(lock_fd,),
            )
            try:
                stdout = await read_bounded_output(
                    process,
                    RESDET_OUTPUT_MAX_BYTES,
                    "resdet returned an invalid resolution",
                )
            except asyncio.TimeoutError:
                raise ResolutionDetectionError(
                    "resdet resolution detection timed out"
                ) from None

            if process.returncode in (124, 137):
                raise ResolutionDetectionError("resdet resolution detection timed out")
            if process.returncode != 0:
                raise ResolutionDetectionError("resdet resolution detection failed")

            parts = stdout.decode(errors="replace").strip().split()
            try:
                width, height = (int(value) for value in parts)
            except ValueError:
                raise ResolutionDetectionError(
                    "resdet returned an invalid resolution"
                ) from None
            if width <= 0 or height <= 0:
                raise ResolutionDetectionError("resdet returned an invalid resolution")
            return width, height

    @staticmethod
    def _validate_resdet_y4m_frame(frame_data: bytes) -> None:
        """Validate FFmpeg's one-frame yuv420p Y4M artifact without allocating from it."""
        if not frame_data or len(frame_data) > RESDET_FRAME_MAX_BYTES:
            raise ResolutionDetectionError("resdet returned an invalid frame")
        header_end = frame_data.find(b"\n")
        if header_end < 0 or header_end > 4096:
            raise ResolutionDetectionError("resdet returned an invalid frame")
        try:
            tokens = frame_data[:header_end].decode("ascii").split(" ")
        except UnicodeDecodeError:
            raise ResolutionDetectionError("resdet returned an invalid frame") from None
        if not tokens or tokens[0] != "YUV4MPEG2" or any(not token for token in tokens):
            raise ResolutionDetectionError("resdet returned an invalid frame")

        dimensions: dict[str, int] = {}
        for axis in ("W", "H"):
            values = [token[1:] for token in tokens[1:] if token.startswith(axis)]
            if len(values) != 1 or re.fullmatch(r"[1-9][0-9]{0,9}", values[0]) is None:
                raise ResolutionDetectionError("resdet returned an invalid frame")
            dimensions[axis] = int(values[0])
        chroma = [token for token in tokens[1:] if token.startswith("C")]
        if len(chroma) != 1 or chroma[0] not in {
            "C420",
            "C420jpeg",
            "C420mpeg2",
            "C420paldv",
        }:
            raise ResolutionDetectionError("resdet returned an invalid frame")

        width, height = dimensions["W"], dimensions["H"]
        pixels = width * height
        if (
            width > RESDET_MAX_WIDTH
            or height > RESDET_MAX_HEIGHT
            or pixels > RESDET_MAX_PIXELS
        ):
            raise ResolutionDetectionError("resdet returned an invalid frame")
        frame_start = header_end + 1
        marker = b"FRAME\n"
        if frame_data[frame_start : frame_start + len(marker)] != marker:
            raise ResolutionDetectionError("resdet returned an invalid frame")
        chroma_pixels = ((width + 1) // 2) * ((height + 1) // 2)
        required_size = frame_start + len(marker) + pixels + 2 * chroma_pixels
        if required_size > RESDET_FRAME_MAX_BYTES or len(frame_data) != required_size:
            raise ResolutionDetectionError("resdet returned an invalid frame")

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
            async with stream_request(
                url,
                timeout=timeout,
                headers=headers,
                scheme_downgrade=PROBE_SCHEME_DOWNGRADE,
            ) as response:
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
                logger.info("[STREAM-PROBE] Measured bitrate: %d bytes in %.2fs = %d bps (%.2f Mbps)", bytes_downloaded, elapsed, bitrate_bps, bitrate_bps/1000000)
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
            # Client exceptions can include the requested URL or a redirect
            # target. Keep diagnostics useful without exposing either value --
            # except for ECM's own guard exceptions, whose messages are fixed
            # strings we wrote (bead enhancedchannelmanager-3dn59).
            detail = operator_safe_detail(e)
            if detail is not None:
                logger.warning(
                    "[STREAM-PROBE] Failed to measure bitrate (%s): %s",
                    type(e).__name__,
                    detail,
                )
            else:
                logger.warning(
                    "[STREAM-PROBE] Failed to measure bitrate (%s)",
                    type(e).__name__,
                )
            return None

    # YAVG brightness threshold for dark/black screen detection.
    # In YUV TV range, 16 = pure black. Real content typically YAVG > 40.
    # Threshold of 20 catches: pure black, dark slates, off-air screens with small logos.
    BLACK_SCREEN_YAVG_THRESHOLD = 20

    async def _detect_black_screen(self, url: str) -> Optional[bool]:
        """Detect dark/black screens by measuring average brightness (YAVG) via signalstats.

        Uses ffmpeg signalstats to compute per-frame average luma (YAVG).
        In YUV TV range: 16 = pure black, ~88 = typical content.

        Returns:
            True  — average YAVG across the sample is below threshold (dark/black).
            False — average YAVG is above threshold (clean content).
            None  — detection was indeterminate (ffmpeg timed out, produced no
                    YAVG samples, or exited abnormally). Callers MUST NOT treat
                    None as "clean"; prior is_black_screen state should be
                    preserved so a scan-task timeout cannot silently overwrite
                    a manual probe's finding.

        Why: this method runs both inline from probe_stream() (where ffprobe
        plus bitrate measurement have already warmed the upstream connection)
        and cold from the standalone Black Screen Scan task. Cold starts on
        slow-to-buffer IPTV providers can exceed a tight asyncio budget. We
        add an ffmpeg input -timeout for network stall detection and give the
        wait_for a generous grace window so cold-start false-timeouts don't
        flip streams to "clean".
        """
        headers = {"User-Agent": "VLC/3.0.20 LibVLC/3.0.20"}
        # Grace window: sample duration + ample headroom for cold-start
        # buffering, connection setup, and ffmpeg startup. The previous 15-s
        # grace was too tight for cold scans and caused every timeout to be
        # silently treated as a clean stream.
        total_timeout = self.black_screen_sample_duration + 30

        async with validated_subprocess_input(
            url, headers=headers, scheme_downgrade=PROBE_SCHEME_DOWNGRADE
        ) as subprocess_input:
            cmd = [
                "ffmpeg",
                "-protocol_whitelist", (
                    RELAY_PROTOCOL_WHITELIST
                    if subprocess_input.is_http_relay
                    else FFPROBE_PROTOCOL_WHITELIST
                ),
                # Network stall guard (microseconds). If the upstream stops
                # delivering data for this long, ffmpeg bails on its own.
                "-timeout", "15000000",
                "-i", subprocess_input.argument,
                "-t", str(self.black_screen_sample_duration),
                "-vf", "signalstats,metadata=mode=print:key=lavfi.signalstats.YAVG",
                "-an", "-f", "null", "-",
            ]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=total_timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                logger.warning(
                    "[STREAM-PROBE] Black screen detection timed out after %ss",
                    total_timeout,
                )
                return None
        output = stderr.decode()
        yavg_values = re.findall(r'lavfi\.signalstats\.YAVG=([\d.]+)', output)
        if not yavg_values:
            logger.debug("[STREAM-PROBE] No YAVG data from signalstats")
            return None
        avg_brightness = sum(float(v) for v in yavg_values) / len(yavg_values)
        is_dark = avg_brightness < self.BLACK_SCREEN_YAVG_THRESHOLD
        if is_dark:
            logger.warning("[STREAM-PROBE] Dark screen detected (YAVG=%.1f, threshold=%d)",
                           avg_brightness, self.BLACK_SCREEN_YAVG_THRESHOLD)
        else:
            logger.debug("[STREAM-PROBE] Screen brightness OK (YAVG=%.1f)", avg_brightness)
        return is_dark

    def _save_probe_result(
        self,
        stream_id: int,
        stream_name: Optional[str],
        ffprobe_data: Optional[dict],
        status: str,
        error_message: Optional[str],
        measured_bitrate: Optional[int] = None,
        is_black_screen: Optional[bool] = None,
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
                stats.is_black_screen = False  # Reset on failure (stream state unknown)
                stats.is_low_fps = False  # Reset on failure (stream state unknown)
            elif status == "success":
                stats.consecutive_failures = 0
                # Only update is_black_screen when detection actually produced
                # a definitive result. is_black_screen=None means detection
                # timed out or returned no YAVG samples — preserve whatever
                # the prior state was so a scan-task cold-start timeout can't
                # silently wipe out a manual probe's finding.
                if self.black_screen_detection_enabled and is_black_screen is not None:
                    stats.is_black_screen = is_black_screen

            if ffprobe_data and status == "success":
                self._parse_ffprobe_data(stats, ffprobe_data)

            # Detect low FPS (< 20) from already-parsed fps field
            if status == "success" and stats.fps:
                try:
                    fps_val = float(stats.fps)
                    stats.is_low_fps = fps_val < self.low_fps_threshold
                    if stats.is_low_fps:
                        logger.warning("[STREAM-PROBE] Low FPS detected (%.1f < %d) for stream %s", fps_val, self.low_fps_threshold, stream_id)
                except (ValueError, TypeError):
                    stats.is_low_fps = False
            elif status == "success":
                stats.is_low_fps = False

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

    async def _push_stats_to_dispatcharr(self, stream_id: int, ecm_stats: dict) -> None:
        """Reflect ECM probe stats back to Dispatcharr via PATCH.

        Reads the `push_stream_stats_to_dispatcharr` setting at call time so
        toggling it takes effect without restarting the prober. On any failure,
        logs and returns — probing is never blocked by this.

        GET-then-merge-then-PATCH preserves keys Dispatcharr wrote itself
        (e.g. pixel_format, audio_bitrate from playback) that ECM doesn't know.
        """
        try:
            from config import get_settings
            if not get_settings().push_stream_stats_to_dispatcharr:
                return
            if ecm_stats.get("probe_status") != "success":
                return

            # Build the payload in Dispatcharr's schema. ECM's "fps" is "source_fps"
            # in Dispatcharr. Omit None values so we don't blank out real data on merge.
            mapped: dict = {}
            if ecm_stats.get("resolution") is not None:
                mapped["resolution"] = ecm_stats["resolution"]
                try:
                    w, h = ecm_stats["resolution"].split("x")
                    mapped["width"] = int(w)
                    mapped["height"] = int(h)
                except (ValueError, AttributeError):
                    # Resolution string is malformed (e.g., "unknown"); skip width/height
                    # but keep the raw resolution above so the operator still sees it.
                    pass
            if ecm_stats.get("video_codec") is not None:
                mapped["video_codec"] = ecm_stats["video_codec"]
            if ecm_stats.get("audio_codec") is not None:
                mapped["audio_codec"] = ecm_stats["audio_codec"]
            if ecm_stats.get("audio_channels") is not None:
                _channel_names = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}
                raw_ch = ecm_stats["audio_channels"]
                mapped["audio_channels"] = (
                    _channel_names.get(raw_ch, str(raw_ch)) if isinstance(raw_ch, int) else raw_ch
                )
            if ecm_stats.get("video_bitrate") is not None:
                mapped["ffmpeg_output_bitrate"] = round(ecm_stats["video_bitrate"] / 1000, 1)
            if ecm_stats.get("fps") is not None:
                try:
                    mapped["source_fps"] = float(ecm_stats["fps"])
                except (ValueError, TypeError):
                    # fps was non-numeric (e.g., "30/1" already failed to parse upstream);
                    # omit source_fps so we don't push a bogus value to Dispatcharr.
                    pass
            if ecm_stats.get("stream_type") is not None:
                mapped["stream_type"] = ecm_stats["stream_type"]

            if not mapped:
                logger.debug("[STREAM-PROBE] No stats to push for stream %s", stream_id)
                return

            # Read existing stream_stats so we merge instead of replace.
            try:
                existing = await self.client.get_stream(stream_id)
            except Exception as e:
                logger.warning(
                    "[STREAM-PROBE] Could not fetch stream %s for stats push (skipping): %s",
                    stream_id, e,
                )
                return

            merged = dict(existing.get("stream_stats") or {})
            merged.update(mapped)

            await self.client.update_stream(stream_id, {
                "stream_stats": merged,
                "stream_stats_updated_at": datetime.utcnow().isoformat() + "Z",
            })
            logger.info(
                "[STREAM-PROBE] Pushed stream_stats to Dispatcharr for stream %s (keys=%s)",
                stream_id, sorted(mapped.keys()),
            )
        except Exception as e:
            logger.warning(
                "[STREAM-PROBE] Failed to push stream_stats to Dispatcharr for stream %s: %s",
                stream_id, e,
            )

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

    async def _resolve_channel_group_ids(
        self,
        channel_groups_override: list[str] | None = None,
        channel_group_ids_override: frozenset[int] | None = None,
    ) -> frozenset[int] | None:
        """Resolve one invocation's group scope to stable numeric IDs."""
        if channel_groups_override is not None and channel_group_ids_override is not None:
            raise ValueError("channel group names and IDs cannot both be provided")
        if channel_groups_override is None and channel_group_ids_override is None:
            return None

        requested_ids = (
            frozenset(channel_group_ids_override)
            if channel_group_ids_override is not None
            else None
        )
        requested_names = (
            list(dict.fromkeys(channel_groups_override))
            if channel_groups_override is not None
            else None
        )
        if requested_ids == frozenset() or requested_names == []:
            return frozenset()

        all_groups = await self.client.get_channel_groups()
        if requested_ids is not None:
            available_ids = {group["id"] for group in all_groups}
            resolved_ids = requested_ids & available_ids
            stale_ids = requested_ids - resolved_ids
            if stale_ids:
                logger.warning(
                    "[STREAM-PROBE] Requested channel group IDs not found: %s",
                    sorted(stale_ids),
                )
            return frozenset(resolved_ids)

        ids_by_name: dict[str, list[int]] = {}
        for group in all_groups:
            ids_by_name.setdefault(group.get("name"), []).append(group["id"])

        resolved_ids: set[int] = set()
        for requested_name in requested_names or []:
            matching_ids = ids_by_name.get(requested_name, [])
            if len(matching_ids) > 1:
                raise ValueError(
                    f"Ambiguous channel group name {requested_name!r}; use a numeric group ID"
                )
            if matching_ids:
                resolved_ids.add(matching_ids[0])
            else:
                logger.warning(
                    "[STREAM-PROBE] Requested channel group not found: %s",
                    requested_name,
                )
        return frozenset(resolved_ids)

    async def _fetch_channel_stream_ids(
        self,
        channel_groups_override: list[str] | None = None,
        channel_group_ids_override: frozenset[int] | None = None,
    ) -> tuple[set, dict, dict]:
        """
        Fetch all unique stream IDs from channels (paginated).
        Only fetches from selected groups if channel_groups_override is set.
        Returns: (set of stream IDs, dict mapping stream_id -> list of channel names, dict mapping stream_id -> lowest channel number)

        Args:
            channel_groups_override: Optional list of channel group names to resolve.
            channel_group_ids_override: Invocation-local resolved numeric group IDs.
        """
        logger.debug(
            "[STREAM-PROBE] _fetch_channel_stream_ids called with names=%s, ids=%s",
            channel_groups_override,
            channel_group_ids_override,
        )

        channel_stream_ids = set()
        stream_to_channels = {}  # stream_id -> list of channel names
        stream_to_channel_number = {}  # stream_id -> lowest channel number (for sorting)

        selected_group_ids = (
            await self._resolve_channel_group_ids(channel_groups_override)
            if channel_groups_override is not None
            else channel_group_ids_override
        )
        if selected_group_ids == frozenset():
            return channel_stream_ids, stream_to_channels, stream_to_channel_number

        filter_by_group = selected_group_ids is not None

        page = 1
        total_channels_seen = 0
        channels_included = 0
        channels_excluded_wrong_group = 0
        channels_with_no_streams = 0
        excluded_channel_names = []  # Track names for debug logging

        while True:
            try:
                result = await self.client.get_channels(page=page, page_size=500)
            except Exception:
                logger.exception("[STREAM-PROBE] Failed to fetch channels page %s", page)
                raise
            channels = result.get("results", [])
            for channel in channels:
                total_channels_seen += 1
                channel_name = channel.get("name", f"Channel {channel.get('id', 'Unknown')}")
                channel_group_id = channel.get("channel_group_id")

                if filter_by_group and channel_group_id not in selected_group_ids:
                    channels_excluded_wrong_group += 1
                    excluded_channel_names.append(channel_name)
                    continue

                channel_number = channel.get("channel_number", 999999)
                stream_ids = channel.get("streams", [])

                if not stream_ids:
                    channels_with_no_streams += 1
                    logger.debug("[STREAM-PROBE] Channel '%s' has no streams, skipping", channel_name)
                    continue

                channels_included += 1
                channel_stream_ids.update(stream_ids)
                logger.debug("[STREAM-PROBE] Including channel '%s' with %s stream(s)", channel_name, len(stream_ids))

                for stream_id in stream_ids:
                    if stream_id not in stream_to_channels:
                        stream_to_channels[stream_id] = []
                    stream_to_channels[stream_id].append(channel_name)
                    if stream_id not in stream_to_channel_number or channel_number < stream_to_channel_number[stream_id]:
                        stream_to_channel_number[stream_id] = channel_number
            if not result.get("next"):
                break
            page += 1
            if page > 50:
                raise RuntimeError("Channel pagination exceeded the 50-page safety limit")

        # Log summary of channel filtering
        logger.debug("[STREAM-PROBE] Channel filtering summary:")
        logger.debug("[STREAM-PROBE]   Total channels seen: %s", total_channels_seen)
        logger.debug("[STREAM-PROBE]   Channels included: %s", channels_included)
        if filter_by_group:
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

        # User-supplied regex — run through safe_regex to cap ReDoS exposure.
        # safe_regex.sub returns the original text unchanged on timeout,
        # oversize pattern, or compile error, and emits a [SAFE_REGEX] WARN
        # log with a pattern sha256 + excerpt. That sentinel is the correct
        # fallback here: an unrewritten URL probes directly against the
        # source, which is safer than blocking the probe entirely.
        rewritten = safe_regex.sub(
            search_pattern,
            replace_pattern,
            original_url,
            diagnostic_mode="metadata_only",
        )
        if rewritten != original_url:
            # Rewrite patterns may themselves contain provider credentials.
            logger.debug("[STREAM-PROBE] Profile %s: rewrote URL", profile['id'])
        return rewritten

    async def _auto_reorder_channels(
        self,
        channel_groups_override: list[str] | None = None,
        stream_to_channels: dict = None,
        channel_group_ids_override: frozenset[int] | None = None,
    ) -> list[dict]:
        """
        Auto-reorder streams in all channels from the selected groups using smart sort.
        Returns a list of dicts with {channel_id, channel_name, stream_count} for channels that were reordered.
        """
        reordered = []

        selected_group_ids = (
            await self._resolve_channel_group_ids(channel_groups_override)
            if channel_groups_override is not None
            else channel_group_ids_override
        )
        if selected_group_ids == frozenset():
            return reordered

        sort_settings = self._sort_settings_snapshot()

        try:
            filter_by_group = selected_group_ids is not None
            logger.info("[STREAM-PROBE-SORT] selected_group_ids=%s", selected_group_ids)

            # Fetch all channels and filter by selected groups
            page = 1
            channels_to_reorder = []
            while True:
                try:
                    result = await self.client.get_channels(page=page, page_size=500)
                    channels = result.get("results", [])
                    for channel in channels:
                        # An explicitly configured selection must never widen to all groups.
                        channel_group_id = channel.get("channel_group_id")
                        if filter_by_group and channel_group_id not in selected_group_ids:
                            continue

                        # Add all channels - we'll check stream count later when we fetch full details
                        # The paginated list might not include full stream data
                        channels_to_reorder.append(channel)

                    if not result.get("next"):
                        break
                    page += 1
                    if page > 50:
                        raise RuntimeError(
                            "Channel pagination exceeded the 50-page safety limit"
                        )
                except Exception as e:
                    logger.error("[STREAM-PROBE] Failed to fetch channels page %s for auto-reorder: %s", page, e)
                    raise

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

                    metadata_criteria = self._stream_metadata_criteria_for(
                        sort_settings
                    )
                    streams_data = (
                        await self.client.get_streams_by_ids(stream_ids)
                        if metadata_criteria
                        else []
                    )
                    # Log raw stream data for debugging
                    for s in streams_data:
                        stream_id = s.get("id", s.get("stream_id"))
                        logger.debug(
                            "[STREAM-PROBE-SORT] Channel %s: Stream %s ('%s') has raw m3u_account=%r",
                            channel_id,
                            stream_id,
                            s.get("name", "Unknown"),
                            s.get("m3u_account"),
                        )
                    # Extract M3U account IDs (handles both direct ID and nested object formats)
                    # Dispatcharr may return either "id" or "stream_id" depending on version/endpoint.
                    # custom_streams criterion: collect operator-added custom stream IDs
                    # (Dispatcharr is_custom) from the same already-fetched stream data,
                    # only when the criterion is active (mirrors how m3u_priority gates).
                    custom_active = "custom_streams" in metadata_criteria
                    catchup_active = "catchup" in metadata_criteria
                    m3u_active = "m3u_priority" in metadata_criteria
                    stream_m3u_map = {}
                    custom_stream_ids: set[int] = set()
                    catchup_stream_ids: set[int] = set()
                    stream_metadata_known_ids: set[int] = set()
                    for s in streams_data:
                        stream_id = s.get("id", s.get("stream_id"))
                        if stream_id is None:
                            continue
                        stream_metadata_known_ids.add(int(stream_id))
                        if m3u_active:
                            stream_m3u_map[int(stream_id)] = self._extract_m3u_account_id(
                                s.get("m3u_account")
                            )
                        if custom_active and s.get("is_custom"):
                            custom_stream_ids.add(int(stream_id))
                        if catchup_active and s.get("is_catchup"):
                            catchup_stream_ids.add(int(stream_id))
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
                        sorted_stream_ids = smart_sort_streams(
                            stream_ids,
                            stats_map,
                            stream_m3u_map,
                            channel_name=channel_name,
                            custom_stream_ids=custom_stream_ids,
                            catchup_stream_ids=catchup_stream_ids,
                            stream_metadata_known_ids=stream_metadata_known_ids,
                            **sort_settings,
                        )
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

                            deprioritized = _priority_deprioritized_streams(
                                sorted_stream_ids,
                                stats_map,
                                sort_settings,
                                stream_m3u_map,
                                custom_stream_ids,
                                catchup_stream_ids,
                                stream_metadata_known_ids,
                            )

                            # Build journal description
                            desc_parts = [f"Smart sort reordered {len(stream_ids)} streams in '{channel_name}'"]
                            if deprioritized:
                                reasons = {}
                                for d in deprioritized:
                                    reasons.setdefault(d["reason"], []).append(d["name"])
                                reason_strs = []
                                for reason, names in reasons.items():
                                    label = health_deprioritization_label(reason)
                                    reason_strs.append(f"{len(names)} {label}")
                                desc_parts.append(f"({', '.join(reason_strs)} deprioritized)")

                            journal.log_entry(
                                category="channel",
                                action_type="smart_sort",
                                entity_id=channel_id,
                                entity_name=channel_name,
                                description=" ".join(desc_parts),
                                before_value={"streams": [s["name"] for s in streams_before]},
                                after_value={
                                    "streams": [s["name"] for s in streams_after],
                                    "deprioritized": deprioritized,
                                },
                            )

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
            raise

        return reordered

    async def _auto_reorder_channels_for_streams(self, probed_stream_ids: list[int]) -> list[dict]:
        """Reorder only channels that contain one of ``probed_stream_ids``.

        Used by the bulk-by-ID probe (probe_streams_by_ids) so auto-reorder stays
        scoped to channels the operator actually touched — matching the prior
        synchronous bulk endpoint's behavior — rather than reordering the whole
        lineup the way the group-scoped _auto_reorder_channels would.
        """
        reordered = []
        sort_settings = self._sort_settings_snapshot()
        probed_set = set(probed_stream_ids)

        # Fetch all channels and keep those containing a probed stream.
        all_channels = []
        page = 1
        while True:
            try:
                result = await self.client.get_channels(page=page, page_size=500)
                batch = result.get("results", [])
                if not batch:
                    break
                all_channels.extend(batch)
                if not result.get("next"):
                    break
                page += 1
                if page > 50:  # Safety limit
                    break
            except Exception as e:
                logger.error("[STREAM-PROBE-SORT] Failed to fetch channels page %s for bulk reorder: %s", page, e)
                break

        affected = [
            ch for ch in all_channels
            if any(sid in probed_set for sid in ch.get("streams", []))
        ]
        if not affected:
            return []

        # Build a stats map for every stream across the affected channels.
        all_stream_ids = list({sid for ch in affected for sid in ch.get("streams", [])})
        stats_map = {}
        with get_session() as session:
            for i in range(0, len(all_stream_ids), 500):
                chunk = all_stream_ids[i:i + 500]
                for stat in session.query(StreamStats).filter(StreamStats.stream_id.in_(chunk)).all():
                    stats_map[stat.stream_id] = stat

        # Build an M3U account map for m3u_priority sorting, and a custom-stream
        # ID set for the custom_streams criterion (only when that criterion is
        # active — mirrors the m3u_priority gating).
        metadata_criteria = self._stream_metadata_criteria_for(sort_settings)
        custom_active = "custom_streams" in metadata_criteria
        catchup_active = "catchup" in metadata_criteria
        m3u_active = "m3u_priority" in metadata_criteria
        stream_m3u_map = {}
        custom_stream_ids: set[int] = set()
        catchup_stream_ids: set[int] = set()
        stream_metadata_known_ids: set[int] = set()
        try:
            streams_data = (
                await self.client.get_streams_by_ids(all_stream_ids)
                if metadata_criteria
                else []
            )
            for s in streams_data:
                sid = s.get("id", s.get("stream_id"))
                if sid is not None:
                    stream_metadata_known_ids.add(int(sid))
                    if m3u_active:
                        stream_m3u_map[int(sid)] = self._extract_m3u_account_id(s.get("m3u_account"))
                    if custom_active and s.get("is_custom"):
                        custom_stream_ids.add(int(sid))
                    if catchup_active and s.get("is_catchup"):
                        catchup_stream_ids.add(int(sid))
        except Exception as e:
            logger.warning("[STREAM-PROBE-SORT] Failed to fetch M3U data for bulk reorder: %s", e)

        for ch in affected:
            channel_id = ch["id"]
            channel_name = ch.get("name", f"Channel {channel_id}")
            stream_ids = ch.get("streams", [])
            if len(stream_ids) < 2:
                continue
            sorted_ids = smart_sort_streams(
                stream_ids=stream_ids,
                stats_map=stats_map,
                stream_m3u_map=stream_m3u_map,
                channel_name=channel_name,
                custom_stream_ids=custom_stream_ids,
                catchup_stream_ids=catchup_stream_ids,
                stream_metadata_known_ids=stream_metadata_known_ids,
                **sort_settings,
            )
            if sorted_ids == stream_ids:
                continue
            try:
                await self.client.update_channel(channel_id, {"streams": sorted_ids})
            except Exception as e:
                logger.error("[STREAM-PROBE-SORT] Failed to update channel %s during bulk reorder: %s", channel_id, e)
                continue

            deprioritized = _priority_deprioritized_streams(
                sorted_ids,
                stats_map,
                sort_settings,
                stream_m3u_map,
                custom_stream_ids,
                catchup_stream_ids,
                stream_metadata_known_ids,
            )

            desc_parts = [f"Smart sort reordered {len(stream_ids)} streams in '{channel_name}'"]
            if deprioritized:
                reasons = {}
                for d in deprioritized:
                    reasons.setdefault(d["reason"], []).append(d["name"])
                reason_strs = []
                for reason, names in reasons.items():
                    label = health_deprioritization_label(reason)
                    reason_strs.append(f"{len(names)} {label}")
                desc_parts.append(f"({', '.join(reason_strs)} deprioritized)")

            journal.log_entry(
                category="channel",
                action_type="smart_sort",
                entity_id=channel_id,
                entity_name=channel_name,
                description=" ".join(desc_parts),
                after_value={"deprioritized": deprioritized} if deprioritized else None,
            )
            reordered.append({
                "channel_id": channel_id,
                "channel_name": channel_name,
                "stream_count": len(stream_ids),
            })

        return reordered

    def _smart_sort_streams(
        self,
        stream_ids: list[int],
        stats_map: dict,
        stream_m3u_map: dict[int, int] = None,
        channel_name: str = "unknown",
        custom_stream_ids: set[int] | None = None,
        catchup_stream_ids: set[int] | None = None,
        stream_metadata_known_ids: set[int] | None = None,
    ) -> list[int]:
        """Sort stream IDs using smart sort logic. Delegates to module-level function."""
        return smart_sort_streams(
            stream_ids,
            stats_map,
            stream_m3u_map=stream_m3u_map or {},
            channel_name=channel_name,
            custom_stream_ids=custom_stream_ids,
            catchup_stream_ids=catchup_stream_ids,
            stream_metadata_known_ids=stream_metadata_known_ids,
            **self._sort_settings_snapshot(),
        )

    async def probe_all_streams(
        self,
        channel_groups_override: list[str] | None = None,
        channel_group_ids_override: frozenset[int] | None = None,
        skip_m3u_refresh: bool = False,
        stream_ids_filter: list[int] = None,
        start_send_alerts: bool = True,
        completion_send_alerts: bool = True,
        allow_reorder_after_probe: bool = True,
    ):
        """Probe all streams that are in channels (runs in background).

        Uses parallel probing - streams from different M3U accounts (or same M3U with
        available capacity) are probed concurrently for faster completion.

        Args:
            channel_groups_override: Optional list of channel group names to filter by.
                                    None probes all groups; an empty list probes none.
            channel_group_ids_override: Invocation-local scheduled group IDs. These
                                        are validated once and never converted to names.
            skip_m3u_refresh: If True, skip M3U refresh even if configured.
                             Use this for on-demand probes from the UI.
            stream_ids_filter: Optional list of specific stream IDs to probe.
                              If provided, only these streams will be probed (useful for re-probing failed streams).
            start_send_alerts: Whether the info-level "probe started" notification
                               should dispatch an external alert. Callers pass the
                               gated ``send_alerts AND alert_on_info`` value so the
                               start alert respects the per-task config (GH #462).
            completion_send_alerts: Whether final probe results dispatch directly
                                    to external alerts. Scheduled tasks pass False
                                    because TaskEngine owns their gated completion.
            allow_reorder_after_probe: Whether this invocation may apply the global
                                       auto-reorder setting. False suppresses reorder
                                       without changing the shared setting.
        """
        logger.info("[STREAM-PROBE] probe_all_streams called with channel_groups_override=%s, channel_group_ids_override=%s, skip_m3u_refresh=%s, stream_ids_filter=%s", channel_groups_override, channel_group_ids_override, skip_m3u_refresh, len(stream_ids_filter) if stream_ids_filter else 0)
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
        self._probe_black_screen_streams = []
        self._probe_progress_black_screen_count = 0
        self._probe_low_fps_streams = []
        self._probe_progress_low_fps_count = 0
        self._account_ramp_state = {}  # Fresh ramp state for each probe run

        probed_count = 0
        start_time = datetime.utcnow()
        try:
            resolved_group_ids = await self._resolve_channel_group_ids(
                channel_groups_override,
                channel_group_ids_override,
            )
            if resolved_group_ids == frozenset():
                if stream_ids_filter is None:
                    self._last_probe_scope_kind = "scoped"
                    self._last_probe_channel_stream_ids = set()
                self._probe_progress_status = "completed"
                self._probe_progress_current_stream = ""
                self._save_probe_history(start_time, 0, reordered_channels=[])
                return {
                    "status": "completed",
                    "probed": 0,
                    "reordered_channels": 0,
                }

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
            logger.info("[STREAM-PROBE] Fetching channel stream IDs (resolved group IDs: %s)...", resolved_group_ids)
            channel_stream_ids, stream_to_channels, stream_to_channel_number = await self._fetch_channel_stream_ids(
                channel_group_ids_override=resolved_group_ids
            )
            logger.info("[STREAM-PROBE] Found %s unique streams across all channels", len(channel_stream_ids))

            if not channel_stream_ids:
                if stream_ids_filter is None:
                    self._last_probe_scope_kind = "all" if resolved_group_ids is None else "scoped"
                    self._last_probe_channel_stream_ids = set()
                self._probe_progress_status = "completed"
                self._probe_progress_current_stream = ""
                self._save_probe_history(start_time, 0, reordered_channels=[])
                return {
                    "status": "completed",
                    "probed": 0,
                    "reordered_channels": 0,
                }

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
            await self._create_probe_notification(len(streams_to_probe), send_alerts=start_send_alerts)

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

                    # Log probe decisions without provider URLs, which may embed
                    # account identifiers or credentials.
                    if selected_profile:
                        logger.debug("[STREAM-PROBE] Stream %s (%s): "
                                     "strategy=%s, "
                                     "profile=%s ('%s')",
                                     stream_id, stream_name,
                                     self.profile_distribution_strategy,
                                     selected_profile['id'], selected_profile.get('name', 'unnamed'))
                    else:
                        logger.debug("[STREAM-PROBE] Stream %s (%s): "
                                     "no profile (direct provider connection)",
                                     stream_id, stream_name)

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
                                if result.get("is_black_screen", False):
                                    stream_info["is_black_screen"] = True
                                if result.get("is_low_fps", False):
                                    stream_info["is_low_fps"] = True

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
                                        if stream_info.get("is_black_screen"):
                                            self._probe_progress_black_screen_count += 1
                                            self._probe_black_screen_streams.append(stream_info)
                                        if stream_info.get("is_low_fps"):
                                            self._probe_progress_low_fps_count += 1
                                            self._probe_low_fps_streams.append(stream_info)
                                    else:
                                        self._probe_progress_failed_count += 1
                                        self._probe_failed_streams.append(stream_info)
                                probed_count += 1
                                self._probe_progress_current = probed_count
                                await self._update_probe_notification()
                            except asyncio.CancelledError:
                                logger.debug("[STREAM-PROBE] Probe task cancelled")
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
                        if result.get("is_black_screen", False):
                            self._probe_progress_black_screen_count += 1
                            self._probe_black_screen_streams.append(stream_info)
                        if result.get("is_low_fps", False):
                            self._probe_progress_low_fps_count += 1
                            self._probe_low_fps_streams.append(stream_info)
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
            logger.info(
                "[STREAM-PROBE-SORT] Checking auto_reorder_after_probe=%s, allow_reorder_after_probe=%s",
                self.auto_reorder_after_probe,
                allow_reorder_after_probe,
            )
            if self.auto_reorder_after_probe and allow_reorder_after_probe:
                logger.info("[STREAM-PROBE] Auto-reorder is enabled, reordering streams in probed channels...")
                self._probe_progress_status = "reordering"
                self._probe_progress_current_stream = "Reordering streams..."
                try:
                    reordered_channels = await self._auto_reorder_channels(
                        stream_to_channels=stream_to_channels,
                        channel_group_ids_override=resolved_group_ids,
                    )
                    logger.info("[STREAM-PROBE-SORT] Auto-reordered %s channels", len(reordered_channels))
                except Exception as e:
                    logger.error("[STREAM-PROBE] Auto-reorder failed: %s", e)
                    raise

            # Publish reprobe scope only after the complete scheduled run succeeds.
            if stream_ids_filter is None:
                self._last_probe_scope_kind = "all" if resolved_group_ids is None else "scoped"
                self._last_probe_channel_stream_ids = set(channel_stream_ids)
                logger.info("[STREAM-PROBE] Saved %s channel stream IDs for reprobe scoping", len(self._last_probe_channel_stream_ids))

            # Save to probe history
            self._save_probe_history(start_time, probed_count, reordered_channels=reordered_channels)

            # Finalize notification with success/warning status
            await self._finalize_probe_notification(send_alerts=completion_send_alerts)

            return {"status": "completed", "probed": probed_count, "reordered_channels": len(reordered_channels)}
        except Exception as e:
            logger.exception("[STREAM-PROBE] Probe all streams failed: %s", e)
            self._probe_progress_status = "failed"
            self._probe_progress_current_stream = ""

            # Save failed run to history
            self._save_probe_history(start_time, probed_count, error=str(e))

            # Finalize notification with error status
            await self._finalize_probe_notification(send_alerts=completion_send_alerts)

            return {"status": "failed", "error": str(e), "probed": probed_count}
        finally:
            self._probing_in_progress = False

    async def probe_streams_by_ids(
        self,
        stream_ids: list[int],
        start_send_alerts: bool = True,
        allow_reorder_after_probe: bool = True,
    ):
        """Probe a specific list of stream IDs in the background (on-demand bulk probe).

        This is the async backing for POST /api/stream-stats/probe/bulk. It reuses
        the SAME progress / results / history envelope as ``probe_all_streams`` —
        the very state that ``get_probe_progress``, ``get_probe_results`` and
        ``get_probe_history`` read — so a manual bulk probe shows up there exactly
        like a scheduled probe-all run (fixing the results-envelope half of
        enhancedchannelmanager-znc76.5).

        Unlike ``probe_all_streams`` it is NOT scoped to streams-in-channels: any
        stream ID that exists in Dispatcharr is probed. It also skips the M3U
        refresh and channel-capacity gating that the channel-scoped probe-all does
        (those are meaningful for "probe everything in my lineup", not for an
        explicit, operator-chosen list). Concurrency is bounded by the same
        ``max_concurrent_probes`` semaphore probe-all uses.

        Args:
            stream_ids: Specific stream IDs to probe.
            start_send_alerts: Whether the info-level "probe started" notification
                should dispatch an external alert (gated value — see
                ``probe_all_streams``; GH #462).
            allow_reorder_after_probe: Whether this invocation may apply the global
                auto-reorder setting. False leaves channel order unchanged.

        Returns:
            Dict envelope: {status, probed, total, success, failed} or
            {status: "already_running"} if a probe is in progress.
        """
        logger.info("[STREAM-PROBE] probe_streams_by_ids called with %s stream IDs", len(stream_ids))

        if self._probing_in_progress:
            logger.warning("[STREAM-PROBE] Probe already in progress — refusing bulk probe")
            return {"status": "already_running"}

        # Reset the shared probe envelope (mirrors probe_all_streams setup) so
        # get_probe_progress / get_probe_results report THIS bulk run.
        self._probing_in_progress = True
        self._probe_cancelled = False
        self._probe_paused = False
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
        self._probe_black_screen_streams = []
        self._probe_progress_black_screen_count = 0
        self._probe_low_fps_streams = []
        self._probe_progress_low_fps_count = 0
        self._account_ramp_state = {}

        probed_count = 0
        start_time = datetime.utcnow()
        try:
            # Fetch all streams once and select the requested IDs (preserving
            # request order). Streams not found in Dispatcharr are recorded as
            # failures so the tallies stay honest.
            all_streams = await self._fetch_all_streams()
            stream_map = {s["id"]: s for s in all_streams}
            streams_to_probe = []
            for sid in stream_ids:
                stream = stream_map.get(sid)
                if stream:
                    streams_to_probe.append(stream)
                else:
                    logger.warning("[STREAM-PROBE] Bulk probe: stream %s not found in Dispatcharr", sid)
                    self._probe_progress_failed_count += 1
                    self._probe_failed_streams.append(
                        {"id": sid, "name": f"Stream {sid}", "url": "", "error": "Stream not found"}
                    )
                    probed_count += 1

            self._probe_progress_total = len(stream_ids)
            self._probe_progress_current = probed_count
            self._probe_progress_status = "probing"

            await self._create_probe_notification(len(stream_ids), send_alerts=start_send_alerts)

            # Bound concurrency with the same global semaphore probe-all uses.
            semaphore = asyncio.Semaphore(self.max_concurrent_probes)
            results_lock = asyncio.Lock()

            async def _probe_one(stream: dict):
                nonlocal probed_count
                stream_id = stream["id"]
                stream_name = stream.get("name", f"Stream {stream_id}")
                stream_url = stream.get("url", "")
                m3u_account_id = self._extract_m3u_account_id(stream.get("m3u_account"))
                async with semaphore:
                    if self._probe_cancelled:
                        return
                    self._probe_progress_current_stream = stream_name
                    result = await self.probe_stream(stream_id, stream_url, stream_name)
                    probe_status = result.get("probe_status", "failed")
                    error_message = result.get("error_message", "")
                    stream_info = {"id": stream_id, "name": stream_name, "url": stream_url}
                    async with results_lock:
                        if probe_status == "success":
                            self._probe_progress_success_count += 1
                            self._probe_success_streams.append(stream_info)
                            if result.get("is_black_screen", False):
                                self._probe_progress_black_screen_count += 1
                                self._probe_black_screen_streams.append(stream_info)
                            if result.get("is_low_fps", False):
                                self._probe_progress_low_fps_count += 1
                                self._probe_low_fps_streams.append(stream_info)
                        else:
                            self._probe_progress_failed_count += 1
                            stream_info["error"] = error_message or "Unknown error"
                            self._probe_failed_streams.append(stream_info)
                        probed_count += 1
                        self._probe_progress_current = probed_count
                    await self._update_probe_notification()

            if streams_to_probe and not self._probe_cancelled:
                tasks = {asyncio.create_task(_probe_one(s)) for s in streams_to_probe}
                self._bulk_probe_tasks.update(tasks)
                try:
                    await asyncio.gather(*tasks)
                except asyncio.CancelledError:
                    if not self._probe_cancelled:
                        raise
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    self._bulk_probe_tasks.difference_update(tasks)

            if self._probe_cancelled:
                self._probe_progress_status = "cancelled"
            else:
                self._probe_progress_status = "completed"
            self._probe_progress_current_stream = ""

            logger.info(
                "[STREAM-PROBE] Bulk probe completed: %s/%s probed (%s success, %s failed)",
                probed_count, len(stream_ids),
                self._probe_progress_success_count, self._probe_progress_failed_count,
            )

            # Honor auto_reorder_after_probe (opt-in). Unlike probe-all (which can
            # reorder a whole group), a bulk-by-ID probe only reorders channels
            # that actually contain a probed stream — matching the prior bulk
            # endpoint's scope so opted-in users don't lose that behavior.
            reordered_channels = []
            if (
                self.auto_reorder_after_probe
                and allow_reorder_after_probe
                and not self._probe_cancelled
            ):
                self._probe_progress_status = "reordering"
                self._probe_progress_current_stream = "Reordering streams..."
                try:
                    reordered_channels = await self._auto_reorder_channels_for_streams(stream_ids)
                    logger.info("[STREAM-PROBE-SORT] Bulk probe auto-reordered %s channels", len(reordered_channels))
                except Exception as e:
                    logger.error("[STREAM-PROBE] Bulk probe auto-reorder failed: %s", e)
                self._probe_progress_status = "completed"
                self._probe_progress_current_stream = ""

            # Reuse the shared history + notification finalization path.
            self._save_probe_history(start_time, probed_count, reordered_channels=reordered_channels)
            await self._finalize_probe_notification()

            return {
                "status": "cancelled" if self._probe_cancelled else "completed",
                "probed": probed_count,
                "total": len(stream_ids),
                "success": self._probe_progress_success_count,
                "failed": self._probe_progress_failed_count,
            }
        except Exception as e:
            logger.exception("[STREAM-PROBE] Bulk probe failed: %s", e)
            self._probe_progress_status = "failed"
            self._probe_progress_current_stream = ""
            self._save_probe_history(start_time, probed_count, error=str(e))
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
            "black_screen_count": self._probe_progress_black_screen_count,
            "low_fps_count": self._probe_progress_low_fps_count,
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

    def _failure_breakdown(self) -> list:
        """Group the last run's failures by cause, most common first.

        Bead enhancedchannelmanager-3dn59. The operator previously saw only a
        failure COUNT, so "every probe against this provider is being refused by
        our own SSRF guard" was indistinguishable from scattered provider
        errors -- which is what turned a one-line answer into an incident.

        Reasons are the per-stream ``error`` already stored on
        ``_probe_failed_streams``: a fixed operator-safe string chosen in
        ``probe_stream`` (see :data:`OPERATOR_SAFE_EXCEPTION_TYPES`), never
        subprocess text.

        Returns:
            A list of ``{"reason": str, "count": int}`` sorted by descending
            count then reason, so the ordering is deterministic.
        """
        counts: dict = {}
        for info in self._probe_failed_streams:
            reason = (info.get("error") or "").strip() or "Unknown error"
            counts[reason] = counts.get(reason, 0) + 1
        return [
            {"reason": reason, "count": count}
            for reason, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    def get_probe_results(self) -> dict:
        """Get detailed results of the last probe all streams operation."""
        return {
            "success_streams": self._probe_success_streams,
            "failed_streams": self._probe_failed_streams,
            "skipped_streams": self._probe_skipped_streams,
            "black_screen_streams": self._probe_black_screen_streams,
            "low_fps_streams": self._probe_low_fps_streams,
            "success_count": len(self._probe_success_streams),
            "failed_count": len(self._probe_failed_streams),
            "skipped_count": len(self._probe_skipped_streams),
            "black_screen_count": len(self._probe_black_screen_streams),
            "low_fps_count": len(self._probe_low_fps_streams),
            # Cause breakdown, not just a count (bead
            # enhancedchannelmanager-3dn59).
            "failure_breakdown": self._failure_breakdown(),
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
            # Why the failures happened, not just how many (bead
            # enhancedchannelmanager-3dn59).
            "failure_breakdown": self._failure_breakdown(),
            "skipped_streams": list(self._probe_skipped_streams),  # Copy the list
            "black_screen_count": self._probe_progress_black_screen_count,
            "black_screen_streams": list(self._probe_black_screen_streams),  # Copy the list
            "low_fps_count": self._probe_progress_low_fps_count,
            "low_fps_streams": list(self._probe_low_fps_streams),  # Copy the list
            "reordered_channels": reordered_channels or [],  # List of channels that were reordered
            # Include sort configuration used for this run (for UI display)
            "sort_config": {
                "priority": list(self.stream_sort_priority),
                "enabled": dict(self.stream_sort_enabled),
                "deprioritize_failed": self.deprioritize_failed_streams,
                "deprioritize_black_screen": self.deprioritize_black_screen,
                "deprioritize_low_fps": self.deprioritize_low_fps,
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
        # Top causes only. A guard message can name a host, so a run spread over
        # many hosts could otherwise emit one line per host; the breakdown is
        # complete in the run report either way.
        breakdown = history_entry["failure_breakdown"]
        for entry in breakdown[:10]:
            logger.info(
                "[STREAM-PROBE] Failure cause: %s x%s", entry["reason"], entry["count"]
            )
        if len(breakdown) > 10:
            logger.info(
                "[STREAM-PROBE] ... and %s further failure cause(s); see the probe "
                "run report for the full breakdown", len(breakdown) - 10
            )

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


def ensure_prober() -> Optional[StreamProber]:
    """Get the global prober, creating one if it doesn't exist and settings are configured.

    This provides self-healing if the prober was never created at startup
    (e.g., settings not yet configured) or was lost during a failed restart.
    """
    global _prober
    if _prober is not None:
        return _prober

    try:
        from config import get_settings, stream_sort_point_rules_for_evaluator
        from dispatcharr_client import get_client

        settings = get_settings()
        if not settings.is_configured():
            logger.debug("[STREAM-PROBE] Cannot create prober - settings not configured")
            return None

        logger.info("[STREAM-PROBE] Prober not found, creating new instance...")
        prober = StreamProber(
            get_client(),
            probe_timeout=settings.stream_probe_timeout,
            use_resdet_for_resolution=settings.use_resdet_for_resolution,
            user_timezone=settings.user_timezone,
            bitrate_sample_duration=settings.bitrate_sample_duration,
            parallel_probing_enabled=settings.parallel_probing_enabled,
            max_concurrent_probes=settings.max_concurrent_probes,
            profile_distribution_strategy=settings.profile_distribution_strategy,
            skip_recently_probed_hours=settings.skip_recently_probed_hours,
            refresh_m3us_before_probe=settings.refresh_m3us_before_probe,
            auto_reorder_after_probe=settings.auto_reorder_after_probe,
            probe_retry_count=settings.probe_retry_count,
            probe_retry_delay=settings.probe_retry_delay,
            deprioritize_failed_streams=settings.deprioritize_failed_streams,
            deprioritize_black_screen=settings.deprioritize_black_screen,
            deprioritize_low_fps=settings.deprioritize_low_fps,
            black_screen_detection_enabled=settings.black_screen_detection_enabled,
            black_screen_sample_duration=settings.black_screen_sample_duration,
            low_fps_threshold=settings.low_fps_threshold,
            stream_sort_priority=settings.stream_sort_priority,
            stream_sort_enabled=settings.stream_sort_enabled,
            stream_fetch_page_limit=settings.stream_fetch_page_limit,
            m3u_account_priorities=settings.m3u_account_priorities,
            failed_stream_sort_order=settings.failed_stream_sort_order,
            stream_sort_strategy=settings.stream_sort_strategy,
            stream_sort_point_rules=stream_sort_point_rules_for_evaluator(settings),
        )
        _prober = prober
        logger.info("[STREAM-PROBE] Auto-created prober instance")

        # Wire up notification callbacks if available
        try:
            from services.notification_service import (
                create_notification_internal,
                update_notification_internal,
                delete_notifications_by_source_internal,
            )
            prober.set_notification_callbacks(
                create_callback=create_notification_internal,
                update_callback=update_notification_internal,
                delete_by_source_callback=delete_notifications_by_source_internal,
            )
        except Exception as e:
            logger.warning("[STREAM-PROBE] Could not set notification callbacks: %s", e)

        return _prober
    except Exception as e:
        logger.error("[STREAM-PROBE] Failed to auto-create prober: %s", e)
        return None
