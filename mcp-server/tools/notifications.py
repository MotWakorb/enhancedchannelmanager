"""Notification management tools."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_notifications(limit: int = 20) -> str:
        """List current notifications with unread count.

        Args:
            limit: Maximum notifications to return (default 20)
        """
        try:
            client = get_ecm_client()
            result = await client.get("/api/notifications", page_size=limit)

            notifications = result.get("notifications", []) if isinstance(result, dict) else result
            total = result.get("total", len(notifications)) if isinstance(result, dict) else len(notifications)
            unread = result.get("unread_count", 0) if isinstance(result, dict) else 0

            if not notifications:
                return "No notifications."

            lines = [f"Notifications ({unread} unread, {total} total):"]
            for n in notifications[:limit]:
                title = n.get("title", n.get("message", ""))
                source = n.get("source", "")
                read = "" if n.get("read") else " [NEW]"
                created = n.get("created_at", "")
                source_info = f" ({source})" if source else ""
                lines.append(f"  {title}{source_info}{read} — {created}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_notifications failed: %s", e)
            return f"Error listing notifications: {e}"

    @mcp.tool()
    async def mark_notifications_read() -> str:
        """Mark all notifications as read."""
        try:
            client = get_ecm_client()
            result = await client.patch("/api/notifications/mark-all-read")
            return "All notifications marked as read."
        except Exception as e:
            logger.error("[MCP] mark_notifications_read failed: %s", e)
            return f"Error marking notifications as read: {e}"

    @mcp.tool()
    async def delete_all_notifications() -> str:
        """Delete all notifications."""
        try:
            client = get_ecm_client()
            await client.delete("/api/notifications")
            return "All notifications deleted."
        except Exception as e:
            logger.error("[MCP] delete_all_notifications failed: %s", e)
            return f"Error deleting notifications: {e}"
