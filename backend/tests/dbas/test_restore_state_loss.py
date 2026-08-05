"""Regression tests for the round-trip drill's silent-state-loss findings.

Beads ``enhancedchannelmanager-2o0cz`` (P0 — restored channels cannot play) and
``enhancedchannelmanager-dfkbn`` (P1 — logos / EPG links / profile membership /
ECM settings lost while the restore reports ``success, 0 failures``). Measured on
ECM 0.18.1-0022 against Dispatcharr 0.28.2 during drill run 2026-08-04-run1
(bead ``…-a429n``).

Each test below pins ONE loss the drill measured, at the layer that can prove it:

* :func:`test_m3u_group_enablement_round_trips` — the archived per-group
  ``enabled`` selection reaches the destination (drill: 1 of 375 enabled ->
  0 of 375, refresh then ingests nothing).
* :func:`test_placeholder_streams_are_rebound_to_real_provider_streams` — a
  channel bound to a URL-less placeholder is rebound once the real provider
  stream exists (drill: 110 real streams beside 12 unplayable channels).
* :func:`test_channels_that_still_cannot_play_are_counted_and_named` — what the
  rebind could NOT fix is a counted, named action item, never silent.
* :func:`test_unreinstatable_channel_logo_is_counted_as_a_logo_miss` — the test
  that would have caught ``logo_misses: 0`` while 12 channels lost their logo.
* :func:`test_epg_links_reattach_by_tvg_id` — a channel's ``epg_data_id`` comes
  back via its archived ``tvg_id`` (drill: 10 of 12 linked -> 0).
* :func:`test_epg_link_reattaches_when_only_the_resolved_natural_key_exists`:
  run 2's remaining half. A channel linked ONLY through ``epg_data_id``, with its
  OWN ``tvg_id`` null, still relinks (drill run-2: 7 of 7 linked -> 0).
* :func:`test_resolved_natural_key_wins_over_the_channels_own_tvg_id`: the
  preference order when the two disagree.
* :func:`test_old_artifact_without_a_resolved_key_still_uses_the_channels_own`:
  backward compatibility with an artifact produced before the fix.
* :func:`test_non_default_profile_membership_does_not_widen_to_all_channels` —
  a profile seeded to EXCLUDE channels does not restore containing all of them
  (drill: 9 of 12 -> 12 of 12).
* :func:`test_ecm_settings_round_trip` — ``user_timezone`` /
  ``stats_poll_interval`` survive (drill: both reverted to defaults).

Conventions: ``docs/pytest_conventions.md``; the Dispatcharr client is an
``AsyncMock`` (no live upstream) — these pin ECM's behaviour against the request
shapes verified from Dispatcharr 0.28.2's own source.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
)


def _client() -> AsyncMock:
    """A Dispatcharr client mock with the read methods every importer probes."""
    client = AsyncMock()
    client.get_m3u_accounts.return_value = []
    client.get_channels.return_value = {"results": []}
    client.get_streams.return_value = {"results": [], "count": 0}
    client.get_channel_groups.return_value = []
    client.get_channel_profiles.return_value = []
    client.get_all_logos_paginated.return_value = []
    client.get_epg_data.return_value = []
    return client


# ---------------------------------------------------------------------------
# FIX 1a — the M3U account's enabled-group selection (bead 2o0cz)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m3u_group_enablement_round_trips():
    """The archived per-group ``enabled`` flags reach the destination account.

    Drill evidence: the source account had exactly ONE of 375 groups enabled;
    after restore the account showed ``0 / 375`` and PENDING SETUP, so the
    refresh ingested nothing and blamed the provider
    (``No streams returned from Xtream Codes provider``).

    Root cause pinned here: the importer only deferred group settings when some
    group had ``auto_channel_sync`` set, so an account whose groups are merely
    ENABLED deferred nothing at all.
    """
    from dbas.importers.m3u_accounts import import_m3u_accounts

    client = _client()
    client.create_m3u_account.return_value = {"id": 77}

    report = RestoreReport(is_dry_run=False)
    ledger = RollbackLedger(restore_id="t")
    remap = IdRemapTable()

    archive = [
        {
            "id": 1,
            "name": "Provider",
            "channel_groups": [
                # The ONE enabled group — no auto_channel_sync anywhere.
                {"channel_group": 10, "enabled": True, "auto_channel_sync": False,
                 "custom_properties": {"xc_id": 42}},
                {"channel_group": 11, "enabled": False, "auto_channel_sync": False},
            ],
        }
    ]

    result = await import_m3u_accounts(
        archive_accounts=archive,
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=False,
    )

    assert result.deferred_auto_sync_settings, (
        "an account with an enabled-group selection must defer its group "
        "settings even when no group has auto_channel_sync"
    )
    groups = result.deferred_auto_sync_settings[0]["settings"]["channel_groups"]
    by_source = {g["channel_group"]: g for g in groups}
    assert by_source[10]["enabled"] is True
    assert by_source[11]["enabled"] is False
    # The provider category id must survive — an XC refresh filters streams by
    # the membership's ``custom_properties["xc_id"]`` (Dispatcharr 0.28.2
    # apps/m3u/tasks.py collect_xc_streams).
    assert by_source[10]["custom_properties"] == {"xc_id": 42}


@pytest.mark.asyncio
async def test_deferred_group_settings_remap_source_group_ids():
    """The deferred apply rewrites SOURCE group ids to DESTINATION ids.

    The archived membership rows carry the SOURCE instance's ``channel_group``
    pk. The M3U importer runs FIRST (before channel groups), so the remap is only
    populated by the time the DEFERRED phase runs — which is where the rewrite
    has to happen.
    """
    from dbas.importers.m3u_accounts import apply_deferred_auto_sync

    client = _client()
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL_GROUP, 10, 910)

    deferred = [
        {
            "m3u_account_id": 77,
            "settings": {
                "channel_groups": [
                    {"channel_group": 10, "enabled": True, "auto_channel_sync": False},
                    # No mapping for 11 — must never be sent with a stale source pk.
                    {"channel_group": 11, "enabled": False, "auto_channel_sync": False},
                ]
            },
        }
    ]

    async def _count(_account_id):
        return 0

    async def _sleep(_seconds):
        return None

    await apply_deferred_auto_sync(
        deferred=deferred,
        client=client,
        remap=remap,
        stream_count_fn=_count,
        sleep_fn=_sleep,
        max_polls=1,
    )

    client.update_m3u_group_settings.assert_awaited_once()
    _account, payload = client.update_m3u_group_settings.await_args.args
    sent = payload["group_settings"]
    assert [g["channel_group"] for g in sent] == [910]
    assert sent[0]["enabled"] is True


@pytest.mark.asyncio
async def test_group_settings_are_applied_before_the_refresh_is_triggered():
    """Group settings must land BEFORE the refresh, or the refresh ingests nothing.

    Dispatcharr's ``PATCH /api/m3u/accounts/<id>/group-settings/`` upserts the
    ``ChannelGroupM3UAccount`` rows (``bulk_create(update_conflicts=True)``), so
    it works on an account that has never refreshed. Applying it AFTER the
    refresh would leave the first refresh with zero enabled groups.
    """
    from dbas.importers.m3u_accounts import apply_deferred_auto_sync

    client = _client()
    calls: list[str] = []
    client.update_m3u_group_settings.side_effect = (
        lambda *a, **k: calls.append("group_settings")
    )
    client.refresh_m3u_account.side_effect = lambda *a, **k: calls.append("refresh")
    client.patch_m3u_account.side_effect = lambda *a, **k: calls.append("patch")

    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL_GROUP, 10, 910)

    async def _count(_account_id):
        return 0

    async def _sleep(_seconds):
        return None

    await apply_deferred_auto_sync(
        deferred=[{
            "m3u_account_id": 77,
            "settings": {"channel_groups": [{"channel_group": 10, "enabled": True}]},
        }],
        client=client,
        remap=remap,
        stream_count_fn=_count,
        sleep_fn=_sleep,
        max_polls=1,
    )

    assert calls.index("group_settings") < calls.index("refresh")


# ---------------------------------------------------------------------------
# FIX 1b — rebind channels off the URL-less placeholders (bead 2o0cz)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_placeholder_streams_are_rebound_to_real_provider_streams():
    """A channel bound to a placeholder is rebound once the real stream exists.

    Drill evidence: after the documented post-restore M3U refresh the destination
    held 110 REAL provider streams AND 12 channels still bound to their URL-less
    placeholders — nothing re-ran the matcher, so not one channel could play.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client = _client()
    # The destination after the deferred refresh: the placeholder (no url) that
    # the restore synthesized, plus the REAL provider stream it stands in for.
    client.get_streams.return_value = {
        "results": [
            {"id": 500, "name": "US: FOX News Channel FHD", "url": None, "m3u_account": 3},
            {"id": 900, "name": "US: FOX News Channel FHD",
             "url": "http://p/live/1", "m3u_account": 1},
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 201, "name": "FOX News", "streams": [500]}]
    }

    report = RestoreReport(is_dry_run=False)
    ledger = RollbackLedger(restore_id="t")
    ledger.record_created(EntityType.STREAM, 500, "US: FOX News Channel FHD")
    ledger.record_created(EntityType.M3U_ACCOUNT, 3, "ECM Custom Streams (DBAS restore)")

    remap = IdRemapTable()
    remap.add(EntityType.STREAM, 7, 500)
    remap.add(EntityType.CHANNEL, 101, 201)

    archive_channels = [
        {
            "id": 101,
            "name": "FOX News",
            "streams": [{"id": 7, "name": "US: FOX News Channel FHD"}],
        }
    ]

    await rebind_placeholder_streams(
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        archive_channels=archive_channels,
    )

    client.update_channel.assert_awaited_once_with(201, {"streams": [900]})
    assert report.streams_rebound == 1
    # The orphaned placeholder and the now-empty synthetic account are removed.
    client.delete_stream.assert_awaited_once_with(500)
    client.delete_m3u_account.assert_awaited_once_with(3)
    assert report.channels_needing_stream_reattach == 0


@pytest.mark.asyncio
async def test_rebind_preserves_order_and_never_drops_an_already_matched_stream():
    """A mixed channel keeps its real streams, its order, and loses only placeholders.

    The multi-stream case the drill exercised: the channel was seeded with three
    ordered streams and the order survived exactly, so the rebind must rewrite
    placeholder slots IN PLACE. It must also start from the DESTINATION's own
    stream list — a stream the importer matched for real is not in the ``STREAM``
    remap (only synthesized orphans are), so a list rebuilt from the remap would
    silently drop it.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client = _client()
    client.get_streams.return_value = {
        "results": [
            {"id": 800, "name": "FOX News HD", "url": "http://p/hd", "m3u_account": 1},
            {"id": 500, "name": "US: FOX News Channel FHD", "url": None, "m3u_account": 3},
            {"id": 900, "name": "US: FOX News Channel FHD", "url": "http://p/fhd",
             "m3u_account": 1},
        ]
    }
    # Slot order on the destination: real, placeholder, real.
    client.get_channels.return_value = {
        "results": [{"id": 201, "name": "FOX", "streams": [800, 500, 800]}]
    }

    report = RestoreReport(is_dry_run=False)
    ledger = RollbackLedger(restore_id="t")
    ledger.record_created(EntityType.STREAM, 500, "US: FOX News Channel FHD")
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)
    remap.add(EntityType.STREAM, 7, 500)  # only the ORPHAN is remapped

    await rebind_placeholder_streams(
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        archive_channels=[
            {
                "id": 101,
                "name": "FOX",
                "streams": [
                    {"id": 6, "name": "FOX News HD"},
                    {"id": 7, "name": "US: FOX News Channel FHD"},
                    {"id": 8, "name": "FOX News HD"},
                ],
            }
        ],
    )

    client.update_channel.assert_awaited_once_with(201, {"streams": [800, 900, 800]})
    assert report.channels_needing_stream_reattach == 0


@pytest.mark.asyncio
async def test_channels_that_still_cannot_play_are_counted_and_named():
    """A channel left on a URL-less placeholder is a counted, NAMED action item.

    The thing this whole bead removes is a restore that says ``success, 0
    failures`` for an instance that plays nothing.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client = _client()
    # Only the placeholder exists — the provider never materialized a match.
    client.get_streams.return_value = {
        "results": [{"id": 500, "name": "Obscure Channel", "url": None, "m3u_account": 3}]
    }
    client.get_channels.return_value = {
        "results": [{"id": 201, "name": "Obscure", "streams": [500]}]
    }

    report = RestoreReport(is_dry_run=False)
    ledger = RollbackLedger(restore_id="t")
    ledger.record_created(EntityType.STREAM, 500, "Obscure Channel")
    remap = IdRemapTable()
    remap.add(EntityType.STREAM, 7, 500)
    remap.add(EntityType.CHANNEL, 101, 201)

    await rebind_placeholder_streams(
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        archive_channels=[
            {"id": 101, "name": "Obscure",
             "streams": [{"id": 7, "name": "Obscure Channel"}]}
        ],
    )

    assert report.channels_needing_stream_reattach == 1
    detail = report.stream_reattach_details[0]
    assert detail.name == "Obscure"
    assert detail.channel_id == 201
    assert detail.placeholder_streams == ["Obscure Channel"]
    # A still-referenced placeholder is NEVER deleted.
    client.delete_stream.assert_not_awaited()


# ---------------------------------------------------------------------------
# FIX 2.1 — logos (bead dfkbn item 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unreinstatable_channel_logo_is_counted_as_a_logo_miss():
    """The test that would have caught ``logo_misses: 0`` while 12 logos vanished.

    Drill evidence: every channel came back with ``logo_id=None`` and the report
    said ``logo created=0 ... logo_misses=0``.
    """
    from dbas.channel_reattach import reattach_channel_logos

    client = _client()
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)
    # No LOGO mapping — the archived logo could not be reinstated.

    await reattach_channel_logos(
        # Predates the mode: no population information, so nothing
        # is preserved and the pass behaves as it always did.
        created_source_ids=None,
        client=client,
        report=report,
        remap=remap,
        archive_channels=[{"id": 101, "name": "FOX News", "logo_id": 55}],
    )

    assert report.logo_misses == 1
    assert len(report.logo_miss_details) == report.logo_misses
    miss = report.logo_miss_details[0]
    assert miss.source_export_id == 55
    assert [c.name for c in miss.channels] == ["FOX News"]
    assert [c.channel_id for c in miss.channels] == [201]
    client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_reinstatable_channel_logo_is_reattached_and_not_a_miss():
    """A logo the restore DID put back is reattached to its channel, silently."""
    from dbas.channel_reattach import reattach_channel_logos

    client = _client()
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)
    remap.add(EntityType.LOGO, 55, 955)

    await reattach_channel_logos(
        # Predates the mode: no population information, so nothing
        # is preserved and the pass behaves as it always did.
        created_source_ids=None,
        client=client,
        report=report,
        remap=remap,
        archive_channels=[{"id": 101, "name": "FOX News", "logo_id": 55}],
    )

    client.update_channel.assert_awaited_once_with(201, {"logo_id": 955})
    assert report.logo_misses == 0


@pytest.mark.asyncio
async def test_remote_url_logo_is_recreated_rather_than_lost():
    """A CDN-URL logo carries no bytes; the restore re-creates it BY URL.

    Drill evidence: 12 of the 13 lost logos were remote CDN URLs whose URLs ARE
    archived but which nothing re-resolved on restore.
    """
    from dbas.importers.logos import import_logos

    client = _client()
    client.create_logo.return_value = {"id": 955}

    report = RestoreReport(is_dry_run=False)
    ledger = RollbackLedger(restore_id="t")
    remap = IdRemapTable()

    await import_logos(
        archive_logos=[{"id": 55, "name": "FOX", "url": "https://cdn.example/fox.png"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=False,
    )

    client.create_logo.assert_awaited_once_with(
        {"name": "FOX", "url": "https://cdn.example/fox.png"}
    )
    assert remap.resolve(EntityType.LOGO, 55) == 955


# ---------------------------------------------------------------------------
# FIX 2.2 — EPG links (bead dfkbn item 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_epg_links_reattach_by_tvg_id():
    """A restored channel is relinked to its EPG row through the archived tvg_id.

    Drill evidence: the EPG SOURCE restored fine (14,668 entries) but every
    restored channel had ``epg_data_id=None``.
    """
    from dbas.channel_reattach import reattach_epg_links

    client = _client()
    client.get_epg_data.return_value = [
        {"id": 4001, "tvg_id": "fox.news.us", "name": "FOX News"},
        {"id": 4002, "tvg_id": "cnn.us", "name": "CNN"},
    ]

    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)

    await reattach_epg_links(
        # Predates the mode: no population information, so nothing
        # is preserved and the pass behaves as it always did.
        created_source_ids=None,
        client=client,
        report=report,
        remap=remap,
        archive_channels=[
            {"id": 101, "name": "FOX News", "tvg_id": "fox.news.us", "epg_data_id": 88},
        ],
    )

    client.update_channel.assert_awaited_once_with(201, {"epg_data_id": 4001})
    assert report.epg_links_unrestored == 0


@pytest.mark.asyncio
async def test_unresolvable_epg_link_is_counted_and_named():
    """A channel whose archived tvg_id matches nothing is counted, not silent."""
    from dbas.channel_reattach import reattach_epg_links

    client = _client()
    client.get_epg_data.return_value = [{"id": 4002, "tvg_id": "cnn.us"}]

    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)

    await reattach_epg_links(
        # Predates the mode: no population information, so nothing
        # is preserved and the pass behaves as it always did.
        created_source_ids=None,
        client=client,
        report=report,
        remap=remap,
        archive_channels=[
            {"id": 101, "name": "FOX News", "tvg_id": "fox.news.us", "epg_data_id": 88},
        ],
    )

    assert report.epg_links_unrestored == 1
    detail = report.epg_link_miss_details[0]
    assert detail.name == "FOX News"
    assert detail.tvg_id == "fox.news.us"
    client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_epg_link_reattaches_when_only_the_resolved_natural_key_exists():
    """A channel linked ONLY via ``epg_data_id`` still relinks (dfkbn run-2).

    Drill run 2026-08-04-run2 measured all 7 EPG-linked channels coming back
    with ``epg_data_id=None`` on BOTH artifact variants. Their archived rows
    looked exactly like this one: ``epg_data_id=2078``, ``tvg_id=None``. ECM's
    own channel PATCH produces that shape: setting ``epg_data_id`` does not
    populate ``tvg_id``. The reattach therefore had nothing to match on and
    dropped every link. The natural key now arrives resolved from the producer.
    """
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links

    client = _client()
    client.get_epg_data.return_value = [
        {"id": 4001, "tvg_id": "fox.news.us", "name": "FOX News"},
    ]

    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)

    relinked = await reattach_epg_links(
        # Predates the mode: no population information, so nothing
        # is preserved and the pass behaves as it always did.
        created_source_ids=None,
        client=client,
        report=report,
        remap=remap,
        archive_channels=[
            {
                "id": 101,
                "name": "FOX News",
                "tvg_id": None,
                "epg_data_id": 2078,
                ARCHIVE_EPG_TVG_ID_KEY: "fox.news.us",
            },
        ],
    )

    assert relinked == 1
    client.update_channel.assert_awaited_once_with(201, {"epg_data_id": 4001})
    assert report.epg_links_unrestored == 0


@pytest.mark.asyncio
async def test_resolved_natural_key_wins_over_the_channels_own_tvg_id():
    """When the two disagree, the row the operator LINKED is the one restored.

    A channel's own ``tvg_id`` is its own field; the link's identity belongs to
    the EPG row it points at. An operator can link a channel to a guide row
    whose tvg_id differs from the one the channel advertises, and that choice is
    the state to restore.
    """
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links

    client = _client()
    client.get_epg_data.return_value = [
        {"id": 4001, "tvg_id": "fox.news.us"},
        {"id": 4002, "tvg_id": "fox.business.us"},
    ]

    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)

    await reattach_epg_links(
        # Predates the mode: no population information, so nothing
        # is preserved and the pass behaves as it always did.
        created_source_ids=None,
        client=client,
        report=report,
        remap=remap,
        archive_channels=[
            {
                "id": 101,
                "name": "FOX Business",
                "tvg_id": "fox.news.us",
                "epg_data_id": 2078,
                ARCHIVE_EPG_TVG_ID_KEY: "fox.business.us",
            },
        ],
    )

    client.update_channel.assert_awaited_once_with(201, {"epg_data_id": 4002})


@pytest.mark.asyncio
async def test_old_artifact_without_a_resolved_key_still_uses_the_channels_own():
    """BACKWARD COMPATIBILITY: a pre-fix artifact restores exactly as before.

    An artifact built before the producer resolved the natural key carries no
    ``epg_data_tvg_id`` at all. The channel's own ``tvg_id``, the only key such
    an artifact has, must still drive the relink, and a channel whose own
    ``tvg_id`` is blank must still degrade to a counted, named miss.
    """
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links

    client = _client()
    client.get_epg_data.return_value = [{"id": 4001, "tvg_id": "fox.news.us"}]

    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)
    remap.add(EntityType.CHANNEL, 102, 202)

    old_shape_channels = [
        {"id": 101, "name": "FOX News", "tvg_id": "fox.news.us", "epg_data_id": 88},
        {"id": 102, "name": "CNN", "tvg_id": "", "epg_data_id": 89},
    ]
    assert not any(ARCHIVE_EPG_TVG_ID_KEY in ch for ch in old_shape_channels)

    relinked = await reattach_epg_links(
        # Predates the mode: no population information, so nothing
        # is preserved and the pass behaves as it always did.
        created_source_ids=None,
        client=client,
        report=report,
        remap=remap,
        archive_channels=old_shape_channels,
    )

    assert relinked == 1
    client.update_channel.assert_awaited_once_with(201, {"epg_data_id": 4001})
    assert report.epg_links_unrestored == 1
    assert report.epg_link_miss_details[0].name == "CNN"


# ---------------------------------------------------------------------------
# FIX 2.3 — channel-profile membership (bead dfkbn item 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_default_profile_membership_does_not_widen_to_all_channels():
    """A profile built to EXCLUDE channels restores still excluding them.

    Drill evidence: profile ``Drill Subset`` was seeded with 9 of 12 channels
    (101/102/103 deliberately excluded) and restored containing ALL 12 — because
    Dispatcharr's channel create adds the new channel to EVERY profile with
    ``enabled=True`` (0.28.2 ``apps/channels/api_views.py``) and nothing then
    disabled the three.
    """
    from dbas.channel_reattach import reattach_profile_memberships

    client = _client()
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    for src, dest in ((101, 201), (102, 202), (104, 204)):
        remap.add(EntityType.CHANNEL, src, dest)
    remap.add(EntityType.CHANNEL_PROFILE, 5, 905)

    await reattach_profile_memberships(
        client=client,
        report=report,
        remap=remap,
        # Archived profile: only source channel 104 is enabled.
        archive_profiles=[{"id": 5, "name": "Drill Subset", "channels": [104]}],
        archive_channels=[
            {"id": 101, "name": "A"}, {"id": 102, "name": "B"}, {"id": 104, "name": "D"},
        ],
    )

    calls = {
        (c.args[0], c.args[1]): c.args[2]
        for c in client.update_profile_channel.await_args_list
    }
    assert calls[(905, 201)] == {"enabled": False}
    assert calls[(905, 202)] == {"enabled": False}
    assert calls[(905, 204)] == {"enabled": True}
    # Two channels had to be corrected away from Dispatcharr's enable-everything
    # default — that drift is counted, not silent.
    assert report.profile_membership_drift == 2


@pytest.mark.asyncio
async def test_profile_membership_reports_nothing_when_already_correct():
    """No drift is reported when the archived membership is all-channels."""
    from dbas.channel_reattach import reattach_profile_memberships

    client = _client()
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)
    remap.add(EntityType.CHANNEL_PROFILE, 5, 905)

    await reattach_profile_memberships(
        client=client,
        report=report,
        remap=remap,
        archive_profiles=[{"id": 5, "name": "All", "channels": [101]}],
        archive_channels=[{"id": 101, "name": "A"}],
    )

    assert report.profile_membership_drift == 0


# ---------------------------------------------------------------------------
# FIX 2.4 — ECM's own settings (bead dfkbn item 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebind_runs_after_the_deferred_refresh(tmp_path):
    """The orchestrator runs the rebind AFTER the deferred phase, never before.

    Order is the whole point: the deferred phase is what triggers the M3U refresh
    and polls until the destination stream count stabilizes, so it is the first
    moment there are real provider streams to match against. Rebinding earlier
    would find nothing and leave every channel on its placeholder — the state the
    drill measured.
    """
    from dbas.preflight import ImportPlan, PlanCategory
    from dbas import restore_orchestrator as orch

    calls: list[str] = []

    async def _deferred(*, deferred, client, **_kwargs):
        calls.append("deferred")
        return [{"m3u_account_id": 1}]

    async def _fake_rebind(**_kwargs):
        calls.append("rebind")

    async def _m3u_step(ctx):
        return [{"m3u_account_id": 1, "settings": {}}]

    plan = ImportPlan(
        manifest={"schema_version": 1},
        categories=[
            PlanCategory(
                entity_type=EntityType.CHANNEL,
                entities=[{"id": 1, "name": "A", "streams": []}],
            )
        ],
    )
    import dbas.placeholder_rebind as rebind_mod

    original = rebind_mod.rebind_placeholder_streams
    rebind_mod.rebind_placeholder_streams = _fake_rebind
    try:
        report = await orch.run_restore(
            plan=plan,
            client=_client(),
            steps=[orch.ImporterStep(EntityType.M3U_ACCOUNT, _m3u_step, defers=True)],
            report=RestoreReport(is_dry_run=False),
            ledger=RollbackLedger(restore_id="t"),
            remap=IdRemapTable(),
            confirm_apply=True,
            deferred_apply_fn=_deferred,
            ledger_dir=tmp_path,
        )
    finally:
        rebind_mod.rebind_placeholder_streams = original

    assert calls == ["deferred", "rebind"]
    assert report.outcome.value == "success"


@pytest.mark.asyncio
async def test_dry_run_never_rebinds(tmp_path):
    """A dry-run mutates nothing — including the rebind pass."""
    from dbas.preflight import ImportPlan, PlanCategory
    from dbas import restore_orchestrator as orch

    calls: list[str] = []

    async def _fake_rebind(**_kwargs):
        calls.append("rebind")

    plan = ImportPlan(
        manifest={"schema_version": 1},
        categories=[
            PlanCategory(
                entity_type=EntityType.CHANNEL,
                entities=[{"id": 1, "name": "A", "streams": []}],
            )
        ],
    )
    import dbas.placeholder_rebind as rebind_mod

    original = rebind_mod.rebind_placeholder_streams
    rebind_mod.rebind_placeholder_streams = _fake_rebind
    try:
        await orch.run_restore(
            plan=plan,
            client=_client(),
            steps=[],
            report=RestoreReport(is_dry_run=True),
            ledger=RollbackLedger(restore_id="t"),
            remap=IdRemapTable(),
            confirm_apply=False,
            ledger_dir=tmp_path,
        )
    finally:
        rebind_mod.rebind_placeholder_streams = original

    assert calls == []


@pytest.mark.asyncio
async def test_ecm_settings_round_trip(monkeypatch):
    """``user_timezone`` and ``stats_poll_interval`` survive a restore.

    Drill evidence: ``user_timezone 'America/Chicago' -> ''`` and
    ``stats_poll_interval 37 -> 10``. The restore's SETTINGS category reported
    ``updated=7`` — but that category is Dispatcharr's core settings; ECM's own
    ``categories/settings.yaml`` was decoded to nothing and never applied.
    """
    from config import DispatcharrSettings
    from dbas.importers import ecm_settings as ecm_settings_mod

    saved: dict = {}
    current = DispatcharrSettings(url="http://live:9191", user_timezone="", stats_poll_interval=10)
    monkeypatch.setattr(ecm_settings_mod, "get_settings", lambda: current)
    monkeypatch.setattr(
        ecm_settings_mod, "save_settings", lambda s: saved.update(s.model_dump())
    )

    report = RestoreReport(is_dry_run=False)
    await ecm_settings_mod.import_ecm_settings(
        archive_settings={
            "user_timezone": "America/Chicago",
            "stats_poll_interval": 37,
            "url": "http://archived-host:9191",
        },
        selected=True,
        report=report,
        is_dry_run=False,
    )

    assert saved["user_timezone"] == "America/Chicago"
    assert saved["stats_poll_interval"] == 37
    # The LIVE Dispatcharr connection is never repointed mid-restore — the
    # in-flight restore is using it.
    assert saved["url"] == "http://live:9191"
    cat = report.category(EntityType.ECM_SETTINGS)
    assert cat.updated == 2


@pytest.mark.asyncio
async def test_ecm_settings_never_writes_the_redaction_sentinel(monkeypatch):
    """A redacted artifact's ``***REDACTED***`` never becomes a live setting."""
    from config import DispatcharrSettings
    from credential_sentinel import REDACTION_SENTINEL
    from dbas.importers import ecm_settings as ecm_settings_mod

    saved: dict = {}
    current = DispatcharrSettings(smtp_password="real-secret")
    monkeypatch.setattr(ecm_settings_mod, "get_settings", lambda: current)
    monkeypatch.setattr(
        ecm_settings_mod, "save_settings", lambda s: saved.update(s.model_dump())
    )

    report = RestoreReport(is_dry_run=False)
    await ecm_settings_mod.import_ecm_settings(
        archive_settings={"smtp_password": REDACTION_SENTINEL, "user_timezone": "UTC"},
        selected=True,
        report=report,
        is_dry_run=False,
    )

    assert saved["smtp_password"] == "real-secret"
    assert saved["user_timezone"] == "UTC"
    assert report.credentials_needing_reentry == 1
    assert report.credential_reentry_details[0].fields == ["smtp_password"]


# ---------------------------------------------------------------------------
# ChannelReattachMode — what happens to channels this restore did NOT create
# (bead dfkbn, PR review W1)
#
# The fix that made EPG links round-trip also made a previously-unreachable
# behaviour fire at archive scale: a channel matched ALREADY_EXISTS_IDENTICAL is
# never overwritten for name/number/group, but it IS in the CHANNEL remap, so
# both reattach passes can PATCH it. Restoring into a live instance to recover
# profile membership would silently reset every matched channel's guide link and
# logo to the archive's view, and the rollback ledger compensates CREATES, so
# nothing undoes it.
# ---------------------------------------------------------------------------


def _archive_channel(source_id, name, **over):
    ch = {"id": source_id, "name": name, "channel_number": source_id}
    ch.update(over)
    return ch


@pytest.mark.asyncio
async def test_preserve_leaves_a_pre_existing_channels_epg_link_alone():
    """PRESERVE: a channel the restore did not create keeps the operator's link."""
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links
    from dbas.restore_contracts import ChannelReattachMode

    client = _client()
    client.get_epg_data.return_value = [{"id": 4001, "tvg_id": "fox.news.us"}]

    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)

    relinked = await reattach_epg_links(
        client=client, report=report, remap=remap,
        archive_channels=[
            _archive_channel(101, "FOX News", epg_data_id=2078,
                             **{ARCHIVE_EPG_TVG_ID_KEY: "fox.news.us"}),
        ],
        mode=ChannelReattachMode.PRESERVE,
        created_source_ids=set(),  # this restore created nothing
    )

    assert relinked == 0
    client.update_channel.assert_not_awaited()
    # Not a MISS: the link resolved fine, it was deliberately left alone.
    assert report.epg_links_unrestored == 0
    assert report.epg_link_reattach.preserved_channels == 1
    assert report.epg_link_reattach.preserved_channels_named == ["FOX News"]
    assert report.epg_link_reattach.existing_channels == 0
    # And it never even fetched the guide: nothing was actionable.
    client.get_epg_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_overwrite_replaces_a_pre_existing_channels_epg_link():
    """OVERWRITE: the operator explicitly asked for the archive's view to win."""
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links
    from dbas.restore_contracts import ChannelReattachMode

    client = _client()
    client.get_epg_data.return_value = [{"id": 4001, "tvg_id": "fox.news.us"}]

    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)

    relinked = await reattach_epg_links(
        client=client, report=report, remap=remap,
        archive_channels=[
            _archive_channel(101, "FOX News", epg_data_id=2078,
                             **{ARCHIVE_EPG_TVG_ID_KEY: "fox.news.us"}),
        ],
        mode=ChannelReattachMode.OVERWRITE,
        created_source_ids=set(),
    )

    assert relinked == 1
    client.update_channel.assert_awaited_once_with(201, {"epg_data_id": 4001})
    assert report.epg_link_reattach.existing_channels == 1
    assert report.epg_link_reattach.existing_channels_named == ["FOX News"]
    assert report.epg_link_reattach.preserved_channels == 0


@pytest.mark.asyncio
async def test_preserve_still_links_channels_this_restore_created():
    """The DR case: PRESERVE and OVERWRITE are identical on a fresh target."""
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links
    from dbas.restore_contracts import ChannelReattachMode

    archive_channels = [
        _archive_channel(101, "FOX News", epg_data_id=2078,
                         **{ARCHIVE_EPG_TVG_ID_KEY: "fox.news.us"}),
        _archive_channel(102, "CNN", epg_data_id=857,
                         **{ARCHIVE_EPG_TVG_ID_KEY: "cnn.us"}),
    ]
    rows = [{"id": 4001, "tvg_id": "fox.news.us"}, {"id": 4002, "tvg_id": "cnn.us"}]

    results = {}
    for mode in (ChannelReattachMode.PRESERVE, ChannelReattachMode.OVERWRITE):
        client = _client()
        client.get_epg_data.return_value = rows
        report = RestoreReport(is_dry_run=False)
        remap = IdRemapTable()
        remap.add(EntityType.CHANNEL, 101, 201)
        remap.add(EntityType.CHANNEL, 102, 202)

        relinked = await reattach_epg_links(
            client=client, report=report, remap=remap,
            archive_channels=archive_channels,
            mode=mode,
            created_source_ids={101, 102},  # every channel was created here
        )
        results[mode] = (
            relinked,
            report.epg_link_reattach.created_channels,
            report.epg_link_reattach.existing_channels,
            report.epg_link_reattach.preserved_channels,
            sorted(c.args for c in client.update_channel.await_args_list),
        )

    assert results[ChannelReattachMode.PRESERVE] == results[ChannelReattachMode.OVERWRITE]
    assert results[ChannelReattachMode.PRESERVE][0] == 2
    assert results[ChannelReattachMode.PRESERVE][1] == 2  # both counted as created


@pytest.mark.asyncio
async def test_preserve_leaves_a_pre_existing_channels_logo_alone():
    """The SAME question, answered the SAME way for logos (one option, both passes)."""
    from dbas.channel_reattach import reattach_channel_logos
    from dbas.restore_contracts import ChannelReattachMode

    client = _client()
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)
    remap.add(EntityType.LOGO, 55, 955)

    reattached = await reattach_channel_logos(
        client=client, report=report, remap=remap,
        archive_channels=[_archive_channel(101, "FOX News", logo_id=55)],
        mode=ChannelReattachMode.PRESERVE,
        created_source_ids=set(),
    )

    assert reattached == 0
    client.update_channel.assert_not_awaited()
    # A preserved logo is NOT a miss — the operator has one, it is just theirs.
    assert report.logo_misses == 0
    assert report.logo_reattach.preserved_channels == 1
    assert report.logo_reattach.preserved_channels_named == ["FOX News"]


@pytest.mark.asyncio
async def test_overwrite_replaces_a_pre_existing_channels_logo():
    from dbas.channel_reattach import reattach_channel_logos
    from dbas.restore_contracts import ChannelReattachMode

    client = _client()
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)
    remap.add(EntityType.LOGO, 55, 955)

    reattached = await reattach_channel_logos(
        client=client, report=report, remap=remap,
        archive_channels=[_archive_channel(101, "FOX News", logo_id=55)],
        mode=ChannelReattachMode.OVERWRITE,
        created_source_ids=set(),
    )

    assert reattached == 1
    client.update_channel.assert_awaited_once_with(201, {"logo_id": 955})
    assert report.logo_reattach.existing_channels == 1
    assert report.logo_reattach.existing_channels_named == ["FOX News"]


@pytest.mark.asyncio
async def test_split_counters_separate_the_two_populations():
    """`relinked=N` is too coarse; the two populations are different events."""
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links
    from dbas.restore_contracts import ChannelReattachMode

    client = _client()
    client.get_epg_data.return_value = [
        {"id": 4001, "tvg_id": "a.us"},
        {"id": 4002, "tvg_id": "b.us"},
        {"id": 4003, "tvg_id": "c.us"},
    ]
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    for source, dest in ((101, 201), (102, 202), (103, 203)):
        remap.add(EntityType.CHANNEL, source, dest)

    await reattach_epg_links(
        client=client, report=report, remap=remap,
        archive_channels=[
            _archive_channel(101, "Made By Restore", epg_data_id=1,
                             **{ARCHIVE_EPG_TVG_ID_KEY: "a.us"}),
            _archive_channel(102, "Already Here", epg_data_id=2,
                             **{ARCHIVE_EPG_TVG_ID_KEY: "b.us"}),
            _archive_channel(103, "Also Already Here", epg_data_id=3,
                             **{ARCHIVE_EPG_TVG_ID_KEY: "c.us"}),
        ],
        mode=ChannelReattachMode.OVERWRITE,
        created_source_ids={101},
    )

    pop = report.epg_link_reattach
    assert pop.mode == ChannelReattachMode.OVERWRITE
    assert pop.created_channels == 1
    assert pop.existing_channels == 2
    assert pop.existing_channels_named == ["Already Here", "Also Already Here"]
    assert "Made By Restore" not in pop.existing_channels_named


@pytest.mark.asyncio
async def test_no_population_information_keeps_the_pre_w1_behaviour():
    """A direct call with no created-id set preserves nothing and links everything.

    ``created_source_ids=None`` means "this caller has no population
    information", which is the shape every pre-W1 call site had. Silently
    preserving everything there would turn the fix off for them.
    """
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links
    from dbas.restore_contracts import ChannelReattachMode

    client = _client()
    client.get_epg_data.return_value = [{"id": 4001, "tvg_id": "fox.news.us"}]
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)

    relinked = await reattach_epg_links(
        client=client, report=report, remap=remap,
        archive_channels=[
            _archive_channel(101, "FOX News", epg_data_id=2078,
                             **{ARCHIVE_EPG_TVG_ID_KEY: "fox.news.us"}),
        ],
        mode=ChannelReattachMode.PRESERVE,
        created_source_ids=None,
    )

    assert relinked == 1
    client.update_channel.assert_awaited_once_with(201, {"epg_data_id": 4001})


@pytest.mark.asyncio
async def test_dry_run_resolves_and_reports_but_never_patches():
    """The EPG preview is faithful: same split, same misses, zero mutation."""
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links
    from dbas.restore_contracts import ChannelReattachMode

    client = _client()
    client.get_epg_data.return_value = [{"id": 4001, "tvg_id": "fox.news.us"}]
    report = RestoreReport(is_dry_run=True)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)

    relinked = await reattach_epg_links(
        client=client, report=report, remap=remap,
        archive_channels=[
            _archive_channel(101, "FOX News", epg_data_id=2078,
                             **{ARCHIVE_EPG_TVG_ID_KEY: "fox.news.us"}),
        ],
        mode=ChannelReattachMode.OVERWRITE,
        created_source_ids=set(),
        is_dry_run=True,
    )

    assert relinked == 1  # the WOULD-BE number
    assert report.epg_link_reattach.existing_channels == 1
    client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_logo_dry_run_reports_no_split_for_an_unresolved_logo():
    """A logo the preview cannot resolve is neither a miss NOR a would-replace.

    The apply resolves the LOGO remap and, when it cannot, records a miss and
    touches nothing — so the channel is NOT in the apply's split. A dry run that
    counted the split BEFORE resolution told the operator two of their channels
    would be replaced while the apply replaced none (PR review round 2,
    finding 2). And it must still not record a miss: a preview claiming the
    operator has LOST something is the dgnms defect.
    """
    from dbas.channel_reattach import reattach_channel_logos
    from dbas.restore_contracts import ChannelReattachMode

    client = _client()
    report = RestoreReport(is_dry_run=True)
    remap = IdRemapTable()  # nothing resolves

    reattached = await reattach_channel_logos(
        client=client, report=report, remap=remap,
        archive_channels=[
            _archive_channel(101, "Made By Restore", logo_id=55),
            _archive_channel(102, "Already Here", logo_id=56),
        ],
        created_source_ids={101},
        mode=ChannelReattachMode.OVERWRITE,
        is_dry_run=True,
    )

    assert reattached == 0
    assert report.logo_misses == 0          # nothing invented
    assert report.logo_reattach.created_channels == 0
    assert report.logo_reattach.existing_channels == 0
    assert report.logo_reattach.existing_channels_named == []
    client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_logo_dry_run_predicts_the_split_for_a_MATCHED_logo():
    """The matched subset IS faithful, which is the merge case that matters.

    ``import_logos`` registers a LOGO remap entry for every archived logo it
    matches on the destination, on a dry run as much as on an apply. Restoring
    into a live install is exactly the case where the archive's logos already
    exist there, so the population the split is about is the population the
    preview CAN see.
    """
    from dbas.channel_reattach import reattach_channel_logos
    from dbas.restore_contracts import ChannelReattachMode

    client = _client()
    report = RestoreReport(is_dry_run=True)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)
    remap.add(EntityType.CHANNEL, 102, 202)
    remap.add(EntityType.LOGO, 55, 955)   # matched on the destination
    remap.add(EntityType.LOGO, 56, 956)

    await reattach_channel_logos(
        client=client, report=report, remap=remap,
        archive_channels=[
            _archive_channel(101, "Made By Restore", logo_id=55),
            _archive_channel(102, "Already Here", logo_id=56),
        ],
        created_source_ids={101},
        mode=ChannelReattachMode.OVERWRITE,
        is_dry_run=True,
    )

    assert report.logo_reattach.created_channels == 1
    assert report.logo_reattach.existing_channels == 1
    assert report.logo_reattach.existing_channels_named == ["Already Here"]
    assert report.logo_misses == 0
    client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_logo_dry_run_and_apply_agree_on_the_split():
    """Direct parity check on ``logo_reattach``, the assertion that was missing."""
    from dbas.channel_reattach import reattach_channel_logos
    from dbas.restore_contracts import ChannelReattachMode

    # The third channel's logo does NOT resolve, which is exactly where the two
    # paths used to diverge: the dry run counted it as a would-replace, the apply
    # recorded a miss and touched nothing.
    archive_channels = [
        _archive_channel(101, "Made By Restore", logo_id=55),
        _archive_channel(102, "Already Here", logo_id=56),
        _archive_channel(103, "Also Already Here", logo_id=57),
    ]

    def _remap():
        r = IdRemapTable()
        r.add(EntityType.CHANNEL, 101, 201)
        r.add(EntityType.CHANNEL, 102, 202)
        r.add(EntityType.CHANNEL, 103, 203)
        r.add(EntityType.LOGO, 55, 955)
        r.add(EntityType.LOGO, 56, 956)
        # logo 57 deliberately absent from the remap.
        return r

    splits, misses = [], []
    for dry in (True, False):
        report = RestoreReport(is_dry_run=dry)
        await reattach_channel_logos(
            client=_client(), report=report, remap=_remap(),
            archive_channels=archive_channels,
            created_source_ids={101},
            mode=ChannelReattachMode.OVERWRITE,
            is_dry_run=dry,
        )
        pop = report.logo_reattach
        splits.append(
            (pop.created_channels, pop.existing_channels,
             pop.preserved_channels, pop.existing_channels_named)
        )
        misses.append(report.logo_misses)

    # The SPLIT is the operator's decision input, and it agrees exactly.
    assert splits[0] == splits[1]
    # Concretely: only the resolvable pre-existing channel is named.
    assert splits[0] == (1, 1, 0, ["Already Here"])

    # MISSES deliberately do NOT agree, and that asymmetry is the dgnms guard:
    # a preview never claims the operator has lost something, so the unresolved
    # logo is a miss on the apply and silence on the preview.
    assert misses == [0, 1]


# ---------------------------------------------------------------------------
# Log hygiene in the reattach passes (PR review W4)
#
# This is the backup path's standard applied to the restore path: an httpx
# error's str() embeds the full request URL, and a Dispatcharr URL is not
# something a log line here may carry. The channel is already named, so the
# exception TYPE is the only thing the type-only form gives up.
# ---------------------------------------------------------------------------


class _UrlBearingError(Exception):
    """Stands in for an httpx error, whose str() embeds the request URL."""

    def __str__(self) -> str:
        return "Server error '500' for url 'http://dispatcharr:9191/api/channels/1/'"


@pytest.mark.asyncio
async def test_a_failed_relink_logs_the_exception_type_not_its_url(caplog):
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links
    from dbas.restore_contracts import ChannelReattachMode

    client = _client()
    client.get_epg_data.return_value = [{"id": 4001, "tvg_id": "fox.news.us"}]
    client.update_channel.side_effect = _UrlBearingError()

    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)

    with caplog.at_level("WARNING"):
        await reattach_epg_links(
            client=client, report=report, remap=remap,
            archive_channels=[
                _archive_channel(101, "FOX News", epg_data_id=2078,
                                 **{ARCHIVE_EPG_TVG_ID_KEY: "fox.news.us"}),
            ],
            mode=ChannelReattachMode.OVERWRITE,
            created_source_ids={101},
        )

    text = caplog.text
    assert "dispatcharr:9191" not in text
    assert "http://" not in text
    assert "_UrlBearingError" in text
    assert "FOX News" in text  # still diagnosable
    assert report.epg_links_unrestored == 1


@pytest.mark.asyncio
async def test_a_failed_logo_reattach_logs_the_exception_type_not_its_url(caplog):
    from dbas.channel_reattach import reattach_channel_logos
    from dbas.restore_contracts import ChannelReattachMode

    client = _client()
    client.update_channel.side_effect = _UrlBearingError()

    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)
    remap.add(EntityType.LOGO, 55, 955)

    with caplog.at_level("WARNING"):
        await reattach_channel_logos(
            client=client, report=report, remap=remap,
            archive_channels=[_archive_channel(101, "FOX News", logo_id=55)],
            mode=ChannelReattachMode.OVERWRITE,
            created_source_ids={101},
        )

    assert "http://" not in caplog.text
    assert "_UrlBearingError" in caplog.text


@pytest.mark.asyncio
async def test_a_failed_membership_patch_logs_the_exception_type_not_its_url(caplog):
    from dbas.channel_reattach import reattach_profile_memberships

    client = _client()
    client.update_profile_channel.side_effect = _UrlBearingError()

    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)
    remap.add(EntityType.CHANNEL_PROFILE, 9, 99)

    with caplog.at_level("WARNING"):
        await reattach_profile_memberships(
            client=client, report=report, remap=remap,
            archive_profiles=[{"id": 9, "name": "Drill Subset", "channels": []}],
            archive_channels=[_archive_channel(101, "FOX News")],
        )

    assert "http://" not in caplog.text
    assert "_UrlBearingError" in caplog.text


@pytest.mark.asyncio
async def test_an_unreadable_destination_guide_logs_the_type_not_its_url(caplog):
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links
    from dbas.restore_contracts import ChannelReattachMode

    client = _client()
    client.get_epg_data.side_effect = _UrlBearingError()

    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)

    with caplog.at_level("WARNING"):
        await reattach_epg_links(
            client=client, report=report, remap=remap,
            archive_channels=[
                _archive_channel(101, "FOX News", epg_data_id=2078,
                                 **{ARCHIVE_EPG_TVG_ID_KEY: "fox.news.us"}),
            ],
            mode=ChannelReattachMode.OVERWRITE,
            created_source_ids={101},
        )

    assert "http://" not in caplog.text
    assert "_UrlBearingError" in caplog.text
    # A failed index turns every link into an honest, counted miss.
    assert report.epg_links_unrestored == 1


# ---------------------------------------------------------------------------
# One rule, both sides of every seam (PR review round 2, findings 3 and 4)
# ---------------------------------------------------------------------------


def test_coerce_parses_its_own_enum():
    """The single parsing rule must accept the type it produces.

    ``str(ChannelReattachMode.OVERWRITE)`` is ``"ChannelReattachMode.OVERWRITE"``,
    which the value lookup rejects — so before this, ``coerce`` handed back
    PRESERVE plus a warning when given a member of its own enum. Not reachable
    from today's call sites (they all pass ``.value``), but a rule that cannot
    parse its own type is not one rule.
    """
    from dbas.restore_contracts import ChannelReattachMode

    for member in ChannelReattachMode:
        assert ChannelReattachMode.coerce(member) is member


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("preserve", "preserve"),
        ("overwrite", "overwrite"),
        ("  OverWrite  ", "overwrite"),
        (None, "preserve"),
        ("", "preserve"),
        ("obliterate", "preserve"),
        (object(), "preserve"),
        (17, "preserve"),
    ],
)
def test_coerce_never_falls_back_to_the_destructive_mode(raw, expected):
    from dbas.restore_contracts import ChannelReattachMode

    assert ChannelReattachMode.coerce(raw).value == expected


@pytest.mark.asyncio
async def test_a_channel_with_an_unusable_id_is_never_reported_as_preserved():
    """PRESERVE must not claim credit for a channel this restore CREATED.

    The importer and the reattach pass now share one id-coercion rule, but the
    residual case is an archived row whose id neither accepts. Such a channel is
    absent from ``created_source_ids`` for a reason that has nothing to do with
    who created it, and preserving on that would put "we left your existing
    channel alone" in the report about a brand-new channel. It must fall through
    to the ordinary path and become a counted, NAMED miss instead.
    """
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links
    from dbas.restore_contracts import ChannelReattachMode

    client = _client()
    client.get_epg_data.return_value = [{"id": 4001, "tvg_id": "fox.news.us"}]
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()

    await reattach_epg_links(
        client=client, report=report, remap=remap,
        archive_channels=[
            {"id": None, "name": "No Usable Id", "epg_data_id": 2078,
             ARCHIVE_EPG_TVG_ID_KEY: "fox.news.us"},
        ],
        created_source_ids=set(),  # this restore created nothing it could key
        mode=ChannelReattachMode.PRESERVE,
    )

    assert report.epg_link_reattach.preserved_channels == 0
    assert report.epg_link_reattach.preserved_channels_named == []
    assert report.epg_links_unrestored == 1
    assert report.epg_link_miss_details[0].name == "No Usable Id"


@pytest.mark.asyncio
async def test_an_unusable_id_is_not_a_would_replace_on_a_dry_run_either():
    """The DRY-RUN half of the same claim, which was unpinned.

    Under PRESERVE, a channel whose id this module cannot key fell straight into
    the "pre-existing channel whose guide link would be REPLACED" bucket — a
    destructive claim under the mode whose entire contract is that it replaces
    nothing. It has no destination id from a preview's point of view, so it
    belongs in neither half of the split.
    """
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links
    from dbas.restore_contracts import ChannelReattachMode

    client = _client()
    report = RestoreReport(is_dry_run=True)
    remap = IdRemapTable()

    for mode in (ChannelReattachMode.PRESERVE, ChannelReattachMode.OVERWRITE):
        report = RestoreReport(is_dry_run=True)
        await reattach_epg_links(
            client=client, report=report, remap=remap,
            archive_channels=[
                {"id": None, "name": "No Usable Id", "epg_data_id": 2078,
                 ARCHIVE_EPG_TVG_ID_KEY: "fox.news.us"},
            ],
            created_source_ids=set(),
            mode=mode,
            is_dry_run=True,
        )
        pop = report.epg_link_reattach
        assert pop.existing_channels == 0, mode
        assert pop.existing_channels_named == [], mode
        assert pop.created_channels == 0, mode
        assert report.epg_links_unrestored == 0, mode

    client.get_epg_data.assert_not_awaited()
    client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_importer_and_the_reattach_agree_on_which_ids_count():
    """A string source id is refused by BOTH sides, never by only one.

    The importer used ``int()`` and the reattach ``as_int``. On a row carrying
    ``"101"`` the importer would remap and mark it created while the reattach
    could not key it — so PRESERVE would report a channel the restore had just
    made as one it had left alone. Now neither side accepts it, and the outcome
    is the counted, named miss it was before any of this existed.
    """
    from dbas.importers.channels import import_channels
    from dbas.restore_contracts import EntityType as ET

    client = AsyncMock()
    client.get_channels = AsyncMock(return_value={"results": [], "count": 0})
    client.get_streams = AsyncMock(return_value={"results": [], "count": 0})
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.create_channel = AsyncMock(return_value={"id": 505})
    client.update_channel = AsyncMock(return_value={})

    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    created: set[int] = set()

    await import_channels(
        archive_channels=[{"id": "101", "name": "Stringy", "channel_number": 1}],
        client=client, selected=True, report=report,
        ledger=RollbackLedger(restore_id="r"), remap=remap,
        created_source_ids=created,
    )

    # The channel WAS created upstream...
    client.create_channel.assert_awaited_once()
    assert report.category(ET.CHANNEL).created == 1
    # ...but neither side keys it, so neither claims it.
    assert created == set()
    assert remap.resolve(ET.CHANNEL, 101) is None
