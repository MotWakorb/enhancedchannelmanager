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
5. ``CleanupTask.execute`` runs VACUUM successfully against a real
   file-backed SQLite engine — proves the SQLAlchemy 2.0 implicit-transaction
   hazard is avoided (post-bd-ygoqr-polish: VACUUM moved off
   ``session.execute`` to a raw ``engine.connect()`` context).
"""
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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


class TestCleanupTaskVacuumExecutesAgainstRealEngine:
    """Lock the post-bd-ygoqr-polish VACUUM contract.

    Background: pre-polish, ``CleanupTask.execute`` ran VACUUM via
    ``session.execute(text("VACUUM"))`` after a ``session.commit()``.
    SQLAlchemy 2.0 sessions use implicit transactions — the next
    ``session.execute`` after a commit opens a NEW transaction, and SQLite
    raises ``OperationalError: cannot VACUUM from within a transaction``.
    The pre-polish hazard was masked everywhere by tests setting
    ``task.vacuum_db = False`` (search for that string in
    ``test_journal_indexes_and_retention.py``); production weekly cron runs
    silently appended ``Vacuum: cannot VACUUM from within a transaction``
    to the task error list. Bundle F's MANUAL→CRON flip made that latent
    bug auto-run weekly for every fresh install — i.e. raised the risk
    profile high enough that the polish round had to fix it.

    The polish moves VACUUM off ``session.execute`` and onto a raw
    ``with session.bind.connect() as conn: conn.execute(text("VACUUM"))``
    block — matching the established ``database._perform_maintenance``
    pattern. This test exercises the real path on a file-backed SQLite
    DB. ``vacuum_db`` is intentionally NOT disabled (the whole point).
    Assertion is "no OperationalError raised" — VACUUM space-reclamation
    is a SQLite implementation detail and not what we lock.

    File-backed SQLite (vs ``:memory:``) is required because the test must
    expose a real ``engine.connect()`` context; the in-memory fixtures use
    StaticPool against a memory URI which behaves differently for VACUUM
    transaction scoping.
    """

    @pytest.mark.asyncio
    async def test_vacuum_completes_without_operational_error(self, tmp_path):
        from models import Base

        db_file = tmp_path / "vacuum_test.db"
        engine = create_engine(
            f"sqlite:///{db_file}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()

        task = CleanupTask()
        # Do NOT disable VACUUM — that was the pre-polish workaround that
        # hid the bug. The whole point of this test is to exercise the
        # real VACUUM path against a real engine.
        assert task.vacuum_db is True, (
            "regression sentinel: this test must NOT disable vacuum_db; "
            "the pre-polish bug was masked by every cleanup test setting "
            "task.vacuum_db = False"
        )

        with patch("tasks.cleanup.get_session", return_value=session):
            result = await task.execute()

        # VACUUM must NOT have failed. The errors list is the only
        # surface where the old ``cannot VACUUM from within a transaction``
        # would land (CleanupTask.execute swallows the exception into
        # ``errors.append(f"Vacuum: {str(e)}")``).
        errors = result.details.get("errors", []) if result.details else []
        vacuum_errors = [e for e in errors if "Vacuum" in e or "VACUUM" in e]
        assert not vacuum_errors, (
            f"VACUUM raised an error — the implicit-transaction regression "
            f"is back. Errors: {vacuum_errors}"
        )

        # Positive contract: the deleted-counts dict records vacuum=completed.
        deleted = result.details.get("deleted", {}) if result.details else {}
        assert deleted.get("vacuum") == "completed", (
            f"VACUUM should have run to completion; deleted dict was "
            f"{deleted}"
        )
