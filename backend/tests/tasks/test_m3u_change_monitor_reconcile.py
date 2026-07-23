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


async def _run_monitor(test_session, *, accounts, snapshot_ts, sweep_return=None,
                       sweep_side_effect=None, change_set=None):
    """Drive M3UChangeMonitorTask.execute with a stubbed sweep + change capture."""
    if snapshot_ts is not None:
        test_session.add(
            M3USnapshot(m3u_account_id=11, dispatcharr_updated_at=snapshot_ts, total_streams=0)
        )
        test_session.commit()
    client = AsyncMock()
    client.get_m3u_accounts.return_value = accounts
    if sweep_side_effect is not None:
        fake_sweep = AsyncMock(side_effect=sweep_side_effect)
    else:
        fake_sweep = AsyncMock(return_value=sweep_return or {})
    task = M3UChangeMonitorTask()
    with patch("tasks.m3u_change_monitor.get_client", return_value=client), \
         patch("tasks.m3u_change_monitor.get_session", return_value=test_session), \
         patch("tasks.m3u_refresh.capture_m3u_changes", AsyncMock(return_value=change_set)), \
         patch("tasks.m3u_digest.send_immediate_digest", AsyncMock()), \
         patch("services.profile_reconcile.reconcile_all_selected_groups", fake_sweep):
        return await task.execute()


@pytest.mark.asyncio
async def test_counter_invariants_hold_across_change_and_error_paths(test_session):
    """BLOCKER (Finding-5 regression): total_items must be >= BOTH success_count
    and failed_count on every path — including a change detected in a
    no-selection deployment (A), a sweep that RAISED (B), and a sweep whose
    group-settings fetch failed (C)."""
    account = {"id": 11, "name": "HD Homerun", "is_active": True, "updated_at": "T1"}

    # (A) 1 account checked, 1 CHANGE detected, deployment has NO selections.
    result_a = await _run_monitor(
        test_session, accounts=[account], snapshot_ts="T0",  # T0 != T1 -> change
        change_set={"changed": True},
        sweep_return={"groups_reconciled": 0, "groups_partial_failure": 0,
                      "groups_degraded": 0, "groups_errored": 0,
                      "groups_with_selection": 0, "accounts_normalized": 0,
                      "accounts_normalize_failed": 0},
    )
    assert result_a.success_count == 1
    assert result_a.total_items >= result_a.success_count
    assert result_a.total_items >= result_a.failed_count


@pytest.mark.asyncio
async def test_counter_invariants_hold_when_sweep_raises(test_session):
    account = {"id": 11, "name": "HD Homerun", "is_active": True, "updated_at": "T1"}
    # (B) no change, sweep RAISED -> a warning with no counted group/account item.
    result_b = await _run_monitor(
        test_session, accounts=[account], snapshot_ts="T1",  # matches -> no change
        sweep_side_effect=RuntimeError("sweep boom"),
    )
    assert result_b.failed_count >= 1
    assert result_b.total_items >= result_b.success_count
    assert result_b.total_items >= result_b.failed_count


@pytest.mark.asyncio
async def test_counter_invariants_hold_when_sweep_fetch_failed(test_session):
    # (C) NO accounts to check, sweep group-settings fetch failed -> groups_errored.
    result_c = await _run_monitor(
        test_session, accounts=[], snapshot_ts=None,
        sweep_return={"groups_reconciled": 0, "groups_partial_failure": 0,
                      "groups_degraded": 0, "groups_errored": 1,
                      "groups_with_selection": 0, "accounts_normalized": 0,
                      "accounts_normalize_failed": 0},
    )
    assert result_c.failed_count >= 1
    assert result_c.total_items >= result_c.success_count
    assert result_c.total_items >= result_c.failed_count


@pytest.mark.asyncio
async def test_counter_invariants_hold_zero_accounts_and_sweep_raised(test_session):
    """Counter edge (double-fault): accounts_checked == 0 AND the sweep RAISED —
    total_items == 0 by domain sum but failed_count == 1. The max()-guard must
    keep failed_count <= total_items (and success_count <= total_items)."""
    result = await _run_monitor(
        test_session, accounts=[], snapshot_ts=None,
        sweep_side_effect=RuntimeError("sweep boom"),
    )
    assert result.failed_count >= 1
    assert result.total_items >= result.failed_count
    assert result.total_items >= result.success_count


@pytest.mark.asyncio
async def test_counter_invariants_hold_deferred_with_zero_accounts(test_session):
    """Counter nit (deferred domain): a DEFERRED (queued) sweep with ZERO backing
    items — no accounts checked, no groups, no normalize — still adds 1 to
    failed_count for the deferral. total_items must include that deferred unit so
    the invariant total_items >= failed_count (and >= success_count) holds."""
    result = await _run_monitor(
        test_session, accounts=[], snapshot_ts=None,
        sweep_return={"status": "queued", "coalesced": True},
    )
    assert result.failed_count >= 1
    assert "deferred" in result.message
    assert result.total_items >= result.failed_count
    assert result.total_items >= result.success_count


@pytest.mark.asyncio
async def test_monitor_counts_capture_exceptions_as_warnings(test_session):
    """Honesty (B2): an account change-capture EXCEPTION is counted as a task
    warning (failed_count), not silently swallowed."""
    # Snapshot T0 != account T1 -> should_capture -> capture raises.
    test_session.add(
        M3USnapshot(m3u_account_id=11, dispatcharr_updated_at="T0", total_streams=0)
    )
    test_session.commit()
    account = {"id": 11, "name": "HD Homerun", "is_active": True, "updated_at": "T1"}
    client = AsyncMock()
    client.get_m3u_accounts.return_value = [account]
    fake_sweep = AsyncMock(return_value={"groups_with_selection": 0})
    task = M3UChangeMonitorTask()

    with patch("tasks.m3u_change_monitor.get_client", return_value=client), \
         patch("tasks.m3u_change_monitor.get_session", return_value=test_session), \
         patch("tasks.m3u_refresh.capture_m3u_changes", AsyncMock(side_effect=RuntimeError("capture boom"))), \
         patch("tasks.m3u_digest.send_immediate_digest", AsyncMock()), \
         patch("services.profile_reconcile.reconcile_all_selected_groups", fake_sweep):
        result = await task.execute()

    assert result.failed_count >= 1
    assert "change capture" in result.message
    assert result.total_items >= result.failed_count


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
async def test_monitor_reflects_queued_sweep_as_deferred_warning(test_session):
    """Finding 3: a COALESCED sweep returns {status:"queued"} — it did NOT run
    this pass (another sweep was already in progress). It must read as DEFERRED
    (a warning surfaced in failed_count + message), never a clean green success,
    so task history is truthful about work not done this pass."""
    account = {"id": 11, "name": "HD Homerun", "is_active": True, "updated_at": "T1"}
    result = await _run_monitor(
        test_session, accounts=[account], snapshot_ts="T1",  # matches -> no change
        sweep_return={"status": "queued", "coalesced": True},
    )
    # Best-effort poll still succeeds, but the deferral is a counted warning.
    assert result.success
    assert result.failed_count >= 1
    assert "deferred" in result.message
    assert result.total_items >= result.failed_count
    assert result.total_items >= result.success_count


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
