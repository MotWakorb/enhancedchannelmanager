"""
Tests for the SQLAlchemy engine-connect PRAGMA listener in database.py.

Verifies that every SQLite connection opened through a SQLAlchemy engine:
  1. Has journal_mode=WAL (for file-backed databases)
  2. Has foreign_keys=ON
  3. Rejects inserts that violate foreign key constraints

These behaviors are required for multi-writer safety and data integrity —
without them, concurrent writes produce "database is locked" errors and
ForeignKey declarations in models.py are silently ignored.
"""
import sqlite3

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database  # noqa: F401 — importing registers the connect-event listener
from models import Tag, TagGroup


@pytest.fixture
def file_engine(tmp_path):
    """A file-backed SQLite engine — the realistic production shape."""
    db_path = tmp_path / "pragma_test.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    database.Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def memory_engine():
    """An in-memory SQLite engine — used by the main test suite."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    database.Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        engine.dispose()


def test_file_connection_has_wal_journal_mode(file_engine):
    """A fresh file-backed SQLite connection must land in WAL mode."""
    with file_engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert mode.lower() == "wal", (
        f"Expected journal_mode=wal, got {mode!r}. "
        "WAL is required for concurrent readers during writes — without it, "
        "probe/task/HTTP writes will produce 'database is locked' errors."
    )


def test_file_connection_has_foreign_keys_enabled(file_engine):
    """A fresh file-backed connection must enforce foreign keys."""
    with file_engine.connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert fk == 1, (
        f"Expected foreign_keys=1, got {fk!r}. "
        "SQLite defaults to OFF; the engine-connect listener must set ON."
    )


def test_file_connection_has_synchronous_normal(file_engine):
    """synchronous=NORMAL balances durability and WAL-write throughput."""
    with file_engine.connect() as conn:
        # 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA. We set NORMAL.
        sync = conn.execute(text("PRAGMA synchronous")).scalar()
    assert sync == 1, f"Expected synchronous=NORMAL (1), got {sync!r}"


def test_memory_connection_has_foreign_keys_enabled(memory_engine):
    """In-memory DBs can't do WAL, but FK enforcement must still apply."""
    with memory_engine.connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert fk == 1, (
        "In-memory databases used in tests must still enforce FKs — "
        "otherwise tests can't catch FK-violation bugs."
    )


def test_memory_connection_does_not_fail_on_wal(memory_engine):
    """The listener must not raise when run against an in-memory DB."""
    # Just opening a connection should succeed. The listener guards :memory:
    # so WAL is skipped but FK/synchronous still apply.
    with memory_engine.connect() as conn:
        # journal_mode for in-memory is "memory" (cannot be WAL).
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert mode.lower() in {"memory", "delete", "wal"}, (
        f"Unexpected journal_mode for in-memory DB: {mode!r}"
    )


def test_foreign_key_violation_raises_integrity_error(file_engine):
    """Inserting a row with an invalid FK must fail — proves FKs are enforced."""
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=file_engine, expire_on_commit=False
    )
    session = SessionLocal()
    try:
        # tags.group_id → tag_groups.id, ondelete=CASCADE, nullable=False.
        # Insert a tag referencing a non-existent group. With FKs enforced,
        # commit must raise IntegrityError.
        orphan_tag = Tag(group_id=99999, value="orphan", case_sensitive=False, enabled=True)
        session.add(orphan_tag)

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()

        # Sanity check: a valid insert (with a real parent row) succeeds.
        group = TagGroup(name="pragma-test-group", description="test")
        session.add(group)
        session.commit()
        session.refresh(group)

        valid_tag = Tag(group_id=group.id, value="valid", case_sensitive=False, enabled=True)
        session.add(valid_tag)
        session.commit()  # Should not raise.
    finally:
        session.close()


def test_raw_sqlite3_connection_is_not_affected():
    """
    Sanity check: the listener only fires for SQLAlchemy-managed connections.
    A raw sqlite3.connect() bypasses the event and should NOT have our PRAGMAs
    applied — this documents the boundary of the fix (all DB access must go
    through the SQLAlchemy engine, which it does in this codebase).
    """
    conn = sqlite3.connect(":memory:")
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys")
        fk = cursor.fetchone()[0]
        # Raw sqlite3 default is 0 — our listener doesn't run for raw connections.
        assert fk == 0
    finally:
        conn.close()
