"""Tests for journal_entries indexes and retention policy (bd-dmu8w).

After bd-91mcq landed per-entity journal rows with a shared ``batch_id``
for bulk auto-creation operations, two latent gaps surfaced:

1. ``journal_entries.batch_id`` had no index → forensic
   "show me batch X" queries full-scan.
2. No composite ``(category, entity_id, timestamp DESC)`` index →
   "history for rule N" queries full-scan.
3. The ``CleanupTask`` default ``journal_days`` retention was 30, but
   bulk operations now amplify row growth N-fold; the documented hot
   retention is 90 days.

This module covers all three gaps in one place so a future regression
that drops an index or shortens the default retention without thought
is caught immediately.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text


# ---------------------------------------------------------------------------
# Index existence tests (model + migration must agree)
# ---------------------------------------------------------------------------


class TestJournalEntryIndexes:
    """The JournalEntry SQLAlchemy model must declare the new indexes.

    These tests inspect ``JournalEntry.__table__.indexes`` so the model
    stays in sync with the Alembic migration. If autogenerate ever drops
    one of these from the declarative side, this test fires before the
    drift test does.
    """

    def test_batch_id_index_declared_on_model(self):
        from models import JournalEntry

        index_names = {idx.name for idx in JournalEntry.__table__.indexes}
        assert "idx_journal_batch_id" in index_names, (
            f"idx_journal_batch_id missing from JournalEntry model "
            f"(found: {sorted(index_names)})"
        )

    def test_batch_id_index_is_single_column_on_batch_id(self):
        from models import JournalEntry

        idx = next(
            (i for i in JournalEntry.__table__.indexes if i.name == "idx_journal_batch_id"),
            None,
        )
        assert idx is not None
        cols = [c.name for c in idx.columns]
        assert cols == ["batch_id"], (
            f"idx_journal_batch_id should index exactly ['batch_id'], got {cols}"
        )

    def test_entity_composite_index_declared_on_model(self):
        from models import JournalEntry

        index_names = {idx.name for idx in JournalEntry.__table__.indexes}
        assert "idx_journal_entity" in index_names, (
            f"idx_journal_entity missing from JournalEntry model "
            f"(found: {sorted(index_names)})"
        )

    def test_entity_composite_index_columns(self):
        """Composite must order ``category, entity_id, timestamp`` so that
        the leading-column rule covers ``WHERE category=? AND entity_id=?
        ORDER BY timestamp DESC`` queries — the "history for rule N" path."""
        from models import JournalEntry

        idx = next(
            (i for i in JournalEntry.__table__.indexes if i.name == "idx_journal_entity"),
            None,
        )
        assert idx is not None
        cols = [c.name for c in idx.columns]
        # SQLAlchemy reports the column expression's name; descending order
        # is encoded on the expression, not the column name.
        assert cols == ["category", "entity_id", "timestamp"], (
            f"idx_journal_entity should index ['category', 'entity_id', 'timestamp'], got {cols}"
        )


class TestJournalEntryIndexesInDatabase:
    """SQLite must actually carry the indexes after metadata.create_all."""

    def test_batch_id_index_present_in_sqlite(self, test_engine):
        with test_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='journal_entries'"
                )
            ).fetchall()
        names = {r[0] for r in rows}
        assert "idx_journal_batch_id" in names, (
            f"idx_journal_batch_id missing from sqlite_master indexes "
            f"(found: {sorted(names)})"
        )

    def test_entity_composite_index_present_in_sqlite(self, test_engine):
        with test_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='journal_entries'"
                )
            ).fetchall()
        names = {r[0] for r in rows}
        assert "idx_journal_entity" in names, (
            f"idx_journal_entity missing from sqlite_master indexes "
            f"(found: {sorted(names)})"
        )

    def test_batch_id_query_uses_index(self, test_engine):
        """EXPLAIN QUERY PLAN must report SEARCH using ``idx_journal_batch_id``
        for a batch_id equality lookup. If SQLite picks SCAN, the index is
        either missing or unusable for this access pattern."""
        with test_engine.connect() as conn:
            plan = conn.execute(
                text(
                    "EXPLAIN QUERY PLAN "
                    "SELECT id FROM journal_entries WHERE batch_id = 'b1'"
                )
            ).fetchall()
        plan_text = " | ".join(str(row) for row in plan)
        assert "idx_journal_batch_id" in plan_text, (
            f"Expected query plan to use idx_journal_batch_id, got: {plan_text}"
        )

    def test_entity_history_query_uses_composite_index(self, test_engine):
        """EXPLAIN QUERY PLAN must report SEARCH using ``idx_journal_entity``
        for the history-for-rule access pattern (``category = ? AND entity_id = ?``)."""
        with test_engine.connect() as conn:
            plan = conn.execute(
                text(
                    "EXPLAIN QUERY PLAN "
                    "SELECT id FROM journal_entries "
                    "WHERE category = 'channel' AND entity_id = 42 "
                    "ORDER BY timestamp DESC"
                )
            ).fetchall()
        plan_text = " | ".join(str(row) for row in plan)
        assert "idx_journal_entity" in plan_text, (
            f"Expected query plan to use idx_journal_entity, got: {plan_text}"
        )


# ---------------------------------------------------------------------------
# Migration tests — the new revision must apply cleanly on a fresh DB and
# on a DB pre-populated with journal rows from the baseline schema.
# ---------------------------------------------------------------------------


def _make_alembic_config(db_url: str):
    import database
    from alembic.config import Config

    ini_path = Path(database.ALEMBIC_INI_PATH)
    assert ini_path.exists(), f"alembic.ini missing at {ini_path}"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _journal_index_names(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='journal_entries'"
            )
        ).fetchall()
    return {r[0] for r in rows}


class TestJournalIndexMigration:
    """Revision must add the two new indexes and round-trip cleanly."""

    def test_upgrade_head_creates_both_indexes_on_fresh_db(self, tmp_path):
        from alembic import command

        db_file = tmp_path / "fresh.db"
        db_url = f"sqlite:///{db_file}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        try:
            names = _journal_index_names(engine)
        finally:
            engine.dispose()

        assert "idx_journal_batch_id" in names, (
            f"Expected idx_journal_batch_id after upgrade head; got {sorted(names)}"
        )
        assert "idx_journal_entity" in names, (
            f"Expected idx_journal_entity after upgrade head; got {sorted(names)}"
        )

    def test_upgrade_preserves_existing_journal_rows(self, tmp_path):
        """Pre-populate journal_entries before applying the new revision and
        confirm row count + values survive the migration. Index creation
        must not lose data."""
        from alembic import command

        db_file = tmp_path / "prepop.db"
        db_url = f"sqlite:///{db_file}"
        cfg = _make_alembic_config(db_url)

        # First, upgrade to the revision *before* the new index migration.
        # We use a constant marker so future migrations don't break this
        # test — we always step to the prior head, insert rows, then
        # complete the upgrade.
        command.upgrade(cfg, "0003")

        engine = create_engine(db_url)
        try:
            with engine.begin() as conn:
                for i in range(50):
                    conn.execute(
                        text(
                            "INSERT INTO journal_entries "
                            "(timestamp, category, action_type, entity_id, "
                            " entity_name, description, user_initiated, batch_id) "
                            "VALUES (:ts, :cat, :act, :eid, :ename, :desc, :ui, :bid)"
                        ),
                        {
                            "ts": datetime.utcnow().isoformat(),
                            "cat": "channel",
                            "act": "create",
                            "eid": i,
                            "ename": f"channel-{i}",
                            "desc": f"created channel {i}",
                            "ui": False,
                            "bid": f"batch-{i // 10}",
                        },
                    )
        finally:
            engine.dispose()

        # Now apply the new revision (and any newer ones).
        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        try:
            with engine.connect() as conn:
                count = conn.execute(
                    text("SELECT COUNT(*) FROM journal_entries")
                ).scalar()
            names = _journal_index_names(engine)
        finally:
            engine.dispose()

        assert count == 50, f"Row count changed across migration: expected 50, got {count}"
        assert "idx_journal_batch_id" in names
        assert "idx_journal_entity" in names

    def test_downgrade_drops_indexes(self, tmp_path):
        """Round-trip safety: reverting revision ``0004`` must drop both indexes.

        Do not use ``downgrade -1``: newer heads (e.g. ``0005`` auto-creation
        columns) sit above ``0004``, so a single step only peels the latest
        revision and leaves ``idx_journal_*`` in place. Target ``0003``
        explicitly — the revision *before* ``0004`` — so journal indexes are
        always removed regardless of how many migrations follow ``0004``."""
        from alembic import command

        db_file = tmp_path / "rt.db"
        db_url = f"sqlite:///{db_file}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        try:
            after_up = _journal_index_names(engine)
        finally:
            engine.dispose()
        assert "idx_journal_batch_id" in after_up
        assert "idx_journal_entity" in after_up

        command.downgrade(cfg, "0003")

        engine = create_engine(db_url)
        try:
            after_down = _journal_index_names(engine)
        finally:
            engine.dispose()
        assert "idx_journal_batch_id" not in after_down, (
            f"downgrade left idx_journal_batch_id behind: {sorted(after_down)}"
        )
        assert "idx_journal_entity" not in after_down, (
            f"downgrade left idx_journal_entity behind: {sorted(after_down)}"
        )

        # Re-upgrade must restore both.
        command.upgrade(cfg, "head")
        engine = create_engine(db_url)
        try:
            after_reup = _journal_index_names(engine)
        finally:
            engine.dispose()
        assert "idx_journal_batch_id" in after_reup
        assert "idx_journal_entity" in after_reup


# ---------------------------------------------------------------------------
# Retention policy tests — CleanupTask must default to 90-day journal
# retention (per bd-dmu8w) and prune correctly.
# ---------------------------------------------------------------------------


class TestCleanupTaskJournalRetentionDefault:
    """The default journal retention must be 90 days (hot retention).

    Bulk auto-creation can produce thousands of journal rows per run.
    Keeping the documented 90-day window matches ``journal.purge_old_entries``
    and prevents silent table bloat from a too-conservative default.
    """

    def test_default_journal_days_is_90(self):
        from tasks.cleanup import CleanupTask

        task = CleanupTask()
        assert task.journal_days == 90, (
            f"CleanupTask.journal_days default must be 90 days "
            f"(got {task.journal_days}). bd-dmu8w aligns the cleanup task "
            f"with journal.purge_old_entries default and the documented "
            f"hot-retention window."
        )

    def test_get_config_reports_journal_days(self):
        from tasks.cleanup import CleanupTask

        cfg = CleanupTask().get_config()
        assert cfg["journal_days"] == 90


class TestCleanupTaskPrunesJournalEntries:
    """End-to-end: running the cleanup task removes rows older than the
    configured threshold and keeps fresher rows untouched."""

    @pytest.mark.asyncio
    async def test_prunes_only_rows_older_than_threshold(self, test_session, test_engine):
        from tests.fixtures.factories import create_journal_entry
        from models import JournalEntry

        now = datetime.utcnow()
        # Older than threshold (should be deleted)
        create_journal_entry(test_session, timestamp=now - timedelta(days=100))
        create_journal_entry(test_session, timestamp=now - timedelta(days=120))
        # Within threshold (should be kept)
        create_journal_entry(test_session, timestamp=now - timedelta(days=10))
        create_journal_entry(test_session, timestamp=now - timedelta(days=89))
        create_journal_entry(test_session, timestamp=now)

        from tasks.cleanup import CleanupTask
        task = CleanupTask()
        # Force vacuum off — VACUUM cannot run inside the test transaction
        # context and is irrelevant to the prune assertion.
        task.vacuum_db = False

        # Patch get_session inside the task module so the task uses the
        # in-memory test DB.
        with patch("tasks.cleanup.get_session", return_value=test_session):
            result = await task.execute()

        assert result.success, f"Cleanup task should succeed: {result.message}"

        # Re-query through the same session: only the 3 in-window rows remain.
        remaining = test_session.query(JournalEntry).all()
        remaining_ages_days = sorted(
            int((now - r.timestamp).total_seconds() // 86400) for r in remaining
        )
        assert len(remaining) == 3, (
            f"Expected 3 rows within 90-day window after prune, got "
            f"{len(remaining)} (ages: {remaining_ages_days})"
        )
        for age in remaining_ages_days:
            assert age <= 89, (
                f"Row with age {age}d survived prune but should have been "
                f"deleted (threshold = 90d)"
            )

    @pytest.mark.asyncio
    async def test_keeps_rows_when_journal_is_empty(self, test_session, test_engine):
        from tasks.cleanup import CleanupTask
        task = CleanupTask()
        task.vacuum_db = False

        with patch("tasks.cleanup.get_session", return_value=test_session):
            result = await task.execute()

        assert result.success
        # No rows existed; deleted count should be 0.
        deleted = result.details.get("deleted", {})
        assert deleted.get("journal_entries", 0) == 0


class TestCleanupTaskRegistration:
    """The cleanup task must be discoverable via the task registry so the
    scheduler actually runs it. Regressions here would silently disable
    journal pruning."""

    def test_cleanup_task_is_registered(self):
        # Importing the package triggers @register_task side effects.
        import tasks  # noqa: F401
        from task_registry import get_registry

        registry = get_registry()
        cleanup = registry.get_task_class("cleanup")
        assert cleanup is not None, (
            "CleanupTask must be registered under task_id='cleanup' so the "
            "scheduler can run journal retention. Check tasks/__init__.py "
            "imports cleanup and the @register_task decorator is intact."
        )
