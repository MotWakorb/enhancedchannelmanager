from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
import pytest

import database


TABLE = "epg_event_probe_claims"


def _config(db_url: str) -> Config:
    config = Config(str(Path(database.ALEMBIC_INI_PATH)))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


@pytest.mark.integration
def test_migration_0054_adds_unique_claims_and_round_trips(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'epg_event_probe_claims.db'}"
    config = _config(db_url)
    command.upgrade(config, "0053")

    engine = create_engine(db_url)
    try:
        assert TABLE not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.upgrade(config, "0054")
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                f"INSERT INTO {TABLE} (trigger_key, claimed_at) "
                "VALUES ('schedule:event:channel', CURRENT_TIMESTAMP)"
            ))
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(
                    f"INSERT INTO {TABLE} (trigger_key, claimed_at) "
                    "VALUES ('schedule:event:channel', CURRENT_TIMESTAMP)"
                ))
    finally:
        engine.dispose()

    command.downgrade(config, "0053")
    engine = create_engine(db_url)
    try:
        assert TABLE not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
