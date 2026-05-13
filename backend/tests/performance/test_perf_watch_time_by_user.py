"""Performance benchmark for ``GET /api/stats/watch-time`` (bd-skqln.10).

Endpoint: per-user watch-time totals (skqln.5 / GH-62), implemented in
``backend/routers/stats.py::get_watch_time_by_user``. The hot read path is
two DB operations:

1. A DISTINCT-by-(user, channel, observed_at) subquery
   (``_distinct_poll_subquery``) that collapses multi-client overcount.
2. A ``SUM(poll_interval_ms) GROUP BY user_id`` aggregate over that subquery,
   optionally filtered by ``observed_at`` range and ``user_id``.

Why benchmark the SQL layer (not the HTTP layer)
================================================
This benchmark measures the SQL work directly via the same SQLAlchemy
constructs the router uses. The HTTP wrapper adds noise (asyncio scheduling,
JSON serialization, FastAPI dependency injection) without adding query-plan
signal. The bead is about hot-query performance, so SQL is the load-bearing
measurement.

Targets and acceptance
======================
* **Bead target (5M-row fixture, local pre-merge gate):** p95 < 800ms.
* **CI default (250k-row fixture):** baseline is captured in
  ``baseline.json`` and the CI gate enforces "no >20% p95 regression vs
  baseline" via ``--benchmark-compare-fail``.
* **Fixture row count is configurable via ``ECM_PERF_ROW_COUNT``.** Set to
  ``5000000`` for the local 5M-row gate; defaults to 250k for CI.

Baseline numbers (captured 2026-05-13 on the dev workstation —
AMD Ryzen 7 PRO 8845HS, Python 3.12.3, fixture row count = 250_000)
-------------------------------------------------------------------
* ``test_watch_time_total_unfiltered``
  — min/median/max ≈ **352 / 363 / 368 ms** across 10 rounds, σ ≈ 5.8 ms.

These numbers are intentionally **not** committed as a portable baseline —
CI runners have very different CPU envelopes and a fixed JSON baseline
would either false-positive (slow runner) or false-negative (fast runner).
Instead, ``perf-benchmarks.yml`` runs the benchmark twice in the same job:
once on the PR's base branch (baseline), then on the PR's HEAD
(candidate), and uses ``--benchmark-compare-fail=median:20%`` to detect
regressions on the same hardware. The number above is informational —
it's the absolute timing on the engineer's machine at the moment skqln.10
landed, useful for sanity-checking future workstation runs.

The bead's <800ms p95 target applies to the **5M-row local pre-merge
gate** (``ECM_PERF_ROW_COUNT=5000000``). At 250k rows the absolute
timings above are well under that ceiling; the 5M-row run is intended
to be exercised by maintainers before each Stats v2 release cut, not on
every CI pass.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func

from models import SessionTelemetry
from routers.stats import _distinct_poll_subquery

# All tests in this module exercise the benchmark gate — mark them with
# ``benchmark`` so the default test run (which excludes the marker) doesn't
# pick them up.
pytestmark = pytest.mark.benchmark


def _watch_time_total_query(session, *, user_id=None, from_ms=None, to_ms=None):
    """Build the same SQL the router builds for ``group_by=total``.

    Returning the resolved rows mirrors what the router does immediately
    after this query — anything past that (username resolution, JSON
    serialization) is HTTP-layer overhead and out of scope.
    """
    distinct = _distinct_poll_subquery(session)
    base_q = session.query(distinct).filter(distinct.c.user_id.isnot(None))
    if from_ms is not None:
        base_q = base_q.filter(distinct.c.observed_at >= from_ms)
    if to_ms is not None:
        base_q = base_q.filter(distinct.c.observed_at < to_ms)
    if user_id is not None:
        base_q = base_q.filter(distinct.c.user_id == user_id)
    inner = base_q.subquery()

    return (
        session.query(
            inner.c.user_id,
            func.sum(inner.c.poll_interval_ms).label("total_ms"),
            func.max(inner.c.observed_at).label("last_observed_at"),
        )
        .group_by(inner.c.user_id)
        .all()
    )


def _run_total_unfiltered(perf_session_factory):
    """One iteration of the benchmarked path.

    Fresh session per call — the SQLAlchemy identity map caches result rows,
    which would turn the second iteration into a dict lookup.
    """
    session = perf_session_factory()
    try:
        rows = _watch_time_total_query(session)
    finally:
        session.close()
    # Sanity: the query must return at least one user-aggregated row.
    # Otherwise we'd be measuring "do nothing", which the gate would silently
    # accept. Caller asserts on this.
    return rows


class TestWatchTimeByUserPerformance:
    """Benchmarks for the GH-62 watch-time-by-user read API."""

    def test_watch_time_total_unfiltered(
        self, benchmark, perf_session_factory
    ):
        """Hot path: per-user totals across the entire seeded population.

        This is the "operator opens the Users panel for the first time"
        path — no date filter, all users in scope. The
        ``idx_session_telemetry_user_observed`` composite index from
        migration 0006 is what makes this tractable; without it, the
        DISTINCT subquery does a full table scan.
        """
        rows = benchmark(_run_total_unfiltered, perf_session_factory)
        # Guard: empty result would mean we're benchmarking nothing. The
        # seeded fixture has 50 users (see conftest._PERF_USER_COUNT), all
        # of which should appear in the unfiltered aggregate.
        assert len(rows) > 0, (
            "watch-time-total query returned zero rows — fixture mis-seeded "
            "or query plan is wrong. Benchmark numbers are meaningless."
        )
