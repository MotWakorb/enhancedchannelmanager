"""GH #1033: confirmation-preview requests need the sidecar's signed authority."""

from unittest.mock import patch

import httpx
import pytest
from auth_claim import SidecarBackendAuth
from mcp.server.fastmcp import FastMCP
from tools import register_all_tools


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["run_channel_pipeline", "run_auto_creation"])
async def test_mutation_free_pipeline_prepare_is_signed_before_confirmation(tool_name):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.path == "/api/channel-pipeline/run/prepare"
        assert request.headers.get("Authorization") == "Bearer synthetic-private-gh1033"
        assert request.headers.get("X-ECM-MCP-Claim", "").startswith("v1.")
        return httpx.Response(
            200,
            json={
                "phase": "execute",
                "plan_id": "prepared",
                "plan_hash": "bound-hash",
                "preview": {"channels_updated": 1},
                "write_count": 1,
                "unique_target_count": 1,
            },
        )

    registry = FastMCP("pipeline-prepare-signing")
    register_all_tools(registry)
    async with httpx.AsyncClient(
        base_url="http://synthetic-backend.invalid",
        transport=httpx.MockTransport(handle),
        auth=SidecarBackendAuth(),
    ) as client:
        with (
            patch("ecm_client._get_client", return_value=client),
            patch(
                "config.get_mcp_backend_credentials",
                return_value=(
                    "synthetic-private-gh1033",
                    "synthetic-confirmation-gh1033",
                ),
            ),
        ):
            preview = await registry.call_tool(tool_name, {"dry_run": False})
    assert "confirmation_token:" in preview[0][0].text
    assert len(calls) == 1, "preparing must not commit or run a legacy mutation"
