"""
M3U Change Monitor Task.

Background task to detect M3U playlist changes made outside of ECM
(e.g., refreshes triggered directly in Dispatcharr).
"""
import logging
from datetime import datetime
from typing import Optional

from dispatcharr_client import get_client
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task
from database import get_session
from models import M3USnapshot

logger = logging.getLogger(__name__)


@register_task
class M3UChangeMonitorTask(TaskScheduler):
    """
    Task to monitor M3U accounts for changes made outside ECM.

    Polls Dispatcharr to check if any M3U account's updated_at timestamp
    has changed since we last captured a snapshot. If changed, triggers
    change detection and optionally sends immediate digest.

    Configuration options (stored in task config JSON):
    - account_ids: List of M3U account IDs to monitor (empty = all active accounts)
    - skip_inactive: Skip inactive accounts (default: True)
    """

    task_id = "m3u_change_monitor"
    task_name = "M3U Change Monitor"
    task_description = "Monitor M3U playlists for external changes"

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        # Default to every 5 minutes (300 seconds)
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.INTERVAL,
                interval_seconds=300,
            )
        super().__init__(schedule_config)

        # Task-specific config
        self.account_ids: list[int] = []  # Empty = all accounts
        self.skip_inactive: bool = True

    def get_config(self) -> dict:
        """Get M3U change monitor configuration."""
        return {
            "account_ids": self.account_ids,
            "skip_inactive": self.skip_inactive,
        }

    def update_config(self, config: dict) -> None:
        """Update M3U change monitor configuration."""
        if "account_ids" in config:
            self.account_ids = config["account_ids"] or []
        if "skip_inactive" in config:
            self.skip_inactive = config["skip_inactive"]

    async def execute(self) -> TaskResult:
        """Execute the M3U change monitor check."""
        from tasks.m3u_refresh import capture_m3u_changes
        from tasks.m3u_digest import send_immediate_digest

        client = get_client()
        started_at = datetime.utcnow()

        logger.info("[%s] Starting M3U change monitor poll...", self.task_id)
        self._set_progress(status="fetching_accounts")

        try:
            # Get all M3U accounts
            all_accounts = await client.get_m3u_accounts()
            logger.debug("[%s] Found %s M3U accounts", self.task_id, len(all_accounts))

            # Filter accounts to check
            accounts_to_check = []
            for account in all_accounts:
                # Skip the "Custom" account
                if account.get("name", "").lower() == "custom":
                    continue

                # Skip inactive accounts if configured
                if self.skip_inactive and not account.get("is_active", True):
                    continue

                # Filter by account IDs if specified
                if self.account_ids and account["id"] not in self.account_ids:
                    continue

                accounts_to_check.append(account)

            # Finding: even when account filtering yields NO accounts to check,
            # the profile-reconcile sweep must STILL run — it is the durable
            # convergence backbone and is independent of change monitoring. So we
            # do NOT early-return here; we skip the per-account loop and fall
            # through to the sweep below.
            self._set_progress(
                total=len(accounts_to_check),
                current=0,
                status="checking",
            )

            # Check each account for changes
            changes_detected = 0
            accounts_checked = 0
            capture_failures = 0  # Honesty finding (B2): counted as task warnings
            changed_accounts = []

            db = get_session()
            try:
                for i, account in enumerate(accounts_to_check):
                    if self._cancel_requested:
                        break

                    account_id = account["id"]
                    account_name = account.get("name", f"Account {account_id}")
                    current_updated_at = account.get("updated_at") or account.get("last_refresh")

                    self._set_progress(
                        current=i + 1,
                        current_item=f"Checking {account_name}...",
                    )

                    # Get the latest snapshot for this account
                    latest_snapshot = db.query(M3USnapshot).filter(
                        M3USnapshot.m3u_account_id == account_id
                    ).order_by(M3USnapshot.snapshot_time.desc()).first()

                    # Determine if we need to capture changes
                    should_capture = False
                    reason = ""

                    if not latest_snapshot:
                        # No snapshot yet - this is a new account or first run
                        should_capture = True
                        reason = "no existing snapshot"
                    elif not latest_snapshot.dispatcharr_updated_at:
                        # Snapshot exists but no dispatcharr timestamp stored
                        # (pre-upgrade snapshot) - capture to get baseline
                        should_capture = True
                        reason = "snapshot missing dispatcharr timestamp"
                    elif current_updated_at and current_updated_at != latest_snapshot.dispatcharr_updated_at:
                        # Dispatcharr's updated_at has changed since last capture
                        should_capture = True
                        reason = f"updated_at changed ({latest_snapshot.dispatcharr_updated_at} -> {current_updated_at})"

                    if should_capture:
                        logger.info("[%s] %s: %s - capturing changes", self.task_id, account_name, reason)
                        self._set_progress(current_item=f"Capturing changes for {account_name}...")

                        try:
                            # Capture changes (this will create/update snapshot)
                            change_set = await capture_m3u_changes(
                                account_id,
                                account_name,
                                dispatcharr_updated_at=current_updated_at,
                            )

                            if change_set:
                                changes_detected += 1
                                changed_accounts.append(account_name)
                                logger.info("[%s] %s: changes detected and logged", self.task_id, account_name)

                                # Send immediate digest if configured
                                try:
                                    await send_immediate_digest(account_id)
                                except Exception as e:
                                    logger.warning("[%s] Failed to send immediate digest for %s: %s", self.task_id, account_name, e)
                            else:
                                # No actual content changes, but update the snapshot's dispatcharr timestamp
                                if latest_snapshot and current_updated_at:
                                    latest_snapshot.dispatcharr_updated_at = current_updated_at
                                    db.commit()
                                    logger.debug("[%s] %s: no changes, updated timestamp", self.task_id, account_name)

                        except Exception as e:
                            logger.error("[%s] Failed to capture changes for %s: %s", self.task_id, account_name, e)
                            capture_failures += 1  # B2: surfaced as a task warning

                    accounts_checked += 1

            finally:
                db.close()

            # GH #720 Part B (bead y3m6o): converging backbone. This is the
            # DURABLE guarantee that the operator's channel_profile_ids
            # selection sticks. Run the idempotent selected-group sweep on EVERY
            # scheduled pass — NOT only when changes were detected (Blocker 4):
            # a reconcile that partially failed, or external profile drift that
            # ECM did not cause, must self-heal without waiting for the next
            # content change. The sweep is idempotent and O(P) per group, so
            # running it every ~5 minutes is cheap. Best-effort: a reconcile
            # failure never fails the monitor poll.
            reconcile_warnings = 0  # partial_failure + degraded groups this pass
            reconcile_deferred = False  # finding 3: sweep coalesced (queued)
            recon: dict = {}
            if not self._cancel_requested:
                try:
                    from services.profile_reconcile import reconcile_all_selected_groups
                    self._set_progress(status="reconciling_profiles")
                    recon = await reconcile_all_selected_groups(
                        client, cancel_check=lambda: self._cancel_requested
                    )
                    # Finding 3: a COALESCED sweep returns {status:"queued"} — it
                    # did NOT run this pass (another sweep was in progress). It
                    # must read as DEFERRED (a warning), never green success, so
                    # task history is truthful about work not done this pass.
                    if recon.get("status") == "queued":
                        reconcile_deferred = True
                        logger.info(
                            "[%s] Profile reconcile DEFERRED (coalesced — another "
                            "sweep in progress); converges on the next sweep",
                            self.task_id,
                        )
                    reconcile_warnings = (
                        recon.get("groups_partial_failure", 0)
                        + recon.get("groups_degraded", 0)
                        + recon.get("groups_errored", 0)
                        + recon.get("groups_conflicted", 0)
                    )
                    if recon.get("groups_reconciled") or reconcile_warnings:
                        logger.info(
                            "[%s] Profile reconcile: %s group(s) reconciled, %s "
                            "partial_failure, %s degraded, %s errored, %s channel(s) scoped",
                            self.task_id,
                            recon.get("groups_reconciled"),
                            recon.get("groups_partial_failure"),
                            recon.get("groups_degraded"),
                            recon.get("groups_errored"),
                            recon.get("channels_scoped"),
                        )
                except Exception as e:
                    logger.warning("[%s] Profile reconcile failed: %s", self.task_id, e)
                    # Blocker 3c: a sweep that raised is itself a warning the task
                    # history should reflect.
                    reconcile_warnings = max(reconcile_warnings, 1)

            self._set_progress(
                success_count=changes_detected,
                status="completed" if not self._cancel_requested else "cancelled",
            )

            if self._cancel_requested:
                return TaskResult(
                    success=False,
                    message="M3U change monitor cancelled",
                    error="CANCELLED",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=len(accounts_to_check),
                    success_count=changes_detected,
                )

            duration = (datetime.utcnow() - started_at).total_seconds()

            # Blocker 3c + Finding 5 (truthful counters): fold the profile-
            # reconcile outcome into the TaskResult so history reflects a warning
            # (not plain success) when any group ended partial_failure/degraded/
            # errored or a normalize account write failed. total_items counts
            # every item across ALL THREE domains at its own granularity — the
            # monitor's own accounts_checked, profile groups with a selection,
            # and accounts the normalize pass ATTEMPTED — and failed_count sums
            # the per-domain failures. A raised/errored sweep can produce a
            # warning with no counted item, so we finally raise total_items to at
            # least success_count and failed_count: that NEVER hides a failure
            # (it only widens the denominator), and guarantees BOTH
            # success_count <= total_items AND failed_count <= total_items.
            recon_detail = {"profile_reconcile": recon} if recon else {}
            groups_with_selection = recon.get("groups_with_selection", 0)
            normalize_failed = recon.get("accounts_normalize_failed", 0)
            normalize_attempted = recon.get("accounts_normalized", 0) + normalize_failed
            # B2: account-capture exceptions are account-domain failures too.
            # Finding 3: a deferred (coalesced) reconcile is a warning too — it
            # did not run this pass, so the task is NOT a clean success.
            failed_count = (
                reconcile_warnings + normalize_failed + capture_failures
                + (1 if reconcile_deferred else 0)
            )
            # The deferred sweep is itself a unit of work accounted for this pass,
            # so it belongs in the natural denominator — not just caught by the
            # failed_count safety-net below. Without it, a deferred sweep with zero
            # backing items (accounts_checked=0, or all captures failed) would leave
            # the primary sum at 0 while failed_count is 1. failed_count stays in
            # max() as the belt-and-suspenders guarantee that the invariant holds by
            # construction on every path.
            total_items = max(
                accounts_checked + groups_with_selection + normalize_attempted
                + (1 if reconcile_deferred else 0),
                changes_detected,
                failed_count,
            )
            warn_bits = []
            if reconcile_warnings:
                warn_bits.append(f"{reconcile_warnings} profile group(s) incomplete")
            if normalize_failed:
                warn_bits.append(f"{normalize_failed} account(s) not normalized")
            if capture_failures:
                warn_bits.append(f"{capture_failures} account(s) failed change capture")
            if reconcile_deferred:
                warn_bits.append("profile reconcile deferred (another sweep in progress)")
            recon_suffix = f" ({', '.join(warn_bits)})" if warn_bits else ""

            if changes_detected > 0:
                logger.info(
                    "[%s] Poll complete in %.1fs: checked %s accounts, %s with changes",
                    self.task_id, duration, accounts_checked, changes_detected
                )
                return TaskResult(
                    success=True,
                    message=f"Detected changes in {changes_detected} M3U account(s){recon_suffix}",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=total_items,
                    success_count=changes_detected,
                    failed_count=failed_count,
                    details={"changed_accounts": changed_accounts, **recon_detail},
                )

            logger.info(
                "[%s] Poll complete in %.1fs: checked %s accounts, no external changes",
                self.task_id, duration, accounts_checked
            )
            return TaskResult(
                success=True,
                message=f"Checked {accounts_checked} M3U accounts - no external changes detected{recon_suffix}",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=total_items,
                success_count=0,
                failed_count=failed_count,
                details=recon_detail,
            )

        except Exception as e:
            logger.exception("[%s] M3U change monitor failed: %s", self.task_id, e)
            return TaskResult(
                success=False,
                message=f"M3U change monitor failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
