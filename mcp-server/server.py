"""ECM MCP Server — exposes Enhanced Channel Manager operations as MCP tools.

Runs as a standalone Streamable HTTP server that Claude Desktop/Code can connect
to (single ``/mcp`` endpoint, session carried via the ``Mcp-Session-Id`` header).
Communicates with the ECM backend via HTTP API using an API key for auth.
"""
import contextlib
import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from config import (
    MCP_PORT,
    get_mcp_api_key,
    get_mcp_api_key_status,
)
# MCP OAuth offering RETIRED (bd-9axgc). The OAuth Resource-Server modules —
# ``oauth_rs`` (HS256 token verify) and ``oauth_discovery`` (RFC 9728 metadata) —
# plus the config OAuth helpers (get_signing_key / get_signing_key_status /
# get_oauth_allow_insecure / OAUTH_ISSUER / MCP_RESOURCE_URL) are kept dormant
# in-tree but NO LONGER IMPORTED here. The only symbol still needed from
# oauth_rs is ``looks_like_jwt``, used to SHAPE-classify and REJECT JWT-shaped
# Bearer tokens (so they can never fall through to the static-key path — CD1).
from oauth_rs import looks_like_jwt
from resources import register_all_resources
from tools import register_all_tools

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Create MCP server using the high-level FastMCP API.
#
# DNS-rebinding protection (Host/Origin allowlisting) is disabled: ECM's MCP
# sidecar is intended to be reached from another host by IP or hostname, and
# FastMCP's default allowlist is localhost-only — which would 421 every remote
# client. Access is gated by the static API key (APIKeyAuthMiddleware) instead.
mcp = FastMCP(
    "ecm-mcp",
    instructions=(
        "You are connected to ECM (Enhanced Channel Manager), an IPTV channel "
        "management system. You can list, create, update, and delete channels, "
        "manage M3U accounts, EPG sources, run auto-creation pipelines, probe "
        "stream health, view statistics, and more."
    ),
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# Register tools and resources
register_all_tools(mcp)
register_all_resources(mcp)


#: The 401 challenge header. Per RFC 6750 / RFC 9728 a client that gets this on
#: /mcp learns OAuth is offered and where to discover it. Always present on a
#: 401 so an OAuth client can bootstrap from any rejected request (ADR-009 §2).
_WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Static-key auth for the MCP RS. OAuth offering RETIRED (bd-9axgc).

    The MCP OAuth offering was retired by PO decision (bd-9axgc): ECM no longer
    accepts OAuth 2.1 Bearer tokens for MCP. The ONLY supported credential is
    the static ``?api_key=`` (or non-JWT-shaped ``Bearer <key>``) path —
    PO-locked permanent.

    Routing (decided pre-validation, preserving the CD1 no-fail-cascade
    invariant — a JWT-shaped Bearer must NEVER be compared to the static key):
      - ``Authorization: Bearer <JWT-shaped>`` (3 base64url segments, header
        JSON with ``alg``) → **REJECTED 401**. OAuth/JWT-shaped tokens are no
        longer accepted; the request is NEVER tried against the static key
        (no fail-cascade leakage).
      - ``?api_key=<value>`` OR ``Bearer <non-JWT-shaped>`` → **static-key path**.
      - Neither → 401 + ``WWW-Authenticate: Bearer``.

    The OAuth verify path (``oauth_rs.verify_oauth_token``) and the RFC 9728
    discovery endpoint are kept dormant in-tree but no longer wired up; re-add
    the ``_handle_oauth`` dispatch + the discovery route to re-enable the
    offering.

    ``/health`` stays public (exempt below). With the Streamable HTTP transport
    every POST and the SSE GET hit the single ``/mcp`` endpoint, so auth is
    checked per request; the static key is re-read from disk per call so
    rotation takes effect without a restart.
    """

    async def dispatch(self, request, call_next):
        path = request.url.path

        # Health endpoint is always public
        if path == "/health":
            return await call_next(request)

        # ── SHAPE CLASSIFICATION (before any validation — CD1 no-fail-cascade) ──
        auth_header = request.headers.get("authorization", "")
        bearer_value = auth_header[7:] if auth_header.startswith("Bearer ") else ""

        if bearer_value and looks_like_jwt(bearer_value):
            # JWT-shaped Bearer → OAuth offering RETIRED (bd-9axgc). Reject with
            # a uniform 401 and NEVER fall through to the static-key check (CD1):
            # an attacker who presents a JWT-shaped value must not be able to
            # have it re-interpreted as a static key. We log only that an OAuth
            # token was rejected — never the token value (CodeQL #1604).
            logger.warning("[MCP] OAuth/JWT-shaped Bearer rejected: MCP OAuth offering retired (bd-9axgc)")
            return JSONResponse(
                {"error": "OAuth is not supported. Use the static ?api_key= MCP credential."},
                status_code=401,
                headers=_WWW_AUTHENTICATE,
            )

        # Not JWT-shaped → static-key path ONLY (the supported method).
        return await self._handle_static_key(request, bearer_value, call_next)

    async def _handle_static_key(self, request, bearer_value, call_next):
        """Static-key-only path — existing behavior, PO-locked permanent (EP2)."""
        expected_key = get_mcp_api_key()
        if not expected_key:
            logger.warning("[MCP] Connection rejected: no MCP API key configured in ECM")
            return JSONResponse(
                {"error": "MCP API key not configured. Generate one in ECM Settings."},
                status_code=503,
            )

        # Extract key from query param, else the (non-JWT-shaped) Bearer value.
        api_key = request.query_params.get("api_key", "") or bearer_value

        if not api_key:
            # No credential at all → 401 with the OAuth bootstrap challenge.
            return JSONResponse(
                {"error": "Authentication required"},
                status_code=401,
                headers=_WWW_AUTHENTICATE,
            )

        if api_key != expected_key:
            logger.warning("[MCP] Connection rejected: invalid API key")
            return JSONResponse(
                {"error": "Invalid API key"},
                status_code=401,
                headers=_WWW_AUTHENTICATE,
            )

        # Success. AC5: distinguish the auth method in logs.
        logger.info("[MCP] Authenticated request auth_method=static_key")
        return await call_next(request)


async def handle_health(request):
    """Health check endpoint.

    Self-diagnosing /health (bd-ix1g6): in addition to the boolean
    ``api_key_configured`` flag, we surface ``api_key_status`` — a machine-
    readable reason that distinguishes the four ways a key can be missing
    (no settings file, corrupted JSON, missing field, empty field). This
    lets an operator (and the ECM Settings UI's MCP Server Status panel)
    diagnose a misconfigured deployment without container shell access.

    MCP OAuth offering RETIRED (bd-9axgc): the previously-reported
    ``signing_key_status`` / ``signing_key_hint`` OAuth fields were REMOVED —
    OAuth Bearer-JWT auth is no longer accepted, so signing-secret readiness is
    irrelevant. Only the static-key (``api_key_*``) diagnostics remain.
    """
    api_key, status = get_mcp_api_key_status()
    configured = bool(api_key)

    # Pick a hint tailored to the specific failure mode so the user sees a
    # remediation matching the actual cause, not a one-size-fits-all message.
    setup_hints = {
        "file_not_found": (
            "ECM has not written settings.json yet, or the MCP container's "
            "/config volume is not sharing the same data as ECM. Verify both "
            "containers mount the same volume and that ECM Settings has been "
            "saved at least once."
        ),
        "invalid_json": (
            "/config/settings.json could not be parsed as JSON. The file may "
            "be corrupted, partially written, or unrelated. Restore from a "
            "backup or recreate it by saving ECM Settings."
        ),
        "field_missing": (
            "settings.json predates the MCP feature and does not contain an "
            "mcp_api_key field. Open ECM Settings > MCP Integration and "
            "generate a key — saving will add the field."
        ),
        "field_empty": (
            "No MCP API key configured. Generate one in ECM Settings > "
            "MCP Integration."
        ),
    }

    response = {
        "status": "ok" if configured else "not_configured",
        "server": "ecm-mcp",
        "transport": "streamable-http",
        "api_key_configured": configured,
        "api_key_status": status,
        "tools_available": len(mcp._tool_manager.list_tools()),
        "resources_available": len(mcp._resource_manager.list_resources()),
    }
    if not configured and status in setup_hints:
        response["setup_hint"] = setup_hints[status]
    return JSONResponse(response)


# MCP OAuth offering RETIRED (bd-9axgc). ``handle_protected_resource`` (RFC 9728
# /.well-known/oauth-protected-resource discovery) and
# ``_log_oauth_discovery_posture`` (the one-per-startup discovery WARN) were
# REMOVED — the RS no longer advertises an OAuth Authorization Server. The
# oauth_discovery module is kept dormant in-tree; re-add this handler + its
# Route registration to re-enable the offering.


# The StreamableHTTP transport needs mcp.session_manager.run() active for the
# lifetime of the app. streamable_http_app() wires that up via its own lifespan,
# but Starlette does NOT propagate a Mounted sub-app's lifespan — so the outer
# app must run the session manager itself.
streamable_app = mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def lifespan(app):
    # MCP OAuth offering RETIRED (bd-9axgc): the one-per-startup OAuth discovery
    # posture WARN was removed with the discovery endpoint.
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/health", endpoint=handle_health),
        # MCP OAuth offering RETIRED (bd-9axgc): the RFC 9728
        # /.well-known/oauth-protected-resource discovery Route was removed —
        # the RS no longer advertises an OAuth Authorization Server. It now
        # falls through to the Mount and returns 404.
        Mount("/", app=streamable_app),
    ],
    lifespan=lifespan,
    middleware=[
        Middleware(APIKeyAuthMiddleware),
    ],
)

if __name__ == "__main__":
    import uvicorn

    logger.info("[MCP] Starting ECM MCP server on port %s", MCP_PORT)
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
