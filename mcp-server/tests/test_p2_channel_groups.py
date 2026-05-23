"""TDD tests for P2 channel_groups fixes.

bd-1wq7z.15: delete_channel_group must surface hidden-vs-deleted truthfully.
  Backend DELETE /api/channel-groups/{id} returns {"status":"hidden"} when
  M3U sync settings exist; the tool must not always say "deleted."
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_mcp_and_register():
    from mcp.server.fastmcp import FastMCP
    from tools.channel_groups import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


class TestDeleteChannelGroupHiddenVsDeleted:
    """Verify delete_channel_group reports hidden vs deleted truthfully."""

    @pytest.mark.asyncio
    async def test_hidden_response_reports_hidden_not_deleted(self):
        """When backend returns {"status":"hidden"}, tool must say 'hidden', not 'deleted'."""
        mcp = _make_mcp_and_register()

        async def call_endpoint_side_effect(endpoint, **kwargs):
            name = endpoint.name  # Endpoint is a dataclass with .name attribute
            if name == "groups_delete":
                return {"status": "hidden"}
            if name == "groups_list":
                return []  # hidden groups filtered from list
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channel_groups.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_channel_group", {"group_id": 5})

        text = result[0][0].text
        assert "hidden" in text.lower(), (
            f"Expected 'hidden' in result when backend returns status=hidden, got: {text!r}"
        )
        assert "deleted" not in text.lower() or "not deleted" in text.lower(), (
            f"Tool incorrectly said 'deleted' when group was only hidden: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_deleted_response_reports_deleted(self):
        """When backend returns success/deleted status, tool reports 'deleted'."""
        mcp = _make_mcp_and_register()

        async def call_endpoint_side_effect(endpoint, **kwargs):
            name = endpoint.name
            if name == "groups_delete":
                return {"status": "deleted"}
            if name == "groups_list":
                return []
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channel_groups.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_channel_group", {"group_id": 7})

        text = result[0][0].text
        assert "deleted" in text.lower(), (
            f"Expected 'deleted' in result when backend confirms deletion, got: {text!r}"
        )
        assert "hidden" not in text.lower(), (
            f"Tool incorrectly mentioned 'hidden' for a confirmed deletion: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_null_response_reports_deleted(self):
        """When backend returns None (204 No Content), tool reports 'deleted'."""
        mcp = _make_mcp_and_register()

        async def call_endpoint_side_effect(endpoint, **kwargs):
            name = endpoint.name
            if name == "groups_delete":
                return None  # 204 No Content — truly deleted
            if name == "groups_list":
                return []
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channel_groups.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_channel_group", {"group_id": 9})

        text = result[0][0].text
        assert "deleted" in text.lower(), (
            f"Expected 'deleted' when backend returns None (204), got: {text!r}"
        )
        assert "hidden" not in text.lower(), (
            f"Unexpected 'hidden' for 204 response: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_hidden_message_includes_reason(self):
        """Hidden message includes context about M3U sync settings."""
        mcp = _make_mcp_and_register()

        async def call_endpoint_side_effect(endpoint, **kwargs):
            name = endpoint.name
            if name == "groups_delete":
                return {"status": "hidden"}
            if name == "groups_list":
                return []
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channel_groups.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_channel_group", {"group_id": 5})

        text = result[0][0].text
        # Must include the group_id so the operator knows which one was affected
        assert "5" in text, f"Expected group_id 5 in result: {text!r}"
        # Should explain WHY (M3U sync / not deleted)
        assert any(term in text.lower() for term in ["m3u", "sync", "not deleted"]), (
            f"Expected explanation (M3U/sync/not deleted) in hidden message: {text!r}"
        )
