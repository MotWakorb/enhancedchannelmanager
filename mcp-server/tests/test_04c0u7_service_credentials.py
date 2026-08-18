import json
from pathlib import Path
from unittest.mock import patch

from auth_claim import claim_context, request_claim_headers
from config import get_mcp_backend_credentials


def test_sidecar_reads_internal_projection_not_external_client_key(tmp_path: Path):
    (tmp_path / "settings.json").write_text(json.dumps({"mcp_api_key": "external"}))
    (tmp_path / "mcp-service.json").write_text(json.dumps({
        "backend_key": "internal", "confirmation_key": "confirm",
    }))
    with patch("config.MCP_SERVICE_FILE", tmp_path / "mcp-service.json"):
        credentials = get_mcp_backend_credentials()
    assert credentials == ("internal", "confirm")


def test_claims_bind_method_path_body_and_are_scoped_to_tool_invocation(tmp_path: Path):
    projection = tmp_path / "mcp-service.json"
    projection.write_text(json.dumps({
        "backend_key": "internal", "confirmation_key": "confirm",
    }))
    with patch("config.MCP_SERVICE_FILE", projection):
        assert request_claim_headers("POST", "/api/x", {"id": 1}) == {}
        with claim_context("delete_channel", "destructive", confirmed=True):
            headers = request_claim_headers("POST", "/api/x", {"id": 1})
    assert headers["Authorization"] == "Bearer internal"
    assert headers["X-ECM-MCP-Claim"].startswith("v1.")
    assert "internal" not in headers["X-ECM-MCP-Claim"]
