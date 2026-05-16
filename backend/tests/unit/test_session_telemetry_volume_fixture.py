"""Unit tests for ``tests.fixtures.session_telemetry_volume`` (bd-skqln.10).

The volume fixture is shared infra for two consumers:

* ``tests/integration/test_session_telemetry_migration.py`` (bd-skqln.2) —
  the local 5M-row migration up/down gate.
* ``tests/performance/`` (bd-skqln.10) — the hot-query benchmark gate, which
  needs a reproducible row distribution to compare against a committed
  baseline.

For benchmark consumers, **determinism is the load-bearing property**:
the same seed must produce exactly the same row distribution every time,
or PR-vs-baseline comparisons are meaningless. These tests pin that
property explicitly.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
import models  # noqa: F401 — registers tables with Base
from tests.fixtures.session_telemetry_volume import (
    DEFAULT_NULL_PROVIDER_FRACTION,
    VolumeShape,
    seed_session_telemetry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine():
    """Tmp in-memory SQLite engine with the full ORM schema."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    database.Base.metadata.create_all(bind=engine)
    return engine


def _row_distribution(engine) -> dict:
    """Snapshot the seeded distribution: useful for equality assertions."""
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = Session()
    try:
        total = session.execute(
            text("SELECT COUNT(*) FROM session_telemetry")
        ).scalar()
        null_providers = session.execute(
            text(
                "SELECT COUNT(*) FROM session_telemetry WHERE provider_id IS NULL"
            )
        ).scalar()
        distinct_users = session.execute(
            text("SELECT COUNT(DISTINCT user_id) FROM session_telemetry")
        ).scalar()
        distinct_channels = session.execute(
            text("SELECT COUNT(DISTINCT channel_id) FROM session_telemetry")
        ).scalar()
        distinct_providers = session.execute(
            text(
                "SELECT COUNT(DISTINCT provider_id) FROM session_telemetry "
                "WHERE provider_id IS NOT NULL"
            )
        ).scalar()
        # Per-provider histogram (sorted by provider_id; NULL excluded).
        provider_hist = dict(
            session.execute(
                text(
                    "SELECT provider_id, COUNT(*) FROM session_telemetry "
                    "WHERE provider_id IS NOT NULL "
                    "GROUP BY provider_id ORDER BY provider_id"
                )
            ).fetchall()
        )
        # First / last 5 session_ids by row order — a strong determinism fingerprint.
        head_sessions = [
            r[0]
            for r in session.execute(
                text(
                    "SELECT session_id FROM session_telemetry "
                    "ORDER BY id ASC LIMIT 5"
                )
            ).fetchall()
        ]
        return {
            "total": total,
            "null_providers": null_providers,
            "distinct_users": distinct_users,
            "distinct_channels": distinct_channels,
            "distinct_providers": distinct_providers,
            "provider_hist": provider_hist,
            "head_sessions": head_sessions,
        }
    finally:
        session.close()


# Small, fast shape for unit-test determinism checks — full 5M is exercised
# in tests/performance/ (benchmark) and tests/integration/ (migration gate).
_SMALL_SHAPE = VolumeShape(row_count=5_000, batch_size=1_000, user_count=10)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestVolumeFixtureDeterminism:
    """Same seed → byte-for-byte identical row distribution. This property
    is what makes the pytest-benchmark gate trustworthy: a PR cannot move
    p95 by changing the seeded data, only by changing the query plan.
    """

    def test_same_seed_produces_identical_distribution(self):
        engine_a = _make_engine()
        engine_b = _make_engine()
        try:
            with engine_a.connect() as conn:
                seed_session_telemetry(conn, _SMALL_SHAPE)
            with engine_b.connect() as conn:
                seed_session_telemetry(conn, _SMALL_SHAPE)

            dist_a = _row_distribution(engine_a)
            dist_b = _row_distribution(engine_b)
            assert dist_a == dist_b, (
                "Same seed produced different distributions — the fixture is "
                "no longer deterministic. The benchmark baseline is invalid."
            )
        finally:
            engine_a.dispose()
            engine_b.dispose()

    def test_different_seeds_produce_different_distributions(self):
        """Sanity check: the seed actually influences output (negative
        control for the determinism test above)."""
        shape_a = _SMALL_SHAPE
        shape_b = VolumeShape(
            row_count=_SMALL_SHAPE.row_count,
            batch_size=_SMALL_SHAPE.batch_size,
            user_count=_SMALL_SHAPE.user_count,
            seed=_SMALL_SHAPE.seed + 1,
        )

        engine_a = _make_engine()
        engine_b = _make_engine()
        try:
            with engine_a.connect() as conn:
                seed_session_telemetry(conn, shape_a)
            with engine_b.connect() as conn:
                seed_session_telemetry(conn, shape_b)
            dist_a = _row_distribution(engine_a)
            dist_b = _row_distribution(engine_b)
            # Same total + same user count (those are deterministic shape
            # parameters, not seed-derived), but different head_sessions.
            assert dist_a["total"] == dist_b["total"]
            assert dist_a["head_sessions"] != dist_b["head_sessions"], (
                "Changing the seed did not change the row distribution — "
                "the RNG is being short-circuited somewhere."
            )
        finally:
            engine_a.dispose()
            engine_b.dispose()


# ---------------------------------------------------------------------------
# Shape contract — properties skqln.10 + skqln.16 benchmarks rely on
# ---------------------------------------------------------------------------


class TestVolumeFixtureShapeContract:
    """The fixture is exportable/reusable infrastructure. These tests pin
    the contract that downstream benchmark suites (watch-time + the 4
    GH-59 provider endpoints arriving in skqln.16) depend on.
    """

    @pytest.fixture
    def seeded_engine(self):
        engine = _make_engine()
        with engine.connect() as conn:
            seed_session_telemetry(conn, _SMALL_SHAPE)
        try:
            yield engine
        finally:
            engine.dispose()

    def test_exact_row_count_matches_shape(self, seeded_engine):
        dist = _row_distribution(seeded_engine)
        assert dist["total"] == _SMALL_SHAPE.row_count

    def test_null_provider_fraction_in_expected_range(self, seeded_engine):
        """~5% NULL provider_id rows — exercises the 'Unknown' bucket path
        the GH-59 provider aggregates have to handle.
        """
        dist = _row_distribution(seeded_engine)
        fraction = dist["null_providers"] / dist["total"]
        target = DEFAULT_NULL_PROVIDER_FRACTION
        # ±2 percentage-point envelope for a 5k-row sample (binomial noise).
        assert abs(fraction - target) < 0.02, (
            f"NULL-provider fraction {fraction:.3f} drifted from target "
            f"{target} — the 'Unknown' bucket path won't be exercised."
        )

    def test_zipfish_provider_skew_present(self, seeded_engine):
        """Provider 1 should dominate provider 6 (Zipf-ish weights 1/k).
        Locks in the multi-provider distribution skqln.16 needs.
        """
        dist = _row_distribution(seeded_engine)
        hist = dist["provider_hist"]
        # All providers in 1..provider_count should be represented.
        assert set(hist.keys()) == set(
            range(1, _SMALL_SHAPE.provider_count + 1)
        ), f"Expected providers 1..{_SMALL_SHAPE.provider_count}, got {sorted(hist)}"
        # Zipf-ish skew: lowest-id provider has the most rows.
        assert hist[1] > hist[_SMALL_SHAPE.provider_count], (
            f"Expected Zipf-ish skew (provider 1 > provider "
            f"{_SMALL_SHAPE.provider_count}); got hist={hist}"
        )
        # The ratio should be at least ~2x — 1/1 vs 1/6 weights ≈ 6x in
        # expectation, but 5k samples have noise. 2x is a safe floor.
        assert hist[1] >= 2 * hist[_SMALL_SHAPE.provider_count], (
            f"Skew too shallow — provider 1 should dominate the long tail. "
            f"hist={hist}"
        )

    def test_user_id_range_matches_shape(self, seeded_engine):
        dist = _row_distribution(seeded_engine)
        # With 10 users and 5k rows, all 10 should be hit.
        assert dist["distinct_users"] == _SMALL_SHAPE.user_count

    def test_check_constraint_holds_for_seeded_data(self, seeded_engine):
        """``bytes_delta`` is always >= 0 — the CHECK constraint from
        migration 0006. If this fails, an insert would have raised."""
        Session = sessionmaker(bind=seeded_engine, autoflush=False)
        session = Session()
        try:
            min_bytes = session.execute(
                text("SELECT MIN(bytes_delta) FROM session_telemetry")
            ).scalar()
            assert min_bytes >= 0, (
                f"Seeded data violates bytes_delta CHECK: min={min_bytes}"
            )
        finally:
            session.close()
