"""Unit tests for the auto-creation pre-run snapshot CAPTURE (ADR-010 §D2/§D3).

Bead: ``enhancedchannelmanager-uc51o.2`` (Phase 2 of epic
``enhancedchannelmanager-uc51o``).

Pins the capture-hook contract:

* ``_capture_snapshot`` serializes the manual (non-Dispatcharr-auto-created)
  channel<->stream state from the in-memory ``self._existing_channels`` —
  STREAM IDs ONLY (§D1), one snapshot row per execution.
* Channels where ``auto_created`` is truthy are EXCLUDED (§D3); manual
  channels (``auto_created`` falsy / absent) are INCLUDED with their
  ``stream_ids`` (coercing dict-or-int stream entries).
* Capture failure LOGS a warning and the run PROCEEDS with no snapshot — the
  helper does NOT raise (§D2 v1 log-and-proceed policy).
* ``run_pipeline`` invokes capture only when ``mode=="execute"`` (``not
  dry_run``); a dry run writes NO snapshot.

Capture tests use a real in-memory SQLite session (so the persisted row is
read back and asserted), bound by patching ``channel_pipeline_engine.get_session``.
The dry-run gating test stubs the pipeline helpers and spies on
``_capture_snapshot`` so the gate is asserted without exercising the full
mutation path.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from channel_pipeline_engine import ChannelPipelineEngine
from models import ChannelPipelineExecution, ChannelPipelineSnapshot


@pytest.fixture()
def db_session_factory():
    """A real in-memory SQLite session factory with the ECM schema created.

    Yields a callable that returns a fresh session bound to the same in-memory
    engine (StaticPool keeps the single connection alive across sessions, so
    rows committed in one session are visible in the next).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    database.Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
    )
    try:
        yield SessionLocal
    finally:
        database.Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _make_execution(session_factory, mode: str = "execute") -> int:
    """Create an ChannelPipelineExecution row and return its id (FK target)."""
    from datetime import datetime

    session = session_factory()
    try:
        execution = ChannelPipelineExecution(
            mode=mode,
            triggered_by="manual",
            started_at=datetime.utcnow(),
            status="running",
        )
        session.add(execution)
        session.commit()
        session.refresh(execution)
        return execution.id
    finally:
        session.close()


class TestCaptureSerialization:
    """_capture_snapshot writes one row with the §D1/§D3 payload."""

    def test_captures_manual_channels_with_stream_ids(self, db_session_factory):
        """A snapshot row is written with manual channels and their stream IDs.

        Stream entries arrive as either bare ints or ``{"id": ...}`` dicts
        (Dispatcharr embeds IDs; the coercion mirrors executor.py:918). Only
        IDs are persisted — never URLs.
        """
        execution_id = _make_execution(db_session_factory)

        client = MagicMock()
        engine = ChannelPipelineEngine(client)
        engine._existing_channels = [
            {
                "id": 10, "name": "ESPN", "channel_group_id": 1,
                "epg_data_id": 99, "tvg_id": "espn.us",
                # Mixed: dict-form and int-form stream entries.
                "streams": [{"id": 501, "url": "http://leak/501.ts"}, 502],
            },
            {
                "id": 11, "name": "CNN", "channel_group_id": 2,
                "epg_data_id": None, "tvg_id": "cnn.us",
                "streams": [601],
            },
        ]

        with patch("channel_pipeline_engine.get_session", side_effect=db_session_factory):
            asyncio.get_event_loop().run_until_complete(
                engine._capture_snapshot(execution_id)
            )

        session = db_session_factory()
        try:
            row = session.query(ChannelPipelineSnapshot).filter(
                ChannelPipelineSnapshot.execution_id == execution_id
            ).first()
            assert row is not None, "no snapshot row was written"
            assert row.channel_count == 2
            channels = row.get_channels_data()["channels"]
            assert {c["id"] for c in channels} == {10, 11}

            espn = next(c for c in channels if c["id"] == 10)
            assert espn["stream_ids"] == [501, 502]
            assert espn["name"] == "ESPN"
            assert espn["channel_group_id"] == 1
            assert espn["epg_data_id"] == 99
            assert espn["tvg_id"] == "espn.us"

            # §D1: no URL leaks into the serialized payload anywhere.
            serialized = row.channels_data
            assert "leak" not in serialized
            assert "url" not in serialized
            assert ".ts" not in serialized
        finally:
            session.close()

    def test_excludes_auto_created_channels(self, db_session_factory):
        """Channels with truthy ``auto_created`` are excluded (§D3); manual
        ones are kept with the correct stream_ids."""
        execution_id = _make_execution(db_session_factory)

        client = MagicMock()
        engine = ChannelPipelineEngine(client)
        engine._existing_channels = [
            # Manual — kept.
            {"id": 1, "name": "Manual A", "streams": [100, 101]},
            # Dispatcharr-auto-created-from-groups — excluded.
            {"id": 2, "name": "Auto B", "auto_created": True, "streams": [200]},
            # auto_created falsy — kept (a "claimed"/cleared channel).
            {"id": 3, "name": "Manual C", "auto_created": False, "streams": [300]},
            # auto_created absent — kept (defaults to manual).
            {"id": 4, "name": "Manual D", "streams": [400]},
        ]

        with patch("channel_pipeline_engine.get_session", side_effect=db_session_factory):
            asyncio.get_event_loop().run_until_complete(
                engine._capture_snapshot(execution_id)
            )

        session = db_session_factory()
        try:
            row = session.query(ChannelPipelineSnapshot).filter(
                ChannelPipelineSnapshot.execution_id == execution_id
            ).first()
            channels = row.get_channels_data()["channels"]
            ids = {c["id"] for c in channels}
            assert ids == {1, 3, 4}, "auto_created channel (id=2) must be excluded"
            assert row.channel_count == 3
            kept = {c["id"]: c["stream_ids"] for c in channels}
            assert kept[1] == [100, 101]
            assert kept[3] == [300]
            assert kept[4] == [400]
        finally:
            session.close()

    def test_no_channels_writes_empty_snapshot(self, db_session_factory):
        """An instance with no manual channels still writes a (zero-count) row
        — the run had nothing manual to capture, not a capture failure."""
        execution_id = _make_execution(db_session_factory)

        client = MagicMock()
        engine = ChannelPipelineEngine(client)
        engine._existing_channels = [
            {"id": 2, "name": "Auto", "auto_created": True, "streams": [200]},
        ]

        with patch("channel_pipeline_engine.get_session", side_effect=db_session_factory):
            asyncio.get_event_loop().run_until_complete(
                engine._capture_snapshot(execution_id)
            )

        session = db_session_factory()
        try:
            row = session.query(ChannelPipelineSnapshot).filter(
                ChannelPipelineSnapshot.execution_id == execution_id
            ).first()
            assert row is not None
            assert row.channel_count == 0
            assert row.get_channels_data()["channels"] == []
        finally:
            session.close()


class TestCaptureFailureLogsAndProceeds:
    """A capture failure logs a warning and does NOT raise (§D2)."""

    def test_failure_logs_warning_and_does_not_raise(self, db_session_factory, caplog):
        """When the DB write fails, _capture_snapshot swallows the error,
        logs at WARNING, and returns normally so the mutating run proceeds."""
        execution_id = _make_execution(db_session_factory)

        client = MagicMock()
        engine = ChannelPipelineEngine(client)
        engine._existing_channels = [
            {"id": 1, "name": "Manual A", "streams": [100]},
        ]

        # Force the persistence to blow up: get_session returns a session whose
        # commit raises. The helper must catch it, log, and NOT re-raise.
        failing_session = MagicMock()
        failing_session.commit.side_effect = RuntimeError("disk full")

        with patch("channel_pipeline_engine.get_session", return_value=failing_session), \
             caplog.at_level(logging.WARNING, logger="channel_pipeline_engine"):
            # Must NOT raise.
            asyncio.get_event_loop().run_until_complete(
                engine._capture_snapshot(execution_id)
            )

        # Warning logged, mentions the execution id, and no snapshot persisted.
        assert any(
            "Failed to capture pre-run snapshot" in rec.message
            for rec in caplog.records
        ), f"expected a capture-failure WARNING; got {[r.message for r in caplog.records]}"

        session = db_session_factory()
        try:
            row = session.query(ChannelPipelineSnapshot).filter(
                ChannelPipelineSnapshot.execution_id == execution_id
            ).first()
            assert row is None, "no snapshot should exist after a capture failure"
        finally:
            session.close()


class TestRunPipelineDryRunGate:
    """run_pipeline captures only on mode=='execute' (not dry-run) — §D2."""

    def _stub_engine(self):
        """An engine with the pipeline helpers stubbed so run_pipeline reaches
        (or skips) the capture call without touching Dispatcharr or the DB."""
        client = MagicMock()
        engine = ChannelPipelineEngine(client)

        engine._load_existing_data = AsyncMock()
        # ti939.1.3: a MagicMock's is_event_sync() is truthy by default, which
        # would classify the stub rule as event_sync and short-circuit the run.
        stub_rule = MagicMock(id=1)
        stub_rule.is_event_sync.return_value = False
        engine._load_rules = AsyncMock(return_value=[stub_rule])
        engine._fetch_streams = AsyncMock(return_value=[])
        engine._apply_global_filters = AsyncMock(return_value=([], []))
        engine._capture_snapshot = AsyncMock()
        engine._update_rule_stats = AsyncMock()
        engine._save_execution = AsyncMock()

        execution = MagicMock()
        execution.id = 4242
        engine._create_execution = AsyncMock(return_value=execution)

        # Minimal valid results dict for the finalize block.
        engine._process_streams = AsyncMock(return_value={
            "streams_evaluated": 0, "streams_matched": 0,
            "channels_created": 0, "channels_updated": 0,
            "groups_created": 0, "streams_merged": 0,
            "channels_touched": 0, "streams_skipped": 0,
            "streams_excluded": 0, "created_entities": [],
            "modified_entities": [], "execution_log": [],
            "dry_run_results": [],
        })
        return engine

    def test_execute_run_captures_snapshot(self):
        """mode=='execute' (dry_run=False) → _capture_snapshot is called with
        the execution id, before _process_streams."""
        engine = self._stub_engine()

        asyncio.get_event_loop().run_until_complete(
            engine.run_pipeline(dry_run=False, triggered_by="manual")
        )

        engine._capture_snapshot.assert_awaited_once_with(4242)

    def test_dry_run_skips_snapshot(self):
        """dry_run=True → _capture_snapshot is NEVER called (no snapshot for a
        run that mutates nothing)."""
        engine = self._stub_engine()

        asyncio.get_event_loop().run_until_complete(
            engine.run_pipeline(dry_run=True, triggered_by="manual")
        )

        engine._capture_snapshot.assert_not_called()
