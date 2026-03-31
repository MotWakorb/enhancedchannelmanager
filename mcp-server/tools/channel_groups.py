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
    async def delete_channel_group(group_id: int) -> str:
        """Delete a channel group.

        Args:
            group_id: The channel group ID to delete
        """
        try:
            client = get_ecm_client()
            await client.delete(f"/api/channel-groups/{group_id}")
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
