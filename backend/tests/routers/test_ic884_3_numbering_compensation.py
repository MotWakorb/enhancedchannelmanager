"""A numbering plan that stops part way puts back what it changed, or says where it is.

Bead ``enhancedchannelmanager-ic884.3``. The PO's decision was best-effort
compensating writes and then a report; the invariant those tests are written
against is the one that matters:

    A failure part way through a numbering plan leaves either the prior state,
    or a state the operator is given exact, deterministic steps to finish or
    undo. Never an unexplained middle.

The scenarios below are examples of that property, not the specification. The
failure is injected at the FIRST, MIDDLE and LAST position of the plan, and the
compensating write is failed too, because a fix that closes only the
demonstrated case is how this epic already lost two review rounds.

Nothing here claims all-or-nothing behaviour: Dispatcharr 0.28.x has no
conditional update and no revision token (measured — see
``backend/channel_number_apply.py``), so a change another client makes between
the failure and the repair is neither seen nor preserved.
"""
from unittest.mock import AsyncMock, patch

import pytest

from routers.channels import BulkCommitRequest, _run_bulk_commit


LINEUP = [
    {"id": 1, "name": "ESPN", "channel_number": 5, "streams": []},
    {"id": 2, "name": "TNT", "channel_number": 6, "streams": []},
    {"id": 3, "name": "AMC", "channel_number": 7, "streams": []},
]


def make_client(lineup=None, fail_on=()):
    """A Dispatcharr client whose ``update_channel`` fails for named channels.

    ``fail_on`` is a set of channel ids; a PATCH naming one raises. Everything
    else succeeds and the local lineup copy moves with it, so the mock behaves
    like the upstream it stands in for.
    """
    channels = [dict(ch) for ch in (LINEUP if lineup is None else lineup)]
    failing = set(fail_on)
    client = AsyncMock()
    client.get_channels.return_value = {"results": channels, "next": None}
    client.get_streams_by_ids.side_effect = lambda ids: [
        {"id": sid, "name": f"Stream {sid}"} for sid in ids
    ]
    client.get_channel_groups.return_value = []
    client.assign_channel_numbers.return_value = {}

    async def update_channel(channel_id, data):
        if channel_id in failing:
            raise RuntimeError(f"upstream refused channel {channel_id}")
        return {"id": channel_id, **data}

    client.update_channel.side_effect = update_channel
    return client


async def run_ops(client, operations, **request_kwargs):
    request = BulkCommitRequest(operations=operations, **request_kwargs)
    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.journal"):
        return await _run_bulk_commit(request, batch_id="batch-ic884-3")


def numbering_patches(client):
    """Every channel-number PATCH the run ATTEMPTED, in order, as (id, number).

    Attempted, not landed: the mock records the call before it decides whether
    to raise, which is what makes "the run tried and was refused" visible in
    the same list as "the run tried and succeeded".
    """
    return [
        (call.args[0], call.args[1]["channel_number"])
        for call in client.update_channel.await_args_list
        if "channel_number" in call.args[1]
    ]


def renumber(channel_id, number):
    return {
        "type": "updateChannel",
        "channelId": channel_id,
        "data": {"channel_number": number},
    }


class TestSafeOrdering:
    """A channel is moved onto a number only once its holder has left it."""

    @pytest.mark.asyncio
    async def test_a_chain_is_written_from_the_far_end_first(self):
        client = make_client()
        # 1 wants 6 (TNT's), 2 wants 7 (AMC's), 3 wants 8 (free).
        result = await run_ops(client, [renumber(1, 6), renumber(2, 7), renumber(3, 8)])
        assert result["success"] is True
        assert numbering_patches(client) == [(3, 8), (2, 7), (1, 6)]

    @pytest.mark.asyncio
    async def test_a_swap_still_reaches_the_previewed_final_state(self):
        client = make_client()
        result = await run_ops(client, [renumber(1, 6), renumber(2, 5)])
        assert result["success"] is True
        assert result["operationsApplied"] == 2
        assert sorted(numbering_patches(client)) == [(1, 6), (2, 5)]

    @pytest.mark.asyncio
    async def test_ordering_never_moves_a_numbering_write_past_another_op_type(self):
        client = make_client()
        await run_ops(client, [
            renumber(1, 6),
            {"type": "updateChannel", "channelId": 3, "data": {"name": "AMC HD"}},
            renumber(2, 99),
        ])
        patched = [call.args[0] for call in client.update_channel.await_args_list]
        assert patched == [1, 3, 2]

    @pytest.mark.asyncio
    async def test_an_ordinary_single_edit_is_untouched(self):
        client = make_client()
        result = await run_ops(client, [renumber(2, 99)])
        assert result["success"] is True
        assert numbering_patches(client) == [(2, 99)]


class TestCompensationOnPartialFailure:
    """A plan that stops part way is written back to where it started."""

    @pytest.mark.parametrize("failing_channel", [1, 2, 3])
    @pytest.mark.asyncio
    async def test_a_failure_at_any_position_restores_the_prior_numbering(
        self, failing_channel
    ):
        client = make_client(fail_on={failing_channel})
        result = await run_ops(
            client,
            [renumber(1, 21), renumber(2, 22), renumber(3, 23)],
            continueOnError=True,
        )
        assert result["success"] is False
        assert result["operationsFailed"] == 1

        # Every channel that DID move is back on the number it started on, and
        # the one that never moved was never touched a second time.
        restored = dict(numbering_patches(client)[3:])
        prior = {1: 5, 2: 6, 3: 7}
        expected = {cid: prior[cid] for cid in (1, 2, 3) if cid != failing_channel}
        assert restored == expected
        assert result["numberingRecovery"] == []

    @pytest.mark.asyncio
    async def test_a_run_where_every_numbering_write_lands_compensates_nothing(self):
        client = make_client()
        result = await run_ops(
            client, [renumber(1, 21), renumber(2, 22)], continueOnError=True
        )
        assert result["success"] is True
        assert numbering_patches(client) == [(1, 21), (2, 22)]
        assert result["numberingRecovery"] == []

    @pytest.mark.asyncio
    async def test_a_run_where_no_numbering_write_lands_compensates_nothing(self):
        client = make_client(fail_on={1, 2})
        result = await run_ops(
            client, [renumber(1, 21), renumber(2, 22)], continueOnError=True
        )
        assert result["operationsFailed"] == 2
        # Both were attempted and both were refused, so there is nothing that
        # landed to put back — and no third PATCH was made.
        assert numbering_patches(client) == [(1, 21), (2, 22)]
        assert result["numberingRecovery"] == []

    @pytest.mark.asyncio
    async def test_a_failure_that_is_not_a_numbering_change_compensates_nothing(self):
        """Invariant 7: a stream failure beside a renumber is not a renumber failure."""
        client = make_client()
        client.get_channel.side_effect = RuntimeError("stream lookup exploded")
        result = await run_ops(
            client,
            [
                renumber(1, 21),
                {"type": "addStreamToChannel", "channelId": 2, "streamId": 9},
            ],
            continueOnError=True,
        )
        assert result["operationsFailed"] == 1
        assert numbering_patches(client) == [(1, 21)]
        assert result["numberingRecovery"] == []

    @pytest.mark.asyncio
    async def test_a_compensating_write_is_journalled_like_any_other(self):
        client = make_client(fail_on={2})
        request = BulkCommitRequest(
            operations=[renumber(1, 21), renumber(2, 22)], continueOnError=True
        )
        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.write_journal_rows") as write_rows, \
             patch("routers.channels.journal"):
            await _run_bulk_commit(request, batch_id="batch-ic884-3")
        rows = write_rows.call_args.args[0]
        put_back = [
            row for row in rows
            if row["entity_id"] == 1 and "back" in row["description"].lower()
        ]
        assert len(put_back) == 1
        assert put_back[0]["after_value"]["channel_number"] == 5
        assert put_back[0]["batch_id"] == "batch-ic884-3"


class TestCompensationThatItselfFails:
    """The one case where neither state is what the operator is left with."""

    @pytest.mark.asyncio
    async def test_the_operator_is_given_the_exact_remaining_step(self):
        # Channel 1's move lands; channel 2's fails; putting channel 1 back
        # fails as well, because the mock refuses every PATCH after the first.
        client = make_client()
        calls = {"n": 0}

        async def update_channel(channel_id, data):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"id": channel_id, **data}
            raise RuntimeError(f"upstream refused channel {channel_id}")

        client.update_channel.side_effect = update_channel

        result = await run_ops(
            client, [renumber(1, 21), renumber(2, 22)], continueOnError=True
        )

        recovery = result["numberingRecovery"]
        assert len(recovery) == 1
        entry = recovery[0]
        assert entry["channelId"] == 1
        assert entry["channelName"] == "ESPN"
        assert entry["currentNumber"] == 21
        assert entry["targetNumber"] == 5
        assert "ESPN" in entry["step"] and "5" in entry["step"]

        assert result["success"] is False
        assert any(
            error.get("operationId") == "bulk-commit-numbering-recovery"
            for error in result["errors"]
        )

    @pytest.mark.asyncio
    async def test_the_envelope_still_adds_up(self):
        """`finalize_bulk_commit_result` raises on a contradictory envelope, so
        reaching a result at all is the assertion; the counts pin the shape."""
        client = make_client()
        calls = {"n": 0}

        async def update_channel(channel_id, data):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"id": channel_id, **data}
            raise RuntimeError("upstream refused")

        client.update_channel.side_effect = update_channel

        result = await run_ops(
            client, [renumber(1, 21), renumber(2, 22)], continueOnError=True
        )
        assert result["operationsApplied"] == 1
        assert result["operationsFailed"] == 1
        assert result["partial"] is True
        assert result["success"] is False


class TestBoundaries:
    """The magnitudes and shapes a plan is allowed to contain."""

    @pytest.mark.asyncio
    async def test_one_decimal_values_order_and_compensate(self):
        lineup = [
            {"id": 1, "name": "A", "channel_number": 5.1, "streams": []},
            {"id": 2, "name": "B", "channel_number": 5.2, "streams": []},
        ]
        client = make_client(lineup, fail_on={2})
        result = await run_ops(
            client, [renumber(1, 5.2), renumber(2, 5.3)], continueOnError=True
        )
        # 5.2 is B's slot, so B is written first; it is refused, A still lands
        # on 5.2, and A is put back on 5.1.
        assert numbering_patches(client) == [(2, 5.3), (1, 5.2), (1, 5.1)]
        assert result["numberingRecovery"] == []

    @pytest.mark.asyncio
    async def test_clearing_a_number_is_compensated_back_to_the_number(self):
        client = make_client(fail_on={2})
        result = await run_ops(
            client,
            [
                {"type": "updateChannel", "channelId": 1, "data": {"channel_number": None}},
                renumber(2, 22),
            ],
            continueOnError=True,
        )
        assert numbering_patches(client) == [(1, None), (2, 22), (1, 5)]
        assert result["numberingRecovery"] == []

    @pytest.mark.asyncio
    async def test_a_channel_with_no_number_is_compensated_back_to_none(self):
        lineup = [
            {"id": 1, "name": "A", "channel_number": None, "streams": []},
            {"id": 2, "name": "B", "channel_number": 6, "streams": []},
        ]
        client = make_client(lineup, fail_on={2})
        await run_ops(client, [renumber(1, 21), renumber(2, 22)], continueOnError=True)
        assert numbering_patches(client) == [(1, 21), (2, 22), (1, None)]

    @pytest.mark.asyncio
    async def test_a_range_assignment_that_fails_compensates_the_edits_beside_it(self):
        client = make_client()
        client.assign_channel_numbers.side_effect = RuntimeError("assign refused")
        result = await run_ops(
            client,
            [
                renumber(1, 21),
                {
                    "type": "bulkAssignChannelNumbers",
                    "channelIds": [2, 3],
                    "startingNumber": 30,
                },
            ],
            continueOnError=True,
        )
        assert numbering_patches(client) == [(1, 21), (1, 5)]
        assert result["operationsFailed"] == 1
        assert result["numberingRecovery"] == []
