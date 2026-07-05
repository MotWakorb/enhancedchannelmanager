"""Tests for ``database._heal_orphaned_normalization_group_refs`` (GH #465 / bd-miut3).

Before bd-miut3, deleting a NormalizationRuleGroup left its id behind in any
``auto_creation_rules.normalization_group_ids`` JSON list. The rule editor then
could not save (the write-time validator rejected the dangling id with 422).
bd-miut3 fixes the delete path going forward; this startup heal repairs rows
orphaned by deletions that happened *before* the fix shipped.

Why a heal vs an Alembic migration: same as the task_schedules heal — the
bd-5w6jz smart-bootstrap fast-path stamps ``alembic_version`` forward when the
live schema covers the model shape, so an Alembic data migration would be
silently skipped on existing installs.
"""
from __future__ import annotations

import json
import logging

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from models import Base, NormalizationRuleGroup, ChannelPipelineRule


@pytest.fixture
def file_engine(tmp_path):
    """File-backed SQLite engine with the full schema."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'heal.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _seed(engine, *, valid_group_ids: list[int], rule_ids_json: str) -> int:
    """Create the given groups and one rule with the given normalization_group_ids JSON.

    Returns the rule id.
    """
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    try:
        for gid in valid_group_ids:
            s.add(NormalizationRuleGroup(id=gid, name=f"G{gid}", enabled=True, priority=gid))
        rule = ChannelPipelineRule(
            name="Rule", conditions="[]", actions="[]",
            normalization_group_ids=rule_ids_json,
        )
        s.add(rule)
        s.commit()
        return rule.id
    finally:
        s.close()


def _read_norm_ids(engine, rule_id: int):
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    try:
        return s.query(ChannelPipelineRule).get(rule_id).get_normalization_group_ids()
    finally:
        s.close()


def test_strips_orphaned_id_keeps_valid_ids(file_engine):
    # Group 1 exists; group 99 was deleted but its id lingers in the rule.
    rule_id = _seed(file_engine, valid_group_ids=[1], rule_ids_json=json.dumps([1, 99]))

    with file_engine.begin() as conn:
        database._heal_orphaned_normalization_group_refs(conn)

    assert _read_norm_ids(file_engine, rule_id) == [1]


def test_sets_null_when_all_ids_orphaned(file_engine):
    # Every referenced group is gone -> column should become NULL (empty list).
    rule_id = _seed(file_engine, valid_group_ids=[], rule_ids_json=json.dumps([42, 43]))

    with file_engine.begin() as conn:
        database._heal_orphaned_normalization_group_refs(conn)

    SessionLocal = sessionmaker(bind=file_engine)
    s = SessionLocal()
    try:
        rule = s.query(ChannelPipelineRule).get(rule_id)
        assert rule.normalization_group_ids is None
        assert rule.get_normalization_group_ids() == []
    finally:
        s.close()


def test_leaves_clean_rule_untouched_and_silent(file_engine, caplog):
    rule_id = _seed(file_engine, valid_group_ids=[1, 2], rule_ids_json=json.dumps([1, 2]))

    with caplog.at_level(logging.INFO, logger="database"):
        with file_engine.begin() as conn:
            database._heal_orphaned_normalization_group_refs(conn)

    assert _read_norm_ids(file_engine, rule_id) == [1, 2]
    # No heal log when nothing changed.
    assert not any("Healed" in r.message for r in caplog.records)


def test_is_idempotent(file_engine):
    rule_id = _seed(file_engine, valid_group_ids=[1], rule_ids_json=json.dumps([1, 99]))

    for _ in range(2):
        with file_engine.begin() as conn:
            database._heal_orphaned_normalization_group_refs(conn)

    assert _read_norm_ids(file_engine, rule_id) == [1]
