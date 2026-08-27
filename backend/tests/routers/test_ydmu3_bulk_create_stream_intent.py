"""Bulk-created channels enforce their declared final stream intent."""

from unittest.mock import AsyncMock, patch

import pytest

from routers.channels import BulkCommitRequest, _run_bulk_commit


def _stateful_stream_client(initial_streams):
    client = AsyncMock()
    state = {"streams": list(initial_streams)}
    client.get_channels.return_value = {
        "results": [{"id": 316, "name": "Existing", "streams": list(initial_streams)}],
        "next": None,
    }
    client.get_streams_by_ids.return_value = [
        {"id": stream_id, "name": f"Stream {stream_id}"}
        for stream_id in (10, 20, 30, 55, 56)
    ]
    client.create_channel.return_value = {"id": 900, "name": "New", "streams": []}

    async def get_channel(channel_id):
        return {"id": channel_id, "name": "New" if channel_id == 900 else "Existing", **state}

    async def update_channel(_channel_id, data):
        if "streams" in data:
            state["streams"] = list(data["streams"])
        return {"id": _channel_id, **state}

    client.get_channel.side_effect = get_channel
    client.update_channel.side_effect = update_channel
    return client


@pytest.mark.asyncio
async def test_consolidated_create_add_remove_refuses_before_upstream_mutation():
    client = AsyncMock()
    client.get_streams_by_ids.return_value = [
        {"id": 55, "name": "Stream 55"},
        {"id": 56, "name": "Stream 56"},
    ]
    request = BulkCommitRequest(
        operations=[
            {
                "type": "createChannel",
                "tempId": -1,
                "name": "Incomplete after consolidation",
                "expectedStreamIds": [55, 56],
            },
            {"type": "addStreamToChannel", "channelId": -1, "streamId": 55},
            {"type": "addStreamToChannel", "channelId": -1, "streamId": 56},
            {"type": "removeStreamFromChannel", "channelId": -1, "streamId": 56},
        ],
        consolidate=True,
        continueOnError=True,
    )

    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.journal") as journal:
        result = await _run_bulk_commit(request, batch_id="batch-ydmu3")

    assert result["success"] is False
    assert result["operationsApplied"] == 0
    assert result["validationPassed"] is False
    assert len(result["validationIssues"]) == 1
    issue = result["validationIssues"][0]
    assert issue["type"] == "invalid_operation"
    assert issue["channelId"] == -1
    assert issue["channelName"] == "Incomplete after consolidation"
    assert issue["streamId"] == 56
    assert "expected stream 56" in issue["message"]
    client.create_channel.assert_not_awaited()
    client.create_channel_group.assert_not_awaited()
    client.update_channel.assert_not_awaited()
    journal.log_entry.assert_not_called()
    journal.log_entries.assert_not_called()


@pytest.mark.asyncio
async def test_consolidated_reorder_must_retain_every_expected_stream():
    client = AsyncMock()
    request = BulkCommitRequest(
        operations=[
            {
                "type": "createChannel",
                "tempId": -1,
                "name": "Incomplete after reorder",
                "expectedStreamIds": [55, 56],
            },
            {
                "type": "reorderChannelStreams",
                "channelId": -1,
                "streamIds": [55],
            },
        ],
        consolidate=True,
        continueOnError=True,
    )

    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.journal"):
        result = await _run_bulk_commit(request, batch_id="batch-ydmu3-reorder")

    assert result["success"] is False
    assert result["operationsApplied"] == 0
    assert any("not a permutation" in issue["message"] for issue in result["validationIssues"])
    assert [
        issue["streamId"]
        for issue in result["validationIssues"]
        if "streamId" in issue
    ] == [55, 56]
    client.create_channel.assert_not_awaited()
    client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("continue_on_error", [False, True])
async def test_create_add_add_reorder_executes_in_submitted_order(continue_on_error):
    client = _stateful_stream_client([])
    request = BulkCommitRequest(
        operations=[
            {
                "type": "createChannel",
                "tempId": -1,
                "name": "New",
                "expectedStreamIds": [55, 56],
            },
            {"type": "addStreamToChannel", "channelId": -1, "streamId": 55},
            {"type": "addStreamToChannel", "channelId": -1, "streamId": 56},
            {"type": "reorderChannelStreams", "channelId": -1, "streamIds": [56, 55]},
        ],
        consolidate=True,
        continueOnError=continue_on_error,
    )

    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.journal"):
        result = await _run_bulk_commit(request, batch_id="batch-ordered-create")

    assert result["success"] is True
    assert result["operationsApplied"] == 4
    assert [call.args[1]["streams"] for call in client.update_channel.await_args_list] == [
        [55],
        [55, 56],
        [56, 55],
    ]


@pytest.mark.asyncio
async def test_existing_channel_stream_sequence_survives_consolidation():
    client = _stateful_stream_client([10, 20])
    request = BulkCommitRequest(
        operations=[
            {"type": "addStreamToChannel", "channelId": 316, "streamId": 30},
            {"type": "removeStreamFromChannel", "channelId": 316, "streamId": 10},
            {"type": "reorderChannelStreams", "channelId": 316, "streamIds": [30, 20]},
        ],
        consolidate=True,
    )

    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.journal"):
        result = await _run_bulk_commit(request, batch_id="batch-existing-order")

    assert result["success"] is True
    assert [call.args[1]["streams"] for call in client.update_channel.await_args_list] == [
        [10, 20, 30],
        [20, 30],
        [30, 20],
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("continue_on_error", [False, True])
async def test_reorder_is_validated_at_sequence_position_before_any_mutation(
    continue_on_error
):
    client = _stateful_stream_client([])
    request = BulkCommitRequest(
        operations=[
            {"type": "createChannel", "tempId": -1, "name": "New"},
            {"type": "addStreamToChannel", "channelId": -1, "streamId": 55},
            {"type": "reorderChannelStreams", "channelId": -1, "streamIds": [56, 55]},
            {"type": "addStreamToChannel", "channelId": -1, "streamId": 56},
        ],
        consolidate=True,
        continueOnError=continue_on_error,
    )

    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.journal"):
        result = await _run_bulk_commit(request, batch_id="batch-forward-reorder")

    assert result["success"] is False
    assert result["operationsApplied"] == 0
    assert any("not a permutation" in issue["message"] for issue in result["validationIssues"])
    client.create_channel.assert_not_awaited()
    client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_without_expected_streams_remains_intentionally_streamless():
    client = AsyncMock()
    client.create_channel.return_value = {"id": 900, "name": "Radio", "streams": []}
    request = BulkCommitRequest(
        operations=[{"type": "createChannel", "tempId": -1, "name": "Radio"}],
        consolidate=True,
    )

    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.journal"):
        result = await _run_bulk_commit(request, batch_id="batch-streamless")

    assert result["success"] is True
    assert result["operationsApplied"] == 1
    client.create_channel.assert_awaited_once()
    client.update_channel.assert_not_awaited()
