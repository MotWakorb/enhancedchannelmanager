"""Tests for ``database._migrate_disable_auto_creation_schedule``
(enhancedchannelmanager-i2xad — production incident).

ADR-011 Phase 2 (bd-ka7j9) shipped the ``auto_creation`` task as a self-firing
~60s INTERVAL schedule, seeded ENABLED, plus a startup migration that flipped
already-installed instances ``manual -> interval`` and left them ENABLED. The
net effect on upgrade was that auto-creation began firing autonomously on every
instance — unwanted (bead i2xad).

The PO decision: scheduled auto-creation is now OPT-IN (disabled by default),
and already-flipped-and-enabled instances must be auto-corrected on upgrade by
disabling auto-creation **once**. Because the bd-5w6jz smart-bootstrap fast-path
silently skips data-only Alembic migrations on existing installs, this corrective
migration lives in ``_run_migrations`` (which runs every startup) like its
sibling ``_migrate_auto_creation_task_manual_to_interval``.

What it disables: the PARENT ``scheduled_tasks`` row only (the master opt-in
switch). task_engine gates firing on BOTH the child schedule's ``enabled`` AND
the parent task's ``enabled``, so disabling the parent fully stops firing. The
child ``task_schedules`` cadence row is deliberately LEFT enabled so opt-in is a
single operator action (flip the task "Enabled" toggle → parent on → fires).

``_run_migrations`` runs on EVERY startup, so a naive unconditional disable would
re-stomp an operator who later re-enables the task. A persisted one-time marker
(``ecm_oneshot_migrations``) gates the disable so it runs exactly once.

Mirrors ``test_auto_creation_task_manual_to_interval_migration`` (real
file-backed SQLite, not mocks).
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import database


def _make_engine(db_file: Path):
    return create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )


def _create_tables(conn) -> None:
    conn.execute(text(
        "CREATE TABLE scheduled_tasks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "task_id VARCHAR(50) NOT NULL UNIQUE, "
        "task_name VARCHAR(100) NOT NULL, "
        "enabled BOOLEAN NOT NULL DEFAULT 1, "
        "schedule_type VARCHAR(20) NOT NULL DEFAULT 'manual', "
        "interval_seconds INTEGER, "
        "next_run_at DATETIME"
        ")"
    ))
    conn.execute(text(
        "CREATE TABLE task_schedules ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "task_id VARCHAR(50) NOT NULL, "
        "name VARCHAR(100), "
        "enabled BOOLEAN NOT NULL DEFAULT 1, "
        "schedule_type VARCHAR(20) NOT NULL, "
        "interval_seconds INTEGER, "
        "next_run_at DATETIME"
        ")"
    ))


def _seed_task(conn, task_id: str, enabled: int = 1) -> None:
    conn.execute(text(
        "INSERT INTO scheduled_tasks (task_id, task_name, enabled, schedule_type, interval_seconds) "
        "VALUES (:t, :n, :e, 'interval', 60)"
    ), {"t": task_id, "n": task_id, "e": enabled})
    conn.execute(text(
        "INSERT INTO task_schedules (task_id, name, enabled, schedule_type, interval_seconds, next_run_at) "
        "VALUES (:t, 'Default Schedule', :e, 'interval', 60, '2099-01-01 00:00:00')"
    ), {"t": task_id, "e": enabled})


def _enabled_states(conn, task_id: str) -> tuple[int | None, int | None]:
    """Return (parent scheduled_tasks.enabled, child task_schedules.enabled)."""
    st = conn.execute(text(
        "SELECT enabled FROM scheduled_tasks WHERE task_id=:t"
    ), {"t": task_id}).fetchone()
    ts = conn.execute(text(
        "SELECT enabled FROM task_schedules WHERE task_id=:t"
    ), {"t": task_id}).fetchone()
    return (st[0] if st else None, ts[0] if ts else None)


class TestDisableAutoCreationScheduleMigration:
    def test_disables_parent_only_for_already_flipped_enabled_instance(self, tmp_path, caplog):
        db_file = tmp_path / "flipped.db"
        engine = _make_engine(db_file)
        try:
            with engine.begin() as conn:
                _create_tables(conn)
                _seed_task(conn, "auto_creation", enabled=1)

            with engine.connect() as conn:
                with caplog.at_level(logging.INFO, logger=database.logger.name):
                    database._migrate_disable_auto_creation_schedule(conn)

            with engine.connect() as conn:
                parent, child = _enabled_states(conn, "auto_creation")

            assert parent == 0, "parent scheduled_tasks.enabled must be 0 (the master opt-in switch)"
            assert child == 1, "child task_schedules cadence row must be LEFT enabled for single-action opt-in"

            disabled = [
                r for r in caplog.records
                if "Disabled auto_creation task" in r.message
            ]
            assert disabled, [r.message for r in caplog.records]
            assert any("i2xad" in r.message for r in disabled)
        finally:
            engine.dispose()

    def test_does_not_touch_other_tasks(self, tmp_path):
        db_file = tmp_path / "others.db"
        engine = _make_engine(db_file)
        try:
            with engine.begin() as conn:
                _create_tables(conn)
                _seed_task(conn, "auto_creation", enabled=1)
                _seed_task(conn, "cleanup", enabled=1)
                _seed_task(conn, "m3u_change_monitor", enabled=1)

            with engine.connect() as conn:
                database._migrate_disable_auto_creation_schedule(conn)

            with engine.connect() as conn:
                assert _enabled_states(conn, "auto_creation") == (0, 1)
                assert _enabled_states(conn, "cleanup") == (1, 1)
                assert _enabled_states(conn, "m3u_change_monitor") == (1, 1)
        finally:
            engine.dispose()

    def test_idempotent_does_not_redisable_operator_reenable(self, tmp_path, caplog):
        """Second run must be a no-op even if the operator re-enabled the task."""
        db_file = tmp_path / "idem.db"
        engine = _make_engine(db_file)
        try:
            with engine.begin() as conn:
                _create_tables(conn)
                _seed_task(conn, "auto_creation", enabled=1)

            # First run disables the parent + sets the one-time marker.
            with engine.connect() as conn:
                with caplog.at_level(logging.INFO, logger=database.logger.name):
                    database._migrate_disable_auto_creation_schedule(conn)
            assert len([r for r in caplog.records if "Disabled auto_creation task" in r.message]) == 1

            # Operator opts back in via the UI (flips the task "Enabled" toggle).
            with engine.begin() as conn:
                conn.execute(text("UPDATE scheduled_tasks SET enabled=1 WHERE task_id='auto_creation'"))

            caplog.clear()

            # Second run (subsequent restart) must NOT re-disable.
            with engine.connect() as conn:
                with caplog.at_level(logging.INFO, logger=database.logger.name):
                    database._migrate_disable_auto_creation_schedule(conn)
            assert not [r for r in caplog.records if "Disabled auto_creation task" in r.message]

            with engine.connect() as conn:
                assert _enabled_states(conn, "auto_creation") == (1, 1)
        finally:
            engine.dispose()

    def test_noop_when_rows_absent(self, tmp_path):
        db_file = tmp_path / "absent.db"
        engine = _make_engine(db_file)
        try:
            with engine.begin() as conn:
                _create_tables(conn)  # tables exist, no auto_creation rows

            with engine.connect() as conn:
                database._migrate_disable_auto_creation_schedule(conn)  # must not raise

            with engine.connect() as conn:
                count = conn.execute(text(
                    "SELECT COUNT(*) FROM scheduled_tasks WHERE task_id='auto_creation'"
                )).scalar()
            assert count == 0
        finally:
            engine.dispose()

    def test_noop_when_table_missing(self, tmp_path):
        db_file = tmp_path / "no_tables.db"
        engine = _make_engine(db_file)
        try:
            with engine.connect() as conn:
                database._migrate_disable_auto_creation_schedule(conn)  # must not raise
        finally:
            engine.dispose()

    def test_already_disabled_is_clean_noop(self, tmp_path):
        db_file = tmp_path / "already.db"
        engine = _make_engine(db_file)
        try:
            with engine.begin() as conn:
                _create_tables(conn)
                _seed_task(conn, "auto_creation", enabled=0)

            with engine.connect() as conn:
                database._migrate_disable_auto_creation_schedule(conn)

            with engine.connect() as conn:
                # parent stays disabled; child untouched (was seeded 0 here).
                assert _enabled_states(conn, "auto_creation") == (0, 0)
        finally:
            engine.dispose()
