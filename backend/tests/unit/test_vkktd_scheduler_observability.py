"""Scheduler observability + effective-enabled correctness (epic vkktd).

Covers the BACKEND slices of the vkktd epic:

vkktd.1 — the task engine logs the previously-silent decision points:
          * parent-disabled skip  -> THROTTLED DEBUG (once per task per window)
          * schedule with no registered instance -> THROTTLED WARNING
          * ChannelPipelineTask "refresh pending but no run_on_refresh rule"
            -> THROTTLED INFO (not DEBUG-only)
          * the M3U-refresh watermark log no longer claims auto-creation
            "picks it up" when the task is gated OFF.
          Plus: no per-tick spam (the shared log_throttle helper).

vkktd.3 — enabling a task auto-reconciles a firing-capable child schedule so a
          single "Enabled" action actually makes the task run; MANUAL tasks are
          never reconciled; disabling never touches children.
"""
import logging
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

import log_throttle
from models import ScheduledTask, TaskSchedule
from task_scheduler import ScheduleType


@pytest.fixture(autouse=True)
def _reset_log_throttle():
    """The throttle state is module-global; reset around every test so an
    armed window in one test never suppresses an assertion in another."""
    log_throttle.reset()
    yield
    log_throttle.reset()


def _session_factory(test_engine):
    """A get_session() drop-in that yields a fresh session on the test engine
    each call (the engine code closes the session it receives)."""
    Local = sessionmaker(bind=test_engine, expire_on_commit=False)
    return lambda: Local()


def _seed_due_child(test_session, *, task_id, parent_enabled):
    test_session.add(ScheduledTask(
        task_id=task_id, task_name="T", enabled=parent_enabled,
        schedule_type="interval", interval_seconds=60,
    ))
    test_session.add(TaskSchedule(
        task_id=task_id, name="Default Schedule", enabled=True,
        schedule_type="interval", interval_seconds=60,
        next_run_at=datetime.utcnow() - timedelta(minutes=5),
    ))
    test_session.commit()


# ---------------------------------------------------------------------------
# log_throttle helper
# ---------------------------------------------------------------------------
def test_throttle_allows_first_then_suppresses_within_window():
    assert log_throttle.should_log("k", window=1000) is True
    assert log_throttle.should_log("k", window=1000) is False
    # Independent key throttles independently.
    assert log_throttle.should_log("other", window=1000) is True


def test_throttle_window_elapsed_allows_again():
    assert log_throttle.should_log("k", window=1000) is True
    with patch("log_throttle.time.monotonic", return_value=log_throttle.time.monotonic() + 5000):
        assert log_throttle.should_log("k", window=1000) is True


# ---------------------------------------------------------------------------
# vkktd.1 — task_engine parent-disabled skip
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_parent_disabled_skip_logs_throttled_debug(test_engine, test_session, caplog):
    from task_engine import TaskEngine
    _seed_due_child(test_session, task_id="auto_creation", parent_enabled=False)

    registry = MagicMock()
    registry.get_task_instance.return_value = MagicMock()  # instance IS registered

    engine = TaskEngine(check_interval=1)
    with caplog.at_level(logging.DEBUG, logger="task_engine"), \
         patch("database.get_session", side_effect=_session_factory(test_engine)), \
         patch("task_engine.get_registry", return_value=registry), \
         patch("task_engine.asyncio.create_task") as mock_create:
        await engine._check_and_run_due_tasks()

    mock_create.assert_not_called()  # gated off -> never fired
    msgs = [r.getMessage() for r in caplog.records]
    assert any("parent task is disabled" in m for m in msgs), msgs


@pytest.mark.asyncio
async def test_parent_disabled_skip_is_throttled_no_per_tick_spam(test_engine, test_session, caplog):
    from task_engine import TaskEngine
    _seed_due_child(test_session, task_id="auto_creation", parent_enabled=False)

    registry = MagicMock()
    registry.get_task_instance.return_value = MagicMock()
    engine = TaskEngine(check_interval=1)

    with caplog.at_level(logging.DEBUG, logger="task_engine"), \
         patch("database.get_session", side_effect=_session_factory(test_engine)), \
         patch("task_engine.get_registry", return_value=registry), \
         patch("task_engine.asyncio.create_task"):
        await engine._check_and_run_due_tasks()  # tick 1
        await engine._check_and_run_due_tasks()  # tick 2 (same throttle window)

    hits = sum("parent task is disabled" in r.getMessage() for r in caplog.records)
    assert hits == 1, "parent-disabled DEBUG must be throttled to one line per window, got %s" % hits


# ---------------------------------------------------------------------------
# vkktd.1 — task_engine schedule with no registered instance
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unregistered_schedule_logs_throttled_warning(test_engine, test_session, caplog):
    from task_engine import TaskEngine
    _seed_due_child(test_session, task_id="ghost_task", parent_enabled=True)

    registry = MagicMock()
    registry.get_task_instance.return_value = None  # NOT registered

    engine = TaskEngine(check_interval=1)
    with caplog.at_level(logging.WARNING, logger="task_engine"), \
         patch("database.get_session", side_effect=_session_factory(test_engine)), \
         patch("task_engine.get_registry", return_value=registry), \
         patch("task_engine.asyncio.create_task") as mock_create:
        await engine._check_and_run_due_tasks()  # tick 1
        await engine._check_and_run_due_tasks()  # tick 2

    mock_create.assert_not_called()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING
                and "no task instance is registered" in r.getMessage()]
    assert len(warnings) == 1, "unregistered WARNING must appear once (throttled), got %s" % len(warnings)


# ---------------------------------------------------------------------------
# vkktd.1 — ChannelPipelineTask "refresh pending but no rule" -> INFO throttled
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pipeline_no_rule_with_pending_refresh_logs_info_throttled(caplog):
    from tasks.channel_pipeline import ChannelPipelineTask

    settings = MagicMock(
        auto_creation_run_on_refresh_disabled=False,
        last_m3u_refresh_completed_at="2026-01-02T00:00:00+00:00",
        last_auto_creation_consumed_refresh_at="2026-01-01T00:00:00+00:00",
    )
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = []  # no rules

    with patch.dict(os.environ, {}, clear=False), \
         patch("tasks.channel_pipeline.get_settings", return_value=settings), \
         patch("tasks.channel_pipeline.save_settings"), \
         patch("database.get_session", return_value=session), \
         caplog.at_level(logging.INFO, logger="tasks.channel_pipeline"):
        os.environ.pop("ECM_DISABLE_RUN_ON_REFRESH", None)
        task = ChannelPipelineTask()
        task._enabled = True
        await task.execute()  # tick 1
        await task.execute()  # tick 2 (same window)

    info_hits = [r for r in caplog.records if r.levelno == logging.INFO
                 and "pending but NO enabled run_on_refresh" in r.getMessage()]
    assert len(info_hits) == 1, "INFO must surface once (throttled), got %s" % len(info_hits)


# ---------------------------------------------------------------------------
# vkktd.1 — watermark log annotation (router + task)
# ---------------------------------------------------------------------------
def test_m3u_router_watermark_log_annotates_when_disabled(caplog):
    from routers import m3u
    settings = MagicMock(last_m3u_refresh_completed_at="")
    with patch("routers.m3u.get_settings", return_value=settings), \
         patch("routers.m3u.save_settings"), \
         patch("task_registry.auto_creation_parent_enabled", return_value=False), \
         caplog.at_level(logging.INFO, logger="routers.m3u"):
        m3u._advance_refresh_watermark()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("DISABLED" in m and "will NOT pick this up" in m for m in msgs), msgs


def test_m3u_router_watermark_log_normal_when_enabled(caplog):
    from routers import m3u
    settings = MagicMock(last_m3u_refresh_completed_at="")
    with patch("routers.m3u.get_settings", return_value=settings), \
         patch("routers.m3u.save_settings"), \
         patch("task_registry.auto_creation_parent_enabled", return_value=True), \
         caplog.at_level(logging.INFO, logger="routers.m3u"):
        m3u._advance_refresh_watermark()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("picks it up on its next tick" in m for m in msgs), msgs
    assert not any("DISABLED" in m for m in msgs), msgs


def test_m3u_refresh_task_watermark_log_annotates_when_disabled(caplog):
    from tasks import m3u_refresh
    settings = MagicMock(last_m3u_refresh_completed_at="")
    with patch("tasks.m3u_refresh.get_settings", return_value=settings), \
         patch("tasks.m3u_refresh.save_settings"), \
         patch("task_registry.auto_creation_parent_enabled", return_value=False), \
         caplog.at_level(logging.INFO, logger="tasks.m3u_refresh"):
        m3u_refresh._advance_refresh_watermark()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("DISABLED" in m and "will NOT pick this up" in m for m in msgs), msgs


# ---------------------------------------------------------------------------
# vkktd.3 — enable() auto-reconciles a firing-capable child schedule
# ---------------------------------------------------------------------------
def _fake_instance(schedule_type=ScheduleType.INTERVAL):
    inst = MagicMock()
    inst.schedule_config.schedule_type = schedule_type
    return inst


def test_enable_reconciles_single_disabled_child(test_engine, test_session):
    from task_registry import TaskRegistry
    test_session.add(TaskSchedule(
        task_id="auto_creation", name="Default Schedule", enabled=False,
        schedule_type="interval", interval_seconds=60, next_run_at=None,
    ))
    test_session.commit()

    reg = TaskRegistry()
    with patch("task_registry.get_session", side_effect=_session_factory(test_engine)):
        reg._reconcile_child_schedule_on_enable("auto_creation", _fake_instance())

    verify = sessionmaker(bind=test_engine, expire_on_commit=False)()
    try:
        child = verify.query(TaskSchedule).filter(TaskSchedule.task_id == "auto_creation").one()
        assert child.enabled is True, "single disabled child must be enabled on task-enable"
        assert child.next_run_at is not None, "reconciled child must get a next_run so it fires"
    finally:
        verify.close()


def test_enable_reconciles_newest_when_multiple_disabled_children(test_engine, test_session):
    from task_registry import TaskRegistry
    old = TaskSchedule(
        task_id="auto_creation", name="Old", enabled=False,
        schedule_type="interval", interval_seconds=60,
        created_at=datetime.utcnow() - timedelta(days=2),
    )
    new = TaskSchedule(
        task_id="auto_creation", name="New", enabled=False,
        schedule_type="interval", interval_seconds=60,
        created_at=datetime.utcnow(),
    )
    test_session.add_all([old, new])
    test_session.commit()
    new_id = new.id

    reg = TaskRegistry()
    with patch("task_registry.get_session", side_effect=_session_factory(test_engine)):
        reg._reconcile_child_schedule_on_enable("auto_creation", _fake_instance())

    verify = sessionmaker(bind=test_engine, expire_on_commit=False)()
    try:
        rows = {s.name: s.enabled for s in verify.query(TaskSchedule).all()}
        assert rows == {"New": True, "Old": False}, rows
        newest = verify.query(TaskSchedule).filter(TaskSchedule.id == new_id).one()
        assert newest.enabled is True
    finally:
        verify.close()


def test_enable_does_not_touch_already_enabled_child(test_engine, test_session):
    from task_registry import TaskRegistry
    # One enabled + one disabled: operator intentionally disabled the second.
    test_session.add(TaskSchedule(
        task_id="auto_creation", name="A", enabled=True,
        schedule_type="interval", interval_seconds=60,
    ))
    test_session.add(TaskSchedule(
        task_id="auto_creation", name="B", enabled=False,
        schedule_type="interval", interval_seconds=60,
    ))
    test_session.commit()

    reg = TaskRegistry()
    with patch("task_registry.get_session", side_effect=_session_factory(test_engine)):
        reg._reconcile_child_schedule_on_enable("auto_creation", _fake_instance())

    verify = sessionmaker(bind=test_engine, expire_on_commit=False)()
    try:
        rows = {s.name: s.enabled for s in verify.query(TaskSchedule).all()}
        assert rows == {"A": True, "B": False}, "an intentionally-disabled child must be left alone"
    finally:
        verify.close()


def test_enable_manual_task_does_not_reconcile(test_engine, test_session):
    from task_registry import TaskRegistry
    test_session.add(TaskSchedule(
        task_id="manual_task", name="Default Schedule", enabled=False,
        schedule_type="interval", interval_seconds=60,
    ))
    test_session.commit()

    reg = TaskRegistry()
    with patch("task_registry.get_session", side_effect=_session_factory(test_engine)):
        reg._reconcile_child_schedule_on_enable(
            "manual_task", _fake_instance(schedule_type=ScheduleType.MANUAL)
        )

    verify = sessionmaker(bind=test_engine, expire_on_commit=False)()
    try:
        child = verify.query(TaskSchedule).filter(TaskSchedule.task_id == "manual_task").one()
        assert child.enabled is False, "MANUAL tasks must never be reconciled"
    finally:
        verify.close()
