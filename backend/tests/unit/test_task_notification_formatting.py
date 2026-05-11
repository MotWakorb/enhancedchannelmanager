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
