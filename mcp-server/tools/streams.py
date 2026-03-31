"""Stream management and health tools."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_streams(
        group: str | None = None,
        provider_id: int | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> str:
        """List streams with optional filtering.

        Args:
            group: Filter by stream group name
            provider_id: Filter by M3U provider/account ID
            search: Search streams by name
            page: Page number (default 1)
            page_size: Results per page (default 50, max 100)
        """
        try:
            client = get_ecm_client()
            result = await client.get(
                "/api/streams",
                group=group,
                provider_id=provider_id,
                search=search,
                page=page,
                page_size=min(page_size, 100),
            )

            if isinstance(result, dict):
                streams = result.get("results", result.get("streams", []))
                total = result.get("count", len(streams))
            else:
                streams = result
                total = len(streams)

            if not streams:
                return "No streams found."

            lines = [f"Showing {len(streams)} of {total} streams (page {page}):"]
            for s in streams:
                name = s.get("name", "Unknown")
                sid = s.get("id", "?")
                group_name = s.get("group", "")
                provider = s.get("provider_name", "")
                info = f" [{group_name}]" if group_name else ""
                info += f" from {provider}" if provider else ""
                lines.append(f"  {name} (id={sid}){info}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_streams failed: %s", e)
            return f"Error listing streams: {e}"

    @mcp.tool()
    async def get_stream_health() -> str:
        """Get an overview of stream health from the most recent probe results."""
        try:
            client = get_ecm_client()
            summary = await client.get("/api/stream-stats/summary")

            if not summary:
                return "No stream health data available. Run a probe first."

            lines = ["Stream Health Summary:"]
            for key, value in summary.items():
                label = key.replace("_", " ").title()
                lines.append(f"  {label}: {value}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_stream_health failed: %s", e)
            return f"Error getting stream health: {e}"

    @mcp.tool()
    async def probe_streams() -> str:
        """Start probing all streams to check their health. This runs in the background and may take a while."""
        try:
            client = get_ecm_client()
            result = await client.post("/api/stream-stats/probe/all")
            return f"Stream probe started. {result.get('message', 'Check progress in ECM.')}"
        except Exception as e:
            logger.error("[MCP] probe_streams failed: %s", e)
            return f"Error starting stream probe: {e}"

    @mcp.tool()
    async def get_streams_for_channel(channel_id: int) -> str:
        """Get detailed stream information for a specific channel, including stream names and metadata.

        Args:
            channel_id: The channel ID to get streams for
        """
        try:
            client = get_ecm_client()
            result = await client.get(f"/api/channels/{channel_id}/streams")

            streams = result if isinstance(result, list) else result.get("streams", result.get("results", []))

            if not streams:
                return f"Channel {channel_id} has no streams assigned."

            lines = [f"Channel {channel_id} has {len(streams)} streams:"]
            for i, s in enumerate(streams, 1):
                name = s.get("name", "Unknown")
                sid = s.get("id", "?")
                group = s.get("group", "")
                provider = s.get("provider_name", s.get("m3u_account", ""))
                info = f" [{group}]" if group else ""
                info += f" from {provider}" if provider else ""
                lines.append(f"  {i}. {name} (id={sid}){info}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_streams_for_channel failed: %s", e)
            return f"Error getting streams for channel {channel_id}: {e}"

    @mcp.tool()
    async def search_streams(
        query: str,
        provider_id: int | None = None,
        limit: int = 25,
    ) -> str:
        """Search for streams by name across all providers.

        Args:
            query: Search term to match against stream names
            provider_id: Optional M3U provider ID to narrow the search
            limit: Maximum results to return (default 25)
        """
        try:
            client = get_ecm_client()
            result = await client.get(
                "/api/streams",
                search=query,
                provider_id=provider_id,
                page_size=min(limit, 100),
            )

            if isinstance(result, dict):
                streams = result.get("results", result.get("streams", []))
                total = result.get("count", len(streams))
            else:
                streams = result
                total = len(streams)

            if not streams:
                return f"No streams found matching '{query}'."

            lines = [f"Found {total} streams matching '{query}' (showing {min(len(streams), limit)}):"]
            for s in streams[:limit]:
                name = s.get("name", "Unknown")
                sid = s.get("id", "?")
                group = s.get("group", "")
                provider = s.get("provider_name", "")
                info = f" [{group}]" if group else ""
                info += f" from {provider}" if provider else ""
                lines.append(f"  {name} (id={sid}){info}")

            if total > limit:
                lines.append(f"  ... and {total - limit} more results")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] search_streams failed: %s", e)
            return f"Error searching streams: {e}"
