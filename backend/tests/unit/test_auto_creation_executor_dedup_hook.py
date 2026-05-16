"""
Integration tests for the BD-F bulk-M3U dedup hook wired into the
auto-creation executor (``_execute_create_channel``).

These exercise the executor side of the BD-F contract — same-group
candidate filtering, exec-context aggregation, and the
``triggered_by`` gate — using a real ``ActionExecutor`` against
mocked Dispatcharr calls and a real in-memory SQLite session for
the ``pending_merges`` writes.

Unit-level tests for the hook service itself live in
``backend/tests/services/test_m3u_dedup_hook.py``.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
from auto_creation_evaluator import StreamContext
from auto_creation_executor import ActionExecutor, ExecutionContext
from models import PendingMerge


@pytest.fixture
def _bind_session_local(test_engine, monkeypatch):
    """Wire database._SessionLocal so the hook's get_session() works."""
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", SessionLocal)
    yield SessionLocal


def _make_executor(*, triggered_by: str, existing_channels: list):
    """Build an ActionExecutor with the channel cache the dedup hook
    needs and a mock create_channel call."""
    client = MagicMock()
    client.create_channel = AsyncMock(
        return_value={"id": 999, "name": "NEW"}
    )
    client.update_channel = AsyncMock()
    return ActionExecutor(
        client,
        existing_channels=existing_channels,
        existing_groups=[{"id": 42, "name": "Sports"}],
        triggered_by=triggered_by,
    )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestM3URefreshTriggeredEnqueue:
    """When triggered_by='m3u_refresh' and a candidate matches above
    threshold, the executor enqueues a pending_merges row and skips
    the create_channel API call."""

    def test_fuzzy_match_enqueues_pending_and_skips_create(
        self, test_session, _bind_session_local, monkeypatch
    ):
        # Existing channel in group 42 with a name that the matcher
        # will score high against "ESPN HD" (token_set_ratio).
        existing = [
            {
                "id": 100,
                "name": "ESPN",
                "channel_group_id": 42,
                "streams": [],
            }
        ]
        executor = _make_executor(
            triggered_by="m3u_refresh", existing_channels=existing
        )

        stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN HD",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="Sports",
        )
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "group_id": 42,
        }
        exec_ctx = ExecutionContext()

        result = _run(executor.execute(action, stream_ctx, exec_ctx))

        assert result.success is True
        assert result.skipped is True
        assert result.created is False
        assert "pending" in result.description.lower()
        # The create_channel API call MUST NOT have been made.
        executor.client.create_channel.assert_not_called()
        # The exec_ctx tracks the enqueue for the engine aggregator.
        assert exec_ctx.pending_merges_added == 1
        # A pending row exists in the DB.
        rows = test_session.query(PendingMerge).all()
        assert len(rows) == 1
        assert rows[0].stream_name == "ESPN HD"
        assert rows[0].group_id == 42
        assert rows[0].candidate_channel_id == "100"
        assert rows[0].status == "pending"
        assert rows[0].trigger_context == "m3u_refresh"

    def test_no_candidate_falls_through_to_normal_create(
        self, test_session, _bind_session_local
    ):
        # Existing channels are in a DIFFERENT group → empty same-group
        # candidate list → hook returns no enqueue → executor proceeds
        # with the normal create_channel API call.
        existing = [
            {
                "id": 100,
                "name": "BBC One",
                "channel_group_id": 7,  # different group
                "streams": [],
            }
        ]
        executor = _make_executor(
            triggered_by="m3u_refresh", existing_channels=existing
        )

        stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN HD",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="Sports",
        )
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "group_id": 42,
        }
        exec_ctx = ExecutionContext()

        result = _run(executor.execute(action, stream_ctx, exec_ctx))

        assert result.success is True
        assert result.created is True
        assert result.skipped is False
        executor.client.create_channel.assert_called_once()
        assert exec_ctx.pending_merges_added == 0
        assert test_session.query(PendingMerge).count() == 0

    def test_high_operator_threshold_above_match_falls_through(
        self, test_session, _bind_session_local, monkeypatch
    ):
        # Operator sets dedup_threshold=0.95 (well above what the
        # matcher would score for these two strings) → no candidate →
        # normal channel creation proceeds.
        existing = [
            {
                "id": 100,
                "name": "BBC One",
                "channel_group_id": 42,
                "streams": [],
            }
        ]
        executor = _make_executor(
            triggered_by="m3u_refresh", existing_channels=existing
        )

        # Stub get_settings to return our high-threshold settings.
        from config import DispatcharrSettings
        high = DispatcharrSettings(dedup_threshold=0.95)
        monkeypatch.setattr(
            "config.get_settings", lambda: high
        )

        stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN HD",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="Sports",
        )
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "group_id": 42,
        }
        exec_ctx = ExecutionContext()

        result = _run(executor.execute(action, stream_ctx, exec_ctx))

        # No match → fell through to normal create.
        assert result.success is True
        assert result.created is True
        executor.client.create_channel.assert_called_once()
        assert exec_ctx.pending_merges_added == 0
        assert test_session.query(PendingMerge).count() == 0


class TestNonM3URefreshLeftAlone:
    """triggered_by other than 'm3u_refresh' must NOT enqueue —
    scheduled / manual auto-creation runs predate the dedup epic."""

    @pytest.mark.parametrize("triggered_by", ["scheduled", "manual", "api"])
    def test_other_triggered_by_falls_through_to_create(
        self, test_session, _bind_session_local, triggered_by
    ):
        existing = [
            {
                "id": 100,
                "name": "ESPN",  # would match strongly if hook were active
                "channel_group_id": 42,
                "streams": [],
            }
        ]
        executor = _make_executor(
            triggered_by=triggered_by, existing_channels=existing
        )

        stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN HD",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="Sports",
        )
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "group_id": 42,
        }
        exec_ctx = ExecutionContext()

        result = _run(executor.execute(action, stream_ctx, exec_ctx))

        # Hook bypassed → normal channel creation.
        assert result.success is True
        assert result.created is True
        executor.client.create_channel.assert_called_once()
        assert exec_ctx.pending_merges_added == 0
        assert test_session.query(PendingMerge).count() == 0


class TestRepeatM3URefreshIdempotency:
    """ADR-008 §D5: repeated M3U refreshes of the same stream against
    the same candidate must NOT crash and MUST NOT duplicate rows."""

    def test_second_refresh_handles_partial_unique_index_collision(
        self, test_session, _bind_session_local, caplog
    ):
        import logging

        existing = [
            {
                "id": 100,
                "name": "ESPN",
                "channel_group_id": 42,
                "streams": [],
            }
        ]

        # First refresh enqueues.
        executor1 = _make_executor(
            triggered_by="m3u_refresh", existing_channels=existing
        )
        stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN HD",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="Sports",
        )
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "group_id": 42,
        }
        first_ctx = ExecutionContext()
        _run(executor1.execute(action, stream_ctx, first_ctx))
        assert test_session.query(PendingMerge).count() == 1

        # Second refresh — must hit the partial unique index and be
        # caught gracefully with an INFO log line.
        executor2 = _make_executor(
            triggered_by="m3u_refresh", existing_channels=existing
        )
        second_ctx = ExecutionContext()
        with caplog.at_level(logging.INFO, logger="services.m3u_dedup_hook"):
            result = _run(executor2.execute(action, stream_ctx, second_ctx))

        # No crash, still signals "skip channel creation" because the
        # prior pending row is still authoritative.
        assert result.success is True
        assert result.skipped is True
        executor2.client.create_channel.assert_not_called()
        # Row count unchanged — no duplicate.
        assert test_session.query(PendingMerge).count() == 1
        # Counter still increments via the tracker on the exec_ctx —
        # the engine surfaces this to the operator via "N pending
        # merges queued" (real + already-queued both count, because
        # operationally both block the channel from being created).
        assert second_ctx.pending_merges_added == 1
        # And the §D5 collision log line fired.
        assert any(
            "already queued" in r.message and r.levelno == logging.INFO
            for r in caplog.records
        )


class TestDryRunBypass:
    """Dry-run previews must NOT mutate the queue."""

    def test_dry_run_does_not_enqueue_even_with_strong_match(
        self, test_session, _bind_session_local
    ):
        existing = [
            {
                "id": 100,
                "name": "ESPN HD",
                "channel_group_id": 42,
                "streams": [],
            }
        ]
        executor = _make_executor(
            triggered_by="m3u_refresh", existing_channels=existing
        )

        stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN HD",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="Sports",
        )
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "group_id": 42,
        }
        exec_ctx = ExecutionContext(dry_run=True)

        result = _run(executor.execute(action, stream_ctx, exec_ctx))

        # Dry-run preview shows "would create" (it actually does match
        # the existing channel via the executor's own _find_channel_by_name
        # exact lookup, so we get the "already exists" path — that is
        # the pre-BD-F behaviour and the hook does NOT change it for
        # dry-run). What matters: no pending_merges row was written.
        assert result.success is True
        assert test_session.query(PendingMerge).count() == 0
        assert exec_ctx.pending_merges_added == 0
