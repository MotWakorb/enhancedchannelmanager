"""Tests for the startup SQLite integrity check + WAL-growth backstop.

bd-12hxn / GH #473: a memory/swap spike corrupted the on-disk journal.db
("database disk image is malformed", raised from a mid-request fetchall). The
fix adds:

* ``database._integrity_check`` — a ``PRAGMA quick_check`` run during boot that
  loud-fails (raises with operator guidance) on a corrupted file, BEFORE
  migrations touch it. In-memory and fresh DBs are skipped/trivially-ok.
* A per-connection WAL-growth backstop (``journal_size_limit`` +
  ``wal_autocheckpoint``) so the -wal can't grow without bound during a long
  run.

These tests cover the healthy-pass path, the corruption-raises path (both real
corruption and a patched non-'ok' result), in-memory skip, and that the WAL
PRAGMA backstop values are actually applied on a new file-backed connection.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import database


def _make_file_engine(db_file: Path):
    """A file-backed SQLite engine matching production settings."""
    return create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )


# --- Part A: integrity check ------------------------------------------------


def test_integrity_check_passes_on_healthy_db(tmp_path):
    """A valid DB with real b-tree content must pass quick_check and not raise."""
    db_file = tmp_path / "healthy.db"
    engine = _make_file_engine(db_file)
    try:
        # Use a self-contained table (no model constraint coupling) so the test
        # exercises quick_check against real, populated b-trees.
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
            for i in range(50):
                conn.execute(
                    text("INSERT INTO t (v) VALUES (:v)"), {"v": f"row-{i}"}
                )
        # Should complete silently (no raise).
        database._integrity_check(engine)
    finally:
        engine.dispose()


def test_integrity_check_skips_in_memory_db():
    """In-memory engines have no on-disk file — the check must no-op, not raise."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    try:
        database.Base.metadata.create_all(bind=engine)
        # Must return without raising and without running quick_check.
        database._integrity_check(engine)
    finally:
        engine.dispose()


def test_integrity_check_raises_on_non_ok_result(tmp_path):
    """A non-'ok' quick_check result must raise RuntimeError with guidance.

    Simulate corruption by intercepting connection execution: the
    ``database_list`` probe reports a real file (so the in-memory skip does NOT
    fire), and the ``quick_check`` reports a damaged page.
    """
    db_file = tmp_path / "patched.db"
    engine = _make_file_engine(db_file)
    try:
        database.Base.metadata.create_all(bind=engine)

        real_connect = engine.connect

        def make_result(sql: str):
            result = MagicMock()
            if "database_list" in sql:
                result.fetchall.return_value = [(0, "main", str(db_file))]
            elif "quick_check" in sql:
                result.scalar.return_value = (
                    "*** in database main ***\nPage 5: btree corrupt"
                )
            return result

        def fake_connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            original_execute = conn.execute
            conn.execute = lambda stmt, *a, **k: (  # type: ignore[assignment]
                make_result(str(stmt))
                if ("database_list" in str(stmt) or "quick_check" in str(stmt))
                else original_execute(stmt, *a, **k)
            )
            return conn

        with patch.object(engine, "connect", side_effect=fake_connect):
            with pytest.raises(RuntimeError, match="integrity check failed"):
                database._integrity_check(engine)
    finally:
        engine.dispose()


def test_integrity_check_raises_on_real_corruption(tmp_path):
    """A genuinely corrupted DB file must be detected and raise.

    We build a valid DB, then overwrite interior pages with garbage while
    leaving the 16-byte SQLite header intact, so the file still opens as a
    SQLite DB but quick_check finds malformed b-tree pages.
    """
    db_file = tmp_path / "corrupt.db"
    engine = _make_file_engine(db_file)
    try:
        # Self-contained table; insert enough rows to span multiple pages so
        # corrupting page bodies actually damages b-tree content.
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
            for i in range(500):
                conn.execute(
                    text("INSERT INTO t (v) VALUES (:v)"), {"v": "x" * 200}
                )
    finally:
        engine.dispose()

    # Corrupt the file: keep the first SQLite page header (first 100 bytes,
    # which includes the 16-byte magic string) but garble the rest of the file.
    data = bytearray(db_file.read_bytes())
    for offset in range(100, len(data)):
        data[offset] = 0xFF
    db_file.write_bytes(bytes(data))

    engine2 = _make_file_engine(db_file)
    try:
        with pytest.raises(RuntimeError):
            database._integrity_check(engine2)
    finally:
        engine2.dispose()


def test_init_db_aborts_on_corruption(tmp_path, monkeypatch):
    """init_db must abort (raise) when the integrity check fails, before
    migrations run — the corruption must not be silently passed to create_all.
    """
    db_file = tmp_path / "journal.db"

    # Point the module's DB file + config dir at our temp path so init_db uses
    # them and does not touch the real config directory.
    monkeypatch.setattr(database, "JOURNAL_DB_FILE", db_file)
    monkeypatch.setattr(database, "CONFIG_DIR", tmp_path)

    # Make the integrity check fail; assert bootstrap is never reached.
    bootstrap_called = {"hit": False}

    def fake_integrity(engine):
        raise RuntimeError("integrity check failed — journal.db is corrupted (simulated)")

    def fake_bootstrap(engine):
        bootstrap_called["hit"] = True

    monkeypatch.setattr(database, "_integrity_check", fake_integrity)
    monkeypatch.setattr(database, "_bootstrap_alembic", fake_bootstrap)

    with pytest.raises(RuntimeError, match="integrity check failed"):
        database.init_db()

    assert bootstrap_called["hit"] is False, (
        "Bootstrap/migrations must NOT run after a failed integrity check — "
        "the whole point is to catch corruption before touching the file."
    )


# --- Part B: WAL-growth backstop -------------------------------------------


def test_wal_journal_size_limit_applied_on_new_connection(tmp_path):
    """A new file-backed connection must have journal_size_limit set to the cap."""
    db_file = tmp_path / "wal_limit.db"
    engine = _make_file_engine(db_file)
    try:
        with engine.connect() as conn:
            limit = conn.execute(text("PRAGMA journal_size_limit")).scalar()
        assert limit == database.WAL_JOURNAL_SIZE_LIMIT_BYTES, (
            f"Expected journal_size_limit={database.WAL_JOURNAL_SIZE_LIMIT_BYTES}, "
            f"got {limit!r}. The WAL-growth backstop must bound retained WAL size."
        )
    finally:
        engine.dispose()


def test_wal_autocheckpoint_applied_on_new_connection(tmp_path):
    """A new file-backed connection must have wal_autocheckpoint set."""
    db_file = tmp_path / "wal_autockpt.db"
    engine = _make_file_engine(db_file)
    try:
        with engine.connect() as conn:
            pages = conn.execute(text("PRAGMA wal_autocheckpoint")).scalar()
        assert pages == database.WAL_AUTOCHECKPOINT_PAGES, (
            f"Expected wal_autocheckpoint={database.WAL_AUTOCHECKPOINT_PAGES}, "
            f"got {pages!r}."
        )
    finally:
        engine.dispose()


def test_wal_backstop_constants_are_sane():
    """Guard the constants against accidental regression to unbounded/zero."""
    # 64 MiB default — comfortably above steady state, well below incident size.
    assert database.WAL_JOURNAL_SIZE_LIMIT_BYTES == 64 * 1024 * 1024
    # journal_size_limit must be positive — 0 would force the WAL to truncate to
    # zero on every checkpoint (churn), -1 would mean "no limit" (the bug).
    assert database.WAL_JOURNAL_SIZE_LIMIT_BYTES > 0
    # wal_autocheckpoint must be positive — 0 disables auto-checkpoint entirely,
    # which would let the WAL grow unbounded (the opposite of the fix).
    assert database.WAL_AUTOCHECKPOINT_PAGES > 0


# --- Part C: mid-run periodic PASSIVE checkpoint (bd-xjlxj / GH #473) -------
#
# The boot-only TRUNCATE bounds the WAL at startup; the per-connection
# ``wal_autocheckpoint`` bounds it per-commit. Neither guarantees the WAL is
# reclaimed mid-run on a busy install where overlapping short readers keep
# making the per-commit PASSIVE checkpoint back off. ``maybe_periodic_wal_
# checkpoint`` is the count-based mid-run backstop: it fires a PASSIVE (never
# TRUNCATE) checkpoint every N poll cycles.
#
# AC: test that the checkpoint is INVOKED on a count-based trigger (NOT by WAL
# file size, which is flaky). The trigger's boolean return is the contract.


def test_periodic_checkpoint_constant_is_sane():
    """The cadence must be a positive integer (0/negative disables the backstop)."""
    assert isinstance(database.WAL_PASSIVE_CHECKPOINT_EVERY_N_POLLS, int)
    assert database.WAL_PASSIVE_CHECKPOINT_EVERY_N_POLLS > 0


def test_periodic_checkpoint_fires_on_multiple_of_n():
    """The trigger fires (returns True) on positive multiples of every_n_polls."""
    fake_engine = MagicMock()
    with patch.object(database, "_wal_checkpoint_passive") as mock_ckpt:
        fired = database.maybe_periodic_wal_checkpoint(
            poll_count=30, every_n_polls=30, engine=fake_engine
        )
    assert fired is True
    mock_ckpt.assert_called_once_with(fake_engine)


def test_periodic_checkpoint_does_not_fire_off_cadence():
    """Polls that are not a multiple of N must NOT checkpoint."""
    fake_engine = MagicMock()
    with patch.object(database, "_wal_checkpoint_passive") as mock_ckpt:
        for poll_count in (1, 5, 29, 31, 59):
            fired = database.maybe_periodic_wal_checkpoint(
                poll_count=poll_count, every_n_polls=30, engine=fake_engine
            )
            assert fired is False, f"poll {poll_count} should not fire"
    mock_ckpt.assert_not_called()


def test_periodic_checkpoint_never_fires_on_zero_poll():
    """poll_count == 0 must never fire — the boot truncate already ran."""
    fake_engine = MagicMock()
    with patch.object(database, "_wal_checkpoint_passive") as mock_ckpt:
        fired = database.maybe_periodic_wal_checkpoint(
            poll_count=0, every_n_polls=30, engine=fake_engine
        )
    assert fired is False
    mock_ckpt.assert_not_called()


def test_periodic_checkpoint_disabled_when_n_non_positive():
    """every_n_polls <= 0 disables the backstop entirely (operator escape hatch)."""
    fake_engine = MagicMock()
    with patch.object(database, "_wal_checkpoint_passive") as mock_ckpt:
        for n in (0, -1):
            fired = database.maybe_periodic_wal_checkpoint(
                poll_count=30, every_n_polls=n, engine=fake_engine
            )
            assert fired is False
    mock_ckpt.assert_not_called()


def test_periodic_checkpoint_noop_when_engine_unavailable(monkeypatch):
    """No engine (DB not initialized) → no-op, no crash."""
    monkeypatch.setattr(database, "_engine", None)
    with patch.object(database, "_wal_checkpoint_passive") as mock_ckpt:
        fired = database.maybe_periodic_wal_checkpoint(
            poll_count=30, every_n_polls=30, engine=None
        )
    assert fired is False
    mock_ckpt.assert_not_called()


def test_passive_checkpoint_uses_passive_not_truncate(tmp_path):
    """The hot-path checkpoint must issue PASSIVE — never TRUNCATE.

    TRUNCATE takes an exclusive WAL lock and contends with concurrent readers
    ('database is locked' / write stalls). The whole point of the mid-run
    backstop is to be contention-free, so the SQL it issues is load-bearing.
    """
    db_file = tmp_path / "passive.db"
    engine = _make_file_engine(db_file)
    captured = []
    real_connect = engine.connect

    def _spy_connect(*a, **k):
        conn = real_connect(*a, **k)
        real_execute = conn.execute

        def _spy_execute(stmt, *ea, **ek):
            captured.append(str(stmt))
            return real_execute(stmt, *ea, **ek)

        conn.execute = _spy_execute
        return conn

    try:
        with patch.object(engine, "connect", side_effect=_spy_connect):
            database._wal_checkpoint_passive(engine)
        joined = " ".join(captured).upper()
        assert "WAL_CHECKPOINT(PASSIVE)" in joined.replace(" ", "")
        assert "TRUNCATE" not in joined
    finally:
        engine.dispose()


def test_passive_checkpoint_failure_is_non_fatal(tmp_path, caplog):
    """A checkpoint failure must WARN and continue — never propagate."""
    broken_engine = MagicMock()
    broken_engine.connect.side_effect = RuntimeError("disk full")
    with caplog.at_level("WARNING"):
        # Must not raise.
        database._wal_checkpoint_passive(broken_engine)
    assert any(
        "Periodic WAL checkpoint (PASSIVE) failed" in r.message for r in caplog.records
    )
