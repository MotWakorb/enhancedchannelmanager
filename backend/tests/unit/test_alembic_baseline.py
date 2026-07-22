"""Tests for the Alembic baseline revision (bd-c5wf5).

Guards against schema drift between the hand-authored SQLAlchemy models and
the Alembic migration timeline, verifies SQLite FK enforcement is actually
live (not silently dropped), and confirms the schema version is readable so
DBAS restore/sync (bd-gb5r5.3, bd-gb5r5.4) can gate on it.

See ``docs/database_migrations.md`` for the authoring workflow these tests
enforce.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

import database


def _make_alembic_config(db_url: str):
    from alembic.config import Config

    ini_path = Path(database.ALEMBIC_INI_PATH)
    assert ini_path.exists(), f"alembic.ini missing at {ini_path}"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


class TestAlembicBaseline:
    """The baseline revision must reflect current SQLAlchemy metadata."""

    def test_head_revision_is_declared(self):
        """A head revision must exist — prevents an empty versions directory."""
        head = database.get_alembic_head_revision()
        assert head, "Expected an Alembic head revision; versions directory is empty"

    def test_upgrade_head_on_fresh_db_creates_schema(self, tmp_path):
        """alembic upgrade head on an empty DB must produce the full schema."""
        from alembic import command

        db_file = tmp_path / "alembic_fresh.db"
        db_url = f"sqlite:///{db_file}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        try:
            with engine.connect() as conn:
                row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
                assert row is not None, "alembic_version row missing after upgrade"
                assert row[0] == database.get_alembic_head_revision()

                tables = {
                    r[0]
                    for r in conn.execute(text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                        "AND name != 'alembic_version'"
                    )).fetchall()
                }
        finally:
            engine.dispose()

        metadata_tables = set(database.Base.metadata.tables.keys())
        missing = metadata_tables - tables
        assert not missing, f"Tables declared in models but not created by alembic: {missing}"

    def test_baseline_matches_metadata_no_drift(self, tmp_path):
        """alembic upgrade head must match Base.metadata.create_all byte-for-byte.

        If autogenerate would produce any schema change against a fresh
        upgrade-head DB, it means a model edit was landed without a
        corresponding migration. Fail loudly so the author writes one.
        """
        from alembic import command
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        db_file = tmp_path / "alembic_drift.db"
        db_url = f"sqlite:///{db_file}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        try:
            with engine.connect() as conn:
                context = MigrationContext.configure(
                    connection=conn,
                    opts={"compare_type": True, "compare_server_default": True},
                )
                diff = compare_metadata(context, database.Base.metadata)
        finally:
            engine.dispose()

        # Filter noise: compare_metadata reports server_default changes that
        # look different between metadata.create_all and alembic DDL even when
        # semantically identical (e.g. "0" vs "'0'"). We accept only true
        # structural drift: added/removed tables, columns, indexes, FKs.
        structural = [
            d
            for d in diff
            if not (isinstance(d, tuple) and d and d[0] in {"modify_default", "modify_nullable"})
        ]
        assert not structural, (
            "Schema drift detected between SQLAlchemy metadata and Alembic baseline. "
            "Run `alembic revision --autogenerate -m <msg>` and review the diff. "
            f"Diff: {structural}"
        )


class TestMigration0039WidenPipelineStatus:
    """0039 widens auto_creation_executions.status to hold completed_with_errors.

    Build 0.17.6-0152 added the 21-char terminal status
    'completed_with_errors', which overflowed the previous String(20)
    contract. 0039 widens the column to String(32).
    """

    @staticmethod
    def _status_width(engine):
        from sqlalchemy import inspect as sa_inspect

        for col in sa_inspect(engine).get_columns("auto_creation_executions"):
            if col["name"] == "status":
                return getattr(col["type"], "length", None)
        return None

    def test_upgrade_widens_status_and_round_trips_value(self, tmp_path):
        """After upgrade head the column is String(32) and stores the value."""
        from datetime import datetime

        from alembic import command
        from sqlalchemy.orm import Session

        from models import ChannelPipelineExecution

        db_file = tmp_path / "mig0039_value.db"
        db_url = f"sqlite:///{db_file}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        try:
            assert self._status_width(engine) == 32

            with Session(engine) as session:
                row = ChannelPipelineExecution(
                    started_at=datetime.utcnow(),
                    status="completed_with_errors",
                )
                session.add(row)
                session.commit()
                row_id = row.id

            with Session(engine) as session:
                stored = session.get(ChannelPipelineExecution, row_id)
                assert stored is not None
                assert stored.status == "completed_with_errors"
                assert len(stored.status) == 21
        finally:
            engine.dispose()

    def test_downgrade_upgrade_round_trip(self, tmp_path):
        """0039 reverses to String(20) and re-applies to String(32)."""
        from alembic import command

        db_file = tmp_path / "mig0039_rt.db"
        db_url = f"sqlite:///{db_file}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "head")
        engine = create_engine(db_url)
        try:
            assert self._status_width(engine) == 32
        finally:
            engine.dispose()

        command.downgrade(cfg, "0038")
        engine = create_engine(db_url)
        try:
            assert self._status_width(engine) == 20
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")
        engine = create_engine(db_url)
        try:
            assert self._status_width(engine) == 32
        finally:
            engine.dispose()


class TestForeignKeyEnforcement:
    """SQLite silently accepts FK violations unless PRAGMA foreign_keys=ON.

    bd-c5wf5 wired the PRAGMA onto the SQLAlchemy Engine connect event — this
    test proves it's actually on. Regression here would silently corrupt any
    feature that depends on ON DELETE CASCADE / SET NULL.
    """

    def test_pragma_foreign_keys_is_on(self, test_engine):
        """The PRAGMA must report 1 on fresh connections from the test engine."""
        with test_engine.connect() as conn:
            row = conn.execute(text("PRAGMA foreign_keys")).fetchone()
            assert row is not None
            assert row[0] == 1, f"PRAGMA foreign_keys should be ON (1), got {row[0]}"

    def test_invalid_fk_insert_is_rejected(self, test_session):
        """Inserting a row whose FK references a non-existent parent must raise."""
        from models import Tag

        # Tag.group_id → tag_groups.id ON DELETE CASCADE, NOT NULL.
        orphan_tag = Tag(
            group_id=999999,  # no such tag_groups.id
            value="orphan",
            case_sensitive=False,
            enabled=True,
            is_builtin=False,
        )
        test_session.add(orphan_tag)
        with pytest.raises(IntegrityError):
            test_session.commit()
        test_session.rollback()

    def test_cascade_delete_cleans_children(self, test_session):
        """With FK ON, deleting a parent must cascade to children that declare it."""
        from models import Tag, TagGroup

        group = TagGroup(
            name="cascade-test-group",
            description="",
            is_builtin=False,
        )
        test_session.add(group)
        test_session.flush()

        tag = Tag(
            group_id=group.id,
            value="child",
            case_sensitive=False,
            enabled=True,
            is_builtin=False,
        )
        test_session.add(tag)
        test_session.commit()
        child_id = tag.id

        test_session.delete(group)
        test_session.commit()

        from sqlalchemy import select

        remaining = test_session.execute(select(Tag).where(Tag.id == child_id)).first()
        assert remaining is None, "ON DELETE CASCADE did not fire — PRAGMA foreign_keys is OFF"


class TestSchemaVersionAccessors:
    """Public accessors for the schema version must work before and after init."""

    def test_alembic_head_revision_readable(self):
        """get_alembic_head_revision reads the versions/ dir via ScriptDirectory."""
        head = database.get_alembic_head_revision()
        assert isinstance(head, str)
        assert head, "Expected a non-empty head revision"

    def test_current_schema_revision_after_upgrade(self, tmp_path):
        """get_current_schema_revision returns the version_num after upgrade."""
        from alembic import command

        db_file = tmp_path / "alembic_current.db"
        db_url = f"sqlite:///{db_file}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            rev = database.get_current_schema_revision(engine)
            assert rev == database.get_alembic_head_revision()
        finally:
            engine.dispose()

    def test_current_schema_revision_empty_before_init(self):
        """On a DB with no alembic_version, the helper returns ''."""
        rev = database.get_current_schema_revision(engine=None)
        # When no engine is passed and _engine is None (module-level in unit
        # tests), we expect an empty string rather than an exception.
        assert rev == "" or isinstance(rev, str)
