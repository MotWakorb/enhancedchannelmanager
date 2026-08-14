"""bead enhancedchannelmanager-9kwzp.5 item 1 — restore-initial's hidden dependency
on ``poolclass=StaticPool``.

WHY THIS FILE EXISTS
--------------------

``routers.backup.restore_backup_initial`` takes
``session: Session = Depends(get_session)`` — it has to read the users table to
decide whether an anonymous first-run restore is still allowed (bead lf29s). A
FastAPI dependency-injected session lives for the whole handler, so an open
SQLite READ TRANSACTION spans ``_restore_from_zip``'s

    close_db()  ->  JOURNAL_DB_FILE.write_bytes(...)  ->  init_db()

sequence. ``close_db()`` is just ``engine.dispose()``.

Under SQLAlchemy's DEFAULT ``QueuePool`` that is a silent data-loss bug: a
CHECKED-OUT connection survives ``dispose()``, so the pre-restore ``-wal``
stays alive and replays over the freshly written ``journal.db``. The security
review of the lf29s branch reproduced it — the endpoint answered 200 with
``integrity_check=ok`` while the instance had reverted to its pre-restore data.
A restore that reports success and throws the backup away is the worst failure
mode this endpoint has, and nothing about it is visible to the caller.

It does not reproduce in ECM only because ``database.init_db`` builds the
engine with ``poolclass=StaticPool``, whose ``dispose()`` closes its single
shared connection regardless of checkout state.

WHY A TEST AND NOT ONLY A COMMENT
---------------------------------

The bead's literal ask was a comment at each end of the coupling, and both
comments exist (``database.py`` at the ``create_engine`` call,
``routers/backup.py`` at the dependency). But the thing that can regress is a
one-word edit — ``poolclass=StaticPool`` changed for a concurrency reason by
someone reading ``database.py`` alone — and a comment does not fail a build.
Nothing else in the suite would go red: the restore endpoint's own tests patch
``close_db``/``init_db`` out entirely, so they cannot see the WAL at all.

:func:`test_init_db_engine_uses_staticpool` is the tripwire.
:func:`test_queuepool_dispose_leaves_a_checked_out_connection_alive` records
WHY the tripwire matters by demonstrating the divergent ``dispose()`` semantics
directly, so a future reader does not have to take the comment's word for it.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool, StaticPool

import database


_FAILURE = (
    "database.init_db no longer builds the engine with poolclass=StaticPool.\n"
    "\n"
    "This is not a free choice (bead enhancedchannelmanager-9kwzp.5). "
    "POST /api/backup/restore-initial holds a Depends(get_session) session — "
    "an open SQLite read transaction — across close_db() -> "
    "journal.db write_bytes() -> init_db(). StaticPool.dispose() closes its "
    "single shared connection regardless of checkout state, which is the only "
    "reason the pre-restore WAL cannot replay over the restored database. "
    "Under QueuePool the checked-out connection survives dispose() and the "
    "restore returns 200 with integrity_check=ok while silently reverting to "
    "pre-restore data.\n"
    "\n"
    "If you need a different pool, change restore-initial FIRST: resolve the "
    "identity gate with a short-lived session opened and closed inside "
    "_guard_initial_restore rather than one held across the file swap."
)


def test_init_db_engine_uses_staticpool(tmp_path, monkeypatch):
    """The engine ECM actually runs on must be a StaticPool engine.

    Exercises the real ``init_db`` against a throwaway database file rather
    than asserting on the source text, so the check is on the constructed
    engine. The migration/maintenance stages after engine creation are stubbed
    out — they are not what is under test and they are slow.
    """
    monkeypatch.setattr(database, "JOURNAL_DB_FILE", tmp_path / "journal.db")
    monkeypatch.setattr(database, "CONFIG_DIR", tmp_path)
    # Restored by monkeypatch on teardown whatever init_db does to them.
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    for stage in (
        "_bootstrap_alembic",
        "_assert_schema_matches_models",
        "_run_migrations",
        "_create_demo_normalization_rules",
        "_perform_maintenance",
    ):
        monkeypatch.setattr(database, stage, lambda *a, **kw: None)

    database.init_db()
    engine = database.get_engine()
    try:
        assert isinstance(engine.pool, StaticPool), _FAILURE
    finally:
        engine.dispose()


def test_queuepool_dispose_leaves_a_checked_out_connection_alive(tmp_path):
    """The divergence the tripwire above is protecting against.

    Not a test of ECM code — a recorded demonstration of the SQLAlchemy
    behaviour the coupling rests on, so the claim in the two comments is
    checkable rather than folklore. StaticPool's ``dispose()`` closes its one
    shared connection even while a caller holds it; QueuePool's does not.
    """
    db_file = tmp_path / "pool_semantics.db"

    static_engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    held = static_engine.connect()
    try:
        held.execute(text("SELECT 1"))
        static_engine.dispose()
        # The shared connection went with the pool — the restore's file swap is
        # safe because nothing is left holding the old database open.
        with pytest.raises(Exception):
            held.execute(text("SELECT 1"))
    finally:
        held.close()
        static_engine.dispose()

    queue_engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
    )
    held = queue_engine.connect()
    try:
        held.execute(text("SELECT 1"))
        queue_engine.dispose()
        # Still usable AFTER dispose() — this is the connection that would keep
        # the pre-restore WAL alive and replay it over the restored file.
        assert held.execute(text("SELECT 1")).scalar() == 1, (
            "QueuePool.dispose() closed a checked-out connection — if SQLAlchemy "
            "changed this, re-derive the restore-initial coupling before "
            "relaxing the StaticPool pin above."
        )
    finally:
        held.close()
        queue_engine.dispose()
