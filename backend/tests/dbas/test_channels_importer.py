"""Tests for the channels restore importer
(enhancedchannelmanager-4vouz — split 2/4 of 0i2vt.14).

Scope under test (TIGHT — channel-row create + profile membership reattach):

1. Create channels. For each archived channel, strip archive-only / non-writable
   keys (id/pk and the FK-id fields that are remapped) and create via
   ``client.create_channel``. Register source->dest in the IdRemapTable under
   EntityType.CHANNEL and record each create in the RollbackLedger.
2. FK remapping. ``channel_group_id`` and ``stream_profile_id`` are FK references
   to other entities (EntityType.CHANNEL_GROUP / STREAM_PROFILE produced by bead
   .12). They are resolved through the IdRemapTable to a destination id before
   create. If a referenced FK is NOT in the remap, the channel is skipped
   DEPENDENCY_UNRESOLVED (never sent upstream with a stale archive id).
3. Profile reattach. After channels are created, each restored channel is
   reattached to its archived channel-profile memberships via
   ``client.update_profile_channel``. The destination profile id is resolved
   through the IdRemapTable (EntityType.CHANNEL_PROFILE). A profile not in the
   remap is skipped DEPENDENCY_UNRESOLVED (dependency not yet restored) — never
   crashes, never guesses a profile id.
4. Opt-in: nothing happens unless the operator selected the category.
5. Dry-run: no creates, no ledger entries; reports would_create.
6. Name/number collision taxonomy: an existing identical channel is skipped
   ALREADY_EXISTS_IDENTICAL; a create that races into a conflict is failed
   CONFLICT.

OUT OF SCOPE (other beads): stream matching/attaching (0i2vt.14 + al6e3 + nav0c),
custom-stream fallback synthesis (ahygg). This importer leaves a clean seam where
.14 attaches streams — it never matches or attaches a stream.

The Dispatcharr client is mocked at the importer module level
(``dbas.importers.channels``); the importer is exercised with an AsyncMock client.
"""
import pytest
from unittest.mock import AsyncMock

from dbas.importers.channels import import_channels
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


def _client(*, existing_channels=None, profiles=None, create_side_effect=None):
    """Build an AsyncMock Dispatcharr client with the methods the importer uses."""
    client = AsyncMock()
    client.get_channels = AsyncMock(
        return_value={"results": existing_channels or [], "count": len(existing_channels or [])}
    )
    client.get_channel_profiles = AsyncMock(return_value=profiles or [])
    created_counter = {"n": 500}

    async def _default_create(payload):
        created_counter["n"] += 1
        return {"id": created_counter["n"], **payload}

    client.create_channel = AsyncMock(side_effect=create_side_effect or _default_create)
    client.update_profile_channel = AsyncMock(return_value={"success": True})
    client.delete_channel = AsyncMock(return_value=None)
    # Stream layer (0i2vt.14): an empty destination-stream candidate set + an
    # attach call. The 4vouz tests use channels with no embedded streams, so the
    # stream layer is a no-op for them; these stubs keep the candidate fetch from
    # hitting an auto-generated MagicMock.
    client.get_streams = AsyncMock(return_value={"results": [], "count": 0})
    client.update_channel = AsyncMock(return_value={"success": True})
    return client


def _report(is_dry_run=False):
    return RestoreReport(is_dry_run=is_dry_run)


def _ledger():
    return RollbackLedger(restore_id="test-restore")


def _remap(**kwargs):
    """Build an IdRemapTable pre-seeded with the given mappings.

    Each kwarg is an EntityType-value name -> {src: dest} dict, e.g.
    ``_remap(channel_group={10: 110}, channel_profile={1: 101})``.
    """
    table = IdRemapTable()
    name_to_type = {
        "channel_group": EntityType.CHANNEL_GROUP,
        "stream_profile": EntityType.STREAM_PROFILE,
        "channel_profile": EntityType.CHANNEL_PROFILE,
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
    """OPT-IN: when the operator did not select the channels category, nothing is
    created — every archived channel is recorded EXCLUDED_BY_OPERATOR."""
    client = _client()
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await import_channels(
        archive_channels=[{"id": 5, "name": "CNN", "channel_number": 1}],
        client=client,
        selected=False,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    client.create_channel.assert_not_awaited()
    cat = report.category(EntityType.CHANNEL)
    assert cat.created == 0
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.EXCLUDED_BY_OPERATOR
    assert cat.skip_details[0].label == "CNN"
    assert ledger.entries == []
    # remap not mutated
    assert remap.resolve(EntityType.CHANNEL, 5) is None


# ---------------------------------------------------------------------------
# Channel create happy path — remap + ledger recorded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_create_happy_path_records_remap_and_ledger():
    """A clean channel create registers source->dest in the IdRemapTable under
    EntityType.CHANNEL and records the create in the RollbackLedger."""
    client = _client()
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await import_channels(
        archive_channels=[{"id": 5, "name": "CNN", "channel_number": 1}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    cat = report.category(EntityType.CHANNEL)
    assert cat.created == 1
    # remap: source export id 5 -> assigned destination id 501
    dest_id = remap.resolve(EntityType.CHANNEL, 5)
    assert dest_id == 501
    # ledger recorded the created channel for compensation
    assert len(ledger.entries) == 1
    entry = ledger.entries[0]
    assert entry.entity_type == EntityType.CHANNEL
    assert entry.destination_id == 501
    assert entry.label == "CNN"


@pytest.mark.asyncio
async def test_create_payload_strips_source_id_keys():
    """The create payload never carries the archive's id/pk — the destination
    assigns its own id."""
    client = _client()
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await import_channels(
        archive_channels=[{"id": 5, "pk": 5, "name": "CNN"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    payload = client.create_channel.await_args.args[0]
    assert "id" not in payload
    assert "pk" not in payload
    assert payload["name"] == "CNN"


# ---------------------------------------------------------------------------
# FK remapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fk_remap_resolves_channel_group_and_stream_profile():
    """The channel's channel_group_id and stream_profile_id FK references are
    rewritten from source ids to destination ids via the IdRemapTable before
    the create is sent — never the stale archive ids."""
    client = _client()
    report = _report()
    ledger = _ledger()
    # source group 10 -> dest 110; source stream profile 7 -> dest 207
    remap = _remap(channel_group={10: 110}, stream_profile={7: 207})

    await import_channels(
        archive_channels=[
            {
                "id": 5,
                "name": "CNN",
                "channel_group_id": 10,
                "stream_profile_id": 7,
            }
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    payload = client.create_channel.await_args.args[0]
    assert payload["channel_group_id"] == 110
    assert payload["stream_profile_id"] == 207
    assert report.category(EntityType.CHANNEL).created == 1


@pytest.mark.asyncio
async def test_fk_target_missing_skips_dependency_unresolved_no_stale_id():
    """When a channel's FK target (channel_group) is NOT in the remap, the channel
    is skipped DEPENDENCY_UNRESOLVED — and NO create is issued, so the stale
    archive id is never sent upstream."""
    client = _client()
    report = _report()
    ledger = _ledger()
    remap = _remap()  # empty — group 10 not resolvable

    await import_channels(
        archive_channels=[
            {"id": 5, "name": "CNN", "channel_group_id": 10}
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    client.create_channel.assert_not_awaited()
    cat = report.category(EntityType.CHANNEL)
    assert cat.created == 0
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.DEPENDENCY_UNRESOLVED
    assert cat.skip_details[0].label == "CNN"
    # no ledger entry, no remap entry for the unresolved channel
    assert ledger.entries == []
    assert remap.resolve(EntityType.CHANNEL, 5) is None


@pytest.mark.asyncio
async def test_non_remappable_fk_fields_dropped_not_sent_stale():
    """logo_id and epg_data_id have no EntityType in the remap contract (logos are
    owned by beads .15/.19). They are DROPPED rather than sent with a stale
    archive id — the create payload carries neither."""
    client = _client()
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await import_channels(
        archive_channels=[
            {"id": 5, "name": "CNN", "logo_id": 99, "epg_data_id": 42}
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    payload = client.create_channel.await_args.args[0]
    assert "logo_id" not in payload
    assert "epg_data_id" not in payload
    assert report.category(EntityType.CHANNEL).created == 1


@pytest.mark.asyncio
async def test_streams_stripped_from_create_payload():
    """The embedded ``streams`` payload is never part of the channel CREATE body —
    the destination assigns the channel its own id, then streams are attached
    separately (the 0i2vt.14 stream layer, tested in test_channels_integration.py).
    Here we only assert the create payload carries no ``streams`` key; the stream
    layer is patched out so this test stays scoped to the create body."""
    from unittest.mock import patch
    import dbas.importers.channels as channels_mod
    from dbas.custom_stream_fallback import CustomStreamFallbackResult

    client = _client()
    client.get_streams = AsyncMock(return_value={"results": [], "count": 0})
    client.update_channel = AsyncMock(return_value={"success": True})
    report = _report()
    ledger = _ledger()
    remap = _remap()

    synth = AsyncMock(return_value=CustomStreamFallbackResult())
    with patch.object(channels_mod, "synthesize_custom_streams", synth):
        await import_channels(
            archive_channels=[
                {"id": 5, "name": "CNN", "streams": [{"id": 1}, {"id": 2}]}
            ],
            client=client,
            selected=True,
            report=report,
            ledger=ledger,
            remap=remap,
        )

    payload = client.create_channel.await_args.args[0]
    assert "streams" not in payload


# ---------------------------------------------------------------------------
# Profile reattach
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_reattach_happy_path():
    """After create, each restored channel is reattached to its archived
    channel-profile membership. The destination profile id is resolved through the
    IdRemapTable (EntityType.CHANNEL_PROFILE) and update_profile_channel is called
    with the destination profile id + destination channel id."""
    client = _client()
    report = _report()
    ledger = _ledger()
    # source profile 1 -> dest profile 101
    remap = _remap(channel_profile={1: 101})

    await import_channels(
        archive_channels=[
            {
                "id": 5,
                "name": "CNN",
                "profile_memberships": [{"profile_id": 1, "enabled": True}],
            }
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    # channel created with dest id 501
    dest_channel_id = remap.resolve(EntityType.CHANNEL, 5)
    assert dest_channel_id == 501
    client.update_profile_channel.assert_awaited_once()
    call = client.update_profile_channel.await_args
    # update_profile_channel(profile_id, channel_id, data)
    assert call.args[0] == 101  # destination profile id (resolved via remap)
    assert call.args[1] == 501  # destination channel id
    assert call.args[2] == {"enabled": True}


@pytest.mark.asyncio
async def test_profile_not_in_remap_skips_dependency_unresolved():
    """A channel-profile membership whose profile is NOT in the remap (the
    channel-profiles importer .12 has not run, or did not restore that profile) is
    handled as DEPENDENCY_UNRESOLVED — no update_profile_channel call, no crash,
    no guessed profile id. The channel itself still created successfully."""
    client = _client()
    report = _report()
    ledger = _ledger()
    remap = _remap()  # no channel_profile mappings

    await import_channels(
        archive_channels=[
            {
                "id": 5,
                "name": "CNN",
                "profile_memberships": [{"profile_id": 1, "enabled": True}],
            }
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    # channel created
    assert report.category(EntityType.CHANNEL).created == 1
    # but profile reattach was NOT issued (dependency unresolved)
    client.update_profile_channel.assert_not_awaited()
    # the unresolved membership is reported under CHANNEL_PROFILE as a skip
    prof_cat = report.category(EntityType.CHANNEL_PROFILE)
    assert prof_cat.skipped == 1
    assert prof_cat.skip_details[0].reason == SkipReason.DEPENDENCY_UNRESOLVED


@pytest.mark.asyncio
async def test_profile_reattach_skipped_in_dry_run():
    """Dry-run reattaches nothing — update_profile_channel is never called."""
    client = _client()
    report = _report(is_dry_run=True)
    ledger = _ledger()
    remap = _remap(channel_profile={1: 101})

    await import_channels(
        archive_channels=[
            {
                "id": 5,
                "name": "CNN",
                "profile_memberships": [{"profile_id": 1, "enabled": True}],
            }
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=True,
    )

    client.update_profile_channel.assert_not_awaited()


# ---------------------------------------------------------------------------
# Name / number collision taxonomy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_identical_channel_skipped_already_exists():
    """A channel whose (name, channel_number) already exists on the destination is
    skipped ALREADY_EXISTS_IDENTICAL — never re-created, never overwritten."""
    client = _client(
        existing_channels=[{"id": 30, "name": "CNN", "channel_number": 1}]
    )
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await import_channels(
        archive_channels=[{"id": 5, "name": "CNN", "channel_number": 1}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    client.create_channel.assert_not_awaited()
    cat = report.category(EntityType.CHANNEL)
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.ALREADY_EXISTS_IDENTICAL
    # an already-existing channel still remaps source->existing dest id so a later
    # profile reattach can resolve the channel.
    assert remap.resolve(EntityType.CHANNEL, 5) == 30


@pytest.mark.asyncio
async def test_null_number_collision_surfaced_as_conflict_not_silent_skip():
    """COLLISION FLOOR (spike xp6mp ruling 1a — the load-bearing test).

    A source channel (name, channel_number=None) that "matches" a destination
    channel (name, channel_number=None) is AMBIGUOUS — a name alone does not prove
    identity. It must be surfaced as FailureReason.CONFLICT (failed-with-reason),
    NEVER a silent ALREADY_EXISTS_IDENTICAL. Under continuous sync that silent
    skip would be permanent recurring divergence (B never converges on A).
    """
    client = _client(
        # Destination channel with NO channel_number.
        existing_channels=[{"id": 30, "name": "CNN"}]
    )
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await import_channels(
        # Source channel with NO channel_number — null on both sides == ambiguous.
        archive_channels=[{"id": 5, "name": "CNN"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    # Surfaced as a CONFLICT failure, NOT a silent skip, and NOT re-created.
    client.create_channel.assert_not_awaited()
    cat = report.category(EntityType.CHANNEL)
    assert cat.failed == 1
    assert cat.skipped == 0
    assert cat.failure_details[0].reason == FailureReason.CONFLICT
    assert cat.failure_details[0].label == "CNN"
    # No silent ALREADY_EXISTS_IDENTICAL skip detail was recorded.
    assert all(
        d.reason != SkipReason.ALREADY_EXISTS_IDENTICAL for d in cat.skip_details
    )


@pytest.mark.asyncio
async def test_non_null_number_match_stays_already_exists_identical():
    """The COMPLEMENT of ruling 1a: a (name, channel_number) match where the
    number is NON-null and EQUAL on both sides is a real identity — it stays
    ALREADY_EXISTS_IDENTICAL (NOT a CONFLICT)."""
    client = _client(
        existing_channels=[{"id": 30, "name": "CNN", "channel_number": 5}]
    )
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await import_channels(
        archive_channels=[{"id": 5, "name": "CNN", "channel_number": 5}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    client.create_channel.assert_not_awaited()
    cat = report.category(EntityType.CHANNEL)
    assert cat.failed == 0
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.ALREADY_EXISTS_IDENTICAL
    assert remap.resolve(EntityType.CHANNEL, 5) == 30


@pytest.mark.asyncio
async def test_create_conflict_recorded_as_failure_conflict():
    """If create_channel raises a CONFLICT-shaped upstream error (race: a colliding
    channel appeared between the pre-check and the create), it is recorded as
    FailureReason.CONFLICT with a sanitized message."""
    async def _conflict(payload):
        raise Exception(
            'Channel creation failed: 400 - {"channel_number": ["already exists."]}'
        )

    client = _client(create_side_effect=_conflict)
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await import_channels(
        archive_channels=[{"id": 5, "name": "CNN", "channel_number": 1}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    cat = report.category(EntityType.CHANNEL)
    assert cat.failed == 1
    assert cat.failure_details[0].reason == FailureReason.CONFLICT
    assert cat.failure_details[0].label == "CNN"
    assert ledger.entries == []


@pytest.mark.asyncio
async def test_upstream_error_recorded_as_failure_upstream_api_error():
    """A non-conflict create failure is recorded UPSTREAM_API_ERROR, not CONFLICT."""
    async def _boom(payload):
        raise Exception("Channel creation failed: 500 - internal server error")

    client = _client(create_side_effect=_boom)
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await import_channels(
        archive_channels=[{"id": 5, "name": "CNN"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    cat = report.category(EntityType.CHANNEL)
    assert cat.failed == 1
    assert cat.failure_details[0].reason == FailureReason.UPSTREAM_API_ERROR


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_issues_zero_creates_and_zero_ledger_entries():
    """A dry-run issues no create_channel calls and records no ledger entries —
    it only reports would_create so the operator sees the plan."""
    client = _client()
    report = _report(is_dry_run=True)
    ledger = _ledger()
    remap = _remap()

    await import_channels(
        archive_channels=[
            {"id": 5, "name": "CNN"},
            {"id": 6, "name": "ESPN"},
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=True,
    )

    client.create_channel.assert_not_awaited()
    cat = report.category(EntityType.CHANNEL)
    assert cat.would_create == 2
    assert cat.created == 0
    assert ledger.entries == []


@pytest.mark.asyncio
async def test_dry_run_reports_dependency_unresolved_would_skip():
    """A dry-run with an unresolved FK reports would_skip DEPENDENCY_UNRESOLVED
    so the operator sees the dependency gap before applying."""
    client = _client()
    report = _report(is_dry_run=True)
    ledger = _ledger()
    remap = _remap()  # group 10 not resolvable

    await import_channels(
        archive_channels=[{"id": 5, "name": "CNN", "channel_group_id": 10}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=True,
    )

    client.create_channel.assert_not_awaited()
    cat = report.category(EntityType.CHANNEL)
    assert cat.would_skip == 1
    assert cat.would_create == 0
    assert any(
        d.reason == SkipReason.DEPENDENCY_UNRESOLVED for d in cat.skip_details
    )


# ---------------------------------------------------------------------------
# Rollback ledger records each created channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_ledger_records_each_created_channel():
    """Each successfully created channel is recorded in the rollback ledger with
    its destination id and name label, so a later failure can compensate-delete."""
    client = _client()
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await import_channels(
        archive_channels=[
            {"id": 5, "name": "CNN"},
            {"id": 6, "name": "ESPN"},
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    assert len(ledger.entries) == 2
    for entry in ledger.entries:
        assert entry.entity_type == EntityType.CHANNEL
        assert entry.destination_id is not None
        assert entry.label in ("CNN", "ESPN")
    labels = {e.label for e in ledger.entries}
    assert labels == {"CNN", "ESPN"}
