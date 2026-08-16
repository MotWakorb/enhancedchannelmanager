"""A failed journal write on a channels endpoint is not invisible to the caller.

Bead ``enhancedchannelmanager-ftidn``. ``journal.log_entry()`` converts a write
failure into a ``None`` return and ``journal.log_entries()`` into ``False``;
neither raises. A caller that ignores the return value therefore cannot
distinguish a journalled mutation from an unjournalled one, and reports success
either way. When the journal database is read-only, unavailable or out of disk,
the upstream mutation has landed and nothing anywhere says the audit trail is
missing.

An AST sweep of ``backend/`` found 75 of 80 call sites discarding the return
value, across 22 modules. This file covers the TEN in ``routers/channels.py``,
which the bead names as the first slice: the shared writer already lives in that
module and these are the highest-traffic operator actions. The rest are left
deliberately — each needs its own decision about where the advisory hangs, and a
mechanical sweep that bolted a field onto 75 responses would change 22 modules'
response shapes without anyone having reasoned about the consumers.

THE CONTRACT, the same one ``PATCH /api/channels/{id}`` and the bulk-commit
envelope already carry: ``journalRowsUnwritten`` is the number of THIS request's
journal rows that could not be written; it is ALWAYS present, so a caller checks
a number rather than probing for a key; and it rides on the ``2xx`` rather than
turning into a ``5xx``, because the mutation LANDED and telling a caller
otherwise is what makes an integrator retry a change that already applied.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _journal_double(*, writable=True):
    """A journal that fails the way ``journal`` really fails: by returning."""
    double = MagicMock()
    double.log_entries.return_value = True if writable else False
    double.log_entry.return_value = MagicMock() if writable else None
    double.get_request_batch_id.return_value = "batch-ftidn"
    return double


def _client():
    client = AsyncMock()
    client.get_channels.return_value = {"results": [], "count": 0, "next": None}
    client.get_channel_groups.return_value = [{"id": 1, "name": "Default Group"}]
    return client


def _channel(**overrides):
    channel = {"id": 42, "name": "Forty Two", "channel_number": 42, "streams": []}
    channel.update(overrides)
    return channel


# --------------------------------------------------------------------------
# One case per endpoint: the mutation lands, the journal does not, and the
# response says so.
# --------------------------------------------------------------------------

class TestEachChannelsEndpointReportsAnUnwrittenRow:
    """The red proof for each of the ten, as one property.

    Every case asserts BOTH halves: that the upstream call was actually awaited
    (so the finding is "a landed write nobody was told about" rather than "the
    request failed early"), and that the response carries the count.
    """

    async def _call(self, async_client, client, journal_double, method, path, **kwargs):
        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            return await getattr(async_client, method)(path, **kwargs)

    @pytest.mark.asyncio
    async def test_create_channel(self, async_client):
        client = _client()
        client.create_channel.return_value = _channel()
        journal_double = _journal_double(writable=False)

        response = await self._call(
            async_client, client, journal_double,
            "post", "/api/channels", json={"name": "Forty Two"},
        )

        client.create_channel.assert_awaited_once()
        assert response.status_code == 200, response.text
        assert response.json()["journalRowsUnwritten"] == 1

    @pytest.mark.asyncio
    async def test_assign_channel_numbers(self, async_client):
        client = _client()
        client.get_channel.return_value = _channel()
        client.assign_channel_numbers.return_value = {"status": "ok"}
        journal_double = _journal_double(writable=False)

        response = await self._call(
            async_client, client, journal_double,
            "post", "/api/channels/assign-numbers",
            json={"channel_ids": [42, 43], "starting_number": 1},
        )

        client.assign_channel_numbers.assert_awaited_once()
        assert response.status_code == 200, response.text
        # One row per channel, as the endpoint writes them.
        assert response.json()["journalRowsUnwritten"] == 2

    @pytest.mark.asyncio
    async def test_clear_auto_created_flag(self, async_client):
        client = _client()
        client.get_channels.return_value = {
            "results": [
                {"id": 42, "name": "Forty Two", "channel_number": 42,
                 "auto_created": True, "channel_group_id": 7},
            ],
            "count": 1,
            "next": None,
        }
        journal_double = _journal_double(writable=False)

        response = await self._call(
            async_client, client, journal_double,
            "post", "/api/channels/clear-auto-created", json={"group_ids": [7]},
        )

        client.update_channel.assert_awaited_once()
        assert response.status_code == 200, response.text
        assert response.json()["journalRowsUnwritten"] == 1

    @pytest.mark.asyncio
    async def test_merge_channels(self, async_client):
        client = _client()
        client.get_channel.return_value = _channel()
        client.create_channel.return_value = {"id": 99, "name": "Merged"}
        journal_double = _journal_double(writable=False)

        response = await self._call(
            async_client, client, journal_double,
            "post", "/api/channels/merge",
            json={"source_channel_ids": [42, 43], "target_name": "Merged"},
        )

        client.create_channel.assert_awaited_once()
        assert response.status_code == 200, response.text
        assert response.json()["journalRowsUnwritten"] == 1

    @pytest.mark.asyncio
    async def test_delete_channel(self, async_client):
        client = _client()
        client.get_channel.return_value = _channel()
        journal_double = _journal_double(writable=False)

        response = await self._call(
            async_client, client, journal_double, "delete", "/api/channels/42",
        )

        client.delete_channel.assert_awaited_once_with(42)
        assert response.status_code == 200, response.text
        assert response.json()["journalRowsUnwritten"] == 1

    @pytest.mark.asyncio
    async def test_add_stream_to_channel(self, async_client):
        client = _client()
        client.get_channel.return_value = _channel()
        client.update_channel.return_value = _channel(streams=[7])
        journal_double = _journal_double(writable=False)

        response = await self._call(
            async_client, client, journal_double,
            "post", "/api/channels/42/add-stream", json={"stream_id": 7},
        )

        client.update_channel.assert_awaited_once()
        assert response.status_code == 200, response.text
        assert response.json()["journalRowsUnwritten"] == 1

    @pytest.mark.asyncio
    async def test_add_streams_to_channel(self, async_client):
        client = _client()
        client.get_channel.return_value = _channel()
        client.update_channel.return_value = _channel(streams=[7, 8])
        journal_double = _journal_double(writable=False)

        response = await self._call(
            async_client, client, journal_double,
            "post", "/api/channels/42/add-streams", json={"stream_ids": [7, 8]},
        )

        client.update_channel.assert_awaited_once()
        assert response.status_code == 200, response.text
        assert response.json()["journalRowsUnwritten"] == 1

    @pytest.mark.asyncio
    async def test_remove_stream_from_channel(self, async_client):
        client = _client()
        client.get_channel.return_value = _channel(streams=[7])
        client.update_channel.return_value = _channel()
        journal_double = _journal_double(writable=False)

        response = await self._call(
            async_client, client, journal_double,
            "post", "/api/channels/42/remove-stream", json={"stream_id": 7},
        )

        client.update_channel.assert_awaited_once()
        assert response.status_code == 200, response.text
        assert response.json()["journalRowsUnwritten"] == 1

    @pytest.mark.asyncio
    async def test_reorder_channel_streams(self, async_client):
        client = _client()
        client.get_channel.return_value = _channel(streams=[7, 8])
        client.update_channel.return_value = _channel(streams=[8, 7])
        journal_double = _journal_double(writable=False)

        response = await self._call(
            async_client, client, journal_double,
            "post", "/api/channels/42/reorder-streams", json={"stream_ids": [8, 7]},
        )

        client.update_channel.assert_awaited_once()
        assert response.status_code == 200, response.text
        assert response.json()["journalRowsUnwritten"] == 1

    @pytest.mark.asyncio
    async def test_bulk_merge_channels(self, async_client):
        client = _client()
        client.get_channel.return_value = _channel(streams=[7])
        journal_double = _journal_double(writable=False)

        response = await self._call(
            async_client, client, journal_double,
            "post", "/api/channels/bulk-merge",
            json={"merges": [{"target_channel_id": 42, "source_channel_ids": [43]}]},
        )

        client.delete_channel.assert_awaited()
        assert response.status_code == 200, response.text
        assert response.json()["journalRowsUnwritten"] == 1


# --------------------------------------------------------------------------
# The field must be able to read zero, or it carries no information
# --------------------------------------------------------------------------

class TestAWritableJournalReportsZeroRatherThanOmittingTheKey:
    """Always present, so a caller checks the number rather than probing.

    A key that appeared only on the bad path would put the burden back on the
    caller to know which shape it is holding — the reason the bulk envelope and
    the direct PATCH both state this contract for the same key.
    """

    async def _call(self, async_client, client, method, path, **kwargs):
        journal_double = _journal_double()
        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            return await getattr(async_client, method)(path, **kwargs)

    @pytest.mark.asyncio
    async def test_create_channel(self, async_client):
        client = _client()
        client.create_channel.return_value = _channel()
        response = await self._call(
            async_client, client, "post", "/api/channels", json={"name": "Forty Two"},
        )
        assert response.json()["journalRowsUnwritten"] == 0

    @pytest.mark.asyncio
    async def test_delete_channel(self, async_client):
        client = _client()
        client.get_channel.return_value = _channel()
        response = await self._call(
            async_client, client, "delete", "/api/channels/42",
        )
        assert response.json()["journalRowsUnwritten"] == 0

    @pytest.mark.asyncio
    async def test_add_streams_to_channel(self, async_client):
        client = _client()
        client.get_channel.return_value = _channel()
        client.update_channel.return_value = _channel(streams=[7])
        response = await self._call(
            async_client, client, "post", "/api/channels/42/add-streams",
            json={"stream_ids": [7]},
        )
        assert response.json()["journalRowsUnwritten"] == 0

    @pytest.mark.asyncio
    async def test_a_no_op_add_stream_still_carries_the_key(self, async_client):
        """The stream was already there, so nothing was written and nothing
        was journalled — and the caller still gets the same shape."""
        client = _client()
        client.get_channel.return_value = _channel(streams=[7])
        response = await self._call(
            async_client, client, "post", "/api/channels/42/add-stream",
            json={"stream_id": 7},
        )
        client.update_channel.assert_not_awaited()
        assert response.json()["journalRowsUnwritten"] == 0

    @pytest.mark.asyncio
    async def test_a_no_op_remove_stream_still_carries_the_key(self, async_client):
        client = _client()
        client.get_channel.return_value = _channel()
        response = await self._call(
            async_client, client, "post", "/api/channels/42/remove-stream",
            json={"stream_id": 7},
        )
        client.update_channel.assert_not_awaited()
        assert response.json()["journalRowsUnwritten"] == 0


# --------------------------------------------------------------------------
# The ONE mechanism, not ten
# --------------------------------------------------------------------------

class TestTheSharedWriterIsWhatRuns:

    @pytest.mark.asyncio
    async def test_a_batch_failure_is_retried_row_by_row(self, async_client):
        """``write_journal_rows``' retry is why one unwritable row does not
        take every other row's audit trail with it. A second implementation
        here would be a second thing to keep in step."""
        client = _client()
        client.get_channel.return_value = _channel()
        client.assign_channel_numbers.return_value = {"status": "ok"}
        journal_double = _journal_double()
        journal_double.log_entries.return_value = False
        journal_double.log_entry.return_value = MagicMock()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            response = await async_client.post(
                "/api/channels/assign-numbers",
                json={"channel_ids": [42, 43], "starting_number": 1},
            )

        assert response.status_code == 200, response.text
        assert response.json()["journalRowsUnwritten"] == 0
        # ONE batch write for the whole assignment, not one per channel: that
        # is what keeps a several-hundred-channel renumber from becoming
        # several hundred transactions.
        assert journal_double.log_entries.call_count == 1
        assert journal_double.log_entry.call_count == 2

    @pytest.mark.asyncio
    async def test_the_assignment_rows_share_one_batch_id(self, async_client):
        """Correlated as one action, which the per-row loop already intended."""
        client = _client()
        client.get_channel.return_value = _channel()
        client.assign_channel_numbers.return_value = {"status": "ok"}
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            await async_client.post(
                "/api/channels/assign-numbers",
                json={"channel_ids": [42, 43], "starting_number": 1},
            )

        rows = journal_double.log_entries.call_args_list[0].args[0]
        assert len({row["batch_id"] for row in rows}) == 1

    @pytest.mark.asyncio
    async def test_an_upstream_answer_that_is_not_an_object_is_named_in_the_log(
        self, async_client
    ):
        """There is nowhere to hang the advisory, so the omission is stated.

        Dispatcharr answering with something that is not an object is the one
        case where the caller genuinely cannot be told. Leaving that to be
        inferred is how a missing field becomes indistinguishable from a field
        that was never set.
        """
        client = _client()
        client.get_channel.return_value = _channel()
        client.update_channel.return_value = ["not", "an", "object"]
        journal_double = _journal_double(writable=False)

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double), \
             patch("routers.channels.logger") as log:
            response = await async_client.post(
                "/api/channels/42/add-stream", json={"stream_id": 7},
            )

        assert response.status_code == 200, response.text
        logged = " ".join(str(call.args) for call in log.error.call_args_list)
        assert "not an object" in logged
