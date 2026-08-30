"""Accepting a pending merge never claims a Dispatcharr update that did not happen.

Bead ``enhancedchannelmanager-i5ic0``, the most serious of its family because it
is not silence where something was expected — it is a positive success claim that
may be untrue. Accepting a pending merge whose stream-name match is ZERO or
AMBIGUOUS still flipped the row to ``merged``, wrote the audit row and returned
HTTP 200 with ``status: "merged"``. ``PendingMergesPage`` treats any non-throwing
response as success, so the operator saw the merge confirmed and the row leave
the queue — while Dispatcharr may never have been updated, and the audit trail
agreed with the false version. There was no in-product signal that anything had
been skipped, and no way to find the affected merges afterwards except by
comparing ECM against Dispatcharr by hand.

WHAT WAS NOT CHANGED, and why. ADR-008 §D6's audit-first contract is deliberate:
ambiguity is a WARN, not an abort, the queue row still reaches a terminal state,
and ``source_channel_id`` falls back to the raw ``stream_name``. Reversing that
would strand the row in the queue forever and is an architectural decision, not
a bug fix. So this takes the bead's OTHER offered shape — apply, and report
partial completion naming what was skipped:

1. The response says whether Dispatcharr was actually updated, and when it was
   not, why, in words an operator can act on.
2. The journal records what OCCURRED. A merge that could not be applied leaves
   a row saying so, which is the "find them afterwards" the bead asks for.
3. The reason distinguishes the four ways the stream can fail to resolve —
   nothing matched, several matched, the search was truncated at the page
   ceiling so a match may exist and be unreachable, and the lookup itself
   failed. A truncated search reported as "no match" is the same defect one
   layer down.
4. A merge that DID land says so, including when it landed by already being in
   the desired state.
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


def _make_merged_with_audit_row(session):
    """A row already resolved, with the audit row the endpoint requires.

    ``_latest_journal_entry_id`` raises when a terminal row has no
    ``pending_merge_journal`` entry, so a replay fixture built without one
    would exercise that guard rather than the replay.
    """
    row = _make_pending(session, status="merged", resolved_at=1,
                        resolution_source="operator")
    session.add(PendingMergeJournal(
        pending_merge_id=row.id,
        actor_token_id="anonymous",
        action_type="merge_confirmed",
        source_channel_id="ESPN HD",
        target_channel_id=row.candidate_channel_id,
        confidence_score=row.confidence,
        timestamp_utc=1_700_000_000_000,
        trigger_context="m3u_refresh",
    ))
    session.commit()
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


# --------------------------------------------------------------------------
# The finding
# --------------------------------------------------------------------------

class TestAnUnappliedMergeIsNotReportedAsApplied:

    @pytest.mark.asyncio
    async def test_zero_matches_says_dispatcharr_was_not_updated(
        self, async_client, test_session,
    ):
        """The bead's reproduction. Nothing matched, so nothing was PATCHed."""
        row = _make_pending(test_session)
        client = _client(streams=[])
        journal_double = _journal_double()

        response = await _accept(async_client, client, journal_double, row.id)

        assert response.status_code == 200, response.text
        client.update_channel.assert_not_called()
        data = response.json()
        assert data["dispatcharr_updated"] is False
        assert data["unapplied_reason"]
        # Actionable: it names the stream the operator has to go and look for.
        assert "ESPN HD" in data["unapplied_reason"]

    @pytest.mark.asyncio
    async def test_an_ambiguous_match_says_how_many_matched(
        self, async_client, test_session,
    ):
        row = _make_pending(test_session)
        client = _client(streams=[
            {"id": 100, "name": "ESPN HD"},
            {"id": 101, "name": "ESPN HD"},
        ])
        journal_double = _journal_double()

        response = await _accept(async_client, client, journal_double, row.id)

        assert response.status_code == 200, response.text
        client.update_channel.assert_not_called()
        data = response.json()
        assert data["dispatcharr_updated"] is False
        assert "2" in data["unapplied_reason"]

    @pytest.mark.asyncio
    async def test_a_truncated_search_is_not_reported_as_no_match(
        self, async_client, test_session,
    ):
        """The sibling finding, and the same defect one layer down.

        The lookup takes ONE page of ``STREAM_LOOKUP_PAGE_SIZE``. When the page
        is full the exact match may exist on a page nobody asked for, so "no
        streams matched" is a claim the search did not establish. Saying
        "nothing matched" here is what makes the operator stop looking.
        """
        row = _make_pending(test_session)
        client = _client(streams=[
            {"id": i, "name": f"ESPN HD West {i}"}
            for i in range(STREAM_LOOKUP_PAGE_SIZE)
        ])
        journal_double = _journal_double()

        response = await _accept(async_client, client, journal_double, row.id)

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["dispatcharr_updated"] is False
        reason = data["unapplied_reason"]
        assert str(STREAM_LOOKUP_PAGE_SIZE) in reason
        # It must not claim the stream is absent — the search never established
        # that. This is the assertion that would pass on a bare "no match" text
        # if the truncation were folded into the zero-match branch.
        assert "no stream" not in reason.lower()

    @pytest.mark.asyncio
    async def test_a_failed_lookup_is_not_reported_as_no_match(
        self, async_client, test_session,
    ):
        """A failed lookup never accuses a stream of not existing.

        ``_resolve_streams_by_name`` catches every exception and returns ``[]``.
        Read as "nothing matched", an outage becomes a statement about the
        operator's data.
        """
        row = _make_pending(test_session)
        client = _client(streams=[])
        client.get_streams.side_effect = RuntimeError("connection reset")
        journal_double = _journal_double()

        response = await _accept(async_client, client, journal_double, row.id)

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["dispatcharr_updated"] is False
        assert "look" in data["unapplied_reason"].lower()
        assert "no stream" not in data["unapplied_reason"].lower()


class TestAnAppliedMergeSaysSo:
    """The flag must be able to read TRUE, or it carries no information."""

    @pytest.mark.asyncio
    async def test_a_unique_match_is_patched_and_reported_as_applied(
        self, async_client, test_session,
    ):
        row = _make_pending(test_session)
        client = _client(streams=[{"id": 100, "name": "ESPN HD"}])
        journal_double = _journal_double()

        response = await _accept(async_client, client, journal_double, row.id)

        assert response.status_code == 200, response.text
        client.update_channel.assert_awaited_once_with(
            "chan-uuid-1", {"streams": [100]}
        )
        data = response.json()
        assert data["dispatcharr_updated"] is True
        assert data["unapplied_reason"] is None
        assert data["source_stream_id"] == "100"

    @pytest.mark.asyncio
    async def test_a_stream_already_on_the_channel_is_applied_without_a_patch(
        self, async_client, test_session,
    ):
        """The requested end state HOLDS, which is not the same as "skipped".

        ``_add_stream_to_channel`` skips the PATCH when the stream is already in
        the channel's list. Reporting that as not-applied would send the
        operator hunting for a discrepancy that does not exist.
        """
        row = _make_pending(test_session)
        client = _client(
            streams=[{"id": 100, "name": "ESPN HD"}], channel_streams=[100],
        )
        journal_double = _journal_double()

        response = await _accept(async_client, client, journal_double, row.id)

        assert response.status_code == 200, response.text
        client.update_channel.assert_not_called()
        data = response.json()
        assert data["dispatcharr_updated"] is True
        assert data["unapplied_reason"] is None


class TestTheJournalRecordsWhatOccurred:
    """The bead's "no way to find the affected merges afterwards"."""

    @pytest.mark.asyncio
    async def test_an_unapplied_merge_leaves_a_findable_row(
        self, async_client, test_session,
    ):
        row = _make_pending(test_session)
        client = _client(streams=[])
        journal_double = _journal_double()

        await _accept(async_client, client, journal_double, row.id)

        rows = _rows(journal_double)
        assert len(rows) == 1, rows
        # A distinct action type, so the affected merges are findable by filter
        # rather than by reading every row's prose.
        assert rows[0]["action_type"] == "merge_unapplied"
        assert rows[0]["entity_name"] == "ESPN"
        assert "ESPN HD" in rows[0]["description"]

    @pytest.mark.asyncio
    async def test_an_applied_merge_leaves_a_stream_add_row(
        self, async_client, test_session,
    ):
        """The same vocabulary the other endpoints use for the same mutation."""
        row = _make_pending(test_session)
        client = _client(streams=[{"id": 100, "name": "ESPN HD"}])
        journal_double = _journal_double()

        await _accept(async_client, client, journal_double, row.id)

        rows = _rows(journal_double)
        assert len(rows) == 1, rows
        assert rows[0]["action_type"] == "stream_add"
        assert rows[0]["after_value"]["streams"] == [100]

    @pytest.mark.asyncio
    async def test_the_pending_merge_audit_row_is_unchanged(
        self, async_client, test_session,
    ):
        """ADR-008 §D6 records the operator's DECISION, which did occur.

        Untouched on purpose: the decision is real, ``merge_confirmed`` is the
        accurate name for it, and the column carries a DB CHECK constraint that
        a new value would need a migration to widen. What was missing was a
        record of the upstream OUTCOME, which is a different fact and now lives
        in the operator-facing journal beside every other channel mutation.
        """
        row = _make_pending(test_session)
        client = _client(streams=[])
        journal_double = _journal_double()

        await _accept(async_client, client, journal_double, row.id)

        entry = test_session.query(PendingMergeJournal).filter(
            PendingMergeJournal.pending_merge_id == row.id
        ).one()
        assert entry.action_type == "merge_confirmed"
        assert entry.source_channel_id == "ESPN HD"

    @pytest.mark.asyncio
    async def test_a_failed_journal_write_is_reported_to_the_caller(
        self, async_client, test_session,
    ):
        """``log_entries`` returns ``False`` and ``log_entry`` ``None``; neither
        raises. Absorbing that is how a landed merge loses its trail in silence.
        """
        row = _make_pending(test_session)
        client = _client(streams=[{"id": 100, "name": "ESPN HD"}])
        journal_double = _journal_double()
        journal_double.log_entries.return_value = False
        journal_double.log_entry.return_value = None

        response = await _accept(async_client, client, journal_double, row.id)

        # The merge LANDED. Reporting a failure to a caller whose change
        # already applied is what makes an integrator retry it.
        assert response.status_code == 200, response.text
        client.update_channel.assert_awaited_once()
        assert response.json()["journal_rows_unwritten"] == 1

    @pytest.mark.asyncio
    async def test_a_writable_journal_reports_zero(self, async_client, test_session):
        row = _make_pending(test_session)
        client = _client(streams=[{"id": 100, "name": "ESPN HD"}])
        journal_double = _journal_double()

        response = await _accept(async_client, client, journal_double, row.id)

        assert response.json()["journal_rows_unwritten"] == 0


class TestTheIdempotentReplayDoesNotGuess:
    """A replay performed no Dispatcharr call, so it cannot claim one."""

    @pytest.mark.asyncio
    async def test_a_replay_reports_the_outcome_as_unknown_rather_than_true(
        self, async_client, test_session,
    ):
        row = _make_merged_with_audit_row(test_session)
        client = _client(streams=[])
        journal_double = _journal_double()

        response = await _accept(async_client, client, journal_double, row.id)

        assert response.status_code == 200, response.text
        data = response.json()
        # Not `True`: this request made no Dispatcharr call and has no evidence
        # about what the original one did. Defaulting to `True` here would be
        # the same false success claim the bead is about, one branch over.
        assert data["dispatcharr_updated"] is None
        assert data["unapplied_reason"]
        assert "journal" in data["unapplied_reason"].lower()
        client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_replay_writes_no_new_journal_row(
        self, async_client, test_session,
    ):
        """Nothing occurred, so nothing is recorded as having occurred."""
        row = _make_merged_with_audit_row(test_session)
        client = _client(streams=[])
        journal_double = _journal_double()

        await _accept(async_client, client, journal_double, row.id)

        assert _rows(journal_double) == []
