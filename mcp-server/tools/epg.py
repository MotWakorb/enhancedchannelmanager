"""EPG (Electronic Program Guide) tools."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_epg_sources() -> str:
        """List all configured EPG data sources."""
        try:
            client = get_ecm_client()
            sources = await client.get("/api/epg/sources")

            if not sources:
                return "No EPG sources configured."

            lines = [f"Found {len(sources)} EPG sources:"]
            for s in sources:
                name = s.get("name", "Unknown")
                sid = s.get("id", "?")
                url = s.get("url", "")[:50]
                channel_count = s.get("channel_count", 0)
                lines.append(f"  {name} (id={sid}) — {channel_count} channels, url: {url}...")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_epg_sources failed: %s", e)
            return f"Error listing EPG sources: {e}"

    @mcp.tool()
    async def refresh_epg(source_id: int) -> str:
        """Refresh an EPG source to fetch the latest program guide data.

        Args:
            source_id: The EPG source ID to refresh
        """
        try:
            client = get_ecm_client()
            result = await client.post(f"/api/epg/sources/{source_id}/refresh")
            return f"EPG source {source_id} refresh started. {result.get('message', '')}"
        except Exception as e:
            logger.error("[MCP] refresh_epg failed: %s", e)
            return f"Error refreshing EPG source {source_id}: {e}"

    @mcp.tool()
    async def match_channels_epg() -> str:
        """Auto-match channels to EPG data based on channel names."""
        try:
            client = get_ecm_client()
            result = await client.post("/api/epg/match")

            matched = result.get("matched", 0)
            unmatched = result.get("unmatched", 0)
            return f"EPG auto-match complete: {matched} channels matched, {unmatched} unmatched."
        except Exception as e:
            logger.error("[MCP] match_channels_epg failed: %s", e)
            return f"Error running EPG auto-match: {e}"
