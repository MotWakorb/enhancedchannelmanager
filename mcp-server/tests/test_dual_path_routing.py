"""Dual-path-by-shape auth routing tests for the MCP RS (bd-buiqr.8).

THE HEADLINE THREAT (threat model CD1): a JWT-shaped-but-invalid Bearer token
must NEVER fall through to the static-key check. Routing is by credential SHAPE,
decided before any validation; a failed OAuth validation returns 401 and is
never re-evaluated as a static key.

These exercise the full Starlette app (``server.app``) + the dual-path
middleware over HTTP (TestClient), mirroring ``test_server.py``'s style.

Coverage map:
  - CD1  — invalid JWT not evaluated as static key; no fail-cascade.
  - SP2  — alg:none / RS256 alg-confusion → 401.
  - SP6  — static-key value shaped as a JWT never enters OAuth path, and a JWT
           never enters the static-key path.
  - EP1  — wrong aud / iss / expired / scope → 401.
  - EP2  — static-key path behavior unchanged (regression).
  - AC5  — auth_method=oauth vs auth_method=static_key logged on success.
"""
import base64
import json
import time

from unittest.mock import patch

import pytest

# MCP OAuth offering RETIRED (bd-9axgc). The OAuth-path classes below
# (TestNoFailCascade, TestOAuthHappyPath, TestBothCredentialsPresent, and the
# discovery/signing-key parts of TestExemptPathsUnaffected) exercise the
# now-disabled OAuth Bearer-JWT auth path and the removed RFC 9728 discovery
# endpoint, and patch symbols (get_signing_key, get_oauth_issuer_for_rs,
# get_signing_key_status, resolve_issuer, …) that were removed from server.py.
# They are skipped. TestStaticKeyRegression stays ACTIVE — it is the guardrail
# proof that the supported static ?api_key= path still works (guardrail #1).
_OAUTH_RETIRED = pytest.mark.skip(
    reason="MCP OAuth offering retired (bd-9axgc); OAuth Bearer path + RFC 9728 discovery removed from server.py. Re-enable when MCP OAuth is re-offered."
)

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

_SECRET = "test-oauth-signing-secret-at-least-32-bytes-long-padding"
_ISSUER = "https://ecm.example.com"
_AUDIENCE = "ecm-mcp"
_STATIC_KEY = "static-key-value-no-dots-1234567890"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _mint(claims: dict, *, secret: str = _SECRET, alg: str = "HS256") -> str:
    import jwt as pyjwt

    return pyjwt.encode(claims, secret, algorithm=alg)


def _base_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": "admin",
        "aud": _AUDIENCE,
        "iss": _ISSUER,
        "scope": "mcp",
        "jti": "jti-test",
        "iat": now,
        "exp": now + 900,
        "token_type": "access",
    }
    claims.update(overrides)
    return claims


def _parse_initialize_result(response):
    ctype = response.headers.get("content-type", "")
    body = response.text
    if "text/event-stream" in ctype:
        for line in body.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise AssertionError(f"no SSE data frame: {body!r}")
    return json.loads(body)


# A spy that asserts the static-key value is NEVER read once we are on the
# OAuth path. ``get_mcp_api_key`` is the static-key reader; if the OAuth path
# fell through to static-key validation, this would be called.
class _StaticKeySpy:
    def __init__(self, value=_STATIC_KEY):
        self.value = value
        self.called = False

    def __call__(self):
        self.called = True
        return self.value


# ───────────────── CD1: no fail-cascade (the headline) ───────────────────────


@_OAUTH_RETIRED
class TestNoFailCascade:
    """A JWT-shaped Bearer that fails OAuth validation returns 401 and is
    NEVER compared against the static key (threat model CD1)."""

    def _assert_401_and_static_key_untouched(self, client, token):
        spy = _StaticKeySpy()
        with patch("server.get_signing_key", return_value=_SECRET), \
             patch("server.get_oauth_issuer_for_rs", return_value=_ISSUER), \
             patch("server.get_mcp_api_key", new=spy):
            response = client.post(
                "/mcp",
                headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
                json=_INITIALIZE,
            )
        assert response.status_code == 401, response.text
        # WWW-Authenticate: Bearer must be present (RS discovery handshake).
        www = response.headers.get("www-authenticate", "")
        assert "Bearer" in www, f"missing WWW-Authenticate: Bearer (got {www!r})"
        # THE INVARIANT: the static-key reader was never consulted.
        assert spy.called is False, (
            "FAIL-CASCADE DETECTED: an invalid JWT-shaped token was evaluated "
            "against the static key (threat model CD1 auth-bypass)."
        )

    def test_bad_signature_jwt_no_fallback(self, client):
        token = _mint(_base_claims(), secret="a-different-secret-32-bytes-padding-xx")
        self._assert_401_and_static_key_untouched(client, token)

    def test_alg_none_jwt_no_fallback(self, client):
        header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        payload = _b64url(json.dumps(_base_claims()).encode())
        token = f"{header}.{payload}."
        self._assert_401_and_static_key_untouched(client, token)

    def test_alg_confusion_rs256_no_fallback(self, client):
        import hashlib
        import hmac

        header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        payload = _b64url(json.dumps(_base_claims()).encode())
        sig = _b64url(
            hmac.new(_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        )
        token = f"{header}.{payload}.{sig}"
        self._assert_401_and_static_key_untouched(client, token)

    def test_expired_jwt_no_fallback(self, client):
        token = _mint(_base_claims(exp=int(time.time()) - 10))
        self._assert_401_and_static_key_untouched(client, token)

    def test_wrong_aud_jwt_no_fallback(self, client):
        token = _mint(_base_claims(aud="other-rs"))
        self._assert_401_and_static_key_untouched(client, token)

    def test_wrong_iss_jwt_no_fallback(self, client):
        token = _mint(_base_claims(iss="https://evil.example.com"))
        self._assert_401_and_static_key_untouched(client, token)

    def test_scope_without_mcp_no_fallback(self, client):
        token = _mint(_base_claims(scope="read"))
        self._assert_401_and_static_key_untouched(client, token)

    def test_jwt_shaped_value_equal_to_static_key_value_still_no_fallback(self, client):
        """ULTIMATE CD1 GUARD: even if a JWT-shaped token's raw string somehow
        equalled the static key, the OAuth path must reject it on validation and
        NOT compare it to the static key. We make the static key BE a JWT-shaped
        invalid token; presenting it as a Bearer must 401, not authenticate."""
        token = _mint(_base_claims(), secret="wrong-secret-32-bytes-padding-aaaa")
        spy = _StaticKeySpy(value=token)  # static key == the invalid JWT string
        with patch("server.get_signing_key", return_value=_SECRET), \
             patch("server.get_oauth_issuer_for_rs", return_value=_ISSUER), \
             patch("server.get_mcp_api_key", new=spy):
            response = client.post(
                "/mcp",
                headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
                json=_INITIALIZE,
            )
        assert response.status_code == 401
        assert spy.called is False


# ───────────────── OAuth happy path ──────────────────────────────────────────


@_OAUTH_RETIRED
class TestOAuthHappyPath:
    def test_valid_oauth_jwt_authenticates(self, client):
        token = _mint(_base_claims())
        with patch("server.get_signing_key", return_value=_SECRET), \
             patch("server.get_oauth_issuer_for_rs", return_value=_ISSUER), \
             patch("server.get_mcp_api_key", return_value=_STATIC_KEY):
            response = client.post(
                "/mcp",
                headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
                json=_INITIALIZE,
            )
        assert response.status_code == 200, response.text
        result = _parse_initialize_result(response)
        assert result["result"]["serverInfo"]["name"] == "ecm-mcp"

    def test_oauth_path_logs_auth_method_oauth(self, client, caplog):
        token = _mint(_base_claims())
        import logging

        with caplog.at_level(logging.INFO, logger="server"):
            with patch("server.get_signing_key", return_value=_SECRET), \
                 patch("server.get_oauth_issuer_for_rs", return_value=_ISSUER), \
                 patch("server.get_mcp_api_key", return_value=_STATIC_KEY):
                client.post(
                    "/mcp",
                    headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
                    json=_INITIALIZE,
                )
        assert any("auth_method=oauth" in r.getMessage() for r in caplog.records), (
            "AC5: a successful OAuth auth must log auth_method=oauth"
        )

    def test_oauth_when_signing_key_missing_returns_401(self, client):
        """If the signing secret is absent, a JWT-shaped Bearer cannot be
        verified offline → 401 (fail-closed). It must NOT fall to static-key."""
        token = _mint(_base_claims())
        spy = _StaticKeySpy()
        with patch("server.get_signing_key", return_value=""), \
             patch("server.get_oauth_issuer_for_rs", return_value=_ISSUER), \
             patch("server.get_mcp_api_key", new=spy):
            response = client.post(
                "/mcp",
                headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
                json=_INITIALIZE,
            )
        assert response.status_code == 401
        assert spy.called is False


# ───────────────── Static-key path regression (EP2) ──────────────────────────


class TestStaticKeyRegression:
    """Existing static-key behavior is PO-locked permanent and must not change."""

    def test_query_param_static_key_still_authenticates(self, client):
        with patch("server.get_mcp_api_key", return_value=_STATIC_KEY):
            response = client.post(
                f"/mcp?api_key={_STATIC_KEY}", headers=_MCP_HEADERS, json=_INITIALIZE
            )
        assert response.status_code == 200, response.text
        result = _parse_initialize_result(response)
        assert result["result"]["serverInfo"]["name"] == "ecm-mcp"

    def test_bearer_static_key_still_authenticates(self, client):
        # A non-JWT-shaped Bearer value routes to the static-key path.
        with patch("server.get_mcp_api_key", return_value=_STATIC_KEY):
            response = client.post(
                "/mcp",
                headers={**_MCP_HEADERS, "Authorization": f"Bearer {_STATIC_KEY}"},
                json=_INITIALIZE,
            )
        assert response.status_code == 200, response.text

    def test_wrong_static_key_query_rejected(self, client):
        with patch("server.get_mcp_api_key", return_value=_STATIC_KEY):
            response = client.post(
                "/mcp?api_key=wrong-key", headers=_MCP_HEADERS, json=_INITIALIZE
            )
        assert response.status_code == 401

    def test_wrong_bearer_static_key_rejected(self, client):
        with patch("server.get_mcp_api_key", return_value=_STATIC_KEY):
            response = client.post(
                "/mcp",
                headers={**_MCP_HEADERS, "Authorization": "Bearer wrong-static-key-no-dots"},
                json=_INITIALIZE,
            )
        assert response.status_code == 401

    def test_no_credential_returns_401_with_www_authenticate(self, client):
        with patch("server.get_mcp_api_key", return_value=_STATIC_KEY):
            response = client.post("/mcp", headers=_MCP_HEADERS, json=_INITIALIZE)
        assert response.status_code == 401
        assert "Bearer" in response.headers.get("www-authenticate", "")

    def test_not_configured_returns_503(self, client):
        with patch("server.get_mcp_api_key", return_value=""):
            response = client.post(
                f"/mcp?api_key={_STATIC_KEY}", headers=_MCP_HEADERS, json=_INITIALIZE
            )
        assert response.status_code == 503

    def test_static_path_logs_auth_method_static_key(self, client, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger="server"):
            with patch("server.get_mcp_api_key", return_value=_STATIC_KEY):
                client.post(
                    f"/mcp?api_key={_STATIC_KEY}", headers=_MCP_HEADERS, json=_INITIALIZE
                )
        assert any(
            "auth_method=static_key" in r.getMessage() for r in caplog.records
        ), "AC5: a successful static-key auth must log auth_method=static_key"


# ───────────────── Both credentials present: OAuth wins ──────────────────────


@_OAUTH_RETIRED
class TestBothCredentialsPresent:
    """When an OAuth-shaped Bearer AND ?api_key= are both present, OAuth wins;
    the static key is ignored entirely (CD1 / ADR-009 §2)."""

    def test_valid_oauth_bearer_plus_wrong_api_key_authenticates(self, client):
        # OAuth Bearer is valid; api_key is wrong. OAuth wins → 200.
        token = _mint(_base_claims())
        with patch("server.get_signing_key", return_value=_SECRET), \
             patch("server.get_oauth_issuer_for_rs", return_value=_ISSUER), \
             patch("server.get_mcp_api_key", return_value=_STATIC_KEY):
            response = client.post(
                "/mcp?api_key=totally-wrong-key",
                headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
                json=_INITIALIZE,
            )
        assert response.status_code == 200, response.text

    def test_invalid_oauth_bearer_plus_valid_api_key_rejected(self, client):
        """CD1 EXTREME: an invalid OAuth Bearer with a VALID ?api_key= present
        must STILL 401 — OAuth wins and its failure does not fall back to the
        (valid) static key. This is the bypass the whole ADR exists to prevent."""
        token = _mint(_base_claims(), secret="wrong-secret-32-bytes-padding-bbbb")
        spy = _StaticKeySpy()
        with patch("server.get_signing_key", return_value=_SECRET), \
             patch("server.get_oauth_issuer_for_rs", return_value=_ISSUER), \
             patch("server.get_mcp_api_key", new=spy):
            response = client.post(
                f"/mcp?api_key={_STATIC_KEY}",  # valid static key present
                headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
                json=_INITIALIZE,
            )
        assert response.status_code == 401, response.text
        assert spy.called is False, (
            "FAIL-CASCADE: invalid OAuth Bearer fell back to a valid api_key — "
            "this is the CD1 auth-bypass."
        )


# ───────────────── /health + discovery stay public ───────────────────────────


@_OAUTH_RETIRED
class TestExemptPathsUnaffected:
    def test_health_still_public(self, client):
        with patch("server.get_mcp_api_key_status", return_value=("k", "ok")), \
             patch("server.get_signing_key_status", return_value=("", "ok")):
            response = client.get("/health")
        assert response.status_code == 200

    def test_discovery_still_public(self, client):
        with patch("server.resolve_issuer", return_value="https://ecm.example.com"), \
             patch("server.get_oauth_allow_insecure", return_value=False), \
             patch("server.resolve_resource_url", return_value="https://mcp.example.com"):
            response = client.get("/.well-known/oauth-protected-resource")
        assert response.status_code == 200
