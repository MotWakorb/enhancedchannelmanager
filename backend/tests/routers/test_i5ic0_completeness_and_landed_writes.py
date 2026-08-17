"""A lookup of unknown completeness is never conclusive, and a landed PATCH always leaves a row.

Fix round on bead ``enhancedchannelmanager-i5ic0``. The round-1 fix built
:class:`StreamLookup` precisely so a page ceiling would stop being read as
evidence — then consulted ``truncated`` on the FAILURE path only. One usable
exact match on a full page still went straight to the PATCH and reported
``dispatcharr_updated: true``, having established nothing about uniqueness: the
500th result is not the last result, and the stream chosen may be arbitrary
among duplicates.

The two invariants pinned here are properties, not the two reproductions that
exposed them:

1. **Completeness is a precondition of conclusiveness, in either direction.**
   ``failed`` and ``truncated`` each mean the search did not see everything it
   asked about. Neither may be read as "the stream is absent" NOR as "this is
   the stream". :meth:`StreamLookup.conclusive_match` is the only way to obtain
   a stream id from a lookup, and it answers ``None`` for both — so the accept
   path cannot reach the PATCH without a complete lookup, rather than having
   one more branch that remembers to check.

2. **A landed upstream write always leaves a journal row.** The ``stream_add``
   row used to be CONSTRUCTED after ``db.commit()`` succeeded, so a commit
   failure after a landed PATCH returned 500 with the stream already attached
   upstream and nothing recording it. A retry then reads as "already in the
   desired state" and conceals which request performed the mutation. The row is
   now queued the moment the PATCH returns and flushed through a ``finally``,
   and the fallible local write is ATTEMPTED (``db.flush()``) before the
   irreversible remote one so the ordinary persistence failures — read-only
   database, disk full, constraint — cannot strand a landed PATCH at all.
"""
import itertools
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import PendingMerge
from routers.channel_merges import STREAM_LOOKUP_PAGE_SIZE, StreamLookup


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


def _page(*, exact_matches, id_usable=True, fill_to_ceiling):
    """A ``get_streams`` result page with ``exact_matches`` exact-name hits.

    ``fill_to_ceiling`` pads with non-matching names until the page holds
    ``STREAM_LOOKUP_PAGE_SIZE`` rows, which is what makes the lookup truncated.
    The padding never matches, so the number of exact matches is independent of
    whether the page is full — the whole point of the matrix below.
    """
    results = [
        {"id": (100 + i) if id_usable else None, "name": "ESPN HD"}
        for i in range(exact_matches)
    ]
    if fill_to_ceiling:
        filler = STREAM_LOOKUP_PAGE_SIZE - len(results)
        results += [
            {"id": 900 + i, "name": f"ESPN HD West {i}"} for i in range(filler)
        ]
    return results


async def _accept(async_client, client, journal_double, merge_id):
    with patch("routers.channel_merges.get_client", return_value=client), \
         patch("routers.channels.journal", journal_double):
        return await async_client.post(f"/api/channel-merges/{merge_id}/accept")


# --------------------------------------------------------------------------
# Invariant 1, at the chokepoint itself
# --------------------------------------------------------------------------

class TestOnlyACompleteLookupCanBeConclusive:
    """PIN. Unit-level, on the type — the accept path has no other door.

    These assert the SHAPE of :class:`StreamLookup`, not a behaviour of the
    endpoint. Reading ``matches[0]["id"]`` directly is what the round-1 code
    did; a helper that answers ``None`` whenever completeness is unknown is the
    only fix that generalises to the next caller.
    """

    def test_a_complete_unique_match_is_conclusive(self):
        lookup = StreamLookup(
            matches=[{"id": 10, "name": "ESPN HD"}], truncated=False, failed=False,
        )
        assert lookup.complete is True
        assert lookup.conclusive_match() == {"id": 10, "name": "ESPN HD"}

    def test_a_truncated_page_with_one_visible_match_is_not_conclusive(self):
        """The reviewer's finding, at the point the decision is made.

        Page one holds exactly one stream named ``ESPN HD``. Page two, which
        nobody asked for, may hold another. One visible match is not a unique
        match.
        """
        lookup = StreamLookup(
            matches=[{"id": 10, "name": "ESPN HD"}], truncated=True, failed=False,
        )
        assert lookup.complete is False
        assert lookup.conclusive_match() is None

    def test_a_failed_lookup_with_one_visible_match_is_not_conclusive(self):
        lookup = StreamLookup(
            matches=[{"id": 10, "name": "ESPN HD"}], truncated=False, failed=True,
        )
        assert lookup.conclusive_match() is None

    def test_an_unusable_id_is_not_conclusive(self):
        lookup = StreamLookup(
            matches=[{"id": None, "name": "ESPN HD"}], truncated=False, failed=False,
        )
        assert lookup.conclusive_match() is None

    def test_several_matches_are_not_conclusive(self):
        lookup = StreamLookup(
            matches=[{"id": 10, "name": "ESPN HD"}, {"id": 11, "name": "ESPN HD"}],
            truncated=False, failed=False,
        )
        assert lookup.conclusive_match() is None

    def test_no_match_is_not_conclusive(self):
        assert StreamLookup(matches=[], truncated=False, failed=False) \
            .conclusive_match() is None


# --------------------------------------------------------------------------
# Invariant 1, as a property of the endpoint over the whole input space
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "failed,truncated,exact_matches,id_usable",
    list(itertools.product([False, True], [False, True], [0, 1, 2], [True, False])),
)
class TestTheEndpointPatchesOnlyOnAConclusiveLookup:
    """Every combination of the four facts a lookup can carry.

    The reproduction (truncated × one match × usable id) is ONE cell of this
    table. Stating the acceptance criterion as the table rather than as the cell
    is what stops the next fix from closing the demonstrated case and leaving
    the property live — which is exactly how round 1 shipped.
    """

    @pytest.mark.asyncio
    async def test_the_patch_happens_exactly_when_the_lookup_is_conclusive(
        self, async_client, test_session, failed, truncated, exact_matches, id_usable,
    ):
        row = _make_pending(test_session)
        client = _client(streams=_page(
            exact_matches=exact_matches,
            id_usable=id_usable,
            fill_to_ceiling=truncated,
        ))
        if failed:
            client.get_streams.side_effect = RuntimeError("connection reset")
        journal_double = _journal_double()

        response = await _accept(async_client, client, journal_double, row.id)

        conclusive = (
            not failed and not truncated and exact_matches == 1 and id_usable
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["dispatcharr_updated"] is conclusive
        if conclusive:
            client.update_channel.assert_awaited_once()
            assert data["unapplied_reason"] is None
        else:
            client.update_channel.assert_not_called()
            # Never silence: an operator who is not told cannot act.
            assert data["unapplied_reason"]
            assert "ESPN HD" in data["unapplied_reason"]

    @pytest.mark.asyncio
    async def test_an_inconclusive_lookup_leaves_a_findable_row(
        self, async_client, test_session, failed, truncated, exact_matches, id_usable,
    ):
        row = _make_pending(test_session)
        client = _client(streams=_page(
            exact_matches=exact_matches,
            id_usable=id_usable,
            fill_to_ceiling=truncated,
        ))
        if failed:
            client.get_streams.side_effect = RuntimeError("connection reset")
        journal_double = _journal_double()

        await _accept(async_client, client, journal_double, row.id)

        conclusive = (
            not failed and not truncated and exact_matches == 1 and id_usable
        )
        rows = _rows(journal_double)
        assert len(rows) == 1, rows
        assert rows[0]["action_type"] == ("stream_add" if conclusive
                                          else "merge_unapplied")


class TestATruncatedPageSaysWhatItActuallySaw:
    """The reason has to be true of the case at hand, or it misdirects.

    Round 1's truncation prose said the page filled "without an exact match" —
    which is false in the reproduction, where an exact match IS visible and the
    unknown is whether it is the only one. An operator told there is no match
    stops looking for a duplicate.
    """

    @pytest.mark.asyncio
    async def test_a_visible_match_on_a_full_page_is_not_called_absent(
        self, async_client, test_session,
    ):
        row = _make_pending(test_session)
        client = _client(streams=_page(
            exact_matches=1, fill_to_ceiling=True,
        ))
        journal_double = _journal_double()

        response = await _accept(async_client, client, journal_double, row.id)

        reason = response.json()["unapplied_reason"]
        assert str(STREAM_LOOKUP_PAGE_SIZE) in reason
        assert "without an exact match" not in reason
        # And it must not claim absence either — the search established neither.
        assert "no stream" not in reason.lower()


# --------------------------------------------------------------------------
# Invariant 2 — a landed write always leaves a row
# --------------------------------------------------------------------------

class TestALandedPatchIsJournalledWhateverFailsAfterwards:

    @pytest.mark.asyncio
    async def test_a_commit_failure_after_the_patch_still_writes_the_row(
        self, async_client, test_session,
    ):
        """The reviewer's reproduction, as an example of invariant 2.

        The lookup is unique, Dispatcharr accepts the PATCH, and the journal DB
        commit then fails. ECM answers 500 and the queue row rolls back to
        pending — but the stream IS attached upstream, and that fact has to
        survive the rollback or a retry reads as "already in the desired state"
        with nothing naming the request that did it.
        """
        row = _make_pending(test_session)
        client = _client(streams=[{"id": 100, "name": "ESPN HD"}])
        journal_double = _journal_double()

        with patch.object(
            test_session, "commit", side_effect=RuntimeError("database is locked"),
        ):
            response = await _accept(async_client, client, journal_double, row.id)

        assert response.status_code == 500, response.text
        client.update_channel.assert_awaited_once()
        rows = _rows(journal_double)
        assert len(rows) == 1, rows
        assert rows[0]["action_type"] == "stream_add"
        assert rows[0]["after_value"]["streams"] == [100]

    @pytest.mark.asyncio
    async def test_a_persistence_failure_happens_before_any_upstream_write(
        self, async_client, test_session,
    ):
        """The window is closed, not merely reported.

        A read-only or full journal database fails when the rows are FLUSHED,
        which now happens before the PATCH. So the ordinary persistence failure
        answers 500 having mutated nothing at all — which is a 500 that is true.
        """
        row = _make_pending(test_session)
        client = _client(streams=[{"id": 100, "name": "ESPN HD"}])
        journal_double = _journal_double()

        with patch.object(
            test_session, "flush", side_effect=RuntimeError("readonly database"),
        ):
            response = await _accept(async_client, client, journal_double, row.id)

        assert response.status_code == 500, response.text
        client.update_channel.assert_not_called()
        assert _rows(journal_double) == []

    @pytest.mark.asyncio
    async def test_a_cancellation_after_the_patch_still_writes_the_row(
        self, async_client, test_session,
    ):
        """``CancelledError`` is a ``BaseException``, so no ``except Exception``
        clause on this path sees it. Application shutdown is the ordinary way
        it arrives, not an exotic one.

        The assertion is deliberately on the ROW rather than on the exception
        type: Starlette's ``BaseHTTPMiddleware`` turns a request that produced
        no response into a ``RuntimeError`` at the transport boundary, so what
        surfaces to the caller is an implementation detail of the stack. What
        must be true either way is that the landed PATCH left its row.
        """
        import asyncio

        row = _make_pending(test_session)
        client = _client(streams=[{"id": 100, "name": "ESPN HD"}])
        journal_double = _journal_double()

        with patch.object(test_session, "commit", side_effect=asyncio.CancelledError()):
            with pytest.raises(BaseException):  # noqa: B017 — see docstring
                await _accept(async_client, client, journal_double, row.id)

        client.update_channel.assert_awaited_once()
        rows = _rows(journal_double)
        assert len(rows) == 1, rows
        assert rows[0]["action_type"] == "stream_add"

    @pytest.mark.asyncio
    async def test_the_queue_row_is_still_pending_after_a_failed_commit(
        self, async_client, test_session,
    ):
        """The 500 is not a lie about the QUEUE row — that transition really
        did roll back. What it must not do is bury the upstream write."""
        row = _make_pending(test_session)
        client = _client(streams=[{"id": 100, "name": "ESPN HD"}])
        journal_double = _journal_double()

        with patch.object(
            test_session, "commit", side_effect=RuntimeError("database is locked"),
        ):
            await _accept(async_client, client, journal_double, row.id)

        test_session.rollback()
        refreshed = test_session.query(PendingMerge).filter(
            PendingMerge.id == row.id
        ).one()
        assert refreshed.status == "pending"

    @pytest.mark.asyncio
    async def test_the_landed_write_is_named_in_the_log_the_500_scrubs(
        self, async_client, test_session,
    ):
        """``sanitized_http_exception_handler`` replaces the detail of every
        500, so the log is the only place this advisory can reach a human."""
        row = _make_pending(test_session)
        client = _client(streams=[{"id": 100, "name": "ESPN HD"}])
        journal_double = _journal_double()

        with patch.object(
            test_session, "commit", side_effect=RuntimeError("database is locked"),
        ), patch("routers.channel_merges.logger") as log:
            await _accept(async_client, client, journal_double, row.id)

        logged = " ".join(str(call.args) for call in log.error.call_args_list)
        assert "chan-uuid-1" in logged
        assert "100" in logged
