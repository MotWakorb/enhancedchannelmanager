"""
Unit tests for DispatcharrClient.get_channels group-filter translation
(bd-1wq7z.11).

Root cause: Dispatcharr's ``/api/channels/channels/`` ``channel_group`` query
filter matches on the group NAME, not the group ID. ECM callers (frontend,
MCP build_channel_lineup/list_channels) pass a numeric group ID, so forwarding
the bare ID as ``channel_group`` matched zero groups and returned 0 rows even
when hundreds of channels belonged to that group.

The client must translate the incoming group ID -> group name before forwarding
the filter to Dispatcharr.
"""
import pytest
from unittest.mock import AsyncMock, patch

import httpx

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient


def _response(status_code: int, json_body=None):
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = lambda: json_body if json_body is not None else {}
    resp.raise_for_status = lambda: None
    return resp


def _make_client():
    settings = DispatcharrSettings(
        url="http://dispatcharr:8000",
        auth_method="password",
        username="admin",
        password="secret",
    )
    return DispatcharrClient(settings)


@pytest.mark.asyncio
async def test_group_filter_forwards_group_name_not_id():
    """A numeric channel_group ID is translated to the group NAME before
    being sent to Dispatcharr (whose channel_group filter is name-based)."""
    client = _make_client()
    try:
        request_mock = AsyncMock(
            return_value=_response(200, {"results": [{"id": 1}], "count": 1})
        )
        groups_mock = AsyncMock(
            return_value=[
                {"id": 1306, "name": "Entertainment"},
                {"id": 1320, "name": "Radio"},
            ]
        )

        with patch.object(client, "_request", request_mock), \
             patch.object(client, "get_channel_groups", groups_mock):
            result = await client.get_channels(channel_group=1320)

        assert result["count"] == 1
        params = request_mock.await_args.kwargs["params"]
        # The bug: params["channel_group"] == 1320 (the ID) -> 0 rows.
        # The fix: params["channel_group"] == "Radio" (the name) -> matches.
        assert params["channel_group"] == "Radio"
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_no_group_filter_skips_group_lookup():
    """When no group filter is requested, the group-name lookup is skipped
    and no channel_group param is sent."""
    client = _make_client()
    try:
        request_mock = AsyncMock(
            return_value=_response(200, {"results": [], "count": 0})
        )
        groups_mock = AsyncMock(return_value=[])

        with patch.object(client, "_request", request_mock), \
             patch.object(client, "get_channel_groups", groups_mock):
            await client.get_channels()

        groups_mock.assert_not_awaited()
        params = request_mock.await_args.kwargs["params"]
        assert "channel_group" not in params
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_unknown_group_id_does_not_silently_return_all():
    """An unresolvable group ID must NOT fall through to an unfiltered query
    (which would silently return every channel). It returns empty instead."""
    client = _make_client()
    try:
        request_mock = AsyncMock(
            return_value=_response(200, {"results": [], "count": 0})
        )
        groups_mock = AsyncMock(
            return_value=[{"id": 1320, "name": "Radio"}]
        )

        with patch.object(client, "_request", request_mock), \
             patch.object(client, "get_channel_groups", groups_mock):
            result = await client.get_channels(channel_group=999999)

        # No matching group name -> empty result, and we must NOT have issued
        # an unfiltered Dispatcharr query that returns everything.
        assert result["count"] == 0
        assert result["results"] == []
        request_mock.assert_not_awaited()
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_group_filter_zero_includes_channel_group_param():
    """channel_group=0 must NOT be silently dropped (bd-d5z9u).

    The original ``if channel_group:`` guard is falsy for 0, so
    ``get_channels(channel_group=0)`` behaved identically to
    ``get_channels()`` and fetched ALL groups. The fix uses
    ``if channel_group is not None:`` so that 0 is a valid filter value."""
    client = _make_client()
    try:
        request_mock = AsyncMock(
            return_value=_response(200, {"results": [{"id": 10}], "count": 1})
        )
        groups_mock = AsyncMock(
            return_value=[
                {"id": 0, "name": "Uncategorized"},
                {"id": 1, "name": "Sports"},
            ]
        )

        with patch.object(client, "_request", request_mock), \
             patch.object(client, "get_channel_groups", groups_mock):
            result = await client.get_channels(channel_group=0)

        params = request_mock.await_args.kwargs["params"]
        assert params["channel_group"] == "Uncategorized"
        assert result["count"] == 1
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_group_filter_none_omits_channel_group_param():
    """channel_group=None (the default) must omit the channel_group param
    entirely, matching the pre-existing no-filter behaviour (bd-d5z9u)."""
    client = _make_client()
    try:
        request_mock = AsyncMock(
            return_value=_response(200, {"results": [], "count": 0})
        )
        groups_mock = AsyncMock(return_value=[{"id": 0, "name": "Uncategorized"}])

        with patch.object(client, "_request", request_mock), \
             patch.object(client, "get_channel_groups", groups_mock):
            await client.get_channels(channel_group=None)

        groups_mock.assert_not_awaited()
        params = request_mock.await_args.kwargs["params"]
        assert "channel_group" not in params
    finally:
        await client._client.aclose()
