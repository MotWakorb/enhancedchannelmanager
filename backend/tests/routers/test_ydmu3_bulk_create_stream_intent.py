"""Bulk-created channels enforce their declared final stream intent."""

from unittest.mock import AsyncMock, patch

import pytest

from routers.channels import BulkCommitRequest, _run_bulk_commit


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
    assert [issue["streamId"] for issue in result["validationIssues"]] == [56]
    client.create_channel.assert_not_awaited()
    client.update_channel.assert_not_awaited()
