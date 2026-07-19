"""Migration tests for the journal automation marker (bead uliyr follow-up).

Covers revision **0037** — adds ``automated_client`` (Boolean, NULLABLE) to
``journal_entries``.

The column is intentionally nullable and additive: existing rows must read
back NULL — that NULL is load-bearing (the noise purge treats unmarked rows
as pre-marker legacy churn, so a backfill to False would freeze ~10k legacy
rows as "operator" forever, and a backfill to True would misclassify old
operator rows as purgeable when they already survived by predicate; NULL
preserves the PO's "legacy keeps aging out" decision exactly). No index —
the only consumer is the noise purge's already category/action-narrowed
scan (idx_journal_category covers it).

All fixtures use synthetic ids — no production-derived data.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

import database


TABLE = "journal_entries"
NEW_COL = "automated_client"


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


def _insert_legacy_entry(engine, entry_id: int) -> None:
    """Insert a journal row at the 0036 schema (no ``automated_client``)."""
    from datetime import datetime

    now = datetime.utcnow().isoformat()
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO journal_entries "
            "(id, timestamp, category, action_type, entity_name, description, "
            " user_initiated) "
            "VALUES (:id, :now, 'auto_creation', 'create', 'Legacy rule', "
            " 'Created before the automation marker', 1)"
        ), {"id": entry_id, "now": now})


@pytest.mark.integration
class TestMigration0037AutomatedClient:
    """Revision 0037 — additive nullable Boolean column, no index."""

    def test_column_added_nullable(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0037.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0037")

        engine = create_engine(db_url, future=True)
        try:
            cols = _columns(engine, TABLE)
            assert NEW_COL in cols, f"{NEW_COL} not added by upgrade to 0037"
            assert cols[NEW_COL]["nullable"] is True, "automated_client must be nullable"
        finally:
            engine.dispose()

    def test_existing_rows_read_back_null(self, tmp_path):
        """A legacy row predating 0037 must read back automated_client=NULL —
        the noise purge's 'unmarked legacy' classification depends on it."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0037_legacy.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0036")

        engine = create_engine(db_url, future=True)
        try:
            _insert_legacy_entry(engine, entry_id=1)
        finally:
            engine.dispose()

        command.upgrade(cfg, "0037")

        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                val = conn.execute(text(
                    f"SELECT {NEW_COL} FROM {TABLE} WHERE id=1"
                )).scalar()
            assert val is None, "legacy row must read back NULL (unmarked)"
        finally:
            engine.dispose()

    def test_round_trip_up_down_up(self, tmp_path):
        """0037 must be cleanly reversible: up → down drops the column, up re-adds."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0037_round.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "0037")
        engine = create_engine(db_url, future=True)
        try:
            assert NEW_COL in _columns(engine, TABLE)
        finally:
            engine.dispose()

        command.downgrade(cfg, "0036")
        engine = create_engine(db_url, future=True)
        try:
            assert NEW_COL not in _columns(engine, TABLE), (
                f"{NEW_COL} still present after downgrade to 0036"
            )
        finally:
            engine.dispose()

        command.upgrade(cfg, "0037")
        engine = create_engine(db_url, future=True)
        try:
            assert NEW_COL in _columns(engine, TABLE), "re-upgrade must re-add column"
        finally:
            engine.dispose()
