"""Unit tests for the OAuth AS provider + client registry (beads buiqr.3 + buiqr.6).

These cover the security-critical token-issuance core in isolation (no HTTP):
PKCE S256 verification, single-use codes, exact-match redirect_uri, HS256 JWT
shape/TTL, and refresh rotation + reuse detection. The router-level tests
(tests/routers/test_oauth_mcp.py) cover the HTTP surface + admin gating + the
AUTH_EXEMPT_PATHS wiring.

Acceptance criteria mapped:
  buiqr.3 AC1 — PKCE S256 enforced; plain rejected 400 invalid_request.
  buiqr.3 AC2 — auth code single-use; replay → 400 invalid_grant.
  buiqr.3 AC3 — access JWT HS256, aud=ecm-mcp, iss=OAUTH_ISSUER, exp ≤ 15 min.
  buiqr.3 AC4 — refresh rotation-on-use; reuse invalidates the family.
  buiqr.6 AC2 — two clients seeded with pinned redirect_uri.
  buiqr.6 AC3 — unknown client_id → invalid_client.
  buiqr.6 AC4 — mismatched redirect_uri → invalid_request.
  threat model T3/RD1/SP4/SP2/EP1.
"""
import base64
import hashlib
import time

import pytest
from jose import jwt

from auth.oauth_clients import (
    CLAUDE_CODE_CLIENT_ID,
    CLAUDE_DESKTOP_CLIENT_ID,
    HARDCODED_CLIENTS,
    seed_oauth_clients,
)
from auth.oauth_provider import (
    ACCESS_TOKEN_TTL_SECONDS,
    AUDIENCE,
    MCP_SCOPE,
    OAuthError,
    OAuthProvider,
    verify_pkce_s256,
)
from auth.oauth_store import OAuthStore
from auth.tokens import ALGORITHM


TEST_ISSUER = "https://ecm.test"
TEST_SECRET = "x" * 48  # >= 32 bytes for HS256


@pytest.fixture()
def store(tmp_path):
    s = OAuthStore(tmp_path / "mcp_oauth.db")
    s.init_schema()
    seed_oauth_clients(s)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def provider(store, monkeypatch):
    """A provider whose OAuth signing secret is pinned so we can verify minted tokens."""
    monkeypatch.setattr("auth.oauth_provider._oauth_signing_secret", lambda: TEST_SECRET)
    return OAuthProvider(store, issuer=TEST_ISSUER)


def _pkce_pair():
    """Return (code_verifier, code_challenge) for a valid S256 pair."""
    verifier = "test-verifier-0123456789-abcdefghijklmnopqrstuvwx"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ───────────────────────── client registry (buiqr.6) ──────────────────────


class TestClientRegistry:
    def test_seed_is_idempotent(self, store):
        """buiqr.6 AC1 — re-seeding does not duplicate or error."""
        seed_oauth_clients(store)
        seed_oauth_clients(store)
        assert store.get_client(CLAUDE_DESKTOP_CLIENT_ID) is not None
        assert store.get_client(CLAUDE_CODE_CLIENT_ID) is not None

    def test_two_clients_present_with_pinned_redirects(self, store):
        """buiqr.6 AC2 — claude-desktop + claude-code, each with redirect_uris."""
        desktop = store.get_client(CLAUDE_DESKTOP_CLIENT_ID)
        code = store.get_client(CLAUDE_CODE_CLIENT_ID)
        assert desktop["client_name"] == "Claude Desktop"
        assert code["client_name"] == "Claude Code"
        assert len(desktop["redirect_uris"]) >= 1
        assert len(code["redirect_uris"]) >= 1

    def test_registry_has_exactly_two_entries(self):
        """No Dynamic Client Registration — the in-code registry is fixed."""
        assert len(HARDCODED_CLIENTS) == 2

    def test_unknown_client_returns_none(self, provider):
        assert provider.get_client("attacker-client") is None


class TestRedirectUriExactMatch:
    """buiqr.6 AC4 / threat model RD1 — exact-match only, no wildcard/prefix."""

    def test_exact_match_accepted(self, store):
        client = store.get_client(CLAUDE_DESKTOP_CLIENT_ID)
        good = client["redirect_uris"][0]
        OAuthProvider.validate_redirect_uri(client, good)  # no raise

    def test_unregistered_uri_rejected(self, store):
        client = store.get_client(CLAUDE_DESKTOP_CLIENT_ID)
        with pytest.raises(OAuthError) as ei:
            OAuthProvider.validate_redirect_uri(client, "https://evil.example/cb")
        assert ei.value.error == "invalid_request"

    def test_prefix_of_registered_uri_rejected(self, store):
        client = store.get_client(CLAUDE_DESKTOP_CLIENT_ID)
        good = client["redirect_uris"][0]
        with pytest.raises(OAuthError):
            OAuthProvider.validate_redirect_uri(client, good[:-3])

    def test_suffix_appended_rejected(self, store):
        client = store.get_client(CLAUDE_DESKTOP_CLIENT_ID)
        good = client["redirect_uris"][0]
        with pytest.raises(OAuthError):
            OAuthProvider.validate_redirect_uri(client, good + ".evil.com")


# ───────────────────────── PKCE (buiqr.3 AC1 / T3) ────────────────────────


class TestPKCE:
    def test_verify_s256_correct_pair(self):
        verifier, challenge = _pkce_pair()
        assert verify_pkce_s256(verifier, challenge) is True

    def test_verify_s256_wrong_verifier(self):
        _, challenge = _pkce_pair()
        assert verify_pkce_s256("wrong-verifier", challenge) is False

    def test_verify_s256_empty_inputs(self):
        assert verify_pkce_s256("", "x") is False
        assert verify_pkce_s256("x", "") is False

    def test_authorize_rejects_plain_method(self, provider):
        """buiqr.3 AC1 — plain → invalid_request."""
        _, challenge = _pkce_pair()
        with pytest.raises(OAuthError) as ei:
            provider.create_authorization_code(
                client_id=CLAUDE_DESKTOP_CLIENT_ID,
                redirect_uri=provider.get_client(CLAUDE_DESKTOP_CLIENT_ID)["redirect_uris"][0],
                code_challenge=challenge,
                code_challenge_method="plain",
                scope=MCP_SCOPE,
                user_sub="admin",
            )
        assert ei.value.error == "invalid_request"

    def test_authorize_rejects_missing_challenge(self, provider):
        with pytest.raises(OAuthError) as ei:
            provider.create_authorization_code(
                client_id=CLAUDE_DESKTOP_CLIENT_ID,
                redirect_uri=provider.get_client(CLAUDE_DESKTOP_CLIENT_ID)["redirect_uris"][0],
                code_challenge="",
                code_challenge_method="S256",
                scope=MCP_SCOPE,
                user_sub="admin",
            )
        assert ei.value.error == "invalid_request"


class TestAuthorizeValidation:
    def test_unknown_client_rejected(self, provider):
        """buiqr.6 AC3 / SP4 — unknown client_id → invalid_client."""
        _, challenge = _pkce_pair()
        with pytest.raises(OAuthError) as ei:
            provider.create_authorization_code(
                client_id="nope",
                redirect_uri="https://x",
                code_challenge=challenge,
                code_challenge_method="S256",
                scope=MCP_SCOPE,
                user_sub="admin",
            )
        assert ei.value.error == "invalid_client"

    def test_mismatched_redirect_rejected(self, provider):
        """buiqr.6 AC4 — mismatched redirect_uri → invalid_request."""
        _, challenge = _pkce_pair()
        with pytest.raises(OAuthError) as ei:
            provider.create_authorization_code(
                client_id=CLAUDE_DESKTOP_CLIENT_ID,
                redirect_uri="https://evil.example/cb",
                code_challenge=challenge,
                code_challenge_method="S256",
                scope=MCP_SCOPE,
                user_sub="admin",
            )
        assert ei.value.error == "invalid_request"

    def test_non_mcp_scope_rejected(self, provider):
        _, challenge = _pkce_pair()
        with pytest.raises(OAuthError) as ei:
            provider.create_authorization_code(
                client_id=CLAUDE_DESKTOP_CLIENT_ID,
                redirect_uri=provider.get_client(CLAUDE_DESKTOP_CLIENT_ID)["redirect_uris"][0],
                code_challenge=challenge,
                code_challenge_method="S256",
                scope="admin",
                user_sub="admin",
            )
        assert ei.value.error == "invalid_scope"


# ───────────────────────── code exchange (buiqr.3 AC2/AC3) ────────────────


def _issue_code(provider):
    verifier, challenge = _pkce_pair()
    redirect = provider.get_client(CLAUDE_DESKTOP_CLIENT_ID)["redirect_uris"][0]
    code = provider.create_authorization_code(
        client_id=CLAUDE_DESKTOP_CLIENT_ID,
        redirect_uri=redirect,
        code_challenge=challenge,
        code_challenge_method="S256",
        scope=MCP_SCOPE,
        user_sub="admin",
    )
    return code, verifier, redirect


class TestCodeExchange:
    def test_happy_path_issues_tokens(self, provider):
        code, verifier, redirect = _issue_code(provider)
        result = provider.exchange_authorization_code(
            client_id=CLAUDE_DESKTOP_CLIENT_ID,
            code=code,
            redirect_uri=redirect,
            code_verifier=verifier,
        )
        assert result["token_type"] == "Bearer"
        assert result["scope"] == MCP_SCOPE
        assert result["access_token"]
        assert result["refresh_token"]
        assert result["expires_in"] == ACCESS_TOKEN_TTL_SECONDS

    def test_access_token_is_hs256_jwt_with_required_claims(self, provider):
        """buiqr.3 AC3 — HS256, sub, aud=ecm-mcp, iss, scope=mcp, jti, exp ≤ 15m."""
        code, verifier, redirect = _issue_code(provider)
        result = provider.exchange_authorization_code(
            client_id=CLAUDE_DESKTOP_CLIENT_ID,
            code=code,
            redirect_uri=redirect,
            code_verifier=verifier,
        )
        header = jwt.get_unverified_header(result["access_token"])
        assert header["alg"] == ALGORITHM == "HS256"
        claims = jwt.decode(
            result["access_token"],
            TEST_SECRET,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=TEST_ISSUER,
        )
        assert claims["sub"] == "admin"
        assert claims["aud"] == AUDIENCE
        assert claims["iss"] == TEST_ISSUER
        assert claims["scope"] == MCP_SCOPE
        assert claims["jti"]
        assert claims["iat"] <= claims["exp"]
        # exp ≤ 15 min from iat
        assert claims["exp"] - claims["iat"] <= 15 * 60

    def test_token_not_signed_with_wrong_secret(self, provider):
        """SP2/T2 — the signature is over the real secret; a forgery fails."""
        code, verifier, redirect = _issue_code(provider)
        result = provider.exchange_authorization_code(
            client_id=CLAUDE_DESKTOP_CLIENT_ID,
            code=code,
            redirect_uri=redirect,
            code_verifier=verifier,
        )
        from jose import JWTError
        with pytest.raises(JWTError):
            jwt.decode(result["access_token"], "wrong-secret", algorithms=[ALGORITHM], audience=AUDIENCE)

    def test_code_single_use_replay_rejected(self, provider):
        """buiqr.3 AC2 — replay of a consumed code → invalid_grant."""
        code, verifier, redirect = _issue_code(provider)
        provider.exchange_authorization_code(
            client_id=CLAUDE_DESKTOP_CLIENT_ID,
            code=code,
            redirect_uri=redirect,
            code_verifier=verifier,
        )
        with pytest.raises(OAuthError) as ei:
            provider.exchange_authorization_code(
                client_id=CLAUDE_DESKTOP_CLIENT_ID,
                code=code,
                redirect_uri=redirect,
                code_verifier=verifier,
            )
        assert ei.value.error == "invalid_grant"

    def test_wrong_pkce_verifier_rejected(self, provider):
        """T3 — wrong verifier → invalid_grant, and the code is burned."""
        code, _verifier, redirect = _issue_code(provider)
        with pytest.raises(OAuthError) as ei:
            provider.exchange_authorization_code(
                client_id=CLAUDE_DESKTOP_CLIENT_ID,
                code=code,
                redirect_uri=redirect,
                code_verifier="totally-wrong-verifier",
            )
        assert ei.value.error == "invalid_grant"
        # Code was consumed before PKCE check — a retry with the right verifier
        # must also fail (no guess-the-verifier retry).
        with pytest.raises(OAuthError):
            provider.exchange_authorization_code(
                client_id=CLAUDE_DESKTOP_CLIENT_ID,
                code=code,
                redirect_uri=redirect,
                code_verifier=_verifier,
            )

    def test_redirect_uri_mismatch_at_token_rejected(self, provider):
        """The token request redirect_uri must match the authorize request."""
        code, verifier, _redirect = _issue_code(provider)
        # A DIFFERENT but still-registered redirect_uri must not be accepted.
        other = provider.get_client(CLAUDE_DESKTOP_CLIENT_ID)["redirect_uris"][1]
        with pytest.raises(OAuthError) as ei:
            provider.exchange_authorization_code(
                client_id=CLAUDE_DESKTOP_CLIENT_ID,
                code=code,
                redirect_uri=other,
                code_verifier=verifier,
            )
        assert ei.value.error == "invalid_grant"

    def test_code_bound_to_issuing_client(self, provider):
        """A code issued to claude-desktop can't be redeemed by claude-code."""
        code, verifier, redirect = _issue_code(provider)
        with pytest.raises(OAuthError) as ei:
            provider.exchange_authorization_code(
                client_id=CLAUDE_CODE_CLIENT_ID,
                code=code,
                redirect_uri=redirect,
                code_verifier=verifier,
            )
        # claude-code is a known client, but the code wasn't issued to it.
        assert ei.value.error == "invalid_grant"

    def test_expired_code_rejected(self, provider, monkeypatch):
        """buiqr.3 AC2 / store cap — an expired code → invalid_grant."""
        verifier, challenge = _pkce_pair()
        redirect = provider.get_client(CLAUDE_DESKTOP_CLIENT_ID)["redirect_uris"][0]
        past = int(time.time()) - 10_000
        code = provider.create_authorization_code(
            client_id=CLAUDE_DESKTOP_CLIENT_ID,
            redirect_uri=redirect,
            code_challenge=challenge,
            code_challenge_method="S256",
            scope=MCP_SCOPE,
            user_sub="admin",
            now=past,
        )
        with pytest.raises(OAuthError) as ei:
            provider.exchange_authorization_code(
                client_id=CLAUDE_DESKTOP_CLIENT_ID,
                code=code,
                redirect_uri=redirect,
                code_verifier=verifier,
            )
        assert ei.value.error == "invalid_grant"

    def test_unknown_grant_code_rejected(self, provider):
        with pytest.raises(OAuthError) as ei:
            provider.exchange_authorization_code(
                client_id=CLAUDE_DESKTOP_CLIENT_ID,
                code="never-issued",
                redirect_uri=provider.get_client(CLAUDE_DESKTOP_CLIENT_ID)["redirect_uris"][0],
                code_verifier="whatever",
            )
        assert ei.value.error == "invalid_grant"


# ───────────────────────── refresh rotation (buiqr.3 AC4) ─────────────────


class TestRefreshRotation:
    def test_rotation_issues_new_pair(self, provider):
        code, verifier, redirect = _issue_code(provider)
        first = provider.exchange_authorization_code(
            client_id=CLAUDE_DESKTOP_CLIENT_ID,
            code=code,
            redirect_uri=redirect,
            code_verifier=verifier,
        )
        rotated = provider.exchange_refresh_token(
            client_id=CLAUDE_DESKTOP_CLIENT_ID,
            refresh_token=first["refresh_token"],
        )
        assert rotated["access_token"]
        assert rotated["refresh_token"]
        assert rotated["refresh_token"] != first["refresh_token"]

    def test_refresh_reuse_kills_family(self, provider):
        """buiqr.3 AC4 — reuse of a rotated refresh token → invalid_grant + family kill."""
        code, verifier, redirect = _issue_code(provider)
        first = provider.exchange_authorization_code(
            client_id=CLAUDE_DESKTOP_CLIENT_ID,
            code=code,
            redirect_uri=redirect,
            code_verifier=verifier,
        )
        old_refresh = first["refresh_token"]
        second = provider.exchange_refresh_token(
            client_id=CLAUDE_DESKTOP_CLIENT_ID, refresh_token=old_refresh
        )
        # Replaying the OLD (consumed) refresh token → invalid_grant.
        with pytest.raises(OAuthError) as ei:
            provider.exchange_refresh_token(
                client_id=CLAUDE_DESKTOP_CLIENT_ID, refresh_token=old_refresh
            )
        assert ei.value.error == "invalid_grant"
        # The family is killed: the (legitimately rotated) successor is dead too.
        with pytest.raises(OAuthError):
            provider.exchange_refresh_token(
                client_id=CLAUDE_DESKTOP_CLIENT_ID, refresh_token=second["refresh_token"]
            )

    def test_unknown_refresh_token_rejected(self, provider):
        with pytest.raises(OAuthError) as ei:
            provider.exchange_refresh_token(
                client_id=CLAUDE_DESKTOP_CLIENT_ID, refresh_token="never-issued"
            )
        assert ei.value.error == "invalid_grant"

    def test_refresh_bound_to_client(self, provider):
        code, verifier, redirect = _issue_code(provider)
        first = provider.exchange_authorization_code(
            client_id=CLAUDE_DESKTOP_CLIENT_ID,
            code=code,
            redirect_uri=redirect,
            code_verifier=verifier,
        )
        with pytest.raises(OAuthError) as ei:
            provider.exchange_refresh_token(
                client_id=CLAUDE_CODE_CLIENT_ID, refresh_token=first["refresh_token"]
            )
        assert ei.value.error == "invalid_grant"


# ───────────────────────── revocation (RFC 7009) ──────────────────────────


class TestRevocation:
    def test_revoke_access_token_marks_jti(self, provider, store):
        code, verifier, redirect = _issue_code(provider)
        result = provider.exchange_authorization_code(
            client_id=CLAUDE_DESKTOP_CLIENT_ID,
            code=code,
            redirect_uri=redirect,
            code_verifier=verifier,
        )
        rec = store.get_access_token(result["access_token"])
        assert store.is_jti_revoked(rec["jti"]) is False
        provider.revoke_token(result["access_token"])
        assert store.is_jti_revoked(rec["jti"]) is True

    def test_revoke_refresh_kills_family(self, provider):
        code, verifier, redirect = _issue_code(provider)
        result = provider.exchange_authorization_code(
            client_id=CLAUDE_DESKTOP_CLIENT_ID,
            code=code,
            redirect_uri=redirect,
            code_verifier=verifier,
        )
        provider.revoke_token(result["refresh_token"])
        # After revoking the refresh family, rotation must fail.
        with pytest.raises(OAuthError):
            provider.exchange_refresh_token(
                client_id=CLAUDE_DESKTOP_CLIENT_ID, refresh_token=result["refresh_token"]
            )

    def test_revoke_unknown_token_is_noop(self, provider):
        provider.revoke_token("never-issued")  # must not raise (RFC 7009)


# ─────────────── buiqr.4 AC3: CSRF state binding (provider) ────────────────


class TestConsentStateCsrf:
    """Provider-level CSRF ``state`` binding (buiqr.4 AC3).

    ``bind_consent_state`` (called at /authorize) → ``verify_consent_state``
    (called at /authorize/approve). A missing/forged/mismatched/replayed/
    cross-session state raises ``OAuthError`` invalid_request (the router maps
    that to 400).
    """

    def test_bound_state_verifies(self, provider):
        provider.bind_consent_state(
            state="good", client_id=CLAUDE_DESKTOP_CLIENT_ID, user_sub="admin"
        )
        provider.verify_consent_state(state="good", user_sub="admin")  # no raise

    def test_missing_state_rejected(self, provider):
        """No state presented at approve → 400 invalid_request (mandatory binding)."""
        with pytest.raises(OAuthError) as ei:
            provider.verify_consent_state(state="", user_sub="admin")
        assert ei.value.error == "invalid_request"
        assert ei.value.status_code == 400

    def test_forged_state_rejected(self, provider):
        """A state never bound at /authorize → 400 invalid_request."""
        with pytest.raises(OAuthError) as ei:
            provider.verify_consent_state(state="forged", user_sub="admin")
        assert ei.value.error == "invalid_request"

    def test_state_for_other_subject_rejected(self, provider):
        """A state bound to admin-A cannot be redeemed by admin-B (cross-session)."""
        provider.bind_consent_state(
            state="cross", client_id=CLAUDE_DESKTOP_CLIENT_ID, user_sub="admin-A"
        )
        with pytest.raises(OAuthError):
            provider.verify_consent_state(state="cross", user_sub="admin-B")

    def test_state_is_single_use(self, provider):
        """Replaying an approved state → 400 (single-use)."""
        provider.bind_consent_state(
            state="once", client_id=CLAUDE_DESKTOP_CLIENT_ID, user_sub="admin"
        )
        provider.verify_consent_state(state="once", user_sub="admin")  # consumes it
        with pytest.raises(OAuthError):
            provider.verify_consent_state(state="once", user_sub="admin")

    def test_empty_state_binding_is_noop(self, provider):
        """Binding an empty state is a no-op; a later forged non-empty state still fails."""
        provider.bind_consent_state(
            state="", client_id=CLAUDE_DESKTOP_CLIENT_ID, user_sub="admin"
        )
        with pytest.raises(OAuthError):
            provider.verify_consent_state(state="anything", user_sub="admin")


class TestGrantsAndRevoke:
    """Provider-level Active Grants listing + grant revoke (bead buiqr.7).

    These verify the registry-pinned client name, the returning-user lookup, and
    that revoke kills the refresh family AND revokes the live access jti — reusing
    the established revocation primitives.
    """

    def _issue_grant(self, provider, store, *, user_sub="admin"):
        """Run a full code flow and return the resulting (access_token, refresh_token)."""
        verifier, challenge = _pkce_pair()
        redirect = store.get_client(CLAUDE_DESKTOP_CLIENT_ID)["redirect_uris"][0]
        code = provider.create_authorization_code(
            client_id=CLAUDE_DESKTOP_CLIENT_ID,
            redirect_uri=redirect,
            code_challenge=challenge,
            code_challenge_method="S256",
            scope=MCP_SCOPE,
            user_sub=user_sub,
        )
        tokens = provider.exchange_authorization_code(
            client_id=CLAUDE_DESKTOP_CLIENT_ID,
            code=code,
            redirect_uri=redirect,
            code_verifier=verifier,
        )
        return tokens

    def test_list_grants_pins_client_name_from_registry(self, provider, store):
        self._issue_grant(provider, store)
        grants = provider.list_grants(user_sub="admin")
        assert len(grants) == 1
        g = grants[0]
        # Display name is the REGISTRY value, not the client_id (CP1).
        assert g["client_name"] == "Claude Desktop"
        assert g["client_id"] == CLAUDE_DESKTOP_CLIENT_ID
        assert "id" in g and g["granted_at"] and g["last_used"]
        # No token values/hashes leak.
        assert "token" not in repr(g) and "hash" not in repr(g)

    def test_list_grants_scoped_to_subject(self, provider, store):
        self._issue_grant(provider, store, user_sub="admin-1")
        self._issue_grant(provider, store, user_sub="admin-2")
        assert len(provider.list_grants(user_sub="admin-1")) == 1
        assert len(provider.list_grants(user_sub="admin-2")) == 1
        # Without a subject filter, both are returned.
        assert len(provider.list_grants()) == 2

    def test_returning_user_detection(self, provider, store):
        assert (
            provider.get_active_grant_for_client(CLAUDE_DESKTOP_CLIENT_ID, "admin")
            is None
        )
        self._issue_grant(provider, store)
        existing = provider.get_active_grant_for_client(
            CLAUDE_DESKTOP_CLIENT_ID, "admin"
        )
        assert existing is not None
        assert existing["client_name"] == "Claude Desktop"

    def test_revoke_grant_kills_family_and_access_jti(self, provider, store):
        tokens = self._issue_grant(provider, store)
        grants = provider.list_grants(user_sub="admin")
        grant_id = grants[0]["id"]

        # The access token's jti is live before revoke.
        access = store.get_access_token(tokens["access_token"])
        assert access is not None
        assert store.is_jti_revoked(access["jti"]) is False

        assert provider.revoke_grant(grant_id=grant_id, user_sub="admin") is True

        # The grant is gone from the active list.
        assert provider.list_grants(user_sub="admin") == []
        # The access jti is now revoked.
        assert store.is_jti_revoked(access["jti"]) is True
        # The refresh family is dead — reuse raises and yields no new tokens.
        assert store.is_refresh_family_revoked(grant_id) is True

    def test_revoke_grant_unknown_id_returns_false(self, provider):
        assert provider.revoke_grant(grant_id="does-not-exist") is False

    def test_revoke_grant_wrong_subject_denied(self, provider, store):
        self._issue_grant(provider, store, user_sub="admin-1")
        grant_id = provider.list_grants(user_sub="admin-1")[0]["id"]
        # A different admin cannot revoke this grant.
        assert provider.revoke_grant(grant_id=grant_id, user_sub="admin-2") is False
        # Still active.
        assert len(provider.list_grants(user_sub="admin-1")) == 1
