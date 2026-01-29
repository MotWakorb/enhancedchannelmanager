"""
M3U Refresh Task.

Scheduled task to refresh M3U accounts (playlists) from providers.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict

from dispatcharr_client import get_client
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)

# Polling configuration for waiting for refresh completion
POLL_INTERVAL_SECONDS = 5  # How often to check if refresh is complete
MAX_WAIT_SECONDS = 300  # Maximum time to wait (5 minutes)


async def capture_m3u_changes(account_id: int, account_name: str) -> Optional[Dict]:
    """
    Capture M3U state changes after a refresh.

    Fetches current groups/streams for the account, compares with previous
    snapshot, and persists any detected changes.

    IMPORTANT: Gets ALL groups from the M3U source (not just enabled ones) by:
    1. Getting the M3U account which has channel_groups with group IDs
    2. Getting all channel groups to build ID -> name mapping
    3. Getting actual stream counts per group (only available for enabled groups)
    4. Merging: all groups get names, stream counts where available

    Returns the change set dict if changes were detected, None otherwise.
    """
    from database import get_session
    from m3u_change_detector import M3UChangeDetector

    api_client = get_client()

    try:
        # Get the M3U account - channel_groups contains ALL groups from this M3U source
        account_data = await api_client.get_m3u_account(account_id)
        account_channel_groups = account_data.get("channel_groups", [])

        # Get all channel groups to build ID -> name mapping
        all_channel_groups = await api_client.get_channel_groups()
        group_lookup = {
            g["id"]: g["name"]
            for g in all_channel_groups
        }

        # Get actual stream counts (only available for enabled groups with imported streams)
        stream_counts = await api_client.get_stream_groups_with_counts(m3u_account_id=account_id)
        stream_count_lookup = {
            g["name"]: g["count"]
            for g in stream_counts
        }

        # Build list of enabled group names to fetch stream names for
        enabled_group_names = []
        for acg in account_channel_groups:
            group_id = acg.get("channel_group")
            if group_id and group_id in group_lookup and acg.get("enabled", False):
                enabled_group_names.append(group_lookup[group_id])

        # Fetch stream names for enabled groups (limit to first 50 per group)
        stream_names_by_group = {}
        MAX_STREAM_NAMES = 50
        for group_name in enabled_group_names:
            try:
                streams_response = await api_client.get_streams(
                    page=1,
                    page_size=MAX_STREAM_NAMES,
                    channel_group_name=group_name,
                    m3u_account=account_id,
                )
                stream_names = [s.get("name", "") for s in streams_response.get("results", [])]
                if stream_names:
                    stream_names_by_group[group_name] = stream_names
            except Exception as e:
                logger.debug(f"[M3U-CHANGE] Could not fetch streams for group '{group_name}': {e}")

        # Match up: for each group in this M3U account, get name and stream count
        current_groups = []
        total_streams = 0

        for acg in account_channel_groups:
            group_id = acg.get("channel_group")
            if group_id and group_id in group_lookup:
                group_name = group_lookup[group_id]
                # Get stream count if available (only for enabled groups), otherwise 0
                stream_count = stream_count_lookup.get(group_name, 0)
                enabled = acg.get("enabled", False)
                current_groups.append({
                    "name": group_name,
                    "stream_count": stream_count,
                    "enabled": enabled,
                })
                total_streams += stream_count

        logger.info(
            f"[M3U-CHANGE] Capturing state for account {account_id} ({account_name}): "
            f"{len(current_groups)} groups, {total_streams} streams (all groups from M3U)"
        )

        # Use change detector to compare and persist
        db = get_session()
        try:
            detector = M3UChangeDetector(db)
            change_set = detector.detect_changes(
                m3u_account_id=account_id,
                current_groups=current_groups,
                current_total_streams=total_streams,
                stream_names_by_group=stream_names_by_group,
            )

            if change_set.has_changes:
                # Persist the changes
                detector.persist_changes(change_set)
                logger.info(
                    f"[M3U-CHANGE] Detected and persisted changes for {account_name}: "
                    f"+{len(change_set.groups_added)} groups, -{len(change_set.groups_removed)} groups, "
                    f"+{sum(s.count for s in change_set.streams_added)} streams, "
                    f"-{sum(s.count for s in change_set.streams_removed)} streams"
                )
                return change_set.to_dict()
            else:
                logger.debug(f"[M3U-CHANGE] No changes detected for {account_name}")
                return None
        finally:
            db.close()

    except Exception as e:
        logger.error(f"[M3U-CHANGE] Failed to capture changes for {account_name}: {e}")
        return None


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
                # Skip the "Custom" account - it has no URL to refresh
                if account.get("name", "").lower() == "custom":
                    continue

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

                    # Capture M3U changes after successful refresh
                    self._set_progress(current_item=f"Capturing changes for {account_name}...")
                    await capture_m3u_changes(account_id, account_name)

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
