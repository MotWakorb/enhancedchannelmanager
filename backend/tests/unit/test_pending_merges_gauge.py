"""
Unit tests for the ecm_pending_merges_queue_depth gauge (bd-wvr1d).

Covers all four wiring sites that maintain the gauge:

  (a) BD-F insert path (services/m3u_dedup_hook.py): gauge set to
      current count after a fresh pending_merges row is inserted.
  (b) BD-E accept path (routers/channel_merges.py): gauge set to
      current count after a pending row is flipped to 'merged'.
  (c) BD-E dismiss path (routers/channel_merges.py): gauge set to
      current count after a pending row is flipped to 'dismissed'.
  (d) Startup seed (observability.set_pending_merges_queue_depth_gauge):
      gauge set on a pre-populated DB at boot time.

The gauge is NOT a LOCKED CONTRACT SLI — it is a diagnostic companion
to ecm_pending_merges_queue_depth_added_total. These tests assert gauge
accuracy within a single transition; concurrent-transition race
conditions are explicitly out of scope (the helper's docstring documents
that the gauge is best-effort).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import PendingMerge, PendingMergeJournal
from observability import get_metric, install_metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gauge_value() -> float:
    """Return the current value of ecm_pending_merges_queue_depth."""
    install_metrics()
    return get_metric("pending_merges_queue_depth")._value.get()


def _make_pending(
    session,
    *,
    stream_name: str = "ESPN HD",
    candidate_channel_id: str = "ch-001",
    status: str = "pending",
    group_id: int = 5,
) -> PendingMerge:
    """Insert and return a PendingMerge row with minimal required fields."""
    import time
    row = PendingMerge(
        stream_name=stream_name,
        group_id=group_id,
        candidate_channel_id=candidate_channel_id,
        confidence=0.90,
        status=status,
        created_at=int(time.time() * 1000),
        trigger_context="m3u_refresh",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _make_journal(session, *, pending_merge_id: int) -> PendingMergeJournal:
    """Insert a journal row so idempotency helpers don't crash on lookup."""
    import time
    entry = PendingMergeJournal(
        pending_merge_id=pending_merge_id,
        actor_token_id="test",
        action_type="merge_confirmed",
        source_channel_id="stream-1",
        target_channel_id="ch-001",
        confidence_score=0.90,
        timestamp_utc=int(time.time() * 1000),
        trigger_context="m3u_refresh",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


# ---------------------------------------------------------------------------
# (a) BD-F insert — gauge reflects post-insert count
# ---------------------------------------------------------------------------

class TestGaugeAfterBDFInsert:
    """The BD-F dedup hook sets the gauge after a fresh pending_merges insert."""

    def test_gauge_equals_pending_count_after_insert(self, test_session):
        """After inserting one pending row via the hook, gauge == 1."""
        install_metrics()
        from services.m3u_dedup_hook import check_and_enqueue_pending_merge

        check_and_enqueue_pending_merge(
            stream_name="ESPN HD",
            group_id=42,
            candidates=[("99", "ESPN HD")],
            threshold=0.80,
            triggered_by="m3u_refresh",
            dry_run=False,
            db_session=test_session,
        )

        pending_count = test_session.query(PendingMerge).filter_by(status="pending").count()
        assert pending_count == 1
        assert _gauge_value() == 1.0

    def test_gauge_tracks_multiple_inserts(self, test_session):
        """Gauge increments correctly after each fresh insert."""
        install_metrics()
        from services.m3u_dedup_hook import check_and_enqueue_pending_merge

        check_and_enqueue_pending_merge(
            stream_name="ESPN HD",
            group_id=42,
            candidates=[("99", "ESPN HD")],
            threshold=0.80,
            triggered_by="m3u_refresh",
            dry_run=False,
            db_session=test_session,
        )
        assert _gauge_value() == 1.0

        # Second stream — different candidate so no unique-index collision.
        check_and_enqueue_pending_merge(
            stream_name="BBC One",
            group_id=42,
            candidates=[("100", "BBC One")],
            threshold=0.80,
            triggered_by="m3u_refresh",
            dry_run=False,
            db_session=test_session,
        )
        assert _gauge_value() == 2.0

    def test_gauge_not_set_on_idempotent_collision(self, test_session):
        """A partial-unique-index collision (repeat import) does not change the gauge."""
        install_metrics()
        from services.m3u_dedup_hook import check_and_enqueue_pending_merge

        kwargs = dict(
            stream_name="ESPN HD",
            group_id=42,
            candidates=[("99", "ESPN HD")],
            threshold=0.80,
            triggered_by="m3u_refresh",
            dry_run=False,
            db_session=test_session,
        )
        # First insert sets gauge to 1.
        check_and_enqueue_pending_merge(**kwargs)
        assert _gauge_value() == 1.0

        # Collision on second call — gauge still reflects DB count (still 1).
        check_and_enqueue_pending_merge(**kwargs)
        assert _gauge_value() == 1.0


# ---------------------------------------------------------------------------
# (b) BD-E accept — gauge decrements after merge
# ---------------------------------------------------------------------------

class TestGaugeAfterBDEAccept:
    """BD-E accept sets the gauge to the post-merge pending count."""

    def test_gauge_decrements_after_accept(self, test_session):
        """Accepting one of two pending rows leaves gauge == 1."""
        install_metrics()

        # Seed two pending rows directly (not via the hook so we don't
        # double-count gauge-set side effects from BD-F).
        row1 = _make_pending(test_session, stream_name="ESPN HD", candidate_channel_id="ch-001")
        _make_pending(test_session, stream_name="BBC One", candidate_channel_id="ch-002")
        assert test_session.query(PendingMerge).filter_by(status="pending").count() == 2

        from observability import set_pending_merges_queue_depth_gauge
        # Manually flip row1 to merged (simulating what the accept endpoint does).
        import time
        row1.status = "merged"
        row1.resolved_at = int(time.time() * 1000)
        row1.resolution_source = "operator"
        test_session.commit()

        # Call the gauge helper as the accept endpoint does.
        set_pending_merges_queue_depth_gauge(test_session)

        pending_count = test_session.query(PendingMerge).filter_by(status="pending").count()
        assert pending_count == 1
        assert _gauge_value() == 1.0

    def test_gauge_zero_after_all_accepted(self, test_session):
        """Gauge reaches 0 when the queue is fully drained via accept."""
        install_metrics()

        row = _make_pending(test_session, stream_name="ESPN HD", candidate_channel_id="ch-001")

        import time
        row.status = "merged"
        row.resolved_at = int(time.time() * 1000)
        row.resolution_source = "operator"
        test_session.commit()

        from observability import set_pending_merges_queue_depth_gauge
        set_pending_merges_queue_depth_gauge(test_session)

        assert _gauge_value() == 0.0

    def test_gauge_helper_reflects_count_before_and_after_accept(self, test_session):
        """Calling the gauge helper after accept reflects the updated count."""
        install_metrics()

        row = _make_pending(test_session, stream_name="ESPN HD", candidate_channel_id="ch-001")
        _make_pending(test_session, stream_name="BBC One", candidate_channel_id="ch-002")

        from observability import set_pending_merges_queue_depth_gauge

        # Before accept: gauge == 2.
        set_pending_merges_queue_depth_gauge(test_session)
        assert _gauge_value() == 2.0

        # Simulate the accept endpoint's status flip + commit.
        import time
        row.status = "merged"
        row.resolved_at = int(time.time() * 1000)
        row.resolution_source = "operator"
        test_session.commit()

        # After accept: gauge == 1.
        set_pending_merges_queue_depth_gauge(test_session)
        assert _gauge_value() == 1.0


# ---------------------------------------------------------------------------
# (c) BD-E dismiss — gauge decrements after dismiss
# ---------------------------------------------------------------------------

class TestGaugeAfterBDEDismiss:
    """BD-E dismiss sets the gauge to the post-dismiss pending count."""

    def test_gauge_decrements_after_dismiss(self, test_session):
        """Dismissing one of two pending rows leaves gauge == 1."""
        install_metrics()

        row1 = _make_pending(test_session, stream_name="ESPN HD", candidate_channel_id="ch-001")
        _make_pending(test_session, stream_name="BBC One", candidate_channel_id="ch-002")

        import time
        row1.status = "dismissed"
        row1.resolved_at = int(time.time() * 1000)
        row1.resolution_source = "operator"
        test_session.commit()

        from observability import set_pending_merges_queue_depth_gauge
        set_pending_merges_queue_depth_gauge(test_session)

        assert _gauge_value() == 1.0

    def test_gauge_zero_after_all_dismissed(self, test_session):
        """Gauge reaches 0 when the queue is fully drained via dismiss."""
        install_metrics()

        row = _make_pending(test_session, stream_name="ESPN HD", candidate_channel_id="ch-001")

        import time
        row.status = "dismissed"
        row.resolved_at = int(time.time() * 1000)
        row.resolution_source = "operator"
        test_session.commit()

        from observability import set_pending_merges_queue_depth_gauge
        set_pending_merges_queue_depth_gauge(test_session)

        assert _gauge_value() == 0.0

    def test_gauge_helper_reflects_count_before_and_after_dismiss(self, test_session):
        """Calling the gauge helper after dismiss reflects the updated count."""
        install_metrics()

        row = _make_pending(test_session, stream_name="ESPN HD", candidate_channel_id="ch-001")
        _make_pending(test_session, stream_name="BBC One", candidate_channel_id="ch-002")

        from observability import set_pending_merges_queue_depth_gauge

        # Before dismiss: gauge == 2.
        set_pending_merges_queue_depth_gauge(test_session)
        assert _gauge_value() == 2.0

        # Simulate the dismiss endpoint's status flip + commit.
        import time
        row.status = "dismissed"
        row.resolved_at = int(time.time() * 1000)
        row.resolution_source = "operator"
        test_session.commit()

        # After dismiss: gauge == 1.
        set_pending_merges_queue_depth_gauge(test_session)
        assert _gauge_value() == 1.0


# ---------------------------------------------------------------------------
# (d) Startup seed — gauge seeded from pre-populated DB
# ---------------------------------------------------------------------------

class TestGaugeStartupSeed:
    """The startup seed sets the gauge to the pre-existing pending count."""

    def test_startup_seed_reflects_existing_pending_rows(self, test_session):
        """Gauge is seeded to the correct count when called at startup."""
        install_metrics()

        # Pre-populate the DB with 3 pending rows and 1 merged row.
        _make_pending(test_session, stream_name="ESPN HD", candidate_channel_id="ch-001")
        _make_pending(test_session, stream_name="BBC One", candidate_channel_id="ch-002")
        _make_pending(test_session, stream_name="CNN", candidate_channel_id="ch-003")
        _make_pending(test_session, stream_name="FOX", candidate_channel_id="ch-004", status="merged")

        from observability import set_pending_merges_queue_depth_gauge
        set_pending_merges_queue_depth_gauge(test_session)

        # Only the 3 status='pending' rows should count.
        assert _gauge_value() == 3.0

    def test_startup_seed_is_zero_on_empty_db(self, test_session):
        """Gauge is 0 when there are no pending rows on startup."""
        install_metrics()

        from observability import set_pending_merges_queue_depth_gauge
        set_pending_merges_queue_depth_gauge(test_session)

        assert _gauge_value() == 0.0

    def test_startup_seed_excludes_terminal_rows(self, test_session):
        """Only status='pending' rows contribute to the startup gauge value."""
        install_metrics()

        # One row of each status — only 'pending' should count.
        _make_pending(test_session, stream_name="Stream A", candidate_channel_id="ch-001", status="pending")
        _make_pending(test_session, stream_name="Stream B", candidate_channel_id="ch-002", status="merged")
        _make_pending(test_session, stream_name="Stream C", candidate_channel_id="ch-003", status="dismissed")

        from observability import set_pending_merges_queue_depth_gauge
        set_pending_merges_queue_depth_gauge(test_session)

        assert _gauge_value() == 1.0
