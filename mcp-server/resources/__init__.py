"""MCP resource registration."""
from mcp.server.fastmcp import FastMCP

from . import overview


def register_all_resources(mcp: FastMCP):
    """Register all ECM resources with the MCP server."""
    overview.register(mcp)
