"""Tests for the bd-ia28g retention blocks in ``CleanupTask`` (bd-p5b8i DBA
re-attribution).

The bd-f9gd8 spike's session_telemetry retention story was the wrong target
— the bd-p5b8i DBA re-attribution found that on a 165MB operator DB:

- ``auto_creation_executions`` BLOB columns are 127MB (77%).
- ``health_checks`` is 20MB / 53k rows (14%).
- ``notifications`` is 2.2MB.
- ``session_telemetry`` is already covered by ``StatsV2RollupTask`` (bd-7i2vv).

This bundle adds three new prune blocks to ``CleanupTask`` against the actual
large tables:

1. **auto_creation_executions BLOB null-out** — KEEP the summary row
   (id, status, started_at, counts, etc.) for audit history; only NULL out
   the four large JSON BLOB columns: ``execution_log``, ``dry_run_results``,
   ``created_entities``, ``modified_entities``. New config key:
   ``auto_creation_blob_days`` (default 30).
2. **health_checks DELETE** — the health_checks table is NOT in models.py
   (legacy from the v0.11.6 Background Health Check Service spec, table
   persists on long-running installs but service was removed). Uses raw SQL
   with an ``inspect()``-based existence check so fresh installs are silent
   no-ops. New config key: ``health_checks_days`` (default 7).
3. **notifications DELETE** — uses ``expires_at`` if set, otherwise
   ``created_at`` + ``notifications_days``. New config key:
   ``notifications_days`` (default 30).

Tests use real file-backed SQLite (matching the bd-ej995 pattern) so the
DELETEs / UPDATEs are observably hitting actual SQLite, not mocks.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models import AutoCreationExecution, Base, Notification
from tasks.cleanup import CleanupTask


def _make_engine(db_file: Path):
    """File-backed SQLite engine — same pattern as bd-ej995 tests."""
    return create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )


def _make_session_and_task(engine, vacuum: bool = False) -> tuple:
    """Build a session + CleanupTask wired to run against the given engine.

    VACUUM is disabled by default in these targeted retention tests — the
    bd-ygoqr VACUUM contract has its own dedicated test in
    ``test_cleanup_task_default_schedule.py::TestCleanupTaskVacuumExecutesAgainstRealEngine``
    that explicitly exercises VACUUM (with a regression sentinel preventing
    accidental disable). These tests are about the prune logic, not VACUUM,
    so disabling it keeps the assertions tight on the rowcounts we care about.
    """
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    task = CleanupTask()
    task.vacuum_db = vacuum
    return session, task


# ============================================================================
# Config schema: get_config / update_config round-trips
# ============================================================================


class TestCleanupTaskConfigSchema:
    """The three new config keys must round-trip via get_config / update_config.

    The Settings UI in TaskEditorModal.tsx reads/writes these keys; if the
    backend silently drops them on update_config the UI changes won't stick.
    """

    def test_get_config_exposes_three_new_retention_keys(self):
        task = CleanupTask()
        config = task.get_config()

        assert config["auto_creation_blob_days"] == 30
        assert config["health_checks_days"] == 7
        assert config["notifications_days"] == 30

    def test_update_config_persists_three_new_retention_keys(self):
        task = CleanupTask()
        task.update_config({
            "auto_creation_blob_days": 14,
            "health_checks_days": 3,
            "notifications_days": 60,
        })

        assert task.auto_creation_blob_days == 14
        assert task.health_checks_days == 3
        assert task.notifications_days == 60

    def test_update_config_with_only_one_new_key_leaves_others_unchanged(self):
        task = CleanupTask()
        task.update_config({"auto_creation_blob_days": 14})

        assert task.auto_creation_blob_days == 14
        # The other two new keys keep their defaults — update_config must
        # not zero out absent keys.
        assert task.health_checks_days == 7
        assert task.notifications_days == 30


# ============================================================================
# Block 1: auto_creation_executions BLOB null-out (77% of operator DB)
# ============================================================================


class TestAutoCreationBlobPrune:
    """The most impactful prune: NULL out BLOB columns on old rows, KEEP
    the summary row for audit history."""

    @pytest.mark.asyncio
    async def test_nulls_out_blobs_on_rows_older_than_cutoff(self, tmp_path):
        """A row started 31 days ago has all four BLOB columns NULLed; the
        summary fields (id, status, started_at, counts) stay intact."""
        db_file = tmp_path / "blob_prune.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        old = datetime.utcnow() - timedelta(days=31)
        row = AutoCreationExecution(
            rule_id=None,
            rule_name="old-rule",
            mode="execute",
            triggered_by="manual",
            started_at=old,
            completed_at=old + timedelta(seconds=2),
            duration_seconds=2.0,
            status="completed",
            streams_evaluated=100,
            streams_matched=50,
            channels_created=10,
            execution_log=json.dumps([{"stream": "s1", "matched": True}] * 200),
            dry_run_results=json.dumps([{"action": "create"}] * 50),
            created_entities=json.dumps([{"type": "channel", "id": 1}] * 10),
            modified_entities=json.dumps([{"type": "channel", "id": 2}] * 5),
        )
        session.add(row)
        session.commit()
        row_id = row.id

        with patch("tasks.cleanup.get_session", return_value=session):
            result = await task.execute()

        # Reload the row from a fresh session to defeat any ORM caching.
        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            persisted = verify.get(AutoCreationExecution, row_id)
            assert persisted is not None, "summary row must NOT have been deleted"
            # Summary fields preserved for audit history.
            assert persisted.rule_name == "old-rule"
            assert persisted.status == "completed"
            assert persisted.streams_evaluated == 100
            assert persisted.channels_created == 10
            # BLOB columns NULLed out.
            assert persisted.execution_log is None
            assert persisted.dry_run_results is None
            assert persisted.created_entities is None
            assert persisted.modified_entities is None
        finally:
            verify.close()

        # The result detail dict surfaces the rowcount under the documented key.
        deleted = result.details.get("deleted", {})
        assert deleted.get("auto_creation_blobs_pruned") == 1

    @pytest.mark.asyncio
    async def test_does_not_touch_rows_newer_than_cutoff(self, tmp_path):
        """A row started 5 days ago retains its BLOBs untouched."""
        db_file = tmp_path / "blob_keep_fresh.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        recent = datetime.utcnow() - timedelta(days=5)
        log_payload = json.dumps([{"stream": "s1"}] * 100)
        row = AutoCreationExecution(
            rule_name="recent-rule",
            mode="execute",
            triggered_by="manual",
            started_at=recent,
            status="completed",
            execution_log=log_payload,
            dry_run_results=json.dumps([{"action": "create"}]),
            created_entities=json.dumps([{"type": "channel", "id": 1}]),
            modified_entities=json.dumps([{"type": "channel", "id": 2}]),
        )
        session.add(row)
        session.commit()
        row_id = row.id

        with patch("tasks.cleanup.get_session", return_value=session):
            result = await task.execute()

        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            persisted = verify.get(AutoCreationExecution, row_id)
            assert persisted.execution_log == log_payload
            assert persisted.dry_run_results is not None
            assert persisted.created_entities is not None
            assert persisted.modified_entities is not None
        finally:
            verify.close()

        deleted = result.details.get("deleted", {})
        assert deleted.get("auto_creation_blobs_pruned") == 0

    @pytest.mark.asyncio
    async def test_idempotent_across_runs(self, tmp_path):
        """Second run on the same data is a strict no-op (rowcount == 0).

        The ``execution_log.isnot(None)`` filter is the idempotency gate:
        once BLOBs are NULL on a row, the next prune pass skips it
        entirely. Without this gate, the rowcount would be 1 on every
        cron run forever and the operator log would lie about "pruned 1
        row" every Sunday.
        """
        db_file = tmp_path / "blob_idempotent.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        old = datetime.utcnow() - timedelta(days=31)
        session.add(AutoCreationExecution(
            rule_name="r", mode="execute", triggered_by="manual",
            started_at=old, status="completed",
            execution_log=json.dumps([{"x": 1}]),
            dry_run_results=json.dumps([{"x": 1}]),
            created_entities=json.dumps([{"x": 1}]),
            modified_entities=json.dumps([{"x": 1}]),
        ))
        session.commit()

        with patch("tasks.cleanup.get_session", return_value=session):
            first = await task.execute()
            second = await task.execute()

        assert first.details["deleted"]["auto_creation_blobs_pruned"] == 1
        assert second.details["deleted"]["auto_creation_blobs_pruned"] == 0


# ============================================================================
# Block 2: health_checks DELETE (legacy table, may or may not exist)
# ============================================================================


def _create_health_checks_table(engine, ts_column: str = "created_at") -> None:
    """Materialise a minimal health_checks table — legacy schema is not in
    models.py, so each test that exercises this prune block creates a
    realistic shape with a chosen timestamp column name (the prune block
    auto-detects created_at / checked_at / timestamp)."""
    with engine.begin() as conn:
        conn.execute(text(
            f"CREATE TABLE health_checks ("
            f"id INTEGER PRIMARY KEY AUTOINCREMENT, "
            f"service VARCHAR(100), "
            f"status VARCHAR(50), "
            f"{ts_column} DATETIME"
            f")"
        ))


class TestHealthChecksPrune:

    @pytest.mark.asyncio
    async def test_deletes_rows_older_than_cutoff(self, tmp_path):
        """A 14-day-old row is deleted; a 3-day-old row survives. Default
        is 7 days, so 14 > 7 (delete) and 3 < 7 (keep)."""
        db_file = tmp_path / "health.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        _create_health_checks_table(engine, ts_column="created_at")

        old_ts = datetime.utcnow() - timedelta(days=14)
        new_ts = datetime.utcnow() - timedelta(days=3)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO health_checks (service, status, created_at) "
                "VALUES (:s1, :st, :t1), (:s2, :st, :t2)"
            ), {"s1": "old-svc", "s2": "new-svc", "st": "ok", "t1": old_ts, "t2": new_ts})

        session, task = _make_session_and_task(engine)
        with patch("tasks.cleanup.get_session", return_value=session):
            result = await task.execute()

        with engine.begin() as conn:
            remaining = conn.execute(text(
                "SELECT service FROM health_checks ORDER BY service"
            )).fetchall()
        assert [r[0] for r in remaining] == ["new-svc"]
        assert result.details["deleted"]["health_checks"] == 1

    @pytest.mark.asyncio
    async def test_silent_noop_when_table_absent(self, tmp_path):
        """Fresh installs (which never had the legacy health_checks table)
        must NOT error — the prune is a silent no-op recording 0 deletions."""
        db_file = tmp_path / "fresh.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        # Deliberately do NOT create the health_checks table.

        session, task = _make_session_and_task(engine)
        with patch("tasks.cleanup.get_session", return_value=session):
            result = await task.execute()

        deleted = result.details["deleted"]
        errors = result.details.get("errors", [])
        assert deleted["health_checks"] == 0
        # No error should be raised for the legitimate absent-table case.
        assert not any("Health" in e for e in errors)

    @pytest.mark.asyncio
    async def test_handles_alternate_timestamp_column_name(self, tmp_path):
        """Some legacy installs may have ``checked_at`` instead of
        ``created_at`` — the prune auto-detects which timestamp column
        is present and uses it. Tests the second fallback in the
        ('created_at', 'checked_at', 'timestamp') chain."""
        db_file = tmp_path / "health_alt.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        _create_health_checks_table(engine, ts_column="checked_at")

        old_ts = datetime.utcnow() - timedelta(days=14)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO health_checks (service, status, checked_at) "
                "VALUES (:s, :st, :t)"
            ), {"s": "old", "st": "ok", "t": old_ts})

        session, task = _make_session_and_task(engine)
        with patch("tasks.cleanup.get_session", return_value=session):
            result = await task.execute()

        with engine.begin() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM health_checks")).scalar()
        assert count == 0
        assert result.details["deleted"]["health_checks"] == 1

    @pytest.mark.asyncio
    async def test_idempotent_across_runs(self, tmp_path):
        """Second run after a successful first run prunes 0 rows."""
        db_file = tmp_path / "health_idem.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        _create_health_checks_table(engine)

        old_ts = datetime.utcnow() - timedelta(days=14)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO health_checks (service, status, created_at) "
                "VALUES (:s, :st, :t)"
            ), {"s": "old", "st": "ok", "t": old_ts})

        session, task = _make_session_and_task(engine)
        with patch("tasks.cleanup.get_session", return_value=session):
            first = await task.execute()
            second = await task.execute()

        assert first.details["deleted"]["health_checks"] == 1
        assert second.details["deleted"]["health_checks"] == 0


# ============================================================================
# Block 3: notifications DELETE — expires_at OR created_at fallback
# ============================================================================


class TestNotificationsPrune:

    @pytest.mark.asyncio
    async def test_deletes_old_notification_by_created_at(self, tmp_path):
        """No ``expires_at`` set → falls back to ``created_at`` + default
        30 days. A 31-day-old row is deleted."""
        db_file = tmp_path / "notif_age.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        old = datetime.utcnow() - timedelta(days=31)
        recent = datetime.utcnow() - timedelta(days=5)
        session.add_all([
            Notification(message="old", created_at=old),
            Notification(message="recent", created_at=recent),
        ])
        session.commit()

        with patch("tasks.cleanup.get_session", return_value=session):
            result = await task.execute()

        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            survivors = [n.message for n in verify.query(Notification).all()]
        finally:
            verify.close()
        assert survivors == ["recent"]
        assert result.details["deleted"]["notifications"] == 1

    @pytest.mark.asyncio
    async def test_deletes_when_expires_at_is_in_the_past(self, tmp_path):
        """A 5-day-old notification with an ``expires_at`` 1 hour in the
        past is deleted — TTL wins over age-based fallback. Without the
        expires_at branch this row would survive (5 days < 30 day default)."""
        db_file = tmp_path / "notif_expired.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        recent_create = datetime.utcnow() - timedelta(days=5)
        already_expired = datetime.utcnow() - timedelta(hours=1)
        session.add(Notification(
            message="ttl-expired",
            created_at=recent_create,
            expires_at=already_expired,
        ))
        session.commit()

        with patch("tasks.cleanup.get_session", return_value=session):
            result = await task.execute()

        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            survivors = [n.message for n in verify.query(Notification).all()]
        finally:
            verify.close()
        assert survivors == []
        assert result.details["deleted"]["notifications"] == 1

    @pytest.mark.asyncio
    async def test_preserves_old_notification_with_future_expires_at(self, tmp_path):
        """The bd-ia28g design point that motivated the ``expires_at``
        branch: an operator may set a deliberately long TTL ("keep this
        for 90 days"). Even though created_at is 40 days ago (> 30d
        default), the future expires_at must protect the row."""
        db_file = tmp_path / "notif_future_ttl.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        old_create = datetime.utcnow() - timedelta(days=40)
        future_expiry = datetime.utcnow() + timedelta(days=50)
        session.add(Notification(
            message="long-ttl",
            created_at=old_create,
            expires_at=future_expiry,
        ))
        session.commit()

        with patch("tasks.cleanup.get_session", return_value=session):
            result = await task.execute()

        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            survivors = [n.message for n in verify.query(Notification).all()]
        finally:
            verify.close()
        assert survivors == ["long-ttl"], (
            "future expires_at must protect the row even when created_at "
            "is older than the notifications_days cutoff"
        )
        assert result.details["deleted"]["notifications"] == 0

    @pytest.mark.asyncio
    async def test_idempotent_across_runs(self, tmp_path):
        """Second run prunes 0 rows once the eligible ones are gone."""
        db_file = tmp_path / "notif_idem.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        old = datetime.utcnow() - timedelta(days=31)
        session.add(Notification(message="old", created_at=old))
        session.commit()

        with patch("tasks.cleanup.get_session", return_value=session):
            first = await task.execute()
            second = await task.execute()

        assert first.details["deleted"]["notifications"] == 1
        assert second.details["deleted"]["notifications"] == 0


# ============================================================================
# Combined: full execute with all three prune blocks active
# ============================================================================


class TestCleanupTaskFullExecuteWithAllBlocks:
    """End-to-end: a single execute() call must run all three new prune
    blocks AND the original three (probe / task_executions / journal)
    without any prune block leaking errors into another's session."""

    @pytest.mark.asyncio
    async def test_all_seven_steps_run_and_total_steps_is_seven(self, tmp_path):
        db_file = tmp_path / "full.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        _create_health_checks_table(engine)

        # Seed one eligible row per prune-by-age block (new ones only —
        # the existing probe/task_executions/journal blocks have their own
        # test coverage, no need to bundle here per the bead spec).
        old = datetime.utcnow() - timedelta(days=31)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO health_checks (service, status, created_at) "
                "VALUES (:s, :st, :t)"
            ), {"s": "old", "st": "ok", "t": datetime.utcnow() - timedelta(days=14)})

        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()
        session.add(AutoCreationExecution(
            rule_name="r", mode="execute", triggered_by="manual",
            started_at=old, status="completed",
            execution_log=json.dumps([{"x": 1}]),
            dry_run_results=json.dumps([{"x": 1}]),
            created_entities=json.dumps([{"x": 1}]),
            modified_entities=json.dumps([{"x": 1}]),
        ))
        session.add(Notification(message="old", created_at=old))
        session.commit()

        task = CleanupTask()
        task.vacuum_db = False  # See _make_session_and_task docstring.
        with patch("tasks.cleanup.get_session", return_value=session):
            result = await task.execute()

        assert result.success is True
        assert result.total_items == 7, (
            "execute() must report 7 cleanup steps now that bd-ia28g added "
            "three new prune blocks (was 4: probe + tasks + journal + vacuum)"
        )
        deleted = result.details["deleted"]
        assert deleted["auto_creation_blobs_pruned"] == 1
        assert deleted["health_checks"] == 1
        assert deleted["notifications"] == 1
