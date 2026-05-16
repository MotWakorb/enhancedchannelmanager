"""Migration tests for the Stats v2 rollup tables (revision 0009).

Bead: ``enhancedchannelmanager-7i2vv``.

Covers, for revision 0009 ``session_telemetry_rollup``:

* Fresh up on an empty DB → three new tables + their indexes + correct
  column types/nullability/PKs. Asserted via SQLAlchemy ``inspect()`` so
  the test reads the *structural* result Alembic produced, not the raw
  DDL text (SQLite rewrites the DDL string during batch ops even when
  the logical schema is identical).
* Fresh down (0009 → 0008) → the three tables and all their indexes are
  gone.
* Round-trip on an empty DB → schema-identical after up → down → up.
* The TEXT ``provider_id`` constraint actually enforces — INSERTing
  ``provider_id = 'unknown'`` succeeds (proves the 'unknown' bucket
  pattern from ADR-007 §line 109 is representable).

The migration is rollup-table-shape only; the rollup *job* and its
``stats_v2_rollup`` task tests live in
``test_stats_v2_rollup_task.py``.

All synthetic identities — no production-derived ``user_id`` / channel
UUIDs (``docs/security/threat_model_stats_v2.md`` §7.7).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

import database


USER_DAILY = "session_telemetry_user_daily"
PROVIDER_DAILY = "session_telemetry_provider_daily"
ROLLUP_STATE = "telemetry_rollup_state"

EXPECTED_USER_DAILY_INDEXES = {
    "idx_session_telemetry_user_daily_day": ["day"],
}
EXPECTED_PROVIDER_DAILY_INDEXES = {
    "idx_session_telemetry_provider_daily_provider_day": ["provider_id", "day"],
    "idx_session_telemetry_provider_daily_day": ["day"],
}


def _make_alembic_config(db_url: str):
    """Build an Alembic Config pinned to the given SQLite URL.

    Mirrors the helper in the sibling migration tests so we don't drag a
    shared helper into the test suite.
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
    return {
        idx["name"]: list(idx["column_names"])
        for idx in inspect(engine).get_indexes(table)
    }


def _structural_snapshot(engine, table: str) -> dict:
    insp = inspect(engine)
    cols = [
        {"name": c["name"], "type": str(c["type"]), "nullable": c["nullable"]}
        for c in insp.get_columns(table)
    ]
    indexes = sorted(
        ({"name": i["name"], "unique": i["unique"], "columns": list(i["column_names"])}
         for i in insp.get_indexes(table)),
        key=lambda d: d["name"] or "",
    )
    pk = list(insp.get_pk_constraint(table).get("constrained_columns", []))
    return {"columns": cols, "indexes": indexes, "pk": pk}


@pytest.mark.integration
class TestRollupMigrationUpgrade:
    """Revision 0009 — fresh upgrade creates the three rollup tables."""

    def test_creates_user_daily_table_and_indexes(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'rollup_user.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            assert USER_DAILY in _table_names(engine), (
                f"{USER_DAILY} not created"
            )

            idx_map = _index_map(engine, USER_DAILY)
            for name, cols in EXPECTED_USER_DAILY_INDEXES.items():
                assert name in idx_map, f"missing index {name}"
                assert idx_map[name] == cols, (
                    f"index {name} columns {idx_map[name]} != expected {cols}"
                )

            cols = {c["name"]: c for c in inspect(engine).get_columns(USER_DAILY)}
            assert set(cols) == {
                "user_id", "channel_id", "day", "watch_seconds", "session_count"
            }
            # user_id is INTEGER NOT NULL (raw NULLs excluded at rollup time).
            assert cols["user_id"]["nullable"] is False
            assert "INT" in str(cols["user_id"]["type"]).upper()
            # channel_id matches session_telemetry.channel_id post-0007.
            assert cols["channel_id"]["nullable"] is False
            assert "VARCHAR" in str(cols["channel_id"]["type"]).upper()
            # day is a DATE column.
            assert cols["day"]["nullable"] is False
            assert "DATE" in str(cols["day"]["type"]).upper()
            assert cols["watch_seconds"]["nullable"] is False
            assert cols["session_count"]["nullable"] is False

            # PK is composite (user_id, channel_id, day).
            pk_cols = inspect(engine).get_pk_constraint(USER_DAILY)["constrained_columns"]
            assert pk_cols == ["user_id", "channel_id", "day"], (
                f"unexpected PK shape: {pk_cols}"
            )
        finally:
            engine.dispose()

    def test_creates_provider_daily_table_and_indexes(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'rollup_provider.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            assert PROVIDER_DAILY in _table_names(engine), (
                f"{PROVIDER_DAILY} not created"
            )

            idx_map = _index_map(engine, PROVIDER_DAILY)
            for name, cols in EXPECTED_PROVIDER_DAILY_INDEXES.items():
                assert name in idx_map, f"missing index {name}"
                assert idx_map[name] == cols, (
                    f"index {name} columns {idx_map[name]} != expected {cols}"
                )

            cols = {c["name"]: c for c in inspect(engine).get_columns(PROVIDER_DAILY)}
            assert set(cols) == {
                "provider_id",
                "channel_id",
                "day",
                "watch_seconds",
                "bytes_delta_sum",
                "buffer_event_count",
            }
            # provider_id is TEXT NOT NULL so the 'unknown' bucket can live
            # in the PK as a literal string (ADR-007 §line 109).
            assert cols["provider_id"]["nullable"] is False
            assert "TEXT" in str(cols["provider_id"]["type"]).upper()
            assert cols["channel_id"]["nullable"] is False
            assert "VARCHAR" in str(cols["channel_id"]["type"]).upper()
            assert cols["day"]["nullable"] is False
            assert "DATE" in str(cols["day"]["type"]).upper()

            pk_cols = inspect(engine).get_pk_constraint(PROVIDER_DAILY)["constrained_columns"]
            assert pk_cols == ["provider_id", "channel_id", "day"], (
                f"unexpected PK shape: {pk_cols}"
            )
        finally:
            engine.dispose()

    def test_creates_telemetry_rollup_state_table(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'rollup_state.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            assert ROLLUP_STATE in _table_names(engine), (
                f"{ROLLUP_STATE} not created"
            )

            cols = {c["name"]: c for c in inspect(engine).get_columns(ROLLUP_STATE)}
            assert set(cols) == {
                "rollup_name",
                "last_completed_day",
                "last_run_at_ms",
                "last_run_status",
                "last_run_error",
            }
            # rollup_name is the PK; the rest are nullable (first-run case).
            assert cols["rollup_name"]["nullable"] is False
            assert cols["last_completed_day"]["nullable"] is True
            assert cols["last_run_at_ms"]["nullable"] is True
            assert cols["last_run_status"]["nullable"] is True
            assert cols["last_run_error"]["nullable"] is True

            pk_cols = inspect(engine).get_pk_constraint(ROLLUP_STATE)["constrained_columns"]
            assert pk_cols == ["rollup_name"]
        finally:
            engine.dispose()

    def test_unknown_provider_id_bucket_is_representable(self, tmp_path):
        """The 'unknown' string can land in provider_daily.provider_id.

        ADR-007 §line 109 says NULL provider_id surfaces as the 'unknown'
        bucket. The rollup job CASTs NULL → 'unknown' at write time; this
        test proves the destination column accepts that literal.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'rollup_unknown.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    f"INSERT INTO {PROVIDER_DAILY} "
                    f"(provider_id, channel_id, day, watch_seconds, "
                    f" bytes_delta_sum, buffer_event_count) "
                    f"VALUES ('unknown', 'ch-uuid-unknown', '2026-05-12', "
                    f"        100, 5000000, 2)"
                ))
                rows = conn.execute(text(
                    f"SELECT provider_id, watch_seconds FROM {PROVIDER_DAILY}"
                )).fetchall()
            assert rows == [("unknown", 100)], (
                f"'unknown' bucket row not retrievable: got {rows}"
            )
        finally:
            engine.dispose()


@pytest.mark.integration
class TestRollupMigrationDowngrade:
    """Revision 0009 down — the three tables and their indexes are gone."""

    def test_downgrade_removes_all_three_tables(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'rollup_down.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0008")

        engine = create_engine(db_url, future=True)
        try:
            tables = _table_names(engine)
            for tbl in (USER_DAILY, PROVIDER_DAILY, ROLLUP_STATE):
                assert tbl not in tables, (
                    f"{tbl} still present after downgrade to 0008"
                )
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name LIKE 'idx_session_telemetry_%_daily%'"
                )).fetchall()
            assert rows == [], (
                f"orphan rollup-table indexes remain after downgrade: {rows}"
            )
        finally:
            engine.dispose()


@pytest.mark.integration
class TestRollupMigrationRoundTrip:
    """Revision 0009 round-trip — up → down → up is schema-identical."""

    def test_round_trip_is_schema_identical(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'rollup_roundtrip.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "head")
        engine = create_engine(db_url, future=True)
        try:
            before = {
                tbl: _structural_snapshot(engine, tbl)
                for tbl in (USER_DAILY, PROVIDER_DAILY, ROLLUP_STATE)
            }
        finally:
            engine.dispose()

        command.downgrade(cfg, "0008")
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            after = {
                tbl: _structural_snapshot(engine, tbl)
                for tbl in (USER_DAILY, PROVIDER_DAILY, ROLLUP_STATE)
            }
        finally:
            engine.dispose()

        assert before == after, (
            f"rollup-table schema differs across 0009 round-trip:\n"
            f"  before: {before}\n  after:  {after}"
        )
