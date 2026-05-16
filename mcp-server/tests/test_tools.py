"""Tests for MCP tool handlers.

Mocks the ECM HTTP API and verifies tool handlers return well-formatted text.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_ecm_client_mock(**method_returns):
    """Create a mock ECMClient with configurable return values.

    Tools migrated to the endpoint-contract registry (bd-vtghg Phase 1) call
    ``client.call_endpoint(ENDPOINTS["..."], ...)`` instead of
    ``client.get/post/...`` — pass ``call_endpoint=<return value>`` for those.
    The legacy ``get=``/``post=``/etc. kwargs still work for tools that haven't
    been migrated and for the raw-verb fallbacks.
    """
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

        mock_client = _make_ecm_client_mock(call_endpoint={"count": 2, "results": [
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

        mock_client = _make_ecm_client_mock(call_endpoint={"count": 0, "results": []})

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
        mock_client.call_endpoint.side_effect = Exception("Connection refused")

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

        mock_client = _make_ecm_client_mock(call_endpoint={
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

        mock_client = _make_ecm_client_mock(call_endpoint=[
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

        mock_client = _make_ecm_client_mock(call_endpoint=[])

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

        mock_client = _make_ecm_client_mock(call_endpoint={
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

        mock_client = _make_ecm_client_mock(call_endpoint={
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

        mock_client = _make_ecm_client_mock(call_endpoint={})

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

        mock_client = _make_ecm_client_mock(call_endpoint={"id": 99, "channel_number": 500, "name": "New Channel"})

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

        mock_client = _make_ecm_client_mock(call_endpoint=None)

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

        mock_client = _make_ecm_client_mock(call_endpoint={
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
        # Endpoint called via the contract registry.
        from _endpoint_contracts import ENDPOINTS
        mock_client.call_endpoint.assert_awaited_once_with(ENDPOINTS["ac_analyze_rules"])
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

        mock_client = _make_ecm_client_mock(call_endpoint={
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


class TestUpdateChannelGroupId:
    """update_channel / create_channel send channel_group_id, not group_id (bd-7q9l3 / GH #221)."""

    @pytest.mark.asyncio
    async def test_update_channel_sends_channel_group_id(self):
        """The group_id arg must be wired to the backend's channel_group_id field."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={"id": 1, "name": "ESPN"})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            await mcp.call_tool("update_channel", {"channel_id": 1, "group_id": 7})

        from _endpoint_contracts import ENDPOINTS
        mock_client.call_endpoint.assert_awaited_once()
        call = mock_client.call_endpoint.call_args
        assert call.args[0] is ENDPOINTS["channels_update"]
        assert call.kwargs["path_args"] == {"channel_id": 1}
        payload = call.kwargs["body"]
        assert payload == {"channel_group_id": 7}
        assert "group_id" not in payload  # the bare key would be silently dropped

    @pytest.mark.asyncio
    async def test_create_channel_sends_channel_group_id(self):
        """create_channel's group_id arg must map to channel_group_id."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={"id": 9, "channel_number": 5, "name": "New"})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            await mcp.call_tool("create_channel", {"name": "New", "group_id": 7})

        from _endpoint_contracts import ENDPOINTS
        mock_client.call_endpoint.assert_awaited_once()
        call = mock_client.call_endpoint.call_args
        assert call.args[0] is ENDPOINTS["channels_create"]
        payload = call.kwargs["body"]
        assert payload.get("channel_group_id") == 7
        assert "group_id" not in payload


class TestListAutoCreationRules:
    """list_auto_creation_rules unwraps the {"rules": [...]} envelope (bd-pvw35 / GH #222)."""

    @pytest.mark.asyncio
    async def test_unwraps_rules_envelope(self):
        """Backend returns {"rules": [...]}; the tool must iterate the list, not the dict keys."""
        from tools.auto_creation import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={"rules": [
            {"id": 1, "name": "Sports", "enabled": True, "priority": 10},
            {"id": 2, "name": "News", "enabled": False, "priority": 20},
        ]})

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_auto_creation_rules", {})

        text = result[0][0].text
        assert "2 auto-creation rules" in text
        assert "Sports" in text
        assert "News" in text
        assert "id=1" in text
        # No AttributeError leaked through as an error string.
        assert "has no attribute" not in text

    @pytest.mark.asyncio
    async def test_empty_rules_envelope(self):
        """{"rules": []} → friendly 'none configured' message."""
        from tools.auto_creation import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={"rules": []})

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_auto_creation_rules", {})

        assert "No auto-creation rules" in result[0][0].text


class TestBulkAddStreamsToChannel:
    """bulk_add_streams_to_channel uses the single-roundtrip backend endpoint (bd-02xjj / GH #223)."""

    @pytest.mark.asyncio
    async def test_calls_plural_add_streams_endpoint_once(self):
        """One POST /api/channels/{id}/add-streams — not one request per stream."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "channel": {"id": 1, "name": "ESPN"},
            "added": [10, 11, 12],
            "skipped": [],
            "total_streams": 3,
        })

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "bulk_add_streams_to_channel",
                {"channel_id": 1, "stream_ids": [10, 11, 12]},
            )

        from _endpoint_contracts import ENDPOINTS
        mock_client.call_endpoint.assert_awaited_once()
        call = mock_client.call_endpoint.call_args
        assert call.args[0] is ENDPOINTS["channels_add_streams"]
        assert call.kwargs["path_args"] == {"channel_id": 1}
        assert call.kwargs["body"] == {"stream_ids": [10, 11, 12]}
        # Generous per-call timeout passed for slow hardware.
        assert call.kwargs.get("timeout", 0) >= 120.0
        text = result[0][0].text
        assert "Added 3 stream(s) to channel 1" in text


class TestBulkCommitChannelsErrorDetail:
    """bulk_commit_channels surfaces the 422 body's detail (bd-mjtxn / GH #224)."""

    @pytest.mark.asyncio
    async def test_surfaces_validation_detail(self):
        """A RuntimeError from the client carrying the FastAPI detail must reach the tool output."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = RuntimeError(
            "POST /api/channels/bulk-commit -> HTTP 422 Unprocessable Entity: "
            "[{'loc': ['body', 'operations', 0, 'channelId'], 'msg': 'field required', 'type': 'missing'}]"
        )

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "bulk_commit_channels",
                {"operations": [{"type": "updateChannel"}]},
            )

        text = result[0][0].text
        assert "422" in text
        assert "operations" in text
        assert "channelId" in text


class TestECMClientHTTPErrorDetail:
    """ECMClient.post surfaces the response body's detail on HTTPStatusError (bd-mjtxn / GH #224)."""

    @pytest.mark.asyncio
    async def test_post_422_includes_detail(self):
        import httpx
        from ecm_client import ECMClient

        request = httpx.Request("POST", "http://ecm/api/channels/bulk-commit")
        response = httpx.Response(
            422,
            request=request,
            json={"detail": [{"loc": ["body", "operations", 0, "channelId"],
                              "msg": "field required", "type": "missing"}]},
        )

        async def fake_post(path, json=None, timeout=None):
            return response

        client = ECMClient()
        with patch("ecm_client._get_client") as get_client:
            get_client.return_value.post = AsyncMock(side_effect=fake_post)
            # httpx.Response.raise_for_status needs the request set (it is).
            with pytest.raises(RuntimeError) as exc_info:
                await client.post("/api/channels/bulk-commit", json_data={"operations": []})

        msg = str(exc_info.value)
        assert "422" in msg
        assert "channelId" in msg
        assert "/api/channels/bulk-commit" in msg


class TestCallEndpoint:
    """ECMClient.call_endpoint — contract enforcement + path formatting (bd-vtghg Phase 1)."""

    @pytest.mark.asyncio
    async def test_rejects_out_of_contract_body_key(self):
        """A body key not in the endpoint's request_fields raises ContractError before any HTTP call."""
        from ecm_client import ECMClient, ContractError
        from _endpoint_contracts import ENDPOINTS

        client = ECMClient()
        with patch("ecm_client._get_client") as get_client:
            get_client.return_value.post = AsyncMock()
            with pytest.raises(ContractError) as exc_info:
                # channels_create accepts channel_group_id, not group_id (GH #221).
                await client.call_endpoint(ENDPOINTS["channels_create"], body={"name": "X", "group_id": 7})
            get_client.return_value.post.assert_not_awaited()
        msg = str(exc_info.value)
        assert "group_id" in msg
        assert "channels_create" in msg

    @pytest.mark.asyncio
    async def test_rejects_missing_path_arg(self):
        """An unfilled {placeholder} raises ContractError naming the missing arg."""
        from ecm_client import ECMClient, ContractError
        from _endpoint_contracts import ENDPOINTS

        client = ECMClient()
        with patch("ecm_client._get_client"):
            with pytest.raises(ContractError) as exc_info:
                await client.call_endpoint(ENDPOINTS["channels_get"])  # missing channel_id
        assert "channel_id" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_formats_path_and_delegates_to_verb(self):
        """A valid call formats the path and delegates to the matching verb method."""
        from ecm_client import ECMClient
        from _endpoint_contracts import ENDPOINTS

        client = ECMClient()
        with patch.object(ECMClient, "post", new=AsyncMock(return_value={"ok": True})) as post_mock:
            result = await client.call_endpoint(
                ENDPOINTS["channels_add_stream"],
                path_args={"channel_id": 42},
                body={"stream_id": 7},
            )
        assert result == {"ok": True}
        post_mock.assert_awaited_once_with("/api/channels/42/add-stream", json_data={"stream_id": 7}, timeout=None)

    @pytest.mark.asyncio
    async def test_rejects_out_of_contract_query_key(self):
        """A query key not in query_params raises ContractError (GH #221 — group_id vs channel_group)."""
        from ecm_client import ECMClient, ContractError
        from _endpoint_contracts import ENDPOINTS

        client = ECMClient()
        with patch("ecm_client._get_client"):
            with pytest.raises(ContractError) as exc_info:
                await client.call_endpoint(ENDPOINTS["channels_list"], query={"group_id": 3})
        assert "group_id" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Phase 2: contract-registry migration of the other domains + verify-the-effect
# ---------------------------------------------------------------------------


class TestPhase2Migration:
    """The other domains now route through call_endpoint(ENDPOINTS[...])."""

    @pytest.mark.asyncio
    async def test_list_channel_groups_uses_endpoint(self):
        from tools.channel_groups import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)
        mock_client = _make_ecm_client_mock(call_endpoint=[{"id": 1, "name": "Sports", "channel_count": 3}])
        with patch("tools.channel_groups.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_channel_groups", {})
        assert "Sports" in result[0][0].text
        mock_client.call_endpoint.assert_awaited_once_with(ENDPOINTS["groups_list"])

    @pytest.mark.asyncio
    async def test_list_streams_sends_backend_query_names(self):
        """group -> channel_group_name, provider_id -> m3u_account (drift fixed in bd-vtghg)."""
        from tools.streams import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)
        mock_client = _make_ecm_client_mock(call_endpoint={"count": 0, "results": []})
        with patch("tools.streams.get_ecm_client", return_value=mock_client):
            await mcp.call_tool("list_streams", {"group": "News", "provider_id": 4, "search": "cnn"})
        call = mock_client.call_endpoint.call_args
        assert call.args[0] is ENDPOINTS["streams_list"]
        q = call.kwargs["query"]
        assert q.get("channel_group_name") == "News"
        assert q.get("m3u_account") == 4
        assert q.get("search") == "cnn"
        assert "group" not in q and "provider_id" not in q

    @pytest.mark.asyncio
    async def test_get_journal_sends_page_size_not_limit(self):
        """Backend /api/journal paginates via page_size (drift fixed in bd-vtghg)."""
        from tools.system import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)
        mock_client = _make_ecm_client_mock(call_endpoint={"entries": []})
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            await mcp.call_tool("get_journal", {"limit": 5, "category": "channels"})
        call = mock_client.call_endpoint.call_args
        assert call.args[0] is ENDPOINTS["journal_list"]
        q = call.kwargs["query"]
        assert q == {"page_size": 5, "category": "channels"}
        assert "limit" not in q


class TestVerifyTheEffect:
    """Mutating tools report the resulting state from the response, not the request."""

    @pytest.mark.asyncio
    async def test_update_channel_reports_response_fields(self):
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)
        # Request asks for group 7, but the backend response says group 99 —
        # the tool must surface what actually happened (99), not what we asked.
        mock_client = _make_ecm_client_mock(
            call_endpoint={"id": 1, "name": "ESPN HD", "channel_number": 42, "channel_group_id": 99}
        )
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("update_channel", {"channel_id": 1, "group_id": 7})
        text = result[0][0].text
        assert "ESPN HD" in text
        assert "group_id=99" in text

    @pytest.mark.asyncio
    async def test_delete_channel_group_warns_when_still_present(self):
        from tools.channel_groups import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)
        mock_client = AsyncMock()
        # delete returns nothing; the read-back still lists the group.
        mock_client.call_endpoint.side_effect = [None, [{"id": 5, "name": "Stale"}]]
        with patch("tools.channel_groups.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("delete_channel_group", {"group_id": 5})
        assert "WARNING" in result[0][0].text

    @pytest.mark.asyncio
    async def test_delete_channel_group_confirms_when_gone(self):
        from tools.channel_groups import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [None, [{"id": 99, "name": "Other"}]]
        with patch("tools.channel_groups.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("delete_channel_group", {"group_id": 5})
        assert "deleted" in result[0][0].text and "WARNING" not in result[0][0].text


class TestAddStream:
    """add_stream MCP tool — dedup_action enum branches (BD-P / bd-7u8ms, ADR-008 §D7)."""

    @pytest.mark.asyncio
    async def test_force_new_skips_candidates_and_creates_channel(self):
        """force_new skips /candidates entirely — creates channel + assigns stream."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        # call_endpoint call order:
        #   1st: channels_create → created channel
        #   2nd: streams_list → stream lookup
        #   3rd: channels_add_stream → stream assignment
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {"id": 42, "name": "ESPN HD", "channel_group_id": 7},  # channels_create
            {"results": [{"id": 101, "name": "ESPN HD"}], "count": 1},  # streams_list
            None,  # channels_add_stream
        ]

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "add_stream",
                {"stream_name": "ESPN HD", "group_id": 7, "dedup_action": "force_new"},
            )

        text = result[0][0].text
        assert "force_new" in text
        assert "ESPN HD" in text
        assert "id=42" in text
        assert "id=101" in text

        # Verify channels_create was called (first call) and NOT candidates.
        calls = mock_client.call_endpoint.call_args_list
        assert calls[0].args[0] is ENDPOINTS["channels_create"]
        # None of the calls should be channel_merges_candidates.
        called_endpoints = [c.args[0] for c in calls]
        assert ENDPOINTS["channel_merges_candidates"] not in called_endpoints

    @pytest.mark.asyncio
    async def test_prompt_no_candidate_creates_channel(self):
        """prompt with no dedup candidate falls through to normal channel creation."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        # call_endpoint order:
        #   1st: channel_merges_candidates → no candidates
        #   2nd: channels_create → created channel
        #   3rd: streams_list → stream lookup
        #   4th: channels_add_stream → stream assignment
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {"stream_name": "CNN HD", "candidates": [], "total": 0,
             "page": 1, "page_size": 50, "total_pages": 0},  # candidates → empty
            {"id": 55, "name": "CNN HD", "channel_group_id": 3},  # channels_create
            {"results": [{"id": 202, "name": "CNN HD"}], "count": 1},  # streams_list
            None,  # channels_add_stream
        ]

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "add_stream",
                {"stream_name": "CNN HD", "group_id": 3},  # dedup_action defaults to 'prompt'
            )

        text = result[0][0].text
        assert "CNN HD" in text
        assert "id=55" in text
        assert "id=202" in text

        calls = mock_client.call_endpoint.call_args_list
        assert calls[0].args[0] is ENDPOINTS["channel_merges_candidates"]
        assert calls[1].args[0] is ENDPOINTS["channels_create"]

    @pytest.mark.asyncio
    async def test_prompt_with_candidate_returns_pending_merge_response(self):
        """prompt with a dedup candidate returns structured candidate info to the agent."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {
                "stream_name": "ESPN",
                "candidates": [
                    {"channel_id": "uuid-abc", "channel_name": "ESPN HD", "confidence": 0.92}
                ],
                "total": 1, "page": 1, "page_size": 50, "total_pages": 1,
            },
        ]

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "add_stream",
                {"stream_name": "ESPN", "group_id": 5, "dedup_action": "prompt"},
            )

        text = result[0][0].text
        assert "pending_merge" in text
        assert "ESPN HD" in text
        assert "uuid-abc" in text
        # Confidence present in response
        assert "92%" in text or "0.92" in text or "92" in text

        # Only one backend call: candidates lookup — no channel create.
        mock_client.call_endpoint.assert_awaited_once()
        call = mock_client.call_endpoint.call_args
        assert call.args[0] is ENDPOINTS["channel_merges_candidates"]
        assert call.kwargs["query"]["stream_name"] == "ESPN"
        assert call.kwargs["query"]["group_id"] == 5

    @pytest.mark.asyncio
    async def test_merge_if_found_with_candidate_adds_stream_to_existing_channel(self):
        """merge_if_found with candidate adds stream to the candidate channel directly."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        # call_endpoint order:
        #   1st: channel_merges_candidates → candidate found
        #   2nd: streams_list → stream id resolution
        #   3rd: channels_add_stream → add stream to candidate channel
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {
                "stream_name": "FOX",
                "candidates": [
                    {"channel_id": "uuid-fox", "channel_name": "FOX Network", "confidence": 0.85}
                ],
                "total": 1, "page": 1, "page_size": 50, "total_pages": 1,
            },
            {"results": [{"id": 303, "name": "FOX"}], "count": 1},  # streams_list
            None,  # channels_add_stream
        ]

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "add_stream",
                {"stream_name": "FOX", "group_id": 2, "dedup_action": "merge_if_found"},
            )

        text = result[0][0].text
        assert "merge_if_found" in text
        assert "FOX Network" in text or "uuid-fox" in text
        assert "303" in text  # stream id

        calls = mock_client.call_endpoint.call_args_list
        assert calls[0].args[0] is ENDPOINTS["channel_merges_candidates"]
        assert calls[1].args[0] is ENDPOINTS["streams_list"]
        # The add-stream call uses channels_add_stream (not channels_create).
        assert calls[2].args[0] is ENDPOINTS["channels_add_stream"]
        assert calls[2].kwargs["path_args"]["channel_id"] == "uuid-fox"
        assert calls[2].kwargs["body"]["stream_id"] == 303

    @pytest.mark.asyncio
    async def test_invalid_dedup_action_returns_error(self):
        """An unrecognized dedup_action value is rejected with an error message."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "add_stream",
                {"stream_name": "X", "group_id": 1, "dedup_action": "not_a_real_mode"},
            )

        text = result[0][0].text
        assert "Invalid" in text or "invalid" in text
        assert "not_a_real_mode" in text
        mock_client.call_endpoint.assert_not_awaited()
