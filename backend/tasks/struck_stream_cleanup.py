"""
Struck Stream Cleanup Task.

Scheduled task to automatically remove struck-out streams from channels.
Streams are "struck out" when their consecutive_failures count reaches
the configured strike_threshold.
"""
import logging
import time
from datetime import datetime
from typing import Optional

from config import get_settings
from database import get_session
from dispatcharr_client import get_client
from models import StreamStats
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)


@register_task
class StruckStreamCleanupTask(TaskScheduler):
    """
    Task to remove struck-out streams from channels.

    Uses the global strike_threshold from settings to identify streams
    with too many consecutive failures, then removes them from all channels.
    """

    task_id = "struck_stream_cleanup"
    task_name = "Struck Stream Cleanup"
    task_description = "Remove struck-out streams from channels"

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.MANUAL,
                schedule_time="05:00",
            )
        super().__init__(schedule_config)

    def get_config(self) -> dict:
        return {}

    def update_config(self, config: dict) -> None:
        pass

    async def execute(self) -> TaskResult:
        """Execute struck stream cleanup."""
        started_at = datetime.utcnow()

        settings = get_settings()
        threshold = settings.strike_threshold

        if threshold <= 0:
            return TaskResult(
                success=True,
                message="Strike threshold is disabled (set to 0)",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=0,
                success_count=0,
                failed_count=0,
            )

        self._set_progress(status="scanning", current_item="Finding struck-out streams...")

        # Find struck-out streams
        session = get_session()
        try:
            struck_stats = session.query(StreamStats).filter(
                StreamStats.consecutive_failures >= threshold
            ).all()
            struck_ids = [s.stream_id for s in struck_stats]
        finally:
            session.close()

        if not struck_ids:
            return TaskResult(
                success=True,
                message="No struck-out streams found",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=0,
                success_count=0,
                failed_count=0,
            )

        self._set_progress(
            status="removing",
            current_item=f"Removing {len(struck_ids)} struck-out streams from channels...",
            total=len(struck_ids),
        )

        logger.info("[%s] Found %d struck-out streams (threshold=%d)", self.task_id, len(struck_ids), threshold)

        client = get_client()
        removed_count = 0
        errors = []

        try:
            start = time.time()

            # Fetch all channels (paginated)
            all_channels = []
            page = 1
            while True:
                if self._cancel_requested:
                    return TaskResult(
                        success=False,
                        message="Struck stream cleanup cancelled",
                        error="CANCELLED",
                        started_at=started_at,
                        completed_at=datetime.utcnow(),
                    )

                result = await client.get_channels(page=page, page_size=100)
                page_channels = result.get("results", [])
                all_channels.extend(page_channels)
                if len(all_channels) >= result.get("count", 0) or not page_channels:
                    break
                page += 1

            struck_set = set(struck_ids)

            # Remove struck streams from each channel
            for ch in all_channels:
                if self._cancel_requested:
                    break

                ch_streams = ch.get("streams", [])
                filtered = [sid for sid in ch_streams if sid not in struck_set]
                if len(filtered) < len(ch_streams):
                    removed_here = len(ch_streams) - len(filtered)
                    try:
                        await client.update_channel(ch["id"], {"streams": filtered})
                        removed_count += removed_here
                        logger.info("[%s] Removed %s struck streams from channel %s (%s)",
                                    self.task_id, removed_here, ch["id"], ch.get("name"))
                    except Exception as e:
                        logger.warning("[%s] Failed to update channel %s: %s", self.task_id, ch["id"], e)
                        errors.append(f"Channel {ch.get('name', ch['id'])}: {str(e)}")

            elapsed_ms = (time.time() - start) * 1000
            logger.info("[%s] Removed %d struck streams from channels in %.1fms", self.task_id, removed_count, elapsed_ms)

            # Reset consecutive_failures for removed streams
            if removed_count > 0:
                session = get_session()
                try:
                    for sid in struck_ids:
                        stats = session.query(StreamStats).filter_by(stream_id=sid).first()
                        if stats:
                            stats.consecutive_failures = 0
                    session.commit()
                    logger.info("[%s] Reset consecutive_failures for %d streams", self.task_id, len(struck_ids))
                finally:
                    session.close()

            self._set_progress(
                success_count=removed_count,
                failed_count=len(errors),
                status="completed" if not self._cancel_requested else "cancelled",
            )

            details = {
                "struck_stream_ids": struck_ids[:50],
                "removed_from_channels": removed_count,
                "threshold": threshold,
            }
            if errors:
                details["errors"] = errors[:20]

            if self._cancel_requested:
                return TaskResult(
                    success=False,
                    message=f"Cleanup cancelled. Removed {removed_count} stream assignments before cancellation.",
                    error="CANCELLED",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=len(struck_ids),
                    success_count=removed_count,
                    failed_count=len(errors),
                    details=details,
                )

            if errors and removed_count == 0:
                return TaskResult(
                    success=False,
                    message=f"Cleanup failed: {len(errors)} errors",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=len(struck_ids),
                    success_count=0,
                    failed_count=len(errors),
                    details=details,
                )

            return TaskResult(
                success=True,
                message=f"Removed {removed_count} struck stream assignments ({len(struck_ids)} streams, threshold={threshold})",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=len(struck_ids),
                success_count=removed_count,
                failed_count=len(errors),
                details=details,
            )

        except Exception as e:
            logger.exception("[%s] Struck stream cleanup failed: %s", self.task_id, e)
            return TaskResult(
                success=False,
                message=f"Struck stream cleanup failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
