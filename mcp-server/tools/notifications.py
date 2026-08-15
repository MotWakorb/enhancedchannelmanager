"""Notification management tools."""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
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
            result = await client.call_endpoint(ENDPOINTS["notifications_list"], query={"page_size": limit})

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
            await client.call_endpoint(ENDPOINTS["notifications_mark_all_read"])
            # Read-back: confirm there are no unread notifications left.
            try:
                result = await client.call_endpoint(ENDPOINTS["notifications_list"], query={"page_size": 1})
                unread = result.get("unread_count", 0) if isinstance(result, dict) else 0
            except Exception:
                unread = None
            if unread:
                return f"WARNING: marked all read but {unread} notification(s) still show as unread."
            return "All notifications marked as read."
        except Exception as e:
            logger.error("[MCP] mark_notifications_read failed: %s", e)
            return f"Error marking notifications as read: {e}"

    @mcp.tool()
    async def delete_all_notifications(include_unread: bool = False) -> str:
        """Delete notifications.

        By default only deletes read notifications (safe behaviour).  Set
        ``include_unread=True`` to also delete unread ones.

        The backend DELETE /api/notifications accepts a ``read_only`` query
        parameter (default ``True``) that controls whether unread notifications
        are included.  Without this parameter the tool can only ever delete read
        notifications (bd-1wq7z.14).

        Args:
            include_unread: If False (default), only delete read notifications.
                            If True, delete all notifications including unread ones.
        """
        try:
            client = get_ecm_client()
            # read_only=True → only delete read notifications (safe default).
            # read_only=False → delete all including unread (opt-in).
            read_only = not include_unread
            await client.call_endpoint(
                ENDPOINTS["notifications_delete_all"],
                query={"read_only": read_only},
            )
            # Read-back: confirm the notification list reflects the deletion.
            try:
                result = await client.call_endpoint(ENDPOINTS["notifications_list"], query={"page_size": 1})
                remaining = result.get("total", 0) if isinstance(result, dict) else len(result or [])
            except Exception:
                remaining = None
            scope = "all" if include_unread else "read"
            if remaining:
                return (
                    f"WARNING: requested delete-all ({scope}) but {remaining} "
                    f"notification(s) remain."
                )
            return f"All {scope} notifications deleted."
        except Exception as e:
            logger.error("[MCP] delete_all_notifications failed: %s", e)
            return f"Error deleting notifications: {e}"

    @mcp.tool()
    async def list_alert_methods() -> str:
        """List all configured alert methods.

        The backing endpoint, ``GET /api/alert-methods``, carried no route
        dependency at all until bead enhancedchannelmanager-9kwzp.10 item 4,
        which gated it as ADMIN — plain ``RequireAdminIfEnabled``, which
        admits the MCP service principal, so this tool keeps working. That
        bead briefly human-admin-gated the route (403 to this tool); the PO
        reversed it, because the tool is the operator's inventory of their
        own alert methods.

        THE RESPONSE IS MASKED AT THE SOURCE (bead
        enhancedchannelmanager-9kwzp.13). The endpoint used to return each
        method's ``config`` VERBATIM, which handed this principal the Discord
        webhook URL, the Telegram bot token and the SMTP password, the exact
        values bead 9ej7f withholds from it on ``GET /api/settings``. The
        handler now serializes through
        ``models.AlertMethod.to_dict(include_sensitive=False)``, so those keys
        come back as ``********`` and no credential VALUE reaches this tool.

        The response is still operator configuration rather than public data:
        it carries the non-credential config keys, which for Telegram includes
        the destination ``chat_id`` and for SMTP the recipient list. This tool
        prints only the name, id, type, enabled flag and severity filters, and
        deliberately does NOT print ``config``. Keep it that way, and do not
        echo a raw response from this endpoint into a transcript.

        The four write/single-read routes on this router (create, get by id,
        update, delete) DO refuse this principal, and ``test_alert_method``
        has been refused since bead 9kwzp.6. There are no MCP tools for them;
        an ECM admin manages alert methods in the UI, Settings tab, Alert
        Methods section.
        """
        try:
            client = get_ecm_client()
            methods = await client.call_endpoint(ENDPOINTS["alert_methods_list"])

            if not methods:
                return "No alert methods configured."

            lines = [f"Alert Methods ({len(methods)}):"]
            for m in methods:
                name = m.get("name", "Unnamed")
                mid = m.get("id", "?")
                mtype = m.get("method_type", "?")
                enabled = "enabled" if m.get("enabled") else "disabled"
                levels = []
                if m.get("notify_error"):
                    levels.append("error")
                if m.get("notify_warning"):
                    levels.append("warning")
                if m.get("notify_success"):
                    levels.append("success")
                if m.get("notify_info"):
                    levels.append("info")
                level_str = f" [{', '.join(levels)}]" if levels else ""
                lines.append(f"  {name} (id={mid}) — {mtype}, {enabled}{level_str}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_alert_methods failed: %s", e)
            return f"Error listing alert methods: {e}"

    @mcp.tool()
    async def test_alert_method(method_id: int) -> str:
        """Send a test notification through an alert method. NOT USABLE OVER MCP.

        REFUSED FOR THE MCP CREDENTIAL whenever ECM authentication is enabled
        (bead enhancedchannelmanager-9kwzp.6), and since bead
        enhancedchannelmanager-2u4e0 also whenever authentication is DISABLED on
        an instance that already holds an operator identity. The backing endpoint,
        ``POST /api/alert-methods/{method_id}/test``, carries
        ``RequireHumanAdminForOutboundTest``, which rejects the static MCP
        service principal with HTTP 403 and the body "The MCP service
        principal cannot run connection tests." Calling this tool returns that
        403 as an error string. It is the designed outcome, not a
        misconfiguration and not a permission an operator can grant to the MCP
        key: the test sends with the method's STORED credentials (the Discord
        webhook URL, the Telegram bot token, the SMTP password held in
        ``AlertMethod.config``), which the caller never had to know, and
        reports the upstream verdict back, so it is reserved for a human
        operator identity.

        HUMAN PATH: an ECM admin runs it from the UI, Settings tab, Alert
        Methods section, via the "Send test message" button on the method.
        There is no MCP equivalent. ``list_alert_methods`` works, so the
        configured methods remain readable over MCP; only the send is gated.

        The one case where this tool still works is an install with
        authentication turned off, because the gate no-ops when
        ``require_auth`` is false or setup is incomplete. Do not rely on that.

        Args:
            method_id: The alert method ID to test
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["alert_methods_test"], path_args={"method_id": method_id})
            success = result.get("success", False) if isinstance(result, dict) else False
            message = result.get("message", "") if isinstance(result, dict) else ""
            if success:
                return f"Test alert sent successfully. {message}"
            return f"Test alert failed: {message}"
        except Exception as e:
            logger.error("[MCP] test_alert_method failed: %s", e)
            return f"Error testing alert method {method_id}: {e}"
