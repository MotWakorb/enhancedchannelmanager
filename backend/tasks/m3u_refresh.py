"""
M3U Refresh Task.

Scheduled task to refresh M3U accounts (playlists) from providers.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict

from config import get_settings, save_settings
from dispatcharr_client import get_client
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)

# Polling configuration for waiting for refresh completion
POLL_INTERVAL_SECONDS = 5  # How often to check if refresh is complete
MAX_WAIT_SECONDS = 300  # Maximum time to wait (5 minutes)

# Page size for the bulk stream pull used to build per-group counts/names.
# Larger pages mean fewer round-trips and a lower burst RPM against Dispatcharr's
# uWSGI workers (each request costs a COUNT(*) + auth/middleware pass regardless
# of size). 1000 keeps response sizes/memory modest while halving the pulls vs
# 500; going much higher risks request timeouts on the largest accounts (bd-iwfr7).
STREAM_PULL_PAGE_SIZE = 1000
# Maximum number of stream names captured per group for the snapshot diff.
MAX_STREAM_NAMES = 500


async def get_streams_grouped(
    api_client,
    account_id: int,
    group_lookup: Dict[int, str],
    enabled_group_names: set,
) -> tuple[Dict[str, int], Dict[str, list]]:
    """Pull ALL streams for an M3U account in a single paginated loop and group
    them client-side by channel group name.

    This replaces both the per-group count probes (one
    ``GET /api/channels/streams/?page_size=1`` per group) and the per-group
    paged pulls with a SINGLE paginated ``get_streams(m3u_account=...)`` loop —
    mirroring the proven pattern in ``auto_creation_engine._fetch_streams``.

    Dispatcharr returns ``channel_group`` as a numeric ID; we resolve it to a
    name via ``group_lookup`` (built from ``get_channel_groups()``).

    Args:
        api_client: Dispatcharr client.
        account_id: M3U account ID to pull streams for.
        group_lookup: {channel_group_id: group_name} for ALL channel groups.
        enabled_group_names: Set of group names that are enabled for this
            account. Stream NAMES are only captured for these groups (parity
            with the prior behavior); COUNTS are captured for every group.

    Returns:
        (stream_count_lookup, stream_names_by_group)
          - stream_count_lookup: {group_name: count} for every group with >=1
            stream (enabled or not).
          - stream_names_by_group: {group_name: [names]} for ENABLED groups
            only, capped at MAX_STREAM_NAMES per group.
    """
    stream_count_lookup: Dict[str, int] = {}
    stream_names_by_group: Dict[str, list] = {}

    page = 1
    fetched = 0
    page_count = 0
    while True:
        response = await api_client.get_streams(
            page=page,
            page_size=STREAM_PULL_PAGE_SIZE,
            m3u_account=account_id,
        )
        page_count += 1
        results = response.get("results", [])
        for stream in results:
            group_id = stream.get("channel_group")
            group_name = group_lookup.get(group_id)
            if not group_name:
                # Stream belongs to a group we don't have a name for; skip it
                # (parity with prior code, which only counted known groups).
                continue
            stream_count_lookup[group_name] = stream_count_lookup.get(group_name, 0) + 1
            # Capture names only for enabled groups, capped per group.
            if group_name in enabled_group_names:
                names = stream_names_by_group.setdefault(group_name, [])
                if len(names) < MAX_STREAM_NAMES:
                    names.append(stream.get("name", ""))

        fetched += len(results)
        total = response.get("count", 0)
        if fetched >= total or not results:
            break
        page += 1

    logger.debug(
        "[M3U-CHANGE] Bulk stream pull for account %s: %s streams over %s page(s), "
        "%s groups with streams, %s enabled groups with names",
        account_id, fetched, page_count, len(stream_count_lookup), len(stream_names_by_group),
    )
    return stream_count_lookup, stream_names_by_group


async def capture_m3u_changes(
    account_id: int,
    account_name: str,
    dispatcharr_updated_at: Optional[str] = None,
    account_data: Optional[Dict] = None,
    all_channel_groups: Optional[list] = None,
) -> Optional[Dict]:
    """
    Capture M3U state changes after a refresh.

    Fetches current groups/streams for the account, compares with previous
    snapshot, and persists any detected changes.

    IMPORTANT: Gets ALL groups from the M3U source (not just enabled ones) by:
    1. Getting the M3U account which has channel_groups with group IDs
    2. Getting all channel groups to build ID -> name mapping
    3. Getting actual stream counts per group (only available for enabled groups)
    4. Merging: all groups get names, stream counts where available

    Args:
        account_id: The M3U account ID
        account_name: The account name (for logging)
        dispatcharr_updated_at: Dispatcharr's updated_at timestamp (for change monitoring)
        account_data: Pre-fetched M3U account dict (from get_m3u_account). When
            None, it is fetched here. Lets a caller that already has the account
            (e.g. the refresh task) avoid a redundant fetch.
        all_channel_groups: Pre-fetched list of all channel groups (from
            get_channel_groups). When None, it is fetched here. Same rationale.

    Returns the change set dict if changes were detected, None otherwise.
    """
    from database import get_session
    from m3u_change_detector import M3UChangeDetector

    api_client = get_client()

    try:
        # Get the M3U account - channel_groups contains ALL groups from this M3U source.
        # Prefer caller-supplied data to avoid redundant fetches within a refresh run;
        # fall back to fetching when called standalone (e.g. from the change monitor).
        if account_data is None:
            account_data = await api_client.get_m3u_account(account_id)
        account_channel_groups = account_data.get("channel_groups", [])

        # Get all channel groups to build ID -> name mapping
        if all_channel_groups is None:
            all_channel_groups = await api_client.get_channel_groups()
        group_lookup = {
            g["id"]: g["name"]
            for g in all_channel_groups
        }

        # Build set of enabled group names (stream names are only captured for these).
        enabled_group_names = set()
        for acg in account_channel_groups:
            group_id = acg.get("channel_group")
            if group_id and group_id in group_lookup and acg.get("enabled", False):
                enabled_group_names.add(group_lookup[group_id])

        # Single bulk paginated pull replaces BOTH the per-group count probes
        # (get_stream_groups_with_counts) AND the per-group get_streams loop.
        # Counts come from every group; names only for enabled groups (capped).
        logger.info(
            "[M3U-CHANGE] Bulk-pulling streams for account %s (%s): %s enabled groups",
            account_id, account_name, len(enabled_group_names),
        )
        stream_count_lookup, stream_names_by_group = await get_streams_grouped(
            api_client, account_id, group_lookup, enabled_group_names
        )

        logger.info("[M3U-CHANGE] Captured stream names for %s groups", len(stream_names_by_group))

        # Match up: for each group in this M3U account, get name and stream count
        current_groups = []
        total_streams = 0

        for acg in account_channel_groups:
            group_id = acg.get("channel_group")
            if group_id and group_id in group_lookup:
                group_name = group_lookup[group_id]
                # Get stream count if available (only for enabled groups), otherwise 0.
                # NOTE (enhancedchannelmanager-05h8w): stream_count MUST stay
                # sourced from the post-refresh bulk pull (stream_count_lookup
                # from get_streams_grouped). The start-of-run accounts list is
                # PRE-refresh (stale counts) and detail-endpoint count seeding
                # is unconfirmed — only ``enabled``/``is_stale`` are taken from
                # channel_groups[], which are authoritative.
                stream_count = stream_count_lookup.get(group_name, 0)
                enabled = acg.get("enabled", False)
                is_stale = acg.get("is_stale", False)
                current_groups.append({
                    "name": group_name,
                    "stream_count": stream_count,
                    "enabled": enabled,
                    "is_stale": is_stale,
                })
                total_streams += stream_count

        logger.info(
            "[M3U-CHANGE] Capturing state for account %s (%s): %s groups, %s streams (all groups from M3U)",
            account_id, account_name, len(current_groups), total_streams
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
                dispatcharr_updated_at=dispatcharr_updated_at,
            )

            if change_set.has_changes:
                # Persist the changes
                detector.persist_changes(change_set)
                logger.info(
                    "[M3U-CHANGE] Detected and persisted changes for %s: +%s groups, -%s groups, +%s streams, -%s streams",
                    account_name, len(change_set.groups_added), len(change_set.groups_removed),
                    sum(s.count for s in change_set.streams_added),
                    sum(s.count for s in change_set.streams_removed)
                )
                return change_set.to_dict()
            else:
                logger.debug("[M3U-CHANGE] No changes detected for %s", account_name)
                return None
        finally:
            db.close()

    except Exception as e:
        logger.error("[M3U-CHANGE] Failed to capture changes for %s: %s", account_name, e)
        return None


def _advance_refresh_watermark() -> None:
    """ADR-011 (bd-ka7j9): mark that an M3U refresh just completed successfully.

    Replaces the old hard chain that invoked auto-creation as a side-effect of
    the refresh task. The interval-scheduled ``AutoCreationTask`` reads this
    watermark FRESH on each tick and auto-fires when it is newer than the
    consumed watermark — so the two subsystems are decoupled and a single
    failed M3U account no longer suppresses auto-creation for the whole batch.

    Per Q1 the watermark advances on EVERY successful refresh (NOT change-gated)
    to preserve today's "runs after every refresh" behavior. We do NOT reuse
    ``M3USnapshot.snapshot_time`` (only written on detected changes — unsuitable
    as a "refresh happened" marker). Best-effort: a settings write failure is
    logged but never fails the refresh task.
    """
    try:
        settings = get_settings()
        settings.last_m3u_refresh_completed_at = datetime.utcnow().isoformat()
        save_settings(settings)
        logger.info(
            "[M3U] Advanced refresh watermark to %s (auto-creation will pick it up on its next tick)",
            settings.last_m3u_refresh_completed_at,
        )
    except Exception as e:  # pragma: no cover — watermark write is best-effort
        logger.warning("[M3U] Failed to advance refresh watermark: %s", e)


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
            logger.info("[%s] Found %s M3U accounts", self.task_id, len(all_accounts))

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

            # Fetch the channel-group list ONCE for the whole run instead of
            # re-fetching it inside capture_m3u_changes per account. Channel
            # groups are a slow-changing read; group membership relevant to the
            # diff comes from each account's own channel_groups (fetched fresh
            # per account below), so a single list snapshot here is safe.
            try:
                all_channel_groups = await client.get_channel_groups()
            except Exception as e:
                logger.warning(
                    "[%s] Could not pre-fetch channel groups, capture will fetch per-account: %s",
                    self.task_id, e,
                )
                all_channel_groups = None

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

                    logger.info("[%s] Triggering M3U refresh for: %s", self.task_id, account_name)
                    await client.refresh_m3u_account(account_id)

                    # Poll until refresh completes or timeout.
                    #
                    # Track WHY we stopped polling so we can gate the expensive
                    # capture sweep:
                    #   - timestamp_moved=True  -> positive evidence of a content
                    #     change; capture.
                    #   - assumed_complete=True -> the 30s fallback fired because
                    #     Dispatcharr exposed no usable timestamp; we have NO
                    #     definitive "no change" signal, so we MUST still capture.
                    #   - neither -> timestamp existed and never moved == definitive
                    #     no-change; skip the sweep.
                    self._set_progress(current_item=f"Waiting for {account_name} to complete...")
                    refresh_complete = False
                    wait_start = datetime.utcnow()
                    timestamp_moved = False
                    assumed_complete = False
                    latest_updated = initial_updated

                    while not refresh_complete and not self._cancel_requested:
                        elapsed = (datetime.utcnow() - wait_start).total_seconds()
                        if elapsed >= MAX_WAIT_SECONDS:
                            logger.warning("[%s] Timeout waiting for %s refresh", self.task_id, account_name)
                            break

                        await asyncio.sleep(POLL_INTERVAL_SECONDS)

                        # Check if account has been updated
                        current_account = await client.get_m3u_account(account_id)
                        current_updated = current_account.get("updated_at") or current_account.get("last_refresh")
                        latest_updated = current_updated

                        if current_updated and current_updated != initial_updated:
                            refresh_complete = True
                            timestamp_moved = True
                            wait_duration = (datetime.utcnow() - wait_start).total_seconds()
                            logger.info("[%s] %s refresh complete in %.1fs", self.task_id, account_name, wait_duration)
                        elif elapsed > 30:
                            # After 30 seconds, assume refresh is complete if no timestamp field
                            # (Dispatcharr might not have updated_at on M3U accounts).
                            # This is the UNCERTAIN path: no usable timestamp means we
                            # cannot prove "no change", so capture must still run.
                            assumed_complete = True
                            logger.info("[%s] %s - assuming complete after %.0fs", self.task_id, account_name, elapsed)
                            break

                    # Gate the expensive capture sweep on POSITIVE evidence of a
                    # change. CRITICAL: never skip when uncertain. We only skip
                    # when the timestamp existed and definitively did NOT move
                    # (timestamp_moved is False AND we did not fall back to the
                    # "assume complete" path AND we saw a real timestamp). Any
                    # other case (moved, or no usable timestamp signal) captures.
                    have_definitive_no_change = (
                        not timestamp_moved
                        and not assumed_complete
                        and latest_updated is not None
                    )
                    if have_definitive_no_change:
                        logger.info(
                            "[%s] %s: updated_at unchanged (%s) - skipping capture sweep",
                            self.task_id, account_name, latest_updated,
                        )
                    else:
                        # Capture M3U changes after refresh. Pass the freshly
                        # fetched account + the run-level channel-group list to
                        # avoid redundant fetches inside capture_m3u_changes.
                        self._set_progress(current_item=f"Capturing changes for {account_name}...")
                        capture_account_data = (
                            current_account if timestamp_moved or assumed_complete else None
                        )
                        await capture_m3u_changes(
                            account_id,
                            account_name,
                            dispatcharr_updated_at=latest_updated,
                            account_data=capture_account_data,
                            all_channel_groups=all_channel_groups,
                        )

                    success_count += 1
                    refreshed.append(account_name)
                    self._increment_progress(success_count=1)
                except Exception as e:
                    logger.error("[%s] Failed to refresh %s: %s", self.task_id, account_name, e)
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
                # ADR-011 (bd-ka7j9): a partial success still completed >=1
                # refresh — advance the watermark so auto-creation picks it up.
                # Decoupling means a single failed account no longer suppresses
                # auto-creation for the rest of the batch (the GH #473 coupling).
                if success_count > 0:
                    _advance_refresh_watermark()
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

            # ADR-011 (bd-ka7j9): the hard chain to auto-creation is gone. A
            # successful refresh advances the refresh watermark; the
            # interval-scheduled AutoCreationTask reads it FRESH on its next
            # tick and decides for itself whether to auto-fire.
            _advance_refresh_watermark()

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
            logger.exception("[%s] M3U refresh failed: %s", self.task_id, e)
            return TaskResult(
                success=False,
                message=f"M3U refresh failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
