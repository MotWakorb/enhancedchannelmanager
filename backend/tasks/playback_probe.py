"""
Playback Probe Task.

Plays the lead stream of the most-watched channels for several minutes and
counts in-place reconnects and failed starts. A short probe only shows that a
source starts; this task shows whether it keeps playing cleanly.

A reconnect is an in-place reconnect mid-stream: an ffmpeg ``Will reconnect
at`` line with a non-zero byte offset. A start that ffmpeg could not get going
(nonzero exit without such a reconnect, or only offset-0 reconnect lines)
counts as a failed start. Timestamp discontinuities are recorded for
information only: ffmpeg prints one per track per event and also prints them
during failed starts.

One stream is sampled at a time. A sample starts only when the channel is not
playing and its M3U account has a free login in the same fresh channel status
read; when that status or the M3U accounts cannot be read, nothing is sampled.
While a sample runs, the task re-reads the channel status every
``VIEWER_POLL_SECONDS`` and aborts the sample within one status poll (about
5 s) when a viewer starts on the same account; it narrows, not closes, the
race in #1032.

A stream without a stats row from an earlier metadata probe is skipped; this
task never creates one.

Pause takes effect between streams: a running sample is not interrupted, and
the next one starts after resume.
"""
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import httpx

from bandwidth_tracker import BandwidthTracker
from database import get_session
from models import StreamStats
from security.stream_outbound import validated_subprocess_input
from stream_prober import (
    FFPROBE_PROTOCOL_WHITELIST,
    PROBE_SCHEME_DOWNGRADE,
    RELAY_PROTOCOL_WHITELIST,
    extract_m3u_account_id,
    operator_safe_detail,
)
from stream_user_agent import StreamUserAgentResolver
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)

# How often a running sample re-reads the channel status for a new viewer.
VIEWER_POLL_SECONDS = 5
# Headroom over the sample length for relay startup and ffmpeg buffering. A
# clean exit more than this short of the sample length counts as ended early.
SAMPLE_DEADLINE_GRACE_SECONDS = 30
# Number of complete samples kept per stream; the sum columns cover this window.
WINDOW_SIZE = 3

DISCONTINUITY_MARKER = "timestamp discontinuity"
RECONNECT_RE = re.compile(r"Will reconnect at (\d+) in")
REDACTED_URL = "[REDACTED stream URL]"


def parse_ffmpeg_stderr(text: str) -> tuple[int, int, int]:
    """Return (discontinuities, reconnects, start_retries) from ffmpeg stderr lines.

    ``reconnects`` counts ``Will reconnect at`` lines with a non-zero byte
    offset, one per in-place reconnect. Offset 0 means ffmpeg retried a start
    that delivered nothing; those lines count as ``start_retries``.
    """
    discontinuities = 0
    reconnects = 0
    start_retries = 0
    for line in text.splitlines():
        if DISCONTINUITY_MARKER in line:
            discontinuities += 1
        match = RECONNECT_RE.search(line)
        if match:
            if int(match.group(1)):
                reconnects += 1
            else:
                start_retries += 1
    return discontinuities, reconnects, start_retries


def _last_stderr_line(text: str, *urls: str) -> Optional[str]:
    """Last non-empty stderr line with stream URLs redacted, or None."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    line = lines[-1]
    for url in urls:
        if url:
            line = line.replace(url, REDACTED_URL)
    return "".join(char if char.isprintable() else " " for char in line).strip()[:300]


def _deadline_detail(sample_seconds: int) -> str:
    return f"ffmpeg did not exit within {sample_seconds + SAMPLE_DEADLINE_GRACE_SECONDS}s"


def _clamp(value: Optional[int], default: int, low: int, high: int) -> int:
    if value is None:
        return default
    return max(low, min(high, int(value)))


@dataclass
class PlaybackSample:
    """Outcome of one sample.

    ``outcome`` is one of:

    - ``complete``: ffmpeg exited 0 after playing about ``-t`` seconds.
    - ``failed_start``: ffmpeg exited nonzero without an in-place reconnect,
      or its only reconnect lines had offset 0, also when it was still
      retrying at the deadline (``timed_out``).
    - ``failed``: ffmpeg exited nonzero after an in-place reconnect.
    - ``ended_early``: ffmpeg exited 0 well before ``-t``.
    - ``viewer``: a channel on the same account started.
    - ``status_unavailable``: the channel status could not be re-read.
    - ``cancelled``, or ``deadline`` when ffmpeg did not exit within the
      wall-clock budget.

    Only complete samples are stored; a failed start increments the stream's
    start-failure count.
    """

    outcome: str
    seconds: int
    discontinuities: int
    reconnects: int
    drop_reason: Optional[str] = None
    stderr_line: Optional[str] = None
    timed_out: bool = False


@dataclass
class _WorkItem:
    channel_uuid: str
    channel_name: str
    stream: dict


@register_task
class PlaybackProbeTask(TaskScheduler):
    """Sample playback stability of the top watched channels' streams."""

    task_id = "playback_probe"
    task_name = "Playback Probe"
    task_description = "Play top channels' streams for several minutes and count reconnects and failed starts"

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.MANUAL,
                schedule_time="04:00",
            )
        super().__init__(schedule_config)

        self._prober = None
        self._top_channels: Optional[int] = None
        self._sample_minutes: Optional[int] = None
        self._playback_retest_days: Optional[int] = None

    def get_config(self) -> dict:
        return {
            "top_channels": self._top_channels,
            "sample_minutes": self._sample_minutes,
            "playback_retest_days": self._playback_retest_days,
        }

    def update_config(self, config: dict) -> None:
        if "top_channels" in config:
            self._top_channels = config["top_channels"]
        if "sample_minutes" in config:
            self._sample_minutes = config["sample_minutes"]
        if "playback_retest_days" in config:
            self._playback_retest_days = config["playback_retest_days"]
        logger.info("[%s] Config updated: top_channels=%s, sample_minutes=%s, playback_retest_days=%s",
                    self.task_id, self._top_channels, self._sample_minutes, self._playback_retest_days)

    def set_prober(self, prober):
        """Set the StreamProber instance that owns the probe guard and login maps."""
        self._prober = prober
        logger.info("[%s] Prober set", self.task_id)

    async def validate_config(self) -> tuple[bool, str]:
        if self._prober is None:
            return False, "StreamProber not initialized"
        return True, ""

    def _stop_requested(self, prober, guard: object) -> bool:
        return self._cancel_requested or prober.probe_guard_cancelled(guard)

    async def _wait_while_paused(self, prober, guard: object) -> None:
        while prober._probe_paused and not self._stop_requested(prober, guard):
            await asyncio.sleep(1)

    async def execute(self) -> TaskResult:
        """Execute the playback probe.

        The prober is captured once, so a settings restart that installs a new
        one with ``set_prober`` cannot move the guard or the cancel flag mid-run.
        """
        started_at = datetime.utcnow()
        prober = self._prober

        if prober is None:
            return TaskResult(
                success=False,
                message="StreamProber not initialized",
                error="NOT_INITIALIZED",
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        guard = prober.try_acquire_probe_guard()
        if guard is None:
            return TaskResult(
                success=False,
                message="A probe is already in progress",
                error="ALREADY_RUNNING",
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        try:
            return await self._run(prober, guard, started_at)
        finally:
            prober.release_probe_guard(guard)

    async def _run(self, prober, guard: object, started_at: datetime) -> TaskResult:
        top_channels = _clamp(self._top_channels, 10, 1, 100)
        sample_seconds = _clamp(self._sample_minutes, 5, 1, 30) * 60
        retest_days = _clamp(self._playback_retest_days, 7, 1, 90)

        self._set_progress(status="starting", current_item="Finding top watched channels...")
        work_items = await self._build_work_items(prober, top_channels, retest_days)
        m3u_accounts_map, m3u_max_streams = await prober._load_m3u_account_profiles()
        if not m3u_max_streams:
            # The loader swallows fetch errors; without the account caps no
            # free login can be proven, so nothing is sampled.
            return TaskResult(
                success=False,
                message="M3U accounts unavailable; no stream was sampled",
                error="M3U_ACCOUNTS_UNAVAILABLE",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=len(work_items),
            )

        total = len(work_items)
        logger.info("[%s] Sampling %d streams for %ss each", self.task_id, total, sample_seconds)
        self._set_progress(status="sampling", total=total, current=0,
                           current_item=f"Sampling {total} streams...")

        sampled = []
        incomplete = []
        skipped = []
        errors = []

        for index, item in enumerate(work_items):
            await self._wait_while_paused(prober, guard)
            if self._stop_requested(prober, guard):
                break
            stream = item.stream
            stream_id = stream["id"]
            name = stream.get("name") or f"Stream {stream_id}"
            self._set_progress(current=index, current_item=f"{item.channel_name}: {name}",
                               success_count=len(sampled), failed_count=len(errors),
                               skipped_count=len(skipped) + len(incomplete))

            try:
                skip_reason, profile, account_id, baseline = await self._check_login(
                    prober, item, m3u_accounts_map, m3u_max_streams
                )
                if not skip_reason and not self._has_stats(stream_id):
                    skip_reason = "no probe stats yet"
                if skip_reason:
                    logger.info("[%s] Skipping stream %s (%s): %s", self.task_id, stream_id, name, skip_reason)
                    skipped.append({"id": stream_id, "name": name, "reason": skip_reason})
                    continue

                url = stream.get("url")
                if profile:
                    url = prober._rewrite_url_for_profile(url, profile)

                user_agent = await StreamUserAgentResolver(prober.client).resolve(stream, stream_id=stream_id)
                sample = await self._sample_stream(
                    prober, url, guard=guard, user_agent=user_agent,
                    sample_seconds=sample_seconds, account_id=account_id, baseline=baseline,
                )
                if sample.outcome == "complete":
                    self._record_sample(stream_id, sample)
                elif sample.outcome == "failed_start":
                    self._record_start_failure(stream_id)
            except Exception as e:
                detail = operator_safe_detail(e) or "Playback sample failed"
                logger.warning("[%s] Playback sample failed for stream %s: %s", self.task_id, stream_id, detail)
                errors.append({"id": stream_id, "name": name, "error": detail})
                continue

            if sample.outcome == "complete":
                sampled.append({
                    "id": stream_id,
                    "name": name,
                    "channel": item.channel_name,
                    "seconds": sample.seconds,
                    "discontinuities": sample.discontinuities,
                    "reconnects": sample.reconnects,
                    "drop_reason": sample.drop_reason,
                })
            elif sample.outcome == "deadline":
                errors.append({"id": stream_id, "name": name, "error": _deadline_detail(sample_seconds)})
            elif sample.outcome in ("failed_start", "failed", "ended_early"):
                detail = sample.stderr_line or "no ffmpeg output"
                if sample.timed_out:
                    detail = f"{_deadline_detail(sample_seconds)}; {detail}"
                errors.append({"id": stream_id, "name": name, "error": f"{sample.outcome}: {detail}"})
            else:
                incomplete.append({"id": stream_id, "name": name, "reason": sample.outcome})

        cancelled = self._stop_requested(prober, guard)
        self._set_progress(
            status="cancelled" if cancelled else "completed",
            current=len(sampled) + len(incomplete) + len(skipped) + len(errors),
            total=total,
            success_count=len(sampled),
            failed_count=len(errors),
            skipped_count=len(skipped) + len(incomplete),
        )

        details = {
            "sampled_streams": sampled[:50],
            "incomplete_streams": incomplete[:50],
            "skipped_streams": skipped[:50],
            "error_streams": errors[:50],
            "sample_seconds": sample_seconds,
        }
        result = dict(
            started_at=started_at,
            completed_at=datetime.utcnow(),
            total_items=total,
            success_count=len(sampled),
            failed_count=len(errors),
            skipped_count=len(skipped) + len(incomplete),
            details=details,
        )
        if cancelled:
            return TaskResult(success=False, message="Playback probe cancelled", error="CANCELLED", **result)
        return TaskResult(
            success=True,
            message=(f"Sampled {len(sampled)} of {total} streams: {len(incomplete)} incomplete, "
                     f"{len(skipped)} skipped, {len(errors)} errors"),
            **result,
        )

    async def _all_channels(self, prober) -> list[dict]:
        channels: list[dict] = []
        page = 1
        while True:
            response = await prober.client.get_channels(page=page, page_size=500)
            channels.extend(response.get("results", []))
            if not response.get("next"):
                return channels
            page += 1
            if page > 50:
                raise RuntimeError("Channel pagination exceeded the 50-page safety limit")

    async def _build_work_items(self, prober, top_channels: int, retest_days: int) -> list[_WorkItem]:
        """Lead stream of each top channel, plus streams due for a retest.

        A non-lead stream is retested when it already has a playback sample
        older than ``retest_days``, so a source that lost the lead position
        still gets a fresh measurement.
        """
        top = BandwidthTracker.get_top_watched_channels(limit=top_channels, sort_by="time")
        if not top:
            return []
        channels_by_uuid = {
            str(ch["uuid"]): ch for ch in await self._all_channels(prober) if ch.get("uuid")
        }
        selected = [channels_by_uuid[str(t["channel_id"])] for t in top
                    if str(t.get("channel_id")) in channels_by_uuid]

        candidate_ids = {sid for ch in selected for sid in (ch.get("streams") or [])[1:]}
        cutoff = datetime.utcnow() - timedelta(days=retest_days)
        session = get_session()
        try:
            due_ids = {
                row.stream_id for row in session.query(StreamStats).filter(
                    StreamStats.stream_id.in_(candidate_ids),
                    StreamStats.playback_samples.isnot(None),
                    StreamStats.last_playback_probed < cutoff,
                ).all()
            } if candidate_ids else set()
        finally:
            session.close()

        items: list[_WorkItem] = []
        seen: set[int] = set()
        for channel in selected:
            stream_ids = channel.get("streams") or []
            wanted = [sid for i, sid in enumerate(stream_ids)
                      if (i == 0 or sid in due_ids) and sid not in seen]
            if not wanted:
                continue
            try:
                streams = {s["id"]: s for s in await prober.client.get_channel_streams(channel["id"])}
            except Exception as e:
                logger.warning("[%s] Failed to fetch streams for channel %s: %s",
                               self.task_id, channel.get("id"), e)
                continue
            for sid in wanted:
                stream = streams.get(sid)
                if stream and stream.get("url"):
                    seen.add(sid)
                    items.append(_WorkItem(
                        channel_uuid=str(channel["uuid"]),
                        channel_name=channel.get("name") or f"Channel {channel['id']}",
                        stream=stream,
                    ))
        return items

    @staticmethod
    def _account_channel_ids(prober, channels: list[dict], account_id: int) -> set[str]:
        """Ids of the playing channels whose connection belongs to ``account_id``."""
        account_map = prober._profile_to_account_map
        return {
            str(ch.get("channel_id")) for ch in channels
            if ch.get("m3u_profile_id")
            and account_map.get(ch["m3u_profile_id"], ch["m3u_profile_id"]) == account_id
        }

    async def _check_login(self, prober, item: _WorkItem, m3u_accounts_map: dict,
                           m3u_max_streams: dict) -> tuple[Optional[str], Optional[dict], Optional[int], set[str]]:
        """Return (skip_reason, profile, account_id, account channel ids) for one item.

        The playing check and the login counts come from one fresh channel
        status read, and every gap fails closed: an unreadable status or an
        unknown M3U account skips the item rather than risk a viewer's login.
        """
        try:
            status = await prober.client.get_channel_stats()
        except Exception as e:
            logger.warning("[%s] Failed to read channel status: %s", self.task_id, e)
            return "channel status unavailable", None, None, set()
        channels = status.get("channels", [])
        if item.channel_uuid in {str(ch.get("channel_id")) for ch in channels}:
            return "channel is playing", None, None, set()

        account_id = extract_m3u_account_id(item.stream.get("m3u_account"))
        if not account_id or account_id not in m3u_max_streams:
            return "M3U account unknown", None, None, set()

        profile_conns: dict[int, int] = {}
        for ch in channels:
            profile_id = ch.get("m3u_profile_id")
            if profile_id:
                profile_conns[profile_id] = profile_conns.get(profile_id, 0) + 1
        account_channels = self._account_channel_ids(prober, channels, account_id)
        account_conns = len(account_channels)
        max_streams = m3u_max_streams[account_id]

        if prober._account_profiles.get(account_id):
            profile = prober._select_probe_profile(
                account_id, profile_conns, {}, max_streams, account_conns
            )
            if profile:
                return None, profile, account_id, account_channels
        elif max_streams <= 0 or account_conns < max_streams:
            return None, None, account_id, account_channels

        m3u_name = m3u_accounts_map.get(account_id, f"M3U {account_id}")
        if max_streams > 0:
            reason = f"M3U '{m3u_name}' at max connections ({account_conns}/{max_streams})"
        else:
            reason = f"M3U '{m3u_name}' has no profile with a free connection"
        return reason, None, account_id, account_channels

    async def _poll_viewer(self, prober, account_id: int, baseline: set[str]) -> Optional[str]:
        """Return ``viewer`` or ``status_unavailable`` to abort the sample, else None.

        Compares channel ids rather than counts, so a viewer that replaces
        another between two polls is still seen.
        """
        try:
            status = await prober.client.get_channel_stats()
        except Exception as e:
            logger.warning("[%s] Failed to read channel status during a sample: %s", self.task_id, e)
            return "status_unavailable"
        if self._account_channel_ids(prober, status.get("channels", []), account_id) - baseline:
            return "viewer"
        return None

    async def _sample_stream(self, prober, url: str, *, guard: object, user_agent: str,
                             sample_seconds: int, account_id: int, baseline: set[str]) -> PlaybackSample:
        """Play ``url`` for ``sample_seconds`` through the SSRF relay.

        The relay does not reconnect upstream; ffmpeg's ``-reconnect`` asks
        the relay again, so each reconnect line is one upstream drop, and
        ``-reconnect_at_eof`` makes a clean upstream close a reconnect too.
        The relay's recorded failure names the cause of the last drop.
        """
        deadline = sample_seconds + SAMPLE_DEADLINE_GRACE_SECONDS
        async with validated_subprocess_input(
            url, headers={"User-Agent": user_agent}, scheme_downgrade=PROBE_SCHEME_DOWNGRADE,
            timeout=prober._subprocess_http_timeout(deadline),
            startup_timeout=30.0,
        ) as subprocess_input:
            cmd = [
                "ffmpeg",
                "-nostats",
                "-loglevel", "warning",
                "-protocol_whitelist", (
                    RELAY_PROTOCOL_WHITELIST
                    if subprocess_input.is_http_relay
                    else FFPROBE_PROTOCOL_WHITELIST
                ),
                "-timeout", "15000000",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_on_network_error", "1",
                "-reconnect_at_eof", "1",
                "-reconnect_delay_max", "2",
                "-i", subprocess_input.argument,
                "-t", str(sample_seconds),
                "-map", "0", "-c", "copy", "-f", "null", "-",
            ]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            started = time.monotonic()
            communicate = asyncio.ensure_future(process.communicate())
            outcome = None
            try:
                while not communicate.done():
                    remaining = deadline - (time.monotonic() - started)
                    if remaining <= 0:
                        outcome = "deadline"
                        break
                    await asyncio.wait({communicate}, timeout=min(VIEWER_POLL_SECONDS, remaining))
                    if communicate.done():
                        break
                    if self._stop_requested(prober, guard):
                        outcome = "cancelled"
                        break
                    outcome = await self._poll_viewer(prober, account_id, baseline)
                    if outcome:
                        break
                elapsed = time.monotonic() - started
            finally:
                # Also on an exception or cancellation: no ffmpeg outlives its sample.
                if not communicate.done():
                    process.kill()
                _, stderr = await asyncio.shield(communicate)

            drop_reason = None
            try:
                await subprocess_input.raise_for_relay_failure()
            except httpx.HTTPError as exc:
                drop_reason = type(exc).__name__

        stderr_text = stderr.decode(errors="replace")
        discontinuities, reconnects, start_retries = parse_ffmpeg_stderr(stderr_text)
        timed_out = outcome == "deadline"
        if timed_out and start_retries and not reconnects:
            # ffmpeg was still retrying a start that never delivered data.
            outcome = "failed_start"
        elif outcome is None:
            if not reconnects and (process.returncode != 0 or start_retries):
                outcome = "failed_start"
            elif process.returncode != 0:
                outcome = "failed"
            elif elapsed < sample_seconds - SAMPLE_DEADLINE_GRACE_SECONDS:
                outcome = "ended_early"
            else:
                outcome = "complete"
        return PlaybackSample(
            outcome=outcome,
            seconds=min(int(elapsed), sample_seconds),
            discontinuities=discontinuities,
            reconnects=reconnects,
            drop_reason=drop_reason,
            stderr_line=_last_stderr_line(stderr_text, url, subprocess_input.argument),
            timed_out=timed_out,
        )

    @staticmethod
    def _has_stats(stream_id: int) -> bool:
        """Whether the stream has a stats row; this task never creates one."""
        session = get_session()
        try:
            return session.query(StreamStats.id).filter_by(stream_id=stream_id).first() is not None
        finally:
            session.close()

    def _record_start_failure(self, stream_id: int) -> None:
        """Count a failed start without touching the sample window."""
        session = get_session()
        try:
            stats = session.query(StreamStats).filter_by(stream_id=stream_id).one()
            stats.playback_start_failures = (stats.playback_start_failures or 0) + 1
            session.commit()
        finally:
            session.close()

    def _record_sample(self, stream_id: int, sample: PlaybackSample) -> None:
        """Append a complete sample to the stream's window, refresh the sums
        and clear the start-failure count."""
        now = datetime.utcnow()
        session = get_session()
        try:
            stats = session.query(StreamStats).filter_by(stream_id=stream_id).one()
            window = json.loads(stats.playback_samples) if stats.playback_samples else []
            window.append({
                "at": now.isoformat() + "Z",
                "seconds": sample.seconds,
                "discontinuities": sample.discontinuities,
                "reconnects": sample.reconnects,
            })
            window = window[-WINDOW_SIZE:]
            stats.playback_samples = json.dumps(window)
            stats.playback_seconds = sum(s["seconds"] for s in window)
            stats.playback_discontinuities = sum(s["discontinuities"] for s in window)
            stats.playback_reconnects = sum(s["reconnects"] for s in window)
            stats.playback_start_failures = 0
            stats.last_playback_probed = now
            session.commit()
        finally:
            session.close()
