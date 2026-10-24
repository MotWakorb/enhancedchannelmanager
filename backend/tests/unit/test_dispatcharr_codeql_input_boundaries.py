"""ADR-005 evidence for #2045's existing FastAPI/Pydantic input boundaries."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,module,parameter",
    [
        ("PATCH", "/api/channel-groups/{}", "channel_groups", "group_id"),
        ("DELETE", "/api/channel-groups/{}", "channel_groups", "group_id"),
        ("GET", "/api/channels/logos/{}", "channels", "logo_id"),
        ("GET", "/api/channels/logos/{}/image", "channels", "logo_id"),
        ("GET", "/api/stream-preview/{}", "stream_preview", "stream_id"),
        ("GET", "/api/channel-preview/{}", "stream_preview", "channel_id"),
    ],
)
@pytest.mark.parametrize(
    "identifier",
    ["42%3Fsynthetic%3D1", "42%23synthetic", "42%252Fother", "..%3Fsynthetic%3D1"],
)
async def test_integer_path_blocks_url_syntax_before_dispatch(
    async_client, method, path, module, parameter, identifier
):
    with (
        patch(f"routers.{module}.get_client", return_value=AsyncMock()) as client,
        patch(
            "dispatcharr_client.DispatcharrClient._request", new_callable=AsyncMock
        ) as request,
    ):
        response = await async_client.request(method, path.format(identifier), json={})

    assert response.status_code == 422, response.text
    assert any(
        error["loc"] == ["path", parameter] and error["type"] == "int_parsing"
        for error in response.json()["detail"]
    )
    client.assert_not_called()
    request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identifier", ["42?synthetic=1", "42#synthetic", "42/other", ".."]
)
@pytest.mark.parametrize("shape", ["assign", "normalize_ids", "normalize_rows"])
async def test_typed_batch_ids_block_url_syntax_before_dispatch(
    async_client, identifier, shape
):
    path = "/api/channels/normalize-preview-batch"
    payload = {"channel_ids": [identifier]}
    location = ["body", "channel_ids", 0]
    if shape == "assign":
        path = "/api/channels/assign-numbers"
    elif shape == "normalize_rows":
        payload = {"channels": [{"channel_id": identifier, "name": "News"}]}
        location = ["body", "channels", 0, "channel_id"]
    with (
        patch("routers.channels.get_client", return_value=AsyncMock()) as client,
        patch(
            "dispatcharr_client.DispatcharrClient._request", new_callable=AsyncMock
        ) as request,
    ):
        response = await async_client.post(path, json=payload)

    assert response.status_code == 422, response.text
    assert any(
        error["loc"] == location and error["type"] == "int_parsing"
        for error in response.json()["detail"]
    )
    client.assert_not_called()
    request.assert_not_awaited()


@pytest.mark.asyncio
async def test_numeric_path_is_parsed_as_an_integer_before_dispatch(async_client):
    client = AsyncMock()
    client.update_channel_group.return_value = {"id": 42}
    with patch("routers.channel_groups.get_client", return_value=client):
        response = await async_client.patch("/api/channel-groups/0042", json={})

    assert response.status_code == 200, response.text
    client.update_channel_group.assert_awaited_once_with(42, {})
