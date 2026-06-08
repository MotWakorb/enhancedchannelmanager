"""EPG (Electronic Program Guide) tools."""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


async def _build_channel_uuid_map(client) -> dict:
    """Fetch all channels and map ``uuid -> {"id", "name"}``.

    Dispatcharr EPG grid program items key the channel by ``channel_uuid``
    (NOT ``channel_id`` / ``channel_name``), so the grid renderer resolves
    names and the channel-id filter through the channel list (lq38l.13 #3).
    A failed lookup degrades to an empty map (caller falls back to the uuid).
    """
    uuid_map: dict = {}
    try:
        page = 1
        while True:
            result = await client.call_endpoint(
                ENDPOINTS["channels_list"],
                query={"page": page, "page_size": 500},
            )
            if isinstance(result, dict):
                batch = result.get("results", result.get("channels", []))
            else:
                batch = result
            if not batch:
                break
            for c in batch:
                if isinstance(c, dict) and c.get("uuid"):
                    uuid_map[c["uuid"]] = {"id": c.get("id"), "name": c.get("name")}
            if not (isinstance(result, dict) and result.get("next")):
                break
            page += 1
    except Exception as e:  # degrade gracefully
        logger.warning("[MCP] could not resolve channel names for EPG grid: %s", e)
    return uuid_map


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_epg_sources() -> str:
        """List all configured EPG data sources."""
        try:
            client = get_ecm_client()
            sources = await client.call_endpoint(ENDPOINTS["epg_list_sources"])
            if isinstance(sources, dict):
                sources = sources.get("sources", sources.get("results", []))

            if not sources:
                return "No EPG sources configured."

            lines = [f"Found {len(sources)} EPG sources:"]
            for s in sources:
                name = s.get("name", "Unknown")
                sid = s.get("id", "?")
                # url may be present-but-None (4 of 8 real sources); `or ""` handles that
                url = (s.get("url") or "")[:50]
                # Dispatcharr payload uses epg_data_count for channel count; channel_count is absent
                epg_count = s.get("epg_data_count", s.get("channel_count"))
                count_str = f"{epg_count} channels, " if epg_count is not None else ""
                lines.append(f"  {name} (id={sid}) — {count_str}url: {url}...")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_epg_sources failed: %s", e)
            return f"Error listing EPG sources: {e}"

    @mcp.tool()
    async def refresh_epg(source_id: int) -> str:
        """Refresh an EPG source to fetch the latest program guide data.

        Args:
            source_id: The EPG source ID to refresh
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["epg_refresh_source"], path_args={"source_id": source_id}, timeout=300.0,
            )
            msg = result.get("message", "") if isinstance(result, dict) else ""
            return f"EPG source {source_id} refresh started. {msg}"
        except Exception as e:
            logger.error("[MCP] refresh_epg failed: %s", e)
            return f"Error refreshing EPG source {source_id}: {e}"

    @mcp.tool()
    async def refresh_all_epg(source_ids: list[int] | None = None) -> str:
        """Refresh multiple EPG sources at once. If no source_ids provided, refreshes all.

        Args:
            source_ids: Optional list of EPG source IDs to refresh. If omitted, refreshes all sources.
        """
        try:
            client = get_ecm_client()

            if source_ids is None:
                sources = await client.call_endpoint(ENDPOINTS["epg_list_sources"])
                if isinstance(sources, dict):
                    sources = sources.get("sources", sources.get("results", []))
                source_ids = [s.get("id") for s in sources if s.get("id")]

            if not source_ids:
                return "No EPG sources found to refresh."

            refreshed = 0
            errors = []
            for sid in source_ids:
                try:
                    await client.call_endpoint(
                        ENDPOINTS["epg_refresh_source"], path_args={"source_id": sid}, timeout=300.0,
                    )
                    refreshed += 1
                except Exception as e:
                    errors.append(f"source {sid}: {e}")

            lines = [f"Refreshed {refreshed}/{len(source_ids)} EPG sources."]
            if errors:
                lines.append(f"Errors ({len(errors)}):")
                for err in errors:
                    lines.append(f"  - {err}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] refresh_all_epg failed: %s", e)
            return f"Error refreshing EPG sources: {e}"

    @mcp.tool()
    async def match_channels_epg(
        channel_ids: list[int] | None = None,
        epg_source_ids: list[int] | None = None,
        source_order: list[int] | None = None,
    ) -> str:
        """Auto-match channels to EPG data based on channel names.

        Args:
            channel_ids: Optional list of channel IDs to match (default: all channels)
            epg_source_ids: Optional list of EPG source IDs to search (default: all sources)
            source_order: Optional list of EPG source IDs defining preferred match order
        """
        try:
            client = get_ecm_client()
            # EPGMatchRequest requires a body; all fields are optional (default []).
            # Omitting the body entirely → HTTP 422 "Field required, loc:['body']".
            body: dict = {
                "channel_ids": channel_ids or [],
                "epg_source_ids": epg_source_ids or [],
                "source_order": source_order or [],
            }
            result = await client.call_endpoint(ENDPOINTS["epg_match"], body=body, timeout=300.0)

            # Real backend response shape (v0.15.0):
            # {"exact":[...], "multiple":[...], "none":[...],
            #  "summary":{"total_channels":N, "exact_count":N, "multiple_count":N,
            #              "none_count":N, "match_time_ms":N}}
            summary = result.get("summary", {}) if isinstance(result, dict) else {}
            total = summary.get("total_channels", len(result.get("exact", [])) + len(result.get("multiple", [])) + len(result.get("none", [])))
            exact_count = summary.get("exact_count", len(result.get("exact", [])) if isinstance(result, dict) else 0)
            multiple_count = summary.get("multiple_count", len(result.get("multiple", [])) if isinstance(result, dict) else 0)
            none_count = summary.get("none_count", len(result.get("none", [])) if isinstance(result, dict) else 0)

            lines = [
                f"EPG auto-match complete: {total} channels total — "
                f"{exact_count} exact, {multiple_count} multiple candidates, {none_count} unmatched."
            ]

            # Surface candidate details for ambiguous channels so the operator
            # can see the options without dropping to the UI (bd-1icn8).
            # The backend returns result["multiple"] as a list of per-channel
            # entries, each with a "matches" list capped at 10 candidates.
            multiple_entries = result.get("multiple", []) if isinstance(result, dict) else []
            if multiple_entries:
                lines.append(f"\nChannels with multiple candidates ({len(multiple_entries)}):")
                for entry in multiple_entries:
                    ch_name = entry.get("channel_name", f"channel {entry.get('channel_id', '?')}")
                    candidates = entry.get("matches", [])
                    lines.append(f"  {ch_name}:")
                    if candidates:
                        for c in candidates:
                            tvg_id = c.get("tvg_id", "?")
                            epg_name = c.get("epg_name", "")
                            confidence = c.get("confidence")
                            conf_str = f" ({confidence:.0f}%)" if confidence is not None else ""
                            name_str = f" — {epg_name}" if epg_name else ""
                            lines.append(f"    • {tvg_id}{name_str}{conf_str}")
                    else:
                        lines.append("    (no candidate details available)")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] match_channels_epg failed: %s", e)
            return f"Error running EPG auto-match: {e}"

    @mcp.tool()
    async def link_channel_epg(
        channel_id: int,
        tvg_id: str | None = None,
        epg_data_id: int | None = None,
    ) -> str:
        """Link a channel to a chosen EPG candidate so its guide data attaches.

        Use this after ``match_channels_epg`` reports a channel with *multiple
        candidates*: pick one candidate's ``tvg_id`` (shown in that output) and
        link it here. This sets the channel's EPG data association (epg_data_id),
        which ``bulk_assign_epg`` does NOT do — and which ``set_logo_from_epg``
        needs to find the linked entry. Completes the
        match -> link -> set-logo workflow without dropping to the UI.

        Args:
            channel_id: The channel to link.
            tvg_id: The chosen candidate's tvg_id (resolved server-side to its
                EPG data row). Either tvg_id or epg_data_id is required.
            epg_data_id: The EPG data row id directly (preferred when known —
                e.g. a candidate's "epg_id"). Wins over tvg_id if both given.
        """
        if tvg_id is None and epg_data_id is None:
            return "Provide either tvg_id or epg_data_id to link."
        try:
            client = get_ecm_client()
            body: dict = {}
            if epg_data_id is not None:
                body["epg_data_id"] = epg_data_id
            if tvg_id is not None:
                body["tvg_id"] = tvg_id

            result = await client.call_endpoint(
                ENDPOINTS["epg_link_channel"],
                path_args={"channel_id": channel_id},
                body=body,
            )

            linked_id = result.get("epg_data_id") if isinstance(result, dict) else None
            ch_name = result.get("name") if isinstance(result, dict) else None
            label = f"'{ch_name}' (id={channel_id})" if ch_name else f"channel {channel_id}"
            return (
                f"Linked {label} to EPG data id={linked_id}. "
                "set_logo_from_epg can now resolve this channel's EPG entry."
            )
        except Exception as e:
            logger.error("[MCP] link_channel_epg failed: %s", e)
            return f"Error linking channel {channel_id} to EPG: {e}"

    @mcp.tool()
    async def create_epg_source(
        name: str,
        url: str | None = None,
        source_type: str = "xmltv",
        username: str | None = None,
        password: str | None = None,
    ) -> str:
        """Create a new EPG data source.

        Args:
            name: Display name for the EPG source
            url: URL of the XMLTV EPG feed (required for source_type="xmltv")
            source_type: "xmltv" (default, standard XMLTV format) or
                "schedules_direct" (Schedules Direct JSON API).
            username: Schedules Direct account username (source_type="schedules_direct").
            password: Schedules Direct account password (source_type="schedules_direct").
                After creating an SD source, add lineups with add_sd_lineup before
                refreshing — an SD source with no lineups pulls no data.
        """
        try:
            client = get_ecm_client()
            # source_type is required by Dispatcharr; omitting it → HTTP 400 (bd-1wq7z.9)
            body: dict = {"name": name, "source_type": source_type}
            if source_type == "schedules_direct":
                if not username or not password:
                    return "Schedules Direct sources require username and password."
                body["username"] = username
                body["password"] = password
            else:
                if not url:
                    return f"source_type '{source_type}' requires a url."
                body["url"] = url
            result = await client.call_endpoint(ENDPOINTS["epg_create_source"], body=body)
            sid = result.get("id", "?") if isinstance(result, dict) else "?"
            rname = result.get("name", name) if isinstance(result, dict) else name
            return f"EPG source created: {rname} (id={sid})"
        except Exception as e:
            logger.error("[MCP] create_epg_source failed: %s", e)
            return f"Error creating EPG source: {e}"

    @mcp.tool()
    async def update_epg_source(
        source_id: int,
        name: str | None = None,
        url: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> str:
        """Update an existing EPG source.

        Args:
            source_id: The EPG source ID to update
            name: New display name
            url: New XMLTV feed URL
            username: New Schedules Direct username
            password: New Schedules Direct password (omit to keep the existing one —
                the stored password is never returned).
        """
        try:
            client = get_ecm_client()
            payload = {}
            if name is not None:
                payload["name"] = name
            if url is not None:
                payload["url"] = url
            if username is not None:
                payload["username"] = username
            if password is not None:
                payload["password"] = password

            if not payload:
                return "No changes specified."

            result = await client.call_endpoint(
                ENDPOINTS["epg_update_source"], path_args={"source_id": source_id}, body=payload,
            )
            if isinstance(result, dict):
                rname = result.get("name", "?")
                rurl = (result.get("url") or "")[:60]
                return f"EPG source {source_id} updated: name='{rname}', url='{rurl}'"
            return f"EPG source {source_id} updated."
        except Exception as e:
            logger.error("[MCP] update_epg_source failed: %s", e)
            return f"Error updating EPG source {source_id}: {e}"

    @mcp.tool()
    async def list_sd_lineups(source_id: int) -> str:
        """List the active Schedules Direct lineups for an SD EPG source.

        Args:
            source_id: The Schedules Direct EPG source ID.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["epg_sd_lineups_list"], path_args={"source_id": source_id},
            )
            if not isinstance(result, dict):
                return f"Unexpected response listing SD lineups for source {source_id}."
            lineups = result.get("lineups", []) or []
            max_lineups = result.get("max_lineups", "?")
            remaining = result.get("changes_remaining", "?")
            if not lineups:
                return (
                    f"No SD lineups configured for source {source_id} "
                    f"(0/{max_lineups}; changes remaining: {remaining}). "
                    "Use search_sd_lineups then add_sd_lineup."
                )
            lines = [f"SD lineups for source {source_id} ({len(lineups)}/{max_lineups}, "
                     f"changes remaining: {remaining}):"]
            for lu in lineups:
                if isinstance(lu, dict):
                    lines.append(f"  {lu.get('lineup', lu.get('id', '?'))} — {lu.get('name', '')}")
                else:
                    lines.append(f"  {lu}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_sd_lineups failed: %s", e)
            return f"Error listing SD lineups for source {source_id}: {e}"

    @mcp.tool()
    async def search_sd_lineups(source_id: int, country: str, postalcode: str) -> str:
        """Search Schedules Direct headends/lineups by country + postal code.

        Args:
            source_id: The Schedules Direct EPG source ID.
            country: ISO-3166 alpha-3 country code (e.g. "USA", "CAN", "GBR").
            postalcode: Postal/ZIP code to search near.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["epg_sd_lineups_search"],
                path_args={"source_id": source_id},
                body={"country": country, "postalcode": postalcode},
            )
            lineups = result.get("lineups", []) if isinstance(result, dict) else []
            if not lineups:
                return f"No SD lineups found for {country}/{postalcode}."
            lines = [f"Found {len(lineups)} SD lineups for {country}/{postalcode}:"]
            for lu in lineups:
                lines.append(
                    f"  {lu.get('lineup', '?')} — {lu.get('name', '')} "
                    f"[{lu.get('transport', '')}] {lu.get('location', '')}"
                )
            lines.append("Add one with add_sd_lineup(source_id, lineup).")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] search_sd_lineups failed: %s", e)
            return f"Error searching SD lineups for source {source_id}: {e}"

    @mcp.tool()
    async def add_sd_lineup(source_id: int, lineup: str) -> str:
        """Add a Schedules Direct lineup to the account (rate-limited: ~6/24h).

        Args:
            source_id: The Schedules Direct EPG source ID.
            lineup: The lineup id from search_sd_lineups (e.g. "USA-NJ29486-X").
        """
        try:
            client = get_ecm_client()
            await client.call_endpoint(
                ENDPOINTS["epg_sd_lineup_add"],
                path_args={"source_id": source_id},
                body={"lineup": lineup},
            )
            return f"Added SD lineup '{lineup}' to source {source_id}."
        except Exception as e:
            logger.error("[MCP] add_sd_lineup failed: %s", e)
            return f"Error adding SD lineup '{lineup}' to source {source_id}: {e}"

    @mcp.tool()
    async def remove_sd_lineup(source_id: int, lineup: str) -> str:
        """Remove a Schedules Direct lineup from the account.

        Args:
            source_id: The Schedules Direct EPG source ID.
            lineup: The lineup id to remove (e.g. "USA-NJ29486-X").
        """
        try:
            client = get_ecm_client()
            await client.call_endpoint(
                ENDPOINTS["epg_sd_lineup_remove"],
                path_args={"source_id": source_id},
                body={"lineup": lineup},
            )
            return f"Removed SD lineup '{lineup}' from source {source_id}."
        except Exception as e:
            logger.error("[MCP] remove_sd_lineup failed: %s", e)
            return f"Error removing SD lineup '{lineup}' from source {source_id}: {e}"

    @mcp.tool()
    async def delete_epg_source(source_id: int) -> str:
        """Delete an EPG data source.

        Args:
            source_id: The EPG source ID to delete
        """
        try:
            client = get_ecm_client()
            await client.call_endpoint(ENDPOINTS["epg_delete_source"], path_args={"source_id": source_id})
            return f"EPG source {source_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_epg_source failed: %s", e)
            return f"Error deleting EPG source {source_id}: {e}"

    @mcp.tool()
    async def get_epg_grid(
        channel_id: int | None = None,
        limit: int = 20,
    ) -> str:
        """Get the EPG schedule grid — what's on TV now and upcoming.

        Args:
            channel_id: Optional channel ID to filter for a specific channel (filtered client-side)
            limit: Maximum number of programs to return (default 20)
        """
        try:
            client = get_ecm_client()
            # Backend GET /api/epg/grid only accepts optional `start`/`end`
            # datetime params — `channel_id`/`limit` are applied client-side
            # below (sending them as query params was silently ignored: drift
            # fixed in bd-vtghg Phase 2).
            result = await client.call_endpoint(ENDPOINTS["epg_grid"])

            # Backend returns the raw Dispatcharr grid, whose program items key
            # the channel by ``channel_uuid`` (not channel_id / channel_name).
            # Resolve names + the channel-id filter through the channel list.
            programs = result if isinstance(result, list) else result.get("data", result.get("programs", []))

            uuid_map = await _build_channel_uuid_map(client)

            if channel_id is not None:
                programs = [
                    p for p in programs
                    if (uuid_map.get(p.get("channel_uuid"), {}).get("id") == channel_id)
                    or channel_id in (p.get("channel_id"), p.get("channel"))
                ]

            if not programs:
                return "No EPG schedule data available."

            lines = [f"EPG Schedule ({min(len(programs), limit)} programs):"]
            for p in programs[:limit]:
                ch_info = uuid_map.get(p.get("channel_uuid"), {})
                channel = (
                    ch_info.get("name")
                    or p.get("channel_name")
                    or p.get("channel")
                    or p.get("channel_uuid")
                    or "Unknown"
                )
                title = p.get("title", "Unknown")
                start = p.get("start", p.get("start_time", "?"))
                end = p.get("stop", p.get("end", p.get("end_time", "?")))
                lines.append(f"  [{channel}] {title} ({start} - {end})")

            if len(programs) > limit:
                lines.append(f"  ... and {len(programs) - limit} more")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_epg_grid failed: %s", e)
            return f"Error getting EPG grid: {e}"

    @mcp.tool()
    async def list_dummy_epg_profiles() -> str:
        """List all dummy EPG profiles used to generate placeholder guide data."""
        try:
            client = get_ecm_client()
            profiles = await client.call_endpoint(ENDPOINTS["dummy_epg_list_profiles"])
            if isinstance(profiles, dict):
                profiles = profiles.get("profiles", profiles.get("results", []))

            if not profiles:
                return "No dummy EPG profiles configured."

            lines = [f"Dummy EPG Profiles ({len(profiles)}):"]
            for p in profiles:
                name = p.get("name", "Unnamed")
                pid = p.get("id", "?")
                enabled = "enabled" if p.get("enabled") else "disabled"
                groups = p.get("group_count", 0)
                lines.append(f"  {name} (id={pid}) — {enabled}, {groups} channel groups")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_dummy_epg_profiles failed: %s", e)
            return f"Error listing dummy EPG profiles: {e}"

    @mcp.tool()
    async def generate_dummy_epg() -> str:
        """Force regeneration of all dummy EPG XMLTV data from enabled profiles."""
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["dummy_epg_generate"], timeout=60.0)
            count = result.get("profiles_generated", 0) if isinstance(result, dict) else 0
            return f"Dummy EPG regenerated for {count} enabled profiles."
        except Exception as e:
            logger.error("[MCP] generate_dummy_epg failed: %s", e)
            return f"Error generating dummy EPG: {e}"
