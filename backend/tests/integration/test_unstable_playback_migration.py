from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import database
from models import StreamStats


def _columns(engine):
    return {column["name"]: column for column in inspect(engine).get_columns("stream_stats")}


def test_unstable_migration_upgrade_downgrade_and_bootstrap(tmp_path):
    url = f"sqlite:///{tmp_path / 'unstable.db'}"
    config = Config(str(Path(database.ALEMBIC_INI_PATH)))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0057")
    engine = create_engine(url)
    assert "is_unstable" not in _columns(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO stream_stats (stream_id, probe_status, created_at, consecutive_failures, "
            "is_black_screen, is_low_fps, is_low_bitrate) VALUES (1, 'success', CURRENT_TIMESTAMP, 0, 0, 0, 0)"
        ))
    command.upgrade(config, "0058")
    assert database._schema_matches_head(engine) is True
    assert _columns(engine)["is_unstable"]["nullable"] is True
    with engine.connect() as connection:
        assert connection.execute(text("SELECT is_unstable FROM stream_stats WHERE stream_id=1")).scalar_one() is None
    command.downgrade(config, "0057")
    assert "is_unstable" not in _columns(engine)
    command.upgrade(config, "0058")
    engine.dispose()

    bootstrap_url = f"sqlite:///{tmp_path / 'bootstrap.db'}"
    bootstrap = create_engine(bootstrap_url)
    StreamStats.__table__.create(bootstrap)
    assert "is_unstable" in _columns(bootstrap)
    config.set_main_option("sqlalchemy.url", bootstrap_url)
    command.stamp(config, "0057")
    command.upgrade(config, "0058")
    assert "is_unstable" in _columns(bootstrap)
    bootstrap.dispose()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [({}, None), ({"is_unstable": True}, True), ({"is_unstable": False}, False)],
)
def test_to_dict_exposes_tri_state(kwargs, expected):
    assert StreamStats(stream_id=1, **kwargs).to_dict()["is_unstable"] is expected
