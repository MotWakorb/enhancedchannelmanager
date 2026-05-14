"""Tests for MCP server endpoints and auth middleware (Streamable HTTP transport)."""
import json

import pytest
from unittest.mock import patch
from starlette.testclient import TestClient

from server import app

# Headers a Streamable HTTP client must send on the POST to /mcp.
_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


@pytest.fixture(scope="module")
def client():
    # The Starlette lifespan starts the StreamableHTTP session manager. That
    # session manager can only be run once per process, so the client (and its
    # lifespan) is shared across the whole module.
    with TestClient(app) as c:
        yield c


def _parse_initialize_result(response):
    """Extract the JSON-RPC result from a /mcp response (SSE or plain JSON)."""
    ctype = response.headers.get("content-type", "")
    body = response.text
    if "text/event-stream" in ctype:
        for line in body.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise AssertionError(f"no SSE data frame in response: {body!r}")
    return json.loads(body)


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_ok(self, client):
        """Health endpoint returns status ok."""
        with patch(
            "server.get_mcp_api_key_status", return_value=("some-key", "ok")
        ):
            response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["server"] == "ecm-mcp"
        assert data["transport"] == "streamable-http"
        assert data["api_key_configured"] is True
        assert data["tools_available"] > 0
        assert data["resources_available"] > 0

    def test_health_shows_unconfigured(self, client):
        """Health shows api_key_configured=false when no key."""
        with patch(
            "server.get_mcp_api_key_status", return_value=("", "field_empty")
        ):
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["api_key_configured"] is False

    def test_health_no_auth_required(self, client):
        """Health endpoint works without API key."""
        with patch(
            "server.get_mcp_api_key_status", return_value=("secret-key", "ok")
        ):
            response = client.get("/health")
        assert response.status_code == 200

    def test_health_reports_status_ok_when_key_present(self, client):
        """/health reports api_key_status='ok' when a key is configured.

        Self-diagnosing /health (bd-ix1g6): operators reporting
        api_key_configured=false now get a machine-readable reason without
        needing container shell access. This test pins the contract.
        """
        with patch("server.get_mcp_api_key_status", return_value=("real-key", "ok")):
            response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["api_key_configured"] is True
        assert data["api_key_status"] == "ok"
        # No setup_hint when everything is wired correctly.
        assert "setup_hint" not in data

    def test_health_reports_file_not_found(self, client):
        """/health reports api_key_status='file_not_found' when settings.json
        is missing on the mounted volume — most common deployment misconfig."""
        with patch(
            "server.get_mcp_api_key_status", return_value=("", "file_not_found")
        ):
            response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["api_key_configured"] is False
        assert data["api_key_status"] == "file_not_found"
        # Setup hint is tailored to the specific failure mode.
        assert "setup_hint" in data
        assert "settings.json" in data["setup_hint"].lower() or "volume" in data["setup_hint"].lower()

    def test_health_reports_invalid_json(self, client):
        """/health reports api_key_status='invalid_json' when settings.json
        exists but is corrupted."""
        with patch(
            "server.get_mcp_api_key_status", return_value=("", "invalid_json")
        ):
            response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["api_key_configured"] is False
        assert data["api_key_status"] == "invalid_json"

    def test_health_reports_field_missing(self, client):
        """/health reports api_key_status='field_missing' when settings.json
        is valid JSON but does not include the mcp_api_key field (legacy file
        from before the MCP feature shipped)."""
        with patch(
            "server.get_mcp_api_key_status", return_value=("", "field_missing")
        ):
            response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["api_key_configured"] is False
        assert data["api_key_status"] == "field_missing"

    def test_health_reports_field_empty(self, client):
        """/health reports api_key_status='field_empty' when the field exists
        but is blank — i.e. no key has been generated yet (or it was revoked)."""
        with patch(
            "server.get_mcp_api_key_status", return_value=("", "field_empty")
        ):
            response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["api_key_configured"] is False
        assert data["api_key_status"] == "field_empty"
        # In this case the setup hint should point the user to Settings.
        assert "setup_hint" in data
        assert "settings" in data["setup_hint"].lower()


class TestMCPAuth:
    """Tests for API key authentication on the /mcp Streamable HTTP endpoint."""

    def test_mcp_rejects_no_key(self, client):
        """/mcp rejects requests with no API key."""
        with patch("server.get_mcp_api_key", return_value="valid-key"):
            response = client.post("/mcp", headers=_MCP_HEADERS, json=_INITIALIZE)
        assert response.status_code == 401

    def test_mcp_rejects_wrong_key(self, client):
        """/mcp rejects an invalid API key."""
        with patch("server.get_mcp_api_key", return_value="valid-key"):
            response = client.post(
                "/mcp?api_key=wrong-key", headers=_MCP_HEADERS, json=_INITIALIZE
            )
        assert response.status_code == 401

    def test_mcp_rejects_when_not_configured(self, client):
        """/mcp returns 503 when no API key is configured."""
        with patch("server.get_mcp_api_key", return_value=""):
            response = client.post("/mcp", headers=_MCP_HEADERS, json=_INITIALIZE)
        assert response.status_code == 503
        assert "not configured" in response.json()["error"].lower()

    def test_mcp_get_requires_key(self, client):
        """A bare GET /mcp (event stream) is also auth-checked."""
        with patch("server.get_mcp_api_key", return_value="valid-key"):
            response = client.get("/mcp")
        assert response.status_code == 401


class TestMCPInitialize:
    """End-to-end MCP initialize round-trip over Streamable HTTP."""

    def test_initialize_with_query_param_key(self, client):
        with patch("server.get_mcp_api_key", return_value="valid-key"):
            response = client.post(
                "/mcp?api_key=valid-key", headers=_MCP_HEADERS, json=_INITIALIZE
            )
        assert response.status_code == 200
        assert "mcp-session-id" in {k.lower() for k in response.headers}
        result = _parse_initialize_result(response)
        assert result["id"] == 1
        assert result["result"]["serverInfo"]["name"] == "ecm-mcp"

    def test_initialize_with_bearer_header_key(self, client):
        headers = {**_MCP_HEADERS, "Authorization": "Bearer valid-key"}
        with patch("server.get_mcp_api_key", return_value="valid-key"):
            response = client.post("/mcp", headers=headers, json=_INITIALIZE)
        assert response.status_code == 200
        assert "mcp-session-id" in {k.lower() for k in response.headers}
        result = _parse_initialize_result(response)
        assert result["id"] == 1
        assert result["result"]["serverInfo"]["name"] == "ecm-mcp"
