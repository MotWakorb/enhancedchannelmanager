"""Channel->group membership on a POPULATED target — reported, and reconciled on replace.

Beads ``enhancedchannelmanager-r1ei7`` (membership never reconciled, never
reported), ``enhancedchannelmanager-3t74w`` (a same-named but different channel
group is adopted while the report claims "already exists identical") and
``enhancedchannelmanager-tddmw`` (the preview omits the Streams category and
mispredicts channel_groups).

WHY NONE OF THIS WAS REACHABLE BEFORE. Every restore drill before run 12
(2026-08-07, ECM ``0.18.1-0040`` / Dispatcharr ``0.28.2``) restored onto a
freshly-wiped target, where every channel and every group is CREATED and the
destination has no opinion to disagree with. Run 12 restored the same artifact
onto a POPULATED, deliberately-diverged target and measured:

===================  ==============================  ==============================
archive              diverged target                 after restore (BOTH modes)
===================  ==============================  ==============================
Drill Sports  (376)  Drill Sports RENAMED (376)      unchanged; an empty second
  ch101-104            ch102-104, ch201-203          ``Drill Sports`` was created
Drill Movies  (377)  Drill Movies (379) — a          adopted by NAME, holding
  ch201-203            DIFFERENT object, ch101       ch101 the archive puts in
                                                     Drill Sports
Drill Empty   (378)  deleted                         recreated, empty
===================  ==============================  ==============================

Not one channel's group was corrected, in either relink mode, and the restore
reported ``outcome=success, failed 0``. The counts reconcile perfectly, which is
exactly the "counts reconciling is not proof" trap.

WHAT THIS SUITE PINS
--------------------
* ``preserve`` (the default) REPORTS every drifted channel and changes NOTHING —
  the "an existing channel is never overwritten" contract (spike ``xp6mp``) and
  the ``ChannelReattachMode`` default both continue to hold.
* ``overwrite`` reports the same set AND moves each channel into the archive's
  group.
* A channel group matched on NAME ONLY no longer claims
  ``ALREADY_EXISTS_IDENTICAL``; it reports ``ALREADY_EXISTS_NAME_MATCH``. The
  adopt + FK remap itself is UNCHANGED — name is the only cross-instance identity
  a channel group carries (kxuj2 contract; ADR-008).
* Channel profiles and stream profiles, which share the same generic engine,
  still report ``ALREADY_EXISTS_IDENTICAL``.
* A DRY RUN predicts the same drift the apply then reports (the ``dgnms``
  discipline), emits the ``Streams`` category it used to omit entirely, and says
  out loud that the ``channel_groups`` split it shows can differ from the apply's.

Conventions: ``docs/pytest_conventions.md``. Upstream is a stateful in-test fake
rather than a bare ``AsyncMock`` so the groups importer, the channels importer
and the reconcile pass observe ONE destination — the seam between them is the
thing under test, and a fixed-return mock would let each half agree with a
different instance.
"""
from __future__ import annotations

import pytest

from dbas.importers.channels import import_channels
from dbas.importers.groups_profiles import (
    import_channel_groups,
    import_channel_profiles,
    import_stream_profiles,
)
from dbas.restore_contracts import (
    ChannelReattachMode,
    EntityType,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
    SkipReason,
)


# ---------------------------------------------------------------------------
# The run-12 fixture — archive and diverged target, verbatim
# ---------------------------------------------------------------------------

# Destination ids the fake assigns to rows the restore creates. Deliberately far
# from the archive's ids so a test can never pass by accident on id equality.
_FIRST_MINTED_ID = 900


def _archive_groups() -> list[dict]:
    return [
        {"id": 376, "name": "Drill Sports"},
        {"id": 377, "name": "Drill Movies"},
        {"id": 378, "name": "Drill Empty"},
    ]


def _archive_channels() -> list[dict]:
    """The archive's seven channels, with the group the archive assigns each."""
    return [
        {"id": 101, "name": "ch101", "channel_number": 101, "channel_group_id": 376},
        {"id": 102, "name": "ch102", "channel_number": 102, "channel_group_id": 376},
        {"id": 103, "name": "ch103", "channel_number": 103, "channel_group_id": 376},
        {"id": 104, "name": "ch104", "channel_number": 104, "channel_group_id": 376},
        {"id": 201, "name": "ch201", "channel_number": 201, "channel_group_id": 377},
        {"id": 202, "name": "ch202", "channel_number": 202, "channel_group_id": 377},
        {"id": 203, "name": "ch203", "channel_number": 203, "channel_group_id": 377},
    ]


def _diverged_target_groups() -> list[dict]:
    """376 RENAMED, 377 deleted, and a DIFFERENT 'Drill Movies' minted as 379."""
    return [
        {"id": 376, "name": "Drill Sports RENAMED"},
        {"id": 379, "name": "Drill Movies"},
    ]


def _diverged_target_channels() -> list[dict]:
    """The same seven channels, in the groups the target has them in.

    Destination ids are the archive's here only because the drill's target WAS a
    previous restore of the same archive; what matters is ``channel_group_id``.
    """
    return [
        {"id": 101, "name": "ch101", "channel_number": 101, "channel_group_id": 379},
        {"id": 102, "name": "ch102", "channel_number": 102, "channel_group_id": 376},
        {"id": 103, "name": "ch103", "channel_number": 103, "channel_group_id": 376},
        {"id": 104, "name": "ch104", "channel_number": 104, "channel_group_id": 376},
        {"id": 201, "name": "ch201", "channel_number": 201, "channel_group_id": 376},
        {"id": 202, "name": "ch202", "channel_number": 202, "channel_group_id": 376},
        {"id": 203, "name": "ch203", "channel_number": 203, "channel_group_id": 376},
    ]


class _FakeDispatcharr:
    """A minimally stateful Dispatcharr the whole restore path shares.

    Only the surface these three steps touch: list/create channel groups, list
    channels, and PATCH a channel. Creates mutate the fake's own state, so the
    reconcile pass sees the groups the groups importer just made — which is the
    ordering the defect lives in.
    """

    def __init__(self, *, groups: list[dict], channels: list[dict]):
        self.groups = [dict(g) for g in groups]
        self.channels = [dict(c) for c in channels]
        self._next_id = _FIRST_MINTED_ID
        self.patches: list[tuple[int, dict]] = []

    async def get_channel_groups(self) -> list[dict]:
        return [dict(g) for g in self.groups]

    async def create_channel_group(self, name: str) -> dict:
        row = {"id": self._next_id, "name": name}
        self._next_id += 1
        self.groups.append(row)
        return dict(row)

    async def get_channels(self, **_kwargs) -> dict:
        return {"results": [dict(c) for c in self.channels]}

    async def get_streams(self, **_kwargs) -> dict:
        return {"results": [], "count": 0}

    async def update_channel(self, channel_id: int, payload: dict) -> dict:
        self.patches.append((channel_id, dict(payload)))
        for row in self.channels:
            if row["id"] == channel_id:
                row.update(payload)
                return dict(row)
        raise AssertionError("PATCHed a channel the destination does not have")

    async def update_profile_channel(self, *_args, **_kwargs) -> dict:
        return {}


async def _restore_groups_then_channels(client, *, is_dry_run: bool = False):
    """Run the two importers that populate the remap, in orchestrator order.

    Returns ``(report, remap, created_source_ids, matched_existing_channels)`` —
    exactly the state the orchestrator's channels step hands the reconcile pass.
    """
    report = RestoreReport(is_dry_run=is_dry_run)
    ledger = RollbackLedger(restore_id="t")
    remap = IdRemapTable()
    created_source_ids: set[int] = set()
    matched_existing_channels: dict[int, dict] = {}

    await import_channel_groups(
        archive_rows=_archive_groups(),
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=is_dry_run,
    )
    await import_channels(
        archive_channels=_archive_channels(),
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=is_dry_run,
        created_source_ids=created_source_ids,
        matched_existing_channels=matched_existing_channels,
    )
    return report, remap, created_source_ids, matched_existing_channels


async def _reconcile(client, *, mode, is_dry_run=False):
    """Drive the whole run-12 scenario and return ``(report, moved, client)``."""
    from dbas.channel_reattach import reconcile_channel_groups

    report, remap, created, matched = await _restore_groups_then_channels(
        client, is_dry_run=is_dry_run
    )
    moved = await reconcile_channel_groups(
        client=client,
        report=report,
        remap=remap,
        archive_channels=_archive_channels(),
        archive_channel_groups=_archive_groups(),
        matched_existing_channels=matched,
        created_source_ids=created,
        mode=mode,
        is_dry_run=is_dry_run,
    )
    return report, moved


def _target() -> _FakeDispatcharr:
    return _FakeDispatcharr(
        groups=_diverged_target_groups(), channels=_diverged_target_channels()
    )


def _drift_by_channel(report: RestoreReport) -> dict[str, tuple[str, str]]:
    """``{channel name: (group it is in, group the archive says)}``."""
    return {
        d.name: (d.current_group, d.archive_group)
        for d in report.channel_group_drift_details
    }


# ---------------------------------------------------------------------------
# r1ei7 — PRESERVE reports every drifted channel and changes nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preserve_reports_the_drift_for_every_channel_run_12_measured():
    """All seven channels are named, with the group they are in and the archive's.

    Run 12 measured this exact set with ``profile_membership_drift`` (channel
    *profile* membership) as the only drift counter in the report — there was no
    channel *group* equivalent, so the whole finding was invisible.
    """
    client = _target()

    report, moved = await _reconcile(client, mode=ChannelReattachMode.PRESERVE)

    assert report.channel_group_drift == 7
    assert moved == 0
    assert _drift_by_channel(report) == {
        # The adopted-by-name group is holding a channel the archive puts in
        # Drill Sports — the 3t74w collision, seen from the channel's side.
        "ch101": ("Drill Movies", "Drill Sports"),
        "ch102": ("Drill Sports RENAMED", "Drill Sports"),
        "ch103": ("Drill Sports RENAMED", "Drill Sports"),
        "ch104": ("Drill Sports RENAMED", "Drill Sports"),
        "ch201": ("Drill Sports RENAMED", "Drill Movies"),
        "ch202": ("Drill Sports RENAMED", "Drill Movies"),
        "ch203": ("Drill Sports RENAMED", "Drill Movies"),
    }
    assert all(not d.moved for d in report.channel_group_drift_details)


@pytest.mark.asyncio
async def test_preserve_changes_nothing_about_a_channel_it_did_not_create():
    """The xp6mp contract holds: reporting is not licence to write.

    An operator merging into a live install chose ``preserve`` precisely so their
    own lineup is left alone. Not one PATCH may be issued, and the destination's
    grouping must be byte-identical afterwards.
    """
    client = _target()
    before = {row["id"]: row["channel_group_id"] for row in client.channels}

    await _reconcile(client, mode=ChannelReattachMode.PRESERVE)

    assert client.patches == []
    assert {row["id"]: row["channel_group_id"] for row in client.channels} == before


# ---------------------------------------------------------------------------
# r1ei7 — OVERWRITE reconciles, and still reports what it moved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overwrite_moves_every_drifted_channel_into_the_archives_group():
    """The lineup's grouping comes back, and the report still names the moves.

    Reconciling silently would trade one invisible outcome for another: the
    operator has to be able to see that seven of their channels changed group.
    """
    client = _target()

    report, moved = await _reconcile(client, mode=ChannelReattachMode.OVERWRITE)

    assert moved == 7
    assert report.channel_group_drift == 7
    assert all(d.moved for d in report.channel_group_drift_details)

    # 'Drill Sports' was recreated (the target had renamed the archived one), so
    # ch101-104 land in the NEW group; ch201-203 land in the adopted id 379.
    drill_sports = next(g["id"] for g in client.groups if g["name"] == "Drill Sports")
    assert {row["id"]: row["channel_group_id"] for row in client.channels} == {
        101: drill_sports,
        102: drill_sports,
        103: drill_sports,
        104: drill_sports,
        201: 379,
        202: 379,
        203: 379,
    }
    assert {cid for cid, _ in client.patches} == {101, 102, 103, 104, 201, 202, 203}
    assert all(set(payload) == {"channel_group_id"} for _, payload in client.patches)


@pytest.mark.asyncio
async def test_a_channel_already_in_the_archives_group_is_not_drift():
    """No drift row, no PATCH, in either mode — an agreeing channel is not news."""
    client = _FakeDispatcharr(
        groups=[{"id": 376, "name": "Drill Sports"}],
        channels=[
            {"id": 101, "name": "ch101", "channel_number": 101, "channel_group_id": 376}
        ],
    )
    from dbas.channel_reattach import reconcile_channel_groups

    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 101)
    remap.add(EntityType.CHANNEL_GROUP, 376, 376)

    moved = await reconcile_channel_groups(
        client=client,
        report=report,
        remap=remap,
        archive_channels=[_archive_channels()[0]],
        archive_channel_groups=[{"id": 376, "name": "Drill Sports"}],
        matched_existing_channels={101: dict(client.channels[0])},
        created_source_ids=set(),
        mode=ChannelReattachMode.OVERWRITE,
        is_dry_run=False,
    )

    assert moved == 0
    assert report.channel_group_drift == 0
    assert client.patches == []


@pytest.mark.asyncio
async def test_a_channel_this_restore_created_is_never_drift():
    """A created channel got the archive's group in its create payload.

    Counting it as drift would report a correction the restore never had to make,
    and on a fresh disaster-recovery target that is EVERY channel.
    """
    from dbas.channel_reattach import reconcile_channel_groups

    client = _FakeDispatcharr(groups=[{"id": 900, "name": "Drill Sports"}], channels=[])
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 555)
    remap.add(EntityType.CHANNEL_GROUP, 376, 900)

    moved = await reconcile_channel_groups(
        client=client,
        report=report,
        remap=remap,
        archive_channels=[_archive_channels()[0]],
        archive_channel_groups=_archive_groups(),
        matched_existing_channels={},
        created_source_ids={101},
        mode=ChannelReattachMode.OVERWRITE,
        is_dry_run=False,
    )

    assert moved == 0
    assert report.channel_group_drift == 0
    assert client.patches == []


@pytest.mark.asyncio
async def test_an_unresolvable_archive_group_is_reported_but_never_guessed():
    """Drift the pass CANNOT fix is still drift, and still never invents an id.

    The archived group failed to create (or its category was deselected), so
    there is no destination id to move the channel to. Silence here would be the
    r1ei7 defect again; a guessed id would be worse than silence.
    """
    from dbas.channel_reattach import reconcile_channel_groups

    client = _FakeDispatcharr(
        groups=[{"id": 379, "name": "Drill Movies"}],
        channels=[
            {"id": 101, "name": "ch101", "channel_number": 101, "channel_group_id": 379}
        ],
    )
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 101)
    # No CHANNEL_GROUP entry for 376 at all.

    moved = await reconcile_channel_groups(
        client=client,
        report=report,
        remap=remap,
        archive_channels=[_archive_channels()[0]],
        archive_channel_groups=_archive_groups(),
        matched_existing_channels={101: dict(client.channels[0])},
        created_source_ids=set(),
        mode=ChannelReattachMode.OVERWRITE,
        is_dry_run=False,
    )

    assert moved == 0
    assert client.patches == []
    assert report.channel_group_drift == 1
    detail = report.channel_group_drift_details[0]
    assert detail.current_group == "Drill Movies"
    assert detail.archive_group == "Drill Sports"
    assert detail.moved is False


@pytest.mark.asyncio
async def test_an_upstream_patch_failure_is_contained_and_reported_unmoved():
    """A best-effort pass never raises and never claims a move it did not make."""
    from dbas.channel_reattach import reconcile_channel_groups

    class _Boom(_FakeDispatcharr):
        async def update_channel(self, channel_id, payload):
            raise RuntimeError("http://secret.example/api/channels/1 exploded")

    client = _Boom(
        groups=[{"id": 379, "name": "Drill Movies"}, {"id": 900, "name": "Drill Sports"}],
        channels=[
            {"id": 101, "name": "ch101", "channel_number": 101, "channel_group_id": 379}
        ],
    )
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 101)
    remap.add(EntityType.CHANNEL_GROUP, 376, 900)

    moved = await reconcile_channel_groups(
        client=client,
        report=report,
        remap=remap,
        archive_channels=[_archive_channels()[0]],
        archive_channel_groups=_archive_groups(),
        matched_existing_channels={101: dict(client.channels[0])},
        created_source_ids=set(),
        mode=ChannelReattachMode.OVERWRITE,
        is_dry_run=False,
    )

    assert moved == 0
    assert report.channel_group_drift == 1
    assert report.channel_group_drift_details[0].moved is False


@pytest.mark.asyncio
async def test_the_patch_failure_log_never_carries_the_upstream_url(caplog):
    """An httpx error's ``str()`` embeds the request URL — log the TYPE only."""
    from dbas.channel_reattach import reconcile_channel_groups

    class _UrlBearingError(RuntimeError):
        def __str__(self):
            return "GET http://user:token@provider.example/api/ failed"

    class _Boom(_FakeDispatcharr):
        async def update_channel(self, channel_id, payload):
            raise _UrlBearingError()

    client = _Boom(
        groups=[{"id": 379, "name": "Drill Movies"}, {"id": 900, "name": "Drill Sports"}],
        channels=[
            {"id": 101, "name": "ch101", "channel_number": 101, "channel_group_id": 379}
        ],
    )
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 101)
    remap.add(EntityType.CHANNEL_GROUP, 376, 900)

    with caplog.at_level("WARNING"):
        await reconcile_channel_groups(
            client=client,
            report=RestoreReport(is_dry_run=False),
            remap=remap,
            archive_channels=[_archive_channels()[0]],
            archive_channel_groups=_archive_groups(),
            matched_existing_channels={101: dict(client.channels[0])},
            created_source_ids=set(),
            mode=ChannelReattachMode.OVERWRITE,
            is_dry_run=False,
        )

    assert "http://" not in caplog.text
    assert "_UrlBearingError" in caplog.text


# ---------------------------------------------------------------------------
# 3t74w — adopt by name, but stop claiming "identical"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_name_matched_channel_group_no_longer_claims_to_be_identical():
    """``Drill Movies`` id 379 is a DIFFERENT object that happens to share a name.

    The adopt + FK remap is contractual and unchanged (name is the only identity
    a channel group carries across instances — kxuj2 / ADR-008). What changes is
    the claim: ``already_exists_identical`` was asserted without ever comparing
    anything.
    """
    client = _target()
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()

    await import_channel_groups(
        archive_rows=_archive_groups(),
        client=client,
        selected=True,
        report=report,
        ledger=RollbackLedger(restore_id="t"),
        remap=remap,
    )

    cat = report.category(EntityType.CHANNEL_GROUP)
    assert [(d.label, d.reason) for d in cat.skip_details] == [
        ("Drill Movies", SkipReason.ALREADY_EXISTS_NAME_MATCH)
    ]
    # Behaviour is untouched: the adopt still happens and the FK still remaps.
    assert remap.resolve(EntityType.CHANNEL_GROUP, 377) == 379
    assert cat.created == 2
    assert SkipReason.ALREADY_EXISTS_IDENTICAL not in {d.reason for d in cat.skip_details}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "importer, entity_type, existing_key",
    [
        (import_channel_profiles, EntityType.CHANNEL_PROFILE, "get_channel_profiles"),
        (import_stream_profiles, EntityType.STREAM_PROFILE, "get_stream_profiles"),
    ],
)
async def test_profiles_still_report_already_exists_identical(
    importer, entity_type, existing_key
):
    """The generic engine is shared — only channel groups get the new reason.

    ``_import_category`` drives all three categories. Changing the skip reason
    for one of them must not silently reclassify the other two, whose reports
    operators and the restore UX already read.
    """
    from unittest.mock import AsyncMock

    client = AsyncMock()
    getattr(client, existing_key).return_value = [{"id": 700, "name": "Sports"}]
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()

    await importer(
        archive_rows=[{"id": 5, "name": "Sports"}],
        client=client,
        selected=True,
        report=report,
        ledger=RollbackLedger(restore_id="t"),
        remap=remap,
    )

    cat = report.category(entity_type)
    assert [d.reason for d in cat.skip_details] == [SkipReason.ALREADY_EXISTS_IDENTICAL]
    assert remap.resolve(entity_type, 5) == 700


# ---------------------------------------------------------------------------
# tddmw + the dgnms discipline — what a PREVIEW may claim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_dry_run_predicts_the_same_drift_the_apply_reports():
    """Preview N == apply N, from the same inputs (bead ``dgnms``).

    The drift count is the number that tells an operator merging into a live
    install what ``replace`` is about to do to their lineup, and it is useless
    after the fact.
    """
    dry_report, _ = await _reconcile(
        _target(), mode=ChannelReattachMode.PRESERVE, is_dry_run=True
    )
    apply_report, _ = await _reconcile(_target(), mode=ChannelReattachMode.PRESERVE)

    assert dry_report.channel_group_drift == apply_report.channel_group_drift == 7
    assert _drift_by_channel(dry_report) == _drift_by_channel(apply_report)


@pytest.mark.asyncio
async def test_a_dry_run_in_overwrite_predicts_the_moves_without_making_them():
    client = _target()

    report, moved = await _reconcile(
        client, mode=ChannelReattachMode.OVERWRITE, is_dry_run=True
    )

    assert moved == 7
    assert all(d.moved for d in report.channel_group_drift_details)
    assert client.patches == []


@pytest.mark.asyncio
async def test_a_preview_emits_the_streams_category_it_used_to_omit():
    """Run 12: apply reported ``Streams 9 CREATED``; the preview had NO row.

    Not ``0`` — absent. That is the category that synthesizes placeholder streams
    on a matcher miss, so it is exactly the one an operator wants forewarning of.
    It cannot be predicted (the provider streams it matches against are
    materialized by the DEFERRED M3U ingest), so it is emitted as NOT PREDICTED
    rather than left out — the same answer the null stream-health counters give.
    """
    client = _target()
    report, _, _, _ = await _restore_groups_then_channels(client, is_dry_run=True)

    stream_cats = [c for c in report.categories if c.entity_type == EntityType.STREAM]
    assert len(stream_cats) == 1, "the preview must render a Streams row"
    assert stream_cats[0].predicted is False
    assert stream_cats[0].caveat


@pytest.mark.asyncio
async def test_an_apply_leaves_the_streams_category_predicted():
    """Only a PREVIEW carries the not-predicted flag; an apply reports facts."""
    client = _target()
    report, _, _, _ = await _restore_groups_then_channels(client, is_dry_run=False)

    for cat in report.categories:
        assert cat.predicted is True
        assert cat.caveat is None


@pytest.mark.asyncio
async def test_a_preview_says_the_channel_groups_split_can_differ_from_the_apply():
    """Run 12: preview ``378 WILL CREATE / 0 WILL SKIP``, apply ``3 / 375``.

    The counts are RIGHT for the state a preview can see; the M3U ingest
    materializes the 375 provider groups before the ``channel_groups`` category
    runs, and a preview refreshes nothing. The fix is to say so, not to model an
    ingest the preview cannot perform.
    """
    client = _target()
    report, _, _, _ = await _restore_groups_then_channels(client, is_dry_run=True)

    caveat = report.category(EntityType.CHANNEL_GROUP).caveat
    assert caveat
    assert "M3U" in caveat


@pytest.mark.asyncio
async def test_the_channel_groups_caveat_is_absent_on_an_apply():
    client = _target()
    report, _, _, _ = await _restore_groups_then_channels(client, is_dry_run=False)

    assert report.category(EntityType.CHANNEL_GROUP).caveat is None


# ---------------------------------------------------------------------------
# The relink-mode default must keep resolving the SAFE way
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw", [None, "", "obliterate", "regroup", object(), 17]
)
def test_a_degenerate_relink_value_still_resolves_to_preserve(raw):
    """Widening what relink governs must not widen what a bad value does.

    ``preserve`` now also means "do not regroup your channels". A value that
    fails to parse must still land there — the unsafe direction is never the
    fallback (the guarantee ``ChannelReattachMode.coerce`` was written for).
    """
    assert ChannelReattachMode.coerce(raw) is ChannelReattachMode.PRESERVE
