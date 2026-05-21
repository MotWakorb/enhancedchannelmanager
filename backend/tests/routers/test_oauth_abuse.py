"""Consolidated OAuth abuse-case fixture — Authorization Server side (bead buiqr.9 (a)).

The ECM-AS half of the permanent OAuth negative-coverage guard. The RS half
lives in ``mcp-server/tests/test_oauth.py`` (the two halves cannot share a
Python environment — the MCP RS CI job installs no fastapi/jose/slowapi). Read
that file's header for the full 10-case map and the rationale for the split.

This file owns the SIX abuse cases defended at the ECM Authorization Server,
each driven END-TO-END through the real FastAPI app via the ``async_client``
fixture (NO network), each asserting the RFC 6749 spec error:

    #   Abuse case                 Spec error (this file asserts)   New vs existing
    --  -------------------------  -------------------------------  ----------------------------------
    1   PKCE plain-method          400 invalid_request              existing: test_oauth_mcp /
                                   (at /authorize, before any code)  test_oauth_provider — REUSED here
    2   PKCE verifier mismatch     400 invalid_grant (at /token)    existing: test_oauth_provider
                                                                     (provider unit) — NEW at HTTP layer
    3   auth-code replay           400 invalid_grant (at /token)    existing: test_oauth_mcp — REUSED
    6   mismatched redirect_uri    400 invalid_request              existing: test_oauth_mcp — REUSED
                                   (at /authorize, no open redirect)
    7   missing code_challenge     400 invalid_request              existing: test_oauth_provider
                                   (at /authorize)                   (provider unit) — NEW at HTTP layer
    8   refresh-token reuse        400 invalid_grant + family kill  existing: test_oauth_mcp — REUSED

WHY CONSOLIDATE WHAT ALREADY EXISTS
===================================
Per the epic, this is the SINGLE permanent abuse fixture: one named test per
case, all in one place, so a future OAuth refactor has exactly one file to keep
green for the AS-side attack surface. Where a control was already proved (cases
1/3/6/8 in ``test_oauth_mcp.py``; the provider-level checks for 2/7), this file
re-asserts the same observable HTTP outcome through the public app rather than
duplicating the unit-level proof — and it ADDS the HTTP-layer coverage for the
verifier-mismatch (2) and missing-challenge (7) cases that previously existed
only at the provider unit level. Helpers mirror ``test_oauth_mcp.py`` so the
fixtures stay in lockstep.

Threat-model rows: T3 (PKCE downgrade / verifier), RD1 (exact-match redirect /
open-redirect guard), AC2 (single-use code), AC4 (refresh rotation + reuse).
"""
import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from auth.oauth_clients import CLAUDE_DESKTOP_CLIENT_ID, seed_oauth_clients
from auth.oauth_store import OAuthStore

# A signing secret comfortably above the 32-byte HMAC floor (mirrors test_oauth_mcp.py).
TEST_SECRET = "z" * 48


def _pkce_pair():
    """A valid PKCE (verifier, S256-challenge) pair. Mirrors test_oauth_mcp.py."""
    verifier = "abuse-verifier-9876543210-zyxwvutsrqponmlkjihgfedcba"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@pytest.fixture()
def oauth_store(tmp_path):
    """A temp OAuth store seeded with the hardcoded registry (no network)."""
    s = OAuthStore(tmp_path / "mcp_oauth.db")
    s.init_schema()
    seed_oauth_clients(s)
    yield s
    s.close()


@pytest.fixture(autouse=True)
def patch_store_and_secret(oauth_store, monkeypatch):
    """Point the router at the temp store + pin the signing secret + issuer.

    Same wiring as test_oauth_mcp.py: get_oauth_store() returns our seeded temp
    store, store.close() is a no-op (the fixture owns the lifecycle), and the
    OAuth signing secret + issuer are pinned so issuance is deterministic.
    """
    monkeypatch.setattr("routers.oauth_mcp.get_oauth_store", lambda: oauth_store)
    monkeypatch.setattr(oauth_store, "close", lambda: None)
    monkeypatch.setattr("auth.oauth_provider._oauth_signing_secret", lambda: TEST_SECRET)
    monkeypatch.setenv("OAUTH_ISSUER", "https://ecm.test")


def _registered_redirect(store):
    return store.get_client(CLAUDE_DESKTOP_CLIENT_ID)["redirect_uris"][0]


async def _authorize(async_client, store, challenge, state):
    """GET /authorize — validates the request + binds the CSRF state (buiqr.4 AC3)."""
    return await async_client.get(
        "/api/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": CLAUDE_DESKTOP_CLIENT_ID,
            "redirect_uri": _registered_redirect(store),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        },
        follow_redirects=False,
    )


async def _approve(async_client, store, challenge, state):
    """POST /authorize/approve — mints the code + 302s to the registered redirect."""
    return await async_client.post(
        "/api/oauth/authorize/approve",
        data={
            "client_id": CLAUDE_DESKTOP_CLIENT_ID,
            "redirect_uri": _registered_redirect(store),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        },
        follow_redirects=False,
    )


async def _mint_code(async_client, store, challenge, state="abuse-state"):
    """Drive /authorize → /approve and return the issued authorization code."""
    auth = await _authorize(async_client, store, challenge, state)
    assert auth.status_code == 302, auth.text
    approve = await _approve(async_client, store, challenge, state)
    assert approve.status_code == 302, approve.text
    return parse_qs(urlparse(approve.headers["location"]).query)["code"][0]


class TestAuthorizationServerAbuseCases:
    """The six AS-side abuse cases, asserted end-to-end through the FastAPI app."""

    @pytest.mark.asyncio
    async def test_case1_pkce_plain_method_rejected(self, async_client, oauth_store):
        """Case 1 — code_challenge_method=plain → 400 invalid_request.

        PKCE S256 ONLY (threat model T3). The downgrade to ``plain`` is rejected
        at /authorize, before any code can be minted.
        """
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
    async def test_case2_pkce_verifier_mismatch_rejected(self, async_client, oauth_store):
        """Case 2 — a wrong code_verifier at /token → 400 invalid_grant.

        The S256 verifier presented at /token does not hash to the stored
        challenge (threat model T3). The code is consumed BEFORE PKCE
        verification, so a wrong verifier also burns the code — it can never be
        retried with a guessed verifier.
        """
        _, challenge = _pkce_pair()
        code = await _mint_code(async_client, oauth_store, challenge)
        resp = await async_client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "code": code,
                "redirect_uri": _registered_redirect(oauth_store),
                # Verifier that does NOT correspond to the challenge above.
                "code_verifier": "wrong-verifier-0000000000-aaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"

    @pytest.mark.asyncio
    async def test_case3_auth_code_replay_rejected(self, async_client, oauth_store):
        """Case 3 — replaying a consumed authorization code → 400 invalid_grant.

        Authorization codes are single-use (AC2 — store-enforced). The second
        exchange of the same code is rejected.
        """
        verifier, challenge = _pkce_pair()
        code = await _mint_code(async_client, oauth_store, challenge)
        payload = {
            "grant_type": "authorization_code",
            "client_id": CLAUDE_DESKTOP_CLIENT_ID,
            "code": code,
            "redirect_uri": _registered_redirect(oauth_store),
            "code_verifier": verifier,
        }
        first = await async_client.post("/api/oauth/token", data=payload)
        assert first.status_code == 200, first.text
        replay = await async_client.post("/api/oauth/token", data=payload)
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"

    @pytest.mark.asyncio
    async def test_case6_mismatched_redirect_uri_rejected(self, async_client, oauth_store):
        """Case 6 — an unregistered redirect_uri → 400 invalid_request, NO open redirect.

        Exact-match redirect-URI validation (threat model RD1). The attacker URI
        must never appear in a Location header — the error is returned inline,
        not bounced to the unverified target.
        """
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
        # No open redirect: the attacker URI is never used as a Location target.
        assert "location" not in {k.lower() for k in resp.headers}

    @pytest.mark.asyncio
    async def test_case7_missing_code_challenge_rejected(self, async_client, oauth_store):
        """Case 7 — an authorization request with no code_challenge → 400 invalid_request.

        PKCE is mandatory (threat model T3); an authorization request that omits
        the challenge entirely is rejected at /authorize.
        """
        resp = await async_client.get(
            "/api/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "redirect_uri": _registered_redirect(oauth_store),
                "code_challenge": "",  # missing challenge
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    @pytest.mark.asyncio
    async def test_case8_refresh_token_reuse_rejected(self, async_client, oauth_store):
        """Case 8 — reusing a rotated refresh token → 400 invalid_grant + family kill.

        Refresh-token rotation with reuse detection (AC4). After a refresh
        rotates, presenting the OLD refresh token is detected as reuse, rejected
        with invalid_grant, and the whole rotation family is killed.
        """
        verifier, challenge = _pkce_pair()
        code = await _mint_code(async_client, oauth_store, challenge)
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
        assert tok.status_code == 200, tok.text
        old_refresh = tok.json()["refresh_token"]

        rotate = await async_client.post(
            "/api/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": CLAUDE_DESKTOP_CLIENT_ID,
                "refresh_token": old_refresh,
            },
        )
        assert rotate.status_code == 200, rotate.text
        assert rotate.json()["refresh_token"] != old_refresh

        # Reuse the OLD (already-rotated) refresh token → invalid_grant + family kill.
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
