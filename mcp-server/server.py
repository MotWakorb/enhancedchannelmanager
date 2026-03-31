"""ECM MCP Server — exposes Enhanced Channel Manager operations as MCP tools.

Runs as a standalone SSE server that Claude Desktop/Code can connect to.
Communicates with the ECM backend via HTTP API using an API key for auth.
"""
import logging

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount

from config import get_mcp_api_key, MCP_PORT
from tools import register_all_tools
from resources import register_all_resources

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Create MCP server using the high-level FastMCP API
mcp = FastMCP(
    "ecm-mcp",
    instructions=(
        "You are connected to ECM (Enhanced Channel Manager), an IPTV channel "
        "management system. You can list, create, update, and delete channels, "
        "manage M3U accounts, EPG sources, run auto-creation pipelines, probe "
        "stream health, view statistics, and more."
    ),
)

# Register tools and resources
register_all_tools(mcp)
register_all_resources(mcp)


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Validate API key on SSE and message endpoints.

    Accepts the key via query param (?api_key=) or Authorization: Bearer header.
    The /health endpoint is exempt so Docker healthchecks work without a key.
    """

    async def dispatch(self, request, call_next):
        path = request.url.path

        # Health endpoint is always public
        # Messages endpoint is session-bound (session_id from SSE handshake) — auth was checked on /sse
        if path == "/health" or path.startswith("/messages/"):
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
    """Health check endpoint."""
    api_key = get_mcp_api_key()
    configured = bool(api_key)
    response = {
        "status": "ok" if configured else "not_configured",
        "server": "ecm-mcp",
        "api_key_configured": configured,
        "tools_available": 80,
        "resources_available": 3,
    }
    if not configured:
        response["setup_hint"] = "Generate an MCP API key in ECM Settings > MCP Integration"
    return JSONResponse(response)


# Build the Starlette app with API key auth middleware
sse_app = mcp.sse_app()

app = Starlette(
    routes=[
        Route("/health", endpoint=handle_health),
        Mount("/", app=sse_app),
    ],
    middleware=[
        Middleware(APIKeyAuthMiddleware),
    ],
)

if __name__ == "__main__":
    import uvicorn

    logger.info("[MCP] Starting ECM MCP server on port %s", MCP_PORT)
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
