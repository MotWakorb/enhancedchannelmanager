"""EPG (Electronic Program Guide) tools."""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


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
                url = s.get("url", "")[:50]
                channel_count = s.get("channel_count", 0)
                lines.append(f"  {name} (id={sid}) — {channel_count} channels, url: {url}...")

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
    async def match_channels_epg() -> str:
        """Auto-match channels to EPG data based on channel names."""
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["epg_match"], timeout=300.0)

            matched = result.get("matched", 0)
            unmatched = result.get("unmatched", 0)
            return f"EPG auto-match complete: {matched} channels matched, {unmatched} unmatched."
        except Exception as e:
            logger.error("[MCP] match_channels_epg failed: %s", e)
            return f"Error running EPG auto-match: {e}"

    @mcp.tool()
    async def create_epg_source(name: str, url: str) -> str:
        """Create a new EPG data source.

        Args:
            name: Display name for the EPG source
            url: URL of the XMLTV EPG feed
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["epg_create_source"], body={"name": name, "url": url})
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
    ) -> str:
        """Update an existing EPG source.

        Args:
            source_id: The EPG source ID to update
            name: New display name
            url: New XMLTV feed URL
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

            programs = result if isinstance(result, list) else result.get("programs", [])

            if channel_id is not None:
                programs = [
                    p for p in programs
                    if channel_id in (p.get("channel_id"), p.get("channel"))
                ]

            if not programs:
                return "No EPG schedule data available."

            lines = [f"EPG Schedule ({min(len(programs), limit)} programs):"]
            for p in programs[:limit]:
                channel = p.get("channel_name", p.get("channel", "Unknown"))
                title = p.get("title", "Unknown")
                start = p.get("start", p.get("start_time", "?"))
                end = p.get("end", p.get("end_time", "?"))
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
