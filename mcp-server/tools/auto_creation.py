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

    @mcp.tool()
    async def get_auto_creation_rule(rule_id: int) -> str:
        """Get detailed information about a specific auto-creation rule.

        Args:
            rule_id: The rule ID to look up
        """
        try:
            client = get_ecm_client()
            r = await client.get(f"/api/auto-creation/rules/{rule_id}")

            lines = [
                f"Rule: {r.get('name', 'Unnamed')}",
                f"  ID: {r.get('id')}",
                f"  Enabled: {r.get('enabled', False)}",
                f"  Priority: {r.get('priority', '?')}",
            ]

            conditions = r.get("conditions", [])
            if conditions:
                lines.append(f"  Conditions ({len(conditions)}):")
                for c in conditions[:5]:
                    lines.append(f"    - {c.get('type', '?')}: {c.get('value', c.get('pattern', '?'))}")

            actions = r.get("actions", [])
            if actions:
                lines.append(f"  Actions ({len(actions)}):")
                for a in actions[:5]:
                    lines.append(f"    - {a.get('type', '?')}: {a.get('value', a.get('target', '?'))}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_auto_creation_rule failed: %s", e)
            return f"Error getting rule {rule_id}: {e}"

    @mcp.tool()
    async def toggle_auto_creation_rule(rule_id: int) -> str:
        """Enable or disable an auto-creation rule (toggles current state).

        Args:
            rule_id: The rule ID to toggle
        """
        try:
            client = get_ecm_client()
            result = await client.post(f"/api/auto-creation/rules/{rule_id}/toggle")
            enabled = result.get("enabled", "unknown")
            return f"Rule {rule_id} is now {'enabled' if enabled else 'disabled'}."
        except Exception as e:
            logger.error("[MCP] toggle_auto_creation_rule failed: %s", e)
            return f"Error toggling rule {rule_id}: {e}"

    @mcp.tool()
    async def duplicate_auto_creation_rule(rule_id: int) -> str:
        """Duplicate an auto-creation rule.

        Args:
            rule_id: The rule ID to duplicate
        """
        try:
            client = get_ecm_client()
            result = await client.post(f"/api/auto-creation/rules/{rule_id}/duplicate")
            new_id = result.get("id", "?")
            return f"Rule {rule_id} duplicated. New rule ID: {new_id}"
        except Exception as e:
            logger.error("[MCP] duplicate_auto_creation_rule failed: %s", e)
            return f"Error duplicating rule {rule_id}: {e}"

    @mcp.tool()
    async def delete_auto_creation_rule(rule_id: int) -> str:
        """Delete an auto-creation rule.

        Args:
            rule_id: The rule ID to delete
        """
        try:
            client = get_ecm_client()
            await client.delete(f"/api/auto-creation/rules/{rule_id}")
            return f"Rule {rule_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_auto_creation_rule failed: %s", e)
            return f"Error deleting rule {rule_id}: {e}"

    @mcp.tool()
    async def list_auto_creation_executions(limit: int = 10) -> str:
        """List recent auto-creation pipeline executions.

        Args:
            limit: Number of executions to return (default 10)
        """
        try:
            client = get_ecm_client()
            result = await client.get("/api/auto-creation/executions", limit=limit)

            executions = result.get("executions", []) if isinstance(result, dict) else result

            if not executions:
                return "No auto-creation executions found."

            lines = [f"Recent executions ({len(executions)}):"]
            for ex in executions[:limit]:
                eid = ex.get("id", "?")
                status = ex.get("status", "?")
                created = ex.get("created_at", ex.get("timestamp", "?"))
                channels = ex.get("channels_created", ex.get("created", 0))
                dry = " (dry run)" if ex.get("dry_run") else ""
                lines.append(f"  #{eid}: {status} — {channels} channels{dry} ({created})")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_auto_creation_executions failed: %s", e)
            return f"Error listing executions: {e}"

    @mcp.tool()
    async def rollback_auto_creation(execution_id: int) -> str:
        """Rollback an auto-creation execution, deleting all channels it created.

        Args:
            execution_id: The execution ID to rollback
        """
        try:
            client = get_ecm_client()
            result = await client.post(f"/api/auto-creation/executions/{execution_id}/rollback")
            deleted = result.get("deleted", result.get("channels_deleted", 0))
            return f"Execution {execution_id} rolled back. {deleted} channels deleted."
        except Exception as e:
            logger.error("[MCP] rollback_auto_creation failed: %s", e)
            return f"Error rolling back execution {execution_id}: {e}"
