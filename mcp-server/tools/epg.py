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
            result = await client.post(f"/api/epg/sources/{source_id}/refresh", timeout=300.0)
            return f"EPG source {source_id} refresh started. {result.get('message', '')}"
        except Exception as e:
            logger.error("[MCP] refresh_epg failed: %s", e)
            return f"Error refreshing EPG source {source_id}: {e}"

    @mcp.tool()
    async def match_channels_epg() -> str:
        """Auto-match channels to EPG data based on channel names."""
        try:
            client = get_ecm_client()
            result = await client.post("/api/epg/match", timeout=300.0)

            matched = result.get("matched", 0)
            unmatched = result.get("unmatched", 0)
            return f"EPG auto-match complete: {matched} channels matched, {unmatched} unmatched."
        except Exception as e:
            logger.error("[MCP] match_channels_epg failed: %s", e)
            return f"Error running EPG auto-match: {e}"

    @mcp.tool()
    async def create_epg_source(name: str, url: str) -> str:
        """Create a new EPG data source.

        Args:
            name: Display name for the EPG source
            url: URL of the XMLTV EPG feed
        """
        try:
            client = get_ecm_client()
            result = await client.post("/api/epg/sources", json_data={"name": name, "url": url})
            sid = result.get("id", "?")
            return f"EPG source created: {name} (id={sid})"
        except Exception as e:
            logger.error("[MCP] create_epg_source failed: %s", e)
            return f"Error creating EPG source: {e}"

    @mcp.tool()
    async def update_epg_source(
        source_id: int,
        name: str | None = None,
        url: str | None = None,
    ) -> str:
        """Update an existing EPG source.

        Args:
            source_id: The EPG source ID to update
            name: New display name
            url: New XMLTV feed URL
        """
        try:
            client = get_ecm_client()
            payload = {}
            if name is not None:
                payload["name"] = name
            if url is not None:
                payload["url"] = url

            if not payload:
                return "No changes specified."

            result = await client.patch(f"/api/epg/sources/{source_id}", json_data=payload)
            return f"EPG source {source_id} updated."
        except Exception as e:
            logger.error("[MCP] update_epg_source failed: %s", e)
            return f"Error updating EPG source {source_id}: {e}"

    @mcp.tool()
    async def delete_epg_source(source_id: int) -> str:
        """Delete an EPG data source.

        Args:
            source_id: The EPG source ID to delete
        """
        try:
            client = get_ecm_client()
            await client.delete(f"/api/epg/sources/{source_id}")
            return f"EPG source {source_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_epg_source failed: %s", e)
            return f"Error deleting EPG source {source_id}: {e}"

    @mcp.tool()
    async def get_epg_grid(
        channel_id: int | None = None,
        limit: int = 20,
    ) -> str:
        """Get the EPG schedule grid — what's on TV now and upcoming.

        Args:
            channel_id: Optional channel ID to filter for a specific channel
            limit: Maximum number of programs to return (default 20)
        """
        try:
            client = get_ecm_client()
            result = await client.get("/api/epg/grid", channel_id=channel_id, limit=limit)

            programs = result if isinstance(result, list) else result.get("programs", [])

            if not programs:
                return "No EPG schedule data available."

            lines = [f"EPG Schedule ({min(len(programs), limit)} programs):"]
            for p in programs[:limit]:
                channel = p.get("channel_name", p.get("channel", "Unknown"))
                title = p.get("title", "Unknown")
                start = p.get("start", p.get("start_time", "?"))
                end = p.get("end", p.get("end_time", "?"))
                lines.append(f"  [{channel}] {title} ({start} - {end})")

            if len(programs) > limit:
                lines.append(f"  ... and {len(programs) - limit} more")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_epg_grid failed: %s", e)
            return f"Error getting EPG grid: {e}"
