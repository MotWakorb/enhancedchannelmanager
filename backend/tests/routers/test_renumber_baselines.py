"""Every numbering write to an existing channel carries the caller's baseline.

The invariant, stated as a property and exercised on both write paths:

    No numbering write to an existing channel, through ``POST
    /api/channels/bulk-commit`` or ``POST /api/channels/assign-numbers``, may
    overwrite a server-side number that differs from the caller's baseline. A
    missing baseline is refused, not assumed.

"Existing" means a real (non-negative) channel id. A negative id names a channel
the same bulk-commit request creates, which nobody else can have moved, so it
needs no baseline and may not carry one.

It is a check, not a guarantee: Dispatcharr 0.28.x has no conditional write, so
a change landing between ECM's read and its write is still lost.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from routers.channels import (
    AssignNumbersRequest,
    BulkCommitRequest,
    _consolidate_operations,
    _run_bulk_commit,
)


LINEUP = [
    {"id": 1, "name": "ESPN", "channel_number": 5, "streams": []},
    {"id": 2, "name": "TNT", "channel_number": 6, "streams": []},
    {"id": 3, "name": "AMC", "channel_number": 7, "streams": []},
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
    client.assign_channel_numbers.return_value = {"success": True}

    async def update_channel(channel_id, data):
        return {"id": channel_id, **data}

    client.update_channel.side_effect = update_channel
    by_id = {ch["id"]: ch for ch in channels}

    async def get_channel(channel_id):
        return dict(by_id[channel_id])

    client.get_channel.side_effect = get_channel
    return client


async def run_ops(client, operations, **kwargs):
    request = BulkCommitRequest(operations=operations, **kwargs)
    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.journal"):
        return await _run_bulk_commit(request, batch_id="batch-baselines")


def assign_range(channel_ids, start, expected=...):
    op = {
        "type": "bulkAssignChannelNumbers",
        "channelIds": channel_ids,
        "startingNumber": start,
    }
    if expected is not ...:
        op["expectedNumbers"] = expected
    return op


def renumber(channel_id, number, expected=...):
    op = {
        "type": "updateChannel",
        "channelId": channel_id,
        "data": {"channel_number": number},
    }
    if expected is not ...:
        op["expectedNumber"] = {"number": expected}
    return op


def error_text(excinfo) -> str:
    return " ".join(err["msg"] for err in excinfo.value.errors())


# ---------------------------------------------------------------------------
# Staged path: bulkAssignChannelNumbers
# ---------------------------------------------------------------------------


class TestStagedRangeBaselines:
    @pytest.mark.asyncio
    async def test_matching_baselines_proceed_unchanged(self):
        client = make_client()
        result = await run_ops(
            client, [assign_range([1, 2], 100, expected={"1": 5, "2": 6})],
        )
        assert result["success"] is True
        client.assign_channel_numbers.assert_awaited_once_with([1, 2], 100)

    @pytest.mark.asyncio
    async def test_a_stale_baseline_refuses_the_whole_range_before_any_write(self):
        client = make_client()
        result = await run_ops(
            client, [assign_range([1, 2], 100, expected={"1": 5, "2": 99})],
        )
        assert result["success"] is False
        assert result["operationsFailed"] == 1
        client.assign_channel_numbers.assert_not_awaited()
        client.update_channel.assert_not_awaited()
        error = result["errors"][0]
        assert error["operationType"] == "bulkAssignChannelNumbers"
        assert "Somebody else changed" in error["error"]
        assert error["concurrentNumberChanges"] == [
            {"channelId": 2, "baselineNumber": 99, "serverNumber": 6},
        ]

    @pytest.mark.asyncio
    async def test_every_stale_channel_is_named(self):
        client = make_client()
        result = await run_ops(
            client,
            [assign_range([1, 2, 3], 100, expected={"1": 50, "2": 6, "3": None})],
        )
        changes = result["errors"][0]["concurrentNumberChanges"]
        assert [change["channelId"] for change in changes] == [1, 3]

    @pytest.mark.asyncio
    async def test_canonical_equivalents_match(self):
        client = make_client([
            {"id": 1, "name": "ESPN", "channel_number": 7.0, "streams": []},
        ])
        result = await run_ops(client, [assign_range([1], 100, expected={"1": 7})])
        assert result["success"] is True
        client.assign_channel_numbers.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_unreadable_lineup_refuses_rather_than_guesses(self):
        client = make_client(channels_readable=False)
        result = await run_ops(
            client, [assign_range([1], 100, expected={"1": 5})], continueOnError=True,
        )
        assert result["operationsFailed"] == 1
        client.assign_channel_numbers.assert_not_awaited()
        assert "could not be read" in result["errors"][0]["error"]

    def test_a_missing_baseline_is_a_validation_error_naming_the_channels(self):
        with pytest.raises(ValidationError) as excinfo:
            BulkCommitRequest(operations=[assign_range([1, 2, 3], 100, expected={"2": 6})])
        message = error_text(excinfo)
        assert "expectedNumbers" in message
        assert "1, 3" in message

    def test_an_omitted_baseline_map_is_a_validation_error(self):
        with pytest.raises(ValidationError) as excinfo:
            BulkCommitRequest(operations=[assign_range([1, 2], 100)])
        assert "1, 2" in error_text(excinfo)

    def test_a_baseline_for_a_channel_not_in_the_range_is_refused(self):
        with pytest.raises(ValidationError) as excinfo:
            BulkCommitRequest(operations=[
                assign_range([1], 100, expected={"1": 5, "9": 3}),
            ])
        assert "9" in error_text(excinfo)

    def test_a_channel_created_in_the_same_request_needs_no_baseline(self):
        request = BulkCommitRequest(operations=[
            {"type": "createChannel", "tempId": -1, "name": "New"},
            assign_range([1, -1], 100, expected={"1": 5}),
        ])
        assert request.operations[1].expectedNumbers == {1: 5}

    def test_a_temp_channel_may_not_carry_a_baseline(self):
        with pytest.raises(ValidationError) as excinfo:
            BulkCommitRequest(operations=[
                {"type": "createChannel", "tempId": -1, "name": "New"},
                assign_range([-1], 100, expected={"-1": 5}),
            ])
        assert "-1" in error_text(excinfo)

    @pytest.mark.asyncio
    async def test_an_omitted_start_is_still_checked(self):
        client = make_client()
        result = await run_ops(client, [assign_range([1], None, expected={"1": 99})])
        assert result["operationsFailed"] == 1
        client.assign_channel_numbers.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_earlier_move_in_the_same_request_is_not_a_conflict(self):
        # The baseline is the caller's read of the lineup, so the range's
        # expectation for channel 1 is 5 even though op 0 moved it first.
        client = make_client()
        result = await run_ops(client, [
            renumber(1, 40, expected=5),
            assign_range([1], 100, expected={"1": 5}),
        ])
        assert result["success"] is True
        client.assign_channel_numbers.assert_awaited_once_with([1], 100)

    @pytest.mark.asyncio
    async def test_a_refused_range_puts_the_rest_of_the_plan_back(self):
        client = make_client()
        result = await run_ops(
            client,
            [renumber(3, 30, expected=7), assign_range([1, 2], 100, expected={"1": 5, "2": 99})],
            continueOnError=True,
        )
        client.assign_channel_numbers.assert_not_awaited()
        patched = [
            (call.args[0], call.args[1].get("channel_number"))
            for call in client.update_channel.await_args_list
        ]
        assert patched == [(3, 30), (3, 7)]


class TestConsolidatedRangeKeepsBaselines:
    def test_merged_ranges_carry_each_channels_baseline(self):
        request = BulkCommitRequest(operations=[
            assign_range([1], 100, expected={"1": 5}),
            assign_range([2], 101, expected={"2": 6}),
        ])
        merged = _consolidate_operations(list(request.operations))
        assert len(merged) == 1
        assert merged[0].channelIds == [1, 2]
        assert merged[0].expectedNumbers == {1: 5, 2: 6}

    def test_split_ranges_carry_only_their_own_channels(self):
        request = BulkCommitRequest(operations=[
            assign_range([1, 2], 100, expected={"1": 5, "2": 6}),
            assign_range([2], 500, expected={"2": 6}),
        ])
        merged = sorted(
            _consolidate_operations(list(request.operations)),
            key=lambda op: op.startingNumber,
        )
        assert [(op.channelIds, op.expectedNumbers) for op in merged] == [
            ([1], {1: 5}),
            ([2], {2: 6}),
        ]

    @pytest.mark.asyncio
    async def test_a_stale_baseline_still_refuses_after_consolidation(self):
        client = make_client()
        result = await run_ops(
            client,
            [
                assign_range([1], 100, expected={"1": 5}),
                assign_range([2], 101, expected={"2": 99}),
            ],
            consolidate=True,
        )
        assert result["operationsFailed"] == 1
        client.assign_channel_numbers.assert_not_awaited()


# ---------------------------------------------------------------------------
# Staged path: updateChannel
# ---------------------------------------------------------------------------


class TestStagedUpdateRequiresExpectedNumber:
    def test_a_number_change_on_an_existing_channel_without_expectation_is_refused(self):
        with pytest.raises(ValidationError) as excinfo:
            BulkCommitRequest(operations=[renumber(1, 20)])
        message = error_text(excinfo)
        assert "expectedNumber" in message
        assert "channel 1" in message

    def test_an_edit_that_does_not_touch_the_number_needs_no_expectation(self):
        request = BulkCommitRequest(operations=[
            {"type": "updateChannel", "channelId": 1, "data": {"name": "ESPN HD"}},
        ])
        assert request.operations[0].expectedNumber is None

    def test_a_channel_created_in_the_same_request_needs_no_expectation(self):
        request = BulkCommitRequest(operations=[
            {"type": "createChannel", "tempId": -1, "name": "New"},
            renumber(-1, 20),
        ])
        assert request.operations[1].expectedNumber is None

    def test_every_offending_operation_is_named(self):
        with pytest.raises(ValidationError) as excinfo:
            BulkCommitRequest(operations=[
                renumber(1, 20),
                renumber(2, 21, expected=6),
                assign_range([3], 30),
            ])
        message = error_text(excinfo)
        assert "Operation 0" in message
        assert "Operation 2" in message
        assert "Operation 1" not in message


# ---------------------------------------------------------------------------
# Direct path: POST /api/channels/assign-numbers
# ---------------------------------------------------------------------------


def _settings():
    settings = MagicMock()
    settings.auto_rename_channel_number = False
    return settings


async def post_assign(async_client, client, body):
    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.get_settings", return_value=_settings()), \
         patch("routers.channels.journal"), \
         patch("routers.channels.write_journal_rows", return_value=0):
        return await async_client.post("/api/channels/assign-numbers", json=body)


class TestDirectAssignBaselines:
    @pytest.mark.asyncio
    async def test_matching_baselines_proceed_unchanged(self, async_client):
        client = make_client()
        response = await post_assign(async_client, client, {
            "channel_ids": [1, 2], "starting_number": 100,
            "expected_numbers": {"1": 5, "2": 6},
        })
        assert response.status_code == 200, response.text
        client.assign_channel_numbers.assert_awaited_once_with([1, 2], 100)

    @pytest.mark.asyncio
    async def test_a_stale_baseline_is_a_409_with_no_write(self, async_client):
        client = make_client()
        response = await post_assign(async_client, client, {
            "channel_ids": [1, 2], "starting_number": 100,
            "expected_numbers": {"1": 5, "2": 99},
        })
        assert response.status_code == 409, response.text
        client.assign_channel_numbers.assert_not_awaited()
        client.update_channel.assert_not_awaited()
        detail = response.json()["detail"]
        assert "Somebody else changed" in detail["message"]
        assert detail["concurrentNumberChanges"] == [
            {"channelId": 2, "baselineNumber": 99, "serverNumber": 6},
        ]

    @pytest.mark.asyncio
    async def test_a_missing_baseline_is_a_422_naming_the_channels(self, async_client):
        client = make_client()
        response = await post_assign(async_client, client, {
            "channel_ids": [1, 2, 3], "starting_number": 100,
            "expected_numbers": {"2": 6},
        })
        assert response.status_code == 422, response.text
        assert "1, 3" in response.text
        client.get_channel.assert_not_awaited()
        client.assign_channel_numbers.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_omitted_baseline_map_is_a_422(self, async_client):
        client = make_client()
        response = await post_assign(async_client, client, {
            "channel_ids": [1], "starting_number": 100,
        })
        assert response.status_code == 422, response.text
        assert "expected_numbers" in response.text
        client.assign_channel_numbers.assert_not_awaited()

    def test_a_baseline_for_a_channel_not_requested_is_refused(self):
        with pytest.raises(ValidationError) as excinfo:
            AssignNumbersRequest(
                channel_ids=[1], starting_number=100, expected_numbers={1: 5, 9: 3},
            )
        assert "9" in error_text(excinfo)

    @pytest.mark.asyncio
    async def test_an_expected_null_against_a_real_number_is_a_change(self, async_client):
        client = make_client()
        response = await post_assign(async_client, client, {
            "channel_ids": [1], "expected_numbers": {"1": None},
        })
        assert response.status_code == 409, response.text
        client.assign_channel_numbers.assert_not_awaited()


# ---------------------------------------------------------------------------
# A channel created by one request is an existing channel to the next one
# ---------------------------------------------------------------------------


class TestCreatedChannelNumbersAreReported:
    """Edit Mode creates channels in one request and numbers them in a later
    one, which then needs the created channel's number as its baseline. The
    create response is the caller's read of that channel."""

    @pytest.mark.asyncio
    async def test_the_number_dispatcharr_created_the_channel_on_is_returned(self):
        client = make_client()
        client.create_channel.return_value = {"id": 77, "name": "New", "channel_number": 12}
        result = await run_ops(client, [
            {"type": "createChannel", "tempId": -1, "name": "New", "channelNumber": 12},
        ])
        assert result["tempIdMap"] == {-1: 77}
        assert result["createdChannelNumbers"] == {77: 12}

    @pytest.mark.asyncio
    async def test_a_number_dispatcharr_chose_is_returned_too(self):
        client = make_client()
        client.create_channel.return_value = {"id": 78, "name": "New", "channel_number": 431}
        result = await run_ops(client, [
            {"type": "createChannel", "tempId": -1, "name": "New"},
        ])
        assert result["createdChannelNumbers"] == {78: 431}

    @pytest.mark.asyncio
    async def test_a_request_that_creates_nothing_reports_an_empty_map(self):
        client = make_client()
        result = await run_ops(client, [
            {"type": "updateChannel", "channelId": 1, "data": {"name": "ESPN HD"}},
        ])
        assert result["createdChannelNumbers"] == {}


# ---------------------------------------------------------------------------
# A baseline is a number somebody READ, so it is not held to the write contract
# ---------------------------------------------------------------------------


OFF_GRID_LINEUP = [
    {"id": 1, "name": "ESPN", "channel_number": 8.25, "streams": []},
    {"id": 2, "name": "TNT", "channel_number": 6, "streams": []},
]


def _nan_body(body: dict) -> str:
    """``body`` as JSON with the literal ``NaN`` Python's parser accepts."""
    import json

    return json.dumps(body).replace('"__NAN__"', "NaN")


class TestBaselinesAcceptAnyFiniteNumber:
    """A number set outside ECM (8.25, say) can be read back, so a caller has
    to be able to say it read it. The one-decimal, non-negative contract binds
    what ECM WRITES and stays exactly as it was."""

    @pytest.mark.asyncio
    async def test_bulk_range_baseline_off_the_grid_that_matches_is_accepted(self):
        client = make_client(OFF_GRID_LINEUP)
        result = await run_ops(
            client, [assign_range([1, 2], 100, expected={"1": 8.25, "2": 6})],
        )
        assert result["success"] is True, result["errors"]
        client.assign_channel_numbers.assert_awaited_once_with([1, 2], 100)

    @pytest.mark.asyncio
    async def test_bulk_range_baseline_off_the_grid_that_moved_is_refused(self):
        client = make_client([
            {"id": 1, "name": "ESPN", "channel_number": 8.3, "streams": []},
        ])
        result = await run_ops(client, [assign_range([1], 100, expected={"1": 8.25})])
        assert result["operationsFailed"] == 1
        client.assign_channel_numbers.assert_not_awaited()
        assert result["errors"][0]["concurrentNumberChanges"] == [
            {"channelId": 1, "baselineNumber": 8.25, "serverNumber": 8.3},
        ]

    @pytest.mark.asyncio
    async def test_bulk_update_expected_number_off_the_grid_that_matches_is_accepted(self):
        # ExpectedChannelNumber.number is the single-channel baseline, so the
        # same loosening covers the pre-existing updateChannel expectation.
        client = make_client(OFF_GRID_LINEUP)
        result = await run_ops(client, [renumber(1, 20, expected=8.25)])
        assert result["success"] is True, result["errors"]

    @pytest.mark.asyncio
    async def test_bulk_update_expected_number_off_the_grid_that_moved_is_refused(self):
        client = make_client([
            {"id": 1, "name": "ESPN", "channel_number": 8.3, "streams": []},
        ])
        result = await run_ops(client, [renumber(1, 20, expected=8.25)])
        assert result["operationsFailed"] == 1
        client.update_channel.assert_not_awaited()

    def test_a_negative_baseline_is_accepted(self):
        request = BulkCommitRequest(operations=[
            assign_range([1], 100, expected={"1": -3}),
            renumber(2, 20, expected=-1.5),
        ])
        assert request.operations[0].expectedNumbers == {1: -3.0}
        assert request.operations[1].expectedNumber.number == -1.5

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_baseline_is_refused(self, bad):
        with pytest.raises(ValidationError):
            BulkCommitRequest(operations=[assign_range([1], 100, expected={"1": bad})])
        with pytest.raises(ValidationError):
            BulkCommitRequest(operations=[renumber(1, 20, expected=bad)])
        with pytest.raises(ValidationError):
            AssignNumbersRequest(channel_ids=[1], expected_numbers={1: bad})

    @pytest.mark.asyncio
    async def test_a_nan_baseline_is_a_422_over_http(self, async_client):
        client = make_client()
        body = {
            "operations": [assign_range([1], 100, expected={"1": "__NAN__"})],
        }
        with patch("routers.channels.get_client", return_value=client):
            response = await async_client.post(
                "/api/channels/bulk-commit", content=_nan_body(body),
                headers={"Content-Type": "application/json"},
            )
        assert response.status_code == 422, response.text
        client.assign_channel_numbers.assert_not_awaited()

    def test_the_write_side_still_refuses_an_off_grid_target(self):
        with pytest.raises(ValidationError):
            BulkCommitRequest(operations=[renumber(1, 8.25, expected=5)])
        with pytest.raises(ValidationError):
            BulkCommitRequest(operations=[assign_range([1], 8.25, expected={"1": 5})])
        with pytest.raises(ValidationError):
            AssignNumbersRequest(
                channel_ids=[1], starting_number=8.25, expected_numbers={1: 5},
            )

    @pytest.mark.asyncio
    async def test_direct_baseline_off_the_grid_that_matches_is_accepted(self, async_client):
        client = make_client(OFF_GRID_LINEUP)
        response = await post_assign(async_client, client, {
            "channel_ids": [1, 2], "starting_number": 100,
            "expected_numbers": {"1": 8.25, "2": 6},
        })
        assert response.status_code == 200, response.text
        client.assign_channel_numbers.assert_awaited_once_with([1, 2], 100)

    @pytest.mark.asyncio
    async def test_direct_baseline_off_the_grid_that_moved_is_a_409(self, async_client):
        client = make_client([
            {"id": 1, "name": "ESPN", "channel_number": 8.3, "streams": []},
        ])
        response = await post_assign(async_client, client, {
            "channel_ids": [1], "starting_number": 100, "expected_numbers": {"1": 8.25},
        })
        assert response.status_code == 409, response.text
        client.assign_channel_numbers.assert_not_awaited()
        assert response.json()["detail"]["concurrentNumberChanges"] == [
            {"channelId": 1, "baselineNumber": 8.25, "serverNumber": 8.3},
        ]

    @pytest.mark.asyncio
    async def test_direct_nan_baseline_is_a_422(self, async_client):
        client = make_client()
        with patch("routers.channels.get_client", return_value=client):
            response = await async_client.post(
                "/api/channels/assign-numbers",
                content=_nan_body({"channel_ids": [1], "expected_numbers": {"1": "__NAN__"}}),
                headers={"Content-Type": "application/json"},
            )
        assert response.status_code == 422, response.text
        client.assign_channel_numbers.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_direct_write_side_still_refuses_an_off_grid_start(self, async_client):
        client = make_client()
        response = await post_assign(async_client, client, {
            "channel_ids": [1], "starting_number": 8.25, "expected_numbers": {"1": 5},
        })
        assert response.status_code == 422, response.text
        client.assign_channel_numbers.assert_not_awaited()


class TestMissingBaselineWording:
    def test_the_422_asks_for_the_number_the_change_was_based_on(self):
        with pytest.raises(ValidationError) as excinfo:
            BulkCommitRequest(operations=[assign_range([1], 100)])
        message = error_text(excinfo)
        assert "the number your change was based on" in message
        assert "Read the channels first" not in message
