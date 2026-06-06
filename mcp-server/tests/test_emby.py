"""Tests for the Emby MCP tools (GH #475, bd-v9tp7).

Covers the ``_clear_logos_with_wait`` 202+poll helper and the
``clear_emby_logos`` tool's validation + result formatting. Mirrors the
bulk-commit helper tests (test_tools.py) — drive an ``AsyncMock`` client, no
real HTTP.
"""
from unittest.mock import AsyncMock

import pytest

from tools import emby as tools_emby


class TestClearLogosWithWait:
    @pytest.mark.asyncio
    async def test_polls_until_completed_and_returns_result(self, monkeypatch):
        """A 202 + running status drives the helper to GET until completed,
        then returns the unwrapped ``result`` summary."""
        monkeypatch.setattr(tools_emby, "_CLEAR_LOGOS_POLL_INTERVAL_S", 0)

        client = AsyncMock()
        client.call_endpoint.return_value = {"job_id": "j1", "status": "running"}
        summary = {
            "channels_processed": 3,
            "images_deleted": 2,
            "images_missing": 1,
            "errors": 0,
            "logo_types": ["Primary"],
        }
        client.get.side_effect = [
            {"job_id": "j1", "status": "running"},
            {"job_id": "j1", "status": "completed", "result": summary},
        ]

        result = await tools_emby._clear_logos_with_wait(client, {"logo_types": ["Primary"]})

        assert result == summary
        assert client.call_endpoint.await_count == 1
        assert client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_failed_job_raises_with_error_message(self, monkeypatch):
        monkeypatch.setattr(tools_emby, "_CLEAR_LOGOS_POLL_INTERVAL_S", 0)
        client = AsyncMock()
        client.call_endpoint.return_value = {"job_id": "j2", "status": "running"}
        client.get.side_effect = [
            {"job_id": "j2", "status": "failed", "error": "401 unauthorized"},
        ]

        with pytest.raises(RuntimeError) as exc:
            await tools_emby._clear_logos_with_wait(client, {"logo_types": ["Primary"]})
        assert "401 unauthorized" in str(exc.value)

    @pytest.mark.asyncio
    async def test_missing_job_id_raises(self):
        client = AsyncMock()
        client.call_endpoint.return_value = {"status": "running"}  # no job_id
        with pytest.raises(RuntimeError):
            await tools_emby._clear_logos_with_wait(client, {"logo_types": ["Primary"]})


class TestClearEmbyLogosTool:
    """Exercises the registered tool function end-to-end against a mock client."""

    def _get_tool(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        captured = {}
        orig_tool = mcp.tool

        def capturing_tool(*a, **k):
            deco = orig_tool(*a, **k)

            def wrap(fn):
                captured[fn.__name__] = fn
                return deco(fn)

            return wrap

        mcp.tool = capturing_tool  # type: ignore[method-assign]
        tools_emby.register(mcp)
        return captured["clear_emby_logos"]

    @pytest.mark.asyncio
    async def test_rejects_invalid_logo_type_without_calling_backend(self, monkeypatch):
        tool = self._get_tool()
        client = AsyncMock()
        monkeypatch.setattr(tools_emby, "get_ecm_client", lambda: client)

        out = await tool(logo_types=["Primary", "Bogus"])

        assert "Invalid logo type" in out
        assert "Bogus" in out
        client.call_endpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_clears_all_three_types(self, monkeypatch):
        tool = self._get_tool()
        monkeypatch.setattr(tools_emby, "_CLEAR_LOGOS_POLL_INTERVAL_S", 0)

        client = AsyncMock()
        client.call_endpoint.return_value = {"job_id": "j", "status": "running"}
        client.get.side_effect = [
            {
                "job_id": "j",
                "status": "completed",
                "result": {
                    "channels_processed": 5,
                    "images_deleted": 4,
                    "images_missing": 11,
                    "errors": 0,
                },
            }
        ]
        monkeypatch.setattr(tools_emby, "get_ecm_client", lambda: client)

        out = await tool()  # no args → all three types

        # The POST body carried all three default types.
        body = client.call_endpoint.await_args.kwargs["body"]
        assert sorted(body["logo_types"]) == ["LogoLight", "LogoLightColor", "Primary"]
        # Result summary rendered for the operator.
        assert "4 image(s) deleted" in out
        assert "5 channel(s)" in out

    @pytest.mark.asyncio
    async def test_backend_failure_surfaces_as_error_string(self, monkeypatch):
        tool = self._get_tool()
        monkeypatch.setattr(tools_emby, "_CLEAR_LOGOS_POLL_INTERVAL_S", 0)
        client = AsyncMock()
        client.call_endpoint.return_value = {"job_id": "j", "status": "running"}
        client.get.side_effect = [
            {"job_id": "j", "status": "failed", "error": "Emby not configured"},
        ]
        monkeypatch.setattr(tools_emby, "get_ecm_client", lambda: client)

        out = await tool(logo_types=["Primary"])

        assert out.startswith("Error clearing Emby logos")
        assert "Emby not configured" in out
