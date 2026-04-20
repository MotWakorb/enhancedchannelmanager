"""Export profile tools."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_export_profiles() -> str:
        """List all export profiles for generating M3U/XMLTV files."""
        try:
            client = get_ecm_client()
            profiles = await client.get("/api/export/profiles")

            if not profiles:
                return "No export profiles configured."

            lines = [f"Found {len(profiles)} export profiles:"]
            for p in profiles:
                name = p.get("name", "Unknown")
                pid = p.get("id", "?")
                ptype = p.get("type", "unknown")
                lines.append(f"  {name} (id={pid}) — type: {ptype}")

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
            result = await client.post(f"/api/export/profiles/{profile_id}/generate")
            return f"Export generated for profile {profile_id}. {result.get('message', 'Check ECM for download links.')}"
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
            result = await client.post("/api/export/profiles", json_data={"name": name})
            pid = result.get("id", "?")
            return f"Export profile created: {name} (id={pid})"
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
            await client.delete(f"/api/export/profiles/{profile_id}")
            return f"Export profile {profile_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_export_profile failed: %s", e)
            return f"Error deleting export profile {profile_id}: {e}"

    @mcp.tool()
    async def list_cloud_targets() -> str:
        """List configured cloud storage targets for publishing exports."""
        try:
            client = get_ecm_client()
            targets = await client.get("/api/export/cloud-targets")

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
            result = await client.post(f"/api/export/publish-configs/{config_id}/publish")
            return f"Publish started for config {config_id}. {result.get('message', '')}"
        except Exception as e:
            logger.error("[MCP] publish_export failed: %s", e)
            return f"Error publishing export: {e}"
