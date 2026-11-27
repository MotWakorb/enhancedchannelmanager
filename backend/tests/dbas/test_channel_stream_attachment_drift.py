"""An existing channel's stream attachment is reported by what the run wrote.

A matched stream row is reused, never updated. The CHANNEL row is what changes
when its ordered stream list is rewritten, so the report must count that write
as a channel update, skip the write when the destination already holds the
same ordered list, and never count reuse for an attachment that was rejected.
The destination candidates are built from a recorded Dispatcharr stream
response so the real matcher resolves them.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from dbas.importers.channels import import_channels
from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
    SkipReason,
)

_RECORDED = json.loads(
    (
        Path(__file__).parents[1]
        / "fixtures/dispatcharr_stream_provider_channel_number.json"
    ).read_text()
)


def _stream(stream_id: int, suffix: str) -> dict:
    stream = dict(_RECORDED)
    stream["id"] = stream_id
    stream["name"] = "%s %s" % (_RECORDED["name"], suffix)
    stream["url"] = _RECORDED["url"].replace("123.ts", "%s.ts" % suffix)
    stream["stream_hash"] = "%s%s" % (_RECORDED["stream_hash"][:-4], suffix)
    return stream


_A = _stream(101, "a01")
_B = _stream(102, "b02")
_C = _stream(103, "c03")


def _client(current_streams: list[int]) -> AsyncMock:
    client = AsyncMock()
    client.get_streams.return_value = {"results": [_A, _B, _C], "next": None}
    client.get_channels.return_value = {
        "results": [
            {
                "id": 50,
                "name": "Recorded lineup",
                "channel_number": 1,
                "streams": list(current_streams),
            }
        ]
    }
    return client


async def _import(client: AsyncMock, *, dry_run: bool = False) -> RestoreReport:
    report = RestoreReport(is_dry_run=dry_run)
    await import_channels(
        archive_channels=[
            {
                "id": 1,
                "name": "Recorded lineup",
                "channel_number": 1,
                # Archived streams carry SOURCE ids; the matcher resolves them
                # to destination rows 101 and 102 by URL.
                "streams": [dict(_A, id=9001), dict(_B, id=9002)],
            }
        ],
        client=client,
        selected=True,
        report=report,
        ledger=RollbackLedger(restore_id="attachment-drift"),
        remap=IdRemapTable(),
    )
    return report


def _reasons(category) -> list[SkipReason]:
    return [detail.reason for detail in category.skip_details]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current", [[103], [102, 101], []], ids=["drifted", "order_only", "empty"]
)
async def test_rewritten_attachment_is_a_channel_update(current):
    client = _client(current)
    report = await _import(client)

    client.update_channel.assert_awaited_once_with(50, {"streams": [101, 102]})
    channels = report.category(EntityType.CHANNEL)
    assert channels.updated == 1
    assert channels.skipped == 0
    assert SkipReason.ALREADY_EXISTS_IDENTICAL not in _reasons(channels)
    streams = report.category(EntityType.STREAM)
    assert streams.updated == streams.created == streams.failed == 0
    assert _reasons(streams) == [SkipReason.EXISTING_STREAM_REUSED] * 2
    assert any(
        "Recorded lineup" in note and "stream" in note for note in report.notes
    )


@pytest.mark.asyncio
async def test_identical_attachment_is_not_rewritten():
    client = _client([101, 102])
    report = await _import(client)

    client.update_channel.assert_not_awaited()
    channels = report.category(EntityType.CHANNEL)
    assert channels.updated == 0
    assert _reasons(channels) == [SkipReason.ALREADY_EXISTS_IDENTICAL]
    streams = report.category(EntityType.STREAM)
    assert _reasons(streams) == [SkipReason.EXISTING_STREAM_REUSED] * 2


@pytest.mark.asyncio
async def test_rejected_attachment_counts_one_failure_and_no_reuse():
    client = _client([103])
    client.update_channel.side_effect = RuntimeError("attachment rejected")
    report = await _import(client)

    client.update_channel.assert_awaited_once_with(50, {"streams": [101, 102]})
    streams = report.category(EntityType.STREAM)
    assert streams.failed == 1
    assert streams.skipped == 0
    assert streams.skip_details == []
    assert report.category(EntityType.CHANNEL).updated == 0
