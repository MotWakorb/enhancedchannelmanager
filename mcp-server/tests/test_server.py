"""Tests for MCP server endpoints and auth middleware."""
import pytest
from unittest.mock import patch
from starlette.testclient import TestClient

from server import app


@pytest.fixture
def client():
    return TestClient(app)


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_ok(self, client):
        """Health endpoint returns status ok."""
        with patch("server.get_mcp_api_key", return_value="some-key"):
            response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["server"] == "ecm-mcp"
        assert data["api_key_configured"] is True

    def test_health_shows_unconfigured(self, client):
        """Health shows api_key_configured=false when no key."""
        with patch("server.get_mcp_api_key", return_value=""):
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["api_key_configured"] is False

    def test_health_no_auth_required(self, client):
        """Health endpoint works without API key."""
        with patch("server.get_mcp_api_key", return_value="secret-key"):
            response = client.get("/health")
        assert response.status_code == 200


class TestSSEAuth:
    """Tests for API key authentication on SSE endpoint."""

    def test_sse_rejects_no_key(self, client):
        """SSE endpoint rejects when no API key provided."""
        with patch("server.get_mcp_api_key", return_value="valid-key"):
            response = client.get("/sse")
        assert response.status_code == 401

    def test_sse_rejects_wrong_key(self, client):
        """SSE endpoint rejects invalid API key."""
        with patch("server.get_mcp_api_key", return_value="valid-key"):
            response = client.get("/sse?api_key=wrong-key")
        assert response.status_code == 401

    def test_sse_rejects_when_not_configured(self, client):
        """SSE returns 503 when no API key is configured."""
        with patch("server.get_mcp_api_key", return_value=""):
            response = client.get("/sse")
        assert response.status_code == 503
        assert "not configured" in response.json()["error"].lower()

    def test_messages_exempt_from_key_check(self, client):
        """Messages endpoint is session-bound, not key-checked (auth was on /sse)."""
        with patch("server.get_mcp_api_key", return_value="valid-key"):
            response = client.post("/messages/")
        # Should not be 401 — messages are exempt from API key auth
        # (may be 400/422 due to missing session_id, but not 401)
        assert response.status_code != 401
