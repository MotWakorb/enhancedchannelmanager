"""Explicit SDK-to-backend validation; intentionally outside automatic discovery.

Run with backend pytest/conftest and ECM_MCP_VALIDATION_PYTHON naming an
interpreter matching mcp-server/requirements.txt. No production is contacted.
"""

import os

import pytest
from tests.integration import (
    test_mcp_pipeline_authenticated_execution as integration,
)

# Reuse the same disposable fixtures, without altering automatic collection.
backend_http = integration.backend_http
mcp_auth = integration.mcp_auth
planning_upstream = integration.planning_upstream


@pytest.mark.asyncio
async def test_registered_tool_confirmation_and_execution_over_real_backend_http(
    backend_http, planning_upstream, mcp_auth, tmp_path
):
    interpreter = os.environ["ECM_MCP_VALIDATION_PYTHON"]
    script = """
import asyncio
import json
import re
import ecm_client
from mcp.server.fastmcp import FastMCP
from tools import register_all_tools

async def main():
    registry = FastMCP("gh1033-real-http")
    register_all_tools(registry)
    try:
        preview = await registry.call_tool("run_channel_pipeline", {"dry_run": False})
        text = preview[0][0].text
        match = re.search(r"confirmation_token: ([^\\s]+)", text)
        assert match, text
        result = await registry.call_tool("run_channel_pipeline", {
            "dry_run": False, "confirmation_token": match[1],
        })
        text = result[0][0].text
        assert "complete" in text.lower(), text
        assert "Error running" not in text, text
        print(json.dumps({"result": text, "confirmation_required": True}))
    finally:
        if ecm_client._client is not None:
            await ecm_client._client.aclose()

asyncio.run(main())
"""
    result = await integration.run_sidecar_process(
        interpreter, script, backend_http, tmp_path
    )
    assert result["confirmation_required"] is True
    assert planning_upstream.channels[7]["name"] == "After"
    assert planning_upstream.calls == [("PATCH", "/api/channels/channels/7/")]
