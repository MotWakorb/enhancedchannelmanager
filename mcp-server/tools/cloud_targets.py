"""Cloud-target tool.

The Export tab's playlist-profile / generate / publish tools were removed with
the tab (beads vrrxv / 1w428). ``list_cloud_targets`` remains because cloud
storage targets are still load-bearing for DBAS backup uploads. The backing
endpoint was relocated from ``/api/export/cloud-targets`` to the dedicated
``/api/cloud-targets`` router with the Export tab's removal.
"""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_cloud_targets() -> str:
        """List configured cloud storage targets (DBAS backup upload destinations)."""
        try:
            client = get_ecm_client()
            targets = await client.call_endpoint(ENDPOINTS["cloud_list_targets"])

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
