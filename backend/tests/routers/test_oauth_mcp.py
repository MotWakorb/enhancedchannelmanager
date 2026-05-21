"""Router-level tests for the OAuth AS endpoints (bead buiqr.3).

These exercise the HTTP surface of ``routers/oauth_mcp.py`` end-to-end through
the FastAPI app + auth middleware:

  buiqr.3 AC1 — PKCE plain rejected 400 invalid_request at /authorize.
  buiqr.3 AC2 — auth code single-use; replay → 400 invalid_grant at /token.
  buiqr.3 AC3 — /token returns an HS256 access JWT (full claim check in the
                provider unit tests; here we assert the token-endpoint shape).
  buiqr.3 AC4 — refresh rotation over HTTP; reuse → 400 invalid_grant.
  buiqr.3 AC5 — /authorize requires admin session; anonymous → 401 when auth on.
  buiqr.3 AC6 — /api/oauth/token is in AUTH_EXEMPT_PATHS (public).
  buiqr.6 AC3/AC4 — unknown client / mismatched redirect over HTTP.
  RFC 7009 — /revoke returns 200 even for unknown tokens.
"""
import base64
import hashlib
from unittest.mock import patch

import pytest

from auth.oauth_clients import CLAUDE_DESKTOP_CLIENT_ID, seed_oauth_clients
from auth.oauth_store import OAuthStore


TEST_SECRET = "z" * 48


def _pkce_pair():
    verifier = "router-verifier-9876543210-zyxwvutsrqponmlkjihgfedcba"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@pytest.fixture()
def oauth_store(tmp_path):
    """A temp OAuth store seeded with the hardcoded registry."""
    s = OAuthStore(tmp_path / "mcp_oauth.db")
    s.init_schema()
    seed_oauth_clients(s)
    yield s
    s.close()


@pytest.fixture(autouse=True)
def patch_store_and_secret(oauth_store, monkeypatch):
    """Point the router at the temp store and pin the JWT secret.

    The router opens a fresh store per request via get_oauth_store(); we patch
    it to return our seeded temp store (and stub close so the fixture owns the
    lifecycle). The JWT secret is pinned so minted tokens are deterministic.
    """
    monkeypatch.setattr("routers.oauth_mcp.get_oauth_store", lambda: oauth_store)
    monkeypatch.setattr(oauth_store, "close", lambda: None)
    monkeypatch.setattr("auth.oauth_provider._oauth_signing_secret", lambda: TEST_SECRET)
    monkeypatch.setenv("OAUTH_ISSUER", "https://ecm.test")


def _registered_redirect(store):
    return store.get_client(CLAUDE_DESKTOP_CLIENT_ID)["redirect_uris"][0]


# ───────────────────────── /authorize (admin-gated) ───────────────────────


class TestAuthorizeEndpoint:
    @pytest.mark.asyncio
    async def test_authorize_redirects_to_consent_when_auth_disabled(
        self, async_client, oauth_store
    ):
        """Open mode (auth disabled): /authorize validates + redirects to consent."""
        _, challenge = _pkce_pair()
        resp = await async_client.get(
            "/api/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "xyz",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/oauth/consent")

    @pytest.mark.asyncio
    async def test_authorize_rejects_plain_pkce(self, async_client, oauth_store):
        """buiqr.3 AC1 — plain code_challenge_method → 400 invalid_request."""
        _, challenge = _pkce_pair()
        resp = await async_client.get(
            "/api/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_challenge": challenge,
                "code_challenge_method": "plain",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    @pytest.mark.asyncio
    async def test_authorize_unknown_client(self, async_client):
        """buiqr.6 AC3 — unknown client_id → 400 invalid_client."""
        _, challenge = _pkce_pair()
        resp = await async_client.get(
            "/api/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": "attacker",
                "redirect_uri": "https://evil/cb",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_client"

    @pytest.mark.asyncio
    async def test_authorize_mismatched_redirect(self, async_client):
        """buiqr.6 AC4 — mismatched redirect_uri → 400 invalid_request, no redirect."""
        _, challenge = _pkce_pair()
        resp = await async_client.get(
            "/api/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "redirect_uri": "https://evil.example/cb",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    @pytest.mark.asyncio
    async def test_authorize_requires_admin_when_auth_enabled(
        self, async_client, oauth_store
    ):
        """buiqr.3 AC5 — anonymous /authorize when auth enabled → 401."""
        _, challenge = _pkce_pair()
        with patch("main.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            resp = await async_client.get(
                "/api/oauth/authorize",
                params={
                    "response_type": "code",
                    "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                    "redirect_uri": _registered_redirect(oauth_store),
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                },
                follow_redirects=False,
            )
        # The global auth middleware rejects the unauthenticated /api/* request.
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_authorize_allows_authenticated_admin(
        self, async_client, oauth_store, test_session
    ):
        """buiqr.3 AC5 — an authenticated admin reaches /authorize (302 consent)."""
        from auth.tokens import create_access_token
        from models import User

        admin = User(username="oauth-admin", is_active=True, is_admin=True)
        test_session.add(admin)
        test_session.commit()
        token = create_access_token(user_id=admin.id, username=admin.username)

        _, challenge = _pkce_pair()
        with patch("main.get_auth_settings") as mw_mock, \
                patch("auth.dependencies.get_auth_settings") as dep_mock:
            for m in (mw_mock, dep_mock):
                m.return_value.require_auth = True
                m.return_value.setup_complete = True
            resp = await async_client.get(
                "/api/oauth/authorize",
                params={
                    "response_type": "code",
                    "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                    "redirect_uri": _registered_redirect(oauth_store),
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                },
                headers={"Authorization": f"Bearer {token}"},
                follow_redirects=False,
            )
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/oauth/consent")

    @pytest.mark.asyncio
    async def test_authorize_rejects_non_admin(
        self, async_client, oauth_store, test_session
    ):
        """buiqr.3 AC5 — an authenticated NON-admin is rejected (403)."""
        from auth.tokens import create_access_token
        from models import User

        user = User(username="oauth-user", is_active=True, is_admin=False)
        test_session.add(user)
        test_session.commit()
        token = create_access_token(user_id=user.id, username=user.username)

        _, challenge = _pkce_pair()
        with patch("main.get_auth_settings") as mw_mock, \
                patch("auth.dependencies.get_auth_settings") as dep_mock:
            for m in (mw_mock, dep_mock):
                m.return_value.require_auth = True
                m.return_value.setup_complete = True
            resp = await async_client.get(
                "/api/oauth/authorize",
                params={
                    "response_type": "code",
                    "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                    "redirect_uri": _registered_redirect(oauth_store),
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                },
                headers={"Authorization": f"Bearer {token}"},
                follow_redirects=False,
            )
        assert resp.status_code == 403


# ───────────────────────── /authorize/approve → code ──────────────────────


class TestApproveAndToken:
    async def _approve(self, async_client, oauth_store, challenge, state="st-1"):
        """Drive the realistic flow: GET /authorize (binds the CSRF state to the
        admin session, bead buiqr.4 AC3) then POST /authorize/approve with the
        same state. Without the preceding /authorize the state is unbound and
        approve correctly rejects it as forged."""
        if state:
            authorize = await async_client.get(
                "/api/oauth/authorize",
                params={
                    "response_type": "code",
                    "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                    "redirect_uri": _registered_redirect(oauth_store),
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": state,
                },
                follow_redirects=False,
            )
            assert authorize.status_code == 302
        resp = await async_client.post(
            "/api/oauth/authorize/approve",
            data={
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            },
            follow_redirects=False,
        )
        return resp

    @pytest.mark.asyncio
    async def test_approve_redirects_with_code(self, async_client, oauth_store):
        _, challenge = _pkce_pair()
        resp = await self._approve(async_client, oauth_store, challenge)
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert _registered_redirect(oauth_store).split("?")[0] in loc
        assert "code=" in loc
        assert "state=st-1" in loc

    @pytest.mark.asyncio
    async def test_full_code_flow_issues_tokens(self, async_client, oauth_store):
        """buiqr.3 AC3 — end-to-end approve → /token returns Bearer + refresh."""
        verifier, challenge = _pkce_pair()
        approve = await self._approve(async_client, oauth_store, challenge)
        # Extract the code from the redirect Location query.
        from urllib.parse import parse_qs, urlparse
        code = parse_qs(urlparse(approve.headers["location"]).query)["code"][0]

        resp = await async_client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "code": code,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_verifier": verifier,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "Bearer"
        assert body["access_token"]
        assert body["refresh_token"]
        assert resp.headers.get("cache-control") == "no-store"

    @pytest.mark.asyncio
    async def test_code_replay_rejected(self, async_client, oauth_store):
        """buiqr.3 AC2 — replaying a consumed code → 400 invalid_grant."""
        verifier, challenge = _pkce_pair()
        approve = await self._approve(async_client, oauth_store, challenge)
        from urllib.parse import parse_qs, urlparse
        code = parse_qs(urlparse(approve.headers["location"]).query)["code"][0]
        payload = {
            "grant_type": "authorization_code",
            "client_id": CLAUDE_DESKTOP_CLIENT_ID,
            "code": code,
            "redirect_uri": _registered_redirect(oauth_store),
            "code_verifier": verifier,
        }
        first = await async_client.post("/api/oauth/token", data=payload)
        assert first.status_code == 200
        replay = await async_client.post("/api/oauth/token", data=payload)
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"

    @pytest.mark.asyncio
    async def test_refresh_rotation_and_reuse(self, async_client, oauth_store):
        """buiqr.3 AC4 — refresh rotates; reuse → 400 invalid_grant over HTTP."""
        verifier, challenge = _pkce_pair()
        approve = await self._approve(async_client, oauth_store, challenge)
        from urllib.parse import parse_qs, urlparse
        code = parse_qs(urlparse(approve.headers["location"]).query)["code"][0]
        tok = await async_client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "code": code,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_verifier": verifier,
            },
        )
        old_refresh = tok.json()["refresh_token"]
        rotate = await async_client.post(
            "/api/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "refresh_token": old_refresh,
            },
        )
        assert rotate.status_code == 200
        assert rotate.json()["refresh_token"] != old_refresh
        # Reusing the old (rotated) refresh token → 400 invalid_grant + family kill.
        reuse = await async_client.post(
            "/api/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "refresh_token": old_refresh,
            },
        )
        assert reuse.status_code == 400
        assert reuse.json()["error"] == "invalid_grant"

    @pytest.mark.asyncio
    async def test_unsupported_grant_type(self, async_client):
        resp = await async_client.post(
            "/api/oauth/token",
            data={"grant_type": "password", "client_id": CLAUDE_DESKTOP_CLIENT_ID},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "unsupported_grant_type"

    @pytest.mark.asyncio
    async def test_token_missing_params(self, async_client):
        resp = await async_client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"


# ───────────────────────── public-endpoint wiring (AC6) ───────────────────


class TestPublicEndpoints:
    @pytest.mark.asyncio
    async def test_token_is_auth_exempt(self, async_client):
        """buiqr.3 AC6 — /api/oauth/token is public even when auth is enabled."""
        from main import AUTH_EXEMPT_PATHS
        assert "/api/oauth/token" in AUTH_EXEMPT_PATHS
        with patch("main.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            resp = await async_client.post(
                "/api/oauth/token",
                data={"grant_type": "password", "client_id": CLAUDE_DESKTOP_CLIENT_ID},
            )
        # Reaches the handler (not 401 from the middleware) → OAuth error instead.
        assert resp.status_code == 400
        assert resp.json()["error"] == "unsupported_grant_type"

    @pytest.mark.asyncio
    async def test_revoke_is_auth_exempt(self):
        from main import AUTH_EXEMPT_PATHS
        assert "/api/oauth/revoke" in AUTH_EXEMPT_PATHS

    @pytest.mark.asyncio
    async def test_revoke_unknown_token_returns_200(self, async_client):
        """RFC 7009 — revoking an unknown token still returns 200."""
        resp = await async_client.post(
            "/api/oauth/revoke", data={"token": "never-issued"}
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_revoke_real_token(self, async_client, oauth_store):
        verifier, challenge = _pkce_pair()
        # Drive the full /authorize → approve flow so the CSRF state is bound
        # (bead buiqr.4 AC3) before the approval mints a code.
        await async_client.get(
            "/api/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "revoke-flow",
            },
            follow_redirects=False,
        )
        approve = await async_client.post(
            "/api/oauth/authorize/approve",
            data={
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "revoke-flow",
            },
            follow_redirects=False,
        )
        from urllib.parse import parse_qs, urlparse
        code = parse_qs(urlparse(approve.headers["location"]).query)["code"][0]
        tok = await async_client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "code": code,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_verifier": verifier,
            },
        )
        access = tok.json()["access_token"]
        rec = oauth_store.get_access_token(access)
        resp = await async_client.post("/api/oauth/revoke", data={"token": access})
        assert resp.status_code == 200
        assert oauth_store.is_jti_revoked(rec["jti"]) is True


# ─────────── buiqr.4 AC3: CSRF state binding over HTTP ─────────────────────


class TestConsentStateCsrf:
    """bead buiqr.4 AC3 — the consent-CSRF control end-to-end through HTTP.

    ``/authorize`` binds the ``state`` to the admin session; ``/authorize/approve``
    must present a matching, live binding. A forged/mismatched/missing/replayed
    state on approve returns 400 BEFORE any code is minted.
    """

    async def _authorize(self, async_client, oauth_store, challenge, state):
        return await async_client.get(
            "/api/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            },
            follow_redirects=False,
        )

    async def _approve(self, async_client, oauth_store, challenge, state):
        return await async_client.post(
            "/api/oauth/authorize/approve",
            data={
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            },
            follow_redirects=False,
        )

    @pytest.mark.asyncio
    async def test_matching_state_approves(self, async_client, oauth_store):
        """The state bound at /authorize is accepted at /approve → 302 + code."""
        _, challenge = _pkce_pair()
        await self._authorize(async_client, oauth_store, challenge, "match-1")
        resp = await self._approve(async_client, oauth_store, challenge, "match-1")
        assert resp.status_code == 302
        assert "code=" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_mismatched_state_rejected_400(self, async_client, oauth_store):
        """buiqr.4 AC3 — approve with a DIFFERENT state than bound → 400."""
        _, challenge = _pkce_pair()
        await self._authorize(async_client, oauth_store, challenge, "bound-state")
        resp = await self._approve(async_client, oauth_store, challenge, "attacker-state")
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    @pytest.mark.asyncio
    async def test_forged_state_without_authorize_rejected_400(
        self, async_client, oauth_store
    ):
        """A state never bound at /authorize (no preceding GET) → 400."""
        _, challenge = _pkce_pair()
        resp = await self._approve(async_client, oauth_store, challenge, "never-bound")
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    @pytest.mark.asyncio
    async def test_missing_state_on_approve_rejected_400(self, async_client, oauth_store):
        """An approve with no state at all → 400 (binding is mandatory)."""
        _, challenge = _pkce_pair()
        await self._authorize(async_client, oauth_store, challenge, "had-a-state")
        resp = await self._approve(async_client, oauth_store, challenge, "")
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    @pytest.mark.asyncio
    async def test_state_replay_rejected_400(self, async_client, oauth_store):
        """Replaying the same approve (single-use binding) → 400 the second time."""
        _, challenge = _pkce_pair()
        await self._authorize(async_client, oauth_store, challenge, "replay-state")
        first = await self._approve(async_client, oauth_store, challenge, "replay-state")
        assert first.status_code == 302
        second = await self._approve(async_client, oauth_store, challenge, "replay-state")
        assert second.status_code == 400
        assert second.json()["error"] == "invalid_request"


# ─────────── buiqr.4 AC2: open-redirect / unregistered redirect_uri ────────


class TestUnregisteredRedirectRegression:
    """bead buiqr.4 AC2 — already enforced by buiqr.3's exact-match RD1 check;
    these regression tests confirm an unregistered redirect_uri is rejected with
    400 and the attacker URI is NEVER used as a redirect target (open-redirect
    guard). The provider-level exact-match suite lives in
    tests/unit/test_oauth_provider.py::TestRedirectUriExactMatch.
    """

    @pytest.mark.asyncio
    async def test_unregistered_redirect_uri_400_no_open_redirect(self, async_client):
        """Unregistered redirect_uri → 400 invalid_request, NOT a 302 to the attacker."""
        _, challenge = _pkce_pair()
        resp = await async_client.get(
            "/api/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "redirect_uri": "https://evil.example/steal",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "x",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"
        # The attacker URI must NEVER appear in a Location header (no open redirect).
        assert "location" not in {k.lower() for k in resp.headers}

    @pytest.mark.asyncio
    async def test_approve_with_unregistered_redirect_uri_rejected(
        self, async_client, oauth_store
    ):
        """Even at /approve, an unregistered redirect_uri is rejected before any code."""
        _, challenge = _pkce_pair()
        # Bind a state first so we get past CSRF and reach the redirect check.
        await async_client.get(
            "/api/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "approve-rd",
            },
            follow_redirects=False,
        )
        resp = await async_client.post(
            "/api/oauth/authorize/approve",
            data={
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "redirect_uri": "https://evil.example/steal",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "approve-rd",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"


# ─────────── buiqr.4 AC4: tokens hashed at rest (regression) ───────────────


class TestTokensHashedAtRest:
    """bead buiqr.4 AC4 — already enforced by buiqr.2's oauth_store.hash_secret();
    this regression confirms an end-to-end HTTP issuance writes only SHA-256
    hashes to mcp_oauth.db — the raw access/refresh token never appears in any
    column or in the raw DB bytes.
    """

    @pytest.mark.asyncio
    async def test_issued_tokens_never_persisted_in_plaintext(
        self, async_client, oauth_store, tmp_path
    ):
        import hashlib as _hashlib

        verifier, challenge = _pkce_pair()
        await async_client.get(
            "/api/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "hash-flow",
            },
            follow_redirects=False,
        )
        approve = await async_client.post(
            "/api/oauth/authorize/approve",
            data={
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "hash-flow",
            },
            follow_redirects=False,
        )
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(approve.headers["location"]).query)["code"][0]
        tok = await async_client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "code": code,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_verifier": verifier,
            },
        )
        body = tok.json()
        access, refresh = body["access_token"], body["refresh_token"]

        # The stored columns hold the SHA-256 hash, not the raw token value.
        access_rows = oauth_store._conn.execute(
            "SELECT token_hash FROM access_tokens"
        ).fetchall()
        refresh_rows = oauth_store._conn.execute(
            "SELECT token_hash FROM refresh_tokens"
        ).fetchall()
        access_hashes = {r["token_hash"] for r in access_rows}
        refresh_hashes = {r["token_hash"] for r in refresh_rows}
        assert _hashlib.sha256(access.encode()).hexdigest() in access_hashes
        assert _hashlib.sha256(refresh.encode()).hexdigest() in refresh_hashes
        # The raw token strings must NOT appear in any stored hash column.
        assert access not in access_hashes
        assert refresh not in refresh_hashes

        # And the raw bytes on disk contain neither plaintext token (the WAL is
        # checkpointed by closing the connection first via a fresh reader).
        db_bytes = oauth_store.db_path.read_bytes()
        wal_path = oauth_store.db_path.with_name(oauth_store.db_path.name + "-wal")
        wal_bytes = wal_path.read_bytes() if wal_path.exists() else b""
        haystack = db_bytes + wal_bytes
        assert access.encode() not in haystack
        assert refresh.encode() not in haystack
