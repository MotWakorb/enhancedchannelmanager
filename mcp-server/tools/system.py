"""System management tools (settings, backup, journal)."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def get_settings() -> str:
        """Get current ECM settings (connection status, preferences, probe configuration)."""
        try:
            client = get_ecm_client()
            s = await client.get("/api/settings")

            lines = [
                "ECM Settings:",
                f"  Dispatcharr URL: {s.get('url', 'Not configured')}",
                f"  Connected: {s.get('configured', False)}",
                f"  Theme: {s.get('theme', 'dark')}",
                f"  Timezone: {s.get('user_timezone', 'UTC') or 'UTC'}",
                "",
                "Probe Settings:",
                f"  Timeout: {s.get('stream_probe_timeout', 30)}s",
                f"  Parallel: {s.get('parallel_probing_enabled', True)}",
                f"  Max Concurrent: {s.get('max_concurrent_probes', 8)}",
                f"  Schedule: {s.get('stream_probe_schedule_time', '03:00')}",
                "",
                "Notifications:",
                f"  SMTP: {'configured' if s.get('smtp_configured') else 'not configured'}",
                f"  Discord: {'configured' if s.get('discord_configured') else 'not configured'}",
                f"  Telegram: {'configured' if s.get('telegram_configured') else 'not configured'}",
            ]

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_settings failed: %s", e)
            return f"Error getting settings: {e}"

    @mcp.tool()
    async def create_backup() -> str:
        """Create a backup of all ECM configuration (settings, database, logos)."""
        try:
            client = get_ecm_client()
            # The backup endpoint returns a file download — we just trigger it
            # and report success. The actual download happens through the ECM UI.
            await client.get("/api/backup/create")
            return "Backup created successfully. Download it from the ECM Settings page."
        except Exception as e:
            logger.error("[MCP] create_backup failed: %s", e)
            return f"Error creating backup: {e}"

    @mcp.tool()
    async def get_journal(
        limit: int = 20,
        category: str | None = None,
    ) -> str:
        """Get recent entries from the ECM activity journal/audit log.

        Args:
            limit: Number of entries to return (default 20)
            category: Filter by category (e.g., 'channels', 'm3u', 'epg', 'settings')
        """
        try:
            client = get_ecm_client()
            entries = await client.get("/api/journal", limit=limit, category=category)

            items = entries if isinstance(entries, list) else entries.get("entries", [])

            if not items:
                return "No journal entries found."

            lines = [f"Recent journal entries ({len(items)}):"]
            for e in items[:limit]:
                ts = e.get("timestamp", "?")
                cat = e.get("category", "?")
                action = e.get("action_type", e.get("action", "?"))
                detail = e.get("detail", e.get("description", ""))[:80]
                lines.append(f"  [{ts}] {cat}/{action}: {detail}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_journal failed: %s", e)
            return f"Error getting journal: {e}"
