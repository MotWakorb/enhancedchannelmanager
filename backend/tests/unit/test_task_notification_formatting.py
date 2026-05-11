"""Unit tests for scheduled-task alert message and metadata formatting."""
from datetime import datetime

from task_engine import _success_task_completion_message, _task_execution_metadata_extra
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
