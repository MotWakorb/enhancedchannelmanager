"""Small Uvicorn target exercising ECM's production auth middleware.

The marker-writing ``/mcp`` endpoint stands in for the MCP transport/tool
dispatcher.  A poisoned request must be rejected by the production middleware
before this endpoint runs.
"""
import os
from pathlib import Path

import server
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

server.get_mcp_api_key = lambda: os.environ["ECM_MCP_TEST_API_KEY"]


async def health(_request):
    return JSONResponse({"status": "ok"})


async def tool_dispatch(_request):
    Path(os.environ["ECM_MCP_DISPATCH_MARKER"]).write_text("dispatched")
    return JSONResponse({"dispatched": True})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/mcp", tool_dispatch, methods=["POST"]),
    ],
    middleware=server.mcp_http_middleware(),
)
