"""Tests for ``database._heal_task_schedules_null_next_run_at`` (bd-1weac).

P0 regression — registry must be populated at heal time
-------------------------------------------------------

``test_heal_subprocess_without_pre_imported_tasks`` (the last test in this
file) locks the contract that the heal function self-imports the ``tasks``
package so ``task_registry.get_task_class()`` can find class defaults. In
production, ``init_db()`` (which calls the heal) runs in ``main.py``'s
lifespan startup BEFORE ``main.py``'s ``import tasks`` statement, so the
registry is otherwise empty when the heal runs. Without the self-import,
every healed row hits the ``class_config is None`` branch and stays at the
broken ``interval/0/NULL`` shape — silently. The in-process tests above
cannot catch this because pytest collection pre-imports the ``tasks``
package transitively via other test modules (e.g.
``test_task_registry_cron_default_schedule.py``), inadvertently pre-staging
the registry. The subprocess test runs the heal in a fresh interpreter
where nothing has imported ``tasks``, which faithfully reproduces the
production startup ordering.


The bug bd-1weac fixes — bd-p5b8i headline:

For 4 months, every fresh and upgraded install has hit the bug in
``task_registry._create_default_task_schedule`` where a CRON-default task
gets a ``task_schedules`` row at ``schedule_type='interval', interval_seconds=0,
next_run_at=NULL``. The Part 1 fix repairs the WRITE path. This Part 2 fix
adds a startup HEAL that scans for pre-existing broken rows (every operator
who has restarted ECM between v0.8.7-0023 and v0.17.0-0042 has at least one
such row) and rewrites them.

Why a heal vs an Alembic migration: same answer as bd-ifmr5 — the bd-5w6jz
smart-bootstrap fast-path stamps ``alembic_version`` forward when the live
schema covers the model shape, so an Alembic data migration would be
SILENTLY SKIPPED on every existing install. ``_run_migrations`` runs
unconditionally on every startup with WHERE-clause idempotency. The
``next_run_at IS NULL AND enabled = 1`` predicate is the natural gate — a
healed row has next_run_at set, so subsequent runs are no-ops.

Six behavioral shapes lock the contract:

1. **Heals pre-existing broken cleanup row**: seed a row at
   ``interval/0/next_run_at=NULL`` (the pre-fix shape for CleanupTask),
   run heal, assert the row is rewritten to weekly Sunday 02:00 with
   ``next_run_at`` populated.
2. **Heals pre-existing broken stats_v2_rollup row**: same as above but
   for the daily-03:30 task.
3. **Idempotent across restarts**: run heal twice; second run finds 0 rows
   to fix and logs nothing. (Same contract as bd-ifmr5's migration — the
   operator must not see "Healed N rows" on every boot.)
4. **Preserves MANUAL tasks**: a row that is legitimately NULL because
   the task is on-demand (no schedule) is NOT touched. We filter on
   ``enabled=1 AND next_run_at IS NULL`` AND require the task to be a
   recognised registry entry with a non-MANUAL default — MANUAL tasks
   that never got a task_schedules row don't end up in this code path
   at all.
5. **Preserves proper INTERVAL tasks**: a healthy m3u_change_monitor
   row at ``interval/300`` with a valid next_run_at is NOT touched.
6. **Heal preserves operator overrides**: if the row's CURRENT
   ``schedule_type`` is non-interval (operator customised it via the UI
   to ``daily 06:30 America/New_York`` for example) but next_run_at is
   NULL for some reason (timezone bug, clock jump, etc.), the heal
   recomputes ``next_run_at`` from the existing schedule fields rather
   than rewriting them from the task's class default.
"""
from __future__ import annotations

import logging
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from models import Base, TaskSchedule


@pytest.fixture
def file_engine(tmp_path):
    """File-backed SQLite engine with full schema."""
    db_file = tmp_path / "heal.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _read_row(engine, task_id: str) -> dict | None:
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    try:
        row = s.query(TaskSchedule).filter_by(task_id=task_id).first()
        if row is None:
            return None
        return {
            "id": row.id,
            "schedule_type": row.schedule_type,
            "interval_seconds": row.interval_seconds,
            "schedule_time": row.schedule_time,
            "days_of_week": row.days_of_week,
            "day_of_month": row.day_of_month,
            "timezone": row.timezone,
            "next_run_at": row.next_run_at,
            "enabled": row.enabled,
        }
    finally:
        s.close()


def _seed_broken_row(engine, task_id: str) -> None:
    """Seed the exact pre-fix shape: interval/0/next_run_at=NULL/enabled=1."""
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO task_schedules "
            "(task_id, name, enabled, schedule_type, interval_seconds, "
            " timezone, next_run_at, created_at, updated_at) "
            "VALUES (:task_id, 'Default Schedule', 1, 'interval', 0, "
            " 'UTC', NULL, :now, :now)"
        ), {"task_id": task_id, "now": datetime.utcnow()})


class TestHealTaskSchedulesNullNextRunAt:
    """Six behavioral shapes for the bd-1weac startup heal."""

    def test_heal_rewrites_broken_cleanup_row_to_weekly_with_next_run_at(
        self, file_engine, caplog
    ):
        """Pre-fix broken cleanup row → healed to weekly Sunday 02:00 + next_run_at set."""
        _seed_broken_row(file_engine, "cleanup")

        with file_engine.connect() as conn:
            with caplog.at_level(logging.INFO, logger=database.logger.name):
                database._heal_task_schedules_null_next_run_at(conn)

        row = _read_row(file_engine, "cleanup")
        assert row is not None, "cleanup row vanished during heal"
        assert row["schedule_type"] == "weekly", (
            f"healed row should be weekly, got {row['schedule_type']!r}"
        )
        assert row["days_of_week"] == "0", (
            f"healed row should target Sunday (0), got {row['days_of_week']!r}"
        )
        assert row["schedule_time"] == "02:00"
        assert row["next_run_at"] is not None, (
            "load-bearing: next_run_at must be populated after heal"
        )

        # Operator-visible log fires only when N > 0.
        heal_logs = [r for r in caplog.records if "Healed" in r.message]
        assert heal_logs, (
            "expected INFO log naming the heal count; got: "
            f"{[r.message for r in caplog.records]}"
        )
        assert any("bd-1weac" in r.message for r in heal_logs), (
            "log must reference bd-1weac for operator-side traceability"
        )

    def test_heal_rewrites_broken_stats_v2_rollup_row_to_daily(self, file_engine):
        """Pre-fix broken stats_v2_rollup row → healed to daily 03:30 + next_run_at set."""
        _seed_broken_row(file_engine, "stats_v2_rollup")

        with file_engine.connect() as conn:
            database._heal_task_schedules_null_next_run_at(conn)

        row = _read_row(file_engine, "stats_v2_rollup")
        assert row is not None
        assert row["schedule_type"] == "daily"
        assert row["schedule_time"] == "03:30"
        assert row["next_run_at"] is not None

    def test_heal_is_idempotent_across_restarts(self, file_engine, caplog):
        """Run heal twice; second run finds 0 rows to fix and logs nothing."""
        _seed_broken_row(file_engine, "cleanup")

        # First run.
        with file_engine.connect() as conn:
            with caplog.at_level(logging.INFO, logger=database.logger.name):
                database._heal_task_schedules_null_next_run_at(conn)

        first_run_heals = [r for r in caplog.records if "Healed" in r.message]
        assert len(first_run_heals) == 1, (
            f"first run must log once; got {len(first_run_heals)}"
        )

        caplog.clear()

        # Second run: must be a silent no-op.
        with file_engine.connect() as conn:
            with caplog.at_level(logging.INFO, logger=database.logger.name):
                database._heal_task_schedules_null_next_run_at(conn)

        second_run_heals = [r for r in caplog.records if "Healed" in r.message]
        assert not second_run_heals, (
            "second run must be silent — found log records: "
            f"{[r.message for r in second_run_heals]}"
        )

        # State must still be the healed shape.
        row = _read_row(file_engine, "cleanup")
        assert row["schedule_type"] == "weekly"
        assert row["next_run_at"] is not None

    def test_heal_preserves_disabled_rows(self, file_engine):
        """A disabled row with NULL next_run_at is NOT touched.

        Operators disable schedules in the UI; the disabled row carries
        next_run_at=NULL by design (no point computing a run time for
        something that won't fire). The heal must skip these — its
        responsibility is enabled rows that have lost their schedule.
        """
        with file_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO task_schedules "
                "(task_id, name, enabled, schedule_type, interval_seconds, "
                " timezone, next_run_at, created_at, updated_at) "
                "VALUES ('cleanup', 'Disabled', 0, 'interval', 0, "
                " 'UTC', NULL, :now, :now)"
            ), {"now": datetime.utcnow()})

        with file_engine.connect() as conn:
            database._heal_task_schedules_null_next_run_at(conn)

        row = _read_row(file_engine, "cleanup")
        assert row is not None
        assert row["enabled"] is False
        # Untouched — schedule_type still the original 'interval', not 'weekly'.
        assert row["schedule_type"] == "interval"
        assert row["interval_seconds"] == 0
        assert row["next_run_at"] is None, (
            "disabled rows must not be healed — next_run_at should still be NULL"
        )

    def test_heal_preserves_healthy_interval_row(self, file_engine):
        """A healthy m3u_change_monitor row at interval/300 with valid next_run_at is untouched."""
        future = datetime(2030, 1, 1, 0, 0, 0)
        with file_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO task_schedules "
                "(task_id, name, enabled, schedule_type, interval_seconds, "
                " timezone, next_run_at, created_at, updated_at) "
                "VALUES ('m3u_change_monitor', 'Default', 1, 'interval', 300, "
                " 'UTC', :next_run, :now, :now)"
            ), {"next_run": future, "now": datetime.utcnow()})

        with file_engine.connect() as conn:
            database._heal_task_schedules_null_next_run_at(conn)

        row = _read_row(file_engine, "m3u_change_monitor")
        assert row is not None
        assert row["schedule_type"] == "interval"
        assert row["interval_seconds"] == 300
        # Original next_run_at preserved (within DB datetime precision).
        assert row["next_run_at"] == future

    def test_heal_preserves_operator_customised_schedule_only_recomputes_next_run(
        self, file_engine
    ):
        """Operator-customised schedule columns are preserved; only next_run_at is recomputed.

        If an operator sets cleanup to daily 06:30 NY-time via the UI but
        the row's next_run_at is somehow NULL (timezone DB corruption, clock
        jump on a long-running install), the heal must recompute next_run_at
        from the EXISTING schedule fields — NOT rewrite the schedule from
        the task's class default. Operator intent must survive.
        """
        with file_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO task_schedules "
                "(task_id, name, enabled, schedule_type, schedule_time, "
                " timezone, next_run_at, created_at, updated_at) "
                "VALUES ('cleanup', 'Operator Custom', 1, 'daily', '06:30', "
                " 'America/New_York', NULL, :now, :now)"
            ), {"now": datetime.utcnow()})

        with file_engine.connect() as conn:
            database._heal_task_schedules_null_next_run_at(conn)

        row = _read_row(file_engine, "cleanup")
        assert row is not None
        # Schedule fields preserved — operator's choice survives.
        assert row["schedule_type"] == "daily"
        assert row["schedule_time"] == "06:30"
        assert row["timezone"] == "America/New_York"
        # next_run_at recomputed.
        assert row["next_run_at"] is not None, (
            "operator-customised row's next_run_at must be recomputed by heal"
        )

    def test_heal_noop_when_table_missing(self, tmp_path):
        """Defensive: missing task_schedules table → no raise, no-op.

        Matches the rest of database._run_migrations guards (e.g.,
        _migrate_cleanup_task_manual_to_cron).
        """
        db_file = tmp_path / "no_table.db"
        engine = create_engine(
            f"sqlite:///{db_file}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            with engine.connect() as conn:
                # Must not raise.
                database._heal_task_schedules_null_next_run_at(conn)
        finally:
            engine.dispose()


def test_heal_subprocess_without_pre_imported_tasks(tmp_path):
    """P0 regression — heal must work in a fresh interpreter (bd-1weac).

    Production startup ordering: ``init_db()`` (which calls the heal) runs
    in ``main.py``'s lifespan startup BEFORE ``main.py``'s ``import tasks``
    statement. At heal time, ``task_registry._tasks`` is empty unless the
    heal itself triggers the registration decorators.

    The in-process tests above cannot catch a missing self-import: pytest
    collects ``test_task_registry_cron_default_schedule.py`` and
    ``test_cleanup_task_default_schedule.py`` in the same session, which
    do ``from tasks.cleanup import CleanupTask`` at module scope. That
    transitive import populates the registry before any heal test runs —
    the registry is unwittingly pre-staged, so ``get_task_class('cleanup')``
    returns the class even if the heal doesn't import tasks itself.

    This subprocess test runs the heal in a brand-new Python process where
    NOTHING has imported the ``tasks`` package. If the heal function does
    not self-import ``tasks``, ``get_task_class()`` returns ``None``, the
    ``class_config is None`` branch fires, ``calculate_next_run`` returns
    ``None`` for the ``interval/0`` row, and the row stays NULL — the
    assertion below fails and the regression is caught.

    Verification protocol: comment out the ``import tasks`` line in
    ``database._heal_task_schedules_null_next_run_at`` and this test must
    fail. Restore the line and it must pass.
    """
    import os
    import subprocess
    import sys
    import textwrap

    backend_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    db_file = tmp_path / "subproc_heal.db"

    # 1. Seed: create the DB with a task_schedules table and the broken
    #    cleanup row. Done in-process so the subprocess starts from a clean
    #    interpreter state.
    seed_engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        Base.metadata.create_all(seed_engine)
        _seed_broken_row(seed_engine, "cleanup")
    finally:
        seed_engine.dispose()

    # 2. Subprocess: import ONLY database (which must self-import tasks
    #    inside the heal), run the heal against the seeded DB, exit.
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {backend_dir!r})

        # CRITICAL: do NOT import the tasks package here. The whole point
        # of this test is to verify the heal function self-imports tasks
        # so the task registry is populated when get_task_class() runs.
        # If a future maintainer adds `import tasks` here, this test
        # silently starts passing for the wrong reason — exactly the
        # anti-pattern bd-1weac was filed against.
        assert "tasks" not in sys.modules, (
            "tasks must not be pre-imported in this subprocess — "
            "see test docstring for why"
        )

        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            "sqlite:///{db_file}",
            connect_args={{"check_same_thread": False}},
            poolclass=StaticPool,
        )
        try:
            import database  # noqa: E402 — order matters for this test
            with engine.connect() as conn:
                database._heal_task_schedules_null_next_run_at(conn)
        finally:
            engine.dispose()
    """).strip()

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=backend_dir,
        timeout=60,
    )
    assert result.returncode == 0, (
        "subprocess heal failed:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )

    # 3. Re-open the DB and assert the row was healed.
    verify_engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        row = _read_row(verify_engine, "cleanup")
    finally:
        verify_engine.dispose()

    assert row is not None, "cleanup row vanished during subprocess heal"
    # The CleanupTask class default is cron `0 2 * * 0` → weekly Sunday 02:00.
    # If the heal failed to populate the registry, the row stays at
    # schedule_type='interval', interval_seconds=0, next_run_at=NULL — this
    # assertion is the regression signal.
    assert row["schedule_type"] == "weekly", (
        "subprocess heal did NOT rewrite the row — registry was not "
        "populated at heal time. Did someone remove the `import tasks` "
        f"self-import from _heal_task_schedules_null_next_run_at? Row: {row!r}"
    )
    assert row["days_of_week"] == "0", (
        f"healed row should target Sunday (0), got {row['days_of_week']!r}"
    )
    assert row["schedule_time"] == "02:00"
    assert row["next_run_at"] is not None, (
        "load-bearing: next_run_at must be populated after subprocess heal "
        "(if NULL, the row will never fire — same root-cause as bd-p5b8i)"
    )
