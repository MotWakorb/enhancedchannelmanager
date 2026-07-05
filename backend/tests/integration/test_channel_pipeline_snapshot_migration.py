"""Migration tests for ``auto_creation_snapshots`` (revision 0022, ADR-010 §D6).

Bead: ``enhancedchannelmanager-uc51o.2`` (Phase 2 of epic
``enhancedchannelmanager-uc51o``).

Covers:

* Fresh up from 0021 → the table exists with the ADR-010 §D6 column set, the
  named UNIQUE constraint on ``execution_id`` (1:1), the ``snapshot_time``
  index, and the FK to ``auto_creation_executions.id`` with ON DELETE CASCADE.
* Fresh down from 0022 → the table and its indexes are gone.
* bd-5w6jz idempotency:
  - Full drift: the table is already materialised (e.g. ``create_all()`` from
    the post-0022 ORM model on a long-running install where
    ``alembic_version`` is still at 0021). Re-running the migration must skip
    the CREATE TABLE without raising ``table ... already exists``.
  - Index drift: the table is present but the time index is missing. The
    migration adds only the absent index.

All fixtures use synthetic ids — no production-derived data.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

import database


TABLE = "auto_creation_snapshots"
UQ_EXECUTION = "uq_auto_snapshot_execution"
IDX_TIME = "idx_auto_snapshot_time"

EXPECTED_COLUMNS = {
    "id", "execution_id", "snapshot_time", "channel_count",
    "channels_data", "created_at",
}


def _make_alembic_config(db_url: str):
    """Build an Alembic Config pinned to *db_url*.

    Mirrors the helper in the other migration test files so each file is
    self-contained (``docs/database_migrations.md`` test-isolation convention).
    """
    from alembic.config import Config

    ini_path = Path(database.ALEMBIC_INI_PATH)
    assert ini_path.exists(), f"alembic.ini missing at {ini_path}"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _table_names(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _index_names(engine, table: str) -> set[str]:
    return {idx["name"] for idx in inspect(engine).get_indexes(table)}


def _unique_constraint_names(engine, table: str) -> set[str]:
    return {
        uc["name"]
        for uc in inspect(engine).get_unique_constraints(table)
        if uc.get("name")
    }


def _foreign_keys(engine, table: str) -> list[dict]:
    return list(inspect(engine).get_foreign_keys(table))


@pytest.mark.integration
class TestMigration0022Fresh:
    """Revision 0022 — fresh upgrade creates the table + index + constraints."""

    def test_migration_0022_creates_table_fresh_install(self, tmp_path):
        """Empty DB → ``alembic upgrade head`` lands the table intact."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0022_fresh.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            assert TABLE in _table_names(engine), (
                f"{TABLE} not created by upgrade head"
            )

            cols = {c["name"]: c for c in inspect(engine).get_columns(TABLE)}
            assert set(cols) == EXPECTED_COLUMNS, (
                f"column set differs from ADR-010 §D6: {sorted(cols)}"
            )
            assert cols["execution_id"]["nullable"] is False
            assert cols["snapshot_time"]["nullable"] is False
            assert cols["channel_count"]["nullable"] is False
            assert cols["created_at"]["nullable"] is False
            assert cols["channels_data"]["nullable"] is True

            # Named UNIQUE constraint on execution_id (1:1, §D6).
            assert UQ_EXECUTION in _unique_constraint_names(engine, TABLE), (
                f"{UQ_EXECUTION} missing — the 1:1 invariant is unenforced"
            )

            # snapshot_time index (the §D7 age-window prune scan).
            assert IDX_TIME in _index_names(engine, TABLE), (
                f"{IDX_TIME} missing: have {sorted(_index_names(engine, TABLE))}"
            )

            # FK → auto_creation_executions.id with ON DELETE CASCADE (§D6).
            fks = _foreign_keys(engine, TABLE)
            assert len(fks) == 1, f"expected exactly one FK, got {fks!r}"
            fk = fks[0]
            assert fk["constrained_columns"] == ["execution_id"]
            assert fk["referred_table"] == "auto_creation_executions"
            assert fk["referred_columns"] == ["id"]
            assert fk.get("options", {}).get("ondelete", "").upper() == "CASCADE", (
                f"FK must use ON DELETE CASCADE per §D6: options={fk.get('options')}"
            )
        finally:
            engine.dispose()

    def test_migration_0022_downgrade_drops_table(self, tmp_path):
        """Downgrade 0022 → 0021 removes the table and its indexes."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0022_down.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0022")

        engine = create_engine(db_url, future=True)
        try:
            assert TABLE in _table_names(engine)
        finally:
            engine.dispose()

        command.downgrade(cfg, "0021")

        engine = create_engine(db_url, future=True)
        try:
            assert TABLE not in _table_names(engine), (
                f"{TABLE} still present after downgrade to 0021"
            )
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name LIKE '%auto_snapshot%'"
                )).fetchall()
            assert rows == [], f"orphan snapshot indexes survive downgrade: {rows}"
        finally:
            engine.dispose()

    def test_migration_0022_cascade_deletes_snapshot(self, tmp_path):
        """Deleting the parent execution cascades to its snapshot (§D6)."""
        from datetime import datetime
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0022_cascade.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        # FK enforcement requires PRAGMA foreign_keys=ON. The app connect
        # listener sets it; here we set it explicitly on the test connection.
        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text("PRAGMA foreign_keys=ON"))
                conn.execute(text(
                    "INSERT INTO auto_creation_executions "
                    "(id, mode, triggered_by, started_at, status, "
                    " streams_evaluated, streams_matched, channels_created, "
                    " channels_updated, groups_created, streams_merged, "
                    " channels_touched, streams_skipped, streams_excluded, "
                    " created_at) "
                    "VALUES (1, 'execute', 'manual', :now, 'completed', "
                    " 0,0,0,0,0,0,0,0,0, :now)"
                ), {"now": datetime.utcnow().isoformat()})
                conn.execute(text(
                    "INSERT INTO auto_creation_snapshots "
                    "(execution_id, snapshot_time, channel_count, "
                    " channels_data, created_at) "
                    "VALUES (1, :now, 0, '{\"channels\": []}', :now)"
                ), {"now": datetime.utcnow().isoformat()})

            with engine.begin() as conn:
                conn.execute(text("PRAGMA foreign_keys=ON"))
                conn.execute(text("DELETE FROM auto_creation_executions WHERE id=1"))

            with engine.connect() as conn:
                remaining = conn.execute(text(
                    "SELECT COUNT(*) FROM auto_creation_snapshots WHERE execution_id=1"
                )).scalar()
            assert remaining == 0, "ON DELETE CASCADE did not remove the snapshot"
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0022Idempotent:
    """bd-5w6jz idempotency for migration 0022."""

    def test_migration_0022_idempotent_table_pre_created(self, tmp_path):
        """Table already materialised pre-migration (create_all drift case).

        Re-running the migration must skip the CREATE TABLE without raising
        ``OperationalError: table auto_creation_snapshots already exists`` and
        still produce a schema with the time index present.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0022_drift.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0021")

        engine = create_engine(db_url, future=True)
        try:
            assert TABLE not in _table_names(engine)
            # Inject the table via raw SQL — mimics create_all() materialising
            # the post-0022 ORM shape while alembic_version is still 0021.
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE TABLE auto_creation_snapshots ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  execution_id INTEGER NOT NULL,"
                    "  snapshot_time DATETIME NOT NULL,"
                    "  channel_count INTEGER NOT NULL DEFAULT 0,"
                    "  channels_data TEXT,"
                    "  created_at DATETIME NOT NULL,"
                    "  CONSTRAINT uq_auto_snapshot_execution UNIQUE (execution_id),"
                    "  FOREIGN KEY(execution_id) "
                    "    REFERENCES auto_creation_executions (id) ON DELETE CASCADE"
                    ")"
                ))
            assert TABLE in _table_names(engine)
        finally:
            engine.dispose()

        # Pre-fix this would raise: table already exists.
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            assert TABLE in _table_names(engine)
            assert IDX_TIME in _index_names(engine, TABLE), (
                "time index must be created even though the table pre-existed"
            )
            assert UQ_EXECUTION in _unique_constraint_names(engine, TABLE)
        finally:
            engine.dispose()

    def test_migration_0022_idempotent_index_missing(self, tmp_path):
        """Table present, time index absent → migration adds only the index."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0022_idxdrift.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0021")

        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE TABLE auto_creation_snapshots ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  execution_id INTEGER NOT NULL,"
                    "  snapshot_time DATETIME NOT NULL,"
                    "  channel_count INTEGER NOT NULL DEFAULT 0,"
                    "  channels_data TEXT,"
                    "  created_at DATETIME NOT NULL,"
                    "  CONSTRAINT uq_auto_snapshot_execution UNIQUE (execution_id),"
                    "  FOREIGN KEY(execution_id) "
                    "    REFERENCES auto_creation_executions (id) ON DELETE CASCADE"
                    ")"
                ))
            # Table present but NO idx_auto_snapshot_time yet.
            assert IDX_TIME not in _index_names(engine, TABLE)
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            assert IDX_TIME in _index_names(engine, TABLE), (
                "migration must add the absent time index"
            )
        finally:
            engine.dispose()
