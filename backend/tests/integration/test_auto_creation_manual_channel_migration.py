"""Migration tests for the manual-channel isolation work (W1, bead orzck).

Covers two revisions:

* **0025** — adds ``allow_manual_channel_merge`` (Boolean, NOT NULL, default
  False) to ``auto_creation_rules``. Existing rows are backfilled to 0, and the
  server default is dropped so the drift test sees "no default" (mirroring the
  0002 convention).
* **0026** — a data migration that flips ``match_scope_target_group`` to True
  for every existing rule (they were backfilled to False by 0002).

All fixtures use synthetic ids — no production-derived data.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

import database


TABLE = "auto_creation_rules"
NEW_COL = "allow_manual_channel_merge"
SCOPE_COL = "match_scope_target_group"


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


def _insert_rule(engine, rule_id: int, scope: int) -> None:
    """Insert a minimal auto_creation_rules row.

    Works at both the 0024 schema (no ``allow_manual_channel_merge`` column)
    and the 0025+ schema (column present, NOT NULL) by only naming the new
    column when it exists.
    """
    from datetime import datetime

    now = datetime.utcnow().isoformat()
    have_new_col = NEW_COL in _columns(engine, TABLE)
    extra_cols = f", {NEW_COL}" if have_new_col else ""
    extra_vals = ", 0" if have_new_col else ""
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO auto_creation_rules "
            "(id, name, enabled, priority, conditions, actions, "
            " run_on_refresh, stop_on_first_match, probe_on_sort, "
            " skip_struck_streams, quality_m3u_tie_break_enabled, match_count, "
            f" orphan_action, match_scope_target_group{extra_cols}, "
            " created_at, updated_at) "
            "VALUES (:id, :name, 1, 0, '[]', '[]', 0, 1, 0, 0, 1, 0, 'delete', "
            f" :scope{extra_vals}, :now, :now)"
        ), {"id": rule_id, "name": f"Rule {rule_id}", "scope": scope, "now": now})


@pytest.mark.integration
class TestMigration0025AllowManualChannelMerge:
    """Revision 0025 — additive column with safe default False."""

    def test_column_added_with_default_false(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0025.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0025")

        engine = create_engine(db_url, future=True)
        try:
            cols = _columns(engine, TABLE)
            assert NEW_COL in cols, f"{NEW_COL} not added by upgrade to 0025"
            assert cols[NEW_COL]["nullable"] is False
            # Server default dropped (drift-test convention): no SQL default.
            assert cols[NEW_COL].get("default") in (None, "", "NULL")
        finally:
            engine.dispose()

    def test_existing_rows_backfilled_to_false(self, tmp_path):
        """A rule that predates 0025 must read back allow_manual_channel_merge=0."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0025_backfill.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0024")

        engine = create_engine(db_url, future=True)
        try:
            _insert_rule(engine, rule_id=1, scope=0)
        finally:
            engine.dispose()

        command.upgrade(cfg, "0025")

        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                val = conn.execute(text(
                    f"SELECT {NEW_COL} FROM {TABLE} WHERE id=1"
                )).scalar()
            assert val == 0, "existing rule must backfill to False (protected)"
        finally:
            engine.dispose()

    def test_downgrade_drops_column(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0025_down.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0025")

        engine = create_engine(db_url, future=True)
        try:
            assert NEW_COL in _columns(engine, TABLE)
        finally:
            engine.dispose()

        command.downgrade(cfg, "0024")

        engine = create_engine(db_url, future=True)
        try:
            assert NEW_COL not in _columns(engine, TABLE), (
                f"{NEW_COL} still present after downgrade to 0024"
            )
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0026ScopeExistingRules:
    """Revision 0026 — data migration flips existing rules onto scoped lookups."""

    def test_existing_unscoped_rules_flipped_to_scoped(self, tmp_path):
        """A rule stored with match_scope_target_group=0 becomes 1 after 0026."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0026_flip.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0025")

        engine = create_engine(db_url, future=True)
        try:
            _insert_rule(engine, rule_id=1, scope=0)  # unscoped (legacy)
            _insert_rule(engine, rule_id=2, scope=1)  # already scoped
        finally:
            engine.dispose()

        command.upgrade(cfg, "0026")

        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                rows = dict(conn.execute(text(
                    f"SELECT id, {SCOPE_COL} FROM {TABLE} ORDER BY id"
                )).fetchall())
            assert rows[1] == 1, "legacy unscoped rule must be flipped to scoped"
            assert rows[2] == 1, "already-scoped rule stays scoped"
        finally:
            engine.dispose()

    def test_data_migration_is_idempotent(self, tmp_path):
        """Running 0026's UPDATE twice leaves every rule scoped, no error."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0026_idem.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0025")

        engine = create_engine(db_url, future=True)
        try:
            _insert_rule(engine, rule_id=1, scope=0)
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")
        # Re-run the idempotent UPDATE directly — must not raise or unset.
        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    f"UPDATE {TABLE} SET {SCOPE_COL} = 1 WHERE {SCOPE_COL} = 0"
                ))
            with engine.connect() as conn:
                val = conn.execute(text(
                    f"SELECT {SCOPE_COL} FROM {TABLE} WHERE id=1"
                )).scalar()
            assert val == 1
        finally:
            engine.dispose()
