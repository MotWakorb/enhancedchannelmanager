"""Unit tests for the auto-creation snapshot RESTORE algorithm (ADR-010 §D8).

Bead: ``enhancedchannelmanager-uc51o.4`` (Phase 3 of epic
``enhancedchannelmanager-uc51o``).

SAFETY-CRITICAL: ``AutoCreationEngine.restore_snapshot`` mutates live
Dispatcharr channels. These tests pin the restore contract:

* Happy path — run-CREATED channels are deleted; every snapshot channel's
  stream set is full-REPLACED (``update_channel(streams=[ids])``) and key
  metadata restored.
* Idempotency — calling restore twice yields the same end state; the second
  call is clean (no double-delete crash, re-issues the same PATCHes).
* Partial failure — a missing channel / vanished stream is COLLECTED into
  ``failed_channels`` and the loop CONTINUES; the result reports it and is NOT
  a blanket success.
* 404-equivalent — an execution with no snapshot returns ``no_snapshot=True``.
* Guards — dry-run / already-reverted executions are refused.

Tests use a real in-memory SQLite session (so the persisted snapshot/execution
rows are read back) bound by patching ``auto_creation_engine.get_session``.
The Dispatcharr client is a mock with async methods.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from auto_creation_engine import AutoCreationEngine
from models import AutoCreationExecution, AutoCreationSnapshot


@pytest.fixture()
def db_session_factory():
    """In-memory SQLite session factory with the ECM schema (StaticPool keeps
    the single connection alive across sessions)."""
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


def _seed_execution_and_snapshot(
    session_factory,
    *,
    mode: str = "execute",
    status: str = "completed",
    created_entities: list | None = None,
    channels: list | None = None,
    with_snapshot: bool = True,
) -> int:
    """Create an execution (+ optional snapshot) and return the execution id."""
    session = session_factory()
    try:
        execution = AutoCreationExecution(
            mode=mode,
            triggered_by="manual",
            started_at=datetime.utcnow(),
            status=status,
            rule_name="Test Rule",
        )
        if created_entities is not None:
            execution.set_created_entities(created_entities)
        session.add(execution)
        session.commit()
        session.refresh(execution)
        execution_id = execution.id

        if with_snapshot:
            snapshot = AutoCreationSnapshot(
                execution_id=execution_id,
                snapshot_time=datetime.utcnow(),
                channel_count=len(channels or []),
            )
            snapshot.set_channels_data({"channels": channels or []})
            session.add(snapshot)
            session.commit()
        return execution_id
    finally:
        session.close()


def _make_engine_with_client():
    client = MagicMock()
    client.update_channel = AsyncMock()
    client.delete_channel = AsyncMock()
    client.delete_channel_group = AsyncMock()
    return AutoCreationEngine(client), client


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestRestoreHappyPath:
    """Created channels deleted + snapshot channels' streams/metadata restored."""

    def test_full_restore_deletes_created_and_replaces_streams(self, db_session_factory):
        channels = [
            {"id": 10, "name": "ESPN", "channel_group_id": 1,
             "epg_data_id": 99, "tvg_id": "espn.us", "stream_ids": [501, 502]},
            {"id": 11, "name": "CNN", "channel_group_id": 2,
             "epg_data_id": None, "tvg_id": "cnn.us", "stream_ids": [601]},
        ]
        created = [
            {"type": "channel", "id": 500, "name": "New Sports"},
            {"type": "group", "id": 77, "name": "New Group"},
        ]
        execution_id = _seed_execution_and_snapshot(
            db_session_factory, created_entities=created, channels=channels,
        )

        engine, client = _make_engine_with_client()
        with patch("auto_creation_engine.get_session", side_effect=db_session_factory):
            result = _run(engine.restore_snapshot(execution_id))

        assert result["success"] is True
        assert result["removed_channels"] == 1  # one created CHANNEL (group not counted)
        assert result["restored_channels"] == 2
        assert result["failed_channels"] == []

        # Created channel + group were deleted.
        client.delete_channel.assert_awaited_once_with(500)
        client.delete_channel_group.assert_awaited_once_with(77)

        # Both snapshot channels got a full-replace PATCH with IDs-only streams
        # and the restored metadata.
        assert client.update_channel.await_count == 2
        calls = {c.args[0]: c.args[1] for c in client.update_channel.await_args_list}
        assert calls[10]["streams"] == [501, 502]
        assert calls[10]["name"] == "ESPN"
        assert calls[10]["channel_group_id"] == 1
        assert calls[10]["epg_data_id"] == 99
        assert calls[10]["tvg_id"] == "espn.us"
        assert calls[11]["streams"] == [601]

        # Execution marked terminal (shared rolled_back state).
        session = db_session_factory()
        try:
            ex = session.query(AutoCreationExecution).filter(
                AutoCreationExecution.id == execution_id
            ).first()
            assert ex.status == "rolled_back"
            assert ex.rolled_back_at is not None
            assert ex.rolled_back_by == "manual"
        finally:
            session.close()

    def test_streams_are_ids_only_never_urls(self, db_session_factory):
        """The PATCH body carries integer stream IDs only — never URLs (§D1)."""
        channels = [{"id": 10, "name": "ESPN", "stream_ids": [501, 502]}]
        execution_id = _seed_execution_and_snapshot(
            db_session_factory, channels=channels,
        )
        engine, client = _make_engine_with_client()
        with patch("auto_creation_engine.get_session", side_effect=db_session_factory):
            _run(engine.restore_snapshot(execution_id))

        body = client.update_channel.await_args_list[0].args[1]
        assert body["streams"] == [501, 502]
        assert all(isinstance(s, int) for s in body["streams"])


class TestRestoreIdempotency:
    """Calling restore twice yields the same end state; the second call is clean
    (no double-delete crash, end state unchanged)."""

    def test_second_restore_after_full_success_is_clean_noop(self, db_session_factory):
        """After a clean restore the execution is terminal (rolled_back). A
        second call is a safe no-op refused as already-reverted — it does NOT
        crash and does NOT re-mutate, so the end state is unchanged. This is the
        terminal-state guard shared with the legacy rollback."""
        channels = [
            {"id": 10, "name": "ESPN", "channel_group_id": 1,
             "epg_data_id": 99, "tvg_id": "espn.us", "stream_ids": [501]},
        ]
        created = [{"type": "channel", "id": 500, "name": "New Sports"}]
        execution_id = _seed_execution_and_snapshot(
            db_session_factory, created_entities=created, channels=channels,
        )

        engine, client = _make_engine_with_client()

        with patch("auto_creation_engine.get_session", side_effect=db_session_factory):
            first = _run(engine.restore_snapshot(execution_id))
            patches_after_first = client.update_channel.await_count
            second = _run(engine.restore_snapshot(execution_id))

        assert first["success"] is True
        # Second call is a clean no-op (already-reverted guard) — no crash, no
        # re-mutation.
        assert second["success"] is False
        assert "already reverted" in second["error"].lower()
        assert client.update_channel.await_count == patches_after_first

    def test_retry_after_partial_failure_reissues_patches(self, db_session_factory):
        """A partial restore leaves the execution NON-terminal, so a retry
        re-issues the same PATCHes → same end state (ADR-010 §D8 step 5)."""
        channels = [
            {"id": 10, "name": "ESPN", "stream_ids": [501]},
            {"id": 11, "name": "CNN", "stream_ids": [601]},
        ]
        execution_id = _seed_execution_and_snapshot(
            db_session_factory, channels=channels,
        )

        engine, client = _make_engine_with_client()

        # First pass: channel 11 fails (transient) → partial, non-terminal.
        async def _fail_11(channel_id, payload):
            if channel_id == 11:
                raise Exception("transient 503")
            return {}
        client.update_channel = AsyncMock(side_effect=_fail_11)

        with patch("auto_creation_engine.get_session", side_effect=db_session_factory):
            first = _run(engine.restore_snapshot(execution_id))
            # Retry: now everything succeeds.
            client.update_channel = AsyncMock(return_value={})
            second = _run(engine.restore_snapshot(execution_id))

        assert first["success"] is False
        assert len(first["failed_channels"]) == 1
        # Retry re-issued BOTH PATCHes (the run was not terminal) and succeeded.
        assert second["success"] is True
        assert second["restored_channels"] == 2
        assert client.update_channel.await_count == 2


class TestRestorePartialFailure:
    """Missing channel / vanished stream is collected and surfaced, not fatal."""

    def test_failure_on_one_channel_does_not_abort_others(self, db_session_factory):
        channels = [
            {"id": 10, "name": "ESPN", "stream_ids": [501]},
            {"id": 11, "name": "GONE", "stream_ids": [601]},   # channel deleted
            {"id": 12, "name": "CNN", "stream_ids": [701]},
        ]
        execution_id = _seed_execution_and_snapshot(
            db_session_factory, channels=channels,
        )

        engine, client = _make_engine_with_client()

        async def _update(channel_id, payload):
            if channel_id == 11:
                raise Exception("404 channel not found")
            return {}

        client.update_channel = AsyncMock(side_effect=_update)

        with patch("auto_creation_engine.get_session", side_effect=db_session_factory):
            result = _run(engine.restore_snapshot(execution_id))

        # NOT a blanket success — one item failed.
        assert result["success"] is False
        # The other two were still restored (loop did not abort on first failure).
        assert result["restored_channels"] == 2
        assert len(result["failed_channels"]) == 1
        failed = result["failed_channels"][0]
        assert failed["id"] == 11
        assert failed["name"] == "GONE"
        assert "404" in failed["error"]

        # All three were ATTEMPTED.
        assert client.update_channel.await_count == 3

        # A partial restore stays re-runnable: execution NOT marked terminal.
        session = db_session_factory()
        try:
            ex = session.query(AutoCreationExecution).filter(
                AutoCreationExecution.id == execution_id
            ).first()
            assert ex.status == "completed", "partial restore must stay re-runnable"
        finally:
            session.close()


class TestRestoreGuards:
    """No-snapshot → no_snapshot flag; dry-run / already-reverted refused."""

    def test_no_snapshot_returns_flag(self, db_session_factory):
        execution_id = _seed_execution_and_snapshot(
            db_session_factory, with_snapshot=False,
        )
        engine, client = _make_engine_with_client()
        with patch("auto_creation_engine.get_session", side_effect=db_session_factory):
            result = _run(engine.restore_snapshot(execution_id))

        assert result["success"] is False
        assert result["no_snapshot"] is True
        assert "rollback" in result["error"].lower()
        client.update_channel.assert_not_called()

    def test_execution_not_found(self, db_session_factory):
        engine, client = _make_engine_with_client()
        with patch("auto_creation_engine.get_session", side_effect=db_session_factory):
            result = _run(engine.restore_snapshot(99999))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_dry_run_refused(self, db_session_factory):
        execution_id = _seed_execution_and_snapshot(
            db_session_factory, mode="dry_run", with_snapshot=False,
        )
        engine, client = _make_engine_with_client()
        with patch("auto_creation_engine.get_session", side_effect=db_session_factory):
            result = _run(engine.restore_snapshot(execution_id))
        assert result["success"] is False
        assert "dry-run" in result["error"].lower()
        client.update_channel.assert_not_called()

    def test_already_reverted_refused(self, db_session_factory):
        execution_id = _seed_execution_and_snapshot(
            db_session_factory, status="rolled_back",
            channels=[{"id": 10, "name": "ESPN", "stream_ids": [501]}],
        )
        engine, client = _make_engine_with_client()
        with patch("auto_creation_engine.get_session", side_effect=db_session_factory):
            result = _run(engine.restore_snapshot(execution_id))
        assert result["success"] is False
        assert "already reverted" in result["error"].lower()
        client.update_channel.assert_not_called()
