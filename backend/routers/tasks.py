"""
Tasks router — scheduled tasks, cron, and task schedule management endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import logging
import time
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import get_session
from dispatcharr_client import get_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Tasks"])


# -------------------------------------------------------------------------
# Request / Response models
# -------------------------------------------------------------------------

class TaskConfigUpdate(BaseModel):
    """Request model for updating task configuration."""
    enabled: Optional[bool] = None
    schedule_type: Optional[str] = None
    interval_seconds: Optional[int] = None
    cron_expression: Optional[str] = None
    schedule_time: Optional[str] = None
    timezone: Optional[str] = None
    config: Optional[dict] = None  # Task-specific configuration (source_ids, account_ids, etc.)
    # Alert configuration
    send_alerts: Optional[bool] = None  # Master toggle for external alerts (email, etc.)
    alert_on_success: Optional[bool] = None  # Alert when task succeeds
    alert_on_warning: Optional[bool] = None  # Alert on partial failures
    alert_on_error: Optional[bool] = None  # Alert on complete failures
    alert_on_info: Optional[bool] = None  # Alert on info messages
    # Notification channels
    send_to_email: Optional[bool] = None  # Send alerts via email
    send_to_discord: Optional[bool] = None  # Send alerts via Discord
    send_to_telegram: Optional[bool] = None  # Send alerts via Telegram
    show_notifications: Optional[bool] = None  # Show in NotificationCenter (bell icon)


class TaskRunRequest(BaseModel):
    """Request body for running a task."""
    schedule_id: Optional[int] = None  # Run with parameters from a specific schedule


class CronValidateRequest(BaseModel):
    """Request to validate a cron expression."""
    expression: str


class TaskScheduleCreate(BaseModel):
    """Request body for creating a task schedule."""
    name: Optional[str] = None
    enabled: bool = True
    schedule_type: Literal['interval', 'daily', 'weekly', 'biweekly', 'monthly']
    interval_seconds: Optional[int] = None
    schedule_time: Optional[str] = None  # HH:MM format
    timezone: Optional[str] = None
    days_of_week: Optional[list] = None  # List of day numbers (0=Sunday, 6=Saturday)
    day_of_month: Optional[int] = None  # 1-31, or -1 for last day
    parameters: Optional[dict] = None  # Task-specific parameters (e.g., channel_groups, batch_size)


class TaskScheduleUpdate(BaseModel):
    """Request body for updating a task schedule."""
    name: Optional[str] = None
    enabled: Optional[bool] = None
    schedule_type: Optional[Literal['interval', 'daily', 'weekly', 'biweekly', 'monthly']] = None
    interval_seconds: Optional[int] = None
    schedule_time: Optional[str] = None
    timezone: Optional[str] = None
    days_of_week: Optional[list] = None
    day_of_month: Optional[int] = None
    parameters: Optional[dict] = None  # Task-specific parameters


# Task parameter schemas - defines what parameters each task type accepts
# This is used by the frontend to render appropriate form fields
TASK_PARAMETER_SCHEMAS = {
    "stream_probe": {
        "description": "Stream health probing parameters",
        "parameters": [
            {
                "name": "auto_sync_groups",
                "type": "boolean",
                "label": "Auto-sync groups",
                "description": "Automatically probe all current groups at runtime (ignores group selection below)",
                "default": False,
            },
            {
                "name": "channel_groups",
                "type": "number_array",
                "label": "Channel Groups",
                "description": "Which channel groups to include in the probe",
                "default": [],
                "source": "channel_groups",  # Tells UI to fetch from channel groups API
            },
            {
                "name": "batch_size",
                "type": "number",
                "label": "Batch Size",
                "description": "Number of streams to probe per batch",
                "default": 10,
                "min": 1,
                "max": 100,
            },
            {
                "name": "timeout",
                "type": "number",
                "label": "Timeout (seconds)",
                "description": "Timeout per stream probe in seconds",
                "default": 30,
                "min": 5,
                "max": 300,
            },
            {
                "name": "max_concurrent",
                "type": "number",
                "label": "Max Concurrent",
                "description": "Maximum concurrent probe operations",
                "default": 3,
                "min": 1,
                "max": 20,
            },
        ],
    },
    "m3u_refresh": {
        "description": "M3U account refresh parameters",
        "parameters": [
            {
                "name": "account_ids",
                "type": "number_array",
                "label": "M3U Accounts",
                "description": "Which M3U accounts to refresh (empty = all accounts)",
                "default": [],
                "source": "m3u_accounts",  # Tells UI to fetch from M3U accounts API
            },
        ],
    },
    "epg_refresh": {
        "description": "EPG data refresh parameters",
        "parameters": [
            {
                "name": "source_ids",
                "type": "number_array",
                "label": "EPG Sources",
                "description": "Which EPG sources to refresh (empty = all sources)",
                "default": [],
                "source": "epg_sources",  # Tells UI to fetch from EPG sources API
            },
        ],
    },
    "cleanup": {
        "description": "Cleanup task parameters",
        "parameters": [
            {
                "name": "retention_days",
                "type": "number",
                "label": "Retention Days",
                "description": "Keep data for this many days (0 = use default)",
                "default": 0,
                "min": 0,
                "max": 365,
            },
        ],
    },
}


# -------------------------------------------------------------------------
# Scheduled Tasks API
# -------------------------------------------------------------------------

# NOTE: Non-parameterized routes (/engine/status, /history/all,
# /parameter-schemas) are defined BEFORE /{task_id} so they are not
# shadowed by the path parameter.

@router.get("/api/tasks/engine/status", tags=["Tasks"])
async def get_engine_status():
    """Get task engine status."""
    try:
        from task_engine import get_engine
        engine = get_engine()
        return engine.get_status()
    except Exception as e:
        logger.error(f"Failed to get engine status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/tasks/history/all", tags=["Tasks"])
async def get_all_task_history(limit: int = 100, offset: int = 0):
    """Get execution history for all tasks."""
    try:
        from task_engine import get_engine
        engine = get_engine()
        history = engine.get_task_history(task_id=None, limit=limit, offset=offset)
        return {"history": history}
    except Exception as e:
        logger.error(f"Failed to get all task history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/tasks/parameter-schemas", tags=["Tasks"])
async def get_all_task_parameter_schemas():
    """Get parameter schemas for all task types."""
    return {"schemas": TASK_PARAMETER_SCHEMAS}


@router.get("/api/tasks", tags=["Tasks"])
async def list_tasks():
    """Get all registered tasks with their status, including schedules."""
    start_time = time.time()
    try:
        from task_registry import get_registry
        from models import TaskSchedule, ScheduledTask
        from schedule_calculator import describe_schedule

        registry = get_registry()
        tasks = registry.get_all_task_statuses()

        # Include schedules and alert config for each task
        session = get_session()
        try:
            for task in tasks:
                task_id = task.get('task_id')
                if task_id:
                    # Get alert configuration from ScheduledTask
                    db_task = session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()
                    if db_task:
                        task['send_alerts'] = db_task.send_alerts
                        task['alert_on_success'] = db_task.alert_on_success
                        task['alert_on_warning'] = db_task.alert_on_warning
                        task['alert_on_error'] = db_task.alert_on_error
                        task['alert_on_info'] = db_task.alert_on_info
                        task['send_to_email'] = db_task.send_to_email
                        task['send_to_discord'] = db_task.send_to_discord
                        task['send_to_telegram'] = db_task.send_to_telegram
                        task['show_notifications'] = db_task.show_notifications

                    # Get schedules
                    schedules = session.query(TaskSchedule).filter(TaskSchedule.task_id == task_id).all()
                    task['schedules'] = []
                    for schedule in schedules:
                        schedule_dict = schedule.to_dict()
                        schedule_dict['description'] = describe_schedule(
                            schedule_type=schedule.schedule_type,
                            interval_seconds=schedule.interval_seconds,
                            schedule_time=schedule.schedule_time,
                            timezone=schedule.timezone,
                            days_of_week=schedule.get_days_of_week_list(),
                            day_of_month=schedule.day_of_month,
                        )
                        task['schedules'].append(schedule_dict)
        finally:
            session.close()

        duration_ms = (time.time() - start_time) * 1000
        running_tasks = [t.get('task_id') for t in tasks if t.get('status') == 'running']
        logger.debug(
            f"[TASKS] Listed {len(tasks)} tasks in {duration_ms:.1f}ms"
            + (f" - running: {running_tasks}" if running_tasks else "")
        )
        return {"tasks": tasks}
    except Exception as e:
        logger.error(f"[TASKS] Failed to list tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/tasks/{task_id}", tags=["Tasks"])
async def get_task(task_id: str):
    """Get status for a specific task, including all schedules."""
    try:
        from task_registry import get_registry
        from models import TaskSchedule, ScheduledTask
        from schedule_calculator import describe_schedule

        registry = get_registry()
        status = registry.get_task_status(task_id)
        if status is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Include schedules and alert config in the response
        session = get_session()
        try:
            # Get alert configuration from ScheduledTask
            db_task = session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()
            if db_task:
                status['send_alerts'] = db_task.send_alerts
                status['alert_on_success'] = db_task.alert_on_success
                status['alert_on_warning'] = db_task.alert_on_warning
                status['alert_on_error'] = db_task.alert_on_error
                status['alert_on_info'] = db_task.alert_on_info
                status['send_to_email'] = db_task.send_to_email
                status['send_to_discord'] = db_task.send_to_discord
                status['send_to_telegram'] = db_task.send_to_telegram
                status['show_notifications'] = db_task.show_notifications

            # Get schedules
            schedules = session.query(TaskSchedule).filter(TaskSchedule.task_id == task_id).all()
            status['schedules'] = []
            for schedule in schedules:
                schedule_dict = schedule.to_dict()
                schedule_dict['description'] = describe_schedule(
                    schedule_type=schedule.schedule_type,
                    interval_seconds=schedule.interval_seconds,
                    schedule_time=schedule.schedule_time,
                    timezone=schedule.timezone,
                    days_of_week=schedule.get_days_of_week_list(),
                    day_of_month=schedule.day_of_month,
                )
                status['schedules'].append(schedule_dict)
        finally:
            session.close()

        return status
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/tasks/{task_id}", tags=["Tasks"])
async def update_task(task_id: str, config: TaskConfigUpdate):
    """Update task configuration."""
    try:
        from task_registry import get_registry
        registry = get_registry()

        result = registry.update_task_config(
            task_id=task_id,
            enabled=config.enabled,
            schedule_type=config.schedule_type,
            interval_seconds=config.interval_seconds,
            cron_expression=config.cron_expression,
            schedule_time=config.schedule_time,
            timezone=config.timezone,
            task_config=config.config,
            send_alerts=config.send_alerts,
            alert_on_success=config.alert_on_success,
            alert_on_warning=config.alert_on_warning,
            alert_on_error=config.alert_on_error,
            alert_on_info=config.alert_on_info,
            send_to_email=config.send_to_email,
            send_to_discord=config.send_to_discord,
            send_to_telegram=config.send_to_telegram,
            show_notifications=config.show_notifications,
        )

        if result is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/tasks/{task_id}/run", tags=["Tasks"])
async def run_task(task_id: str, request: Optional[TaskRunRequest] = None):
    """Manually trigger a task execution."""
    try:
        from task_engine import get_engine
        engine = get_engine()
        schedule_id = request.schedule_id if request else None
        result = await engine.run_task(task_id, schedule_id=schedule_id)

        if result is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        return result.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to run task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/tasks/{task_id}/cancel", tags=["Tasks"])
async def cancel_task(task_id: str):
    """Cancel a running task."""
    try:
        from task_engine import get_engine
        engine = get_engine()
        result = await engine.cancel_task(task_id)
        if result.get("status") == "not_found":
            raise HTTPException(status_code=404, detail=result.get("message", f"Task {task_id} not found"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/tasks/{task_id}/history", tags=["Tasks"])
async def get_task_history(task_id: str, limit: int = 50, offset: int = 0):
    """Get execution history for a task."""
    try:
        from task_engine import get_engine
        engine = get_engine()
        history = engine.get_task_history(task_id=task_id, limit=limit, offset=offset)
        return {"history": history}
    except Exception as e:
        logger.error(f"Failed to get history for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/tasks/{task_id}/parameter-schema", tags=["Tasks"])
async def get_task_parameter_schema(task_id: str):
    """Get the parameter schema for a task type."""
    schema = TASK_PARAMETER_SCHEMAS.get(task_id)
    if not schema:
        # Return empty schema for tasks without special parameters
        return {"task_id": task_id, "description": "No configurable parameters", "parameters": []}
    return {"task_id": task_id, **schema}


# -------------------------------------------------------------------------
# Cron API
# -------------------------------------------------------------------------

@router.get("/api/cron/presets", tags=["Cron"])
async def get_cron_presets():
    """Get available cron presets for task scheduling."""
    try:
        from cron_parser import get_preset_list
        return {"presets": get_preset_list()}
    except Exception as e:
        logger.error(f"Failed to get cron presets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/cron/validate", tags=["Cron"])
async def validate_cron(request: CronValidateRequest):
    """Validate a cron expression."""
    try:
        from cron_parser import validate_cron_expression, describe_cron_expression, get_next_n_run_times

        is_valid, error = validate_cron_expression(request.expression)

        if not is_valid:
            return {
                "valid": False,
                "error": error,
            }

        # Get next run times for valid expressions
        next_times = get_next_n_run_times(request.expression, n=5)

        return {
            "valid": True,
            "description": describe_cron_expression(request.expression),
            "next_runs": [t.isoformat() + "Z" for t in next_times],
        }
    except Exception as e:
        logger.error(f"Failed to validate cron expression: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# Task Schedule API - Multiple schedules per task
# =========================================================================

@router.get("/api/tasks/{task_id}/schedules", tags=["Tasks"])
async def list_task_schedules(task_id: str):
    """Get all schedules for a task."""
    try:
        from models import TaskSchedule, ScheduledTask
        from schedule_calculator import describe_schedule

        session = get_session()
        try:
            # Verify task exists
            task = session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()
            if not task:
                raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

            # Get all schedules for this task
            schedules = session.query(TaskSchedule).filter(TaskSchedule.task_id == task_id).all()

            # For stream_probe, validate channel_groups against current groups
            current_groups_data = None
            if task_id == "stream_probe":
                try:
                    client = get_client()
                    current_groups_data = await client.get_channel_groups()
                except Exception as e:
                    logger.debug(f"Could not fetch current groups for validation: {e}")

            result = []
            schedules_fixed = False
            current_by_id = {g["id"]: g.get("name") for g in current_groups_data} if current_groups_data else {}

            for schedule in schedules:
                schedule_dict = schedule.to_dict()
                # Add human-readable description
                schedule_dict['description'] = describe_schedule(
                    schedule_type=schedule.schedule_type,
                    interval_seconds=schedule.interval_seconds,
                    schedule_time=schedule.schedule_time,
                    timezone=schedule.timezone,
                    days_of_week=schedule.get_days_of_week_list(),
                    day_of_month=schedule.day_of_month,
                )
                # Auto-cleanup: remove stale groups (deleted from Dispatcharr).
                # Do NOT auto-add new groups — users control which groups to probe
                # via the schedule editor. Use auto_sync_groups for "probe all".
                if current_groups_data is not None and schedule_dict.get("parameters"):
                    params = schedule_dict["parameters"]
                    stored = params.get("channel_groups", [])
                    if stored:  # Only cleanup if schedule has an explicit group list
                        if isinstance(stored[0], int):
                            valid = [gid for gid in stored if gid in current_by_id]
                            stale = [gid for gid in stored if gid not in current_by_id]
                        else:
                            current_by_name = {g.get("name"): g["id"] for g in current_groups_data}
                            valid = [current_by_name[n] for n in stored if n in current_by_name]
                            stale = [n for n in stored if n not in current_by_name]

                        if stale:
                            params["channel_groups"] = valid
                            params.pop("_stale_groups", None)

                            # Persist fix to DB
                            db_params = schedule.get_parameters()
                            db_params["channel_groups"] = valid
                            db_params.pop("_stale_groups", None)
                            schedule.set_parameters(db_params)
                            session.add(schedule)
                            schedules_fixed = True

                            logger.info(f"Auto-removed {len(stale)} stale group(s) from probe schedule {schedule.id}")
                result.append(schedule_dict)

            # Commit any auto-fixes
            if schedules_fixed:
                session.commit()

            # Always clean up stale group notifications since we auto-fix now
            from models import Notification as NotificationModel
            stale_notifs = session.query(NotificationModel).filter(
                NotificationModel.source_id == "stream_probe_stale_groups",
            ).all()
            for n in stale_notifs:
                session.delete(n)
            if stale_notifs:
                session.commit()

            return {"schedules": result}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list schedules for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/tasks/{task_id}/schedules", tags=["Tasks"])
async def create_task_schedule(task_id: str, data: TaskScheduleCreate):
    """Create a new schedule for a task."""
    try:
        from models import TaskSchedule, ScheduledTask
        from schedule_calculator import calculate_next_run, describe_schedule

        session = get_session()
        try:
            # Verify task exists
            task = session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()
            if not task:
                raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

            # Create the schedule
            schedule = TaskSchedule(
                task_id=task_id,
                name=data.name,
                enabled=data.enabled,
                schedule_type=data.schedule_type,
                interval_seconds=data.interval_seconds,
                schedule_time=data.schedule_time,
                timezone=data.timezone or "UTC",
                day_of_month=data.day_of_month,
            )

            # Set days_of_week if provided
            if data.days_of_week:
                schedule.set_days_of_week_list(data.days_of_week)

            # Set task-specific parameters if provided (strip internal metadata keys)
            if data.parameters:
                clean_params = {k: v for k, v in data.parameters.items() if not k.startswith("_")}
                schedule.set_parameters(clean_params)

            # Calculate next run time
            if data.enabled:
                schedule.next_run_at = calculate_next_run(
                    schedule_type=data.schedule_type,
                    interval_seconds=data.interval_seconds,
                    schedule_time=data.schedule_time,
                    timezone=data.timezone or "UTC",
                    days_of_week=data.days_of_week,
                    day_of_month=data.day_of_month,
                )

            session.add(schedule)
            session.commit()
            session.refresh(schedule)

            # Build response
            result = schedule.to_dict()
            result['description'] = describe_schedule(
                schedule_type=schedule.schedule_type,
                interval_seconds=schedule.interval_seconds,
                schedule_time=schedule.schedule_time,
                timezone=schedule.timezone,
                days_of_week=schedule.get_days_of_week_list(),
                day_of_month=schedule.day_of_month,
            )

            # Update the parent task's next_run_at to be the earliest of all schedules
            _update_task_next_run(session, task_id)
            session.commit()

            return result
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create schedule for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/tasks/{task_id}/schedules/{schedule_id}", tags=["Tasks"])
async def update_task_schedule(task_id: str, schedule_id: int, data: TaskScheduleUpdate):
    """Update a task schedule."""
    try:
        from models import TaskSchedule, ScheduledTask
        from schedule_calculator import calculate_next_run, describe_schedule

        session = get_session()
        try:
            # Verify schedule exists and belongs to task
            schedule = session.query(TaskSchedule).filter(
                TaskSchedule.id == schedule_id,
                TaskSchedule.task_id == task_id
            ).first()

            if not schedule:
                raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found for task {task_id}")

            # Update fields if provided
            if data.name is not None:
                schedule.name = data.name
            if data.enabled is not None:
                schedule.enabled = data.enabled
            if data.schedule_type is not None:
                schedule.schedule_type = data.schedule_type
            if data.interval_seconds is not None:
                schedule.interval_seconds = data.interval_seconds
            if data.schedule_time is not None:
                schedule.schedule_time = data.schedule_time
            if data.timezone is not None:
                schedule.timezone = data.timezone
            if data.days_of_week is not None:
                schedule.set_days_of_week_list(data.days_of_week)
            if data.day_of_month is not None:
                schedule.day_of_month = data.day_of_month
            if data.parameters is not None:
                clean_params = {k: v for k, v in data.parameters.items() if not k.startswith("_")}
                schedule.set_parameters(clean_params)

            # Recalculate next run time
            if schedule.enabled:
                schedule.next_run_at = calculate_next_run(
                    schedule_type=schedule.schedule_type,
                    interval_seconds=schedule.interval_seconds,
                    schedule_time=schedule.schedule_time,
                    timezone=schedule.timezone,
                    days_of_week=schedule.get_days_of_week_list(),
                    day_of_month=schedule.day_of_month,
                )
            else:
                schedule.next_run_at = None

            session.commit()
            session.refresh(schedule)

            # Build response
            result = schedule.to_dict()
            result['description'] = describe_schedule(
                schedule_type=schedule.schedule_type,
                interval_seconds=schedule.interval_seconds,
                schedule_time=schedule.schedule_time,
                timezone=schedule.timezone,
                days_of_week=schedule.get_days_of_week_list(),
                day_of_month=schedule.day_of_month,
            )

            # Update the parent task's next_run_at
            _update_task_next_run(session, task_id)
            session.commit()

            return result
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update schedule {schedule_id} for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/tasks/{task_id}/schedules/{schedule_id}", tags=["Tasks"])
async def delete_task_schedule(task_id: str, schedule_id: int):
    """Delete a task schedule."""
    try:
        from models import TaskSchedule

        session = get_session()
        try:
            # Verify schedule exists and belongs to task
            schedule = session.query(TaskSchedule).filter(
                TaskSchedule.id == schedule_id,
                TaskSchedule.task_id == task_id
            ).first()

            if not schedule:
                raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found for task {task_id}")

            session.delete(schedule)
            session.commit()

            # Update the parent task's next_run_at
            _update_task_next_run(session, task_id)
            session.commit()

            return {"status": "deleted", "id": schedule_id}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete schedule {schedule_id} for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------------------
# Helper functions
# -------------------------------------------------------------------------

def _update_task_next_run(session, task_id: str) -> None:
    """Update a task's next_run_at based on its schedules."""
    from models import TaskSchedule, ScheduledTask

    # Get the earliest next_run_at from all enabled schedules
    schedules = session.query(TaskSchedule).filter(
        TaskSchedule.task_id == task_id,
        TaskSchedule.enabled == True,
        TaskSchedule.next_run_at != None
    ).order_by(TaskSchedule.next_run_at).all()

    task = session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()
    if task:
        if schedules:
            task.next_run_at = schedules[0].next_run_at
        else:
            task.next_run_at = None
