"""MCP tool registration — collects all domain tool modules."""
from mcp.server.fastmcp import FastMCP

from . import (
    channels,
    channel_groups,
    streams,
    m3u,
    epg,
    auto_creation,
    export,
    tasks,
    stats,
    system,
)

_MODULES = [
    channels,
    channel_groups,
    streams,
    m3u,
    epg,
    auto_creation,
    export,
    tasks,
    stats,
    system,
]


def register_all_tools(mcp: FastMCP):
    """Register all ECM tools with the MCP server."""
    for module in _MODULES:
        module.register(mcp)
