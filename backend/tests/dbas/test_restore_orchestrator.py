"""Tests for the Phase-2 DBAS restore ORCHESTRATOR
(enhancedchannelmanager-0i2vt.18, part 2 of 2).

Scope under test:

1. Pre-flight FAIL → restore refused, ZERO mutation (no importer step called).
2. Pre-flight PASS → apply proceeds (importer steps run, in order).
3. Rollback: an importer fails mid-run → every prior creation gets a compensating
   DELETE, in compensation_order (reverse creation); outcome =
   PARTIAL_FAILED_ROLLED_BACK.
4. Rollback 404-as-success: a compensating DELETE that 404s counts as success.
5. Rollback INCOMPLETE: a non-404 delete error → outcome =
   FAILED_ROLLBACK_INCOMPLETE, residue surfaced, NOT reported success.
6. Happy path: all steps succeed → SUCCESS; deferred phase runs LAST.
7. Never-success-on-mixed-state invariant asserted directly (compute_outcome).
8. A step that REPORTS a failure (without raising) also rolls back.
9. Durable ledger: persisted during apply; removed on clean success.

The Dispatcharr client and the importer steps are mocked at module level; the
durable ledger writes to a tmp dir, never CONFIG_DIR.
"""

import httpx
import pytest
from unittest.mock import AsyncMock

from dbas.preflight import ImportPlan, PlanCategory
from dbas.restore_contracts import (
    EntityType,
    FailureDetail,
    FailureReason,
    IdRemapTable,
    RestoreOutcome,
    RestoreReport,
    RollbackLedger,
)
from dbas.restore_orchestrator import (
    ApplyContext,
    ImporterStep,
    RollbackResult,
    compute_outcome,
    run_restore,
    run_rollback,
)

_GOOD_MANIFEST = {"schema_version": 1}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plan(*categories, manifest=None):
    return ImportPlan(
        manifest=manifest if manifest is not None else dict(_GOOD_MANIFEST),
        categories=list(categories),
    )


def _cat(entity_type, entities, selected=True):
    return PlanCategory(entity_type=entity_type, entities=entities, selected=selected)


def _client(*, delete_side_effects=None):
    """An AsyncMock client with all the delete methods the rollback dispatches to.

    ``delete_side_effects`` maps a delete-method name -> side_effect (e.g. an
    exception) to simulate a 404 or a hard error during compensation.
    """
    client = AsyncMock()
    for name in (
        "delete_m3u_account",
        "delete_channel_group",
        "delete_channel_profile",
        "delete_channel",
        "delete_stream",
        "delete_user",
    ):
        setattr(client, name, AsyncMock(return_value=None))
    for name, eff in (delete_side_effects or {}).items():
        getattr(client, name).side_effect = eff
    return client


def _http_error(status_code):
    """Build an httpx.HTTPStatusError carrying a given status (mirrors raise_for_status)."""
    request = httpx.Request("DELETE", "http://dispatcharr/api/x/1/")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


def _report():
    return RestoreReport(is_dry_run=False)


def _ledger(restore_id="test-restore"):
    return RollbackLedger(restore_id=restore_id)


def _ctx(plan, client, report, ledger, remap=None):
    return ApplyContext(
        plan=plan,
        client=client,
        report=report,
        ledger=ledger,
        remap=remap or IdRemapTable(),
    )


def _creating_step(entity_type, dest_id, *, defers=None):
    """An importer step that records ONE creation into report + ledger and succeeds."""

    async def _importer(ctx: ApplyContext):
        cat = ctx.report.category(entity_type)
        cat.created += 1
        ctx.ledger.record_created(entity_type, dest_id, f"{entity_type.value}-{dest_id}")
        return defers

    return ImporterStep(entity_type, _importer)


def _raising_step(entity_type):
    """An importer step that raises mid-run (fault injection)."""

    async def _importer(ctx: ApplyContext):
        raise RuntimeError("simulated upstream 500 at category midpoint")

    return ImporterStep(entity_type, _importer)


def _reporting_failure_step(entity_type, dest_id):
    """A step that creates one entity, then REPORTS a failure without raising."""

    async def _importer(ctx: ApplyContext):
        cat = ctx.report.category(entity_type)
        cat.created += 1
        ctx.ledger.record_created(entity_type, dest_id, f"{entity_type.value}-{dest_id}")
        cat.failed += 1
        cat.failure_details.append(
            FailureDetail(
                reason=FailureReason.UPSTREAM_API_ERROR,
                label="boom",
                message="upstream rejected",
            )
        )
        return None

    return ImporterStep(entity_type, _importer)


# ---------------------------------------------------------------------------
# 1. Pre-flight FAIL → refused, ZERO mutation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_failure_refuses_with_zero_mutation(tmp_path):
    # Bad FK → pre-flight fails. The (would-be) importer must NEVER be called.
    called = {"n": 0}

    async def _importer(ctx):
        called["n"] += 1
        return None

    plan = _plan(
        _cat(EntityType.CHANNEL, [{"id": 1, "name": "ESPN", "channel_group_id": 999}]),
    )
    client = _client()
    report = _report()
    ledger = _ledger()
    out = await run_restore(
        plan=plan,
        client=client,
        steps=[ImporterStep(EntityType.CHANNEL, _importer)],
        report=report,
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert called["n"] == 0  # zero mutation
    assert out.outcome is None  # nothing applied → no realized outcome
    assert ledger.entries == []
    assert any("pre-flight refused" in note for note in out.notes)


# ---------------------------------------------------------------------------
# 2. Pre-flight PASS → apply proceeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_pass_apply_proceeds(tmp_path):
    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    report = _report()
    ledger = _ledger()
    out = await run_restore(
        plan=plan,
        client=_client(),
        steps=[_creating_step(EntityType.M3U_ACCOUNT, 901)],
        report=report,
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert out.outcome == RestoreOutcome.SUCCESS
    assert report.category(EntityType.M3U_ACCOUNT).created == 1


# ---------------------------------------------------------------------------
# 3. Rollback in compensation_order → PARTIAL_FAILED_ROLLED_BACK
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_midrun_rolls_back_in_compensation_order(tmp_path):
    # Two successful creating steps, then a raising step. Both prior creations
    # must be compensating-deleted, in reverse creation order.
    client = _client()
    plan = _plan(
        _cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]),
        _cat(EntityType.CHANNEL, [{"id": 1, "name": "ESPN"}]),
    )
    report = _report()
    ledger = _ledger()
    steps = [
        _creating_step(EntityType.M3U_ACCOUNT, 901),  # sequence 0
        _creating_step(EntityType.CHANNEL, 501),      # sequence 1
        _raising_step(EntityType.USER),               # boom
    ]
    out = await run_restore(
        plan=plan,
        client=client,
        steps=steps,
        report=report,
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert out.outcome == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    # Both deletes issued.
    client.delete_channel.assert_awaited_once_with(501)
    client.delete_m3u_account.assert_awaited_once_with(901)
    # Reverse creation order: channel (seq 1) compensated before m3u (seq 0).
    # Assert via the await ordering on the shared mock parent.
    call_order = [c for c in client.mock_calls if c[0] in ("delete_channel", "delete_m3u_account")]
    assert [c[0] for c in call_order] == ["delete_channel", "delete_m3u_account"]
    # Every ledger entry marked compensated.
    assert all(e.compensated for e in ledger.entries)


# ---------------------------------------------------------------------------
# 4. 404-as-success during rollback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_404_counts_as_success(tmp_path):
    # The channel delete 404s (already gone) — still a COMPLETE rollback.
    client = _client(delete_side_effects={"delete_channel": _http_error(404)})
    ledger = _ledger()
    ledger.record_created(EntityType.M3U_ACCOUNT, 901, "prov")
    ledger.record_created(EntityType.CHANNEL, 501, "espn")
    result = await run_rollback(ledger=ledger, client=client, ledger_dir=tmp_path)
    assert result.complete is True
    assert result.residue == []
    assert all(e.compensated for e in ledger.entries)


# ---------------------------------------------------------------------------
# 5. Non-404 delete error → INCOMPLETE, residue surfaced, NOT success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_non_404_is_incomplete(tmp_path):
    client = _client(delete_side_effects={"delete_channel": _http_error(500)})
    ledger = _ledger()
    ledger.record_created(EntityType.M3U_ACCOUNT, 901, "prov")
    ledger.record_created(EntityType.CHANNEL, 501, "espn")
    result = await run_rollback(ledger=ledger, client=client, ledger_dir=tmp_path)
    assert result.complete is False
    assert len(result.residue) == 1
    assert result.residue[0].entity_type == EntityType.CHANNEL
    # The residue entry is NOT marked compensated.
    chan_entry = next(e for e in ledger.entries if e.entity_type == EntityType.CHANNEL)
    assert chan_entry.compensated is False


@pytest.mark.asyncio
async def test_failure_with_non_404_rollback_error_outcome_incomplete(tmp_path):
    client = _client(delete_side_effects={"delete_m3u_account": _http_error(500)})
    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    report = _report()
    ledger = _ledger()
    steps = [
        _creating_step(EntityType.M3U_ACCOUNT, 901),
        _raising_step(EntityType.CHANNEL),
    ]
    out = await run_restore(
        plan=plan,
        client=client,
        steps=steps,
        report=report,
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert out.outcome == RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE
    assert out.outcome != RestoreOutcome.SUCCESS
    assert any("INCOMPLETE" in note for note in out.notes)


# ---------------------------------------------------------------------------
# 6. Happy path: SUCCESS; deferred phase runs LAST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_success_and_deferred_runs_last(tmp_path):
    order: list[str] = []

    async def _deferred_apply(*, deferred, client):
        order.append("deferred")
        return []

    def _ordered_creating_step(entity_type, dest_id, defers=None):
        async def _importer(ctx):
            order.append(entity_type.value)
            cat = ctx.report.category(entity_type)
            cat.created += 1
            ctx.ledger.record_created(entity_type, dest_id, f"{entity_type.value}-{dest_id}")
            return defers

        return ImporterStep(entity_type, _importer)

    plan = _plan(
        _cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]),
        _cat(EntityType.CHANNEL, [{"id": 1, "name": "ESPN"}]),
    )
    report = _report()
    ledger = _ledger()
    steps = [
        _ordered_creating_step(EntityType.M3U_ACCOUNT, 901, defers=[{"m3u_account_id": 901, "settings": {}}]),
        _ordered_creating_step(EntityType.CHANNEL, 501),
    ]
    out = await run_restore(
        plan=plan,
        client=_client(),
        steps=steps,
        report=report,
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=True,
        deferred_apply_fn=_deferred_apply,
        ledger_dir=tmp_path,
    )
    assert out.outcome == RestoreOutcome.SUCCESS
    # Deferred MUST be the last thing to run.
    assert order[-1] == "deferred"
    assert order == ["m3u_account", "channel", "deferred"]


@pytest.mark.asyncio
async def test_clean_success_removes_ledger_file(tmp_path):
    from dbas.restore_orchestrator import _ledger_path

    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    ledger = _ledger("rid-clean")
    await run_restore(
        plan=plan,
        client=_client(),
        steps=[_creating_step(EntityType.M3U_ACCOUNT, 901)],
        report=_report(),
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert not _ledger_path("rid-clean", tmp_path).exists()


@pytest.mark.asyncio
async def test_ledger_persisted_during_rollback(tmp_path):
    from dbas.restore_orchestrator import _ledger_path

    client = _client(delete_side_effects={"delete_channel": _http_error(500)})
    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    ledger = _ledger("rid-residue")
    steps = [
        _creating_step(EntityType.M3U_ACCOUNT, 901),
        _creating_step(EntityType.CHANNEL, 501),
        _raising_step(EntityType.USER),
    ]
    await run_restore(
        plan=plan,
        client=client,
        steps=steps,
        report=_report(),
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    # Rollback INCOMPLETE (channel 500) → ledger file retained with the residue.
    assert _ledger_path("rid-residue", tmp_path).exists()


# ---------------------------------------------------------------------------
# 7. Never-success-on-mixed-state (compute_outcome, asserted directly)
# ---------------------------------------------------------------------------


def test_compute_outcome_never_success_on_mixed_state():
    # A report carrying a failure can NEVER yield SUCCESS, even if the caller
    # somehow passes failure_occurred=False.
    report = RestoreReport(is_dry_run=False)
    cat = report.category(EntityType.CHANNEL)
    cat.created = 3
    cat.failed = 1  # mixed state
    outcome = compute_outcome(report=report, failure_occurred=False, rollback=None)
    assert outcome != RestoreOutcome.SUCCESS
    assert outcome == RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE


def test_compute_outcome_clean_is_success():
    report = RestoreReport(is_dry_run=False)
    report.category(EntityType.CHANNEL).created = 3
    outcome = compute_outcome(report=report, failure_occurred=False, rollback=None)
    assert outcome == RestoreOutcome.SUCCESS


def test_compute_outcome_complete_rollback_is_partial():
    report = RestoreReport(is_dry_run=False)
    report.category(EntityType.CHANNEL).failed = 1
    rb = RollbackResult(complete=True, compensated=[], residue=[])
    outcome = compute_outcome(report=report, failure_occurred=True, rollback=rb)
    assert outcome == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK


def test_compute_outcome_incomplete_rollback_is_incomplete():
    report = RestoreReport(is_dry_run=False)
    report.category(EntityType.CHANNEL).failed = 1
    rb = RollbackResult(complete=False, compensated=[], residue=[object()])  # type: ignore[list-item]
    outcome = compute_outcome(report=report, failure_occurred=True, rollback=rb)
    assert outcome == RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE


# ---------------------------------------------------------------------------
# 8. A reported failure (no raise) also triggers rollback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reported_failure_triggers_rollback(tmp_path):
    client = _client()
    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    report = _report()
    ledger = _ledger()
    steps = [_reporting_failure_step(EntityType.M3U_ACCOUNT, 901)]
    out = await run_restore(
        plan=plan,
        client=client,
        steps=steps,
        report=report,
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert out.outcome == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    client.delete_m3u_account.assert_awaited_once_with(901)


# ---------------------------------------------------------------------------
# 9. Seam steps are no-ops (default registry wiring)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seam_step_is_noop(tmp_path):
    # A step with importer=None must be skipped without error and without mutation.
    plan = _plan(_cat(EntityType.CHANNEL_GROUP, [{"id": 10, "name": "Sports"}]))
    report = _report()
    ledger = _ledger()
    out = await run_restore(
        plan=plan,
        client=_client(),
        steps=[ImporterStep(EntityType.CHANNEL_GROUP, None)],
        report=report,
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert out.outcome == RestoreOutcome.SUCCESS
    assert ledger.entries == []


def test_default_importer_steps_order_and_wiring():
    from dbas.restore_orchestrator import default_importer_steps

    steps = default_importer_steps()
    order = [s.entity_type for s in steps]
    # Hard ordering: M3U first, channels last; channel groups/profiles before
    # channels; users before channels.
    assert order[0] == EntityType.M3U_ACCOUNT
    assert order[-1] == EntityType.CHANNEL
    assert order.index(EntityType.CHANNEL_GROUP) < order.index(EntityType.CHANNEL)
    assert order.index(EntityType.USER) < order.index(EntityType.CHANNEL)
    # M3U is wired and defers; groups/profiles/user-agents are seams.
    wired = {s.entity_type for s in steps if s.importer is not None}
    seams = {s.entity_type for s in steps if s.importer is None}
    assert {EntityType.M3U_ACCOUNT, EntityType.USER, EntityType.CHANNEL} <= wired
    assert {EntityType.CHANNEL_GROUP, EntityType.CHANNEL_PROFILE, EntityType.STREAM_PROFILE, EntityType.USER_AGENT} <= seams
    m3u_step = next(s for s in steps if s.entity_type == EntityType.M3U_ACCOUNT)
    assert m3u_step.defers is True
