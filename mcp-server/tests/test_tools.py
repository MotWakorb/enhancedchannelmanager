"""Tests for MCP tool handlers.

Mocks the ECM HTTP API and verifies tool handlers return well-formatted text.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_ecm_client_mock(**method_returns):
    """Create a mock ECMClient with configurable return values."""
    mock = AsyncMock()
    for method, return_value in method_returns.items():
        getattr(mock, method).return_value = return_value
    return mock


class TestListChannels:
    """Tests for list_channels tool."""

    @pytest.mark.asyncio
    async def test_returns_formatted_channels(self):
        """Returns formatted channel list from paginated response."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(get={"count": 2, "results": [
            {"id": 1, "channel_number": 100, "name": "ESPN", "streams": [10, 20]},
            {"id": 2, "channel_number": 101, "name": "CNN", "streams": [30]},
        ]})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_channels", {})

        text = result[0][0].text
        assert "2 channels" in text
        assert "ESPN" in text
        assert "CNN" in text
        assert "id=1" in text

    @pytest.mark.asyncio
    async def test_empty_channels(self):
        """Returns 'no channels' message when empty."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(get={"count": 0, "results": []})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_channels", {})

        assert "No channels found" in result[0][0].text

    @pytest.mark.asyncio
    async def test_api_error_returns_message(self):
        """Returns error message on API failure."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_channels", {})

        assert "Error" in result[0][0].text
        assert "Connection refused" in result[0][0].text


class TestGetChannel:
    """Tests for get_channel tool."""

    @pytest.mark.asyncio
    async def test_returns_channel_details(self):
        """Returns formatted channel details."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(get={
            "id": 1, "name": "ESPN", "channel_number": 100,
            "channel_group_id": 5, "tvg_id": "espn.us",
            "logo_id": 42, "streams": [10, 20, 30],
            "auto_created": False,
        })

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_channel", {"channel_id": 1})

        text = result[0][0].text
        assert "ESPN" in text
        assert "100" in text
        assert "3" in text  # 3 streams


class TestListM3UAccounts:
    """Tests for list_m3u_accounts tool."""

    @pytest.mark.asyncio
    async def test_returns_accounts(self):
        """Returns formatted M3U account list."""
        from tools.m3u import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(get=[
            {"id": 1, "name": "Provider A", "stream_count": 5000, "status": "success"},
            {"id": 2, "name": "Provider B", "stream_count": 3000, "status": "error"},
        ])

        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_m3u_accounts", {})

        text = result[0][0].text
        assert "2 M3U accounts" in text
        assert "Provider A" in text
        assert "5000 streams" in text

    @pytest.mark.asyncio
    async def test_empty_accounts(self):
        """Returns message when no accounts configured."""
        from tools.m3u import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(get=[])

        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_m3u_accounts", {})

        assert "No M3U accounts" in result[0][0].text


class TestGetSettings:
    """Tests for get_settings tool."""

    @pytest.mark.asyncio
    async def test_returns_formatted_settings(self):
        """Returns formatted settings summary."""
        from tools.system import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(get={
            "url": "http://dispatcharr:8000",
            "configured": True,
            "theme": "dark",
            "user_timezone": "America/Chicago",
            "stream_probe_timeout": 30,
            "parallel_probing_enabled": True,
            "max_concurrent_probes": 8,
            "stream_probe_schedule_time": "03:00",
            "smtp_configured": True,
            "discord_configured": False,
            "telegram_configured": False,
        })

        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_settings", {})

        text = result[0][0].text
        assert "dispatcharr:8000" in text
        assert "Connected: True" in text
        assert "SMTP: configured" in text
        assert "Discord: not configured" in text


class TestGetStreamHealth:
    """Tests for get_stream_health tool."""

    @pytest.mark.asyncio
    async def test_returns_summary(self):
        """Returns formatted health summary."""
        from tools.streams import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(get={
            "total_streams": 500,
            "probed": 480,
            "healthy": 450,
            "failed": 30,
        })

        with patch("tools.streams.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_stream_health", {})

        text = result[0][0].text
        assert "Health Summary" in text
        assert "500" in text

    @pytest.mark.asyncio
    async def test_empty_health(self):
        """Returns message when no probe data."""
        from tools.streams import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(get={})

        with patch("tools.streams.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_stream_health", {})

        assert "No stream health data" in result[0][0].text


class TestCreateChannel:
    """Tests for create_channel tool."""

    @pytest.mark.asyncio
    async def test_creates_channel(self):
        """Returns confirmation on successful creation."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(post={"id": 99, "channel_number": 500, "name": "New Channel"})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("create_channel", {"name": "New Channel", "channel_number": 500})

        text = result[0][0].text
        assert "created" in text.lower()
        assert "New Channel" in text
        assert "id=99" in text


class TestDeleteChannel:
    """Tests for delete_channel tool."""

    @pytest.mark.asyncio
    async def test_deletes_channel(self):
        """Returns confirmation on successful deletion."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(delete=None)

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("delete_channel", {"channel_id": 42})

        text = result[0][0].text
        assert "deleted" in text.lower()
        assert "42" in text


class TestAnalyzeAutoCreationRules:
    """Tests for analyze_auto_creation_rules MCP tool (bd-0gntx)."""

    @pytest.mark.asyncio
    async def test_live_mode_calls_analyze_endpoint(self):
        """No bundle_path → POST /api/auto-creation/rules/analyze."""
        from tools.auto_creation import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(post={
            "rules": [{
                "rule_id": 2,
                "rule_name": "Sports Networks - excl Fr and Es",
                "findings": [
                    {
                        "code": "REGEX_TRIVIALLY_MATCHES_ALL",
                        "severity": "warning",
                        "field": "conditions[1].value",
                        "message": "Pattern 'UK|' contains an empty alternation...",
                        "suggestion": "",
                        "detail": {"reason": "empty-alternation"},
                    },
                ],
            }],
            "summary": {"error": 0, "warning": 1, "info": 0},
        })

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("analyze_auto_creation_rules", {})

        text = result[0][0].text
        # Endpoint called.
        mock_client.post.assert_awaited_once_with(
            "/api/auto-creation/rules/analyze"
        )
        # Markdown surfaces the rule and the finding code.
        assert "Sports Networks" in text
        assert "REGEX_TRIVIALLY_MATCHES_ALL" in text
        # Summary surfaces.
        assert "warning" in text.lower()

    @pytest.mark.asyncio
    async def test_clean_rules_says_so(self):
        """No findings → friendly all-clean message."""
        from tools.auto_creation import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(post={
            "rules": [{"rule_id": 1, "rule_name": "Clean", "findings": []}],
            "summary": {"error": 0, "warning": 0, "info": 0},
        })

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("analyze_auto_creation_rules", {})

        text = result[0][0].text
        assert "no findings" in text.lower() or "clean" in text.lower()

    @pytest.mark.asyncio
    async def test_bundle_mode_uploads_file(self, tmp_path):
        """bundle_path set → POST /api/auto-creation/rules/analyze/from-bundle."""
        from tools.auto_creation import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        bundle_file = tmp_path / "debug.tar.gz"
        bundle_file.write_bytes(b"\x1f\x8b\x08\x00fake-gzip-content")

        mock_client = _make_ecm_client_mock(post_multipart={
            "rules": [],
            "summary": {"error": 0, "warning": 0, "info": 0},
        })

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "analyze_auto_creation_rules",
                {"bundle_path": str(bundle_file)},
            )

        mock_client.post_multipart.assert_awaited_once()
        call = mock_client.post_multipart.call_args
        assert call.args[0] == "/api/auto-creation/rules/analyze/from-bundle"
        files = call.kwargs.get("files") or call.args[1]
        assert "file" in files
        # File payload is (filename, bytes, content_type) tuple.
        filename, content_bytes, content_type = files["file"]
        assert filename == "debug.tar.gz"
        assert content_bytes == b"\x1f\x8b\x08\x00fake-gzip-content"

    @pytest.mark.asyncio
    async def test_bundle_path_does_not_exist(self):
        """Missing file → friendly error, no upload attempt."""
        from tools.auto_creation import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(post_multipart={})

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "analyze_auto_creation_rules",
                {"bundle_path": "/no/such/path.tar.gz"},
            )

        text = result[0][0].text
        assert "not found" in text.lower() or "does not exist" in text.lower()
        mock_client.post_multipart.assert_not_awaited()
