"""Exclusion migration/persistence and independent writer behavior on SQLite."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import sqlite3

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import database
from m3u_change_exclusions import M3UChangeExclusionsUpdate, list_exclusions, update_exclusions


def test_migration_round_trip_preserves_history_and_existing_exclusions(tmp_path):
    url = f"sqlite:///{tmp_path / 'exclusions.db'}"
    cfg = Config(str(database.ALEMBIC_INI_PATH))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0056")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO m3u_change_logs (m3u_account_id, change_time, change_type, group_name, count, enabled) "
                "VALUES (1, '2026-11-16', 'streams_added', 'Sports', 5, 1)"
            ))
        command.upgrade(cfg, "0057")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO m3u_change_exclusions VALUES (1, 'Sports')"))
        for sql in [
            "INSERT INTO m3u_change_exclusions VALUES (1, 'Sports')",
            "INSERT INTO m3u_change_exclusions VALUES (0, 'Sports')",
            "INSERT INTO m3u_change_exclusions VALUES (1, '')",
        ]:
            with pytest.raises(IntegrityError), engine.begin() as conn:
                conn.execute(text(sql))
        # Models-ahead-of-Alembic startup must not erase saved preferences.
        command.stamp(cfg, "0056")
        command.upgrade(cfg, "0057")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT group_name FROM m3u_change_exclusions")).scalar_one() == "Sports"
        command.downgrade(cfg, "0056")
        assert "m3u_change_exclusions" not in inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count FROM m3u_change_logs")).scalar_one() == 5
    finally:
        engine.dispose()


def test_independent_concurrent_writers_and_reopened_database(tmp_path):
    from models import M3UChangeExclusion

    url = f"sqlite:///{tmp_path / 'concurrent.db'}"
    engine = create_engine(url, connect_args={"timeout": 10})
    @event.listens_for(engine, "connect")
    def legacy_bind_limit(connection, _record):
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

    M3UChangeExclusion.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    barrier = Barrier(2)

    def save(account):
        barrier.wait(timeout=5)
        with factory() as session:
            update_exclusions(session, M3UChangeExclusionsUpdate(add=[
                {"m3u_account_id": account, "group_name": f"Sports {index}"} for index in range(600)
            ]))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(save, [1, 2]))
    finally:
        engine.dispose()
    reopened = create_engine(url)
    try:
        with sessionmaker(bind=reopened)() as session:
            assert len(list_exclusions(session)["exclusions"]) == 1200
            update_exclusions(session, M3UChangeExclusionsUpdate(remove=[
                {"m3u_account_id": 1, "group_name": f"Sports {index}"} for index in range(600)
            ]))
            assert {row["m3u_account_id"] for row in list_exclusions(session)["exclusions"]} == {2}
    finally:
        reopened.dispose()


def test_standard_backup_keeps_saved_display_preferences(tmp_path):
    from routers.backup import _scrub_journal_db_in_place

    path = tmp_path / "journal.db"
    engine = create_engine(f"sqlite:///{path}")
    from models import M3UChangeExclusion
    M3UChangeExclusion.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO m3u_change_exclusions VALUES (7, 'Sports 🏈')"))
    engine.dispose()
    _scrub_journal_db_in_place(path)
    reopened = create_engine(f"sqlite:///{path}")
    try:
        with reopened.connect() as conn:
            assert conn.execute(text("SELECT m3u_account_id, group_name FROM m3u_change_exclusions")).one() == (7, "Sports 🏈")
    finally:
        reopened.dispose()


def test_filter_query_plan_uses_composite_exclusion_index(test_session):
    from m3u_change_exclusions import visible_changes
    from models import M3UChangeLog

    query = visible_changes(test_session.query(M3UChangeLog))
    sql = str(query.statement.compile(compile_kwargs={"literal_binds": True}))
    plan = test_session.execute(text(f"EXPLAIN QUERY PLAN {sql}")).all()
    assert any(
        "SEARCH m3u_change_exclusions USING COVERING INDEX" in row[3]
        and "m3u_account_id=? AND group_name=?" in row[3]
        for row in plan
    ), plan
