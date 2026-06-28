"""Migration tests for the AI-mutation audit log (W3, bead vp1rx).

Covers revision **0027** — adds ``mutation_source`` (String(20), NULLABLE) to
``journal_entries`` plus the ``idx_journal_mutation_source`` index.

The column is intentionally nullable and additive: existing rows must read back
NULL (unknown actor), and the up/down round-trip must be clean. ``user_initiated``
is left untouched.

All fixtures use synthetic ids — no production-derived data.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

import database


TABLE = "journal_entries"
NEW_COL = "mutation_source"
INDEX = "idx_journal_mutation_source"


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


def _indexes(engine, table: str) -> set:
    return {ix["name"] for ix in inspect(engine).get_indexes(table)}


def _insert_legacy_entry(engine, entry_id: int) -> None:
    """Insert a journal row at the 0026 schema (no ``mutation_source`` column)."""
    from datetime import datetime

    now = datetime.utcnow().isoformat()
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO journal_entries "
            "(id, timestamp, category, action_type, entity_name, description, "
            " user_initiated) "
            "VALUES (:id, :now, 'channel', 'delete', 'Legacy Channel', "
            " 'Deleted before W3', 1)"
        ), {"id": entry_id, "now": now})


@pytest.mark.integration
class TestMigration0027MutationSource:
    """Revision 0027 — additive nullable column + index."""

    def test_column_added_nullable(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0027.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0027")

        engine = create_engine(db_url, future=True)
        try:
            cols = _columns(engine, TABLE)
            assert NEW_COL in cols, f"{NEW_COL} not added by upgrade to 0027"
            assert cols[NEW_COL]["nullable"] is True, "mutation_source must be nullable"
            assert INDEX in _indexes(engine, TABLE), f"{INDEX} not created"
        finally:
            engine.dispose()

    def test_existing_rows_read_back_null(self, tmp_path):
        """A legacy row predating 0027 must read back mutation_source=NULL."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0027_legacy.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0026")

        engine = create_engine(db_url, future=True)
        try:
            _insert_legacy_entry(engine, entry_id=1)
        finally:
            engine.dispose()

        command.upgrade(cfg, "0027")

        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                val = conn.execute(text(
                    f"SELECT {NEW_COL} FROM {TABLE} WHERE id=1"
                )).scalar()
            assert val is None, "legacy row must back-fill to NULL (unknown actor)"
        finally:
            engine.dispose()

    def test_round_trip_up_down_up(self, tmp_path):
        """0027 must be cleanly reversible: up → down drops col+index, up re-adds."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0027_round.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "0027")
        engine = create_engine(db_url, future=True)
        try:
            assert NEW_COL in _columns(engine, TABLE)
            assert INDEX in _indexes(engine, TABLE)
        finally:
            engine.dispose()

        command.downgrade(cfg, "0026")
        engine = create_engine(db_url, future=True)
        try:
            assert NEW_COL not in _columns(engine, TABLE), (
                f"{NEW_COL} still present after downgrade to 0026"
            )
            assert INDEX not in _indexes(engine, TABLE), (
                f"{INDEX} still present after downgrade to 0026"
            )
        finally:
            engine.dispose()

        command.upgrade(cfg, "0027")
        engine = create_engine(db_url, future=True)
        try:
            assert NEW_COL in _columns(engine, TABLE), "re-upgrade must re-add column"
            assert INDEX in _indexes(engine, TABLE), "re-upgrade must re-add index"
        finally:
            engine.dispose()
