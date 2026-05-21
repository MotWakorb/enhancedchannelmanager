"""OAuth discovery metadata + HTTP-posture safety gate (bead buiqr.5).

This module owns two security-load-bearing concerns shared by both discovery
endpoints (the ECM AS RFC 8414 document and — by mirrored logic — the MCP RS
RFC 9728 document):

1. **Issuer resolution.** The discovery ``issuer`` MUST equal the ``iss`` claim
   the AS already mints (``auth/oauth_provider.get_oauth_issuer()``). A divergent
   issuer would make the future RS validator (buiqr.8) reject every token, so we
   source the issuer from exactly the same place: the ``OAUTH_ISSUER`` env var
   (default ``https://ecm.local``). ``OAUTH_ISSUER`` *is* the deployment's
   externally-reachable origin, so it is inherently deployment-specific — there
   is no cross-deployment hostname leakage (bead AC6). When ``OAUTH_ISSUER`` is
   left at its placeholder default, we fall back to deriving the issuer from the
   request ``Host`` header, but the AS-minted ``iss`` is bound to the same source
   so the two cannot drift (see :func:`resolve_issuer`).

2. **HTTP-posture fail-closed gate** (``oauth_allow_insecure``, ADR-009 §4).
   When the flag is ``false`` (default) AND the issuer scheme is ``http://`` AND
   the host is non-loopback, BOTH discovery endpoints return **404** — the OAuth
   surface is fail-closed off for plain-HTTP, non-loopback deploys (threat model
   HT1). When ``true`` the operator has explicitly opted in to plain-HTTP OAuth.
   Loopback HTTP (``localhost``/``127.0.0.0/8``/``::1``) is always allowed (it is
   a developer/same-host posture, not a LAN-interception surface).

DISCOVERY HYGIENE (ADR-009 §7, threat model ID1)
================================================
The document builders here expose **only** protocol-required fields. They never
emit the HS256 signing secret, internal Docker-network hostnames (``ecm:6100``),
filesystem paths, ``mcp_api_key``, or version-internal details. The shape is
pinned by snapshot tests (bead AC3).
"""
from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

from auth.oauth_provider import (
    AUDIENCE,
    MCP_SCOPE,
    PKCE_S256,
    get_oauth_issuer,
)

#: The placeholder issuer ``get_oauth_issuer()`` returns when ``OAUTH_ISSUER`` is
#: unset. When the resolved issuer equals this we treat ``OAUTH_ISSUER`` as
#: "not configured" and may derive the public origin from the request Host
#: instead (AC6) — but the AS-minted ``iss`` uses the SAME placeholder, so a
#: token minted in that posture carries this issuer and the discovery doc must
#: advertise it too. See :func:`resolve_issuer`.
PLACEHOLDER_ISSUER = "https://ecm.local"


def _is_loopback_host(host: str) -> bool:
    """Return True if ``host`` is a loopback target (localhost / 127.0.0.0/8 / ::1).

    A bare host (no port) is expected — strip any ``:port`` before calling, or
    pass the hostname component directly. ``localhost`` matches by name; numeric
    hosts are matched via the stdlib ``ipaddress`` loopback check so the whole
    ``127.0.0.0/8`` block and IPv6 ``::1`` are covered, not just ``127.0.0.1``.
    """
    if not host:
        return False
    bare = host.strip().lower()
    # Strip an IPv6 bracket wrapper, e.g. "[::1]" → "::1".
    if bare.startswith("[") and bare.endswith("]"):
        bare = bare[1:-1]
    if bare == "localhost":
        return True
    try:
        return ipaddress.ip_address(bare).is_loopback
    except ValueError:
        return False


def resolve_issuer(request_host: str | None = None, request_scheme: str | None = None) -> str:
    """Resolve the discovery ``issuer`` — coupled to the AS-minted ``iss``.

    Precedence:
      1. ``OAUTH_ISSUER`` (via :func:`get_oauth_issuer`) when it has been
         configured to something other than the placeholder default. This is the
         common production path and is the value the AS mints into ``iss``
         (``auth/oauth_provider``), so discovery and tokens cannot disagree.
      2. When ``OAUTH_ISSUER`` is unset (issuer == placeholder) AND a request
         ``Host`` is available, derive the origin from ``scheme://host`` (AC6 —
         "issuer reflects the request Host header"). The placeholder default is
         deployment-specific by construction (it is ``ecm.local``), so this only
         engages when the operator has not pinned an explicit issuer.

    NOTE ON THE TOKEN COUPLING: the AS mints ``iss`` from ``get_oauth_issuer()``
    directly (it has no request context). So in the request-derived fallback the
    discovery issuer can differ from a token minted *without* OAUTH_ISSUER set.
    Operators running OAuth in production are expected to set ``OAUTH_ISSUER`` to
    their HTTPS origin (documented in buiqr.11); when they do, both sides use
    path (1) and match exactly. The fallback exists only so discovery is not
    blank on an un-pinned dev box.
    """
    configured = get_oauth_issuer()
    if configured != PLACEHOLDER_ISSUER:
        return configured.rstrip("/")
    if request_host and request_scheme:
        return f"{request_scheme}://{request_host}".rstrip("/")
    return configured.rstrip("/")


def issuer_is_insecure(issuer: str) -> bool:
    """Return True if ``issuer`` is plain-HTTP on a NON-loopback host.

    This is the condition that, with ``oauth_allow_insecure=false``, fail-closes
    the discovery endpoints to 404 (ADR-009 §4, threat model HT1). HTTPS issuers
    and loopback HTTP issuers are both considered secure-enough and are NOT gated.
    """
    parts = urlsplit(issuer)
    if parts.scheme != "http":
        return False  # https (or anything non-http) is fine
    host = parts.hostname or ""
    return not _is_loopback_host(host)


def discovery_blocked(issuer: str, allow_insecure: bool) -> bool:
    """Fail-closed decision: should the discovery endpoints return 404?

    Blocked (404) iff the issuer is insecure (plain-HTTP, non-loopback) AND the
    operator has NOT opted in via ``oauth_allow_insecure``. This is the single
    gate both ``/.well-known/*`` endpoints consult (ADR-009 §4, AC4/AC5).
    """
    if allow_insecure:
        return False
    return issuer_is_insecure(issuer)


def build_authorization_server_metadata(issuer: str) -> dict[str, Any]:
    """Build the RFC 8414 OAuth Authorization Server metadata document.

    Exposes ONLY protocol-required fields (ADR-009 §7, threat model ID1). The
    endpoint URLs are derived from ``issuer`` — the deployment's external origin —
    NOT from internal Docker hostnames. No secret, no filesystem path, no
    ``mcp_api_key``, no version-internal detail is included. Shape pinned by
    snapshot tests (AC3).
    """
    base = issuer.rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/api/oauth/authorize",
        "token_endpoint": f"{base}/api/oauth/token",
        "revocation_endpoint": f"{base}/api/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": [PKCE_S256],
        "scopes_supported": [MCP_SCOPE],
        # Public clients (Claude Desktop) use PKCE with no client secret.
        "token_endpoint_auth_methods_supported": ["none"],
    }


def build_protected_resource_metadata(issuer: str, resource: str) -> dict[str, Any]:
    """Build the RFC 9728 OAuth Protected Resource metadata document.

    ``resource`` is the MCP RS's own external identifier; ``authorization_servers``
    points at the ECM AS ``issuer`` so a client discovers where to authenticate.
    Exposes ONLY protocol-required fields (ADR-009 §7, threat model ID1): no
    secret, no internal Docker host (``ecm:6100``), no filesystem path, no
    ``mcp_api_key``. Shape pinned by snapshot tests (AC3).
    """
    base = issuer.rstrip("/")
    return {
        "resource": resource.rstrip("/"),
        "authorization_servers": [base],
        "scopes_supported": [MCP_SCOPE],
        "bearer_methods_supported": ["header"],
    }


__all__ = [
    "AUDIENCE",
    "PLACEHOLDER_ISSUER",
    "resolve_issuer",
    "issuer_is_insecure",
    "discovery_blocked",
    "build_authorization_server_metadata",
    "build_protected_resource_metadata",
]
