"""assign-numbers and bulk-merge queue each row the moment its write lands.

Fix round on beads ``enhancedchannelmanager-ftidn`` / ``-kz089``. Batching the
journal writes was right — a several-hundred-channel renumber must not become
several hundred transactions, and ``write_journal_rows`` already retries rows
individually when the batch write fails. What batching moved, and what these
tests pin back, is WHEN the rows come into existence.

Both endpoints built their rows only after several further ``await``s:

* ``assign-numbers`` — the bulk number assignment lands, then the auto-rename
  loop PATCHes each renamed channel. A cancellation during the first rename
  unwinds through ``except Exception`` (which does not catch ``CancelledError``,
  a ``BaseException``) with no rows constructed and nothing flushed. The
  renumber is already true upstream and nothing records it.
* ``bulk-merge`` — the target's stream update lands and sources are deleted one
  at a time, and the item's row was appended only after the last deletion
  returned. A cancellation during the second deletion loses the row for the
  merge AND for the source channel that was already deleted — and this endpoint
  DELETES those channels, so its rows are the only remaining record that they
  existed.

The invariant, stated as a property: **a journal row exists from the moment its
write lands, and every exit — return, ``HTTPException``, ``Exception``,
``BaseException``, cancellation — flushes what is queued.** The same shape the
immediate group-delete path already uses (``routers/channel_groups.py``): a
pending list, a drain-then-write ``flush_rows`` that is idempotent, and a
``try/finally``.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _journal_double():
    double = MagicMock()
    double.log_entries.return_value = True
    double.log_entry.return_value = MagicMock()
    double.get_request_batch_id.return_value = "batch-ftidn-round2"
    return double


def _rows(journal_double):
    rows = []
    for call in journal_double.log_entries.call_args_list:
        rows.extend(call.args[0])
    for call in journal_double.log_entry.call_args_list:
        rows.append(call.kwargs)
    return rows


def _settings(*, auto_rename):
    settings = MagicMock()
    settings.auto_rename_channel_number = auto_rename
    return settings


# --------------------------------------------------------------------------
# assign-numbers
# --------------------------------------------------------------------------

class TestAssignNumbersJournalsTheRenumberBeforeTheRenames:

    def _client(self):
        client = AsyncMock()
        client.get_channels.return_value = {"results": [], "count": 0, "next": None}

        async def _get_channel(channel_id):
            return {
                "id": channel_id,
                "name": f"Channel {channel_id}",
                "channel_number": channel_id,
                "streams": [],
            }

        client.get_channel.side_effect = _get_channel
        client.assign_channel_numbers.return_value = {"status": "ok"}
        return client

    async def _post(self, async_client, client, journal_double, *, auto_rename=True):
        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.get_settings",
                   return_value=_settings(auto_rename=auto_rename)), \
             patch("routers.channels.journal", journal_double):
            return await async_client.post(
                "/api/channels/assign-numbers",
                json={"channel_ids": [42, 43], "starting_number": 1},
            )

    @pytest.mark.asyncio
    async def test_a_cancelled_rename_does_not_lose_the_renumber_rows(
        self, async_client,
    ):
        """The reviewer's reproduction, as an example of the invariant.

        ``assign_channel_numbers`` lands both numbers. The first auto-rename
        PATCH is then cancelled. ``CancelledError`` inherits from
        ``BaseException``, so neither the per-rename ``except Exception`` nor
        the endpoint's outer one sees it — and before this fix no row had been
        built, so the renumber left no trace at all.
        """
        client = self._client()
        client.update_channel.side_effect = asyncio.CancelledError()
        journal_double = _journal_double()

        with pytest.raises(BaseException):  # noqa: B017 — see the module docstring
            await self._post(async_client, client, journal_double)

        client.assign_channel_numbers.assert_awaited_once()
        rows = _rows(journal_double)
        assert [row["entity_id"] for row in rows] == [42, 43], rows
        assert {row["action_type"] for row in rows} == {"reorder"}
        assert len({row["batch_id"] for row in rows}) == 1

    @pytest.mark.asyncio
    async def test_a_rename_that_never_landed_is_not_recorded_as_a_new_name(
        self, async_client,
    ):
        """The row says what is true at the moment it is written.

        The renumber landed and the rename did not, so the channel's name
        upstream is still the old one. Recording the PLANNED name would be the
        same false claim in the other direction.
        """
        client = self._client()
        client.update_channel.side_effect = RuntimeError("502 Bad Gateway")
        journal_double = _journal_double()

        response = await self._post(async_client, client, journal_double)

        assert response.status_code == 200, response.text
        rows = _rows(journal_double)
        assert [row["after_value"]["name"] for row in rows] == [
            "Channel 42", "Channel 43",
        ]

    @pytest.mark.asyncio
    async def test_a_rename_that_landed_is_recorded_on_the_row(self, async_client):
        """The other direction — the field has to be able to read the new name.

        Channel 42 is numbered 1, so ``Channel 42`` becomes ``Channel 1``.
        """
        client = self._client()
        journal_double = _journal_double()

        response = await self._post(async_client, client, journal_double)

        assert response.status_code == 200, response.text
        rows = _rows(journal_double)
        assert [row["after_value"]["name"] for row in rows] == [
            "Channel 1", "Channel 2",
        ]
        assert [row["after_value"]["channel_number"] for row in rows] == [1, 2]

    @pytest.mark.asyncio
    async def test_the_ordinary_path_still_writes_one_batch(self, async_client):
        """PIN. Batching is not what was wrong, and must not regress."""
        client = self._client()
        journal_double = _journal_double()

        response = await self._post(
            async_client, client, journal_double, auto_rename=False,
        )

        assert response.status_code == 200, response.text
        assert response.json()["journalRowsUnwritten"] == 0
        assert journal_double.log_entries.call_count == 1
        assert len(journal_double.log_entries.call_args_list[0].args[0]) == 2

    @pytest.mark.asyncio
    async def test_the_rows_are_not_written_twice(self, async_client):
        """The flush drains its queue, so the ``finally`` cannot re-write it."""
        client = self._client()
        journal_double = _journal_double()

        await self._post(async_client, client, journal_double, auto_rename=False)

        assert len(_rows(journal_double)) == 2


# --------------------------------------------------------------------------
# bulk-merge
# --------------------------------------------------------------------------

class TestBulkMergeJournalsEachItemAsItsWritesLand:

    #: The merge targets used below. Only a target holds streams, so only a
    #: target's ``update_channel`` lands — which is what decides whether a
    #: group has a landed write to journal at all.
    TARGET_IDS = (42, 50)

    def _client(self, *, target_streams=(7,)):
        client = AsyncMock()
        client.get_channels.return_value = {"results": [], "count": 0, "next": None}

        async def _get_channel(channel_id):
            return {
                "id": channel_id,
                "name": f"Channel {channel_id}",
                "channel_number": channel_id,
                "streams": (list(target_streams)
                            if channel_id in self.TARGET_IDS else []),
            }

        client.get_channel.side_effect = _get_channel
        return client

    async def _post(self, async_client, client, journal_double, merges):
        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            return await async_client.post(
                "/api/channels/bulk-merge", json={"merges": merges},
            )

    @pytest.mark.asyncio
    async def test_a_cancelled_deletion_does_not_lose_the_earlier_one(
        self, async_client,
    ):
        """The reviewer's reproduction, as an example of the invariant.

        The target's stream update lands, source 43 is deleted, and the
        deletion of source 44 is cancelled. Channel 43 is gone and this row is
        the only remaining record that it existed.
        """
        client = self._client()
        client.delete_channel.side_effect = [None, asyncio.CancelledError()]
        journal_double = _journal_double()

        with pytest.raises(BaseException):  # noqa: B017 — see the module docstring
            await self._post(async_client, client, journal_double, [
                {"target_channel_id": 42, "source_channel_ids": [43, 44]},
            ])

        rows = _rows(journal_double)
        assert len(rows) == 1, rows
        # `bulk_merge_incomplete`, not `bulk_merge`. Round 2 pinned the latter
        # here and that was the wrong expectation: source 44 is still upstream,
        # so this group did not merge. Corrected in round 3 along with the
        # defect it was pinning — see
        # ``test_ftidn_round3_rows_finalised_from_the_outcome.py``.
        assert rows[0]["action_type"] == "bulk_merge_incomplete"
        assert rows[0]["entity_id"] == 42
        assert rows[0]["after_value"]["deleted_ids"] == [43]
        assert rows[0]["after_value"]["undeleted_ids"] == [44]

    @pytest.mark.asyncio
    async def test_an_earlier_item_survives_a_later_items_cancellation(
        self, async_client,
    ):
        """Two merge groups; the second is cancelled mid-deletion."""
        client = self._client()
        client.delete_channel.side_effect = [None, asyncio.CancelledError()]
        journal_double = _journal_double()

        with pytest.raises(BaseException):  # noqa: B017 — see the module docstring
            await self._post(async_client, client, journal_double, [
                {"target_channel_id": 42, "source_channel_ids": [43]},
                {"target_channel_id": 50, "source_channel_ids": [51]},
            ])

        rows = _rows(journal_double)
        assert [row["entity_id"] for row in rows] == [42, 50], rows
        assert rows[0]["after_value"]["deleted_ids"] == [43]
        # The second group's target update landed; its deletion did not.
        assert rows[1]["after_value"]["deleted_ids"] == []

    @pytest.mark.asyncio
    async def test_a_stale_id_exit_still_flushes_the_earlier_item(
        self, async_client,
    ):
        """PIN. The 422 exit already flushed; the restructure keeps it."""
        client = self._client()

        async def _get_channel(channel_id):
            if channel_id == 99:
                raise _not_found()
            return {
                "id": channel_id, "name": f"Channel {channel_id}",
                "channel_number": channel_id,
                "streams": [7] if channel_id == 42 else [],
            }

        client.get_channel.side_effect = _get_channel
        journal_double = _journal_double()

        response = await self._post(async_client, client, journal_double, [
            {"target_channel_id": 42, "source_channel_ids": [43]},
            {"target_channel_id": 99, "source_channel_ids": [51]},
        ])

        assert response.status_code == 422, response.text
        rows = _rows(journal_double)
        assert [row["entity_id"] for row in rows] == [42]

    @pytest.mark.asyncio
    async def test_the_ordinary_path_still_writes_one_batch(self, async_client):
        """PIN. One transaction for the whole bulk merge, as before."""
        client = self._client()
        journal_double = _journal_double()

        response = await self._post(async_client, client, journal_double, [
            {"target_channel_id": 42, "source_channel_ids": [43]},
            {"target_channel_id": 50, "source_channel_ids": [51]},
        ])

        assert response.status_code == 200, response.text
        assert response.json()["journalRowsUnwritten"] == 0
        assert journal_double.log_entries.call_count == 1
        assert len(_rows(journal_double)) == 2

    @pytest.mark.asyncio
    async def test_an_item_whose_writes_all_failed_still_reports_its_outcome(
        self, async_client,
    ):
        """PIN. The row set and the reported outcomes stay in step.

        A group whose target has no streams to move and whose only deletion
        fails performed no write at all — but it is still one of the outcomes
        the envelope reports, so it keeps its row rather than silently
        vanishing from the trail.
        """
        client = self._client(target_streams=())
        client.delete_channel.side_effect = RuntimeError("502 Bad Gateway")
        journal_double = _journal_double()

        response = await self._post(async_client, client, journal_double, [
            {"target_channel_id": 42, "source_channel_ids": [43]},
        ])

        assert response.status_code == 200, response.text
        assert response.json()["merged"] == 1
        rows = _rows(journal_double)
        assert len(rows) == 1, rows
        assert rows[0]["after_value"]["deleted_ids"] == []


def _not_found():
    """An ``httpx.HTTPStatusError`` carrying a 404, as Dispatcharr raises."""
    import httpx

    request = httpx.Request("GET", "http://dispatcharr/api/channels/99/")
    response = httpx.Response(404, request=request)
    return httpx.HTTPStatusError("404", request=request, response=response)
