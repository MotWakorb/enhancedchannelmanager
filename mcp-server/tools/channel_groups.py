"""Channel group management tools."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_channel_groups() -> str:
        """List all channel groups with their channel counts."""
        try:
            client = get_ecm_client()
            groups = await client.get("/api/channel-groups")

            if not groups:
                return "No channel groups found."

            lines = [f"Found {len(groups)} channel groups:"]
            for g in groups:
                name = g.get("name", "Unknown")
                gid = g.get("id", "?")
                channel_count = g.get("channel_count", 0)
                lines.append(f"  {name} (id={gid}) — {channel_count} channels")

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
            result = await client.post("/api/channel-groups", json_data={"name": name})
            return f"Channel group created: {name} (id={result.get('id', '?')})"
        except Exception as e:
            logger.error("[MCP] create_channel_group failed: %s", e)
            return f"Error creating channel group: {e}"

    @mcp.tool()
    async def get_orphaned_groups() -> str:
        """List channel groups that exist in Dispatcharr but have no channels assigned in ECM."""
        try:
            client = get_ecm_client()
            groups = await client.get("/api/channel-groups/orphaned")

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
                # Paginate through all channels in the group and delete them
                all_channel_ids = []
                page = 1
                while True:
                    result = await client.get(
                        "/api/channels", group_id=group_id, page=page, page_size=500,
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
                        await client.delete(f"/api/channels/{cid}")
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

            await client.delete(f"/api/channel-groups/{group_id}")

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
            groups = await client.get("/api/channel-groups/hidden")

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
        """List channel groups that were created by the auto-creation pipeline."""
        try:
            client = get_ecm_client()
            groups = await client.get("/api/channel-groups/auto-created")

            if not groups:
                return "No auto-created channel groups."

            lines = [f"Found {len(groups)} auto-created groups:"]
            for g in groups:
                name = g.get("name", "Unknown")
                gid = g.get("id", "?")
                channel_count = g.get("channel_count", 0)
                lines.append(f"  {name} (id={gid}) — {channel_count} channels")

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
            if group_ids:
                result = await client.delete(
                    "/api/channel-groups/orphaned",
                    json_data={"group_ids": group_ids},
                )
            else:
                result = await client.delete("/api/channel-groups/orphaned")

            if result is None:
                return "No orphaned groups were deleted."

            deleted = result.get("deleted", 0)
            groups = result.get("groups", [])

            if deleted == 0:
                return "No orphaned groups were deleted."

            lines = [f"Deleted {deleted} orphaned group(s):"]
            for g in groups:
                lines.append(f"  {g.get('name', 'Unknown')} (id={g.get('id', '?')})")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] delete_orphaned_groups failed: %s", e)
            return f"Error deleting orphaned groups: {e}"

    @mcp.tool()
    async def get_groups_with_streams() -> str:
        """List channel groups with their stream count information."""
        try:
            client = get_ecm_client()
            groups = await client.get("/api/channel-groups/with-streams")

            if not groups:
                return "No channel groups found."

            lines = [f"Found {len(groups)} groups with stream info:"]
            for g in groups:
                name = g.get("name", "Unknown")
                gid = g.get("id", "?")
                stream_count = g.get("stream_count", 0)
                lines.append(f"  {name} (id={gid}) — {stream_count} streams")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_groups_with_streams failed: %s", e)
            return f"Error listing groups with streams: {e}"
