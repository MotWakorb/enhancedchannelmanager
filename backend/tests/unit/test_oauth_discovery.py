"""Unit tests for the OAuth discovery posture gate + document builders (bead buiqr.5).

Covers:
  - Issuer resolution coupled to get_oauth_issuer() / OAUTH_ISSUER (AC6 contract).
  - Loopback detection (localhost / 127.0.0.0/8 / ::1).
  - Fail-closed gate: plain-HTTP non-loopback + flag false → blocked (HT1).
  - RFC 8414 / RFC 9728 document SHAPE (snapshot pin, AC3).
  - No-leak assertions: secret / internal host / path / mcp_api_key absent (ID1).
"""
import pytest

from auth import oauth_discovery as od
from auth.oauth_provider import get_oauth_issuer


# ───────────────────────────── loopback detection ────────────────────────────


class TestLoopbackDetection:
    @pytest.mark.parametrize(
        "host",
        ["localhost", "127.0.0.1", "127.0.0.5", "127.255.255.254", "::1", "[::1]"],
    )
    def test_loopback_hosts(self, host):
        assert od._is_loopback_host(host) is True

    @pytest.mark.parametrize(
        "host",
        ["192.168.1.10", "10.0.0.5", "ecm.example.com", "172.18.0.1", "", "8.8.8.8"],
    )
    def test_non_loopback_hosts(self, host):
        assert od._is_loopback_host(host) is False


# ───────────────────────────── issuer resolution ─────────────────────────────


class TestResolveIssuer:
    def test_uses_oauth_issuer_env_when_set(self, monkeypatch):
        """Issuer = OAUTH_ISSUER (the AS-minted iss source) — AC6 reconciliation."""
        monkeypatch.setenv("OAUTH_ISSUER", "https://ecm.example.com")
        # Must equal what the AS mints into iss (no drift → RS validator works).
        assert od.resolve_issuer() == "https://ecm.example.com"
        assert get_oauth_issuer() == "https://ecm.example.com"

    def test_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("OAUTH_ISSUER", "https://ecm.example.com/")
        assert od.resolve_issuer() == "https://ecm.example.com"

    def test_falls_back_to_request_host_when_unset(self, monkeypatch):
        """OAUTH_ISSUER unset (placeholder) → derive from request Host (AC6)."""
        monkeypatch.delenv("OAUTH_ISSUER", raising=False)
        issuer = od.resolve_issuer(request_host="192.168.1.50:6100", request_scheme="http")
        assert issuer == "http://192.168.1.50:6100"

    def test_placeholder_when_unset_and_no_request(self, monkeypatch):
        monkeypatch.delenv("OAUTH_ISSUER", raising=False)
        assert od.resolve_issuer() == od.PLACEHOLDER_ISSUER


# ───────────────────────────── posture gate (HT1) ────────────────────────────


class TestPostureGate:
    def test_http_non_loopback_is_insecure(self):
        assert od.issuer_is_insecure("http://192.168.1.50:6100") is True

    def test_https_is_secure(self):
        assert od.issuer_is_insecure("https://ecm.example.com") is False

    def test_http_loopback_is_secure(self):
        assert od.issuer_is_insecure("http://localhost:6100") is False
        assert od.issuer_is_insecure("http://127.0.0.1:6100") is False

    def test_blocked_when_insecure_and_flag_false(self):
        """Default fail-closed: plain-HTTP non-loopback + flag false → 404 (HT1)."""
        assert od.discovery_blocked("http://192.168.1.50:6100", allow_insecure=False) is True

    def test_not_blocked_when_opted_in(self):
        """Explicit opt-in serves over plain HTTP (ADR-009 §4)."""
        assert od.discovery_blocked("http://192.168.1.50:6100", allow_insecure=True) is False

    def test_not_blocked_when_https(self):
        assert od.discovery_blocked("https://ecm.example.com", allow_insecure=False) is False

    def test_not_blocked_when_loopback_http(self):
        assert od.discovery_blocked("http://localhost:6100", allow_insecure=False) is False


# ─────────────────────────── document shape (AC3) ────────────────────────────


class TestAuthorizationServerMetadata:
    ISSUER = "https://ecm.example.com"

    def test_exact_shape(self):
        """RFC 8414 document shape pinned (AC3 snapshot)."""
        doc = od.build_authorization_server_metadata(self.ISSUER)
        assert doc == {
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

    def test_issuer_matches_token_iss(self, monkeypatch):
        """The discovery issuer MUST equal the AS-minted iss (buiqr.8 contract)."""
        monkeypatch.setenv("OAUTH_ISSUER", "https://ecm.example.com")
        doc = od.build_authorization_server_metadata(od.resolve_issuer())
        assert doc["issuer"] == get_oauth_issuer()


class TestProtectedResourceMetadata:
    def test_exact_shape(self):
        """RFC 9728 document shape pinned (AC3 snapshot)."""
        doc = od.build_protected_resource_metadata(
            issuer="https://ecm.example.com",
            resource="https://mcp.example.com",
        )
        assert doc == {
            "resource": "https://mcp.example.com",
            "authorization_servers": ["https://ecm.example.com"],
            "scopes_supported": ["mcp"],
            "bearer_methods_supported": ["header"],
        }

    def test_authorization_servers_points_at_as(self):
        doc = od.build_protected_resource_metadata(
            issuer="https://ecm.example.com", resource="https://mcp.example.com"
        )
        assert doc["authorization_servers"] == ["https://ecm.example.com"]


# ───────────────────────────── no-leak (ID1) ─────────────────────────────────


class TestNoLeak:
    """Threat model ID1: discovery docs must not leak secrets/internal hosts/paths."""

    SECRET = "super-secret-signing-value-do-not-leak"
    API_KEY = "mcp-api-key-do-not-leak"
    INTERNAL_HOST = "ecm:6100"

    def _all_strings(self, doc):
        import json

        return json.dumps(doc)

    def test_as_doc_has_no_secret_or_internal_host_or_path(self):
        doc = od.build_authorization_server_metadata("https://ecm.example.com")
        blob = self._all_strings(doc)
        assert self.SECRET not in blob
        assert self.API_KEY not in blob
        assert self.INTERNAL_HOST not in blob
        assert "/config/" not in blob
        assert "mcp_oauth_signing_secret" not in blob
        assert "mcp_api_key" not in blob

    def test_rs_doc_has_no_secret_or_internal_host_or_path(self):
        doc = od.build_protected_resource_metadata(
            issuer="https://ecm.example.com", resource="https://mcp.example.com"
        )
        blob = self._all_strings(doc)
        assert self.SECRET not in blob
        assert self.API_KEY not in blob
        assert self.INTERNAL_HOST not in blob
        assert "/config/" not in blob
        assert "mcp_oauth_signing_secret" not in blob
        assert "mcp_api_key" not in blob

    def test_as_doc_field_allowlist(self):
        """Only the protocol-required field keys are present (ID1 — no extras)."""
        doc = od.build_authorization_server_metadata("https://ecm.example.com")
        assert set(doc.keys()) == {
            "issuer",
            "authorization_endpoint",
            "token_endpoint",
            "revocation_endpoint",
            "response_types_supported",
            "grant_types_supported",
            "code_challenge_methods_supported",
            "scopes_supported",
            "token_endpoint_auth_methods_supported",
        }

    def test_rs_doc_field_allowlist(self):
        doc = od.build_protected_resource_metadata(
            issuer="https://ecm.example.com", resource="https://mcp.example.com"
        )
        assert set(doc.keys()) == {
            "resource",
            "authorization_servers",
            "scopes_supported",
            "bearer_methods_supported",
        }
