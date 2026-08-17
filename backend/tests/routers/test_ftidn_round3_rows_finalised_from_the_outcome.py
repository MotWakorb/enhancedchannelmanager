"""Round 3 on ``enhancedchannelmanager-ftidn`` / ``-kz089``: the row has to be
true, not just present.

Round 2 moved row construction in front of the awaits that could lose it. That
was the right move and is unchanged here. What it introduced is two ways for a
row to exist and be WRONG, plus one way for an exception to be replaced while
the rows are being written. All three are stated below as properties; the
scenario in each test is an example of the property, not its specification.

**Property 1 — a row is finalised from the outcome, never asserted before the
outcome is known.** ``assign-numbers`` computed ``starting_number + idx`` in
front of the null check, so a request that omits ``starting_number`` (which
asks Dispatcharr to choose, and which the client passes through unchanged)
raised ``TypeError`` on ``None + 0`` after the assignment had already landed
upstream — a 500 with the queue empty and no row even attempted. Dispatcharr's
own ``POST /api/channels/channels/assign/`` declares no response body beyond
"Channels have been auto-assigned!" (``swagger.json``), so the numbers it chose
are not in the response and have to be READ BACK.

**Property 2 — the row describes what happened, on every branch.** bulk-merge
queued a row saying ``Merged {n} channels into '{target}'`` at the moment the
target PATCH landed, and source ``DELETE`` failures are swallowed with
``continue``. Target patched + every delete failing therefore produced a row
claiming a completed merge while every source channel still existed. Mutating
``deleted_ids`` by reference updates the id list and corrects neither the
action nor the prose. The zero-write branch appended the same sentence for a
group where nothing at all had happened.

**Property 3 — an exception already unwinding is never replaced by one raised
while cleaning up.** ``write_journal_rows`` re-raises ``BaseException`` by
design. A handler already unwinding a ``CancelledError`` that then hits a
``SystemExit`` from the synchronous journal dependency inside its ``finally``
had the original REPLACED — turning a disconnected or cancelled request into
worker termination. This is the same precedence guard the bulk-commit executor
carries (``unwinding_base_exception``); the reasoning that a request handler
had no analogue was wrong.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _journal_double():
    double = MagicMock()
    double.log_entries.return_value = True
    double.log_entry.return_value = MagicMock()
    double.get_request_batch_id.return_value = "batch-ftidn-round3"
    return double


def _rows(journal_double):
    rows = []
    for call in journal_double.log_entries.call_args_list:
        rows.extend(call.args[0])
    for call in journal_double.log_entry.call_args_list:
        rows.append(call.kwargs)
    return rows


def _settings(*, auto_rename=False):
    settings = MagicMock()
    settings.auto_rename_channel_number = auto_rename
    return settings


# ---------------------------------------------------------------------------
# Property 1 — assign-numbers with no starting_number
# ---------------------------------------------------------------------------

class TestAssignNumbersWithoutAStartingNumber:
    """``starting_number`` is genuinely optional and means "you choose"."""

    def _client(self, *, after=None):
        """A client whose channels renumber themselves once ``assign`` runs.

        ``after`` maps channel id -> the number Dispatcharr chose. Reads before
        the assignment see the old number; reads after it see the new one, which
        is the only place the chosen numbers are observable.
        """
        client = AsyncMock()
        client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        state = {"assigned": False}
        before = {42: 900.0, 43: 901.0}
        after = after if after is not None else {42: 1.0, 43: 2.0}

        async def _get_channel(channel_id):
            numbers = after if state["assigned"] else before
            return {
                "id": channel_id,
                "name": f"Channel {channel_id}",
                "channel_number": numbers.get(channel_id),
                "streams": [],
            }

        async def _assign(channel_ids, starting_number):
            state["assigned"] = True
            return {"status": "ok"}

        client.get_channel.side_effect = _get_channel
        client.assign_channel_numbers.side_effect = _assign
        return client

    async def _post(self, async_client, client, journal_double, body):
        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.get_settings", return_value=_settings()), \
             patch("routers.channels.journal", journal_double):
            return await async_client.post("/api/channels/assign-numbers", json=body)

    @pytest.mark.asyncio
    async def test_an_omitted_starting_number_is_not_arithmetic(self, async_client):
        """The reviewer's reproduction, as an example of the property.

        Dispatcharr assigns successfully; ECM must not evaluate ``None + 0``.
        Before the fix this was a 500 with ``pending_rows`` empty — the landed
        renumber left no trace at all, which is the exact defect the whole
        branch exists to remove.
        """
        client = self._client()
        journal_double = _journal_double()

        response = await self._post(
            async_client, client, journal_double, {"channel_ids": [42, 43]},
        )

        assert response.status_code == 200, response.text
        client.assign_channel_numbers.assert_awaited_once_with([42, 43], None)
        rows = _rows(journal_double)
        assert [row["entity_id"] for row in rows] == [42, 43], rows

    @pytest.mark.asyncio
    async def test_the_row_carries_the_number_dispatcharr_chose(self, async_client):
        """Read back, not guessed.

        The upstream response carries no numbers, so the only truthful source
        for ``after_value`` is a read of the channel once the assignment has
        landed.
        """
        client = self._client(after={42: 7.0, 43: 8.5})
        journal_double = _journal_double()

        response = await self._post(
            async_client, client, journal_double, {"channel_ids": [42, 43]},
        )

        assert response.status_code == 200, response.text
        rows = _rows(journal_double)
        assert [row["before_value"]["channel_number"] for row in rows] == [900.0, 901.0]
        assert [row["after_value"]["channel_number"] for row in rows] == [7.0, 8.5]
        assert rows[0]["description"] == "Changed channel number from 900 to 7"
        assert rows[1]["description"] == "Changed channel number from 901 to 8.5"

    @pytest.mark.asyncio
    async def test_a_cancelled_read_back_still_leaves_a_row_per_channel(
        self, async_client,
    ):
        """Queued before the read-back, so the read-back cannot lose them.

        The assignment has landed upstream. A row that cannot yet name the
        chosen number says so rather than naming one, which is the same
        discipline the auto-rename path already follows for ``name``.
        """
        client = self._client()
        journal_double = _journal_double()

        calls = {"n": 0}
        original = client.get_channel.side_effect

        async def _get_channel(channel_id):
            calls["n"] += 1
            if calls["n"] > 2:  # the two pre-assignment reads have happened
                raise asyncio.CancelledError()
            return await original(channel_id)

        client.get_channel.side_effect = _get_channel

        with pytest.raises(BaseException):  # noqa: B017 — CancelledError
            await self._post(
                async_client, client, journal_double, {"channel_ids": [42, 43]},
            )

        rows = _rows(journal_double)
        assert [row["entity_id"] for row in rows] == [42, 43], rows
        assert [row["after_value"]["channel_number"] for row in rows] == [None, None]
        for row in rows:
            assert "has not read it back" in row["description"], row["description"]

    @pytest.mark.asyncio
    async def test_a_named_starting_number_is_unchanged(self, async_client):
        """Regression pin: the specified-number path keeps its arithmetic and
        performs no read-back."""
        client = self._client()
        journal_double = _journal_double()

        response = await self._post(
            async_client, client, journal_double,
            {"channel_ids": [42, 43], "starting_number": 10},
        )

        assert response.status_code == 200, response.text
        assert client.get_channel.await_count == 2
        rows = _rows(journal_double)
        assert [row["after_value"]["channel_number"] for row in rows] == [10.0, 11.0]


# ---------------------------------------------------------------------------
# Property 2 — bulk-merge rows describe the outcome
# ---------------------------------------------------------------------------

class TestBulkMergeRowsDescribeWhatHappened:

    def _client(self, *, streams=(11, 12)):
        client = AsyncMock()

        async def _get_channel(channel_id):
            return {
                "id": channel_id,
                "name": f"Channel {channel_id}",
                "streams": list(streams) if channel_id == 1 else [],
            }

        client.get_channel.side_effect = _get_channel
        client.update_channel.return_value = {}
        return client

    async def _post(self, async_client, client, journal_double, merges):
        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            return await async_client.post(
                "/api/channels/bulk-merge", json={"merges": merges},
            )

    @pytest.mark.asyncio
    async def test_a_merge_whose_sources_all_survive_is_not_journalled_as_merged(
        self, async_client,
    ):
        """The reviewer's reproduction, as an example of the property.

        The target PATCH lands and every source DELETE returns 500. The streams
        moved; nothing was merged away. A row asserting the merge completed is
        the exact untruth this branch exists to eliminate.
        """
        client = self._client()
        client.delete_channel.side_effect = RuntimeError("500 Server Error")
        journal_double = _journal_double()

        response = await self._post(
            async_client, client, journal_double,
            [{"target_channel_id": 1, "source_channel_ids": [2, 3]}],
        )

        assert response.status_code == 200, response.text
        rows = _rows(journal_double)
        assert len(rows) == 1, rows
        row = rows[0]
        assert row["action_type"] == "bulk_merge_incomplete", row
        assert "Merged 2 channels into" not in row["description"], row["description"]
        assert row["after_value"]["deleted_ids"] == []
        assert row["after_value"]["undeleted_ids"] == [2, 3]
        assert response.json()["results"][0]["sources_failed"] == 2

    @pytest.mark.asyncio
    async def test_a_partial_delete_names_the_survivors(self, async_client):
        """Not scoped to the all-fail case: the property is per-outcome."""
        client = self._client()

        async def _delete(channel_id):
            if channel_id == 3:
                raise RuntimeError("500 Server Error")

        client.delete_channel.side_effect = _delete
        journal_double = _journal_double()

        response = await self._post(
            async_client, client, journal_double,
            [{"target_channel_id": 1, "source_channel_ids": [2, 3]}],
        )

        assert response.status_code == 200, response.text
        row = _rows(journal_double)[0]
        assert row["action_type"] == "bulk_merge_incomplete", row
        assert row["after_value"]["deleted_ids"] == [2]
        assert row["after_value"]["undeleted_ids"] == [3]
        assert "1 of 2" in row["description"], row["description"]
        assert response.json()["results"][0]["sources_failed"] == 1

    @pytest.mark.asyncio
    async def test_a_group_where_nothing_landed_says_so(self, async_client):
        """The zero-write branch. No streams to move and no source deleted."""
        client = self._client(streams=())
        client.delete_channel.side_effect = RuntimeError("500 Server Error")
        journal_double = _journal_double()

        response = await self._post(
            async_client, client, journal_double,
            [{"target_channel_id": 1, "source_channel_ids": [2]}],
        )

        assert response.status_code == 200, response.text
        client.update_channel.assert_not_awaited()
        row = _rows(journal_double)[0]
        assert row["action_type"] == "bulk_merge_incomplete", row
        assert "Merged" not in row["description"], row["description"]

    @pytest.mark.asyncio
    async def test_a_complete_merge_still_reads_as_one(self, async_client):
        """Regression pin: the happy path keeps its action type and prose."""
        client = self._client()
        client.delete_channel.return_value = None
        journal_double = _journal_double()

        response = await self._post(
            async_client, client, journal_double,
            [{"target_channel_id": 1, "source_channel_ids": [2, 3]}],
        )

        assert response.status_code == 200, response.text
        row = _rows(journal_double)[0]
        assert row["action_type"] == "bulk_merge"
        assert row["after_value"]["deleted_ids"] == [2, 3]
        assert row["after_value"]["undeleted_ids"] == []
        assert response.json()["results"][0]["sources_failed"] == 0

    @pytest.mark.asyncio
    async def test_a_cancelled_delete_flushes_a_row_true_at_that_instant(
        self, async_client,
    ):
        """The row on the queue is correct at EVERY await, not only at the end.

        One source deleted, the second cancelled. The flushed row must not
        claim the group merged, and must not claim the second source is gone.
        """
        client = self._client()

        async def _delete(channel_id):
            if channel_id == 3:
                raise asyncio.CancelledError()

        client.delete_channel.side_effect = _delete
        journal_double = _journal_double()

        with pytest.raises(BaseException):  # noqa: B017 — CancelledError
            await self._post(
                async_client, client, journal_double,
                [{"target_channel_id": 1, "source_channel_ids": [2, 3]}],
            )

        row = _rows(journal_double)[0]
        assert row["action_type"] == "bulk_merge_incomplete", row
        assert row["after_value"]["deleted_ids"] == [2]
        assert row["after_value"]["undeleted_ids"] == [3]


# ---------------------------------------------------------------------------
# Property 3 — the flush never replaces what is already unwinding
# ---------------------------------------------------------------------------

class TestTheFlushDoesNotReplaceAnUnwindingException:

    def _assign_client(self):
        client = AsyncMock()

        async def _get_channel(channel_id):
            # The name carries the number, so auto-rename has a PATCH to make
            # and the cancellation below has somewhere to land.
            return {
                "id": channel_id,
                "name": "Channel 900",
                "channel_number": 900.0,
                "streams": [],
            }

        client.get_channel.side_effect = _get_channel
        client.assign_channel_numbers.return_value = {"status": "ok"}
        return client

    @pytest.mark.asyncio
    async def test_assign_numbers_keeps_the_cancellation(self):
        """``CancelledError`` in flight, ``SystemExit`` from the journal.

        The cancellation must reach the caller. Before the guard, the flush's
        ``SystemExit`` replaced it and a disconnected request became worker
        termination.

        Called as a coroutine rather than through the ASGI stack ON PURPOSE:
        Starlette's ``BaseHTTPMiddleware`` rewrites an escaping
        ``CancelledError`` into ``RuntimeError('No response returned.')`` and
        ``BaseExceptionContainmentMiddleware`` rewrites an escaping
        ``SystemExit`` into a different ``RuntimeError``. Asserting on the
        exception TYPE at the handler boundary is what makes this test able to
        tell the two apart at all.
        """
        from routers.channels import AssignNumbersRequest, assign_channel_numbers

        client = self._assign_client()
        client.update_channel.side_effect = asyncio.CancelledError()
        journal_double = _journal_double()
        journal_double.log_entries.side_effect = SystemExit("journal exits")
        journal_double.log_entry.side_effect = SystemExit("journal exits")

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.get_settings",
                   return_value=_settings(auto_rename=True)), \
             patch("routers.channels.journal", journal_double):
            with pytest.raises(asyncio.CancelledError):
                await assign_channel_numbers(
                    AssignNumbersRequest(channel_ids=[42], starting_number=1),
                    _admin=None,
                )

        client.update_channel.assert_awaited_once()
        journal_double.log_entries.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_merge_keeps_the_stale_id_422(self, async_client):
        """An ``HTTPException`` is in flight too, and must equally not be lost.

        Item one merges and is queued; item two names a source that no longer
        exists, which aborts the whole request with a 422. The flush of item
        one's row then raises ``SystemExit``. The operator-actionable 422 is
        what the caller must see.
        """
        import httpx

        client = AsyncMock()

        async def _get_channel(channel_id):
            if channel_id == 99:
                request = httpx.Request("GET", "http://x/api/channels/99/")
                raise httpx.HTTPStatusError(
                    "404", request=request,
                    response=httpx.Response(404, request=request),
                )
            return {"id": channel_id, "name": f"Channel {channel_id}", "streams": [7]}

        client.get_channel.side_effect = _get_channel
        client.update_channel.return_value = {}
        client.delete_channel.return_value = None
        journal_double = _journal_double()
        journal_double.log_entries.side_effect = SystemExit("journal exits")
        journal_double.log_entry.side_effect = SystemExit("journal exits")

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            response = await async_client.post(
                "/api/channels/bulk-merge",
                json={"merges": [
                    {"target_channel_id": 1, "source_channel_ids": [2]},
                    {"target_channel_id": 5, "source_channel_ids": [99]},
                ]},
            )

        assert response.status_code == 422, response.text
        assert "no longer exist" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_a_flush_failure_with_nothing_unwinding_still_surfaces(
        self, async_client,
    ):
        """The guard must not swallow unconditionally.

        PIN, not a red: this passes before the change too. It is here because
        the guard's whole risk is over-reach — a bare ``except BaseException:
        pass`` would uncancel a task on the ordinary return path — and on these
        three handlers the success path flushes INLINE, so this is where a
        flush failure with nothing in flight is reachable at all.

        ``BaseExceptionContainmentMiddleware`` (GH #546) converts an escaping
        ``SystemExit`` to ``RuntimeError`` before it can kill the event loop,
        so the assertion is on the CAUSE: the ``SystemExit`` reached the edge
        rather than being swallowed inside the handler.
        """
        client = self._assign_client()
        journal_double = _journal_double()
        journal_double.log_entries.side_effect = SystemExit("journal exits")
        journal_double.log_entry.side_effect = SystemExit("journal exits")

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.get_settings", return_value=_settings()), \
             patch("routers.channels.journal", journal_double):
            with pytest.raises(RuntimeError) as excinfo:
                await async_client.post(
                    "/api/channels/assign-numbers",
                    json={"channel_ids": [42], "starting_number": 1},
                )

        assert isinstance(excinfo.value.__cause__, SystemExit)
