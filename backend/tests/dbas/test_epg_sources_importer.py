"""Tests for the EPG sources restore importer
(enhancedchannelmanager-0i2vt.11 — Phase 2, restored AFTER M3U, BEFORE Channels).

Scope under test:

1. Create EPG sources. For each archived source, strip archive-only /
   non-writable keys (id/pk, read-only timestamps/status) and create via
   ``client.create_epg_source``. Register source->dest in the IdRemapTable under
   EntityType.EPG_SOURCE and record each create in the RollbackLedger.

2. Collision taxonomy: an existing identical source (matched on the stable
   identity key ``(source_type, normalized name)``) is skipped
   ALREADY_EXISTS_IDENTICAL, its source id still remapped to the existing dest
   id so a later FK reference resolves. A create that races into an upstream
   uniqueness conflict is failed CONFLICT.

3. FK remap: an archived source carrying an ``m3u_account`` FK reference is
   remapped through the IdRemapTable (M3U_ACCOUNT namespace). An unresolvable
   FK is skipped DEPENDENCY_UNRESOLVED (no create attempted, no dangling id sent
   upstream).

4. Opt-in: nothing happens unless the operator selected the category.

5. Dry-run: no creates, no ledger entries; reports would_create.

6. NO credential leakage: report / ledger / logs carry only safe fields (name,
   source_type, id, counts, status). A Schedules-Direct ``username`` /
   ``password`` and an XMLTV ``url`` (which may embed a token) never surface in
   the report, the ledger, or a sanitized failure message.

The Dispatcharr client is mocked at the importer module level
(``dbas.importers.epg_sources``); the importer is exercised with an AsyncMock
client.
"""
import logging

import pytest
from unittest.mock import AsyncMock

from credential_sentinel import REDACTION_SENTINEL
from dbas.importers.epg_sources import import_epg_sources
from dbas.restore_contracts import (
    EntityType,
    FailureReason,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
    SkipReason,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client(*, existing_sources=None, create_side_effect=None):
    """Build an AsyncMock Dispatcharr client with the methods the importer uses."""
    client = AsyncMock()
    client.get_epg_sources = AsyncMock(return_value=existing_sources or [])
    created_counter = {"n": 800}

    async def _default_create(payload):
        created_counter["n"] += 1
        return {"id": created_counter["n"], **payload}

    client.create_epg_source = AsyncMock(side_effect=create_side_effect or _default_create)
    client.delete_epg_source = AsyncMock(return_value=None)
    return client


def _report(is_dry_run=False):
    return RestoreReport(is_dry_run=is_dry_run)


def _ledger():
    return RollbackLedger(restore_id="test-restore")


def _remap(**kwargs):
    """Build an IdRemapTable pre-seeded with the given mappings.

    e.g. ``_remap(m3u_account={1: 901}, epg_source={2: 802})``.
    """
    table = IdRemapTable()
    name_to_type = {
        "m3u_account": EntityType.M3U_ACCOUNT,
        "epg_source": EntityType.EPG_SOURCE,
    }
    for name, mapping in kwargs.items():
        for src, dest in mapping.items():
            table.add(name_to_type[name], src, dest)
    return table


# ---------------------------------------------------------------------------
# Opt-in gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_category_skipped_when_not_selected():
    """OPT-IN: when the operator did not select the category, nothing is created —
    every archived source is recorded EXCLUDED_BY_OPERATOR."""
    client = _client()
    report = _report()
    ledger = _ledger()

    await import_epg_sources(
        archive_sources=[{"id": 5, "name": "EPG A", "source_type": "xmltv", "url": "http://e/a"}],
        client=client,
        selected=False,
        report=report,
        ledger=ledger,
        remap=_remap(),
    )

    client.create_epg_source.assert_not_called()
    cat = report.category(EntityType.EPG_SOURCE)
    assert cat.created == 0
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.EXCLUDED_BY_OPERATOR


# ---------------------------------------------------------------------------
# Create — happy path (remap + ledger)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_source_happy_path_remap_and_ledger():
    """An archived source is created; source->dest is registered in the
    IdRemapTable (EPG_SOURCE) and the create is recorded in the RollbackLedger."""
    async def _create(payload):
        return {"id": 802, **payload}

    client = _client()
    client.create_epg_source = AsyncMock(side_effect=_create)
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await import_epg_sources(
        archive_sources=[{"id": 5, "name": "EPG A", "source_type": "xmltv", "url": "http://e/a"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    cat = report.category(EntityType.EPG_SOURCE)
    assert cat.created == 1
    assert remap.resolve(EntityType.EPG_SOURCE, 5) == 802
    assert len(ledger.entries) == 1
    assert ledger.entries[0].entity_type == EntityType.EPG_SOURCE
    assert ledger.entries[0].destination_id == 802
    assert ledger.entries[0].label == "EPG A"


@pytest.mark.asyncio
async def test_create_payload_strips_source_id_and_readonly_fields():
    """The create payload drops the archive source id and read-only / derived
    fields (timestamps, status, counts), but keeps name/source_type/url/creds."""
    captured = {}

    async def _create(payload):
        captured.update(payload)
        return {"id": 802, **payload}

    client = _client()
    client.create_epg_source = AsyncMock(side_effect=_create)

    await import_epg_sources(
        archive_sources=[{
            "id": 5,
            "name": "EPG A",
            "source_type": "xmltv",
            "url": "http://e/a",
            "created_at": "2020-01-01",
            "updated_at": "2020-02-02",
            "status": "ok",
            "last_refresh": "2020-03-03",
        }],
        client=client,
        selected=True,
        report=_report(),
        ledger=_ledger(),
        remap=_remap(),
    )

    assert "id" not in captured
    assert "created_at" not in captured
    assert "updated_at" not in captured
    assert "status" not in captured
    assert "last_refresh" not in captured
    assert captured["name"] == "EPG A"
    assert captured["source_type"] == "xmltv"
    assert captured["url"] == "http://e/a"


# ---------------------------------------------------------------------------
# FK remap (m3u_account)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m3u_account_fk_remapped_through_table():
    """An archived source's ``m3u_account`` FK is rewritten from the source id to
    the destination id via the IdRemapTable (M3U_ACCOUNT namespace) before
    create."""
    captured = {}

    async def _create(payload):
        captured.update(payload)
        return {"id": 802, **payload}

    client = _client()
    client.create_epg_source = AsyncMock(side_effect=_create)
    remap = _remap(m3u_account={1: 901})

    await import_epg_sources(
        archive_sources=[{
            "id": 5,
            "name": "EPG A",
            "source_type": "xmltv",
            "url": "http://e/a",
            "m3u_account": 1,
        }],
        client=client,
        selected=True,
        report=_report(),
        ledger=_ledger(),
        remap=remap,
    )

    assert captured["m3u_account"] == 901  # remapped from source id 1


@pytest.mark.asyncio
async def test_unresolvable_m3u_account_fk_skipped_dependency_unresolved():
    """An archived source whose ``m3u_account`` FK cannot be resolved through the
    remap table is skipped DEPENDENCY_UNRESOLVED — no create is attempted, no
    dangling source id is sent upstream."""
    client = _client()
    report = _report()

    await import_epg_sources(
        archive_sources=[{
            "id": 5,
            "name": "EPG A",
            "source_type": "xmltv",
            "url": "http://e/a",
            "m3u_account": 99,  # not in the remap table
        }],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),  # empty
    )

    client.create_epg_source.assert_not_called()
    cat = report.category(EntityType.EPG_SOURCE)
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.DEPENDENCY_UNRESOLVED


@pytest.mark.asyncio
async def test_null_m3u_account_fk_is_not_treated_as_unresolved():
    """A source with no m3u_account association (None / absent) is created
    normally — a null FK is not an unresolved dependency."""
    async def _create(payload):
        return {"id": 802, **payload}

    client = _client()
    client.create_epg_source = AsyncMock(side_effect=_create)
    report = _report()

    await import_epg_sources(
        archive_sources=[{
            "id": 5,
            "name": "EPG A",
            "source_type": "xmltv",
            "url": "http://e/a",
            "m3u_account": None,
        }],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    cat = report.category(EntityType.EPG_SOURCE)
    assert cat.created == 1
    assert cat.skipped == 0


# ---------------------------------------------------------------------------
# Collision taxonomy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_source_by_identity_skipped_already_exists():
    """An archived source whose (source_type, normalized name) already exists on
    the destination is skipped ALREADY_EXISTS_IDENTICAL; its source id is still
    remapped to the existing dest id so a later FK reference resolves."""
    client = _client(existing_sources=[
        {"id": 700, "name": "EPG A", "source_type": "xmltv"},
    ])
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await import_epg_sources(
        archive_sources=[{"id": 5, "name": "  epg a  ", "source_type": "xmltv", "url": "http://e/a"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    client.create_epg_source.assert_not_called()
    cat = report.category(EntityType.EPG_SOURCE)
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.ALREADY_EXISTS_IDENTICAL
    assert remap.resolve(EntityType.EPG_SOURCE, 5) == 700
    assert len(ledger.entries) == 0


@pytest.mark.asyncio
async def test_same_name_different_source_type_is_not_a_collision():
    """The identity key includes source_type: a same-name source of a DIFFERENT
    source_type is NOT a collision and is created."""
    async def _create(payload):
        return {"id": 802, **payload}

    client = _client(existing_sources=[
        {"id": 700, "name": "EPG A", "source_type": "schedules_direct"},
    ])
    client.create_epg_source = AsyncMock(side_effect=_create)
    report = _report()

    await import_epg_sources(
        archive_sources=[{"id": 5, "name": "EPG A", "source_type": "xmltv", "url": "http://e/a"}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    cat = report.category(EntityType.EPG_SOURCE)
    assert cat.created == 1
    assert cat.skipped == 0


@pytest.mark.asyncio
async def test_create_conflict_recorded_as_failure_conflict():
    """A create that races into an upstream uniqueness conflict is failed
    CONFLICT (not skipped)."""
    async def _conflict(payload):
        raise RuntimeError("name already exists")

    client = _client()
    client.create_epg_source = AsyncMock(side_effect=_conflict)
    report = _report()
    ledger = _ledger()

    await import_epg_sources(
        archive_sources=[{"id": 5, "name": "EPG A", "source_type": "xmltv", "url": "http://e/a"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=_remap(),
    )

    cat = report.category(EntityType.EPG_SOURCE)
    assert cat.failed == 1
    assert cat.failure_details[0].reason == FailureReason.CONFLICT
    assert len(ledger.entries) == 0


@pytest.mark.asyncio
async def test_create_upstream_error_recorded_as_failure():
    """A non-conflict create error is failed UPSTREAM_API_ERROR."""
    async def _boom(payload):
        raise RuntimeError("502 bad gateway")

    client = _client()
    client.create_epg_source = AsyncMock(side_effect=_boom)
    report = _report()

    await import_epg_sources(
        archive_sources=[{"id": 5, "name": "EPG A", "source_type": "xmltv", "url": "http://e/a"}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    cat = report.category(EntityType.EPG_SOURCE)
    assert cat.failed == 1
    assert cat.failure_details[0].reason == FailureReason.UPSTREAM_API_ERROR


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_no_creates_no_ledger():
    """Dry-run: no source is created, no ledger entry written; the importer
    reports would_create."""
    client = _client()
    report = _report(is_dry_run=True)
    ledger = _ledger()

    await import_epg_sources(
        archive_sources=[{"id": 5, "name": "EPG A", "source_type": "xmltv", "url": "http://e/a"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=_remap(),
        is_dry_run=True,
    )

    client.create_epg_source.assert_not_called()
    cat = report.category(EntityType.EPG_SOURCE)
    assert cat.would_create == 1
    assert cat.created == 0
    assert len(ledger.entries) == 0


@pytest.mark.asyncio
async def test_dry_run_unresolved_fk_reported_would_skip():
    """Dry-run: an unresolvable FK is reported as a would-skip with reason
    DEPENDENCY_UNRESOLVED (so the operator sees why before applying)."""
    client = _client()
    report = _report(is_dry_run=True)

    await import_epg_sources(
        archive_sources=[{
            "id": 5, "name": "EPG A", "source_type": "xmltv",
            "url": "http://e/a", "m3u_account": 99,
        }],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
        is_dry_run=True,
    )

    client.create_epg_source.assert_not_called()
    cat = report.category(EntityType.EPG_SOURCE)
    assert cat.would_skip == 1
    assert cat.skip_details[0].reason == SkipReason.DEPENDENCY_UNRESOLVED


# ---------------------------------------------------------------------------
# Credential / secret hygiene (the bead .8 clear-text-logging lesson)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_and_ledger_carry_no_credential_material():
    """No url (token-bearing) / username / password surfaces in the report
    (labels, notes) or the ledger."""
    async def _create(payload):
        return {"id": 802, **payload}

    client = _client()
    client.create_epg_source = AsyncMock(side_effect=_create)
    report = _report()
    ledger = _ledger()

    secret_markers = (
        "http://secret-provider",
        "s3cr3t-pass",
        "secret-user",
        "token=abc123",
    )

    await import_epg_sources(
        archive_sources=[{
            "id": 5,
            "name": "EPG A",
            "source_type": "schedules_direct",
            "url": "http://secret-provider/epg.xml?token=abc123",
            "username": "secret-user",
            "password": "s3cr3t-pass",
        }],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=_remap(),
    )

    blob = report.model_dump_json() + ledger.model_dump_json()
    for marker in secret_markers:
        assert marker not in blob


@pytest.mark.asyncio
async def test_failure_message_does_not_leak_url_or_credentials(caplog):
    """An upstream error body that echoes the source url / credentials is scrubbed
    from the operator-facing failure message AND not written to the log."""
    async def _boom(payload):
        raise RuntimeError(
            "POST http://secret-provider/epg.xml?token=abc123 failed: "
            "username=secret-user password=s3cr3t-pass rejected"
        )

    client = _client()
    client.create_epg_source = AsyncMock(side_effect=_boom)
    report = _report()

    with caplog.at_level(logging.DEBUG):
        await import_epg_sources(
            archive_sources=[{
                "id": 5,
                "name": "EPG A",
                "source_type": "schedules_direct",
                "url": "http://secret-provider/epg.xml?token=abc123",
                "username": "secret-user",
                "password": "s3cr3t-pass",
            }],
            client=client,
            selected=True,
            report=report,
            ledger=_ledger(),
            remap=_remap(),
        )

    cat = report.category(EntityType.EPG_SOURCE)
    assert cat.failed == 1
    message = cat.failure_details[0].message
    log_text = caplog.text
    for marker in ("secret-provider", "token=abc123", "secret-user", "s3cr3t-pass"):
        assert marker not in message
        assert marker not in log_text


# ---------------------------------------------------------------------------
# 7. wait_for_epg_downloads (bead kxcjf — the unmet 0i2vt.11 acceptance item):
# bounded 2-stage trigger+poll for Dispatcharr's EPG data download. Never hangs,
# never raises, non-silent on timeout (completed=False for the caller to note).
# ---------------------------------------------------------------------------


def _wait_client(source_row):
    client = AsyncMock()
    client.refresh_epg_source = AsyncMock(return_value={})
    client.get_epg_source = AsyncMock(return_value=source_row)
    return client


@pytest.mark.asyncio
async def test_wait_completes_immediately_on_terminal_status():
    from dbas.importers.epg_sources import wait_for_epg_downloads

    sleeps = []

    async def _sleep(seconds):
        sleeps.append(seconds)

    client = _wait_client({"id": 7, "status": "success", "epg_count": 0})
    summaries = await wait_for_epg_downloads(
        source_ids=[7], client=client, sleep_fn=_sleep
    )
    assert summaries == [
        {"epg_source_id": 7, "completed": True, "status": "success", "polls": 1}
    ]
    # Terminal on the FIRST check — zero sleeping (check-first, sleep-between).
    assert sleeps == []
    client.refresh_epg_source.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_wait_epg_count_is_terminal_even_with_unknown_status():
    # Future-proofing: if Dispatcharr's status vocabulary drifts, a positive
    # epg_count still terminates the wait (data actually landed).
    from dbas.importers.epg_sources import wait_for_epg_downloads

    client = _wait_client({"id": 8, "status": "somenewstate", "epg_count": 120})

    async def _sleep(_):
        raise AssertionError("must not sleep when the first check is terminal")

    summaries = await wait_for_epg_downloads(
        source_ids=[8], client=client, sleep_fn=_sleep
    )
    assert summaries[0]["completed"] is True


@pytest.mark.asyncio
async def test_wait_polls_until_download_finishes():
    from dbas.importers.epg_sources import wait_for_epg_downloads

    rows = [
        {"id": 9, "status": "fetching", "epg_count": 0},
        {"id": 9, "status": "parsing", "epg_count": 0},
        {"id": 9, "status": "success", "epg_count": 500},
    ]
    client = AsyncMock()
    client.refresh_epg_source = AsyncMock(return_value={})
    client.get_epg_source = AsyncMock(side_effect=rows)
    sleeps = []

    async def _sleep(seconds):
        sleeps.append(seconds)

    summaries = await wait_for_epg_downloads(
        source_ids=[9], client=client, sleep_fn=_sleep, poll_interval_seconds=0.5
    )
    assert summaries[0]["completed"] is True
    assert summaries[0]["polls"] == 3
    # Two sleeps BETWEEN the three polls, at the configured interval.
    assert sleeps == [0.5, 0.5]


@pytest.mark.asyncio
async def test_wait_times_out_bounded_and_reports_incomplete():
    from dbas.importers.epg_sources import wait_for_epg_downloads

    client = _wait_client({"id": 10, "status": "fetching", "epg_count": 0})

    async def _sleep(_):
        return None

    summaries = await wait_for_epg_downloads(
        source_ids=[10], client=client, sleep_fn=_sleep, max_polls=4
    )
    # Bounded: exactly max_polls probes, then a NON-SILENT incomplete result.
    assert summaries[0]["completed"] is False
    assert summaries[0]["polls"] == 4
    assert client.get_epg_source.await_count == 4


@pytest.mark.asyncio
async def test_wait_trigger_and_probe_errors_are_nonfatal():
    # A failing refresh trigger and failing probes never raise — the wait runs
    # its bounded course and reports incomplete.
    from dbas.importers.epg_sources import wait_for_epg_downloads

    client = AsyncMock()
    client.refresh_epg_source = AsyncMock(side_effect=RuntimeError("boom"))
    client.get_epg_source = AsyncMock(side_effect=RuntimeError("probe down"))

    async def _sleep(_):
        return None

    summaries = await wait_for_epg_downloads(
        source_ids=[11], client=client, sleep_fn=_sleep, max_polls=2
    )
    assert summaries[0]["completed"] is False
    assert summaries[0]["polls"] == 2


@pytest.mark.asyncio
async def test_wait_covers_every_source_id():
    from dbas.importers.epg_sources import wait_for_epg_downloads

    client = AsyncMock()
    client.refresh_epg_source = AsyncMock(return_value={})
    client.get_epg_source = AsyncMock(
        side_effect=lambda sid: {"id": sid, "status": "success", "epg_count": 1}
    )

    async def _sleep(_):
        return None

    summaries = await wait_for_epg_downloads(
        source_ids=[21, 22, 23], client=client, sleep_fn=_sleep
    )
    assert [s["epg_source_id"] for s in summaries] == [21, 22, 23]
    assert all(s["completed"] for s in summaries)
    assert client.refresh_epg_source.await_count == 3


# ---------------------------------------------------------------------------
# Redaction-sentinel handling on restore (bead …-6pilh)
# ---------------------------------------------------------------------------
#
# Dispatcharr 0.28.2's EPGSourceSerializer marks ``password`` write_only with no
# admin re-add (unlike M3UAccountSerializer, which re-adds it for user_level>=10),
# so a live gather does not normally carry an EPG password at all. The importer
# still refuses the sentinel by VALUE rather than relying on that upstream
# behaviour: an artifact built by a future/other Dispatcharr, or a
# ``custom_properties`` credential, must never be written through as a
# placeholder.


@pytest.mark.asyncio
async def test_redacted_password_is_never_written_to_the_destination():
    captured = {}

    async def _create(payload):
        captured["payload"] = payload
        return {"id": 801, **payload}

    client = _client()
    client.create_epg_source = AsyncMock(side_effect=_create)

    await import_epg_sources(
        archive_sources=[{
            "id": 3,
            "name": "SD Sports",
            "source_type": "schedules_direct",
            "username": "sduser",
            "password": REDACTION_SENTINEL,
        }],
        client=client,
        selected=True,
        report=_report(),
        ledger=_ledger(),
        remap=_remap(),
    )

    payload = captured["payload"]
    assert payload.get("password") != REDACTION_SENTINEL
    assert "password" not in payload
    assert payload["username"] == "sduser"


@pytest.mark.asyncio
async def test_redacted_credential_is_a_counted_post_restore_action_item():
    report = _report()

    await import_epg_sources(
        archive_sources=[{
            "id": 3,
            "name": "SD Sports",
            "source_type": "schedules_direct",
            "username": "sduser",
            "password": REDACTION_SENTINEL,
        }],
        client=_client(),
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    assert report.credentials_needing_reentry == 1
    detail = report.credential_reentry_details[0]
    assert detail.entity_type == EntityType.EPG_SOURCE
    assert detail.label == "SD Sports"
    assert detail.fields == ["password"]
    assert detail.source_export_id == 3
    assert detail.destination_id == 801


@pytest.mark.asyncio
async def test_credential_bearing_artifact_still_restores_the_real_password():
    captured = {}

    async def _create(payload):
        captured["payload"] = payload
        return {"id": 801, **payload}

    client = _client()
    client.create_epg_source = AsyncMock(side_effect=_create)
    report = _report()

    await import_epg_sources(
        archive_sources=[{
            "id": 3,
            "name": "SD Sports",
            "source_type": "schedules_direct",
            "username": "sduser",
            "password": "realsdpass",
        }],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    assert captured["payload"]["password"] == "realsdpass"
    assert report.credentials_needing_reentry == 0
