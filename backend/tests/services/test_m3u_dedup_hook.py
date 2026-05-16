"""
Unit tests for the bulk-M3U dedup hook service (BD-F / bd-a5lb2).

Covers ADR-008 §D1 (m3u_refresh is one of four trigger surfaces),
§D5 (partial unique-index idempotency on repeat enqueue), §D9 (direct
write to pending_merges, no broker), the operator threshold path
(below-threshold returns no candidate), the dry-run short-circuit, and
the LOCKED CONTRACT metric ``ecm_pending_merges_queue_depth_added_total``.

Integration tests against the auto-creation executor live in
``tests/unit/test_auto_creation_executor_dedup_hook.py``. These tests
exercise the hook service in isolation so failures point at hook logic
rather than at the executor's plumbing.
"""
from __future__ import annotations

import logging

import pytest

from models import PendingMerge
from services.m3u_dedup_hook import (
    M3U_REFRESH_TRIGGER_CONTEXT,
    M3U_REFRESH_TRIGGERED_BY,
    DedupHookResult,
    check_and_enqueue_pending_merge,
)


# ---------------------------------------------------------------------------
# Constants — keep the surface contracts honest.
# ---------------------------------------------------------------------------


class TestHookConstants:
    """The trigger_context strings ARE the contract per ADR-008 §D6."""

    def test_trigger_context_is_m3u_refresh(self):
        # Schema CHECK constraint accepts the four enum values; the bulk-M3U
        # path is exactly this one. Any rename of the constant must update
        # the migration / model CHECK in lockstep.
        assert M3U_REFRESH_TRIGGER_CONTEXT == "m3u_refresh"

    def test_triggered_by_string_matches_engine_value(self):
        # The auto-creation engine uses the same string for triggered_by;
        # the hook short-circuits unless they match exactly.
        assert M3U_REFRESH_TRIGGERED_BY == "m3u_refresh"


# ---------------------------------------------------------------------------
# Short-circuit paths — these run before any DB / matcher work.
# ---------------------------------------------------------------------------


class TestHookShortCircuits:
    """Cheap rejections before scoring or DB writes."""

    def test_dry_run_returns_no_enqueue(self, test_session):
        result = check_and_enqueue_pending_merge(
            stream_name="ESPN HD",
            group_id=1,
            candidates=[("100", "ESPN")],
            threshold=0.80,
            triggered_by="m3u_refresh",
            dry_run=True,
            db_session=test_session,
        )
        assert result == DedupHookResult(enqueued=False)
        # No write happened.
        assert test_session.query(PendingMerge).count() == 0

    def test_non_m3u_triggered_by_returns_no_enqueue(self, test_session):
        # Auto-creation scheduled/manual runs are NOT one of the four ADR-008
        # §D1 trigger surfaces — they must stay on legacy "always create"
        # semantics and not enqueue pending merges.
        for triggered_by in ("scheduled", "manual", "api", ""):
            result = check_and_enqueue_pending_merge(
                stream_name="ESPN HD",
                group_id=1,
                candidates=[("100", "ESPN")],
                threshold=0.80,
                triggered_by=triggered_by,
                dry_run=False,
                db_session=test_session,
            )
            assert result.enqueued is False, (
                f"triggered_by={triggered_by!r} unexpectedly enqueued"
            )
        assert test_session.query(PendingMerge).count() == 0

    def test_empty_candidates_returns_no_enqueue(self, test_session):
        # Empty target group — nothing to match. The auto-creation pipeline
        # proceeds with normal channel creation.
        result = check_and_enqueue_pending_merge(
            stream_name="ESPN HD",
            group_id=1,
            candidates=[],
            threshold=0.80,
            triggered_by="m3u_refresh",
            dry_run=False,
            db_session=test_session,
        )
        assert result.enqueued is False
        assert test_session.query(PendingMerge).count() == 0


# ---------------------------------------------------------------------------
# Fresh insert — happy path.
# ---------------------------------------------------------------------------


class TestHookFreshInsert:
    """A new (stream_name, candidate_channel_id) pair lands a fresh row."""

    def test_match_above_threshold_inserts_pending_row(self, test_session):
        result = check_and_enqueue_pending_merge(
            stream_name="ESPN HD",
            group_id=42,
            # Same name → exact-match path → confidence 1.0, above any
            # operator-configured threshold by construction.
            candidates=[("99", "ESPN HD")],
            threshold=0.80,
            triggered_by="m3u_refresh",
            dry_run=False,
            db_session=test_session,
        )
        assert result.enqueued is True
        assert result.candidate is not None
        assert result.candidate.candidate_channel_id == "99"
        assert result.candidate.confidence == 1.0

        rows = test_session.query(PendingMerge).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.stream_name == "ESPN HD"
        assert row.group_id == 42
        assert row.candidate_channel_id == "99"
        assert row.confidence == 1.0
        assert row.status == "pending"
        assert row.trigger_context == "m3u_refresh"
        assert row.resolved_at is None
        assert row.resolution_source is None
        # Epoch-ms — matches ADR-007 / pending_merges convention.
        assert row.created_at > 0

    def test_stringifies_integer_channel_ids(self, test_session):
        # Dispatcharr returns int channel ids; ADR-008 §D8 stores them as
        # TEXT. The hook must stringify at insert time so the partial
        # unique index keys consistently.
        result = check_and_enqueue_pending_merge(
            stream_name="ESPN HD",
            group_id=42,
            candidates=[(100, "ESPN HD")],  # int, not str
            threshold=0.80,
            triggered_by="m3u_refresh",
            dry_run=False,
            db_session=test_session,
        )
        assert result.enqueued is True
        row = test_session.query(PendingMerge).first()
        assert row.candidate_channel_id == "100"
        assert isinstance(row.candidate_channel_id, str)

    def test_null_group_id_stored_as_null(self, test_session):
        # ADR-008 §D8: group_id is nullable; the "ungrouped import" case
        # stores NULL and is treated as an open candidate scope.
        result = check_and_enqueue_pending_merge(
            stream_name="ESPN HD",
            group_id=None,
            candidates=[("99", "ESPN HD")],
            threshold=0.80,
            triggered_by="m3u_refresh",
            dry_run=False,
            db_session=test_session,
        )
        assert result.enqueued is True
        row = test_session.query(PendingMerge).first()
        assert row.group_id is None

    def test_metric_incremented_on_fresh_insert(self, test_session):
        # LOCKED CONTRACT per BD-M / SLO-10b: one increment per row
        # inserted into the pending_merges queue.
        from observability import get_metric, install_metrics

        install_metrics()
        counter = get_metric("pending_merges_queue_depth_added_total")
        before = counter._value.get()

        check_and_enqueue_pending_merge(
            stream_name="ESPN HD",
            group_id=42,
            candidates=[("99", "ESPN HD")],
            threshold=0.80,
            triggered_by="m3u_refresh",
            dry_run=False,
            db_session=test_session,
        )
        after = counter._value.get()
        assert after == before + 1


# ---------------------------------------------------------------------------
# Below-threshold path — silent refusal per ADR-008 §D2.
# ---------------------------------------------------------------------------


class TestHookBelowThreshold:
    """No candidate above (operator threshold || floor) → no enqueue."""

    def test_high_threshold_above_match_score_returns_no_enqueue(self, test_session):
        # Operator sets threshold to 0.95; the matcher scores
        # "ESPN HD" vs "BBC One" well below that → no candidate emitted.
        # The hook returns no enqueue and the auto-creation pipeline
        # proceeds with normal channel creation.
        result = check_and_enqueue_pending_merge(
            stream_name="ESPN HD",
            group_id=42,
            candidates=[("99", "BBC One")],
            threshold=0.95,
            triggered_by="m3u_refresh",
            dry_run=False,
            db_session=test_session,
        )
        assert result.enqueued is False
        assert result.candidate is None
        assert test_session.query(PendingMerge).count() == 0

    def test_below_floor_threshold_clamps_at_floor(self, test_session):
        # ADR-008 §D2 hard floor: even if the caller passes threshold=0.30,
        # the matcher clamps to CONFIDENCE_FLOOR=0.60. Two unrelated names
        # that would score ~0.3-0.4 must NOT enqueue.
        result = check_and_enqueue_pending_merge(
            stream_name="Stream Alpha",
            group_id=42,
            candidates=[("99", "Different Channel Beta")],
            threshold=0.30,  # below floor
            triggered_by="m3u_refresh",
            dry_run=False,
            db_session=test_session,
        )
        assert result.enqueued is False
        assert test_session.query(PendingMerge).count() == 0


# ---------------------------------------------------------------------------
# Idempotency — ADR-008 §D5 partial unique index.
# ---------------------------------------------------------------------------


class TestHookIdempotency:
    """Repeat M3U imports of the same stream must not crash or duplicate."""

    def test_duplicate_pending_row_handled_gracefully(self, test_session, caplog):
        # First call inserts; second call hits the partial unique index
        # and is caught + logged at INFO. The hook still signals "skip
        # channel creation" because the prior row is authoritative.
        kwargs = dict(
            stream_name="ESPN HD",
            group_id=42,
            candidates=[("99", "ESPN HD")],
            threshold=0.80,
            triggered_by="m3u_refresh",
            dry_run=False,
            db_session=test_session,
        )

        first = check_and_enqueue_pending_merge(**kwargs)
        assert first.enqueued is True
        assert first.candidate is not None
        assert test_session.query(PendingMerge).count() == 1

        with caplog.at_level(logging.INFO, logger="services.m3u_dedup_hook"):
            second = check_and_enqueue_pending_merge(**kwargs)
        assert second.enqueued is True
        # The collision branch does NOT re-score, so candidate is None.
        assert second.candidate is None
        # Row count unchanged — no duplicate.
        assert test_session.query(PendingMerge).count() == 1

        # Log line confirms the §D5 path was exercised.
        collision_logs = [
            r for r in caplog.records
            if "already queued" in r.message and r.levelno == logging.INFO
        ]
        assert len(collision_logs) == 1, (
            "Expected one INFO log line confirming partial-unique-index "
            "collision; got: "
            + repr([r.message for r in caplog.records])
        )

    def test_duplicate_collision_does_not_increment_metric(self, test_session):
        # The metric counts FRESH inserts only — the SLI-10b denominator
        # in docs/sre/slos.md treats the queue as "rows that need
        # resolution" and a collision is not a new row.
        from observability import get_metric, install_metrics

        install_metrics()
        counter = get_metric("pending_merges_queue_depth_added_total")
        kwargs = dict(
            stream_name="ESPN HD",
            group_id=42,
            candidates=[("99", "ESPN HD")],
            threshold=0.80,
            triggered_by="m3u_refresh",
            dry_run=False,
            db_session=test_session,
        )
        check_and_enqueue_pending_merge(**kwargs)
        mid = counter._value.get()
        check_and_enqueue_pending_merge(**kwargs)  # collision
        end = counter._value.get()
        assert end == mid, (
            "Collision path must not increment "
            "ecm_pending_merges_queue_depth_added_total"
        )

    def test_terminal_state_row_does_not_block_fresh_pending(self, test_session):
        # ADR-008 §D5 partial-unique-index: the constraint is WHERE
        # status='pending'. A merged or dismissed row for the same pair
        # is historical and must NOT block a new pending row from a later
        # M3U refresh (e.g. operator un-merged, stream re-appears).
        prior = PendingMerge(
            stream_name="ESPN HD",
            group_id=42,
            candidate_channel_id="99",
            confidence=0.95,
            status="merged",
            created_at=1000,
            resolved_at=2000,
            resolution_source="operator",
            trigger_context="drag_drop",
        )
        test_session.add(prior)
        test_session.commit()

        result = check_and_enqueue_pending_merge(
            stream_name="ESPN HD",
            group_id=42,
            candidates=[("99", "ESPN HD")],
            threshold=0.80,
            triggered_by="m3u_refresh",
            dry_run=False,
            db_session=test_session,
        )
        assert result.enqueued is True
        assert result.candidate is not None
        # Two rows now — one historical 'merged', one fresh 'pending'.
        assert test_session.query(PendingMerge).count() == 2
        pending = test_session.query(PendingMerge).filter_by(status="pending").one()
        assert pending.trigger_context == "m3u_refresh"
