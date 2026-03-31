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

    @mcp.tool()
    async def cancel_task(task_id: str) -> str:
        """Cancel a currently running task.

        Args:
            task_id: The task ID to cancel
        """
        try:
            client = get_ecm_client()
            result = await client.post(f"/api/tasks/{task_id}/cancel")
            return f"Task '{task_id}' cancelled. {result.get('message', '')}"
        except Exception as e:
            logger.error("[MCP] cancel_task failed: %s", e)
            return f"Error cancelling task '{task_id}': {e}"

    @mcp.tool()
    async def get_task_history(task_id: str | None = None, limit: int = 10) -> str:
        """View task execution history.

        Args:
            task_id: Optional specific task ID to get history for. If omitted, returns all task history.
            limit: Number of history entries to return (default 10)
        """
        try:
            client = get_ecm_client()
            if task_id:
                result = await client.get(f"/api/tasks/{task_id}/history", limit=limit)
            else:
                result = await client.get("/api/tasks/history/all", limit=limit)

            history = result.get("history", []) if isinstance(result, dict) else result

            if not history:
                scope = f"for task '{task_id}'" if task_id else ""
                return f"No task history {scope}."

            lines = [f"Task history ({len(history)} entries):"]
            for h in history[:limit]:
                name = h.get("task_name", h.get("task_id", "?"))
                status = h.get("status", "?")
                started = h.get("started_at", h.get("timestamp", "?"))
                duration = h.get("duration_seconds", h.get("duration", 0))
                dur_str = f"{duration:.1f}s" if duration else "?"
                lines.append(f"  {name}: {status} ({dur_str}) — {started}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_task_history failed: %s", e)
            return f"Error getting task history: {e}"

    @mcp.tool()
    async def list_task_schedules(task_id: str) -> str:
        """List schedules for a specific task.

        Args:
            task_id: The task ID to list schedules for
        """
        try:
            client = get_ecm_client()
            schedules = await client.get(f"/api/tasks/{task_id}/schedules")

            items = schedules if isinstance(schedules, list) else schedules.get("schedules", [])

            if not items:
                return f"No schedules configured for task '{task_id}'."

            lines = [f"Schedules for '{task_id}' ({len(items)}):"]
            for s in items:
                sid = s.get("id", "?")
                cron = s.get("cron_expression", s.get("cron", "?"))
                enabled = "enabled" if s.get("enabled") else "disabled"
                next_run = s.get("next_run", "?")
                lines.append(f"  #{sid}: {cron} ({enabled}), next: {next_run}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_task_schedules failed: %s", e)
            return f"Error listing schedules for '{task_id}': {e}"

    @mcp.tool()
    async def create_task_schedule(
        task_id: str,
        cron_expression: str,
    ) -> str:
        """Create a new schedule for a task using a cron expression.

        Args:
            task_id: The task ID to schedule
            cron_expression: Cron expression (e.g., "0 */6 * * *" for every 6 hours)
        """
        try:
            client = get_ecm_client()
            result = await client.post(
                f"/api/tasks/{task_id}/schedules",
                json_data={"cron_expression": cron_expression},
            )
            sid = result.get("id", "?")
            return f"Schedule created for '{task_id}': {cron_expression} (id={sid})"
        except Exception as e:
            logger.error("[MCP] create_task_schedule failed: %s", e)
            return f"Error creating schedule: {e}"

    @mcp.tool()
    async def delete_task_schedule(task_id: str, schedule_id: int) -> str:
        """Delete a task schedule.

        Args:
            task_id: The task ID the schedule belongs to
            schedule_id: The schedule ID to delete
        """
        try:
            client = get_ecm_client()
            await client.delete(f"/api/tasks/{task_id}/schedules/{schedule_id}")
            return f"Schedule {schedule_id} deleted from task '{task_id}'."
        except Exception as e:
            logger.error("[MCP] delete_task_schedule failed: %s", e)
            return f"Error deleting schedule: {e}"
