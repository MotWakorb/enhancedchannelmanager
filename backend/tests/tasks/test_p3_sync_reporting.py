"""Real mapper/importer seams: mode, mutations and unsupported predictions."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from dbas.importers.channels import import_channels
from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    RestoreOutcome,
    RestoreReport,
    RollbackLedger,
)
from tasks.dbas_sync import DbasSyncTask


@pytest.mark.parametrize("preview", [False, True])
def test_missing_outcome_does_not_borrow_another_mode(preview, caplog):
    report = RestoreReport(is_dry_run=preview)
    task = DbasSyncTask()
    with caplog.at_level(logging.INFO, logger="tasks.dbas_sync"):
        result = task._result_from_report(datetime.now(UTC), report)
    expected = "dry_run" if preview else "unknown"
    assert result.details["outcome"] == expected
    assert result.details["sync_report"]["outcome"] is None
    assert result.success is preview
    assert result.error == (None if preview else "SYNC_UNKNOWN")
    assert f"outcome={expected}" in caplog.text
    if not preview:
        assert "dry_run" not in result.message


def test_unpredicted_categories_are_named_and_excluded_from_prediction_totals():
    report = RestoreReport(is_dry_run=True)
    report.category(EntityType.CHANNEL).would_create = 2
    for entity in (EntityType.STREAM, EntityType.LOGO):
        category = report.category(entity)
        category.predicted = False
        category.caveat = f"Cannot predict {entity.value} before refresh."
        # Adversarial stale counters must not sneak into the promised totals.
        category.would_create = 77
        category.would_skip = 55
    result = DbasSyncTask()._result_from_report(datetime.now(UTC), report)
    assert "would create 2, update 0, skip 0" in result.message
    assert "predicted categories only" in result.message.lower()
    assert "stream: not predicted" in result.message.lower()
    assert "logo: not predicted" in result.message.lower()
    assert "Cannot predict stream before refresh." in result.message
    assert "Cannot predict logo before refresh." in result.message
    assert result.total_items == result.success_count == 2
    assert result.skipped_count == 0
    assert result.details["sync_report"]["categories"][1]["predicted"] is False


def test_apply_counts_ignore_preview_prediction_flags():
    report = RestoreReport(is_dry_run=False, outcome=RestoreOutcome.SUCCESS)
    category = report.category(EntityType.STREAM)
    category.predicted = False
    category.updated = 3
    result = DbasSyncTask()._result_from_report(datetime.now(UTC), report)
    assert result.success_count == 3
    assert "updated 3" in result.message
    assert "not predicted" not in result.message.lower()


def test_preview_without_any_predictions_does_not_offer_zero_predictions():
    report = RestoreReport(is_dry_run=True)
    report.category(EntityType.STREAM).predicted = False
    result = DbasSyncTask()._result_from_report(datetime.now(UTC), report)
    assert "no category counts predicted" in result.message.lower()
    assert "would create" not in result.message.lower()
    assert "stream: not predicted" in result.message.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("attach_fails", [False, True])
async def test_recorded_stream_matches_are_reused_not_fictitious_row_updates(
    attach_fails,
):
    recorded = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/dispatcharr_stream_provider_channel_number.json"
        ).read_text()
    )
    original = dict(recorded)
    client = AsyncMock()
    client.get_streams.return_value = {"results": [recorded], "next": None}
    # A converged destination already holds the stream; a drifted one holds
    # nothing, so only the drifted cycle has an attachment to write.
    held = [] if attach_fails else [recorded["id"]]
    client.get_channels.return_value = {
        "results": [
            {
                "id": 50,
                "name": "Recorded lineup",
                "channel_number": 1,
                "streams": held,
            }
        ]
    }
    if attach_fails:
        client.update_channel.side_effect = RuntimeError("attachment rejected")
    for _cycle in range(2):
        report = RestoreReport(is_dry_run=False)
        await import_channels(
            archive_channels=[
                {
                    "id": 1,
                    "name": "Recorded lineup",
                    "channel_number": 1,
                    "streams": [dict(recorded)],
                }
            ],
            client=client,
            selected=True,
            report=report,
            ledger=RollbackLedger(restore_id="p3-counts"),
            remap=IdRemapTable(),
        )
        streams = report.category(EntityType.STREAM)
        assert streams.updated == streams.created == 0
        if attach_fails:
            # A rejected attachment is one failure, never also a reuse.
            assert streams.failed == 1
            assert streams.skipped == 0
        else:
            assert streams.failed == 0
            assert streams.skipped == 1
            assert streams.skip_details[0].reason.value == "existing_stream_reused"
        assert recorded == original
    client.update_stream.assert_not_awaited()
    client.create_stream.assert_not_awaited()
    if attach_fails:
        client.update_channel.assert_awaited_with(50, {"streams": [recorded["id"]]})
    else:
        client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_apply_refused_by_preflight_is_not_labelled_a_preview(
    tmp_path, caplog
):
    """The archive restore shares the mapper's mode invariant.

    The real orchestrator refuses the apply at pre-flight and returns a report
    with no outcome. That run was an apply, so neither its task details nor its
    log line may call it a dry run.
    """
    from unittest.mock import patch

    from dbas import restore_orchestrator
    from tests.tasks.test_dbas_restore_task import _make_task, _write_artifact

    real_run_restore = restore_orchestrator.run_restore

    async def refused_by_preflight(**kwargs):
        return await real_run_restore(**kwargs, max_entities_per_category=0)

    task = _make_task(_write_artifact(tmp_path), confirm_apply=True)
    with patch("dbas.restore_orchestrator.run_restore", side_effect=refused_by_preflight), \
         patch("dispatcharr_client.get_client", return_value=AsyncMock()), \
         caplog.at_level(logging.INFO, logger="tasks.dbas_restore"):
        result = await task.execute()

    restore_report = result.details["restore_report"]
    assert any(n.startswith("pre-flight refused") for n in restore_report["notes"])
    assert restore_report["outcome"] is None
    assert result.details["is_dry_run"] is False
    assert result.success is False
    assert result.details["outcome"] == "unknown"
    assert "outcome=dry_run" not in caplog.text
    assert "mode=apply, outcome=unknown" in caplog.text


@pytest.mark.asyncio
async def test_sync_apply_aborted_at_destination_read_gate_reports_apply_mode(
    tmp_path, caplog
):
    """A confirmed apply that the destination-read gate aborts is still an apply.

    The real engine returns a report with no outcome and the unread marker. The
    error code keeps naming the unread destination; the recorded outcome and
    the log line must not call the run a preview.
    """
    from tests.tasks.test_dbas_sync_destination_readable import (
        _http_error,
        _preview,
        _refusing_dest_client,
    )
    from tests.tasks.test_dbas_sync_engine import _sync_target

    report = await _preview(
        _sync_target(), _refusing_dest_client(_http_error(401)), tmp_path,
        confirm_apply=True,
    )
    assert report.is_dry_run is False
    assert report.outcome is None
    assert report.destination_unreadable is not None

    with caplog.at_level(logging.INFO, logger="tasks.dbas_sync"):
        result = DbasSyncTask()._result_from_report(datetime.now(UTC), report)
    assert result.success is False
    assert result.error == "SYNC_DESTINATION_UNREADABLE"
    assert result.details["is_dry_run"] is False
    assert result.details["outcome"] == "unknown"
    assert "sync run could not read the destination" in result.message
    assert "mode=apply, outcome=unknown" in caplog.text
