"""Auth routing tests for the MCP RS static-key path (bd-buiqr.8 / bd-9axgc).

THE HEADLINE THREAT (threat model CD1): a JWT-shaped Bearer token must NEVER
fall through to the static-key check. Routing is by credential SHAPE, decided
before any static-key compare.

MCP OAuth offering RETIRED (bd-9axgc): ECM no longer accepts OAuth 2.1 Bearer
tokens for MCP. A JWT-shaped Bearer is now REJECTED with 401 and is never
compared against the static key — the CD1 no-fail-cascade invariant still holds,
and these tests pin it. The supported credential is the static ?api_key= path.

These exercise the full Starlette app (``server.app``) + the dual-path
middleware over HTTP (TestClient), mirroring ``test_server.py``'s style.

Coverage map:
  - CD1 — JWT-shaped Bearer rejected 401, never evaluated as a static key.
  - SP2 — alg:none / RS256 alg-confusion still classified JWT-shaped → 401.
  - SP6 — a static-key value shaped as a JWT never enters the static-key path.
  - EP2 — static-key path behavior unchanged (regression).
  - AC5 — auth_method=static_key logged on success.
"""
import base64
import json
import time

from unittest.mock import patch

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


# A spy that asserts the static-key value is NEVER read once a JWT-shaped Bearer
# has been classified. ``get_mcp_api_key`` is the static-key reader; if a
# JWT-shaped token fell through to static-key validation, this would be called.
class _StaticKeySpy:
    def __init__(self, value=_STATIC_KEY):
        self.value = value
        self.called = False

    def __call__(self):
        self.called = True
        return self.value


# ───────────────── CD1: no fail-cascade (the headline) ───────────────────────


class TestNoFailCascade:
    """A JWT-shaped Bearer is REJECTED 401 and NEVER compared against the static
    key (threat model CD1). OAuth retired (bd-9axgc): every JWT-shaped value is
    rejected outright — the supported path is static ?api_key=."""

    def _assert_401_and_static_key_untouched(self, client, token):
        spy = _StaticKeySpy()
        with patch("server.get_mcp_api_key", new=spy):
            response = client.post(
                "/mcp",
                headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
                json=_INITIALIZE,
            )
        assert response.status_code == 401, response.text
        # WWW-Authenticate: Bearer must be present (RFC 6750 challenge).
        www = response.headers.get("www-authenticate", "")
        assert "Bearer" in www, f"missing WWW-Authenticate: Bearer (got {www!r})"
        # THE INVARIANT: the static-key reader was never consulted.
        assert spy.called is False, (
            "FAIL-CASCADE DETECTED: a JWT-shaped token was evaluated against "
            "the static key (threat model CD1 auth-bypass)."
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
        equalled the static key, the JWT-reject branch must 401 and NOT compare
        it to the static key. We make the static key BE a JWT-shaped value;
        presenting it as a Bearer must 401, not authenticate."""
        token = _mint(_base_claims(), secret="wrong-secret-32-bytes-padding-aaaa")
        spy = _StaticKeySpy(value=token)  # static key == the JWT-shaped value
        with patch("server.get_mcp_api_key", new=spy):
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


# ───────────────── /health stays public; OAuth discovery is gone ─────────────


class TestExemptPathsUnaffected:
    def test_health_still_public(self, client):
        with patch("server.get_mcp_api_key_status", return_value=("k", "ok")):
            response = client.get("/health")
        assert response.status_code == 200

    def test_oauth_discovery_no_longer_served(self, client):
        """OAuth retired (bd-9axgc): the RFC 9728 discovery endpoint was removed.
        It now falls through to the MCP mount, which does not serve it → not 200."""
        with patch("server.get_mcp_api_key", return_value=_STATIC_KEY):
            response = client.get("/.well-known/oauth-protected-resource")
        assert response.status_code != 200
