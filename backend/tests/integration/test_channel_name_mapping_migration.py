"""u0ko6 upgrade/downgrade preserves existing data and owns aliases uniquely."""
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

import database


def test_mapping_migration_round_trip(tmp_path):
    url = f"sqlite:///{tmp_path / 'mappings.db'}"
    cfg = Config(str(database.ALEMBIC_INI_PATH))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0054")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE existing_fixture (value TEXT)"))
            conn.execute(text("INSERT INTO existing_fixture VALUES ('keep')"))
        command.upgrade(cfg, "0055")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO channel_name_mappings (id, preferred_name) VALUES (1, 'TVN'), (2, 'Other')"))
            conn.execute(text("INSERT INTO channel_name_aliases (mapping_id, name, match_key) VALUES (1, 'TVN-HD', 'tvn-hd')"))
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text("INSERT INTO channel_name_aliases (mapping_id, name, match_key) VALUES (2, 'tvn-hd', 'tvn-hd')"))
        # Application startup also supports models getting ahead of Alembic.
        command.stamp(cfg, "0054")
        command.upgrade(cfg, "0055")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT name FROM channel_name_aliases")).scalar_one() == "TVN-HD"
        command.downgrade(cfg, "0054")
        assert "channel_name_mappings" not in inspect(engine).get_table_names()
        assert "channel_name_aliases" not in inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT value FROM existing_fixture")).scalar_one() == "keep"
    finally:
        engine.dispose()
