"""A caller's channel-number expectation is checked before the number is written.

Bead ``enhancedchannelmanager-ic884.4``. The browser has its own half of this
check (``frontend/src/utils/channelNumberConcurrency.ts``) and neither is the
other's safety net: the browser's is what can show the operator the STAGED
CHANGE they recognise and ask them to choose, and this one is what exists at all
for a caller that never touches the UI, and what closes the window between the
browser reading the lineup and this executor writing to it.

The invariant, stated as a property:

    A staged change never overwrites a server-side change made after the
    baseline was captured, without the operator being shown it and choosing.

It is a CHECK, not a guarantee. Dispatcharr 0.28.x has no conditional update and
no revision token (measured — see ``ExpectedChannelNumber``), so a change that
lands between this executor's own read and its PATCH is still lost. Nothing here
says otherwise.
"""
from unittest.mock import AsyncMock, patch

import pytest

from routers.channels import BulkCommitRequest, _run_bulk_commit


LINEUP = [
    {"id": 1, "name": "ESPN", "channel_number": 5, "streams": []},
    {"id": 2, "name": "TNT", "channel_number": 6, "streams": []},
]


def make_client(lineup=None, channels_readable=True):
    channels = [dict(ch) for ch in (LINEUP if lineup is None else lineup)]
    client = AsyncMock()
    if channels_readable:
        client.get_channels.return_value = {"results": channels, "next": None}
    else:
        client.get_channels.side_effect = RuntimeError("Dispatcharr unreachable")
    client.get_streams_by_ids.side_effect = lambda ids: []
    client.get_channel_groups.return_value = []

    async def update_channel(channel_id, data):
        return {"id": channel_id, **data}

    client.update_channel.side_effect = update_channel
    return client


async def run_ops(client, operations, **kwargs):
    request = BulkCommitRequest(operations=operations, **kwargs)
    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.journal"):
        return await _run_bulk_commit(request, batch_id="batch-ic884-4")


def renumber(channel_id, number, expected=...):
    op = {
        "type": "updateChannel",
        "channelId": channel_id,
        "data": {"channel_number": number},
    }
    if expected is not ...:
        op["expectedNumber"] = {"number": expected}
    return op


def patched_numbers(client):
    return [
        (call.args[0], call.args[1].get("channel_number"))
        for call in client.update_channel.await_args_list
    ]


class TestExpectationHonoured:
    @pytest.mark.asyncio
    async def test_a_matching_expectation_writes_the_number(self):
        client = make_client()
        result = await run_ops(client, [renumber(1, 20, expected=5)])
        assert result["success"] is True
        assert patched_numbers(client) == [(1, 20)]

    @pytest.mark.asyncio
    async def test_canonical_equivalents_match(self):
        client = make_client([
            {"id": 1, "name": "ESPN", "channel_number": 7.0, "streams": []},
        ])
        result = await run_ops(client, [renumber(1, 20, expected=7)])
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_an_expectation_of_no_number_matches_no_number(self):
        client = make_client([
            {"id": 1, "name": "ESPN", "channel_number": None, "streams": []},
        ])
        result = await run_ops(client, [renumber(1, 20, expected=None)])
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_an_operation_sending_no_expectation_is_unchanged(self):
        """Invariant 7: every existing caller behaves exactly as it did."""
        client = make_client()
        result = await run_ops(client, [renumber(1, 20)])
        assert result["success"] is True
        assert patched_numbers(client) == [(1, 20)]


class TestExpectationRefused:
    @pytest.mark.asyncio
    async def test_a_number_that_moved_is_not_written_over(self):
        client = make_client()
        result = await run_ops(client, [renumber(1, 20, expected=99)])
        assert result["success"] is False
        assert result["operationsFailed"] == 1
        assert patched_numbers(client) == []
        assert "Somebody else changed it" in result["errors"][0]["error"]

    @pytest.mark.asyncio
    async def test_a_tenth_of_a_channel_apart_is_a_change(self):
        client = make_client([
            {"id": 1, "name": "ESPN", "channel_number": 7.1, "streams": []},
        ])
        result = await run_ops(client, [renumber(1, 20, expected=7)])
        assert result["operationsFailed"] == 1
        assert patched_numbers(client) == []

    @pytest.mark.asyncio
    async def test_an_expected_null_against_a_real_number_is_a_change(self):
        client = make_client()
        result = await run_ops(client, [renumber(1, 20, expected=None)])
        assert result["operationsFailed"] == 1

    @pytest.mark.asyncio
    async def test_a_lineup_that_could_not_be_read_refuses_rather_than_guesses(self):
        client = make_client(channels_readable=False)
        result = await run_ops(
            client, [renumber(1, 20, expected=5)], continueOnError=True
        )
        assert result["operationsFailed"] == 1
        assert patched_numbers(client) == []
        assert "could not be read" in result["errors"][0]["error"]

    @pytest.mark.asyncio
    async def test_an_expectation_on_an_operation_that_writes_no_number_is_ignored(self):
        client = make_client()
        result = await run_ops(client, [{
            "type": "updateChannel",
            "channelId": 1,
            "data": {"name": "ESPN HD"},
            "expectedNumber": {"number": 99},
        }])
        assert result["success"] is True


class TestRefusalMeetsTheCompensationPass:
    """The two beads' mechanisms have to agree, because they meet here."""

    @pytest.mark.asyncio
    async def test_a_refused_expectation_puts_the_rest_of_the_plan_back(self):
        # Channel 2's move lands; channel 1's is refused because somebody else
        # moved it. The run stopped part way through a numbering plan, so
        # channel 2 goes back on 6 (bead enhancedchannelmanager-ic884.3).
        client = make_client()
        result = await run_ops(
            client,
            [renumber(2, 22, expected=6), renumber(1, 20, expected=99)],
            continueOnError=True,
        )
        assert result["operationsFailed"] == 1
        assert patched_numbers(client) == [(2, 22), (2, 6)]
        assert result["numberingRecovery"] == []

    @pytest.mark.asyncio
    async def test_the_envelope_still_adds_up(self):
        client = make_client()
        result = await run_ops(
            client,
            [renumber(2, 22, expected=6), renumber(1, 20, expected=99)],
            continueOnError=True,
        )
        assert result["operationsApplied"] == 1
        assert result["operationsFailed"] == 1
        assert result["partial"] is True


class TestExpectationSurvivesConsolidation:
    @pytest.mark.asyncio
    async def test_the_merged_operation_keeps_the_expectation(self):
        """Consolidation is on by default from the UI, so an expectation lost
        here would be an expectation nobody ever checks."""
        client = make_client()
        result = await run_ops(
            client,
            [
                {"type": "updateChannel", "channelId": 1, "data": {"name": "ESPN HD"}},
                renumber(1, 20, expected=99),
            ],
            consolidate=True,
        )
        assert result["operationsFailed"] == 1
        assert patched_numbers(client) == []
