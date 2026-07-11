"""TDD tests for enhancedchannelmanager-twadj — logo management tools.

Covers list_logos (paged), create_logo (URL-based), update_logo (PATCH), and
delete_logo (confirm-gated per the delete_channel/delete_channel_pipeline_rule
convention). Multipart upload is intentionally out of scope.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _mcp():
    from mcp.server.fastmcp import FastMCP
    from tools.logos import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


class TestListLogos:
    @pytest.mark.asyncio
    async def test_lists_logos_with_usage_info(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "count": 2,
            "next": None,
            "results": [
                {"id": 1, "name": "ESPN", "url": "http://x/espn.png", "channel_count": 3},
                {"id": 2, "name": "Unused", "url": "http://x/unused.png", "channel_count": 0},
            ],
        }

        with patch("tools.logos.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_logos", {})

        text = _text(result)
        assert "ESPN" in text
        assert "used by 3 channel" in text
        assert "unused" in text

        call = client.call_endpoint.call_args
        assert call.kwargs["query"] == {"page": 1, "page_size": 100, "search": None}

    @pytest.mark.asyncio
    async def test_no_logos_found(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"count": 0, "results": [], "next": None}

        with patch("tools.logos.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_logos", {})

        assert "No logos found" in _text(result)

    @pytest.mark.asyncio
    async def test_pagination_query_forwarded(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"count": 1, "results": [{"id": 3, "name": "X"}], "next": "http://.../?page=3"}

        with patch("tools.logos.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_logos", {"page": 2, "page_size": 25, "search": "ESPN"})

        call = client.call_endpoint.call_args
        assert call.kwargs["query"] == {"page": 2, "page_size": 25, "search": "ESPN"}
        assert "next page" in _text(result)


class TestCreateLogo:
    @pytest.mark.asyncio
    async def test_creates_from_url(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 5, "name": "ESPN"}

        with patch("tools.logos.get_ecm_client", return_value=client):
            result = await mcp.call_tool("create_logo", {"name": "ESPN", "url": "http://x/espn.png"})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"name": "ESPN", "url": "http://x/espn.png"}
        assert "id=5" in _text(result)


class TestUpdateLogo:
    @pytest.mark.asyncio
    async def test_updates_provided_fields_only(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 5, "name": "ESPN HD"}

        with patch("tools.logos.get_ecm_client", return_value=client):
            await mcp.call_tool("update_logo", {"logo_id": 5, "name": "ESPN HD"})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"name": "ESPN HD"}

    @pytest.mark.asyncio
    async def test_no_changes_short_circuits(self):
        mcp = _mcp()
        client = AsyncMock()

        with patch("tools.logos.get_ecm_client", return_value=client):
            result = await mcp.call_tool("update_logo", {"logo_id": 5})

        assert "No changes specified" in _text(result)
        client.call_endpoint.assert_not_called()


class TestDeleteLogo:
    @pytest.mark.asyncio
    async def test_preview_on_confirm_false_deletes_nothing(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 5, "name": "ESPN", "channel_count": 0}

        with patch("tools.logos.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_logo", {"logo_id": 5})

        text = _text(result)
        assert "ESPN" in text
        assert "confirm=True" in text
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["channels_get_logo"]

    @pytest.mark.asyncio
    async def test_preview_warns_when_in_use(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 5, "name": "ESPN", "channel_count": 4}

        with patch("tools.logos.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_logo", {"logo_id": 5})

        text = _text(result)
        assert "WARNING" in text
        assert "4 channel" in text

    @pytest.mark.asyncio
    async def test_confirm_true_deletes(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = None

        with patch("tools.logos.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_logo", {"logo_id": 5, "confirm": True})

        assert "deleted" in _text(result).lower()
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["channels_delete_logo"]
