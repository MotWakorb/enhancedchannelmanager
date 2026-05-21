"""Tests for the MCP RS OAuth discovery helper (bead buiqr.5).

Covers the protected-resource posture gate + document shape on the MCP side:
  - Loopback detection + insecure-posture gate (HT1).
  - RFC 9728 document shape (AC3 snapshot) pointing at the ECM AS issuer.
  - No secret / internal host / path / mcp_api_key leakage (ID1).
"""
import importlib

import pytest


def _fresh_module(monkeypatch, *, oauth_issuer=None):
    """Reload oauth_discovery with a pinned OAUTH_ISSUER env (module-level read)."""
    import config

    if oauth_issuer is None:
        monkeypatch.delenv("OAUTH_ISSUER", raising=False)
    else:
        monkeypatch.setenv("OAUTH_ISSUER", oauth_issuer)
    importlib.reload(config)
    import oauth_discovery

    importlib.reload(oauth_discovery)
    return oauth_discovery


class TestLoopbackDetection:
    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "127.5.5.5", "::1", "[::1]"])
    def test_loopback(self, host, monkeypatch):
        od = _fresh_module(monkeypatch)
        assert od._is_loopback_host(host) is True

    @pytest.mark.parametrize("host", ["192.168.1.10", "ecm.example.com", "", "10.0.0.1"])
    def test_non_loopback(self, host, monkeypatch):
        od = _fresh_module(monkeypatch)
        assert od._is_loopback_host(host) is False


class TestPostureGate:
    def test_http_non_loopback_insecure(self, monkeypatch):
        od = _fresh_module(monkeypatch)
        assert od.issuer_is_insecure("http://192.168.1.50:6100") is True

    def test_https_secure(self, monkeypatch):
        od = _fresh_module(monkeypatch)
        assert od.issuer_is_insecure("https://ecm.example.com") is False

    def test_http_loopback_secure(self, monkeypatch):
        od = _fresh_module(monkeypatch)
        assert od.issuer_is_insecure("http://localhost:6100") is False

    def test_blocked_when_insecure_and_flag_false(self, monkeypatch):
        od = _fresh_module(monkeypatch)
        assert od.discovery_blocked("http://192.168.1.50:6100", allow_insecure=False) is True

    def test_not_blocked_when_opted_in(self, monkeypatch):
        od = _fresh_module(monkeypatch)
        assert od.discovery_blocked("http://192.168.1.50:6100", allow_insecure=True) is False

    def test_not_blocked_when_https(self, monkeypatch):
        od = _fresh_module(monkeypatch)
        assert od.discovery_blocked("https://ecm.example.com", allow_insecure=False) is False


class TestResolveIssuer:
    def test_uses_oauth_issuer_env(self, monkeypatch):
        od = _fresh_module(monkeypatch, oauth_issuer="https://ecm.example.com")
        assert od.resolve_issuer() == "https://ecm.example.com"

    def test_falls_back_to_request_when_unset(self, monkeypatch):
        od = _fresh_module(monkeypatch)
        assert (
            od.resolve_issuer(request_host="192.168.1.50:6100", request_scheme="http")
            == "http://192.168.1.50:6100"
        )


class TestResolveResourceUrl:
    def test_uses_configured(self, monkeypatch):
        od = _fresh_module(monkeypatch)
        assert od.resolve_resource_url("https://mcp.example.com/") == "https://mcp.example.com"

    def test_falls_back_to_request(self, monkeypatch):
        od = _fresh_module(monkeypatch)
        assert (
            od.resolve_resource_url("", request_host="mcp.local:6101", request_scheme="https")
            == "https://mcp.local:6101"
        )


class TestProtectedResourceMetadata:
    def test_exact_shape(self, monkeypatch):
        """AC3 — pin the RFC 9728 document shape."""
        od = _fresh_module(monkeypatch)
        doc = od.build_protected_resource_metadata(
            issuer="https://ecm.example.com", resource="https://mcp.example.com"
        )
        assert doc == {
            "resource": "https://mcp.example.com",
            "authorization_servers": ["https://ecm.example.com"],
            "scopes_supported": ["mcp"],
            "bearer_methods_supported": ["header"],
        }

    def test_authorization_servers_points_at_as(self, monkeypatch):
        od = _fresh_module(monkeypatch)
        doc = od.build_protected_resource_metadata(
            issuer="https://ecm.example.com", resource="https://mcp.example.com"
        )
        assert doc["authorization_servers"] == ["https://ecm.example.com"]


class TestNoLeak:
    """Threat model ID1 — no secret / internal host / path / mcp_api_key."""

    def test_doc_field_allowlist(self, monkeypatch):
        od = _fresh_module(monkeypatch)
        doc = od.build_protected_resource_metadata(
            issuer="https://ecm.example.com", resource="https://mcp.example.com"
        )
        assert set(doc.keys()) == {
            "resource",
            "authorization_servers",
            "scopes_supported",
            "bearer_methods_supported",
        }

    def test_no_internal_host_or_path(self, monkeypatch):
        import json

        od = _fresh_module(monkeypatch)
        doc = od.build_protected_resource_metadata(
            issuer="https://ecm.example.com", resource="https://mcp.example.com"
        )
        blob = json.dumps(doc)
        assert "ecm:6100" not in blob
        assert "/config/" not in blob
        assert "mcp_oauth_signing_secret" not in blob
        assert "mcp_api_key" not in blob
