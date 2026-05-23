"""Tests for MCP server endpoints and auth middleware (Streamable HTTP transport).

DUAL-PATH REGRESSION MATRIX (bead buiqr.9 (b), PO decision #4)
=============================================================
The MCP RS authenticates a request on EXACTLY ONE of two paths, chosen by
credential SHAPE (ADR-009 §2): the static-key path (``?api_key=`` /
non-JWT-shaped Bearer) and the OAuth-bearer path (JWT-shaped Bearer, offline
HS256 verify). The focused ``TestMCPAuth`` / ``TestMCPInitialize`` classes below
pin the static-path-SPECIFIC contract (503-when-unconfigured, query-vs-header).

Alongside them, the PERMANENT matrix classes ``TestMCPAuthMatrix`` /
``TestMCPInitializeMatrix`` parametrize every auth-mode-AGNOSTIC scenario
(no credential, valid credential, wrong credential, bare GET) so each runs once
per ``auth_mode`` — ``static_key`` AND ``oauth_bearer``. This is the forever
guard that a future OAuth refactor cannot regress one path while leaving the
other green. The per-mode credential factory lives in ``_AUTH_MODES`` and mints
real tokens (valid HS256 JWT for OAuth, opaque key for static), so the matrix
exercises the SHAPE router end-to-end through ``server.app`` (no network).
"""
import base64
import hmac
import json
import time

from unittest.mock import patch

import pytest

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

    @pytest.mark.skip(
        reason="MCP OAuth offering retired (bd-9axgc); /health no longer reports signing_key_status/hint (OAuth Bearer-JWT auth removed). Re-enable when MCP OAuth is re-offered."
    )
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

    @pytest.mark.skip(
        reason="MCP OAuth offering retired (bd-9axgc); /health no longer reports signing_key_status/hint (OAuth Bearer-JWT auth removed). Re-enable when MCP OAuth is re-offered."
    )
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

    @pytest.mark.skip(
        reason="MCP OAuth offering retired (bd-9axgc); /health no longer reports signing_key_status/hint (OAuth Bearer-JWT auth removed). Re-enable when MCP OAuth is re-offered."
    )
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

    @pytest.mark.skip(
        reason="MCP OAuth offering retired (bd-9axgc); /health no longer reports signing_key_status/hint (OAuth Bearer-JWT auth removed). Re-enable when MCP OAuth is re-offered."
    )
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

    @pytest.mark.skip(
        reason="MCP OAuth offering retired (bd-9axgc); /health no longer reports signing_key_status/hint (OAuth Bearer-JWT auth removed). Re-enable when MCP OAuth is re-offered."
    )
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

    def test_mcp_uses_constant_time_compare(self, client):
        """The static-key check goes through hmac.compare_digest (bd-i3axt LOW-1).

        Spy on hmac.compare_digest as imported into the server module and assert
        the wrong-key path runs the constant-time comparator (never a plain
        ``!=``), and that it still rejects with 401.
        """
        with patch("server.get_mcp_api_key", return_value="valid-key"), \
             patch("server.hmac.compare_digest", wraps=hmac.compare_digest) as spy:
            response = client.post(
                "/mcp?api_key=wrong-key", headers=_MCP_HEADERS, json=_INITIALIZE
            )
        assert response.status_code == 401
        spy.assert_called_once_with("wrong-key", "valid-key")

    def test_mcp_accepts_lowercase_bearer_scheme(self, client):
        """RFC 6750 §2.1: the Bearer scheme is case-insensitive (bd-i3axt LOW-2)."""
        headers = {**_MCP_HEADERS, "Authorization": "bearer valid-key"}
        with patch("server.get_mcp_api_key", return_value="valid-key"):
            response = client.post("/mcp", headers=headers, json=_INITIALIZE)
        assert response.status_code == 200

    def test_mcp_strips_bearer_credential_whitespace(self, client):
        """RFC 6750: surrounding whitespace on the credential is trimmed (LOW-2)."""
        headers = {**_MCP_HEADERS, "Authorization": "Bearer   valid-key   "}
        with patch("server.get_mcp_api_key", return_value="valid-key"):
            response = client.post("/mcp", headers=headers, json=_INITIALIZE)
        assert response.status_code == 200

    def test_mcp_empty_bearer_value_rejected(self, client):
        """A header of just 'Bearer' (no credential) fails safe with 401."""
        headers = {**_MCP_HEADERS, "Authorization": "Bearer"}
        with patch("server.get_mcp_api_key", return_value="valid-key"):
            response = client.post("/mcp", headers=headers, json=_INITIALIZE)
        assert response.status_code == 401


@pytest.mark.skip(
    reason="MCP OAuth offering retired (bd-9axgc); RS no longer serves /.well-known/oauth-protected-resource. Re-enable when MCP OAuth is re-offered."
)
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


# ─────────────────── DUAL-PATH REGRESSION MATRIX (buiqr.9 (b)) ────────────────
#
# A per-auth-mode harness so the SAME auth scenarios run on BOTH paths. Each mode
# knows how to (1) install the auth state the path reads, (2) mint a VALID and a
# WRONG credential for /mcp, and (3) place that credential on the request (query
# param vs Authorization header). The scenario tests below are written once and
# parametrized over both modes.

_OAUTH_SECRET = "matrix-oauth-signing-secret-at-least-32-bytes-long-pad"
_OAUTH_ISSUER = "https://ecm.example.com"
_OAUTH_AUDIENCE = "ecm-mcp"
_STATIC_KEY = "matrix-static-key-no-dots-1234567890abcdef"


def _mint_oauth_jwt(claims: dict, *, secret: str = _OAUTH_SECRET) -> str:
    import jwt as pyjwt

    return pyjwt.encode(claims, secret, algorithm="HS256")


def _oauth_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": "admin",
        "aud": _OAUTH_AUDIENCE,
        "iss": _OAUTH_ISSUER,
        "scope": "mcp",
        "jti": "jti-matrix",
        "iat": now,
        "exp": now + 900,
        "token_type": "access",
    }
    claims.update(overrides)
    return claims


class _AuthMode:
    """One row of the dual-path matrix: how to drive /mcp on a single auth path."""

    name: str

    def patches(self):
        """The unittest.mock.patch context managers installing this path's state."""
        raise NotImplementedError

    def valid_request(self, client):
        """POST /mcp with a VALID credential for this path."""
        raise NotImplementedError

    def wrong_request(self, client):
        """POST /mcp with a WRONG (rejectable) credential for this path."""
        raise NotImplementedError

    def get_request(self, client):
        """Bare GET /mcp (no credential) — must be auth-checked on this path."""
        raise NotImplementedError


class _StaticKeyMode(_AuthMode):
    """auth_mode=static_key — ?api_key= / non-JWT-shaped Bearer (PO-locked path)."""

    name = "static_key"

    def patches(self):
        return [patch("server.get_mcp_api_key", return_value=_STATIC_KEY)]

    def valid_request(self, client):
        return client.post(
            f"/mcp?api_key={_STATIC_KEY}", headers=_MCP_HEADERS, json=_INITIALIZE
        )

    def wrong_request(self, client):
        return client.post(
            "/mcp?api_key=wrong-static-key", headers=_MCP_HEADERS, json=_INITIALIZE
        )

    def get_request(self, client):
        return client.get("/mcp")


class _OAuthBearerMode(_AuthMode):
    """auth_mode=oauth_bearer — JWT-shaped Bearer, offline HS256 verify."""

    name = "oauth_bearer"

    def patches(self):
        # The static-key reader is also patched (to a real value) so a
        # fail-cascade would NOT silently authenticate — a bug would surface as
        # the WRONG path accepting, not as a missing-config 503.
        return [
            patch("server.get_signing_key", return_value=_OAUTH_SECRET),
            patch("server.get_oauth_issuer_for_rs", return_value=_OAUTH_ISSUER),
            patch("server.get_mcp_api_key", return_value=_STATIC_KEY),
        ]

    def valid_request(self, client):
        token = _mint_oauth_jwt(_oauth_claims())
        return client.post(
            "/mcp",
            headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
            json=_INITIALIZE,
        )

    def wrong_request(self, client):
        # JWT-shaped but signed with the WRONG secret → rejected on the OAuth path.
        token = _mint_oauth_jwt(_oauth_claims(), secret="wrong-secret-32-bytes-padding-zzzz")
        return client.post(
            "/mcp",
            headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
            json=_INITIALIZE,
        )

    def get_request(self, client):
        return client.get("/mcp")


# MCP OAuth offering RETIRED (bd-9axgc): the oauth_bearer auth-mode was removed
# from the parametrize set — the RS no longer accepts OAuth Bearer JWTs and the
# OAuth verify wiring (_OAuthBearerMode patches get_signing_key /
# get_oauth_issuer_for_rs) was removed from server.py. The matrix now guards the
# supported static-key path only. _OAuthBearerMode is kept dormant above for
# reversibility; re-add it here to re-enable the OAuth matrix coverage.
_AUTH_MODES = [_StaticKeyMode()]


@pytest.fixture(params=_AUTH_MODES, ids=[m.name for m in _AUTH_MODES])
def auth_mode(request):
    """Parametrizes a test over BOTH auth paths (static_key + oauth_bearer)."""
    return request.param


def _enter(mode):
    """Enter all of a mode's patches; return the list so the caller can exit them."""
    ctxs = mode.patches()
    for c in ctxs:
        c.__enter__()
    return ctxs


def _exit(ctxs):
    for c in reversed(ctxs):
        c.__exit__(None, None, None)


class TestMCPAuthMatrix:
    """Auth-rejection scenarios that MUST behave identically on both paths.

    Mirrors the auth-mode-agnostic cases in ``TestMCPAuth`` but runs each once
    per ``auth_mode`` (buiqr.9 (b)). The static-path-SPECIFIC 503-when-not-
    configured case stays in ``TestMCPAuth`` (it has no OAuth-path analogue).
    """

    def test_no_credential_rejected_401(self, client, auth_mode):
        """No credential at all → 401 on either path."""
        ctxs = _enter(auth_mode)
        try:
            response = client.post("/mcp", headers=_MCP_HEADERS, json=_INITIALIZE)
        finally:
            _exit(ctxs)
        assert response.status_code == 401, f"[{auth_mode.name}] {response.text}"
        assert "Bearer" in response.headers.get("www-authenticate", "")

    def test_wrong_credential_rejected_401(self, client, auth_mode):
        """A wrong/invalid credential → 401 on either path (no cross-path bypass)."""
        ctxs = _enter(auth_mode)
        try:
            response = auth_mode.wrong_request(client)
        finally:
            _exit(ctxs)
        assert response.status_code == 401, f"[{auth_mode.name}] {response.text}"

    def test_bare_get_requires_credential(self, client, auth_mode):
        """A bare GET /mcp (event stream) is auth-checked on either path."""
        ctxs = _enter(auth_mode)
        try:
            response = auth_mode.get_request(client)
        finally:
            _exit(ctxs)
        assert response.status_code == 401, f"[{auth_mode.name}] {response.text}"


class TestMCPInitializeMatrix:
    """A full initialize round-trip MUST succeed with a valid credential on BOTH paths.

    Mirrors ``TestMCPInitialize`` but parametrized over ``auth_mode`` so the
    happy-path round-trip is permanently guarded on both the static-key and the
    OAuth-bearer path (buiqr.9 (b), PO decision #4).
    """

    def test_initialize_round_trip_succeeds(self, client, auth_mode):
        ctxs = _enter(auth_mode)
        try:
            response = auth_mode.valid_request(client)
        finally:
            _exit(ctxs)
        assert response.status_code == 200, f"[{auth_mode.name}] {response.text}"
        assert "mcp-session-id" in {k.lower() for k in response.headers}
        result = _parse_initialize_result(response)
        assert result["id"] == 1
        assert result["result"]["serverInfo"]["name"] == "ecm-mcp"


# ─────────────────── .mcp.json COMPATIBILITY GUARD (buiqr.9 (c)) ──────────────
#
# The repo-root .mcp.json is the LITERAL client config an operator points Claude
# at today: the static ?api_key= path over the http transport. The whole epic
# (AC2/AC3) is that OAuth is ADDITIVE — the existing query-string static path
# stays working. This guard loads that real config as a fixture and proves it
# still drives a successful initialize. A future OAuth refactor that breaks
# query-string auth fails THIS test before it can merge.

from pathlib import Path  # noqa: E402 — co-located with the guard it serves

#: The repo-root .mcp.json. tests/ is mcp-server/tests/, so the repo root is two
#: levels up. Resolved from THIS file's location (CI runs pytest from
#: mcp-server/, but anchoring on __file__ is robust to the working directory).
_REPO_ROOT_MCP_JSON = Path(__file__).resolve().parents[2] / ".mcp.json"


def _load_repo_mcp_json() -> dict:
    """Load and parse the repo-root .mcp.json literal config."""
    return json.loads(_REPO_ROOT_MCP_JSON.read_text())


class TestMcpJsonCompatGuard:
    """The repo-root .mcp.json static-path config must keep working (AC2/AC3).

    These read the ACTUAL committed .mcp.json — not a hand-built dict — so the
    guard tracks the file an operator copies. If the file moves or its shape
    drifts, these fail loudly rather than silently testing nothing.
    """

    def test_mcp_json_exists_and_is_http_transport(self):
        """.mcp.json declares the ecm server over the http transport."""
        assert _REPO_ROOT_MCP_JSON.exists(), (
            f"repo-root .mcp.json not found at {_REPO_ROOT_MCP_JSON} — the compat "
            "guard cannot validate the literal client config"
        )
        cfg = _load_repo_mcp_json()
        servers = cfg.get("mcpServers", {})
        assert "ecm" in servers, "expected an 'ecm' server entry in .mcp.json"
        assert servers["ecm"]["type"] == "http", (
            "the ecm MCP server must use the http transport (Streamable HTTP)"
        )

    def test_mcp_json_url_uses_query_string_api_key(self):
        """The ecm URL carries the static ?api_key= credential (the static path).

        This is the exact property a query-string-breaking OAuth refactor would
        violate. Pinning it here means such a refactor cannot merge silently.
        """
        cfg = _load_repo_mcp_json()
        url = cfg["mcpServers"]["ecm"]["url"]
        assert "/mcp" in url, f"expected the /mcp endpoint in the URL: {url!r}"
        assert "api_key=" in url, (
            f"expected a static ?api_key= credential in the .mcp.json URL: {url!r}"
        )

    def test_mcp_json_config_drives_initialize_round_trip(self, client):
        """The literal .mcp.json URL drives a successful initialize on the static path.

        We extract the api_key from the committed config's query string and POST
        an initialize exactly as a client configured from .mcp.json would — the
        round-trip must succeed (AC3). This proves the static path the config
        relies on is intact end-to-end, not just that the file parses.
        """
        from urllib.parse import parse_qs, urlparse

        cfg = _load_repo_mcp_json()
        url = cfg["mcpServers"]["ecm"]["url"]
        api_key = parse_qs(urlparse(url).query)["api_key"][0]
        assert api_key, "the .mcp.json URL must carry a non-empty api_key value"

        # The server reads the configured key from settings.json; patch it to the
        # value the literal config presents so the static path authenticates.
        with patch("server.get_mcp_api_key", return_value=api_key):
            response = client.post(
                f"/mcp?api_key={api_key}", headers=_MCP_HEADERS, json=_INITIALIZE
            )
        assert response.status_code == 200, response.text
        assert "mcp-session-id" in {k.lower() for k in response.headers}
        result = _parse_initialize_result(response)
        assert result["id"] == 1
        assert result["result"]["serverInfo"]["name"] == "ecm-mcp"
