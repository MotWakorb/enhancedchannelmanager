"""The journal must never name a starting number ECM did not send.

A ``bulkAssignChannelNumbers`` operation with no ``startingNumber`` reaches
Dispatcharr as "choose the numbers yourself" whenever consolidation is off,
because consolidation is the only step that materialises the omitted start.
The rows for that operation therefore cannot name a target number: nothing
sent one, and the assign endpoint returns none.

Every case also pins the exact outgoing ``assign_channel_numbers`` call, so
the journal change provably leaves the wire alone.
"""
from unittest.mock import AsyncMock, call, patch

import pytest

from routers.channels import BulkCommitRequest, _run_bulk_commit


CHANNELS = [
    {"id": 1, "name": "Alpha", "channel_number": 50},
    {"id": 2, "name": "Bravo", "channel_number": 51},
]


def make_client(channels):
    mock_client = AsyncMock()
    mock_client.get_channels.return_value = {"results": [dict(c) for c in channels], "next": None}
    mock_client.get_streams_by_ids.side_effect = lambda ids: []
    mock_client.get_channel_groups.return_value = []
    by_id = {ch["id"]: ch for ch in channels}
    mock_client.get_channel.side_effect = lambda cid: dict(by_id.get(cid, {"id": cid}))
    mock_client.assign_channel_numbers.return_value = {"success": True}
    mock_client.update_channel.return_value = {}
    return mock_client


def rows_from(mock_journal):
    rows = []
    for c in mock_journal.log_entries.call_args_list:
        rows.extend(c.args[0] if c.args else c.kwargs["entries"])
    return rows


async def run_assign(operation, *, consolidate):
    client = make_client(CHANNELS)
    request = BulkCommitRequest(operations=[operation], consolidate=consolidate)
    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.journal") as mock_journal:
        result = await _run_bulk_commit(request, batch_id="batch-1")
    return result, client, rows_from(mock_journal)


def assert_no_invented_number(row):
    description = row["description"]
    assert "Dispatcharr chose the new number" in description, description
    assert " to " not in description, description
    for absent_marker in ("None", "null", "undefined", "NaN"):
        assert absent_marker not in description, description
    # Absent, not null: null would claim the channel now carries no number.
    assert "channel_number" not in row["after_value"], row["after_value"]


class TestConsolidationOff:

    @pytest.mark.asyncio
    async def test_omitted_start_journals_no_starting_number(self):
        result, client, rows = await run_assign(
            {"type": "bulkAssignChannelNumbers", "channelIds": [1, 2]},
            consolidate=False,
        )

        assert result["success"] is True
        assert client.assign_channel_numbers.call_args_list == [call([1, 2], None)]
        assert [r["entity_name"] for r in rows] == ["Alpha", "Bravo"]
        assert rows[0]["description"] == (
            "Changed channel number from 50; Dispatcharr chose the new number "
            "and ECM has not read it back"
        )
        assert rows[1]["description"] == (
            "Changed channel number from 51; Dispatcharr chose the new number "
            "and ECM has not read it back"
        )
        for row in rows:
            assert_no_invented_number(row)
        assert rows[0]["before_value"] == {"channel_number": 50, "name": "Alpha"}
        assert rows[0]["after_value"] == {"name": "Alpha"}

    @pytest.mark.asyncio
    async def test_omitted_start_journals_a_channel_whose_number_was_1(self):
        """Old code skipped a channel already on its assumed target. With the
        target unknown there is nothing to compare against, so it is recorded."""
        client = make_client([{"id": 1, "name": "Alpha", "channel_number": 1}])
        request = BulkCommitRequest(
            operations=[{"type": "bulkAssignChannelNumbers", "channelIds": [1]}],
            consolidate=False,
        )
        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal") as mock_journal:
            await _run_bulk_commit(request, batch_id="batch-1")

        rows = rows_from(mock_journal)
        assert client.assign_channel_numbers.call_args_list == [call([1], None)]
        assert len(rows) == 1
        assert_no_invented_number(rows[0])

    @pytest.mark.asyncio
    async def test_explicit_start_journals_the_number_sent(self):
        _result, client, rows = await run_assign(
            {"type": "bulkAssignChannelNumbers", "channelIds": [1, 2], "startingNumber": 10},
            consolidate=False,
        )

        assert client.assign_channel_numbers.call_args_list == [call([1, 2], 10)]
        assert [r["description"] for r in rows] == [
            "Changed channel number from 50 to 10",
            "Changed channel number from 51 to 11",
        ]
        assert rows[0]["after_value"] == {"channel_number": 10, "name": "Alpha"}


class TestConsolidationOn:

    @pytest.mark.asyncio
    async def test_omitted_start_is_materialised_and_journaled(self):
        _result, client, rows = await run_assign(
            {"type": "bulkAssignChannelNumbers", "channelIds": [1, 2]},
            consolidate=True,
        )

        assert client.assign_channel_numbers.call_args_list == [call([1, 2], 1)]
        assert [r["description"] for r in rows] == [
            "Changed channel number from 50 to 1",
            "Changed channel number from 51 to 2",
        ]
        assert rows[1]["after_value"] == {"channel_number": 2, "name": "Bravo"}

    @pytest.mark.asyncio
    async def test_explicit_start_journals_the_number_sent(self):
        _result, client, rows = await run_assign(
            {"type": "bulkAssignChannelNumbers", "channelIds": [1, 2], "startingNumber": 10},
            consolidate=True,
        )

        assert client.assign_channel_numbers.call_args_list == [call([1, 2], 10)]
        assert [r["description"] for r in rows] == [
            "Changed channel number from 50 to 10",
            "Changed channel number from 51 to 11",
        ]
