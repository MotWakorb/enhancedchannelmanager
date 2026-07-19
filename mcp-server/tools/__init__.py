"""MCP tool registration — collects all domain tool modules."""
from mcp.server.fastmcp import FastMCP

from . import (
    channels,
    channel_groups,
    streams,
    m3u,
    epg,
    channel_pipeline,
    event_sync_exclusions,
    cloud_targets,
    tasks,
    stats,
    system,
    notifications,
    profiles,
    normalization,
    dedup,
    emby,
    logos,
    tags,
    sync_targets,
    channels_csv,
)

_MODULES = [
    channels,
    channel_groups,
    streams,
    m3u,
    epg,
    channel_pipeline,
    event_sync_exclusions,
    cloud_targets,
    tasks,
    stats,
    system,
    notifications,
    profiles,
    normalization,
    dedup,
    emby,
    logos,
    tags,
    sync_targets,
    channels_csv,
]


def register_all_tools(mcp: FastMCP):
    """Register all ECM tools with the MCP server."""
    for module in _MODULES:
        module.register(mcp)
