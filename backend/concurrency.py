"""
Concurrency primitives for offloading sync CPU-heavy work off the FastAPI event loop.

Exposes:
- run_cpu_bound(func, *args, **kwargs): awaitable wrapper that runs `func` on a
  bounded thread-pool executor and returns its result. Use this at any
  user-reachable async handler that calls a sync CPU-heavy function
  (normalization_engine.normalize, dummy_epg_engine.generate_xmltv, etc.).

Why this exists (bd-w3z4h): ECM runs uvicorn with a single worker, no
--limit-concurrency and no reverse proxy. Any sync CPU-heavy call inside an
async handler blocks the event loop for every concurrent request, including
/api/health. A pathological regex (see bd-eio04.5) can freeze the loop for
hundreds of milliseconds. Offloading via a bounded thread-pool keeps the loop
responsive while still serializing CPU work so the container doesn't OOM.

Worker count defaults to min(32, os.cpu_count() * 2) — aligned with
asyncio.to_thread's default in Python 3.12. Override via ECM_CPU_POOL_WORKERS.
"""
from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Bounded thread-pool for CPU-bound offload.
# Lazily constructed so tests can reset it and so import order isn't load-bearing.
_executor: ThreadPoolExecutor | None = None


def _resolve_max_workers() -> int:
    override = os.environ.get("ECM_CPU_POOL_WORKERS")
    if override and override.isdigit():
        return max(1, int(override))
    # Default: 2x CPU count, capped at 32 (matches asyncio default)
    cpu_count = os.cpu_count() or 2
    return min(32, cpu_count * 2)


def get_cpu_pool() -> ThreadPoolExecutor:
    """Return the singleton CPU-bound thread pool, constructing it on first call."""
    global _executor
    if _executor is None:
        max_workers = _resolve_max_workers()
        _executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="ecm-cpu",
        )
        logger.info("[CONCURRENCY] CPU-bound thread pool initialized (max_workers=%s)", max_workers)
    return _executor


def shutdown_cpu_pool(wait: bool = True) -> None:
    """Shut down the CPU-bound thread pool. Primarily for test teardown."""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=wait)
        _executor = None


async def run_cpu_bound(func: Callable[..., T], *args, **kwargs) -> T:
    """Run a sync, CPU-heavy callable on the bounded thread pool.

    Returns the callable's result. Exceptions propagate to the caller.

    Contract:
    - Use only for user-reachable async handlers that call CPU-heavy sync code
      (regex-heavy rule engines, XML builders, template rendering).
    - Do NOT use for trivial work (< ~1ms). The thread-hop overhead isn't worth
      it and you lose event-loop locality.
    - Do NOT use for DB I/O that's already async-safe or for awaited HTTP calls.
    """
    loop = asyncio.get_running_loop()
    executor = get_cpu_pool()
    if kwargs:
        bound = partial(func, *args, **kwargs)
        return await loop.run_in_executor(executor, bound)
    return await loop.run_in_executor(executor, func, *args)
