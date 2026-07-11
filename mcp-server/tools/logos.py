"""Logo management tools (enhancedchannelmanager-twadj).

Only ``set_logo_from_epg`` (tools/channels.py) existed before this module —
there was no general logo CRUD via MCP. Covers list/create/update/delete
against ``/api/channels/logos``. URL-based create only; multipart upload
(``POST /api/channels/logos/upload``) is intentionally NOT exposed here —
deferred, no MCP transport for binary file uploads in this server.
"""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_logos(
        page: int = 1,
        page_size: int = 100,
        search: str | None = None,
    ) -> str:
        """List logos with pagination and search.

        Args:
            page: Page number (default 1)
            page_size: Results per page (default 100)
            search: Optional name/url substring filter
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["channels_list_logos"],
                query={"page": page, "page_size": page_size, "search": search},
            )
            logos = result.get("results", []) if isinstance(result, dict) else (result or [])
            total = result.get("count", len(logos)) if isinstance(result, dict) else len(logos)

            if not logos:
                return "No logos found."

            lines = [f"Found {total} logos (showing {len(logos)}):"]
            for logo in logos:
                lid = logo.get("id", "?")
                name = logo.get("name", "?")
                used = logo.get("channel_count", 0)
                used_str = f"used by {used} channel(s)" if used else "unused"
                lines.append(f"  id={lid}: {name} — {used_str}")

            has_next = bool(result.get("next")) if isinstance(result, dict) else False
            if has_next:
                lines.append(f"  ... more results on next page (page={page + 1})")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_logos failed: %s", e)
            return f"Error listing logos: {e}"

    @mcp.tool()
    async def create_logo(name: str, url: str) -> str:
        """Create a logo from a URL.

        Multipart file upload is not supported via MCP — the logo must
        already be reachable at a URL. If a logo with this URL already
        exists, the backend returns the existing logo instead of erroring
        (dedup on URL).

        Args:
            name: Display name for the logo
            url: URL the logo image is hosted at
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["channels_create_logo"], body={"name": name, "url": url},
            )
            lid = result.get("id", "?") if isinstance(result, dict) else "?"
            rname = result.get("name", name) if isinstance(result, dict) else name
            return f"Logo created: '{rname}' (id={lid})"
        except Exception as e:
            logger.error("[MCP] create_logo failed: %s", e)
            return f"Error creating logo: {e}"

    @mcp.tool()
    async def update_logo(
        logo_id: int,
        name: str | None = None,
        url: str | None = None,
    ) -> str:
        """Update a logo's name and/or URL.

        Args:
            logo_id: The logo ID to update
            name: New display name
            url: New URL the logo image is hosted at
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
                ENDPOINTS["channels_update_logo"], path_args={"logo_id": logo_id}, body=payload,
            )
            rname = result.get("name", "?") if isinstance(result, dict) else "?"
            return f"Logo {logo_id} updated: name='{rname}'."
        except Exception as e:
            logger.error("[MCP] update_logo failed: %s", e)
            return f"Error updating logo {logo_id}: {e}"

    @mcp.tool()
    async def delete_logo(logo_id: int, confirm: bool = False) -> str:
        """Delete a logo.

        CONFIRM GATING (bd-onazy convention): this is a two-call operation.
        The first call (``confirm=False``, the default) fetches the logo and
        returns a preview naming it — WARNING if it's currently in use by any
        channels — and deletes NOTHING. Re-invoke with ``confirm=True`` to
        actually delete.

        Args:
            logo_id: The logo ID to delete
            confirm: Set True on the second call to perform the deletion.
        """
        try:
            client = get_ecm_client()
            if not confirm:
                logo = await client.call_endpoint(
                    ENDPOINTS["channels_get_logo"], path_args={"logo_id": logo_id}
                )
                name = logo.get("name", "?") if isinstance(logo, dict) else "?"
                used = logo.get("channel_count", 0) if isinstance(logo, dict) else 0
                warn = (
                    f" WARNING: currently in use by {used} channel(s) — those "
                    f"channels will lose this logo."
                ) if used else ""
                return (
                    f"Logo {logo_id} '{name}' will be deleted.{warn} "
                    f"Re-invoke with confirm=True to delete."
                )
            await client.call_endpoint(ENDPOINTS["channels_delete_logo"], path_args={"logo_id": logo_id})
            return f"Logo {logo_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_logo failed: %s", e)
            return f"Error deleting logo {logo_id}: {e}"
