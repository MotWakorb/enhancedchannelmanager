"""TDD tests for enhancedchannelmanager-uahp6 — find_duplicate_channels MCP
tool accepts an optional channel_ids scope and forwards it to the backend
unchanged, preserving the historical no-args (global) call shape.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _register_and_get_mcp():
    from tools.channels import register
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _make_mock(return_value=None):
    mock = AsyncMock()
    mock.call_endpoint.return_value = return_value if return_value is not None else {"groups": []}
    return mock


class TestFindDuplicateChannelsScopePassthrough:
    """channel_ids rides through to the backend body unchanged."""

    @pytest.mark.asyncio
    async def test_channel_ids_forwarded_in_body(self):
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"groups": [], "total_duplicate_channels": 0})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            await mcp.call_tool("find_duplicate_channels", {"channel_ids": [101, 102, 103]})

        call = mock_client.call_endpoint.call_args
        assert call.kwargs.get("body") == {"channel_ids": [101, 102, 103]}

    @pytest.mark.asyncio
    async def test_omitted_channel_ids_sends_no_body(self):
        """No channel_ids arg at all -> body=None, matching the pre-existing
        global call shape so MCP callers that never scope keep working."""
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"groups": [], "total_duplicate_channels": 0})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            await mcp.call_tool("find_duplicate_channels", {})

        call = mock_client.call_endpoint.call_args
        assert call.kwargs.get("body") is None

    @pytest.mark.asyncio
    async def test_empty_channel_ids_list_still_forwarded_not_dropped(self):
        """An explicit [] must reach the backend as [] (which the backend
        treats as 'scope to nothing') — it must NOT be coerced to None,
        which would silently widen the scope to a global scan."""
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"groups": [], "total_duplicate_channels": 0})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            await mcp.call_tool("find_duplicate_channels", {"channel_ids": []})

        call = mock_client.call_endpoint.call_args
        assert call.kwargs.get("body") == {"channel_ids": []}

    @pytest.mark.asyncio
    async def test_scoped_results_render_in_summary_text(self):
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={
            "groups": [{
                "normalized_name": "espn",
                "channels": [
                    {"id": 101, "name": "ESPN", "channel_number": 1, "stream_count": 2, "channel_group_name": ""},
                    {"id": 102, "name": "ESPN (dup)", "channel_number": None, "stream_count": 0, "channel_group_name": ""},
                ],
            }],
            "total_duplicate_channels": 2,
        })

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("find_duplicate_channels", {"channel_ids": [101, 102]})

        text = result[0][0].text
        assert "Found 1 duplicate groups (2 channels total)" in text
        assert "id=101" in text and "id=102" in text

    @pytest.mark.asyncio
    async def test_scoped_empty_result_names_the_scope_in_message(self):
        """Distinguishes a scoped no-duplicates result from a global one, so
        the caller isn't misled into thinking the whole install was clean."""
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"groups": [], "total_duplicate_channels": 0})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("find_duplicate_channels", {"channel_ids": [101, 102]})

        text = result[0][0].text
        assert "within the given channel_ids" in text


class TestFindDuplicateChannelsScopeContract:
    """channel_ids is a declared request field on the endpoint contract, so
    call_endpoint's subset check doesn't reject it."""

    def test_channel_ids_is_in_find_duplicates_contract(self):
        from _endpoint_contracts import ENDPOINTS

        assert "channel_ids" in ENDPOINTS["channels_find_duplicates"].request_fields
