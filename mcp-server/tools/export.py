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
