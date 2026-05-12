"""Channel and stream profile tools."""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_channel_profiles() -> str:
        """List all channel profiles (configuration presets for channels)."""
        try:
            client = get_ecm_client()
            profiles = await client.call_endpoint(ENDPOINTS["channel_profiles_list"])

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
            profiles = await client.call_endpoint(ENDPOINTS["stream_profiles_list"])

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
            await client.call_endpoint(
                ENDPOINTS["channel_profiles_bulk_update"],
                path_args={"profile_id": profile_id},
                body={"channel_ids": channel_ids},
            )
            # Read-back: confirm the profile now lists the requested channels.
            try:
                profiles = await client.call_endpoint(ENDPOINTS["channel_profiles_list"])
                prof = next(
                    (p for p in (profiles or []) if isinstance(p, dict) and p.get("id") == profile_id),
                    None,
                )
                now_count = len(prof.get("channels", [])) if prof else None
            except Exception:
                now_count = None
            suffix = f" Profile now has {now_count} channels." if now_count is not None else ""
            return f"Profile {profile_id} applied to {len(channel_ids)} channels.{suffix}"
        except Exception as e:
            logger.error("[MCP] apply_profile_to_channels failed: %s", e)
            return f"Error applying profile: {e}"
