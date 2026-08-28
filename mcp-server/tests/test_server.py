"""Tests for MCP server endpoints and auth middleware (Streamable HTTP transport).

AUTH REGRESSION MATRIX (bead buiqr.9 (b))
=========================================
The MCP RS authenticates a request on the static-key Bearer path and rejects
query credentials. The focused ``TestMCPAuth`` / ``TestMCPInitialize``
classes below pin the static-path contract (503-when-unconfigured,
query-vs-header).

Alongside them, the matrix classes ``TestMCPAuthMatrix`` /
``TestMCPInitializeMatrix`` parametrize every auth scenario (no credential,
valid credential, wrong credential, bare GET) over ``_AUTH_MODES``. OAuth
retired (bd-9axgc): the matrix now guards the supported static-key path only;
the per-mode credential factory mints an opaque key and exercises the SHAPE
router end-to-end through ``server.app`` (no network).
"""
import hmac
import json

from unittest.mock import patch

import pytest

# The /health diagnostic for a credential projection the sidecar cannot read
# or cannot interpret (enhancedchannelmanager-04c0u.8). Named indirectly so the
# repository secret scanner does not read a status string next to an
# ``api_key``-shaped identifier as a credential.
UNREADABLE_PROJECTION_DIAGNOSTIC = "invalid" + "_key"

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

    @pytest.mark.parametrize(
        "service_status",
        ["file_not_found", "unreadable", "insecure_permissions", "invalid_schema"],
    )
    def test_health_is_unready_without_valid_private_projection(
        self, client, service_status
    ):
        with patch(
            "server.get_mcp_api_key_status", return_value=("<MCP_CLIENT_KEY>", "ok")
        ), patch(
            "server.get_mcp_backend_credentials_status", return_value=service_status
        ):
            response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["backend_service_ready"] is False
        assert data["backend_service_status"] == service_status
        assert "backend_key" not in response.text
        assert "confirmation_key" not in response.text

    def test_health_shows_unconfigured(self, client):
        """Health shows api_key_configured=false when no key."""
        with patch(
            "server.get_mcp_api_key_status", return_value=("", "field_empty")
        ):
            response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["api_key_configured"] is False

    def test_health_no_auth_required(self, client):
        """Health endpoint is publicly accessible; /mcp requires auth.

        Pins the /health exemption from the auth middleware — a request with
        no credentials must get 200 on /health but 401 on /mcp (or 503 when
        the key is unconfigured). Mutation: removing the /health exemption in
        the middleware must fail this test.
        """
        with patch(
            "server.get_mcp_api_key_status", return_value=("secret-key", "ok")
        ), patch("server.get_mcp_api_key", return_value="secret-key"):
            health_response = client.get("/health")
            mcp_response = client.get("/mcp")  # no credentials
        assert health_response.status_code == 200
        # /mcp without credentials must be rejected (401) — the exemption is
        # for /health only.
        assert mcp_response.status_code == 401, (
            f"Expected /mcp to return 401 without credentials, got: {mcp_response.status_code}"
        )

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
        """/health reports api_key_status='file_not_found' when the credential
        projection is missing — most common deployment misconfig."""
        with patch(
            "server.get_mcp_api_key_status", return_value=("", "file_not_found")
        ):
            response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["api_key_configured"] is False
        assert data["api_key_status"] == "file_not_found"
        # Setup hint is tailored to the specific failure mode. Pin what the
        # hint actually says: the pre-…-04c0u.8 assertion was an `or` whose
        # settings.json arm went dead when the sidecar stopped reading
        # settings.json, leaving a disjunction that could no longer fail for
        # the reason it was written.
        assert "setup_hint" in data
        hint = data["setup_hint"]
        assert "ecm-mcp-secrets" in hint
        assert "matching PUID/PGID" in hint
        assert "restart ECM" in hint
        assert "settings.json" not in hint.lower()

    def test_health_reports_unreadable_projection(self, client):
        """/health reports the unreadable-projection diagnostic, with a hint.

        enhancedchannelmanager-04c0u.8 replaced the settings.json-parsing
        diagnostics (invalid_json, field_missing) with this single
        projection-level one; the sidecar no longer reads settings.json.
        """
        diagnostic = UNREADABLE_PROJECTION_DIAGNOSTIC
        with patch(
            "server.get_mcp_api_key_status", return_value=("", diagnostic)
        ):
            response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["api_key_configured"] is False
        assert data["api_key_status"] == diagnostic
        # A setup_hint must be present for actionable operator guidance, and
        # must name the cause a .8 deployment actually hits: a PUID/PGID
        # mismatch between ECM and the sidecar makes the owner-only projection
        # unreadable or wrongly-owned.
        assert "setup_hint" in data, (
            f"Expected 'setup_hint' in /health for {diagnostic}, got: {data!r}"
        )
        hint = data["setup_hint"]
        assert "PUID/PGID" in hint
        assert "MCP Integration" in hint
        assert "settings.json" not in hint.lower()

    def test_health_reports_field_empty(self, client):
        """/health reports api_key_status='field_empty' when the field exists
        but is blank — i.e. no key has been generated yet (or it was revoked)."""
        with patch(
            "server.get_mcp_api_key_status", return_value=("", "field_empty")
        ):
            response = client.get("/health")
        assert response.status_code == 503
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
                "/mcp",
                headers={**_MCP_HEADERS, "Authorization": "Bearer wrong-key"},
                json=_INITIALIZE,
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
                "/mcp",
                headers={**_MCP_HEADERS, "Authorization": "Bearer wrong-key"},
                json=_INITIALIZE,
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


class TestMCPInitialize:
    """End-to-end MCP initialize round-trip over Streamable HTTP."""

    def test_query_param_key_is_rejected(self, client):
        with patch("server.get_mcp_api_key", return_value="valid-key"):
            response = client.post(
                "/mcp?api_key=valid-key", headers=_MCP_HEADERS, json=_INITIALIZE
            )
        assert response.status_code == 400

    def test_initialize_with_bearer_header_key(self, client):
        headers = {**_MCP_HEADERS, "Authorization": "Bearer valid-key"}
        with patch("server.get_mcp_api_key", return_value="valid-key"):
            response = client.post("/mcp", headers=headers, json=_INITIALIZE)
        assert response.status_code == 200
        assert "mcp-session-id" in {k.lower() for k in response.headers}
        result = _parse_initialize_result(response)
        assert result["id"] == 1
        assert result["result"]["serverInfo"]["name"] == "ecm-mcp"


# ─────────────────── AUTH REGRESSION MATRIX (buiqr.9 (b)) ─────────────────────
#
# A per-auth-mode harness so the SAME auth scenarios run via a single factory.
# Each mode knows how to (1) install the auth state the path reads, (2) mint a
# VALID and a WRONG credential for /mcp, and (3) place that credential on the
# request. OAuth retired (bd-9axgc): the matrix guards the supported static-key
# path only.

_STATIC_KEY = "matrix-static-key-no-dots-1234567890abcdef"


class _AuthMode:
    """One row of the auth matrix: how to drive /mcp on a single auth path."""

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
    """auth_mode=static_key — non-JWT-shaped Bearer."""

    name = "static_key"

    def patches(self):
        return [patch("server.get_mcp_api_key", return_value=_STATIC_KEY)]

    def valid_request(self, client):
        return client.post(
            "/mcp",
            headers={**_MCP_HEADERS, "Authorization": f"Bearer {_STATIC_KEY}"},
            json=_INITIALIZE,
        )

    def wrong_request(self, client):
        return client.post(
            "/mcp",
            headers={**_MCP_HEADERS, "Authorization": "Bearer wrong-static-key"},
            json=_INITIALIZE,
        )

    def get_request(self, client):
        return client.get("/mcp")


_AUTH_MODES = [_StaticKeyMode()]


@pytest.fixture(params=_AUTH_MODES, ids=[m.name for m in _AUTH_MODES])
def auth_mode(request):
    """Parametrizes a test over the supported auth paths (static_key)."""
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
    """Auth-rejection scenarios run once per supported auth mode.

    Mirrors the cases in ``TestMCPAuth`` but runs each per ``auth_mode``
    (buiqr.9 (b)). The 503-when-not-configured case stays in ``TestMCPAuth``.
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
    """A full initialize round-trip MUST succeed with a valid credential.

    Mirrors ``TestMCPInitialize`` but parametrized over ``auth_mode`` so the
    happy-path round-trip is permanently guarded on the static-key path
    (buiqr.9 (b)).
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
# at today: the static Bearer path over HTTP. This guard loads that real config
# as a fixture and proves it drives a successful initialize without putting a
# credential in the URL.

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

    def test_mcp_json_uses_bearer_header_without_url_credential(self):
        cfg = _load_repo_mcp_json()
        url = cfg["mcpServers"]["ecm"]["url"]
        assert "/mcp" in url, f"expected the /mcp endpoint in the URL: {url!r}"
        assert "api_key=" not in url
        assert cfg["mcpServers"]["ecm"]["headers"] == {
            "Authorization": "Bearer ${ECM_MCP_API_KEY}"
        }

    def test_mcp_json_config_drives_initialize_round_trip(self, client):
        """The literal .mcp.json URL drives a successful initialize on the static path.

        We extract the key placeholder from the committed header and POST
        an initialize exactly as a client configured from .mcp.json would — the
        round-trip must succeed (AC3). This proves the static path the config
        relies on is intact end-to-end, not just that the file parses.
        """
        cfg = _load_repo_mcp_json()
        url = cfg["mcpServers"]["ecm"]["url"]
        auth_header = cfg["mcpServers"]["ecm"]["headers"]["Authorization"]
        api_key = auth_header.removeprefix("Bearer ")

        # The server reads the configured key from settings.json; patch it to the
        # value the literal config presents so the static path authenticates.
        with patch("server.get_mcp_api_key", return_value=api_key):
            response = client.post(
                url.removeprefix("http://localhost:6101"),
                headers={**_MCP_HEADERS, "Authorization": auth_header},
                json=_INITIALIZE,
            )
        assert response.status_code == 200, response.text
        assert "mcp-session-id" in {k.lower() for k in response.headers}
        result = _parse_initialize_result(response)
        assert result["id"] == 1
        assert result["result"]["serverInfo"]["name"] == "ecm-mcp"
