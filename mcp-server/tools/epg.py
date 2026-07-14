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

        When several EPG sources offer an equally-good match for a channel,
        the match ranking automatically prefers the source with the higher
        configured priority (set by reordering sources in the EPG Manager).
        No action is needed here to honor that preference.

        Args:
            channel_ids: Optional list of channel IDs to match (default: all channels)
            epg_source_ids: Optional list of EPG source IDs to search (default: all sources)
            source_order: DEPRECATED and ignored — EPG source priority is now
                resolved server-side from each source's configured priority.
                Retained for backward compatibility; scheduled for removal in v0.19.0.
        """
        if source_order:
            logger.warning(
                "[MCP] match_channels_epg: source_order is deprecated and "
                "ignored; EPG source priority is resolved server-side."
            )
        try:
            client = get_ecm_client()
            # EPGMatchRequest requires a body; all fields are optional (default []).
            # Omitting the body entirely → HTTP 422 "Field required, loc:['body']".
            # source_order is intentionally NOT sent — priority is server-side now.
            body: dict = {
                "channel_ids": channel_ids or [],
                "epg_source_ids": epg_source_ids or [],
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
    async def audit_epg_duplicates() -> str:
        """Audit for channels silently sharing one EPG link (read-only).

        Lists every set of two or more channels linked to the SAME EPG row
        (same ``epg_data_id``). This is the fingerprint of the West-shares-East
        mis-link bug: a West feed pointed at its East counterpart's EPG row, so
        it shows the wrong schedule. Channels with no EPG link are excluded
        (they are unlinked, not mis-linked). This audit changes nothing — to
        fix a flagged channel, re-link it with ``link_channel_epg``.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["epg_audit_duplicates"])
            groups = result.get("shared_links", []) if isinstance(result, dict) else []
            summary = result.get("summary", {}) if isinstance(result, dict) else {}
            total = summary.get("total_channels", "?")

            if not groups:
                return (
                    f"No shared EPG links found across {total} channels — "
                    "every linked channel points at a distinct EPG row."
                )

            lines = [
                f"Found {len(groups)} set(s) of channels sharing one EPG link "
                f"({summary.get('affected_channels', '?')} channels affected, "
                f"{total} total):"
            ]
            for g in groups:
                epg_name = g.get("epg_name") or "(EPG row name unavailable)"
                tvg_id = g.get("tvg_id")
                tvg_str = f", tvg_id={tvg_id}" if tvg_id else ""
                lines.append(
                    f"\n  EPG row id={g.get('epg_data_id')} — {epg_name}{tvg_str} "
                    f"→ {g.get('count')} channels share it:"
                )
                for c in g.get("channels", []):
                    lines.append(
                        f"    • channel {c.get('channel_id')}: {c.get('channel_name')}"
                    )
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] audit_epg_duplicates failed: %s", e)
            return f"Error auditing EPG duplicates: {e}"

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
        is_active: bool | None = None,
        refresh_interval: int | None = None,
        priority: int | None = None,
        custom_properties: dict | None = None,
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
            is_active: Whether the source is active (backend default True)
            refresh_interval: Auto-refresh interval in hours
            priority: EPG source priority — lower values win ties during
                auto-match ranking (see match_channels_epg)
            custom_properties: Free-form dict of source-type-specific settings
                (e.g. Dummy EPG generation config — see docs/template_engine.md)
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
            if is_active is not None:
                body["is_active"] = is_active
            if refresh_interval is not None:
                body["refresh_interval"] = refresh_interval
            if priority is not None:
                body["priority"] = priority
            if custom_properties is not None:
                body["custom_properties"] = custom_properties
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
        is_active: bool | None = None,
        refresh_interval: int | None = None,
        priority: int | None = None,
        custom_properties: dict | None = None,
    ) -> str:
        """Update an existing EPG source.

        Args:
            source_id: The EPG source ID to update
            name: New display name
            url: New XMLTV feed URL
            username: New Schedules Direct username
            password: New Schedules Direct password (omit to keep the existing one —
                the stored password is never returned).
            is_active: Enable/disable the source
            refresh_interval: New auto-refresh interval in hours
            priority: New EPG source priority — lower values win ties during
                auto-match ranking (see match_channels_epg)
            custom_properties: Replacement free-form dict of source-type-specific
                settings (e.g. Dummy EPG generation config) — this REPLACES the
                existing dict, it does not merge into it.
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
            if is_active is not None:
                payload["is_active"] = is_active
            if refresh_interval is not None:
                payload["refresh_interval"] = refresh_interval
            if priority is not None:
                payload["priority"] = priority
            if custom_properties is not None:
                payload["custom_properties"] = custom_properties

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

    @mcp.tool()
    async def get_dummy_epg_profile(profile_id: int) -> str:
        """Get full configuration for a single Dummy EPG profile (bd-omxy5).

        Includes channel group assignments. See docs/template_engine.md for
        the pattern/template DSL these fields drive.

        Args:
            profile_id: The profile ID.
        """
        try:
            client = get_ecm_client()
            p = await client.call_endpoint(
                ENDPOINTS["dummy_epg_get_profile"], path_args={"profile_id": profile_id}
            )
            name = p.get("name", "?") if isinstance(p, dict) else "?"
            enabled = "enabled" if (isinstance(p, dict) and p.get("enabled")) else "disabled"
            groups = (p.get("channel_group_ids") or []) if isinstance(p, dict) else []
            lines = [
                f"Dummy EPG Profile '{name}' (id={profile_id}) — {enabled}",
                f"  name_source={p.get('name_source')}, stream_index={p.get('stream_index')}",
                f"  title_pattern={p.get('title_pattern')!r}",
                f"  time_pattern={p.get('time_pattern')!r}",
                f"  date_pattern={p.get('date_pattern')!r}",
                f"  title_template={p.get('title_template')!r}",
                f"  event_timezone={p.get('event_timezone')}, program_duration={p.get('program_duration')}min",
                f"  channel_group_ids={groups} ({len(groups)} group(s) assigned)",
            ]
            sub_pairs = p.get("substitution_pairs") or []
            if sub_pairs:
                lines.append(f"  substitution_pairs: {len(sub_pairs)} configured")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_dummy_epg_profile failed: %s", e)
            return f"Error getting dummy EPG profile {profile_id}: {e}"

    @mcp.tool()
    async def create_dummy_epg_profile(
        name: str,
        enabled: bool = True,
        name_source: str = "channel",
        stream_index: int = 1,
        title_pattern: str | None = None,
        time_pattern: str | None = None,
        date_pattern: str | None = None,
        substitution_pairs: list[dict] | None = None,
        title_template: str | None = None,
        description_template: str | None = None,
        upcoming_title_template: str | None = None,
        upcoming_description_template: str | None = None,
        ended_title_template: str | None = None,
        ended_description_template: str | None = None,
        fallback_title_template: str | None = None,
        fallback_description_template: str | None = None,
        event_timezone: str = "US/Eastern",
        output_timezone: str | None = None,
        program_duration: int = 180,
        categories: str | None = None,
        channel_logo_url_template: str | None = None,
        program_poster_url_template: str | None = None,
        tvg_id_template: str = "ecm-{channel_number}",
        include_date_tag: bool = False,
        include_live_tag: bool = False,
        include_new_tag: bool = False,
        pattern_builder_examples: str | None = None,
        pattern_variants: list[dict] | None = None,
        channel_group_ids: list[int] | None = None,
    ) -> str:
        """Create a new Dummy EPG profile (bd-omxy5).

        A profile generates placeholder XMLTV guide data for channels
        lacking real EPG — see docs/template_engine.md for the
        pattern/template DSL. Use preview_dummy_epg to test a configuration
        BEFORE creating the profile.

        Args:
            name: Profile name (must be unique).
            enabled: Whether the profile actively generates XMLTV.
            name_source: Sample-name source for pattern matching — "channel"
                or "stream".
            stream_index: Which of a channel's streams (1-based) to source
                the name from when name_source="stream".
            title_pattern: Regex extracting the raw program title.
            time_pattern: Regex extracting the event start time.
            date_pattern: Regex extracting the event date.
            substitution_pairs: List of {find, replace, is_regex, enabled}
                dicts applied to the extracted title before templating.
            title_template: Output title template (placeholders + pipes).
            description_template: Output description template.
            upcoming_title_template / upcoming_description_template:
                Templates for the "upcoming" (pre-event) placeholder program.
            ended_title_template / ended_description_template: Templates for
                the "ended" (post-event) placeholder program.
            fallback_title_template / fallback_description_template:
                Templates used when extraction fails entirely.
            event_timezone: Timezone the extracted event time is in.
            output_timezone: Timezone to render output times in (defaults to
                event_timezone when unset).
            program_duration: Fallback program duration in minutes.
            categories: Comma-separated XMLTV category tags.
            channel_logo_url_template: Template for the per-channel logo URL.
            program_poster_url_template: Template for the per-program poster URL.
            tvg_id_template: Template for the generated tvg-id.
            include_date_tag / include_live_tag / include_new_tag: XMLTV
                <date>/"Live"/"New" tag toggles.
            pattern_builder_examples: Free-text sample inputs for the UI's
                pattern builder (not consumed by generation).
            pattern_variants: Optional list of per-variant pattern/template
                override dicts.
            channel_group_ids: Channel group IDs this profile applies to.
        """
        try:
            client = get_ecm_client()
            body: dict = {
                "name": name,
                "enabled": enabled,
                "name_source": name_source,
                "stream_index": stream_index,
                "event_timezone": event_timezone,
                "program_duration": program_duration,
                "tvg_id_template": tvg_id_template,
                "include_date_tag": include_date_tag,
                "include_live_tag": include_live_tag,
                "include_new_tag": include_new_tag,
            }
            optional = {
                "title_pattern": title_pattern,
                "time_pattern": time_pattern,
                "date_pattern": date_pattern,
                "substitution_pairs": substitution_pairs,
                "title_template": title_template,
                "description_template": description_template,
                "upcoming_title_template": upcoming_title_template,
                "upcoming_description_template": upcoming_description_template,
                "ended_title_template": ended_title_template,
                "ended_description_template": ended_description_template,
                "fallback_title_template": fallback_title_template,
                "fallback_description_template": fallback_description_template,
                "output_timezone": output_timezone,
                "categories": categories,
                "channel_logo_url_template": channel_logo_url_template,
                "program_poster_url_template": program_poster_url_template,
                "pattern_builder_examples": pattern_builder_examples,
                "pattern_variants": pattern_variants,
                "channel_group_ids": channel_group_ids,
            }
            for key, value in optional.items():
                if value is not None:
                    body[key] = value

            result = await client.call_endpoint(ENDPOINTS["dummy_epg_create_profile"], body=body)
            pid = result.get("id", "?") if isinstance(result, dict) else "?"
            rname = result.get("name", name) if isinstance(result, dict) else name
            return f"Dummy EPG profile created: '{rname}' (id={pid})"
        except Exception as e:
            logger.error("[MCP] create_dummy_epg_profile failed: %s", e)
            return f"Error creating dummy EPG profile: {e}"

    @mcp.tool()
    async def update_dummy_epg_profile(
        profile_id: int,
        name: str | None = None,
        enabled: bool | None = None,
        name_source: str | None = None,
        stream_index: int | None = None,
        title_pattern: str | None = None,
        time_pattern: str | None = None,
        date_pattern: str | None = None,
        substitution_pairs: list[dict] | None = None,
        title_template: str | None = None,
        description_template: str | None = None,
        upcoming_title_template: str | None = None,
        upcoming_description_template: str | None = None,
        ended_title_template: str | None = None,
        ended_description_template: str | None = None,
        fallback_title_template: str | None = None,
        fallback_description_template: str | None = None,
        event_timezone: str | None = None,
        output_timezone: str | None = None,
        program_duration: int | None = None,
        categories: str | None = None,
        channel_logo_url_template: str | None = None,
        program_poster_url_template: str | None = None,
        tvg_id_template: str | None = None,
        include_date_tag: bool | None = None,
        include_live_tag: bool | None = None,
        include_new_tag: bool | None = None,
        pattern_builder_examples: str | None = None,
        pattern_variants: list[dict] | None = None,
        channel_group_ids: list[int] | None = None,
    ) -> str:
        """Update a Dummy EPG profile — only provided fields change (bd-omxy5).

        Args: same semantics as create_dummy_epg_profile; omit any field you
            don't want to change.

        Args:
            profile_id: The profile ID to update.
        """
        try:
            client = get_ecm_client()
            fields = {
                "name": name, "enabled": enabled, "name_source": name_source,
                "stream_index": stream_index, "title_pattern": title_pattern,
                "time_pattern": time_pattern, "date_pattern": date_pattern,
                "substitution_pairs": substitution_pairs, "title_template": title_template,
                "description_template": description_template,
                "upcoming_title_template": upcoming_title_template,
                "upcoming_description_template": upcoming_description_template,
                "ended_title_template": ended_title_template,
                "ended_description_template": ended_description_template,
                "fallback_title_template": fallback_title_template,
                "fallback_description_template": fallback_description_template,
                "event_timezone": event_timezone, "output_timezone": output_timezone,
                "program_duration": program_duration, "categories": categories,
                "channel_logo_url_template": channel_logo_url_template,
                "program_poster_url_template": program_poster_url_template,
                "tvg_id_template": tvg_id_template,
                "include_date_tag": include_date_tag, "include_live_tag": include_live_tag,
                "include_new_tag": include_new_tag,
                "pattern_builder_examples": pattern_builder_examples,
                "pattern_variants": pattern_variants, "channel_group_ids": channel_group_ids,
            }
            body = {k: v for k, v in fields.items() if v is not None}

            if not body:
                return "No changes specified."

            result = await client.call_endpoint(
                ENDPOINTS["dummy_epg_update_profile"], path_args={"profile_id": profile_id}, body=body,
            )
            rname = result.get("name", "?") if isinstance(result, dict) else "?"
            return f"Dummy EPG profile {profile_id} updated: name='{rname}'."
        except Exception as e:
            logger.error("[MCP] update_dummy_epg_profile failed: %s", e)
            return f"Error updating dummy EPG profile {profile_id}: {e}"

    @mcp.tool()
    async def delete_dummy_epg_profile(profile_id: int, confirm: bool = False) -> str:
        """Delete a Dummy EPG profile (bd-omxy5).

        CONFIRM GATING (bd-onazy convention): the first call (confirm=False,
        the default) fetches the profile and returns a preview naming it —
        WARNING if any channel groups are currently assigned — and deletes
        NOTHING. Re-invoke with confirm=True to actually delete. The backend
        also best-effort removes any Dispatcharr EPG source pointing at this
        profile's XMLTV URL.

        Args:
            profile_id: The profile ID to delete.
            confirm: Set True on the second call to perform the deletion.
        """
        try:
            client = get_ecm_client()
            if not confirm:
                profile = await client.call_endpoint(
                    ENDPOINTS["dummy_epg_get_profile"], path_args={"profile_id": profile_id}
                )
                name = profile.get("name", "?") if isinstance(profile, dict) else "?"
                groups = (profile.get("channel_group_ids") or []) if isinstance(profile, dict) else []
                warn = (
                    f" WARNING: {len(groups)} channel group(s) are currently assigned — "
                    f"those channels will lose this profile's XMLTV data."
                ) if groups else ""
                return (
                    f"Dummy EPG profile {profile_id} '{name}' will be deleted.{warn} "
                    f"Re-invoke with confirm=True to delete."
                )
            await client.call_endpoint(
                ENDPOINTS["dummy_epg_delete_profile"], path_args={"profile_id": profile_id}
            )
            return f"Dummy EPG profile {profile_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_dummy_epg_profile failed: %s", e)
            return f"Error deleting dummy EPG profile {profile_id}: {e}"

    @mcp.tool()
    async def preview_dummy_epg(
        sample_name: str,
        title_pattern: str | None = None,
        time_pattern: str | None = None,
        date_pattern: str | None = None,
        substitution_pairs: list[dict] | None = None,
        title_template: str | None = None,
        description_template: str | None = None,
        upcoming_title_template: str | None = None,
        upcoming_description_template: str | None = None,
        ended_title_template: str | None = None,
        ended_description_template: str | None = None,
        fallback_title_template: str | None = None,
        fallback_description_template: str | None = None,
        event_timezone: str = "US/Eastern",
        output_timezone: str | None = None,
        program_duration: int = 180,
        channel_logo_url_template: str | None = None,
        program_poster_url_template: str | None = None,
        pattern_variants: list[dict] | None = None,
        include_trace: bool = False,
    ) -> str:
        """Test a Dummy EPG pattern/template configuration against a sample name (bd-omxy5).

        The natural test companion to create/update_dummy_epg_profile — run
        this BEFORE saving a profile to see what title/description it would
        produce for a real channel/stream name. Does not touch any saved
        profile or persist anything.

        Args:
            sample_name: A sample channel/stream name to run the pipeline on.
            title_pattern / time_pattern / date_pattern: Extraction regexes.
            substitution_pairs: List of {find, replace, is_regex, enabled}
                dicts applied before extraction.
            title_template / description_template: Output templates.
            upcoming_title_template / upcoming_description_template:
                "Upcoming" placeholder-program templates.
            ended_title_template / ended_description_template: "Ended"
                placeholder-program templates.
            fallback_title_template / fallback_description_template:
                Fallback templates used when extraction fails.
            event_timezone: Timezone the extracted event time is in.
            output_timezone: Timezone to render output times in.
            program_duration: Fallback program duration in minutes.
            channel_logo_url_template / program_poster_url_template: URL
                templates.
            pattern_variants: Optional list of per-variant override dicts.
            include_trace: If true, the backend computes a step-by-step
                trace of placeholder resolution and pipe transforms (the
                summary here just notes it was computed — read the raw MCP
                response for the full trace).
        """
        try:
            client = get_ecm_client()
            body: dict = {
                "sample_name": sample_name,
                "event_timezone": event_timezone,
                "program_duration": program_duration,
                "include_trace": include_trace,
            }
            optional = {
                "title_pattern": title_pattern,
                "time_pattern": time_pattern,
                "date_pattern": date_pattern,
                "substitution_pairs": substitution_pairs,
                "title_template": title_template,
                "description_template": description_template,
                "upcoming_title_template": upcoming_title_template,
                "upcoming_description_template": upcoming_description_template,
                "ended_title_template": ended_title_template,
                "ended_description_template": ended_description_template,
                "fallback_title_template": fallback_title_template,
                "fallback_description_template": fallback_description_template,
                "output_timezone": output_timezone,
                "channel_logo_url_template": channel_logo_url_template,
                "program_poster_url_template": program_poster_url_template,
                "pattern_variants": pattern_variants,
            }
            for key, value in optional.items():
                if value is not None:
                    body[key] = value

            result = await client.call_endpoint(ENDPOINTS["dummy_epg_preview"], body=body)
            if not isinstance(result, dict):
                return str(result)

            matched = result.get("matched", False)
            rendered = result.get("rendered", {}) or {}
            lines = [f"Preview for '{sample_name}': {'MATCHED' if matched else 'NOT MATCHED'}"]
            if result.get("matched_variant"):
                lines.append(f"  Matched variant: {result['matched_variant']}")
            subst = result.get("substituted_name")
            if subst and subst != sample_name:
                lines.append(f"  Substituted name: {subst}")
            if rendered.get("title"):
                lines.append(f"  Title: {rendered['title']}")
            if rendered.get("description"):
                lines.append(f"  Description: {rendered['description']}")
            if not matched and rendered.get("fallback_title"):
                lines.append(f"  Fallback title: {rendered['fallback_title']}")
            if not matched and rendered.get("fallback_description"):
                lines.append(f"  Fallback description: {rendered['fallback_description']}")
            if include_trace and result.get("traces"):
                lines.append(f"  Trace: {len(result['traces'])} field(s) traced.")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] preview_dummy_epg failed: %s", e)
            return f"Error previewing dummy EPG: {e}"
