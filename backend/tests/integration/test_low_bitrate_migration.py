from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import database
from models import StreamStats


def test_low_bitrate_migration_upgrade_downgrade_and_bootstrap(tmp_path):
    url = f"sqlite:///{tmp_path / 'low_bitrate.db'}"
    config = Config(str(Path(database.ALEMBIC_INI_PATH)))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0055")
    engine = create_engine(url)
    assert "is_low_bitrate" not in {column["name"] for column in inspect(engine).get_columns("stream_stats")}
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO stream_stats (stream_id, probe_status, created_at, consecutive_failures, is_black_screen, is_low_fps) VALUES (1, 'success', CURRENT_TIMESTAMP, 0, 0, 0)"))
    command.upgrade(config, "0056")
    assert database._schema_matches_head(engine) is True
    with engine.connect() as connection:
        assert connection.execute(text("SELECT is_low_bitrate FROM stream_stats WHERE stream_id=1")).scalar_one() == 0
    command.downgrade(config, "0055")
    assert "is_low_bitrate" not in {column["name"] for column in inspect(engine).get_columns("stream_stats")}
    command.upgrade(config, "0056")
    engine.dispose()

    bootstrap_url = f"sqlite:///{tmp_path / 'bootstrap.db'}"
    bootstrap = create_engine(bootstrap_url)
    StreamStats.__table__.create(bootstrap)
    with bootstrap.begin() as connection:
        connection.execute(text("INSERT INTO stream_stats (stream_id, probe_status, created_at, consecutive_failures, is_black_screen, is_low_fps) VALUES (1, 'success', CURRENT_TIMESTAMP, 0, 0, 0)"))
        assert connection.execute(text("SELECT is_low_bitrate FROM stream_stats WHERE stream_id=1")).scalar_one() == 0
    config.set_main_option("sqlalchemy.url", bootstrap_url)
    command.stamp(config, "0055")
    command.upgrade(config, "0056")
    assert "is_low_bitrate" in {column["name"] for column in inspect(bootstrap).get_columns("stream_stats")}
    bootstrap.dispose()
