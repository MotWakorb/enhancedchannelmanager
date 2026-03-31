"""Channel statistics tools."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def get_channel_stats() -> str:
        """Get channel viewing statistics including active viewers and stream status."""
        try:
            client = get_ecm_client()
            stats = await client.get("/api/stats/channels")

            if not stats:
                return "No channel statistics available."

            channels = stats if isinstance(stats, list) else stats.get("channels", [])

            if not channels:
                return "No active channels."

            active = [c for c in channels if c.get("active_connections", 0) > 0]

            lines = [f"Channel Stats ({len(active)} active of {len(channels)} total):"]

            if active:
                lines.append("\nActive channels:")
                for c in active:
                    name = c.get("channel_name", c.get("name", "Unknown"))
                    viewers = c.get("active_connections", 0)
                    lines.append(f"  {name} — {viewers} viewer(s)")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_channel_stats failed: %s", e)
            return f"Error getting channel stats: {e}"
