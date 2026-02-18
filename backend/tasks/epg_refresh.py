"""
EPG Refresh Task.

Scheduled task to refresh EPG (Electronic Program Guide) data from sources.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from dispatcharr_client import get_client
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)

# Polling configuration for waiting for refresh completion
POLL_INTERVAL_SECONDS = 5  # How often to check if refresh is complete
MAX_WAIT_SECONDS = 300  # Maximum time to wait (5 minutes)


@register_task
class EPGRefreshTask(TaskScheduler):
    """
    Task to refresh EPG data from configured sources.

    Configuration options (stored in task config JSON):
    - source_ids: List of EPG source IDs to refresh (empty = all active sources)

    Note: Dummy EPG sources are always skipped since they are automatically
    refreshed during M3U refresh.
    """

    task_id = "epg_refresh"
    task_name = "EPG Refresh"
    task_description = "Refresh EPG (Electronic Program Guide) data from sources"

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        # Default to daily at 4 AM
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.MANUAL,
                schedule_time="04:00",
            )
        super().__init__(schedule_config)

        # Task-specific config
        self.source_ids: list[int] = []  # Empty = all sources

    def get_config(self) -> dict:
        """Get EPG refresh configuration."""
        return {
            "source_ids": self.source_ids,
        }

    def update_config(self, config: dict) -> None:
        """Update EPG refresh configuration."""
        logger.debug("[%s] Updating config: %s", self.task_id, config)
        if "source_ids" in config:
            self.source_ids = config["source_ids"] or []

    async def execute(self) -> TaskResult:
        """Execute the EPG refresh."""
        client = get_client()
        started_at = datetime.utcnow()

        self._set_progress(status="fetching_sources")

        try:
            # Get all EPG sources
            all_sources = await client.get_epg_sources()
            logger.info("[%s] Found %s EPG sources", self.task_id, len(all_sources))

            # Filter sources to refresh
            sources_to_refresh = []
            for source in all_sources:
                source_name = source.get("name", f"Source {source['id']}")
                # Skip inactive sources
                if not source.get("is_active", True):
                    logger.debug("[%s] Skipping inactive source: %s (id=%s)", self.task_id, source_name, source["id"])
                    continue

                # Always skip dummy sources (they refresh automatically with M3U refresh)
                if source.get("source_type") == "dummy":
                    logger.debug("[%s] Skipping dummy source: %s (id=%s)", self.task_id, source_name, source["id"])
                    continue

                # Filter by source IDs if specified
                if self.source_ids and source["id"] not in self.source_ids:
                    logger.debug("[%s] Skipping source not in filter: %s (id=%s)", self.task_id, source_name, source["id"])
                    continue

                sources_to_refresh.append(source)

            logger.info(
                "[%s] %s of %s sources selected for refresh%s",
                self.task_id, len(sources_to_refresh), len(all_sources),
                " (filter: %s)" % self.source_ids if self.source_ids else ""
            )

            if not sources_to_refresh:
                return TaskResult(
                    success=True,
                    message="No EPG sources to refresh",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=0,
                )

            self._set_progress(
                total=len(sources_to_refresh),
                current=0,
                status="refreshing",
            )

            # Refresh each source
            success_count = 0
            failed_count = 0
            refreshed = []
            errors = []

            for i, source in enumerate(sources_to_refresh):
                if self._cancel_requested:
                    break

                source_id = source["id"]
                source_name = source.get("name", f"Source {source_id}")
                self._set_progress(
                    current=i + 1,
                    current_item=f"Refreshing {source_name}...",
                )

                try:
                    # Get initial state to detect when refresh completes
                    initial_source = await client.get_epg_source(source_id)
                    initial_updated = initial_source.get("updated_at") or initial_source.get("last_updated")

                    logger.info("[%s] Triggering EPG refresh for: %s (id=%s)", self.task_id, source_name, source_id)
                    logger.debug("[%s] Initial updated_at for %s: %s", self.task_id, source_name, initial_updated)
                    await client.refresh_epg_source(source_id)

                    # Poll until refresh completes or timeout
                    self._set_progress(current_item=f"Waiting for {source_name} to complete...")
                    refresh_complete = False
                    wait_start = datetime.utcnow()
                    poll_count = 0

                    while not refresh_complete and not self._cancel_requested:
                        elapsed = (datetime.utcnow() - wait_start).total_seconds()
                        if elapsed >= MAX_WAIT_SECONDS:
                            logger.warning("[%s] Timeout waiting for %s refresh", self.task_id, source_name)
                            break

                        await asyncio.sleep(POLL_INTERVAL_SECONDS)
                        poll_count += 1

                        # Check if source has been updated
                        current_source = await client.get_epg_source(source_id)
                        current_updated = current_source.get("updated_at") or current_source.get("last_updated")
                        logger.debug(
                            "[%s] Poll #%s for %s: current_updated=%s elapsed=%.0fs",
                            self.task_id, poll_count, source_name, current_updated, elapsed
                        )

                        if current_updated and current_updated != initial_updated:
                            refresh_complete = True
                            wait_duration = (datetime.utcnow() - wait_start).total_seconds()
                            logger.info("[%s] %s refresh complete in %.1fs", self.task_id, source_name, wait_duration)
                        elif elapsed > 30:
                            # After 30 seconds, assume refresh is complete if no timestamp field
                            # (Dispatcharr might not have updated_at on EPG sources)
                            logger.info("[%s] %s - assuming complete after %.0fs", self.task_id, source_name, elapsed)
                            break

                    success_count += 1
                    refreshed.append(source_name)
                    self._increment_progress(success_count=1)
                except Exception as e:
                    logger.error("[%s] Failed to refresh %s: %s", self.task_id, source_name, e)
                    failed_count += 1
                    errors.append(f"{source_name}: {str(e)}")
                    self._increment_progress(failed_count=1)

            self._set_progress(
                success_count=success_count,
                failed_count=failed_count,
                status="completed" if not self._cancel_requested else "cancelled",
            )

            duration = (datetime.utcnow() - started_at).total_seconds()
            logger.info(
                "[%s] EPG refresh finished in %.1fs: %s succeeded, %s failed, refreshed=[%s]",
                self.task_id, duration, success_count, failed_count, ", ".join(refreshed)
            )

            # Build result
            if self._cancel_requested:
                return TaskResult(
                    success=False,
                    message="EPG refresh cancelled",
                    error="CANCELLED",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=len(sources_to_refresh),
                    success_count=success_count,
                    failed_count=failed_count,
                    details={"refreshed": refreshed, "errors": errors},
                )

            if failed_count > 0:
                return TaskResult(
                    success=success_count > 0,
                    message=f"Refreshed {success_count} EPG sources, {failed_count} failed",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=len(sources_to_refresh),
                    success_count=success_count,
                    failed_count=failed_count,
                    details={"refreshed": refreshed, "errors": errors},
                )

            return TaskResult(
                success=True,
                message=f"Successfully refreshed {success_count} EPG sources",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=len(sources_to_refresh),
                success_count=success_count,
                failed_count=0,
                details={"refreshed": refreshed},
            )

        except Exception as e:
            logger.exception("[%s] EPG refresh failed: %s", self.task_id, e)
            return TaskResult(
                success=False,
                message=f"EPG refresh failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
