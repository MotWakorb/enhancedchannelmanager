"""Migration tests for revision 0023 — cloud_storage_targets credential
freshness columns + sync_targets table (bead enhancedchannelmanager-0i2vt.4).

Covers:

* Fresh up from 0022 → cloud_storage_targets gains credential_version /
  token_revoked_at / insecure; sync_targets table exists with its column set
  and named UNIQUE(name) constraint.
* Fresh down from 0023 → the three columns and the sync_targets table are gone
  (round-trip up → down → up is symmetric).
* POPULATED-TABLE server_default: the real upgrade scenario. A
  cloud_storage_targets row exists at 0022 (no credential_version column);
  after upgrade the existing row backfills to credential_version=1 and
  insecure=0 — proving the NOT NULL add does not fail / orphan live rows.
* Idempotent re-run: upgrade applied against a DB that already drifted forward
  (columns + table present) is a no-op rather than raising "duplicate column"
  / "table already exists".

All fixtures use synthetic data — no production-derived rows.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

import database


CLOUD_TABLE = "cloud_storage_targets"
SYNC_TABLE = "sync_targets"
NEW_CLOUD_COLUMNS = {"credential_version", "token_revoked_at", "insecure"}
SYNC_COLUMNS = {
    "id", "name", "base_url", "credentials", "enabled",
    "credential_version", "token_revoked_at", "insecure",
    "created_at", "updated_at",
}


def _make_alembic_config(db_url: str):
    from alembic.config import Config

    ini_path = Path(database.ALEMBIC_INI_PATH)
    assert ini_path.exists(), f"alembic.ini missing at {ini_path}"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _column_names(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def _table_names(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _unique_constraint_names(engine, table: str) -> set[str]:
    return {
        uc["name"]
        for uc in inspect(engine).get_unique_constraints(table)
        if uc.get("name")
    }


@pytest.mark.integration
class TestMigration0023Fresh:
    def test_upgrade_adds_columns_and_table(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'm0023_fresh.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0023")

        engine = create_engine(db_url)
        try:
            cloud_cols = _column_names(engine, CLOUD_TABLE)
            assert NEW_CLOUD_COLUMNS <= cloud_cols

            assert SYNC_TABLE in _table_names(engine)
            assert SYNC_COLUMNS <= _column_names(engine, SYNC_TABLE)
            assert "uq_sync_targets_name" in _unique_constraint_names(engine, SYNC_TABLE)
        finally:
            engine.dispose()

    def test_downgrade_removes_columns_and_table(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'm0023_down.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0023")
        command.downgrade(cfg, "0022")

        engine = create_engine(db_url)
        try:
            cloud_cols = _column_names(engine, CLOUD_TABLE)
            assert not (NEW_CLOUD_COLUMNS & cloud_cols), (
                f"columns not dropped on downgrade: {NEW_CLOUD_COLUMNS & cloud_cols}"
            )
            assert SYNC_TABLE not in _table_names(engine)
        finally:
            engine.dispose()

    def test_round_trip_is_symmetric(self, tmp_path):
        """up → down → up must end at the same schema as a single up."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'm0023_rt.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0023")
        command.downgrade(cfg, "0022")
        command.upgrade(cfg, "0023")

        engine = create_engine(db_url)
        try:
            assert NEW_CLOUD_COLUMNS <= _column_names(engine, CLOUD_TABLE)
            assert SYNC_TABLE in _table_names(engine)
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0023PopulatedTable:
    """The REAL upgrade scenario: cloud_storage_targets already holds rows at
    0022. The NOT NULL credential_version add must backfill via server_default
    rather than fail."""

    def test_existing_row_backfills_credential_version(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'm0023_pop.db'}"
        cfg = _make_alembic_config(db_url)
        # Bring the DB up to 0022 ONLY (pre-feature schema).
        command.upgrade(cfg, "0022")

        engine = create_engine(db_url)
        try:
            # Insert a live target into the 0022-shaped table.
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO cloud_storage_targets "
                    "(name, provider_type, credentials, upload_path, enabled, "
                    " created_at, updated_at) "
                    "VALUES ('live-target', 's3', 'old-ciphertext', '/', 1, "
                    " '2026-06-17T00:00:00', '2026-06-17T00:00:00')"
                ))
        finally:
            engine.dispose()

        # Now apply 0023 against the populated table.
        command.upgrade(cfg, "0023")

        engine = create_engine(db_url)
        try:
            with engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT credential_version, token_revoked_at, insecure "
                    "FROM cloud_storage_targets WHERE name='live-target'"
                )).fetchone()
            assert row is not None, "live row vanished across the migration"
            assert row[0] == 1, f"expected credential_version backfill=1, got {row[0]}"
            assert row[1] is None, "token_revoked_at should backfill NULL"
            assert row[2] == 0, f"expected insecure backfill=0, got {row[2]}"
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0023Idempotent:
    """A DB drifted forward (columns + table already present) must re-run as a
    no-op, matching the smart-bootstrap stamp-forward path."""

    def test_reupgrade_after_drift_is_noop(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'm0023_idem.db'}"
        cfg = _make_alembic_config(db_url)
        # Apply once.
        command.upgrade(cfg, "0023")
        # Stamp back to 0022 WITHOUT dropping the artifacts (simulates the
        # create_all()-materialised-ORM-shape-while-version-behind drift).
        command.stamp(cfg, "0022")
        # Re-running the upgrade must not raise duplicate-column / table-exists.
        command.upgrade(cfg, "0023")

        engine = create_engine(db_url)
        try:
            assert NEW_CLOUD_COLUMNS <= _column_names(engine, CLOUD_TABLE)
            assert SYNC_TABLE in _table_names(engine)
        finally:
            engine.dispose()
