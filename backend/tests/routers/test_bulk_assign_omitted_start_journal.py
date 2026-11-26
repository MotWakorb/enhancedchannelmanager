"""The journal must never name a starting number ECM did not send.

A ``bulkAssignChannelNumbers`` operation with no ``startingNumber`` reaches
Dispatcharr as "choose the numbers yourself" whenever consolidation is off,
because consolidation is the only step that materialises the omitted start.
The assign endpoint returns no numbers, so a row either carries the number
read back from Dispatcharr afterwards or says it was not read back. Nothing
downstream in the same batch (later rows, numbering compensation, recovery
steps) may cite the number the range would have had starting from 1.

Every case also pins the exact outgoing ``assign_channel_numbers`` call, so
the journal change provably leaves the mutation alone.
"""
import asyncio
from unittest.mock import AsyncMock, call, patch

import pytest

from routers.channels import (
    BEFORE_STATE_UNKNOWN_KEY,
    BulkCommitRequest,
    _run_bulk_commit,
)


def catalog():
    return [
        {"id": 1, "name": "Alpha", "channel_number": 50},
        {"id": 2, "name": "Bravo", "channel_number": 51},
    ]


def make_client(channels, *, chosen=None, read_back_fails=False):
    """A Dispatcharr double whose assign moves channels onto ``chosen``.

    ``chosen`` maps channel id to the number Dispatcharr picks when no start is
    sent; with a start, the range is applied from it. ``read_back_fails`` makes
    every ``get_channel`` after the assign raise.
    """
    state = {ch["id"]: dict(ch) for ch in channels}
    mock_client = AsyncMock()
    mock_client.get_channels.return_value = {
        "results": [dict(c) for c in channels], "next": None,
    }
    mock_client.get_streams_by_ids.side_effect = lambda ids: []
    mock_client.get_channel_groups.return_value = []
    assigned = {"done": False}

    async def assign(ids, start):
        for offset, cid in enumerate(ids):
            if cid in state:
                state[cid]["channel_number"] = (
                    (chosen or {}).get(cid) if start is None else start + offset
                )
        assigned["done"] = True
        return {"success": True}

    async def get_channel(cid):
        if read_back_fails and assigned["done"]:
            raise RuntimeError("upstream is busy")
        return dict(state.get(cid, {"id": cid}))

    mock_client.assign_channel_numbers.side_effect = assign
    mock_client.get_channel.side_effect = get_channel
    mock_client.update_channel.return_value = {}
    return mock_client


def rows_from(mock_journal):
    rows = []
    for c in mock_journal.log_entries.call_args_list:
        rows.extend(c.args[0] if c.args else c.kwargs["entries"])
    return rows


async def run(client, operations, **request_kwargs):
    request = BulkCommitRequest(operations=operations, **request_kwargs)
    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.journal") as mock_journal:
        result = await _run_bulk_commit(request, batch_id="batch-1")
    return result, rows_from(mock_journal)


# Baselines are the catalog's numbers: what a caller that read it would send.
CATALOG_BASELINES = {1: 50, 2: 51}
NO_START = {
    "type": "bulkAssignChannelNumbers", "channelIds": [1, 2],
    "expectedNumbers": CATALOG_BASELINES,
}
START_10 = {
    "type": "bulkAssignChannelNumbers", "channelIds": [1, 2], "startingNumber": 10,
    "expectedNumbers": CATALOG_BASELINES,
}
# The lineup below lists Bravo without a number, so its baseline is null.
NO_START_BRAVO_UNNUMBERED = {**NO_START, "expectedNumbers": {1: 50, 2: None}}
UNREAD = "Dispatcharr chose the new number and ECM has not read it back"


def assert_names_no_unobserved_number(row, *assumed):
    """No description or value cites a number from the 1-based assumption."""
    text = repr(row)
    for number in assumed:
        assert f"to {number}" not in row["description"], row
        assert f"on {number}" not in row["description"], row
        assert f"'channel_number': {number}" not in text, row
    for absent_marker in ("None", "null", "undefined", "NaN"):
        assert absent_marker not in row["description"], row


class TestConsolidationOffNoStart:

    @pytest.mark.asyncio
    async def test_rows_carry_the_numbers_read_back(self):
        client = make_client(catalog(), chosen={1: 7, 2: 8.5})
        result, rows = await run(client, [NO_START], consolidate=False)

        assert result["success"] is True
        assert client.assign_channel_numbers.call_args_list == [call([1, 2], None)]
        assert [r["description"] for r in rows] == [
            "Changed channel number from 50 to 7",
            "Changed channel number from 51 to 8.5",
        ]
        assert rows[0]["before_value"] == {"channel_number": 50, "name": "Alpha"}
        assert rows[0]["after_value"] == {"channel_number": 7, "name": "Alpha"}
        assert rows[1]["after_value"] == {"channel_number": 8.5, "name": "Bravo"}

    @pytest.mark.asyncio
    async def test_a_failed_read_back_leaves_rows_unobserved(self):
        client = make_client(catalog(), chosen={1: 7, 2: 8}, read_back_fails=True)
        result, rows = await run(client, [NO_START], consolidate=False)

        assert result["success"] is True
        assert result["operationsApplied"] == 1
        assert result["operationsFailed"] == 0
        assert client.assign_channel_numbers.call_args_list == [call([1, 2], None)]
        assert [r["description"] for r in rows] == [
            f"Changed channel number from 50; {UNREAD}",
            f"Changed channel number from 51; {UNREAD}",
        ]
        # Absent, not null: null would claim the channel now carries no number.
        assert rows[0]["after_value"] == {"name": "Alpha"}
        assert rows[1]["after_value"] == {"name": "Bravo"}
        for row in rows:
            assert_names_no_unobserved_number(row, 1, 2)

    @pytest.mark.asyncio
    async def test_a_channel_already_on_its_assumed_target_is_still_recorded(self):
        client = make_client(
            [{"id": 1, "name": "Alpha", "channel_number": 1}],
            chosen={1: 40}, read_back_fails=True,
        )
        _result, rows = await run(
            client,
            [{"type": "bulkAssignChannelNumbers", "channelIds": [1], "expectedNumbers": {1: 1}}],
            consolidate=False,
        )

        assert client.assign_channel_numbers.call_args_list == [call([1], None)]
        assert [r["description"] for r in rows] == [f"Changed channel number from 1; {UNREAD}"]
        assert rows[0]["after_value"] == {"name": "Alpha"}


class TestLaterRowsInTheSameBatch:
    """A later operation's before-state is what the read-back observed, or unknown."""

    @pytest.mark.asyncio
    async def test_a_later_renumber_cites_the_observed_number(self):
        client = make_client(catalog(), chosen={1: 7, 2: 8})
        _result, rows = await run(client, [
            NO_START,
            {"type": "updateChannel", "channelId": 1, "data": {"channel_number": 20},
             "expectedNumber": {"number": 50}},
        ], consolidate=False)

        update_row = rows[-1]
        assert update_row["action_type"] == "update"
        assert update_row["before_value"] == {"channel_number": 7}
        assert update_row["after_value"] == {"channel_number": 20}

    @pytest.mark.asyncio
    async def test_a_later_renumber_of_an_unread_channel_names_no_before_number(self):
        client = make_client(catalog(), chosen={1: 7, 2: 8}, read_back_fails=True)
        _result, rows = await run(client, [
            NO_START,
            {"type": "updateChannel", "channelId": 1, "data": {"channel_number": 20},
             "expectedNumber": {"number": 50}},
        ], consolidate=False)

        update_row = rows[-1]
        assert update_row["action_type"] == "update"
        assert update_row["description"] == "Updated channel: number to 20"
        assert update_row["before_value"] == {BEFORE_STATE_UNKNOWN_KEY: ["channel_number"]}
        assert_names_no_unobserved_number(update_row, 1)

    @pytest.mark.asyncio
    async def test_a_later_range_over_an_unread_channel_names_no_before_number(self):
        client = make_client(catalog(), chosen={1: 7, 2: 8}, read_back_fails=True)
        _result, rows = await run(client, [
            NO_START,
            {"type": "bulkAssignChannelNumbers", "channelIds": [1], "startingNumber": 30,
             "expectedNumbers": {1: 50}},
        ], consolidate=False)

        later = rows[-1]
        assert later["description"] == (
            "Changed channel number to 30; ECM had not read the previous number"
        )
        assert later["before_value"] == {
            BEFORE_STATE_UNKNOWN_KEY: ["channel_number"], "name": "Alpha",
        }
        assert later["after_value"] == {"channel_number": 30, "name": "Alpha"}
        assert_names_no_unobserved_number(later, 1)

    @pytest.mark.asyncio
    async def test_a_later_delete_of_an_unread_channel_names_no_number(self):
        client = make_client(catalog(), chosen={1: 7, 2: 8}, read_back_fails=True)
        _result, rows = await run(client, [
            NO_START,
            {"type": "deleteChannel", "channelId": 1},
        ], consolidate=False)

        delete_row = rows[-1]
        assert delete_row["action_type"] == "delete"
        assert delete_row["before_value"] == {
            BEFORE_STATE_UNKNOWN_KEY: ["channel_number"], "name": "Alpha",
        }
        assert_names_no_unobserved_number(delete_row, 1)


class TestPartialBatchRecovery:
    """The range landed, a later numbering write failed, so compensation runs."""

    PLAN = [
        NO_START,
        {"type": "updateChannel", "channelId": 2, "data": {"channel_number": 99},
         "expectedNumber": {"number": 51}},
    ]

    @staticmethod
    def failing_update(*, compensation_fails):
        async def update(cid, data):
            if data == {"channel_number": 99}:
                raise RuntimeError("dispatcharr said no")
            if compensation_fails:
                raise RuntimeError("compensation refused")
            return {}
        return update

    @pytest.mark.asyncio
    async def test_compensation_rows_cite_no_unobserved_number(self):
        client = make_client(catalog(), chosen={1: 7, 2: 8}, read_back_fails=True)
        client.update_channel.side_effect = self.failing_update(compensation_fails=False)
        result, rows = await run(client, self.PLAN, consolidate=False, continueOnError=True)

        assert client.assign_channel_numbers.call_args_list == [call([1, 2], None)]
        restores = [r for r in rows if r["description"].startswith("Put channel number back")]
        assert sorted(r["entity_id"] for r in restores) == [1, 2]
        by_id = {r["entity_id"]: r for r in restores}
        assert by_id[1]["description"] == (
            "Put channel number back to 50 after this batch's numbering changes "
            "stopped part way"
        )
        assert by_id[1]["before_value"] == {
            BEFORE_STATE_UNKNOWN_KEY: ["channel_number"], "name": "Alpha",
        }
        assert by_id[1]["after_value"] == {"channel_number": 50, "name": "Alpha"}
        for row in restores:
            assert_names_no_unobserved_number(row, 1, 2)
        assert result["numberingRecovery"] == []

    @pytest.mark.asyncio
    async def test_compensation_uses_the_observed_numbers_when_read_back(self):
        client = make_client(catalog(), chosen={1: 7, 2: 8})
        client.update_channel.side_effect = self.failing_update(compensation_fails=False)
        _result, rows = await run(client, self.PLAN, consolidate=False, continueOnError=True)

        restores = {
            r["entity_id"]: r for r in rows
            if r["description"].startswith("Put channel number back")
        }
        assert restores[1]["before_value"] == {"channel_number": 7, "name": "Alpha"}
        assert restores[2]["before_value"] == {"channel_number": 8, "name": "Bravo"}

    @pytest.mark.asyncio
    async def test_recovery_steps_cite_no_unobserved_number(self):
        client = make_client(catalog(), chosen={1: 7, 2: 8}, read_back_fails=True)
        client.update_channel.side_effect = self.failing_update(compensation_fails=True)
        result, _rows = await run(client, self.PLAN, consolidate=False, continueOnError=True)

        steps = {s["channelId"]: s for s in result["numberingRecovery"]}
        assert sorted(steps) == [1, 2]
        assert steps[1]["step"] == (
            'Set "Alpha" (channel 1) back to channel number 50; this run moved it '
            "to a number ECM has not read back."
        )
        assert "currentNumber" not in steps[1]
        assert steps[1]["targetNumber"] == 50
        for step in steps.values():
            for number in (1, 2):
                assert f"on {number}." not in step["step"], step


class TestExplicitOrConsolidatedStart:

    @pytest.mark.asyncio
    async def test_explicit_start_without_consolidation_journals_the_number_sent(self):
        client = make_client(catalog())
        _result, rows = await run(client, [START_10], consolidate=False)

        assert client.assign_channel_numbers.call_args_list == [call([1, 2], 10)]
        assert client.get_channel.await_count == 0
        assert [r["description"] for r in rows] == [
            "Changed channel number from 50 to 10",
            "Changed channel number from 51 to 11",
        ]
        assert rows[0]["after_value"] == {"channel_number": 10, "name": "Alpha"}

    @pytest.mark.asyncio
    async def test_omitted_start_with_consolidation_is_materialised_and_journaled(self):
        client = make_client(catalog())
        _result, rows = await run(client, [NO_START], consolidate=True)

        assert client.assign_channel_numbers.call_args_list == [call([1, 2], 1)]
        assert client.get_channel.await_count == 0
        assert [r["description"] for r in rows] == [
            "Changed channel number from 50 to 1",
            "Changed channel number from 51 to 2",
        ]
        assert rows[1]["after_value"] == {"channel_number": 2, "name": "Bravo"}

    @pytest.mark.asyncio
    async def test_explicit_start_with_consolidation_journals_the_number_sent(self):
        client = make_client(catalog())
        _result, rows = await run(client, [START_10], consolidate=True)

        assert client.assign_channel_numbers.call_args_list == [call([1, 2], 10)]
        assert [r["description"] for r in rows] == [
            "Changed channel number from 50 to 10",
            "Changed channel number from 51 to 11",
        ]


def script_read_back(client, outcomes):
    """Make the post-assign ``get_channel`` calls follow ``outcomes`` in order.

    Each outcome is a dict (returned), or an exception instance (raised). Reads
    before the assign are untouched.
    """
    original = client.get_channel.side_effect
    assigned = {"done": False}
    remaining = list(outcomes)
    assign = client.assign_channel_numbers.side_effect

    async def assign_then_mark(ids, start):
        result = await assign(ids, start)
        assigned["done"] = True
        return result

    async def get_channel(cid):
        if not assigned["done"]:
            return await original(cid)
        outcome = remaining.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    client.assign_channel_numbers.side_effect = assign_then_mark
    client.get_channel.side_effect = get_channel


class TestReadBackInterrupted:
    """Rows are queued before the read-back awaits, so what is true is journaled."""

    @pytest.mark.asyncio
    async def test_cancellation_mid_read_back_still_journals_both_rows(self):
        client = make_client(catalog(), chosen={1: 7, 2: 8})
        script_read_back(client, [
            {"id": 1, "name": "Alpha", "channel_number": 7},
            asyncio.CancelledError(),
        ])
        request = BulkCommitRequest(operations=[NO_START], consolidate=False)
        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal") as mock_journal:
            with pytest.raises(asyncio.CancelledError):
                await _run_bulk_commit(request, batch_id="batch-1")
        rows = rows_from(mock_journal)

        assert client.assign_channel_numbers.call_args_list == [call([1, 2], None)]
        # The first read succeeded, but rows are only finalised after the whole
        # loop, so both honestly say they were not read back.
        assert [r["description"] for r in rows] == [
            f"Changed channel number from 50; {UNREAD}",
            f"Changed channel number from 51; {UNREAD}",
        ]
        assert [r["entity_id"] for r in rows] == [1, 2]
        assert rows[0]["after_value"] == {"name": "Alpha"}
        assert rows[1]["after_value"] == {"name": "Bravo"}
        for row in rows:
            assert "channel_number" not in row["after_value"]

    @pytest.mark.asyncio
    async def test_channel_without_a_starting_number_names_no_previous_number(self):
        # A truly missing channel is rejected by validation before any write,
        # so the reachable shape is a channel the lineup listed WITHOUT a
        # channel_number: ECM never observed its number before the range ran.
        client = make_client([{"id": 1, "name": "Alpha", "channel_number": 50},
                              {"id": 2, "name": "Bravo"}],
                             chosen={1: 7, 2: 8})
        script_read_back(client, [RuntimeError("busy"), RuntimeError("busy")])
        _result, rows = await run(client, [NO_START_BRAVO_UNNUMBERED], consolidate=False)

        absent = next(r for r in rows if r["entity_id"] == 2)
        assert absent["description"] == (
            "Changed channel number; Dispatcharr chose the new number and ECM "
            "has not read it back"
        )
        assert BEFORE_STATE_UNKNOWN_KEY in absent["before_value"]
        assert absent["before_value"][BEFORE_STATE_UNKNOWN_KEY] == ["channel_number"]
        assert "channel_number" not in absent["before_value"]
        assert "channel_number" not in absent["after_value"]
        assert "from " not in absent["description"]

    @pytest.mark.asyncio
    async def test_channel_without_a_starting_number_but_read_back_names_only_the_new_number(self):
        client = make_client([{"id": 1, "name": "Alpha", "channel_number": 50},
                              {"id": 2, "name": "Bravo"}],
                             chosen={1: 7, 2: 8})
        _result, rows = await run(client, [NO_START_BRAVO_UNNUMBERED], consolidate=False)

        absent = next(r for r in rows if r["entity_id"] == 2)
        assert absent["description"] == (
            "Changed channel number to 8; ECM had not read the previous number"
        )
        assert absent["before_value"][BEFORE_STATE_UNKNOWN_KEY] == ["channel_number"]
        assert "channel_number" not in absent["before_value"]
        assert absent["after_value"]["channel_number"] == 8

    @pytest.mark.asyncio
    async def test_partial_read_back_finalises_only_the_row_it_observed(self):
        client = make_client(catalog(), chosen={1: 7, 2: 8})
        script_read_back(client, [
            {"id": 1, "name": "Alpha", "channel_number": 7},
            RuntimeError("upstream is busy"),
        ])
        result, rows = await run(client, [NO_START], consolidate=False)

        assert result["success"] is True
        assert [r["description"] for r in rows] == [
            "Changed channel number from 50 to 7",
            f"Changed channel number from 51; {UNREAD}",
        ]
        assert rows[0]["after_value"] == {"channel_number": 7, "name": "Alpha"}
        assert rows[1]["after_value"] == {"name": "Bravo"}
        assert_names_no_unobserved_number(rows[1], 1, 2)

    @pytest.mark.asyncio
    async def test_response_without_a_channel_number_key_stays_unobserved(self):
        client = make_client(catalog(), chosen={1: 7, 2: 8})
        script_read_back(client, [
            {"id": 1, "name": "Alpha"},
            {"id": 2, "name": "Bravo", "channel_number": 8},
        ])
        _result, rows = await run(client, [NO_START], consolidate=False)

        assert [r["description"] for r in rows] == [
            f"Changed channel number from 50; {UNREAD}",
            "Changed channel number from 51 to 8",
        ]
        assert rows[0]["after_value"] == {"name": "Alpha"}
        assert "channel_number" not in rows[0]["after_value"]
        assert None not in rows[0]["after_value"].values()
