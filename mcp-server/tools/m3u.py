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
                # Dispatcharr payload uses server_url; there is no stream_count
                # in the account payload — derive it via a page_size=1 probe.
                stream_count_str = ""
                if pid != "?":
                    try:
                        sc_resp = await client.call_endpoint(
                            ENDPOINTS["streams_list"],
                            query={"m3u_account": pid, "page_size": 1, "enrich": False},
                        )
                        stream_count = sc_resp.get("count", 0) if isinstance(sc_resp, dict) else 0
                        stream_count_str = f"{stream_count} streams, "
                    except Exception:
                        pass
                status = p.get("status", p.get("is_active", "unknown"))
                lines.append(f"  {name} (id={pid}) — {stream_count_str}status: {status}")

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

            # Dispatcharr payload: server_url holds the URL; stream_count is
            # not present in the account object — derive via streams_list.
            url_raw = a.get("server_url") or a.get("url") or ""
            url_display = url_raw[:60] + ("..." if len(url_raw) > 60 else "") if url_raw else "N/A"
            stream_count = 0
            aid = a.get("id")
            if aid is not None:
                try:
                    sc_resp = await client.call_endpoint(
                        ENDPOINTS["streams_list"],
                        query={"m3u_account": aid, "page_size": 1, "enrich": False},
                    )
                    stream_count = sc_resp.get("count", 0) if isinstance(sc_resp, dict) else 0
                except Exception:
                    pass
            lines = [
                f"M3U Account: {a.get('name', 'Unknown')}",
                f"  ID: {aid}",
                f"  Type: {a.get('account_type', a.get('server_type', 'standard'))}",
                f"  URL: {url_display}",
                f"  Status: {a.get('status', a.get('is_active', 'unknown'))}",
                f"  Streams: {stream_count}",
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
                rurl = (result.get("server_url") or result.get("url") or "")[:60]
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

    async def _resolve_group_ids(client, account_id: int, group_names: list[str]) -> tuple[list[dict], list[str]]:
        """Map a list of group names to the integer channel_group IDs expected by
        PATCH /api/m3u/accounts/{id}/group-settings.

        The M3U account response embeds ``channel_groups`` — a list of dicts with
        ``channel_group`` (int id) and ``name`` (str).  This helper fetches the
        account, builds a name→id map, then returns:

        * A list of ``{"channel_group": <id>, "name": <name>}`` dicts for each
          matched group name (preserving the order of *group_names*).
        * A list of unresolved names (those not found in the account).

        Backend expects the structured body — not a flat ``{name: bool}`` dict
        (bd-1wq7z.7).
        """
        account = await client.call_endpoint(
            ENDPOINTS["m3u_get_account"], path_args={"account_id": account_id}
        )
        # channel_groups is embedded in the account; each entry has "channel_group" (int id)
        # and "name" (str, provided by Dispatcharr).
        embedded_groups = account.get("channel_groups", []) if isinstance(account, dict) else []
        # Build case-insensitive name → id map.
        name_to_id: dict[str, int] = {}
        for g in embedded_groups:
            gname = g.get("name", "")
            gid = g.get("channel_group")
            if gname and gid is not None:
                name_to_id[gname.casefold()] = gid

        resolved: list[dict] = []
        unresolved: list[str] = []
        for name in group_names:
            gid = name_to_id.get(name.casefold())
            if gid is not None:
                resolved.append({"channel_group": gid, "name": name})
            else:
                unresolved.append(name)
        return resolved, unresolved

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
            # Resolve group name → integer channel_group id. The backend
            # PATCH /api/m3u/accounts/{id}/group-settings expects:
            # {"group_settings": [{"channel_group": <int id>, "enabled": bool}]}
            # NOT a flat {name: bool} dict (bd-1wq7z.7).
            resolved, unresolved = await _resolve_group_ids(client, account_id, [group_name])
            if unresolved:
                return (
                    f"Group '{group_name}' not found on M3U account {account_id}. "
                    f"Check the group name and try again."
                )
            group_entry = resolved[0]
            body = {
                "group_settings": [
                    {"channel_group": group_entry["channel_group"], "enabled": enabled}
                ]
            }
            await client.patch(f"/api/m3u/accounts/{account_id}/group-settings", json_data=body)  # contract-exempt: dynamic-key body (group names)
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
            # Resolve all group names → integer channel_group ids. Backend
            # expects {"group_settings": [{"channel_group": <id>, "enabled": bool}]}.
            resolved, unresolved = await _resolve_group_ids(client, account_id, list(groups.keys()))

            group_settings = [
                {"channel_group": g["channel_group"], "enabled": groups[g["name"]]}
                for g in resolved
            ]
            body = {"group_settings": group_settings}
            await client.patch(f"/api/m3u/accounts/{account_id}/group-settings", json_data=body)  # contract-exempt: dynamic-key body (group names)

            changes = [
                f"{'enabled' if groups[g['name']] else 'disabled'} '{g['name']}'"
                for g in resolved
            ]
            lines = [f"Updated {len(resolved)} groups on M3U account {account_id}:"]
            if changes:
                lines.append("  " + "\n  ".join(changes))
            if unresolved:
                lines.append(
                    f"  WARNING: {len(unresolved)} group(s) not found: "
                    + ", ".join(f"'{n}'" for n in unresolved)
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] bulk_update_m3u_group_settings failed: %s", e)
            return f"Error updating group settings: {e}"
