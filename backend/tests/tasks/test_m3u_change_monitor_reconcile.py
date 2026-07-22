"""Blocker 4: the M3U change monitor runs the profile-reconcile sweep on EVERY
scheduled pass — the durable convergence backbone — NOT only when a content
change was detected. A failed reconcile or external profile drift must self-heal
without waiting for the next content change.
"""
from unittest.mock import AsyncMock, patch

import pytest

from models import M3USnapshot
from tasks.m3u_change_monitor import M3UChangeMonitorTask


@pytest.mark.asyncio
async def test_monitor_runs_sweep_when_no_changes_detected(test_session):
    # Seed a snapshot whose dispatcharr_updated_at MATCHES the account's
    # updated_at, so the monitor detects NO content changes (changes_detected
    # stays 0) — the pre-fix code would then have SKIPPED the reconcile.
    test_session.add(
        M3USnapshot(m3u_account_id=11, dispatcharr_updated_at="T1", total_streams=0)
    )
    test_session.commit()

    account = {"id": 11, "name": "HD Homerun", "is_active": True, "updated_at": "T1"}
    client = AsyncMock()
    client.get_m3u_accounts.return_value = [account]

    fake_sweep = AsyncMock(return_value={
        "groups_reconciled": 0, "groups_partial_failure": 0, "channels_scoped": 0,
    })
    task = M3UChangeMonitorTask()

    with patch("tasks.m3u_change_monitor.get_client", return_value=client), \
         patch("tasks.m3u_change_monitor.get_session", return_value=test_session), \
         patch("services.profile_reconcile.reconcile_all_selected_groups", fake_sweep):
        result = await task.execute()

    assert result.success
    # Blocker 4: the sweep ran even though zero changes were detected.
    fake_sweep.assert_awaited_once()
    assert fake_sweep.await_args.args[0] is client
    # A clean sweep is not a warning.
    assert result.failed_count == 0


@pytest.mark.asyncio
async def test_monitor_runs_sweep_even_with_no_accounts_to_check(test_session):
    """Finding: even when account filtering yields NO accounts, the profile
    sweep still runs (it is the durable convergence backbone, independent of
    change monitoring)."""
    client = AsyncMock()
    client.get_m3u_accounts.return_value = []  # no accounts pass the filter
    fake_sweep = AsyncMock(return_value={
        "groups_reconciled": 1, "groups_partial_failure": 0, "groups_degraded": 0,
        "groups_with_selection": 1, "channels_scoped": 1,
    })
    task = M3UChangeMonitorTask()

    with patch("tasks.m3u_change_monitor.get_client", return_value=client), \
         patch("tasks.m3u_change_monitor.get_session", return_value=test_session), \
         patch("services.profile_reconcile.reconcile_all_selected_groups", fake_sweep):
        result = await task.execute()

    assert result.success
    fake_sweep.assert_awaited_once()  # sweep ran despite zero accounts


@pytest.mark.asyncio
async def test_monitor_reflects_reconcile_degraded_as_warning(test_session):
    """Blocker 3c: a sweep that ended with degraded/partial_failure groups is
    reflected in the TaskResult (failed_count > 0 -> 'completed with warnings')
    so task history does not read as a clean success."""
    test_session.add(
        M3USnapshot(m3u_account_id=11, dispatcharr_updated_at="T1", total_streams=0)
    )
    test_session.commit()
    account = {"id": 11, "name": "HD Homerun", "is_active": True, "updated_at": "T1"}
    client = AsyncMock()
    client.get_m3u_accounts.return_value = [account]

    fake_sweep = AsyncMock(return_value={
        "groups_reconciled": 1, "groups_partial_failure": 1, "groups_degraded": 1,
        "groups_with_selection": 3, "accounts_normalize_failed": 0,
        "channels_scoped": 3,
    })
    task = M3UChangeMonitorTask()

    with patch("tasks.m3u_change_monitor.get_client", return_value=client), \
         patch("tasks.m3u_change_monitor.get_session", return_value=test_session), \
         patch("services.profile_reconcile.reconcile_all_selected_groups", fake_sweep):
        result = await task.execute()

    assert result.success  # best-effort — the poll itself succeeded
    assert result.failed_count == 2  # partial_failure + degraded
    assert "incomplete" in result.message
    assert result.details.get("profile_reconcile", {}).get("groups_degraded") == 1
