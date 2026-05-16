"""
Regression tests for ``CleanupTask`` default schedule (bd-ygoqr).

bd-ygoqr (follow-up to bd-f9gd8 DBA spike) flipped the in-code default for a
freshly-constructed ``CleanupTask`` from ``ScheduleType.MANUAL`` to
``ScheduleType.CRON`` (``"0 2 * * 0"`` — Sunday 02:00 UTC). The flip targets
fresh installs only: long-running operators who already persisted a
``ScheduleConfig`` (in the ``ScheduledTask`` DB row) must keep their explicit
choice — including operators who deliberately left the cleanup task on
MANUAL.

The contract these tests lock:

1. ``CleanupTask()`` with no args → CRON / "0 2 * * 0".
2. ``CleanupTask(ScheduleConfig(schedule_type=MANUAL))`` → MANUAL preserved
   (proves the constructor doesn't clobber an explicit operator choice).
3. ``CleanupTask(ScheduleConfig(schedule_type=INTERVAL, ...))`` → INTERVAL
   preserved (any explicit schedule wins over the default).
4. ``task_registry.sync_from_database`` rehydrates an operator-saved MANUAL
   config without falling through to the new CRON default — the load path
   from the DB is what guards bd-ygoqr's "don't clobber existing operators"
   promise, not the constructor.
"""
import pytest

from task_scheduler import ScheduleConfig, ScheduleType
from tasks.cleanup import CleanupTask


class TestCleanupTaskDefaultSchedule:
    def test_fresh_install_defaults_to_cron_sunday_0200_utc(self):
        """No persisted schedule_config → constructor's CRON default applies."""
        task = CleanupTask()

        assert task.schedule_config.schedule_type == ScheduleType.CRON
        assert task.schedule_config.cron_expression == "0 2 * * 0"

    def test_explicit_manual_config_is_preserved(self):
        """An operator who set MANUAL must keep MANUAL after re-instantiation."""
        operator_config = ScheduleConfig(
            schedule_type=ScheduleType.MANUAL,
            cron_expression="",
        )

        task = CleanupTask(operator_config)

        assert task.schedule_config.schedule_type == ScheduleType.MANUAL
        # Constructor must not silently rewrite the cron_expression either.
        assert task.schedule_config.cron_expression == ""

    def test_explicit_interval_config_is_preserved(self):
        """Operator-chosen INTERVAL schedule wins over the new CRON default."""
        operator_config = ScheduleConfig(
            schedule_type=ScheduleType.INTERVAL,
            interval_seconds=3600,
        )

        task = CleanupTask(operator_config)

        assert task.schedule_config.schedule_type == ScheduleType.INTERVAL
        assert task.schedule_config.interval_seconds == 3600

    def test_explicit_cron_config_uses_operator_cron_expression(self):
        """Operator-chosen CRON expression wins over the default."""
        operator_config = ScheduleConfig(
            schedule_type=ScheduleType.CRON,
            cron_expression="0 5 * * *",  # Daily 05:00, not Sunday 02:00
        )

        task = CleanupTask(operator_config)

        assert task.schedule_config.schedule_type == ScheduleType.CRON
        assert task.schedule_config.cron_expression == "0 5 * * *"


class TestCleanupTaskRegistrySyncPreservesOperatorChoice:
    """The load path is the actual guard for the 'don't clobber' promise.

    ``TaskRegistry.sync_from_database`` (see ``task_registry.py``) reads the
    persisted ``ScheduledTask`` row and constructs a fresh ``ScheduleConfig``
    from those columns, then passes it as the ``schedule_config`` argument to
    the task class — which means an operator who saved MANUAL in v0.17.0-0037
    or earlier will hit the constructor with that MANUAL config and the
    constructor's default branch never fires.
    """

    def test_db_persisted_manual_config_does_not_fall_through_to_default(
        self, test_session, monkeypatch
    ):
        from tests.fixtures.factories import create_scheduled_task
        from task_registry import TaskRegistry

        monkeypatch.setattr("task_registry.get_session", lambda: test_session)

        # Simulate a v0.17.0-0037 operator who left cleanup on MANUAL.
        create_scheduled_task(
            test_session,
            task_id="cleanup",
            task_name="Database Cleanup",
            description="x",
            enabled=True,
            schedule_type="manual",
            cron_expression=None,
        )

        reg = TaskRegistry()
        reg.register(CleanupTask)
        reg.sync_from_database()

        # Registry must have hydrated the instance from the DB row, NOT from
        # the constructor's bd-ygoqr CRON default.
        instance = reg.get_task_instance("cleanup")
        assert instance is not None
        assert instance.schedule_config.schedule_type == ScheduleType.MANUAL

    def test_db_persisted_cron_config_round_trips(
        self, test_session, monkeypatch
    ):
        """An operator-customized CRON expression survives sync_from_database."""
        from tests.fixtures.factories import create_scheduled_task
        from task_registry import TaskRegistry

        monkeypatch.setattr("task_registry.get_session", lambda: test_session)

        create_scheduled_task(
            test_session,
            task_id="cleanup",
            task_name="Database Cleanup",
            description="x",
            enabled=True,
            schedule_type="cron",
            cron_expression="30 4 * * 1",  # Mondays 04:30, not Sun 02:00
        )

        reg = TaskRegistry()
        reg.register(CleanupTask)
        reg.sync_from_database()

        instance = reg.get_task_instance("cleanup")
        assert instance is not None
        assert instance.schedule_config.schedule_type == ScheduleType.CRON
        assert instance.schedule_config.cron_expression == "30 4 * * 1"
