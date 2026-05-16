"""
Tests for the Pending Channel Merges API (BD-E / bd-acqkb).

Covers the contract in ADR-008 §D1 (endpoints), §D3 (state machine),
§D4 (lazy resolution), §D6 (audit journal field set), and the BD-M
metric contract (`ecm_dedup_merge_requests_total{status}`).

Test surface (per the BD-E ticket):

  list:
    * empty queue → empty list, total=0
    * status filter (pending vs merged vs dismissed)
    * pagination correctness
    * group_id filter

  accept:
    * happy path: pending → merged + Dispatcharr update + journal row
    * idempotent: double-accept → same outcome, no second merge / journal
    * stale target: get_channel 404 → HTTP 404 with operator-actionable detail
    * invalid state: accept on dismissed row → 409
    * metric: success emits status="success" +1
    * metric: failure emits status="error" +1

  dismiss:
    * happy path: pending → dismissed + journal row
    * idempotent: double-dismiss → same outcome
    * invalid state: dismiss on merged row → 409
    * metric: dismiss emits status="dismissed" +1

  audit journal — every row must carry ALL SEVEN §D6 fields populated:
  actor_token_id, action_type, source_channel_id, target_channel_id,
  confidence_score, timestamp_utc, trigger_context.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

import observability
from models import PendingMerge, PendingMergeJournal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_pending(
    test_session,
    *,
    stream_name="ESPN HD",
    group_id=5,
    candidate_channel_id="ch-uuid-001",
    confidence=0.87,
    status="pending",
    created_at=1_700_000_000_000,
    resolved_at=None,
    resolution_source=None,
    trigger_context="m3u_refresh",
):
    """Insert and return a ``PendingMerge`` row with sensible defaults."""
    row = PendingMerge(
        stream_name=stream_name,
        group_id=group_id,
        candidate_channel_id=candidate_channel_id,
        confidence=confidence,
        status=status,
        created_at=created_at,
        resolved_at=resolved_at,
        resolution_source=resolution_source,
        trigger_context=trigger_context,
    )
    test_session.add(row)
    test_session.commit()
    test_session.refresh(row)
    return row


def _make_journal(
    test_session,
    *,
    pending_merge_id,
    actor_token_id="42",
    action_type="merge_confirmed",
    source_channel_id="stream-abc",
    target_channel_id="ch-uuid-001",
    confidence_score=0.87,
    timestamp_utc=1_700_000_001_000,
    trigger_context="m3u_refresh",
):
    """Insert a journal row for the idempotency tests."""
    entry = PendingMergeJournal(
        pending_merge_id=pending_merge_id,
        actor_token_id=actor_token_id,
        action_type=action_type,
        source_channel_id=source_channel_id,
        target_channel_id=target_channel_id,
        confidence_score=confidence_score,
        timestamp_utc=timestamp_utc,
        trigger_context=trigger_context,
    )
    test_session.add(entry)
    test_session.commit()
    test_session.refresh(entry)
    return entry


def _metric_value(status: str) -> float:
    """Read the current value of ``ecm_dedup_merge_requests_total{status=...}``.

    Reaches into prometheus_client's accumulator directly — same
    pattern as ``test_database_size_metrics.py`` and other observability
    tests. Returns 0.0 when the label set has not been touched yet.
    """
    observability.install_metrics()
    metric = observability.get_metric("dedup_merge_requests_total")
    # ``_metrics`` is the (label-tuple → Sample-storing-child) accumulator.
    sample = metric.labels(status=status)
    return float(sample._value.get())


# ---------------------------------------------------------------------------
# GET /api/channel-merges — list
# ---------------------------------------------------------------------------
class TestListPendingMerges:
    """Tests for GET /api/channel-merges."""

    @pytest.mark.asyncio
    async def test_empty_queue_returns_empty_list(self, async_client, test_session):
        """An empty pending_merges table returns ``{merges: [], total: 0, ...}``."""
        response = await async_client.get("/api/channel-merges")

        assert response.status_code == 200
        data = response.json()
        assert data["merges"] == []
        assert data["total"] == 0
        assert data["page"] == 1
        assert data["page_size"] == 50
        assert data["total_pages"] == 0

    @pytest.mark.asyncio
    async def test_status_filter_pending(self, async_client, test_session):
        """``?status=pending`` (default) returns only pending rows."""
        _make_pending(test_session, stream_name="ESPN HD", status="pending")
        _make_pending(test_session, stream_name="TNT", status="merged",
                      resolved_at=1_700_000_010_000, resolution_source="operator")
        _make_pending(test_session, stream_name="TBS", status="dismissed",
                      resolved_at=1_700_000_020_000, resolution_source="operator")

        response = await async_client.get("/api/channel-merges")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["merges"]) == 1
        assert data["merges"][0]["stream_name"] == "ESPN HD"
        assert data["merges"][0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_status_filter_merged(self, async_client, test_session):
        """``?status=merged`` returns only merged rows."""
        _make_pending(test_session, stream_name="ESPN HD", status="pending")
        _make_pending(test_session, stream_name="TNT", status="merged",
                      resolved_at=1_700_000_010_000, resolution_source="operator")

        response = await async_client.get("/api/channel-merges?status=merged")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["merges"][0]["stream_name"] == "TNT"
        assert data["merges"][0]["status"] == "merged"

    @pytest.mark.asyncio
    async def test_status_filter_dismissed(self, async_client, test_session):
        """``?status=dismissed`` returns only dismissed rows."""
        _make_pending(test_session, stream_name="TBS", status="dismissed",
                      resolved_at=1_700_000_020_000, resolution_source="operator")

        response = await async_client.get("/api/channel-merges?status=dismissed")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["merges"][0]["status"] == "dismissed"

    @pytest.mark.asyncio
    async def test_invalid_status_returns_400(self, async_client, test_session):
        """``?status=garbage`` returns 400 with a clear detail."""
        response = await async_client.get("/api/channel-merges?status=garbage")

        assert response.status_code == 400
        assert "status must be one of" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_group_id_filter(self, async_client, test_session):
        """``?group_id=X`` returns only rows in that group."""
        _make_pending(test_session, stream_name="ESPN HD", group_id=5)
        _make_pending(test_session, stream_name="TNT", group_id=7)

        response = await async_client.get("/api/channel-merges?group_id=5")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["merges"][0]["stream_name"] == "ESPN HD"
        assert data["merges"][0]["group_id"] == 5

    @pytest.mark.asyncio
    async def test_pagination(self, async_client, test_session):
        """page + page_size correctly slice the result set."""
        # Insert 7 pending rows with increasing created_at so ordering is deterministic.
        for i in range(7):
            _make_pending(
                test_session,
                stream_name=f"stream-{i}",
                candidate_channel_id=f"ch-{i:03d}",
                created_at=1_700_000_000_000 + i,
            )

        # Page 1 of 3
        response = await async_client.get(
            "/api/channel-merges?page=1&page_size=3"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 7
        assert data["page"] == 1
        assert data["page_size"] == 3
        assert data["total_pages"] == 3
        assert len(data["merges"]) == 3
        # DESC ordering — newest first.
        assert data["merges"][0]["stream_name"] == "stream-6"

        # Page 3 — only 1 row left.
        response = await async_client.get(
            "/api/channel-merges?page=3&page_size=3"
        )
        assert response.status_code == 200
        assert len(response.json()["merges"]) == 1

    @pytest.mark.asyncio
    async def test_invalid_pagination_returns_400(self, async_client, test_session):
        """``page=0`` or ``page_size=0`` returns 400."""
        r1 = await async_client.get("/api/channel-merges?page=0")
        assert r1.status_code == 400

        r2 = await async_client.get("/api/channel-merges?page_size=0")
        assert r2.status_code == 400

        r3 = await async_client.get("/api/channel-merges?page_size=999")
        assert r3.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/channel-merges/{id}/accept
# ---------------------------------------------------------------------------
class TestAcceptPendingMerge:
    """Tests for POST /api/channel-merges/{id}/accept."""

    @pytest.mark.asyncio
    async def test_happy_path(self, async_client, test_session):
        """Pending → merged: Dispatcharr update called, journal row written."""
        row = _make_pending(test_session, confidence=0.92, trigger_context="m3u_refresh")
        before_success = _metric_value("success")

        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {
            "id": row.candidate_channel_id, "name": "ESPN", "streams": [],
        }
        mock_client.get_streams.return_value = {
            "results": [{"id": 4242, "name": row.stream_name}],
        }
        mock_client.update_channel.return_value = {"id": row.candidate_channel_id}

        with patch("routers.channel_merges.get_client", return_value=mock_client):
            response = await async_client.post(
                f"/api/channel-merges/{row.id}/accept"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "merged"
        assert data["merged_into_channel_id"] == row.candidate_channel_id
        assert isinstance(data["journal_entry_id"], int)
        # W3 (post-review): AcceptOutcome carries the §D6 audit set so
        # the client sees what the journal recorded without a round-trip.
        assert data["source_stream_id"] == "4242"
        assert data["confidence"] == pytest.approx(0.92)

        # Dispatcharr update_channel called with the new stream id appended.
        mock_client.update_channel.assert_called_once()
        update_args = mock_client.update_channel.call_args
        assert update_args[0][0] == row.candidate_channel_id
        assert update_args[0][1] == {"streams": [4242]}

        # DB state: row.status='merged', resolved_at + resolution_source populated.
        test_session.expire_all()
        refreshed = test_session.query(PendingMerge).filter(PendingMerge.id == row.id).one()
        assert refreshed.status == "merged"
        assert refreshed.resolved_at is not None
        assert refreshed.resolution_source == "operator"

        # Journal row written with ALL SEVEN §D6 fields populated.
        journal = test_session.query(PendingMergeJournal).filter(
            PendingMergeJournal.pending_merge_id == row.id
        ).one()
        assert journal.actor_token_id  # not empty
        assert journal.action_type == "merge_confirmed"
        assert journal.source_channel_id == "4242"
        assert journal.target_channel_id == row.candidate_channel_id
        assert journal.confidence_score == pytest.approx(0.92)
        assert journal.timestamp_utc > 0
        assert journal.trigger_context == "m3u_refresh"

        # Metric: status="success" incremented by 1.
        assert _metric_value("success") == pytest.approx(before_success + 1)

    @pytest.mark.asyncio
    async def test_idempotent_double_accept(self, async_client, test_session):
        """A second accept on a 'merged' row returns the prior outcome, no second merge."""
        row = _make_pending(test_session, status="merged",
                            resolved_at=1_700_000_010_000, resolution_source="operator")
        prior_journal = _make_journal(test_session, pending_merge_id=row.id,
                                       action_type="merge_confirmed")

        mock_client = AsyncMock()

        before_success = _metric_value("success")
        with patch("routers.channel_merges.get_client", return_value=mock_client):
            response = await async_client.post(
                f"/api/channel-merges/{row.id}/accept"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "merged"
        assert data["merged_into_channel_id"] == row.candidate_channel_id
        assert data["journal_entry_id"] == prior_journal.id
        # W3 (post-review): idempotent replay echoes the prior outcome
        # envelope — including the audit set the original action wrote.
        assert data["source_stream_id"] == prior_journal.source_channel_id
        assert data["confidence"] == pytest.approx(row.confidence)

        # No Dispatcharr calls were made on the idempotent replay.
        mock_client.get_channel.assert_not_called()
        mock_client.update_channel.assert_not_called()

        # No second journal row.
        count = test_session.query(PendingMergeJournal).filter(
            PendingMergeJournal.pending_merge_id == row.id
        ).count()
        assert count == 1

        # Metric: no new bump (idempotent replays don't count).
        assert _metric_value("success") == pytest.approx(before_success)

    @pytest.mark.asyncio
    async def test_stale_target_returns_404(self, async_client, test_session):
        """A 404 from Dispatcharr on get_channel returns 404 with operator-actionable detail."""
        row = _make_pending(test_session)

        mock_client = AsyncMock()
        mock_response = httpx.Response(
            status_code=404, request=httpx.Request("GET", "http://x/api/channels"),
        )
        mock_client.get_channel.side_effect = httpx.HTTPStatusError(
            "404", request=mock_response.request, response=mock_response,
        )

        with patch("routers.channel_merges.get_client", return_value=mock_client):
            response = await async_client.post(
                f"/api/channel-merges/{row.id}/accept"
            )

        assert response.status_code == 404
        assert "no longer exists" in response.json()["detail"].lower()
        assert "dismiss" in response.json()["detail"].lower()

        # Pending row was NOT mutated — operator can still /dismiss.
        test_session.expire_all()
        refreshed = test_session.query(PendingMerge).filter(PendingMerge.id == row.id).one()
        assert refreshed.status == "pending"
        assert refreshed.resolved_at is None

        # No journal row written.
        count = test_session.query(PendingMergeJournal).filter(
            PendingMergeJournal.pending_merge_id == row.id
        ).count()
        assert count == 0

    @pytest.mark.asyncio
    async def test_invalid_state_dismissed_returns_409(self, async_client, test_session):
        """Trying to accept a dismissed row returns 409 with a clear detail."""
        row = _make_pending(test_session, status="dismissed",
                            resolved_at=1_700_000_010_000, resolution_source="operator")
        _make_journal(test_session, pending_merge_id=row.id, action_type="merge_dismissed")

        mock_client = AsyncMock()
        with patch("routers.channel_merges.get_client", return_value=mock_client):
            response = await async_client.post(
                f"/api/channel-merges/{row.id}/accept"
            )

        assert response.status_code == 409
        assert "already dismissed" in response.json()["detail"].lower()

        # No Dispatcharr call was made.
        mock_client.get_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_accept_nonexistent_returns_404(self, async_client, test_session):
        """An accept against an unknown id returns 404."""
        response = await async_client.post(
            "/api/channel-merges/99999/accept"
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_dispatcharr_5xx_returns_500_and_bumps_error_metric(
        self, async_client, test_session,
    ):
        """A non-404 Dispatcharr error returns 500 and bumps status='error'."""
        row = _make_pending(test_session)
        before_error = _metric_value("error")

        mock_client = AsyncMock()
        mock_response = httpx.Response(
            status_code=503, request=httpx.Request("GET", "http://x/api/channels"),
        )
        mock_client.get_channel.side_effect = httpx.HTTPStatusError(
            "503", request=mock_response.request, response=mock_response,
        )

        with patch("routers.channel_merges.get_client", return_value=mock_client):
            response = await async_client.post(
                f"/api/channel-merges/{row.id}/accept"
            )

        assert response.status_code == 500
        assert _metric_value("error") == pytest.approx(before_error + 1)

        # Pending row is untouched on error.
        test_session.expire_all()
        refreshed = test_session.query(PendingMerge).filter(PendingMerge.id == row.id).one()
        assert refreshed.status == "pending"

    @pytest.mark.asyncio
    async def test_ambiguous_stream_match_still_records_decision(
        self, async_client, test_session,
    ):
        """When the name search returns multiple matches, the decision is still recorded.

        Audit-first contract: the operator's accept is captured in the
        journal even when the Dispatcharr-side merge cannot be effected
        unambiguously. The journal records the stream_name as the source
        identifier (no resolved stream id) and a WARN is emitted.
        """
        row = _make_pending(test_session, stream_name="ESPN HD")

        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {
            "id": row.candidate_channel_id, "name": "ESPN", "streams": [],
        }
        # Two streams with the same name — ambiguous.
        mock_client.get_streams.return_value = {
            "results": [
                {"id": 100, "name": "ESPN HD"},
                {"id": 101, "name": "ESPN HD"},
            ],
        }

        with patch("routers.channel_merges.get_client", return_value=mock_client):
            response = await async_client.post(
                f"/api/channel-merges/{row.id}/accept"
            )

        assert response.status_code == 200
        # No update_channel call when the source is ambiguous.
        mock_client.update_channel.assert_not_called()

        # W3 + B3 (post-review): the response surfaces the audit-first
        # fallback transparently — source_stream_id == stream_name so
        # the client can see the lookup was unresolved.
        data = response.json()
        assert data["source_stream_id"] == "ESPN HD"

        # Decision still recorded.
        journal = test_session.query(PendingMergeJournal).filter(
            PendingMergeJournal.pending_merge_id == row.id
        ).one()
        assert journal.action_type == "merge_confirmed"
        assert journal.source_channel_id == "ESPN HD"  # fallback to stream_name

    @pytest.mark.asyncio
    async def test_pagination_overflow_emits_warning(
        self, async_client, test_session, caplog,
    ):
        """When the stream-name lookup hits the page_size ceiling, a WARN logs.

        B2 (post-review): Dispatcharr substring-search can match >500
        streams for a common prefix (e.g. "ESPN" → "ESPN HD", "ESPN HD
        West", "ESPN 2 HD", ...). The resolver uses page_size=500
        (matching the bulk-import ceiling in dispatcharr_client.py); when
        the returned result count equals that ceiling, the exact match
        may be on an untested page 2+, so we emit a WARN so operators
        can see the ambiguity in trace. The audit-first contract still
        records the operator's decision in the journal.

        The mock here returns 500 results all with a non-matching name so
        the exact-name filter selects none — the operator decision still
        records (no Dispatcharr-side merge effected, source_channel_id
        falls back to stream_name).
        """
        import logging
        from routers.channel_merges import STREAM_LOOKUP_PAGE_SIZE

        row = _make_pending(test_session, stream_name="ESPN HD")

        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {
            "id": row.candidate_channel_id, "name": "ESPN", "streams": [],
        }
        # Exactly STREAM_LOOKUP_PAGE_SIZE results — synthetic, all share
        # a prefix but none exact-match "ESPN HD" so the resolver returns
        # nothing matchable; the page-ceiling WARN still fires.
        mock_client.get_streams.return_value = {
            "results": [
                {"id": i, "name": f"ESPN HD West {i}"}
                for i in range(STREAM_LOOKUP_PAGE_SIZE)
            ],
        }

        with caplog.at_level(logging.WARNING, logger="routers.channel_merges"):
            with patch("routers.channel_merges.get_client", return_value=mock_client):
                response = await async_client.post(
                    f"/api/channel-merges/{row.id}/accept"
                )

        assert response.status_code == 200

        # The page-ceiling WARN must be present in caplog.
        warn_lines = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING and "page_size ceiling" in r.getMessage()
        ]
        assert warn_lines, (
            f"expected a 'page_size ceiling' WARN; got: "
            f"{[r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]}"
        )
        # Verify the WARN names the stream and the ceiling value.
        assert any("ESPN HD" in line for line in warn_lines)
        assert any(str(STREAM_LOOKUP_PAGE_SIZE) in line for line in warn_lines)


# ---------------------------------------------------------------------------
# POST /api/channel-merges/{id}/dismiss
# ---------------------------------------------------------------------------
class TestDismissPendingMerge:
    """Tests for POST /api/channel-merges/{id}/dismiss."""

    @pytest.mark.asyncio
    async def test_happy_path(self, async_client, test_session):
        """Pending → dismissed: journal row written, no Dispatcharr call."""
        row = _make_pending(test_session, confidence=0.76, trigger_context="drag_drop")
        before_dismissed = _metric_value("dismissed")

        mock_client = AsyncMock()
        with patch("routers.channel_merges.get_client", return_value=mock_client):
            response = await async_client.post(
                f"/api/channel-merges/{row.id}/dismiss"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "dismissed"
        assert isinstance(data["journal_entry_id"], int)

        # No Dispatcharr calls.
        mock_client.get_channel.assert_not_called()
        mock_client.update_channel.assert_not_called()

        # DB state: row.status='dismissed' with resolution fields.
        test_session.expire_all()
        refreshed = test_session.query(PendingMerge).filter(PendingMerge.id == row.id).one()
        assert refreshed.status == "dismissed"
        assert refreshed.resolved_at is not None
        assert refreshed.resolution_source == "operator"

        # Journal row: ALL SEVEN §D6 fields populated.
        journal = test_session.query(PendingMergeJournal).filter(
            PendingMergeJournal.pending_merge_id == row.id
        ).one()
        assert journal.actor_token_id
        assert journal.action_type == "merge_dismissed"
        assert journal.source_channel_id  # falls back to stream_name
        assert journal.target_channel_id == row.candidate_channel_id
        assert journal.confidence_score == pytest.approx(0.76)
        assert journal.timestamp_utc > 0
        assert journal.trigger_context == "drag_drop"

        # Metric: status="dismissed" incremented by 1.
        assert _metric_value("dismissed") == pytest.approx(before_dismissed + 1)

    @pytest.mark.asyncio
    async def test_idempotent_double_dismiss(self, async_client, test_session):
        """A second dismiss on a 'dismissed' row returns the prior outcome."""
        row = _make_pending(test_session, status="dismissed",
                            resolved_at=1_700_000_010_000, resolution_source="operator")
        prior = _make_journal(test_session, pending_merge_id=row.id,
                              action_type="merge_dismissed")

        before_dismissed = _metric_value("dismissed")
        response = await async_client.post(
            f"/api/channel-merges/{row.id}/dismiss"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "dismissed"
        assert data["journal_entry_id"] == prior.id

        # No second journal row.
        count = test_session.query(PendingMergeJournal).filter(
            PendingMergeJournal.pending_merge_id == row.id
        ).count()
        assert count == 1

        # No metric bump on idempotent replay.
        assert _metric_value("dismissed") == pytest.approx(before_dismissed)

    @pytest.mark.asyncio
    async def test_invalid_state_merged_returns_409(self, async_client, test_session):
        """Trying to dismiss a merged row returns 409."""
        row = _make_pending(test_session, status="merged",
                            resolved_at=1_700_000_010_000, resolution_source="operator")
        _make_journal(test_session, pending_merge_id=row.id, action_type="merge_confirmed")

        response = await async_client.post(
            f"/api/channel-merges/{row.id}/dismiss"
        )

        assert response.status_code == 409
        assert "already merged" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_dismiss_nonexistent_returns_404(self, async_client, test_session):
        """A dismiss against an unknown id returns 404."""
        response = await async_client.post(
            "/api/channel-merges/99999/dismiss"
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Metrics contract (BD-M LOCKED — labels MUST be success|error|dismissed|cancelled)
# ---------------------------------------------------------------------------
class TestMetricsContract:
    """Lock the BD-M metric-name + label contract."""

    def test_metric_exists_with_expected_name_and_label(self):
        """``ecm_dedup_merge_requests_total`` is registered with a ``status`` label.

        prometheus_client's Counter strips the ``_total`` suffix from
        ``_name`` (it appends it back at scrape time); ``_original_name``
        preserves the literal source string. Verify the scrape-time name
        via ``generate_latest`` for the contract that actually hits the
        wire.
        """
        from prometheus_client import generate_latest

        observability.install_metrics()
        metric = observability.get_metric("dedup_merge_requests_total")
        # The scrape-rendered output is the authoritative contract.
        rendered = generate_latest(observability.REGISTRY).decode("utf-8")
        assert "ecm_dedup_merge_requests_total" in rendered
        # Label cardinality bounded to ``status`` only.
        assert metric._labelnames == ("status",)

    def test_queue_depth_added_metric_exists(self):
        """``ecm_pending_merges_queue_depth_added_total`` is registered (BD-F emits it)."""
        from prometheus_client import generate_latest

        observability.install_metrics()
        metric = observability.get_metric("pending_merges_queue_depth_added_total")
        rendered = generate_latest(observability.REGISTRY).decode("utf-8")
        assert "ecm_pending_merges_queue_depth_added_total" in rendered
        # Label-free per cardinality discipline.
        assert metric._labelnames == ()
