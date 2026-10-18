"""Real ECM route, synthetic upstream: clearing provenance keeps channels."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_clearing_flags_preserves_channels_and_unselected_groups(async_client):
    rows = {
        17: {"id": 17, "name": "Synthetic auto", "channel_number": 17,
             "channel_group_id": 5, "auto_created": True, "auto_created_by": 4,
             "streams": [71, 72]},
        18: {"id": 18, "name": "Synthetic manual", "channel_number": 18,
             "channel_group_id": 5, "auto_created": False, "auto_created_by": None},
        19: {"id": 19, "name": "Synthetic other group", "channel_number": 19,
             "channel_group_id": 6, "auto_created": True, "auto_created_by": 4},
    }
    before = deepcopy(rows)
    upstream = AsyncMock()
    upstream.get_channels.return_value = {"results": list(rows.values()), "next": None}

    async def update(channel_id, data):
        rows[channel_id].update(data)

    upstream.update_channel.side_effect = update
    with patch("routers.channels.get_client", return_value=upstream):
        response = await async_client.post("/api/channels/clear-auto-created", json={"group_ids": [5]})

    assert response.status_code == 200
    assert response.json()["updated_count"] == 1
    assert response.json()["failed_channels"] == []
    upstream.update_channel.assert_awaited_once_with(17, {"auto_created": False, "auto_created_by": None})
    upstream.delete_channel.assert_not_awaited()
    upstream.bulk_delete_channels.assert_not_awaited()
    expected = deepcopy(before)
    expected[17].update(auto_created=False, auto_created_by=None)
    assert rows == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("body,status", [({}, 422), ({"group_ids": []}, 400)])
async def test_backend_requires_explicit_groups_before_any_update(async_client, body, status):
    upstream = AsyncMock()
    with patch("routers.channels.get_client", return_value=upstream):
        response = await async_client.post("/api/channels/clear-auto-created", json=body)
    assert response.status_code == status
    upstream.get_channels.assert_not_awaited()
    upstream.update_channel.assert_not_awaited()
    upstream.delete_channel.assert_not_awaited()
