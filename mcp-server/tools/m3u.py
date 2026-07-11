"""M3U account management tools."""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client
from tools import _guardrails

logger = logging.getLogger(__name__)


async def _get_priorities(client) -> dict:
    """Fetch the ECM-local M3U account priority map (account_id str -> priority).

    Lives in ``settings.m3u_account_priorities`` (Smart Sort's "m3u_priority"
    criterion) — NOT a Dispatcharr account field, so it's read via
    ``/api/settings`` rather than the account endpoint. Reuses
    ``_guardrails.get_caps_settings`` (degrades to ``{}`` on failure) since this
    is a read-only display lookup, not the authoritative fetch a save needs.
    """
    settings = await _guardrails.get_caps_settings(client)
    return dict(settings.get("m3u_account_priorities", {}))


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_m3u_accounts() -> str:
        """List all configured M3U provider accounts."""
        try:
            client = get_ecm_client()
            providers = await client.call_endpoint(ENDPOINTS["m3u_list_providers"])

            if not providers:
                return "No M3U accounts configured."

            # m3u_account_priorities is an ECM-local setting (Smart Sort's
            # "m3u_priority" criterion) — one settings fetch covers every
            # account in the list, rather than N+1 lookups.
            priorities = await _get_priorities(client)

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
                        pass  # Stream count is supplementary display info; degrade gracefully.
                status = p.get("status", p.get("is_active", "unknown"))
                priority = priorities.get(str(pid))
                priority_str = f", priority: {priority}" if priority is not None else ""
                lines.append(f"  {name} (id={pid}) — {stream_count_str}status: {status}{priority_str}")

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
                    pass  # Stream count is supplementary display info; degrade gracefully.
            priority = (await _get_priorities(client)).get(str(aid)) if aid is not None else None
            lines = [
                f"M3U Account: {a.get('name', 'Unknown')}",
                f"  ID: {aid}",
                f"  Type: {a.get('account_type', a.get('server_type', 'standard'))}",
                f"  URL: {url_display}",
                f"  Status: {a.get('status', a.get('is_active', 'unknown'))}",
                f"  Streams: {stream_count}",
                f"  Priority: {priority if priority is not None else 'not set (Smart Sort default)'}",
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
        account_type: str = "STD",
        server_type: str | None = None,
        username: str | None = None,
        password: str | None = None,
        file_path: str | None = None,
        server_group: int | None = None,
        max_streams: int | None = None,
        is_active: bool | None = None,
        refresh_interval: int | None = None,
        stale_stream_days: int | None = None,
        enable_vod: bool | None = None,
        auto_enable_new_groups_live: bool | None = None,
        auto_enable_new_groups_vod: bool | None = None,
        auto_enable_new_groups_series: bool | None = None,
    ) -> str:
        """Create a new M3U provider account.

        enhancedchannelmanager-yd6qh: expanded to the full field set Dispatcharr
        accepts (previously only name/url/server_type — most of the account
        config, and Xtream credentials specifically, had no MCP path at all).
        This also FIXES a latent bug: the old ``server_type`` param sent a
        literal ``server_type`` key, which is not a real Dispatcharr field
        (the real field is ``account_type``, values "STD"/"XC") — Dispatcharr
        silently ignored it, so ``server_type`` never actually set the account
        type. ``account_type`` now sends the correct field; ``server_type`` is
        kept only as a deprecated convenience alias (see below).

        Args:
            name: Display name for the account
            url: URL of the M3U playlist (STD) or Xtream server URL (XC)
            account_type: Dispatcharr account type — "STD" (standard M3U
                URL/file, default) or "XC" (Xtream Codes; requires username
                and password). For an HD HomeRun source, use "STD" with the
                lineup URL directly in `url` (e.g. "http://<ip>/lineup.m3u").
            server_type: DEPRECATED legacy alias for account_type — only takes
                effect when account_type is left at its default. Accepts
                "standard" (-> STD), "xtream" (-> XC), "hdhr" (-> STD).
            username: XtreamCodes account username (account_type="XC")
            password: XtreamCodes account password (account_type="XC"). Never
                echoed back in this tool's result.
            file_path: Path to a previously-uploaded M3U file (see the M3U
                upload flow) — an alternative to `url` for STD accounts.
            server_group: Optional server group ID (see server group tools)
            max_streams: Max concurrent streams for this account (0 = unlimited)
            is_active: Whether the account is active (backend default True)
            refresh_interval: Auto-refresh interval in hours (backend default 24)
            stale_stream_days: Days of no activity before a stream is flagged
                stale (backend default 7)
            enable_vod: Whether to import VOD content (backend default False)
            auto_enable_new_groups_live: Auto-enable newly discovered LIVE
                groups (backend default True)
            auto_enable_new_groups_vod: Auto-enable newly discovered VOD groups
                (backend default False)
            auto_enable_new_groups_series: Auto-enable newly discovered SERIES
                groups (backend default False)
        """
        try:
            client = get_ecm_client()
            resolved_type = account_type
            if server_type is not None and account_type == "STD":
                # Legacy alias only takes effect when the caller left account_type
                # at its default — never silently overrides an explicit choice.
                legacy_map = {"standard": "STD", "xtream": "XC", "hdhr": "STD"}
                resolved_type = legacy_map.get(server_type.lower(), account_type)

            payload: dict = {"name": name, "url": url, "account_type": resolved_type}
            if file_path is not None:
                payload["file_path"] = file_path
            if server_group is not None:
                payload["server_group"] = server_group
            if max_streams is not None:
                payload["max_streams"] = max_streams
            if is_active is not None:
                payload["is_active"] = is_active
            if refresh_interval is not None:
                payload["refresh_interval"] = refresh_interval
            if stale_stream_days is not None:
                payload["stale_stream_days"] = stale_stream_days
            if enable_vod is not None:
                payload["enable_vod"] = enable_vod
            if auto_enable_new_groups_live is not None:
                payload["auto_enable_new_groups_live"] = auto_enable_new_groups_live
            if auto_enable_new_groups_vod is not None:
                payload["auto_enable_new_groups_vod"] = auto_enable_new_groups_vod
            if auto_enable_new_groups_series is not None:
                payload["auto_enable_new_groups_series"] = auto_enable_new_groups_series
            # Credential params: accepted and forwarded, never echoed back below.
            if username is not None:
                payload["username"] = username
            if password is not None:
                payload["password"] = password

            result = await client.call_endpoint(ENDPOINTS["m3u_create_account"], body=payload)
            aid = result.get("id", "?") if isinstance(result, dict) else "?"
            rname = result.get("name", name) if isinstance(result, dict) else name
            return f"M3U account created: {rname} (id={aid}, type={resolved_type})"
        except Exception as e:
            logger.error("[MCP] create_m3u_account failed: %s", e)
            return f"Error creating M3U account: {e}"

    @mcp.tool()
    async def update_m3u_account(
        account_id: int,
        name: str | None = None,
        url: str | None = None,
        is_active: bool | None = None,
    ) -> str:
        """Update an existing M3U account.

        Args:
            account_id: The M3U account ID to update
            name: New display name
            url: New M3U playlist URL
            is_active: Enable/disable the account
        """
        try:
            client = get_ecm_client()
            payload = {}
            if name is not None:
                payload["name"] = name
            if url is not None:
                payload["url"] = url
            if is_active is not None:
                payload["is_active"] = is_active

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
    async def set_m3u_account_priority(account_id: int, priority: int) -> str:
        """Set (or clear) an M3U account's Smart Sort priority.

        This is an ECM-local setting (stored in ``settings.m3u_account_priorities``,
        NOT a Dispatcharr account field) consumed by Smart Sort's "m3u_priority"
        criterion — higher priority wins when that criterion is enabled
        (Settings -> Stream Sort). It has no effect unless "m3u_priority" is
        enabled in the stream sort configuration.

        Args:
            account_id: The M3U account ID
            priority: Priority value, 1-100 (higher = preferred streams from this
                account sort first). 0 clears the priority for this account.
        """
        if priority < 0 or priority > 100:
            return "Priority must be between 0 and 100 (0 clears the account's priority)."
        try:
            client = get_ecm_client()
            # Best-effort display name only — deliberately NOT allowed to block the
            # priority write below. delete_m3u_account doesn't clean up
            # m3u_account_priorities, so a stale entry for an already-deleted
            # account (404 here) must still be clearable with priority=0.
            name = str(account_id)
            try:
                acct = await client.call_endpoint(
                    ENDPOINTS["m3u_get_account"], path_args={"account_id": account_id}
                )
                if isinstance(acct, dict):
                    name = acct.get("name", name)
            except Exception:
                pass  # Account may be gone; still allow clearing a stale priority entry.

            # Read-modify-write the full settings blob, mirroring the frontend's
            # M3UManagerTab save pattern ({...settings, m3u_account_priorities}) —
            # m3u_account_priorities isn't in the admin-only settings field list,
            # so the MCP service principal is permitted to write it. This fetch
            # must raise on failure (unlike _get_priorities' advisory read) —
            # swallowing it here would POST a settings blob missing every other
            # field, resetting them to defaults.
            current = await client.call_endpoint(ENDPOINTS["settings_get"])
            priorities = dict(current.get("m3u_account_priorities", {}))
            if priority == 0:
                priorities.pop(str(account_id), None)
            else:
                priorities[str(account_id)] = priority

            payload = {**current, "m3u_account_priorities": priorities}
            await client.post("/api/settings", json_data=payload)  # contract-exempt: full settings round-trip (dynamic body mirrors M3UManagerTab save pattern)

            if priority == 0:
                return f"Priority cleared for M3U account {account_id} ('{name}')."
            return f"M3U account {account_id} ('{name}') priority set to {priority}."
        except Exception as e:
            logger.error("[MCP] set_m3u_account_priority failed: %s", e)
            return f"Error setting priority for M3U account {account_id}: {e}"

    @mcp.tool()
    async def delete_m3u_account(account_id: int, confirm: bool = False) -> str:
        """Delete an M3U provider account and all its streams.

        CONFIRM GATING (bd-onazy): this is a two-call operation and DESTRUCTIVE
        (it removes the account AND all its streams). The first call
        (``confirm=False``, the default) fetches the account and returns a
        preview naming it, deleting NOTHING. Re-invoke with ``confirm=True`` to
        actually delete.

        Args:
            account_id: The M3U account ID to delete
            confirm: Set True on the second call to perform the deletion.
        """
        try:
            client = get_ecm_client()
            if not confirm:
                acct = await client.call_endpoint(
                    ENDPOINTS["m3u_get_account"], path_args={"account_id": account_id}
                )
                name = acct.get("name", "?") if isinstance(acct, dict) else "?"
                url = acct.get("url", "") if isinstance(acct, dict) else ""
                return (
                    f"M3U account {account_id} '{name}' ({url}) and ALL its streams "
                    f"will be deleted — re-invoke with confirm=True to delete."
                )
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
