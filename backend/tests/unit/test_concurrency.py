"""
Unit tests for backend/concurrency.py — run_cpu_bound wrapper + CPU pool.

bd-w3z4h: the event-loop-offload helper.
"""
import asyncio
import threading
import time

import pytest

from concurrency import (
    get_cpu_pool,
    run_cpu_bound,
    shutdown_cpu_pool,
    _resolve_max_workers,
)


class TestResolveMaxWorkers:
    """ECM_CPU_POOL_WORKERS override + default formula."""

    def test_env_override_applies(self, monkeypatch):
        monkeypatch.setenv("ECM_CPU_POOL_WORKERS", "4")
        assert _resolve_max_workers() == 4

    def test_env_override_clamped_to_min_1(self, monkeypatch):
        monkeypatch.setenv("ECM_CPU_POOL_WORKERS", "0")
        assert _resolve_max_workers() == 1

    def test_non_numeric_override_ignored(self, monkeypatch):
        monkeypatch.setenv("ECM_CPU_POOL_WORKERS", "nope")
        n = _resolve_max_workers()
        # falls back to default: 2x cpu capped at 32, always >= 2
        assert 2 <= n <= 32

    def test_default_is_capped_at_32(self, monkeypatch):
        monkeypatch.delenv("ECM_CPU_POOL_WORKERS", raising=False)
        n = _resolve_max_workers()
        assert n <= 32


class TestGetCpuPool:
    """Lazy singleton pool."""

    def setup_method(self):
        shutdown_cpu_pool()

    def teardown_method(self):
        shutdown_cpu_pool()

    def test_returns_same_instance(self):
        p1 = get_cpu_pool()
        p2 = get_cpu_pool()
        assert p1 is p2

    def test_shutdown_resets_pool(self):
        p1 = get_cpu_pool()
        shutdown_cpu_pool()
        p2 = get_cpu_pool()
        assert p1 is not p2


class TestRunCpuBound:
    """Wrapper behavior: returns result, propagates exceptions, runs off-loop."""

    def teardown_method(self):
        shutdown_cpu_pool()

    @pytest.mark.asyncio
    async def test_returns_result(self):
        def add(a, b):
            return a + b

        result = await run_cpu_bound(add, 2, 3)
        assert result == 5

    @pytest.mark.asyncio
    async def test_accepts_kwargs(self):
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}"

        result = await run_cpu_bound(greet, "world", greeting="Hi")
        assert result == "Hi, world"

    @pytest.mark.asyncio
    async def test_propagates_exception(self):
        def boom():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await run_cpu_bound(boom)

    @pytest.mark.asyncio
    async def test_runs_off_event_loop_thread(self):
        main_thread = threading.get_ident()

        def who_am_i():
            return threading.get_ident()

        worker_thread = await run_cpu_bound(who_am_i)
        assert worker_thread != main_thread

    @pytest.mark.asyncio
    async def test_does_not_block_event_loop(self):
        """Core reliability claim: a slow sync call does not block concurrent
        awaits. A 300ms blocking call should not prevent a 50ms asyncio.sleep
        from completing roughly on time if both are launched concurrently."""
        def slow_cpu_work():
            time.sleep(0.3)
            return "done"

        async def fast_sleep():
            start = time.monotonic()
            await asyncio.sleep(0.05)
            return time.monotonic() - start

        slow_task = asyncio.create_task(run_cpu_bound(slow_cpu_work))
        fast_task = asyncio.create_task(fast_sleep())

        sleep_elapsed = await fast_task
        slow_result = await slow_task

        assert slow_result == "done"
        # asyncio.sleep(0.05) should finish well before the 300ms blocker — give
        # CI headroom but verify we're not serialized behind the blocker.
        assert sleep_elapsed < 0.25, (
            f"Event loop was blocked: asyncio.sleep(0.05) took {sleep_elapsed:.3f}s"
        )
