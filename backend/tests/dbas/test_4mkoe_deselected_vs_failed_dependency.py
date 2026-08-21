"""An unresolved dependency is TWO opposite facts, and only one is a shortfall.

Bead ``enhancedchannelmanager-4mkoe``. ``SkipReason.DEPENDENCY_UNRESOLVED`` was
one value covering two situations that call for opposite reporting:

* the operator DESELECTED the category the dependency lives in, so it was never
  going to resolve. The absence is what the operator ASKED FOR — reporting it is
  bead ``…-15g1j``'s crying wolf, forever, on every unattended cycle;
* the dependency was IN SCOPE for this run and still is not there. The replica is
  missing something the operator asked for and did not receive.

THE INVARIANT these tests are written against — ``DEPENDENCY_UNRESOLVED`` is one
example of it, not its specification:

    An entity the operator asked for and did not receive is surfaced; an entity
    absent BECAUSE the operator asked for it to be absent is not.

THE MECHANICAL RULE, and why it is not "the upstream category was deselected".
Deselecting ``channel_groups`` while selecting ``channels`` would strand every
channel — and a channel is a first-class entity the operator DID ask for, so its
loss is a shortfall no matter why its group is missing. The only absence the
operator's own selection ENTAILS is a LINK INTO the deselected category, which
this restore records under that category rather than under the entity's own. So:

    a skip is faithful when it is recorded UNDER the same category as the
    dependency it could not resolve, AND that category was deselected.

Both halves of that conjunction are asserted below, per producer.

WHY EVERY OUTCOME TEST HERE IS MULTI-CYCLE. Both halves fail on a second cycle in
opposite directions: a shortfall that is honest once and then silent (``…-ukjx5``)
and a false alarm that fires forever (``…-15g1j``). Cycle 1 is the cycle on which
the broken code is right.

STRUCTURES USED — the trap six engineers fell into on this branch. Three distinct
places carry this, and every assertion below names which one it reads:

* ``EntityCategoryReport.skip_details`` -> ``SkipDetail.reason`` ->
  :class:`~dbas.restore_contracts.SkipReason` — the per-entity CLASSIFICATION,
  and the named drill-down. NOT a failure: none of these rows errored.
* ``failure_details`` / :class:`~dbas.restore_contracts.FailureReason` — NOT used
  here at all, and asserted empty where it matters. A skip is not a failure.
* ``RestoreReport.entities_blocked_by_dependency`` — a TOP-LEVEL ``int``
  aggregate, and the only one of the three the outcome decision reads (through
  :data:`RestoreReport.DELIVERY_SHORTFALL_FIELDS`).

Conventions: ``docs/pytest_conventions.md``.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from dbas.preflight import ImportPlan, PlanCategory
from dbas.restore_contracts import (
    EntityType,
    FailureReason,
    IdRemapTable,
    RestoreOutcome,
    RestoreReport,
    RollbackLedger,
    SkipReason,
)
from dbas.restore_orchestrator import (
    compute_outcome,
    default_importer_steps,
    new_restore_id,
    run_restore,
)
from tasks.dbas_restore import DbasRestoreTask


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _apply(**counts) -> RestoreReport:
    """A realized APPLY report with clean categories and the given aggregates."""
    return RestoreReport(is_dry_run=False, **counts)


def _outcome(report: RestoreReport) -> RestoreOutcome:
    return compute_outcome(report=report, failure_occurred=False, rollback=None)


def _scoped_remap(*selected: EntityType) -> IdRemapTable:
    """A remap whose run scope has been RECORDED — the only state that can ever
    classify an unresolved dependency as deselected."""
    remap = IdRemapTable()
    remap.record_run_scope(selected)
    return remap


def _reasons(report: RestoreReport, entity_type: EntityType) -> list[SkipReason]:
    return [d.reason for d in report.category(entity_type).skip_details]


_DEPENDENCY_REASONS = (
    SkipReason.DEPENDENCY_UNRESOLVED,
    SkipReason.DEPENDENCY_DESELECTED,
)


def _dependency_reasons(
    report: RestoreReport, entity_type: EntityType
) -> list[SkipReason]:
    """Only the two reasons this bead splits — a category can also carry an
    ``EXCLUDED_BY_OPERATOR`` row for the entity its own importer skipped, which
    is a different verdict this bead does not touch."""
    return [r for r in _reasons(report, entity_type) if r in _DEPENDENCY_REASONS]


class _StatefulDest:
    """A destination that remembers what it was given, across cycles.

    Enough of the client surface for the channel + groups/profiles importers.
    Deliberately NOT a fault injector: both halves of this bead reach a report
    with ZERO category failures, which is the whole reason the loss was invisible.
    """

    def __init__(self) -> None:
        self.channels: list[dict] = []
        self.channel_groups: list[dict] = []
        self.channel_profiles: list[dict] = []
        self.stream_profiles: list[dict] = []
        self.user_agents: list[dict] = []
        self.memberships: list[tuple[int, int, bool]] = []
        self._next_id = 5000

    def _mint(self) -> int:
        self._next_id += 1
        return self._next_id

    def as_client(self) -> AsyncMock:
        client = AsyncMock()
        client.get_channel_groups = AsyncMock(return_value=self.channel_groups)
        client.get_channel_profiles = AsyncMock(return_value=self.channel_profiles)
        client.get_stream_profiles = AsyncMock(return_value=self.stream_profiles)
        client.get_user_agents = AsyncMock(return_value=self.user_agents)
        client.get_epg_sources = AsyncMock(return_value=[])
        client.get_m3u_accounts = AsyncMock(return_value=[])

        async def _get_channels(*_a, **_kw):
            return {"count": len(self.channels), "next": None, "results": list(self.channels)}

        client.get_channels = AsyncMock(side_effect=_get_channels)
        client.get_channel_streams = AsyncMock(return_value=[])

        def _creator(store: list[dict]):
            async def _create(payload):
                row = dict(payload) if isinstance(payload, dict) else {"name": payload}
                row["id"] = self._mint()
                store.append(row)
                return row

            return _create

        client.create_channel = AsyncMock(side_effect=_creator(self.channels))
        client.create_channel_group = AsyncMock(side_effect=_creator(self.channel_groups))
        client.create_channel_profile = AsyncMock(
            side_effect=_creator(self.channel_profiles)
        )
        client.create_stream_profile = AsyncMock(
            side_effect=_creator(self.stream_profiles)
        )
        client.create_user_agent = AsyncMock(side_effect=_creator(self.user_agents))

        async def _update_profile_channel(profile_id, channel_id, data):
            self.memberships.append(
                (int(profile_id), int(channel_id), bool(data.get("enabled", True)))
            )
            return {}

        client.update_profile_channel = AsyncMock(side_effect=_update_profile_channel)
        return client


async def _cycle(plan: ImportPlan, dest: _StatefulDest, tmp_path) -> RestoreReport:
    """One realized apply of ``plan`` against ``dest``, through the real chokepoint."""
    return await run_restore(
        plan=plan,
        client=dest.as_client(),
        steps=default_importer_steps(),
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )


def _membership_plan(*, profiles_selected: bool) -> ImportPlan:
    """One channel that belongs to one archived channel profile.

    ``profiles_selected=False`` is the FAITHFUL half: the operator excluded
    channel profiles, so a membership INTO one cannot exist on the replica and
    its absence is exactly what they asked for. The channel carries no group FK,
    so pre-flight has nothing to refuse.
    """
    return ImportPlan(
        manifest={"schema_version": 1},
        categories=[
            PlanCategory(
                entity_type=EntityType.CHANNEL_PROFILE,
                selected=profiles_selected,
                entities=[{"id": 11, "name": "Restricted"}],
            ),
            PlanCategory(
                entity_type=EntityType.CHANNEL,
                selected=True,
                entities=[
                    {
                        "id": 1,
                        "name": "CNN",
                        "channel_number": 5,
                        "profile_memberships": [{"profile_id": 11, "enabled": True}],
                    }
                ],
            ),
        ],
    )


def _degraded_user_agent_plan() -> ImportPlan:
    """A stream profile pointing at a user agent the ARCHIVE does not carry.

    The genuine half, and not a hypothetical: ``_gather_dispatcharr_sections``
    degrades a failed upstream fetch to a ``{"_warning": …}`` stub (bead
    ``…-zt3kf``), so a backup can arrive with a full ``stream_profiles`` slice
    beside an EMPTY ``user_agents`` one. USER_AGENT is SELECTED here — the
    operator asked for it — so nothing about this absence is faithful, and it is
    clearable: re-take the backup and the next restore delivers the profile.
    """
    return ImportPlan(
        manifest={"schema_version": 1},
        categories=[
            PlanCategory(entity_type=EntityType.USER_AGENT, selected=True, entities=[]),
            PlanCategory(
                entity_type=EntityType.STREAM_PROFILE,
                selected=True,
                entities=[
                    {"id": 9, "name": "Proxy Profile", "command": "ffmpeg", "user_agent": 4}
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 1. THE TWO HALVES, end to end through run_restore, TWO CYCLES EACH.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_deselected_upstream_is_named_on_no_cycle(tmp_path):
    """The FAITHFUL half. The operator excluded channel profiles; the memberships
    into them cannot exist, and the run says nothing about them, forever.

    Reads all three structures: the ``skip_details`` reason (classification), the
    top-level aggregate (the shortfall), and the outcome the aggregate feeds.
    """
    dest = _StatefulDest()
    reports = [
        await _cycle(_membership_plan(profiles_selected=False), dest, tmp_path)
        for _ in range(2)
    ]

    # The replica genuinely holds no membership — read off the destination.
    assert dest.memberships == []

    for cycle, report in enumerate(reports, start=1):
        # The profile ROW itself is EXCLUDED_BY_OPERATOR (its own importer's
        # verdict, unchanged); the MEMBERSHIP into it is the row this bead
        # reclassifies, so the assertion is on the dependency reasons only.
        assert _dependency_reasons(report, EntityType.CHANNEL_PROFILE) == [
            SkipReason.DEPENDENCY_DESELECTED
        ], cycle
        assert report.entities_blocked_by_dependency == 0, cycle
        assert report.delivery_shortfalls() == {}, cycle
        assert report.outcome == RestoreOutcome.SUCCESS, cycle
        message = DbasRestoreTask._summary_message(report, True)
        assert "depend" not in message.lower(), cycle


@pytest.mark.asyncio
async def test_a_dependency_that_was_in_scope_and_absent_is_named_on_every_cycle(
    tmp_path,
):
    """The GENUINE half. USER_AGENT was selected and the archive lost it, so a
    stream profile the operator asked for never reaches the replica.

    Zero category failures throughout — which is precisely why this was invisible.
    """
    dest = _StatefulDest()
    reports = [
        await _cycle(_degraded_user_agent_plan(), dest, tmp_path) for _ in range(2)
    ]

    # The replica genuinely never got the profile — read off the destination.
    assert dest.stream_profiles == []

    for cycle, report in enumerate(reports, start=1):
        assert _reasons(report, EntityType.STREAM_PROFILE) == [
            SkipReason.DEPENDENCY_UNRESOLVED
        ], cycle
        assert sum(c.failed for c in report.categories) == 0, cycle
        assert report.entities_blocked_by_dependency == 1, cycle
        assert report.delivery_shortfalls() == {"entities_blocked_by_dependency": 1}, cycle
        assert report.outcome == RestoreOutcome.COMPLETED_WITH_FAILURES, cycle
        message = DbasRestoreTask._summary_message(report, True)
        assert "1 archived item(s) were not restored" in message, cycle


@pytest.mark.asyncio
async def test_the_two_halves_are_distinguished_inside_one_run(tmp_path):
    """Both producers in ONE cycle: one clause, counting only the genuine half.

    A rule that fires per-run rather than per-skip would report 2 or 0 here.
    """
    plan = _membership_plan(profiles_selected=False)
    plan.categories.extend(_degraded_user_agent_plan().categories)
    dest = _StatefulDest()

    report = await _cycle(plan, dest, tmp_path)

    assert _dependency_reasons(report, EntityType.CHANNEL_PROFILE) == [
        SkipReason.DEPENDENCY_DESELECTED
    ]
    assert _dependency_reasons(report, EntityType.STREAM_PROFILE) == [
        SkipReason.DEPENDENCY_UNRESOLVED
    ]
    assert report.entities_blocked_by_dependency == 1
    assert report.outcome == RestoreOutcome.COMPLETED_WITH_FAILURES


@pytest.mark.asyncio
async def test_a_membership_whose_profile_was_in_scope_is_the_genuine_half(tmp_path):
    """The SAME producer, the other way. Selection is what separates them — not
    which line of code recorded the skip.

    The channel references profile 11 and the archive's SELECTED profile slice
    carries only profile 12, so the membership is lost from a category the
    operator asked for.
    """
    plan = _membership_plan(profiles_selected=True)
    plan.category(EntityType.CHANNEL_PROFILE).entities = [{"id": 12, "name": "Other"}]
    dest = _StatefulDest()

    reports = [await _cycle(plan, dest, tmp_path) for _ in range(2)]

    for cycle, report in enumerate(reports, start=1):
        assert (
            SkipReason.DEPENDENCY_UNRESOLVED
            in _reasons(report, EntityType.CHANNEL_PROFILE)
        ), cycle
        assert (
            SkipReason.DEPENDENCY_DESELECTED
            not in _reasons(report, EntityType.CHANNEL_PROFILE)
        ), cycle
        assert report.entities_blocked_by_dependency == 1, cycle
        assert report.outcome == RestoreOutcome.COMPLETED_WITH_FAILURES, cycle


# ---------------------------------------------------------------------------
# 2. THE RUN SCOPE — the state the classification reads, and its fail-loud default.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_restore_records_the_run_scope_on_the_shared_remap(tmp_path):
    """``run_restore`` is the single chokepoint that knows the plan, so it is the
    single place the scope is recorded. Without this the classifier has nothing
    to read and every skip stays a shortfall.
    """
    remap = IdRemapTable()
    assert remap.selected_categories is None

    await run_restore(
        plan=_membership_plan(profiles_selected=False),
        client=_StatefulDest().as_client(),
        steps=default_importer_steps(),
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=remap,
        confirm_apply=True,
        ledger_dir=tmp_path,
    )

    assert remap.selected_categories == {EntityType.CHANNEL}
    assert remap.category_deselected(EntityType.CHANNEL_PROFILE) is True
    assert remap.category_deselected(EntityType.CHANNEL) is False


def test_an_unrecorded_scope_never_claims_a_deselection():
    """FAIL LOUD. A remap that was never told the run's scope cannot prove any
    category was excluded, so it claims none — the defect being fixed is
    UNDER-reporting, and a default that silences is the same bug with a new name.
    """
    blank = IdRemapTable()
    for entity_type in EntityType:
        assert blank.category_deselected(entity_type) is False

    report = RestoreReport(is_dry_run=False)
    reason = report.record_dependency_unresolved(
        recorded_under=EntityType.CHANNEL_PROFILE,
        dependency=EntityType.CHANNEL_PROFILE,
        label="CNN",
        remap=blank,
        is_dry_run=False,
    )
    assert reason == SkipReason.DEPENDENCY_UNRESOLVED
    assert report.entities_blocked_by_dependency == 1


def test_a_category_absent_from_the_plan_counts_as_deselected():
    """``ImportPlan.category`` returns ``None`` for a category the plan never
    carried, and the orchestrator's own ``_selected`` already reads that as False.
    The scope record must agree, or a restore whose plan omits a category entirely
    would report every link into it as a loss.
    """
    remap = _scoped_remap(EntityType.CHANNEL)
    assert remap.category_deselected(EntityType.LOGO) is True


def test_the_recorder_writes_the_count_and_the_reason_together():
    """One recorder, so the aggregate and the classification cannot drift — the
    property ``…-posm1`` established for the shortfall set, applied one layer down.
    """
    report = RestoreReport(is_dry_run=False)
    deselected = _scoped_remap(EntityType.CHANNEL)

    faithful = report.record_dependency_unresolved(
        recorded_under=EntityType.CHANNEL_PROFILE,
        dependency=EntityType.CHANNEL_PROFILE,
        label="CNN",
        remap=deselected,
        is_dry_run=False,
    )
    assert faithful == SkipReason.DEPENDENCY_DESELECTED
    assert report.entities_blocked_by_dependency == 0
    assert report.category(EntityType.CHANNEL_PROFILE).skipped == 1

    genuine = report.record_dependency_unresolved(
        recorded_under=EntityType.CHANNEL,
        dependency=EntityType.CHANNEL_GROUP,
        label="BBC",
        remap=deselected,
        is_dry_run=False,
    )
    assert genuine == SkipReason.DEPENDENCY_UNRESOLVED
    assert report.entities_blocked_by_dependency == 1
    assert report.category(EntityType.CHANNEL).skipped == 1
    # A skip is not a failure — the shortfall never touches failure_details, and
    # ``FailureReason.DEPENDENCY_UNRESOLVED`` (the settings-key reuse, bead
    # …-zt3kf) is a different structure this bead leaves alone.
    assert all(not c.failure_details for c in report.categories)
    assert not any(
        isinstance(d.reason, FailureReason)
        for c in report.categories
        for d in c.skip_details
    )


def test_a_dry_run_counts_the_would_skip_not_the_skip():
    """The preview PREDICTS the loss honestly; refusing to downgrade it is the
    outcome decision's job, not the recorder's (``…-daziw``).
    """
    report = RestoreReport(is_dry_run=True)
    report.record_dependency_unresolved(
        recorded_under=EntityType.STREAM_PROFILE,
        dependency=EntityType.USER_AGENT,
        label="Proxy Profile",
        remap=_scoped_remap(EntityType.STREAM_PROFILE, EntityType.USER_AGENT),
        is_dry_run=True,
    )
    cat = report.category(EntityType.STREAM_PROFILE)
    assert (cat.would_skip, cat.skipped) == (1, 0)
    assert report.entities_blocked_by_dependency == 1
    assert report.delivery_shortfalls() == {"entities_blocked_by_dependency": 1}
    assert _outcome(report) == RestoreOutcome.SUCCESS


# ---------------------------------------------------------------------------
# 3. EVERY PRODUCER routes through the recorder — the "consistently across all
#    of them" half of the bead, asserted per producer at importer level.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_producer_channels_a_channel_is_a_loss_even_with_its_group_deselected():
    """``importers/channels.py`` — the CHANNEL skip.

    Deselecting ``channel_groups`` does NOT make a stranded CHANNEL faithful: the
    operator asked for channels. This is the conjunct that stops the rule
    degenerating into "the upstream was deselected".
    """
    from dbas.importers.channels import import_channels

    report = RestoreReport(is_dry_run=False)
    client = AsyncMock()
    client.get_channels = AsyncMock(return_value={"count": 0, "next": None, "results": []})
    client.get_channel_streams = AsyncMock(return_value=[])

    await import_channels(
        archive_channels=[{"id": 1, "name": "CNN", "channel_number": 5, "channel_group_id": 7}],
        client=client,
        selected=True,
        report=report,
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=_scoped_remap(EntityType.CHANNEL),
    )

    assert _reasons(report, EntityType.CHANNEL) == [SkipReason.DEPENDENCY_UNRESOLVED]
    assert report.entities_blocked_by_dependency == 1
    client.create_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_producer_epg_sources_a_source_is_a_loss_even_with_its_account_deselected():
    """``importers/epg_sources.py`` — the EPG_SOURCE skip, dependency M3U_ACCOUNT."""
    from dbas.importers.epg_sources import import_epg_sources

    report = RestoreReport(is_dry_run=False)
    client = AsyncMock()
    client.get_epg_sources = AsyncMock(return_value=[])

    await import_epg_sources(
        archive_sources=[
            {"id": 3, "name": "EPG One", "source_type": "xmltv", "m3u_account": 42}
        ],
        client=client,
        selected=True,
        report=report,
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=_scoped_remap(EntityType.EPG_SOURCE),
    )

    assert _reasons(report, EntityType.EPG_SOURCE) == [SkipReason.DEPENDENCY_UNRESOLVED]
    assert report.entities_blocked_by_dependency == 1


@pytest.mark.asyncio
async def test_producer_groups_profiles_a_profile_is_a_loss_even_with_its_agent_deselected():
    """``importers/groups_profiles.py`` — the STREAM_PROFILE skip, dep USER_AGENT."""
    from dbas.importers.groups_profiles import import_stream_profiles

    report = RestoreReport(is_dry_run=False)
    client = AsyncMock()
    client.get_stream_profiles = AsyncMock(return_value=[])

    await import_stream_profiles(
        archive_rows=[
            {"id": 9, "name": "Proxy Profile", "command": "ffmpeg", "user_agent": 4}
        ],
        client=client,
        selected=True,
        report=report,
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=_scoped_remap(EntityType.STREAM_PROFILE),
    )

    assert _reasons(report, EntityType.STREAM_PROFILE) == [
        SkipReason.DEPENDENCY_UNRESOLVED
    ]
    assert report.entities_blocked_by_dependency == 1


@pytest.mark.asyncio
async def test_producer_users_a_user_is_a_loss_even_with_its_profiles_deselected():
    """``importers/users.py`` — the USER skip, dependency CHANNEL_PROFILE.

    A user is a first-class entity, so its loss is reported even when the
    profiles that stranded it were excluded on purpose.
    """
    from dbas.importers.users import import_users

    report = RestoreReport(is_dry_run=False)
    client = AsyncMock()
    client.get_users = AsyncMock(return_value=[])
    client.get_current_user = AsyncMock(return_value={"id": 1, "username": "admin"})
    client.get_user_schema_write_fields = AsyncMock(
        return_value={
            "username", "email", "is_superuser", "is_staff", "user_level",
            "channel_profiles",
        }
    )

    await import_users(
        archive_users=[
            {
                "id": 6,
                "username": "viewer",
                "user_level": 0,
                "channel_profiles": [11],
            }
        ],
        client=client,
        selected=True,
        report=report,
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=_scoped_remap(EntityType.USER),
    )

    assert SkipReason.DEPENDENCY_UNRESOLVED in _reasons(report, EntityType.USER)
    assert report.entities_blocked_by_dependency == 1


# ---------------------------------------------------------------------------
# 3b. THE PAYLOAD BUILDERS name WHICH namespace failed.
#
# LAYER, stated because it matters: these are UNIT tests of two pure helpers,
# not engine tests, and they exist because the engine CANNOT see this. Both
# importers run only when their own category is selected, so
# ``recorded_under == dependency`` is false for them on every reachable path and
# the ``dependency`` value they pass changes no verdict today — a mutant that
# hardcodes it to the wrong namespace survives the entire engine-level suite
# (measured: mutant M12). What it does change is the log line, and the guard
# against a future producer whose recorded_under COULD equal its dependency. The
# builders are the only place that knows the answer, so the contract is pinned
# where it is observable.
# ---------------------------------------------------------------------------


def test_the_channel_payload_builder_names_the_namespace_it_could_not_resolve():
    from dbas.importers.channels import _build_create_payload

    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL_GROUP, 7, 700)

    payload, unresolved = _build_create_payload(
        {"id": 1, "name": "CNN", "channel_group_id": 7, "stream_profile_id": 9}, remap
    )
    assert payload is None
    assert unresolved == EntityType.STREAM_PROFILE

    payload, unresolved = _build_create_payload(
        {"id": 1, "name": "CNN", "channel_group_id": 7}, remap
    )
    assert unresolved is None
    assert payload["channel_group_id"] == 700


def test_the_groups_profiles_payload_builder_names_its_namespace():
    from dbas.importers.groups_profiles import (
        _CATEGORY_CONFIGS,
        _build_create_payload,
    )

    config = _CATEGORY_CONFIGS["stream_profiles"]
    payload, unresolved = _build_create_payload(
        {"id": 9, "name": "Proxy Profile", "user_agent": 4}, config, IdRemapTable()
    )
    assert payload is None
    assert unresolved == EntityType.USER_AGENT

    remap = IdRemapTable()
    remap.add(EntityType.USER_AGENT, 4, 400)
    payload, unresolved = _build_create_payload(
        {"id": 9, "name": "Proxy Profile", "user_agent": 4}, config, remap
    )
    assert unresolved is None
    assert payload["user_agent"] == 400


# ---------------------------------------------------------------------------
# 4. THE SET (…-posm1 / …-cwmid): a member, keyed on the OUTCOME alone.
# ---------------------------------------------------------------------------


def test_the_genuine_half_is_a_member_of_the_delivery_shortfall_set():
    """It passes every clause of the membership test on
    :data:`RestoreReport.DELIVERY_SHORTFALL_FIELDS`: a LOSS (an entity the source
    had and the replica does not), never a faithful absence (the deselected half
    never increments it), never something the run was asked NOT to carry, never
    work the run did — and CLEARABLE, which is what separates it from
    ``credentials_needing_reentry``: restore the dependency and the next cycle
    counts zero.
    """
    assert "entities_blocked_by_dependency" in RestoreReport.DELIVERY_SHORTFALL_FIELDS
    report = _apply(entities_blocked_by_dependency=1)
    assert report.delivery_shortfalls() == {"entities_blocked_by_dependency": 1}
    assert _outcome(report) == RestoreOutcome.COMPLETED_WITH_FAILURES


def test_the_new_member_adds_no_severity_mapping():
    """``…-cwmid``, as the property. Severity is read off the OUTCOME alone, so a
    new member cannot reorder anything — asserted rather than assumed.
    """
    outcome = _outcome(_apply(entities_blocked_by_dependency=12))
    assert outcome == RestoreOutcome.COMPLETED_WITH_FAILURES
    assert outcome.is_degraded_not_failed is True


def test_the_deselected_reason_is_never_counted_as_a_shortfall_by_any_route():
    """The boundary, at the aggregate level: a report full of faithful skips is
    an unqualified success. This is ``…-15g1j``'s ten-keystone-scenario guard
    stated locally, so a future widening trips here first.
    """
    report = RestoreReport(is_dry_run=False)
    remap = _scoped_remap(EntityType.CHANNEL)
    for label in ("CNN", "BBC", "ESPN"):
        report.record_dependency_unresolved(
            recorded_under=EntityType.CHANNEL_PROFILE,
            dependency=EntityType.CHANNEL_PROFILE,
            label=label,
            remap=remap,
            is_dry_run=False,
        )
    assert report.category(EntityType.CHANNEL_PROFILE).skipped == 3
    assert report.delivery_shortfalls() == {}
    assert _outcome(report) == RestoreOutcome.SUCCESS


def test_the_summary_renders_the_member_in_both_tenses():
    """A preview says WOULD (bead ``…-juu3c``); an apply says what happened."""
    report = _apply(entities_blocked_by_dependency=2)
    applied = DbasRestoreTask._credential_reentry_suffix(report, is_preview=False)
    predicted = DbasRestoreTask._credential_reentry_suffix(report, is_preview=True)
    assert "2 archived item(s) were not restored" in applied
    assert "2 archived item(s) would not be restored" in predicted
    assert DbasRestoreTask._credential_reentry_suffix(_apply()) == ""
