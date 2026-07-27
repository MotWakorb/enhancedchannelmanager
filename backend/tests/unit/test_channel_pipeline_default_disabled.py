"""Tests for the auto-creation opt-in model (enhancedchannelmanager-i2xad).

Scheduled auto-creation is now OPT-IN: the ``ChannelPipelineTask`` ships with
``default_enabled = False``, so a fresh install seeds the PARENT
``scheduled_tasks`` row disabled and never fires auto-creation autonomously. The
child ``task_schedules`` cadence row is seeded ENABLED (the "every 60s" schedule
exists); firing is gated by the parent. The ADR-011 INTERVAL/60s architecture
(self-firing tick + AUTO-FIRE GUARD) is preserved — only the default-enabled
flag changed.

task_engine fires a task only when BOTH the child schedule's ``enabled`` AND the
parent task's ``enabled`` hold (``_scheduler_loop``: the due-schedule query
filters ``TaskSchedule.enabled == True`` and then skips when the parent
``ScheduledTask.enabled`` is False). So the single operator action "flip the task
Enabled toggle" (``PATCH /api/tasks/{id}`` → parent on) is enough to opt in,
because the child cadence row is already enabled.

Regression: the other default-scheduled tasks keep ``default_enabled = True`` and
so their parent rows still seed enabled.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from models import ScheduledTask, TaskSchedule
from task_registry import TaskRegistry

# Import task classes (registers them via @register_task as a side effect, and
# gives us the concrete classes to instantiate directly).
from tasks.channel_pipeline import ChannelPipelineTask
from tasks.cleanup import CleanupTask
from tasks.stats_v2_rollup import StatsV2RollupTask
from tasks.m3u_change_monitor import M3UChangeMonitorTask


def _seed_task(session, instance):
    """Seed parent ScheduledTask + child TaskSchedule via the real registry path."""
    registry = TaskRegistry()
    registry._save_task_to_db(session, instance)
    session.commit()
    parent = session.query(ScheduledTask).filter(
        ScheduledTask.task_id == instance.task_id
    ).one()
    child = session.query(TaskSchedule).filter(
        TaskSchedule.task_id == instance.task_id
    ).one()
    return parent, child


class TestAutoCreationDefaultDisabled:
    def test_default_enabled_is_false(self):
        assert ChannelPipelineTask.default_enabled is False
        assert ChannelPipelineTask()._enabled is False

    def test_schedule_architecture_preserved(self):
        """Opt-in must NOT change the INTERVAL/60s schedule (ADR-011)."""
        from task_scheduler import ScheduleType
        from task_engine import DEFAULT_CHECK_INTERVAL

        task = ChannelPipelineTask()
        assert task.schedule_config.schedule_type == ScheduleType.INTERVAL
        assert task.schedule_config.interval_seconds == DEFAULT_CHECK_INTERVAL

    def test_parent_seeded_disabled_child_cadence_enabled(self, test_session):
        """Fresh install: parent task disabled (no autonomous firing), child
        cadence row enabled (so the single task toggle is the opt-in)."""
        parent, child = _seed_task(test_session, ChannelPipelineTask())
        assert parent.enabled is False
        assert child.enabled is True
        assert child.schedule_type == "interval"
        assert child.interval_seconds == 60

    @pytest.mark.asyncio
    async def test_execute_short_circuits_when_disabled(self):
        """AUTO-FIRE GUARD condition (a): a disabled instance never runs the
        pipeline, even if execute() is invoked directly.

        Uses ``async def`` (pytest-asyncio ``asyncio_mode=auto``) rather than
        ``asyncio.run()`` — the latter closes the thread's default event loop and
        unsets it, breaking sibling tests that call ``asyncio.get_event_loop()``
        (the cross-test event-loop pollution class).
        """
        result = await ChannelPipelineTask().execute()
        assert result.success is True
        assert result.message == "Auto-creation task disabled"
        assert result.details == {}


class TestOtherTasksRemainEnabled:
    """Regression: the opt-in change must not disable other default tasks."""

    def test_cleanup_parent_seeds_enabled(self, test_session):
        assert CleanupTask.default_enabled is True
        parent, _ = _seed_task(test_session, CleanupTask())
        assert parent.enabled is True

    def test_stats_v2_rollup_parent_seeds_enabled(self, test_session):
        assert StatsV2RollupTask.default_enabled is True
        parent, _ = _seed_task(test_session, StatsV2RollupTask())
        assert parent.enabled is True

    def test_m3u_change_monitor_parent_seeds_enabled(self, test_session):
        assert M3UChangeMonitorTask.default_enabled is True
        parent, _ = _seed_task(test_session, M3UChangeMonitorTask())
        assert parent.enabled is True


class TestOptInFires:
    """Models task_engine's two-gate firing: a task fires only when the due child
    schedule is enabled AND the parent task is enabled."""

    def _would_fire(self, session, task_id, now):
        # Mirror task_engine._scheduler_loop: due child schedules ...
        due = session.query(TaskSchedule).filter(
            TaskSchedule.enabled == True,  # noqa: E712
            TaskSchedule.next_run_at != None,  # noqa: E711
            TaskSchedule.next_run_at <= now,
            TaskSchedule.task_id == task_id,
        ).all()
        if not due:
            return False
        # ... then the parent-enabled gate.
        parent = session.query(ScheduledTask).filter(
            ScheduledTask.task_id == task_id
        ).first()
        return bool(parent and parent.enabled)

    def test_disabled_task_does_not_fire_even_with_due_child(self, test_session):
        """Default state: parent disabled. Even with the child cadence enabled
        and due, the parent gate prevents firing."""
        parent, child = _seed_task(test_session, ChannelPipelineTask())
        child.next_run_at = datetime.utcnow() - timedelta(seconds=1)  # due
        test_session.commit()

        assert parent.enabled is False
        assert child.enabled is True  # cadence exists
        assert self._would_fire(test_session, "auto_creation", datetime.utcnow()) is False

    def test_operator_enable_parent_makes_it_fire(self, test_session):
        """Single-action opt-in: enabling the parent task (the UI toggle) with the
        child cadence already enabled makes the task fire on its 60s interval."""
        from schedule_calculator import calculate_next_run

        parent, child = _seed_task(test_session, ChannelPipelineTask())
        # Operator flips the task "Enabled" toggle -> parent enabled.
        parent.enabled = True
        # The schedule endpoint recomputes a valid next_run_at on enable.
        child.next_run_at = calculate_next_run(schedule_type="interval", interval_seconds=60)
        test_session.commit()
        assert child.next_run_at is not None

        # Make it due and confirm it would fire (both gates open).
        child.next_run_at = datetime.utcnow() - timedelta(seconds=1)
        test_session.commit()
        assert self._would_fire(test_session, "auto_creation", datetime.utcnow()) is True
