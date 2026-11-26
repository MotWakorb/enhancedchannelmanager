"""GH #1018: the read-only pending-merge prediction behind dry-run previews.

A dry run must predict the same create-versus-defer decision the live
M3U-refresh hook makes, without touching the queue. Both paths share
:func:`find_pending_merge_candidate` (the trigger gate plus the matcher) and
:func:`find_authoritative_pending_row` (the §D5 lookup the live collision
branch already trusted). These tests pin that the prediction:

* defers against an existing pending row and names that row's id;
* defers with no id when a live run would insert a fresh row;
* ignores terminal (merged/dismissed) rows exactly as §D5 does;
* creates when nothing matches, or for any trigger other than m3u_refresh;
* writes nothing (rows and journal are semantically identical afterwards);
* agrees with the live hook, decision and row id, for the same queue state.
"""
from __future__ import annotations

import pytest

from models import PendingMerge, PendingMergeJournal
from services.m3u_dedup_hook import (
    PendingMergePrediction,
    check_and_enqueue_pending_merge,
    find_authoritative_pending_row,
    find_pending_merge_candidate,
    predict_pending_merge,
)

STREAM = "Sky Sports F1 HD"
CANDIDATES = [(100, "Sky Sports F1")]


def _queue_snapshot(session) -> tuple[list[tuple], list[tuple]]:
    """Every column of every queue and journal row, in id order."""
    session.expire_all()
    rows = [
        tuple(getattr(r, c.name) for c in PendingMerge.__table__.columns)
        for r in session.query(PendingMerge).order_by(PendingMerge.id).all()
    ]
    journal = [
        tuple(getattr(r, c.name) for c in PendingMergeJournal.__table__.columns)
        for r in session.query(PendingMergeJournal).order_by(PendingMergeJournal.id).all()
    ]
    return rows, journal


def _predict(session, *, stream=STREAM, candidates=CANDIDATES, triggered_by="m3u_refresh"):
    return predict_pending_merge(
        stream_name=stream,
        candidates=candidates,
        threshold=0.80,
        triggered_by=triggered_by,
        db_session=session,
    )


def _live(session, *, stream=STREAM, candidates=CANDIDATES):
    return check_and_enqueue_pending_merge(
        stream_name=stream,
        group_id=42,
        candidates=candidates,
        threshold=0.80,
        triggered_by="m3u_refresh",
        dry_run=False,
        db_session=session,
    )


def _seed_row(session, *, status="pending", stream=STREAM, candidate="100") -> PendingMerge:
    row = PendingMerge(
        stream_name=stream,
        group_id=42,
        candidate_channel_id=candidate,
        confidence=0.9,
        status=status,
        created_at=1000,
        resolved_at=None if status == "pending" else 2000,
        resolution_source=None if status == "pending" else "operator",
        trigger_context="m3u_refresh",
    )
    session.add(row)
    session.commit()
    return row


class TestPrediction:
    def test_existing_pending_row_predicts_deferral_with_its_id(self, test_session):
        row = _seed_row(test_session)
        before = _queue_snapshot(test_session)

        prediction = _predict(test_session)

        assert prediction.deferred is True
        assert prediction.merge_id == row.id
        assert prediction.candidate is not None
        assert prediction.candidate.candidate_channel_id == "100"
        assert _queue_snapshot(test_session) == before

    def test_fresh_queue_state_predicts_deferral_behind_a_new_row(self, test_session):
        before = _queue_snapshot(test_session)

        prediction = _predict(test_session)

        assert prediction.deferred is True
        assert prediction.merge_id is None          # a live run would INSERT one
        assert prediction.candidate.candidate_channel_id == "100"
        assert _queue_snapshot(test_session) == before == ([], [])

    @pytest.mark.parametrize("status", ["merged", "dismissed"])
    def test_terminal_rows_do_not_block_so_a_new_row_is_predicted(self, test_session, status):
        _seed_row(test_session, status=status)
        before = _queue_snapshot(test_session)

        prediction = _predict(test_session)

        assert prediction.deferred is True
        assert prediction.merge_id is None
        assert _queue_snapshot(test_session) == before

    def test_no_match_predicts_create(self, test_session):
        prediction = _predict(test_session, candidates=[(100, "BBC News")])
        assert prediction == PendingMergePrediction(deferred=False)

    @pytest.mark.parametrize("triggered_by", ["api", "manual", "scheduled_all", ""])
    def test_other_triggers_predict_create_even_with_a_blocking_row(self, test_session, triggered_by):
        _seed_row(test_session)
        prediction = _predict(test_session, triggered_by=triggered_by)
        assert prediction == PendingMergePrediction(deferred=False)


class TestSharedDecision:
    """The live hook and the prediction answer from the same two helpers."""

    def test_candidate_helper_applies_the_trigger_gate(self):
        assert find_pending_merge_candidate(
            stream_name=STREAM, candidates=CANDIDATES, threshold=0.80,
            triggered_by="api",
        ) is None
        match = find_pending_merge_candidate(
            stream_name=STREAM, candidates=CANDIDATES, threshold=0.80,
            triggered_by="m3u_refresh",
        )
        assert match is not None and match.candidate_channel_id == "100"

    def test_authoritative_row_lookup_ignores_terminal_rows(self, test_session):
        _seed_row(test_session, status="dismissed")
        assert find_authoritative_pending_row(
            test_session, stream_name=STREAM, candidate_channel_id="100",
        ) is None
        live = _seed_row(test_session)
        found = find_authoritative_pending_row(
            test_session, stream_name=STREAM, candidate_channel_id="100",
        )
        assert found is not None and found.id == live.id

    @pytest.mark.parametrize("seed", [None, "pending", "dismissed"])
    def test_prediction_agrees_with_the_live_hook(self, test_session, seed):
        if seed:
            _seed_row(test_session, status=seed)
        prediction = _predict(test_session)

        live = _live(test_session)

        assert live.enqueued is prediction.deferred is True
        if prediction.merge_id is not None:
            assert live.merge_id == prediction.merge_id   # §D5 collision
        else:
            # The live run inserted the row the preview said it would.
            assert live.merge_id is not None
            assert live.candidate is not None

    def test_prediction_and_live_hook_agree_on_create(self, test_session):
        candidates = [(100, "BBC News")]
        assert _predict(test_session, candidates=candidates).deferred is False
        assert _live(test_session, candidates=candidates).enqueued is False
        assert test_session.query(PendingMerge).count() == 0
