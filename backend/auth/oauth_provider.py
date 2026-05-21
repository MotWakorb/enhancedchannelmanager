"""OAuth 2.1 Authorization Server provider logic (bead buiqr.3).

This is the security-critical core of the ECM Authorization Server: PKCE S256
verification, single-use authorization codes, exact-match redirect-URI
validation, HS256 access-token minting, and refresh-token rotation with
reuse detection. The FastAPI router (``routers/oauth_mcp.py``) is a thin shape
over this module; all the auth logic lives here so it is unit-testable without
HTTP and so the contract between the client registry (buiqr.6) and the AS
endpoints (buiqr.3) cannot drift (one owner).

It is shaped to satisfy MCP SDK 1.27.0's ``OAuthAuthorizationServerProvider``
Protocol (``get_client``, ``load_authorization_code``,
``exchange_authorization_code``, ``load_refresh_token``,
``exchange_refresh_token``, ``load_access_token``, ``revoke_token``). The HTTP
layer ECM exposes is ECM-native FastAPI (not the SDK's Starlette routes), but
the method names/semantics match the Protocol so a future swap to the SDK's
router is mechanical.

SECURITY CONTRACT (ADR-009 §3, threat model T3/RD1/SP4/SP2/CD1)
==============================================================
  - **PKCE S256 only.** ``code_challenge_method`` other than ``"S256"`` (incl.
    ``"plain"`` or missing) → :class:`OAuthError` ``invalid_request`` (T3).
  - **Authorization code single-use.** The store enforces single-use; a replay
    surfaces as :class:`OAuthError` ``invalid_grant`` (AC2).
  - **Exact-match redirect_uri.** Matched character-for-character against the
    registered client's allowlist — no prefix/substring/wildcard (RD1).
  - **Unknown client_id** → :class:`OAuthError` ``invalid_client`` (SP4).
  - **Access token = HS256 JWT.** ``sub`` = admin user id, ``aud`` = ``ecm-mcp``,
    ``iss`` = issuer, ``scope`` = ``mcp``, ``jti``, ``iat``, ``exp`` (≤ 15 min).
    Signed with the dedicated ``mcp_oauth_signing_secret`` (SP1/SP2/T2).
  - **Refresh rotation + reuse detection.** Delegated to
    :meth:`OAuthStore.rotate_refresh_token`; reuse → ``invalid_grant`` +
    family kill (T3).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from typing import Any, Optional

from jose import jwt

from auth.oauth_store import OAuthStore, RefreshTokenReuseError
from auth.tokens import ALGORITHM

logger = logging.getLogger(__name__)

# ── Protocol / token constants (ADR-009 §3) ─────────────────────────────────
#: The single scope offered in v1 (ADR-009 §3, §8).
MCP_SCOPE = "mcp"
#: Audience claim bound to the MCP Resource Server (threat model EP1).
AUDIENCE = "ecm-mcp"
#: PKCE method — S256 ONLY. "plain" is rejected (ADR-009 §3, threat model T3).
PKCE_S256 = "S256"
#: Access-token TTL: 15 minutes. Under Option A the TTL is the SOLE access-token
#: revocation backstop (ADR-009 §3/§5; bead buiqr.3 AC3 — exp ≤ 15 min).
ACCESS_TOKEN_TTL_SECONDS = 15 * 60
#: Refresh-token TTL: 30 days (the store caps at this ceiling too).
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
#: Authorization-code TTL: 10 minutes (the store caps at this ceiling too).
AUTH_CODE_TTL_SECONDS = 10 * 60


def get_oauth_issuer() -> str:
    """Resolve the OAuth issuer (``iss`` claim + discovery ``issuer``).

    Sourced from the ``OAUTH_ISSUER`` env var. There is no safe universal
    default for the issuer (it must be the operator's externally-reachable
    HTTPS origin, e.g. ``https://ecm.example.com``), so when unset we fall back
    to a clearly-non-production placeholder. The discovery endpoints (buiqr.5)
    own the HTTP/HTTPS posture (``oauth_allow_insecure``); this function only
    provides the string used in the ``iss``/``aud`` checks, which must be
    consistent between minting and verification.
    """
    return os.environ.get("OAUTH_ISSUER", "https://ecm.local")


class OAuthError(Exception):
    """An OAuth 2.1 protocol error carrying an RFC 6749 error code.

    The router maps :attr:`error` to the OAuth-standard JSON error body and the
    appropriate HTTP status (400 for ``invalid_request`` / ``invalid_grant`` /
    ``invalid_client`` / ``unsupported_grant_type``). Detail is short and
    OAuth-standard; verbose internals stay server-side (threat model ID4).
    """

    def __init__(self, error: str, description: str = "", status_code: int = 400):
        self.error = error
        self.description = description
        self.status_code = status_code
        super().__init__(f"{error}: {description}" if description else error)


def _now() -> int:
    return int(time.time())


def _new_token() -> str:
    """Generate a high-entropy opaque token value (codes / refresh tokens)."""
    return secrets.token_urlsafe(32)


def _new_jti() -> str:
    """Generate a unique token id, mirroring backend/auth/tokens.py jti style."""
    return secrets.token_urlsafe(16)


def verify_pkce_s256(code_verifier: str, code_challenge: str) -> bool:
    """Verify a PKCE S256 ``code_verifier`` against the stored ``code_challenge``.

    S256 (RFC 7636): ``challenge == BASE64URL-NO-PAD(SHA256(ASCII(verifier)))``.
    Constant-time comparison guards against timing oracles on the challenge.
    """
    if not code_verifier or not code_challenge:
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(expected, code_challenge)


class OAuthProvider:
    """ECM Authorization Server provider over the ECM-managed OAuth store.

    Stateless apart from the injected :class:`OAuthStore` and the issuer string;
    a new instance can be created per request (the store owns the connection).
    """

    def __init__(self, store: OAuthStore, issuer: Optional[str] = None):
        self.store = store
        self.issuer = issuer or get_oauth_issuer()

    # ── client registry (Protocol: get_client) ──────────────────────────────

    def get_client(self, client_id: str) -> Optional[dict[str, Any]]:
        """Return the registered client record, or None if unknown (SP4)."""
        return self.store.get_client(client_id)

    def _require_client(self, client_id: str) -> dict[str, Any]:
        client = self.store.get_client(client_id)
        if client is None:
            raise OAuthError("invalid_client", "unknown client_id")
        return client

    @staticmethod
    def validate_redirect_uri(client: dict[str, Any], redirect_uri: str) -> None:
        """Enforce EXACT-match redirect_uri against the client allowlist (RD1).

        No prefix, substring, or wildcard matching — the presented value must be
        byte-for-byte equal to a registered URI. Mismatch → ``invalid_request``.
        """
        allowed = client.get("redirect_uris") or []
        if redirect_uri not in allowed:
            raise OAuthError("invalid_request", "redirect_uri not registered for client")

    # ── /authorize (Protocol: authorize) ─────────────────────────────────────

    def create_authorization_code(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        scope: str,
        user_sub: str,
        now: Optional[int] = None,
    ) -> str:
        """Validate an authorization request and mint a single-use PKCE code.

        Enforces (in order): known client (SP4), exact-match redirect_uri (RD1),
        PKCE S256-only (T3), single ``mcp`` scope. Returns the raw authorization
        code (the store persists only its SHA-256 hash). The caller (router /
        consent flow, buiqr.7) is responsible for having authenticated the admin
        before calling this.
        """
        client = self._require_client(client_id)
        self.validate_redirect_uri(client, redirect_uri)

        # PKCE S256 ONLY. "plain", missing, or anything else → 400 invalid_request.
        if code_challenge_method != PKCE_S256:
            raise OAuthError(
                "invalid_request",
                "code_challenge_method must be S256 (plain is not supported)",
            )
        if not code_challenge:
            raise OAuthError("invalid_request", "missing code_challenge")

        # Single 'mcp' scope in v1. An empty scope defaults to mcp; any other
        # explicit scope is rejected.
        requested_scope = scope or MCP_SCOPE
        if requested_scope != MCP_SCOPE:
            raise OAuthError("invalid_scope", "only the 'mcp' scope is supported")

        code = _new_token()
        self.store.create_auth_code(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=PKCE_S256,
            scope=MCP_SCOPE,
            user_sub=user_sub,
            ttl_seconds=AUTH_CODE_TTL_SECONDS,
            now=now,
        )
        logger.info(
            "[OAUTH] Issued authorization code client=%s sub=%s", client_id, user_sub
        )
        return code

    # ── CSRF state binding (consent flow, bead buiqr.4 AC3) ──────────────────

    def bind_consent_state(
        self,
        *,
        state: str,
        client_id: str,
        user_sub: str,
        now: Optional[int] = None,
    ) -> None:
        """Bind the anti-CSRF ``state`` to the admin subject at ``/authorize``.

        Called once ``/authorize`` has validated the request AND confirmed the
        admin session, this records a single-use, short-lived (≤10 min) binding
        of ``state`` → ``user_sub`` in the store (state hashed at rest). The
        consent-approval step later proves it carried the *same* admin's state by
        consuming this binding (:meth:`verify_consent_state`). An empty state is
        a no-op (nothing to bind) — the approval step then has nothing to match,
        so a forged non-empty state on approve is still rejected.
        """
        if not state:
            return
        self.store.bind_consent_state(
            state=state, client_id=client_id, user_sub=user_sub, now=now
        )

    def verify_consent_state(
        self,
        *,
        state: str,
        user_sub: str,
        now: Optional[int] = None,
    ) -> None:
        """Verify + consume the CSRF ``state`` binding on consent approval.

        Raises :class:`OAuthError` ``invalid_request`` (400) when the presented
        ``state`` has no live binding for *this* admin subject — i.e. it is
        missing, forged, expired, already consumed (replay), or was bound to a
        different admin session (cross-session CSRF). On success the binding is
        consumed (single-use). This is the server-side seam the consent UI
        (buiqr.7) relies on: the UI round-trips the ``state`` it received and
        this method enforces the binding (bead buiqr.4 AC3, threat model CSRF1).
        """
        if not state or not self.store.consume_consent_state(
            state=state, user_sub=user_sub, now=now
        ):
            raise OAuthError(
                "invalid_request",
                "state parameter is missing or does not match the authorization request",
            )

    # ── token minting ────────────────────────────────────────────────────────

    def _mint_access_token(
        self, *, user_sub: str, client_id: str, scope: str, now: int
    ) -> tuple[str, str]:
        """Mint an HS256 access-token JWT and persist its hashed audit record.

        Returns ``(token, jti)``. Claims: ``sub`` (admin), ``aud=ecm-mcp``,
        ``iss``, ``scope=mcp``, ``jti``, ``iat``, ``exp`` (now + 15 min).
        Signed with the dedicated OAuth signing secret (SP1/SP2/T2).
        """
        jti = _new_jti()
        exp = now + ACCESS_TOKEN_TTL_SECONDS
        payload = {
            "sub": user_sub,
            "aud": AUDIENCE,
            "iss": self.issuer,
            "scope": scope,
            "jti": jti,
            "iat": now,
            "exp": exp,
            "token_type": "access",
        }
        token = jwt.encode(payload, _oauth_signing_secret(), algorithm=ALGORITHM)
        self.store.store_access_token(
            token=token,
            jti=jti,
            client_id=client_id,
            user_sub=user_sub,
            scope=scope,
            ttl_seconds=ACCESS_TOKEN_TTL_SECONDS,
            now=now,
        )
        return token, jti

    def _mint_refresh_token(
        self,
        *,
        user_sub: str,
        client_id: str,
        scope: str,
        family_id: str,
        now: int,
    ) -> tuple[str, str]:
        """Mint an opaque refresh token and persist its hashed record.

        Returns ``(token, jti)``. Refresh tokens are opaque (NOT JWTs): they are
        only ever presented back to ECM's ``/token`` and looked up by hash, so
        there is no value in making them self-describing, and an opaque value
        keeps them out of the JWT-shaped routing path on the RS.
        """
        token = _new_token()
        jti = _new_jti()
        self.store.store_refresh_token(
            token=token,
            jti=jti,
            family_id=family_id,
            client_id=client_id,
            user_sub=user_sub,
            scope=scope,
            ttl_seconds=REFRESH_TOKEN_TTL_SECONDS,
            now=now,
        )
        return token, jti

    # ── /token: authorization_code grant (Protocol: exchange_authorization_code)

    def exchange_authorization_code(
        self,
        *,
        client_id: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        now: Optional[int] = None,
    ) -> dict[str, Any]:
        """Exchange an auth code + PKCE verifier for access + refresh tokens.

        Enforces (in order): known client (SP4); single-use, unexpired code
        (AC2 — store-enforced); the code was issued to THIS client and THIS
        redirect_uri (RD1 / binding); PKCE S256 verifier matches the stored
        challenge (T3). On any failure → ``invalid_grant`` / ``invalid_request``.

        IMPORTANT: the code is consumed (single-use) BEFORE PKCE verification, so
        a replay or a wrong verifier both burn the code — a code can never be
        retried with a guessed verifier.
        """
        now = _now() if now is None else now
        self._require_client(client_id)

        grant = self.store.consume_auth_code(code, now=now)
        if grant is None:
            # Unknown, expired, or already-consumed (replay) — all invalid_grant.
            raise OAuthError("invalid_grant", "authorization code invalid or expired")

        # The code is bound to the client and redirect_uri it was issued for.
        if grant["client_id"] != client_id:
            raise OAuthError("invalid_grant", "code was not issued to this client")
        if grant["redirect_uri"] != redirect_uri:
            raise OAuthError("invalid_grant", "redirect_uri does not match the authorization request")

        # PKCE S256 verification (the stored method is always S256 by construction).
        if grant["code_challenge_method"] != PKCE_S256:
            raise OAuthError("invalid_grant", "unsupported code_challenge_method")
        if not verify_pkce_s256(code_verifier, grant["code_challenge"]):
            raise OAuthError("invalid_grant", "PKCE verification failed")

        user_sub = grant["user_sub"]
        scope = grant["scope"]
        family_id = _new_jti()  # a fresh refresh-rotation family per grant

        access_token, _ = self._mint_access_token(
            user_sub=user_sub, client_id=client_id, scope=scope, now=now
        )
        refresh_token, _ = self._mint_refresh_token(
            user_sub=user_sub,
            client_id=client_id,
            scope=scope,
            family_id=family_id,
            now=now,
        )
        logger.info("[OAUTH] Issued tokens via code grant client=%s sub=%s", client_id, user_sub)
        return _token_response(access_token, refresh_token, scope)

    # ── /token: refresh_token grant (Protocol: exchange_refresh_token) ───────

    def exchange_refresh_token(
        self,
        *,
        client_id: str,
        refresh_token: str,
        scope: Optional[str] = None,
        now: Optional[int] = None,
    ) -> dict[str, Any]:
        """Rotate a refresh token for a new access + refresh pair (AC4).

        Delegates rotation + reuse detection to the store: a replayed (already
        rotated) refresh token raises :class:`RefreshTokenReuseError`, which the
        store maps to a family kill; here we surface it as ``invalid_grant``.
        An unknown/expired refresh token → ``invalid_grant`` (no family kill).
        """
        now = _now() if now is None else now
        self._require_client(client_id)

        new_refresh = _new_token()
        new_refresh_jti = _new_jti()
        try:
            rotated = self.store.rotate_refresh_token(
                old_token=refresh_token,
                new_token=new_refresh,
                new_jti=new_refresh_jti,
                ttl_seconds=REFRESH_TOKEN_TTL_SECONDS,
                now=now,
            )
        except RefreshTokenReuseError as exc:
            # Reuse detected — the store has already killed the family. 400.
            logger.warning("[OAUTH] Refresh-token reuse detected: %s", exc)
            raise OAuthError("invalid_grant", "refresh token reuse detected; session revoked")

        if rotated is None:
            raise OAuthError("invalid_grant", "refresh token invalid or expired")

        # The refresh token is bound to the client it was issued to.
        if rotated["client_id"] != client_id:
            raise OAuthError("invalid_grant", "refresh token was not issued to this client")

        granted_scope = rotated["scope"]
        # A narrowing scope request is permitted only to the same single scope.
        if scope and scope != granted_scope:
            raise OAuthError("invalid_scope", "scope cannot be widened on refresh")

        access_token, _ = self._mint_access_token(
            user_sub=rotated["user_sub"],
            client_id=client_id,
            scope=granted_scope,
            now=now,
        )
        logger.info("[OAUTH] Rotated refresh token client=%s sub=%s", client_id, rotated["user_sub"])
        return _token_response(access_token, new_refresh, granted_scope)

    # ── /revoke (Protocol: revoke_token; RFC 7009) ───────────────────────────

    def revoke_token(self, token: str, now: Optional[int] = None) -> None:
        """Revoke a token by value (RFC 7009).

        Looks the value up as an access token first, then as a refresh token.
        An access token's ``jti`` is marked revoked (audit ledger); a refresh
        token's whole *family* is killed so no successor can rotate. Per RFC
        7009 the endpoint returns 200 even for an unknown/already-revoked token
        (this method is a no-op in that case) — the router never signals whether
        the token existed.
        """
        now = _now() if now is None else now

        access = self.store.get_access_token(token, now=now)
        if access is not None:
            self.store.revoke_jti(access["jti"], reason="rfc7009-revoke", now=now)
            logger.info("[OAUTH] Revoked access token jti=%s", access["jti"])
            return

        refresh = self.store.get_refresh_token(token, now=now)
        if refresh is not None:
            self.store.revoke_refresh_family(
                refresh["family_id"], reason="rfc7009-revoke", now=now
            )
            logger.info("[OAUTH] Revoked refresh family=%s", refresh["family_id"])
            return

        # Unknown / expired token — RFC 7009 says return success regardless.
        logger.debug("[OAUTH] Revoke request for unknown/expired token (no-op)")


def _oauth_signing_secret() -> str:
    """Resolve the DEDICATED OAuth signing secret (settings.json → mcp_oauth_signing_secret).

    ADR-009 §3 (amended 2026-05-21): MCP OAuth access tokens are signed with a
    secret that is **separate** from ECM's user-session ``jwt.secret_key``. The
    MCP RS reads this dedicated secret (from the shared ``/config/settings.json``,
    read-only) for offline verification (ADR-009 §1) — and reads ONLY this, never
    ECM's session key — so a compromised MCP can forge only MCP-scope tokens, not
    ECM admin sessions (threat model SR1). Auto-generated/persisted by ECM.
    """
    from config import get_or_create_oauth_signing_secret

    return get_or_create_oauth_signing_secret()


def _token_response(
    access_token: str, refresh_token: str, scope: str
) -> dict[str, Any]:
    """Build an RFC 6749 token-endpoint success body."""
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
        "refresh_token": refresh_token,
        "scope": scope,
    }
