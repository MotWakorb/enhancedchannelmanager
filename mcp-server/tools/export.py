"""Export profile tools."""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_export_profiles() -> str:
        """List all export profiles for generating M3U/XMLTV files."""
        try:
            client = get_ecm_client()
            profiles = await client.call_endpoint(ENDPOINTS["export_list_profiles"])

            if not profiles:
                return "No export profiles configured."

            lines = [f"Found {len(profiles)} export profiles:"]
            for p in profiles:
                name = p.get("name", "Unknown")
                pid = p.get("id", "?")
                mode = p.get("selection_mode", p.get("type", "unknown"))
                lines.append(f"  {name} (id={pid}) — selection: {mode}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_export_profiles failed: %s", e)
            return f"Error listing export profiles: {e}"

    @mcp.tool()
    async def generate_export(profile_id: int) -> str:
        """Generate M3U/XMLTV output for an export profile.

        Args:
            profile_id: The export profile ID to generate
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["export_generate_profile"], path_args={"profile_id": profile_id},
            )
            msg = result.get("message", "Check ECM for download links.") if isinstance(result, dict) else ""
            return f"Export generated for profile {profile_id}. {msg}"
        except Exception as e:
            logger.error("[MCP] generate_export failed: %s", e)
            return f"Error generating export for profile {profile_id}: {e}"

    @mcp.tool()
    async def create_export_profile(name: str) -> str:
        """Create a new export profile for generating M3U/XMLTV files.

        Args:
            name: Display name for the export profile
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["export_create_profile"], body={"name": name})
            pid = result.get("id", "?") if isinstance(result, dict) else "?"
            rname = result.get("name", name) if isinstance(result, dict) else name
            return f"Export profile created: {rname} (id={pid})"
        except Exception as e:
            logger.error("[MCP] create_export_profile failed: %s", e)
            return f"Error creating export profile: {e}"

    @mcp.tool()
    async def delete_export_profile(profile_id: int) -> str:
        """Delete an export profile.

        Args:
            profile_id: The export profile ID to delete
        """
        try:
            client = get_ecm_client()
            await client.call_endpoint(ENDPOINTS["export_delete_profile"], path_args={"profile_id": profile_id})
            return f"Export profile {profile_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_export_profile failed: %s", e)
            return f"Error deleting export profile {profile_id}: {e}"

    @mcp.tool()
    async def list_cloud_targets() -> str:
        """List configured cloud storage targets for publishing exports."""
        try:
            client = get_ecm_client()
            targets = await client.call_endpoint(ENDPOINTS["export_list_cloud_targets"])

            if not targets:
                return "No cloud targets configured."

            lines = [f"Found {len(targets)} cloud targets:"]
            for t in targets:
                name = t.get("name", "Unknown")
                tid = t.get("id", "?")
                ttype = t.get("type", t.get("provider", "unknown"))
                lines.append(f"  {name} (id={tid}) — {ttype}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_cloud_targets failed: %s", e)
            return f"Error listing cloud targets: {e}"

    @mcp.tool()
    async def publish_export(config_id: int) -> str:
        """Publish an export to a cloud storage target.

        Args:
            config_id: The publish configuration ID to execute
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["export_publish_config"], path_args={"config_id": config_id},
            )
            msg = result.get("message", "") if isinstance(result, dict) else ""
            return f"Publish started for config {config_id}. {msg}"
        except Exception as e:
            logger.error("[MCP] publish_export failed: %s", e)
            return f"Error publishing export: {e}"
