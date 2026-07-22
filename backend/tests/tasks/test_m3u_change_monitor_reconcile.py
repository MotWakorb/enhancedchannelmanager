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
