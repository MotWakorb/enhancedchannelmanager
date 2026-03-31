"""Task management tools."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_tasks() -> str:
        """List all scheduled tasks and their status."""
        try:
            client = get_ecm_client()
            tasks = await client.get("/api/tasks")

            if not tasks:
                return "No tasks configured."

            lines = [f"Found {len(tasks)} tasks:"]
            for t in tasks:
                name = t.get("name", "Unknown")
                tid = t.get("task_id", t.get("id", "?"))
                enabled = "enabled" if t.get("enabled") else "disabled"
                last_run = t.get("last_run", "never")
                status = t.get("status", "idle")
                lines.append(f"  {name} (id={tid}) — {enabled}, status: {status}, last run: {last_run}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_tasks failed: %s", e)
            return f"Error listing tasks: {e}"

    @mcp.tool()
    async def run_task(task_id: str) -> str:
        """Run a scheduled task immediately.

        Args:
            task_id: The task ID to run (e.g., 'm3u_refresh', 'stream_probe')
        """
        try:
            client = get_ecm_client()
            result = await client.post(f"/api/tasks/{task_id}/run")
            return f"Task '{task_id}' started. {result.get('message', '')}"
        except Exception as e:
            logger.error("[MCP] run_task failed: %s", e)
            return f"Error running task '{task_id}': {e}"
