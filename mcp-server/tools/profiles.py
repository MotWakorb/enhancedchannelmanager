"""Channel and stream profile tools."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_channel_profiles() -> str:
        """List all channel profiles (configuration presets for channels)."""
        try:
            client = get_ecm_client()
            profiles = await client.get("/api/channel-profiles")

            if not profiles:
                return "No channel profiles configured."

            lines = [f"Found {len(profiles)} channel profiles:"]
            for p in profiles:
                name = p.get("name", "Unknown")
                pid = p.get("id", "?")
                channel_count = len(p.get("channels", []))
                lines.append(f"  {name} (id={pid}) — {channel_count} channels assigned")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_channel_profiles failed: %s", e)
            return f"Error listing channel profiles: {e}"

    @mcp.tool()
    async def list_stream_profiles() -> str:
        """List all stream profiles (FFmpeg/transcoding presets for streams)."""
        try:
            client = get_ecm_client()
            profiles = await client.get("/api/stream-profiles")

            if not profiles:
                return "No stream profiles configured."

            lines = [f"Found {len(profiles)} stream profiles:"]
            for p in profiles:
                name = p.get("name", "Unknown")
                pid = p.get("id", "?")
                active = "active" if p.get("is_active") else "inactive"
                locked = " (locked)" if p.get("locked") else ""
                lines.append(f"  {name} (id={pid}) — {active}{locked}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_stream_profiles failed: %s", e)
            return f"Error listing stream profiles: {e}"

    @mcp.tool()
    async def apply_profile_to_channels(
        profile_id: int,
        channel_ids: list[int],
    ) -> str:
        """Bulk-assign a channel profile to multiple channels.

        Args:
            profile_id: The channel profile ID to apply
            channel_ids: List of channel IDs to assign the profile to
        """
        try:
            client = get_ecm_client()
            await client.patch(
                f"/api/channel-profiles/{profile_id}/channels/bulk-update",
                json_data={"channel_ids": channel_ids},
            )
            return f"Profile {profile_id} applied to {len(channel_ids)} channels."
        except Exception as e:
            logger.error("[MCP] apply_profile_to_channels failed: %s", e)
            return f"Error applying profile: {e}"
