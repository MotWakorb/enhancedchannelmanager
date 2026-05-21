"""MCP Resource Server OAuth Bearer-JWT verifier (bead enhancedchannelmanager-buiqr.8).

This is the security-critical core of the dual-path auth router (ADR-009 §2):
the OFFLINE HS256 verifier for OAuth access tokens minted by the ECM
Authorization Server (``backend/auth/oauth_provider.py``).

SECURITY CONTRACT (ADR-009 §1/§2/§3; threat model CD1/SP1/SP2/SP6/EP1/EP2)
=========================================================================
  - **Offline verification only (AC4).** Verifies the HS256 signature against
    the dedicated ``mcp_oauth_signing_secret`` read from ``/config/settings.json``.
    NO callback to ECM, NO read of the token store (``mcp_oauth.db``). Access-token
    revocation is bounded solely by the short TTL (≤ 15 min) per the Option A
    amendment — the RS never opens the store.
  - **alg PINNED to HS256 (SP2).** The verifier passes an explicit
    ``algorithms=["HS256"]`` allowlist; ``alg:none``, ``RS256``, or any other
    algorithm in the token header is rejected. We NEVER trust the header's alg.
  - **Claims enforced:** ``aud == "ecm-mcp"`` (EP1), ``iss == OAUTH_ISSUER``
    (must match the AS — see config.OAUTH_ISSUER), ``exp`` not past, and
    ``scope`` contains ``"mcp"``. Any failure raises :class:`OAuthTokenError`,
    which the router maps to a 401 (never to a static-key fall-through — CD1).
  - **Shape classification is independent of validation (CD1/SP6).**
    :func:`looks_like_jwt` decides routing BEFORE any validation runs; an
    ``alg:none`` token is JWT-SHAPED and therefore routes to the OAuth path
    (where it is rejected), NEVER to the static-key path.

DEPENDENCY NOTE: this module uses **PyJWT** (``import jwt``), which is already a
declared dependency of the MCP container (transitive via the ``mcp`` SDK; pinned
directly in ``requirements.in`` by buiqr.8). The ECM AS signs with
``python-jose``; jose-signed HS256 tokens are standard JWTs and verify byte-for-
byte with PyJWT (covered by the test suite). PyJWT is preferred here because the
MCP container's ``requirements.txt`` does NOT install ``python-jose`` — adding it
to the most network/agent-exposed container would be unnecessary surface.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any

import jwt
from jwt import InvalidTokenError

logger = logging.getLogger(__name__)

# ── Pinned algorithm + bound claims (ADR-009 §1/§3) ──────────────────────────
#: Explicit single-algorithm allowlist. Mirrors backend/auth/tokens.py
#: ``ALGORITHM = "HS256"``. We pin HS256 and NOTHING else — the token header's
#: declared ``alg`` is never trusted (SP2 alg-confusion / alg:none defense).
ALLOWED_ALGORITHMS = ["HS256"]
#: Audience the AS binds MCP access tokens to (oauth_provider.AUDIENCE). EP1.
AUDIENCE = "ecm-mcp"
#: The single scope offered in v1 (oauth_provider.MCP_SCOPE, ADR-009 §3/§8).
REQUIRED_SCOPE = "mcp"
#: Claims that MUST be present, else reject (defense-in-depth alongside the
#: signature check — a stripped-claim token is invalid even if signed).
_REQUIRED_CLAIMS = ["exp", "aud", "iss", "scope", "sub"]


class OAuthTokenError(Exception):
    """An OAuth Bearer token failed offline validation.

    Raised for ANY reason a JWT-shaped Bearer is not a valid OAuth session
    (bad/absent signature, wrong/forbidden alg, expired, wrong aud/iss, missing
    or wrong scope, malformed). The router maps every instance to a single 401
    with ``WWW-Authenticate: Bearer`` — it NEVER falls through to the static-key
    check (the CD1 no-fail-cascade invariant). The message is for SERVER-SIDE
    logging only and must never echo the token value.
    """


def looks_like_jwt(value: str) -> bool:
    """Classify a credential by SHAPE: is it a JWT we route to the OAuth path?

    A value is JWT-shaped iff it has EXACTLY three ``.``-separated segments AND
    the first segment base64url-decodes to a JSON object carrying an ``alg``
    field. This is decided BEFORE any signature/claim validation (ADR-009 §2,
    threat model CD1/SP6) — classification never depends on whether the token is
    *valid*, only on its shape.

    CRITICAL: an ``alg:none`` token is JWT-SHAPED and returns True here — so it
    routes to the OAuth path (where it is rejected with 401), and can NEVER be
    misrouted to the static-key path. A static key (no dots, or dots but a
    non-JSON / no-``alg`` first segment) returns False and routes to static-key.

    Returns False on anything that is not unambiguously JWT-shaped — fail
    toward the static-key path is safe here because a real JWT always satisfies
    this shape, and a non-JWT static key never should.
    """
    if not value:
        return False
    segments = value.split(".")
    if len(segments) != 3:
        return False
    header_segment = segments[0]
    if not header_segment:
        return False
    try:
        # base64url-decode the header with correct padding.
        padded = header_segment + "=" * (-len(header_segment) % 4)
        raw = base64.urlsafe_b64decode(padded)
        header = json.loads(raw)
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(header, dict):
        return False
    return "alg" in header


def verify_oauth_token(token: str, *, secret: str, issuer: str) -> dict[str, Any]:
    """Verify an OAuth access-token JWT OFFLINE and return its claims.

    Pins ``algorithms=["HS256"]`` (rejecting ``alg:none``/``RS256``/other — SP2),
    verifies the HS256 signature against ``secret`` (the dedicated
    ``mcp_oauth_signing_secret``), and enforces ``aud == "ecm-mcp"``,
    ``iss == issuer``, ``exp`` not past, required claims present, and that
    ``scope`` contains ``"mcp"``. NO store read, NO network call (AC4).

    Raises :class:`OAuthTokenError` on ANY failure (the router → 401, never a
    static-key fall-through). The exception message is for server logs only and
    never contains the token value or the secret.
    """
    if not secret:
        # Fail closed: with no signing secret we cannot verify offline. A
        # JWT-shaped Bearer must NOT be accepted, and (per CD1) must NOT fall
        # through to the static-key path. The router translates this to 401.
        raise OAuthTokenError("no signing secret configured for offline verify")

    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=ALLOWED_ALGORITHMS,  # explicit allowlist — never the header's alg
            audience=AUDIENCE,
            issuer=issuer,
            options={
                "require": _REQUIRED_CLAIMS,
                "verify_signature": True,
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
            },
        )
    except InvalidTokenError as exc:
        # InvalidTokenError is PyJWT's base for ALL validation failures
        # (InvalidAlgorithmError, InvalidSignatureError, ExpiredSignatureError,
        # InvalidAudienceError, InvalidIssuerError, MissingRequiredClaimError,
        # DecodeError, ...). Collapsing them to one OAuthTokenError keeps the
        # caller from leaking which check failed (ID4) and guarantees uniform
        # 401 handling. Log the failure CLASS only — never the token.
        raise OAuthTokenError(f"token validation failed: {type(exc).__name__}") from exc
    except Exception as exc:  # noqa: BLE001 — fail closed on any unexpected error
        raise OAuthTokenError(f"unexpected token validation error: {type(exc).__name__}") from exc

    # Manual scope enforcement: PyJWT does not understand OAuth ``scope``. The
    # scope claim is a space-delimited string (RFC 6749 §3.3); require "mcp".
    scope_value = claims.get("scope", "")
    if not isinstance(scope_value, str) or REQUIRED_SCOPE not in scope_value.split():
        raise OAuthTokenError("token scope does not include 'mcp'")

    return claims
