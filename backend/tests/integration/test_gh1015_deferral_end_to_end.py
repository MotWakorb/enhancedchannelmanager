"""GH #1015 / PR #1016: deferral visibility, end to end through real code.

The unit and formatter tests around this feature each prove one boundary
with the neighbours mocked. This module removes the mocks: a real
``pending_merges`` table in SQLite, the real ``ActionExecutor`` and BD-F
hook, the real ``ChannelPipelineEngine.run_pipeline``, the real
``ChannelPipelineTask`` post-refresh path, and the real ``TaskEngine``
completion notification, with only Dispatcharr (``client``) and the
notification sink doubled.

What it pins, per the PR #1016 acceptance matrix:

* the single *Completed with Warnings* notification an unattended
  failed-action run emits names the deferral and the blocking row ids, and
  those ids are the ids of the rows actually persisted in ``pending_merges``
  — on the fresh-insert refresh AND on the next refresh, where every row is
  a §D5 collision against the one already there;
* the three counters keep their units apart when two streams share one
  pending row (same name from two providers): two deferred create actions,
  two streams, one row — in the engine result, the task details and the
  warning metadata;
* no channel is created for a deferred stream, and its follow-on
  ``assign_epg`` reports the deferral rather than a bare missing channel.
"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

import channel_pipeline_engine as engine_module
import database
import task_registry
from channel_pipeline_engine import ChannelPipelineEngine, set_channel_pipeline_engine
from models import ChannelPipelineExecution, ChannelPipelineRule, PendingMerge, ScheduledTask
from task_engine import TaskEngine
from task_scheduler import ScheduleConfig, ScheduleType
from tasks.channel_pipeline import ChannelPipelineTask

GROUP_ID = 42
EXISTING_CHANNEL_ID = 100
# A genuine near-duplicate (no airing in either name, so the GH #1015 gate
# stays out of the way): the quality suffix is the only difference and the
# pair scores well above the 0.80 default.
EXISTING_NAME = "Sky Sports F1"
INCOMING_NAME = "Sky Sports F1 HD"


@pytest.fixture
def wired_db(test_engine, monkeypatch):
    """Point every ``get_session`` in the path at the test SQLite engine."""
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", SessionLocal)
    monkeypatch.setattr(engine_module, "get_session", SessionLocal)
    session = SessionLocal()
    try:
        session.query(PendingMerge).delete()
        session.query(ChannelPipelineRule).delete()
        session.query(ChannelPipelineExecution).delete()
        session.query(ScheduledTask).filter(
            ScheduledTask.task_id == ChannelPipelineTask.task_id
        ).delete()
        session.add(ChannelPipelineRule(
            name="Slots",
            enabled=True,
            priority=0,
            run_on_refresh=True,
            target_group_id=GROUP_ID,
            conditions=json.dumps([{"type": "always"}]),
            actions=json.dumps([
                {"type": "create_channel", "name_template": "{stream_name}", "group_id": GROUP_ID},
                {"type": "assign_epg", "epg_id": 5},
            ]),
            orphan_action="none",
        ))
        session.add(ScheduledTask(
            task_id=ChannelPipelineTask.task_id,
            task_name=ChannelPipelineTask.task_name,
            description="test",
            enabled=True,
            schedule_type="manual",
            send_alerts=True,
            alert_on_success=True,
            alert_on_warning=True,
            alert_on_error=True,
            show_notifications=True,
        ))
        session.commit()
    finally:
        session.close()
    return SessionLocal


def _dispatcharr(existing_channels: list[dict], streams: list[dict]) -> MagicMock:
    client = MagicMock()
    client.get_channels = AsyncMock(
        return_value={"count": len(existing_channels), "results": existing_channels}
    )
    client.get_channel_groups = AsyncMock(return_value=[{"id": GROUP_ID, "name": "Sports"}])
    client.get_m3u_accounts = AsyncMock(return_value=[
        {"id": 1, "name": "Provider A"}, {"id": 2, "name": "Provider B"},
    ])

    async def get_streams(page=1, page_size=1000, m3u_account=None, **_):
        rows = [s for s in streams if s["m3u_account"] == m3u_account]
        return {"count": len(rows), "results": rows}

    client.get_streams = AsyncMock(side_effect=get_streams)
    client.create_channel = AsyncMock(
        side_effect=AssertionError("a deferred stream must not create a channel")
    )
    client.update_channel = AsyncMock()
    return client


def _two_providers_same_slot() -> list[dict]:
    return [
        {"id": 201, "name": INCOMING_NAME, "channel_group": GROUP_ID, "m3u_account": 1},
        {"id": 202, "name": INCOMING_NAME, "channel_group": GROUP_ID, "m3u_account": 2},
    ]


@pytest.fixture
def registered_pipeline_task():
    registry = task_registry.get_registry()
    already = ChannelPipelineTask.task_id in getattr(registry, "_tasks", {})
    if not already:
        registry.register(ChannelPipelineTask)
    instance = ChannelPipelineTask(ScheduleConfig(schedule_type=ScheduleType.MANUAL))
    instance._enabled = True
    previous = registry._instances.get(ChannelPipelineTask.task_id)
    registry._instances[ChannelPipelineTask.task_id] = instance
    yield instance
    if previous is not None:
        registry._instances[ChannelPipelineTask.task_id] = previous
    else:
        registry._instances.pop(ChannelPipelineTask.task_id, None)
    if not already:
        registry.unregister(ChannelPipelineTask.task_id)


async def _refresh_once(client, monkeypatch, refresh_stamp: str) -> tuple[list[dict], dict | None]:
    """Drive one M3U-refresh tick through the REAL task engine.

    Returns every notification the run emitted and the task-engine
    completion notification (title starts with ``Task Completed``).
    """
    from config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "last_m3u_refresh_completed_at", refresh_stamp)
    monkeypatch.setattr(settings, "last_auto_creation_consumed_refresh_at", "")
    monkeypatch.setattr(settings, "auto_creation_run_on_refresh_disabled", False, raising=False)
    monkeypatch.delenv("ECM_DISABLE_RUN_ON_REFRESH", raising=False)

    set_channel_pipeline_engine(ChannelPipelineEngine(client))
    task_engine = TaskEngine()
    task_engine._create_notification_callback = AsyncMock(return_value={"id": 1})
    task_engine._update_notification_callback = AsyncMock()
    task_engine._delete_notification_callback = AsyncMock()

    notify = AsyncMock(return_value={"id": 2})
    try:
        with patch("services.notification_service.create_notification_internal", new=notify), \
             patch("tasks.channel_pipeline.get_client", return_value=client), \
             patch("tasks.channel_pipeline.save_settings"), \
             patch("task_engine.log_entry"):
            await task_engine._execute_task(
                task_id=ChannelPipelineTask.task_id, triggered_by="test"
            )
    finally:
        set_channel_pipeline_engine(None)

    emitted = [call.kwargs for call in notify.await_args_list]
    completion = [
        n for n in emitted if str(n.get("title", "")).startswith("Task Completed")
    ]
    return emitted, (completion[-1] if completion else None)


@pytest.mark.asyncio
async def test_fresh_and_collision_refreshes_both_name_the_persisted_row(
    wired_db, monkeypatch, registered_pipeline_task
):
    client = _dispatcharr(
        existing_channels=[{
            "id": EXISTING_CHANNEL_ID, "name": EXISTING_NAME,
            "channel_group_id": GROUP_ID, "streams": [],
        }],
        streams=_two_providers_same_slot(),
    )

    # --- refresh 1: stream 201 inserts the row, stream 202 collides on it ---
    emitted, warning = await _refresh_once(client, monkeypatch, "2026-09-20T10:00:00Z")

    session = wired_db()
    try:
        rows = session.query(PendingMerge).all()
        execution = (
            session.query(ChannelPipelineExecution)
            .order_by(ChannelPipelineExecution.id.desc()).first()
        )
    finally:
        session.close()

    assert len(rows) == 1, [r.__dict__ for r in rows]
    row = rows[0]
    assert row.stream_name == INCOMING_NAME
    assert row.candidate_channel_id == str(EXISTING_CHANNEL_ID)
    assert row.status == "pending"
    client.create_channel.assert_not_awaited()

    # The run is a failed-action run (assign_epg had no channel), so the ONE
    # notification the task engine emits is the warning — and it must name
    # the cause and the persisted row, not just "Completed with N failures".
    assert warning is not None, [n.get("title") for n in emitted]
    assert warning["notification_type"] == "warning"
    assert warning["title"].startswith("Task Completed with Warnings")
    assert warning["message"].startswith("Completed with 2 failures out of 2 items.")
    assert "2 streams deferred by pending merges" in warning["message"]
    assert f"rows: {row.id}" in warning["message"]
    assert "Pending Merges" in warning["message"]
    meta = warning["metadata"]
    assert meta["pending_merge_ids"] == [row.id]
    assert meta["pending_merge_stream_count"] == 2
    assert meta["pending_merges_added"] == 2
    # No competing success/info toast for the same run.
    assert not any(
        str(n.get("title", "")).startswith("Auto-Creation:") and n.get("notification_type") == "success"
        for n in emitted
    )

    # The persisted execution agrees with the notification.
    assert execution is not None
    assert execution.status == "completed_with_errors"
    log = execution.get_execution_log()
    epg_failures = [
        a for entry in log for a in (entry.get("actions_executed") or [])
        if a.get("type") == "assign_epg"
    ]
    assert len(epg_failures) == 2, log
    for failure in epg_failures:
        assert failure["success"] is False
        assert "deferred" in (failure.get("error") or ""), failure
        assert str(row.id) in (failure.get("error") or ""), failure

    # --- refresh 2: the row already exists; both streams hit the §D5 branch ---
    emitted2, warning2 = await _refresh_once(client, monkeypatch, "2026-09-20T11:00:00Z")

    session = wired_db()
    try:
        rows_after = session.query(PendingMerge).all()
    finally:
        session.close()
    assert [r.id for r in rows_after] == [row.id]  # nothing new inserted
    client.create_channel.assert_not_awaited()
    assert warning2 is not None, [n.get("title") for n in emitted2]
    assert "2 streams deferred by pending merges" in warning2["message"]
    assert f"rows: {row.id}" in warning2["message"]
    assert warning2["metadata"]["pending_merge_ids"] == [row.id]
    assert warning2["metadata"]["pending_merge_stream_count"] == 2


@pytest.mark.asyncio
async def test_engine_result_keeps_streams_actions_and_rows_distinct_for_a_shared_row(
    wired_db, monkeypatch
):
    """Two streams, one row: the engine result — not a hand-built dict — is
    what the task and the warning read, so its units are asserted here."""
    client = _dispatcharr(
        existing_channels=[{
            "id": EXISTING_CHANNEL_ID, "name": EXISTING_NAME,
            "channel_group_id": GROUP_ID, "streams": [],
        }],
        streams=_two_providers_same_slot(),
    )
    engine = ChannelPipelineEngine(client)
    result = await engine.run_pipeline(dry_run=False, triggered_by="m3u_refresh")

    session = wired_db()
    try:
        row_ids = [r.id for r in session.query(PendingMerge).all()]
    finally:
        session.close()

    assert len(row_ids) == 1
    assert result["channels_created"] == 0
    assert result["pending_merges_added"] == 2           # deferred create ACTIONS
    assert result["pending_merge_stream_count"] == 2     # distinct STREAMS
    assert result["pending_merge_ids"] == row_ids        # distinct ROWS, persisted ids
    assert "pending_merge_stream_ids" not in result      # JSON-safe
    assert result["status"] == "completed_with_errors"
    assert result["failed_action_count"] == 2


@pytest.mark.asyncio
async def test_a_refresh_with_nothing_deferred_emits_no_deferral_wording(
    wired_db, monkeypatch, registered_pipeline_task
):
    """Control: a group with no near-duplicate creates normally and the
    completion notification carries no deferral clause or row ids."""
    client = _dispatcharr(
        existing_channels=[{
            "id": EXISTING_CHANNEL_ID, "name": "BBC News",
            "channel_group_id": GROUP_ID, "streams": [],
        }],
        streams=[{"id": 201, "name": INCOMING_NAME, "channel_group": GROUP_ID, "m3u_account": 1}],
    )
    client.create_channel = AsyncMock(return_value={"id": 555, "name": INCOMING_NAME})
    # The engine iterates these directly (a flat list of entry dicts).
    client.get_epg_data = AsyncMock(return_value=[])
    client.get_epg_sources = AsyncMock(return_value=[])

    emitted, completion = await _refresh_once(client, monkeypatch, "2026-09-20T12:00:00Z")

    session = wired_db()
    try:
        assert session.query(PendingMerge).count() == 0
    finally:
        session.close()
    client.create_channel.assert_awaited_once()
    assert completion is not None, [n.get("title") for n in emitted]
    assert "deferred" not in completion["message"]
    assert "pending_merge_ids" not in completion.get("metadata", {})


@pytest.mark.asyncio
async def test_a_capped_failed_deferred_refresh_names_the_rows_in_its_single_warning(
    wired_db, monkeypatch, registered_pipeline_task,
):
    """PR #1016 review round 3: when the run is ALSO capped, the task emits its
    own combined cap/error warning and suppresses the task engine's generic
    one. That single notification must still carry the deferral cause and the
    persisted row ids. Real queue, executor, engine, task and cap logic; only
    Dispatcharr and the notification sink are doubled."""
    from config import get_settings
    monkeypatch.setattr(get_settings(), "max_auto_created_channels_per_run", 1)

    client = _dispatcharr(
        existing_channels=[{
            "id": EXISTING_CHANNEL_ID, "name": EXISTING_NAME,
            "channel_group_id": GROUP_ID, "streams": [],
        }],
        streams=[
            # Deferred behind the near-duplicate (processed first: creates nothing).
            {"id": 201, "name": INCOMING_NAME, "channel_group": GROUP_ID, "m3u_account": 1},
            # Creates (count 1 == cap), then the third stream trips the cap.
            {"id": 202, "name": "BBC One", "channel_group": GROUP_ID, "m3u_account": 1},
            {"id": 203, "name": "BBC Two", "channel_group": GROUP_ID, "m3u_account": 1},
        ],
    )
    created_ids = iter([555, 556])
    client.create_channel = AsyncMock(
        side_effect=lambda data: {"id": next(created_ids), **data}
    )
    client.get_epg_data = AsyncMock(return_value=[])
    client.get_epg_sources = AsyncMock(return_value=[])

    emitted, completion = await _refresh_once(client, monkeypatch, "2026-09-20T13:00:00Z")

    session = wired_db()
    try:
        rows = session.query(PendingMerge).all()
        execution = (
            session.query(ChannelPipelineExecution)
            .order_by(ChannelPipelineExecution.id.desc()).first()
        )
    finally:
        session.close()
    assert [r.stream_name for r in rows] == [INCOMING_NAME]
    row_id = rows[0].id
    assert client.create_channel.await_count == 1                 # capped after one create
    assert execution.status == "capped"

    # Exactly one warning, the task's own combined one; the engine's generic
    # "Task Completed with Warnings" is suppressed.
    warnings = [n for n in emitted if n.get("notification_type") == "warning"]
    assert len(warnings) == 1, [n.get("title") for n in emitted]
    assert completion is None or not str(completion.get("title", "")).startswith("Task Completed with Warnings")
    cap = warnings[0]
    assert cap["title"] == "Auto-Creation: Capped, with errors"
    assert "capped at 1 of ~2" in cap["message"]
    assert "1 stream deferred by pending merges" in cap["message"]
    assert f"rows: {row_id}" in cap["message"]
    assert "Pending Merges" in cap["message"]
    assert cap["metadata"]["pending_merge_ids"] == [row_id]
    assert cap["metadata"]["pending_merge_stream_count"] == 1
