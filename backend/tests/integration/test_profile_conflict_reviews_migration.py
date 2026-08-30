from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
import pytest

import database


TABLE = "profile_conflict_reviews"


def _config(db_url: str) -> Config:
    cfg = Config(str(Path(database.ALEMBIC_INI_PATH)))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.mark.integration
def test_migration_0050_fresh_shape_constraints_and_round_trip(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'profile_conflicts.db'}"
    cfg = _config(db_url)
    command.upgrade(cfg, "0050")
    engine = create_engine(db_url)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns(TABLE)}
        assert columns == {
            "id", "fingerprint", "fingerprint_version", "effective_group_id",
            "status", "accepted_choice_key", "accepted_profile_ids", "evidence",
            "created_at", "last_seen_at", "resolved_at", "applied_at",
            "actor_token_id", "retry_error", "notified_at", "accept_journaled_at",
        }
        indexes = {row["name"]: row for row in inspect(engine).get_indexes(TABLE)}
        assert indexes["uq_profile_conflict_reviews_fingerprint"]["unique"]
        with engine.begin() as conn:
            conn.execute(text(
                f"INSERT INTO {TABLE} (fingerprint, effective_group_id, status, evidence, created_at, last_seen_at) "
                "VALUES ('abc', 665, 'pending', '{}', 1, 1)"
            ))
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text(
                    f"INSERT INTO {TABLE} (fingerprint, effective_group_id, status, evidence, created_at, last_seen_at) "
                    "VALUES ('abc', 666, 'pending', '{}', 1, 1)"
                ))
    finally:
        engine.dispose()

    command.downgrade(cfg, "0049")
    engine = create_engine(db_url)
    try:
        assert TABLE not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_migration_0050_is_idempotent_over_create_all_drift(tmp_path):
    import models

    db_url = f"sqlite:///{tmp_path / 'profile_conflicts_drift.db'}"
    cfg = _config(db_url)
    command.upgrade(cfg, "0049")
    engine = create_engine(db_url)
    models.Base.metadata.create_all(engine)
    engine.dispose()
    command.upgrade(cfg, "0050")
