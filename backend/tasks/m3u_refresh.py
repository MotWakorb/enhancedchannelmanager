"""
M3U Refresh Task.

Scheduled task to refresh M3U accounts (playlists) from providers.
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
class M3URefreshTask(TaskScheduler):
    """
    Task to refresh M3U accounts from providers.

    Configuration options (stored in task config JSON):
    - account_ids: List of M3U account IDs to refresh (empty = all active accounts)
    - skip_inactive: Skip inactive accounts (default: True)
    """

    task_id = "m3u_refresh"
    task_name = "M3U Refresh"
    task_description = "Refresh M3U playlists from providers"

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        # Default to daily at 5 AM
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.MANUAL,
                schedule_time="05:00",
            )
        super().__init__(schedule_config)

        # Task-specific config
        self.account_ids: list[int] = []  # Empty = all accounts
        self.skip_inactive: bool = True

    def get_config(self) -> dict:
        """Get M3U refresh configuration."""
        return {
            "account_ids": self.account_ids,
            "skip_inactive": self.skip_inactive,
        }

    def update_config(self, config: dict) -> None:
        """Update M3U refresh configuration."""
        if "account_ids" in config:
            self.account_ids = config["account_ids"] or []
        if "skip_inactive" in config:
            self.skip_inactive = config["skip_inactive"]

    async def execute(self) -> TaskResult:
        """Execute the M3U refresh."""
        client = get_client()
        started_at = datetime.utcnow()

        self._set_progress(status="fetching_accounts")

        try:
            # Get all M3U accounts
            all_accounts = await client.get_m3u_accounts()
            logger.info(f"[{self.task_id}] Found {len(all_accounts)} M3U accounts")

            # Filter accounts to refresh
            accounts_to_refresh = []
            for account in all_accounts:
                # Skip inactive accounts if configured
                if self.skip_inactive and not account.get("is_active", True):
                    continue

                # Filter by account IDs if specified
                if self.account_ids and account["id"] not in self.account_ids:
                    continue

                accounts_to_refresh.append(account)

            if not accounts_to_refresh:
                return TaskResult(
                    success=True,
                    message="No M3U accounts to refresh",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=0,
                )

            self._set_progress(
                total=len(accounts_to_refresh),
                current=0,
                status="refreshing",
            )

            # Refresh each account
            success_count = 0
            failed_count = 0
            refreshed = []
            errors = []

            for i, account in enumerate(accounts_to_refresh):
                if self._cancel_requested:
                    break

                account_id = account["id"]
                account_name = account.get("name", f"Account {account_id}")
                self._set_progress(
                    current=i + 1,
                    current_item=f"Refreshing {account_name}...",
                )

                try:
                    # Get initial state to detect when refresh completes
                    initial_account = await client.get_m3u_account(account_id)
                    initial_updated = initial_account.get("updated_at") or initial_account.get("last_refresh")

                    logger.info(f"[{self.task_id}] Triggering M3U refresh for: {account_name}")
                    await client.refresh_m3u_account(account_id)

                    # Poll until refresh completes or timeout
                    self._set_progress(current_item=f"Waiting for {account_name} to complete...")
                    refresh_complete = False
                    wait_start = datetime.utcnow()

                    while not refresh_complete and not self._cancel_requested:
                        elapsed = (datetime.utcnow() - wait_start).total_seconds()
                        if elapsed >= MAX_WAIT_SECONDS:
                            logger.warning(f"[{self.task_id}] Timeout waiting for {account_name} refresh")
                            break

                        await asyncio.sleep(POLL_INTERVAL_SECONDS)

                        # Check if account has been updated
                        current_account = await client.get_m3u_account(account_id)
                        current_updated = current_account.get("updated_at") or current_account.get("last_refresh")

                        if current_updated and current_updated != initial_updated:
                            refresh_complete = True
                            wait_duration = (datetime.utcnow() - wait_start).total_seconds()
                            logger.info(f"[{self.task_id}] {account_name} refresh complete in {wait_duration:.1f}s")
                        elif elapsed > 30:
                            # After 30 seconds, assume refresh is complete if no timestamp field
                            # (Dispatcharr might not have updated_at on M3U accounts)
                            logger.info(f"[{self.task_id}] {account_name} - assuming complete after {elapsed:.0f}s")
                            break

                    success_count += 1
                    refreshed.append(account_name)
                    self._increment_progress(success_count=1)
                except Exception as e:
                    logger.error(f"[{self.task_id}] Failed to refresh {account_name}: {e}")
                    failed_count += 1
                    errors.append(f"{account_name}: {str(e)}")
                    self._increment_progress(failed_count=1)

            self._set_progress(
                success_count=success_count,
                failed_count=failed_count,
                status="completed" if not self._cancel_requested else "cancelled",
            )

            # Build result
            if self._cancel_requested:
                return TaskResult(
                    success=False,
                    message="M3U refresh cancelled",
                    error="CANCELLED",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=len(accounts_to_refresh),
                    success_count=success_count,
                    failed_count=failed_count,
                    details={"refreshed": refreshed, "errors": errors},
                )

            if failed_count > 0:
                return TaskResult(
                    success=success_count > 0,
                    message=f"Refreshed {success_count} M3U accounts, {failed_count} failed",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=len(accounts_to_refresh),
                    success_count=success_count,
                    failed_count=failed_count,
                    details={"refreshed": refreshed, "errors": errors},
                )

            return TaskResult(
                success=True,
                message=f"Successfully refreshed {success_count} M3U accounts",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=len(accounts_to_refresh),
                success_count=success_count,
                failed_count=0,
                details={"refreshed": refreshed},
            )

        except Exception as e:
            logger.exception(f"[{self.task_id}] M3U refresh failed: {e}")
            return TaskResult(
                success=False,
                message=f"M3U refresh failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
