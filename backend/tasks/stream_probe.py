"""
Stream Probe Task.

Scheduled task wrapper for the existing StreamProber functionality.
Integrates the StreamProber with the task scheduler framework.
"""
import logging
from datetime import datetime
from typing import Optional

from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)


@register_task
class StreamProbeTask(TaskScheduler):
    """
    Task wrapper for the StreamProber service.

    This task integrates the existing StreamProber with the task scheduler framework.
    The StreamProber itself maintains its own configuration (from settings) and handles
    all the complex probing logic. This task simply delegates to it.

    Configuration is managed via the existing settings in DispatcharrSettings:
    - stream_probe_interval_hours
    - stream_probe_schedule_time
    - probe_channel_groups
    - etc.

    Note: Scheduled probing is controlled by the Task Engine. The old stream_probe_enabled
    setting has been removed.
    """

    task_id = "stream_probe"
    task_name = "Stream Probe"
    task_description = "Probe streams to collect metadata (resolution, bitrate, codecs)"

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        # Default schedule - will be overridden by settings
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.MANUAL,
                schedule_time="03:00",
            )
        super().__init__(schedule_config)

        # The actual prober is obtained from main.py where it's initialized
        self._prober = None
        self._channel_groups: list[str] = []  # Override for group filtering

    def get_config(self) -> dict:
        """Get stream probe configuration."""
        return {
            "channel_groups": self._channel_groups,
        }

    def update_config(self, config: dict) -> None:
        """Update stream probe configuration."""
        if "channel_groups" in config:
            self._channel_groups = config["channel_groups"] or []

    def set_prober(self, prober):
        """Set the StreamProber instance to delegate to."""
        self._prober = prober

    def set_channel_groups(self, groups: list[str]):
        """Set channel groups to filter by for this run."""
        self._channel_groups = groups

    async def validate_config(self) -> tuple[bool, str]:
        """Validate that we have a prober instance."""
        if self._prober is None:
            return False, "StreamProber not initialized"
        return True, ""

    async def execute(self) -> TaskResult:
        """Execute the stream probe by delegating to StreamProber."""
        started_at = datetime.utcnow()

        if self._prober is None:
            return TaskResult(
                success=False,
                message="StreamProber not initialized",
                error="NOT_INITIALIZED",
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        # Check if a probe is already running
        if self._prober._probing_in_progress:
            return TaskResult(
                success=False,
                message="A probe is already in progress",
                error="ALREADY_RUNNING",
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        self._set_progress(status="starting", current_item="Initializing probe...")

        try:
            # Determine channel groups to use
            channel_groups = self._channel_groups if self._channel_groups else None

            # Start the probe in background so we can poll for progress
            logger.info(f"[{self.task_id}] Starting stream probe (groups: {channel_groups})")

            import asyncio
            # Run probe_all_streams as a background task
            probe_task = asyncio.create_task(
                self._prober.probe_all_streams(
                    channel_groups_override=channel_groups,
                    skip_m3u_refresh=False,  # Scheduled probes should refresh
                )
            )

            # Poll for progress while the probe runs
            while not probe_task.done():
                # Check for cancellation
                if self._cancel_requested:
                    self._prober.cancel_probe()
                    break

                # Update our progress from prober's progress
                self._set_progress(
                    total=self._prober._probe_progress_total,
                    current=self._prober._probe_progress_current,
                    status=self._prober._probe_progress_status,
                    current_item=self._prober._probe_progress_current_stream,
                    success_count=self._prober._probe_progress_success_count,
                    failed_count=self._prober._probe_progress_failed_count,
                    skipped_count=self._prober._probe_progress_skipped_count,
                )

                await asyncio.sleep(1)  # Poll every second

            # Wait for the task to complete (in case of cancellation, this ensures cleanup)
            try:
                await probe_task
            except Exception:
                pass  # Any exception is handled below via prober state

            # Get final results from prober
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

            # Build result details
            details = {
                "success_streams": [
                    {"id": s.get("id"), "name": s.get("name")}
                    for s in self._prober._probe_success_streams[:50]  # Limit for storage
                ],
                "failed_streams": [
                    {"id": s.get("id"), "name": s.get("name"), "error": s.get("error")}
                    for s in self._prober._probe_failed_streams[:50]
                ],
            }

            if self._cancel_requested:
                return TaskResult(
                    success=False,
                    message="Stream probe cancelled",
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
                    message=f"Stream probe completed: {failed_count} failed, {skipped_count} skipped",
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
                message=f"Probed {success_count} streams successfully, {failed_count} failed, {skipped_count} skipped",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=total,
                success_count=success_count,
                failed_count=failed_count,
                skipped_count=skipped_count,
                details=details,
            )

        except Exception as e:
            logger.exception(f"[{self.task_id}] Stream probe failed: {e}")
            return TaskResult(
                success=False,
                message=f"Stream probe failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
        finally:
            # Clear channel groups override
            self._channel_groups = []
