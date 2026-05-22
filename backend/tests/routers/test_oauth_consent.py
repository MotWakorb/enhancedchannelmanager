"""Router-level tests for the consent + Active-Grants surface (bead buiqr.7).

These exercise the HTTP shape of the new ``routers/oauth_mcp.py`` endpoints that
back the consent screen and the Active Grants Settings sub-section:

  GET    /api/oauth/authorize/consent-context — registry-pinned client name +
         returning-user signal + open-redirect-guarded return_to (CP1, OR1).
  GET    /api/oauth/grants                     — list the admin's active grants.
  DELETE /api/oauth/grants/{id}                — revoke a grant (204 / 404).

The consent SCREEN itself is a frontend route (``/oauth/consent``) served by the
SPA catch-all; its anti-framing header (CP1) is asserted in the main app tests.
"""
import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from auth.oauth_clients import (
    CLAUDE_CODE_CLIENT_ID,
    CLAUDE_DESKTOP_CLIENT_ID,
    seed_oauth_clients,
)
from auth.oauth_store import OAuthStore

# MCP OAuth offering RETIRED (bd-9axgc). The OAuth AS router (consent-context +
# grants endpoints) is no longer registered, so /api/oauth/* returns 404. Every
# test here exercises those now-disabled endpoints, so the whole module is
# skipped. Re-enable when MCP OAuth is re-offered.
pytestmark = pytest.mark.skip(
    reason="MCP OAuth offering retired (bd-9axgc); /api/oauth/* endpoints unregistered → 404. Re-enable when MCP OAuth is re-offered."
)


TEST_SECRET = "z" * 48


def _pkce_pair():
    verifier = "consent-verifier-9876543210-zyxwvutsrqponmlkjihgfedcba"
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
    monkeypatch.setattr(
        "auth.oauth_provider._oauth_signing_secret", lambda: TEST_SECRET
    )
    monkeypatch.setenv("OAUTH_ISSUER", "https://ecm.test")


def _registered_redirect(store):
    return store.get_client(CLAUDE_DESKTOP_CLIENT_ID)["redirect_uris"][0]


async def _issue_grant(async_client, oauth_store, state="grant-state"):
    """Drive a full authorize → approve → token flow to create one grant.

    Returns the token-endpoint JSON body (access_token + refresh_token).
    """
    verifier, challenge = _pkce_pair()
    redirect = _registered_redirect(oauth_store)
    authorize = await async_client.get(
        "/api/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": CLAUDE_DESKTOP_CLIENT_ID,
            "redirect_uri": redirect,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        },
        follow_redirects=False,
    )
    assert authorize.status_code == 302
    approve = await async_client.post(
        "/api/oauth/authorize/approve",
        data={
            "client_id": CLAUDE_DESKTOP_CLIENT_ID,
            "redirect_uri": redirect,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        },
        follow_redirects=False,
    )
    assert approve.status_code == 302
    code = parse_qs(urlparse(approve.headers["location"]).query)["code"][0]
    tok = await async_client.post(
        "/api/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": CLAUDE_DESKTOP_CLIENT_ID,
            "code": code,
            "redirect_uri": redirect,
            "code_verifier": verifier,
        },
    )
    assert tok.status_code == 200
    return tok.json()


# ───────────────────── GET /authorize/consent-context ──────────────────────


class TestConsentContext:
    @pytest.mark.asyncio
    async def test_returns_registry_pinned_client_name(self, async_client):
        """CP1 — the client name comes from the registry, not the query input."""
        resp = await async_client.get(
            "/api/oauth/authorize/consent-context",
            params={"client_id": CLAUDE_DESKTOP_CLIENT_ID},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["client_name"] == "Claude Desktop"
        assert body["scope"] == "mcp"
        assert body["already_connected"] is False

    @pytest.mark.asyncio
    async def test_unknown_client_rejected(self, async_client):
        """An unregistered client_id → 400 invalid_client (no name to reflect)."""
        resp = await async_client.get(
            "/api/oauth/authorize/consent-context",
            params={"client_id": "Claude Desktop (Official)"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_client"

    @pytest.mark.asyncio
    async def test_already_connected_when_grant_exists(
        self, async_client, oauth_store
    ):
        """bead buiqr.7 (c) — an existing grant flips already_connected to true."""
        await _issue_grant(async_client, oauth_store)
        resp = await async_client.get(
            "/api/oauth/authorize/consent-context",
            params={"client_id": CLAUDE_DESKTOP_CLIENT_ID},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["already_connected"] is True
        assert body["existing_grant"]["client_name"] == "Claude Desktop"

    @pytest.mark.asyncio
    async def test_off_origin_return_to_defaulted(self, async_client):
        """OR1 — an off-origin return_to is replaced with the safe internal default."""
        resp = await async_client.get(
            "/api/oauth/authorize/consent-context",
            params={
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "return_to": "https://evil.example/phish",
            },
        )
        assert resp.status_code == 200
        rt = resp.json()["return_to"]
        assert not rt.startswith("http")
        assert rt.startswith("/")

    @pytest.mark.asyncio
    async def test_same_origin_return_to_preserved(self, async_client):
        """OR1 — a same-origin relative return_to is honored unchanged."""
        resp = await async_client.get(
            "/api/oauth/authorize/consent-context",
            params={
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "return_to": "/?tab=settings&section=mcp",
            },
        )
        assert resp.json()["return_to"] == "/?tab=settings&section=mcp"

    @pytest.mark.asyncio
    async def test_protocol_relative_return_to_defaulted(self, async_client):
        """OR1 — a //host protocol-relative return_to is treated as off-origin."""
        resp = await async_client.get(
            "/api/oauth/authorize/consent-context",
            params={
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "return_to": "//evil.example/x",
            },
        )
        assert resp.json()["return_to"].startswith("/?")


# ───────────────────────── GET /grants + DELETE ────────────────────────────


class TestGrantsEndpoints:
    @pytest.mark.asyncio
    async def test_empty_when_no_grants(self, async_client):
        resp = await async_client.get("/api/oauth/grants")
        assert resp.status_code == 200
        assert resp.json() == {"grants": []}

    @pytest.mark.asyncio
    async def test_lists_active_grant(self, async_client, oauth_store):
        await _issue_grant(async_client, oauth_store)
        resp = await async_client.get("/api/oauth/grants")
        assert resp.status_code == 200
        grants = resp.json()["grants"]
        assert len(grants) == 1
        g = grants[0]
        assert g["client_name"] == "Claude Desktop"
        assert g["client_id"] == CLAUDE_DESKTOP_CLIENT_ID
        assert "id" in g and g["granted_at"] and g["last_used"]
        # No token values/hashes ever leave the store.
        assert "token" not in g and "token_hash" not in g

    @pytest.mark.asyncio
    async def test_revoke_grant_removes_it(self, async_client, oauth_store):
        await _issue_grant(async_client, oauth_store)
        listing = await async_client.get("/api/oauth/grants")
        grant_id = listing.json()["grants"][0]["id"]

        revoke = await async_client.delete(f"/api/oauth/grants/{grant_id}")
        assert revoke.status_code == 204

        after = await async_client.get("/api/oauth/grants")
        assert after.json()["grants"] == []

    @pytest.mark.asyncio
    async def test_revoke_unknown_grant_404(self, async_client):
        resp = await async_client.delete("/api/oauth/grants/does-not-exist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_revoked_refresh_cannot_rotate(self, async_client, oauth_store):
        """Revoking a grant kills the refresh family — rotation now fails."""
        tokens = await _issue_grant(async_client, oauth_store)
        listing = await async_client.get("/api/oauth/grants")
        grant_id = listing.json()["grants"][0]["id"]
        await async_client.delete(f"/api/oauth/grants/{grant_id}")

        rotate = await async_client.post(
            "/api/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "refresh_token": tokens["refresh_token"],
            },
        )
        assert rotate.status_code == 400
        assert rotate.json()["error"] == "invalid_grant"


# ─────────────────── admin gating (auth enabled) ───────────────────────────


class TestGrantsAdminGating:
    @pytest.mark.asyncio
    async def test_grants_requires_auth_when_enabled(self, async_client):
        """The grants endpoints are NOT in AUTH_EXEMPT_PATHS — anon → 401."""
        from unittest.mock import patch

        with patch("main.get_auth_settings") as mw:
            mw.return_value.require_auth = True
            mw.return_value.setup_complete = True
            resp = await async_client.get("/api/oauth/grants")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_revoke_requires_auth_when_enabled(self, async_client):
        from unittest.mock import patch

        with patch("main.get_auth_settings") as mw:
            mw.return_value.require_auth = True
            mw.return_value.setup_complete = True
            resp = await async_client.delete("/api/oauth/grants/anything")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_consent_context_requires_auth_when_enabled(self, async_client):
        from unittest.mock import patch

        with patch("main.get_auth_settings") as mw:
            mw.return_value.require_auth = True
            mw.return_value.setup_complete = True
            resp = await async_client.get(
                "/api/oauth/authorize/consent-context",
                params={"client_id": CLAUDE_DESKTOP_CLIENT_ID},
            )
        assert resp.status_code == 401
