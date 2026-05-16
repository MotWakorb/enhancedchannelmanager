"""Tests for ``database._migrate_cleanup_task_manual_to_cron`` (bd-ifmr5).

bd-ygoqr (PR #289) flipped the ``CleanupTask`` constructor default from
``ScheduleType.MANUAL`` to ``ScheduleType.CRON`` (Sunday 02:00 UTC) so the
journal/task-execution/stream_stats retention actually enforces. But that fix
only reaches **fresh** installs: every existing install already has a
persisted ``scheduled_tasks`` row at ``schedule_type='manual'`` that
``task_registry.sync_from_database`` faithfully rehydrates, so the new
default is never reached for the GH #243 population that prompted the fix.

The bd-5w6jz smart-bootstrap fast-path in ``database._bootstrap_alembic``
stamps ``alembic_version`` forward when ``_schema_matches_head()`` is True,
which means a regular Alembic 0012 data migration would be SILENTLY SKIPPED
on every existing v0.17.0 install. The codebase's ``_run_migrations`` pattern
bypasses that fast-path by running unconditionally on every startup, with
idempotency enforced via WHERE clauses on the UPDATE.

These tests pin the contract on real file-backed SQLite (the bd-ej995
fixture pattern) — not mocks — so the UPDATE is observably hitting the
actual SQLite engine, not a mocked context. Five behavioral shapes:

1. **Flips MANUAL**: seed a ``cleanup`` row at MANUAL, run, assert flipped
   to CRON ``0 2 * * 0`` and the migration logs the per-operator count.
2. **Preserves CRON**: seed a ``cleanup`` row at CRON with a different
   expression, run, assert untouched (the WHERE-clause gate must not
   clobber an explicit operator schedule).
3. **Preserves INTERVAL**: seed a ``cleanup`` row at INTERVAL, run, assert
   untouched.
4. **No-op when no row**: empty ``scheduled_tasks`` table (fresh install
   that hasn't materialised a cleanup row yet), run, assert no UPDATE and
   no log line.
5. **Idempotent across restarts**: run the migration twice; second run
   must be a strict no-op (zero rowcount, zero log lines about flipping).
   This is the production contract — ``_run_migrations`` runs on every
   container start, and the operator must not see "flipped N operators"
   on every reboot after the first.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import database


def _make_engine(db_file: Path):
    """Return a file-backed SQLite engine matching production settings."""
    return create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )


def _create_scheduled_tasks_table(conn) -> None:
    """Materialise the minimal ``scheduled_tasks`` schema this migration touches.

    We only need ``id``, ``task_id``, ``task_name``, ``schedule_type``,
    ``cron_expression``, and ``interval_seconds`` for the UPDATE to land
    correctly. Full table shape (with all 20+ alert/notification columns)
    isn't needed — the migration's WHERE clause and SET list only touch
    these. Keeping the fixture minimal makes the test's intent obvious.
    """
    conn.execute(text(
        "CREATE TABLE scheduled_tasks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "task_id VARCHAR(50) NOT NULL UNIQUE, "
        "task_name VARCHAR(100) NOT NULL, "
        "schedule_type VARCHAR(20) NOT NULL DEFAULT 'manual', "
        "cron_expression VARCHAR(100), "
        "interval_seconds INTEGER"
        ")"
    ))


def _read_cleanup_row(conn) -> dict | None:
    row = conn.execute(text(
        "SELECT schedule_type, cron_expression, interval_seconds "
        "FROM scheduled_tasks WHERE task_id='cleanup'"
    )).fetchone()
    if row is None:
        return None
    return {
        "schedule_type": row[0],
        "cron_expression": row[1],
        "interval_seconds": row[2],
    }


class TestCleanupTaskManualToCronMigration:
    """Five behavioral shapes for the bd-ifmr5 one-time data migration."""

    def test_flips_existing_manual_row_to_cron_and_logs_count(self, tmp_path, caplog):
        """Existing MANUAL row gets flipped to CRON 0 2 * * 0 and the operator-visible log fires."""
        db_file = tmp_path / "manual.db"
        engine = _make_engine(db_file)
        try:
            with engine.begin() as conn:
                _create_scheduled_tasks_table(conn)
                conn.execute(text(
                    "INSERT INTO scheduled_tasks (task_id, task_name, schedule_type) "
                    "VALUES ('cleanup', 'Database Cleanup', 'manual')"
                ))

            with engine.connect() as conn:
                with caplog.at_level(logging.INFO, logger=database.logger.name):
                    database._migrate_cleanup_task_manual_to_cron(conn)

            with engine.connect() as conn:
                row = _read_cleanup_row(conn)

            assert row is not None, "cleanup row vanished during migration"
            assert row["schedule_type"] == "cron", (
                f"schedule_type should be 'cron', got {row['schedule_type']!r}"
            )
            assert row["cron_expression"] == "0 2 * * 0", (
                f"cron_expression should be Sunday 02:00 UTC, got {row['cron_expression']!r}"
            )

            # Operator-visible log proves the migration actually fired
            # (rowcount > 0 branch is what the operator sees in logs to
            # confirm bd-ifmr5 ran for them).
            flipped_records = [
                r for r in caplog.records
                if "Flipped cleanup task schedule MANUAL -> CRON" in r.message
            ]
            assert flipped_records, (
                "expected INFO log naming the MANUAL->CRON flip; got: "
                f"{[r.message for r in caplog.records]}"
            )
            assert any("bd-ifmr5" in r.message for r in flipped_records), (
                "log must reference bd-ifmr5 for operator-side traceability"
            )
        finally:
            engine.dispose()

    def test_preserves_existing_cron_row_with_different_expression(self, tmp_path, caplog):
        """An operator who deliberately set CRON with a custom expression must not be clobbered."""
        db_file = tmp_path / "cron.db"
        engine = _make_engine(db_file)
        try:
            with engine.begin() as conn:
                _create_scheduled_tasks_table(conn)
                # Operator-chosen daily-at-04:30 schedule (NOT the bd-ygoqr default).
                conn.execute(text(
                    "INSERT INTO scheduled_tasks (task_id, task_name, schedule_type, cron_expression) "
                    "VALUES ('cleanup', 'Database Cleanup', 'cron', '30 4 * * *')"
                ))

            with engine.connect() as conn:
                with caplog.at_level(logging.INFO, logger=database.logger.name):
                    database._migrate_cleanup_task_manual_to_cron(conn)

            with engine.connect() as conn:
                row = _read_cleanup_row(conn)

            assert row is not None
            assert row["schedule_type"] == "cron"
            # The WHERE clause filtered on schedule_type='manual', so the
            # custom CRON expression must survive untouched.
            assert row["cron_expression"] == "30 4 * * *", (
                f"existing CRON expression was clobbered: {row['cron_expression']!r}"
            )

            # Strict no-op: the "flipped N operator(s)" log must not fire
            # because the WHERE clause matched zero rows.
            flipped_records = [
                r for r in caplog.records
                if "Flipped cleanup task" in r.message
            ]
            assert not flipped_records, (
                "migration should be silent when no MANUAL row exists; "
                f"got log: {[r.message for r in flipped_records]}"
            )
        finally:
            engine.dispose()

    def test_preserves_existing_interval_row(self, tmp_path, caplog):
        """An operator who set INTERVAL must not be flipped to CRON either."""
        db_file = tmp_path / "interval.db"
        engine = _make_engine(db_file)
        try:
            with engine.begin() as conn:
                _create_scheduled_tasks_table(conn)
                # Operator-chosen every-6-hours schedule.
                conn.execute(text(
                    "INSERT INTO scheduled_tasks (task_id, task_name, schedule_type, interval_seconds) "
                    "VALUES ('cleanup', 'Database Cleanup', 'interval', 21600)"
                ))

            with engine.connect() as conn:
                with caplog.at_level(logging.INFO, logger=database.logger.name):
                    database._migrate_cleanup_task_manual_to_cron(conn)

            with engine.connect() as conn:
                row = _read_cleanup_row(conn)

            assert row is not None
            assert row["schedule_type"] == "interval", (
                f"INTERVAL row was clobbered to {row['schedule_type']!r}"
            )
            assert row["interval_seconds"] == 21600
            # cron_expression must stay NULL — the migration must not
            # write the bd-ygoqr default into an INTERVAL row.
            assert row["cron_expression"] is None, (
                f"INTERVAL row got an unexpected cron_expression: {row['cron_expression']!r}"
            )

            flipped_records = [
                r for r in caplog.records
                if "Flipped cleanup task" in r.message
            ]
            assert not flipped_records
        finally:
            engine.dispose()

    def test_noop_when_no_cleanup_row_exists(self, tmp_path, caplog):
        """Fresh install with no persisted cleanup row: migration is a strict no-op.

        This is the path the bd-ygoqr fix already covers — fresh installs
        get the CRON default from the constructor. The migration must not
        invent a row, must not raise, and must not log.
        """
        db_file = tmp_path / "fresh.db"
        engine = _make_engine(db_file)
        try:
            with engine.begin() as conn:
                _create_scheduled_tasks_table(conn)
                # No INSERT — table is empty.

            with engine.connect() as conn:
                with caplog.at_level(logging.INFO, logger=database.logger.name):
                    database._migrate_cleanup_task_manual_to_cron(conn)

            with engine.connect() as conn:
                row_count = conn.execute(text(
                    "SELECT COUNT(*) FROM scheduled_tasks WHERE task_id='cleanup'"
                )).scalar()

            assert row_count == 0, (
                "migration must not invent a cleanup row; "
                f"found {row_count} rows after run"
            )

            flipped_records = [
                r for r in caplog.records
                if "Flipped cleanup task" in r.message
            ]
            assert not flipped_records
        finally:
            engine.dispose()

    def test_is_idempotent_across_restarts(self, tmp_path, caplog):
        """Run twice: second run must be a strict no-op with no log line.

        ``_run_migrations`` is called from ``init_db`` on every container
        startup. The first start flips MANUAL -> CRON; every subsequent
        start must observe ``schedule_type='cron'`` and skip silently.
        If this contract regresses, operators would see "Flipped cleanup
        task ... for 1 operator(s)" in their logs on every reboot, which
        is both noise and a (false) signal that something is wrong with
        their persisted schedule.
        """
        db_file = tmp_path / "idempotent.db"
        engine = _make_engine(db_file)
        try:
            with engine.begin() as conn:
                _create_scheduled_tasks_table(conn)
                conn.execute(text(
                    "INSERT INTO scheduled_tasks (task_id, task_name, schedule_type) "
                    "VALUES ('cleanup', 'Database Cleanup', 'manual')"
                ))

            # First run: flips and logs.
            with engine.connect() as conn:
                with caplog.at_level(logging.INFO, logger=database.logger.name):
                    database._migrate_cleanup_task_manual_to_cron(conn)

            first_run_flips = [
                r for r in caplog.records
                if "Flipped cleanup task" in r.message
            ]
            assert len(first_run_flips) == 1, (
                f"first run must log once; got {len(first_run_flips)}"
            )

            # Clear the log buffer before second run so the assertion below
            # is unambiguous about what happened in run #2 only.
            caplog.clear()

            # Second run: must be a silent no-op.
            with engine.connect() as conn:
                with caplog.at_level(logging.INFO, logger=database.logger.name):
                    database._migrate_cleanup_task_manual_to_cron(conn)

            second_run_flips = [
                r for r in caplog.records
                if "Flipped cleanup task" in r.message
            ]
            assert not second_run_flips, (
                "second run must be silent — found log records: "
                f"{[r.message for r in second_run_flips]}"
            )

            # And the row state must still be the bd-ygoqr default.
            with engine.connect() as conn:
                row = _read_cleanup_row(conn)
            assert row["schedule_type"] == "cron"
            assert row["cron_expression"] == "0 2 * * 0"
        finally:
            engine.dispose()

    def test_noop_when_scheduled_tasks_table_missing(self, tmp_path, caplog):
        """Defensive: if the table doesn't exist yet, log debug and return without raising.

        This shouldn't happen in production (``Base.metadata.create_all()``
        materialises the table before ``_run_migrations`` runs), but the
        rest of this file's migrations carry the same guard, so we lock
        it for consistency.
        """
        db_file = tmp_path / "no_table.db"
        engine = _make_engine(db_file)
        try:
            # Don't create scheduled_tasks at all.
            with engine.connect() as conn:
                # Must not raise.
                database._migrate_cleanup_task_manual_to_cron(conn)
        finally:
            engine.dispose()
