"""Tests for the UniqueClientConnection retention prune in ``CleanupTask``
(bd-1wi3y / bd-f9gd8 DBA spike).

``unique_client_connections`` has a high write rate (one row per (channel, IP)
connection start) plus 6 indexes (~500 B effective per row on disk).  Currently
no retention beyond manual stats reset.

This adds an age-based prune block to step 9 of ``CleanupTask``, BEFORE the
VACUUM step, mirroring the established age-window pattern.  Knob from global
Settings (config.py): ``unique_client_connection_days`` (default 90), pruned
by ``connected_at``.

Tests use real file-backed SQLite (matching the bd-ia28g / uc51o.3 pattern) so
the DELETEs observably hit actual SQLite, not mocks.  The settings knob is
patched per-test via a SimpleNamespace stub returned from ``config.get_settings``.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models import Base, UniqueClientConnection
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


def _settings(ucc_days: int = 90) -> SimpleNamespace:
    """Minimal settings stub with the UCC knob plus the knobs needed by
    steps 7 and 8 so those prune blocks don't error."""
    return SimpleNamespace(
        unique_client_connection_days=ucc_days,
        auto_creation_snapshot_days=30,
        auto_creation_snapshot_max=50,
        m3u_snapshot_days=90,
        m3u_change_log_days=90,
    )


def _add_ucc(
    session,
    *,
    age_days: float,
    ip: str = "192.168.1.1",
    channel_id: str = "ch-001",
    channel_name: str = "Test Channel",
) -> int:
    """Insert a UniqueClientConnection aged ``age_days`` days; return its id."""
    ts = datetime.utcnow() - timedelta(days=age_days)
    row = UniqueClientConnection(
        ip_address=ip,
        channel_id=channel_id,
        channel_name=channel_name,
        date=date.today() - timedelta(days=int(age_days)),
        connected_at=ts,
        watch_seconds=60,
        created_at=ts,
    )
    session.add(row)
    session.commit()
    return row.id


# ============================================================================
# Age-window prune
# ============================================================================


class TestUCCAgeWindowPrune:
    """Rows older than ``unique_client_connection_days`` are deleted by
    ``connected_at``."""

    @pytest.mark.asyncio
    async def test_deletes_old_rows_keeps_fresh(self, tmp_path):
        """A 100-day-old connection is deleted; a 5-day-old one survives.
        Default window is 90 days."""
        db_file = tmp_path / "ucc_age.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        old_id = _add_ucc(session, age_days=100)
        fresh_id = _add_ucc(session, age_days=5)

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings(ucc_days=90)):
            result = await task.execute()

        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            surviving_ids = {r.id for r in verify.query(UniqueClientConnection).all()}
        finally:
            verify.close()

        assert old_id not in surviving_ids, "connection past the age window must be deleted"
        assert fresh_id in surviving_ids, "fresh connection must survive"
        assert result.details["deleted"]["unique_client_connections"] == 1

    @pytest.mark.asyncio
    async def test_respects_config_knob(self, tmp_path):
        """A shorter configured window prunes more aggressively."""
        db_file = tmp_path / "ucc_cfg.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        s45 = _add_ucc(session, age_days=45, ip="192.168.1.2")
        s10 = _add_ucc(session, age_days=10, ip="192.168.1.3")

        # 30-day window: 45-day row is pruned, 10-day kept.
        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings(ucc_days=30)):
            result = await task.execute()

        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            surviving_ids = {r.id for r in verify.query(UniqueClientConnection).all()}
        finally:
            verify.close()

        assert s45 not in surviving_ids
        assert s10 in surviving_ids
        assert result.details["deleted"]["unique_client_connections"] == 1

    @pytest.mark.asyncio
    async def test_idempotent_across_runs(self, tmp_path):
        """Second run is a no-op once eligible rows are gone."""
        db_file = tmp_path / "ucc_idem.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        _add_ucc(session, age_days=100)

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings()):
            first = await task.execute()
            second = await task.execute()

        assert first.details["deleted"]["unique_client_connections"] == 1
        assert second.details["deleted"]["unique_client_connections"] == 0

    @pytest.mark.asyncio
    async def test_no_rows_is_silent_noop(self, tmp_path):
        """Empty table produces zero deletions without error."""
        db_file = tmp_path / "ucc_empty.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings()):
            result = await task.execute()

        assert result.details["deleted"]["unique_client_connections"] == 0
        errors = result.details.get("errors", [])
        assert not any("UniqueClient" in e for e in errors)

    @pytest.mark.asyncio
    async def test_multiple_channels_and_ips_mixed_ages(self, tmp_path):
        """Rows spanning multiple (channel, IP) combinations are each pruned
        or kept individually based on their own ``connected_at``.  This
        validates the simple WHERE-clause prune handles high-cardinality tables
        (no GROUP BY / PARTITION quirks)."""
        db_file = tmp_path / "ucc_multi.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        # 3 old (prune), 2 fresh (keep).
        old_ids = [
            _add_ucc(session, age_days=100, ip=f"10.0.0.{i}", channel_id=f"ch-{i}")
            for i in range(1, 4)
        ]
        fresh_ids = [
            _add_ucc(session, age_days=5, ip=f"10.0.1.{i}", channel_id=f"ch-{i+10}")
            for i in range(1, 3)
        ]

        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=_settings(ucc_days=90)):
            result = await task.execute()

        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            surviving_ids = {r.id for r in verify.query(UniqueClientConnection).all()}
        finally:
            verify.close()

        for oid in old_ids:
            assert oid not in surviving_ids, f"old row {oid} must be pruned"
        for fid in fresh_ids:
            assert fid in surviving_ids, f"fresh row {fid} must survive"

        assert result.details["deleted"]["unique_client_connections"] == 3


# ============================================================================
# Order: prune runs before VACUUM (step 9 < step 10)
# ============================================================================


class TestUCCPruneRunsBeforeVacuum:
    """The UCC prune step (step 9) must run BEFORE the VACUUM step (step 10)."""

    @pytest.mark.asyncio
    async def test_prune_step_precedes_vacuum_step(self, tmp_path):
        db_file = tmp_path / "ucc_order.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine, vacuum=True)

        _add_ucc(session, age_days=100)

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

        ucc_item = next(
            (i for i in progress_items if "unique" in i.lower() or "client" in i.lower()),
            None,
        )
        vacuum_item = next(
            (i for i in progress_items if "vacuum" in i.lower()), None
        )
        assert ucc_item is not None, f"no UCC-prune step in progress: {progress_items}"
        assert vacuum_item is not None, f"no vacuum step in progress: {progress_items}"
        assert progress_items.index(ucc_item) < progress_items.index(vacuum_item), (
            f"UCC prune must run BEFORE vacuum; order was {progress_items}"
        )


# ============================================================================
# Config defaults: getattr fallback when knob absent from Settings stub
# ============================================================================


class TestUCCPruneConfigDefaults:
    """Falls back to 90 days when the knob is absent from the Settings stub."""

    @pytest.mark.asyncio
    async def test_defaults_when_knob_absent_from_settings(self, tmp_path):
        """A settings object lacking ``unique_client_connection_days`` defaults
        via getattr() to 90 days — a 100-day-old row is still pruned."""
        db_file = tmp_path / "ucc_defaults.db"
        engine = _make_engine(db_file)
        Base.metadata.create_all(engine)
        session, task = _make_session_and_task(engine)

        old_id = _add_ucc(session, age_days=100)

        # Stub without the UCC knob; step 7/8 knobs included so those don't error.
        stub = SimpleNamespace(
            auto_creation_snapshot_days=30,
            auto_creation_snapshot_max=50,
            m3u_snapshot_days=90,
            m3u_change_log_days=90,
        )
        with patch("tasks.cleanup.get_session", return_value=session), \
             patch("config.get_settings", return_value=stub):
            result = await task.execute()

        SessionLocal = sessionmaker(bind=engine)
        verify = SessionLocal()
        try:
            surviving_ids = {r.id for r in verify.query(UniqueClientConnection).all()}
        finally:
            verify.close()

        assert old_id not in surviving_ids, "100d-old row must be pruned at default 90d"
        assert result.details["deleted"]["unique_client_connections"] == 1
