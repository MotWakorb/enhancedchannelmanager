"""GH #1018: dry-run previews predict pending-merge deferrals, end to end.

Real ``pending_merges`` table in SQLite, the real ``ActionExecutor`` and the
real ``ChannelPipelineEngine.run_pipeline``, and for the response contract
the real ``POST /api/channel-pipeline/run`` + ``GET .../executions/{id}``
routes. Only Dispatcharr is doubled.

What it pins (issue #1018 acceptance criteria):

1. A dry run against an existing matching pending row predicts DEFERRAL,
   names that row's id, and predicts no channel creation.
2. Fresh (no row yet) and collision/reused (row already there, two streams
   sharing it) queue states give consistent predictions without mutation.
3. With no blocking match the create prediction is unchanged.
4. Coverage crosses persisted queue -> executor -> engine -> preview response.
5. The pending-merge table and its journal are semantically identical before
   and after every preview.
6. Preview and the subsequent live run agree on create-versus-defer, stream
   by stream, and on the blocking row ids, for identical inputs.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from channel_pipeline_engine import ChannelPipelineEngine
from models import ChannelPipelineExecution, PendingMerge, PendingMergeJournal
from tests.integration.test_gh1015_deferral_end_to_end import (  # noqa: F401 — fixture
    EXISTING_CHANNEL_ID,
    EXISTING_NAME,
    GROUP_ID,
    INCOMING_NAME,
    _dispatcharr,
    _two_providers_same_slot,
    wired_db,
)

PREDICTED_WARNING = "pending_merge_deferral_predicted"


def _near_duplicate_client():
    return _dispatcharr(
        existing_channels=[{
            "id": EXISTING_CHANNEL_ID, "name": EXISTING_NAME,
            "channel_group_id": GROUP_ID, "streams": [],
        }],
        streams=_two_providers_same_slot(),
    )


def _queue_snapshot(SessionLocal) -> tuple[list[tuple], list[tuple]]:
    """Every column of every pending_merges and journal row, id order."""
    session = SessionLocal()
    try:
        rows = [
            tuple(getattr(r, c.name) for c in PendingMerge.__table__.columns)
            for r in session.query(PendingMerge).order_by(PendingMerge.id).all()
        ]
        journal = [
            tuple(getattr(r, c.name) for c in PendingMergeJournal.__table__.columns)
            for r in session.query(PendingMergeJournal).order_by(PendingMergeJournal.id).all()
        ]
    finally:
        session.close()
    return rows, journal


def _create_decisions(result: dict) -> dict[int, tuple[bool, int | None]]:
    """Per stream: (deferred?, blocking row id) for every create_channel action.

    Read from the execution log, which both modes write in the same shape,
    so the live and preview decisions are compared on the same units.
    """
    decisions: dict[int, tuple[bool, int | None]] = {}
    for entry in result["execution_log"]:
        for action in entry.get("actions_executed") or []:
            if action.get("type") != "create_channel":
                continue
            decisions[entry["stream_id"]] = (
                bool(action.get("pending_merge_deferred")),
                action.get("pending_merge_id"),
            )
    return decisions


async def _run(client, *, dry_run: bool, triggered_by: str = "m3u_refresh") -> dict:
    return await ChannelPipelineEngine(client).run_pipeline(
        dry_run=dry_run, triggered_by=triggered_by,
    )


@pytest.mark.asyncio
async def test_preview_predicts_deferral_behind_the_persisted_row(wired_db):
    client = _near_duplicate_client()
    live_first = await _run(client, dry_run=False)          # persists the row
    before = _queue_snapshot(wired_db)
    (row_id,) = [r[0] for r in before[0]]
    assert live_first["pending_merge_ids"] == [row_id]

    preview = await _run(client, dry_run=True)

    assert _queue_snapshot(wired_db) == before                # AC5
    client.create_channel.assert_not_awaited()
    assert preview["channels_created"] == 0                   # AC1: no create predicted
    # Same three units the live report uses (#1015): actions, streams, rows.
    assert preview["pending_merges_added"] == 2
    assert preview["pending_merge_stream_count"] == 2
    assert preview["pending_merge_ids"] == [row_id]
    assert preview["pending_merge_new_row_count"] == 0

    creates = [r for r in preview["dry_run_results"] if r.get("would_defer")]
    assert {r["stream_id"] for r in creates} == {201, 202}
    for r in creates:
        assert r["would_create"] is False
        assert r["pending_merge_id"] == row_id
        assert r["action"].startswith("PREDICTED")
        assert f"pending merge row id={row_id}" in r["action"]
    assert not any(r["would_create"] for r in preview["dry_run_results"])

    # The follow-on assign_epg explains the predicted deferral in predicted terms.
    epg = [
        a for e in preview["execution_log"] for a in e["actions_executed"]
        if a["type"] == "assign_epg"
    ]
    assert len(epg) == 2
    for failure in epg:
        assert "would be deferred" in failure["error"]
        assert str(row_id) in failure["error"]

    # The persisted dry-run execution carries a predicted, not applied, warning.
    session = wired_db()
    try:
        execution = session.get(ChannelPipelineExecution, preview["execution_id"])
        warnings = execution.get_warnings()
    finally:
        session.close()
    (warning,) = [w for w in warnings if w.get("type") == PREDICTED_WARNING]
    assert warning["pending_merge_ids"] == [row_id]
    assert warning["stream_count"] == 2
    assert warning["action_count"] == 2
    assert warning["new_row_count"] == 0
    assert warning["message"].startswith("PREDICTED")
    assert "2 streams would be deferred by pending merges" in warning["message"]
    assert f"rows: {row_id}" in warning["message"]


@pytest.mark.asyncio
async def test_fresh_queue_preview_predicts_a_new_row_and_the_live_run_agrees(wired_db):
    client = _near_duplicate_client()
    before = _queue_snapshot(wired_db)
    assert before == ([], [])

    preview = await _run(client, dry_run=True)

    assert _queue_snapshot(wired_db) == before                # nothing queued
    assert preview["channels_created"] == 0
    assert preview["pending_merges_added"] == 2
    assert preview["pending_merge_stream_count"] == 2
    assert preview["pending_merge_ids"] == []                 # no row exists yet
    assert preview["pending_merge_new_row_count"] == 1        # both share one new row
    predicted = _create_decisions(preview)
    assert predicted == {201: (True, None), 202: (True, None)}

    live = await _run(client, dry_run=False)

    client.create_channel.assert_not_awaited()
    actual = _create_decisions(live)
    assert {s: d for s, (d, _) in actual.items()} == {s: d for s, (d, _) in predicted.items()}
    assert len(live["pending_merge_ids"]) == preview["pending_merge_new_row_count"]


@pytest.mark.asyncio
async def test_collision_queue_preview_and_live_run_agree_on_decision_and_rows(wired_db):
    client = _near_duplicate_client()
    await _run(client, dry_run=False)                         # seed the row
    before = _queue_snapshot(wired_db)

    preview = await _run(client, dry_run=True)
    assert _queue_snapshot(wired_db) == before
    live = await _run(client, dry_run=False)

    assert _queue_snapshot(wired_db)[0] == before[0]          # live collided, no insert
    assert _create_decisions(preview) == _create_decisions(live)
    assert preview["pending_merge_ids"] == live["pending_merge_ids"]
    assert preview["pending_merge_stream_count"] == live["pending_merge_stream_count"]
    assert preview["pending_merges_added"] == live["pending_merges_added"]


@pytest.mark.asyncio
async def test_no_blocker_preview_keeps_the_create_prediction(wired_db):
    client = _dispatcharr(
        existing_channels=[{
            "id": EXISTING_CHANNEL_ID, "name": "BBC News",
            "channel_group_id": GROUP_ID, "streams": [],
        }],
        streams=[{"id": 201, "name": INCOMING_NAME, "channel_group": GROUP_ID, "m3u_account": 1}],
    )
    before = _queue_snapshot(wired_db)

    preview = await _run(client, dry_run=True)

    assert _queue_snapshot(wired_db) == before
    assert preview["channels_created"] == 1
    assert preview["pending_merges_added"] == 0
    assert preview["pending_merge_ids"] == []
    (create,) = [r for r in preview["dry_run_results"] if r["would_create"]]
    assert create["stream_id"] == 201
    assert "would_defer" not in create
    session = wired_db()
    try:
        warnings = session.get(ChannelPipelineExecution, preview["execution_id"]).get_warnings()
    finally:
        session.close()
    assert not any(w.get("type") == PREDICTED_WARNING for w in warnings)
    assert _create_decisions(preview) == {201: (False, None)}


@pytest.mark.asyncio
async def test_api_trigger_preview_matches_a_live_api_run_which_never_defers(wired_db):
    """The queue only blocks the M3U-refresh path. A dry run under the API
    trigger must therefore keep predicting creation even with a blocking row,
    because a live API run with the same inputs creates the channel."""
    client = _near_duplicate_client()
    await _run(client, dry_run=False)                         # seed the row
    before = _queue_snapshot(wired_db)

    preview = await _run(client, dry_run=True, triggered_by="api")
    assert _queue_snapshot(wired_db) == before
    assert preview["pending_merges_added"] == 0
    assert preview["channels_created"] == 1

    # Echo the payload (group included) so the second same-named stream finds
    # the channel the first one created, as it would against Dispatcharr.
    client.create_channel = AsyncMock(side_effect=lambda data: {"id": 555, **data})
    client.get_epg_data = AsyncMock(return_value=[])
    client.get_epg_sources = AsyncMock(return_value=[])
    live = await _run(client, dry_run=False, triggered_by="api")
    assert live["channels_created"] == 1
    assert live["pending_merges_added"] == 0
    assert {s: d for s, (d, _) in _create_decisions(preview).items()} == \
        {s: d for s, (d, _) in _create_decisions(live).items()}


async def _wait_terminal(async_client, execution_id: int) -> dict:
    for _ in range(400):
        response = await async_client.get(f"/api/channel-pipeline/executions/{execution_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] not in ("running", "pending"):
            return body
        await asyncio.sleep(0.01)
    raise AssertionError(f"execution {execution_id} never reached a terminal status")


@pytest.mark.asyncio
async def test_run_endpoint_refresh_preview_returns_the_predicted_deferral(wired_db, async_client):
    """Persisted queue -> router -> engine -> executor -> persisted execution
    -> GET response, the surface the UI and API callers read."""
    client = _near_duplicate_client()
    await _run(client, dry_run=False)
    before = _queue_snapshot(wired_db)
    (row_id,) = [r[0] for r in before[0]]

    engine = ChannelPipelineEngine(client)
    with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=engine):
        response = await async_client.post(
            "/api/channel-pipeline/run",
            json={"dry_run": True, "preview_trigger": "m3u_refresh"},
        )
        assert response.status_code == 202, response.text
        body = await _wait_terminal(async_client, response.json()["execution_id"])

    assert _queue_snapshot(wired_db) == before
    client.create_channel.assert_not_awaited()
    assert body["mode"] == "dry_run"
    assert body["triggered_by"] == "m3u_refresh"
    assert body["channels_created"] == 0
    (warning,) = [w for w in body["warnings"] if w.get("type") == PREDICTED_WARNING]
    assert warning["pending_merge_ids"] == [row_id]
    assert warning["message"].startswith("PREDICTED")
    deferred = [r for r in body["dry_run_results"] if r.get("would_defer")]
    assert {r["pending_merge_id"] for r in deferred} == {row_id}


@pytest.mark.asyncio
@pytest.mark.parametrize("path, extra", [
    ("/api/channel-pipeline/run", {}),
    ("/api/channel-pipeline/run-selected", {"rule_ids": [1]}),
])
async def test_preview_trigger_is_refused_for_a_live_run(async_client, path, extra):
    response = await async_client.post(
        path, json={"dry_run": False, "preview_trigger": "m3u_refresh", **extra},
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_preview_trigger_rejects_unknown_values(async_client):
    response = await async_client.post(
        "/api/channel-pipeline/run",
        json={"dry_run": True, "preview_trigger": "scheduled_all"},
    )
    assert response.status_code == 422, response.text
