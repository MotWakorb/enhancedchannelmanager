from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import database
from models import StreamStats

PLAYBACK_COLUMNS = {
    "playback_samples",
    "playback_seconds",
    "playback_discontinuities",
    "playback_reconnects",
    "playback_start_failures",
    "last_playback_probed",
}


def _stream_stats_columns(engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("stream_stats")}


def test_playback_stability_migration_upgrade_downgrade_and_bootstrap(tmp_path):
    url = f"sqlite:///{tmp_path / 'playback.db'}"
    config = Config(str(Path(database.ALEMBIC_INI_PATH)))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0056")
    engine = create_engine(url)
    assert not PLAYBACK_COLUMNS & _stream_stats_columns(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO stream_stats (stream_id, probe_status, created_at, consecutive_failures, is_black_screen, is_low_fps, is_low_bitrate) VALUES (1, 'success', CURRENT_TIMESTAMP, 0, 0, 0, 0)"))
    command.upgrade(config, "0057")
    assert database._schema_matches_head(engine) is True
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT playback_samples, playback_seconds, playback_discontinuities, playback_reconnects, playback_start_failures, last_playback_probed "
            "FROM stream_stats WHERE stream_id=1"
        )).one()
    assert tuple(row) == (None, 0, 0, 0, 0, None)
    command.downgrade(config, "0056")
    assert not PLAYBACK_COLUMNS & _stream_stats_columns(engine)
    command.upgrade(config, "0057")
    engine.dispose()

    bootstrap_url = f"sqlite:///{tmp_path / 'bootstrap.db'}"
    bootstrap = create_engine(bootstrap_url)
    StreamStats.__table__.create(bootstrap)
    with bootstrap.begin() as connection:
        connection.execute(text("INSERT INTO stream_stats (stream_id, probe_status, created_at, consecutive_failures, is_black_screen, is_low_fps, is_low_bitrate) VALUES (1, 'success', CURRENT_TIMESTAMP, 0, 0, 0, 0)"))
        assert connection.execute(text("SELECT playback_seconds FROM stream_stats WHERE stream_id=1")).scalar_one() == 0
    config.set_main_option("sqlalchemy.url", bootstrap_url)
    command.stamp(config, "0056")
    command.upgrade(config, "0057")
    assert PLAYBACK_COLUMNS <= _stream_stats_columns(bootstrap)
    bootstrap.dispose()
