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

    # ---- bd-buiqr10 Option-A slice: signing_key_missing diagnostic ----

    def test_health_reports_signing_key_ok_when_present(self, client):
        """/health includes signing_key_status='ok' when the HS256 signing secret
        is present in settings.json (bd-buiqr10).

        The signing secret exists → OAuth Bearer-JWT verification is possible.
        """
        with patch("server.get_mcp_api_key_status", return_value=("real-key", "ok")), \
             patch("server.get_signing_key_status", return_value=("", "ok")):
            response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["signing_key_status"] == "ok"
        # No signing_key_hint when the secret is wired correctly.
        assert "signing_key_hint" not in data

    def test_health_reports_signing_key_missing_when_absent(self, client):
        """/health reports signing_key_status='signing_key_missing' when the HS256
        secret field is absent from settings.json (bd-buiqr10).

        This is the diagnostic an operator sees when OAuth Bearer-JWT auth is not
        yet wired: the MCP RS cannot verify tokens offline without the secret.
        """
        with patch("server.get_mcp_api_key_status", return_value=("real-key", "ok")), \
             patch("server.get_signing_key_status", return_value=("", "signing_key_missing")):
            response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["signing_key_status"] == "signing_key_missing"
        # A setup hint must guide the operator to remediation.
        assert "signing_key_hint" in data
        hint = data["signing_key_hint"].lower()
        assert "signing" in hint or "oauth" in hint or "secret" in hint

    def test_health_signing_key_status_does_not_expose_secret(self, client):
        """SECURITY: /health response body never contains the HS256 signing secret.

        This is a direct test of threat model ID1 — the /health endpoint is
        effectively public and must not leak the secret value, even in the 'ok'
        branch (bd-buiqr10).
        """
        secret = "super-secret-signing-value-must-not-appear"
        with patch("server.get_mcp_api_key_status", return_value=("real-key", "ok")), \
             patch("server.get_signing_key_status", return_value=("", "ok")):
            response = client.get("/health")
        assert response.status_code == 200
        assert secret not in response.text

    def test_health_signing_key_missing_hint_is_distinct(self, client):
        """/health signing_key_hint differs from api_key setup_hint strings.

        Each failure mode should have a distinct, operator-targeted hint so
        the operator can diagnose the specific problem (bd-buiqr10).
        """
        with patch("server.get_mcp_api_key_status", return_value=("", "field_missing")), \
             patch("server.get_signing_key_status", return_value=("", "signing_key_missing")):
            response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        # Both hints must be present
        assert "setup_hint" in data
        assert "signing_key_hint" in data
        # They must be distinct strings
        assert data["setup_hint"] != data["signing_key_hint"]

    def test_health_existing_api_key_statuses_unaffected(self, client):
        """Existing api_key_status codes are unaffected by the signing_key addition.

        No regression on bd-ix1g6's contract (bd-buiqr10 is additive only).
        """
        for status in ("file_not_found", "invalid_json", "field_missing", "field_empty"):
            with patch("server.get_mcp_api_key_status", return_value=("", status)), \
                 patch("server.get_signing_key_status", return_value=("", "ok")):
                response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["api_key_status"] == status, f"Expected api_key_status={status}"
            # signing_key_status is always present alongside api_key_status
            assert "signing_key_status" in data, f"signing_key_status missing for api_key_status={status}"


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


class TestProtectedResourceDiscovery:
    """GET /.well-known/oauth-protected-resource (RFC 9728, bead buiqr.5).

    Exercised through the Starlette app + APIKeyAuthMiddleware:
      AC2 — RFC 9728 fields incl. authorization_servers pointing at the AS.
      AC3 — exact JSON snapshot pinned over HTTP.
      AC4 — oauth_allow_insecure=false + http non-loopback issuer → 404.
      AC5 — oauth_allow_insecure=true → 200 over plain HTTP.
      Public — reachable WITHOUT the MCP API key (exempt from the middleware).
      ID1 — no secret / internal host / path / mcp_api_key in the body.
    """

    _PATH = "/.well-known/oauth-protected-resource"

    def test_public_no_api_key_required(self, client):
        """Discovery is public — the API-key middleware must not gate it."""
        with patch("server.resolve_issuer", return_value="https://ecm.example.com"), \
             patch("server.get_oauth_allow_insecure", return_value=False), \
             patch("server.resolve_resource_url", return_value="https://mcp.example.com"):
            # No api_key / Authorization header at all.
            response = client.get(self._PATH)
        assert response.status_code == 200

    def test_returns_rfc9728_fields(self, client):
        with patch("server.resolve_issuer", return_value="https://ecm.example.com"), \
             patch("server.get_oauth_allow_insecure", return_value=False), \
             patch("server.resolve_resource_url", return_value="https://mcp.example.com"):
            response = client.get(self._PATH)
        assert response.status_code == 200
        data = response.json()
        assert data["resource"] == "https://mcp.example.com"
        assert data["authorization_servers"] == ["https://ecm.example.com"]
        assert data["scopes_supported"] == ["mcp"]

    def test_exact_snapshot(self, client):
        """AC3 — pin the exact JSON shape served over HTTP."""
        with patch("server.resolve_issuer", return_value="https://ecm.example.com"), \
             patch("server.get_oauth_allow_insecure", return_value=False), \
             patch("server.resolve_resource_url", return_value="https://mcp.example.com"):
            response = client.get(self._PATH)
        assert response.json() == {
            "resource": "https://mcp.example.com",
            "authorization_servers": ["https://ecm.example.com"],
            "scopes_supported": ["mcp"],
            "bearer_methods_supported": ["header"],
        }

    def test_http_non_loopback_flag_false_returns_404(self, client):
        """AC4 — plain-HTTP non-loopback issuer + flag false → 404 (fail-closed)."""
        with patch("server.resolve_issuer", return_value="http://192.168.1.50:6101"), \
             patch("server.get_oauth_allow_insecure", return_value=False):
            response = client.get(self._PATH)
        assert response.status_code == 404

    def test_http_non_loopback_flag_true_returns_200(self, client):
        """AC5 — opt-in serves over plain HTTP."""
        with patch("server.resolve_issuer", return_value="http://192.168.1.50:6101"), \
             patch("server.get_oauth_allow_insecure", return_value=True), \
             patch("server.resolve_resource_url", return_value="http://192.168.1.50:6101"):
            response = client.get(self._PATH)
        assert response.status_code == 200
        assert response.json()["authorization_servers"] == ["http://192.168.1.50:6101"]

    def test_404_body_is_generic(self, client):
        """The fail-closed 404 reveals nothing about the gate (HT1/ID1)."""
        with patch("server.resolve_issuer", return_value="http://192.168.1.50:6101"), \
             patch("server.get_oauth_allow_insecure", return_value=False):
            response = client.get(self._PATH)
        assert response.status_code == 404
        blob = response.text.lower()
        assert "oauth_allow_insecure" not in blob
        assert "insecure" not in blob

    def test_no_secret_or_internal_host_leak(self, client):
        """ID1 — discovery body never contains the secret / internal host / api key."""
        with patch("server.resolve_issuer", return_value="https://ecm.example.com"), \
             patch("server.get_oauth_allow_insecure", return_value=False), \
             patch("server.resolve_resource_url", return_value="https://mcp.example.com"):
            response = client.get(self._PATH)
        blob = response.text
        assert "ecm:6100" not in blob
        assert "/config/" not in blob
        assert "mcp_oauth_signing_secret" not in blob
        assert "mcp_api_key" not in blob


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
