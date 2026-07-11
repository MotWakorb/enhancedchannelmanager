"""Migration tests for the event_sync rule kind (bead ti939.1.3).

Covers revision **0031** — adds ``event_sync_config`` (Text, NULLABLE) to
``auto_creation_rules``. Follows the 0027 idempotency pattern (guarded
add_column; SQLite; no batch mode; clean downgrade).

The column is intentionally nullable and additive: pre-feature rule rows
must read back NULL (standard rule kind, unchanged behavior), and the
up/down round-trip must be clean. All fixtures use synthetic data.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

import database


TABLE = "auto_creation_rules"
NEW_COL = "event_sync_config"


def _make_alembic_config(db_url: str):
    """Build an Alembic Config pinned to *db_url* (self-contained per convention)."""
    from alembic.config import Config

    ini_path = Path(database.ALEMBIC_INI_PATH)
    assert ini_path.exists(), f"alembic.ini missing at {ini_path}"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _columns(engine, table: str) -> dict:
    return {c["name"]: c for c in inspect(engine).get_columns(table)}


def _insert_legacy_rule(engine, rule_id: int) -> None:
    """Insert a rule row at the 0030 schema (no ``event_sync_config`` column)."""
    from datetime import datetime

    now = datetime.utcnow().isoformat()
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO auto_creation_rules "
            "(id, name, enabled, priority, conditions, actions, "
            " run_on_refresh, stop_on_first_match, probe_on_sort, "
            " quality_m3u_tie_break_enabled, skip_struck_streams, "
            " orphan_action, match_scope_target_group, "
            " allow_manual_channel_merge, created_at, updated_at) "
            "VALUES (:id, 'Legacy Rule', 1, 0, '[]', '[]', "
            " 0, 1, 0, 1, 0, 'delete', 1, 0, :now, :now)"
        ), {"id": rule_id, "now": now})


@pytest.mark.integration
class TestMigration0031EventSyncConfig:
    """Revision 0031 — additive nullable column, 0027 idempotency pattern."""

    def test_column_added_nullable(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0031.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0031")

        engine = create_engine(db_url, future=True)
        try:
            cols = _columns(engine, TABLE)
            assert NEW_COL in cols, f"{NEW_COL} not added by upgrade to 0031"
            assert cols[NEW_COL]["nullable"] is True, (
                "event_sync_config must be nullable — NULL is the standard "
                "(pre-feature) rule kind"
            )
        finally:
            engine.dispose()

    def test_existing_rules_read_back_null(self, tmp_path):
        """Backward compat: a rule row predating 0031 must read back
        event_sync_config=NULL — i.e. load unchanged as a standard rule."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0031_legacy.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0030")

        engine = create_engine(db_url, future=True)
        try:
            _insert_legacy_rule(engine, rule_id=1)
        finally:
            engine.dispose()

        command.upgrade(cfg, "0031")

        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                row = conn.execute(text(
                    f"SELECT name, enabled, priority, conditions, actions, "
                    f"{NEW_COL} FROM {TABLE} WHERE id=1"
                )).fetchone()
            assert row is not None, "legacy rule row lost by migration"
            name, enabled, priority, conditions, actions, es_config = row
            # The pre-feature row is byte-identical apart from the new NULL.
            assert name == "Legacy Rule"
            assert enabled == 1
            assert priority == 0
            assert conditions == "[]"
            assert actions == "[]"
            assert es_config is None, (
                "legacy rule must back-fill to NULL (standard rule kind)"
            )
        finally:
            engine.dispose()

    def test_round_trip_up_down_up(self, tmp_path):
        """0031 must be cleanly reversible: up → down drops the column, up re-adds."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0031_round.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "0031")
        engine = create_engine(db_url, future=True)
        try:
            assert NEW_COL in _columns(engine, TABLE)
        finally:
            engine.dispose()

        command.downgrade(cfg, "0030")
        engine = create_engine(db_url, future=True)
        try:
            assert NEW_COL not in _columns(engine, TABLE), (
                f"{NEW_COL} still present after downgrade to 0030"
            )
        finally:
            engine.dispose()

        command.upgrade(cfg, "0031")
        engine = create_engine(db_url, future=True)
        try:
            assert NEW_COL in _columns(engine, TABLE), "re-upgrade must re-add column"
        finally:
            engine.dispose()

    def test_downgrade_preserves_other_columns_and_rows(self, tmp_path):
        """Downgrade drops ONLY event_sync_config — rule rows survive."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0031_down.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0031")

        engine = create_engine(db_url, future=True)
        try:
            _insert_legacy_rule(engine, rule_id=7)
        finally:
            engine.dispose()

        command.downgrade(cfg, "0030")

        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                row = conn.execute(text(
                    f"SELECT name FROM {TABLE} WHERE id=7"
                )).fetchone()
            assert row is not None and row[0] == "Legacy Rule"
        finally:
            engine.dispose()

    def test_upgrade_idempotent_when_column_pre_exists(self, tmp_path):
        """bd-5w6jz race: create_all() may have materialised the column before
        alembic runs. The guarded add_column must skip, not raise."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0031_idem.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0030")

        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    f"ALTER TABLE {TABLE} ADD COLUMN {NEW_COL} TEXT"
                ))
        finally:
            engine.dispose()

        # Must not raise "duplicate column name".
        command.upgrade(cfg, "0031")

        engine = create_engine(db_url, future=True)
        try:
            assert NEW_COL in _columns(engine, TABLE)
        finally:
            engine.dispose()
