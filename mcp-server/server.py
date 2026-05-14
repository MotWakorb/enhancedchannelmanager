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

from config import MCP_PORT, get_mcp_api_key, get_mcp_api_key_status
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


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Validate the ECM MCP API key on every request except ``/health``.

    Accepts the key via query param (``?api_key=``) or ``Authorization: Bearer``
    header. The ``/health`` endpoint is exempt so Docker healthchecks work
    without a key. With the Streamable HTTP transport every request (POST and
    the SSE GET stream) hits the single ``/mcp`` endpoint, so auth is checked on
    each one — the key is static and re-read from disk per call.
    """

    async def dispatch(self, request, call_next):
        path = request.url.path

        # Health endpoint is always public
        if path == "/health":
            return await call_next(request)

        expected_key = get_mcp_api_key()
        if not expected_key:
            logger.warning("[MCP] Connection rejected: no MCP API key configured in ECM")
            return JSONResponse(
                {"error": "MCP API key not configured. Generate one in ECM Settings."},
                status_code=503,
            )

        # Extract key from query param or Authorization header
        api_key = request.query_params.get("api_key", "")
        if not api_key:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                api_key = auth_header[7:]

        if api_key != expected_key:
            logger.warning("[MCP] Connection rejected: invalid API key")
            return JSONResponse({"error": "Invalid API key"}, status_code=401)

        return await call_next(request)


async def handle_health(request):
    """Health check endpoint.

    Self-diagnosing /health (bd-ix1g6): in addition to the boolean
    ``api_key_configured`` flag, we surface ``api_key_status`` — a machine-
    readable reason that distinguishes the four ways a key can be missing
    (no settings file, corrupted JSON, missing field, empty field). This
    lets an operator (and the ECM Settings UI's MCP Server Status panel)
    diagnose a misconfigured deployment without container shell access.
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


# The StreamableHTTP transport needs mcp.session_manager.run() active for the
# lifetime of the app. streamable_http_app() wires that up via its own lifespan,
# but Starlette does NOT propagate a Mounted sub-app's lifespan — so the outer
# app must run the session manager itself.
streamable_app = mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/health", endpoint=handle_health),
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
