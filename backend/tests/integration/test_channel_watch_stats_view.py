"""Read-equivalence regression test for ``channel_watch_stats_v`` (bd-skqln.3 step (b)).

The view is a scoped-down read-compat shim over ``session_telemetry`` that
exposes the subset of legacy ``channel_watch_stats`` columns that faithfully
map from per-poll telemetry to per-channel aggregates:

* ``channel_id`` — direct passthrough.
* ``total_watch_seconds`` — sum of distinct poll intervals per channel.
* ``last_watched`` — MAX(observed_at) converted to DATETIME.

Omitted by design (see migration 0008's docstring):
* ``channel_name`` — not present in ``session_telemetry``.
* ``watch_count`` — state-transition counter, not derivable from per-poll rows.

Equivalence-test strategy:

1. Spin up a fresh SQLite DB via alembic upgrade head — this exercises the
   real migration 0008 DDL, not a hand-rolled CREATE VIEW.
2. Seed matching telemetry rows into ``session_telemetry`` AND matching
   rollup rows into ``channel_watch_stats`` (the same channel_ids,
   pre-computed totals that the view should reproduce).
3. Run a query equivalent to ``popularity_calculator._gather_metrics``
   (the only reader of ``channel_watch_stats`` filtered by ``last_watched
   >= start_date``) against both surfaces.
4. Assert: same channel-id set, same total_watch_seconds, same
   last_watched ordering (within the mapped subset of columns).

Why a real alembic-driven DB and not the in-memory ``test_engine``:
``test_engine`` uses ``Base.metadata.create_all()`` which does NOT execute
migration DDL. The view exists only as a result of running migration 0008,
so a meaningful regression test must use ``alembic upgrade head``.

Bead: ``enhancedchannelmanager-skqln.3`` step (b).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import database


def _make_alembic_config(db_url: str):
    """Build an Alembic Config pinned to the given SQLite URL."""
    from alembic.config import Config

    ini_path = Path(database.ALEMBIC_INI_PATH)
    assert ini_path.exists(), f"alembic.ini missing at {ini_path}"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _seed_matched_data(engine, channels: list[dict]) -> None:
    """Seed both surfaces with matching data.

    For each channel entry in ``channels``:

    * Insert ``poll_count`` rows into ``session_telemetry`` with the same
      ``channel_id`` and one row per ``observed_at`` step (single client,
      so no DISTINCT collapse — the view computes the same total as the
      naive SUM in this single-client case).
    * Insert one row into ``channel_watch_stats`` with the equivalent
      ``total_watch_seconds`` = ``poll_count * (poll_interval_ms / 1000)``
      and ``last_watched`` = max observed_at converted to datetime.
    """
    import models

    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = SessionLocal()
    try:
        # Seed a synthetic User so session_telemetry.user_id has a valid FK
        # parent if needed (we leave user_id NULL in these rows, but having
        # an active users row is harmless).
        synthetic_user = models.User(
            id=999,
            username="synthetic-skqln3-stepb",
            email="synthetic-skqln3-stepb@example.invalid",
            auth_provider="local",
            is_active=True,
        )
        session.add(synthetic_user)
        session.commit()

        for ch in channels:
            channel_id: str = ch["channel_id"]
            poll_count: int = ch["poll_count"]
            poll_interval_ms: int = ch["poll_interval_ms"]
            base_observed_at_ms: int = ch["base_observed_at_ms"]

            # session_telemetry rows: poll_count distinct observed_at values.
            for i in range(poll_count):
                observed_at = base_observed_at_ms + i * poll_interval_ms
                session.add(
                    models.SessionTelemetry(
                        session_id=f"sess-{channel_id}-{i // 10}",
                        observed_at=observed_at,
                        user_id=None,  # equivalence does not require a user
                        provider_id=None,
                        channel_id=channel_id,
                        bytes_delta=1000 * (i + 1),
                        buffer_event_count=0,
                        poll_interval_ms=poll_interval_ms,
                    )
                )

            # channel_watch_stats: matching pre-computed aggregate.
            last_observed_ms = base_observed_at_ms + (poll_count - 1) * poll_interval_ms
            # Convert last_observed_ms → naive UTC datetime, mirroring the
            # SQLite datetime() function the view uses (unixepoch → UTC).
            last_watched_dt = datetime.utcfromtimestamp(last_observed_ms / 1000.0)
            total_watch_seconds = poll_count * (poll_interval_ms // 1000)

            session.add(
                models.ChannelWatchStats(
                    channel_id=channel_id,
                    channel_name=ch["channel_name"],
                    watch_count=ch.get("watch_count", poll_count),  # legacy-only
                    total_watch_seconds=total_watch_seconds,
                    last_watched=last_watched_dt,
                )
            )

        session.commit()
    finally:
        session.close()


@pytest.mark.integration
class TestChannelWatchStatsViewEquivalence:
    """View must return the same channel set / total_watch_seconds / last_watched
    ordering as the legacy ``channel_watch_stats`` table for matched seed data.

    Restricted to the columns the view exposes (channel_id, total_watch_seconds,
    last_watched). The legacy table's channel_name and watch_count are NOT
    covered by this regression — see migration 0008's docstring for the
    scoped-down rationale.
    """

    def test_view_is_created_at_head(self, tmp_path):
        """Sanity: ``alembic upgrade head`` creates the view."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'view_smoke.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            # SQLite distinguishes tables from views in sqlite_master.type.
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='view' AND name='channel_watch_stats_v'"
                )).fetchall()
            assert len(rows) == 1, (
                "channel_watch_stats_v view not present after upgrade head"
            )
        finally:
            engine.dispose()

    def test_view_round_trip_drops_and_recreates(self, tmp_path):
        """Downgrade 0008 → upgrade 0008 leaves the view present (and identical).

        Catches the case where the migration's DDL references a column or
        construct that doesn't survive a downgrade/re-upgrade cycle on
        SQLite.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'view_roundtrip.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "head")
        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                pre_sql = conn.execute(text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='view' AND name='channel_watch_stats_v'"
                )).scalar()
            assert pre_sql is not None
        finally:
            engine.dispose()

        command.downgrade(cfg, "0007")
        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='view' AND name='channel_watch_stats_v'"
                )).fetchall()
            assert rows == [], (
                "channel_watch_stats_v view should be dropped at revision 0007"
            )
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")
        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                post_sql = conn.execute(text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='view' AND name='channel_watch_stats_v'"
                )).scalar()
            assert post_sql == pre_sql, (
                "View DDL changed across downgrade/upgrade cycle:\n"
                f"  before: {pre_sql!r}\n  after:  {post_sql!r}"
            )
        finally:
            engine.dispose()

    def test_view_read_equivalent_to_legacy_table(self, tmp_path):
        """Reading the mapped columns through the view must match the legacy
        ``channel_watch_stats`` table when both are seeded with the same data.

        Equivalence is restricted to ``channel_id``, ``total_watch_seconds``,
        and ``last_watched`` — the columns migration 0008 deliberately exposes.
        ``channel_name`` and ``watch_count`` are out of scope for the view.

        Reader pattern modeled on ``popularity_calculator._gather_metrics``
        (line 209-221 of ``backend/popularity_calculator.py``):
        ``SELECT ... WHERE last_watched >= start_date``.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'view_equiv.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        # Realistic shape: three channels, varying poll counts. Base
        # observed_at far enough in the past that the `last_watched >=
        # start_date` filter still includes them (use a "now" reference
        # the test controls).
        now_ms = 1_700_000_000_000  # fixed reference, 2023-11-14 UTC-ish
        poll_interval_ms = 10_000  # 10s polls — matches default
        channels = [
            {
                "channel_id": "ch-uuid-aaa",
                "channel_name": "Alpha",
                "poll_count": 30,  # 30 polls × 10s = 300s
                "poll_interval_ms": poll_interval_ms,
                # most-recently watched: ends at now_ms - 10*poll_interval
                "base_observed_at_ms": now_ms - 39 * poll_interval_ms,
                "watch_count": 5,
            },
            {
                "channel_id": "ch-uuid-bbb",
                "channel_name": "Bravo",
                "poll_count": 12,  # 12 polls × 10s = 120s
                "poll_interval_ms": poll_interval_ms,
                "base_observed_at_ms": now_ms - 200 * poll_interval_ms,
                "watch_count": 3,
            },
            {
                "channel_id": "ch-uuid-ccc",
                "channel_name": "Charlie",
                "poll_count": 5,
                "poll_interval_ms": poll_interval_ms,
                "base_observed_at_ms": now_ms - 100 * poll_interval_ms,
                "watch_count": 2,
            },
        ]

        engine = create_engine(db_url, future=True)
        try:
            _seed_matched_data(engine, channels)

            # Filter by last_watched: pick a start_date that includes all
            # three. Use a datetime slightly older than the earliest seed.
            start_date_dt = datetime.utcfromtimestamp(
                (now_ms - 300 * poll_interval_ms) / 1000.0
            )

            # Legacy read — mirrors popularity_calculator._gather_metrics
            # WHERE clause.
            with engine.connect() as conn:
                legacy_rows = conn.execute(
                    text(
                        "SELECT channel_id, total_watch_seconds, last_watched "
                        "FROM channel_watch_stats "
                        "WHERE last_watched >= :start_date "
                        "ORDER BY channel_id"
                    ),
                    {"start_date": start_date_dt.strftime("%Y-%m-%d %H:%M:%S")},
                ).fetchall()

                view_rows = conn.execute(
                    text(
                        "SELECT channel_id, total_watch_seconds, last_watched "
                        "FROM channel_watch_stats_v "
                        "WHERE last_watched >= :start_date "
                        "ORDER BY channel_id"
                    ),
                    {"start_date": start_date_dt.strftime("%Y-%m-%d %H:%M:%S")},
                ).fetchall()

            # Same channel set.
            legacy_channel_ids = {r[0] for r in legacy_rows}
            view_channel_ids = {r[0] for r in view_rows}
            assert legacy_channel_ids == view_channel_ids, (
                f"View returns different channel set than legacy table:\n"
                f"  legacy: {sorted(legacy_channel_ids)}\n"
                f"  view:   {sorted(view_channel_ids)}"
            )

            # Same row count, same ordering.
            assert len(legacy_rows) == len(view_rows) == 3
            for legacy, view in zip(legacy_rows, view_rows):
                # channel_id matches.
                assert legacy[0] == view[0], (
                    f"channel_id mismatch: legacy={legacy[0]!r} view={view[0]!r}"
                )
                # total_watch_seconds matches exactly.
                assert legacy[1] == view[1], (
                    f"total_watch_seconds mismatch for {legacy[0]!r}: "
                    f"legacy={legacy[1]} view={view[1]}"
                )
                # last_watched matches as a parsed datetime (SQLite stores
                # DATETIME columns as strings; the legacy row's value comes
                # from a Python datetime serialised by SQLAlchemy, while the
                # view's value comes from datetime(unixepoch). They are
                # equivalent in seconds but format may have a different
                # microsecond suffix — compare at second resolution).
                legacy_dt = _parse_sqlite_datetime(legacy[2])
                view_dt = _parse_sqlite_datetime(view[2])
                assert legacy_dt.replace(microsecond=0) == view_dt.replace(
                    microsecond=0
                ), (
                    f"last_watched mismatch for {legacy[0]!r}: "
                    f"legacy={legacy[2]!r} view={view[2]!r}"
                )
        finally:
            engine.dispose()

    def test_view_collapses_multiple_clients_into_one_poll(self, tmp_path):
        """A channel with N concurrent clients in one poll must contribute
        only one poll interval to total_watch_seconds — matching the legacy
        ``_update_watch_time`` semantic (which adds ``self.poll_interval``
        once per channel per still-active poll regardless of client count).

        This is the subtlety that necessitates the DISTINCT-by-(channel,
        observed_at) subquery inside the view. A naive
        ``SUM(poll_interval_ms) GROUP BY channel_id`` would multiply by
        client count and silently overcount watch time by 2x-10x.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'view_collapse.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        poll_interval_ms = 10_000
        observed_at_ms = 1_700_000_000_000

        engine = create_engine(db_url, future=True)
        try:
            import models

            SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
            session = SessionLocal()
            try:
                # Three "clients" on the same channel in the SAME poll —
                # three rows, all with the same observed_at + channel_id.
                # The view must report total_watch_seconds = 10 (one poll
                # interval), NOT 30 (3 × interval).
                for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
                    session.add(
                        models.SessionTelemetry(
                            session_id=f"sess-{ip}",
                            observed_at=observed_at_ms,
                            user_id=None,
                            provider_id=None,
                            channel_id="ch-uuid-multi",
                            bytes_delta=1000,
                            buffer_event_count=0,
                            poll_interval_ms=poll_interval_ms,
                        )
                    )
                session.commit()

                row = session.execute(text(
                    "SELECT channel_id, total_watch_seconds "
                    "FROM channel_watch_stats_v "
                    "WHERE channel_id = 'ch-uuid-multi'"
                )).fetchone()
                assert row is not None, "View returned no row for multi-client channel"
                # 10s — one poll interval, not 30s (which would be the bug).
                assert row[1] == 10, (
                    f"total_watch_seconds for multi-client channel should be 10s "
                    f"(one poll interval), got {row[1]}. The view's DISTINCT-by-"
                    f"(channel, observed_at) subquery may be broken — see "
                    f"migration 0008's CHANNEL_WATCH_STATS_V_SQL."
                )
            finally:
                session.close()
        finally:
            engine.dispose()


def _parse_sqlite_datetime(value):
    """Parse a SQLite-stored DATETIME string into a Python datetime.

    SQLite stores DATETIMEs as ISO-format strings. SQLAlchemy writes them as
    ``YYYY-MM-DD HH:MM:SS.ffffff``; the SQLite ``datetime()`` function writes
    them as ``YYYY-MM-DD HH:MM:SS`` (no microseconds). Try both.
    """
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise AssertionError(f"Cannot parse SQLite datetime: {value!r}")
