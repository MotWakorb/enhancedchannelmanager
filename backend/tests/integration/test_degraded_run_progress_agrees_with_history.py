"""The live progress surface and the scheduler log agree with the history row.

Bead ``enhancedchannelmanager-bdmby``, measured on published ``:dev``
0.18.1-0047 in backup/restore drill run ``2026-08-08-run16`` and reproduced
twice (the redacted-artifact round and the deliberate logo-failure round).

THE DEFECT
----------
A DBAS restore that ended ``outcome: completed_with_failures``, with ``failed: 0``
in every category and nothing rolled back, was described two different ways by
:mod:`task_scheduler` and three other ways by everything else::

    GET /api/tasks/dbas_restore -> progress.status = "failed" (failed_count: 0)
    task-history status / success = "completed_with_warnings" / True   <- correct
    details.outcome               = "completed_with_failures"          <- correct
    notification type             = "warning"                          <- correct
    restore dialog                = "nothing was rolled back"          <- correct

and the container log carried both halves of the disagreement at once::

    WARNING task_scheduler  [dbas_restore] Task failed: Restore completed_with_failures: …
    WARNING task_engine     [dbas_restore] Task completed in a degraded state: …

``progress.status`` is the field a script or an operator polls to know when a
restore has finished, and ``"Task failed"`` is a string
``docs/user_guide/troubleshooting/read-the-logs.md`` encourages operators to
grep for. Both said "failed" for a run that completed and kept its state.

THE FIX THIS FILE PINS
----------------------
The terminal live progress status comes from the SAME derivation as the
persisted history row — :func:`task_scheduler.execution_status`, one projection
of :func:`task_scheduler.task_outcome` — so the two cannot disagree by
construction. ``failed`` is reserved for the outcomes that genuinely failed
(``TaskOutcome.ERROR``: a rolled-back restore, an orchestration error, an
exception), exactly the rule the alert severity already follows (bead
``…-cwmid``).

Bead ``…-fexq1``'s history row is NOT touched and its assertions still hold;
this file covers the two surfaces fexq1 did not: the live ``TaskProgress`` and
the ``task_scheduler`` log line.

Conventions: ``docs/pytest_conventions.md``. The task shapes mirror
``tests/integration/test_degraded_run_history_agrees_with_alert.py`` so the two
files describe the same runs.
"""
from __future__ import annotations

import logging
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from task_scheduler import (
    ScheduleConfig,
    ScheduleType,
    TaskResult,
    TaskScheduler,
    execution_status,
)

SCHEDULER_LOGGER = "task_scheduler"


def _result(**overrides) -> TaskResult:
    now = datetime.utcnow()
    kwargs = dict(
        success=True,
        message="ok",
        started_at=now,
        completed_at=now,
        total_items=420,
        success_count=420,
        failed_count=0,
    )
    kwargs.update(overrides)
    return TaskResult(**kwargs)


class _DegradedNoFailedItemsTask(TaskScheduler):
    """Drill case A: the redacted restore. Nothing failed; nothing can play."""

    task_id = "test_bdmby_degraded_clean_counts"
    task_name = "Bdmby Degraded Clean Counts"
    task_description = "success=False, completed_degraded=True, failed_count=0."
    default_enabled = False

    async def execute(self) -> TaskResult:
        return _result(
            success=False,
            message=(
                "Restore completed_with_failures: created 423, failed 0 across 13 "
                "categories; 12 channel(s) have NO playable stream"
            ),
            completed_degraded=True,
        )


class _DegradedWithFailedItemsTask(TaskScheduler):
    """Drill case B: the logo-failure restore. One non-fatal row failed."""

    task_id = "test_bdmby_degraded_failed_row"
    task_name = "Bdmby Degraded Failed Row"
    task_description = "success=False, completed_degraded=True, failed_count=1."
    default_enabled = False

    async def execute(self) -> TaskResult:
        return _result(
            success=False,
            message=(
                "Restore completed_with_failures: created 47, failed 1 across 13 "
                "categories; 1 logo(s) could not be reinstated"
            ),
            success_count=47,
            failed_count=1,
            completed_degraded=True,
        )


class _PartialFailureTask(TaskScheduler):
    """A successful run with failed items — the pre-existing warning shape."""

    task_id = "test_bdmby_partial"
    task_name = "Bdmby Partial"
    task_description = "success=True with failed items."
    default_enabled = False

    async def execute(self) -> TaskResult:
        return _result(success_count=418, failed_count=2)


class _RolledBackTask(TaskScheduler):
    """A restore that genuinely failed and rolled back. Still a failure."""

    task_id = "test_bdmby_rolled_back"
    task_name = "Bdmby Rolled Back"
    task_description = "success=False, not degraded — partial_failed_rolled_back."
    default_enabled = False

    async def execute(self) -> TaskResult:
        return _result(
            success=False,
            message="Restore partial_failed_rolled_back: every change was reverted",
            error="Restore failed during orchestration",
            success_count=0,
            failed_count=3,
        )


class _RaisingTask(TaskScheduler):
    """An orchestration error — the exception path out of ``run()``."""

    task_id = "test_bdmby_raising"
    task_name = "Bdmby Raising"
    task_description = "execute() raises."
    default_enabled = False

    async def execute(self) -> TaskResult:
        raise RuntimeError("archive unreadable")


class _SuccessTask(TaskScheduler):
    task_id = "test_bdmby_success"
    task_name = "Bdmby Success"
    task_description = "Clean success."
    default_enabled = False

    async def execute(self) -> TaskResult:
        return _result(message="Restore success: created 420, failed 0")


class _CancelledTask(TaskScheduler):
    """Cancelled mid-run: the task observes the flag and returns what it did."""

    task_id = "test_bdmby_cancelled"
    task_name = "Bdmby Cancelled"
    task_description = "Cancelled mid-run."
    default_enabled = False

    async def execute(self) -> TaskResult:
        self._cancel_requested = True
        return _result(success=False, success_count=12, message="stopped early")


def _make(task_cls) -> TaskScheduler:
    return task_cls(ScheduleConfig(schedule_type=ScheduleType.MANUAL))


# ---------------------------------------------------------------------------
# 1. The live progress status — the surface the drill measured as "failed"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_cls", [_DegradedNoFailedItemsTask, _DegradedWithFailedItemsTask]
)
async def test_a_degraded_run_does_not_report_a_failed_progress_status(task_cls):
    """F1: ``completed_with_failures`` with nothing rolled back is not a failure."""
    task = _make(task_cls)

    await task.run()

    assert task.progress.status != "failed"
    assert task.progress.status == "completed_with_warnings"
    assert task.progress.to_dict()["status"] == "completed_with_warnings"


@pytest.mark.asyncio
@pytest.mark.parametrize("task_cls", [_RolledBackTask, _RaisingTask])
async def test_a_genuine_failure_still_reports_a_failed_progress_status(task_cls):
    """The control. A rolled-back restore and an orchestration error still fail.

    Flattening these to a non-failed status would be a far worse defect than the
    one this bead fixes.
    """
    task = _make(task_cls)

    await task.run()

    assert task.progress.status == "failed"


@pytest.mark.asyncio
async def test_a_clean_run_still_reports_completed():
    """Unchanged: ``outcome: success`` already reported ``completed``."""
    task = _make(_SuccessTask)

    await task.run()

    assert task.progress.status == "completed"


@pytest.mark.asyncio
async def test_a_partially_failed_run_reports_the_warning_status():
    """A successful run with failed items uses the history row's vocabulary.

    Its persisted row has said ``completed_with_warnings`` since fexq1; the live
    surface now says the same thing instead of rounding it to ``completed``.
    """
    task = _make(_PartialFailureTask)

    await task.run()

    assert task.progress.status == "completed_with_warnings"


@pytest.mark.asyncio
async def test_a_cancelled_run_reports_cancelled_not_failed():
    """A cancelled run stopped; it did not fail. It used to report ``failed``."""
    task = _make(_CancelledTask)

    await task.run()

    assert task.progress.status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_cls",
    [
        _DegradedNoFailedItemsTask,
        _DegradedWithFailedItemsTask,
        _PartialFailureTask,
        _RolledBackTask,
        _SuccessTask,
        _CancelledTask,
    ],
)
async def test_the_progress_status_is_the_history_rows_status(task_cls):
    """The invariant: one derivation, so the two surfaces cannot disagree."""
    task = _make(task_cls)

    result = await task.run()

    assert task.progress.status == execution_status(result)


# ---------------------------------------------------------------------------
# 2. The scheduler log line — what an operator greps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_cls", [_DegradedNoFailedItemsTask, _DegradedWithFailedItemsTask]
)
async def test_a_degraded_run_is_not_logged_as_a_failure(task_cls, caplog):
    """Grepping the log for "Task failed" must not hit a degraded restore."""
    task = _make(task_cls)

    with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
        await task.run()

    scheduler_lines = [
        r.getMessage() for r in caplog.records if r.name == SCHEDULER_LOGGER
    ]
    assert not any("Task failed" in line for line in scheduler_lines), scheduler_lines
    assert any(
        "Task completed with warnings" in line for line in scheduler_lines
    ), scheduler_lines
    # The shortfall is still named — the operator's only clue to what degraded.
    assert any("completed_with_failures" in line for line in scheduler_lines)


@pytest.mark.asyncio
async def test_a_genuine_failure_is_still_logged_as_a_failure(caplog):
    """The control: "Task failed" still means a run that failed."""
    task = _make(_RolledBackTask)

    with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
        await task.run()

    scheduler_lines = [
        r.getMessage() for r in caplog.records if r.name == SCHEDULER_LOGGER
    ]
    assert any("Task failed" in line for line in scheduler_lines), scheduler_lines


# ---------------------------------------------------------------------------
# 3. The finalized progress notification carries the same status
# ---------------------------------------------------------------------------


async def _finalized_progress_metadata(task_cls) -> dict:
    """Run a task with notification callbacks wired; return the final payload."""
    task = _make(task_cls)
    task._notification_id = 7
    update = AsyncMock()
    task._update_notification_callback = update

    await task.run()

    # The finalize call is the only one that re-types the notification; the
    # rate-limited in-flight updates never pass ``notification_type``.
    finals = [
        call.kwargs
        for call in update.await_args_list
        if "notification_type" in call.kwargs
    ]
    assert len(finals) == 1, update.await_args_list
    return finals[0]


@pytest.mark.asyncio
async def test_the_finalized_progress_notification_agrees_with_the_row():
    """The notification the scheduler re-types at the end says the same thing."""
    final = await _finalized_progress_metadata(_DegradedNoFailedItemsTask)

    assert final["notification_type"] == "warning"
    assert final["metadata"]["progress"]["status"] == "completed_with_warnings"


@pytest.mark.asyncio
async def test_the_finalized_progress_notification_still_marks_a_failure():
    """The control, on the same surface."""
    final = await _finalized_progress_metadata(_RolledBackTask)

    assert final["notification_type"] == "error"
    assert final["metadata"]["progress"]["status"] == "failed"
