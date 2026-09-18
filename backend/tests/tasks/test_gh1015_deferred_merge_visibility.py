"""GH #1015: a run stopped by the pending-merges queue must say so.

The live incident: every slot in a group rolled over, every stream was
queued as a pending merge, and the post-refresh task reported

    Auto-creation after M3U refresh: 26 streams evaluated, 26 matched,
    0 channels created, 0 updated; 24 actions failed

— which is also exactly what "the provider had nothing today" looks like.
The operator cannot tell the two apart, and nothing in the message names
the queue rows they would have to resolve to get the group moving again.

These tests pin the summary contract, not the notification plumbing: the
task summary is what reaches the operator on BOTH the clean and the
failed-action path, so it is the surface the deferral must appear on.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from task_scheduler import TaskResult
from tasks.channel_pipeline import ChannelPipelineTask


def _pipeline_result(**overrides) -> dict:
    result = {
        "success": True,
        "status": "completed",
        "execution_id": 7,
        "streams_evaluated": 26,
        "streams_matched": 26,
        "channels_created": 0,
        "channels_updated": 0,
        "groups_created": 0,
        "streams_merged": 0,
        "pending_merges_added": 0,
        "pending_merge_ids": [],
        "conflicts": [],
        "failed_action_count": 0,
    }
    result.update(overrides)
    return result


async def _run_post_refresh(pipeline_result: dict) -> TaskResult:
    engine = AsyncMock()
    engine.run_pipeline.return_value = pipeline_result
    task = ChannelPipelineTask()

    with patch(
        "channel_pipeline_engine.get_channel_pipeline_engine",
        return_value=engine,
    ), patch(
        "tasks.channel_pipeline.get_client",
        return_value=MagicMock(),
    ), patch(
        "services.notification_service.create_notification_internal",
        new=AsyncMock(),
    ):
        return await task._run_post_refresh_pipeline(
            [1], ["Event Slot Rules"], datetime.utcnow()
        )


@pytest.mark.asyncio
async def test_deferred_streams_are_named_in_the_summary():
    result = await _run_post_refresh(
        _pipeline_result(pending_merges_added=3, pending_merge_ids=[53, 54, 55])
    )

    assert "0 channels created" in result.message
    assert "3 streams deferred by pending merges" in result.message
    # The rows the operator has to resolve, not just a count.
    assert "53, 54, 55" in result.message
    assert result.details["pending_merge_ids"] == [53, 54, 55]


@pytest.mark.asyncio
async def test_deferral_is_reported_on_a_failed_action_run_too():
    # The frozen-group signature: created nothing AND failed the follow-on
    # actions. This is the run shape from the incident, and it is the one
    # where the deferral is the CAUSE of the failures reported next to it.
    result = await _run_post_refresh(
        _pipeline_result(
            status="completed_with_errors",
            success=False,
            failed_action_count=24,
            pending_merges_added=13,
            pending_merge_ids=list(range(53, 66)),
        )
    )

    assert "13 streams deferred by pending merges" in result.message
    assert "24 actions failed" in result.message
    # A long list is truncated rather than dumped whole: the first ten ids
    # in full, then a count of the rest.
    assert "53, 54, 55, 56, 57, 58, 59, 60, 61, 62" in result.message
    assert "63" not in result.message
    assert "+3 more" in result.message


@pytest.mark.asyncio
async def test_quiet_run_says_nothing_about_deferrals():
    # The other half of the contract: a run that genuinely had nothing to
    # do must not acquire deferral noise it cannot justify.
    result = await _run_post_refresh(_pipeline_result())

    assert "deferred" not in result.message
    assert result.details["pending_merge_ids"] == []


# --- PR #1016 review items 3 and 4 ------------------------------------------


@pytest.mark.asyncio
async def test_units_are_distinct_in_the_summary_and_details():
    result = await _run_post_refresh(
        _pipeline_result(
            pending_merges_added=2, pending_merge_stream_count=1, pending_merge_ids=[53],
        )
    )
    assert "1 stream deferred by pending merges" in result.message
    assert "2 deferred create actions" in result.message
    assert "rows: 53" in result.message
    assert result.details["pending_merge_stream_count"] == 1
    assert result.details["pending_merge_ids"] == [53]
    assert result.details["deferred_note"].startswith("1 stream deferred")


@pytest.mark.asyncio
async def test_the_emitted_warning_carries_the_deferral_and_rows():
    """Item 3: the single 'Completed with Warnings' notification the task
    engine emits for a failed-action run must name the cause and the rows,
    not only the generic counts. Crosses task result -> task-engine payload."""
    from task_engine import _task_execution_metadata_extra, _warning_task_completion_message
    from tasks.channel_pipeline import ChannelPipelineTask

    result = await _run_post_refresh(
        _pipeline_result(
            status="completed_with_errors", success=False, failed_action_count=1,
            streams_evaluated=1, streams_matched=1,
            pending_merges_added=1, pending_merge_stream_count=1, pending_merge_ids=[7],
        )
    )
    assert result.failed_count == 1
    message = _warning_task_completion_message(ChannelPipelineTask.task_id, result)
    assert message.startswith("Completed with 1 failures out of 1 items.")
    assert "1 stream deferred by pending merges" in message
    assert "rows: 7" in message
    assert "Pending Merges" in message

    metadata = _task_execution_metadata_extra(ChannelPipelineTask.task_id, result)
    assert metadata["pending_merge_ids"] == [7]
    assert metadata["pending_merge_stream_count"] == 1
    assert metadata["pending_merges_added"] == 1


@pytest.mark.asyncio
async def test_a_run_without_deferrals_keeps_the_generic_warning():
    from task_engine import _task_execution_metadata_extra, _warning_task_completion_message
    from tasks.channel_pipeline import ChannelPipelineTask

    result = await _run_post_refresh(
        _pipeline_result(status="completed_with_errors", success=False, failed_action_count=2)
    )
    message = _warning_task_completion_message(ChannelPipelineTask.task_id, result)
    assert "deferred" not in message
    assert "pending_merge_ids" not in _task_execution_metadata_extra(ChannelPipelineTask.task_id, result)
