"""Channel group management tools."""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_channel_groups(
        compact: bool = False,
        page: int = 1,
        page_size: int = 100,
    ) -> str:
        """List all channel groups with their channel counts.

        The live system can hold ~1000+ groups, so the default human-readable
        call is PAGINATED to stay within the MCP token limit (bd-0hjrk.4).

        Args:
            compact: If True, output pipe-delimited format optimized for agent
                consumption — ``id|name|channel_count`` with a header line.
                Ignores pagination and shows ALL groups (mirrors list_channels).
            page: 1-based page number for the human-readable format (default 1).
            page_size: Rows per page for the human-readable format (default 100).
        """
        try:
            client = get_ecm_client()
            groups = await client.call_endpoint(ENDPOINTS["groups_list"])
            # Backend groups_list returns a flat list with no server-side
            # pagination, so we slice client-side.
            groups = groups or []

            if not groups:
                return "No channel groups found."

            if compact:
                # Pipe-delimited, all rows, no pagination — same idiom as
                # list_channels' compact mode.
                lines = ["id|name|channel_count"]
                for g in groups:
                    gid = g.get("id", "?")
                    name = g.get("name", "Unknown")
                    channel_count = g.get("channel_count", 0)
                    lines.append(f"{gid}|{name}|{channel_count}")
                return "\n".join(lines)

            total = len(groups)
            page = max(page, 1)
            page_size = max(page_size, 1)
            start = (page - 1) * page_size
            end = start + page_size
            shown = groups[start:end]
            total_pages = (total + page_size - 1) // page_size

            if not shown:
                return (
                    f"No channel groups on page {page} "
                    f"(total {total} groups across {total_pages} pages)."
                )

            lines = [
                f"Found {total} channel groups "
                f"(page {page}/{total_pages}, showing {len(shown)}):"
            ]
            for g in shown:
                name = g.get("name", "Unknown")
                gid = g.get("id", "?")
                channel_count = g.get("channel_count", 0)
                lines.append(f"  {name} (id={gid}) — {channel_count} channels")

            if page < total_pages:
                lines.append(
                    f"  ... {total - end} more — request page {page + 1} "
                    f"(or use compact=True for all groups in one call)"
                )

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_channel_groups failed: %s", e)
            return f"Error listing channel groups: {e}"

    @mcp.tool()
    async def create_channel_group(name: str) -> str:
        """Create a new channel group.

        Args:
            name: Name for the new channel group
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["groups_create"], body={"name": name})
            # Report the resulting object, not the request — the backend dedupes
            # by name (returns the existing group if one already had this name).
            gid = result.get("id", "?") if isinstance(result, dict) else "?"
            rname = result.get("name", name) if isinstance(result, dict) else name
            return f"Channel group ready: {rname} (id={gid})"
        except Exception as e:
            logger.error("[MCP] create_channel_group failed: %s", e)
            return f"Error creating channel group: {e}"

    @mcp.tool()
    async def get_orphaned_groups() -> str:
        """List channel groups that exist in Dispatcharr but have no channels assigned in ECM."""
        try:
            client = get_ecm_client()
            resp = await client.call_endpoint(ENDPOINTS["groups_orphaned"])
            # Backend returns {"orphaned_groups": [...], "total_groups": N, "groups_with_content": N}
            # unwrap defensively (same class as bd-pvw35 / GH #222).
            groups = resp.get("orphaned_groups", []) if isinstance(resp, dict) else (resp or [])

            if not groups:
                return "No orphaned channel groups found."

            lines = [f"Found {len(groups)} orphaned groups:"]
            for g in groups:
                lines.append(f"  {g.get('name', 'Unknown')} (id={g.get('id', '?')})")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_orphaned_groups failed: %s", e)
            return f"Error listing orphaned groups: {e}"

    @mcp.tool()
    async def delete_channel_group(
        group_id: int,
        delete_channels: bool = False,
    ) -> str:
        """Delete a channel group, optionally deleting all its channels first.

        Args:
            group_id: The channel group ID to delete
            delete_channels: If True, delete all channels in the group before deleting the group
        """
        try:
            client = get_ecm_client()
            deleted_count = 0

            if delete_channels:
                # Paginate through all channels in the group and delete them.
                # Backend GET /api/channels filters by ``channel_group`` (NOT a
                # bare ``group_id`` — that's the GH #221 drift class).
                all_channel_ids = []
                page = 1
                while True:
                    result = await client.call_endpoint(
                        ENDPOINTS["channels_list"],
                        query={"channel_group": group_id, "page": page, "page_size": 500},
                    )
                    if isinstance(result, dict):
                        channels = result.get("results", result.get("channels", []))
                    else:
                        channels = result
                    if not channels:
                        break
                    all_channel_ids.extend(c["id"] for c in channels)
                    if isinstance(result, dict) and not result.get("next"):
                        break
                    page += 1

                # Delete channels in batches
                for cid in all_channel_ids:
                    try:
                        await client.call_endpoint(ENDPOINTS["channels_delete"], path_args={"channel_id": cid})
                        deleted_count += 1
                    except Exception as channel_delete_err:
                        # Best-effort: continue with the remaining channels and let
                        # the operator see the final deleted_count. Individual
                        # channel-delete failures are logged but don't abort the group.
                        logger.warning(
                            "[MCP] delete_channel_group: failed to delete channel %s: %s",
                            cid,
                            channel_delete_err,
                        )

            delete_response = await client.call_endpoint(ENDPOINTS["groups_delete"], path_args={"group_id": group_id})

            # Inspect the backend response status — the backend returns
            # {"status": "hidden"} when M3U sync settings prevent true deletion
            # and {"status": "deleted"} (or None/204) when truly gone.
            backend_status = (
                delete_response.get("status") if isinstance(delete_response, dict) else None
            )

            if backend_status == "hidden":
                return (
                    f"Channel group {group_id} hidden (has M3U sync settings; not deleted). "
                    f"Remove M3U sync settings first to fully delete this group."
                )

            # Read-back: confirm the group is actually gone.
            try:
                remaining = await client.call_endpoint(ENDPOINTS["groups_list"])
                still_present = any(
                    isinstance(g, dict) and g.get("id") == group_id for g in (remaining or [])
                )
            except Exception:
                still_present = None

            if still_present is True:
                return (
                    f"WARNING: requested deletion of channel group {group_id} but it "
                    f"still appears in the channel-group list (delete may not have applied)."
                )
            if delete_channels:
                return f"Channel group {group_id} deleted with {deleted_count} channels."
            return f"Channel group {group_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_channel_group failed: %s", e)
            return f"Error deleting channel group {group_id}: {e}"

    @mcp.tool()
    async def get_hidden_groups() -> str:
        """List channel groups that are hidden from the UI."""
        try:
            client = get_ecm_client()
            groups = await client.call_endpoint(ENDPOINTS["groups_hidden"])

            if not groups:
                return "No hidden channel groups."

            lines = [f"Found {len(groups)} hidden groups:"]
            for g in groups:
                lines.append(f"  {g.get('name', 'Unknown')} (id={g.get('id', '?')})")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_hidden_groups failed: %s", e)
            return f"Error listing hidden groups: {e}"

    @mcp.tool()
    async def get_auto_created_groups() -> str:
        """List channel groups that contain auto-created channels.

        Each group shows two distinct counts:
          - **channels**: total group membership (every channel in the group,
            regardless of how it was created).
          - **auto-created**: the subset of that membership whose channels were
            produced by the auto-creation pipeline (auto_created=True).

        A group can show e.g. "170 channels (12 auto-created)" — most of the
        group was created manually or by M3U sync; only 12 came from a rule.
        """
        try:
            client = get_ecm_client()
            resp = await client.call_endpoint(ENDPOINTS["groups_auto_created"])
            # Backend returns {"groups": [...], "total_auto_created_channels": N}
            # unwrap defensively (same class as bd-pvw35 / GH #222).
            groups = resp.get("groups", []) if isinstance(resp, dict) else (resp or [])

            if not groups:
                return "No auto-created channel groups."

            lines = [f"Found {len(groups)} groups with auto-created channels:"]
            for g in groups:
                name = g.get("name", "Unknown")
                gid = g.get("id", "?")
                # channel_count = total membership; auto_created_count = subset.
                # bd-0hjrk.3: the tool used to read a non-existent
                # "channel_count" off the old payload and always printed 0;
                # the backend now supplies both, so render both explicitly.
                channel_count = g.get("channel_count", 0)
                auto_created_count = g.get("auto_created_count", 0)
                lines.append(
                    f"  {name} (id={gid}) — {channel_count} channels "
                    f"({auto_created_count} auto-created)"
                )

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_auto_created_groups failed: %s", e)
            return f"Error listing auto-created groups: {e}"

    @mcp.tool()
    async def delete_orphaned_groups(group_ids: list[int] | None = None) -> str:
        """Delete orphaned channel groups (groups with no channels assigned).

        Args:
            group_ids: Optional list of specific orphaned group IDs to delete. If None, deletes all orphaned groups.
        """
        try:
            client = get_ecm_client()
            body = {"group_ids": group_ids} if group_ids else None
            result = await client.call_endpoint(ENDPOINTS["groups_delete_orphaned"], body=body)

            if result is None:
                return "No orphaned groups were deleted."

            # Backend returns {"status":..., "message":..., "deleted_groups": [...], "failed_groups": [...]}
            # (bd-1wq7z.5 — old code read non-existent "deleted"/"groups" keys)
            deleted_groups = result.get("deleted_groups", [])
            failed_groups = result.get("failed_groups", [])

            if not deleted_groups:
                return "No orphaned groups were deleted."

            lines = [f"Deleted {len(deleted_groups)} orphaned group(s):"]
            for g in deleted_groups:
                lines.append(f"  {g.get('name', 'Unknown')} (id={g.get('id', '?')})")

            if failed_groups:
                lines.append(f"Failed to delete {len(failed_groups)} group(s):")
                for g in failed_groups:
                    err = g.get("error", "unknown error")
                    lines.append(f"  {g.get('name', 'Unknown')} (id={g.get('id', '?')}) — {err}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] delete_orphaned_groups failed: %s", e)
            return f"Error deleting orphaned groups: {e}"

    @mcp.tool()
    async def get_groups_with_streams() -> str:
        """List channel groups with their stream count information."""
        try:
            client = get_ecm_client()
            resp = await client.call_endpoint(ENDPOINTS["groups_with_streams"])
            # Backend returns {"groups": [...], "total_groups": N}
            # unwrap defensively (same class as bd-pvw35 / GH #222).
            groups = resp.get("groups", []) if isinstance(resp, dict) else (resp or [])

            if not groups:
                return "No channel groups found."

            # The /api/channel-groups/with-streams endpoint returns {id, name}
            # only — no per-group stream count is available in this payload.
            lines = [f"Found {len(groups)} groups that have channels with streams:"]
            for g in groups:
                name = g.get("name", "Unknown")
                gid = g.get("id", "?")
                lines.append(f"  {name} (id={gid})")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_groups_with_streams failed: %s", e)
            return f"Error listing groups with streams: {e}"
