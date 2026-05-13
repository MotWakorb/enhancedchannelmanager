"""Shared fixtures for the Stats v2 performance benchmark suite (bd-skqln.10).

Design choices
==============

* **One file-backed SQLite DB per benchmark session.** The fixture is
  session-scoped — seeding 250k+ rows is too expensive to repeat per test.
  The DB file lives under ``tmp_path_factory`` and is deleted when the
  session ends.
* **Read-only after seed.** Benchmarks never write, so we don't need a
  transactional rollback fixture. A regular SQLAlchemy ``sessionmaker`` is
  fine; each benchmark iteration opens a fresh session to avoid identity-map
  caching skewing numbers.
* **PRAGMA tuning is identical to production.** ``database.py``'s connect
  listener (``PRAGMA foreign_keys=ON`` + ``journal_mode=WAL`` in some paths)
  is wired against the SQLAlchemy ``Engine`` class globally — engines made
  via ``create_engine`` here inherit that listener automatically. So we're
  measuring the same query plans the app sees in production.
* **Row count is configurable via env var ``ECM_PERF_ROW_COUNT``.** Default
  is **250_000** — a balance between meaningful index pressure and feasible
  CI runtime. The bead amendment specifies a 5M-row target for the local
  pre-merge gate; set ``ECM_PERF_ROW_COUNT=5000000`` to run that locally.
  Increasing the row count changes baseline numbers, so CI uses the default.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import database
import models  # noqa: F401 — registers tables with Base
from tests.fixtures.session_telemetry_volume import (
    VolumeShape,
    seed_session_telemetry,
)

# Default CI row count. The bead amendment asks for 5M rows as the stretch
# target — that gate lives locally (set ECM_PERF_ROW_COUNT=5000000), not in
# CI. 250k is sufficient to exercise the user/observed_at composite index
# and produce a stable p95 envelope inside a few seconds.
_DEFAULT_PERF_ROW_COUNT = 250_000

# Smaller user count so the watch-time-by-user aggregate hits a realistic
# fan-out (50 users * 5k polls each → 250k rows).
_PERF_USER_COUNT = 50


def _perf_row_count() -> int:
    raw = os.environ.get("ECM_PERF_ROW_COUNT")
    if not raw:
        return _DEFAULT_PERF_ROW_COUNT
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(
            f"ECM_PERF_ROW_COUNT={raw!r} is not an integer"
        ) from e


@pytest.fixture(scope="session")
def perf_db_engine(tmp_path_factory):
    """Session-scoped file-backed SQLite engine, pre-seeded with synthetic
    ``session_telemetry`` rows.

    Yields an ``Engine`` ready to be opened for benchmark iterations.
    """
    row_count = _perf_row_count()
    shape = VolumeShape(
        row_count=row_count,
        batch_size=50_000,
        user_count=_PERF_USER_COUNT,
    )

    db_path = tmp_path_factory.mktemp("perf") / "stats_perf.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
        echo=False,
    )
    database.Base.metadata.create_all(bind=engine)

    with engine.connect() as conn:
        seed_session_telemetry(conn, shape)

    # Materialize ANALYZE so the query planner has up-to-date stats. In
    # production this is run during the nightly maintenance task — the
    # benchmark must measure the post-ANALYZE plan, not the cold one.
    with engine.connect() as conn:
        conn.exec_driver_sql("ANALYZE")
        conn.commit()

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def perf_session_factory(perf_db_engine):
    """Session factory bound to the seeded benchmark engine.

    Each benchmark iteration should call ``perf_session_factory()`` to get a
    fresh ``Session`` — the SQLAlchemy identity map otherwise caches result
    rows across iterations and the numbers degrade into "lookup in dict".
    """
    return sessionmaker(
        bind=perf_db_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
