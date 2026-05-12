"""Task management tools."""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_tasks() -> str:
        """List all scheduled tasks and their status."""
        try:
            client = get_ecm_client()
            tasks = await client.call_endpoint(ENDPOINTS["tasks_list"])

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
            result = await client.call_endpoint(ENDPOINTS["tasks_run"], path_args={"task_id": task_id})
            if isinstance(result, dict):
                status = result.get("status", "")
                msg = result.get("message", "")
                status_info = f" Status: {status}." if status else ""
                return f"Task '{task_id}' started.{status_info} {msg}".rstrip()
            return f"Task '{task_id}' started."
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
            result = await client.call_endpoint(ENDPOINTS["tasks_cancel"], path_args={"task_id": task_id})
            msg = result.get("message", "") if isinstance(result, dict) else ""
            return f"Task '{task_id}' cancelled. {msg}".rstrip()
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
                result = await client.call_endpoint(
                    ENDPOINTS["tasks_history"], path_args={"task_id": task_id}, query={"limit": limit},
                )
            else:
                result = await client.call_endpoint(ENDPOINTS["tasks_history_all"], query={"limit": limit})

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
            schedules = await client.call_endpoint(ENDPOINTS["tasks_list_schedules"], path_args={"task_id": task_id})

            items = schedules if isinstance(schedules, list) else schedules.get("schedules", [])

            if not items:
                return f"No schedules configured for task '{task_id}'."

            lines = [f"Schedules for '{task_id}' ({len(items)}):"]
            for s in items:
                sid = s.get("id", "?")
                stype = s.get("schedule_type", "?")
                desc = s.get("description", "")
                enabled = "enabled" if s.get("enabled") else "disabled"
                next_run = s.get("next_run_at", s.get("next_run", "?"))
                desc_info = f" — {desc}" if desc else ""
                lines.append(f"  #{sid}: {stype}{desc_info} ({enabled}), next: {next_run}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_task_schedules failed: %s", e)
            return f"Error listing schedules for '{task_id}': {e}"

    @mcp.tool()
    async def create_task_schedule(
        task_id: str,
        schedule_type: str,
        schedule_time: str | None = None,
        interval_seconds: int | None = None,
        days_of_week: list[int] | None = None,
        day_of_month: int | None = None,
        enabled: bool = True,
        name: str | None = None,
    ) -> str:
        """Create a new schedule for a task.

        The backend supports these schedule types (a cron-expression form does
        NOT exist — passing one was silently rejected: drift fixed in bd-vtghg
        Phase 2, hence this signature change from ``cron_expression``):

        Args:
            task_id: The task ID to schedule
            schedule_type: One of 'interval', 'daily', 'weekly', 'biweekly', 'monthly'
            schedule_time: HH:MM time-of-day (for daily/weekly/biweekly/monthly)
            interval_seconds: Interval in seconds (for schedule_type='interval')
            days_of_week: List of day numbers 0=Sunday..6=Saturday (for weekly/biweekly)
            day_of_month: Day of month 1-31, or -1 for last day (for monthly)
            enabled: Whether the schedule is active (default True)
            name: Optional display name for the schedule
        """
        try:
            client = get_ecm_client()
            payload: dict = {"schedule_type": schedule_type, "enabled": enabled}
            if schedule_time is not None:
                payload["schedule_time"] = schedule_time
            if interval_seconds is not None:
                payload["interval_seconds"] = interval_seconds
            if days_of_week is not None:
                payload["days_of_week"] = days_of_week
            if day_of_month is not None:
                payload["day_of_month"] = day_of_month
            if name is not None:
                payload["name"] = name

            result = await client.call_endpoint(
                ENDPOINTS["tasks_create_schedule"], path_args={"task_id": task_id}, body=payload,
            )
            if isinstance(result, dict):
                sid = result.get("id", "?")
                desc = result.get("description", schedule_type)
                return f"Schedule created for '{task_id}': {desc} (id={sid})"
            return f"Schedule created for '{task_id}' ({schedule_type})."
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
            await client.call_endpoint(
                ENDPOINTS["tasks_delete_schedule"],
                path_args={"task_id": task_id, "schedule_id": schedule_id},
            )
            # Read-back: confirm the schedule is gone from the task's list.
            try:
                schedules = await client.call_endpoint(
                    ENDPOINTS["tasks_list_schedules"], path_args={"task_id": task_id},
                )
                items = schedules if isinstance(schedules, list) else (schedules or {}).get("schedules", [])
                still_present = any(isinstance(s, dict) and s.get("id") == schedule_id for s in items)
            except Exception:
                still_present = None
            if still_present is True:
                return f"WARNING: requested deletion of schedule {schedule_id} but it still appears on task '{task_id}'."
            return f"Schedule {schedule_id} deleted from task '{task_id}'."
        except Exception as e:
            logger.error("[MCP] delete_task_schedule failed: %s", e)
            return f"Error deleting schedule: {e}"
