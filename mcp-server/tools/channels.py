"""Channel management tools."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_channels(
        group_id: int | None = None,
        search: str | None = None,
    ) -> str:
        """List all channels, optionally filtered by group or search term.

        Args:
            group_id: Filter by channel group ID
            search: Search channels by name
        """
        try:
            client = get_ecm_client()
            result = await client.get("/api/channels", group_id=group_id, search=search, page_size=50)

            # Handle paginated response (Dispatcharr API returns {count, results})
            if isinstance(result, dict):
                channels = result.get("results", result.get("channels", []))
                total = result.get("count", len(channels))
            else:
                channels = result
                total = len(channels)

            if not channels:
                return "No channels found."

            lines = [f"Found {total} channels (showing first {len(channels)}):"]
            for c in channels:
                num = c.get("channel_number", "?")
                name = c.get("name", "Unknown")
                cid = c.get("id", "?")
                stream_count = len(c.get("streams", []))
                lines.append(f"  #{num}: {name} (id={cid}) — {stream_count} streams")

            if total > len(channels):
                lines.append(f"  ... and {total - len(channels)} more")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_channels failed: %s", e)
            return f"Error listing channels: {e}"

    @mcp.tool()
    async def get_channel(channel_id: int) -> str:
        """Get detailed information about a specific channel.

        Args:
            channel_id: The channel ID to look up
        """
        try:
            client = get_ecm_client()
            c = await client.get(f"/api/channels/{channel_id}")

            stream_ids = c.get("streams", [])
            lines = [
                f"Channel: {c.get('name', 'Unknown')}",
                f"  ID: {c.get('id')}",
                f"  Number: {c.get('channel_number', 'N/A')}",
                f"  Group ID: {c.get('channel_group_id', 'None')}",
                f"  EPG TVG ID: {c.get('tvg_id', 'None')}",
                f"  Logo: {'Yes' if c.get('logo_id') else 'No'}",
                f"  Streams: {len(stream_ids)} (IDs: {stream_ids[:10]}{'...' if len(stream_ids) > 10 else ''})",
                f"  Auto-created: {c.get('auto_created', False)}",
            ]

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_channel failed: %s", e)
            return f"Error getting channel {channel_id}: {e}"

    @mcp.tool()
    async def create_channel(
        name: str,
        channel_number: int | None = None,
        group_id: int | None = None,
    ) -> str:
        """Create a new channel.

        Args:
            name: Channel name
            channel_number: Optional channel number
            group_id: Optional channel group ID to assign the channel to
        """
        try:
            client = get_ecm_client()
            payload = {"name": name}
            if channel_number is not None:
                payload["channel_number"] = channel_number
            if group_id is not None:
                payload["group_id"] = group_id

            result = await client.post("/api/channels", json_data=payload)
            cid = result.get("id", "?")
            return f"Channel created: #{result.get('channel_number', '?')}: {name} (id={cid})"
        except Exception as e:
            logger.error("[MCP] create_channel failed: %s", e)
            return f"Error creating channel: {e}"

    @mcp.tool()
    async def update_channel(
        channel_id: int,
        name: str | None = None,
        channel_number: int | None = None,
        group_id: int | None = None,
    ) -> str:
        """Update an existing channel.

        Args:
            channel_id: The channel ID to update
            name: New channel name
            channel_number: New channel number
            group_id: New channel group ID
        """
        try:
            client = get_ecm_client()
            payload = {}
            if name is not None:
                payload["name"] = name
            if channel_number is not None:
                payload["channel_number"] = channel_number
            if group_id is not None:
                payload["group_id"] = group_id

            if not payload:
                return "No changes specified."

            result = await client.patch(f"/api/channels/{channel_id}", json_data=payload)
            return f"Channel {channel_id} updated: {result.get('name', 'OK')}"
        except Exception as e:
            logger.error("[MCP] update_channel failed: %s", e)
            return f"Error updating channel {channel_id}: {e}"

    @mcp.tool()
    async def delete_channel(channel_id: int) -> str:
        """Delete a channel.

        Args:
            channel_id: The channel ID to delete
        """
        try:
            client = get_ecm_client()
            await client.delete(f"/api/channels/{channel_id}")
            return f"Channel {channel_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_channel failed: %s", e)
            return f"Error deleting channel {channel_id}: {e}"

    @mcp.tool()
    async def add_stream_to_channel(channel_id: int, stream_id: int) -> str:
        """Add a stream to a channel.

        Args:
            channel_id: The channel to add the stream to
            stream_id: The stream ID to add
        """
        try:
            client = get_ecm_client()
            result = await client.post(
                f"/api/channels/{channel_id}/add-stream",
                json_data={"stream_id": stream_id},
            )
            return f"Stream {stream_id} added to channel {channel_id}."
        except Exception as e:
            logger.error("[MCP] add_stream_to_channel failed: %s", e)
            return f"Error adding stream {stream_id} to channel {channel_id}: {e}"

    @mcp.tool()
    async def remove_stream_from_channel(channel_id: int, stream_id: int) -> str:
        """Remove a stream from a channel.

        Args:
            channel_id: The channel to remove the stream from
            stream_id: The stream ID to remove
        """
        try:
            client = get_ecm_client()
            result = await client.post(
                f"/api/channels/{channel_id}/remove-stream",
                json_data={"stream_id": stream_id},
            )
            return f"Stream {stream_id} removed from channel {channel_id}."
        except Exception as e:
            logger.error("[MCP] remove_stream_from_channel failed: %s", e)
            return f"Error removing stream {stream_id} from channel {channel_id}: {e}"

    @mcp.tool()
    async def reorder_streams(channel_id: int, stream_ids: list[int]) -> str:
        """Reorder streams within a channel. The order of stream_ids defines the new priority.

        Args:
            channel_id: The channel whose streams to reorder
            stream_ids: Ordered list of stream IDs (first = highest priority)
        """
        try:
            client = get_ecm_client()
            result = await client.post(
                f"/api/channels/{channel_id}/reorder-streams",
                json_data={"stream_ids": stream_ids},
            )
            return f"Streams reordered for channel {channel_id}. New order: {stream_ids}"
        except Exception as e:
            logger.error("[MCP] reorder_streams failed: %s", e)
            return f"Error reordering streams for channel {channel_id}: {e}"

    @mcp.tool()
    async def assign_channel_numbers(
        channel_ids: list[int],
        starting_number: int | None = None,
    ) -> str:
        """Bulk-assign sequential channel numbers to a list of channels.

        Args:
            channel_ids: List of channel IDs to number
            starting_number: Starting number (auto-assigned if omitted)
        """
        try:
            client = get_ecm_client()
            payload = {"channel_ids": channel_ids}
            if starting_number is not None:
                payload["starting_number"] = starting_number
            result = await client.post("/api/channels/assign-numbers", json_data=payload)
            return f"Assigned numbers to {len(channel_ids)} channels starting from {starting_number or 'auto'}."
        except Exception as e:
            logger.error("[MCP] assign_channel_numbers failed: %s", e)
            return f"Error assigning channel numbers: {e}"

    @mcp.tool()
    async def merge_channels(
        target_channel_id: int,
        source_channel_ids: list[int],
    ) -> str:
        """Merge multiple channels into one, combining all their streams.

        Args:
            target_channel_id: The channel to merge INTO (keeps this channel)
            source_channel_ids: Channels to merge FROM (these get deleted after merge)
        """
        try:
            client = get_ecm_client()
            result = await client.post("/api/channels/merge", json_data={
                "target_channel_id": target_channel_id,
                "source_channel_ids": source_channel_ids,
            })
            merged = len(source_channel_ids)
            return f"Merged {merged} channels into channel {target_channel_id}."
        except Exception as e:
            logger.error("[MCP] merge_channels failed: %s", e)
            return f"Error merging channels: {e}"

    @mcp.tool()
    async def clear_auto_created(group_ids: list[int] | None = None) -> str:
        """Clear channels that were created by the auto-creation pipeline.

        Args:
            group_ids: Optional list of group IDs to limit clearing to specific groups.
                       If omitted, clears all auto-created channels.
        """
        try:
            client = get_ecm_client()
            payload = {}
            if group_ids is not None:
                payload["group_ids"] = group_ids
            result = await client.post("/api/channels/clear-auto-created", json_data=payload)
            deleted = result.get("deleted", result.get("count", 0))
            scope = f"in {len(group_ids)} groups" if group_ids else "across all groups"
            return f"Cleared {deleted} auto-created channels {scope}."
        except Exception as e:
            logger.error("[MCP] clear_auto_created failed: %s", e)
            return f"Error clearing auto-created channels: {e}"
