"""Tests for ``database._bootstrap_alembic`` (bd-fwpzw).

The bootstrap is the only path that runs at every container start, so its
correctness directly determines whether post-baseline migrations land on
upgrade. This suite covers four shapes of journal.db a user can present at
startup:

1. Pre-Alembic install — user tables exist, ``alembic_version`` table missing
   (the population that originally hit the bug). Bootstrap must stamp at the
   baseline revision (NOT head) and then run ``upgrade head`` so 0002, 0003,
   0004 land.

2. Stamped-at-head-but-schema-at-baseline — the affected-user state created
   by the original buggy bootstrap. ``upgrade head`` is a no-op for these
   users; the self-heal canary check has to detect the divergence and
   recover by re-stamping at baseline.

3. Fresh install — empty DB, bootstrap must produce the full head schema.

4. Already at head, healthy — bootstrap must be idempotent and not log the
   self-heal error path.

5. Pathological — canaries still missing after self-heal (mocked
   ``command.upgrade`` no-op) must raise rather than silently continue.

Each test uses a temp file-backed SQLite DB. Alembic operations need a real
file URI (``ScriptDirectory`` + multi-statement transactions are unhappy
against ``:memory:`` engines created independently).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
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


def _alembic_config(db_file: Path):
    """Build an Alembic ``Config`` pointed at ``db_file``."""
    from alembic.config import Config

    cfg = Config(str(database.ALEMBIC_INI_PATH))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    return cfg


def _build_pre_alembic_install(db_file: Path) -> None:
    """Materialize a DB with the baseline schema but no ``alembic_version`` table.

    Strategy: run ``alembic upgrade 0001`` to land the baseline DDL — that IS
    the pre-Alembic schema by design — then drop the ``alembic_version``
    table so the bootstrap path sees it as an unstamped legacy install.
    """
    from alembic import command

    cfg = _alembic_config(db_file)
    command.upgrade(cfg, "0001")

    engine = _make_engine(db_file)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE alembic_version"))
    finally:
        engine.dispose()


def _build_stamped_at_head_missing_columns(db_file: Path) -> None:
    """Reproduce the affected-user state: stamped at head, schema at baseline.

    The original buggy bootstrap stamped ``alembic_version`` at head while
    leaving the physical schema at the baseline. ``upgrade head`` is then a
    no-op and post-baseline migrations never apply.
    """
    from alembic import command

    cfg = _alembic_config(db_file)
    command.upgrade(cfg, "0001")

    engine = _make_engine(db_file)
    head = database.get_alembic_head_revision()
    try:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE alembic_version SET version_num = :rev"),
                {"rev": head},
            )
    finally:
        engine.dispose()


def _table_columns(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _table_indexes(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA index_list({table})")).fetchall()
    return {row[1] for row in rows}


def _alembic_version(engine) -> str:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
    return row[0] if row else ""


class TestBootstrapAlembic:
    """End-to-end coverage for the four DB shapes ``init_db`` may encounter."""

    def test_bootstrap_pre_alembic_install_runs_all_migrations(self, tmp_path):
        """Pre-Alembic install: stamp at baseline, then upgrade head applies 0002-0004."""
        db_file = tmp_path / "pre_alembic.db"
        _build_pre_alembic_install(db_file)

        engine = _make_engine(db_file)
        try:
            # Sanity: baseline column from 0002 must NOT yet be present.
            assert "match_scope_target_group" not in _table_columns(engine, "auto_creation_rules")

            database._bootstrap_alembic(engine)

            head = database.get_alembic_head_revision()
            assert _alembic_version(engine) == head, (
                "alembic_version must be stamped at head after bootstrap"
            )
            assert "match_scope_target_group" in _table_columns(engine, "auto_creation_rules"), (
                "Migration 0002 column missing — bootstrap stamped instead of upgrading"
            )
            assert "idx_journal_batch_id" in _table_indexes(engine, "journal_entries"), (
                "Migration 0004 index missing — bootstrap stopped before head"
            )
        finally:
            engine.dispose()

    def test_bootstrap_self_heal_when_stamped_at_head_but_column_missing(self, tmp_path):
        """Already-affected user: stamped-at-head with baseline schema must self-heal."""
        db_file = tmp_path / "stamped_head.db"
        _build_stamped_at_head_missing_columns(db_file)

        engine = _make_engine(db_file)
        try:
            # Precondition: version is at head, but the canary column is absent.
            assert _alembic_version(engine) == database.get_alembic_head_revision()
            assert "match_scope_target_group" not in _table_columns(engine, "auto_creation_rules")

            database._bootstrap_alembic(engine)

            assert "match_scope_target_group" in _table_columns(engine, "auto_creation_rules"), (
                "Self-heal did not recover the missing canary column"
            )
            assert "idx_journal_batch_id" in _table_indexes(engine, "journal_entries"), (
                "Self-heal did not recover the missing canary index"
            )
            assert _alembic_version(engine) == database.get_alembic_head_revision()
        finally:
            engine.dispose()

    def test_bootstrap_fresh_install_creates_full_schema(self, tmp_path):
        """Empty DB: bootstrap must produce the full head schema."""
        db_file = tmp_path / "fresh.db"
        engine = _make_engine(db_file)
        try:
            database._bootstrap_alembic(engine)

            head = database.get_alembic_head_revision()
            assert _alembic_version(engine) == head
            # Spot-check a baseline-era column plus both post-baseline canaries.
            assert "name" in _table_columns(engine, "auto_creation_rules")
            assert "match_scope_target_group" in _table_columns(engine, "auto_creation_rules")
            assert "idx_journal_batch_id" in _table_indexes(engine, "journal_entries")
        finally:
            engine.dispose()

    def test_bootstrap_already_at_head_is_idempotent(self, tmp_path):
        """A healthy at-head DB must round-trip through bootstrap unchanged."""
        from alembic import command

        db_file = tmp_path / "at_head.db"
        cfg = _alembic_config(db_file)
        command.upgrade(cfg, "head")

        engine = _make_engine(db_file)
        try:
            head = database.get_alembic_head_revision()
            assert _alembic_version(engine) == head

            database._bootstrap_alembic(engine)

            assert _alembic_version(engine) == head
            assert "match_scope_target_group" in _table_columns(engine, "auto_creation_rules")
            assert "idx_journal_batch_id" in _table_indexes(engine, "journal_entries")
        finally:
            engine.dispose()

    def test_bootstrap_self_heal_raises_if_canary_still_missing_after_recovery(self, tmp_path):
        """If self-heal cannot restore the canaries, bootstrap must raise.

        Simulate a pathological case where ``command.upgrade`` is wedged
        (e.g. mounted-read-only versions dir, broken migration). The canary
        check after the recovery attempt must fail loudly rather than let
        the app start with an inconsistent schema.
        """
        db_file = tmp_path / "broken.db"
        _build_stamped_at_head_missing_columns(db_file)

        engine = _make_engine(db_file)
        try:
            # Make ``command.upgrade`` a no-op so the self-heal recovery
            # cannot actually advance the schema.
            with patch("alembic.command.upgrade", return_value=None):
                with pytest.raises(RuntimeError, match="self-heal failed"):
                    database._bootstrap_alembic(engine)
        finally:
            engine.dispose()
