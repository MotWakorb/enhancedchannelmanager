"""Auto-creation pipeline tools."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_auto_creation_rules() -> str:
        """List all auto-creation rules that automatically create channels from streams."""
        try:
            client = get_ecm_client()
            rules = await client.get("/api/auto-creation/rules")

            if not rules:
                return "No auto-creation rules configured."

            lines = [f"Found {len(rules)} auto-creation rules:"]
            for r in rules:
                name = r.get("name", "Unnamed")
                rid = r.get("id", "?")
                enabled = "enabled" if r.get("enabled") else "disabled"
                priority = r.get("priority", "?")
                lines.append(f"  [{priority}] {name} (id={rid}) — {enabled}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_auto_creation_rules failed: %s", e)
            return f"Error listing auto-creation rules: {e}"

    @mcp.tool()
    async def run_auto_creation(dry_run: bool = True) -> str:
        """Run the auto-creation pipeline to create channels from matching streams.

        Args:
            dry_run: If true (default), preview what would be created without making changes.
                     Set to false to actually create the channels.
        """
        try:
            client = get_ecm_client()
            result = await client.post("/api/auto-creation/run", json_data={"dry_run": dry_run})

            mode = "Dry run" if dry_run else "Execution"
            created = result.get("created", 0)
            skipped = result.get("skipped", 0)
            errors = result.get("errors", 0)

            lines = [f"Auto-creation {mode} complete:"]
            lines.append(f"  Channels {'would be ' if dry_run else ''}created: {created}")
            lines.append(f"  Skipped: {skipped}")
            if errors:
                lines.append(f"  Errors: {errors}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] run_auto_creation failed: %s", e)
            return f"Error running auto-creation: {e}"
