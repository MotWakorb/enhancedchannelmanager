"""MCP tool registration — collects all domain tool modules."""
from mcp.server.fastmcp import FastMCP

from . import (
    channels,
    channel_groups,
    streams,
    m3u,
    epg,
    channel_pipeline,
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
    # ti939.4.2 — appended at the END (not beside channel_pipeline) to stay
    # clear of the in-flight exclusions PR's insertion point.
    event_sync_aliases,
)

_MODULES = [
    channels,
    channel_groups,
    streams,
    m3u,
    epg,
    channel_pipeline,
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
    event_sync_aliases,
]


def register_all_tools(mcp: FastMCP):
    """Register all ECM tools with the MCP server."""
    for module in _MODULES:
        module.register(mcp)
