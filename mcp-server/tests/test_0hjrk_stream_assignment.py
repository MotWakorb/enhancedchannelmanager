"""TDD tests for enhancedchannelmanager-0hjrk.6 — stream assignment status (MCP).

The backend derives channel-assignment status (has_channel /
assigned_channel_id / assigned_channel_ids) from a cached reverse
stream->channel index. These tests verify the MCP layer:

  * get_streams_by_ids(include_assignment=True) renders "-> channel N" for
    assigned streams and "[unassigned]" for unassigned ones, and passes the
    flag through to the backend.
  * get_streams_by_ids default (include_assignment=False) is unchanged — no
    assignment markers, no include_assignment in the request body.
  * list_streams / search_streams `assigned` filter selects assigned-only
    (True) or unassigned-only (False), requesting assignment from the backend
    and filtering client-side. Default (None) does no lookup.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_mcp_and_register():
    """Register streams tools on a fresh FastMCP and return the mcp instance."""
    from mcp.server.fastmcp import FastMCP
    from tools.streams import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


# ---------------------------------------------------------------------------
# get_streams_by_ids with include_assignment
# ---------------------------------------------------------------------------

class TestGetStreamsByIdsAssignment:
    @pytest.mark.asyncio
    async def test_include_assignment_renders_channel_and_unassigned(self):
        """Assigned -> '-> channel N'; unassigned -> '[unassigned]'."""
        mcp = _make_mcp_and_register()

        by_ids_payload = [
            {"id": 1, "name": "Stream 1", "has_channel": True,
             "assigned_channel_id": 100, "assigned_channel_ids": [100]},
            {"id": 2, "name": "Stream 2", "has_channel": True,
             "assigned_channel_id": 100, "assigned_channel_ids": [100, 200]},
            {"id": 3, "name": "Stream 3", "has_channel": False,
             "assigned_channel_id": None, "assigned_channel_ids": []},
        ]

        async def call_endpoint_side_effect(endpoint, **kwargs):
            name = endpoint.name
            if name == "streams_by_ids":
                return by_ids_payload
            if name == "m3u_list_providers":
                return []
            if name == "groups_list":
                return []
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "get_streams_by_ids",
                {"stream_ids": [1, 2, 3], "include_assignment": True},
            )

        text = result[0][0].text
        # Single-channel assignment.
        assert "channel 100" in text
        # Multi-channel assignment rendered with the full list.
        assert "channels" in text and "200" in text
        # Unassigned marker.
        assert "[unassigned]" in text

        # include_assignment was forwarded to the backend body.
        by_ids_calls = [
            c for c in client.call_endpoint.call_args_list
            if c.args and c.args[0].name == "streams_by_ids"
        ]
        assert by_ids_calls, "streams_by_ids endpoint was not called"
        body = by_ids_calls[0].kwargs.get("body", {})
        assert body.get("include_assignment") is True

    @pytest.mark.asyncio
    async def test_default_unchanged_no_assignment(self):
        """Default (no include_assignment): no markers, no include_assignment body field."""
        mcp = _make_mcp_and_register()

        by_ids_payload = [{"id": 1, "name": "Stream 1"}]

        async def call_endpoint_side_effect(endpoint, **kwargs):
            name = endpoint.name
            if name == "streams_by_ids":
                return by_ids_payload
            if name == "m3u_list_providers":
                return []
            if name == "groups_list":
                return []
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_streams_by_ids", {"stream_ids": [1]})

        text = result[0][0].text
        assert "[unassigned]" not in text
        assert "-> channel" not in text and "→ channel" not in text

        by_ids_calls = [
            c for c in client.call_endpoint.call_args_list
            if c.args and c.args[0].name == "streams_by_ids"
        ]
        body = by_ids_calls[0].kwargs.get("body", {})
        # Default must not send include_assignment (keeps backend on cheap path).
        assert "include_assignment" not in body or body["include_assignment"] is False


# ---------------------------------------------------------------------------
# list_streams / search_streams `assigned` filter
# ---------------------------------------------------------------------------

def _list_payload():
    return {
        "count": 3,
        "results": [
            {"id": 1, "name": "Assigned A", "has_channel": True,
             "assigned_channel_id": 100, "assigned_channel_ids": [100]},
            {"id": 2, "name": "Unassigned B", "has_channel": False,
             "assigned_channel_id": None, "assigned_channel_ids": []},
            {"id": 3, "name": "Assigned C", "has_channel": True,
             "assigned_channel_id": 200, "assigned_channel_ids": [200]},
        ],
    }


class TestListStreamsAssignedFilter:
    @pytest.mark.asyncio
    async def test_assigned_true_selects_only_assigned(self):
        mcp = _make_mcp_and_register()
        client = AsyncMock()
        client.call_endpoint.return_value = _list_payload()

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_streams", {"assigned": True})

        text = result[0][0].text
        assert "Assigned A" in text
        assert "Assigned C" in text
        assert "Unassigned B" not in text

        # The backend was asked to include assignment data.
        call = client.call_endpoint.call_args
        query = call.kwargs.get("query", {})
        assert query.get("include_assignment") is True

    @pytest.mark.asyncio
    async def test_assigned_false_selects_only_unassigned(self):
        mcp = _make_mcp_and_register()
        client = AsyncMock()
        client.call_endpoint.return_value = _list_payload()

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_streams", {"assigned": False})

        text = result[0][0].text
        assert "Unassigned B" in text
        assert "Assigned A" not in text
        assert "Assigned C" not in text

        call = client.call_endpoint.call_args
        query = call.kwargs.get("query", {})
        assert query.get("include_assignment") is True

    @pytest.mark.asyncio
    async def test_default_none_no_lookup(self):
        mcp = _make_mcp_and_register()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "count": 1,
            "results": [{"id": 1, "name": "Stream 1"}],
        }

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_streams", {})

        text = result[0][0].text
        assert "Stream 1" in text
        # Default must not request assignment (no added cost).
        call = client.call_endpoint.call_args
        query = call.kwargs.get("query", {})
        assert "include_assignment" not in query


class TestSearchStreamsAssignedFilter:
    @pytest.mark.asyncio
    async def test_assigned_true_selects_only_assigned(self):
        mcp = _make_mcp_and_register()
        client = AsyncMock()
        client.call_endpoint.return_value = _list_payload()

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "search_streams", {"query": "x", "assigned": True}
            )

        text = result[0][0].text
        assert "Assigned A" in text
        assert "Assigned C" in text
        assert "Unassigned B" not in text

        call = client.call_endpoint.call_args
        query = call.kwargs.get("query", {})
        assert query.get("include_assignment") is True

    @pytest.mark.asyncio
    async def test_assigned_false_selects_only_unassigned(self):
        mcp = _make_mcp_and_register()
        client = AsyncMock()
        client.call_endpoint.return_value = _list_payload()

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "search_streams", {"query": "x", "assigned": False}
            )

        text = result[0][0].text
        assert "Unassigned B" in text
        assert "Assigned A" not in text

    @pytest.mark.asyncio
    async def test_default_none_no_lookup(self):
        mcp = _make_mcp_and_register()
        client = AsyncMock()
        client.call_endpoint.return_value = _list_payload()

        with patch("tools.streams.get_ecm_client", return_value=client):
            await mcp.call_tool("search_streams", {"query": "x"})

        call = client.call_endpoint.call_args
        query = call.kwargs.get("query", {})
        assert "include_assignment" not in query
