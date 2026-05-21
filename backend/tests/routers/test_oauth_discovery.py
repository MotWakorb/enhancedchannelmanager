"""Router-level tests for the ECM AS discovery endpoint (bead buiqr.5).

Exercises GET /.well-known/oauth-authorization-server end-to-end through the
FastAPI app:

  AC1 — RFC 8414 fields present.
  AC3 — exact JSON snapshot pinned over HTTP.
  AC4 — oauth_allow_insecure=false + http non-loopback issuer → 404.
  AC5 — oauth_allow_insecure=true → 200 over plain HTTP.
  AC6 — issuer reflects OAUTH_ISSUER (the AS-minted iss source).
  ID1 — no secret / internal host / path / mcp_api_key in the body.
"""
from unittest.mock import patch

import pytest

from config import DispatcharrSettings

_WELL_KNOWN = "/.well-known/oauth-authorization-server"


def _settings(allow_insecure=False):
    return DispatcharrSettings(oauth_allow_insecure=allow_insecure)


@pytest.mark.asyncio
class TestDiscoverySecureIssuer:
    """HTTPS issuer — endpoint serves regardless of the flag."""

    async def test_returns_rfc8414_fields(self, async_client, monkeypatch):
        monkeypatch.setenv("OAUTH_ISSUER", "https://ecm.example.com")
        with patch("routers.oauth_discovery.get_settings", return_value=_settings(False)):
            resp = await async_client.get(_WELL_KNOWN)
        assert resp.status_code == 200
        body = resp.json()
        assert body["issuer"] == "https://ecm.example.com"
        assert body["authorization_endpoint"] == "https://ecm.example.com/api/oauth/authorize"
        assert body["token_endpoint"] == "https://ecm.example.com/api/oauth/token"
        assert body["response_types_supported"] == ["code"]
        assert body["code_challenge_methods_supported"] == ["S256"]
        assert body["scopes_supported"] == ["mcp"]
        assert body["token_endpoint_auth_methods_supported"] == ["none"]

    async def test_exact_snapshot(self, async_client, monkeypatch):
        """AC3 — pin the exact JSON shape served over HTTP."""
        monkeypatch.setenv("OAUTH_ISSUER", "https://ecm.example.com")
        with patch("routers.oauth_discovery.get_settings", return_value=_settings(False)):
            resp = await async_client.get(_WELL_KNOWN)
        assert resp.status_code == 200
        assert resp.json() == {
            "issuer": "https://ecm.example.com",
            "authorization_endpoint": "https://ecm.example.com/api/oauth/authorize",
            "token_endpoint": "https://ecm.example.com/api/oauth/token",
            "revocation_endpoint": "https://ecm.example.com/api/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": ["mcp"],
            "token_endpoint_auth_methods_supported": ["none"],
        }

    async def test_issuer_reflects_oauth_issuer_env(self, async_client, monkeypatch):
        """AC6 — issuer is deployment-specific (OAUTH_ISSUER), no cross-deploy leak."""
        monkeypatch.setenv("OAUTH_ISSUER", "https://deployment-a.example.com")
        with patch("routers.oauth_discovery.get_settings", return_value=_settings(False)):
            resp = await async_client.get(_WELL_KNOWN)
        assert resp.json()["issuer"] == "https://deployment-a.example.com"


@pytest.mark.asyncio
class TestDiscoveryInsecurePostureGate:
    """The oauth_allow_insecure fail-closed matrix (AC4/AC5, threat model HT1)."""

    async def test_http_non_loopback_flag_false_returns_404(self, async_client, monkeypatch):
        """AC4 — plain-HTTP non-loopback issuer + flag false → 404 (fail-closed)."""
        monkeypatch.setenv("OAUTH_ISSUER", "http://192.168.1.50:6100")
        with patch("routers.oauth_discovery.get_settings", return_value=_settings(False)):
            resp = await async_client.get(_WELL_KNOWN)
        assert resp.status_code == 404

    async def test_http_non_loopback_flag_true_returns_200(self, async_client, monkeypatch):
        """AC5 — opt-in serves over plain HTTP."""
        monkeypatch.setenv("OAUTH_ISSUER", "http://192.168.1.50:6100")
        with patch("routers.oauth_discovery.get_settings", return_value=_settings(True)):
            resp = await async_client.get(_WELL_KNOWN)
        assert resp.status_code == 200
        assert resp.json()["issuer"] == "http://192.168.1.50:6100"

    async def test_http_loopback_flag_false_returns_200(self, async_client, monkeypatch):
        """Loopback HTTP is always allowed (dev/same-host posture)."""
        monkeypatch.setenv("OAUTH_ISSUER", "http://localhost:6100")
        with patch("routers.oauth_discovery.get_settings", return_value=_settings(False)):
            resp = await async_client.get(_WELL_KNOWN)
        assert resp.status_code == 200

    async def test_404_body_is_generic(self, async_client, monkeypatch):
        """The fail-closed 404 reveals nothing about the gate (HT1/ID1)."""
        monkeypatch.setenv("OAUTH_ISSUER", "http://192.168.1.50:6100")
        with patch("routers.oauth_discovery.get_settings", return_value=_settings(False)):
            resp = await async_client.get(_WELL_KNOWN)
        assert resp.status_code == 404
        blob = resp.text.lower()
        assert "oauth_allow_insecure" not in blob
        assert "fail" not in blob
        assert "insecure" not in blob


@pytest.mark.asyncio
class TestDiscoveryNoLeak:
    """Threat model ID1 — no secret / internal host / path / mcp_api_key leaks."""

    async def test_no_secret_or_internal_host_or_apikey(self, async_client, monkeypatch):
        monkeypatch.setenv("OAUTH_ISSUER", "https://ecm.example.com")
        # Populate credential-class settings so a leak would actually show up.
        leaky = DispatcharrSettings(
            oauth_allow_insecure=False,
            mcp_oauth_signing_secret="SECRET-SIGNING-VALUE-LEAK-CANARY",
            mcp_api_key="MCP-API-KEY-LEAK-CANARY",
        )
        with patch("routers.oauth_discovery.get_settings", return_value=leaky):
            resp = await async_client.get(_WELL_KNOWN)
        assert resp.status_code == 200
        blob = resp.text
        assert "SECRET-SIGNING-VALUE-LEAK-CANARY" not in blob
        assert "MCP-API-KEY-LEAK-CANARY" not in blob
        assert "ecm:6100" not in blob
        assert "/config/" not in blob
        assert "mcp_oauth_signing_secret" not in blob
        assert "mcp_api_key" not in blob


@pytest.mark.asyncio
class TestDiscoveryPublic:
    """The discovery path is public — reachable without auth (RFC 8414)."""

    async def test_path_in_auth_exempt_paths(self):
        from main import AUTH_EXEMPT_PATHS

        assert "/.well-known/oauth-authorization-server" in AUTH_EXEMPT_PATHS
        assert "/.well-known/oauth-protected-resource" in AUTH_EXEMPT_PATHS
