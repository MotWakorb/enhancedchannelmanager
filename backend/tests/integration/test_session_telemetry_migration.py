"""Migration tests for ``session_telemetry`` (revision 0006).

Bead: ``enhancedchannelmanager-skqln.2``.

Covers:

* Up migration on a fresh empty DB → table + all 5 indexes + the named CHECK
  constraint present (asserted via ``inspect()``).
* Down migration → table + indexes gone.
* Up → down → up round-trip on an empty DB is schema-identical.
* CHECK constraint is actually *enforced* in the test env (SQLite + Alembic
  can silently drop CHECKs — ``docs/database_migrations.md``).
* Account-deletion scrub: deleting a ``users`` row NULLs the corresponding
  ``session_telemetry.user_id`` (FK ``ON DELETE SET NULL`` fires).
  NOTE: the rollup-table extension of this scrub (delete a ``users`` row →
  no residual ``user_id`` in the daily rollup tables) is bead
  ``enhancedchannelmanager-7i2vv``'s responsibility — those tables don't exist
  yet.
* The 5M-row seeded migration up/down volume test — a **LOCAL PRE-MERGE GATE**,
  not a CI job. Marked ``slow`` (CI runs ``-m "not slow"``) AND gated behind
  ``ECM_RUN_VOLUME_TESTS=1`` so it never runs by accident. Prints wall-time
  for each direction. To run it locally:
  ``ECM_RUN_VOLUME_TESTS=1 python -m pytest \\
      tests/integration/test_session_telemetry_migration.py -m slow -s --no-cov``

All fixtures use synthetic identities only — no production-derived
``user_id``/``username``/``email`` values (``docs/security/threat_model_stats_v2.md`` §7.7).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import database


TABLE = "session_telemetry"
EXPECTED_INDEXES = {
    "idx_session_telemetry_observed_at": ["observed_at"],
    "idx_session_telemetry_user_observed": ["user_id", "observed_at"],
    "idx_session_telemetry_provider_observed": ["provider_id", "observed_at"],
    "idx_session_telemetry_session_id": ["session_id"],
    "idx_session_telemetry_provider_channel_observed_bytes": [
        "provider_id",
        "channel_id",
        "observed_at",
        "bytes_delta",
    ],
}
CHECK_NAME = "ck_session_telemetry_bytes_delta_non_negative"


def _make_alembic_config(db_url: str):
    from alembic.config import Config

    ini_path = Path(database.ALEMBIC_INI_PATH)
    assert ini_path.exists(), f"alembic.ini missing at {ini_path}"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _table_names(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _index_map(engine, table: str) -> dict[str, list[str]]:
    return {
        idx["name"]: list(idx["column_names"])
        for idx in inspect(engine).get_indexes(table)
    }


def _check_constraint_names(engine, table: str) -> set[str]:
    # SQLAlchemy's SQLite dialect surfaces named CHECK constraints via
    # get_check_constraints since 1.4.
    return {
        cc["name"]
        for cc in inspect(engine).get_check_constraints(table)
        if cc.get("name")
    }


def _structural_snapshot(engine):
    insp = inspect(engine)
    cols = [
        {"name": c["name"], "type": str(c["type"]), "nullable": c["nullable"]}
        for c in insp.get_columns(TABLE)
    ]
    indexes = sorted(
        ({"name": i["name"], "unique": i["unique"], "columns": list(i["column_names"])}
         for i in insp.get_indexes(TABLE)),
        key=lambda d: d["name"] or "",
    )
    fks = sorted(
        ({"cols": list(fk["constrained_columns"]),
          "ref": fk["referred_table"],
          "ref_cols": list(fk["referred_columns"]),
          "options": fk.get("options", {})}
         for fk in insp.get_foreign_keys(TABLE)),
        key=lambda d: tuple(d["cols"]),
    )
    checks = sorted(c.get("name") or "" for c in insp.get_check_constraints(TABLE))
    pk = list(insp.get_pk_constraint(TABLE).get("constrained_columns", []))
    return {"columns": cols, "indexes": indexes, "fks": fks, "checks": checks, "pk": pk}


@pytest.mark.integration
class TestSessionTelemetryMigration:
    """Revision 0006 — fresh up, down, round-trip, CHECK enforcement."""

    def test_upgrade_creates_table_indexes_and_check(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'st_up.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            assert TABLE in _table_names(engine), "session_telemetry not created"

            idx_map = _index_map(engine, TABLE)
            for name, cols in EXPECTED_INDEXES.items():
                assert name in idx_map, f"missing index {name}"
                assert idx_map[name] == cols, (
                    f"index {name} columns {idx_map[name]} != expected {cols}"
                )

            assert CHECK_NAME in _check_constraint_names(engine, TABLE), (
                f"named CHECK {CHECK_NAME} missing from {TABLE}"
            )

            # Columns / FK shape.
            cols = {c["name"]: c for c in inspect(engine).get_columns(TABLE)}
            # ``stream_id`` and ``stream_name`` land in migration 0010
            # (bd-kh23e) — both NULLABLE, no FK. ``dispatcharr_username``
            # lands in migration 0011 (bd-gsn3r) — TEXT NULL, no FK; the
            # denormalized read-side replacement for the dropped FK join
            # against ECM ``users``. ``reconnect_event_count``,
            # ``error_event_count``, and ``switch_event_count`` land in
            # migration 0013 (bd-ov5vb) — INTEGER NOT NULL DEFAULT 0,
            # paired with the pre-existing ``buffer_event_count`` so the
            # broadened channel-event ingest can attribute each
            # ``event_type`` to its own per-poll counter. ``emby_user_id``
            # and ``emby_user_name`` land in migration 0016 (bd-k026g) —
            # both TEXT NULL, denormalized Emby attribution from the
            # bd-2cenq epic resolver. ``plex_user_id``, ``plex_user_name``,
            # ``jellyfin_user_id``, and ``jellyfin_user_name`` land in
            # migration 0017 (bd-r5f0c.1) — all four TEXT NULL,
            # denormalized Plex + Jellyfin attribution at parity with
            # the Emby pair (the W2/W3/W4 resolvers + writer in epic
            # bd-r5f0c). Carry every column in the head-shape assertion
            # so a future delete/rename surfaces here.
            assert set(cols) == {
                "id", "session_id", "observed_at", "user_id", "provider_id",
                "channel_id", "bytes_delta", "buffer_event_count", "poll_interval_ms",
                "stream_id", "stream_name", "dispatcharr_username",
                "reconnect_event_count", "error_event_count", "switch_event_count",
                "emby_user_id", "emby_user_name",
                "plex_user_id", "plex_user_name",
                "jellyfin_user_id", "jellyfin_user_name",
            }
            assert cols["session_id"]["nullable"] is False
            assert cols["observed_at"]["nullable"] is False
            assert cols["user_id"]["nullable"] is True
            assert cols["provider_id"]["nullable"] is True
            # channel_id was Integer NULL in migration 0006; migration 0007
            # corrected it to VARCHAR(64) NOT NULL to match every other
            # channel-keyed table in the schema (bd-skqln.3 step (a)).
            assert cols["channel_id"]["nullable"] is False
            assert "VARCHAR" in str(cols["channel_id"]["type"]).upper(), (
                f"channel_id should be VARCHAR after migration 0007, "
                f"got {cols['channel_id']['type']!r}"
            )
            assert cols["bytes_delta"]["nullable"] is False
            assert cols["buffer_event_count"]["nullable"] is False
            # bd-ov5vb (migration 0013): the three per-type counters
            # are NOT NULL DEFAULT 0 — same shape contract as
            # ``buffer_event_count`` so SUM rollups never need
            # COALESCE / NULL-safe arithmetic.
            assert cols["reconnect_event_count"]["nullable"] is False
            assert cols["error_event_count"]["nullable"] is False
            assert cols["switch_event_count"]["nullable"] is False
            assert cols["poll_interval_ms"]["nullable"] is False
            # bd-kh23e: both stream identity columns are NULLABLE
            # (resolver-failure rows write NULL) and untyped against any
            # FK (``streams`` is not an ECM table).
            assert cols["stream_id"]["nullable"] is True
            assert cols["stream_name"]["nullable"] is True
            # bd-gsn3r: dispatcharr_username is NULLABLE (anonymous
            # viewers + pre-0011 rows surface as NULL on read).
            assert cols["dispatcharr_username"]["nullable"] is True
            # bd-k026g: both Emby attribution columns are NULLABLE
            # (non-Emby viewers + Emby-mediated rows where the resolver
            # could not match the active stream + pre-0016 rows surface
            # as NULL on read).
            assert cols["emby_user_id"]["nullable"] is True
            assert cols["emby_user_name"]["nullable"] is True
            # bd-r5f0c.1: all four Plex + Jellyfin attribution columns
            # are NULLABLE (non-Plex / non-Jellyfin viewers + rows where
            # the resolver could not match the active stream + pre-0017
            # rows surface as NULL on read).
            assert cols["plex_user_id"]["nullable"] is True
            assert cols["plex_user_name"]["nullable"] is True
            assert cols["jellyfin_user_id"]["nullable"] is True
            assert cols["jellyfin_user_name"]["nullable"] is True

            # bd-gsn3r: migration 0011 dropped the FK to ECM ``users.id``.
            # ECM and Dispatcharr ``users`` are different namespaces with
            # coincidentally-overlapping integer IDs; the FK was a
            # structural lie. ``user_id`` is now an opaque Dispatcharr-
            # side identifier with no constraints.
            fks = inspect(engine).get_foreign_keys(TABLE)
            assert fks == [], (
                "session_telemetry has unexpected FK constraints at head — "
                f"migration 0011 should have dropped the user_id FK; got {fks}"
            )
        finally:
            engine.dispose()

    def test_downgrade_removes_table_and_indexes(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'st_down.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0005")

        engine = create_engine(db_url, future=True)
        try:
            assert TABLE not in _table_names(engine), (
                "session_telemetry still present after downgrade to 0005"
            )
            # And nothing in sqlite_master references the indexes either.
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name LIKE 'idx_session_telemetry_%'"
                )).fetchall()
            assert rows == [], f"orphan session_telemetry indexes remain: {rows}"
        finally:
            engine.dispose()

    def test_round_trip_is_schema_identical(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'st_roundtrip.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "head")
        engine = create_engine(db_url, future=True)
        try:
            before = _structural_snapshot(engine)
        finally:
            engine.dispose()

        command.downgrade(cfg, "0005")
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            after = _structural_snapshot(engine)
        finally:
            engine.dispose()

        assert before == after, (
            f"session_telemetry schema differs across 0006 round-trip:\n"
            f"  before: {before}\n  after:  {after}"
        )

    def test_check_constraint_is_enforced(self, tmp_path):
        """A negative bytes_delta insert must be rejected by the DB.

        Guards against SQLite/Alembic silently dropping the CHECK. PRAGMA
        foreign_keys / general constraint enforcement is wired by
        ``database.py``'s connect listener, which Alembic + SQLAlchemy engines
        both inherit; CHECKs are always enforced in SQLite regardless, but this
        proves the constraint actually made it into the table DDL.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'st_check.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                # Valid row — bytes_delta >= 0 — must succeed.
                # channel_id is NOT NULL post-migration-0007: supply a
                # synthetic UUID string for both valid and CHECK-violation
                # inserts so the only thing being exercised is the CHECK.
                conn.execute(text(
                    "INSERT INTO session_telemetry "
                    "(session_id, observed_at, channel_id, bytes_delta, "
                    " buffer_event_count, poll_interval_ms) "
                    "VALUES ('sess-ok', 1000, 'ch-uuid-check', 0, 0, 10000)"
                ))
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(text(
                        "INSERT INTO session_telemetry "
                        "(session_id, observed_at, channel_id, bytes_delta, "
                        " buffer_event_count, poll_interval_ms) "
                        "VALUES ('sess-bad', 2000, 'ch-uuid-check', -1, 0, 10000)"
                    ))
        finally:
            engine.dispose()


@pytest.mark.integration
class TestSessionTelemetryAccountDeletionScrub:
    """Deleting an ECM ``users`` row no longer affects ``session_telemetry``.

    bd-gsn3r REVERSAL of the prior privacy semantic: this test originally
    asserted that deleting an ECM ``User`` SET-NULL'd matching
    ``session_telemetry.user_id`` rows via the FK. Migration 0011 dropped
    that FK because ECM ``users`` and Dispatcharr ``users`` are separate
    namespaces with coincidentally-overlapping integer IDs — the FK was a
    structural lie that joined ECM identity to Dispatcharr behavioral
    telemetry, and the SET NULL behavior would scrub the wrong human's
    rows on any ID collision.

    The new contract: deleting an ECM ``User`` is a no-op against
    ``session_telemetry`` (those rows are about Dispatcharr-side viewers,
    not ECM auth identities). Privacy of the actual Dispatcharr-side
    behavioral trail is the operator's concern via raw-row retention
    pruning (ADR-007 D1, 30 days) — they cannot scrub a Dispatcharr
    user from ECM because ECM does not own that identity.

    Privacy 11a §7.8 update follow-up: the threat model and ADR-007
    docstrings will be updated in a separate doc bead so this test
    documents the new contract for the runtime.
    """

    def test_deleting_user_does_not_touch_telemetry_user_id(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'st_scrub.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        # The connect listener that turns on PRAGMA foreign_keys is registered
        # against the SQLAlchemy Engine class in database.py, so a fresh engine
        # here inherits it. Belt-and-braces: also set it explicitly.
        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                conn.execute(text("PRAGMA foreign_keys=ON"))
                conn.commit()

            Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
            session = Session()
            try:
                import models

                user = models.User(
                    username="synthetic-scrub-user",
                    email="synthetic-scrub@example.invalid",
                    password_hash=None,
                    auth_provider="local",
                )
                session.add(user)
                session.flush()
                uid = user.id
                assert uid is not None

                # channel_id is String(64) NOT NULL after migration 0007.
                session.add_all([
                    models.SessionTelemetry(
                        session_id=f"sess-scrub-{n}",
                        observed_at=1_700_000_000_000 + n * 10_000,
                        user_id=uid,
                        provider_id=1,
                        channel_id="ch-uuid-scrub-42",
                        bytes_delta=1_000 * (n + 1),
                        buffer_event_count=0,
                        poll_interval_ms=10_000,
                    )
                    for n in range(5)
                ])
                # One unattributed row (user_id NULL) — must be untouched.
                session.add(models.SessionTelemetry(
                    session_id="sess-anon",
                    observed_at=1_700_000_000_000,
                    user_id=None,
                    provider_id=None,
                    channel_id="ch-uuid-scrub-7",
                    bytes_delta=500,
                    buffer_event_count=0,
                    poll_interval_ms=10_000,
                ))
                session.commit()

                # FK enforcement must be on for the SET NULL to fire on this
                # session's connection.
                session.execute(text("PRAGMA foreign_keys=ON"))
                session.commit()

                session.delete(user)
                session.commit()

                rows = session.execute(text(
                    "SELECT user_id FROM session_telemetry ORDER BY session_id"
                )).fetchall()
                # bd-gsn3r: post-FK-drop, the 5 attributed rows still
                # carry user_id == uid and the 1 anonymous row still
                # carries NULL. Deleting the ECM user is a no-op against
                # the telemetry trail because that trail is about a
                # different namespace (Dispatcharr-side viewer accounts).
                anon_rows = [r for r in rows if r[0] is None]
                attributed_rows = [r for r in rows if r[0] == uid]
                assert len(attributed_rows) == 5, (
                    f"expected 5 attributed rows untouched, got {attributed_rows}"
                )
                assert len(anon_rows) == 1, (
                    f"expected 1 already-null row untouched, got {anon_rows}"
                )
                assert len(rows) == 6
            finally:
                session.close()
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 5M-row volume gate — LOCAL PRE-MERGE ONLY, not CI.
# ---------------------------------------------------------------------------

_VOLUME_ENV_FLAG = "ECM_RUN_VOLUME_TESTS"


@pytest.mark.slow
@pytest.mark.skipif(
    not os.environ.get(_VOLUME_ENV_FLAG),
    reason=(
        f"Volume test: set {_VOLUME_ENV_FLAG}=1 to run. Local pre-merge gate, "
        "not CI (also marked 'slow', which CI excludes). See bead skqln.2 / the "
        "PR checklist."
    ),
)
def test_migration_up_down_against_5m_rows(tmp_path):
    """Seed ~5M session_telemetry rows, then time ``upgrade head`` (already at
    head — re-run is a no-op so we instead measure a *fresh* upgrade against a
    pre-seeded DB) and ``downgrade 0005`` against that population.

    To get a meaningful "upgrade against volume" number we: (1) upgrade head on
    an empty DB, (2) bulk-seed 5M rows, (3) downgrade to 0005 (this is the
    DROP-against-5M-rows timing — index + table drop on a big table is the
    expensive direction in SQLite), (4) upgrade head again (fresh CREATE — fast,
    empty), (5) re-seed and downgrade once more to confirm repeatability.

    Prints wall-time for the seed and for each migration direction. Leave this
    test gated; capture the timings in the PR.
    """
    from alembic import command
    from tests.fixtures.session_telemetry_volume import (
        VolumeShape,
        seed_session_telemetry,
    )

    db_url = f"sqlite:///{tmp_path / 'st_volume.db'}"
    cfg = _make_alembic_config(db_url)

    # 1. Fresh upgrade (empty) — establishes the baseline schema.
    t0 = time.perf_counter()
    command.upgrade(cfg, "head")
    t_up_empty = time.perf_counter() - t0

    # 2. Bulk-seed ~5M rows.
    shape = VolumeShape()  # 5,000,000 rows, realistic multi-provider + ~5% null
    engine = create_engine(db_url, future=True)
    try:
        with engine.connect() as conn:
            t0 = time.perf_counter()
            inserted = seed_session_telemetry(conn, shape)
            t_seed = time.perf_counter() - t0
        assert inserted == shape.row_count, f"seeded {inserted}, expected {shape.row_count}"
        # Sanity: ~5% of rows have NULL provider_id.
        with engine.connect() as conn:
            null_frac = conn.execute(text(
                "SELECT CAST(SUM(CASE WHEN provider_id IS NULL THEN 1 ELSE 0 END) AS FLOAT) "
                "/ COUNT(*) FROM session_telemetry"
            )).scalar()
        assert 0.02 < null_frac < 0.10, f"unexpected NULL-provider fraction: {null_frac}"
    finally:
        engine.dispose()

    # 3. Downgrade to 0005 against the 5M-row population — the expensive path.
    t0 = time.perf_counter()
    command.downgrade(cfg, "0005")
    t_down_5m = time.perf_counter() - t0

    engine = create_engine(db_url, future=True)
    try:
        assert TABLE not in _table_names(engine), "table survived downgrade against 5M rows"
    finally:
        engine.dispose()

    # 4. Re-upgrade (now empty again) — fresh CREATE, should be fast.
    t0 = time.perf_counter()
    command.upgrade(cfg, "head")
    t_up_after = time.perf_counter() - t0

    engine = create_engine(db_url, future=True)
    try:
        assert TABLE in _table_names(engine)
        # Round-trip the indexes back too.
        assert set(_index_map(engine, TABLE)) == set(EXPECTED_INDEXES)
    finally:
        engine.dispose()

    # 5. Re-seed + downgrade once more to confirm repeatability.
    engine = create_engine(db_url, future=True)
    try:
        with engine.connect() as conn:
            t0 = time.perf_counter()
            seed_session_telemetry(conn, shape)
            t_reseed = time.perf_counter() - t0
    finally:
        engine.dispose()
    t0 = time.perf_counter()
    command.downgrade(cfg, "0005")
    t_down_5m_again = time.perf_counter() - t0

    print(
        "\n[session_telemetry 5M-row volume gate] timings (seconds):\n"
        f"  upgrade head (empty DB)          : {t_up_empty:.3f}\n"
        f"  bulk-seed {shape.row_count:,} rows       : {t_seed:.3f}\n"
        f"  downgrade 0005 (against 5M rows) : {t_down_5m:.3f}\n"
        f"  upgrade head (empty again)       : {t_up_after:.3f}\n"
        f"  bulk-re-seed {shape.row_count:,} rows    : {t_reseed:.3f}\n"
        f"  downgrade 0005 (against 5M, #2)  : {t_down_5m_again:.3f}\n"
    )
