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
        """Returns formatted M3U account list with stream count derived via streams_list."""
        from tools.m3u import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        # stream_count is NOT in the Dispatcharr account payload; the tool
        # derives it by calling streams_list with m3u_account=<id>&page_size=1.
        # Use side_effect to return the right value per endpoint call.
        async def call_endpoint_side_effect(endpoint, **kwargs):
            if endpoint.name == "m3u_list_providers":
                return [
                    {"id": 1, "name": "Provider A", "server_url": "http://provider-a.example.com/m3u", "status": "active"},
                    {"id": 2, "name": "Provider B", "server_url": "http://provider-b.example.com/m3u", "status": "error"},
                ]
            if endpoint.name == "streams_list":
                query = kwargs.get("query", {})
                m3u_id = query.get("m3u_account")
                if m3u_id == 1:
                    return {"count": 5000, "results": []}
                if m3u_id == 2:
                    return {"count": 3000, "results": []}
            return {}

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = call_endpoint_side_effect

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

        # Verify channels_create was called (first call) and NOT enqueue.
        calls = mock_client.call_endpoint.call_args_list
        assert calls[0].args[0] is ENDPOINTS["channels_create"]
        # None of the calls should be the dedup enqueue.
        called_endpoints = [c.args[0] for c in calls]
        assert ENDPOINTS["channel_merges_enqueue"] not in called_endpoints

    @pytest.mark.asyncio
    async def test_prompt_no_candidate_creates_channel(self):
        """prompt with no dedup candidate falls through to normal channel creation."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        # call_endpoint order:
        #   1st: channel_merges_enqueue → no candidate (merge_id None)
        #   2nd: channels_create → created channel
        #   3rd: streams_list → stream lookup
        #   4th: channels_add_stream → stream assignment
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {"merge_id": None, "created": False},  # enqueue → no candidate
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
        assert calls[0].args[0] is ENDPOINTS["channel_merges_enqueue"]
        assert calls[1].args[0] is ENDPOINTS["channels_create"]

    @pytest.mark.asyncio
    async def test_prompt_with_candidate_returns_merge_id_and_accept_dismiss(self):
        """prompt with a candidate enqueues and returns merge_id + accept/dismiss guidance."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {
                "merge_id": 77,
                "created": True,
                "candidate_channel_id": "uuid-abc",
                "candidate_channel_name": "ESPN HD",
                "confidence": 0.92,
                "meets_threshold": True,
                "status": "pending",
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
        # merge_id surfaced and accept/dismiss referenced (NOT old re-call text).
        assert "merge_id: 77" in text
        assert "accept_channel_merge(77)" in text
        assert "dismiss_channel_merge(77)" in text
        assert "merge_if_found') to add" not in text  # old guidance gone
        # Confidence present.
        assert "92%" in text or "0.92" in text or "92" in text

        # Only one backend call: the enqueue POST — no channel create.
        mock_client.call_endpoint.assert_awaited_once()
        call = mock_client.call_endpoint.call_args
        assert call.args[0] is ENDPOINTS["channel_merges_enqueue"]
        assert call.kwargs["body"]["stream_name"] == "ESPN"
        assert call.kwargs["body"]["group_id"] == 5

    @pytest.mark.asyncio
    async def test_merge_if_found_exact_match_auto_accepts(self):
        """merge_if_found auto-accepts ONLY on an exact name match (confidence 1.0)."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        # call_endpoint order:
        #   1st: channel_merges_enqueue → exact-match candidate (merge_id 88)
        #   2nd: channel_merges_accept → auto-accept the queued merge
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {
                "merge_id": 88,
                "created": True,
                "candidate_channel_id": "uuid-fox",
                "candidate_channel_name": "FOX Network",
                "confidence": 1.0,  # exact normalized-name match
                "meets_threshold": True,
                "status": "pending",
            },
            {
                "merged_into_channel_id": "uuid-fox",
                "journal_entry_id": 5,
                "source_stream_id": "303",
                "confidence": 1.0,
                "status": "merged",
            },
        ]

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "add_stream",
                {"stream_name": "FOX Network", "group_id": 2, "dedup_action": "merge_if_found"},
            )

        text = result[0][0].text
        assert "merge_if_found" in text
        assert "FOX Network" in text or "uuid-fox" in text
        assert "merge_id=88" in text
        # Did NOT fall back to prompt — the row was auto-accepted.
        assert "pending_merge" not in text

        calls = mock_client.call_endpoint.call_args_list
        assert calls[0].args[0] is ENDPOINTS["channel_merges_enqueue"]
        # Auto-accept goes through accept_channel_merge (not direct add-stream).
        assert calls[1].args[0] is ENDPOINTS["channel_merges_accept"]
        assert calls[1].kwargs["path_args"]["merge_id"] == 88

    @pytest.mark.asyncio
    async def test_merge_if_found_fuzzy_above_threshold_does_not_auto_accept(self):
        """merge_if_found does NOT auto-accept a fuzzy candidate even above threshold.

        Regression for lq38l.7: a 90% token-set over-match ('US: ESPN 2' vs
        'US: ESPN U') met the operator threshold and was wrongly auto-merged.
        Under the exact-match-only rule, any confidence < 1.0 — even with
        meets_threshold True — must fall back to prompt semantics so the
        operator/agent confirms via accept/dismiss before the stream attaches.
        """
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {
                "merge_id": 2,
                "created": True,
                "candidate_channel_id": "12341",
                "candidate_channel_name": "US: ESPN U",
                "confidence": 0.90,  # fuzzy over-match — NOT exact
                "meets_threshold": True,  # above the operator threshold...
                "status": "pending",
            },
        ]

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "add_stream",
                {"stream_name": "US: ESPN 2", "group_id": 1351, "dedup_action": "merge_if_found"},
            )

        text = result[0][0].text
        # ...yet it falls back to prompt — no auto-accept.
        assert "pending_merge" in text
        assert "merge_id: 2" in text
        assert "accept_channel_merge(2)" in text
        assert "dismiss_channel_merge(2)" in text
        # Only the enqueue call — accept was NOT invoked for a non-exact match.
        mock_client.call_endpoint.assert_awaited_once()
        called_endpoints = [c.args[0] for c in mock_client.call_endpoint.call_args_list]
        assert ENDPOINTS["channel_merges_enqueue"] in called_endpoints
        assert ENDPOINTS["channel_merges_accept"] not in called_endpoints

    @pytest.mark.asyncio
    async def test_merge_if_found_below_threshold_enqueues_and_prompts(self):
        """merge_if_found below threshold falls back to prompt — queues + returns merge_id."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        # The enqueue returns a candidate above the floor but below the
        # auto-merge threshold → not exact → prompt fallback.
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {
                "merge_id": 99,
                "created": True,
                "candidate_channel_id": "uuid-low",
                "candidate_channel_name": "ESPN Sports",
                "confidence": 0.73,
                "meets_threshold": False,
                "status": "pending",
            },
        ]

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "add_stream",
                {"stream_name": "ESPN HD", "group_id": 2, "dedup_action": "merge_if_found"},
            )

        text = result[0][0].text
        assert "pending_merge" in text
        assert "merge_id: 99" in text
        assert "accept_channel_merge(99)" in text
        assert "dismiss_channel_merge(99)" in text
        # Only the enqueue call — no auto-accept below threshold.
        mock_client.call_endpoint.assert_awaited_once()
        assert mock_client.call_endpoint.call_args.args[0] is ENDPOINTS["channel_merges_enqueue"]

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

    @pytest.mark.asyncio
    async def test_enqueue_failure_returns_structured_error(self):
        """A failed enqueue surfaces a structured error (no silent drop / throw)."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = RuntimeError(
            "POST /api/channel-merges -> HTTP 500 boom"
        )
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "add_stream",
                {"stream_name": "ESPN", "group_id": 5, "dedup_action": "prompt"},
            )
        text = result[0][0].text
        assert "action=error" in text
        # Agent is told it can fall back to force_new.
        assert "force_new" in text


class TestChannelMergesEnqueueContract:
    """The new POST /api/channel-merges enqueue endpoint contract (bd-b3czq)."""

    def test_enqueue_endpoint_registered_with_expected_contract(self):
        """channel_merges_enqueue exists with the §D7 method/path/fields."""
        from _endpoint_contracts import ENDPOINTS

        ep = ENDPOINTS["channel_merges_enqueue"]
        assert ep.method == "POST"
        assert ep.path == "/api/channel-merges"
        # Tool sends only the stream context — never a confidence.
        assert ep.request_fields == frozenset({"stream_name", "group_id"})
        assert "confidence" not in ep.request_fields
        # Response carries the merge_id + candidate + meets_threshold signal
        # the tool reads to drive prompt vs auto-accept.
        assert {
            "merge_id",
            "candidate_channel_id",
            "confidence",
            "meets_threshold",
        }.issubset(ep.response_fields)


# --- TestGetOrphanedGroupsEnvelopeUnwrap (A1) ---
class TestGetOrphanedGroupsEnvelopeUnwrap:
    """get_orphaned_groups must unwrap {"orphaned_groups": [...]} (bd-1wq7z.4)."""

    @pytest.mark.asyncio
    async def test_unwraps_orphaned_groups_envelope(self):
        """Backend returns {"orphaned_groups": [...], ...}; tool must iterate the list."""
        from tools.channel_groups import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "orphaned_groups": [
                {"id": 10, "name": "Dead Group A", "reason": "No streams, channels, or M3U association"},
                {"id": 11, "name": "Dead Group B", "reason": "No streams, channels, or M3U association"},
            ],
            "total_groups": 50,
            "groups_with_content": 48,
        })

        with patch("tools.channel_groups.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_orphaned_groups", {})

        text = result[0][0].text
        assert "2 orphaned" in text
        assert "Dead Group A" in text
        assert "Dead Group B" in text
        assert "id=10" in text
        # No AttributeError leaked through as an error string.
        assert "has no attribute" not in text

    @pytest.mark.asyncio
    async def test_empty_orphaned_groups_envelope(self):
        """{"orphaned_groups": []} → friendly 'none found' message."""
        from tools.channel_groups import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "orphaned_groups": [],
            "total_groups": 10,
            "groups_with_content": 10,
        })

        with patch("tools.channel_groups.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_orphaned_groups", {})

        assert "No orphaned" in result[0][0].text


# --- TestGetAutoCreatedGroupsEnvelopeUnwrap (A1) ---
class TestGetAutoCreatedGroupsEnvelopeUnwrap:
    """get_auto_created_groups must unwrap {"groups": [...]} (bd-1wq7z.4)."""

    @pytest.mark.asyncio
    async def test_unwraps_groups_envelope(self):
        """Backend returns {"groups": [...], "total_auto_created_channels": N}."""
        from tools.channel_groups import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "groups": [
                {"id": 20, "name": "Sports AC", "auto_created_count": 5},
                {"id": 21, "name": "News AC", "auto_created_count": 3},
            ],
            "total_auto_created_channels": 8,
        })

        with patch("tools.channel_groups.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_auto_created_groups", {})

        text = result[0][0].text
        assert "2 auto-created" in text
        assert "Sports AC" in text
        assert "News AC" in text
        assert "id=20" in text
        assert "has no attribute" not in text

    @pytest.mark.asyncio
    async def test_empty_auto_created_groups_envelope(self):
        """{"groups": []} → friendly 'none' message."""
        from tools.channel_groups import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "groups": [],
            "total_auto_created_channels": 0,
        })

        with patch("tools.channel_groups.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_auto_created_groups", {})

        assert "No auto-created" in result[0][0].text


# --- TestGetGroupsWithStreamsEnvelopeUnwrap (A1) ---
class TestGetGroupsWithStreamsEnvelopeUnwrap:
    """get_groups_with_streams must unwrap {"groups": [...]} (bd-1wq7z.4)."""

    @pytest.mark.asyncio
    async def test_unwraps_groups_envelope(self):
        """Backend returns {"groups": [...], "total_groups": N}."""
        from tools.channel_groups import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "groups": [
                {"id": 30, "name": "Entertainment", "stream_count": 12},
                {"id": 31, "name": "Sports", "stream_count": 8},
            ],
            "total_groups": 15,
        })

        with patch("tools.channel_groups.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_groups_with_streams", {})

        text = result[0][0].text
        assert "2 groups" in text
        assert "Entertainment" in text
        assert "Sports" in text
        assert "id=30" in text
        assert "has no attribute" not in text

    @pytest.mark.asyncio
    async def test_empty_groups_with_streams_envelope(self):
        """{"groups": []} → friendly 'none found' message."""
        from tools.channel_groups import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "groups": [],
            "total_groups": 0,
        })

        with patch("tools.channel_groups.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_groups_with_streams", {})

        assert "No channel groups" in result[0][0].text


# --- TestListTasksEnvelopeUnwrap (A1) ---
class TestListTasksEnvelopeUnwrap:
    """list_tasks must unwrap {"tasks": [...]} (bd-1wq7z.4)."""

    @pytest.mark.asyncio
    async def test_unwraps_tasks_envelope(self):
        """Backend returns {"tasks": [...]}; tool must iterate the list, not dict keys.
        Backend uses task_name (not name) for the human-readable label (lq38l.1).
        """
        from tools.tasks import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "tasks": [
                {"task_id": "m3u_refresh", "task_name": "M3U Refresh", "enabled": True, "status": "idle", "last_run": "2026-05-22"},
                {"task_id": "stream_probe", "task_name": "Stream Probe", "enabled": False, "status": "idle", "last_run": "never"},
            ]
        })

        with patch("tools.tasks.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_tasks", {})

        text = result[0][0].text
        assert "2 tasks" in text
        assert "M3U Refresh" in text
        assert "Stream Probe" in text
        assert "m3u_refresh" in text
        assert "Unknown" not in text
        assert "has no attribute" not in text

    @pytest.mark.asyncio
    async def test_empty_tasks_envelope(self):
        """{"tasks": []} → friendly 'no tasks' message."""
        from tools.tasks import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={"tasks": []})

        with patch("tools.tasks.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_tasks", {})

        assert "No tasks" in result[0][0].text


class TestListTasksTaskNameRendering:
    """list_tasks reads task_name (not name) from backend payload (lq38l.1)."""

    @pytest.mark.asyncio
    async def test_task_name_field_rendered(self):
        """Backend payload carries task_name; tool must render it, not 'Unknown'."""
        from tools.tasks import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "tasks": [
                {
                    "task_id": "epg_refresh",
                    "task_name": "EPG Refresh",
                    "enabled": True,
                    "status": "idle",
                    "last_run": "never",
                },
                {
                    "task_id": "popularity_calculation",
                    "task_name": "Stats v2 Rollup & Prune",
                    "enabled": True,
                    "status": "running",
                    "last_run": "2026-05-22T10:00:00Z",
                },
            ]
        })

        with patch("tools.tasks.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_tasks", {})

        text = result[0][0].text
        assert "EPG Refresh" in text, "task_name must be rendered"
        assert "Stats v2 Rollup & Prune" in text, "task_name must be rendered"
        assert "Unknown" not in text, "Unknown must not appear when task_name is present"
        assert "id=epg_refresh" in text
        assert "id=popularity_calculation" in text

    @pytest.mark.asyncio
    async def test_fallback_to_prettified_task_id_when_no_task_name(self):
        """When task_name is absent, tool prettifies task_id rather than 'Unknown'."""
        from tools.tasks import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "tasks": [
                {
                    "task_id": "stream_probe",
                    "enabled": True,
                    "status": "idle",
                    "last_run": "never",
                    # no task_name key at all
                },
            ]
        })

        with patch("tools.tasks.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_tasks", {})

        text = result[0][0].text
        assert "Unknown" not in text, "Unknown must not appear; prettified task_id used instead"
        assert "stream_probe" in text  # task_id still in output as id=...


# --- TestDeleteOrphanedGroupsResultKeys (A1) ---
class TestDeleteOrphanedGroupsResultKeys:
    """delete_orphaned_groups must read deleted_groups/failed_groups (bd-1wq7z.5)."""

    @pytest.mark.asyncio
    async def test_reports_deleted_groups_correctly(self):
        """Reads deleted_groups (not deleted/groups) from the real backend shape."""
        from tools.channel_groups import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "status": "ok",
            "message": "Deleted 2 orphaned channel groups",
            "deleted_groups": [
                {"id": 10, "name": "Dead Group A"},
                {"id": 11, "name": "Dead Group B"},
            ],
            "failed_groups": [],
        })

        with patch("tools.channel_groups.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("delete_orphaned_groups", {})

        text = result[0][0].text
        # Must report the 2 deleted groups, not "No orphaned groups deleted"
        assert "No orphaned groups" not in text
        assert "2" in text
        assert "Dead Group A" in text
        assert "Dead Group B" in text

    @pytest.mark.asyncio
    async def test_reports_none_deleted_when_empty(self):
        """{"deleted_groups": []} → friendly 'nothing deleted' message."""
        from tools.channel_groups import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "status": "ok",
            "message": "No orphaned channel groups found",
            "deleted_groups": [],
            "failed_groups": [],
        })

        with patch("tools.channel_groups.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("delete_orphaned_groups", {})

        text = result[0][0].text
        assert "No orphaned" in text

    @pytest.mark.asyncio
    async def test_reports_failed_groups(self):
        """failed_groups list is surfaced to the operator."""
        from tools.channel_groups import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "status": "ok",
            "message": "Deleted 1 orphaned channel groups",
            "deleted_groups": [{"id": 10, "name": "OK Group"}],
            "failed_groups": [{"id": 11, "name": "Fail Group", "error": "Dispatcharr refused"}],
        })

        with patch("tools.channel_groups.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("delete_orphaned_groups", {})

        text = result[0][0].text
        assert "OK Group" in text
        assert "Fail Group" in text


# --- TestCancelTaskStatusAware (A1) ---
class TestCancelTaskStatusAware:
    """cancel_task must read status field and report truthfully (bd-1wq7z.12)."""

    @pytest.mark.asyncio
    async def test_cancelled_when_running(self):
        """status='cancelled' → reports task was cancelled."""
        from tools.tasks import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "status": "cancelled",
            "message": "Task m3u_refresh cancelled",
            "task_id": "m3u_refresh",
        })

        with patch("tools.tasks.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("cancel_task", {"task_id": "m3u_refresh"})

        text = result[0][0].text
        assert "cancel" in text.lower()
        assert "m3u_refresh" in text
        assert "not running" not in text.lower()

    @pytest.mark.asyncio
    async def test_not_running_when_idle(self):
        """status='not_running' → reports task was not running (not 'cancelled')."""
        from tools.tasks import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "status": "not_running",
            "message": "Task stream_probe is not currently running",
            "task_id": "stream_probe",
        })

        with patch("tools.tasks.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("cancel_task", {"task_id": "stream_probe"})

        text = result[0][0].text
        assert "not running" in text.lower() or "not_running" in text
        assert "stream_probe" in text
        # Must NOT claim it was cancelled.
        assert "cancelled" not in text.lower()


# --- TestListEpgSources (A2) ---
class TestListEpgSources:
    """Tests for list_epg_sources — bd-1wq7z.10 (null url crash)."""

    @pytest.mark.asyncio
    async def test_source_with_none_url_does_not_crash(self):
        """A source with url=None (present but None) must not raise TypeError."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        # 4 of 8 real sources have url present-but-None
        sources = [
            {"id": 1, "name": "Sky UK", "url": None, "channel_count": 120},
            {"id": 2, "name": "BBC EPG", "url": "http://epg.example.com/bbc.xml", "channel_count": 30},
        ]
        mock_client = _make_ecm_client_mock(call_endpoint=sources)

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_epg_sources", {})

        text = result[0][0].text
        # Must not crash — we should see both sources listed
        assert "Sky UK" in text
        assert "BBC EPG" in text
        # The None-url source renders a blank/empty url (not a crash)
        assert "TypeError" not in text
        assert "NoneType" not in text

    @pytest.mark.asyncio
    async def test_source_with_missing_url_does_not_crash(self):
        """A source with url key absent also renders without crash."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        sources = [{"id": 3, "name": "No URL Source", "channel_count": 0}]
        mock_client = _make_ecm_client_mock(call_endpoint=sources)

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_epg_sources", {})

        text = result[0][0].text
        assert "No URL Source" in text

    @pytest.mark.asyncio
    async def test_empty_sources(self):
        """Returns 'no EPG sources' when list is empty."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint=[])

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_epg_sources", {})

        assert "No EPG sources" in result[0][0].text


# --- TestCreateEpgSource (A2) ---
class TestCreateEpgSource:
    """Tests for create_epg_source — bd-1wq7z.9 (missing source_type)."""

    @pytest.mark.asyncio
    async def test_sends_source_type_in_body(self):
        """Tool must include source_type in the request body (defaults to 'xmltv')."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        created = {"id": 42, "name": "My EPG", "url": "http://example.com/epg.xml", "source_type": "xmltv"}
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = created

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("create_epg_source", {"name": "My EPG", "url": "http://example.com/epg.xml"})

        text = result[0][0].text
        assert "created" in text.lower() or "id=42" in text

        # Verify source_type was sent in the body
        call_kwargs = mock_client.call_endpoint.call_args
        body = call_kwargs.kwargs.get("body") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else {})
        # body is passed as keyword arg
        body = mock_client.call_endpoint.call_args.kwargs.get("body", {})
        assert body.get("source_type") == "xmltv"

    @pytest.mark.asyncio
    async def test_source_type_param_override(self):
        """Caller can override source_type (e.g., 'iptv')."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        created = {"id": 7, "name": "IPTV EPG", "url": "http://example.com/iptv.xml", "source_type": "iptv"}
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = created

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("create_epg_source", {
                "name": "IPTV EPG",
                "url": "http://example.com/iptv.xml",
                "source_type": "iptv",
            })

        body = mock_client.call_endpoint.call_args.kwargs.get("body", {})
        assert body.get("source_type") == "iptv"

    @pytest.mark.asyncio
    async def test_backend_error_surfaces_cleanly(self):
        """When backend returns an error, tool returns a readable error message."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = Exception("HTTP 400: source_type is required")

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("create_epg_source", {"name": "Bad", "url": "http://x.com/e.xml"})

        text = result[0][0].text
        assert "Error" in text
        assert "400" in text or "source_type" in text


# --- TestMatchChannelsEpg (A2) ---
class TestMatchChannelsEpg:
    """Tests for match_channels_epg — bd-1wq7z.5 (no body + wrong result keys)."""

    @pytest.mark.asyncio
    async def test_sends_request_body(self):
        """Tool must send a body (even if empty-defaults) — not a bodyless POST."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        # Real shape: exact/multiple/none/summary
        response = {
            "exact": [],
            "multiple": [{"channel_id": 1}] * 151,
            "none": [{"channel_id": 2}] * 420,
            "summary": {
                "total_channels": 571,
                "exact_count": 0,
                "multiple_count": 151,
                "none_count": 420,
                "match_time_ms": 1200.5,
            },
        }
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = response

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("match_channels_epg", {})

        # Verify call_endpoint was called with a body kwarg (not omitted)
        call_kwargs = mock_client.call_endpoint.call_args.kwargs
        assert "body" in call_kwargs, "Tool must pass body= to call_endpoint"

    @pytest.mark.asyncio
    async def test_reports_real_response_keys(self):
        """Tool must parse exact/multiple/none/summary — not matched/unmatched."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        response = {
            "exact": [],
            "multiple": [{"channel_id": i} for i in range(151)],
            "none": [{"channel_id": i + 200} for i in range(420)],
            "summary": {
                "total_channels": 571,
                "exact_count": 0,
                "multiple_count": 151,
                "none_count": 420,
                "match_time_ms": 1200.5,
            },
        }
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = response

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("match_channels_epg", {})

        text = result[0][0].text
        # Must report the real counts — NOT the wrong "matched/unmatched" keys
        assert "571" in text or "total" in text.lower()
        assert "151" in text  # multiple_count
        assert "420" in text  # none_count
        # Verify it doesn't silently report 0/0 from missing keys
        assert "0 channels matched, 0 unmatched" not in text

    @pytest.mark.asyncio
    async def test_exact_match_count_reported(self):
        """Exact count is surfaced when non-zero."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        response = {
            "exact": [{"channel_id": 1}, {"channel_id": 2}],
            "multiple": [],
            "none": [],
            "summary": {
                "total_channels": 2,
                "exact_count": 2,
                "multiple_count": 0,
                "none_count": 0,
                "match_time_ms": 50.0,
            },
        }
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = response

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("match_channels_epg", {})

        text = result[0][0].text
        assert "2" in text  # exact_count

    @pytest.mark.asyncio
    async def test_api_error_returns_message(self):
        """Returns error message on API failure."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = Exception("EPG match timed out")

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("match_channels_epg", {})

        text = result[0][0].text
        assert "Error" in text

    @pytest.mark.asyncio
    async def test_multiple_candidates_surfaced_in_output(self):
        """Channels with status=multiple show candidate tvg_ids, names, and confidence (bd-1icn8)."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        response = {
            "exact": [{"channel_id": 10, "channel_name": "ESPN", "matches": [
                {"tvg_id": "ESPN.us", "epg_name": "ESPN", "confidence": 1.0, "match_type": "exact"},
            ]}],
            "multiple": [
                {
                    "channel_id": 20,
                    "channel_name": "Fox News",
                    "status": "multiple",
                    "best_score": 0.92,
                    "matches": [
                        {"tvg_id": "FoxNews.us", "epg_name": "Fox News Channel", "confidence": 92.0, "match_type": "fuzzy"},
                        {"tvg_id": "FoxNews.int", "epg_name": "Fox News Int", "confidence": 85.0, "match_type": "fuzzy"},
                    ],
                },
                {
                    "channel_id": 21,
                    "channel_name": "Discovery",
                    "status": "multiple",
                    "best_score": 0.80,
                    "matches": [
                        {"tvg_id": "Discovery.us", "epg_name": "Discovery Channel", "confidence": 80.0, "match_type": "fuzzy"},
                    ],
                },
            ],
            "none": [],
            "summary": {
                "total_channels": 3,
                "exact_count": 1,
                "multiple_count": 2,
                "none_count": 0,
                "match_time_ms": 75.0,
            },
        }
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = response

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("match_channels_epg", {})

        text = result[0][0].text

        # Summary line still present
        assert "3" in text
        assert "1" in text   # exact_count
        assert "2" in text   # multiple_count

        # Channel names for ambiguous channels listed
        assert "Fox News" in text
        assert "Discovery" in text

        # Candidate tvg_ids surfaced
        assert "FoxNews.us" in text
        assert "FoxNews.int" in text
        assert "Discovery.us" in text

        # Candidate EPG names surfaced
        assert "Fox News Channel" in text
        assert "Discovery Channel" in text

        # Confidence values rendered (92%, 85%, 80%)
        assert "92%" in text
        assert "85%" in text
        assert "80%" in text

    @pytest.mark.asyncio
    async def test_multiple_candidates_missing_matches_key_handled(self):
        """Defensive: if a multiple entry has no 'matches' key, renders graceful fallback (bd-1icn8)."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        response = {
            "exact": [],
            "multiple": [
                # Entry missing 'matches' key entirely — defensive handling
                {"channel_id": 30, "channel_name": "CNN"},
            ],
            "none": [],
            "summary": {
                "total_channels": 1,
                "exact_count": 0,
                "multiple_count": 1,
                "none_count": 0,
                "match_time_ms": 10.0,
            },
        }
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = response

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("match_channels_epg", {})

        text = result[0][0].text
        # Must not raise; channel name must appear; fallback message present
        assert "CNN" in text
        assert "no candidate details available" in text


# --- TestLinkChannelEpg (znc76.2 / r5pu5) ---
class TestLinkChannelEpg:
    """link_channel_epg picks a 'multiple' candidate and links it to a channel.

    Closes the Scenario 6 seam: match_channels_epg lists candidates, but only
    this tool establishes the channel's epg_data link so set_logo_from_epg works.
    """

    @pytest.mark.asyncio
    async def test_links_by_tvg_id_calls_endpoint(self):
        """Resolves channel + tvg_id through the link endpoint with path_args+body."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "id": 20, "name": "Fox News", "epg_data_id": 555,
        }

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "link_channel_epg", {"channel_id": 20, "tvg_id": "FoxNews.us"},
            )

        # Calls the registered link endpoint with the channel in path_args and
        # the chosen tvg_id in the body.
        call = mock_client.call_endpoint.call_args
        assert call.args[0] is ENDPOINTS["epg_link_channel"]
        assert call.kwargs["path_args"] == {"channel_id": 20}
        assert call.kwargs["body"] == {"tvg_id": "FoxNews.us"}

        text = result[0][0].text
        assert "Fox News" in text
        assert "555" in text  # linked epg_data_id surfaced
        assert "set_logo_from_epg" in text

    @pytest.mark.asyncio
    async def test_links_by_epg_data_id(self):
        """epg_data_id is forwarded in the body when provided."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {"id": 7, "epg_data_id": 42}

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "link_channel_epg", {"channel_id": 7, "epg_data_id": 42},
            )

        body = mock_client.call_endpoint.call_args.kwargs["body"]
        assert body == {"epg_data_id": 42}
        assert "42" in result[0][0].text

    @pytest.mark.asyncio
    async def test_requires_a_candidate(self):
        """Neither tvg_id nor epg_data_id -> guidance, no backend call."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("link_channel_epg", {"channel_id": 7})

        text = result[0][0].text
        assert "tvg_id" in text and "epg_data_id" in text
        mock_client.call_endpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_error_returns_message(self):
        """Backend error surfaces as a readable message."""
        from tools.epg import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = Exception("HTTP 404: channel not found")

        with patch("tools.epg.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "link_channel_epg", {"channel_id": 999, "tvg_id": "X.us"},
            )

        text = result[0][0].text
        assert "Error" in text
        assert "404" in text


# --- TestEpgContractRegistry (A2) ---
class TestEpgContractRegistry:
    """Verify endpoint contracts are correct for the EPG domain after fixes."""

    def test_epg_link_channel_registered(self):
        """epg_link_channel endpoint is registered with correct method/path/fields."""
        from _endpoint_contracts import ENDPOINTS

        ep = ENDPOINTS["epg_link_channel"]
        assert ep.method == "POST"
        assert ep.path == "/api/epg/channels/{channel_id}/link"
        assert "epg_data_id" in ep.request_fields
        assert "tvg_id" in ep.request_fields
        assert ep.response_is_list is False

    def test_epg_create_source_includes_source_type(self):
        """epg_create_source contract must declare source_type as a request field."""
        from _endpoint_contracts import ENDPOINTS

        ep = ENDPOINTS["epg_create_source"]
        assert "source_type" in ep.request_fields, (
            "epg_create_source contract must include source_type — "
            "Dispatcharr requires it and we now send it"
        )

    def test_epg_match_includes_required_fields(self):
        """epg_match contract declares channel_ids, epg_source_ids, source_order."""
        from _endpoint_contracts import ENDPOINTS

        ep = ENDPOINTS["epg_match"]
        assert "channel_ids" in ep.request_fields
        assert "epg_source_ids" in ep.request_fields
        assert "source_order" in ep.request_fields


# --- TestRollbackAutoCreation (A3) ---
class TestRollbackAutoCreation:
    """rollback_auto_creation reads the right fields from the engine response (bd-1wq7z.5)."""

    @pytest.mark.asyncio
    async def test_reports_entities_removed(self):
        """Backend returns entities_removed/entities_restored; tool must not report 0."""
        from tools.auto_creation import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        # Engine response shape — neither "deleted" nor "channels_deleted" exists.
        mock_client = _make_ecm_client_mock(call_endpoint={
            "success": True,
            "execution_id": 7,
            "rule_name": "Sports Rule",
            "entities_removed": 45,
            "entities_restored": 3,
        })

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("rollback_auto_creation", {"execution_id": 7})

        text = result[0][0].text
        # Must report the real removed count, not zero.
        assert "45" in text
        assert "0 channels deleted" not in text

    @pytest.mark.asyncio
    async def test_reports_entities_restored(self):
        """Restored count is included in the output."""
        from tools.auto_creation import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "success": True,
            "execution_id": 8,
            "rule_name": "News Rule",
            "entities_removed": 10,
            "entities_restored": 2,
        })

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("rollback_auto_creation", {"execution_id": 8})

        text = result[0][0].text
        assert "10" in text
        assert "2" in text


# --- TestRunAutoCreation (A3) ---
class TestRunAutoCreation:
    """run_auto_creation polls for the final result instead of treating 202 as complete (bd-1wq7z.8)."""

    @pytest.mark.asyncio
    async def test_polls_and_reports_final_result_dry_run(self):
        """202 kick-off → poll executions/{id} → report real counts for dry run."""
        from tools.auto_creation import register
        from mcp.server.fastmcp import FastMCP
        from unittest.mock import AsyncMock, call as mock_call

        mcp = FastMCP("test")
        register(mcp)

        # First call_endpoint → 202 acceptance envelope (ac_run).
        # Second call_endpoint → completed execution (ac_get_execution).
        kickoff_response = {
            "execution_id": 42,
            "status": "running",
            "message": "Pipeline started",
        }
        final_response = {
            "id": 42,
            "status": "completed",
            "mode": "dry_run",
            "streams_evaluated": 2679,
            "streams_matched": 45,
            "channels_created": 45,
            "channels_updated": 0,
            "groups_created": 0,
            "streams_skipped": 0,
            "duration_seconds": 288.4,
            "dry_run_results": [],
        }

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [kickoff_response, final_response]

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("run_auto_creation", {"dry_run": True})

        text = result[0][0].text
        # Must report real evaluated count (2679), not 0.
        assert "2679" in text
        # Must indicate dry-run mode.
        assert "dry" in text.lower() or "Dry" in text
        # Must NOT report fake zero-duration.
        assert "Duration: 0.0s" not in text
        # Two calls were made: kick-off + one poll.
        assert mock_client.call_endpoint.call_count >= 2

    @pytest.mark.asyncio
    async def test_polls_and_reports_final_result_live_run(self):
        """Live (non-dry) run shows created channels count from final execution row."""
        from tools.auto_creation import register
        from mcp.server.fastmcp import FastMCP
        from unittest.mock import AsyncMock

        mcp = FastMCP("test")
        register(mcp)

        kickoff_response = {"execution_id": 99, "status": "running", "message": "started"}
        final_response = {
            "id": 99,
            "status": "completed",
            "mode": "execute",
            "streams_evaluated": 1000,
            "streams_matched": 20,
            "channels_created": 20,
            "channels_updated": 2,
            "groups_created": 1,
            "streams_skipped": 5,
            "duration_seconds": 60.0,
        }

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [kickoff_response, final_response]

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("run_auto_creation", {"dry_run": False})

        text = result[0][0].text
        assert "1000" in text
        assert "20" in text

    @pytest.mark.asyncio
    async def test_poll_timeout_returns_execution_id_not_false_complete(self):
        """If poll times out still-running, report execution_id + 'still running' (not fake complete)."""
        from tools.auto_creation import register
        from mcp.server.fastmcp import FastMCP
        from unittest.mock import AsyncMock

        mcp = FastMCP("test")
        register(mcp)

        kickoff_response = {"execution_id": 55, "status": "running", "message": "started"}
        # Every poll returns still-running.
        still_running = {
            "id": 55,
            "status": "running",
            "streams_evaluated": 0,
            "channels_created": 0,
            "duration_seconds": None,
        }

        mock_client = AsyncMock()
        # kick-off + unlimited running responses
        mock_client.call_endpoint.side_effect = [kickoff_response] + [still_running] * 100

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            with patch("tools.auto_creation._poll_sleep", return_value=None):
                result = await mcp.call_tool("run_auto_creation", {"dry_run": True})

        text = result[0][0].text
        # Must surface execution_id so the user can check later.
        assert "55" in text
        # Must say still running, not "complete".
        assert "still running" in text.lower() or "running" in text.lower()
        # Must NOT say "complete" or "Duration: 0.0s".
        assert "complete" not in text.lower()

    @pytest.mark.asyncio
    async def test_ac_get_execution_endpoint_registered(self):
        """ac_get_execution must exist in ENDPOINTS so the poll call is contract-checked."""
        from _endpoint_contracts import ENDPOINTS

        assert "ac_get_execution" in ENDPOINTS
        ep = ENDPOINTS["ac_get_execution"]
        assert ep.method == "GET"
        assert ep.path == "/api/auto-creation/executions/{execution_id}"


# --- TestResolveStreamIdExactMatch (A4) ---
class TestResolveStreamIdExactMatch:
    """_resolve_stream_id prefers an exact (case-insensitive) name match over
    the first result (bd-1wq7z.6)."""

    @pytest.mark.asyncio
    async def test_exact_match_chosen_over_first_result(self):
        """Search for 'US : ESPN' returns id=5001 first ('US: ESPN FHD') and
        id=5203 second ('US : ESPN').  The tool must pick stream 5203 (exact match)
        not 5001 (first result), verified end-to-end via add_stream(force_new)."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {"id": 99, "name": "US : ESPN", "channel_group_id": 7},  # channels_create
            {                                                            # streams_list
                "results": [
                    {"id": 5001, "name": "US: ESPN FHD"},   # first result, not exact
                    {"id": 5203, "name": "US : ESPN"},       # exact match
                ],
                "count": 2,
            },
            None,  # channels_add_stream
        ]

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "add_stream",
                {"stream_name": "US : ESPN", "group_id": 7, "dedup_action": "force_new"},
            )

        # add_stream must surface stream id=5203 (exact match), not 5001.
        text = result[0][0].text
        assert "id=5203" in text, f"Expected id=5203 in output, got: {text}"
        assert "id=5001" not in text

        # The add-stream call must carry stream_id=5203.
        add_stream_call = mock_client.call_endpoint.call_args_list[2]
        assert add_stream_call.kwargs["body"]["stream_id"] == 5203

    @pytest.mark.asyncio
    async def test_falls_back_to_first_when_no_exact_match(self):
        """When no exact match exists, fall back to the first result (existing
        behaviour for non-exact situations) — verified via add_stream(force_new)."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {"id": 88, "name": "US : ESPN", "channel_group_id": 7},  # channels_create
            {
                "results": [
                    {"id": 100, "name": "US: ESPN FHD"},
                    {"id": 101, "name": "US: ESPN 4K"},
                ],
                "count": 2,
            },
            None,
        ]

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "add_stream",
                {"stream_name": "US : ESPN", "group_id": 7, "dedup_action": "force_new"},
            )

        # No exact match; falls back to first result (id=100).
        add_stream_call = mock_client.call_endpoint.call_args_list[2]
        assert add_stream_call.kwargs["body"]["stream_id"] == 100

    @pytest.mark.asyncio
    async def test_exact_match_case_insensitive(self):
        """Exact-match comparison is normalised and case-insensitive — verified
        end-to-end via add_stream(force_new)."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {"id": 77, "name": "CNN HD", "channel_group_id": 2},  # channels_create
            {
                "results": [
                    {"id": 200, "name": "cnn hd unrelated"},
                    {"id": 201, "name": "CNN HD"},  # case-insensitive exact match
                ],
                "count": 2,
            },
            None,
        ]

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "add_stream",
                {"stream_name": "CNN HD", "group_id": 2, "dedup_action": "force_new"},
            )

        # The case-insensitive exact match (id=201) wins over first result (id=200).
        add_stream_call = mock_client.call_endpoint.call_args_list[2]
        assert add_stream_call.kwargs["body"]["stream_id"] == 201

    @pytest.mark.asyncio
    async def test_add_stream_force_new_picks_exact_match_stream(self):
        """End-to-end: add_stream(force_new) calls _resolve_stream_id which
        must pick the exact-match stream, not the first result."""
        from tools.channels import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {"id": 42, "name": "US : ESPN", "channel_group_id": 7},  # channels_create
            {                                                            # streams_list
                "results": [
                    {"id": 5001, "name": "US: ESPN FHD"},
                    {"id": 5203, "name": "US : ESPN"},
                ],
                "count": 2,
            },
            None,  # channels_add_stream
        ]

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "add_stream",
                {"stream_name": "US : ESPN", "group_id": 7, "dedup_action": "force_new"},
            )

        text = result[0][0].text
        # Must report stream id=5203 (the exact match), not 5001.
        assert "id=5203" in text, f"Expected id=5203 in output, got: {text}"
        assert "id=5001" not in text

        # The add-stream call must carry stream_id=5203.
        add_stream_call = mock_client.call_endpoint.call_args_list[2]
        assert add_stream_call.kwargs["body"]["stream_id"] == 5203


# --- TestM3UGroupSettingsWireFormat (A4) ---
class TestM3UGroupSettingsWireFormat:
    """update_m3u_group_settings and bulk_update_m3u_group_settings must send
    {"group_settings": [{"channel_group": <int id>, "enabled": bool}]} not a
    flat {name: bool} dict (bd-1wq7z.7)."""

    @pytest.mark.asyncio
    async def test_update_sends_group_settings_list_with_id(self):
        """Single-group update: body is {"group_settings": [{"channel_group": <id>, "enabled": <bool>}]}."""
        from tools.m3u import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        # First call: get account to resolve name → id.
        mock_client.call_endpoint.return_value = {
            "id": 3,
            "name": "My Provider",
            "channel_groups": [
                {"channel_group": 42, "name": "Sports", "enabled": True},
                {"channel_group": 99, "name": "News", "enabled": True},
            ],
        }
        # patch() call returns None (success).
        mock_client.patch.return_value = None

        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "update_m3u_group_settings",
                {"account_id": 3, "group_name": "Sports", "enabled": False},
            )

        # Exactly one patch call with the correct structured body.
        mock_client.patch.assert_awaited_once()
        call = mock_client.patch.call_args
        body = call.kwargs.get("json_data") or call.args[1]
        assert "group_settings" in body, f"Expected 'group_settings' key in body, got {body}"
        gs = body["group_settings"]
        assert isinstance(gs, list), f"group_settings must be a list, got {type(gs)}"
        assert len(gs) == 1
        assert gs[0]["channel_group"] == 42
        assert gs[0]["enabled"] is False

        text = result[0][0].text
        assert "Sports" in text
        assert "disabled" in text.lower()

    @pytest.mark.asyncio
    async def test_bulk_update_sends_group_settings_list(self):
        """Bulk update: body is {"group_settings": [{"channel_group": <id>, "enabled": <bool>}, ...]}."""
        from tools.m3u import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "id": 5,
            "name": "Provider X",
            "channel_groups": [
                {"channel_group": 10, "name": "Sports", "enabled": True},
                {"channel_group": 20, "name": "News", "enabled": True},
                {"channel_group": 30, "name": "Movies", "enabled": False},
            ],
        }
        mock_client.patch.return_value = None

        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "bulk_update_m3u_group_settings",
                {"account_id": 5, "groups": {"Sports": False, "News": False, "Movies": True}},
            )

        mock_client.patch.assert_awaited_once()
        call = mock_client.patch.call_args
        body = call.kwargs.get("json_data") or call.args[1]
        assert "group_settings" in body
        gs = body["group_settings"]
        assert isinstance(gs, list)
        assert len(gs) == 3

        # Verify each entry maps to the correct integer id and bool.
        by_id = {item["channel_group"]: item["enabled"] for item in gs}
        assert by_id[10] is False  # Sports disabled
        assert by_id[20] is False  # News disabled
        assert by_id[30] is True   # Movies enabled

        text = result[0][0].text
        assert "3" in text  # 3 groups updated

    @pytest.mark.asyncio
    async def test_unknown_group_name_reports_error(self):
        """A group name that doesn't appear in the account's channel_groups is reported."""
        from tools.m3u import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "id": 3,
            "name": "My Provider",
            "channel_groups": [
                {"channel_group": 42, "name": "Sports", "enabled": True},
            ],
        }
        mock_client.patch.return_value = None

        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "update_m3u_group_settings",
                {"account_id": 3, "group_name": "DoesNotExist", "enabled": False},
            )

        text = result[0][0].text
        assert "not found" in text.lower() or "DoesNotExist" in text


# --- TestApplyProfileToChannelsEnabled (A4) ---
class TestApplyProfileToChannelsEnabled:
    """apply_profile_to_channels must send an explicit 'enabled' field so the
    backend doesn't silently default to True (bd-1wq7z.13)."""

    @pytest.mark.asyncio
    async def test_sends_enabled_true_explicitly(self):
        """Calling with enabled=True sends enabled=True in the request body."""
        from tools.profiles import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        # bulk_update returns success; list_profiles for read-back returns [].
        mock_client.call_endpoint.side_effect = [
            {"success": True},  # channel_profiles_bulk_update
            [],                  # channel_profiles_list (read-back)
        ]

        with patch("tools.profiles.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "apply_profile_to_channels",
                {"profile_id": 7, "channel_ids": [1, 2, 3], "enabled": True},
            )

        call = mock_client.call_endpoint.call_args_list[0]
        body = call.kwargs["body"]
        assert "enabled" in body, "enabled field must be present in request body"
        assert body["enabled"] is True
        assert body["channel_ids"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_sends_enabled_false_explicitly(self):
        """Calling with enabled=False sends enabled=False — the operator can disable."""
        from tools.profiles import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {"success": True},
            [],
        ]

        with patch("tools.profiles.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "apply_profile_to_channels",
                {"profile_id": 7, "channel_ids": [10, 20], "enabled": False},
            )

        call = mock_client.call_endpoint.call_args_list[0]
        body = call.kwargs["body"]
        assert body["enabled"] is False

    @pytest.mark.asyncio
    async def test_default_enabled_is_true(self):
        """When enabled is omitted, it defaults to True (safe, opt-in to enable)."""
        from tools.profiles import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            {"success": True},
            [],
        ]

        with patch("tools.profiles.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "apply_profile_to_channels",
                {"profile_id": 7, "channel_ids": [1]},
            )

        call = mock_client.call_endpoint.call_args_list[0]
        body = call.kwargs["body"]
        assert "enabled" in body
        assert body["enabled"] is True


# --- TestDeleteAllNotificationsReadOnly (A4) ---
class TestDeleteAllNotificationsReadOnly:
    """delete_all_notifications must pass read_only as a query param so unread
    notifications can be included (bd-1wq7z.14)."""

    @pytest.mark.asyncio
    async def test_default_only_deletes_read(self):
        """Default (read_only=True) sends read_only=True query param — safe behaviour."""
        from tools.notifications import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        # delete_all returns None; list returns empty (for read-back).
        mock_client.call_endpoint.side_effect = [
            None,                                          # notifications_delete_all
            {"notifications": [], "total": 0, "unread_count": 0},  # read-back list
        ]

        with patch("tools.notifications.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("delete_all_notifications", {})

        delete_call = mock_client.call_endpoint.call_args_list[0]
        assert delete_call.args[0] is ENDPOINTS["notifications_delete_all"]
        query = delete_call.kwargs.get("query", {})
        assert query.get("read_only") is True, (
            f"Default must pass read_only=True, got query={query}"
        )

        text = result[0][0].text
        assert "deleted" in text.lower() or "notifications" in text.lower()

    @pytest.mark.asyncio
    async def test_include_unread_sends_read_only_false(self):
        """include_unread=True (or read_only=False) sends read_only=False to backend."""
        from tools.notifications import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            None,
            {"notifications": [], "total": 0, "unread_count": 0},
        ]

        with patch("tools.notifications.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "delete_all_notifications",
                {"include_unread": True},
            )

        delete_call = mock_client.call_endpoint.call_args_list[0]
        query = delete_call.kwargs.get("query", {})
        assert query.get("read_only") is False, (
            f"include_unread=True must pass read_only=False, got query={query}"
        )

    @pytest.mark.asyncio
    async def test_notifications_delete_all_contract_has_read_only_param(self):
        """The endpoint contract declares read_only as a query_param."""
        from _endpoint_contracts import ENDPOINTS

        ep = ENDPOINTS["notifications_delete_all"]
        assert "read_only" in ep.query_params, (
            f"notifications_delete_all must declare read_only in query_params, "
            f"got query_params={ep.query_params}"
        )


# --- TestListNormalizationRules (lq38l.2) ---
class TestListNormalizationRules:
    """list_normalization_rules sources rule data from /rules (not /groups) endpoint.

    The /groups endpoint returns group metadata only — no nested rules, no
    rule count.  The /rules endpoint returns groups WITH rules nested.
    This class pins that the fix is in place and stays in place.
    """

    @pytest.mark.asyncio
    async def test_shows_nonzero_rule_count_and_names(self):
        """Groups with rules show correct count and up to 5 rule names."""
        from tools.normalization import register
        from mcp.server.fastmcp import FastMCP
        from _endpoint_contracts import ENDPOINTS

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "groups": [
                {
                    "id": 1,
                    "name": "Title Case",
                    "enabled": True,
                    "priority": 0,
                    "rules": [
                        {"id": 10, "name": "Title-case all", "action_type": "replace", "condition_type": "always", "enabled": True},
                        {"id": 11, "name": "Fix abbreviations", "action_type": "replace", "condition_type": "regex", "enabled": True},
                    ],
                },
                {
                    "id": 2,
                    "name": "Strip Quality",
                    "enabled": True,
                    "priority": 1,
                    "rules": [
                        {"id": 20, "name": "Strip HD suffix", "action_type": "strip_suffix", "condition_type": "ends_with", "enabled": True},
                        {"id": 21, "name": "Strip FHD", "action_type": "remove", "condition_type": "contains", "enabled": True},
                        {"id": 22, "name": "Strip 4K", "action_type": "remove", "condition_type": "contains", "enabled": True},
                    ],
                },
            ]
        })

        with patch("tools.normalization.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_normalization_rules", {})

        text = result[0][0].text
        # Correct group count.
        assert "2 groups" in text
        # Group names present.
        assert "Title Case" in text
        assert "Strip Quality" in text
        # Non-zero rule counts — the key regression guard.
        assert "2 rules" in text
        assert "3 rules" in text
        # Rule names surface.
        assert "Title-case all" in text
        assert "Strip HD suffix" in text
        # Called the rules endpoint, not the groups-only endpoint.
        mock_client.call_endpoint.assert_awaited_once_with(ENDPOINTS["normalization_list_rules"])

    @pytest.mark.asyncio
    async def test_empty_groups(self):
        """Empty groups list returns 'no rules' message."""
        from tools.normalization import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={"groups": []})

        with patch("tools.normalization.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_normalization_rules", {})

        assert "No normalization rules" in result[0][0].text

    @pytest.mark.asyncio
    async def test_group_with_zero_rules_shows_zero(self):
        """A group whose rules list is empty renders '0 rules' (not crashing)."""
        from tools.normalization import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "groups": [
                {"id": 5, "name": "Empty Group", "enabled": True, "priority": 0, "rules": []},
            ]
        })

        with patch("tools.normalization.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_normalization_rules", {})

        text = result[0][0].text
        assert "Empty Group" in text
        assert "0 rules" in text

    @pytest.mark.asyncio
    async def test_more_than_five_rules_truncated(self):
        """When a group has >5 rules, only 5 names are shown plus an overflow line."""
        from tools.normalization import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        rules = [
            {"id": i, "name": f"Rule {i}", "action_type": "remove", "condition_type": "contains", "enabled": True}
            for i in range(1, 9)
        ]
        mock_client = _make_ecm_client_mock(call_endpoint={
            "groups": [{"id": 3, "name": "Big Group", "enabled": True, "priority": 0, "rules": rules}]
        })

        with patch("tools.normalization.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_normalization_rules", {})

        text = result[0][0].text
        assert "8 rules" in text
        assert "Rule 1" in text
        assert "Rule 5" in text
        # Rule 6-8 are suppressed — the overflow line must appear.
        assert "3 more" in text

    @pytest.mark.asyncio
    async def test_group_with_none_rules_key_handled_gracefully(self):
        """A group where 'rules' key is None or absent does not crash."""
        from tools.normalization import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "groups": [
                {"id": 7, "name": "No Key Group", "enabled": False, "priority": 0},
            ]
        })

        with patch("tools.normalization.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_normalization_rules", {})

        text = result[0][0].text
        assert "No Key Group" in text
        assert "0 rules" in text

    def test_normalization_list_rules_endpoint_registered(self):
        """The normalization_list_rules endpoint is in the registry with correct method/path."""
        from _endpoint_contracts import ENDPOINTS

        ep = ENDPOINTS["normalization_list_rules"]
        assert ep.method == "GET"
        assert ep.path == "/api/normalization/rules"


# --- TestRunAutoCreationMergeLabels (bd-0emgo.4) ---
class TestRunAutoCreationMergeLabels:
    """run_auto_creation output renders 'Stream merges' and 'Channels touched'
    instead of the misleading single 'Channels updated' label (bd-0emgo.4)."""

    @pytest.mark.asyncio
    async def test_output_shows_stream_merges_and_channels_touched(self):
        """Live run with merge data shows 'Stream merges' and 'Channels touched' lines."""
        from tools.auto_creation import register
        from mcp.server.fastmcp import FastMCP
        from unittest.mock import AsyncMock

        mcp = FastMCP("test")
        register(mcp)

        kickoff_response = {"execution_id": 77, "status": "running", "message": "started"}
        final_response = {
            "id": 77,
            "status": "completed",
            "mode": "execute",
            "streams_evaluated": 2000,
            "streams_matched": 1341,
            "channels_created": 0,
            "channels_updated": 5,   # genuine property updates only
            "channels_touched": 726,  # distinct channels that received merges
            "streams_merged": 1341,   # individual merge operations
            "groups_created": 0,
            "streams_skipped": 0,
            "duration_seconds": 120.0,
        }

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [kickoff_response, final_response]

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("run_auto_creation", {"dry_run": False})

        text = result[0][0].text

        # Must show the honest merge counts.
        assert "Stream merges: 1341" in text, (
            f"Expected 'Stream merges: 1341' in output, got: {text!r}"
        )
        assert "Channels touched: 726" in text, (
            f"Expected 'Channels touched: 726' in output, got: {text!r}"
        )
        # channels_updated (5) reflects only property updates — still shown.
        assert "Channels updated: 5" in text, (
            f"Expected 'Channels updated: 5' in output, got: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_old_label_does_not_appear_alone_as_merge_count(self):
        """The misleading 'Channels updated: 1341' pattern from the bug must not appear.

        Pre-fix the output was:  'Channels updated: 1341'  (inflate)
        Post-fix it is:          'Stream merges: 1341'
                                 'Channels touched: 726'
                                 'Channels updated: 5'    (genuine updates)

        This test explicitly guards against regressing to the pre-fix shape.
        """
        from tools.auto_creation import register
        from mcp.server.fastmcp import FastMCP
        from unittest.mock import AsyncMock

        mcp = FastMCP("test")
        register(mcp)

        kickoff_response = {"execution_id": 88, "status": "running"}
        final_response = {
            "id": 88,
            "status": "completed",
            "mode": "execute",
            "streams_evaluated": 3000,
            "streams_matched": 1341,
            "channels_created": 0,
            "channels_updated": 0,
            "channels_touched": 726,
            "streams_merged": 1341,
            "groups_created": 0,
            "streams_skipped": 0,
            "duration_seconds": 95.0,
        }

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [kickoff_response, final_response]

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("run_auto_creation", {"dry_run": False})

        text = result[0][0].text

        # The old bug would render "Channels updated: 1341".
        # After the fix, streams_merged is shown as "Stream merges: 1341"
        # and channels_updated reflects only property updates (0 here).
        assert "Channels updated: 1341" not in text, (
            "Pre-fix inflation bug still present: 'Channels updated: 1341' rendered"
        )
        assert "Stream merges: 1341" in text
        assert "Channels touched: 726" in text

    @pytest.mark.asyncio
    async def test_channels_touched_defaults_to_zero_when_absent(self):
        """If the execution result has no channels_touched key, output shows 0 (graceful fallback)."""
        from tools.auto_creation import register
        from mcp.server.fastmcp import FastMCP
        from unittest.mock import AsyncMock

        mcp = FastMCP("test")
        register(mcp)

        kickoff_response = {"execution_id": 99, "status": "running"}
        # Omit channels_touched / streams_merged — older execution rows won't have them.
        final_response = {
            "id": 99,
            "status": "completed",
            "mode": "execute",
            "streams_evaluated": 500,
            "streams_matched": 10,
            "channels_created": 10,
            "channels_updated": 0,
            "groups_created": 0,
            "streams_skipped": 0,
            "duration_seconds": 30.0,
        }

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [kickoff_response, final_response]

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("run_auto_creation", {"dry_run": False})

        text = result[0][0].text

        # Should render 0 gracefully, not crash.
        assert "Stream merges: 0" in text
        assert "Channels touched: 0" in text
