"""
Black Screen Scan Task.

Standalone task that runs black screen detection on already-probed streams
without repeating the full probe (ffprobe/bitrate). Calls StreamProber._detect_black_screen()
directly on each stream URL.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from database import get_session
from models import StreamStats
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)


@register_task
class BlackScreenScanTask(TaskScheduler):
    """
    Scan previously-probed streams for black screen without running a full probe.

    Only targets streams with probe_status='success' in StreamStats.
    Ignores the global black_screen_detection_enabled setting — if the user
    schedules or runs this task, they explicitly want black screen checks.
    """

    task_id = "black_screen_scan"
    task_name = "Black Screen Scan"
    task_description = "Scan probed streams for black screens without re-probing"

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.MANUAL,
                schedule_time="05:00",
            )
        super().__init__(schedule_config)

        self._prober = None
        self._sample_duration_override: Optional[int] = None
        self._max_concurrent_override: Optional[int] = None
        self._channel_groups: Optional[list] = None
        self._auto_sync_groups: bool = False

    def get_config(self) -> dict:
        return {
            "sample_duration": self._sample_duration_override,
            "max_concurrent": self._max_concurrent_override,
            "channel_groups": self._channel_groups,
            "auto_sync_groups": self._auto_sync_groups,
        }

    def update_config(self, config: dict) -> None:
        if "sample_duration" in config:
            self._sample_duration_override = config["sample_duration"]
        if "max_concurrent" in config:
            self._max_concurrent_override = config["max_concurrent"]
        if "channel_groups" in config:
            val = config["channel_groups"]
            self._channel_groups = val if val is not None else []
        if "auto_sync_groups" in config:
            self._auto_sync_groups = bool(config["auto_sync_groups"])
        logger.info("[%s] Config updated: sample_duration=%s, max_concurrent=%s, channel_groups=%s, auto_sync=%s",
                    self.task_id, self._sample_duration_override, self._max_concurrent_override,
                    self._channel_groups, self._auto_sync_groups)

    def set_prober(self, prober):
        """Set the StreamProber instance to use for black screen detection."""
        self._prober = prober
        logger.info("[%s] Prober set", self.task_id)

    async def validate_config(self) -> tuple[bool, str]:
        if self._prober is None:
            return False, "StreamProber not initialized"
        return True, ""

    async def execute(self) -> TaskResult:
        """Execute the black screen scan."""
        started_at = datetime.utcnow()

        if self._prober is None:
            return TaskResult(
                success=False,
                message="StreamProber not initialized",
                error="NOT_INITIALIZED",
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        if self._prober._probing_in_progress:
            return TaskResult(
                success=False,
                message="A probe is already in progress — cannot run black screen scan concurrently",
                error="PROBE_RUNNING",
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        self._set_progress(status="starting", current_item="Finding probed streams...")

        # 1. Get successfully-probed stream IDs from StreamStats
        session = get_session()
        try:
            probed_stats = session.query(StreamStats).filter(
                StreamStats.probe_status == "success"
            ).all()
            probed_stream_ids = {s.stream_id for s in probed_stats}
        finally:
            session.close()

        if not probed_stream_ids:
            return TaskResult(
                success=True,
                message="No successfully probed streams to scan",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=0,
                success_count=0,
                failed_count=0,
                skipped_count=0,
            )

        # 2. Fetch streams from Dispatcharr to get URLs
        self._set_progress(status="fetching", current_item="Fetching stream URLs from Dispatcharr...")
        all_streams = await self._prober._fetch_all_streams()
        stream_map = {s["id"]: s for s in all_streams}

        # 3. Intersect: only scan streams we have both probe data and URLs for
        candidate_ids = probed_stream_ids & set(stream_map.keys())

        # 4. Filter by channel groups if configured
        if self._auto_sync_groups:
            logger.info("[%s] Auto-sync enabled, scanning all probed streams", self.task_id)
        elif self._channel_groups:
            try:
                group_names = await self._resolve_group_names()
                channel_stream_ids, _, _ = await self._prober._fetch_channel_stream_ids(
                    channel_groups_override=group_names
                )
                candidate_ids = candidate_ids & channel_stream_ids
            except Exception as e:
                logger.warning("[%s] Failed to filter by channel groups: %s", self.task_id, e)

        if not candidate_ids:
            return TaskResult(
                success=True,
                message="No matching streams to scan for black screens",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=0,
                success_count=0,
                failed_count=0,
                skipped_count=0,
            )

        # Build work list
        work_items = []
        for sid in candidate_ids:
            stream = stream_map[sid]
            url = stream.get("url")
            if url:
                work_items.append((sid, stream.get("name", f"Stream {sid}"), url))

        total = len(work_items)
        logger.info("[%s] Scanning %d streams for black screens", self.task_id, total)
        self._set_progress(status="scanning", total=total, current=0,
                           current_item=f"Scanning {total} streams...")

        # 5. Save/restore prober sample duration
        original_sample_duration = self._prober.black_screen_sample_duration
        if self._sample_duration_override is not None:
            self._prober.black_screen_sample_duration = max(3, min(30, self._sample_duration_override))
            logger.info("[%s] Using sample_duration=%ss", self.task_id, self._prober.black_screen_sample_duration)

        max_concurrent = max(1, min(10, self._max_concurrent_override or 3))
        semaphore = asyncio.Semaphore(max_concurrent)

        black_count = 0
        clear_count = 0
        error_count = 0
        completed = 0
        black_streams = []
        error_streams = []

        async def scan_one(stream_id: int, name: str, url: str):
            nonlocal black_count, clear_count, error_count, completed
            async with semaphore:
                if self._cancel_requested:
                    return
                try:
                    is_black = await self._prober._detect_black_screen(url)

                    # None = indeterminate (ffmpeg timed out or returned no
                    # YAVG samples). Count as an error and leave the existing
                    # is_black_screen value in the DB alone — overwriting it
                    # with False on timeout was erasing findings from manual
                    # probes earlier in the pipeline.
                    if is_black is None:
                        error_count += 1
                        error_streams.append({
                            "id": stream_id,
                            "name": name,
                            "error": "detection indeterminate (timeout or no video samples)",
                        })
                    else:
                        db = get_session()
                        try:
                            stats = db.query(StreamStats).filter_by(stream_id=stream_id).first()
                            if stats:
                                stats.is_black_screen = is_black
                                db.commit()
                        finally:
                            db.close()

                        if is_black:
                            black_count += 1
                            black_streams.append({"id": stream_id, "name": name})
                        else:
                            clear_count += 1
                except Exception as e:
                    error_count += 1
                    error_streams.append({"id": stream_id, "name": name, "error": str(e)})
                    logger.warning("[%s] Black screen check failed for stream %s: %s", self.task_id, stream_id, e)
                finally:
                    completed += 1
                    self._set_progress(
                        current=completed,
                        total=total,
                        status="scanning",
                        current_item=name,
                        success_count=clear_count,
                        failed_count=black_count,
                        skipped_count=error_count,
                    )

        # Save group names before finally block clears them (needed for post-scan reorder)
        reorder_group_names = None
        if self._channel_groups and not self._auto_sync_groups:
            try:
                reorder_group_names = await self._resolve_group_names()
            except Exception:
                pass

        try:
            tasks = [scan_one(sid, name, url) for sid, name, url in work_items]
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.exception("[%s] Black screen scan failed: %s", self.task_id, e)
            return TaskResult(
                success=False,
                message=f"Black screen scan failed: {e}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
        finally:
            # Restore original sample duration
            self._prober.black_screen_sample_duration = original_sample_duration

            # Clear overrides
            self._sample_duration_override = None
            self._max_concurrent_override = None
            self._channel_groups = None
            self._auto_sync_groups = False

        status = "cancelled" if self._cancel_requested else "completed"
        self._set_progress(
            status=status,
            current=completed,
            total=total,
            success_count=clear_count,
            failed_count=black_count,
            skipped_count=error_count,
        )

        # Re-sort channels if black screens were found and auto-reorder is enabled
        reorder_count = 0
        if black_count > 0 and not self._cancel_requested and self._prober.auto_reorder_after_probe:
            logger.info("[%s] %d black screen streams found, triggering smart sort reorder", self.task_id, black_count)
            self._set_progress(status="reordering", current_item="Reordering streams after black screen scan...")
            try:
                reordered = await self._prober._auto_reorder_channels(reorder_group_names)
                reorder_count = len(reordered)
                logger.info("[%s] Reordered %d channels after black screen scan", self.task_id, reorder_count)
            except Exception as e:
                logger.error("[%s] Auto-reorder after black screen scan failed: %s", self.task_id, e)

        details = {
            "black_screen_streams": black_streams[:50],
            "error_streams": error_streams[:50],
            "sample_duration": self._prober.black_screen_sample_duration,
            "reordered_channels": reorder_count,
        }

        if self._cancel_requested:
            return TaskResult(
                success=False,
                message="Black screen scan cancelled",
                error="CANCELLED",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=total,
                success_count=clear_count,
                failed_count=black_count,
                skipped_count=error_count,
                details=details,
            )

        reorder_msg = f", reordered {reorder_count} channels" if reorder_count else ""
        return TaskResult(
            success=True,
            message=f"Scanned {total} streams: {black_count} black screen, {clear_count} clear, {error_count} errors{reorder_msg}",
            started_at=started_at,
            completed_at=datetime.utcnow(),
            total_items=total,
            success_count=clear_count,
            failed_count=black_count,
            skipped_count=error_count,
            details=details,
        )

    async def _resolve_group_names(self) -> list[str]:
        """Resolve channel_groups (which may be IDs or names) to group names."""
        if not self._channel_groups:
            return []
        # If already strings, return as-is
        if isinstance(self._channel_groups[0], str):
            return self._channel_groups
        # IDs → names
        try:
            all_groups = await self._prober.client.get_channel_groups()
            id_to_name = {g["id"]: g.get("name") for g in all_groups}
            return [id_to_name[gid] for gid in self._channel_groups if gid in id_to_name]
        except Exception as e:
            logger.warning("[%s] Failed to resolve group IDs to names: %s", self.task_id, e)
            return []
