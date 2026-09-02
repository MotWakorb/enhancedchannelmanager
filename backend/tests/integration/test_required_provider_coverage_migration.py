from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
import pytest

import database


TABLE = "auto_creation_rules"
COLUMN = "required_provider_ids"


def _config(db_url: str) -> Config:
    cfg = Config(str(Path(database.ALEMBIC_INI_PATH)))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.mark.integration
def test_migration_0053_adds_and_round_trips_required_provider_ids(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'required_provider_coverage.db'}"
    cfg = _config(db_url)
    command.upgrade(cfg, "0052")

    engine = create_engine(db_url)
    try:
        assert COLUMN not in {item["name"] for item in inspect(engine).get_columns(TABLE)}
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO auto_creation_rules "
                "(id, name, enabled, priority, conditions, actions, run_on_refresh, "
                "stop_on_first_match, probe_on_sort, quality_m3u_tie_break_enabled, "
                "skip_struck_streams, orphan_action, match_scope_target_group, "
                "allow_manual_channel_merge, fold_match_key, match_count, "
                "created_at, updated_at) VALUES "
                "(1, 'provider-coverage', 1, 0, '[]', '[]', 0, 1, 0, 1, 0, "
                "'delete', 1, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ))
    finally:
        engine.dispose()

    command.upgrade(cfg, "0053")
    engine = create_engine(db_url)
    try:
        assert COLUMN in {item["name"] for item in inspect(engine).get_columns(TABLE)}
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE auto_creation_rules SET required_provider_ids = '[11, 22]' WHERE id = 1"
            ))
            assert conn.execute(text(
                "SELECT required_provider_ids FROM auto_creation_rules WHERE id = 1"
            )).scalar_one() == "[11, 22]"
    finally:
        engine.dispose()

    command.downgrade(cfg, "0052")
    engine = create_engine(db_url)
    try:
        assert COLUMN not in {item["name"] for item in inspect(engine).get_columns(TABLE)}
        with engine.connect() as conn:
            assert conn.execute(text(
                "SELECT name FROM auto_creation_rules WHERE id = 1"
            )).scalar_one() == "provider-coverage"
    finally:
        engine.dispose()
