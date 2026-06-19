"""Tests for the custom-stream fallback synthesis
(enhancedchannelmanager-ahygg — split 4/4 of the 0i2vt.14 channels importer).

When the 4-tier stream matcher (al6e3) returns a MISS for an archived stream,
that stream is "orphaned" — no destination stream matched it. This module
synthesizes a home for orphans:

1. Ensure ONE synthesized "custom-stream" M3U account exists on the destination
   (find-or-create by a well-known name; idempotent — a second batch reuses it).
2. Create each orphaned stream under that synthesized account and attribute it
   (set ``m3u_account`` to the synthesized account id). Register each created
   stream id in the IdRemapTable (STREAM) + RollbackLedger.
3. WARN log EVERY time the fallback path fires (operator observability),
   including the count of orphaned streams.

The Dispatcharr client is mocked at the module level
(``dbas.custom_stream_fallback``); the function is exercised with an AsyncMock
client. The matcher itself is NOT run here — these tests feed pre-identified
orphans (the MISS results), matching the bead's tight scope.
"""
import logging

import pytest
from unittest.mock import AsyncMock

from dbas.custom_stream_fallback import (
    CUSTOM_STREAM_ACCOUNT_NAME,
    synthesize_custom_streams,
)
from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client(*, existing_accounts=None, create_account_id=900, create_side_effect=None):
    """Build an AsyncMock Dispatcharr client with the methods the fallback uses.

    By default ``get_m3u_accounts`` returns no synthesized account (so it gets
    created), ``create_m3u_account`` returns an account with id
    ``create_account_id``, and ``create_stream`` assigns monotonic ids.
    """
    client = AsyncMock()
    client.get_m3u_accounts = AsyncMock(return_value=existing_accounts or [])
    client.create_m3u_account = AsyncMock(
        return_value={"id": create_account_id, "name": CUSTOM_STREAM_ACCOUNT_NAME}
    )

    created_counter = {"n": 0}

    async def _default_create_stream(payload):
        created_counter["n"] += 1
        return {"id": 1000 + created_counter["n"], **payload}

    client.create_stream = AsyncMock(
        side_effect=create_side_effect or _default_create_stream
    )
    return client


def _report():
    return RestoreReport(is_dry_run=False)


def _ledger():
    return RollbackLedger(restore_id="test-restore")


def _remap():
    return IdRemapTable()


def _orphan(stream_id, name="Orphan Stream", url="http://example/x"):
    """An archived stream record that the matcher returned MISS for."""
    return {"id": stream_id, "name": name, "url": url, "m3u_account": 42}


# ---------------------------------------------------------------------------
# Happy path: zero orphans => no synthesis, no WARN, no creates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_orphans_no_account_no_warn_no_creates(caplog):
    """The common case where everything matched: no orphans => the fallback is
    a complete no-op. No account synthesized, no stream created, no WARN."""
    client = _client()
    report = _report()
    ledger = _ledger()
    remap = _remap()

    with caplog.at_level(logging.WARNING):
        result = await synthesize_custom_streams(
            orphans=[],
            client=client,
            remap=remap,
            ledger=ledger,
            report=report,
        )

    client.get_m3u_accounts.assert_not_awaited()
    client.create_m3u_account.assert_not_awaited()
    client.create_stream.assert_not_awaited()
    assert result.account_id is None
    assert result.created_stream_ids == []
    # No WARN at all on the clean path.
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
    assert ledger.entries == []


# ---------------------------------------------------------------------------
# Orphans present: account synthesized once + orphans created + attributed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orphans_create_account_once_and_attribute_each(caplog):
    """Orphans present => the synthesized account is created exactly once, each
    orphan is created under it and attributed (m3u_account == account id), and
    the remap + ledger record each created stream."""
    client = _client(create_account_id=900)
    report = _report()
    ledger = _ledger()
    remap = _remap()

    orphans = [_orphan(11, "A"), _orphan(12, "B"), _orphan(13, "C")]

    with caplog.at_level(logging.WARNING):
        result = await synthesize_custom_streams(
            orphans=orphans,
            client=client,
            remap=remap,
            ledger=ledger,
            report=report,
        )

    # Account synthesized exactly once.
    client.create_m3u_account.assert_awaited_once()
    assert result.account_id == 900

    # Each orphan created under the synthesized account, attributed.
    assert client.create_stream.await_count == 3
    for call in client.create_stream.await_args_list:
        payload = call.args[0]
        assert payload["m3u_account"] == 900

    # remap STREAM: source export id -> destination id for each orphan.
    assert remap.resolve(EntityType.STREAM, 11) == 1001
    assert remap.resolve(EntityType.STREAM, 12) == 1002
    assert remap.resolve(EntityType.STREAM, 13) == 1003

    # ledger records each created stream under EntityType.STREAM.
    assert len(ledger.entries) == 3
    assert all(e.entity_type == EntityType.STREAM for e in ledger.entries)
    assert {e.destination_id for e in ledger.entries} == {1001, 1002, 1003}

    assert result.created_stream_ids == [1001, 1002, 1003]


# ---------------------------------------------------------------------------
# WARN fires on the fallback path, with the orphan count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warn_fires_with_orphan_count_on_fallback(caplog):
    """Observability requirement: the fallback WARN-logs EVERY time it fires,
    and the message includes the count of orphaned streams."""
    client = _client()
    report = _report()
    ledger = _ledger()
    remap = _remap()

    orphans = [_orphan(21), _orphan(22)]

    with caplog.at_level(logging.WARNING):
        await synthesize_custom_streams(
            orphans=orphans,
            client=client,
            remap=remap,
            ledger=ledger,
            report=report,
        )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a WARN log on the fallback path"
    # The orphan count (2) must appear in the rendered message.
    assert any("2" in r.getMessage() for r in warnings)
    assert any("[DBAS_RESTORE]" in r.getMessage() for r in warnings)


# ---------------------------------------------------------------------------
# Idempotency: a second batch reuses the account, does NOT create a second one
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_batch_reuses_synthesized_account(caplog):
    """Idempotent find-or-create: after the first batch creates the account, a
    second batch (now seeing it in get_m3u_accounts) reuses it — it does NOT
    create a second synthesized account."""
    # First batch: no account exists yet -> create id 900.
    client = _client(existing_accounts=[], create_account_id=900)
    report = _report()
    ledger = _ledger()
    remap = _remap()

    first = await synthesize_custom_streams(
        orphans=[_orphan(31)],
        client=client,
        remap=remap,
        ledger=ledger,
        report=report,
    )
    assert first.account_id == 900
    client.create_m3u_account.assert_awaited_once()

    # Second batch: the synthesized account is now present on the destination.
    client.get_m3u_accounts = AsyncMock(
        return_value=[{"id": 900, "name": CUSTOM_STREAM_ACCOUNT_NAME}]
    )
    second = await synthesize_custom_streams(
        orphans=[_orphan(32)],
        client=client,
        remap=remap,
        ledger=ledger,
        report=report,
    )

    assert second.account_id == 900
    # No SECOND create — still only the one from the first batch.
    client.create_m3u_account.assert_awaited_once()


# ---------------------------------------------------------------------------
# Account-already-exists (pre-existing before any batch): reused, not recreated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_account_already_exists_is_reused_not_recreated():
    """If the synthesized custom-stream account already exists on the
    destination (e.g. from a prior restore run), the fallback reuses it by id
    and never calls create_m3u_account."""
    client = _client(
        existing_accounts=[
            {"id": 7, "name": "Some Other Provider"},
            {"id": 8, "name": CUSTOM_STREAM_ACCOUNT_NAME},
        ]
    )
    report = _report()
    ledger = _ledger()
    remap = _remap()

    result = await synthesize_custom_streams(
        orphans=[_orphan(41)],
        client=client,
        remap=remap,
        ledger=ledger,
        report=report,
    )

    assert result.account_id == 8
    client.create_m3u_account.assert_not_awaited()
    # Orphan still created + attributed to the reused account.
    payload = client.create_stream.await_args_list[0].args[0]
    assert payload["m3u_account"] == 8


# ---------------------------------------------------------------------------
# Rollback ledger records every created orphan stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_ledger_records_created_orphans():
    """Every synthesized orphan stream is recorded in the RollbackLedger so a
    later restore failure can compensate by deleting them (reverse order)."""
    client = _client()
    report = _report()
    ledger = _ledger()
    remap = _remap()

    orphans = [_orphan(51, "X"), _orphan(52, "Y")]
    await synthesize_custom_streams(
        orphans=orphans,
        client=client,
        remap=remap,
        ledger=ledger,
        report=report,
    )

    assert len(ledger.entries) == 2
    # Compensation order is reverse-creation (descending sequence).
    order = ledger.compensation_order()
    assert [e.destination_id for e in order] == [1002, 1001]
    assert all(e.entity_type == EntityType.STREAM for e in order)


# ---------------------------------------------------------------------------
# A create that returns no usable id is reported failed, not ledgered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_orphan_create_is_reported_not_ledgered(caplog):
    """If create_stream raises for one orphan, that orphan is reported failed
    (RestoreReport CHANNEL... no: STREAM category) and is NOT recorded in the
    ledger/remap, while the other orphans still succeed."""
    async def _flaky_create(payload):
        if payload.get("name") == "boom":
            raise Exception("Stream creation failed: 400 - bad payload")
        return {"id": 2000, **payload}

    client = _client(create_side_effect=_flaky_create)
    report = _report()
    ledger = _ledger()
    remap = _remap()

    orphans = [_orphan(61, "boom"), _orphan(62, "ok")]

    with caplog.at_level(logging.WARNING):
        result = await synthesize_custom_streams(
            orphans=orphans,
            client=client,
            remap=remap,
            ledger=ledger,
            report=report,
        )

    # Only the good one is ledgered/remapped.
    assert len(ledger.entries) == 1
    assert ledger.entries[0].destination_id == 2000
    assert remap.resolve(EntityType.STREAM, 62) == 2000
    assert remap.resolve(EntityType.STREAM, 61) is None
    assert result.created_stream_ids == [2000]

    cat = report.category(EntityType.STREAM)
    assert cat.created == 1
    assert cat.failed == 1
    assert cat.failure_details[0].source_export_id == 61
