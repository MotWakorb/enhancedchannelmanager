"""Unit tests for scheduled-task alert message and metadata formatting."""
from datetime import datetime

from task_engine import (
    _success_task_completion_message,
    _task_execution_metadata_extra,
    _warning_task_completion_message,
)
from task_scheduler import TaskResult


def test_auto_creation_metadata_uses_pipeline_fields_not_generic_items():
    started = datetime(2026, 5, 10, 12, 0, 0)
    completed = datetime(2026, 5, 10, 12, 0, 45)
    result = TaskResult(
        success=True,
        message=(
            "Executed auto-creation pipeline: 27304 streams evaluated, 1656 matched, "
            "0 channels created, 25 updated, 0 groups created"
        ),
        started_at=started,
        completed_at=completed,
        total_items=27304,
        success_count=25,
        details={
            "streams_evaluated": 27304,
            "streams_matched": 1656,
            "channels_created": 0,
            "channels_updated": 25,
            "groups_created": 0,
            "conflicts": 0,
            "mode": "execute",
            "execution_id": 42,
        },
    )
    meta = _task_execution_metadata_extra("auto_creation", result)
    assert meta["streams_evaluated"] == 27304
    assert meta["streams_matched"] == 1656
    assert meta["channels_updated"] == 25
    assert "total_items" not in meta
    assert "success_count" not in meta

    msg = _success_task_completion_message("auto_creation", result)
    assert "27304 streams evaluated" in msg
    assert "45.0s" in msg


def test_stream_probe_metadata_includes_failed_count_for_probe_failures_threshold():
    """alert_methods.send_alert uses failed_count for min_failures; keep legacy key with streams_*."""
    started = datetime(2026, 5, 10, 12, 0, 0)
    completed = datetime(2026, 5, 10, 12, 0, 10)
    result = TaskResult(
        success=True,
        message="legacy",
        started_at=started,
        completed_at=completed,
        total_items=100,
        success_count=90,
        failed_count=7,
        skipped_count=3,
        details={"black_screen_count": 0, "low_fps_count": 0},
    )
    meta = _task_execution_metadata_extra("stream_probe", result)
    assert meta["failed_count"] == 7
    assert meta["streams_failed"] == 7


def test_auto_creation_success_message_without_details_uses_result_message():
    """Empty details must not fall back to generic 'N items processed' with misleading totals."""
    started = datetime(2026, 5, 10, 12, 0, 0)
    completed = datetime(2026, 5, 10, 12, 0, 1)
    result = TaskResult(
        success=True,
        message="No enabled auto-creation rules to process",
        started_at=started,
        completed_at=completed,
        total_items=0,
        details={},
    )
    msg = _success_task_completion_message("auto_creation", result)
    assert "No enabled auto-creation rules" in msg
    assert "items processed" not in msg


def test_stream_probe_success_message_includes_totals_and_quality_flags():
    started = datetime(2026, 5, 10, 12, 0, 0)
    completed = datetime(2026, 5, 10, 12, 0, 10)
    result = TaskResult(
        success=True,
        message="legacy",
        started_at=started,
        completed_at=completed,
        total_items=100,
        success_count=90,
        failed_count=3,
        skipped_count=7,
        details={"black_screen_count": 2, "low_fps_count": 1},
    )
    meta = _task_execution_metadata_extra("stream_probe", result)
    assert meta["streams_scheduled"] == 100
    assert meta["streams_ok"] == 90
    assert meta["black_screen_detections"] == 2

    msg = _success_task_completion_message("stream_probe", result)
    assert "100 stream(s)" in msg
    assert "90 ok" in msg
    assert "black screen" in msg


# ---------------------------------------------------------------------------
# zt3kf — dbas_backup "Completed with Warnings" message names the degraded
# Dispatcharr category/categories rather than the generic "N items" phrasing.
# ---------------------------------------------------------------------------


def _backup_result(degraded=(), **overrides):
    started = datetime(2026, 8, 3, 3, 0, 0)
    completed = datetime(2026, 8, 3, 3, 0, 5)
    details = {"filename": "ecm-backup-20260803_030000.zip"}
    if degraded:
        details["degraded_categories"] = list(degraded)
    fields = dict(
        success=True,
        message="Built DBAS backup ecm-backup-20260803_030000.zip (schema v1, 12 files)",
        started_at=started,
        completed_at=completed,
        total_items=1,
        success_count=0 if degraded else 1,
        failed_count=1 if degraded else 0,
        details=details,
    )
    fields.update(overrides)
    return TaskResult(**fields)


def test_dbas_backup_warning_message_names_single_degraded_category():
    result = _backup_result(degraded=["dvr_rules"])
    msg = _warning_task_completion_message("dbas_backup", result)
    assert "dvr_rules" in msg
    assert "1 Dispatcharr category" in msg
    assert "is degraded" in msg
    # Never the generic count-only phrasing for this task_id.
    assert "out of" not in msg


def test_dbas_backup_warning_message_names_multiple_degraded_categories():
    result = _backup_result(degraded=["dvr_rules", "core_settings"])
    msg = _warning_task_completion_message("dbas_backup", result)
    assert "dvr_rules" in msg
    assert "core_settings" in msg
    assert "2 Dispatcharr categories" in msg
    assert "are degraded" in msg


def test_dbas_backup_warning_message_falls_back_to_generic_without_degraded_list():
    """failed_count>0 with no degraded_categories detail (shouldn't normally
    happen, but the fallback must not crash or fabricate a category name)."""
    result = _backup_result(degraded=[], failed_count=1, success_count=0)
    msg = _warning_task_completion_message("dbas_backup", result)
    assert "out of" in msg  # generic fallback phrasing
    assert "Dispatcharr categor" not in msg


def test_stream_probe_warning_message_unaffected_by_dbas_backup_branch():
    """Regression guard: adding the dbas_backup branch must not change the
    pre-existing stream_probe warning phrasing."""
    started = datetime(2026, 5, 10, 12, 0, 0)
    completed = datetime(2026, 5, 10, 12, 0, 10)
    result = TaskResult(
        success=True,
        message="legacy",
        started_at=started,
        completed_at=completed,
        total_items=100,
        success_count=90,
        failed_count=7,
        skipped_count=3,
    )
    msg = _warning_task_completion_message("stream_probe", result)
    assert "100 streams" in msg
    assert "90 ok" in msg
    assert "7 failures" in msg


def test_generic_task_warning_message_unchanged():
    result = TaskResult(
        success=True,
        message="legacy",
        total_items=10,
        success_count=8,
        failed_count=2,
    )
    msg = _warning_task_completion_message("some_other_task", result)
    assert "Completed with 2 failures out of 10 items" in msg
    assert "8 succeeded" in msg


def test_dbas_backup_success_message_names_the_unit_it_is_counting():
    """Bead …-fexq1. The counts are CATEGORIES; a bare "N items" hides that.

    Before the counts meant items, a clean backup's completion notification
    read "Successfully completed. 1 items processed in 1.8s" — the `1` being a
    boolean in disguise, and the unit invisible either way. This task's WARNING
    message already names categories; this is its clean-run counterpart.
    """
    started = datetime(2026, 8, 6, 12, 0, 0)
    completed = datetime(2026, 8, 6, 12, 0, 2)
    result = TaskResult(
        success=True,
        message="Built DBAS backup ecm-backup-2026-08-06_120000.zip (schema v6, 41 files)",
        started_at=started,
        completed_at=completed,
        total_items=16,
        success_count=16,
        failed_count=0,
        details={"filename": "ecm-backup-2026-08-06_120000.zip"},
    )
    msg = _success_task_completion_message("dbas_backup", result)
    assert "16 categories" in msg
    assert "items processed" not in msg
    assert "2.0s" in msg


def test_dbas_backup_success_message_uses_the_singular_for_one_category():
    started = datetime(2026, 8, 6, 12, 0, 0)
    completed = datetime(2026, 8, 6, 12, 0, 1)
    result = TaskResult(
        success=True,
        message="Built DBAS backup",
        started_at=started,
        completed_at=completed,
        total_items=1,
        success_count=1,
        failed_count=0,
    )
    msg = _success_task_completion_message("dbas_backup", result)
    assert "1 category" in msg
    assert "categories" not in msg
