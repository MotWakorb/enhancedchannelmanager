from unittest.mock import patch

import pytest

from auth_claim import request_claim_headers
from server import mcp


class ClaimCheckingClient:
    async def call_endpoint(self, endpoint):
        headers = request_claim_headers(endpoint.method, endpoint.path)
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["X-ECM-MCP-Claim"].startswith("v1.")
        if endpoint.name == "stats_channels":
            return []
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("uri", [
    "ecm://stats/overview",
    "ecm://channels/summary",
    "ecm://tasks/status",
])
async def test_resource_backend_calls_have_read_only_service_claim(uri):
    resource = await mcp._resource_manager.get_resource(uri)
    with patch("resources.overview.get_ecm_client", return_value=ClaimCheckingClient()), patch(
        "config.get_mcp_backend_credentials", return_value=("b" * 48, "c" * 48)
    ):
        result = await resource.read()
    assert isinstance(result, str)
