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
    async def get_export_sections() -> str:
        """List available YAML export sections (for selective backup)."""
        try:
            client = get_ecm_client()
            sections = await client.get("/api/backup/export-sections")
            if not sections:
                return "No export sections available."
            lines = ["Available export sections:"]
            for s in sections:
                lines.append(f"  - {s['key']}: {s['label']}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_export_sections failed: %s", e)
            return f"Error: {e}"

    @mcp.tool()
    async def list_saved_backups() -> str:
        """List saved YAML backup files on the server (created by scheduled backup task)."""
        try:
            client = get_ecm_client()
            backups = await client.get("/api/backup/saved")
            if not backups:
                return "No saved backups."
            lines = [f"Saved backups ({len(backups)}):"]
            for b in backups:
                size_kb = b.get("size_bytes", 0) / 1024
                lines.append(f"  {b['filename']} — {size_kb:.1f} KB ({b.get('created_at', '?')})")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_saved_backups failed: %s", e)
            return f"Error: {e}"

    @mcp.tool()
    async def delete_saved_backup(filename: str) -> str:
        """Delete a saved YAML backup file from the server.

        Args:
            filename: The backup filename (e.g., ecm-backup-2026-04-07_120000.yaml)
        """
        try:
            client = get_ecm_client()
            await client.delete(f"/api/backup/saved/{filename}")
            return f"Deleted backup: {filename}"
        except Exception as e:
            logger.error("[MCP] delete_saved_backup failed: %s", e)
            return f"Error: {e}"

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
