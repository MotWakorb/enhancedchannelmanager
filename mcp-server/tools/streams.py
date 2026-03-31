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
    async def get_probe_progress() -> str:
        """Check the progress of an ongoing stream probe."""
        try:
            client = get_ecm_client()
            p = await client.get("/api/stream-stats/probe/progress")

            if not p.get("in_progress"):
                return "No probe is currently running."

            total = p.get("total", 0)
            current = p.get("current", 0)
            pct = (current / total * 100) if total else 0
            lines = [
                f"Probe in progress: {current}/{total} ({pct:.0f}%)",
                f"  Success: {p.get('success_count', 0)}",
                f"  Failed: {p.get('failed_count', 0)}",
                f"  Skipped: {p.get('skipped_count', 0)}",
            ]
            cur_stream = p.get("current_stream", "")
            if cur_stream:
                lines.append(f"  Currently probing: {cur_stream}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_probe_progress failed: %s", e)
            return f"Error getting probe progress: {e}"

    @mcp.tool()
    async def probe_single_stream(stream_id: int) -> str:
        """Probe a single stream to check its health.

        Args:
            stream_id: The stream ID to probe
        """
        try:
            client = get_ecm_client()
            result = await client.post(f"/api/stream-stats/probe/{stream_id}")
            status = result.get("status", result.get("probe_status", "unknown"))
            return f"Stream {stream_id} probe complete. Status: {status}"
        except Exception as e:
            logger.error("[MCP] probe_single_stream failed: %s", e)
            return f"Error probing stream {stream_id}: {e}"

    @mcp.tool()
    async def get_struck_out_streams() -> str:
        """List streams that have been struck out due to consecutive probe failures."""
        try:
            client = get_ecm_client()
            result = await client.get("/api/stream-stats/struck-out")

            streams = result.get("streams", []) if isinstance(result, dict) else result
            threshold = result.get("threshold", "?") if isinstance(result, dict) else "?"
            enabled = result.get("enabled", True) if isinstance(result, dict) else True

            if not enabled:
                return "Strike detection is disabled."

            if not streams:
                return f"No struck-out streams (threshold: {threshold} consecutive failures)."

            lines = [f"Struck-out streams ({len(streams)}, threshold: {threshold} failures):"]
            for s in streams[:30]:
                name = s.get("stream_name", s.get("name", "Unknown"))
                sid = s.get("stream_id", s.get("id", "?"))
                failures = s.get("consecutive_failures", s.get("strike_count", "?"))
                lines.append(f"  {name} (id={sid}) — {failures} consecutive failures")

            if len(streams) > 30:
                lines.append(f"  ... and {len(streams) - 30} more")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_struck_out_streams failed: %s", e)
            return f"Error getting struck-out streams: {e}"

    @mcp.tool()
    async def cancel_probe() -> str:
        """Cancel the currently running stream probe."""
        try:
            client = get_ecm_client()
            result = await client.post("/api/stream-stats/probe/cancel")
            return f"Probe cancelled. {result.get('message', '')}"
        except Exception as e:
            logger.error("[MCP] cancel_probe failed: %s", e)
            return f"Error cancelling probe: {e}"

    @mcp.tool()
    async def get_probe_results() -> str:
        """Get results from the most recent completed probe run."""
        try:
            client = get_ecm_client()
            result = await client.get("/api/stream-stats/probe/results")

            if not result:
                return "No probe results available."

            if isinstance(result, dict):
                lines = ["Latest Probe Results:"]
                for key, value in result.items():
                    label = key.replace("_", " ").title()
                    lines.append(f"  {label}: {value}")
                return "\n".join(lines)

            # If it's a list of per-stream results
            lines = [f"Probe results for {len(result)} streams:"]
            healthy = sum(1 for s in result if s.get("status") == "success")
            failed = sum(1 for s in result if s.get("status") == "failed")
            lines.append(f"  Healthy: {healthy}, Failed: {failed}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_probe_results failed: %s", e)
            return f"Error getting probe results: {e}"

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
