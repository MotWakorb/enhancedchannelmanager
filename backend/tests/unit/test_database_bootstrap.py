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


class TestInitDbLoudFailOnBootstrap:
    """``init_db`` must not silently swallow alembic bootstrap failures (bd-zaaey).

    The original ``init_db`` wrapped ``_bootstrap_alembic`` in a bare
    ``try/except Exception`` and logged a "falling back to create_all()"
    message before continuing. That fallback path is the disease vector that
    let a user's container run for days with a half-applied schema and a
    flood of ``session_telemetry write failed: no column named stream_id``
    WARN logs: ``create_all()`` is a no-op for an existing table, so a
    partially-upgraded ``session_telemetry`` stayed broken forever.

    The contract these tests pin: any failure inside ``_bootstrap_alembic``
    propagates out of ``init_db`` so startup fails loud (the operator sees
    the boot failure, the symptom is not silently ongoing log noise).
    """

    def test_init_db_raises_when_bootstrap_alembic_raises(self, tmp_path, monkeypatch):
        """A raise inside ``_bootstrap_alembic`` must propagate, not be swallowed."""
        # Redirect the journal DB to a tmp path so we don't touch the real one.
        monkeypatch.setattr(database, "JOURNAL_DB_FILE", tmp_path / "journal.db")
        monkeypatch.setattr(database, "_engine", None)
        monkeypatch.setattr(database, "_SessionLocal", None)

        def boom(_engine):
            raise RuntimeError("simulated alembic upgrade failure")

        with patch.object(database, "_bootstrap_alembic", side_effect=boom):
            with pytest.raises(RuntimeError, match="simulated alembic upgrade failure"):
                database.init_db()


class TestWalCheckpointTruncate:
    """Coverage for ``database._wal_checkpoint_truncate`` (bd-ej995).

    The startup WAL checkpoint protects long-running installs from the
    GH #274 disease vector: a bloated ``journal.db-wal`` (reporter saw
    1.4 GB+) stretches the v0.16.0 ``normalize_names`` migration past
    Docker's health-check ``start_period``, the container is marked
    unhealthy, and ``ecm-mcp`` (which gates on ``service_healthy``)
    refuses to start. Truncating the WAL at startup — before bootstrap
    reads or migrations write — keeps the migration timeline running
    against a clean baseline.

    These tests exercise the helper on a real file-backed SQLite engine
    (not just mocks) so the truncate is genuinely observable on disk —
    that's the contract operators care about. Mock-only coverage would
    have proven the SQL was issued without proving SQLite actually
    truncated the WAL file.
    """

    def _build_engine_with_dirty_wal(self, tmp_path):
        """Materialise a SQLite DB with an uncheckpointed WAL.

        Strategy: use raw ``sqlite3`` to enable WAL, write rows, and close
        the connection WITHOUT a checkpoint. We bypass SQLAlchemy + the
        application's PRAGMA listener because the listener auto-enables
        WAL mode AND because SQLAlchemy's engine.dispose() can trigger
        an implicit checkpoint that would defeat the fixture.

        We also hold a second sqlite3 connection open with an active
        read transaction while issuing writes — SQLite's auto-checkpoint
        will NOT truncate the WAL while a reader is active, so the WAL
        is guaranteed to persist past the writes. (This mirrors the
        GH #274 disease vector: a long-lived reader has been pinning
        the WAL on production installs.)

        Returns the production-facing engine plus the WAL path and the
        size we observed pre-truncate.
        """
        import sqlite3

        db_file = tmp_path / "journal.db"
        wal_file = tmp_path / "journal.db-wal"

        # Reader connection: opened with a deferred BEGIN that we promote
        # to a real read transaction by issuing a SELECT. This holds the
        # read lock so the writer's auto-checkpoint cannot truncate.
        reader = sqlite3.connect(str(db_file))
        # Writer connection: enables WAL and writes the rows. We
        # explicitly disable auto-checkpoint so even if the reader is
        # released, the WAL stays dirty for our assertion.
        writer = sqlite3.connect(str(db_file))
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, payload TEXT)")
            writer.commit()

            # Promote the reader to an active read transaction now that
            # the table exists — this is what pins the WAL.
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM t").fetchall()

            # ~50 KB of writes — large enough to push pages into the WAL.
            writer.executemany(
                "INSERT INTO t (id, payload) VALUES (?, ?)",
                [(i, "x" * 200) for i in range(250)],
            )
            writer.commit()
        finally:
            writer.close()
            # Reader stays open until the end of this method so the WAL
            # cannot be auto-truncated. We release it just before
            # returning the production engine.

        # Sanity: the WAL file should exist and be non-empty BEFORE the
        # truncate. If this assertion fails, the test fixture is broken
        # (likely SQLite version differences) and we'd be testing nothing.
        assert wal_file.exists(), "fixture failed to produce a WAL file"
        wal_size_before = wal_file.stat().st_size
        assert wal_size_before > 0, (
            f"fixture WAL is empty ({wal_size_before} bytes) — checkpoint already ran"
        )

        # Release the reader now — the WAL is on disk and the production
        # engine that follows will own the connection that runs the
        # checkpoint helper.
        reader.execute("COMMIT")
        reader.close()

        # Production-facing engine: this is the one the helper runs
        # against, matching the production posture where init_db creates
        # a fresh engine. NullPool avoids the StaticPool implicit
        # checkpoint hazard noted above.
        from sqlalchemy.pool import NullPool
        production_engine = create_engine(
            f"sqlite:///{db_file}",
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )
        return production_engine, wal_file, wal_size_before

    def test_truncate_reduces_dirty_wal_to_zero_bytes(self, tmp_path, monkeypatch):
        """The whole point of the helper: a dirty WAL becomes a zero-byte WAL."""
        # Redirect JOURNAL_DB_FILE so the helper's size-logging stat() hits
        # our tmp WAL, not the real one. The truncate itself runs through
        # the engine and is independent of this path.
        monkeypatch.setattr(database, "JOURNAL_DB_FILE", tmp_path / "journal.db")

        engine, wal_file, size_before = self._build_engine_with_dirty_wal(tmp_path)
        try:
            database._wal_checkpoint_truncate(engine)

            # Contract: after PRAGMA wal_checkpoint(TRUNCATE), the WAL
            # file shrinks to zero bytes. (The file itself may persist as
            # an empty placeholder — that's SQLite's normal behavior and
            # what we want; we're testing truncation, not deletion.)
            size_after = wal_file.stat().st_size if wal_file.exists() else 0
            assert size_after == 0, (
                f"WAL not truncated: before={size_before} after={size_after}"
            )
        finally:
            engine.dispose()

    def test_truncate_is_safe_when_no_wal_file_exists(self, tmp_path, monkeypatch):
        """Fresh installs have no WAL file yet — helper must not raise."""
        # Use a db file path that does not exist. SQLAlchemy will create
        # the DB on first connect; the WAL file will not exist until a
        # write happens in WAL mode. Calling the helper on this empty
        # state must succeed silently.
        monkeypatch.setattr(database, "JOURNAL_DB_FILE", tmp_path / "fresh.db")

        engine = create_engine(
            f"sqlite:///{tmp_path}/fresh.db",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            # Must not raise.
            database._wal_checkpoint_truncate(engine)
        finally:
            engine.dispose()

    def test_truncate_failure_is_non_fatal(self, tmp_path, monkeypatch, caplog):
        """A raise inside the engine.connect() block must be swallowed as WARN.

        The startup path must continue even if the optimization can't
        run (e.g. read-only filesystem, disk full). Matches the
        backup.py pattern of WARN-and-continue.
        """
        import logging
        monkeypatch.setattr(database, "JOURNAL_DB_FILE", tmp_path / "journal.db")

        broken_engine = type("BrokenEngine", (), {})()
        def _boom():
            raise RuntimeError("simulated WAL checkpoint failure")
        broken_engine.connect = _boom

        with caplog.at_level(logging.WARNING, logger=database.logger.name):
            # Must not raise.
            database._wal_checkpoint_truncate(broken_engine)

        # The failure must be loud enough that an operator can see it
        # in their logs — the WARN is the only signal that the
        # optimization didn't run.
        assert any(
            "WAL checkpoint failed" in record.message
            for record in caplog.records
        ), "expected a WARN log naming the WAL checkpoint failure"

    def test_init_db_invokes_wal_checkpoint_before_bootstrap(self, tmp_path, monkeypatch):
        """Ordering contract: WAL checkpoint runs BEFORE _bootstrap_alembic.

        If this ordering ever regresses, a bloated WAL would still be in
        place when migrations start running — defeating the entire fix.
        Captures the call order via a side-effect list so a future
        refactor cannot silently invert it.
        """
        monkeypatch.setattr(database, "JOURNAL_DB_FILE", tmp_path / "journal.db")
        monkeypatch.setattr(database, "_engine", None)
        monkeypatch.setattr(database, "_SessionLocal", None)

        call_order: list[str] = []

        def fake_wal(_engine):
            call_order.append("wal_checkpoint")

        def fake_bootstrap(_engine):
            call_order.append("bootstrap_alembic")

        def fake_parity(_engine):
            call_order.append("parity_check")

        with patch.object(database, "_wal_checkpoint_truncate", side_effect=fake_wal), \
             patch.object(database, "_bootstrap_alembic", side_effect=fake_bootstrap), \
             patch.object(database, "_assert_schema_matches_models", side_effect=fake_parity), \
             patch.object(database, "_run_migrations"), \
             patch.object(database, "_create_demo_normalization_rules"), \
             patch.object(database, "_perform_maintenance"):
            database.init_db()

        # The WAL checkpoint must be the first of the three startup steps
        # — anything else means a bloated WAL is in play during bootstrap.
        assert call_order[:3] == [
            "wal_checkpoint",
            "bootstrap_alembic",
            "parity_check",
        ], f"startup ordering regressed: {call_order}"


class TestModelSchemaParityCheck:
    """Post-bootstrap parity check: every SQLAlchemy model column must exist
    in the live DB (bd-zaaey).

    The original ``_BOOTSTRAP_CANARIES`` list covered migrations 0002 and
    0004 only. A user whose ``alembic_version`` is stamped beyond the
    canaries but whose physical schema is missing later-migration columns
    (e.g. ``session_telemetry.stream_id`` from migration 0010) would pass
    the canary check and silently run with a broken schema. This durable
    parity check replaces the canary's hand-curated list with an
    automatically-derived diff between ``Base.metadata`` and the live DB —
    so any future migration's columns are covered without extending a
    canary list per release.

    Contract: ``_assert_schema_matches_models`` raises ``RuntimeError`` with
    a message naming the missing ``table.column`` if a model-declared
    column is absent from the live DB. Tables that exist in models but not
    in the DB are not the parity check's concern (``create_all`` covers
    that — see ``init_db``'s ordering); only missing **columns on existing
    tables** are flagged, because those are the columns that ``ALTER TABLE``
    migrations are supposed to add and that ``create_all`` cannot add.
    """

    def test_parity_check_passes_at_head(self, tmp_path):
        """A fully-migrated DB must pass the parity check without raising."""
        from alembic import command

        db_file = tmp_path / "at_head.db"
        cfg = _alembic_config(db_file)
        command.upgrade(cfg, "head")

        engine = _make_engine(db_file)
        try:
            # Must not raise.
            database._assert_schema_matches_models(engine)
        finally:
            engine.dispose()

    def test_parity_check_raises_on_missing_column(self, tmp_path):
        """A column dropped from the live DB (simulating an unapplied migration)
        must trigger the parity check to raise."""
        from alembic import command

        db_file = tmp_path / "missing_col.db"
        cfg = _alembic_config(db_file)
        command.upgrade(cfg, "head")

        engine = _make_engine(db_file)
        try:
            # Drop the ``stream_id`` column from ``session_telemetry`` to
            # simulate a user whose schema lags behind the model (e.g.
            # migration 0010 was skipped, the bd-zaaey symptom). SQLite has no
            # native ``DROP COLUMN`` in older versions; the rebuild dance is:
            # 1. DROP VIEW that references the table (else the RENAME fails
            #    with "error in view: no such table" — same constraint that
            #    forced migration 0010 to drop/recreate channel_watch_stats_v).
            # 2. CREATE TABLE replacement WITHOUT the dropped columns
            # 3. INSERT SELECT
            # 4. DROP original
            # 5. RENAME replacement → original
            with engine.begin() as conn:
                conn.execute(text("DROP VIEW IF EXISTS channel_watch_stats_v"))
                conn.execute(text(
                    "CREATE TABLE session_telemetry_rebuild ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "session_id TEXT NOT NULL, "
                    "observed_at INTEGER NOT NULL, "
                    "user_id INTEGER, "
                    "provider_id INTEGER, "
                    "channel_id VARCHAR(64) NOT NULL, "
                    "bytes_delta BIGINT NOT NULL, "
                    "buffer_event_count INTEGER NOT NULL DEFAULT 0, "
                    "poll_interval_ms INTEGER NOT NULL, "
                    "CONSTRAINT ck_session_telemetry_bytes_delta_non_negative CHECK (bytes_delta >= 0)"
                    ")"
                ))
                conn.execute(text(
                    "INSERT INTO session_telemetry_rebuild "
                    "(id, session_id, observed_at, user_id, provider_id, channel_id, "
                    " bytes_delta, buffer_event_count, poll_interval_ms) "
                    "SELECT id, session_id, observed_at, user_id, provider_id, channel_id, "
                    " bytes_delta, buffer_event_count, poll_interval_ms FROM session_telemetry"
                ))
                conn.execute(text("DROP TABLE session_telemetry"))
                conn.execute(text("ALTER TABLE session_telemetry_rebuild RENAME TO session_telemetry"))

            with pytest.raises(RuntimeError, match="schema drift") as exc_info:
                database._assert_schema_matches_models(engine)

            # Error message must name the offending column so the operator
            # can act without re-running pytest.
            assert "session_telemetry.stream_id" in str(exc_info.value), str(exc_info.value)
        finally:
            engine.dispose()
