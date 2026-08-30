"""A shortfall the replica still exhibits is reported on EVERY cycle.

Bead ``enhancedchannelmanager-ukjx5`` (epic ``f5a5j``). Cross-instance sync runs
UNATTENDED ON A SCHEDULE, so a counter that is honest once and then silent is
worse than one that never fires: the operator has already learned that this
number means something, and the green becomes evidence.

WHAT WAS MEASURED, live on 0.29.0, across consecutive cycles on an UNCHANGED B::

    cycle 1   stream_urls_redacted: 53      credentials_needing_reentry: 2
    cycle 2   stream_urls_redacted:  0      credentials_needing_reentry: 0
    B, unchanged: 53 streams still carry a redacted url, and the credentials
                  were never re-entered

Nothing was fixed between the cycles. The condition is identical; only the
report changed. Both counters were recorded AT CREATE TIME, and on a repeat
cycle the rows already exist and are SKIPPED, so nothing is recorded.

``profile_membership_drift`` is the SAME confusion pointing the other way, and
it is fixed in the same pass: it counted the memberships this cycle ASSERTED
rather than the ones that had DRIFTED, so a converged replica reported ``drift:
4`` forever.

THE INVARIANT these tests are written against — the counters are examples of it,
not its specification:

    A shortfall the destination STILL EXHIBITS is reported on EVERY cycle that
    observes it, and a condition the destination does NOT exhibit is reported on
    NONE.

Two prior rulings bound the fix, and each of the tests below pins one of them:

* ``…-kcfru``: detection reads the DESTINATION'S CURRENT STATE and is unscoped
  and read-only; WRITE AUTHORITY stays ledger-scoped. Nothing here widens what
  a cycle may write.
* ``…-15g1j``: a FAITHFUL absence is not a shortfall. A stream whose SOURCE url
  carries no credential, and an account the source has no credential for, must
  never be counted — trading permanent silence for permanent noise is the same
  defect reached from the other side.

WHY EVERY TEST HERE IS MULTI-CYCLE. A single-apply test is what let this through
in the first place, and ``…-kcfru`` was caught the same way. The creating cycle
is the one cycle on which the broken code is RIGHT.

Structures used (the trap six engineers fell into): these are top-level ``int``
aggregates on :class:`~dbas.restore_contracts.RestoreReport` with their own
detail lists — ``stream_urls_redacted`` / ``stream_url_redaction_details``,
``credentials_needing_reentry`` / ``credential_reentry_details``,
``profile_membership_drift`` / ``profile_membership_drift_details``. NOT
``skip_details``/``SkipReason`` and NOT ``failure_details``/``FailureReason``:
none of them is a failure, the entity was created, and the outcome is decided
elsewhere (bead ``…-posm1`` owns whether any of them downgrades a run).

All credential values here are SYNTHETIC.

Conventions: ``docs/pytest_conventions.md``.
"""
from __future__ import annotations

import pytest

from credential_sentinel import REDACTION_SENTINEL
from dbas.restore_contracts import EntityType
from tasks.dbas_sync import DbasSyncTask
from tests.fixtures.sync_harness import StatefulDispatcharrFake, SyncHarness
from tests.tasks.test_msqf7_stream_url_credential_leak import (
    _DECOY_NUMERIC_URL,
    _DECOY_RESEMBLING_URL,
    _STD_PLAIN_URL,
    _XC_ACCOUNT_NAME,
    _XC_HOST,
    _source_with_xc_streams,
)
from tests.tasks.test_sync_roundtrip import _restricting_profile_source

# The three source streams whose address IS the credential. The two decoys and
# the standard-M3U stream on the same instance are the CONTRAST: they never
# carried one.
_REDACTED_ON_B = {"Summit Sports 1", "Silverline Cinema", "Orbit Sci-Fi"}
_UNTOUCHED_ON_B = {"Pitchside FC", "Matinee Family", "Valley Public"}


def _source_already_redacted() -> StatefulDispatcharrFake:
    """Source-A that ITSELF holds sentinelled credentials and addresses.

    AMENDED 2026-08-22 (ADR-013 amendment (b)). The steady-state tests below
    used to reach this state through the SYNC PATH: A held real credentials, the
    per-cycle redactor sentinelled them, and B was left holding placeholders on
    every cycle. Provider credentials now cross whole, so that route no longer
    produces the shortfall — which is bead ``…-2jvvb`` closed, not a counter
    that stopped working.

    **The steady-state invariant these tests exist for is unchanged and still
    reachable**, by the route that still produces it: A itself restored from a
    STANDARD (redact-by-default) backup artifact, which is a file designed to be
    attachable to a support ticket and therefore still redacts. Such an A holds
    ``***REDACTED***`` in its own account fields and stream addresses; every
    cycle faithfully copies that state to B; and the counters must say so on
    EVERY cycle, which is the property ``ukjx5`` is about.

    So the fixture moved and the invariant did not. Repointing rather than
    deleting matters: a counter that reports the destination's CURRENT state on
    cycle 1 and goes quiet on cycle 2 is the defect this file was written for,
    and it would still be a defect today.
    """
    source = _source_with_xc_streams()
    for row in source.m3u_accounts.rows.values():
        if row.get("name") == _XC_ACCOUNT_NAME:
            row["username"] = REDACTION_SENTINEL
            row["password"] = REDACTION_SENTINEL
    for row in source.streams.rows.values():
        if row.get("name") in _REDACTED_ON_B:
            row["url"] = "%s/live/%s/%s/%d.ts" % (
                _XC_HOST, REDACTION_SENTINEL, REDACTION_SENTINEL, row["id"],
            )
    return source


def _redacted_urls_on(dest: StatefulDispatcharrFake) -> set:
    """The NAMES of the destination streams whose stored url is redacted.

    Read off B's rows, never off the run's own report — the report is the thing
    that lied.
    """
    return {
        str(row.get("name"))
        for row in dest.streams.list()
        if REDACTION_SENTINEL in str(row.get("url") or "")
    }


# ---------------------------------------------------------------------------
# 1. stream_urls_redacted — the replica still holds them, so say so every cycle.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_second_cycle_still_names_the_redacted_urls_b_still_holds(tmp_path):
    """The exact live measurement, in the harness: 3, then 3 — not 3, then 0."""
    source = _source_already_redacted()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    first = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # B DID NOT CHANGE between the cycles — asserted, not assumed.
    assert _redacted_urls_on(dest) == _REDACTED_ON_B

    assert first.stream_urls_redacted == 3
    assert second.stream_urls_redacted == 3
    assert {d.label for d in second.stream_url_redaction_details} == _REDACTED_ON_B


@pytest.mark.asyncio
async def test_the_third_cycle_reports_what_the_second_did(tmp_path):
    """Steady state is the property, not an extension of it.

    A counter that decays over cycles would still pass a two-cycle test if it
    only lost half its rows each time.
    """
    source = _source_already_redacted()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    third = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert third.stream_urls_redacted == second.stream_urls_redacted == 3


@pytest.mark.asyncio
async def test_a_stream_that_lost_nothing_is_never_counted_on_any_cycle(tmp_path):
    """Bead ``…-15g1j``'s faithful-versus-undelivered line, held over cycles.

    The two decoy URLs and the standard-M3U stream carry no credential, cross
    byte-identical, and play on the replica. Counting them would hand the
    operator a number they cannot act on, forever.
    """
    source = _source_with_xc_streams()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    by_name = {row["name"]: row.get("url") or "" for row in dest.streams.list()}
    assert by_name["Pitchside FC"] == _DECOY_RESEMBLING_URL
    assert by_name["Matinee Family"] == _DECOY_NUMERIC_URL
    assert by_name["Valley Public"] == _STD_PLAIN_URL

    named = {d.label for d in second.stream_url_redaction_details}
    assert named.isdisjoint(_UNTOUCHED_ON_B)


@pytest.mark.asyncio
async def test_the_count_falls_to_zero_once_the_replica_holds_real_urls(tmp_path):
    """The signal must be able to go OUT, or it is just a different lie.

    An operator who gives B its own provider account and refreshes it ends up
    with real addresses on those rows. The next cycle must stop reporting a
    shortfall the destination no longer exhibits.
    """
    source = _source_already_redacted()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    first = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    assert first.stream_urls_redacted == 3

    # B's own provider account now supplies the addresses.
    for row in dest.streams.list():
        if REDACTION_SENTINEL in str(row.get("url") or ""):
            dest.streams.update(
                row["id"], {"url": "%s/live/b-own-user/b-own-pass/%d.ts" % (
                    _XC_HOST, row["id"],
                )}
            )

    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert _redacted_urls_on(dest) == set()
    assert second.stream_urls_redacted == 0
    assert second.stream_url_redaction_details == []


@pytest.mark.asyncio
async def test_the_operators_line_repeats_the_clause_on_the_second_cycle(tmp_path):
    """The one line an unattended run produces is the only surface it has."""
    source = _source_already_redacted()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    message = DbasSyncTask._summary_message(second, False, second.outcome.value)

    assert "3 stream(s) restored without a playable URL" in message


@pytest.mark.asyncio
async def test_a_preview_says_not_measured_rather_than_a_confident_zero(tmp_path):
    """Bead ``…-dgnms``'s rule, inherited with the counter's new meaning.

    The number now describes THE DESTINATION, and the pass that reads the
    destination's streams cannot run on a preview. ``0`` would therefore be a
    claim ("B holds no redacted stream") derived from having looked at nothing —
    and on the second cycle it is a FALSE one. ``None`` says what is true.
    """
    source = _source_already_redacted()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    assert _redacted_urls_on(dest) == _REDACTED_ON_B

    preview = await harness.run(confirm_apply=False, ledger_dir=tmp_path)

    assert preview.stream_urls_redacted is None
    message = DbasSyncTask._summary_message(preview, False, "unknown")
    assert "without a playable URL" not in message


# ---------------------------------------------------------------------------
# 2. credentials_needing_reentry — still unset on B, so still an action item.
# ---------------------------------------------------------------------------


def _needing_reentry(report) -> set:
    """``{(entity type, label)}`` the report says still needs a credential."""
    return {(d.entity_type, d.label) for d in report.credential_reentry_details}


@pytest.mark.asyncio
async def test_a_second_cycle_still_reports_the_credentials_b_still_lacks(tmp_path):
    """The account exists on B and authenticates nowhere. Cycle 2 must say so."""
    source = _source_already_redacted()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    first = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # B's XC account still holds NO credential — read off B, not off the report.
    xc_on_b = next(
        row for row in dest.m3u_accounts.list() if row.get("name") == _XC_ACCOUNT_NAME
    )
    assert not xc_on_b.get("username")
    assert not xc_on_b.get("password")

    # ONE account, not three: on this fixture the shortfall comes from A's own
    # sentinelled record (see ``_source_already_redacted``), so only the XC
    # account is short. It used to be three because the per-cycle redactor
    # sentinelled the EPG sources too, and it no longer does.
    assert first.credentials_needing_reentry == 1
    assert second.credentials_needing_reentry == 1
    assert (EntityType.M3U_ACCOUNT, _XC_ACCOUNT_NAME) in _needing_reentry(second)


@pytest.mark.asyncio
async def test_the_account_goes_quiet_once_the_operator_re_enters_it(tmp_path):
    """Re-entering the credential on B ends the report, on the next cycle."""
    source = _source_with_xc_streams()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    xc_on_b = next(
        row for row in dest.m3u_accounts.list() if row.get("name") == _XC_ACCOUNT_NAME
    )
    dest.m3u_accounts.update(
        xc_on_b["id"],
        {"username": "<b-own-xc-user>", "password": "<b-own-xc-secret>"},
    )

    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert (EntityType.M3U_ACCOUNT, _XC_ACCOUNT_NAME) not in _needing_reentry(second)


@pytest.mark.asyncio
async def test_an_account_whose_source_has_no_credential_is_never_reported(tmp_path):
    """The faithful half again: nothing was lost, so nothing is an action item.

    The standard-M3U account on the same source carries no username and an empty
    password. Reporting it would tell the operator to re-enter a credential that
    never existed — on every cycle, forever.
    """
    source = _source_with_xc_streams()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    labels = {label for _, label in _needing_reentry(second)}
    assert "Northwind Local Affiliates (Standard M3U)" not in labels


# ---------------------------------------------------------------------------
# 3. profile_membership_drift — the mirror image, reported forever.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_converged_profile_reports_no_drift_on_the_second_cycle(tmp_path):
    """"Memberships we ASSERTED" is not "memberships that had DRIFTED".

    Recorded as a note on bead ``…-posm1``: on a scheduled task a converged
    cycle reported ``drift: 4`` every time, forever — a number that looks like a
    finding and never changes.
    """
    source = _restricting_profile_source()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    first = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # B still enables exactly what A does — the replica is converged, and the
    # membership pass still did its (idempotent) work.
    assert dest.enabled_channel_names("Kids & Family") == source.enabled_channel_names(
        "Kids & Family"
    )
    # Cycle 1 IS drift: Dispatcharr enabled all four excluded channels on create.
    assert first.profile_membership_drift == 4
    assert second.profile_membership_drift == 0
    assert second.profile_membership_drift_details == []


@pytest.mark.asyncio
async def test_a_membership_that_really_drifted_is_reported_again(tmp_path):
    """The counter must still FIRE, or silencing it was the whole change.

    Someone re-enables a channel the profile exists to hide. The next cycle puts
    it back and says which one.
    """
    source = _restricting_profile_source()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    profile_id = next(
        row_id
        for row_id, row in dest.channel_profiles.rows.items()
        if row.get("name") == "Kids & Family"
    )
    reopened = next(
        row_id
        for row_id, row in dest.channels.rows.items()
        if row.get("name") == "Capitol Report"
    )
    dest.set_membership(profile_id, reopened, True)
    assert "Capitol Report" in dest.enabled_channel_names("Kids & Family")

    third = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert third.profile_membership_drift == 1
    assert third.profile_membership_drift_details[0].channels_disabled == [
        "Capitol Report"
    ]
    assert dest.enabled_channel_names("Kids & Family") == source.enabled_channel_names(
        "Kids & Family"
    )


@pytest.mark.asyncio
async def test_the_preview_predicts_the_apply_on_both_cycles(tmp_path):
    """Preview N == apply N, before AND after convergence.

    The docstring of ``reattach_profile_memberships`` says the prediction is
    exact because neither branch reads the destination. Now that BOTH branches
    read it, that guarantee has to be re-proven rather than assumed.
    """
    source = _restricting_profile_source()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    before = await harness.run(confirm_apply=False, ledger_dir=tmp_path)
    applied = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    assert before.profile_membership_drift == applied.profile_membership_drift == 4

    after = await harness.run(confirm_apply=False, ledger_dir=tmp_path)
    converged = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    assert after.profile_membership_drift == converged.profile_membership_drift == 0


@pytest.mark.asyncio
async def test_a_profile_that_enables_everything_never_reports_drift(tmp_path):
    """The contrast that keeps the fix from being "count nothing"."""
    source = _restricting_profile_source()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert dest.enabled_channel_names("Default Profile") == source.enabled_channel_names(
        "Default Profile"
    )
    named = {d.name for d in second.profile_membership_drift_details}
    assert "Default Profile" not in named


# ---------------------------------------------------------------------------
# 4. The boundaries of each counter, found by mutating the fix and looking for
#    the mutants nothing killed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stream_with_no_address_at_all_is_not_a_redacted_url(tmp_path):
    """Two different losses, two different counters, two different remedies.

    ``url_can_serve`` is False for an EMPTY url as well as for a redacted one,
    so counting "everything that cannot serve" here would fold the operator's
    own URL-less custom stream into a number whose remedy is "give the
    destination its own provider account". A stream with nothing in its address
    was never redacted and belongs to ``channels_with_no_playable_stream``.
    """
    source = _source_already_redacted()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    # An operator-owned, address-less stream on B, under one of B's OWN provider
    # accounts so the residue sweep leaves it alone (it sweeps only the
    # synthetic account ECM creates).
    b_own_account = next(
        row for row in dest.m3u_accounts.list() if row.get("name") == _XC_ACCOUNT_NAME
    )
    dest.streams.create(
        {"name": "Operator Slate", "url": "", "m3u_account": b_own_account["id"]}
    )

    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert second.stream_urls_redacted == 3
    assert "Operator Slate" not in {
        d.label for d in second.stream_url_redaction_details
    }


@pytest.mark.asyncio
async def test_a_stand_in_this_cycle_removed_is_not_still_counted(tmp_path):
    """The reading is taken AFTER the deletes, so it describes what B is LEFT with.

    This is the documented two-step recovery: the operator gives B its own
    provider account, B ingests the real addresses, and the next cycle re-matches
    every channel onto them and removes the stand-ins it no longer needs. A count
    taken before that cleanup would report three streams that are gone by the
    time the operator reads the line.
    """
    source = _source_with_xc_streams()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    b_own_account = next(
        row for row in dest.m3u_accounts.list() if row.get("name") == _XC_ACCOUNT_NAME
    )
    for name in sorted(_REDACTED_ON_B):
        dest.streams.create({
            "name": name,
            "url": "%s/live/b-own-user/b-own-pass/%s.ts" % (_XC_HOST, name[:3].lower()),
            "m3u_account": b_own_account["id"],
        })

    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # B is left holding no redacted row at all — the stand-ins were removed.
    assert _redacted_urls_on(dest) == set()
    assert second.stream_urls_redacted == 0


def test_observing_the_same_destination_twice_does_not_double_the_count():
    """The recorder REPLACES the population; that is what makes it a reading.

    An append-shaped recorder is what tied this number to "what happened during
    this run" in the first place. Pinned directly on the contract so the property
    survives a future second caller.
    """
    from dbas.restore_contracts import RestoreReport

    report = RestoreReport(is_dry_run=False)
    observed = [(7, "Summit Sports 1"), (8, "Silverline Cinema")]

    report.record_redacted_stream_urls(observed)
    report.record_redacted_stream_urls(observed)

    assert report.stream_urls_redacted == 2
    assert [d.label for d in report.stream_url_redaction_details] == [
        "Summit Sports 1",
        "Silverline Cinema",
    ]


@pytest.mark.asyncio
async def test_a_new_channel_the_profile_excludes_is_predicted_before_it_exists(
    tmp_path,
):
    """The preview has to model Dispatcharr's enable-everything create default.

    Once the replica has converged, the profile IS on B — so "what does B enable
    for this profile" answers honestly for every channel B already has, and
    answers NOTHING for a channel that does not exist there yet. The apply will
    create that channel, Dispatcharr will enable it in every profile, and the
    pass will turn it off: one drift. A preview that read B alone would predict
    zero and the operator would learn about the widening after it happened —
    bead ``…-dgnms``'s failure, one layer in.
    """
    source = _restricting_profile_source()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    converged = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    assert converged.profile_membership_drift == 0

    # A new channel on A that 'Kids & Family' does NOT enable.
    profile_id = next(
        row_id
        for row_id, row in source.channel_profiles.rows.items()
        if row.get("name") == "Kids & Family"
    )
    newcomer = source.channels.create(
        {"name": "Late Night Desk", "channel_number": 104, "streams": []}
    )
    source.set_membership(profile_id, newcomer["id"], False)

    preview = await harness.run(confirm_apply=False, ledger_dir=tmp_path)
    applied = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # The AGGREGATE is not enough on its own — a mutant that dropped the
    # create-default term still produced ``1``, by naming the WRONG profile in
    # the WRONG direction (``Default Profile``, channel ENABLED). Assert the row
    # itself, on both sides.
    def _rows(report):
        return [
            (d.name, d.channels_disabled, d.channels_enabled)
            for d in report.profile_membership_drift_details
        ]

    assert preview.profile_membership_drift == applied.profile_membership_drift == 1
    assert _rows(preview) == _rows(applied) == [
        ("Kids & Family", ["Late Night Desk"], []),
    ]


@pytest.mark.asyncio
async def test_a_membership_read_that_fails_says_so_instead_of_claiming_drift():
    """An unreadable destination is UNKNOWN, and the run has to admit it.

    Falling back to the pre-fix meaning — assume Dispatcharr's enable-everything
    default and count what this run turned OFF — is the right fallback: the
    alternative is reporting ``0`` drift for a destination nobody could look at,
    which is the silence this bead exists to end. What is NOT acceptable is doing
    it without saying so, so the note is part of the behaviour and is asserted
    here beside the count.
    """
    from unittest.mock import AsyncMock

    from dbas.channel_reattach import reattach_profile_memberships
    from dbas.restore_contracts import IdRemapTable, RestoreReport

    client = AsyncMock()
    client.get_channel_profiles = AsyncMock(side_effect=RuntimeError("boom"))
    client.update_profile_channel = AsyncMock(return_value={"success": True})
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    for src, dest_id in ((101, 201), (102, 202)):
        remap.add(EntityType.CHANNEL, src, dest_id)
    remap.add(EntityType.CHANNEL_PROFILE, 5, 905)

    await reattach_profile_memberships(
        client=client,
        report=report,
        remap=remap,
        archive_profiles=[{"id": 5, "name": "Drill Subset", "channels": [102]}],
        archive_channels=[{"id": 101, "name": "A"}, {"id": 102, "name": "B"}],
    )

    # The memberships were still applied — a failed READ never stops the WRITE.
    assert client.update_profile_channel.await_count == 2
    # Channel 101 is the one the archive EXCLUDES, so the fallback counts it and
    # not the one the archive enables.
    assert report.profile_membership_drift == 1
    assert report.profile_membership_drift_details[0].channels_disabled == ["A"]
    assert any("could not be read" in note for note in report.notes)


@pytest.mark.asyncio
async def test_two_destination_profiles_sharing_a_name_are_not_guessed_between():
    """A profile's cross-instance identity is its NAME; a duplicated one has none.

    Picking the first row would silently attribute one profile's memberships to
    another and report drift, or its absence, about the wrong profile. Dropping
    the name yields "unknown", which the caller already handles as "these
    memberships will land on Dispatcharr's create default".
    """
    from unittest.mock import AsyncMock

    from dbas.channel_reattach import _destination_enabled_by_profile

    client = AsyncMock()
    client.get_channel_profiles = AsyncMock(return_value=[
        {"id": 1, "name": "Kids & Family", "channels": [10, 11]},
        {"id": 2, "name": "kids & family", "channels": [99]},
        {"id": 3, "name": "Living Room", "channels": [10]},
    ])

    enabled = await _destination_enabled_by_profile(client)

    assert "kids & family" not in enabled
    assert enabled["living room"] == {10}
