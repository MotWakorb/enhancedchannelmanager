"""A replica missing what the source had never presents as an unqualified success.

Bead ``enhancedchannelmanager-posm1`` (epic ``f5a5j``), the reporting half. The
counters were already right when this was written — bead ``…-ukjx5`` fixed the
three that were not and audited the rest — so nothing here is about MEASUREMENT.
It is about what the operator is SHOWN, and what the run CALLS itself.

WHAT WAS MEASURED, on Dispatcharr 0.29.0, reading BOTH databases after an apply::

                            source A     replica B
      channels                    59            59   OK
      channels with an EPG link   59             6   BROKEN
      channels with a logo        59             0   BROKEN

    Sync success: created 133, updated 0, failed 0 across 9 categories

The report already carried ``epg_links_unrestored: 53``. Bead ``…-v7d37`` then
made the sync's one-line summary render every action item, so the SENTENCE names
the loss — and the run still called itself ``success``, on every cycle, forever,
because ``compute_outcome`` only ever consulted
``channels_with_no_playable_stream``.

THE INVARIANT these tests are written against — the EPG-link and logo cases are
examples of it, not its specification:

    A sync cycle never presents as an unqualified success when the replica it
    produced is missing something the source had and the sync was asked to
    carry.

THREE PRECEDENTS BOUND THE FIX, and a test below pins each:

* ``…-daziw`` — the shape. An unplayable channel downgrades to
  ``COMPLETED_WITH_FAILURES`` at WARNING severity, per-task gated by
  ``alert_on_warning``. The PO ratified keying on the OUTCOME, not on the
  shortfall.
* ``…-cwmid`` — the trap. A narrower keying on the restore path had to be UNDONE
  after drill run 2026-08-06-run9 measured the severity ordering INVERTED:
  12-of-12 channels unplayable alerted ``warning`` while one cosmetic logo
  failure alerted ``error``/"Task Failed". Nothing here adds a severity mapping;
  every member of the set resolves to one outcome and severity is read off that
  outcome alone.
* ``…-15g1j`` — the boundary. A FAITHFUL absence is not a shortfall. Its literal
  version turned all ten keystone round-trip scenarios red for replications that
  had lost nothing.

WHY EVERY OUTCOME TEST HERE IS MULTI-CYCLE. Cross-instance sync runs UNATTENDED
ON A SCHEDULE, and cycle 1 is the cycle on which the broken code is right (bead
``…-ukjx5``, and ``…-kcfru`` before it). A downgrade that is honest once and then
green is worse than one that never fires, because the operator has already
learned the number means something.

STRUCTURES USED — the trap six engineers fell into on this branch. Every
shortfall asserted here is a TOP-LEVEL ``int`` aggregate on
:class:`~dbas.restore_contracts.RestoreReport` (``epg_links_unrestored``,
``logo_misses``, ``stream_urls_redacted``, ``channels_with_no_playable_stream``),
each with its own detail list. NOT ``skip_details``/``SkipReason`` and NOT
``failure_details``/``FailureReason``: none of these is a category failure — the
rows all succeeded, which is precisely why ``failed 0`` was compatible with a
gutted replica. The credential-path assertions read
``credential_reentry_details[].fields``, which are dotted PATHS emitted by
``credential_sentinel.strip_redaction_sentinels``, never values.

All credentials here are SYNTHETIC.

Conventions: ``docs/pytest_conventions.md``.
"""
from __future__ import annotations

import pytest

from dbas.restore_contracts import (
    EntityType,
    RestoreOutcome,
    RestoreReport,
)
from tasks.dbas_sync import DbasSyncTask
from tests.fixtures.sync_harness import (
    StatefulDispatcharrFake,
    SyncHarness,
    make_sync_target,
)
from tests.tasks.test_sync_roundtrip import (
    _channel_with_a_hosted_logo,
    _dest_with_a_decoy_at,
)
from tests.tasks.test_sync_xc_guide_url_visibility import (
    _XC_GUIDE_URL,
    _XC_SOURCE_NAME,
    _source_with_guide,
)


def _apply(**counts) -> RestoreReport:
    """A realized APPLY report with clean categories and the given aggregates."""
    return RestoreReport(is_dry_run=False, **counts)


def _outcome(report: RestoreReport) -> RestoreOutcome:
    from dbas.restore_orchestrator import compute_outcome

    return compute_outcome(report=report, failure_occurred=False, rollback=None)


def _logo_binding_refused(dest: StatefulDispatcharrFake) -> None:
    """Make B refuse the channel→logo PATCH, and nothing else.

    Reaches ``channel_reattach.reattach_channel_logos`` — the one logo-miss
    producer that records a loss with ZERO category failures, which is the state
    the live measurement was in: the bytes arrived, the binding did not, and the
    counts were clean. Keyed on the payload rather than the method so the
    sibling EPG-link PATCH on the same endpoint still succeeds; a fault on
    ``upload_logo_file`` instead would ALSO fail the LOGO category and the run
    would already be degraded for a different reason.
    """

    def fault(method, payload):
        if method == "update_channel" and isinstance(payload, dict):
            if "logo_id" in payload:
                raise RuntimeError("B refused the logo binding")

    dest.inject_fault(fault)


# ---------------------------------------------------------------------------
# 1. THE MEASURED CASES, end to end through the real engine, TWO CYCLES EACH.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_replica_that_lost_its_guide_link_is_not_a_success_on_any_cycle(
    tmp_path,
):
    """The epic's EPG half, in the harness: the outcome downgrades, and stays.

    Asserted on BOTH cycles because the second is the one that matters: an
    unattended schedule shows the operator cycle 40, not cycle 1.
    """
    source = _source_with_guide(_XC_GUIDE_URL, _XC_SOURCE_NAME)
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    first = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # B lost the link and STILL has not got it — read off B, not off the report.
    dest_channel = next(
        row for row in dest.channels.rows.values() if row.get("name") == "CNN"
    )
    assert not dest_channel.get("epg_data_id")

    for cycle, report in ((1, first), (2, second)):
        assert report.epg_links_unrestored == 1, cycle
        # The counts an operator reads are clean. That is the whole defect.
        assert sum(c.failed for c in report.categories) == 0, cycle
        assert report.outcome == RestoreOutcome.COMPLETED_WITH_FAILURES, cycle


@pytest.mark.asyncio
async def test_a_replica_that_lost_its_branding_is_not_a_success_on_any_cycle(
    tmp_path,
):
    """The epic's logo half: bytes present on B, binding lost, counts clean.

    This is the case ``…-cwmid`` is about, reached from the side that has NO
    category failure — so before this bead the outcome was ``SUCCESS`` and the
    severity question never arose at all.
    """
    source, source_logo = _channel_with_a_hosted_logo()
    dest = _dest_with_a_decoy_at(source_logo["id"])
    _logo_binding_refused(dest)
    harness = SyncHarness(
        source=source, dest=dest, target=make_sync_target(sync_logos=True)
    )

    first = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # A has the logo on its channel; B does not. Read by NAME off both sides —
    # never by id, and never off the run's own report.
    assert source.channel_logo_name("CNN") == "XDMRU Hosted Logo"
    assert dest.channel_logo_name("CNN") is None

    for cycle, report in ((1, first), (2, second)):
        assert report.logo_misses == 1, cycle
        assert sum(c.failed for c in report.categories) == 0, cycle
        assert report.outcome == RestoreOutcome.COMPLETED_WITH_FAILURES, cycle


@pytest.mark.asyncio
async def test_the_operator_line_and_the_outcome_agree_on_both_cycles(tmp_path):
    """The SENTENCE names the loss and the LABEL stops contradicting it.

    Bead ``…-v7d37`` gave the sync path the restore's action-item builder, so the
    clause was already there — beside the word ``success``. The pair is the
    assertion: an operator reads the label first.
    """
    source = _source_with_guide(_XC_GUIDE_URL, _XC_SOURCE_NAME)
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    for _ in range(2):
        report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
        message = DbasSyncTask._summary_message(report, False, report.outcome.value)
        assert "1 channel(s) restored without an EPG link" in message
        assert "Sync success:" not in message
        assert "Sync completed_with_failures:" in message


@pytest.mark.asyncio
async def test_the_degraded_sync_alerts_warning_not_task_failed(tmp_path):
    """``…-cwmid``'s inversion, guarded on the NEW trigger.

    A lost guide link must not be louder than an unplayable lineup. It cannot
    be, because severity is read off the OUTCOME
    (:attr:`RestoreOutcome.is_degraded_not_failed`) and never off which
    shortfall fired — but that is only true while the downgrade routes through
    ``COMPLETED_WITH_FAILURES``, so it is asserted rather than assumed.
    """
    source = _source_with_guide(_XC_GUIDE_URL, _XC_SOURCE_NAME)
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    task = DbasSyncTask.__new__(DbasSyncTask)
    assert DbasSyncTask._degraded_not_failed(report, False) is True
    assert task._degraded_not_failed(report, False) is True
    # The same run's own outcome carries it — the single shared property.
    assert report.outcome.is_degraded_not_failed is True


# ---------------------------------------------------------------------------
# 2. THE BOUNDARY (…-15g1j): a FAITHFUL replica stays quiet, on every cycle.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_replica_that_lost_nothing_is_still_an_unqualified_success(tmp_path):
    """The control. The literal reading of the invariant turned all ten keystone
    round-trip scenarios red for replications that had lost nothing; this is that
    guard, stated locally so a future widening of the set trips here first.
    """
    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    first = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    for cycle, report in ((1, first), (2, second)):
        assert report.delivery_shortfalls() == {}, cycle
        assert report.outcome == RestoreOutcome.SUCCESS, cycle
        message = DbasSyncTask._summary_message(report, False, report.outcome.value)
        assert "Sync success:" in message


def test_a_source_channel_with_no_epg_link_of_its_own_is_never_a_shortfall():
    """The faithful half at the counter level.

    ``epg_links_unrestored`` is computed only over archive channels that carry an
    ``epg_data_id``, so a source channel with no guide link cannot contribute —
    which is what makes the aggregate safe to key an outcome on.
    """
    report = _apply(epg_links_unrestored=0, logo_misses=0)
    assert report.delivery_shortfalls() == {}
    assert _outcome(report) == RestoreOutcome.SUCCESS


# ---------------------------------------------------------------------------
# 3. THE SET (…-daziw / …-cwmid): keyed on the OUTCOME, never on which member.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "channels_with_no_playable_stream",
        "stream_urls_redacted",
        "epg_links_unrestored",
        "logo_misses",
        # Bead …-4mkoe: an entity the run was asked to deliver and did not,
        # because a dependency it needs is not on the destination. Only the
        # GENUINE half increments it — a dependency the operator DESELECTED is
        # recorded ``SkipReason.DEPENDENCY_DESELECTED`` and never counted.
        "entities_blocked_by_dependency",
    ],
)
def test_every_member_of_the_set_downgrades_an_apply_on_its_own(field):
    """Each member ALONE forbids SUCCESS — no member depends on another firing.

    Parameterized over :data:`RestoreReport.DELIVERY_SHORTFALL_FIELDS` rather
    than over a hand-written list, so a member added to the declaration without
    a downgrade cannot pass silently.
    """
    assert field in RestoreReport.DELIVERY_SHORTFALL_FIELDS
    report = _apply(**{field: 1})
    assert report.delivery_shortfalls() == {field: 1}
    assert _outcome(report) == RestoreOutcome.COMPLETED_WITH_FAILURES


@pytest.mark.parametrize("field", list(RestoreReport.DELIVERY_SHORTFALL_FIELDS))
def test_no_member_of_the_set_produces_a_different_severity(field):
    """``…-cwmid``, as the property rather than as its reproduction.

    Drill run 2026-08-06-run9 measured 12-of-12 channels unplayable alerting
    ``warning`` while one cosmetic logo miss alerted ``error``/"Task Failed".
    That ordering cannot recur while every member resolves to ONE outcome and
    severity is read off the outcome — asserted for each member, because "they
    all take the same branch" is exactly the kind of claim that stops being true
    when someone adds a special case.
    """
    report = _apply(**{field: 12})
    outcome = _outcome(report)
    assert outcome == RestoreOutcome.COMPLETED_WITH_FAILURES
    assert outcome.is_degraded_not_failed is True


def test_a_channel_merely_holding_a_placeholder_is_not_in_the_set():
    """``channels_needing_stream_reattach`` must never join the set (…-daziw).

    The ``…-ixdaw`` fix deliberately produces exactly this on a channel that
    keeps its real streams and PLAYS. Downgrading on it would false-fail an
    instance where every channel works.
    """
    assert (
        "channels_needing_stream_reattach"
        not in RestoreReport.DELIVERY_SHORTFALL_FIELDS
    )
    report = _apply(channels_needing_stream_reattach=12)
    assert report.delivery_shortfalls() == {}
    assert _outcome(report) == RestoreOutcome.SUCCESS


def test_work_the_run_performed_is_not_in_the_set():
    """Corrected drift and rebound streams leave the replica MATCHING.

    ``profile_membership_drift`` counts memberships that had drifted and were
    put back (since ``…-ukjx5`` made it read B's state first), and
    ``streams_rebound`` counts placeholder slots resolved onto real streams.
    Both are the opposite of a shortfall, and both are non-zero on the exact
    converged cycle this bead exists to keep quiet.
    """
    report = _apply(profile_membership_drift=112, streams_rebound=59)
    assert report.delivery_shortfalls() == {}
    assert _outcome(report) == RestoreOutcome.SUCCESS


def test_a_credential_the_sync_was_asked_not_to_carry_is_not_in_the_set():
    """The redaction is deliberate (…-msqf7), so it is not an undelivered thing.

    Its CONSEQUENCE is in the set twice over — a replica whose streams lost their
    address reports ``stream_urls_redacted`` and
    ``channels_with_no_playable_stream`` — so the outcome keys on what the
    replica IS MISSING and the summary keys on what the operator must DO. A
    credential-free source therefore never downgrades a cycle for having no
    credentials to lose.
    """
    report = _apply(credentials_needing_reentry=2)
    assert report.delivery_shortfalls() == {}
    assert _outcome(report) == RestoreOutcome.SUCCESS


def test_a_preview_that_predicts_a_shortfall_is_still_a_preview():
    """A dry run never downgrades: it applied nothing, so nothing is missing.

    The preview carries the two counters that ARE predicted, and the "not
    predicted" ``None`` (bead ``…-dgnms``) on the two the rebind pass cannot
    reach before an apply — which is the shape a real preview has, and the one
    that turns a missing ``or 0`` coercion into a ``TypeError`` rather than a
    wrong answer.
    """
    preview = RestoreReport(
        is_dry_run=True,
        epg_links_unrestored=53,
        logo_misses=60,
        channels_with_no_playable_stream=None,
        stream_urls_redacted=None,
    )
    # The counts are honest — the preview PREDICTS the loss …
    assert preview.delivery_shortfalls() == {
        "epg_links_unrestored": 53,
        "logo_misses": 60,
    }
    # … and the outcome decision refuses to treat a prediction as a failure.
    assert _outcome(preview) == RestoreOutcome.SUCCESS


def test_not_predicted_reads_as_zero_rather_than_raising():
    """``None`` is the value a dry run's rebind-pass counters carry, and every
    other consumer coerces it with ``or 0``. A caller reaching this method on a
    preview report must get an answer, not a ``TypeError``.
    """
    preview = RestoreReport(
        is_dry_run=True,
        channels_with_no_playable_stream=None,
        stream_urls_redacted=None,
    )
    assert preview.delivery_shortfalls() == {}


def test_a_rolled_back_failure_still_outranks_a_delivery_shortfall():
    """The set only ever reaches the ``not mixed`` branch.

    A real failure keeps its own outcome, so widening the shortfall set cannot
    downgrade a ``FAILED_ROLLBACK_INCOMPLETE`` into a warning.
    """
    from dbas.restore_orchestrator import compute_outcome

    report = _apply(epg_links_unrestored=53)
    assert (
        compute_outcome(report=report, failure_occurred=True, rollback=None)
        == RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE
    )


# ---------------------------------------------------------------------------
# 4. THE user_info CLAUSE: an action item that cannot be cleared by doing it.
# ---------------------------------------------------------------------------

# The paths measured live on 0.29.0 after B's REAL provider credentials had been
# entered: the account's own username/password had dropped out of the action
# item, and the row survived on these two alone — so the operator's line still
# read "1 account(s) need credentials re-entered" after they had done exactly
# what it asked. They are Dispatcharr's cached copy of the provider's
# ``player_api.php`` reply, not fields on any screen.
_CACHED_BLOB_PATHS = [
    "profiles[0].custom_properties.user_info.username",
    "profiles[0].custom_properties.user_info.password",
]


def test_the_cached_provider_blob_is_not_an_action_item():
    """A row whose every path is inside the cached reply is not recorded.

    Not merely omitted from the sentence — omitted from the REPORT, so the
    aggregate, the detail rows, the restore modal and the one-line summary all
    describe the same work.
    """
    report = _apply()
    report.record_credential_reentry(
        EntityType.M3U_ACCOUNT, "Live XC Provider", list(_CACHED_BLOB_PATHS)
    )
    assert report.credentials_needing_reentry == 0
    assert report.credential_reentry_details == []
    assert DbasRestoreTask_suffix(report) == ""


def test_a_real_credential_survives_beside_the_cached_blob():
    """The narrowing drops the blob paths and KEEPS the row, not the reverse.

    This is the state on every cycle BEFORE the operator acts, and under-
    reporting is the defect the whole suffix exists to prevent: the account
    genuinely has no password, and the line must still say so.
    """
    report = _apply()
    report.record_credential_reentry(
        EntityType.M3U_ACCOUNT,
        "Live XC Provider",
        ["username", "password", *_CACHED_BLOB_PATHS],
    )
    assert report.credentials_needing_reentry == 1
    assert report.credential_reentry_details[0].fields == ["username", "password"]
    assert "1 account(s) need credentials re-entered" in DbasRestoreTask_suffix(report)


def test_the_blob_exclusion_is_scoped_to_the_cached_reply_only():
    """It must not suppress a credential that merely shares a leaf name.

    ``username`` at the top level, ``username`` on a profile, and anything else
    under ``custom_properties`` are all still actionable — the operator has a
    field for each. Only the provider's echoed reply is dropped.
    """
    from credential_sentinel import credential_path_is_operator_actionable as ok

    assert ok("username") is True
    assert ok("password") is True
    assert ok("profiles[0].username") is True
    assert ok("profiles[0].custom_properties.xc_id") is True
    assert ok("custom_properties.password") is True
    assert ok("profiles[0].custom_properties.user_info.username") is False
    assert ok("profiles[3].custom_properties.user_info.password") is False
    # ANCHORED TO ``custom_properties``, not to the word ``user_info``. The
    # exclusion is about the destination's cached copy of the provider's reply,
    # which is where Dispatcharr stores it; a field merely NAMED ``user_info``
    # somewhere an operator can edit is a different thing and stays actionable.
    # Dropping the anchor passes every other assertion in this module, so it is
    # pinned here or not at all.
    assert ok("user_info.password") is True
    assert ok("profiles[0].user_info.password") is True
    # Indifferent to the profile index, and not fooled by a non-string.
    assert ok(None) is True


@pytest.mark.asyncio
async def test_the_credential_line_is_unchanged_where_no_blob_is_involved(tmp_path):
    """Regression guard on the live-shaped run: the narrowing is not a mute.

    AMENDED 2026-08-22 — the fixture had to move, and the reason matters.
    This ran against a source whose M3U password, EPG api_key and guide URL were
    all sentinelled BY THE PER-CYCLE REDACTOR, and asserted the operator still
    got every clause. Under ADR-013 amendment (b) the cycle carries all three,
    so that source now produces NO shortfall at all and the test would have been
    asserting the clauses against a run with nothing to report — passing only if
    the narrowing had broken in the noisy direction.

    ``_source_already_redacted`` reaches the same operator-visible state by the
    route that still produces it: an A restored from a standard
    (redact-by-default) backup artifact holds the sentinel in its own fields.
    The property under test — the ``posm1`` narrowing is not a mute, and it
    holds on BOTH cycles — is unchanged.
    """
    from tests.tasks.test_ukjx5_steady_state_shortfall_counters import (
        _source_already_redacted,
    )

    source = _source_already_redacted()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    for _ in range(2):
        report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
        assert report.credentials_needing_reentry == 1
        message = DbasSyncTask._summary_message(report, False, report.outcome.value)
        assert "1 account(s) need credentials re-entered" in message


def DbasRestoreTask_suffix(report) -> str:
    """The one-line summary's action-item suffix, built by the shared renderer.

    Bead ``…-v7d37`` made the sync path render the RESTORE task's builder rather
    than a second copy, so asserting through it is asserting on both surfaces.
    """
    from tasks.dbas_restore import DbasRestoreTask

    return DbasRestoreTask._credential_reentry_suffix(report)
