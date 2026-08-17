"""ECM MCP Server — exposes Enhanced Channel Manager operations as MCP tools.

Runs as a standalone Streamable HTTP server that Claude Desktop/Code can connect
to (single ``/mcp`` endpoint, session carried via the ``Mcp-Session-Id`` header).
Communicates with the ECM backend via HTTP API using an API key for auth.
"""
import contextlib
import hmac
import ipaddress
import logging

from config import (
    MCP_ALLOWED_HOSTS,
    MCP_PORT,
    get_mcp_api_key,
    get_mcp_api_key_status,
    normalize_mcp_allowed_host,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# MCP OAuth offering RETIRED (bd-9axgc). The OAuth Resource-Server verify path
# (oauth_rs.verify_oauth_token), the RFC 9728 discovery module (oauth_discovery),
# and the config OAuth helpers (get_signing_key / get_signing_key_status /
# get_oauth_allow_insecure / OAUTH_ISSUER / MCP_RESOURCE_URL) were REMOVED with
# the feature. The only symbol still needed from oauth_rs is ``looks_like_jwt``,
# used to SHAPE-classify and REJECT JWT-shaped Bearer tokens (so they can never
# fall through to the static-key path — CD1 no-fail-cascade).
from oauth_rs import looks_like_jwt
from resources import register_all_resources
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from tools import register_all_tools

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Create MCP server using the high-level FastMCP API.
#
_MCP_SDK_ALLOWED_HOSTS = [
    variant
    for host in MCP_ALLOWED_HOSTS
    for variant in (host, f"{host}:*")
]

# The home-lab defaults admit loopback and the canonical Compose service name.
# Operators serving the sidecar at a LAN IP/hostname add it explicitly through
# MCP_ALLOWED_HOSTS. The SDK performs the same check again at the transport
# boundary, so a future outer-app routing change cannot silently remove it.
mcp = FastMCP(
    "ecm-mcp",
    instructions=(
        "You are connected to ECM (Enhanced Channel Manager), an IPTV channel "
        "management system. You can list, create, update, and delete channels, "
        "manage M3U accounts, EPG sources, run auto-creation pipelines, probe "
        "stream health, view statistics, and more."
    ),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_MCP_SDK_ALLOWED_HOSTS,
    ),
)

# Register tools and resources
register_all_tools(mcp)
register_all_resources(mcp)


#: The 401 challenge header (RFC 6750). Signals that a Bearer credential is
#: required. The supported credential is the static MCP API key presented as
#: ``?api_key=`` or a non-JWT-shaped ``Bearer <key>`` (OAuth retired — bd-9axgc).
_WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}


def _validated_request_host(host_header: str) -> str | None:
    """Return the normalized hostname when an HTTP Host authority is valid."""
    if not host_header or any(character.isspace() for character in host_header):
        return None

    hostname: str
    remainder: str
    if host_header.startswith("["):
        closing_bracket = host_header.find("]")
        if closing_bracket < 0:
            return None
        hostname = host_header[: closing_bracket + 1]
        remainder = host_header[closing_bracket + 1 :]
        try:
            ipaddress.IPv6Address(hostname[1:-1])
        except ValueError:
            return None
    else:
        if host_header.count(":") > 1:
            return None
        hostname, separator, port = host_header.partition(":")
        remainder = f":{port}" if separator else ""

    if remainder:
        if not remainder.startswith(":"):
            return None
        port = remainder[1:]
        if not port.isascii() or not port.isdigit() or int(port) > 65535:
            return None

    try:
        return normalize_mcp_allowed_host(hostname)
    except ValueError:
        return None


class MCPAllowedHostMiddleware:
    """Reject malformed/untrusted Host values before Starlette routing.

    Starlette's generic TrustedHostMiddleware splits on the first colon, which
    cannot correctly validate bracketed IPv6 authorities. This small MCP-only
    boundary validates the full RFC-style authority and compares the normalized
    hostname against the configured exact allowlist.
    """

    def __init__(self, app, allowed_hosts: tuple[str, ...]) -> None:
        self.app = app
        self.allowed_hosts = frozenset(allowed_hosts)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        host = _validated_request_host(Headers(scope=scope).get("host", ""))
        if host not in self.allowed_hosts:
            response = PlainTextResponse("Invalid Host header", status_code=400)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


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
    discovery endpoint were REMOVED with the feature (bd-9axgc).

    ``/health`` stays public (exempt below). With the Streamable HTTP transport
    every POST and the SSE GET hit the single ``/mcp`` endpoint, so auth is
    checked per request; the static key is re-read from disk per call so
    rotation takes effect without a restart.
    """

    async def dispatch(self, request, call_next):
        # ``scope["path"]`` is the trusted path Uvicorn decoded from the HTTP
        # request target. Never derive an auth exemption from ``request.url``:
        # Starlette <=1.0.0 allowed a malformed Host header to poison its path
        # and make a routed /mcp request appear to be /health (CVE-2026-48710).
        path = request.scope.get("path", "")

        # Health endpoint is always public
        if path == "/health":
            return await call_next(request)

        # ── SHAPE CLASSIFICATION (before any validation — CD1 no-fail-cascade) ──
        # RFC 6750 §2.1: the "Bearer" auth-scheme token is case-insensitive and
        # the credential is whitespace-trimmed. Parse leniently so a well-formed
        # request from a spec-compliant client is never rejected on a casing nit
        # (LOW-2 hardening, bd-i3axt); a malformed/absent header still fails safe.
        auth_header = request.headers.get("authorization", "")
        bearer_value = ""
        scheme, _, credential = auth_header.partition(" ")
        if scheme.lower() == "bearer":
            bearer_value = credential.strip()

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

        # Constant-time compare to avoid a timing oracle on the static key
        # (bd-i3axt LOW-1). ``expected_key`` is already known non-empty (guarded
        # above) and ``api_key`` is non-empty here, so compare_digest never sees
        # None; a length/charset mismatch fails closed without leaking timing.
        if not hmac.compare_digest(api_key, expected_key):
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
# REMOVED with the feature — the RS no longer advertises an OAuth Authorization
# Server, and the oauth_discovery module was deleted.


# The StreamableHTTP transport needs mcp.session_manager.run() active for the
# lifetime of the app. streamable_http_app() wires that up via its own lifespan,
# but Starlette does NOT propagate a Mounted sub-app's lifespan — so the outer
# app must run the session manager itself.
streamable_app = mcp.streamable_http_app()


def mcp_http_middleware() -> list[Middleware]:
    """Build the outer HTTP security stack used by production and E2E tests.

    Authentication deliberately runs first: an unauthenticated poisoned /mcp
    request receives the same 401 as any other missing-credential request.
    Requests admitted by auth, plus public /health requests, then pass through
    strict Host validation before routing.
    """
    return [
        Middleware(APIKeyAuthMiddleware),
        Middleware(MCPAllowedHostMiddleware, allowed_hosts=MCP_ALLOWED_HOSTS),
    ]


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
    middleware=mcp_http_middleware(),
)

if __name__ == "__main__":
    import uvicorn

    logger.info("[MCP] Starting ECM MCP server on port %s", MCP_PORT)
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
