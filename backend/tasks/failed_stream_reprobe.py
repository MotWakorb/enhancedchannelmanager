"""
Failed Stream Re-probe Task.

Scheduled task to re-probe only streams that previously failed or timed out,
rather than probing all streams. Delegates to the existing StreamProber with
a stream_ids_filter for targeted re-probing.
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
class FailedStreamReprobeTask(TaskScheduler):
    """
    Task to re-probe only streams that previously failed or timed out.

    Queries StreamStats for failed/timeout probe_status, then delegates
    to probe_all_streams(stream_ids_filter=...) for targeted re-probing.
    """

    task_id = "failed_stream_reprobe"
    task_name = "Re-probe Failed Streams"
    task_description = "Re-probe only streams that previously failed or timed out"

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.MANUAL,
                schedule_time="04:00",
            )
        super().__init__(schedule_config)

        self._prober = None
        self._timeout_override: Optional[int] = None
        self._max_concurrent_override: Optional[int] = None

    def get_config(self) -> dict:
        return {
            "timeout": self._timeout_override,
            "max_concurrent": self._max_concurrent_override,
        }

    def update_config(self, config: dict) -> None:
        if "timeout" in config:
            self._timeout_override = config["timeout"]
        if "max_concurrent" in config:
            self._max_concurrent_override = config["max_concurrent"]
        logger.info("[%s] Config updated: timeout=%s, max_concurrent=%s",
                    self.task_id, self._timeout_override, self._max_concurrent_override)

    def set_prober(self, prober):
        """Set the StreamProber instance to delegate to."""
        self._prober = prober
        logger.info("[%s] Prober set", self.task_id)

    async def _create_progress_notification(self):
        """Skip task engine notification — StreamProber creates its own."""
        pass

    async def validate_config(self) -> tuple[bool, str]:
        if self._prober is None:
            return False, "StreamProber not initialized"
        return True, ""

    async def execute(self) -> TaskResult:
        """Execute the failed stream re-probe."""
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
                message="A probe is already in progress",
                error="ALREADY_RUNNING",
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        self._set_progress(status="starting", current_item="Finding failed streams...")

        # Query for failed/timeout streams
        session = get_session()
        try:
            failed_stats = session.query(StreamStats).filter(
                StreamStats.probe_status.in_(["failed", "timeout"])
            ).all()
            failed_ids = [s.stream_id for s in failed_stats]
        finally:
            session.close()

        if not failed_ids:
            return TaskResult(
                success=True,
                message="No failed streams to re-probe",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=0,
                success_count=0,
                failed_count=0,
                skipped_count=0,
            )

        self._set_progress(
            status="probing",
            current_item=f"Re-probing {len(failed_ids)} failed streams...",
            total=len(failed_ids),
        )

        # Save original prober settings
        original_timeout = self._prober.probe_timeout
        original_max_concurrent = self._prober.max_concurrent_probes

        try:
            # Apply schedule parameter overrides
            if self._timeout_override is not None:
                self._prober.probe_timeout = self._timeout_override
                logger.info("[%s] Using schedule timeout: %ss", self.task_id, self._timeout_override)
            if self._max_concurrent_override is not None:
                self._prober.max_concurrent_probes = max(1, min(16, self._max_concurrent_override))
                logger.info("[%s] Using schedule max_concurrent: %s", self.task_id, self._prober.max_concurrent_probes)

            logger.info("[%s] Re-probing %d failed streams", self.task_id, len(failed_ids))

            probe_task = asyncio.create_task(
                self._prober.probe_all_streams(
                    stream_ids_filter=failed_ids,
                    skip_m3u_refresh=True,
                )
            )

            # Poll for progress while the probe runs
            while not probe_task.done():
                if self._cancel_requested:
                    self._prober.cancel_probe()
                    break

                self._set_progress(
                    total=self._prober._probe_progress_total,
                    current=self._prober._probe_progress_current,
                    status=self._prober._probe_progress_status,
                    current_item=self._prober._probe_progress_current_stream,
                    success_count=self._prober._probe_progress_success_count,
                    failed_count=self._prober._probe_progress_failed_count,
                    skipped_count=self._prober._probe_progress_skipped_count,
                )

                await asyncio.sleep(1)

            try:
                await probe_task
            except Exception:
                pass

            # Get final results
            success_count = self._prober._probe_progress_success_count
            failed_count = self._prober._probe_progress_failed_count
            skipped_count = self._prober._probe_progress_skipped_count
            total = self._prober._probe_progress_total

            self._set_progress(
                success_count=success_count,
                failed_count=failed_count,
                skipped_count=skipped_count,
                status="completed" if not self._cancel_requested else "cancelled",
            )

            details = {
                "failed_stream_ids": failed_ids[:50],
                "success_streams": [
                    {"id": s.get("id"), "name": s.get("name")}
                    for s in self._prober._probe_success_streams[:50]
                ],
                "failed_streams": [
                    {"id": s.get("id"), "name": s.get("name"), "error": s.get("error")}
                    for s in self._prober._probe_failed_streams[:50]
                ],
            }

            if self._cancel_requested:
                return TaskResult(
                    success=False,
                    message="Re-probe cancelled",
                    error="CANCELLED",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=total,
                    success_count=success_count,
                    failed_count=failed_count,
                    skipped_count=skipped_count,
                    details=details,
                )

            if failed_count > 0 and success_count == 0:
                return TaskResult(
                    success=False,
                    message=f"Re-probe completed: {failed_count} still failed, {skipped_count} skipped",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=total,
                    success_count=success_count,
                    failed_count=failed_count,
                    skipped_count=skipped_count,
                    details=details,
                )

            return TaskResult(
                success=True,
                message=f"Re-probed {success_count} streams successfully, {failed_count} failed, {skipped_count} skipped",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=total,
                success_count=success_count,
                failed_count=failed_count,
                skipped_count=skipped_count,
                details=details,
            )

        except Exception as e:
            logger.exception("[%s] Failed stream re-probe failed: %s", self.task_id, e)
            return TaskResult(
                success=False,
                message=f"Failed stream re-probe failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
        finally:
            # Restore original prober settings
            self._prober.probe_timeout = original_timeout
            self._prober.max_concurrent_probes = original_max_concurrent

            # Clear schedule parameter overrides
            self._timeout_override = None
            self._max_concurrent_override = None
