from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
import pytest

import database


TABLE = "event_sync_reviews"
INDEX = "idx_event_sync_reviews_status_seen_id"
EXISTING_INDEX = "idx_event_sync_reviews_status_created"


def _config(db_url: str) -> Config:
    cfg = Config(str(Path(database.ALEMBIC_INI_PATH)))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _indexes(engine) -> dict[str, list[str]]:
    return {
        row["name"]: list(row["column_names"])
        for row in inspect(engine).get_indexes(TABLE)
    }


@pytest.mark.integration
def test_migration_0052_adds_and_round_trips_retention_index(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'event_sync_retention_index.db'}"
    cfg = _config(db_url)
    command.upgrade(cfg, "0051")

    engine = create_engine(db_url)
    try:
        assert INDEX not in _indexes(engine)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO auto_creation_rules "
                "(id, name, enabled, priority, conditions, actions, run_on_refresh, "
                "stop_on_first_match, probe_on_sort, quality_m3u_tie_break_enabled, "
                "skip_struck_streams, orphan_action, match_scope_target_group, "
                "allow_manual_channel_merge, fold_match_key, match_count, "
                "created_at, updated_at) VALUES "
                "(1, 'retention-index', 1, 0, '[]', '[]', 0, 1, 0, 1, 0, "
                "'delete', 1, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ))
            conn.execute(text(
                f"INSERT INTO {TABLE} "
                "(rule_id, provider_id, stream_name_hash, event_key, status, "
                "created_at, last_seen_at, evidence) VALUES "
                "(1, 7, :stream_hash, 'event|2026-09-02T00:00:00+00:00', "
                "'rejected', 1, 2, '{}')"
            ), {"stream_hash": "a" * 64})
        assert database._schema_matches_head(engine) is True
        database._bootstrap_alembic(engine)
    finally:
        engine.dispose()

    engine = create_engine(db_url)
    try:
        indexes = _indexes(engine)
        assert indexes[INDEX] == ["status", "last_seen_at", "id"]
        assert indexes[EXISTING_INDEX] == ["status", "created_at"]
        with engine.connect() as conn:
            assert conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar_one() == 1
    finally:
        engine.dispose()

    command.downgrade(cfg, "0051")
    engine = create_engine(db_url)
    try:
        indexes = _indexes(engine)
        assert INDEX not in indexes
        assert indexes[EXISTING_INDEX] == ["status", "created_at"]
        with engine.connect() as conn:
            assert conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar_one() == 1
    finally:
        engine.dispose()

    command.upgrade(cfg, "0052")
    engine = create_engine(db_url)
    try:
        assert _indexes(engine)[INDEX] == ["status", "last_seen_at", "id"]
    finally:
        engine.dispose()
