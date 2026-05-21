"""OAuth protected-resource discovery + HTTP-posture gate for the MCP RS (bd-buiqr.5).

The MCP container is the OAuth Resource Server (RS) (ADR-009 §1). It serves the
RFC 9728 protected-resource metadata document at
``/.well-known/oauth-protected-resource``, pointing clients at the ECM
Authorization Server. This module mirrors the backend's
``auth/oauth_discovery.py`` logic — it is a SEPARATE copy on purpose: the MCP
container does not share a Python package with the backend, and the security
contract (fail-closed on plain HTTP, no secret/internal-host leakage) must hold
identically on both sides. Keep the two in sync.

DISCOVERY HYGIENE (ADR-009 §7, threat model ID1)
================================================
The document exposes ONLY protocol-required fields: the resource identifier, the
AS issuer(s), the supported scopes, and the bearer method. It NEVER emits the
HS256 signing secret, the internal Docker host (``ecm:6100``), filesystem paths,
or ``mcp_api_key``. Shape pinned by snapshot tests (bead AC3).
"""
from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

from config import MCP_PORT, OAUTH_ISSUER, PLACEHOLDER_ISSUER

#: Single 'mcp' scope in v1 (ADR-009 §3) — mirrors backend MCP_SCOPE.
MCP_SCOPE = "mcp"


def _is_loopback_host(host: str) -> bool:
    """Return True if ``host`` is a loopback target (localhost / 127.0.0.0/8 / ::1)."""
    if not host:
        return False
    bare = host.strip().lower()
    if bare.startswith("[") and bare.endswith("]"):
        bare = bare[1:-1]
    if bare == "localhost":
        return True
    try:
        return ipaddress.ip_address(bare).is_loopback
    except ValueError:
        return False


def resolve_issuer(request_host: str | None = None, request_scheme: str | None = None) -> str:
    """Resolve the ECM AS issuer advertised in ``authorization_servers``.

    Precedence: a pinned ``OAUTH_ISSUER`` env var (production), else (when unset
    / placeholder) the request-derived origin (AC6). The pinned value must equal
    the AS-minted ``iss`` so a client can validate tokens against the same issuer.
    """
    if OAUTH_ISSUER != PLACEHOLDER_ISSUER:
        return OAUTH_ISSUER.rstrip("/")
    if request_host and request_scheme:
        return f"{request_scheme}://{request_host}".rstrip("/")
    return OAUTH_ISSUER.rstrip("/")


def resolve_resource_url(
    configured: str,
    request_host: str | None = None,
    request_scheme: str | None = None,
) -> str:
    """Resolve the MCP RS's own external resource identifier.

    Uses ``MCP_RESOURCE_URL`` when the operator pinned it, else the request
    origin, else a port-derived localhost default for un-pinned dev boxes.
    """
    if configured:
        return configured.rstrip("/")
    if request_host and request_scheme:
        return f"{request_scheme}://{request_host}".rstrip("/")
    return f"http://localhost:{MCP_PORT}"


def issuer_is_insecure(issuer: str) -> bool:
    """Return True if ``issuer`` is plain-HTTP on a NON-loopback host (HT1 gate)."""
    parts = urlsplit(issuer)
    if parts.scheme != "http":
        return False
    host = parts.hostname or ""
    return not _is_loopback_host(host)


def discovery_blocked(issuer: str, allow_insecure: bool) -> bool:
    """Fail-closed: 404 iff the issuer is insecure AND not opted in (ADR-009 §4)."""
    if allow_insecure:
        return False
    return issuer_is_insecure(issuer)


def build_protected_resource_metadata(issuer: str, resource: str) -> dict[str, Any]:
    """Build the RFC 9728 protected-resource metadata document (ID1-clean shape)."""
    base = issuer.rstrip("/")
    return {
        "resource": resource.rstrip("/"),
        "authorization_servers": [base],
        "scopes_supported": [MCP_SCOPE],
        "bearer_methods_supported": ["header"],
    }
