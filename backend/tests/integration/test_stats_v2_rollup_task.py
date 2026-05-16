"""Integration tests for the Stats v2 nightly rollup + prune task.

Bead: ``enhancedchannelmanager-7i2vv``.

Covers the ``tasks.stats_v2_rollup.StatsV2RollupTask`` implementation of
ADR-007 D3-D6. Two flavors of test:

1. **Aggregation correctness** — seed ``session_telemetry`` with known
   per-(provider, user, channel, day) rows, run the task, assert the
   rollup totals match the raw totals across the day. Covers the
   data-correctness invariant the briefing names explicitly:

       pre-rollup  SUM(bytes_delta) WHERE observed_at::day = X
   ≡   post-rollup SUM(bytes_delta_sum) FROM session_telemetry_provider_daily
                   WHERE day = X

   plus the per-poll-DISTINCT collapse for watch_seconds (same shape the
   channel_watch_stats_v view uses, skqln.3 step (b)).

2. **Operational behavior** — idempotency on re-run, failure-path marker
   state, prune-skipped-on-rollup-failure, metric emission. These cover
   the failure modes ADR-007 D6 numbers 1-5, plus the metric contract
   from the briefing.

The task uses an in-process SQLite engine — same fixture pattern the
sibling session_telemetry migration tests use. All identities are
synthetic (``docs/security/threat_model_stats_v2.md`` §7.7).
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import database
import observability


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

ROLLUP_NAME_USER_DAILY = "user_daily"
ROLLUP_NAME_PROVIDER_DAILY = "provider_daily"

# A frozen "now" used by the tests so the prune predicate's 30-day cutoff
# is deterministic. Picked far enough in the future that all the seed
# rows are within the 30-day window unless the test deliberately ages
# them out.
FROZEN_NOW = datetime(2026, 6, 15, 3, 30, 0, tzinfo=timezone.utc)


def _make_alembic_config(db_url: str):
    from alembic.config import Config

    ini_path = Path(database.ALEMBIC_INI_PATH)
    assert ini_path.exists(), f"alembic.ini missing at {ini_path}"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture
def _reset_metrics():
    """Rebuild the metric registry per test so counter/gauge values don't
    leak across cases."""
    observability.reset_for_tests()
    observability.install_metrics()
    yield
    observability.reset_for_tests()


@pytest.fixture
def rollup_db(tmp_path, _reset_metrics):
    """Provision a SQLite DB at Alembic ``head`` and yield a (session, engine)
    pair plus the absolute path so the test can also issue raw SQL.

    The task's implementation uses ``database.get_session()``; we patch that
    function to return sessions bound to this engine so the task writes
    against the test DB.
    """
    from alembic import command

    db_url = f"sqlite:///{tmp_path / 'rollup_task.db'}"
    cfg = _make_alembic_config(db_url)
    command.upgrade(cfg, "head")

    engine = create_engine(db_url, future=True)
    # FK enforcement is enabled globally by database.py's connect listener.
    # The rollup tests don't seed users rows (synthetic IDs only), so any
    # session_telemetry insert would trip the ON DELETE SET NULL FK to
    # users.id. Disable PRAGMA foreign_keys on this engine's connections —
    # the FK semantics are covered by test_session_telemetry_migration.py.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _disable_fks(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.close()

    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield engine, Session

    engine.dispose()


def _seed_session_telemetry(
    Session, rows: list[dict]
) -> None:
    """Insert a list of session_telemetry row dicts via raw SQL.

    Using raw INSERTs (not the ORM) so the test can stamp arbitrary
    observed_at ms values and exercise NULL user_id / NULL provider_id
    edge cases without ORM-side defaulting.

    Per-type event counter columns (``reconnect_event_count``,
    ``error_event_count``, ``switch_event_count``) default to 0 when
    absent from the row dict — callers that only care about bytes/buffer
    need not change.
    """
    session = Session()
    try:
        for row in rows:
            session.execute(
                text(
                    "INSERT INTO session_telemetry "
                    "(session_id, observed_at, user_id, provider_id, "
                    " channel_id, bytes_delta, buffer_event_count, "
                    " reconnect_event_count, error_event_count, "
                    " switch_event_count, poll_interval_ms) "
                    "VALUES (:session_id, :observed_at, :user_id, "
                    "        :provider_id, :channel_id, :bytes_delta, "
                    "        :buffer_event_count, :reconnect_event_count, "
                    "        :error_event_count, :switch_event_count, "
                    "        :poll_interval_ms)"
                ),
                {
                    "reconnect_event_count": 0,
                    "error_event_count": 0,
                    "switch_event_count": 0,
                    **row,
                },
            )
        session.commit()
    finally:
        session.close()


def _ms_at_utc(dt: datetime) -> int:
    """Convert a UTC datetime to unix-ms."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _run_task(Session, **kwargs):
    """Instantiate ``StatsV2RollupTask`` with database.get_session patched to
    return a session against the test engine, and invoke its execute()
    coroutine synchronously.

    Returns the TaskResult.

    Note on event-loop handling: this helper uses an explicit
    ``new_event_loop()`` + ``run_until_complete()`` rather than
    ``asyncio.run()`` because the latter closes the loop and unbinds it
    from the thread, which breaks downstream tests in
    ``test_auto_creation_engine.py`` that call the deprecated
    ``asyncio.get_event_loop()`` pattern. We restore the original loop
    (or None) after completion so the suite's test ordering remains
    insensitive to ours.
    """
    import asyncio
    from tasks.stats_v2_rollup import StatsV2RollupTask

    task = StatsV2RollupTask()
    # The task reads any kwargs (e.g., now_utc) off the instance — set
    # them after construction. The production task reads
    # datetime.utcnow() at runtime; tests stub it via now_utc.
    for k, v in kwargs.items():
        setattr(task, k, v)

    try:
        previous_loop = asyncio.get_event_loop()
    except RuntimeError:
        previous_loop = None

    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    try:
        with patch("tasks.stats_v2_rollup.get_session", lambda: Session()):
            result = new_loop.run_until_complete(task.execute())
    finally:
        new_loop.close()
        # Restore whatever loop (or absence-of-loop) was current before.
        asyncio.set_event_loop(previous_loop)
    return result, task


# ---------------------------------------------------------------------------
# 1. AGGREGATION CORRECTNESS
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestRollupCorrectness:
    """Pre-rollup raw totals must equal post-rollup rollup totals."""

    def test_provider_rollup_bytes_delta_sum_matches_raw_total(
        self, rollup_db
    ):
        """SUM(bytes_delta) raw ≡ SUM(bytes_delta_sum) rollup, per day."""
        engine, Session = rollup_db

        # Day to roll up: 2 days before FROZEN_NOW. (Yesterday is incomplete
        # in the production cron; the task only rolls up complete UTC days.)
        target_day = (FROZEN_NOW.date() - timedelta(days=2))

        # Seed: two providers × two channels × five polls/day = 20 rows.
        rows = []
        for provider_id in (1, 2):
            for channel_id in ("ch-corr-A", "ch-corr-B"):
                for poll in range(5):
                    obs = datetime.combine(
                        target_day, datetime.min.time(), tzinfo=timezone.utc
                    ) + timedelta(hours=10, minutes=poll * 2)
                    rows.append({
                        "session_id": f"sess-{provider_id}-{channel_id}-{poll}",
                        "observed_at": _ms_at_utc(obs),
                        "user_id": 100,
                        "provider_id": provider_id,
                        "channel_id": channel_id,
                        "bytes_delta": 1_000_000 * (poll + 1),  # 1M, 2M, 3M, 4M, 5M
                        "buffer_event_count": poll % 2,
                        "poll_interval_ms": 10_000,
                    })

        _seed_session_telemetry(Session, rows)

        # Run rollup with frozen now.
        result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert result.success, f"task failed: {result.message!r}"

        # Verify: per-day raw total == per-day rollup total.
        with engine.connect() as conn:
            raw_total = conn.execute(text(
                "SELECT COALESCE(SUM(bytes_delta), 0) FROM session_telemetry "
                "WHERE date(observed_at / 1000, 'unixepoch') = :day"
            ), {"day": target_day.isoformat()}).scalar()

            rollup_total = conn.execute(text(
                "SELECT COALESCE(SUM(bytes_delta_sum), 0) FROM "
                "session_telemetry_provider_daily WHERE day = :day"
            ), {"day": target_day.isoformat()}).scalar()

        assert raw_total == rollup_total, (
            f"raw bytes_delta total {raw_total} != rollup bytes_delta_sum "
            f"total {rollup_total} for day {target_day}"
        )

    def test_user_rollup_watch_seconds_uses_distinct_poll_collapse(
        self, rollup_db
    ):
        """watch_seconds collapses (channel_id, observed_at) duplicates.

        The same DISTINCT-poll collapse the channel_watch_stats_v view uses
        (skqln.3 step (b)): a channel with N concurrent clients in one poll
        contributes ONE poll interval to watch_seconds, not N.
        """
        engine, Session = rollup_db

        target_day = (FROZEN_NOW.date() - timedelta(days=2))
        base_obs = datetime.combine(
            target_day, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=12)

        # User 7 watched channel ch-collapse: 3 polls, each with 4 concurrent
        # clients (4 raw rows per poll, same observed_at, same channel).
        # Expected watch_seconds = 3 polls × 10s/poll = 30 (not 120).
        rows = []
        for poll in range(3):
            obs_ms = _ms_at_utc(base_obs + timedelta(seconds=poll * 10))
            for client in range(4):
                rows.append({
                    "session_id": f"sess-collapse-{poll}-{client}",
                    "observed_at": obs_ms,
                    "user_id": 7,
                    "provider_id": 1,
                    "channel_id": "ch-collapse",
                    "bytes_delta": 100,
                    "buffer_event_count": 0,
                    "poll_interval_ms": 10_000,
                })
        _seed_session_telemetry(Session, rows)

        result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert result.success

        with engine.connect() as conn:
            ws = conn.execute(text(
                "SELECT watch_seconds FROM session_telemetry_user_daily "
                "WHERE user_id = 7 AND channel_id = 'ch-collapse' "
                "  AND day = :day"
            ), {"day": target_day.isoformat()}).scalar()
        assert ws == 30, (
            f"expected 30s (3 distinct polls × 10s), got {ws}s — the "
            f"per-poll-per-client multiplicity was not collapsed"
        )

    def test_null_provider_id_lands_in_unknown_bucket(self, rollup_db):
        """Raw NULL provider_id surfaces as the 'unknown' string bucket.

        ADR-007 §line 109: the resolver miss must NOT be silently dropped.
        """
        engine, Session = rollup_db

        target_day = (FROZEN_NOW.date() - timedelta(days=2))
        base_obs = datetime.combine(
            target_day, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=14)

        rows = [
            {
                "session_id": f"sess-null-prov-{i}",
                "observed_at": _ms_at_utc(base_obs + timedelta(seconds=i * 10)),
                "user_id": 5,
                "provider_id": None,
                "channel_id": "ch-prov-miss",
                "bytes_delta": 2_000_000,
                "buffer_event_count": 1,
                "poll_interval_ms": 10_000,
            }
            for i in range(3)
        ]
        _seed_session_telemetry(Session, rows)

        result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert result.success

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT provider_id, watch_seconds, bytes_delta_sum, "
                "       buffer_event_count "
                "FROM session_telemetry_provider_daily "
                "WHERE channel_id = 'ch-prov-miss' AND day = :day"
            ), {"day": target_day.isoformat()}).fetchall()

        assert row == [("unknown", 30, 6_000_000, 3)], (
            f"NULL provider_id was not bucketed as 'unknown': {row}"
        )

    def test_null_user_id_is_excluded_from_user_rollup(self, rollup_db):
        """Raw NULL user_id does NOT appear in the user_daily rollup.

        Per the model docstring: there is no behavioral subject to
        attribute, so no row is written. This is the per-user analog of
        the per-provider 'unknown' bucket — but for the per-user case the
        rollup PK is INTEGER and a fabricated sentinel would pollute the
        ``users`` namespace, so the rollup excludes instead of buckets.
        The data still flows through the *provider* rollup (which DOES
        bucket NULL provider_id), so no information is lost — just not
        attributable to a user.
        """
        engine, Session = rollup_db

        target_day = (FROZEN_NOW.date() - timedelta(days=2))
        base_obs = datetime.combine(
            target_day, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=15)

        # Three NULL-user rows + one real-user row (user_id=42).
        rows = [
            {
                "session_id": f"sess-null-user-{i}",
                "observed_at": _ms_at_utc(base_obs + timedelta(seconds=i * 10)),
                "user_id": None,
                "provider_id": 1,
                "channel_id": "ch-anon",
                "bytes_delta": 500_000,
                "buffer_event_count": 0,
                "poll_interval_ms": 10_000,
            }
            for i in range(3)
        ] + [
            {
                "session_id": "sess-real",
                "observed_at": _ms_at_utc(base_obs + timedelta(seconds=40)),
                "user_id": 42,
                "provider_id": 1,
                "channel_id": "ch-anon",
                "bytes_delta": 500_000,
                "buffer_event_count": 0,
                "poll_interval_ms": 10_000,
            }
        ]
        _seed_session_telemetry(Session, rows)

        result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert result.success

        with engine.connect() as conn:
            user_rows = conn.execute(text(
                "SELECT user_id, watch_seconds FROM "
                "session_telemetry_user_daily WHERE day = :day "
                "ORDER BY user_id"
            ), {"day": target_day.isoformat()}).fetchall()

        # Only user 42's row should be present.
        assert user_rows == [(42, 10)], (
            f"NULL user_id was not excluded from user_daily rollup: "
            f"{user_rows}"
        )

    def test_session_count_is_distinct_session_ids(self, rollup_db):
        """session_count = distinct session_ids per (user, channel, day)."""
        engine, Session = rollup_db

        target_day = (FROZEN_NOW.date() - timedelta(days=2))
        base_obs = datetime.combine(
            target_day, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=16)

        # User 9, channel ch-sessions: 2 distinct sessions (A, B); session
        # A has 3 polls, session B has 2 polls.
        rows = []
        for poll in range(3):
            rows.append({
                "session_id": "sess-A",
                "observed_at": _ms_at_utc(base_obs + timedelta(seconds=poll * 10)),
                "user_id": 9,
                "provider_id": 1,
                "channel_id": "ch-sessions",
                "bytes_delta": 100,
                "buffer_event_count": 0,
                "poll_interval_ms": 10_000,
            })
        for poll in range(2):
            rows.append({
                "session_id": "sess-B",
                "observed_at": _ms_at_utc(base_obs + timedelta(seconds=60 + poll * 10)),
                "user_id": 9,
                "provider_id": 1,
                "channel_id": "ch-sessions",
                "bytes_delta": 100,
                "buffer_event_count": 0,
                "poll_interval_ms": 10_000,
            })
        _seed_session_telemetry(Session, rows)

        result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert result.success

        with engine.connect() as conn:
            sc = conn.execute(text(
                "SELECT session_count FROM session_telemetry_user_daily "
                "WHERE user_id = 9 AND channel_id = 'ch-sessions' "
                "  AND day = :day"
            ), {"day": target_day.isoformat()}).scalar()

        assert sc == 2, f"expected 2 distinct sessions, got {sc}"


# ---------------------------------------------------------------------------
# 2. OPERATIONAL BEHAVIOR
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestRollupIdempotency:
    """Re-running the task on the same day updates (not duplicates) rows."""

    def test_rerun_does_not_duplicate_rows(self, rollup_db):
        engine, Session = rollup_db

        target_day = (FROZEN_NOW.date() - timedelta(days=2))
        base_obs = datetime.combine(
            target_day, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=17)

        rows = [{
            "session_id": "sess-idem",
            "observed_at": _ms_at_utc(base_obs + timedelta(seconds=i * 10)),
            "user_id": 11,
            "provider_id": 3,
            "channel_id": "ch-idem",
            "bytes_delta": 1_000,
            "buffer_event_count": 0,
            "poll_interval_ms": 10_000,
        } for i in range(4)]
        _seed_session_telemetry(Session, rows)

        # First run.
        r1, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert r1.success
        # Second run — should be a no-op-ish: row count unchanged, values
        # unchanged.
        r2, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert r2.success

        with engine.connect() as conn:
            user_rows = conn.execute(text(
                "SELECT COUNT(*) FROM session_telemetry_user_daily"
            )).scalar()
            provider_rows = conn.execute(text(
                "SELECT COUNT(*) FROM session_telemetry_provider_daily"
            )).scalar()

        # Exactly one (user, channel, day) row + one (provider, channel, day).
        assert user_rows == 1, (
            f"re-run duplicated rows in user_daily: {user_rows}"
        )
        assert provider_rows == 1, (
            f"re-run duplicated rows in provider_daily: {provider_rows}"
        )


@pytest.mark.integration
class TestRollupFailurePath:
    """A failure during rollup populates telemetry_rollup_state with error."""

    def test_rollup_sql_error_records_failure_in_marker(self, rollup_db):
        """If the rollup INSERT raises, the marker row records the error."""
        engine, Session = rollup_db

        target_day = (FROZEN_NOW.date() - timedelta(days=2))
        base_obs = datetime.combine(
            target_day, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=18)

        _seed_session_telemetry(Session, [{
            "session_id": "sess-fail",
            "observed_at": _ms_at_utc(base_obs),
            "user_id": 20,
            "provider_id": 1,
            "channel_id": "ch-fail",
            "bytes_delta": 100,
            "buffer_event_count": 0,
            "poll_interval_ms": 10_000,
        }])

        # Patch the rollup helper to raise — the simplest way to simulate
        # a DB error mid-run without corrupting the schema.
        import tasks.stats_v2_rollup as rollup_module

        def _raise(*args, **kwargs):
            raise RuntimeError("simulated DB failure")

        with patch.object(
            rollup_module, "_rollup_user_daily", side_effect=_raise
        ):
            result, _ = _run_task(Session, now_utc=FROZEN_NOW)

        assert not result.success, "task should report failure"

        # Marker row records the failure.
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT last_run_status, last_run_error FROM "
                "telemetry_rollup_state WHERE rollup_name = :n"
            ), {"n": ROLLUP_NAME_USER_DAILY}).fetchone()
        assert row is not None, "telemetry_rollup_state row was not written"
        assert row[0] == "failure", (
            f"expected last_run_status='failure', got {row[0]!r}"
        )
        assert "simulated DB failure" in (row[1] or ""), (
            f"error detail not captured: {row[1]!r}"
        )

    def test_prune_skipped_when_rollup_fails(self, rollup_db):
        """Failed rollup must NOT prune raw rows — never lose data."""
        engine, Session = rollup_db

        # Age a raw row well past the 30-day cutoff so it WOULD be pruned
        # if the guard fails.
        old_obs = FROZEN_NOW - timedelta(days=45)
        _seed_session_telemetry(Session, [{
            "session_id": "sess-old",
            "observed_at": _ms_at_utc(old_obs),
            "user_id": 21,
            "provider_id": 1,
            "channel_id": "ch-old",
            "bytes_delta": 100,
            "buffer_event_count": 0,
            "poll_interval_ms": 10_000,
        }])

        import tasks.stats_v2_rollup as rollup_module

        def _raise(*args, **kwargs):
            raise RuntimeError("simulated failure — prune must be skipped")

        with patch.object(
            rollup_module, "_rollup_user_daily", side_effect=_raise
        ):
            result, _ = _run_task(Session, now_utc=FROZEN_NOW)

        assert not result.success

        # The aged raw row must still be present (prune skipped).
        with engine.connect() as conn:
            cnt = conn.execute(text(
                "SELECT COUNT(*) FROM session_telemetry"
            )).scalar()
        assert cnt == 1, (
            f"prune ran despite rollup failure — aged raw rows lost! "
            f"remaining: {cnt}"
        )


@pytest.mark.integration
class TestRollupPrunes:
    """The prune step deletes raw rows older than the 30-day window."""

    def test_prune_deletes_aged_rows_when_rollup_succeeds(self, rollup_db):
        engine, Session = rollup_db

        # Two rows: one fresh (within 30d), one aged (>30d).
        fresh_day = FROZEN_NOW.date() - timedelta(days=2)
        fresh_obs = datetime.combine(
            fresh_day, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=12)
        old_obs = FROZEN_NOW - timedelta(days=45)

        _seed_session_telemetry(Session, [
            {
                "session_id": "sess-fresh",
                "observed_at": _ms_at_utc(fresh_obs),
                "user_id": 30,
                "provider_id": 1,
                "channel_id": "ch-fresh",
                "bytes_delta": 100,
                "buffer_event_count": 0,
                "poll_interval_ms": 10_000,
            },
            {
                "session_id": "sess-old",
                "observed_at": _ms_at_utc(old_obs),
                "user_id": 31,
                "provider_id": 1,
                "channel_id": "ch-old",
                "bytes_delta": 100,
                "buffer_event_count": 0,
                "poll_interval_ms": 10_000,
            },
        ])

        result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert result.success

        with engine.connect() as conn:
            remaining = conn.execute(text(
                "SELECT session_id FROM session_telemetry"
            )).fetchall()
        # The aged row is gone; the fresh row remains.
        session_ids = sorted(r[0] for r in remaining)
        assert session_ids == ["sess-fresh"], (
            f"prune did not delete aged row(s); remaining: {session_ids}"
        )


# ---------------------------------------------------------------------------
# 3. METRIC EMISSION
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestRollupMetrics:
    """The task emits the ADR-007 D6 metrics on each run."""

    def test_raw_rows_pruned_counter_increments(self, rollup_db):
        engine, Session = rollup_db

        old_obs = FROZEN_NOW - timedelta(days=45)
        _seed_session_telemetry(Session, [{
            "session_id": "sess-old-metric",
            "observed_at": _ms_at_utc(old_obs),
            "user_id": 40,
            "provider_id": 1,
            "channel_id": "ch-metric",
            "bytes_delta": 100,
            "buffer_event_count": 0,
            "poll_interval_ms": 10_000,
        }])

        counter = observability.get_metric("telemetry_raw_rows_pruned")
        before = counter._value.get()

        result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert result.success

        after = counter._value.get()
        assert after - before == 1, (
            f"expected ecm_telemetry_raw_rows_pruned to increment by 1, "
            f"got {before} → {after}"
        )

    def test_duration_histogram_observes_a_sample(self, rollup_db):
        engine, Session = rollup_db

        # No raw rows needed — the rollup runs (over zero days of work)
        # and the duration is still observed.
        result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert result.success

        hist = observability.get_metric("telemetry_rollup_duration_seconds")
        # Read the labeled (rollup_name=user_daily) _count via .collect().
        count = 0
        for family in hist.collect():
            for sample in family.samples:
                if sample.name.endswith("_count") and sample.labels.get(
                    "rollup_name"
                ) == ROLLUP_NAME_USER_DAILY:
                    count = int(sample.value)
        assert count >= 1, (
            f"telemetry_rollup_duration_seconds did not observe "
            f"user_daily sample (count={count})"
        )

    def test_last_success_timestamp_advances_on_success(self, rollup_db):
        engine, Session = rollup_db

        gauge = observability.get_metric("telemetry_rollup_last_success_timestamp")
        before = gauge.labels(rollup_name=ROLLUP_NAME_USER_DAILY)._value.get()

        result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert result.success

        after = gauge.labels(rollup_name=ROLLUP_NAME_USER_DAILY)._value.get()
        # FROZEN_NOW.timestamp() is far in the future relative to before
        # (which starts at 0 for a fresh registry).
        assert after > before, (
            f"last_success_timestamp did not advance: {before} → {after}"
        )
        # And it's roughly the FROZEN_NOW timestamp (rounded to seconds).
        assert abs(after - FROZEN_NOW.timestamp()) < 2.0, (
            f"last_success_timestamp diverges from FROZEN_NOW: "
            f"{after} vs {FROZEN_NOW.timestamp()}"
        )

    def test_errors_total_increments_on_rollup_failure(self, rollup_db):
        engine, Session = rollup_db

        # Seed a row so the rollup has work; then patch the rollup helper
        # to fail.
        target_day = (FROZEN_NOW.date() - timedelta(days=2))
        base_obs = datetime.combine(
            target_day, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=19)
        _seed_session_telemetry(Session, [{
            "session_id": "sess-err",
            "observed_at": _ms_at_utc(base_obs),
            "user_id": 50,
            "provider_id": 1,
            "channel_id": "ch-err",
            "bytes_delta": 100,
            "buffer_event_count": 0,
            "poll_interval_ms": 10_000,
        }])

        counter = observability.get_metric("telemetry_rollup_errors_total")
        before = counter.labels(phase="rollup")._value.get()

        import tasks.stats_v2_rollup as rollup_module

        def _raise(*args, **kwargs):
            raise RuntimeError("error metric test failure")

        with patch.object(
            rollup_module, "_rollup_user_daily", side_effect=_raise
        ):
            result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert not result.success

        after = counter.labels(phase="rollup")._value.get()
        assert after - before >= 1, (
            f"expected errors_total{{phase=rollup}} to increment, "
            f"got {before} → {after}"
        )


# ---------------------------------------------------------------------------
# 4. EVENT COUNTER ROLLUP CORRECTNESS (bd-d0ha9)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestRollupProviderDailyEventCounters:
    """Migration 0015 / bd-d0ha9: reconnect/error/switch counters are rolled up.

    The three per-type channel-event counters added to ``session_telemetry``
    by migration 0013 (bd-ov5vb) must appear in
    ``session_telemetry_provider_daily`` after the nightly rollup. The rollup
    uses the same per-poll-per-client SUM logic as ``bytes_delta`` and
    ``buffer_event_count`` — no DISTINCT collapse needed because these are
    per-poll-per-client samples.
    """

    def test_rollup_provider_daily_includes_reconnect_event_count(
        self, rollup_db
    ):
        """SUM(reconnect_event_count) from raw rows equals rollup column."""
        engine, Session = rollup_db

        target_day = FROZEN_NOW.date() - timedelta(days=2)
        base_obs = datetime.combine(
            target_day, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=8)

        # Seed: 4 rows with 3 reconnect events each for provider 10.
        rows = [
            {
                "session_id": f"sess-rcn-{i}",
                "observed_at": _ms_at_utc(base_obs + timedelta(seconds=i * 10)),
                "user_id": 60,
                "provider_id": 10,
                "channel_id": "ch-reconnect",
                "bytes_delta": 500,
                "buffer_event_count": 0,
                "reconnect_event_count": 3,
                "error_event_count": 0,
                "switch_event_count": 0,
                "poll_interval_ms": 10_000,
            }
            for i in range(4)
        ]
        _seed_session_telemetry(Session, rows)

        result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert result.success, f"task failed: {result.message!r}"

        with engine.connect() as conn:
            rollup_reconnect = conn.execute(text(
                "SELECT COALESCE(SUM(reconnect_event_count), 0) FROM "
                "session_telemetry_provider_daily WHERE day = :day"
            ), {"day": target_day.isoformat()}).scalar()

            raw_reconnect = conn.execute(text(
                "SELECT COALESCE(SUM(reconnect_event_count), 0) FROM "
                "session_telemetry WHERE "
                "date(observed_at / 1000, 'unixepoch') = :day"
            ), {"day": target_day.isoformat()}).scalar()

        assert rollup_reconnect == raw_reconnect, (
            f"reconnect_event_count rollup ({rollup_reconnect}) != "
            f"raw sum ({raw_reconnect}) for day {target_day}"
        )
        # Explicit: 4 rows × 3 reconnect events = 12
        assert rollup_reconnect == 12, (
            f"expected reconnect_event_count=12, got {rollup_reconnect}"
        )

    def test_rollup_provider_daily_includes_error_event_count(
        self, rollup_db
    ):
        """SUM(error_event_count) from raw rows equals rollup column."""
        engine, Session = rollup_db

        target_day = FROZEN_NOW.date() - timedelta(days=2)
        base_obs = datetime.combine(
            target_day, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=9)

        rows = [
            {
                "session_id": f"sess-err-{i}",
                "observed_at": _ms_at_utc(base_obs + timedelta(seconds=i * 10)),
                "user_id": 61,
                "provider_id": 11,
                "channel_id": "ch-error",
                "bytes_delta": 200,
                "buffer_event_count": 0,
                "reconnect_event_count": 0,
                "error_event_count": 2,
                "switch_event_count": 0,
                "poll_interval_ms": 10_000,
            }
            for i in range(5)
        ]
        _seed_session_telemetry(Session, rows)

        result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert result.success, f"task failed: {result.message!r}"

        with engine.connect() as conn:
            rollup_error = conn.execute(text(
                "SELECT COALESCE(SUM(error_event_count), 0) FROM "
                "session_telemetry_provider_daily WHERE day = :day"
            ), {"day": target_day.isoformat()}).scalar()

            raw_error = conn.execute(text(
                "SELECT COALESCE(SUM(error_event_count), 0) FROM "
                "session_telemetry WHERE "
                "date(observed_at / 1000, 'unixepoch') = :day"
            ), {"day": target_day.isoformat()}).scalar()

        assert rollup_error == raw_error, (
            f"error_event_count rollup ({rollup_error}) != "
            f"raw sum ({raw_error}) for day {target_day}"
        )
        # Explicit: 5 rows × 2 error events = 10
        assert rollup_error == 10, (
            f"expected error_event_count=10, got {rollup_error}"
        )

    def test_rollup_provider_daily_includes_switch_event_count(
        self, rollup_db
    ):
        """SUM(switch_event_count) from raw rows equals rollup column."""
        engine, Session = rollup_db

        target_day = FROZEN_NOW.date() - timedelta(days=2)
        base_obs = datetime.combine(
            target_day, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=10)

        rows = [
            {
                "session_id": f"sess-sw-{i}",
                "observed_at": _ms_at_utc(base_obs + timedelta(seconds=i * 10)),
                "user_id": 62,
                "provider_id": 12,
                "channel_id": "ch-switch",
                "bytes_delta": 300,
                "buffer_event_count": 0,
                "reconnect_event_count": 0,
                "error_event_count": 0,
                "switch_event_count": 1,
                "poll_interval_ms": 10_000,
            }
            for i in range(6)
        ]
        _seed_session_telemetry(Session, rows)

        result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert result.success, f"task failed: {result.message!r}"

        with engine.connect() as conn:
            rollup_switch = conn.execute(text(
                "SELECT COALESCE(SUM(switch_event_count), 0) FROM "
                "session_telemetry_provider_daily WHERE day = :day"
            ), {"day": target_day.isoformat()}).scalar()

            raw_switch = conn.execute(text(
                "SELECT COALESCE(SUM(switch_event_count), 0) FROM "
                "session_telemetry WHERE "
                "date(observed_at / 1000, 'unixepoch') = :day"
            ), {"day": target_day.isoformat()}).scalar()

        assert rollup_switch == raw_switch, (
            f"switch_event_count rollup ({rollup_switch}) != "
            f"raw sum ({raw_switch}) for day {target_day}"
        )
        # Explicit: 6 rows × 1 switch event = 6
        assert rollup_switch == 6, (
            f"expected switch_event_count=6, got {rollup_switch}"
        )

    def test_rollup_provider_daily_buffer_event_count_no_regression(
        self, rollup_db
    ):
        """Pre-existing buffer_event_count rollup still works after bd-d0ha9.

        Verifies the INSERT OR REPLACE projection extension did not
        accidentally drop or mis-map the buffer_event_count column.
        """
        engine, Session = rollup_db

        target_day = FROZEN_NOW.date() - timedelta(days=2)
        base_obs = datetime.combine(
            target_day, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=11)

        rows = [
            {
                "session_id": f"sess-buf-{i}",
                "observed_at": _ms_at_utc(base_obs + timedelta(seconds=i * 10)),
                "user_id": 63,
                "provider_id": 13,
                "channel_id": "ch-buffer",
                "bytes_delta": 400,
                "buffer_event_count": 4,
                "reconnect_event_count": 0,
                "error_event_count": 0,
                "switch_event_count": 0,
                "poll_interval_ms": 10_000,
            }
            for i in range(3)
        ]
        _seed_session_telemetry(Session, rows)

        result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert result.success, f"task failed: {result.message!r}"

        with engine.connect() as conn:
            rollup_buffer = conn.execute(text(
                "SELECT COALESCE(SUM(buffer_event_count), 0) FROM "
                "session_telemetry_provider_daily WHERE day = :day"
            ), {"day": target_day.isoformat()}).scalar()

        # 3 rows × 4 buffer events = 12
        assert rollup_buffer == 12, (
            f"buffer_event_count regression: expected 12, got {rollup_buffer}"
        )

    def test_rollup_provider_daily_all_four_counters_mixed(
        self, rollup_db
    ):
        """All four event counters roll up correctly in a mixed-event row set.

        Verifies that the rollup does not cross-contaminate counters when
        a single session_telemetry row has nonzero values in multiple event
        counter columns simultaneously.
        """
        engine, Session = rollup_db

        target_day = FROZEN_NOW.date() - timedelta(days=2)
        base_obs = datetime.combine(
            target_day, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=13)

        # Three rows, each with different counts across all four event types.
        rows = [
            {
                "session_id": "sess-mix-0",
                "observed_at": _ms_at_utc(base_obs),
                "user_id": 64,
                "provider_id": 14,
                "channel_id": "ch-mix",
                "bytes_delta": 100,
                "buffer_event_count": 1,
                "reconnect_event_count": 2,
                "error_event_count": 3,
                "switch_event_count": 4,
                "poll_interval_ms": 10_000,
            },
            {
                "session_id": "sess-mix-1",
                "observed_at": _ms_at_utc(base_obs + timedelta(seconds=10)),
                "user_id": 64,
                "provider_id": 14,
                "channel_id": "ch-mix",
                "bytes_delta": 100,
                "buffer_event_count": 0,
                "reconnect_event_count": 1,
                "error_event_count": 0,
                "switch_event_count": 2,
                "poll_interval_ms": 10_000,
            },
            {
                "session_id": "sess-mix-2",
                "observed_at": _ms_at_utc(base_obs + timedelta(seconds=20)),
                "user_id": 64,
                "provider_id": 14,
                "channel_id": "ch-mix",
                "bytes_delta": 100,
                "buffer_event_count": 5,
                "reconnect_event_count": 0,
                "error_event_count": 1,
                "switch_event_count": 0,
                "poll_interval_ms": 10_000,
            },
        ]
        _seed_session_telemetry(Session, rows)

        result, _ = _run_task(Session, now_utc=FROZEN_NOW)
        assert result.success, f"task failed: {result.message!r}"

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT buffer_event_count, reconnect_event_count, "
                "       error_event_count, switch_event_count "
                "FROM session_telemetry_provider_daily "
                "WHERE channel_id = 'ch-mix' AND day = :day"
            ), {"day": target_day.isoformat()}).fetchone()

        assert row is not None, "no rollup row written for ch-mix"
        buf, rcn, err, sw = row
        # buf: 1+0+5=6, rcn: 2+1+0=3, err: 3+0+1=4, sw: 4+2+0=6
        assert buf == 6, f"buffer_event_count expected 6, got {buf}"
        assert rcn == 3, f"reconnect_event_count expected 3, got {rcn}"
        assert err == 4, f"error_event_count expected 4, got {err}"
        assert sw == 6, f"switch_event_count expected 6, got {sw}"
