"""GH #1018 review-fix tests: parity under fuzzy scores and candidate sets,
the ``/run-selected`` ``preview_trigger`` success path, and the live-run
guard on the PREDICTED warning.

Each test here exists because a one-line breakage left the earlier suite
green (every earlier fixture scored 1.0 against a single candidate):

* F1 -- the executor's dry-run branch passing ``threshold + 0.19`` or a
  truncated candidate list to ``predict_pending_merge``.
* F2 -- ``/run-selected`` hard-coding ``triggered_by = "api"``.
* F3 -- the engine emitting the PREDICTED warning on a live run.
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from channel_pipeline_engine import ChannelPipelineEngine
from models import ChannelPipelineExecution, ChannelPipelineRule, PendingMerge
from tests.integration.test_gh1015_deferral_end_to_end import (  # noqa: F401 -- fixture
    GROUP_ID,
    _dispatcharr,
    wired_db,
)
from tests.integration.test_gh1018_preview_deferral_end_to_end import (
    PREDICTED_WARNING,
    _create_decisions,
    _near_duplicate_client,
    _queue_snapshot,
    _run,
    _wait_terminal,
)

# token_set_ratio scores, computed with services.dedup_matcher.find_candidate
# (floor 0.60). Plain suffixes ("HD", "Plus", "Two") score 1.0 because the
# shorter name is a token subset, so these use a one-letter typo instead.
STREAM = "Discovery Channel"
BEST_ID, BEST_NAME = 101, "Discovery Chanel"      # 0.9697  (the better candidate)
WEAK_ID, WEAK_NAME = 100, "Discovery Chanl"       # 0.9375  (the decoy)
JUST_ABOVE_BEST = 0.96   # both candidates clear it; best wins on score
JUST_BELOW_BEST = 0.98   # neither clears it -> create


def _fuzzy_client():
    return _dispatcharr(
        existing_channels=[
            {"id": WEAK_ID, "name": WEAK_NAME, "channel_group_id": GROUP_ID, "streams": []},
            {"id": BEST_ID, "name": BEST_NAME, "channel_group_id": GROUP_ID, "streams": []},
        ],
        streams=[{"id": 301, "name": STREAM, "channel_group": GROUP_ID, "m3u_account": 1}],
    )


def _set_threshold(monkeypatch, value: float) -> None:
    from config import get_settings

    monkeypatch.setattr(get_settings(), "dedup_threshold", value, raising=False)


def _seed_decoy_row(SessionLocal) -> int:
    """A pending row for the WEAK candidate, inserted first so it has the
    lower id. A preview that picks the wrong candidate reports this id."""
    session = SessionLocal()
    try:
        row = PendingMerge(
            stream_name=STREAM, group_id=GROUP_ID, candidate_channel_id=str(WEAK_ID),
            confidence=0.9375, status="pending",
            created_at=int(time.time() * 1000), trigger_context="m3u_refresh",
        )
        session.add(row)
        session.commit()
        return int(row.id)
    finally:
        session.close()


def _row_for(SessionLocal, candidate_id: int) -> int:
    session = SessionLocal()
    try:
        (row,) = session.query(PendingMerge).filter(
            PendingMerge.candidate_channel_id == str(candidate_id),
            PendingMerge.status == "pending",
        ).all()
        return int(row.id)
    finally:
        session.close()


def test_fixture_scores_sit_strictly_between_floor_and_one():
    """Pins the fixture itself: a future matcher change that moves these
    scores must fail here rather than silently weaken the parity tests."""
    from services.dedup_matcher import CONFIDENCE_FLOOR, find_candidate

    best = find_candidate(STREAM, [(str(BEST_ID), BEST_NAME)], 0.6)
    weak = find_candidate(STREAM, [(str(WEAK_ID), WEAK_NAME)], 0.6)
    assert CONFIDENCE_FLOOR < weak.confidence < best.confidence < 1.0
    assert weak.confidence >= JUST_ABOVE_BEST - 0.03       # decoy qualifies at 0.93
    assert JUST_ABOVE_BEST < best.confidence < JUST_BELOW_BEST
    both = find_candidate(
        STREAM, [(str(WEAK_ID), WEAK_NAME), (str(BEST_ID), BEST_NAME)], JUST_ABOVE_BEST
    )
    assert both.candidate_channel_id == str(BEST_ID)


@pytest.mark.asyncio
async def test_fuzzy_score_just_above_threshold_preview_and_live_both_defer(wired_db, monkeypatch):
    client = _fuzzy_client()
    decoy_id = _seed_decoy_row(wired_db)

    # Threshold just above the DECOY's score: only BEST can match.
    _set_threshold(monkeypatch, JUST_ABOVE_BEST)
    before = _queue_snapshot(wired_db)
    preview = await _run(client, dry_run=True)
    assert _queue_snapshot(wired_db) == before
    assert _create_decisions(preview) == {301: (True, None)}   # new row, not the decoy
    assert decoy_id not in preview["pending_merge_ids"]

    live = await _run(client, dry_run=False)
    client.create_channel.assert_not_awaited()
    assert {s: d for s, (d, _) in _create_decisions(live).items()} == {301: True}
    best_row = _row_for(wired_db, BEST_ID)
    assert best_row != decoy_id
    assert live["pending_merge_ids"] == [best_row]

    # Second pass: the row now exists, preview and live both name it.
    preview_again = await _run(client, dry_run=True)
    live_again = await _run(client, dry_run=False)
    assert _create_decisions(preview_again) == {301: (True, best_row)}
    assert _create_decisions(live_again) == _create_decisions(preview_again)
    assert preview_again["pending_merge_ids"] == live_again["pending_merge_ids"] == [best_row]


@pytest.mark.asyncio
async def test_fuzzy_score_just_below_threshold_preview_and_live_both_create(wired_db, monkeypatch):
    _set_threshold(monkeypatch, JUST_BELOW_BEST)
    client = _fuzzy_client()
    decoy_id = _seed_decoy_row(wired_db)
    before = _queue_snapshot(wired_db)

    preview = await _run(client, dry_run=True)
    assert _queue_snapshot(wired_db) == before
    assert preview["channels_created"] == 1
    assert preview["pending_merges_added"] == 0
    assert _create_decisions(preview) == {301: (False, None)}

    client.create_channel = AsyncMock(side_effect=lambda data: {"id": 555, **data})
    client.get_epg_data = AsyncMock(return_value=[])
    client.get_epg_sources = AsyncMock(return_value=[])
    live = await _run(client, dry_run=False)
    assert live["channels_created"] == 1
    assert live["pending_merges_added"] == 0
    assert _create_decisions(live) == _create_decisions(preview)
    assert decoy_id not in live["pending_merge_ids"]


@pytest.mark.asyncio
async def test_two_candidates_preview_reports_the_better_candidates_row(wired_db, monkeypatch):
    """The chosen candidate decides the reported row id: with a decoy row
    queued for the weaker candidate, preview and live must both report the
    row for the better one."""
    _set_threshold(monkeypatch, JUST_ABOVE_BEST)
    client = _fuzzy_client()
    decoy_id = _seed_decoy_row(wired_db)
    await _run(client, dry_run=False)                      # live inserts the BEST row
    best_row = _row_for(wired_db, BEST_ID)
    before = _queue_snapshot(wired_db)

    preview = await _run(client, dry_run=True)
    live = await _run(client, dry_run=False)

    assert _queue_snapshot(wired_db)[0] == before[0]
    assert preview["pending_merge_ids"] == [best_row]
    assert decoy_id != best_row
    assert _create_decisions(preview) == {301: (True, best_row)}
    assert _create_decisions(live) == _create_decisions(preview)


# --------------------------------------------------------------------- F3

@pytest.mark.asyncio
async def test_live_refresh_run_that_defers_never_carries_the_predicted_warning(wired_db):
    client = _near_duplicate_client()

    live = await _run(client, dry_run=False)               # m3u_refresh, really defers

    client.create_channel.assert_not_awaited()
    assert live["pending_merges_added"] == 2               # still reported the normal way
    assert live["pending_merge_stream_count"] == 2
    assert len(live["pending_merge_ids"]) == 1
    assert _create_decisions(live) == {
        201: (True, live["pending_merge_ids"][0]),
        202: (True, live["pending_merge_ids"][0]),
    }
    session = wired_db()
    try:
        warnings = session.get(ChannelPipelineExecution, live["execution_id"]).get_warnings()
    finally:
        session.close()
    assert not any(w.get("type") == PREDICTED_WARNING for w in warnings)
    assert not any(w.get("predicted") for w in warnings)
    assert "PREDICTED" not in json.dumps(warnings)


# --------------------------------------------------------------------- F2

@pytest.mark.asyncio
async def test_run_selected_refresh_preview_records_trigger_and_predicts_the_row(wired_db, async_client):
    client = _near_duplicate_client()
    await _run(client, dry_run=False)                      # persists the blocking row
    before = _queue_snapshot(wired_db)
    (row_id,) = [r[0] for r in before[0]]
    session = wired_db()
    try:
        rule_id = session.query(ChannelPipelineRule).one().id
    finally:
        session.close()

    engine = ChannelPipelineEngine(client)
    with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=engine):
        response = await async_client.post(
            "/api/channel-pipeline/run-selected",
            json={"dry_run": True, "preview_trigger": "m3u_refresh", "rule_ids": [rule_id]},
        )
        assert response.status_code == 202, response.text
        body = await _wait_terminal(async_client, response.json()["execution_id"])

    assert _queue_snapshot(wired_db) == before
    assert body["mode"] == "dry_run"
    assert body["triggered_by"] == "m3u_refresh"
    assert body["channels_created"] == 0
    (warning,) = [w for w in body["warnings"] if w.get("type") == PREDICTED_WARNING]
    assert warning["pending_merge_ids"] == [row_id]
    deferred = [r for r in body["dry_run_results"] if r.get("would_defer")]
    assert deferred and {r["pending_merge_id"] for r in deferred} == {row_id}
