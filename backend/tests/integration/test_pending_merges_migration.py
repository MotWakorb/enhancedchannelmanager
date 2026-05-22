"""Migration tests for ``pending_merges`` + ``pending_merge_journal`` (revision 0014).

Bead: ``enhancedchannelmanager-6by2n`` (BD-C of the v0.17.1 interactive
stream dedup epic, ADR-008 §D8).

Covers:

* Fresh up from 0013 → both tables present with all seven indexes and both
  named CHECK constraints (status + action_type).
* Fresh down from 0014 → both tables and all seven indexes gone.
* bd-5w6jz idempotency: re-run the migration on a DB where one or both
  tables have already been materialised by ``create_all()`` from the
  post-0014 ORM model (long-running install scenario). Each CREATE TABLE
  / CREATE INDEX is independently guarded so any subset of pre-existing
  artifacts is safe to re-encounter.
* CHECK enforcement: a direct INSERT with ``status='bogus'`` on
  ``pending_merges`` or with ``action_type='not_an_action'`` on
  ``pending_merge_journal`` raises ``IntegrityError``.
* §D5 partial-unique invariant: two ``pending`` rows with the same
  ``(stream_name, candidate_channel_id)`` pair → second raises
  IntegrityError. A ``merged`` + ``pending`` pair on the same key → both
  succeed (the partial WHERE clause excludes ``merged`` from the
  uniqueness scope).
* FK enforcement on ``pending_merge_journal.pending_merge_id`` → insert
  with a non-existent FK target raises ``IntegrityError`` when SQLite FK
  enforcement is on. The connect listener in ``database.py`` sets
  ``PRAGMA foreign_keys=ON`` on every connect, so the test environment
  matches production. A debug-only ``sqlite3`` CLI session would
  silently insert (FK is declarative without the PRAGMA) — that
  divergence is documented in the migration docstring's PRAGMA caveat.

ADR deviation note (carried verbatim from the migration docstring):
``trigger_context`` is app-validated at write time rather than CHECK-ed
at the DB layer. ADR-008 §D8 originally specified
``CHECK in ('drag_drop','add_stream','m3u_refresh','mcp_tool')`` for both
tables; the PO's BD-C implementation brief downgraded both to
app-validated enums so future surface additions do not force a schema
migration. The four canonical tags remain unchanged.

All fixtures use synthetic identities — no production-derived channel
UUIDs or token strings.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

import database


PENDING_MERGES = "pending_merges"
JOURNAL = "pending_merge_journal"

EXPECTED_PENDING_MERGES_INDEXES = {
    "idx_pending_merges_group_created": ["group_id", "created_at"],
    "idx_pending_merges_status_created": ["status", "created_at"],
    "idx_pending_merges_candidate": ["candidate_channel_id"],
    "uq_pending_merges_active": ["stream_name", "candidate_channel_id"],
}

EXPECTED_JOURNAL_INDEXES = {
    "idx_pending_merge_journal_pending": ["pending_merge_id"],
    "idx_pending_merge_journal_time": ["timestamp_utc"],
    "idx_pending_merge_journal_actor": ["actor_token_id"],
}

PENDING_MERGES_CHECK = "ck_pending_merges_status"
JOURNAL_CHECK = "ck_pending_merge_journal_action_type"


def _make_alembic_config(db_url: str):
    """Build an Alembic Config pinned to *db_url*.

    Mirrors the helper in ``test_session_telemetry_migration.py`` so each
    test file is self-contained and can diverge without shared-helper
    churn (`docs/database_migrations.md` test-isolation convention).
    """
    from alembic.config import Config

    ini_path = Path(database.ALEMBIC_INI_PATH)
    assert ini_path.exists(), f"alembic.ini missing at {ini_path}"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _table_names(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _index_map(engine, table: str) -> dict[str, list[str]]:
    """Return ``{index_name: [column, ...]}`` for *table*."""
    return {
        idx["name"]: list(idx["column_names"])
        for idx in inspect(engine).get_indexes(table)
    }


def _check_constraint_names(engine, table: str) -> set[str]:
    """Return the set of named CHECK constraint identifiers on *table*."""
    return {
        cc["name"]
        for cc in inspect(engine).get_check_constraints(table)
        if cc.get("name")
    }


def _foreign_keys(engine, table: str) -> list[dict]:
    """Return the FK descriptors on *table* (SQLAlchemy Inspector shape)."""
    return list(inspect(engine).get_foreign_keys(table))


@pytest.mark.integration
class TestMigration0014Fresh:
    """Revision 0014 — fresh upgrade creates both tables + 7 indexes."""

    def test_migration_0014_creates_both_tables_fresh_install(self, tmp_path):
        """Empty DB → ``alembic upgrade head`` lands both tables intact.

        Asserts every column name from ADR-008 §D8 is present, both named
        CHECKs are present, the FK on ``pending_merge_journal`` points
        at ``pending_merges.id`` with ``ON DELETE RESTRICT``, and every
        documented index exists with its expected column list.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0014_fresh.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            # Both tables exist.
            names = _table_names(engine)
            assert PENDING_MERGES in names, (
                f"{PENDING_MERGES} not created by upgrade head"
            )
            assert JOURNAL in names, (
                f"{JOURNAL} not created by upgrade head"
            )

            # pending_merges column set (ADR-008 §D8 verbatim).
            pm_cols = {c["name"]: c for c in inspect(engine).get_columns(PENDING_MERGES)}
            assert set(pm_cols) == {
                "id", "stream_name", "group_id", "candidate_channel_id",
                "confidence", "status", "created_at", "resolved_at",
                "resolution_source", "trigger_context",
            }, (
                f"pending_merges column set differs from ADR-008 §D8: "
                f"{sorted(pm_cols)}"
            )
            assert pm_cols["stream_name"]["nullable"] is False
            assert pm_cols["group_id"]["nullable"] is True
            assert pm_cols["candidate_channel_id"]["nullable"] is False
            assert pm_cols["confidence"]["nullable"] is False
            assert pm_cols["status"]["nullable"] is False
            assert pm_cols["created_at"]["nullable"] is False
            assert pm_cols["resolved_at"]["nullable"] is True
            assert pm_cols["resolution_source"]["nullable"] is True
            assert pm_cols["trigger_context"]["nullable"] is False

            # pending_merge_journal column set (ADR-008 §D8 verbatim).
            jr_cols = {c["name"]: c for c in inspect(engine).get_columns(JOURNAL)}
            assert set(jr_cols) == {
                "id", "pending_merge_id", "actor_token_id", "action_type",
                "source_channel_id", "target_channel_id", "confidence_score",
                "timestamp_utc", "trigger_context",
            }, (
                f"pending_merge_journal column set differs from ADR-008 §D8: "
                f"{sorted(jr_cols)}"
            )
            for col in (
                "pending_merge_id", "actor_token_id", "action_type",
                "source_channel_id", "target_channel_id", "confidence_score",
                "timestamp_utc", "trigger_context",
            ):
                assert jr_cols[col]["nullable"] is False, (
                    f"{col} must be NOT NULL per ADR-008 §D8 / §D6"
                )

            # All 4 pending_merges indexes.
            pm_idx = _index_map(engine, PENDING_MERGES)
            for name, cols in EXPECTED_PENDING_MERGES_INDEXES.items():
                assert name in pm_idx, (
                    f"missing pending_merges index {name}: have {sorted(pm_idx)}"
                )
                assert pm_idx[name] == cols, (
                    f"index {name} columns {pm_idx[name]} != expected {cols}"
                )
            # Partial unique index must be marked unique on the inspector.
            uq_descriptor = next(
                idx
                for idx in inspect(engine).get_indexes(PENDING_MERGES)
                if idx["name"] == "uq_pending_merges_active"
            )
            # SQLAlchemy's SQLite dialect returns ``unique`` as ``1`` (int),
            # not ``True`` — coerce to bool before comparing.
            assert bool(uq_descriptor["unique"]), (
                "uq_pending_merges_active must be UNIQUE per §D5"
            )

            # All 3 pending_merge_journal indexes.
            jr_idx = _index_map(engine, JOURNAL)
            for name, cols in EXPECTED_JOURNAL_INDEXES.items():
                assert name in jr_idx, (
                    f"missing journal index {name}: have {sorted(jr_idx)}"
                )
                assert jr_idx[name] == cols, (
                    f"index {name} columns {jr_idx[name]} != expected {cols}"
                )

            # Named CHECK constraints (SQLite-stability requirement).
            assert PENDING_MERGES_CHECK in _check_constraint_names(engine, PENDING_MERGES), (
                f"{PENDING_MERGES_CHECK} missing from {PENDING_MERGES}"
            )
            assert JOURNAL_CHECK in _check_constraint_names(engine, JOURNAL), (
                f"{JOURNAL_CHECK} missing from {JOURNAL}"
            )

            # FK on journal.pending_merge_id → pending_merges.id with RESTRICT.
            fks = _foreign_keys(engine, JOURNAL)
            assert len(fks) == 1, (
                f"expected exactly one FK on {JOURNAL}, got {fks!r}"
            )
            fk = fks[0]
            assert fk["constrained_columns"] == ["pending_merge_id"]
            assert fk["referred_table"] == "pending_merges"
            assert fk["referred_columns"] == ["id"]
            # ondelete is exposed in fk["options"] for SQLite.
            assert fk.get("options", {}).get("ondelete", "").upper() == "RESTRICT", (
                "FK must use ON DELETE RESTRICT per BD-C brief: "
                f"got options={fk.get('options')}"
            )

            # pending_merges should have NO foreign keys (candidate_channel_id
            # is intentionally not FK'd — §D4).
            assert _foreign_keys(engine, PENDING_MERGES) == [], (
                "pending_merges must have no FKs (channels is not an ECM "
                "table — ADR-008 §D4)"
            )
        finally:
            engine.dispose()

    def test_migration_0014_downgrade_drops_both_tables(self, tmp_path):
        """Downgrade 0014 → 0013 removes both tables and all seven indexes."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0014_down.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0014")

        engine = create_engine(db_url, future=True)
        try:
            assert PENDING_MERGES in _table_names(engine)
            assert JOURNAL in _table_names(engine)
        finally:
            engine.dispose()

        command.downgrade(cfg, "0013")

        engine = create_engine(db_url, future=True)
        try:
            names = _table_names(engine)
            assert PENDING_MERGES not in names, (
                f"{PENDING_MERGES} still present after downgrade to 0013"
            )
            assert JOURNAL not in names, (
                f"{JOURNAL} still present after downgrade to 0013"
            )
            # Sanity: orphan indexes should also be gone (SQLite cascades
            # index drops with the table, but the migration also drops
            # them explicitly — both paths should reach the same state).
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND (name LIKE 'idx_pending_merge%' "
                    "OR name LIKE 'uq_pending_merge%')"
                )).fetchall()
            assert rows == [], (
                f"orphan pending_merge indexes survive downgrade: {rows}"
            )
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0014Idempotent:
    """bd-5w6jz idempotency for migration 0014.

    Drift scenarios:

    * Partial state: ``pending_merges`` already materialised (e.g. by
      ``create_all()`` from the post-0014 ORM model on a long-running
      install where ``alembic_version`` is still at 0013). Re-running
      the migration must skip the CREATE TABLE for pending_merges,
      create ``pending_merge_journal`` from scratch, and produce a
      schema indistinguishable from a clean fresh-install.
    * Full state: the migration has already run successfully. A subsequent
      forced re-run (or smart-bootstrap fast-path stamping through 0014
      without DDL) must not raise — every CREATE is guarded.

    Each scenario validates the full post-migration shape (both tables,
    all seven indexes) so a regression that silently drops an artifact
    on the drift path surfaces immediately.
    """

    def test_migration_0014_idempotent_partial_state(self, tmp_path):
        """``pending_merges`` already present pre-migration; journal absent.

        Reproduces the bd-5w6jz drift case: a long-running install where
        ``create_all()`` materialised ``pending_merges`` from the
        post-0014 ORM model while ``alembic_version`` was still at 0013.
        The migration's per-table guard must skip the CREATE TABLE for
        ``pending_merges`` without raising
        ``OperationalError: table pending_merges already exists`` and
        still create ``pending_merge_journal`` + every index.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0014_partial.db'}"
        cfg = _make_alembic_config(db_url)
        # Bring schema to 0013 (head before this migration).
        command.upgrade(cfg, "0013")

        engine = create_engine(db_url, future=True)
        try:
            # Sanity: neither table exists at 0013.
            names = _table_names(engine)
            assert PENDING_MERGES not in names
            assert JOURNAL not in names

            # Inject pending_merges via raw SQL — mimics what create_all()
            # would emit. Schema must match the migration's CREATE TABLE
            # shape so the post-migration assert sees a unified schema.
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE TABLE pending_merges ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  stream_name TEXT NOT NULL,"
                    "  group_id INTEGER,"
                    "  candidate_channel_id TEXT NOT NULL,"
                    "  confidence REAL NOT NULL,"
                    "  status TEXT NOT NULL DEFAULT 'pending' "
                    "    CHECK (status IN ('pending','merged','dismissed')),"
                    "  created_at INTEGER NOT NULL,"
                    "  resolved_at INTEGER,"
                    "  resolution_source TEXT,"
                    "  trigger_context TEXT NOT NULL"
                    ")"
                ))
            assert PENDING_MERGES in _table_names(engine)
            assert JOURNAL not in _table_names(engine)
        finally:
            engine.dispose()

        # Pre-fix this would raise:
        #   OperationalError: table pending_merges already exists
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            names = _table_names(engine)
            assert PENDING_MERGES in names
            assert JOURNAL in names, (
                "pending_merge_journal must be created even though "
                "pending_merges was pre-existing"
            )

            # Every index must land — the per-index guard adds only the
            # missing ones, but on this drift path NONE of the indexes
            # existed pre-upgrade (the inline-injected CREATE TABLE
            # above did not create them).
            pm_idx = _index_map(engine, PENDING_MERGES)
            for name in EXPECTED_PENDING_MERGES_INDEXES:
                assert name in pm_idx, (
                    f"missing pending_merges index {name} after partial-drift "
                    f"upgrade: have {sorted(pm_idx)}"
                )
            jr_idx = _index_map(engine, JOURNAL)
            for name in EXPECTED_JOURNAL_INDEXES:
                assert name in jr_idx, (
                    f"missing journal index {name}: have {sorted(jr_idx)}"
                )

            # alembic_version advanced to head.
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None
            assert row[0] == database.get_alembic_head_revision()
        finally:
            engine.dispose()

    def test_migration_0014_idempotent_full_state(self, tmp_path):
        """Migration already applied — a re-run is a clean no-op.

        Simulates the smart-bootstrap fast-path stamping past 0014
        followed by a forced re-run (operator manually invokes
        ``alembic upgrade head`` on a DB that is already at head).
        Every CREATE TABLE / CREATE INDEX must skip rather than raise.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0014_full.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            # Capture the post-first-upgrade index set.
            pm_idx_before = set(_index_map(engine, PENDING_MERGES))
            jr_idx_before = set(_index_map(engine, JOURNAL))
        finally:
            engine.dispose()

        # Roll alembic_version back to 0013 without touching the schema
        # — this is the "fast-path stamped past me" state. A subsequent
        # ``upgrade head`` re-runs 0014's DDL against the already-
        # complete schema; every guard must trigger.
        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE alembic_version SET version_num = '0013'"
                ))
        finally:
            engine.dispose()

        # Pre-fix this would raise:
        #   OperationalError: table pending_merges already exists
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            assert _index_map(engine, PENDING_MERGES).keys() == pm_idx_before, (
                "pending_merges index set changed after second upgrade — "
                "the guard should leave existing indexes untouched."
            )
            assert _index_map(engine, JOURNAL).keys() == jr_idx_before, (
                "journal index set changed after second upgrade"
            )
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None
            assert row[0] == database.get_alembic_head_revision()
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0014Constraints:
    """CHECK / partial-unique / FK enforcement at the DB layer."""

    def test_pending_merges_check_constraint_status(self, tmp_path):
        """``status='bogus'`` insert must be rejected by the CHECK.

        Guards against SQLite/Alembic silently dropping the CHECK.
        Aligns with ``test_session_telemetry_migration.py``'s named-CHECK
        enforcement test — named CHECK constraints are stable across
        SQLite versions (``docs/database_migrations.md`` SQLite-specific
        gotchas) but a future migration that rewrites the table via
        batch-rebuild could lose the CHECK silently. This test is the
        canary.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0014_check_pm.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            # Valid row succeeds — sanity that the insert mechanic is fine.
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO pending_merges "
                    "(stream_name, candidate_channel_id, confidence, "
                    " status, created_at, trigger_context) "
                    "VALUES ('US: TNT', 'ch-uuid-1', 0.85, "
                    " 'pending', 1747000000000, 'drag_drop')"
                ))
            # Invalid status — CHECK must reject.
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(text(
                        "INSERT INTO pending_merges "
                        "(stream_name, candidate_channel_id, confidence, "
                        " status, created_at, trigger_context) "
                        "VALUES ('US: TBS', 'ch-uuid-2', 0.85, "
                        " 'bogus', 1747000000001, 'drag_drop')"
                    ))
        finally:
            engine.dispose()

    def test_pending_merge_journal_check_constraint_action_type(self, tmp_path):
        """``action_type='not_an_action'`` insert must be rejected."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0014_check_jr.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            # Seed a pending_merges row so the journal FK can be satisfied
            # (the action_type CHECK must reject even with a valid FK).
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO pending_merges "
                    "(id, stream_name, candidate_channel_id, confidence, "
                    " status, created_at, trigger_context) "
                    "VALUES (1, 'US: TNT', 'ch-uuid-1', 0.85, "
                    " 'pending', 1747000000000, 'drag_drop')"
                ))
            # Valid action_type — sanity.
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO pending_merge_journal "
                    "(pending_merge_id, actor_token_id, action_type, "
                    " source_channel_id, target_channel_id, "
                    " confidence_score, timestamp_utc, trigger_context) "
                    "VALUES (1, 'tok-1', 'merge_confirmed', "
                    " 'src-uuid', 'tgt-uuid', 0.85, "
                    " 1747000001000, 'drag_drop')"
                ))
            # Invalid action_type — CHECK must reject.
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(text(
                        "INSERT INTO pending_merge_journal "
                        "(pending_merge_id, actor_token_id, action_type, "
                        " source_channel_id, target_channel_id, "
                        " confidence_score, timestamp_utc, trigger_context) "
                        "VALUES (1, 'tok-1', 'not_an_action', "
                        " 'src-uuid', 'tgt-uuid', 0.85, "
                        " 1747000002000, 'drag_drop')"
                    ))
        finally:
            engine.dispose()

    def test_pending_merges_partial_unique_index_pending_only(self, tmp_path):
        """§D5: at most one ``pending`` row per (stream_name, candidate_channel_id).

        Scenarios:
          * Two ``pending`` rows on the same (stream, candidate) pair →
            second raises IntegrityError (partial unique fires).
          * A ``merged`` row + a ``pending`` row on the same pair → both
            succeed (the partial WHERE clause excludes ``merged`` from
            the uniqueness scope; the repeat-after-dismissal flow in
            §D10 depends on this).
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0014_unique.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            # Two pending rows on the same key — second must raise.
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO pending_merges "
                    "(stream_name, candidate_channel_id, confidence, "
                    " status, created_at, trigger_context) "
                    "VALUES ('US: TNT', 'ch-uuid-1', 0.85, "
                    " 'pending', 1747000000000, 'drag_drop')"
                ))
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(text(
                        "INSERT INTO pending_merges "
                        "(stream_name, candidate_channel_id, confidence, "
                        " status, created_at, trigger_context) "
                        "VALUES ('US: TNT', 'ch-uuid-1', 0.90, "
                        " 'pending', 1747000001000, 'm3u_refresh')"
                    ))

            # A merged row on the same key should not block another pending row.
            # Use a different stream/candidate pair so the first INSERT's
            # row doesn't itself collide.
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO pending_merges "
                    "(stream_name, candidate_channel_id, confidence, "
                    " status, created_at, resolved_at, resolution_source, "
                    " trigger_context) "
                    "VALUES ('US: TBS', 'ch-uuid-2', 0.85, "
                    " 'merged', 1747000010000, 1747000020000, 'operator', "
                    " 'drag_drop')"
                ))
            # A pending row on the same key as the merged row must succeed
            # — partial WHERE excludes 'merged' from the unique scope.
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO pending_merges "
                    "(stream_name, candidate_channel_id, confidence, "
                    " status, created_at, trigger_context) "
                    "VALUES ('US: TBS', 'ch-uuid-2', 0.90, "
                    " 'pending', 1747000030000, 'm3u_refresh')"
                ))
            # And a dismissed row on the same key as a pending row should
            # also succeed for the same reason.
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO pending_merges "
                    "(stream_name, candidate_channel_id, confidence, "
                    " status, created_at, resolved_at, resolution_source, "
                    " trigger_context) "
                    "VALUES ('US: TBS', 'ch-uuid-2', 0.50, "
                    " 'dismissed', 1747000040000, 1747000050000, 'operator', "
                    " 'drag_drop')"
                ))
        finally:
            engine.dispose()

    def test_pending_merge_journal_fk_to_pending_merges_id(self, tmp_path):
        """FK pointing at a non-existent pending_merges.id must be rejected.

        SQLite enforces FKs only when ``PRAGMA foreign_keys=ON`` (per-
        connection). ECM's connect listener in ``database.py`` sets
        this on every connect — the test environment inherits the
        listener through the SQLAlchemy ``Engine`` class. If a future
        refactor breaks the listener, this test fails immediately.

        Raw ``sqlite3`` CLI sessions opened for debugging do NOT honour
        the FK without an explicit PRAGMA; that divergence is a
        debug-only caveat documented in the migration docstring.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0014_fk.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            # Insert a journal row pointing at a pending_merge_id that
            # does not exist → FK violation.
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(text(
                        "INSERT INTO pending_merge_journal "
                        "(pending_merge_id, actor_token_id, action_type, "
                        " source_channel_id, target_channel_id, "
                        " confidence_score, timestamp_utc, "
                        " trigger_context) "
                        "VALUES (9999, 'tok-x', 'merge_confirmed', "
                        " 'src', 'tgt', 0.9, 1747000000000, 'drag_drop')"
                    ))

            # ON DELETE RESTRICT half: seed a pending_merges row +
            # journal row, then try to delete the pending_merges row.
            # The RESTRICT clause must reject the delete (foreign-key
            # violation) because a journal row still references it.
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO pending_merges "
                    "(id, stream_name, candidate_channel_id, confidence, "
                    " status, created_at, trigger_context) "
                    "VALUES (42, 'US: TNT', 'ch-uuid-1', 0.85, "
                    " 'pending', 1747000000000, 'drag_drop')"
                ))
                conn.execute(text(
                    "INSERT INTO pending_merge_journal "
                    "(pending_merge_id, actor_token_id, action_type, "
                    " source_channel_id, target_channel_id, "
                    " confidence_score, timestamp_utc, "
                    " trigger_context) "
                    "VALUES (42, 'tok-1', 'auto_queued', "
                    " 'src', 'tgt', 0.85, 1747000001000, 'm3u_refresh')"
                ))
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(text("DELETE FROM pending_merges WHERE id = 42"))

            # After deleting the journal row first, the parent delete is
            # allowed. Confirms RESTRICT only fires when references exist.
            with engine.begin() as conn:
                conn.execute(text(
                    "DELETE FROM pending_merge_journal "
                    "WHERE pending_merge_id = 42"
                ))
                conn.execute(text("DELETE FROM pending_merges WHERE id = 42"))
        finally:
            engine.dispose()
