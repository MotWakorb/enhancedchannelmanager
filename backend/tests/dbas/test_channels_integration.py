"""Integration tests for the channels importer stream layer
(enhancedchannelmanager-0i2vt.14 — the integration umbrella).

This umbrella WIRES the four merged splits together inside
``dbas.importers.channels.import_channels``:

* 4vouz — channel-row create + profile reattach (already covered by
  ``test_channels_importer.py``; this file must NOT regress it).
* al6e3 — the pure 4-tier ``match_stream`` matcher (used real, not mocked —
  it is pure, so feeding it real candidate lists is the strongest test).
* ahygg — ``synthesize_custom_streams`` for MISS orphans (mocked at the
  channels module level so we can assert it is called with exactly the orphans
  and thread one fallback state across the whole import).
* nav0c — ``create_stream`` / attach client methods (the attach is
  ``client.update_channel(dest_channel_id, {"streams": [ordered_ids]})``).

What this layer adds on top of 4vouz: for each restored channel, the archived
embedded streams are matched against the destination's existing streams; tier
1-4 hits are attached by their matched destination id; MISS (tier 0) streams are
collected as orphans and routed to ``synthesize_custom_streams`` (once, batched
across the whole import, threading one fallback ``state`` so the synthesized
account is created at most once). The final per-channel stream ordering mirrors
the archived order. Matched-existing streams are attached but NEVER
created/ledgered (they already exist); synthesized orphans ARE ledgered (ahygg).

The Dispatcharr client and the fallback synthesizer are mocked at the channels
module level; the real pure matcher runs against real candidate lists.
"""
import pytest
from unittest.mock import AsyncMock, patch

import dbas.importers.channels as channels_mod
from dbas.importers.channels import import_channels
from dbas.custom_stream_fallback import CustomStreamFallbackResult
from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client(*, existing_channels=None, dest_streams=None):
    """AsyncMock client.

    ``dest_streams`` is the destination instance's existing streams — the
    candidate set fed to the real matcher via ``get_streams``.
    """
    client = AsyncMock()
    client.get_channels = AsyncMock(
        return_value={"results": existing_channels or [], "count": len(existing_channels or [])}
    )
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.update_profile_channel = AsyncMock(return_value={"success": True})
    client.delete_channel = AsyncMock(return_value=None)

    # get_streams returns the paginated shape {"results": [...]} that the
    # importer unwraps to a candidate list for the matcher.
    streams = dest_streams or []
    client.get_streams = AsyncMock(
        return_value={"results": streams, "count": len(streams)}
    )

    created_counter = {"n": 500}

    async def _default_create(payload):
        created_counter["n"] += 1
        return {"id": created_counter["n"], **payload}

    client.create_channel = AsyncMock(side_effect=_default_create)
    client.update_channel = AsyncMock(return_value={"success": True})
    return client


def _report(is_dry_run=False):
    return RestoreReport(is_dry_run=is_dry_run)


def _ledger():
    return RollbackLedger(restore_id="test-restore")


def _remap():
    return IdRemapTable()


def _channel(channel_id, name, streams, number=None):
    ch = {"id": channel_id, "name": name, "streams": streams}
    if number is not None:
        ch["channel_number"] = number
    return ch


def _patched_fallback(synth_mock):
    """Patch synthesize_custom_streams at the channels module level."""
    return patch.object(channels_mod, "synthesize_custom_streams", synth_mock)


# ---------------------------------------------------------------------------
# All streams match (tiers 1-4) — attach by matched id, NO fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_streams_match_attached_no_fallback():
    """A channel whose archived streams ALL match destination streams (tier 1-4)
    has each matched destination id attached to the created channel via
    update_channel; the fallback synthesizer is NEVER called; no synthesized
    account is created."""
    # Destination streams: exact-URL matches for both archived streams.
    dest_streams = [
        {"id": 9001, "name": "CNN", "url": "http://prov/cnn", "m3u_account": 1},
        {"id": 9002, "name": "ESPN", "url": "http://prov/espn", "m3u_account": 1},
    ]
    client = _client(dest_streams=dest_streams)
    report, ledger, remap = _report(), _ledger(), _remap()

    synth = AsyncMock(return_value=CustomStreamFallbackResult())
    with _patched_fallback(synth):
        await import_channels(
            archive_channels=[
                _channel(
                    5,
                    "CNN+ESPN",
                    [
                        {"id": 1, "name": "CNN", "url": "http://prov/cnn"},
                        {"id": 2, "name": "ESPN", "url": "http://prov/espn"},
                    ],
                )
            ],
            client=client,
            selected=True,
            report=report,
            ledger=ledger,
            remap=remap,
        )

    # Fallback never fired — every stream matched.
    synth.assert_not_awaited()

    # The created channel (dest id 501) was attached the two matched dest ids,
    # in archived order.
    dest_channel_id = remap.resolve(EntityType.CHANNEL, 5)
    assert dest_channel_id == 501
    client.update_channel.assert_awaited_once_with(501, {"streams": [9001, 9002]})

    # Matched-existing streams are NOT created/ledgered.
    assert ledger.entries[0].entity_type == EntityType.CHANNEL  # only the channel
    assert all(e.entity_type != EntityType.STREAM for e in ledger.entries)


# ---------------------------------------------------------------------------
# Some MISS — matched attached, MISS routed to fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_some_miss_routes_only_orphans_to_fallback():
    """A channel with SOME MISS streams: matched ones attach by matched id, MISS
    ones are passed to synthesize_custom_streams (exactly the orphans), and the
    synthesized ids are attached back to the channel."""
    dest_streams = [
        {"id": 9001, "name": "CNN", "url": "http://prov/cnn", "m3u_account": 1},
    ]
    client = _client(dest_streams=dest_streams)
    report, ledger, remap = _report(), _ledger(), _remap()

    orphan = {"id": 2, "name": "Local Cam", "url": "http://home/cam"}

    async def _synth(*, orphans, client, remap, ledger, report, state=None):
        # synthesize the orphan as dest id 7777 and remap it.
        remap.add(EntityType.STREAM, 2, 7777)
        ledger.record_created(EntityType.STREAM, 7777, "Local Cam")
        return CustomStreamFallbackResult(
            account_id=4242, created_stream_ids=[7777], orphan_count=len(orphans)
        )

    synth = AsyncMock(side_effect=_synth)
    with _patched_fallback(synth):
        await import_channels(
            archive_channels=[
                _channel(
                    5,
                    "Mixed",
                    [
                        {"id": 1, "name": "CNN", "url": "http://prov/cnn"},  # tier1 hit
                        orphan,  # MISS
                    ],
                )
            ],
            client=client,
            selected=True,
            report=report,
            ledger=ledger,
            remap=remap,
        )

    # Fallback called with EXACTLY the orphan(s).
    synth.assert_awaited_once()
    passed_orphans = synth.await_args.kwargs["orphans"]
    assert list(passed_orphans) == [orphan]

    # Channel attached: matched 9001 first, synthesized 7777 second (archived order).
    client.update_channel.assert_awaited_once_with(501, {"streams": [9001, 7777]})


@pytest.mark.asyncio
async def test_warn_fires_on_fallback(caplog):
    """The fallback path WARNs (ahygg's own warning) when it fires. We assert the
    real synthesizer emits its WARN by invoking the REAL synthesize_custom_streams
    end-to-end with a create_stream-capable client."""
    import logging

    dest_streams = []  # nothing matches -> all MISS
    client = _client(dest_streams=dest_streams)
    # real synthesizer needs m3u-account + create_stream.
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.create_m3u_account = AsyncMock(return_value={"id": 4242})
    client.create_stream = AsyncMock(return_value={"id": 7777})

    report, ledger, remap = _report(), _ledger(), _remap()

    with caplog.at_level(logging.WARNING):
        await import_channels(
            archive_channels=[
                _channel(5, "Orphan", [{"id": 2, "name": "Cam", "url": "http://home/cam"}])
            ],
            client=client,
            selected=True,
            report=report,
            ledger=ledger,
            remap=remap,
        )

    assert any(
        "synthesizing custom-stream" in rec.message.lower()
        or "stream matcher miss" in rec.message.lower()
        for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# All MISS — every stream goes to fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_miss_all_to_fallback():
    """A channel with no matching destination streams routes ALL its archived
    streams to the fallback."""
    client = _client(dest_streams=[])  # empty candidates -> all MISS
    report, ledger, remap = _report(), _ledger(), _remap()

    o1 = {"id": 1, "name": "A", "url": "http://x/a"}
    o2 = {"id": 2, "name": "B", "url": "http://x/b"}

    async def _synth(*, orphans, client, remap, ledger, report, state=None):
        ids = {1: 8001, 2: 8002}
        created = []
        for o in orphans:
            d = ids[o["id"]]
            remap.add(EntityType.STREAM, o["id"], d)
            created.append(d)
        return CustomStreamFallbackResult(
            account_id=4242, created_stream_ids=created, orphan_count=len(orphans)
        )

    synth = AsyncMock(side_effect=_synth)
    with _patched_fallback(synth):
        await import_channels(
            archive_channels=[_channel(5, "AllMiss", [o1, o2])],
            client=client,
            selected=True,
            report=report,
            ledger=ledger,
            remap=remap,
        )

    synth.assert_awaited_once()
    assert list(synth.await_args.kwargs["orphans"]) == [o1, o2]
    client.update_channel.assert_awaited_once_with(501, {"streams": [8001, 8002]})


# ---------------------------------------------------------------------------
# Fallback state threaded — account synthesized ONCE across channels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_state_threaded_once_across_channels():
    """Two channels both produce orphans. The fallback is invoked with one shared
    ``state`` so the synthesized account is created at most once across the whole
    import (state threaded, not a fresh state per channel/batch)."""
    client = _client(dest_streams=[])
    report, ledger, remap = _report(), _ledger(), _remap()

    seen_states = []

    async def _synth(*, orphans, client, remap, ledger, report, state=None):
        seen_states.append(state)
        return CustomStreamFallbackResult(orphan_count=len(orphans))

    synth = AsyncMock(side_effect=_synth)
    with _patched_fallback(synth):
        await import_channels(
            archive_channels=[
                _channel(5, "Ch1", [{"id": 1, "name": "A", "url": "http://x/a"}]),
                _channel(6, "Ch2", [{"id": 2, "name": "B", "url": "http://x/b"}]),
            ],
            client=client,
            selected=True,
            report=report,
            ledger=ledger,
            remap=remap,
        )

    # However the importer batches, the SAME non-None state object must be
    # threaded through every fallback invocation (so the account is created once).
    assert seen_states, "fallback was never invoked"
    assert all(s is not None for s in seen_states)
    first = seen_states[0]
    assert all(s is first for s in seen_states)


# ---------------------------------------------------------------------------
# Ordering preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_ordering_preserved_mixed():
    """The attached stream order mirrors the archived order, interleaving matched
    and synthesized ids."""
    dest_streams = [
        {"id": 9001, "name": "A", "url": "http://x/a", "m3u_account": 1},
        {"id": 9003, "name": "C", "url": "http://x/c", "m3u_account": 1},
    ]
    client = _client(dest_streams=dest_streams)
    report, ledger, remap = _report(), _ledger(), _remap()

    async def _synth(*, orphans, client, remap, ledger, report, state=None):
        # B (id 2) is the only orphan -> synthesized 8002.
        for o in orphans:
            remap.add(EntityType.STREAM, o["id"], 8002)
        return CustomStreamFallbackResult(
            account_id=42, created_stream_ids=[8002], orphan_count=len(orphans)
        )

    synth = AsyncMock(side_effect=_synth)
    with _patched_fallback(synth):
        await import_channels(
            archive_channels=[
                _channel(
                    5,
                    "Ordered",
                    [
                        {"id": 1, "name": "A", "url": "http://x/a"},  # -> 9001
                        {"id": 2, "name": "B", "url": "http://x/b"},  # MISS -> 8002
                        {"id": 3, "name": "C", "url": "http://x/c"},  # -> 9003
                    ],
                )
            ],
            client=client,
            selected=True,
            report=report,
            ledger=ledger,
            remap=remap,
        )

    # archived order A, B, C -> dest ids 9001, 8002, 9003.
    client.update_channel.assert_awaited_once_with(501, {"streams": [9001, 8002, 9003]})


# ---------------------------------------------------------------------------
# Matched-existing NOT ledgered; synthesized ARE ledgered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matched_existing_not_ledgered_synthesized_are():
    """Matched (existing) destination streams are attached but NOT created or
    ledgered (they already exist). Synthesized orphans ARE ledgered (ahygg does
    that). We use the REAL synthesizer to assert the ledger end-to-end."""
    dest_streams = [
        {"id": 9001, "name": "Match", "url": "http://x/match", "m3u_account": 1},
    ]
    client = _client(dest_streams=dest_streams)
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.create_m3u_account = AsyncMock(return_value={"id": 4242})
    client.create_stream = AsyncMock(return_value={"id": 7777})

    report, ledger, remap = _report(), _ledger(), _remap()

    await import_channels(
        archive_channels=[
            _channel(
                5,
                "Mix",
                [
                    {"id": 1, "name": "Match", "url": "http://x/match"},  # matched 9001
                    {"id": 2, "name": "Orphan", "url": "http://x/orphan"},  # MISS
                ],
            )
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    stream_ledger = [e for e in ledger.entries if e.entity_type == EntityType.STREAM]
    # Only the synthesized orphan (7777) is ledgered — NOT the matched 9001.
    assert len(stream_ledger) == 1
    assert stream_ledger[0].destination_id == 7777
    assert all(e.destination_id != 9001 for e in stream_ledger)

    # The channel got both: matched 9001 + synthesized 7777, archived order.
    client.update_channel.assert_awaited_once_with(501, {"streams": [9001, 7777]})


# ---------------------------------------------------------------------------
# Dry-run — no creates, no attach side-effects, no synthesized account
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_no_attach_no_synthesis():
    """A dry-run issues no create_channel, no update_channel (attach), and never
    calls the fallback synthesizer."""
    client = _client(dest_streams=[{"id": 9001, "name": "A", "url": "http://x/a"}])
    report, ledger, remap = _report(is_dry_run=True), _ledger(), _remap()

    synth = AsyncMock(return_value=CustomStreamFallbackResult())
    with _patched_fallback(synth):
        await import_channels(
            archive_channels=[
                _channel(
                    5,
                    "Dry",
                    [
                        {"id": 1, "name": "A", "url": "http://x/a"},
                        {"id": 2, "name": "B", "url": "http://x/b"},
                    ],
                )
            ],
            client=client,
            selected=True,
            report=report,
            ledger=ledger,
            remap=remap,
            is_dry_run=True,
        )

    client.create_channel.assert_not_awaited()
    client.update_channel.assert_not_awaited()
    synth.assert_not_awaited()
    assert ledger.entries == []


# ---------------------------------------------------------------------------
# A channel with no streams attaches nothing and fires no fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_without_streams_no_attach_no_fallback():
    """A channel with no archived streams: created, but no attach call and no
    fallback (the no-stream channel must not regress 4vouz's create path)."""
    client = _client(dest_streams=[{"id": 9001, "name": "A", "url": "http://x/a"}])
    report, ledger, remap = _report(), _ledger(), _remap()

    synth = AsyncMock(return_value=CustomStreamFallbackResult())
    with _patched_fallback(synth):
        await import_channels(
            archive_channels=[{"id": 5, "name": "NoStreams"}],
            client=client,
            selected=True,
            report=report,
            ledger=ledger,
            remap=remap,
        )

    assert report.category(EntityType.CHANNEL).created == 1
    client.update_channel.assert_not_awaited()
    synth.assert_not_awaited()


# ---------------------------------------------------------------------------
# Stream FLOOR (spike xp6mp ruling 1b) — sync path floors at Tier-3 exact;
# fuzzy is opt-in per target and reports low-confidence, never silent.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_floor_default_does_not_fuzzy_match_stream():
    """allow_fuzzy_stream_match=False (the sync default): a stream that only a
    Tier-4 fuzzy rung would catch is NOT attached — it routes to the fallback as
    an orphan (ruling 1b). No fuzzy guess silently shadows a real stream."""
    # Destination stream matches ONLY by fuzzy name (token-set), not exact.
    dest_streams = [
        {"id": 9001, "name": "ESPN East HD", "url": "http://prov/x", "m3u_account": 99},
    ]
    client = _client(dest_streams=dest_streams)
    report, ledger, remap = _report(), _ledger(), _remap()

    archived_stream = {"id": 1, "name": "ESPN HD East", "url": "http://prov/old"}

    captured = {}

    async def _synth(*, orphans, client, remap, ledger, report, state=None):
        captured["orphans"] = list(orphans)
        return CustomStreamFallbackResult(orphan_count=len(orphans))

    synth = AsyncMock(side_effect=_synth)
    with _patched_fallback(synth):
        await import_channels(
            archive_channels=[_channel(5, "ESPN", [archived_stream])],
            client=client,
            selected=True,
            report=report,
            ledger=ledger,
            remap=remap,
            allow_fuzzy_stream_match=False,
        )

    # Floored: the fuzzy-only candidate was NOT attached as an updated stream;
    # the archived stream was routed to the fallback as an orphan instead.
    synth.assert_awaited_once()
    assert captured["orphans"] == [archived_stream]
    assert report.category(EntityType.STREAM).updated == 0


@pytest.mark.asyncio
async def test_sync_fuzzy_opt_in_attaches_and_reports_low_confidence():
    """allow_fuzzy_stream_match=True (per-target opt-in): a Tier-4 fuzzy hit IS
    attached, but is flagged LOW-CONFIDENCE in report.notes — never a silent
    ``updated`` (ruling 1b)."""
    dest_streams = [
        {"id": 9001, "name": "ESPN East HD", "url": "http://prov/x", "m3u_account": 99},
    ]
    client = _client(dest_streams=dest_streams)
    report, ledger, remap = _report(), _ledger(), _remap()

    archived_stream = {"id": 1, "name": "ESPN HD East", "url": "http://prov/old"}

    synth = AsyncMock(return_value=CustomStreamFallbackResult())
    with _patched_fallback(synth):
        await import_channels(
            archive_channels=[_channel(5, "ESPN", [archived_stream])],
            client=client,
            selected=True,
            report=report,
            ledger=ledger,
            remap=remap,
            allow_fuzzy_stream_match=True,
        )

    # The fuzzy hit attached the matched dest id (9001); no orphan/fallback.
    synth.assert_not_awaited()
    dest_channel_id = remap.resolve(EntityType.CHANNEL, 5)
    client.update_channel.assert_awaited_once_with(dest_channel_id, {"streams": [9001]})
    assert report.category(EntityType.STREAM).updated == 1
    # NOT silent: a low-confidence note was surfaced for the fuzzy attach.
    assert any("low-confidence stream match (fuzzy)" in n for n in report.notes)
