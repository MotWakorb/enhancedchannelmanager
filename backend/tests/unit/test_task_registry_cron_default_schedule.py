"""Tests for ``TaskRegistry._create_default_task_schedule`` CRON translation (bd-1weac).

The bug bd-1weac fixes — bd-p5b8i headline:

``_create_default_task_schedule`` (added v0.8.7-0023, 2026-01-29) was written
when every task in the registry had either ``ScheduleType.INTERVAL`` or
``ScheduleType.MANUAL`` defaults. The else branch silently assumed the
schedule_type was "interval-with-no-time" and wrote
``schedule_type='interval', interval_seconds=0`` into ``task_schedules``.
Then ``calculate_next_run`` (in ``schedule_calculator``) takes one look at
``interval_seconds <= 0`` and returns ``None`` — and ``task_engine`` filters
``WHERE next_run_at IS NOT NULL``, so the row exists but never fires.

bd-ygoqr (v0.17.0-0039, PR #289) flipped ``CleanupTask`` default from MANUAL
to CRON ``0 2 * * 0``. bd-1weac (this fix) plus an ever-growing list of
CRON-default tasks (``stats_v2_rollup`` at ``30 3 * * *`` — daily 03:30 UTC,
added bd-7i2vv) means every fresh install since bd-ygoqr has been silently
not running cleanup, and every install since stats_v2_rollup landed has
been silently not running the rollup either.

The fix: when ``instance.schedule_config.schedule_type == ScheduleType.CRON``,
delegate to the existing ``database._convert_cron_to_schedule`` helper to
translate the cron expression into proper ``task_schedules`` columns
(``schedule_type='weekly'|'daily'|'monthly'|'interval'``, plus ``schedule_time``,
``days_of_week``, ``day_of_month``, ``interval_seconds``), then call
``calculate_next_run`` with the translated fields. We reuse the helper rather
than re-implementing cron parsing because it's already battle-tested by the
v0.8.7 ``_migrate_task_schedules`` migration that did exactly this for the
``scheduled_tasks → task_schedules`` migration.

Five behavioral shapes lock the contract:

1. **CRON weekly translation**: CleanupTask CRON ``0 2 * * 0`` produces a
   ``task_schedules`` row at ``schedule_type='weekly'``, ``days_of_week='0'``,
   ``schedule_time='02:00'``, with ``next_run_at`` populated (NOT NULL).
2. **CRON daily translation**: StatsV2RollupTask CRON ``30 3 * * *`` produces
   a row at ``schedule_type='daily'``, ``schedule_time='03:30'``, with
   ``next_run_at`` populated.
3. **INTERVAL passthrough is unchanged**: M3UChangeMonitor INTERVAL/300 still
   produces ``schedule_type='interval'``, ``interval_seconds=300``, with
   ``next_run_at`` populated. (Regression guard — the fix must not break the
   path the bug DIDN'T affect.)
4. **No-op when a row already exists**: existing task_schedules row for the
   task is left alone (idempotency — this is the same contract the original
   v0.8.7 code carried; the fix preserves it).
5. **next_run_at is NOT NULL**: the load-bearing assertion for every CRON
   task. Pre-fix, this column was always NULL for CRON-default tasks, which
   is exactly the symptom ``task_engine`` filters on.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models import Base, TaskSchedule
from task_registry import TaskRegistry
from task_scheduler import ScheduleConfig, ScheduleType
from tasks.cleanup import CleanupTask
from tasks.m3u_change_monitor import M3UChangeMonitorTask
from tasks.stats_v2_rollup import StatsV2RollupTask


@pytest.fixture
def file_session(tmp_path):
    """File-backed SQLite session (real engine, full schema)."""
    db_file = tmp_path / "task_registry_cron.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class TestCreateDefaultTaskScheduleCronTranslation:
    """The five behavioral shapes for bd-1weac's _create_default_task_schedule fix."""

    def test_cron_weekly_translates_for_cleanup_task_sunday_0200(self, file_session):
        """CleanupTask CRON ``0 2 * * 0`` → weekly Sunday 02:00 with next_run_at set."""
        registry = TaskRegistry()
        # Fresh CleanupTask instance carries the bd-ygoqr CRON default.
        instance = CleanupTask()
        assert instance.schedule_config.schedule_type == ScheduleType.CRON, (
            "fixture sanity: CleanupTask() must default to CRON post-bd-ygoqr"
        )
        assert instance.schedule_config.cron_expression == "0 2 * * 0"

        registry._create_default_task_schedule(file_session, instance)
        file_session.commit()

        row = file_session.query(TaskSchedule).filter_by(task_id="cleanup").first()
        assert row is not None, "expected a task_schedules row to be created"
        assert row.schedule_type == "weekly", (
            f"cron '0 2 * * 0' should translate to weekly, got {row.schedule_type!r}"
        )
        assert row.days_of_week == "0", (
            f"Sunday should be encoded as '0', got {row.days_of_week!r}"
        )
        assert row.schedule_time == "02:00", (
            f"02:00 schedule_time expected, got {row.schedule_time!r}"
        )
        assert row.timezone == "UTC"
        assert row.next_run_at is not None, (
            "load-bearing: next_run_at must be populated, else task_engine "
            "(which filters WHERE next_run_at IS NOT NULL) will never fire "
            "this task — exactly the bd-p5b8i symptom"
        )

    def test_cron_daily_translates_for_stats_v2_rollup_03_30(self, file_session):
        """StatsV2RollupTask CRON ``30 3 * * *`` → daily 03:30 with next_run_at set."""
        registry = TaskRegistry()
        instance = StatsV2RollupTask()
        assert instance.schedule_config.schedule_type == ScheduleType.CRON, (
            "fixture sanity: StatsV2RollupTask() must default to CRON"
        )
        assert instance.schedule_config.cron_expression == "30 3 * * *"

        registry._create_default_task_schedule(file_session, instance)
        file_session.commit()

        row = file_session.query(TaskSchedule).filter_by(task_id="stats_v2_rollup").first()
        assert row is not None
        assert row.schedule_type == "daily", (
            f"cron '30 3 * * *' should translate to daily, got {row.schedule_type!r}"
        )
        assert row.schedule_time == "03:30", (
            f"03:30 schedule_time expected, got {row.schedule_time!r}"
        )
        assert row.days_of_week is None, (
            "daily schedule should not carry days_of_week"
        )
        assert row.next_run_at is not None, (
            "load-bearing: next_run_at must be populated"
        )

    def test_interval_passthrough_unchanged_for_m3u_change_monitor(self, file_session):
        """M3UChangeMonitorTask INTERVAL/300 still works (the path the bug didn't touch).

        Regression guard: the bd-1weac fix only adds a CRON branch; the
        INTERVAL passthrough is the established v0.8.7-0023 happy path and
        must not change. This catches a future refactor that accidentally
        rewires the interval branch.
        """
        registry = TaskRegistry()
        instance = M3UChangeMonitorTask()
        assert instance.schedule_config.schedule_type == ScheduleType.INTERVAL
        assert instance.schedule_config.interval_seconds == 300

        registry._create_default_task_schedule(file_session, instance)
        file_session.commit()

        row = file_session.query(TaskSchedule).filter_by(task_id="m3u_change_monitor").first()
        assert row is not None
        assert row.schedule_type == "interval"
        assert row.interval_seconds == 300
        assert row.schedule_time is None
        assert row.next_run_at is not None, (
            "interval task next_run_at was always populated — guard the no-regression"
        )

    def test_noop_when_row_already_exists(self, file_session):
        """Idempotency: existing task_schedules row is preserved (v0.8.7-0023 contract).

        The original code returns early when a row already exists; the
        bd-1weac fix preserves that. If a user customised the schedule via
        the UI (which writes into task_schedules), a subsequent
        sync_from_database call must NOT overwrite it.
        """
        # Seed an operator-customised row.
        seeded = TaskSchedule(
            task_id="cleanup",
            name="Operator Custom",
            enabled=True,
            schedule_type="daily",
            schedule_time="06:30",
            timezone="America/New_York",
        )
        file_session.add(seeded)
        file_session.commit()

        registry = TaskRegistry()
        instance = CleanupTask()
        registry._create_default_task_schedule(file_session, instance)
        file_session.commit()

        rows = file_session.query(TaskSchedule).filter_by(task_id="cleanup").all()
        assert len(rows) == 1, "must not insert a second row for the same task_id"
        assert rows[0].name == "Operator Custom"
        assert rows[0].schedule_type == "daily"
        assert rows[0].schedule_time == "06:30"
        assert rows[0].timezone == "America/New_York"

    def test_pre_fix_symptom_does_not_recur_no_interval_0_no_null_next_run(
        self, file_session
    ):
        """Direct regression guard against the bd-p5b8i symptom.

        Pre-fix, a CRON-default task hitting _create_default_task_schedule
        wrote ``schedule_type='interval', interval_seconds=0, next_run_at=NULL``.
        This test asserts: for a CRON-default task, the row is NOT
        ``interval/0`` AND ``next_run_at`` is NOT NULL.
        """
        registry = TaskRegistry()
        instance = CleanupTask()  # bd-ygoqr CRON default
        registry._create_default_task_schedule(file_session, instance)
        file_session.commit()

        row = file_session.query(TaskSchedule).filter_by(task_id="cleanup").first()
        assert row is not None

        # Negative: the pre-fix shape.
        assert not (row.schedule_type == "interval" and row.interval_seconds == 0), (
            "REGRESSION: CRON-default task got the pre-fix interval/0 shape — "
            "bd-p5b8i is back. schedule_type=%r interval_seconds=%r"
            % (row.schedule_type, row.interval_seconds)
        )
        assert row.next_run_at is not None, (
            "REGRESSION: next_run_at is NULL on a CRON-default task — "
            "task_engine.check_and_run_tasks will silently skip this row "
            "forever (bd-p5b8i symptom)"
        )
