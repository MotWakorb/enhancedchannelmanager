"""Bulk volume-fixture generator for the ``session_telemetry`` table.

This is shared infra. Two consumers:

* ``tests/integration/test_session_telemetry_migration.py`` — the local
  pre-merge 5M-row migration up/down gate (bead ``enhancedchannelmanager-skqln.2``).
* ``enhancedchannelmanager-skqln.10`` — the hot-query benchmark gate, which
  needs a realistic-shape population to measure aggregate read paths.

Design constraints:

* **Fast.** Row-by-row ORM ``session.add`` is far too slow at 5M rows; this
  uses batched multi-row ``INSERT`` via raw ``executemany`` on the DBAPI
  connection. ~5M rows seed in a couple of minutes on a laptop SSD.
* **Realistic-ish.** Multi-provider distribution (a handful of providers,
  Zipf-ish skew) with ~5% ``provider_id IS NULL`` rows (the GH-59 provider
  tagging gap — pre-tagging history has no provider). ``user_id`` spans a
  modest set of synthetic accounts; ``channel_id`` a larger set. ``observed_at``
  walks backwards from "now" in ``poll_interval_ms`` steps so retention-sweep
  queries (``WHERE observed_at < ?``) hit realistic ranges.
* **Synthetic identities only.** ``user_id`` values are small integers
  (1..``user_count``); the generator first seeds matching synthetic ``users``
  rows (``synthetic-volume-user-NNN`` / ``…@example.invalid``) so the
  ``user_id`` FK is satisfied under ``PRAGMA foreign_keys=ON``. Nothing here is
  derived from production data (``threat_model_stats_v2`` §7.7).

Both the ``session_telemetry`` table and the ``users`` table must already
exist (run ``alembic upgrade head`` first).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Iterator

# Realistic-ish shape knobs. Tunable by callers via :func:`seed_session_telemetry`.
DEFAULT_ROW_COUNT = 5_000_000
DEFAULT_BATCH_SIZE = 50_000
DEFAULT_USER_COUNT = 40           # synthetic households
DEFAULT_CHANNEL_COUNT = 600       # upstream Dispatcharr channels
DEFAULT_PROVIDER_COUNT = 6        # upstream M3U providers
DEFAULT_NULL_PROVIDER_FRACTION = 0.05  # pre-GH-59-tagging history
DEFAULT_POLL_INTERVAL_MS = 10_000      # current stats_poll_interval (10s)
_SEED = 1_234_567  # deterministic — reproducible benchmark populations


@dataclass(frozen=True)
class VolumeShape:
    row_count: int = DEFAULT_ROW_COUNT
    batch_size: int = DEFAULT_BATCH_SIZE
    user_count: int = DEFAULT_USER_COUNT
    channel_count: int = DEFAULT_CHANNEL_COUNT
    provider_count: int = DEFAULT_PROVIDER_COUNT
    null_provider_fraction: float = DEFAULT_NULL_PROVIDER_FRACTION
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS
    seed: int = _SEED


_INSERT_SQL = (
    "INSERT INTO session_telemetry "
    "(session_id, observed_at, user_id, provider_id, channel_id, "
    " bytes_delta, buffer_event_count, poll_interval_ms) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

# session_telemetry.user_id is a real FK (users.id, ON DELETE SET NULL). When
# PRAGMA foreign_keys=ON (the app/test default), the parent rows must exist.
# Seed minimal synthetic users (ids 1..user_count) so the bulk insert is
# FK-clean. Username is deterministic + obviously synthetic — never derived
# from production data (threat_model_stats_v2 §7.7).
# NOTE: users has several NOT NULL columns whose defaults are Python-side
# (is_active, is_admin, created_at, updated_at) — they must be supplied
# explicitly here, and we must NOT use ``INSERT OR IGNORE`` (it would silently
# swallow a NOT NULL violation and leave the FK parent missing). We delete +
# re-insert ids 1..user_count instead, which is idempotent enough for a fixture.
_USERS_DELETE_SQL = "DELETE FROM users WHERE id BETWEEN 1 AND ?"
_USERS_SQL = (
    "INSERT INTO users "
    "(id, username, email, password_hash, auth_provider, "
    " is_active, is_admin, created_at, updated_at) "
    "VALUES (?, ?, ?, NULL, 'local', 1, 0, ?, ?)"
)


def _zipfish_provider(rng: random.Random, provider_count: int) -> int:
    """Pick a provider id (1..provider_count) with mild skew toward the first few."""
    # Weight provider k as 1/k — cheap Zipf-ish skew without numpy.
    weights = [1.0 / k for k in range(1, provider_count + 1)]
    return rng.choices(range(1, provider_count + 1), weights=weights, k=1)[0]


def _row_batches(shape: VolumeShape) -> Iterator[list[tuple]]:
    """Yield batches of parameter tuples for executemany."""
    rng = random.Random(shape.seed)
    # observed_at walks backwards from "now" in poll-interval steps.
    now_ms = int(time.time() * 1000)
    # Spread sessions: ~ row_count / (avg session length) distinct sessions.
    # Keep it simple — a session is just a uuid-ish string; correlation
    # realism matters less for a volume gate than row count + index shape.
    remaining = shape.row_count
    batch: list[tuple] = []
    i = 0
    while remaining > 0:
        n = min(shape.batch_size, remaining)
        for _ in range(n):
            observed_at = now_ms - i * shape.poll_interval_ms
            user_id = rng.randint(1, shape.user_count)
            if rng.random() < shape.null_provider_fraction:
                provider_id = None
            else:
                provider_id = _zipfish_provider(rng, shape.provider_count)
            channel_id = rng.randint(1, shape.channel_count)
            bytes_delta = rng.randint(0, 12_000_000)  # >= 0 — respects the CHECK
            buffer_event_count = 0 if rng.random() < 0.95 else rng.randint(1, 4)
            session_id = f"sess-{user_id:03d}-{channel_id:04d}-{i // 30}"
            batch.append(
                (
                    session_id,
                    observed_at,
                    user_id,
                    provider_id,
                    channel_id,
                    bytes_delta,
                    buffer_event_count,
                    shape.poll_interval_ms,
                )
            )
            i += 1
        yield batch
        batch = []
        remaining -= n


def seed_session_telemetry(connection, shape: VolumeShape | None = None) -> int:
    """Bulk-insert ``shape.row_count`` rows into ``session_telemetry``.

    ``connection`` is a SQLAlchemy ``Connection`` (or anything exposing
    ``connection.connection`` → a DBAPI connection with a cursor). Commits in
    batches so the WAL doesn't blow up. Returns the number of rows inserted.
    """
    shape = shape or VolumeShape()
    # Reach down to the raw DBAPI connection for executemany — the SQLAlchemy
    # Core ``connection.execute(text(...), [dict, dict, ...])` path is markedly
    # slower for millions of rows.
    raw = connection.connection  # DBAPI connection (sqlite3.Connection)

    # Seed synthetic parent users so the user_id FK is satisfied under
    # PRAGMA foreign_keys=ON.
    _ts = "2026-01-01T00:00:00"
    user_cur = raw.cursor()
    try:
        user_cur.execute(_USERS_DELETE_SQL, (shape.user_count,))
        user_cur.executemany(
            _USERS_SQL,
            [
                (uid, f"synthetic-volume-user-{uid:03d}",
                 f"synthetic-volume-{uid:03d}@example.invalid", _ts, _ts)
                for uid in range(1, shape.user_count + 1)
            ],
        )
    finally:
        user_cur.close()
    raw.commit()

    inserted = 0
    for batch in _row_batches(shape):
        cur = raw.cursor()
        try:
            cur.executemany(_INSERT_SQL, batch)
        finally:
            cur.close()
        raw.commit()
        inserted += len(batch)
    return inserted
