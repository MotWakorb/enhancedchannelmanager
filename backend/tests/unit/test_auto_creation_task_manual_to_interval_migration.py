"""Tests for ``database._migrate_auto_creation_task_manual_to_interval`` (ADR-011, bd-ka7j9).

ADR-011 decoupled M3U refresh from auto-creation: the ``AutoCreationTask``
constructor default flipped MANUAL -> INTERVAL (60s) so the task self-fires on
a schedule and decides for itself whether to run the post-refresh pipeline. As
with the bd-ifmr5 cleanup migration, the new default only reaches fresh
installs; existing installs have a persisted ``scheduled_tasks`` row at
``schedule_type='manual'`` that ``TaskRegistry.sync_from_database`` rehydrates.
This one-time, idempotent, WHERE-gated UPDATE flips them once.

Mirrors ``test_cleanup_task_manual_to_cron_migration`` (real file-backed SQLite,
not mocks).
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


def _create_scheduled_tasks_table(conn) -> None:
    conn.execute(text(
        "CREATE TABLE scheduled_tasks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "task_id VARCHAR(50) NOT NULL UNIQUE, "
        "task_name VARCHAR(100) NOT NULL, "
        "schedule_type VARCHAR(20) NOT NULL DEFAULT 'manual', "
        "cron_expression VARCHAR(100), "
        "interval_seconds INTEGER, "
        "next_run_at DATETIME"
        ")"
    ))


def _read_row(conn) -> dict | None:
    row = conn.execute(text(
        "SELECT schedule_type, interval_seconds, next_run_at "
        "FROM scheduled_tasks WHERE task_id='auto_creation'"
    )).fetchone()
    if row is None:
        return None
    return {"schedule_type": row[0], "interval_seconds": row[1], "next_run_at": row[2]}


class TestAutoCreationTaskManualToIntervalMigration:
    def test_flips_existing_manual_row_to_interval_and_logs(self, tmp_path, caplog):
        db_file = tmp_path / "manual.db"
        engine = _make_engine(db_file)
        try:
            with engine.begin() as conn:
                _create_scheduled_tasks_table(conn)
                conn.execute(text(
                    "INSERT INTO scheduled_tasks (task_id, task_name, schedule_type) "
                    "VALUES ('auto_creation', 'Auto-Create Channels', 'manual')"
                ))

            with engine.connect() as conn:
                with caplog.at_level(logging.INFO, logger=database.logger.name):
                    database._migrate_auto_creation_task_manual_to_interval(conn)

            with engine.connect() as conn:
                row = _read_row(conn)

            assert row is not None
            assert row["schedule_type"] == "interval"
            assert row["interval_seconds"] == 60
            assert row["next_run_at"] is None  # reset so the registry recomputes it

            flipped = [
                r for r in caplog.records
                if "Flipped auto_creation task schedule MANUAL -> INTERVAL" in r.message
            ]
            assert flipped, [r.message for r in caplog.records]
            assert any("bd-ka7j9" in r.message for r in flipped)
        finally:
            engine.dispose()

    def test_preserves_existing_interval_row(self, tmp_path, caplog):
        db_file = tmp_path / "interval.db"
        engine = _make_engine(db_file)
        try:
            with engine.begin() as conn:
                _create_scheduled_tasks_table(conn)
                conn.execute(text(
                    "INSERT INTO scheduled_tasks (task_id, task_name, schedule_type, interval_seconds) "
                    "VALUES ('auto_creation', 'Auto-Create Channels', 'interval', 3600)"
                ))

            with engine.connect() as conn:
                with caplog.at_level(logging.INFO, logger=database.logger.name):
                    database._migrate_auto_creation_task_manual_to_interval(conn)

            with engine.connect() as conn:
                row = _read_row(conn)

            # Operator-chosen interval untouched (WHERE filtered on 'manual').
            assert row["schedule_type"] == "interval"
            assert row["interval_seconds"] == 3600
            assert not [r for r in caplog.records if "Flipped auto_creation task" in r.message]
        finally:
            engine.dispose()

    def test_noop_when_no_row_exists(self, tmp_path, caplog):
        db_file = tmp_path / "fresh.db"
        engine = _make_engine(db_file)
        try:
            with engine.begin() as conn:
                _create_scheduled_tasks_table(conn)

            with engine.connect() as conn:
                with caplog.at_level(logging.INFO, logger=database.logger.name):
                    database._migrate_auto_creation_task_manual_to_interval(conn)

            with engine.connect() as conn:
                count = conn.execute(text(
                    "SELECT COUNT(*) FROM scheduled_tasks WHERE task_id='auto_creation'"
                )).scalar()

            assert count == 0
            assert not [r for r in caplog.records if "Flipped auto_creation task" in r.message]
        finally:
            engine.dispose()

    def test_is_idempotent_across_restarts(self, tmp_path, caplog):
        db_file = tmp_path / "idempotent.db"
        engine = _make_engine(db_file)
        try:
            with engine.begin() as conn:
                _create_scheduled_tasks_table(conn)
                conn.execute(text(
                    "INSERT INTO scheduled_tasks (task_id, task_name, schedule_type) "
                    "VALUES ('auto_creation', 'Auto-Create Channels', 'manual')"
                ))

            with engine.connect() as conn:
                with caplog.at_level(logging.INFO, logger=database.logger.name):
                    database._migrate_auto_creation_task_manual_to_interval(conn)
            assert len([r for r in caplog.records if "Flipped auto_creation task" in r.message]) == 1

            caplog.clear()

            with engine.connect() as conn:
                with caplog.at_level(logging.INFO, logger=database.logger.name):
                    database._migrate_auto_creation_task_manual_to_interval(conn)
            assert not [r for r in caplog.records if "Flipped auto_creation task" in r.message]

            with engine.connect() as conn:
                row = _read_row(conn)
            assert row["schedule_type"] == "interval"
            assert row["interval_seconds"] == 60
        finally:
            engine.dispose()

    def test_noop_when_table_missing(self, tmp_path):
        db_file = tmp_path / "no_table.db"
        engine = _make_engine(db_file)
        try:
            with engine.connect() as conn:
                database._migrate_auto_creation_task_manual_to_interval(conn)  # must not raise
        finally:
            engine.dispose()
