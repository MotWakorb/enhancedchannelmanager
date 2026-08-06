"""A preview must not report a confident ``0`` for a condition the apply reports as N.

Bead ``enhancedchannelmanager-dgnms``. Drill run 2026-08-05-run4 previewed and
then applied the SAME standard artifact against a FRESH target, back to back. The
per-category ``would_create`` counts matched the apply's ``created`` EXACTLY for
every category — so the category counts were sound. The health/split counters
were not:

==========================================  =======  =====
counter                                     PREVIEW  APPLY
==========================================  =======  =====
``epg_link_reattach.created_channels``            12     12
``logo_reattach.created_channels``                 0     11
``channels_needing_stream_reattach``               0     12
``channels_with_no_playable_stream``               0     12
``profile_membership_drift``                       0      6
==========================================  =======  =====

An operator is told to preview before applying. A preview that reports "0
channels needing attention" for a restore that will leave all 12 unplayable is
worse than no number at all.

THREE DIFFERENT DEFECTS, THREE DIFFERENT ANSWERS
------------------------------------------------
* ``logo_reattach`` — PREDICTED. A fresh target matches no logo, so every logo is
  a would-CREATE and has no destination id; the whole population fell out of the
  split. The logos importer now reports the SOURCE ids it would create and the
  reattach pass counts those channels. No destination id is invented.
* ``profile_membership_drift`` — PREDICTED. The pass was apply-only, yet the
  flip set is computable from the archive alone: it is the restored channels the
  archived profile EXCLUDES, against Dispatcharr's enable-everything default.
* the two stream-health counters — NOT PREDICTED, and now say so with ``None``
  instead of ``0``. The pass that writes them re-matches against provider streams
  the DEFERRED M3U refresh materializes, and a preview refreshes nothing. The
  number is genuinely unknowable before the apply, so the honest report is "not
  predicted" rather than a fabricated one.

THE REGRESSION THAT WOULD HURT MOST is the populated-target split, which the same
drill measured as EXACT in both relink modes. Three distinct correct shapes exist
and all three are pinned below.

Conventions: ``docs/pytest_conventions.md``; the Dispatcharr client is an
``AsyncMock`` (no live upstream). Helpers are imported from the state-loss suite
rather than copied, so these tests track that fixture instead of a snapshot of it.
"""
from __future__ import annotations

import pytest

from dbas.restore_contracts import (
    ChannelReattachMode,
    EntityType,
    IdRemapTable,
    RestoreReport,
)
from tests.dbas.test_restore_state_loss import _archive_channel, _client


# ---------------------------------------------------------------------------
# 1. logo_reattach on a FRESH target — preview 0 vs apply 11
# ---------------------------------------------------------------------------


def _fresh_target_channels() -> list[dict]:
    """Two archived channels, each pointing at a logo the destination lacks."""
    return [
        _archive_channel(101, "Made By Restore", logo_id=55),
        _archive_channel(102, "Also Made By Restore", logo_id=56),
    ]


def _fresh_target_remap() -> IdRemapTable:
    """A FRESH target's remap: channels resolve, no logo matched anything.

    The channels importer registers a provisional destination id for a
    would-create on a dry run; the logos importer deliberately does NOT, because
    the id an apply mints is not knowable. That asymmetry is the whole defect.
    """
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 101)
    remap.add(EntityType.CHANNEL, 102, 102)
    return remap


@pytest.mark.asyncio
async def test_fresh_target_preview_predicts_the_logo_split_it_used_to_read_zero_for():
    """The would-CREATE population is the whole population on a fresh target.

    Drill run 4: preview ``logo_reattach.created_channels: 0`` against apply
    ``11``. Without ``would_create_logo_source_ids`` the preview sees no
    destination logo id for any of them and reports nothing.
    """
    from dbas.channel_reattach import reattach_channel_logos

    report = RestoreReport(is_dry_run=True)

    await reattach_channel_logos(
        client=_client(),
        report=report,
        remap=_fresh_target_remap(),
        archive_channels=_fresh_target_channels(),
        created_source_ids={101, 102},
        mode=ChannelReattachMode.PRESERVE,
        is_dry_run=True,
        would_create_logo_source_ids={55, 56},
    )

    assert report.logo_reattach.created_channels == 2
    # Still not a claim of loss: a preview never says the operator lost a logo.
    assert report.logo_misses == 0


@pytest.mark.asyncio
async def test_fresh_target_preview_and_apply_agree_on_the_logo_split():
    """Preview N == apply N for the fresh-target case the drill measured.

    The apply resolves real destination ids through the LOGO remap; the preview
    resolves the would-create source ids. Different roads, and they must reach
    the same number — that is the property the operator is relying on.
    """
    from dbas.channel_reattach import reattach_channel_logos

    archive_channels = _fresh_target_channels()

    dry_report = RestoreReport(is_dry_run=True)
    await reattach_channel_logos(
        client=_client(), report=dry_report, remap=_fresh_target_remap(),
        archive_channels=archive_channels,
        created_source_ids={101, 102},
        mode=ChannelReattachMode.PRESERVE,
        is_dry_run=True,
        would_create_logo_source_ids={55, 56},
    )

    # The apply: the logos importer has now uploaded both, so both have ids.
    applied_remap = _fresh_target_remap()
    applied_remap.add(EntityType.LOGO, 55, 955)
    applied_remap.add(EntityType.LOGO, 56, 956)
    apply_report = RestoreReport(is_dry_run=False)
    await reattach_channel_logos(
        client=_client(), report=apply_report, remap=applied_remap,
        archive_channels=archive_channels,
        created_source_ids={101, 102},
        mode=ChannelReattachMode.PRESERVE,
        is_dry_run=False,
    )

    assert dry_report.logo_reattach.created_channels == 2
    assert (
        dry_report.logo_reattach.created_channels
        == apply_report.logo_reattach.created_channels
    )


@pytest.mark.asyncio
async def test_a_logo_the_preview_will_not_create_stays_out_of_the_split():
    """A REJECTED logo is not a would-create, so it must not be counted.

    The importer records a source id only on a path that decided the logo comes
    back. A logo it refuses (bad bytes, unreadable content) is absent from the
    set, and this pass must leave that channel out of the split exactly as
    before — the preview must not over-report either.
    """
    from dbas.channel_reattach import reattach_channel_logos

    report = RestoreReport(is_dry_run=True)

    await reattach_channel_logos(
        client=_client(),
        report=report,
        remap=_fresh_target_remap(),
        archive_channels=_fresh_target_channels(),
        created_source_ids={101, 102},
        mode=ChannelReattachMode.PRESERVE,
        is_dry_run=True,
        # Logo 56 was rejected: only 55 comes back.
        would_create_logo_source_ids={55},
    )

    assert report.logo_reattach.created_channels == 1
    assert report.logo_misses == 0


@pytest.mark.asyncio
async def test_the_apply_never_reads_the_would_create_set():
    """An apply resolves real ids only — a would-create id is never PATCHable.

    Guards the direction of the widening: if the apply ever consulted the set it
    would try to PATCH ``logo_id=None`` onto a channel, or count a logo that does
    not exist. It must record a MISS, exactly as it did before.
    """
    from dbas.channel_reattach import reattach_channel_logos

    client = _client()
    report = RestoreReport(is_dry_run=False)

    await reattach_channel_logos(
        client=client,
        report=report,
        remap=_fresh_target_remap(),   # no LOGO entries at all
        archive_channels=_fresh_target_channels(),
        created_source_ids={101, 102},
        mode=ChannelReattachMode.PRESERVE,
        is_dry_run=False,
        would_create_logo_source_ids={55, 56},
    )

    assert report.logo_reattach.created_channels == 0
    assert report.logo_misses == 2
    client.update_channel.assert_not_awaited()


# ---------------------------------------------------------------------------
# 2. THE REGRESSION GUARD — the populated-target splits must not move
# ---------------------------------------------------------------------------


def _populated_target_remap() -> IdRemapTable:
    """A POPULATED target: the channels AND their logos already exist there."""
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)
    remap.add(EntityType.CHANNEL, 102, 202)
    remap.add(EntityType.LOGO, 55, 955)
    remap.add(EntityType.LOGO, 56, 956)
    return remap


@pytest.mark.asyncio
@pytest.mark.parametrize("would_create", [None, set(), {55, 56}])
async def test_populated_overwrite_split_is_unchanged(would_create):
    """POPULATED + overwrite: preview ``created 0 / existing 2``, exactly as measured.

    Parametrized over the new argument INCLUDING a hostile value (the ids of
    logos that in fact matched): a matched logo resolves through the remap and
    the would-create widening is never consulted for it, so no value of the new
    argument may move this number.
    """
    from dbas.channel_reattach import reattach_channel_logos

    report = RestoreReport(is_dry_run=True)

    await reattach_channel_logos(
        client=_client(), report=report, remap=_populated_target_remap(),
        archive_channels=_fresh_target_channels(),
        created_source_ids=set(),          # this restore created NEITHER channel
        mode=ChannelReattachMode.OVERWRITE,
        is_dry_run=True,
        would_create_logo_source_ids=would_create,
    )

    pop = report.logo_reattach
    assert (pop.created_channels, pop.existing_channels, pop.preserved_channels) == (
        0, 2, 0,
    )
    assert pop.existing_channels_named == ["Made By Restore", "Also Made By Restore"]


@pytest.mark.asyncio
@pytest.mark.parametrize("would_create", [None, set(), {55, 56}])
async def test_populated_preserve_split_is_unchanged(would_create):
    """POPULATED + preserve: ``created 0 / existing 0 / preserved 2``.

    A preserved channel is decided BEFORE any logo resolution, so the widening
    can never reach it. Pinned anyway — this is the shape an operator merging
    into a live install sees, and silently turning "left alone" into "would be
    replaced" is the worst regression available here.
    """
    from dbas.channel_reattach import reattach_channel_logos

    report = RestoreReport(is_dry_run=True)

    await reattach_channel_logos(
        client=_client(), report=report, remap=_populated_target_remap(),
        archive_channels=_fresh_target_channels(),
        created_source_ids=set(),
        mode=ChannelReattachMode.PRESERVE,
        is_dry_run=True,
        would_create_logo_source_ids=would_create,
    )

    pop = report.logo_reattach
    assert (pop.created_channels, pop.existing_channels, pop.preserved_channels) == (
        0, 0, 2,
    )


# ---------------------------------------------------------------------------
# 3. The stream-health counters — NOT PREDICTED, never a confident zero
# ---------------------------------------------------------------------------


def test_a_dry_run_reports_the_stream_health_counters_as_not_predicted():
    """``None``, not ``0``: the preview cannot run the pass that answers this."""
    report = RestoreReport(is_dry_run=True)
    assert report.channels_needing_stream_reattach == 0   # the raw default

    report.mark_stream_health_unpredicted()

    assert report.channels_needing_stream_reattach is None
    assert report.channels_with_no_playable_stream is None
    # streams_rebound is deliberately untouched: 0 is literally true there, and
    # it is a work-done counter rather than a needs-attention alarm.
    assert report.streams_rebound == 0


def test_marking_never_erases_a_real_measurement():
    """A recorded detail row wins over the marker — counts are never blanked."""
    report = RestoreReport(is_dry_run=True)
    report.record_stream_reattach_needed(
        name="Obscure", placeholder_streams=["only one"], has_playable_stream=False,
    )

    report.mark_stream_health_unpredicted()

    assert report.channels_needing_stream_reattach == 1
    assert report.channels_with_no_playable_stream == 1


def test_the_not_predicted_marker_serializes_as_null():
    """The wire shape is ``null``, and the field stays OPTIONAL for old clients.

    The API contract change is additive-compatible: the key is still present and
    still optional, and every backend consumer coerces with ``or 0``. A client
    that never looked at it is unaffected; one that did now gets a value it can
    distinguish from a real zero.
    """
    report = RestoreReport(is_dry_run=True)
    report.mark_stream_health_unpredicted()

    payload = report.model_dump(mode="json")
    assert payload["channels_needing_stream_reattach"] is None
    assert payload["channels_with_no_playable_stream"] is None
    # And the coercion every consumer uses still yields a number.
    assert (payload["channels_with_no_playable_stream"] or 0) == 0


def test_an_apply_still_reports_real_integers():
    """The marker is dry-run-only; an apply's zero is a measurement, and stays 0."""
    report = RestoreReport(is_dry_run=False)
    assert report.channels_needing_stream_reattach == 0
    assert report.channels_with_no_playable_stream == 0


@pytest.mark.asyncio
async def test_the_orchestrator_marks_a_dry_run_and_never_marks_an_apply(tmp_path):
    """End of the dry-run path sets the marker; the apply path runs the pass.

    The wiring, not just the recorder: the defect was that ``run_restore``
    reported the default ``0`` on the branch where it skips the rebind.
    """
    from dbas.restore_contracts import RollbackLedger
    from dbas.restore_orchestrator import run_restore
    from tests.dbas.test_restore_orchestrator import _plan

    dry = await run_restore(
        plan=_plan(),
        client=_client(),
        steps=[],
        report=RestoreReport(is_dry_run=True),
        ledger=RollbackLedger(restore_id="dry"),
        remap=IdRemapTable(),
        confirm_apply=False,
        ledger_dir=tmp_path,
    )
    assert dry.is_dry_run is True
    assert dry.channels_needing_stream_reattach is None
    assert dry.channels_with_no_playable_stream is None

    applied = await run_restore(
        plan=_plan(),
        client=_client(),
        steps=[],
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id="apply"),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert applied.is_dry_run is False
    assert applied.channels_needing_stream_reattach == 0
    assert applied.channels_with_no_playable_stream == 0


# ---------------------------------------------------------------------------
# 4. profile_membership_drift — preview 0 vs apply 6
# ---------------------------------------------------------------------------


def _drift_fixture():
    """One profile that EXCLUDES two of its three restored channels."""
    archive_profiles = [{"id": 5, "name": "Kids", "channels": [101]}]
    archive_channels = [
        _archive_channel(101, "Cartoons"),
        _archive_channel(102, "News"),
        _archive_channel(103, "Sports"),
    ]
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL_PROFILE, 5, 5)
    remap.add(EntityType.CHANNEL, 101, 201)
    remap.add(EntityType.CHANNEL, 102, 202)
    remap.add(EntityType.CHANNEL, 103, 203)
    return archive_profiles, archive_channels, remap


@pytest.mark.asyncio
async def test_a_dry_run_predicts_the_profile_membership_drift():
    """The preview names the widening at the point the operator can still act.

    Drill run 4: preview ``profile_membership_drift: 0`` against apply ``6``.
    The counter exists to warn that a profile built to HIDE channels is about to
    expose them, which is precisely a pre-apply decision.
    """
    from dbas.channel_reattach import reattach_profile_memberships

    client = _client()
    archive_profiles, archive_channels, remap = _drift_fixture()
    report = RestoreReport(is_dry_run=True)

    asserted = await reattach_profile_memberships(
        client=client, report=report, remap=remap,
        archive_profiles=archive_profiles,
        archive_channels=archive_channels,
        is_dry_run=True,
    )

    assert asserted == 3                      # the WOULD-BE number
    assert report.profile_membership_drift == 2
    assert report.profile_membership_drift_details[0].channels_disabled == [
        "News", "Sports",
    ]
    # A preview mutates nothing.
    client.update_profile_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_predicted_drift_equals_the_applied_drift():
    """Preview N == apply N, from the same arithmetic over the same inputs."""
    from dbas.channel_reattach import reattach_profile_memberships

    drifts = []
    for dry in (True, False):
        archive_profiles, archive_channels, remap = _drift_fixture()
        report = RestoreReport(is_dry_run=dry)
        await reattach_profile_memberships(
            client=_client(), report=report, remap=remap,
            archive_profiles=archive_profiles,
            archive_channels=archive_channels,
            is_dry_run=dry,
        )
        drifts.append(report.profile_membership_drift)

    assert drifts[0] == drifts[1] == 2


# ---------------------------------------------------------------------------
# 5. END TO END — the whole preview, through the real registry
# ---------------------------------------------------------------------------


def _fresh_target_plan():
    """A FRESH-target archive shaped like the drill's: nothing matches.

    Two channels, each with a logo the destination does not have (one restored
    by upload, one by URL re-create — the two would-create branches), and a
    profile that ENABLES only the first, so the second is drift.
    """
    from dbas.preflight import ImportPlan, PlanCategory
    from tests.dbas.test_dry_run_engine import _GOOD_MANIFEST, _PNG_B64

    return ImportPlan(
        manifest=dict(_GOOD_MANIFEST),
        categories=[
            PlanCategory(
                entity_type=EntityType.CHANNEL_PROFILE,
                entities=[{"id": 20, "name": "Kids", "channels": [50]}],
            ),
            PlanCategory(
                entity_type=EntityType.CHANNEL,
                entities=[
                    {"id": 50, "name": "ESPN", "channel_number": 1, "logo_id": 60},
                    {"id": 51, "name": "CNN", "channel_number": 2, "logo_id": 62},
                ],
            ),
            PlanCategory(
                entity_type=EntityType.LOGO,
                entities=[
                    # Restored by UPLOAD (has bytes).
                    {"id": 60, "name": "espn-logo", "filename": "espn.png",
                     "content_type": "image/png", "content_b64": _PNG_B64},
                    # Restored by URL RE-CREATE (no bytes, absolute http url).
                    {"id": 62, "name": "cnn-logo", "url": "https://cdn.example/cnn.png"},
                ],
            ),
        ],
    )


@pytest.mark.asyncio
async def test_end_to_end_fresh_target_preview_matches_the_apply(tmp_path):
    """The whole drill scenario through the REAL importer registry.

    Pins the wiring, not just the units: the logos importer must report its
    would-create source ids, the orchestrator must forward them, the profile
    pass must be invoked on a dry run at all, and the stream-health counters must
    come back NULL. Each of the four is a separate link and any one of them
    silently returns the preview to reporting 0.
    """
    from dbas.restore_contracts import RollbackLedger
    from dbas.restore_orchestrator import (
        dry_run_importer_steps,
        new_restore_id,
        run_dry_run,
        run_restore,
    )
    from tests.dbas.test_dry_run_engine import _assert_no_mutations
    from tests.dbas.test_dry_run_engine import _client as _engine_client

    dry_client = _engine_client()
    dry = await run_dry_run(
        plan=_fresh_target_plan(), client=dry_client, ledger_dir=tmp_path
    )

    applied = await run_restore(
        plan=_fresh_target_plan(),
        client=_engine_client(),
        steps=dry_run_importer_steps(),
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )

    # A preview still mutates NOTHING, including the profile pass it now runs.
    _assert_no_mutations(dry_client)

    # THE DRILL'S NUMBER: 0 vs 11 becomes N vs N.
    assert applied.logo_reattach.created_channels == 2
    assert dry.logo_reattach.created_channels == applied.logo_reattach.created_channels

    # …and the EPG-link split, which was already correct, must stay correct.
    assert (
        dry.epg_link_reattach.created_channels
        == applied.epg_link_reattach.created_channels
    )

    # Profile drift: the archived profile enables ONE of the two channels.
    assert applied.profile_membership_drift == 1
    assert dry.profile_membership_drift == applied.profile_membership_drift

    # The two counters a preview genuinely cannot know say so.
    assert dry.channels_needing_stream_reattach is None
    assert dry.channels_with_no_playable_stream is None
    assert applied.channels_needing_stream_reattach == 0
    assert applied.channels_with_no_playable_stream == 0


@pytest.mark.asyncio
async def test_the_logos_importer_reports_the_source_ids_it_would_create():
    """The producer half: a dry run names the logos it would bring back.

    Both create branches feed one set — the byte-upload path and the URL
    re-create path — and a REJECTED logo feeds neither.
    """
    from dbas.importers.logos import import_logos
    from dbas.restore_contracts import RollbackLedger
    from tests.dbas.test_dry_run_engine import _PNG_B64
    from tests.dbas.test_dry_run_engine import _client as _engine_client

    result = await import_logos(
        archive_logos=[
            {"id": 60, "name": "by-upload", "filename": "a.png",
             "content_type": "image/png", "content_b64": _PNG_B64},
            {"id": 62, "name": "by-url", "url": "https://cdn.example/b.png"},
            # No bytes and no usable URL: rejected, and NOT a would-create.
            {"id": 63, "name": "unrestorable", "filename": "c.png"},
        ],
        client=_engine_client(),
        selected=True,
        report=RestoreReport(is_dry_run=True),
        ledger=RollbackLedger(restore_id="t"),
        remap=IdRemapTable(),
        is_dry_run=True,
    )

    assert result.would_create_source_ids == {60, 62}


@pytest.mark.asyncio
async def test_an_already_correct_profile_predicts_no_drift():
    """No flip, no drift — a preview must not invent a warning either."""
    from dbas.channel_reattach import reattach_profile_memberships

    archive_profiles = [{"id": 5, "name": "Everything", "channels": [101, 102, 103]}]
    _, archive_channels, remap = _drift_fixture()
    report = RestoreReport(is_dry_run=True)

    await reattach_profile_memberships(
        client=_client(), report=report, remap=remap,
        archive_profiles=archive_profiles,
        archive_channels=archive_channels,
        is_dry_run=True,
    )

    assert report.profile_membership_drift == 0
    assert report.profile_membership_drift_details == []
