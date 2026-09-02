import json

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

import database
from tests.integration.test_alembic_smoke import _make_alembic_config


def _database(tmp_path, name):
    url = f"sqlite:///{tmp_path / name}"
    return url, _make_alembic_config(url)


def _column(engine):
    return next(
        item for item in inspect(engine).get_columns("auto_creation_executions")
        if item["name"] == "selected_rule_outcomes"
    )


def _add_column(url, ddl):
    engine = create_engine(url, future=True)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                f"ALTER TABLE auto_creation_executions ADD COLUMN "
                f"selected_rule_outcomes {ddl}"
            ))
    finally:
        engine.dispose()


def test_fresh_and_0050_upgrade_create_nullable_text(tmp_path):
    for target, name in (("head", "fresh.db"), ("0050", "from-0050.db")):
        url, cfg = _database(tmp_path, name)
        command.upgrade(cfg, target)
        if target == "0050":
            command.upgrade(cfg, "head")
        engine = create_engine(url, future=True)
        try:
            column = _column(engine)
            assert str(column["type"]).upper() == "TEXT"
            assert column["nullable"] is True
        finally:
            engine.dispose()


def test_upgrade_is_idempotent_only_for_compatible_existing_shape(tmp_path):
    url, cfg = _database(tmp_path, "compatible.db")
    command.upgrade(cfg, "0050")
    _add_column(url, "TEXT NULL")

    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    try:
        assert _column(engine)["nullable"] is True
    finally:
        engine.dispose()


@pytest.mark.parametrize("ddl", ["VARCHAR(255) NULL", "TEXT NOT NULL DEFAULT ''"])
def test_upgrade_rejects_incompatible_existing_shape(tmp_path, ddl):
    url, cfg = _database(tmp_path, ddl.split()[0].replace("(", "-") + ".db")
    command.upgrade(cfg, "0050")
    _add_column(url, ddl)

    with pytest.raises(RuntimeError, match="selected_rule_outcomes.*nullable TEXT"):
        command.upgrade(cfg, "head")


def test_smart_bootstrap_and_post_bootstrap_reject_incompatible_shape(tmp_path):
    url, cfg = _database(tmp_path, "bootstrap.db")
    command.upgrade(cfg, "0050")
    _add_column(url, "VARCHAR(255) NULL")
    engine = create_engine(url, future=True)
    try:
        assert database._schema_matches_head(engine) is False
        with pytest.raises(RuntimeError, match="selected_rule_outcomes.*nullable TEXT"):
            database._assert_schema_matches_models(engine)

        command.stamp(cfg, "head")
        with pytest.raises(RuntimeError, match="selected_rule_outcomes.*nullable TEXT"):
            database._assert_schema_matches_models(engine)
    finally:
        engine.dispose()


def test_downgrade_refuses_selected_audit_data(tmp_path):
    url, cfg = _database(tmp_path, "populated.db")
    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO auto_creation_executions "
                "(mode, triggered_by, started_at, status, streams_evaluated, "
                "streams_matched, channels_created, channels_updated, "
                "groups_created, streams_merged, channels_touched, streams_skipped, "
                "streams_excluded, is_event_sync, created_at, selected_rule_outcomes) "
                "VALUES ('execute', 'api', CURRENT_TIMESTAMP, 'completed', 0, 0, "
                "0, 0, 0, 0, 0, 0, 0, 0, CURRENT_TIMESTAMP, :outcomes)"
            ), {"outcomes": json.dumps([{
                "rule_id": 1, "rule_name": "Cached", "rule_kind": "standard",
                "status": "completed", "match_count": 0, "error_count": 0,
            }])})
    finally:
        engine.dispose()

    with pytest.raises(
        RuntimeError,
        match="all ECM instances.*stopped.*backup.*selected.*audit data",
    ):
        command.downgrade(cfg, "0050")


def test_downgrade_refuses_active_execution_even_without_selected_audit(tmp_path):
    url, cfg = _database(tmp_path, "active.db")
    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO auto_creation_executions "
                "(mode, triggered_by, started_at, status, streams_evaluated, "
                "streams_matched, channels_created, channels_updated, "
                "groups_created, streams_merged, channels_touched, streams_skipped, "
                "streams_excluded, is_event_sync, created_at) "
                "VALUES ('execute', 'api', CURRENT_TIMESTAMP, 'running', 0, 0, "
                "0, 0, 0, 0, 0, 0, 0, 0, CURRENT_TIMESTAMP)"
            ))
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="active/running execution"):
        command.downgrade(cfg, "0050")


def test_empty_downgrade_and_reupgrade_are_allowed(tmp_path):
    url, cfg = _database(tmp_path, "empty.db")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0050")
    engine = create_engine(url, future=True)
    try:
        names = {item["name"] for item in inspect(engine).get_columns(
            "auto_creation_executions"
        )}
        assert "selected_rule_outcomes" not in names
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    try:
        assert str(_column(engine)["type"]).upper() == "TEXT"
    finally:
        engine.dispose()
