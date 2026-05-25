"""Tests for the M3U snapshot and change-log retention prune in ``CleanupTask``
(bd-wehek / bd-f9gd8 DBA spike).

``m3u_snapshots`` stores ~1-10 kB groups_data JSON per row; ``m3u_change_logs``
stores ~500 B per detected change.  Both grow with every Dispatcharr upstream
change (every 5-min poll if upstream churns).  Neither had retention.

This bundle adds two age-based prune blocks to step 8 of ``CleanupTask``,
BEFORE the VACUUM step:
1. Delete ``m3u_change_logs`` rows older than ``m3u_change_log_days`` (default
   90) by ``change_time``.
2. Delete ``m3u_snapshots`` rows older than ``m3u_snapshot_days`` (default 90)
   by ``snapshot_time``.

The change-log prune runs before the snapshot prune because the FK from
``m3u_change_logs.snapshot_id`` uses ON DELETE SET NULL — surviving change-log
rows are NOT cascade-deleted when a snapshot is deleted, so order doesn't
affect correctness, but pruning change-logs first keeps the audit trail cleaner
for operators who configure different windows.

Tests use real file-backed SQLite (matching the bd-ia28g / uc51o.3 pattern) so
the DELETEs observably hit actual SQLite, not mocks.  Settings knobs are
patched per-test via a SimpleNamespace stub returned from ``config.get_settings``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models import Base, M3UChangeLog, M3USnapshot
from tasks.cleanup import CleanupTask


def _make_engine(db_file: Path):
    """File-backed SQLite engine — matches the bd-ia28g / uc51o.3 pattern."""
    return create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )


def _make_session_and_task(engine, vacuum: bool = False):
    """Build a session + CleanupTask wired to run against the given engine."""
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    task = CleanupTask()
    task.vacuum_db = vacuum
    return session, task


def _settings(
    snapshot_days: int = 90,
    change_log_days: int = 90,
    snapshot_max: int = 50,
    auto_creation_snapshot_days: int = 30,
) -> SimpleNamespace:
    """Minimal settings stub carrying the M3U retention knobs plus the
    auto_creation_snapshot knobs needed by step 7."""
    return SimpleNamespace(
        m3u_snapshot_days=snapshot_days,
        m3u_change_log_days=change_log_days,
        auto_creation_snapshot_days=auto_creation_snapshot_days,
        auto_creation_snapshot_max=snapshot_max,
        unique_client_connection_days=90,
    )


def _add_snapshot(session, *, age_days: float, m3u_account_id: int = 1) -> int:
    """Insert an M3USnapshot aged ``age_days`` days; return its id."""
    ts = datetime.utcnow() - timedelta(days=age_days)
    snap = M3USnapshot(
        m3u_account_id=m3u_account_id,
        snapshot_time=ts,
        groups_data=json.dumps({"groups": [{"name": "G1", "stream_count": 5}]}),
        total_streams=5,
        created_at=ts,
    )
    session.add(snap)
    session.commit()
    return snap.id


def _add_change_log(
    session, *, age_days: float, m3u_account_id: int = 1, snapshot_id: int | None = None
) -> int:
    """Insert an M3UChangeLog aged ``age_days`` days; return its id."""
    ts = datetime.utcnow() - timedelta(days=age_days)
    log = M3UChangeLog(
        m3u_account_id=m3u_account_id,
        change_time=ts,
        change_type="streams_added",
        group_name="Sports",
        count=3,
        enabled=True,
        snapshot_id=snapshot_id,
    )
    session.add(log)
    session.commit()
    return log.id


def _count(engine, Model) -> int:
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    try:
        return s.query(Model).count()
    finally:
        s.close()


# ============================================================================
# M3USnapshot prune
# ============================================================================


class TestM3USnapshotPrune:
    """M3USnapshot rows older than ``m3u_snapshot_days`` are deleted."""

    @pytest.mark.asyncio
    async def test_deletes_old_snapshots_keeps_fresh(self, tmp_path):
        """A 100-day-old snapshot is deleted; a 5-day-old one survives.
        Default window is 90 days."""
        db_file = tmp_path / "snap.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        old_id = _add_snapshot(session, age_days=100)
        fresh_id = _add_snapshot(session, age_days=5)

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings(snapshot_days=90)):
            result = await task.execute()

        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            surviving_ids = {r.id for r in verify.query(M3USnapshot).all()}
        finally:
            verify.close()

        assert old_id not in surviving_ids, "snapshot past the age window must be deleted"
        assert fresh_id in surviving_ids, "fresh snapshot must survive"
        assert result.details["deleted"]["m3u_snapshots"] == 1

    @pytest.mark.asyncio
    async def test_respects_config_knob(self, tmp_path):
        """A shorter configured window prunes more aggressively."""
        db_file = tmp_path / "snap_cfg.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        s45 = _add_snapshot(session, age_days=45)
        s10 = _add_snapshot(session, age_days=10)

        # 30-day window: the 45-day snapshot is pruned, the 10-day kept.
        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings(snapshot_days=30)):
            result = await task.execute()

        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            surviving_ids = {r.id for r in verify.query(M3USnapshot).all()}
        finally:
            verify.close()

        assert s45 not in surviving_ids
        assert s10 in surviving_ids
        assert result.details["deleted"]["m3u_snapshots"] == 1

    @pytest.mark.asyncio
    async def test_idempotent_across_runs(self, tmp_path):
        """Second run is a no-op once eligible rows are gone."""
        db_file = tmp_path / "snap_idem.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        _add_snapshot(session, age_days=100)

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings()):
            first = await task.execute()
            second = await task.execute()

        assert first.details["deleted"]["m3u_snapshots"] == 1
        assert second.details["deleted"]["m3u_snapshots"] == 0

    @pytest.mark.asyncio
    async def test_no_rows_is_silent_noop(self, tmp_path):
        """Empty table produces zero deletions without error."""
        db_file = tmp_path / "snap_empty.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings()):
            result = await task.execute()

        assert result.details["deleted"]["m3u_snapshots"] == 0
        errors = result.details.get("errors", [])
        assert not any("M3U" in e for e in errors)


# ============================================================================
# M3UChangeLog prune
# ============================================================================


class TestM3UChangeLogPrune:
    """M3UChangeLog rows older than ``m3u_change_log_days`` are deleted."""

    @pytest.mark.asyncio
    async def test_deletes_old_change_logs_keeps_fresh(self, tmp_path):
        """A 100-day-old change log is deleted; a 5-day-old one survives."""
        db_file = tmp_path / "chlog.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        old_id = _add_change_log(session, age_days=100)
        fresh_id = _add_change_log(session, age_days=5)

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings(change_log_days=90)):
            result = await task.execute()

        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            surviving_ids = {r.id for r in verify.query(M3UChangeLog).all()}
        finally:
            verify.close()

        assert old_id not in surviving_ids, "change log past the age window must be deleted"
        assert fresh_id in surviving_ids, "fresh change log must survive"
        assert result.details["deleted"]["m3u_change_logs"] == 1

    @pytest.mark.asyncio
    async def test_respects_config_knob(self, tmp_path):
        """A shorter change-log window prunes more aggressively than the
        snapshot window when both are configured independently."""
        db_file = tmp_path / "chlog_cfg.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        old_id = _add_change_log(session, age_days=45)
        fresh_id = _add_change_log(session, age_days=10)

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings(change_log_days=30)):
            result = await task.execute()

        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            surviving_ids = {r.id for r in verify.query(M3UChangeLog).all()}
        finally:
            verify.close()

        assert old_id not in surviving_ids
        assert fresh_id in surviving_ids
        assert result.details["deleted"]["m3u_change_logs"] == 1

    @pytest.mark.asyncio
    async def test_change_log_with_fk_to_snapshot_set_null_on_snapshot_delete(
        self, tmp_path
    ):
        """When a snapshot is deleted the FK on its change-log rows becomes
        NULL (ON DELETE SET NULL) — surviving change-log rows are NOT deleted
        as a side-effect of the snapshot prune."""
        db_file = tmp_path / "chlog_fk.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        # Old snapshot (to be pruned) with a FRESH change-log pointing at it.
        old_snap_id = _add_snapshot(session, age_days=100)
        fresh_log_id = _add_change_log(
            session, age_days=5, snapshot_id=old_snap_id
        )

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings()):
            result = await task.execute()

        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            # Snapshot must be gone (older than 90d window).
            assert verify.query(M3USnapshot).count() == 0
            # Change-log row must SURVIVE (only 5 days old < 90d window).
            surviving_log = verify.query(M3UChangeLog).filter(
                M3UChangeLog.id == fresh_log_id
            ).one_or_none()
            assert surviving_log is not None, (
                "fresh change-log row must survive even when its FK snapshot "
                "was pruned (ON DELETE SET NULL)"
            )
            # FK should now be NULL (set by ON DELETE SET NULL cascade).
            assert surviving_log.snapshot_id is None
        finally:
            verify.close()

        assert result.details["deleted"]["m3u_snapshots"] == 1
        assert result.details["deleted"]["m3u_change_logs"] == 0

    @pytest.mark.asyncio
    async def test_idempotent_across_runs(self, tmp_path):
        """Second run is a no-op once eligible rows are gone."""
        db_file = tmp_path / "chlog_idem.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        _add_change_log(session, age_days=100)

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings()):
            first = await task.execute()
            second = await task.execute()

        assert first.details["deleted"]["m3u_change_logs"] == 1
        assert second.details["deleted"]["m3u_change_logs"] == 0


# ============================================================================
# Order: prune runs before VACUUM (step 8 < step 10)
# ============================================================================


class TestM3UPruneRunsBeforeVacuum:
    """The M3U prune step (step 8) must run BEFORE the VACUUM step (step 10)."""

    @pytest.mark.asyncio
    async def test_prune_step_precedes_vacuum_step(self, tmp_path):
        """Record progress step labels and assert the M3U prune fires before
        VACUUM.  Step 8 emits 'Pruning M3U snapshots'; step 10 emits
        'Vacuuming database'."""
        db_file = tmp_path / "order.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine, vacuum=True)

        _add_snapshot(session, age_days=100)

        progress_items = []
        orig = task._set_progress

        def _spy(*args, **kwargs):
            if "current_item" in kwargs:
                progress_items.append(kwargs["current_item"])
            return orig(*args, **kwargs)

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings()), \
             patch.object(task, "_set_progress", side_effect=_spy):
            await task.execute()

        m3u_item = next(
            (i for i in progress_items if "M3U" in i or "snapshot" in i.lower()), None
        )
        vacuum_item = next(
            (i for i in progress_items if "vacuum" in i.lower()), None
        )
        assert m3u_item is not None, f"no M3U-prune step in progress: {progress_items}"
        assert vacuum_item is not None, f"no vacuum step in progress: {progress_items}"
        assert progress_items.index(m3u_item) < progress_items.index(vacuum_item), (
            f"M3U prune must run BEFORE vacuum; order was {progress_items}"
        )


# ============================================================================
# Config defaults: getattr fallback when knobs absent from Settings stub
# ============================================================================


class TestM3UPruneConfigDefaults:
    """Falls back to the documented defaults (90d) when the knobs are absent."""

    @pytest.mark.asyncio
    async def test_defaults_when_knobs_absent_from_settings(self, tmp_path):
        """A settings object lacking ``m3u_snapshot_days`` / ``m3u_change_log_days``
        defaults via getattr() to 90 days each — a 100-day-old row is still pruned."""
        db_file = tmp_path / "defaults.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        old_snap = _add_snapshot(session, age_days=100)
        old_log = _add_change_log(session, age_days=100)

        # Empty-ish stub — the new steps use getattr(_, ..., 90) so absent knobs
        # are fine.  Include snapshot knobs so step 7 doesn't error.
        stub = SimpleNamespace(
            auto_creation_snapshot_days=30,
            auto_creation_snapshot_max=50,
            unique_client_connection_days=90,
        )
        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=stub):
            result = await task.execute()

        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            assert verify.query(M3USnapshot).count() == 0, "100d-old snapshot pruned at default 90d"
            assert verify.query(M3UChangeLog).count() == 0, "100d-old change-log pruned at default 90d"
        finally:
            verify.close()
