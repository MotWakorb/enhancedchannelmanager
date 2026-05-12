"""M3U account management tools."""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_m3u_accounts() -> str:
        """List all configured M3U provider accounts."""
        try:
            client = get_ecm_client()
            providers = await client.call_endpoint(ENDPOINTS["m3u_list_providers"])

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
            result = await client.call_endpoint(
                ENDPOINTS["m3u_refresh_account"], path_args={"account_id": account_id}, timeout=300.0,
            )
            msg = result.get("message", "") if isinstance(result, dict) else ""
            return f"M3U account {account_id} refresh started. {msg}"
        except Exception as e:
            logger.error("[MCP] refresh_m3u failed: %s", e)
            return f"Error refreshing M3U account {account_id}: {e}"

    @mcp.tool()
    async def refresh_all_m3u() -> str:
        """Refresh all M3U accounts to fetch the latest stream lists."""
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["m3u_refresh_all"], timeout=300.0)
            msg = result.get("message", "") if isinstance(result, dict) else ""
            return f"M3U refresh started for all accounts. {msg}"
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
            a = await client.call_endpoint(ENDPOINTS["m3u_get_account"], path_args={"account_id": account_id})

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
            result = await client.call_endpoint(
                ENDPOINTS["m3u_create_account"],
                body={"name": name, "url": url, "server_type": server_type},
            )
            aid = result.get("id", "?") if isinstance(result, dict) else "?"
            rname = result.get("name", name) if isinstance(result, dict) else name
            return f"M3U account created: {rname} (id={aid})"
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

            result = await client.call_endpoint(
                ENDPOINTS["m3u_update_account"], path_args={"account_id": account_id}, body=payload,
            )
            if isinstance(result, dict):
                rname = result.get("name", "?")
                rurl = (result.get("url") or "")[:60]
                return f"M3U account {account_id} updated: name='{rname}', url='{rurl}'"
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
            await client.call_endpoint(ENDPOINTS["m3u_delete_account"], path_args={"account_id": account_id})
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
            # PATCH /api/m3u/accounts/{id}/group-settings takes a body keyed by
            # arbitrary group names — it can't be expressed as one Endpoint with
            # a fixed request_fields set, so it stays on raw client.patch.
            await client.patch(f"/api/m3u/accounts/{account_id}/group-settings", json_data={group_name: enabled})  # contract-exempt: dynamic-key body (group names)
            state = "enabled" if enabled else "disabled"
            return f"Group '{group_name}' {state} on M3U account {account_id}."
        except Exception as e:
            logger.error("[MCP] update_m3u_group_settings failed: %s", e)
            return f"Error updating group settings: {e}"

    @mcp.tool()
    async def bulk_update_m3u_group_settings(
        account_id: int,
        groups: dict[str, bool],
    ) -> str:
        """Enable or disable multiple stream groups on an M3U account at once.

        Args:
            account_id: The M3U account ID
            groups: Dict of group_name -> enabled. Example: {"Sports": false, "News": false, "Movies": true}
        """
        try:
            client = get_ecm_client()
            # Dynamic-key body (group names) — see update_m3u_group_settings.
            await client.patch(f"/api/m3u/accounts/{account_id}/group-settings", json_data=groups)  # contract-exempt: dynamic-key body (group names)
            changes = [f"{'enabled' if v else 'disabled'} '{k}'" for k, v in groups.items()]
            return f"Updated {len(groups)} groups on M3U account {account_id}:\n  " + "\n  ".join(changes)
        except Exception as e:
            logger.error("[MCP] bulk_update_m3u_group_settings failed: %s", e)
            return f"Error updating group settings: {e}"
