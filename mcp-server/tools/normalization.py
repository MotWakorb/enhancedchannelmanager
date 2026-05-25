"""Stream name normalization tools."""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def test_normalization(text: str) -> str:
        """Test how stream names are normalized by running all enabled rules against the input.

        Args:
            text: The stream name to test normalization on (can pass multiple separated by commas)
        """
        try:
            client = get_ecm_client()
            texts = [t.strip() for t in text.split(",") if t.strip()]
            result = await client.call_endpoint(ENDPOINTS["normalization_test_batch"], body={"texts": texts})

            results = result.get("results", result) if isinstance(result, dict) else result

            if not results:
                return "No normalization results."

            if isinstance(results, list):
                lines = ["Normalization Results:"]
                for r in results:
                    if isinstance(r, dict):
                        orig = r.get("original", "?")
                        norm = r.get("normalized", r.get("result", "?"))
                        lines.append(f"  {orig} → {norm}")
                    else:
                        lines.append(f"  {r}")
                return "\n".join(lines)

            # Dict response
            lines = ["Normalization Results:"]
            for key, value in results.items():
                lines.append(f"  {key} → {value}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] test_normalization failed: %s", e)
            return f"Error testing normalization: {e}"

    @mcp.tool()
    async def set_normalization_group_enabled(group_id: int, enabled: bool) -> str:
        """Enable or disable a normalization rule group globally.

        A normalization group must be globally enabled for its rules to be applied
        by the engine — even if the group is listed in an auto-creation rule's
        normalization_group_ids, the engine skips it when enabled=False.

        Use list_normalization_rules to discover group IDs and their current
        enabled status.

        Args:
            group_id: ID of the NormalizationRuleGroup to update.
            enabled: True to enable the group; False to disable it.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["normalization_update_group"],
                path_args={"group_id": group_id},
                body={"enabled": enabled},
            )
            name = result.get("name", f"group {group_id}") if isinstance(result, dict) else f"group {group_id}"
            state = "enabled" if enabled else "disabled"
            return f"Normalization group '{name}' (id={group_id}) is now {state}."
        except Exception as e:
            logger.error("[MCP] set_normalization_group_enabled failed: %s", e)
            return f"Error updating normalization group {group_id}: {e}"

    @mcp.tool()
    async def list_normalization_rules() -> str:
        """List all normalization rule groups and their rules."""
        try:
            client = get_ecm_client()
            # Use the /rules endpoint — it returns groups WITH nested rules.
            # The /groups endpoint returns group metadata only (no rules).
            result = await client.call_endpoint(ENDPOINTS["normalization_list_rules"])

            groups = result.get("groups", []) if isinstance(result, dict) else result

            if not groups:
                return "No normalization rules configured."

            lines = [f"Normalization Rules ({len(groups)} groups):"]
            for g in groups:
                name = g.get("name", "Unknown")
                gid = g.get("id", "?")
                enabled = "enabled" if g.get("enabled", True) else "disabled"
                rules = g.get("rules") or []
                lines.append(f"\n  {name} (id={gid}) — {enabled}, {len(rules)} rules")
                for r in rules[:5]:
                    rname = r.get("name", "?")
                    rtype = r.get("action_type", r.get("condition_type", "?"))
                    lines.append(f"    - {rname} ({rtype})")
                if len(rules) > 5:
                    lines.append(f"    ... and {len(rules) - 5} more")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_normalization_rules failed: %s", e)
            return f"Error listing normalization rules: {e}"
