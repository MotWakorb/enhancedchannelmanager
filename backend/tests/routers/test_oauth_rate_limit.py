"""Rate-limit tests for the OAuth AS endpoints (bead buiqr.4 AC1, threat D1/D2).

These are the only OAuth tests that run with rate limiting ENABLED. The default
test suite disables it (``RATE_LIMIT_ENABLED=0`` in conftest, which is read when
``auth.routes.limiter`` is constructed at import time), so here we flip the
already-constructed limiter's runtime ``enabled`` flag on, reset its in-memory
storage between tests for isolation, and restore the disabled state afterwards.

Coverage:
  - ``/api/oauth/token`` trips 429 after the configured per-window limit (D1).
  - ``/api/oauth/authorize`` trips 429 after the configured limit (D2).
  - The per-IP and per-user buckets are wired (two ``@limiter.limit`` decorators).
  - The per-user key function classifies the principal (admin sub / client_id / IP).
  - The limit strings are env-var configurable.
"""
import base64
import hashlib

import pytest

from auth.oauth_clients import CLAUDE_DESKTOP_CLIENT_ID, seed_oauth_clients
from auth.oauth_store import OAuthStore


def _pkce_pair():
    verifier = "rate-verifier-9876543210-zyxwvutsrqponmlkjihgfedcba"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@pytest.fixture()
def oauth_store(tmp_path):
    s = OAuthStore(tmp_path / "mcp_oauth.db")
    s.init_schema()
    seed_oauth_clients(s)
    yield s
    s.close()


@pytest.fixture(autouse=True)
def patch_store_and_secret(oauth_store, monkeypatch):
    monkeypatch.setattr("routers.oauth_mcp.get_oauth_store", lambda: oauth_store)
    monkeypatch.setattr(oauth_store, "close", lambda: None)
    monkeypatch.setattr("auth.oauth_provider._oauth_signing_secret", lambda: "z" * 48)
    monkeypatch.setenv("OAUTH_ISSUER", "https://ecm.test")


@pytest.fixture()
def rate_limiting_enabled():
    """Turn the shared limiter on for the duration of a test, then restore.

    The limiter is constructed once at import with ``enabled`` read from
    ``RATE_LIMIT_ENABLED`` (0 in tests). We flip the runtime flag and reset the
    in-memory storage so each test starts with empty buckets and other tests are
    unaffected.
    """
    from auth.routes import limiter

    prior = limiter.enabled
    limiter.reset()
    limiter.enabled = True
    try:
        yield limiter
    finally:
        limiter.enabled = prior
        limiter.reset()


def _registered_redirect(store):
    return store.get_client(CLAUDE_DESKTOP_CLIENT_ID)["redirect_uris"][0]


# ───────────────────────── /token rate limit (D1) ─────────────────────────


@pytest.mark.skip(
    reason="MCP OAuth offering retired (bd-9axgc); /api/oauth/token endpoint unregistered → 404. Re-enable when MCP OAuth is re-offered."
)
class TestTokenRateLimit:
    @pytest.mark.asyncio
    async def test_token_trips_429_after_limit(
        self, async_client, rate_limiting_enabled, monkeypatch
    ):
        """buiqr.4 AC1 / D1 — /token returns 429 once the per-window limit is hit."""
        # Pin a tiny limit so the test is fast and deterministic.
        monkeypatch.setenv("OAUTH_TOKEN_RATE_LIMIT", "3/minute")

        payload = {"grant_type": "password", "client_id": CLAUDE_DESKTOP_CLIENT_ID}
        statuses = []
        for _ in range(5):
            resp = await async_client.post("/api/oauth/token", data=payload)
            statuses.append(resp.status_code)

        # The first 3 reach the handler (400 unsupported_grant_type); the rest 429.
        assert statuses[:3] == [400, 400, 400]
        assert 429 in statuses[3:]

    @pytest.mark.asyncio
    async def test_token_under_limit_not_throttled(
        self, async_client, rate_limiting_enabled, monkeypatch
    ):
        """Requests under the limit are NOT throttled."""
        monkeypatch.setenv("OAUTH_TOKEN_RATE_LIMIT", "10/minute")
        payload = {"grant_type": "password", "client_id": CLAUDE_DESKTOP_CLIENT_ID}
        for _ in range(5):
            resp = await async_client.post("/api/oauth/token", data=payload)
            assert resp.status_code == 400  # reaches handler, never 429


# ───────────────────────── /authorize rate limit (D2) ─────────────────────


@pytest.mark.skip(
    reason="MCP OAuth offering retired (bd-9axgc); /api/oauth/authorize endpoint unregistered → 404. Re-enable when MCP OAuth is re-offered."
)
class TestAuthorizeRateLimit:
    @pytest.mark.asyncio
    async def test_authorize_trips_429_after_limit(
        self, async_client, oauth_store, rate_limiting_enabled, monkeypatch
    ):
        """buiqr.4 AC1 / D2 — /authorize returns 429 once the limit is hit."""
        monkeypatch.setenv("OAUTH_AUTHORIZE_RATE_LIMIT", "3/minute")
        _, challenge = _pkce_pair()
        params = {
            "response_type": "code",
            "client_id": CLAUDE_DESKTOP_CLIENT_ID,
            "redirect_uri": _registered_redirect(oauth_store),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "rl",
        }
        statuses = []
        for _ in range(5):
            resp = await async_client.get(
                "/api/oauth/authorize", params=params, follow_redirects=False
            )
            statuses.append(resp.status_code)

        # First 3 validate + 302 to consent; the rest 429.
        assert statuses[:3] == [302, 302, 302]
        assert 429 in statuses[3:]


# ───────────────────── per-user key function (both buckets) ────────────────


class TestPerUserRateKey:
    """The per-user bucket's key function classifies the principal (AC1)."""

    def test_key_uses_jwt_subject_when_present(self):
        from unittest.mock import MagicMock

        from auth.oauth_rate_limit import oauth_user_rate_key

        req = MagicMock()
        req.cookies = {"access_token": "tok"}
        req.headers = {}
        req.query_params = {}
        with _patch_decode("admin-42"):
            key = oauth_user_rate_key(req)
        assert key == "oauth-sub:admin-42"

    def test_key_uses_client_id_when_no_jwt(self):
        from unittest.mock import MagicMock

        from auth.oauth_rate_limit import oauth_user_rate_key

        req = MagicMock()
        req.cookies = {}
        req.headers = {}
        req.query_params = {"client_id": "claude-desktop"}
        key = oauth_user_rate_key(req)
        assert key == "oauth-client:claude-desktop"

    def test_key_falls_back_to_ip(self):
        from unittest.mock import MagicMock

        from auth.oauth_rate_limit import oauth_user_rate_key

        req = MagicMock()
        req.cookies = {}
        req.headers = {}
        req.query_params = {}
        req.client = MagicMock()
        req.client.host = "10.0.0.9"
        key = oauth_user_rate_key(req)
        assert key == "oauth-ip:10.0.0.9"


class TestRateLimitConfig:
    def test_defaults_match_login_posture(self, monkeypatch):
        from auth import oauth_rate_limit as rl

        monkeypatch.delenv("OAUTH_AUTHORIZE_RATE_LIMIT", raising=False)
        monkeypatch.delenv("OAUTH_TOKEN_RATE_LIMIT", raising=False)
        assert rl.authorize_rate_limit() == "5/minute"
        assert rl.token_rate_limit() == "10/minute"

    def test_env_overrides_apply(self, monkeypatch):
        from auth import oauth_rate_limit as rl

        monkeypatch.setenv("OAUTH_AUTHORIZE_RATE_LIMIT", "2/minute")
        monkeypatch.setenv("OAUTH_TOKEN_RATE_LIMIT", "7/second")
        assert rl.authorize_rate_limit() == "2/minute"
        assert rl.token_rate_limit() == "7/second"


def _patch_decode(sub):
    """Context manager that stubs decode_token_safe to return a payload with sub."""
    from unittest.mock import patch

    return patch(
        "auth.oauth_rate_limit.decode_token_safe", return_value={"sub": sub}
    )
