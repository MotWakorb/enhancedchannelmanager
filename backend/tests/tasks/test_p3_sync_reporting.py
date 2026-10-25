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
    client.get_channels.return_value = {
        "results": [
            {
                "id": 50,
                "name": "Recorded lineup",
                "channel_number": 1,
                "streams": [recorded["id"]],
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
        assert streams.skipped == 1
        assert streams.failed == int(attach_fails)
        assert streams.skip_details[0].reason.value == "existing_stream_reused"
        assert recorded == original
    client.update_stream.assert_not_awaited()
    client.create_stream.assert_not_awaited()
    client.update_channel.assert_awaited_with(50, {"streams": [recorded["id"]]})
