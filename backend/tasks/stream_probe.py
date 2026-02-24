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

    Channel groups to probe are now configured per-schedule via task parameters,
    not via a global setting. This allows different schedules to probe different groups.

    Note: Scheduled probing is controlled by the Task Engine.
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
        # Schedule parameter overrides
        self._channel_groups: Optional[list] = None  # None = not configured (probe all), [] = explicitly empty (probe nothing)
        self._auto_sync_groups: bool = False  # Probe all current groups at runtime
        self._timeout_override: Optional[int] = None
        self._max_concurrent_override: Optional[int] = None

    def get_config(self) -> dict:
        """Get stream probe configuration."""
        return {
            "channel_groups": self._channel_groups,
            "auto_sync_groups": self._auto_sync_groups,
            "timeout": self._timeout_override,
            "max_concurrent": self._max_concurrent_override,
        }

    def update_config(self, config: dict) -> None:
        """Update stream probe configuration from schedule parameters.

        Supported parameters:
        - channel_groups: list[str] - which channel groups to probe
        - timeout: int - probe timeout in seconds
        - max_concurrent: int - max concurrent probe operations
        """
        if "channel_groups" in config:
            # Preserve distinction: None = not configured, [] = explicitly empty
            val = config["channel_groups"]
            self._channel_groups = val if val is not None else []
        if "auto_sync_groups" in config:
            self._auto_sync_groups = bool(config["auto_sync_groups"])
        if "timeout" in config:
            self._timeout_override = config["timeout"]
        if "max_concurrent" in config:
            self._max_concurrent_override = config["max_concurrent"]

        logger.info("[%s] Config updated: channel_groups=%s, auto_sync_groups=%s, timeout=%s, max_concurrent=%s",
                   self.task_id, self._channel_groups, self._auto_sync_groups,
                   self._timeout_override, self._max_concurrent_override)

    def set_prober(self, prober):
        """Set the StreamProber instance to delegate to.

        When a new prober is set (e.g., after settings update), clear any cached
        channel groups so the task uses the prober's updated settings.
        """
        self._prober = prober
        # Clear cached channel groups - will be set by schedule parameters on next run
        self._channel_groups = None
        logger.info("[%s] Prober updated, cleared channel groups cache", self.task_id)

    def set_channel_groups(self, groups: list[str]):
        """Set channel groups to filter by for this run."""
        self._channel_groups = groups

    async def _create_progress_notification(self):
        """Skip task engine notification — StreamProber creates its own."""
        pass

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

        # Save original prober settings so we can restore them after
        original_timeout = self._prober.probe_timeout
        original_max_concurrent = self._prober.max_concurrent_probes

        try:
            # Apply schedule parameter overrides if set
            if self._timeout_override is not None:
                self._prober.probe_timeout = self._timeout_override
                logger.info("[%s] Using schedule timeout: %ss", self.task_id, self._timeout_override)
            if self._max_concurrent_override is not None:
                self._prober.max_concurrent_probes = max(1, min(16, self._max_concurrent_override))
                logger.info("[%s] Using schedule max_concurrent: %s", self.task_id, self._prober.max_concurrent_probes)

            # Determine channel groups to use
            # None = not configured (probe everything), [] = explicitly empty (probe nothing)
            channel_groups = self._channel_groups if self._channel_groups is not None else None

            if self._auto_sync_groups:
                # Auto-sync mode: always probe ALL current groups, ignore stored list
                logger.info("[%s] Auto-sync enabled, probing all current groups", self.task_id)
                channel_groups = None  # None = probe everything
            elif channel_groups:
                # Fixed-list mode: validate stored IDs/names against current groups
                # Note: stale groups are auto-removed on schedule load, but validate
                # here too in case groups were deleted between loads
                try:
                    current_groups = await self._prober.client.get_channel_groups()
                    current_by_id = {g["id"]: g.get("name") for g in current_groups}
                    current_by_name = {g.get("name"): g for g in current_groups}

                    if channel_groups and isinstance(channel_groups[0], int):
                        valid_ids = [gid for gid in channel_groups if gid in current_by_id]
                        stale_count = len(channel_groups) - len(valid_ids)
                        valid_groups = [current_by_id[gid] for gid in valid_ids]
                    else:
                        valid_groups = [g for g in channel_groups if g in current_by_name]
                        stale_count = len(channel_groups) - len(valid_groups)

                    if stale_count:
                        logger.warning("[%s] Skipping %s stale group(s) (will be auto-removed on next schedule load)", self.task_id, stale_count)

                    # Use validated group names; if all were stale, keep empty list
                    # (empty = probe nothing, not probe everything)
                    channel_groups = valid_groups
                except Exception as e:
                    logger.warning("[%s] Failed to validate channel groups: %s", self.task_id, e)

            # Start the probe in background so we can poll for progress
            logger.info("[%s] Starting stream probe (groups: %s)", self.task_id, channel_groups)

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
            logger.exception("[%s] Stream probe failed: %s", self.task_id, e)
            return TaskResult(
                success=False,
                message=f"Stream probe failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
        finally:
            # Restore original prober settings
            self._prober.probe_timeout = original_timeout
            self._prober.max_concurrent_probes = original_max_concurrent

            # Clear all schedule parameter overrides
            self._channel_groups = None
            self._auto_sync_groups = False
            self._timeout_override = None
            self._max_concurrent_override = None
