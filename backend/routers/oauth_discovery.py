"""OAuth discovery endpoint for the ECM Authorization Server (bead buiqr.5).

ECM is the OAuth Authorization Server (AS) (ADR-009 §1). This router exposes the
RFC 8414 AS-metadata document at the well-known root path so Claude Desktop's
Custom Connector UI can discover ECM as a valid OAuth server:

  - ``GET /.well-known/oauth-authorization-server`` — RFC 8414 AS metadata.

The MCP container serves the companion RFC 9728 protected-resource document
(``/.well-known/oauth-protected-resource``) itself; see ``mcp-server/server.py``.

PUBLIC + FAIL-CLOSED (ADR-009 §4/§7)
====================================
The endpoint is public/unauthenticated by spec (its path is added to
``AUTH_EXEMPT_PATHS`` in ``main.py`` for documentation — well-known paths are not
under ``/api/`` so the auth middleware never gates them anyway). It is gated by
the ``oauth_allow_insecure`` posture flag: on a plain-HTTP, non-loopback issuer
with the flag ``false`` (default) it returns **404** (fail-closed, threat model
HT1). The metadata document exposes ONLY protocol-required fields — never the
HS256 secret, internal Docker hostnames, filesystem paths, or ``mcp_api_key``
(threat model ID1).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from auth.oauth_discovery import (
    build_authorization_server_metadata,
    discovery_blocked,
    resolve_issuer,
)
from config import get_settings

logger = logging.getLogger(__name__)

#: No prefix — the discovery document lives at the well-known root, not /api/*.
router = APIRouter(tags=["OAuth"])

#: 404 body returned when the posture gate fail-closes the endpoint. Generic by
#: design — it must read like "no such endpoint", revealing nothing about the
#: gate (threat model HT1/ID1).
_NOT_FOUND = {"detail": "Not Found"}


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
async def oauth_authorization_server_metadata(request: Request):
    """RFC 8414 Authorization Server metadata (PUBLIC; fail-closed on plain HTTP).

    Returns the AS metadata document unless the ``oauth_allow_insecure`` posture
    gate fail-closes it (plain-HTTP non-loopback issuer, flag false → 404).
    """
    settings = get_settings()
    issuer = resolve_issuer(
        request_host=request.headers.get("host"),
        request_scheme=request.url.scheme,
    )
    if discovery_blocked(issuer, settings.oauth_allow_insecure):
        # Fail-closed: do not advertise OAuth on an insecure posture (HT1). The
        # one-per-startup WARN explaining the gate is emitted at app startup, not
        # here, to avoid log-flooding on every probe.
        return JSONResponse(status_code=404, content=_NOT_FOUND)
    return JSONResponse(
        status_code=200,
        content=build_authorization_server_metadata(issuer),
    )
