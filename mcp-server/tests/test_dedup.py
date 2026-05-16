"""Tests for MCP dedup tools (BD-O / bd-70ylc).

Covers the three tools declared in mcp-server/tools/dedup.py:

  list_pending_channel_merges — paginated queue reader
  accept_channel_merge        — confirms a merge; 4xx → structured error
  dismiss_channel_merge       — rejects a candidate; 4xx → structured error

Mocks the ECM HTTP client at the call_endpoint boundary so tests do not
require a running ECM backend.

FastMCP serialises dict-returning tools to JSON text in a single
``TextContent`` object: ``result[0].text`` is the JSON string.  All dedup
tools return dicts so we parse the text back to a dict for assertions.

All 4xx → structured-envelope tests verify that the tool returns
``{error: {code, message}}`` rather than raising, per ADR-008 §D7.
5xx → ToolError tests verify that unrecoverable backend errors are not
silently swallowed.
"""
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from unittest.mock import AsyncMock, patch


def _make_client_mock(**kwargs):
    """Build an AsyncMock ECMClient with configurable call_endpoint behaviour.

    Pass ``call_endpoint=<return_value>`` for normal success, or
    ``call_endpoint_side_effect=<exception>`` for error scenarios.
    """
    mock = AsyncMock()
    if "call_endpoint_side_effect" in kwargs:
        mock.call_endpoint.side_effect = kwargs["call_endpoint_side_effect"]
    elif "call_endpoint" in kwargs:
        mock.call_endpoint.return_value = kwargs["call_endpoint"]
    return mock


def _parse(result) -> dict:
    """Parse the JSON text from a FastMCP dict-returning tool call result.

    FastMCP serialises dict returns as JSON in a single TextContent object:
    ``result == [TextContent(text='{"key": ...}')]``.
    """
    return json.loads(result[0].text)


def _http_error_for(status_code: int, path: str = "/api/channel-merges/1/accept") -> RuntimeError:
    """Build the RuntimeError that ecm_client._http_error() would produce.

    call_endpoint re-raises HTTPStatusError as RuntimeError with the string
    format: "<METHOD> <path> -> HTTP <status> <reason>[: <detail>]".
    """
    return RuntimeError(f"POST {path} -> HTTP {status_code} Something")


class TestListPendingChannelMerges:
    """Tests for list_pending_channel_merges tool."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_paginated_list(self):
        """list_pending_channel_merges returns the raw paginated envelope on success."""
        from tools.dedup import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        payload = {
            "merges": [
                {
                    "id": 1,
                    "stream_name": "ESPN HD",
                    "group_id": 10,
                    "candidate_channel_id": "uuid-abc",
                    "confidence": 0.92,
                    "status": "pending",
                    "created_at": 1716000000000,
                    "resolved_at": None,
                    "resolution_source": None,
                    "trigger_context": "m3u_refresh",
                },
                {
                    "id": 2,
                    "stream_name": "CNN International",
                    "group_id": 10,
                    "candidate_channel_id": "uuid-def",
                    "confidence": 0.85,
                    "status": "pending",
                    "created_at": 1716000001000,
                    "resolved_at": None,
                    "resolution_source": None,
                    "trigger_context": "drag_drop",
                },
            ],
            "total": 2,
            "page": 1,
            "page_size": 50,
            "total_pages": 1,
        }
        mock_client = _make_client_mock(call_endpoint=payload)

        with patch("tools.dedup.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_pending_channel_merges", {})

        data = _parse(result)
        assert data["total"] == 2
        assert len(data["merges"]) == 2
        assert data["merges"][0]["stream_name"] == "ESPN HD"
        assert data["merges"][1]["stream_name"] == "CNN International"
        assert data["page"] == 1
        assert data["total_pages"] == 1

    @pytest.mark.asyncio
    async def test_default_status_is_pending(self):
        """list_pending_channel_merges sends status='pending' when no status arg."""
        from tools.dedup import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client_mock(call_endpoint={
            "merges": [], "total": 0, "page": 1, "page_size": 50, "total_pages": 0,
        })

        with patch("tools.dedup.get_ecm_client", return_value=mock_client):
            await mcp.call_tool("list_pending_channel_merges", {})

        # Verify call_endpoint was called with query containing status='pending'.
        call_kwargs = mock_client.call_endpoint.call_args
        # call_endpoint is called positionally (ep) + keyword query=...
        passed_query = call_kwargs.kwargs.get("query", {}) if call_kwargs.kwargs else {}
        assert passed_query.get("status") == "pending"

    @pytest.mark.asyncio
    async def test_status_filter_forwarded(self):
        """list_pending_channel_merges forwards explicit status filter to backend."""
        from tools.dedup import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client_mock(call_endpoint={
            "merges": [], "total": 0, "page": 1, "page_size": 50, "total_pages": 0,
        })

        with patch("tools.dedup.get_ecm_client", return_value=mock_client):
            await mcp.call_tool("list_pending_channel_merges", {"status": "merged"})

        call_kwargs = mock_client.call_endpoint.call_args
        passed_query = call_kwargs.kwargs.get("query", {}) if call_kwargs.kwargs else {}
        assert passed_query.get("status") == "merged"

    @pytest.mark.asyncio
    async def test_group_id_filter_forwarded(self):
        """list_pending_channel_merges forwards group_id to backend query."""
        from tools.dedup import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client_mock(call_endpoint={
            "merges": [], "total": 0, "page": 1, "page_size": 50, "total_pages": 0,
        })

        with patch("tools.dedup.get_ecm_client", return_value=mock_client):
            await mcp.call_tool("list_pending_channel_merges", {"group_id": 42})

        call_kwargs = mock_client.call_endpoint.call_args
        passed_query = call_kwargs.kwargs.get("query", {}) if call_kwargs.kwargs else {}
        assert passed_query.get("group_id") == 42


class TestAcceptChannelMerge:
    """Tests for accept_channel_merge tool."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_outcome(self):
        """accept_channel_merge returns the merge outcome envelope on success."""
        from tools.dedup import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        backend_response = {
            "merged_into_channel_id": "uuid-abc",
            "journal_entry_id": 99,
            "source_stream_id": "stream-xyz",
            "confidence": 0.92,
        }
        mock_client = _make_client_mock(call_endpoint=backend_response)

        with patch("tools.dedup.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("accept_channel_merge", {"merge_id": 1})

        data = _parse(result)
        assert data["merged_into_channel_id"] == "uuid-abc"
        assert data["journal_entry_id"] == 99
        assert data["source_stream_id"] == "stream-xyz"
        assert data["confidence"] == 0.92
        # Tool injects status='merged' when backend omits it.
        assert data["status"] == "merged"

    @pytest.mark.asyncio
    async def test_404_returns_target_not_found_envelope(self):
        """accept_channel_merge 404 returns {error: {code: TARGET_NOT_FOUND}} — does not raise."""
        from tools.dedup import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client_mock(
            call_endpoint_side_effect=_http_error_for(404)
        )

        with patch("tools.dedup.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("accept_channel_merge", {"merge_id": 7})

        data = _parse(result)
        assert "error" in data
        assert data["error"]["code"] == "TARGET_NOT_FOUND"
        assert "dismiss_channel_merge" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_409_returns_invalid_state_envelope(self):
        """accept_channel_merge 409 returns {error: {code: INVALID_STATE}} — does not raise."""
        from tools.dedup import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client_mock(
            call_endpoint_side_effect=_http_error_for(409)
        )

        with patch("tools.dedup.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("accept_channel_merge", {"merge_id": 3})

        data = _parse(result)
        assert "error" in data
        assert data["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_5xx_raises_tool_error(self):
        """accept_channel_merge propagates 5xx errors as ToolError (FastMCP wrapper)."""
        from tools.dedup import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client_mock(
            call_endpoint_side_effect=RuntimeError(
                "POST /api/channel-merges/1/accept -> HTTP 500 Internal Server Error"
            )
        )

        with patch("tools.dedup.get_ecm_client", return_value=mock_client):
            with pytest.raises(ToolError):
                await mcp.call_tool("accept_channel_merge", {"merge_id": 1})


class TestDismissChannelMerge:
    """Tests for dismiss_channel_merge tool."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_outcome(self):
        """dismiss_channel_merge returns the dismissal outcome envelope on success."""
        from tools.dedup import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        backend_response = {"journal_entry_id": 55}
        mock_client = _make_client_mock(call_endpoint=backend_response)

        with patch("tools.dedup.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("dismiss_channel_merge", {"merge_id": 2})

        data = _parse(result)
        assert data["journal_entry_id"] == 55
        # Tool injects status='dismissed' when backend omits it.
        assert data["status"] == "dismissed"

    @pytest.mark.asyncio
    async def test_409_returns_invalid_state_envelope(self):
        """dismiss_channel_merge 409 returns {error: {code: INVALID_STATE}} — does not raise."""
        from tools.dedup import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client_mock(
            call_endpoint_side_effect=RuntimeError(
                "POST /api/channel-merges/5/dismiss -> HTTP 409 Conflict"
            )
        )

        with patch("tools.dedup.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("dismiss_channel_merge", {"merge_id": 5})

        data = _parse(result)
        assert "error" in data
        assert data["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_5xx_raises_tool_error(self):
        """dismiss_channel_merge propagates 5xx errors as ToolError (FastMCP wrapper)."""
        from tools.dedup import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client_mock(
            call_endpoint_side_effect=RuntimeError(
                "POST /api/channel-merges/5/dismiss -> HTTP 500 Internal Server Error"
            )
        )

        with patch("tools.dedup.get_ecm_client", return_value=mock_client):
            with pytest.raises(ToolError):
                await mcp.call_tool("dismiss_channel_merge", {"merge_id": 5})
