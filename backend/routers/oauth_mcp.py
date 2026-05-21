"""OAuth 2.1 Authorization Server endpoints for the ECM MCP integration.

Bead buiqr.3 — the ECM container is the OAuth Authorization Server (AS); the MCP
container is the Resource Server (RS) that verifies Bearer JWTs offline
(ADR-009 §1). This router exposes, under prefix ``/api/oauth``:

  - ``GET  /api/oauth/authorize``        — PKCE S256 authorization endpoint;
                                            requires the ECM admin session
                                            (RequireAdminIfEnabled), then hands
                                            off to the consent screen (buiqr.7).
  - ``POST /api/oauth/authorize/approve`` — consent-approval seam (buiqr.7
                                            submits here); mints the single-use
                                            authorization code and redirects to
                                            the client's registered redirect_uri.
  - ``POST /api/oauth/token``            — PUBLIC token endpoint (RFC 6749);
                                            authorization_code + refresh_token
                                            grants. In AUTH_EXEMPT_PATHS.
  - ``POST /api/oauth/revoke``           — PUBLIC revocation endpoint (RFC 7009).
                                            In AUTH_EXEMPT_PATHS.

All token-issuance logic lives in ``auth/oauth_provider.py``; this router is a
thin HTTP shape over it (validate inputs, map :class:`OAuthError` → OAuth JSON
error bodies, manage redirects). The store is opened per request against the
ECM-managed DB at ``/config/mcp_oauth.db`` (ADR-009 §5, Option A).

The consent SCREEN is buiqr.7, not this bead. ``GET /authorize`` validates the
request and redirects to the consent route as a clean seam; it does NOT render
consent HTML here.

SECURITY HARDENING (bead buiqr.4)
=================================
  - **Rate limiting** (threat model D1/D2): ``/authorize`` and ``/token`` each
    carry two stacked slowapi limits — a per-IP bucket and a per-user bucket
    (``auth/oauth_rate_limit.py``). They reuse the same ``limiter`` the login
    endpoints use, so the existing ``RateLimitExceeded`` handler + the
    ``RATE_LIMIT_ENABLED`` test switch apply unchanged. Defaults are
    env-configurable (``5/min`` authorize, ``10/min`` token).
  - **Consent CSRF** (threat model CSRF1): ``GET /authorize`` binds the
    anti-forgery ``state`` to the admin subject; ``POST /authorize/approve``
    consumes that binding and rejects any missing/forged/mismatched/replayed/
    cross-session ``state`` with 400 before a code is minted. This is the
    server-side state seam the consent UI (buiqr.7) round-trips.
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlencode, urlparse, urlunparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from slowapi.util import get_remote_address

from auth.dependencies import RequireAdminIfEnabled
from auth.oauth_provider import MCP_SCOPE, PKCE_S256, OAuthError, OAuthProvider
from auth.oauth_rate_limit import (
    authorize_rate_limit,
    oauth_user_rate_key,
    token_rate_limit,
)
from auth.oauth_store import OAuthStore
from auth.routes import limiter
from config import CONFIG_DIR
from models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/oauth", tags=["OAuth"])

#: The consent-screen route the AS redirects to after validating /authorize and
#: confirming the admin session. Owned by buiqr.7; this is the integration seam.
CONSENT_ROUTE = "/oauth/consent"

#: Sentinel admin subject used when ECM auth is disabled (open mode). In open
#: mode RequireAdminIfEnabled returns None (no user); the deployment is
#: single-admin by definition, so the grant binds to this stable subject.
ANONYMOUS_ADMIN_SUB = "admin"


def get_oauth_store() -> OAuthStore:
    """Open the ECM-managed OAuth store and ensure its schema (idempotent).

    A fresh per-request store/connection — the store is single-owner (ECM) and
    sqlite3 connections are per-thread. Schema init is a cheap idempotent
    ``CREATE TABLE IF NOT EXISTS`` (the same DDL the startup seed runs).
    """
    store = OAuthStore(CONFIG_DIR / "mcp_oauth.db")
    store.init_schema()
    return store


def _oauth_error_response(exc: OAuthError) -> JSONResponse:
    """Render an :class:`OAuthError` as an RFC 6749 JSON error body."""
    body = {"error": exc.error}
    if exc.description:
        body["error_description"] = exc.description
    return JSONResponse(status_code=exc.status_code, content=body)


def _admin_sub(admin: Optional[User]) -> str:
    """Resolve the admin subject for the grant's ``sub`` claim.

    When ECM auth is enabled, ``admin`` is the authenticated admin User; bind to
    its id. When auth is disabled (open mode), ``admin`` is None — bind to the
    stable single-admin sentinel (ADR-009 §3: single ECM admin on behalf of the
    deployment).
    """
    if admin is not None:
        return str(admin.id)
    return ANONYMOUS_ADMIN_SUB


# ── GET /authorize — admin-gated authorization endpoint (seam to consent) ────


@router.get("/authorize")
@limiter.limit(authorize_rate_limit, key_func=get_remote_address)  # per-IP (D2)
@limiter.limit(authorize_rate_limit, key_func=oauth_user_rate_key)  # per-user (D2)
async def authorize(
    request: Request,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str = "",
    code_challenge_method: str = "",
    scope: str = MCP_SCOPE,
    state: str = "",
    admin: Optional[User] = RequireAdminIfEnabled,
):
    """Validate a PKCE authorization request, then redirect to the consent UI.

    Requires the ECM admin session (RequireAdminIfEnabled) — an anonymous
    request when auth is enabled is rejected with 401 by the dependency before
    reaching here (threat model SP3). On a valid request this redirects to the
    consent route (buiqr.7) carrying the validated params; it does NOT mint a
    code (the code is minted on consent approval). Validation failures return
    OAuth-standard errors WITHOUT redirecting to a client-controlled URI until
    the redirect_uri has been confirmed registered (RD1 — never bounce an error
    to an unverified redirect target).
    """
    store = get_oauth_store()
    try:
        provider = OAuthProvider(store)

        # response_type must be "code" (authorization-code flow only, ADR-009 §3).
        if response_type != "code":
            raise OAuthError("unsupported_response_type", "only response_type=code is supported")

        # Validate client + EXACT redirect_uri BEFORE any redirect (RD1, SP4).
        client = provider.get_client(client_id)
        if client is None:
            raise OAuthError("invalid_client", "unknown client_id")
        OAuthProvider.validate_redirect_uri(client, redirect_uri)

        # PKCE S256 only — reject plain/missing here, before consent (T3).
        if code_challenge_method != PKCE_S256:
            raise OAuthError(
                "invalid_request",
                "code_challenge_method must be S256 (plain is not supported)",
            )
        if not code_challenge:
            raise OAuthError("invalid_request", "missing code_challenge")

        if (scope or MCP_SCOPE) != MCP_SCOPE:
            raise OAuthError("invalid_scope", "only the 'mcp' scope is supported")

        # CSRF: bind the anti-forgery `state` to THIS admin subject before
        # handing off to consent (bead buiqr.4 AC3). The approval step
        # (/authorize/approve) consumes this binding; a forged/mismatched state
        # there has no live binding and is rejected with 400. A future cross-
        # session attacker who guesses the consent URL cannot mint a code
        # because their forged state was never bound to their session.
        provider.bind_consent_state(
            state=state, client_id=client_id, user_sub=_admin_sub(admin)
        )

        # Hand off to the consent screen (buiqr.7) with the validated params.
        consent_params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": PKCE_S256,
            "scope": MCP_SCOPE,
            "state": state,
        }
        target = f"{CONSENT_ROUTE}?{urlencode(consent_params)}"
        logger.info("[OAUTH] /authorize validated client=%s — redirecting to consent", client_id)
        return RedirectResponse(url=target, status_code=302)
    except OAuthError as exc:
        logger.warning("[OAUTH] /authorize rejected: %s", exc)
        return _oauth_error_response(exc)
    finally:
        store.close()


# ── POST /authorize/approve — consent-approval seam (buiqr.7 submits here) ───


@router.post("/authorize/approve")
async def authorize_approve(
    request: Request,
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    code_challenge: str = Form(...),
    code_challenge_method: str = Form(...),
    scope: str = Form(MCP_SCOPE),
    state: str = Form(""),
    admin: Optional[User] = RequireAdminIfEnabled,
):
    """Mint a single-use authorization code and redirect to the client.

    This is the action the consent screen (buiqr.7) submits when the admin
    approves. It re-validates the request server-side (defence in depth — never
    trust that the consent form preserved the validated params), enforces the
    CSRF ``state`` binding created at ``/authorize`` (bead buiqr.4 AC3 — a
    forged/mismatched/cross-session state → 400 before any code is minted),
    mints the PKCE authorization code bound to the admin subject, and
    302-redirects to the client's registered redirect_uri with ``code``
    (+ ``state``). Requires the admin session (the consent screen is admin-only,
    SP3).
    """
    store = get_oauth_store()
    try:
        provider = OAuthProvider(store)
        user_sub = _admin_sub(admin)
        # CSRF: the submitted `state` MUST match a live binding created for THIS
        # admin at /authorize (bead buiqr.4 AC3). A missing/forged/mismatched or
        # cross-session state has no live binding → 400 before any code is minted.
        provider.verify_consent_state(state=state, user_sub=user_sub)
        code = provider.create_authorization_code(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope=scope,
            user_sub=user_sub,
        )
        # redirect_uri is exact-match validated inside create_authorization_code
        # (RD1) BEFORE the code is minted, so this redirect target is trusted.
        location = _append_query(redirect_uri, {"code": code, "state": state} if state else {"code": code})
        logger.info("[OAUTH] Consent approved client=%s sub=%s — redirecting with code", client_id, user_sub)
        return RedirectResponse(url=location, status_code=302)
    except OAuthError as exc:
        logger.warning("[OAUTH] /authorize/approve rejected: %s", exc)
        return _oauth_error_response(exc)
    finally:
        store.close()


# ── POST /token — PUBLIC token endpoint (RFC 6749) ───────────────────────────


@router.post("/token")
@limiter.limit(token_rate_limit, key_func=get_remote_address)  # per-IP (D1)
@limiter.limit(token_rate_limit, key_func=oauth_user_rate_key)  # per-user (D1)
async def token(
    request: Request,
    grant_type: str = Form(...),
    client_id: str = Form(...),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    code_verifier: str = Form(""),
    refresh_token: str = Form(""),
    scope: str = Form(""),
):
    """Token endpoint — authorization_code and refresh_token grants.

    PUBLIC by spec (no admin session — the PKCE verifier / refresh token IS the
    credential). Added to ``AUTH_EXEMPT_PATHS`` in main.py (buiqr.3 AC6). All
    errors are OAuth-standard JSON bodies (threat model ID4).
    """
    store = get_oauth_store()
    try:
        provider = OAuthProvider(store)

        if grant_type == "authorization_code":
            if not code or not redirect_uri or not code_verifier:
                raise OAuthError("invalid_request", "code, redirect_uri, and code_verifier are required")
            result = provider.exchange_authorization_code(
                client_id=client_id,
                code=code,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
            )
            return JSONResponse(status_code=200, content=result, headers=_NO_STORE)

        if grant_type == "refresh_token":
            if not refresh_token:
                raise OAuthError("invalid_request", "refresh_token is required")
            result = provider.exchange_refresh_token(
                client_id=client_id,
                refresh_token=refresh_token,
                scope=scope or None,
            )
            return JSONResponse(status_code=200, content=result, headers=_NO_STORE)

        raise OAuthError("unsupported_grant_type", f"unsupported grant_type: {grant_type}")
    except OAuthError as exc:
        logger.warning("[OAUTH] /token rejected: %s", exc)
        return _oauth_error_response(exc)
    finally:
        store.close()


# ── POST /revoke — PUBLIC revocation endpoint (RFC 7009) ─────────────────────


@router.post("/revoke")
async def revoke(
    request: Request,
    token: str = Form(...),
    token_type_hint: str = Form(""),
):
    """RFC 7009 token revocation.

    PUBLIC by spec (in ``AUTH_EXEMPT_PATHS``). Always returns 200, even for an
    unknown/already-revoked token — the endpoint never reveals whether the token
    existed (RFC 7009 §2.2). Revoking an access token marks its jti revoked;
    revoking a refresh token kills its whole rotation family.
    """
    store = get_oauth_store()
    try:
        provider = OAuthProvider(store)
        provider.revoke_token(token)
        return JSONResponse(status_code=200, content={}, headers=_NO_STORE)
    finally:
        store.close()


# ── helpers ──────────────────────────────────────────────────────────────────

#: Cache-control headers for token/revoke responses (RFC 6749 §5.1).
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _append_query(url: str, params: dict[str, str]) -> str:
    """Append query params to a URL, preserving any existing query/scheme.

    Used to attach ``code``/``state`` to the client's registered redirect_uri.
    The redirect_uri has already passed the exact-match registry check (RD1), so
    we are appending to a trusted, allowlisted target — never to user input.
    """
    parsed = urlparse(url)
    existing = parsed.query
    new_query = urlencode(params)
    combined = f"{existing}&{new_query}" if existing else new_query
    return urlunparse(parsed._replace(query=combined))
