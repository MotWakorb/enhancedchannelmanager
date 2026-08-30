"""An unapplied merge KEEPS ITS QUEUE ROW, flagged, and stays retryable.

PO decision 2026-08-16 (bead ``enhancedchannelmanager-i5ic0`` NOTES). The
previous shape — transition to ``merged``, leave the queue, carry
``dispatcharr_updated: false``, an ``unapplied_reason``, a ``merge_unapplied``
journal row and a UI notice — was confirmed internally consistent by an
external reviewer. The objection is PLACEMENT, not correctness: the reason
outlived the row, but only an operator who went looking in the journal ever
found it.

THE SHAPE, stated as invariants rather than as one reproduction:

1. A merge ECM could not apply leaves the queue row in ``pending`` with
   ``unapplied_reason`` set. ``resolved_at`` and ``resolution_source`` stay
   NULL, because the row was not resolved.
2. ALL the unapplied reasons behave identically — nothing matched, several
   matched, the lookup failed, the page truncated so uniqueness is unknown, and
   a single match with no usable id. None of them is a special case.
3. The row is still in every ``status='pending'`` consumer: the list endpoint,
   the admin snapshot the bulk accept targets, and the queue-depth gauge that
   feeds the count badge. That is the whole point of the decision.
4. A retry behaves like an ordinary accept. It is not a replay: the row never
   went terminal, so the request really does contact Dispatcharr, and on
   success the row goes ``merged`` with the flag CLEARED.
5. ``dispatcharr_updated``'s three values map onto the new state rather than
   duplicating it. ``true`` -> ``merged``, flag clear. ``false`` -> ``pending``,
   flag set. ``null`` -> only ever a replay of an already-terminal row, which a
   flagged row can no longer be. The ambiguity the bead flagged ("no upstream
   evidence" vs "deliberately still queued") disappears because the two states
   are now distinct.
6. Dismiss still resolves a flagged row — the operator's other exit must not be
   blocked by the flag.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import PendingMerge, PendingMergeJournal
from routers.channel_merges import STREAM_LOOKUP_PAGE_SIZE


def _make_pending(session, **overrides):
    fields = {
        "stream_name": "ESPN HD",
        "candidate_channel_id": "chan-uuid-1",
        "confidence": 0.91,
        "status": "pending",
        "trigger_context": "m3u_refresh",
        "created_at": 1_700_000_000_000,
        "group_id": 5,
    }
    fields.update(overrides)
    row = PendingMerge(**fields)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _client(*, streams, channel_streams=()):
    client = AsyncMock()
    client.get_channel.return_value = {
        "id": "chan-uuid-1", "name": "ESPN", "streams": list(channel_streams),
    }
    client.get_streams.return_value = {"results": list(streams)}
    return client


def _journal_double():
    double = MagicMock()
    double.log_entries.return_value = True
    double.log_entry.return_value = MagicMock()
    return double


def _rows(journal_double):
    rows = []
    for call in journal_double.log_entries.call_args_list:
        rows.extend(call.args[0])
    for call in journal_double.log_entry.call_args_list:
        rows.append(call.kwargs)
    return rows


async def _accept(async_client, client, journal_double, merge_id):
    with patch("routers.channel_merges.get_client", return_value=client), \
         patch("routers.channels.journal", journal_double):
        return await async_client.post(f"/api/channel-merges/{merge_id}/accept")


def _failed_lookup_client():
    client = _client(streams=[])
    client.get_streams.side_effect = RuntimeError("connection reset")
    return client


#: The five ways ``conclusive_match()`` refuses, as client fixtures. Every one
#: leaves the merge unapplied, and the decision is that every one behaves the
#: same — so they are a parameter, not five branches with five tests.
UNAPPLIED_LOOKUPS = {
    "nothing matched": lambda: _client(streams=[]),
    "several matched": lambda: _client(streams=[
        {"id": 100, "name": "ESPN HD"}, {"id": 101, "name": "ESPN HD"},
    ]),
    "page truncated": lambda: _client(streams=[
        {"id": i, "name": f"ESPN HD West {i}"}
        for i in range(STREAM_LOOKUP_PAGE_SIZE)
    ]),
    "no usable id": lambda: _client(streams=[{"name": "ESPN HD"}]),
    "lookup failed": _failed_lookup_client,
}


class TestAnUnappliedMergeKeepsItsQueueRow:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reason_name", sorted(UNAPPLIED_LOOKUPS))
    async def test_the_row_stays_pending_and_carries_its_reason(
        self, async_client, test_session, reason_name,
    ):
        """Invariants 1 and 2. Not scoped to the no-match case."""
        row = _make_pending(test_session)
        client = UNAPPLIED_LOOKUPS[reason_name]()
        journal_double = _journal_double()

        response = await _accept(async_client, client, journal_double, row.id)

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["dispatcharr_updated"] is False
        assert data["unapplied_reason"]
        # The envelope names the queue row's state, which is no longer always
        # terminal — a consumer that hardcodes 'merged' is reading a claim the
        # request did not make.
        assert data["status"] == "pending"

        test_session.expire_all()
        refreshed = test_session.query(PendingMerge).filter(
            PendingMerge.id == row.id
        ).one()
        assert refreshed.status == "pending"
        assert refreshed.unapplied_reason == data["unapplied_reason"]
        # NOT resolved. Both columns describe a row that left the queue.
        assert refreshed.resolved_at is None
        assert refreshed.resolution_source is None

    @pytest.mark.asyncio
    async def test_the_operator_decision_is_still_recorded(
        self, async_client, test_session,
    ):
        """The PO rejected refusing the accept outright, precisely because the
        operator's decision must be recorded even when ECM cannot apply it
        (ADR-008 §D6 audit-first). The DECISION happened; the OUTCOME is what
        the queue flag and the ``merge_unapplied`` row carry."""
        row = _make_pending(test_session)
        journal_double = _journal_double()

        await _accept(async_client, _client(streams=[]), journal_double, row.id)

        entry = test_session.query(PendingMergeJournal).filter(
            PendingMergeJournal.pending_merge_id == row.id
        ).one()
        assert entry.action_type == "merge_confirmed"
        assert _rows(journal_double)[0]["action_type"] == "merge_unapplied"

    @pytest.mark.asyncio
    async def test_the_row_is_still_in_the_queue_list(
        self, async_client, test_session,
    ):
        """Invariant 3 — the list endpoint, which is the Pending Merges page."""
        row = _make_pending(test_session)
        journal_double = _journal_double()
        await _accept(async_client, _client(streams=[]), journal_double, row.id)

        with patch("routers.channel_merges._resolve_candidate_channels",
                   new=AsyncMock(return_value={})):
            response = await async_client.get("/api/channel-merges?status=pending")

        assert response.status_code == 200, response.text
        data = response.json()
        assert [m["id"] for m in data["merges"]] == [row.id]
        assert data["merges"][0]["unapplied_reason"]

    @pytest.mark.asyncio
    async def test_the_row_is_still_in_the_bulk_accept_snapshot(
        self, async_client, test_session,
    ):
        """Invariant 3 — the admin snapshot the queue-wide bulk accept targets.

        A flagged row that fell out of this set could never be retried in bulk,
        which is half of what "remains retryable" means.
        """
        row = _make_pending(test_session)
        journal_double = _journal_double()
        await _accept(async_client, _client(streams=[]), journal_double, row.id)

        with patch("routers.channel_merges._resolve_candidate_channels",
                   new=AsyncMock(return_value={})):
            response = await async_client.get("/api/channel-merges/snapshot")

        assert response.status_code == 200, response.text
        assert [m["id"] for m in response.json()["merges"]] == [row.id]

    @pytest.mark.asyncio
    async def test_the_count_badge_still_counts_it(
        self, async_client, test_session,
    ):
        """Invariant 3 — the queue-depth gauge behind the subnav count badge.

        It reads ``COUNT(*) ... WHERE status='pending'``, so keeping the row in
        ``pending`` is what keeps the badge honest. Asserted against the query
        the gauge runs rather than the gauge object, which is process-global.
        """
        from sqlalchemy import text as sa_text

        row = _make_pending(test_session)
        journal_double = _journal_double()
        await _accept(async_client, _client(streams=[]), journal_double, row.id)

        count = test_session.execute(sa_text(
            "SELECT COUNT(*) FROM pending_merges WHERE status='pending'"
        )).scalar()
        assert count == 1, row

    @pytest.mark.asyncio
    async def test_the_uniqueness_slot_is_still_held(
        self, async_client, test_session,
    ):
        """A flagged row keeps its ``uq_pending_merges_active`` slot.

        §D5's partial unique index is on ``status='pending'``. Because the
        flagged row stays pending, a re-import of the same stream cannot queue a
        second row for the same candidate — the operator sees one flagged row,
        not a growing pile.
        """
        from sqlalchemy.exc import IntegrityError

        row = _make_pending(test_session)
        journal_double = _journal_double()
        await _accept(async_client, _client(streams=[]), journal_double, row.id)

        test_session.add(PendingMerge(
            stream_name=row.stream_name,
            candidate_channel_id=row.candidate_channel_id,
            confidence=0.91, status="pending",
            trigger_context="m3u_refresh", created_at=1_700_000_100_000,
        ))
        with pytest.raises(IntegrityError):
            test_session.commit()
        test_session.rollback()


class TestARetrySucceedsLikeAnOrdinaryAccept:

    @pytest.mark.asyncio
    async def test_a_retry_that_resolves_clears_the_flag_and_resolves_the_row(
        self, async_client, test_session,
    ):
        """Invariant 4. The stream reappears; the second accept is a real one."""
        row = _make_pending(test_session)
        journal_double = _journal_double()
        await _accept(async_client, _client(streams=[]), journal_double, row.id)

        retry_client = _client(streams=[{"id": 100, "name": "ESPN HD"}])
        retry_journal = _journal_double()
        response = await _accept(async_client, retry_client, retry_journal, row.id)

        assert response.status_code == 200, response.text
        data = response.json()
        # Not `None`: the row was never terminal, so this is not a replay. It
        # contacted Dispatcharr and has evidence.
        assert data["dispatcharr_updated"] is True
        assert data["unapplied_reason"] is None
        assert data["status"] == "merged"
        retry_client.update_channel.assert_awaited_once_with(
            "chan-uuid-1", {"streams": [100]},
        )

        test_session.expire_all()
        refreshed = test_session.query(PendingMerge).filter(
            PendingMerge.id == row.id
        ).one()
        assert refreshed.status == "merged"
        assert refreshed.unapplied_reason is None
        assert refreshed.resolved_at is not None
        assert refreshed.resolution_source == "operator"
        assert _rows(retry_journal)[0]["action_type"] == "stream_add"

    @pytest.mark.asyncio
    async def test_a_retry_that_fails_again_re_states_the_current_reason(
        self, async_client, test_session,
    ):
        """The flag is the CURRENT reason, not the first one ever recorded."""
        row = _make_pending(test_session)
        await _accept(async_client, _client(streams=[]), _journal_double(), row.id)

        ambiguous = _client(streams=[
            {"id": 100, "name": "ESPN HD"}, {"id": 101, "name": "ESPN HD"},
        ])
        response = await _accept(async_client, ambiguous, _journal_double(), row.id)

        assert response.status_code == 200, response.text
        test_session.expire_all()
        refreshed = test_session.query(PendingMerge).filter(
            PendingMerge.id == row.id
        ).one()
        assert refreshed.status == "pending"
        assert "2" in refreshed.unapplied_reason


class TestTheThreeValuedFlagMapsOntoTheState:

    @pytest.mark.asyncio
    async def test_null_is_only_ever_a_replay_of_a_terminal_row(
        self, async_client, test_session,
    ):
        """Invariant 5.

        ``null`` means "this request obtained no upstream evidence", which is
        only true of a replay. A flagged row is still ``pending``, so accepting
        it again is a RETRY that does contact Dispatcharr — and can therefore
        never come back ``null``. That is what stops the flag from having to
        carry two different meanings.
        """
        row = _make_pending(test_session)
        await _accept(async_client, _client(streams=[]), _journal_double(), row.id)

        second = await _accept(
            async_client, _client(streams=[]), _journal_double(), row.id,
        )
        assert second.json()["dispatcharr_updated"] is False

        # A genuinely terminal row still replays, and still refuses to guess.
        merged = _make_pending(
            test_session, stream_name="TNT", status="merged",
            resolved_at=1, resolution_source="operator",
        )
        test_session.add(PendingMergeJournal(
            pending_merge_id=merged.id, actor_token_id="anonymous",
            action_type="merge_confirmed", source_channel_id="TNT",
            target_channel_id=merged.candidate_channel_id,
            confidence_score=merged.confidence,
            timestamp_utc=1_700_000_000_000, trigger_context="m3u_refresh",
        ))
        test_session.commit()

        replay = await _accept(
            async_client, _client(streams=[]), _journal_double(), merged.id,
        )
        assert replay.json()["dispatcharr_updated"] is None
        assert replay.json()["status"] == "merged"


class TestDismissStillResolvesAFlaggedRow:

    @pytest.mark.asyncio
    async def test_a_flagged_row_can_still_be_dismissed(
        self, async_client, test_session,
    ):
        """Invariant 6 — the operator's other exit is not blocked by the flag."""
        row = _make_pending(test_session)
        await _accept(async_client, _client(streams=[]), _journal_double(), row.id)

        response = await async_client.post(f"/api/channel-merges/{row.id}/dismiss")

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "dismissed"
        test_session.expire_all()
        refreshed = test_session.query(PendingMerge).filter(
            PendingMerge.id == row.id
        ).one()
        assert refreshed.status == "dismissed"


class TestTheResolutionCounterStillCountsResolutions:
    """SLI-10b's numerator is "terminal-state transitions out of the queue".

    ``docs/sre/slos.md`` defines it that way and the alert
    ``ECMDedupPendingMergeResolutionStale`` fires when the ratio of those
    transitions to queue insertions falls below 95% over 24h. An accept that
    leaves its row queued is not one of them, so counting it as ``success``
    would report the queue being cleared while flagged rows piled up in it —
    and would suppress the one alert that exists to notice that. The request
    still happened, so it is still counted, under its own label: dropping it
    entirely would instead shrink SLI-10c's error-rate DENOMINATOR.
    """

    def _metric_value(self, status: str) -> float:
        import observability

        metric = observability.get_metric("dedup_merge_requests_total")
        for sample_family in metric.collect():
            for sample in sample_family.samples:
                if (
                    sample.name.endswith("_total")
                    and sample.labels.get("status") == status
                ):
                    return sample.value
        return 0.0

    @pytest.mark.asyncio
    async def test_an_unapplied_accept_is_not_counted_as_a_resolution(
        self, async_client, test_session,
    ):
        row = _make_pending(test_session)
        before_success = self._metric_value("success")
        before_unapplied = self._metric_value("unapplied")

        await _accept(async_client, _client(streams=[]), _journal_double(), row.id)

        assert self._metric_value("success") == pytest.approx(before_success)
        assert self._metric_value("unapplied") == pytest.approx(before_unapplied + 1)

    @pytest.mark.asyncio
    async def test_a_retry_that_lands_is_counted_as_one(
        self, async_client, test_session,
    ):
        """The other direction, or the label change has taken the signal away."""
        row = _make_pending(test_session)
        await _accept(async_client, _client(streams=[]), _journal_double(), row.id)
        before_success = self._metric_value("success")

        await _accept(
            async_client,
            _client(streams=[{"id": 100, "name": "ESPN HD"}]),
            _journal_double(),
            row.id,
        )

        assert self._metric_value("success") == pytest.approx(before_success + 1)
