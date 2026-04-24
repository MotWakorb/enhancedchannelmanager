"""Read-only MCP resources for quick situational awareness."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.resource("ecm://stats/overview")
    async def stats_overview() -> str:
        """Current system overview: active channels, stream health, viewer count."""
        try:
            client = get_ecm_client()
            stats = await client.get("/api/stats/channels")

            channels = stats if isinstance(stats, list) else stats.get("channels", [])
            active = sum(1 for c in channels if c.get("active_connections", 0) > 0)
            total_viewers = sum(c.get("active_connections", 0) for c in channels)

            return (
                f"ECM Overview\n"
                f"  Total channels: {len(channels)}\n"
                f"  Active channels: {active}\n"
                f"  Total viewers: {total_viewers}"
            )
        except Exception as e:
            return f"Unable to fetch stats: {e}"

    @mcp.resource("ecm://channels/summary")
    async def channels_summary() -> str:
        """Channel count by group."""
        try:
            client = get_ecm_client()
            groups = await client.get("/api/channel-groups")

            if not groups:
                return "No channel groups."

            lines = ["Channels by Group:"]
            total = 0
            for g in groups:
                count = g.get("channel_count", 0)
                total += count
                lines.append(f"  {g.get('name', 'Unknown')}: {count} channels")
            lines.insert(1, f"  Total: {total} channels across {len(groups)} groups\n")

            return "\n".join(lines)
        except Exception as e:
            return f"Unable to fetch channel summary: {e}"

    @mcp.resource("ecm://tasks/status")
    async def tasks_status() -> str:
        """All tasks with schedule and last run info."""
        try:
            client = get_ecm_client()
            tasks = await client.get("/api/tasks")

            if not tasks:
                return "No tasks configured."

            lines = ["Task Status:"]
            for t in tasks:
                name = t.get("name", "Unknown")
                status = t.get("status", "idle")
                last_run = t.get("last_run", "never")
                enabled = "enabled" if t.get("enabled") else "disabled"
                lines.append(f"  {name}: {status} ({enabled}), last run: {last_run}")

            return "\n".join(lines)
        except Exception as e:
            return f"Unable to fetch task status: {e}"
