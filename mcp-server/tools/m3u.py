"""M3U account management tools."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_m3u_accounts() -> str:
        """List all configured M3U provider accounts."""
        try:
            client = get_ecm_client()
            providers = await client.get("/api/providers")

            if not providers:
                return "No M3U accounts configured."

            lines = [f"Found {len(providers)} M3U accounts:"]
            for p in providers:
                name = p.get("name", "Unknown")
                pid = p.get("id", "?")
                stream_count = p.get("stream_count", 0)
                status = p.get("status", "unknown")
                lines.append(f"  {name} (id={pid}) — {stream_count} streams, status: {status}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_m3u_accounts failed: %s", e)
            return f"Error listing M3U accounts: {e}"

    @mcp.tool()
    async def refresh_m3u(account_id: int) -> str:
        """Refresh a specific M3U account to fetch the latest stream list.

        Args:
            account_id: The M3U account ID to refresh
        """
        try:
            client = get_ecm_client()
            result = await client.post(f"/api/m3u/refresh/{account_id}", timeout=300.0)
            return f"M3U account {account_id} refresh started. {result.get('message', '')}"
        except Exception as e:
            logger.error("[MCP] refresh_m3u failed: %s", e)
            return f"Error refreshing M3U account {account_id}: {e}"

    @mcp.tool()
    async def refresh_all_m3u() -> str:
        """Refresh all M3U accounts to fetch the latest stream lists."""
        try:
            client = get_ecm_client()
            result = await client.post("/api/m3u/refresh", timeout=300.0)
            return f"M3U refresh started for all accounts. {result.get('message', '')}"
        except Exception as e:
            logger.error("[MCP] refresh_all_m3u failed: %s", e)
            return f"Error refreshing M3U accounts: {e}"

    @mcp.tool()
    async def get_m3u_account(account_id: int) -> str:
        """Get detailed information about a specific M3U account.

        Args:
            account_id: The M3U account ID to look up
        """
        try:
            client = get_ecm_client()
            a = await client.get(f"/api/m3u/accounts/{account_id}")

            lines = [
                f"M3U Account: {a.get('name', 'Unknown')}",
                f"  ID: {a.get('id')}",
                f"  Type: {a.get('type', a.get('server_type', 'standard'))}",
                f"  URL: {a.get('url', 'N/A')[:60]}{'...' if len(a.get('url', '')) > 60 else ''}",
                f"  Status: {a.get('status', 'unknown')}",
                f"  Streams: {a.get('stream_count', 0)}",
                f"  Last refresh: {a.get('last_refresh', a.get('updated_at', 'never'))}",
            ]

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_m3u_account failed: %s", e)
            return f"Error getting M3U account {account_id}: {e}"

    @mcp.tool()
    async def create_m3u_account(
        name: str,
        url: str,
        server_type: str = "standard",
    ) -> str:
        """Create a new M3U provider account.

        Args:
            name: Display name for the account
            url: URL of the M3U playlist
            server_type: Account type — "standard" (M3U URL), "xtream" (Xtream Codes), or "hdhr" (HD Homerun)
        """
        try:
            client = get_ecm_client()
            result = await client.post("/api/m3u/accounts", json_data={
                "name": name,
                "url": url,
                "server_type": server_type,
            })
            aid = result.get("id", "?")
            return f"M3U account created: {name} (id={aid})"
        except Exception as e:
            logger.error("[MCP] create_m3u_account failed: %s", e)
            return f"Error creating M3U account: {e}"

    @mcp.tool()
    async def update_m3u_account(
        account_id: int,
        name: str | None = None,
        url: str | None = None,
    ) -> str:
        """Update an existing M3U account.

        Args:
            account_id: The M3U account ID to update
            name: New display name
            url: New M3U playlist URL
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

            result = await client.patch(f"/api/m3u/accounts/{account_id}", json_data=payload)
            return f"M3U account {account_id} updated."
        except Exception as e:
            logger.error("[MCP] update_m3u_account failed: %s", e)
            return f"Error updating M3U account {account_id}: {e}"

    @mcp.tool()
    async def delete_m3u_account(account_id: int) -> str:
        """Delete an M3U provider account and all its streams.

        Args:
            account_id: The M3U account ID to delete
        """
        try:
            client = get_ecm_client()
            await client.delete(f"/api/m3u/accounts/{account_id}")
            return f"M3U account {account_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_m3u_account failed: %s", e)
            return f"Error deleting M3U account {account_id}: {e}"

    @mcp.tool()
    async def update_m3u_group_settings(
        account_id: int,
        group_name: str,
        enabled: bool,
    ) -> str:
        """Enable or disable a stream group on an M3U account.

        Args:
            account_id: The M3U account ID
            group_name: The stream group name to toggle
            enabled: True to enable, False to disable
        """
        try:
            client = get_ecm_client()
            result = await client.patch(
                f"/api/m3u/accounts/{account_id}/group-settings",
                json_data={group_name: enabled},
            )
            state = "enabled" if enabled else "disabled"
            return f"Group '{group_name}' {state} on M3U account {account_id}."
        except Exception as e:
            logger.error("[MCP] update_m3u_group_settings failed: %s", e)
            return f"Error updating group settings: {e}"
