"""MCP uses the same account/name view-exclusion API as the tab."""
import json
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from tools.m3u import register
from tools._safety_policy import SAFETY_INVENTORY, ToolSafety


@pytest.mark.asyncio
async def test_exclusion_tools_use_explicit_shared_contracts():
    mcp = FastMCP("test")
    register(mcp)
    group = {"m3u_account_id": 2, "group_name": "Sports.* 🏈"}
    client = AsyncMock()
    client.call_endpoint.return_value = {"exclusions": [group]}
    with patch("tools.m3u.get_ecm_client", return_value=client):
        result = await mcp.call_tool("get_m3u_change_exclusions", {})
        assert json.loads(result[0][0].text) == {"exclusions": [group]}
        assert client.call_endpoint.call_args.args[0].name == "m3u_change_exclusions_get"
        await mcp.call_tool("update_m3u_change_exclusions", {"add": [group]})
        call = client.call_endpoint.call_args
        assert call.args[0].name == "m3u_change_exclusions_update"
        assert call.kwargs["body"] == {"add": [group], "remove": [], "clear": False}
        await mcp.call_tool("update_m3u_change_exclusions", {"remove": [group]})
        assert client.call_endpoint.call_args.kwargs["body"]["remove"] == [group]
        await mcp.call_tool("update_m3u_change_exclusions", {"clear": True})
        assert client.call_endpoint.call_args.kwargs["body"]["clear"] is True
        await mcp.call_tool("list_m3u_change_groups", {"m3u_account_id": 2, "search": "Sports", "page": 3})
        call = client.call_endpoint.call_args
        assert call.args[0].name == "m3u_change_groups"
        assert call.kwargs["query"] == {"m3u_account_id": 2, "search": "Sports", "page": 3, "page_size": 200}
    assert SAFETY_INVENTORY["get_m3u_change_exclusions"] == ToolSafety.READ_ONLY
    assert SAFETY_INVENTORY["list_m3u_change_groups"] == ToolSafety.READ_ONLY
    assert SAFETY_INVENTORY["update_m3u_change_exclusions"] == ToolSafety.MUTATING


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [
    {"m3u_account_id": True, "group_name": "Sports"},
    {"m3u_account_id": 0, "group_name": "Sports"},
    {"m3u_account_id": 1, "group_name": ""},
    {"m3u_account_id": 1, "group_name": "Sports", "unknown": 1},
])
async def test_invalid_identities_are_rejected_before_backend_call(identity):
    mcp = FastMCP("test")
    register(mcp)
    client = AsyncMock()
    with patch("tools.m3u.get_ecm_client", return_value=client):
        with pytest.raises(ToolError):
            await mcp.call_tool("update_m3u_change_exclusions", {"add": [identity]})
    client.call_endpoint.assert_not_called()
