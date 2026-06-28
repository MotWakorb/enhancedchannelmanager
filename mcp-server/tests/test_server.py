"""Tests for MCP server endpoints and auth middleware (Streamable HTTP transport).

AUTH REGRESSION MATRIX (bead buiqr.9 (b))
=========================================
The MCP RS authenticates a request on the static-key path (``?api_key=`` /
non-JWT-shaped Bearer). The focused ``TestMCPAuth`` / ``TestMCPInitialize``
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
        exists but is corrupted — and includes a setup_hint guiding the operator."""
        with patch(
            "server.get_mcp_api_key_status", return_value=("", "invalid_json")
        ):
            response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["api_key_configured"] is False
        assert data["api_key_status"] == "invalid_json"
        # A setup_hint must be present for actionable operator guidance.
        assert "setup_hint" in data, (
            f"Expected 'setup_hint' in /health response for invalid_json, got: {data!r}"
        )

    def test_health_reports_field_missing(self, client):
        """/health reports api_key_status='field_missing' when settings.json
        is valid JSON but does not include the mcp_api_key field (legacy file
        from before the MCP feature shipped) — and includes a setup_hint."""
        with patch(
            "server.get_mcp_api_key_status", return_value=("", "field_missing")
        ):
            response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["api_key_configured"] is False
        assert data["api_key_status"] == "field_missing"
        # A setup_hint must be present so operators know to open ECM Settings.
        assert "setup_hint" in data, (
            f"Expected 'setup_hint' in /health response for field_missing, got: {data!r}"
        )

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
