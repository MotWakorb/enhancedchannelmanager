"""Unit test for the scheduler-loop wiring of update_task_schedule_null_count.

bd-qxi02 P1 reviewer fix: the
``ecm_task_schedule_next_run_null_count`` gauge was only being
refreshed at ``database.init_db`` and
``task_registry.TaskRegistry.sync_from_database`` — both boot-only call
sites. The 5m alert window in ``prometheus_rules.yaml`` for
``ECMTaskSchedulerNextRunNull`` (currently commented out pending
Bundle H deploy) assumes per-scrape freshness, so a mid-life
regression that caused ``next_run_at`` to drift to NULL would not be
detected until the next container restart.

The fix adds a third call site inside
``TaskEngine._scheduler_loop`` so the gauge is refreshed on every
scheduler tick (default cadence ~60s, configured via
``check_interval``).

This test exercises one iteration of the scheduler loop and asserts
that ``observability.update_task_schedule_null_count`` was invoked.
We patch ``_check_and_run_due_tasks`` to a no-op so the test doesn't
need a real database, and we patch ``asyncio.sleep`` to fast-forward
the loop and break out after a single tick. The contract under test
is purely "the loop body calls the helper" — not the helper's
internals (those have their own dedicated unit tests in
``test_observability_task_scheduler.py``).
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from task_engine import TaskEngine


@pytest.mark.asyncio
async def test_scheduler_loop_refreshes_null_count_gauge_each_tick():
    """The scheduler loop calls update_task_schedule_null_count on every tick.

    Per bd-qxi02 P1 fix, the wiring is essential for the 5m alert
    window in prometheus_rules.yaml to see per-scrape freshness on
    the next_run_at NULL count. Without this wiring, the gauge stays
    at its boot-time value forever — mid-life regressions would not
    be detected until restart.
    """
    engine = TaskEngine(check_interval=0.01)
    engine._running = True

    # Patch the heavy work — we are only testing the wiring, not the
    # downstream tick behavior.
    engine._check_and_run_due_tasks = AsyncMock()

    # Patch asyncio.sleep so the initial 5s startup wait and the
    # per-tick sleeps complete instantly. We stop the loop after
    # the second sleep call (one full tick + the post-tick sleep)
    # by flipping _running to False so the next while-check exits.
    sleep_calls = {"count": 0}
    real_sleep = asyncio.sleep

    async def _fast_sleep(_duration):
        sleep_calls["count"] += 1
        # First call: the initial 5s "system stabilize" wait.
        # Second call: the post-tick sleep. After that, stop the loop.
        if sleep_calls["count"] >= 2:
            engine._running = False
        # Yield control so the event loop can schedule cancellation.
        await real_sleep(0)

    # Patch the symbol at the top-level observability module so we
    # see the call regardless of where task_engine imports from.
    with patch("observability.update_task_schedule_null_count") as mock_update:
        with patch("asyncio.sleep", side_effect=_fast_sleep):
            await engine._scheduler_loop()

    # The helper must have been called at least once per scheduler
    # tick. With one iteration of the loop body, that's exactly one
    # invocation.
    assert mock_update.call_count >= 1, (
        "update_task_schedule_null_count was not called from the scheduler "
        "loop tick. The 5m alert window on ecm_task_schedule_next_run_null_count "
        "depends on per-tick gauge refresh — without this wiring, the gauge "
        "stays at its boot-time value forever."
    )
    # Sanity check: _check_and_run_due_tasks was also called, proving
    # we exercised the loop body and not just the startup wait.
    assert engine._check_and_run_due_tasks.await_count >= 1


@pytest.mark.asyncio
async def test_scheduler_loop_continues_when_null_count_refresh_raises():
    """Observability failures must NOT break the scheduler loop.

    The helper is wrapped in a try/except in
    ``task_engine._scheduler_loop`` precisely because observability
    must never break the task engine. This test asserts the loop
    continues to tick (and continues to dispatch
    ``_check_and_run_due_tasks``) even when the helper raises.
    """
    engine = TaskEngine(check_interval=0.01)
    engine._running = True
    engine._check_and_run_due_tasks = AsyncMock()

    sleep_calls = {"count": 0}
    real_sleep = asyncio.sleep

    async def _fast_sleep(_duration):
        sleep_calls["count"] += 1
        if sleep_calls["count"] >= 2:
            engine._running = False
        await real_sleep(0)

    with patch(
        "observability.update_task_schedule_null_count",
        side_effect=RuntimeError("DB unreachable"),
    ):
        with patch("asyncio.sleep", side_effect=_fast_sleep):
            # The loop must not raise — the try/except in the loop
            # body swallows observability failures.
            await engine._scheduler_loop()

    # The loop body ran a full iteration despite the helper raising.
    assert engine._check_and_run_due_tasks.await_count >= 1
