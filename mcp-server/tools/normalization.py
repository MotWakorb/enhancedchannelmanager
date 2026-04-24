"""Stream name normalization tools."""
import logging

from mcp.server.fastmcp import FastMCP

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
            result = await client.post("/api/normalization/test-batch", json_data={"texts": texts})

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
    async def list_normalization_rules() -> str:
        """List all normalization rule groups and their rules."""
        try:
            client = get_ecm_client()
            result = await client.get("/api/normalization/groups")

            groups = result.get("groups", []) if isinstance(result, dict) else result

            if not groups:
                return "No normalization rules configured."

            lines = [f"Normalization Rules ({len(groups)} groups):"]
            for g in groups:
                name = g.get("name", "Unknown")
                gid = g.get("id", "?")
                enabled = "enabled" if g.get("enabled", True) else "disabled"
                rules = g.get("rules", [])
                lines.append(f"\n  {name} (id={gid}) — {enabled}, {len(rules)} rules")
                for r in rules[:5]:
                    rname = r.get("name", r.get("pattern", "?"))
                    rtype = r.get("type", "?")
                    lines.append(f"    - {rname} ({rtype})")
                if len(rules) > 5:
                    lines.append(f"    ... and {len(rules) - 5} more")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_normalization_rules failed: %s", e)
            return f"Error listing normalization rules: {e}"
