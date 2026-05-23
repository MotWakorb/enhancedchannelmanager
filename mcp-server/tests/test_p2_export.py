"""TDD tests for bd-1wq7z.21 — list_publish_configs new MCP tool.

Tests are written RED-first: they assert the fix before it exists.
After implementation the tests must turn GREEN.

Backend endpoint confirmed: GET /api/export/publish-configs
Response shape (from backend/routers/export.py list_publish_configs):
  List of objects with keys:
    id, name, profile_id, profile_name, target_id, target_name,
    schedule_type, cron_expression, event_triggers, enabled, webhook_url
"""
import pytest
from unittest.mock import AsyncMock, patch

from _endpoint_contracts import ENDPOINTS


def _make_client(return_value=None, side_effect=None):
    mock = AsyncMock()
    if side_effect is not None:
        mock.call_endpoint.side_effect = side_effect
    else:
        mock.call_endpoint.return_value = return_value
    return mock


class TestListPublishConfigsEndpointContract:
    """Verify the export_list_publish_configs Endpoint is registered."""

    def test_endpoint_registered(self):
        """export_list_publish_configs must exist in ENDPOINTS registry."""
        assert "export_list_publish_configs" in ENDPOINTS

    def test_endpoint_method_and_path(self):
        """Endpoint must target GET /api/export/publish-configs."""
        ep = ENDPOINTS["export_list_publish_configs"]
        assert ep.method == "GET"
        assert ep.path == "/api/export/publish-configs"

    def test_endpoint_is_list_response(self):
        """Backend returns a JSON array, so response_is_list must be True."""
        ep = ENDPOINTS["export_list_publish_configs"]
        assert ep.response_is_list is True


class TestListPublishConfigsTool:
    """bd-1wq7z.21 — list_publish_configs tool behaviour."""

    @pytest.mark.asyncio
    async def test_tool_is_registered(self):
        """list_publish_configs must be a registered MCP tool."""
        from tools.export import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        # Should not raise KeyError / ToolNotFoundError
        mock_client = _make_client(return_value=[])
        with patch("tools.export.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_publish_configs", {})
        assert result is not None

    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(self):
        """Tool must call call_endpoint with export_list_publish_configs."""
        from tools.export import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client(return_value=[])
        with patch("tools.export.get_ecm_client", return_value=mock_client):
            await mcp.call_tool("list_publish_configs", {})

        mock_client.call_endpoint.assert_called_once()
        called_endpoint = mock_client.call_endpoint.call_args[0][0]
        assert called_endpoint == ENDPOINTS["export_list_publish_configs"], (
            "Tool must use the export_list_publish_configs Endpoint object"
        )

    @pytest.mark.asyncio
    async def test_empty_list_message(self):
        """Returns a 'no configs' message when backend returns empty list."""
        from tools.export import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client(return_value=[])
        with patch("tools.export.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_publish_configs", {})

        text = result[0][0].text
        assert "No publish" in text or "no publish" in text.lower()

    @pytest.mark.asyncio
    async def test_formats_config_list(self):
        """Returns formatted list showing id, name, profile, schedule type."""
        from tools.export import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        configs = [
            {
                "id": 1,
                "name": "Nightly S3 Publish",
                "profile_id": 5,
                "profile_name": "Main Playlist",
                "target_id": 2,
                "target_name": "My S3 Bucket",
                "schedule_type": "cron",
                "cron_expression": "0 2 * * *",
                "event_triggers": [],
                "enabled": True,
                "webhook_url": None,
            },
            {
                "id": 2,
                "name": "On-demand Upload",
                "profile_id": 5,
                "profile_name": "Main Playlist",
                "target_id": None,
                "target_name": None,
                "schedule_type": "manual",
                "cron_expression": None,
                "event_triggers": [],
                "enabled": False,
                "webhook_url": None,
            },
        ]
        mock_client = _make_client(return_value=configs)

        with patch("tools.export.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_publish_configs", {})

        text = result[0][0].text
        assert "2" in text  # count or id
        assert "Nightly S3 Publish" in text
        assert "On-demand Upload" in text
        assert "id=1" in text or "id=2" in text

    @pytest.mark.asyncio
    async def test_shows_profile_name(self):
        """Output must show the profile_name so users can correlate to export profiles."""
        from tools.export import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        configs = [
            {
                "id": 3,
                "name": "Weekly Drive",
                "profile_id": 7,
                "profile_name": "Kids Profile",
                "target_id": 4,
                "target_name": "Google Drive",
                "schedule_type": "cron",
                "cron_expression": "0 3 * * 0",
                "event_triggers": [],
                "enabled": True,
                "webhook_url": None,
            }
        ]
        mock_client = _make_client(return_value=configs)

        with patch("tools.export.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_publish_configs", {})

        text = result[0][0].text
        assert "Kids Profile" in text

    @pytest.mark.asyncio
    async def test_api_error_returns_message(self):
        """Returns error message on API failure."""
        from tools.export import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client(side_effect=Exception("Connection refused"))
        with patch("tools.export.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_publish_configs", {})

        text = result[0][0].text
        assert "Error" in text
        assert "Connection refused" in text
