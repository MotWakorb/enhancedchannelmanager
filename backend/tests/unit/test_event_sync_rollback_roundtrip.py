"""Event Sync rollback/snapshot end-to-end verification (SRE ship-blocker,
bead enhancedchannelmanager-ti939.2.3).

Proves the recovery mechanisms for event_sync attach runs rather than
assuming the pre-existing snapshot machinery covers the new path:

* **Snapshot/rollback round trip** — a manual attach run captures a
  pre-mutation ChannelPipelineSnapshot that INCLUDES the event master-group
  channels; ``rollback_execution`` (confirm-gated, delegating to
  ``restore_snapshot``) removes every attachment the run made and NEVER
  deletes the master channels themselves (ECM never created them).
* **Streams-only master restore** (PR #616 review nit): channels flagged
  ``event_sync_master`` at capture time are restored with a STREAMS-ONLY
  payload — Dispatcharr owns their name/group/epg/tvg and updates them in
  place across refreshes (slot renames), so writing captured metadata back
  would revert Dispatcharr-owned state to stale pre-run values with no
  guarantee Dispatcharr self-heals before the source stream next changes.
  Unflagged channels (standard-rule snapshots, legacy pre-flag snapshots)
  keep the full-payload restore byte-identical.
* **No group-settings writes** — an AST scan of every event_sync code path
  proves no code writes Dispatcharr group settings (Phase 1 never toggles
  ``auto_channel_sync``; snapshot restore therefore never needs to either).
* **Circuit breaker** — the channel-pipeline breaker (bd-exo4j: the startup
  crash-sentinel abandons runs left 'running' and trips the persisted
  run-on-refresh flag) covers event_sync runs: their executions are ordinary
  ChannelPipelineExecution rows, so a crashed attach run trips the breaker;
  the reset endpoint clears it; and a tripped breaker gates ONLY the
  unattended auto-fire chain — a deliberate manual event_sync run is never
  gated (by design: manual "Run Now" is the operator's recovery surface).
"""
from __future__ import annotations

import ast
import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from channel_pipeline_engine import ChannelPipelineEngine
from models import (
    ChannelPipelineExecution,
    ChannelPipelineRule,
    ChannelPipelineSnapshot,
)
from tests.event_sync_fixtures import (
    FakeDispatcharrState,
    GROUP_NAMES,
    MASTER_GROUP_ID,
    SECONDARY_A,
    assert_never_touched_group_settings,
    event_sync_config,
    make_stateful_client,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]

MASTER_MERCURY = "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
STREAM_MERCURY = "WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
SECONDARY_GROUP_NAME = GROUP_NAMES[SECONDARY_A]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture()
def db_session_factory():
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


def _add_event_rule(session_factory) -> int:
    session = session_factory()
    try:
        rule = ChannelPipelineRule(
            name="Event Rule",
            enabled=True,
            priority=0,
            conditions=json.dumps([{"type": "always"}]),
            actions=json.dumps([{"type": "skip"}]),
            event_sync_config=json.dumps(
                event_sync_config(secondary_group_ids=[SECONDARY_A])
            ),
        )
        session.add(rule)
        session.commit()
        session.refresh(rule)
        return rule.id
    finally:
        session.close()


class TestSnapshotRollbackRoundTrip:
    """Attach run -> pre-mutation snapshot -> confirm-gated rollback ->
    attachments gone, master channels intact."""

    def _attach_run(self, db_session_factory):
        """A manual live run that attaches one secondary stream, against a
        state that also carries a MANUAL channel (the standard snapshot
        population) so the restore exercises BOTH payload shapes."""
        _add_event_rule(db_session_factory)
        state = FakeDispatcharrState(
            channels=[
                {"id": 100, "name": MASTER_MERCURY,
                 "channel_group_id": MASTER_GROUP_ID,
                 "auto_created": True, "epg_data_id": 77, "tvg_id": "evt.1",
                 "streams": [9001]},
                {"id": 50, "name": "My Manual", "channel_group_id": 3,
                 "auto_created": False, "epg_data_id": 88, "tvg_id": "man.1",
                 "streams": [601]},
            ],
            secondary_streams={SECONDARY_GROUP_NAME: [
                {"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1},
            ]},
        )
        client = make_stateful_client(state)
        engine = ChannelPipelineEngine(client)
        with patch("channel_pipeline_engine.get_session",
                   side_effect=db_session_factory), \
             patch("journal.log_entries"):
            result = _run(engine.run_pipeline(
                dry_run=False, triggered_by="manual"
            ))
        assert result["success"] is True
        assert result["event_sync"][0]["attached"] == 1
        assert state.stream_ids_of(100) == [9001, 7001]
        return engine, state, client, result["execution_id"]

    def test_snapshot_is_captured_pre_mutation_with_master_flagged(
        self, db_session_factory
    ):
        _, _, _, execution_id = self._attach_run(db_session_factory)

        session = db_session_factory()
        try:
            snapshot = session.query(ChannelPipelineSnapshot).filter(
                ChannelPipelineSnapshot.execution_id == execution_id
            ).first()
            assert snapshot is not None
            by_id = {c["id"]: c
                     for c in snapshot.get_channels_data()["channels"]}
            # Master captured PRE-mutation (attachment 7001 absent) and
            # flagged for the streams-only restore.
            assert by_id[100]["stream_ids"] == [9001]
            assert by_id[100]["event_sync_master"] is True
            # Manual channel captured unflagged (standard restore payload).
            assert by_id[50]["stream_ids"] == [601]
            assert "event_sync_master" not in by_id[50]
        finally:
            session.close()

    def test_rollback_without_confirm_is_refused_with_the_overwrite_warning(
        self, db_session_factory
    ):
        engine, state, _, execution_id = self._attach_run(db_session_factory)

        with patch("channel_pipeline_engine.get_session",
                   side_effect=db_session_factory):
            result = _run(engine.rollback_execution(execution_id))

        assert result["success"] is False
        assert result["requires_confirm"] is True
        assert result["has_snapshot"] is True
        # Nothing was touched by the refusal.
        assert state.stream_ids_of(100) == [9001, 7001]

    def test_confirmed_rollback_removes_attachments_and_preserves_masters(
        self, db_session_factory
    ):
        engine, state, client, execution_id = self._attach_run(
            db_session_factory
        )
        pre_rollback_patch_count = len(state.update_channel_calls)

        with patch("channel_pipeline_engine.get_session",
                   side_effect=db_session_factory):
            result = _run(engine.rollback_execution(
                execution_id, confirm=True
            ))

        assert result["success"] is True
        assert result["failed_channels"] == []
        assert result["restored_channels"] == 2
        # ECM never created the master channels, so rollback never deletes
        # them: the channel survives with its own (pre-run) stream intact.
        assert result["removed_channels"] == 0
        client.delete_channel.assert_not_awaited()
        assert 100 in state.channels
        assert state.stream_ids_of(100) == [9001]  # attachment 7001 removed
        assert state.stream_ids_of(50) == [601]

        # Payload shapes: STREAMS-ONLY for the flagged master; the full
        # §D8 payload (byte-identical standard path) for the manual channel.
        restore_calls = dict(
            state.update_channel_calls[pre_rollback_patch_count:]
        )
        assert restore_calls[100] == {"streams": [9001]}
        assert restore_calls[50] == {
            "streams": [601],
            "name": "My Manual",
            "channel_group_id": 3,
            "epg_data_id": 88,
            "tvg_id": "man.1",
        }

        session = db_session_factory()
        try:
            execution = session.get(ChannelPipelineExecution, execution_id)
            assert execution.status == "rolled_back"
        finally:
            session.close()

        assert_never_touched_group_settings(client)

    def test_master_metadata_drift_survives_the_rollback(
        self, db_session_factory
    ):
        """The scenario the streams-only payload exists for: Dispatcharr
        renames the master in place AFTER the run (slot rename) — the
        rollback removes the run's attachment but does NOT revert the
        channel to its stale pre-run name."""
        engine, state, client, execution_id = self._attach_run(
            db_session_factory
        )
        renamed = "Peacock 14: Mercury vs. Aces FINAL @ 11 Jul 06:00 PM ET"
        state.master_refresh(renames={100: renamed})

        with patch("channel_pipeline_engine.get_session",
                   side_effect=db_session_factory):
            result = _run(engine.rollback_execution(
                execution_id, confirm=True
            ))

        assert result["success"] is True
        assert state.stream_ids_of(100) == [9001]
        # Dispatcharr-owned metadata untouched by the revert.
        assert state.channels[100]["name"] == renamed


class TestRestorePayloadShapes:
    """restore_snapshot unit-level: the flag selects the payload; its
    absence (legacy snapshots, standard-rule runs) keeps the full payload."""

    def _seeded_execution(self, session_factory, channels_data: list) -> int:
        session = session_factory()
        try:
            execution = ChannelPipelineExecution(
                mode="execute", triggered_by="manual",
                started_at=datetime.utcnow(), status="completed",
            )
            session.add(execution)
            session.commit()
            session.refresh(execution)
            snapshot = ChannelPipelineSnapshot(
                execution_id=execution.id,
                snapshot_time=datetime.utcnow(),
                channel_count=len(channels_data),
            )
            snapshot.set_channels_data({"channels": channels_data})
            session.add(snapshot)
            session.commit()
            return execution.id
        finally:
            session.close()

    def test_unflagged_legacy_snapshot_channel_keeps_full_payload(
        self, db_session_factory
    ):
        execution_id = self._seeded_execution(db_session_factory, [{
            "id": 100, "name": "Old Name", "channel_group_id": 10,
            "epg_data_id": 5, "tvg_id": "x.1", "stream_ids": [1, 2],
            # no event_sync_master flag — a pre-flag snapshot row
        }])
        client = MagicMock()
        client.update_channel = AsyncMock(return_value={})
        engine = ChannelPipelineEngine(client)

        with patch("channel_pipeline_engine.get_session",
                   side_effect=db_session_factory):
            result = _run(engine.restore_snapshot(execution_id))

        assert result["success"] is True
        client.update_channel.assert_awaited_once_with(100, {
            "streams": [1, 2], "name": "Old Name", "channel_group_id": 10,
            "epg_data_id": 5, "tvg_id": "x.1",
        })

    def test_flagged_master_channel_gets_streams_only_payload(
        self, db_session_factory
    ):
        execution_id = self._seeded_execution(db_session_factory, [{
            "id": 100, "name": "Old Name", "channel_group_id": 10,
            "epg_data_id": 5, "tvg_id": "x.1", "stream_ids": [1, 2],
            "event_sync_master": True,
        }])
        client = MagicMock()
        client.update_channel = AsyncMock(return_value={})
        engine = ChannelPipelineEngine(client)

        with patch("channel_pipeline_engine.get_session",
                   side_effect=db_session_factory):
            result = _run(engine.restore_snapshot(execution_id))

        assert result["success"] is True
        client.update_channel.assert_awaited_once_with(
            100, {"streams": [1, 2]}
        )


# ---------------------------------------------------------------------------
# No code path in the event_sync feature writes Dispatcharr group settings
# ---------------------------------------------------------------------------

# Writers of Dispatcharr group settings / group objects. If a new writer is
# added to dispatcharr_client, add it here.
GROUP_SETTINGS_WRITERS = frozenset({
    "update_m3u_group_settings",
    "bulk_update_m3u_group_settings",
    "update_channel_group",
    "create_channel_group",
    "delete_channel_group",
})
# The event_sync feature additionally never creates or deletes channels.
CHANNEL_LIFECYCLE_WRITERS = frozenset({"create_channel", "delete_channel"})


def _called_attribute_names(node: ast.AST) -> set[str]:
    """Every attribute name used as a call target under ``node``
    (``x.y(...)`` yields ``y``, whatever the receiver chain)."""
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            names.add(sub.func.attr)
    return names


def _function_node(tree: ast.AST, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    raise AssertionError(
        f"function {name!r} not found — if it was renamed, update this "
        f"no-group-writes gate to scan its replacement"
    )


class TestNoGroupSettingsWrites:
    """Static (AST) proof for bead ti939.2.3 verification 2: Phase 1 never
    toggles ``auto_channel_sync`` (guidance-only UI), so no event_sync code
    path may call a Dispatcharr group-settings writer — and consequently
    snapshot restore never needs to touch group settings either."""

    # Every function that participates in the event_sync execution path.
    ENGINE_FUNCTIONS = (
        "_fetch_event_sync_secondary_streams",
        "_run_event_sync_rules",
    )
    EXECUTOR_FUNCTIONS = (
        "_resolve_event_sync",
        "execute_event_sync_rule",
    )
    ROUTER_FUNCTIONS = (
        "preview_event_sync",
        "_load_event_sync_preview_config",
    )

    def _assert_no_writers(self, called: set[str], where: str) -> None:
        offenders = called & (GROUP_SETTINGS_WRITERS | CHANNEL_LIFECYCLE_WRITERS)
        assert not offenders, (
            f"{where} calls Dispatcharr group-settings/channel-lifecycle "
            f"writer(s) {sorted(offenders)} — the Phase 1 hard constraint "
            f"forbids this (ti939.2.3)"
        )

    def _scan_functions(self, path: Path, names: tuple) -> None:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in names:
            node = _function_node(tree, name)
            self._assert_no_writers(
                _called_attribute_names(node), f"{path.name}::{name}"
            )

    def test_engine_event_sync_phase_never_writes_group_settings(self):
        self._scan_functions(
            BACKEND_DIR / "channel_pipeline_engine.py", self.ENGINE_FUNCTIONS
        )

    def test_executor_attach_path_never_writes_group_settings(self):
        self._scan_functions(
            BACKEND_DIR / "channel_pipeline_executor.py",
            self.EXECUTOR_FUNCTIONS,
        )

    def test_preview_endpoint_never_writes_group_settings(self):
        self._scan_functions(
            BACKEND_DIR / "routers" / "channel_pipeline.py",
            self.ROUTER_FUNCTIONS,
        )

    def test_event_sync_service_modules_never_write_group_settings(self):
        """The service layer is stronger still: matcher and resolver are
        PURE (no client attribute calls at all); preflight performs exactly
        one READ (``get_all_m3u_group_settings``)."""
        services = BACKEND_DIR / "services"
        for module in ("event_sync_matcher.py", "event_sync_resolver.py",
                       "event_sync_preflight.py"):
            tree = ast.parse((services / module).read_text(encoding="utf-8"))
            self._assert_no_writers(
                _called_attribute_names(tree), f"services/{module}"
            )


# ---------------------------------------------------------------------------
# Circuit breaker covers event_sync runs
# ---------------------------------------------------------------------------


class TestCircuitBreakerCoversEventSync:
    """The channel-pipeline circuit breaker (bd-exo4j) and event_sync runs.

    The breaker trips when the startup crash-sentinel finds an execution
    abandoned mid-run (status left 'running' by a SIGKILL/OOM). event_sync
    runs create ordinary ChannelPipelineExecution rows, so they are covered
    by the SAME sentinel with no event_sync-specific carve-out. The tripped
    breaker gates the unattended auto-fire chain only — manual runs (the
    only way event_sync executes in Phase 1) are never gated, which is the
    designed recovery surface, not a gap.
    """

    def test_crashed_manual_event_sync_run_trips_the_breaker(
        self, test_session
    ):
        from task_engine import _abandon_orphaned_auto_creation_executions

        crashed = ChannelPipelineExecution(
            mode="execute", triggered_by="manual",
            started_at=datetime.utcnow(), status="running",
        )
        test_session.add(crashed)
        test_session.commit()
        crashed_id = crashed.id

        with patch("config.save_settings") as mock_save, \
             patch("config.get_settings", return_value=MagicMock(
                 auto_creation_run_on_refresh_disabled=False)):
            abandoned = _abandon_orphaned_auto_creation_executions(
                session=test_session
            )

        assert abandoned == 1
        test_session.expire_all()
        row = test_session.get(ChannelPipelineExecution, crashed_id)
        assert row.status == "abandoned"
        # The persisted breaker flag was written (tripped).
        mock_save.assert_called_once()
        assert (mock_save.call_args.args[0]
                .auto_creation_run_on_refresh_disabled is True)

    @pytest.mark.asyncio
    async def test_reset_endpoint_clears_a_tripped_breaker(self, async_client):
        settings = MagicMock(auto_creation_run_on_refresh_disabled=True)
        with patch("config.get_settings", return_value=settings), \
             patch("config.save_settings") as mock_save, \
             patch("journal.log_entry"):
            resp = await async_client.post(
                "/api/channel-pipeline/reset-circuit-breaker"
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data == {
            "success": True, "was_disabled": True, "disabled": False,
        }
        assert settings.auto_creation_run_on_refresh_disabled is False
        mock_save.assert_called_once()

    def test_tripped_breaker_gates_unattended_path_but_not_manual_event_sync(
        self, db_session_factory
    ):
        """With the breaker TRIPPED: the unattended task no-ops (belt), while
        a deliberate manual event_sync run still executes — manual "Run Now"
        is the operator's recovery surface and is never gated."""
        import os
        from tasks.channel_pipeline import ChannelPipelineTask

        _add_event_rule(db_session_factory)
        state = FakeDispatcharrState(
            channels=[{
                "id": 100, "name": MASTER_MERCURY,
                "channel_group_id": MASTER_GROUP_ID,
                "auto_created": True, "streams": [9001],
            }],
            secondary_streams={SECONDARY_GROUP_NAME: [
                {"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1},
            ]},
        )
        client = make_stateful_client(state)

        # (1) Unattended path: suppressed outright by the tripped breaker.
        tripped = MagicMock(
            auto_creation_run_on_refresh_disabled=True,
            last_m3u_refresh_completed_at="2026-01-02T00:00:00+00:00",
            last_auto_creation_consumed_refresh_at="2026-01-01T00:00:00+00:00",
        )
        os.environ.pop("ECM_DISABLE_RUN_ON_REFRESH", None)
        with patch("tasks.channel_pipeline.get_settings", return_value=tripped), \
             patch("services.notification_service.create_notification_internal",
                   new=AsyncMock()), \
             patch("journal.log_entry"):
            task = ChannelPipelineTask()
            task._enabled = True
            result = _run(task.execute())
        assert result.details.get("skipped") is True
        assert result.details.get("reason") == "circuit_breaker"
        assert state.update_channel_calls == []

        # (2) Manual run: attaches normally despite the tripped breaker.
        engine = ChannelPipelineEngine(client)
        with patch("channel_pipeline_engine.get_session",
                   side_effect=db_session_factory), \
             patch("journal.log_entries"):
            run_result = _run(engine.run_pipeline(
                dry_run=False, triggered_by="manual"
            ))
        assert run_result["success"] is True
        assert run_result["event_sync"][0]["attached"] == 1
        assert state.stream_ids_of(100) == [9001, 7001]
