"""ONE archived logo that fails to come back is ONE logo miss.

Bead ``enhancedchannelmanager-k2r7m``, found live in backup/restore drill run
``2026-08-06-run9`` against published build ``0.18.1-0035``. The genuine
logo-failure path (bead ``…-d0agi``) had never been exercised before that run:
the destination path of an ECM-uploaded logo was occupied by a directory, so
Dispatcharr refused the upload. Exactly one logo failed, and the report said so
three times and contradicted itself once::

    logo category:   created 12, updated 0, skipped 0, FAILED 1
    failure_details: [{"reason": "upstream_api_error", "label": "Run9 Uploaded Logo",
                       "source_export_id": 13}]
    note:            "1 logo row(s) could not be restored; ... nothing was rolled back."

    logo_misses = 2                                    <-- WRONG
    logo_miss_details = [
      {"source_export_id": 13, "label": "Run9 Uploaded Logo",  "channels": [...channel 12]},
      {"source_export_id": 13, "label": "logo #13 (archived)", "channels": [...channel 12]},
    ]

WHY THE SAME LOGO WAS EMITTED TWICE
-----------------------------------
Two of the three producers named in :meth:`RestoreReport.record_logo_miss` fire
on the SAME failure, by design:

1. :func:`dbas.importers.logos.import_logos` — the upload was rejected, so the
   logo is lost. It knows the logo's ARCHIVED DISPLAY NAME.
2. :func:`dbas.channel_reattach.reattach_channel_logos` — runs next, finds no
   destination id in the LOGO remap for that same archived logo (there is none;
   the upload failed), and records the channels left without their artwork. It
   only has the archive id, so it synthesizes ``"logo #13 (archived)"``.

Both are correct reports of the same LOST LOGO. ``logo_misses`` counts LOGOS,
not report events (:class:`dbas.restore_contracts.LogoMissDetail`: "ONE miss
stays ONE detail row"), so the two must collapse into one row keyed on the
archived logo's identity — keeping the operator-facing display name and the
UNION of the affected channels, because each producer sees a different slice of
them.

The count is what an operator reconciles against: the drill's summary line read
"failed 1 ... 2 logo(s) could not be reinstated" in one sentence, sending them
hunting for a second broken logo that does not exist.

Conventions: ``docs/pytest_conventions.md``; the Dispatcharr client is an
``AsyncMock`` (no live upstream).
"""
from __future__ import annotations

import base64

import pytest
from unittest.mock import AsyncMock

from dbas.channel_reattach import reattach_channel_logos
from dbas.importers.logos import import_logos
from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    LogoMissChannel,
    RestoreReport,
    RollbackLedger,
)

# A 1x1 PNG (valid magic bytes) — the same fixture shape the logos importer
# suite uses, so a rejection here is the UPLOAD failing, never validation.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

# The drill's numbers, kept verbatim so a re-run of run9 reads as confirmation.
_LOGO_ID = 13
_LOGO_NAME = "Run9 Uploaded Logo"
_CHANNEL_SOURCE_ID = 7
_CHANNEL_DEST_ID = 12
_CHANNEL_NAME = "AL | Birmingham | PBS WBIQ"


def _archive_logo(*, src_id: int = _LOGO_ID, name: str = _LOGO_NAME) -> dict:
    return {
        "id": src_id,
        "name": name,
        "filename": "run9-uploaded-logo.png",
        "content_type": "image/png",
        "content_b64": base64.b64encode(_PNG).decode("ascii"),
    }


def _archive_channel(
    *,
    src_id: int = _CHANNEL_SOURCE_ID,
    name: str = _CHANNEL_NAME,
    logo_id: int = _LOGO_ID,
) -> dict:
    return {"id": src_id, "name": name, "logo_id": logo_id}


async def _rejecting_upload(name, filename, content, content_type):
    """Dispatcharr's answer when the destination path is occupied by a directory."""
    raise RuntimeError("upstream rejected the upload")


def _client() -> AsyncMock:
    client = AsyncMock()
    client.get_all_logos_paginated = AsyncMock(return_value=[])
    client.upload_logo_file = AsyncMock(side_effect=_rejecting_upload)
    client.update_channel = AsyncMock(return_value={})
    return client


def _remap(*channel_ids: tuple[int, int]) -> IdRemapTable:
    remap = IdRemapTable()
    for source_id, dest_id in channel_ids or ((_CHANNEL_SOURCE_ID, _CHANNEL_DEST_ID),):
        remap.add(EntityType.CHANNEL, source_id, dest_id)
    return remap


async def _run_logo_step(
    *,
    report: RestoreReport,
    client: AsyncMock,
    remap: IdRemapTable,
    archive_logos: list[dict],
    archive_channels: list[dict],
) -> None:
    """The orchestrator's logo step: import, then reattach — in that order."""
    await import_logos(
        archive_logos=archive_logos,
        archive_channels=archive_channels,
        client=client,
        selected=True,
        report=report,
        ledger=RollbackLedger(restore_id="k2r7m"),
        remap=remap,
    )
    await reattach_channel_logos(
        client=client,
        report=report,
        remap=remap,
        archive_channels=archive_channels,
        created_source_ids={c["id"] for c in archive_channels},
    )


# ---------------------------------------------------------------------------
# 1. The drill scenario, end to end through both producers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_failed_logo_is_counted_once_across_both_producers():
    """THE regression: ``logo_misses`` reported 2 for a single failed logo."""
    report = RestoreReport(is_dry_run=False)
    client = _client()

    await _run_logo_step(
        report=report,
        client=client,
        remap=_remap(),
        archive_logos=[_archive_logo()],
        archive_channels=[_archive_channel()],
    )

    assert report.category(EntityType.LOGO).failed == 1
    assert report.logo_misses == 1
    assert len(report.logo_miss_details) == 1


@pytest.mark.asyncio
async def test_the_merged_row_keeps_the_archived_display_name():
    """"Run9 Uploaded Logo" tells the operator which logo; "logo #13" does not."""
    report = RestoreReport(is_dry_run=False)

    await _run_logo_step(
        report=report,
        client=_client(),
        remap=_remap(),
        archive_logos=[_archive_logo()],
        archive_channels=[_archive_channel()],
    )

    detail = report.logo_miss_details[0]
    assert detail.source_export_id == _LOGO_ID
    assert detail.label == _LOGO_NAME


@pytest.mark.asyncio
async def test_the_merged_row_names_each_affected_channel_once():
    """Both producers saw the same channel; the operator sees it once."""
    report = RestoreReport(is_dry_run=False)

    await _run_logo_step(
        report=report,
        client=_client(),
        remap=_remap(),
        archive_logos=[_archive_logo()],
        archive_channels=[_archive_channel()],
    )

    channels = report.logo_miss_details[0].channels
    assert [(c.channel_id, c.name) for c in channels] == [
        (_CHANNEL_DEST_ID, _CHANNEL_NAME)
    ]


@pytest.mark.asyncio
async def test_two_genuinely_lost_logos_still_count_two():
    """The control: de-duplication collapses reports, never distinct logos."""
    report = RestoreReport(is_dry_run=False)

    await _run_logo_step(
        report=report,
        client=_client(),
        remap=_remap((7, 12), (8, 14)),
        archive_logos=[
            _archive_logo(src_id=13, name="Run9 Uploaded Logo"),
            _archive_logo(src_id=14, name="Run9 Second Logo"),
        ],
        archive_channels=[
            _archive_channel(src_id=7, name="Channel A", logo_id=13),
            _archive_channel(src_id=8, name="Channel B", logo_id=14),
        ],
    )

    assert report.logo_misses == 2
    assert sorted(d.source_export_id for d in report.logo_miss_details) == [13, 14]
    assert sorted(d.label for d in report.logo_miss_details) == [
        "Run9 Second Logo",
        "Run9 Uploaded Logo",
    ]


# ---------------------------------------------------------------------------
# 2. The merge rule itself (record_logo_miss is the single writer)
# ---------------------------------------------------------------------------


def test_the_display_name_wins_whichever_producer_reports_first():
    """Producer order is an implementation detail; the operator's label is not."""
    synthetic_first = RestoreReport(is_dry_run=False)
    synthetic_first.record_logo_miss(
        label="logo #13 (archived)", source_export_id=13, label_is_synthetic=True
    )
    synthetic_first.record_logo_miss(label="Run9 Uploaded Logo", source_export_id=13)

    display_first = RestoreReport(is_dry_run=False)
    display_first.record_logo_miss(label="Run9 Uploaded Logo", source_export_id=13)
    display_first.record_logo_miss(
        label="logo #13 (archived)", source_export_id=13, label_is_synthetic=True
    )

    assert synthetic_first.logo_misses == 1
    assert display_first.logo_misses == 1
    assert synthetic_first.logo_miss_details[0].label == "Run9 Uploaded Logo"
    assert display_first.logo_miss_details[0].label == "Run9 Uploaded Logo"


def test_affected_channels_are_unioned_never_dropped():
    """Each producer sees a different slice of the channels; the row keeps both."""
    report = RestoreReport(is_dry_run=False)
    report.record_logo_miss(
        label="Run9 Uploaded Logo",
        source_export_id=13,
        channels=[LogoMissChannel(channel_id=12, name="AL | Birmingham | PBS WBIQ")],
    )
    report.record_logo_miss(
        label="logo #13 (archived)",
        source_export_id=13,
        label_is_synthetic=True,
        channels=[
            LogoMissChannel(channel_id=12, name="AL | Birmingham | PBS WBIQ"),
            LogoMissChannel(channel_id=14, name="CO | Denver | PBS KRMA"),
        ],
    )

    assert report.logo_misses == 1
    channels = report.logo_miss_details[0].channels
    assert [(c.channel_id, c.name) for c in channels] == [
        (12, "AL | Birmingham | PBS WBIQ"),
        (14, "CO | Denver | PBS KRMA"),
    ]


def test_misses_with_no_archive_id_are_never_merged():
    """Without an archived id there is no identity to merge on — count them all."""
    report = RestoreReport(is_dry_run=False)
    report.record_logo_miss(label="Unnamed A")
    report.record_logo_miss(label="Unnamed B")

    assert report.logo_misses == 2
    assert [d.label for d in report.logo_miss_details] == ["Unnamed A", "Unnamed B"]


def test_a_reattach_only_miss_is_still_reported():
    """A logo that uploaded fine but could not be PATCHed onto its channel.

    The logos importer records nothing here, so the reattach pass's row is the
    ONLY report of the loss — de-duplication must not swallow it.
    """
    report = RestoreReport(is_dry_run=False)
    report.record_logo_miss(
        label="logo #13 (archived)",
        source_export_id=13,
        label_is_synthetic=True,
        channels=[LogoMissChannel(channel_id=12, name="AL | Birmingham | PBS WBIQ")],
    )

    assert report.logo_misses == 1
    assert report.logo_miss_details[0].label == "logo #13 (archived)"
