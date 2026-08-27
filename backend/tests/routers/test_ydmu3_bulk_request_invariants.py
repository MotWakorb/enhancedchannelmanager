"""Bulk commit validates temp-channel dependencies before any mutation."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest


DEPENDENT_OPERATIONS = [
    {"type": "updateChannel", "channelId": -1, "data": {"name": "Changed"}},
    {"type": "deleteChannel", "channelId": -1},
    {"type": "addStreamToChannel", "channelId": -1, "streamId": 55},
    {"type": "removeStreamFromChannel", "channelId": -1, "streamId": 55},
    {"type": "reorderChannelStreams", "channelId": -1, "streamIds": []},
    {
        "type": "bulkAssignChannelNumbers",
        "channelIds": [316, -1],
        "startingNumber": 1,
    },
    {
        "type": "setProfileMembership",
        "profileId": 5,
        "channelId": -1,
        "enabled": True,
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize("consolidate", [False, True])
@pytest.mark.parametrize("continue_on_error", [False, True])
async def test_forward_temp_reference_rejects_request_before_mutation(
    async_client, consolidate, continue_on_error
):
    client = AsyncMock()
    with patch("routers.channels.get_client", return_value=client):
        response = await async_client.post(
            "/api/channels/bulk-commit",
            json={
                "operations": [
                    {"type": "addStreamToChannel", "channelId": -1, "streamId": 55},
                    {"type": "createChannel", "tempId": -1, "name": "Too late"},
                ],
                "consolidate": consolidate,
                "continueOnError": continue_on_error,
            },
        )

    assert response.status_code == 422
    assert "earlier createChannel" in response.text
    assert client.mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("temp_id", [0, 55])
async def test_create_temp_id_must_be_strictly_negative(async_client, temp_id):
    client = AsyncMock()
    with patch("routers.channels.get_client", return_value=client):
        response = await async_client.post(
            "/api/channels/bulk-commit",
            json={
                "operations": [
                    {"type": "createChannel", "tempId": temp_id, "name": "Invalid"},
                    {"type": "addStreamToChannel", "channelId": temp_id, "streamId": 55},
                ]
            },
        )

    assert response.status_code == 422
    assert client.mock_calls == []


@pytest.mark.asyncio
async def test_duplicate_create_temp_ids_reject_request_before_mutation(async_client):
    client = AsyncMock()
    with patch("routers.channels.get_client", return_value=client):
        response = await async_client.post(
            "/api/channels/bulk-commit",
            json={
                "operations": [
                    {"type": "createChannel", "tempId": -1, "name": "First"},
                    {"type": "createChannel", "tempId": -1, "name": "Second"},
                ],
                "continueOnError": True,
            },
        )

    assert response.status_code == 422
    assert "unique" in response.text
    assert client.mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("dependent", DEPENDENT_OPERATIONS, ids=lambda op: op["type"])
async def test_every_negative_channel_reference_shape_uses_common_validator(
    async_client, dependent
):
    client = AsyncMock()
    with patch("routers.channels.get_client", return_value=client):
        response = await async_client.post(
            "/api/channels/bulk-commit",
            json={"operations": [deepcopy(dependent)]},
        )

    assert response.status_code == 422
    assert "earlier createChannel" in response.text
    assert client.mock_calls == []


@pytest.mark.asyncio
async def test_positive_existing_channel_reference_remains_valid(async_client):
    client = AsyncMock()
    client.get_channels.return_value = {
        "results": [{"id": 316, "name": "Existing", "streams": []}],
        "next": None,
    }
    client.get_streams_by_ids.return_value = [{"id": 55, "name": "Stream 55"}]
    client.get_channel.return_value = {"id": 316, "name": "Existing", "streams": []}

    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.journal"):
        response = await async_client.post(
            "/api/channels/bulk-commit",
            json={
                "operations": [
                    {"type": "addStreamToChannel", "channelId": 316, "streamId": 55}
                ],
                "validateOnly": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["validationPassed"] is True
