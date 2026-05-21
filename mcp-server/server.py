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
    MCP_RESOURCE_URL,
    OAUTH_ISSUER,
    get_mcp_api_key,
    get_mcp_api_key_status,
    get_oauth_allow_insecure,
    get_signing_key,
    get_signing_key_status,
)
from oauth_discovery import (
    build_protected_resource_metadata,
    discovery_blocked,
    issuer_is_insecure,
    resolve_issuer,
    resolve_resource_url,
)
from oauth_rs import OAuthTokenError, looks_like_jwt, verify_oauth_token
from resources import register_all_resources
from tools import register_all_tools

#: The well-known path the RS serves its RFC 9728 discovery document at.
PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"

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


def get_oauth_issuer_for_rs() -> str:
    """Resolve the OAuth issuer the RS pins ``iss`` against (bd-buiqr.8).

    Must equal the value the ECM AS mints into the token's ``iss`` claim
    (``backend/auth/oauth_provider.get_oauth_issuer`` — also ``OAUTH_ISSUER``).

    DEPLOYMENT REQUIREMENT (flagged for buiqr.11): the MCP container and the ECM
    container MUST share the same ``OAUTH_ISSUER`` environment value. If they
    drift, every OAuth Bearer token fails ``iss`` verification here and clients
    silently get 401s. When unset, both default to the non-production placeholder
    ``https://ecm.local`` (config.OAUTH_ISSUER); a real deploy must pin the
    operator's external HTTPS origin in BOTH containers.
    """
    return OAUTH_ISSUER


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Dual-path-by-SHAPE auth router for the MCP RS (bd-buiqr.8, ADR-009 §2).

    Classifies each request by the SHAPE of the presented credential BEFORE any
    validation runs, then dispatches to EXACTLY ONE path. It NEVER tries one
    path, fails, and falls through to the other (the CD1 no-fail-cascade
    invariant — the headline auth-bypass this whole design exists to prevent).

    Routing (decided pre-validation):
      - ``Authorization: Bearer <JWT-shaped>`` (3 base64url segments, header
        JSON with ``alg``) → **OAuth path only**. Offline HS256 verify. A failed
        validation → 401 + ``WWW-Authenticate: Bearer``; it is NEVER compared to
        the static key.
      - ``?api_key=<value>`` OR ``Bearer <non-JWT-shaped>`` → **static-key path
        only** (existing behavior, PO-locked permanent).
      - Both an OAuth-shaped Bearer AND ``?api_key=`` present → **OAuth wins**;
        the static key is ignored entirely.
      - Neither → 401 + ``WWW-Authenticate: Bearer``.

    ``/health`` and the RFC 9728 discovery doc stay public (exempt below). With
    the Streamable HTTP transport every POST and the SSE GET hit the single
    ``/mcp`` endpoint, so auth is checked per request; the secret/key are re-read
    from disk per call so rotation takes effect without a restart (offline HMAC
    verify keeps this inside the ≤5 ms budget — AC6).
    """

    async def dispatch(self, request, call_next):
        path = request.url.path

        # Health endpoint is always public
        if path == "/health":
            return await call_next(request)

        # OAuth protected-resource discovery is PUBLIC by spec (RFC 9728) — a
        # client must read it BEFORE it has any credential. Its own handler
        # applies the fail-closed HTTP-posture gate (bd-buiqr.5, ADR-009 §4/§7).
        if path == PROTECTED_RESOURCE_PATH:
            return await call_next(request)

        # ── SHAPE CLASSIFICATION (before any validation — ADR-009 §2 / CD1) ──
        # An OAuth-shaped Bearer takes precedence over ?api_key= (OAuth wins).
        auth_header = request.headers.get("authorization", "")
        bearer_value = auth_header[7:] if auth_header.startswith("Bearer ") else ""

        if bearer_value and looks_like_jwt(bearer_value):
            # JWT-shaped → OAuth path ONLY. Its outcome is "valid OAuth session"
            # or 401 — it can NEVER fall through to the static-key check.
            return await self._handle_oauth(request, bearer_value, call_next)

        # Not JWT-shaped → static-key path ONLY (existing behavior, permanent).
        return await self._handle_static_key(request, bearer_value, call_next)

    async def _handle_oauth(self, request, token, call_next):
        """OAuth-only path. Failure → 401; NEVER a static-key fall-through (CD1)."""
        secret = get_signing_key()  # dedicated mcp_oauth_signing_secret (never logged)
        issuer = get_oauth_issuer_for_rs()
        try:
            claims = verify_oauth_token(token, secret=secret, issuer=issuer)
        except OAuthTokenError as exc:
            # Single uniform 401 for every OAuth failure. We log the failure
            # CLASS only (never the token / secret) and do NOT touch the static
            # key — the CD1 invariant. The 401 carries WWW-Authenticate: Bearer.
            logger.warning("[MCP] OAuth Bearer rejected: %s", exc)
            return JSONResponse(
                {"error": "Invalid or expired OAuth token"},
                status_code=401,
                headers=_WWW_AUTHENTICATE,
            )
        # Success. AC5: distinguish the auth method in logs (sub for audit).
        logger.info(
            "[MCP] Authenticated request auth_method=oauth sub=%s", claims.get("sub")
        )
        return await call_next(request)

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

    bd-buiqr10 (Option-A slice): we also surface ``signing_key_status`` —
    whether the shared HS256 OAuth signing secret is present in settings.json.
    This is additive; existing ``api_key_status`` behavior is unchanged.
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

    # bd-buiqr10: signing key diagnostic (additive — does not alter api_key_status).
    # SECURITY (threat model ID1): the hint text describes the PROBLEM and
    # remediation; it never contains or references the secret value itself.
    _, signing_status = get_signing_key_status()
    signing_key_hints = {
        "signing_key_missing": (
            "The OAuth signing secret (mcp_oauth_signing_secret) is not present "
            "in settings.json. OAuth Bearer-JWT verification requires this shared "
            "HS256 secret. Ensure ECM has written the signing secret to the shared "
            "/config volume — this is set automatically when the OAuth AS is "
            "configured. Check that both containers mount the same /config volume."
        ),
        "file_not_found": (
            "settings.json was not found at /config/settings.json. The OAuth "
            "signing secret cannot be read. Verify the /config volume is mounted "
            "and ECM Settings has been saved at least once."
        ),
        "invalid_json": (
            "/config/settings.json could not be parsed as JSON. The OAuth signing "
            "secret cannot be read. Restore from a backup or recreate the file by "
            "saving ECM Settings."
        ),
    }

    response = {
        "status": "ok" if configured else "not_configured",
        "server": "ecm-mcp",
        "transport": "streamable-http",
        "api_key_configured": configured,
        "api_key_status": status,
        # bd-buiqr10: signing key status is always included in the response
        # so operators can diagnose OAuth readiness independently of api_key
        # status. 'ok' means the HS256 secret is present; 'signing_key_missing'
        # means OAuth Bearer-JWT auth cannot work until the secret is set.
        "signing_key_status": signing_status,
        "tools_available": len(mcp._tool_manager.list_tools()),
        "resources_available": len(mcp._resource_manager.list_resources()),
    }
    if not configured and status in setup_hints:
        response["setup_hint"] = setup_hints[status]
    if signing_status != "ok" and signing_status in signing_key_hints:
        response["signing_key_hint"] = signing_key_hints[signing_status]
    return JSONResponse(response)


async def handle_protected_resource(request):
    """RFC 9728 OAuth protected-resource discovery (PUBLIC; fail-closed on plain HTTP).

    Points clients at the ECM AS issuer (``authorization_servers``). Returns 404
    when the ``oauth_allow_insecure`` posture gate fail-closes the endpoint
    (plain-HTTP non-loopback issuer, flag false → 404; bd-buiqr.5, ADR-009 §4,
    threat model HT1). The document exposes ONLY protocol-required fields — never
    the HS256 secret, the internal Docker host (``ecm:6100``), filesystem paths,
    or ``mcp_api_key`` (threat model ID1).
    """
    host = request.headers.get("host")
    scheme = request.url.scheme
    issuer = resolve_issuer(request_host=host, request_scheme=scheme)
    allow_insecure = get_oauth_allow_insecure()

    if discovery_blocked(issuer, allow_insecure):
        # Generic 404 — reveals nothing about the gate (threat model HT1/ID1).
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    resource = resolve_resource_url(MCP_RESOURCE_URL, request_host=host, request_scheme=scheme)
    return JSONResponse(build_protected_resource_metadata(issuer, resource))


def _log_oauth_discovery_posture():
    """Emit EXACTLY ONE WARN per startup describing the discovery posture.

    Mirrors the ECM AS startup WARN (bd-buiqr.5, ADR-009 §4, threat model HT1):
    fail-closed (404) when plain-HTTP/non-loopback and not opted in; an explicit
    insecure-opt-in acknowledgement when oauth_allow_insecure=true on an insecure
    posture. A secure (HTTPS or loopback) posture logs nothing. Best-effort.
    """
    try:
        issuer = resolve_issuer()
        allow_insecure = get_oauth_allow_insecure()
        if discovery_blocked(issuer, allow_insecure):
            logger.warning(
                "[OAUTH] Protected-resource discovery DISABLED (HTTP 404): the AS issuer "
                "is plain-HTTP on a non-loopback host and oauth_allow_insecure=false "
                "(fail-closed, ADR-009 §4). Front MCP with HTTPS or set "
                "oauth_allow_insecure=true in settings.json to opt in to plain-HTTP OAuth."
            )
        elif allow_insecure and issuer_is_insecure(issuer):
            logger.warning(
                "[OAUTH] oauth_allow_insecure=true — serving OAuth protected-resource "
                "discovery over PLAIN HTTP on a non-loopback host. Tokens traverse "
                "cleartext and can be replayed within their TTL (threat model HT1). "
                "This is an explicit, operator-accepted insecure posture."
            )
    except Exception as exc:  # noqa: BLE001 — startup log must never abort boot
        logger.warning("[MCP] OAuth discovery posture check failed: %s", exc)


# The StreamableHTTP transport needs mcp.session_manager.run() active for the
# lifetime of the app. streamable_http_app() wires that up via its own lifespan,
# but Starlette does NOT propagate a Mounted sub-app's lifespan — so the outer
# app must run the session manager itself.
streamable_app = mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def lifespan(app):
    # One-per-startup OAuth discovery posture WARN (bd-buiqr.5, ADR-009 §4).
    _log_oauth_discovery_posture()
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/health", endpoint=handle_health),
        # RFC 9728 protected-resource discovery — registered BEFORE the catch-all
        # Mount so it is matched here (and exempted from the API-key middleware).
        Route(PROTECTED_RESOURCE_PATH, endpoint=handle_protected_resource),
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
