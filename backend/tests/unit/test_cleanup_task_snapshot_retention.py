"""Tests for the AutoCreationSnapshot retention prune in ``CleanupTask``
(ADR-010 §D7 / bead ``enhancedchannelmanager-uc51o.3``).

A per-run snapshot of ~570 channels serializes to roughly 50 KB–3 MB and is
captured on every execute (incl. hourly run_on_refresh) with NO pruning today
— an unbounded SQLite-growth bomb. This adds a prune step to ``CleanupTask``,
BEFORE the VACUUM step, bounded by TWO knobs read from the global Settings
(config.py), whichever fires first:

* ``auto_creation_snapshot_days`` (default 30) — age window by ``snapshot_time``.
* ``auto_creation_snapshot_max`` (default 50) — count cap; keep newest N.

Tests use real file-backed SQLite (matching the bd-ia28g pattern) so the
DELETEs observably hit actual SQLite, not mocks. The settings knobs are patched
per-test via a stub returned from ``config.get_settings``.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models import AutoCreationExecution, AutoCreationSnapshot, Base
from tasks.cleanup import CleanupTask


def _make_engine(db_file: Path):
    return create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )


def _make_session_and_task(engine, vacuum: bool = False):
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    task = CleanupTask()
    task.vacuum_db = vacuum
    return session, task


def _settings(days: int = 30, max_count: int = 50):
    """A minimal settings stub carrying just the two retention knobs."""
    return SimpleNamespace(
        auto_creation_snapshot_days=days,
        auto_creation_snapshot_max=max_count,
    )


def _make_snapshot(session, *, age_days: float, channels=None) -> int:
    """Create an execution + snapshot aged ``age_days`` days; return snapshot id."""
    ts = datetime.utcnow() - timedelta(days=age_days)
    execution = AutoCreationExecution(
        mode="execute",
        triggered_by="manual",
        started_at=ts,
        status="completed",
    )
    session.add(execution)
    session.commit()
    snapshot = AutoCreationSnapshot(
        execution_id=execution.id,
        snapshot_time=ts,
        channel_count=len(channels or []),
    )
    snapshot.set_channels_data({"channels": channels or []})
    session.add(snapshot)
    session.commit()
    return snapshot.id


def _count_snapshots(engine) -> int:
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    try:
        return s.query(AutoCreationSnapshot).count()
    finally:
        s.close()


def _surviving_ids(engine) -> set:
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    try:
        return {row.id for row in s.query(AutoCreationSnapshot.id).all()}
    finally:
        s.close()


class TestSnapshotAgeWindowPrune:
    """Snapshots older than auto_creation_snapshot_days are pruned."""

    @pytest.mark.asyncio
    async def test_prunes_snapshots_older_than_age_window(self, tmp_path):
        db_file = tmp_path / "snap_age.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        old_id = _make_snapshot(session, age_days=40)   # past the 30d window
        fresh_id = _make_snapshot(session, age_days=5)  # inside the window

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings(days=30, max_count=50)):
            result = await task.execute()

        survivors = _surviving_ids(engine)
        assert old_id not in survivors, "snapshot past the age window must be pruned"
        assert fresh_id in survivors, "snapshot inside the window must be kept"

        deleted = result.details.get("deleted", {})
        assert deleted.get("auto_creation_snapshots") == 1

    @pytest.mark.asyncio
    async def test_age_window_respects_config_knob(self, tmp_path):
        """A shorter configured window prunes more aggressively."""
        db_file = tmp_path / "snap_age_cfg.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        s10 = _make_snapshot(session, age_days=10)
        s3 = _make_snapshot(session, age_days=3)

        # With a 7-day window, the 10-day snapshot is pruned, the 3-day kept.
        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings(days=7, max_count=50)):
            await task.execute()

        survivors = _surviving_ids(engine)
        assert s10 not in survivors
        assert s3 in survivors


class TestSnapshotCountCapPrune:
    """At most auto_creation_snapshot_max snapshots are kept (newest N)."""

    @pytest.mark.asyncio
    async def test_keeps_only_newest_n(self, tmp_path):
        db_file = tmp_path / "snap_cap.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        # 5 snapshots, all inside the age window, ages 1..5 days.
        ids_by_age = {}
        for age in range(1, 6):
            ids_by_age[age] = _make_snapshot(session, age_days=age)

        # Cap of 3 → keep the 3 NEWEST (ages 1,2,3), prune the 2 oldest (4,5).
        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings(days=30, max_count=3)):
            result = await task.execute()

        survivors = _surviving_ids(engine)
        assert _count_snapshots(engine) == 3
        assert ids_by_age[1] in survivors
        assert ids_by_age[2] in survivors
        assert ids_by_age[3] in survivors
        assert ids_by_age[4] not in survivors
        assert ids_by_age[5] not in survivors

        deleted = result.details.get("deleted", {})
        assert deleted.get("auto_creation_snapshots") == 2

    @pytest.mark.asyncio
    async def test_cap_not_exceeded_means_no_prune(self, tmp_path):
        """When count <= cap and all are fresh, nothing is pruned."""
        db_file = tmp_path / "snap_cap_under.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        for age in range(1, 4):  # 3 fresh snapshots
            _make_snapshot(session, age_days=age)

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings(days=30, max_count=50)):
            result = await task.execute()

        assert _count_snapshots(engine) == 3
        deleted = result.details.get("deleted", {})
        assert deleted.get("auto_creation_snapshots") == 0


class TestSnapshotPruneRunsBeforeVacuum:
    """The snapshot prune step must run BEFORE the VACUUM step (ADR-010 §D7)."""

    @pytest.mark.asyncio
    async def test_prune_step_precedes_vacuum_step(self, tmp_path):
        """Record the order of the snapshot-prune and VACUUM operations and
        assert the prune fires first. We spy on the progress steps: step 7 is
        the snapshot prune, step 8 is VACUUM."""
        db_file = tmp_path / "snap_order.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine, vacuum=True)

        _make_snapshot(session, age_days=40)  # one prunable snapshot

        progress_items = []
        orig_set_progress = task._set_progress

        def _spy(*args, **kwargs):
            if "current_item" in kwargs:
                progress_items.append(kwargs["current_item"])
            return orig_set_progress(*args, **kwargs)

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings(days=30, max_count=50)), \
             patch.object(task, "_set_progress", side_effect=_spy):
            await task.execute()

        # Both steps ran, and the snapshot prune appears before the vacuum.
        snap_item = next((i for i in progress_items if "snapshot" in i.lower()), None)
        vacuum_item = next((i for i in progress_items if "vacuum" in i.lower()), None)
        assert snap_item is not None, f"no snapshot-prune step seen: {progress_items}"
        assert vacuum_item is not None, f"no vacuum step seen: {progress_items}"
        assert progress_items.index(snap_item) < progress_items.index(vacuum_item), (
            f"snapshot prune must run BEFORE vacuum; order was {progress_items}"
        )


class TestSnapshotPruneConfigDefaults:
    """Falls back to the documented defaults when the knobs are absent."""

    @pytest.mark.asyncio
    async def test_defaults_when_settings_missing_knobs(self, tmp_path):
        """A settings object lacking the knobs → 30d / 50 defaults via getattr."""
        db_file = tmp_path / "snap_defaults.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        old_id = _make_snapshot(session, age_days=40)
        fresh_id = _make_snapshot(session, age_days=5)

        # Empty settings stub — getattr() must supply 30d / 50.
        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=SimpleNamespace()):
            await task.execute()

        survivors = _surviving_ids(engine)
        assert old_id not in survivors   # 40 > default 30d
        assert fresh_id in survivors
