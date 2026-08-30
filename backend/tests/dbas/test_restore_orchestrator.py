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
from dbas.destination_read import DestinationReadError, ReadObservingClient

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
        "delete_epg_source",
        "delete_channel_group",
        "delete_channel_profile",
        "delete_stream_profile",
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


@pytest.mark.asyncio
async def test_orchestrator_rollback_opens_explicit_delete_compensation_scope(tmp_path):
    inner = _client()
    inner.get_m3u_accounts.side_effect = _http_error(503)
    report = _report()
    client = ReadObservingClient(inner, report, reject_mutations=True)
    with pytest.raises(DestinationReadError):
        await client.get_m3u_accounts()
    ledger = _ledger("explicit-compensation")
    ledger.record_created(EntityType.M3U_ACCOUNT, 901, "prov")

    result = await run_rollback(ledger=ledger, client=client, ledger_dir=tmp_path)

    assert result.complete is True
    inner.delete_m3u_account.assert_awaited_once_with(901)


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
# 9b. Per-create durable flush (bead l1p4p): an importer can flush the shared
# ledger to disk WITHIN a step (after each record_created, before the next
# create) via ctx.flush_ledger(), so a mid-category crash leaves a recoverable
# record — not only after a whole step completes.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_create_flush_persists_first_entry_before_second_create(tmp_path):
    import json as _json
    from dbas.restore_orchestrator import _ledger_path

    seen_on_disk = []  # ledger entry count observed on disk at each create point

    async def _two_create_step(ctx: ApplyContext):
        cat = ctx.report.category(EntityType.USER)
        for dest_id, name in ((201, "alice"), (202, "bob")):
            # Record what is ALREADY durable before issuing this create.
            path = _ledger_path(ctx.ledger.restore_id, tmp_path)
            if path.exists():
                seen_on_disk.append(len(_json.loads(path.read_text())["entries"]))
            else:
                seen_on_disk.append(0)
            cat.created += 1
            ctx.ledger.record_created(EntityType.USER, dest_id, name)
            ctx.flush_ledger()  # durable per-create flush
        return None

    ledger = _ledger("rid-percreate")
    await run_restore(
        plan=_plan(_cat(EntityType.USER, [{"id": 1, "username": "alice"}])),
        client=_client(),
        steps=[ImporterStep(EntityType.USER, _two_create_step)],
        report=_report(),
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    # Before alice's create: 0 entries durable. Before bob's create: alice (1)
    # is ALREADY flushed — the durability contract.
    assert seen_on_disk == [0, 1]


@pytest.mark.asyncio
async def test_per_create_flush_is_noop_on_dry_run(tmp_path):
    from dbas.restore_orchestrator import _ledger_path

    async def _flushing_step(ctx: ApplyContext):
        # On a dry-run nothing is created; flush must be a no-op that never
        # writes the ledger path.
        ctx.flush_ledger()
        ctx.report.category(EntityType.USER).would_create += 1
        return None

    report = RestoreReport(is_dry_run=True)
    ledger = _ledger("rid-dryrun")
    await run_restore(
        plan=_plan(_cat(EntityType.USER, [{"id": 1, "username": "alice"}])),
        client=_client(),
        steps=[ImporterStep(EntityType.USER, _flushing_step)],
        report=report,
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=False,
        ledger_dir=tmp_path,
    )
    assert not _ledger_path("rid-dryrun", tmp_path).exists()


# ---------------------------------------------------------------------------
# 6b. EPG_SOURCE + STREAM_PROFILE compensators (enhancedchannelmanager-v1uz9)
#
# Before v1uz9 the rollback dispatch had NO compensator for epg_source or
# stream_profile, so a late-step failure after those were created could only
# reach FAILED_ROLLBACK_INCOMPLETE (residue left on the destination). These
# tests pin the closed gap: a late failure now rolls back BOTH new types
# cleanly → PARTIAL_FAILED_ROLLED_BACK, and 404-on-already-deleted is still
# treated as success for the new compensators.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_late_failure_rolls_back_epg_source_complete(tmp_path):
    # M3U + EPG_SOURCE created, then a late channel step raises. The EPG source
    # MUST be compensated (delete_epg_source) → COMPLETE rollback, not residue.
    client = _client()
    plan = _plan(
        _cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]),
        _cat(EntityType.CHANNEL, [{"id": 1, "name": "ESPN"}]),
    )
    report = _report()
    ledger = _ledger()
    steps = [
        _creating_step(EntityType.M3U_ACCOUNT, 901),
        _creating_step(EntityType.EPG_SOURCE, 701),
        _raising_step(EntityType.CHANNEL),  # late boom
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
    client.delete_epg_source.assert_awaited_once_with(701)
    client.delete_m3u_account.assert_awaited_once_with(901)
    assert all(e.compensated for e in ledger.entries)


@pytest.mark.asyncio
async def test_late_failure_rolls_back_stream_profile_complete(tmp_path):
    # M3U + STREAM_PROFILE created, then a late channel step raises. The stream
    # profile MUST be compensated (delete_stream_profile) → COMPLETE rollback.
    client = _client()
    plan = _plan(
        _cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]),
        _cat(EntityType.CHANNEL, [{"id": 1, "name": "ESPN"}]),
    )
    report = _report()
    ledger = _ledger()
    steps = [
        _creating_step(EntityType.M3U_ACCOUNT, 901),
        _creating_step(EntityType.STREAM_PROFILE, 801),
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
    assert out.outcome == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    client.delete_stream_profile.assert_awaited_once_with(801)
    assert all(e.compensated for e in ledger.entries)


@pytest.mark.asyncio
async def test_rollback_404_counts_as_success_for_new_compensators(tmp_path):
    # Both new compensators 404 (already gone) — still a COMPLETE rollback.
    client = _client(
        delete_side_effects={
            "delete_epg_source": _http_error(404),
            "delete_stream_profile": _http_error(404),
        }
    )
    ledger = _ledger()
    ledger.record_created(EntityType.EPG_SOURCE, 701, "epg")
    ledger.record_created(EntityType.STREAM_PROFILE, 801, "sp")
    result = await run_rollback(ledger=ledger, client=client, ledger_dir=tmp_path)
    assert result.complete is True
    assert result.residue == []
    assert all(e.compensated for e in ledger.entries)


def test_delete_dispatch_registers_epg_and_stream_profile():
    from dbas.restore_orchestrator import _delete_dispatch

    dispatch = _delete_dispatch(_client())
    # The v1uz9 gap closure: both types now have a registered compensator.
    assert EntityType.EPG_SOURCE in dispatch
    assert EntityType.STREAM_PROFILE in dispatch


# ---------------------------------------------------------------------------
# 7. Never-success-on-mixed-state (compute_outcome, asserted directly)
# ---------------------------------------------------------------------------


def test_compute_outcome_never_success_on_mixed_state():
    # A report carrying a failure can NEVER yield SUCCESS. With no abort and no
    # rollback (``failure_occurred=False``) the honest state is
    # COMPLETED_WITH_FAILURES (y65si) — the run finished, some rows did not, and
    # nothing was undone. It is emphatically not SUCCESS.
    report = RestoreReport(is_dry_run=False)
    cat = report.category(EntityType.CHANNEL)
    cat.created = 3
    cat.failed = 1  # mixed state
    outcome = compute_outcome(report=report, failure_occurred=False, rollback=None)
    assert outcome != RestoreOutcome.SUCCESS
    assert outcome == RestoreOutcome.COMPLETED_WITH_FAILURES


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
    """kxcjf — the apply registry wires EVERY category; no seam rows remain.

    Before kxcjf only M3U/users/channels were wired: a confirmed apply silently
    no-opped EPG sources, groups/profiles, user agents, DVR rules, settings, and
    logos while the dry-run preview promised their counts. This pins the full
    wiring and the dependency order.
    """
    from dbas.restore_orchestrator import default_importer_steps

    steps = default_importer_steps()
    order = [s.entity_type for s in steps]
    # Hard dependency ordering. M3U leads every category that remaps an
    # ``m3u_account`` FK; USER_AGENT leads M3U because an account's own
    # ``user_agent`` FK remaps through that namespace (bead …-9h6cv), so this is
    # pinned as a RELATION rather than as index 0.
    assert order.index(EntityType.M3U_ACCOUNT) < order.index(EntityType.EPG_SOURCE)
    assert order.index(EntityType.USER_AGENT) < order.index(EntityType.M3U_ACCOUNT)
    assert order.index(EntityType.EPG_SOURCE) < order.index(EntityType.CHANNEL)
    assert order.index(EntityType.CHANNEL_GROUP) < order.index(EntityType.CHANNEL)
    assert order.index(EntityType.CHANNEL_PROFILE) < order.index(EntityType.CHANNEL)
    assert order.index(EntityType.STREAM_PROFILE) < order.index(EntityType.CHANNEL)
    assert order.index(EntityType.USER_AGENT) < order.index(EntityType.CHANNEL)
    # lvfwd — a stream profile's ``user_agent`` FK remaps through the USER_AGENT
    # namespace, so user agents MUST be restored first. Reversing these two
    # aborted the whole restore on a fresh Dispatcharr (400 "Invalid pk"). The
    # M3U account carries the same FK (…-9h6cv), pinned above.
    assert order.index(EntityType.USER_AGENT) < order.index(EntityType.STREAM_PROFILE)
    assert order.index(EntityType.SETTINGS) < order.index(EntityType.CHANNEL)
    assert order.index(EntityType.USER) < order.index(EntityType.CHANNEL)
    # A DVR rule's ``channel`` FK remaps through the CHANNEL namespace, so DVR
    # rules run AFTER channels; logos attach last.
    assert order.index(EntityType.DVR_RULE) > order.index(EntityType.CHANNEL)
    assert order[-1] == EntityType.LOGO
    # EVERY step is wired — no importer=None seam remains in the apply registry.
    seams = [s.entity_type for s in steps if s.importer is None]
    assert seams == [], "apply registry still carries seam rows: %s" % seams
    m3u_step = next(s for s in steps if s.entity_type == EntityType.M3U_ACCOUNT)
    assert m3u_step.defers is True
    # Plugins stay excluded (ADR-012 D10) — no plugins category exists at all.
    assert not any("plugin" in s.entity_type.value for s in steps)


def test_dry_run_and_apply_registries_cover_the_same_categories():
    """kxcjf parity bar: both registries cover the SAME category set, same order."""
    from dbas.restore_orchestrator import default_importer_steps, dry_run_importer_steps

    apply_order = [s.entity_type for s in default_importer_steps()]
    dry_order = [s.entity_type for s in dry_run_importer_steps()]
    assert apply_order == dry_order
    # And the dry-run registry is fully wired too (no seam rows).
    assert all(s.importer is not None for s in dry_run_importer_steps())
    # lvfwd — the FK ordering holds on the PREVIEW registry too, or the operator
    # previews a stream-profile count the apply cannot deliver.
    assert dry_order.index(EntityType.USER_AGENT) < dry_order.index(
        EntityType.STREAM_PROFILE
    )


def test_delete_dispatch_registers_all_ledgerable_types():
    """kxcjf — every ledgerable created-entity type has a rollback compensator.

    SETTINGS is deliberately absent (config, never ledgered, not compensatable —
    surfaced via a report note instead; see
    test_rollback_notes_settings_not_rolled_back).
    """
    from dbas.restore_orchestrator import _delete_dispatch

    dispatch = _delete_dispatch(_client())
    for entity_type in (
        EntityType.M3U_ACCOUNT,
        EntityType.EPG_SOURCE,
        EntityType.CHANNEL_GROUP,
        EntityType.CHANNEL_PROFILE,
        EntityType.STREAM_PROFILE,
        EntityType.CHANNEL,
        EntityType.STREAM,
        EntityType.USER,
        EntityType.USER_AGENT,
        EntityType.DVR_RULE,
        EntityType.LOGO,
    ):
        assert entity_type in dispatch, "no compensator for %s" % entity_type.value
    assert EntityType.SETTINGS not in dispatch


@pytest.mark.asyncio
async def test_late_failure_rolls_back_user_agent_dvr_rule_and_logo(tmp_path):
    # kxcjf — the three newly-compensable types are cleanly rolled back on a
    # late-step failure (COMPLETE rollback, not residue).
    client = _client()
    for name in ("delete_user_agent", "delete_dvr_rule", "delete_logo"):
        setattr(client, name, AsyncMock(return_value=None))
    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    report = _report()
    ledger = _ledger()
    steps = [
        _creating_step(EntityType.USER_AGENT, 601),
        _creating_step(EntityType.DVR_RULE, 602),
        _creating_step(EntityType.LOGO, 603),
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
    assert out.outcome == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    client.delete_user_agent.assert_awaited_once_with(601)
    client.delete_dvr_rule.assert_awaited_once_with(602)
    client.delete_logo.assert_awaited_once_with(603)
    assert all(e.compensated for e in ledger.entries)


@pytest.mark.asyncio
async def test_rollback_notes_settings_not_rolled_back(tmp_path):
    # kxcjf — settings are applied config, never ledgered, NOT compensatable.
    # When a rollback runs after settings were applied, the report must SAY the
    # settings remain applied rather than let "rollback completed" read as a
    # full undo.
    client = _client()

    async def _settings_step(ctx: ApplyContext):
        ctx.report.category(EntityType.SETTINGS).updated += 3
        return None

    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    report = _report()
    out = await run_restore(
        plan=plan,
        client=client,
        steps=[
            ImporterStep(EntityType.SETTINGS, _settings_step),
            _creating_step(EntityType.M3U_ACCOUNT, 901),
            _raising_step(EntityType.CHANNEL),
        ],
        report=report,
        ledger=_ledger(),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert out.outcome == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    assert any(
        "NOT rolled back" in note and "3 applied setting(s)" in note
        for note in out.notes
    )


@pytest.mark.asyncio
async def test_rollback_without_applied_settings_has_no_settings_note(tmp_path):
    client = _client()
    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    out = await run_restore(
        plan=plan,
        client=client,
        steps=[
            _creating_step(EntityType.M3U_ACCOUNT, 901),
            _raising_step(EntityType.CHANNEL),
        ],
        report=_report(),
        ledger=_ledger(),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert out.outcome == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    assert not any("setting" in note.lower() for note in out.notes)


@pytest.mark.asyncio
async def test_rollback_notes_settings_dependency_unresolved_retry_wont_help(tmp_path):
    # zt3kf (PO decision 2026-08-03, rollback policy): a settings-key
    # DEPENDENCY_UNRESOLVED failure aborts the WHOLE restore and triggers full
    # rollback exactly like any other failed category — no per-key
    # skip-with-warning. But because this specific reason means "the archive
    # references a settings key the destination does not have," a retry of
    # the SAME restore against the SAME destination will fail identically.
    # The operator-facing note must say so, so nobody burns a retry on it.
    client = _client()

    async def _settings_step(ctx: ApplyContext):
        cat = ctx.report.category(EntityType.SETTINGS)
        cat.failed += 1
        cat.failure_details.append(
            FailureDetail(
                reason=FailureReason.DEPENDENCY_UNRESOLVED,
                label="some_setting_key",
                message="setting key not found on destination",
            )
        )
        return None

    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    out = await run_restore(
        plan=plan,
        client=client,
        steps=[ImporterStep(EntityType.SETTINGS, _settings_step)],
        report=_report(),
        ledger=_ledger(),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert out.outcome == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    assert any(
        "retry" in note.lower() and ("will fail" in note.lower() or "cannot" in note.lower() or "won't" in note.lower() or "will not" in note.lower())
        for note in out.notes
    )
    # Points the operator at the actual remediation, not just "it failed".
    assert any(
        "category selection" in note.lower() or "destination" in note.lower()
        for note in out.notes
    )


@pytest.mark.asyncio
async def test_rollback_notes_other_category_dependency_unresolved_no_settings_retry_note(tmp_path):
    """The settings-key retry-guidance note is SETTINGS-specific — a FK-target
    DEPENDENCY_UNRESOLVED failure on an unrelated category (a different
    failure shape: an id, not a settings key) must not get the same wording."""
    client = _client()

    async def _failing_step(ctx: ApplyContext):
        cat = ctx.report.category(EntityType.CHANNEL_GROUP)
        cat.failed += 1
        cat.failure_details.append(
            FailureDetail(
                reason=FailureReason.DEPENDENCY_UNRESOLVED,
                label="some_group",
                message="FK target missing",
            )
        )
        return None

    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    out = await run_restore(
        plan=plan,
        client=client,
        steps=[ImporterStep(EntityType.CHANNEL_GROUP, _failing_step)],
        report=_report(),
        ledger=_ledger(),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert out.outcome == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    assert not any("settings key" in note.lower() for note in out.notes)


# ---------------------------------------------------------------------------
# 9d. NON-FATAL categories (bead enhancedchannelmanager-y65si)
#
# A dispatcharr_users row that upstream refuses must NOT cost the operator their
# channels, groups, profiles and settings. It is counted as a failure and the
# restore runs to completion instead of rolling the whole instance back.
# ---------------------------------------------------------------------------


def _user_failure_step():
    """A USER step that reports one per-row create failure (no raise)."""

    async def _importer(ctx: ApplyContext):
        cat = ctx.report.category(EntityType.USER)
        cat.failed += 1
        cat.failure_details.append(
            FailureDetail(
                reason=FailureReason.UPSTREAM_API_ERROR,
                label="drilladmin",
                message="User creation failed: 500 - Server Error (500)",
            )
        )
        return None

    return ImporterStep(EntityType.USER, _importer)


@pytest.mark.asyncio
async def test_user_category_failure_does_not_roll_back_the_restore(tmp_path):
    """y65si: a user-create failure is COUNTED but never fatal.

    The drill lost an entire restore — M3U account, EPG source, two channel
    groups, a channel profile — because one archived Dispatcharr user could not
    be created on a rebuilt instance. Everything created before AND after the
    failing user category must survive, and no compensating DELETE may fire.
    """
    client = _client()
    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    report = _report()
    ledger = _ledger()
    steps = [
        _creating_step(EntityType.M3U_ACCOUNT, 901),
        _user_failure_step(),
        _creating_step(EntityType.CHANNEL, 501),
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

    # The failure is VISIBLE and COUNTED, not swallowed.
    assert out.category(EntityType.USER).failed == 1
    assert out.category(EntityType.USER).failure_details[0].label == "drilladmin"
    # …and the restore ran to completion: the step AFTER users still applied.
    assert out.category(EntityType.CHANNEL).created == 1
    assert out.category(EntityType.M3U_ACCOUNT).created == 1
    # NOTHING was compensated — the ledger entries are intact and untouched.
    client.delete_m3u_account.assert_not_called()
    client.delete_channel.assert_not_called()
    assert [e.destination_id for e in ledger.entries] == [901, 501]
    assert all(not e.compensated for e in ledger.entries)
    # Honest outcome: completed, but NOT a clean success.
    assert out.outcome == RestoreOutcome.COMPLETED_WITH_FAILURES
    assert out.outcome != RestoreOutcome.SUCCESS
    assert not any("rollback" in note.lower() for note in out.notes)
    # The operator is told which category degraded and that nothing was undone.
    assert any("user" in note.lower() for note in out.notes)


@pytest.mark.asyncio
async def test_non_fatal_set_is_exactly_the_leaf_categories(tmp_path):
    """Guard: only the LEAF categories are non-fatal; the rest still roll back.

    Every member passes the same admission test — nothing else in the restore
    holds a hard FK into it, so a row that does not come back degrades only
    itself. Widening this set silently is the failure this guard exists to catch,
    so it is an equality assertion, not a membership one.

    ``UPCOMING_RECORDING`` joined it with bead ``…-ciabe``: a recording is
    referenced by nothing (a channel does not know its recordings, and a DVR
    rule finds its own by querying rather than by reference), and the refusal it
    most plausibly meets is a stale timestamp, which is a property of the
    archive's age — a retry cannot fix it and a rollback would cost the operator
    every other category for it. The reasoning per member is on the constant.

    THE DEFAULT IS THE ARCHIVE-RESTORE CONTRACT and it does not move. In
    particular it still carries the PO's 2026-08-03 ruling on bead ``…-zt3kf``:
    a settings-key ``DEPENDENCY_UNRESOLVED`` aborts the whole restore and rolls
    back. A per-run widening exists for cross-instance sync (see the test
    below), and it is a PARAMETER precisely so it cannot reach this default.
    """
    from dbas.restore_orchestrator import NON_FATAL_FAILURE_CATEGORIES

    assert NON_FATAL_FAILURE_CATEGORIES == frozenset(
        {EntityType.USER, EntityType.LOGO, EntityType.UPCOMING_RECORDING}
    )


@pytest.mark.asyncio
async def test_a_run_may_widen_the_non_fatal_set_without_touching_the_default(
    tmp_path,
):
    """``non_fatal_categories`` is per-run (bead ``…-10wnq``).

    Cross-instance sync widens it to include SETTINGS, because a replica that
    deletes every entity it just created over one unreadable
    ``GET /api/core/settings/`` is the ``…-d0agi`` trade in a category that is
    never ledgered and therefore cannot be rolled back anyway. The archive
    restore keeps the zt3kf ruling. Asserted by BEHAVIOUR — the created entity
    survives — rather than by reading the constant back, because the constant
    being right proves nothing about the branch that consumes it.
    """
    client = _client()

    async def _settings_step(ctx: ApplyContext):
        cat = ctx.report.category(EntityType.SETTINGS)
        cat.failed += 1
        cat.failure_details.append(
            FailureDetail(
                reason=FailureReason.DEPENDENCY_UNRESOLVED,
                label="some_setting_key",
                message="setting key not found on destination",
            )
        )
        return None

    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    out = await run_restore(
        plan=plan,
        client=client,
        steps=[
            _creating_step(EntityType.M3U_ACCOUNT, 901),
            ImporterStep(EntityType.SETTINGS, _settings_step),
        ],
        report=_report(),
        ledger=_ledger(),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
        non_fatal_categories=frozenset(
            {EntityType.USER, EntityType.LOGO, EntityType.SETTINGS}
        ),
    )
    # Counted and surfaced — nothing goes silent…
    assert out.category(EntityType.SETTINGS).failed == 1
    assert out.outcome != RestoreOutcome.SUCCESS
    # …but the account this run created was NOT deleted around it.
    assert out.outcome == RestoreOutcome.COMPLETED_WITH_FAILURES
    assert not any(c.args[0] == 901 for c in client.delete_m3u_account.call_args_list)


@pytest.mark.asyncio
async def test_non_user_category_failure_still_rolls_back(tmp_path):
    """The same failure shape on a load-bearing category is STILL fatal."""
    client = _client()
    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    ledger = _ledger()
    steps = [
        _creating_step(EntityType.M3U_ACCOUNT, 901),
        _reporting_failure_step(EntityType.CHANNEL_GROUP, 761),
        _creating_step(EntityType.CHANNEL, 501),
    ]
    out = await run_restore(
        plan=plan,
        client=client,
        steps=steps,
        report=_report(),
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert out.outcome == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    # The step AFTER the failing category never ran.
    assert out.category(EntityType.CHANNEL).created == 0
    client.delete_m3u_account.assert_awaited_once_with(901)


@pytest.mark.asyncio
async def test_user_step_that_RAISES_is_still_fatal(tmp_path):
    """Non-fatal covers a REPORTED per-row failure, not an importer that blew up.

    ``UsersCapabilityError`` (the fail-closed schema guard) surfaces as a raise;
    that is a "we cannot reason about this destination" signal, not one bad row,
    and must keep its rollback.
    """
    client = _client()
    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    out = await run_restore(
        plan=plan,
        client=client,
        steps=[
            _creating_step(EntityType.M3U_ACCOUNT, 901),
            _raising_step(EntityType.USER),
        ],
        report=_report(),
        ledger=_ledger(),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert out.outcome == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    client.delete_m3u_account.assert_awaited_once_with(901)


def test_compute_outcome_never_reports_success_on_a_non_fatal_failure():
    """Direct contract check: a counted failure forbids SUCCESS even when nothing
    was rolled back."""
    report = _report()
    report.category(EntityType.USER).failed = 1
    assert (
        compute_outcome(report=report, failure_occurred=False, rollback=None)
        == RestoreOutcome.COMPLETED_WITH_FAILURES
    )


def _logo_failure_step():
    """A LOGO step that reports one per-row failure and a counted logo miss.

    This is the exact shape the logos importer produces for a logo it cannot
    restore: a VALIDATION_ERROR row plus a logo_misses increment, which is what
    the importer's own comments describe as "an honest miss ... reported instead
    of silent".
    """

    async def _importer(ctx: ApplyContext):
        cat = ctx.report.category(EntityType.LOGO)
        cat.failed += 1
        cat.failure_details.append(
            FailureDetail(
                reason=FailureReason.VALIDATION_ERROR,
                label="Drill Uploaded Logo",
                message="unsafe or empty logo filename",
            )
        )
        ctx.report.logo_misses += 1
        return None

    return ImporterStep(EntityType.LOGO, _importer)


@pytest.mark.asyncio
async def test_logo_category_failure_does_not_roll_back_the_restore(tmp_path):
    """d0agi: one image that cannot be written must not destroy the restore.

    Drill run 2026-08-04-run2: a restore that had already created 44 entities
    reported ``partial_failed_rolled_back`` and compensated all 44 away because
    ONE logo failed. Logos run LAST in the hard ordering, so everything the
    rollback destroyed had already succeeded.
    """
    client = _client()
    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    report = _report()
    ledger = _ledger()
    steps = [
        _creating_step(EntityType.M3U_ACCOUNT, 901),
        _creating_step(EntityType.CHANNEL, 501),
        _logo_failure_step(),
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

    # The failure is VISIBLE, COUNTED, and named.
    assert out.category(EntityType.LOGO).failed == 1
    assert out.category(EntityType.LOGO).failure_details[0].label == "Drill Uploaded Logo"
    assert out.logo_misses == 1
    # Every other category survives; nothing was compensated.
    assert out.category(EntityType.M3U_ACCOUNT).created == 1
    assert out.category(EntityType.CHANNEL).created == 1
    client.delete_m3u_account.assert_not_called()
    client.delete_channel.assert_not_called()
    assert [e.destination_id for e in ledger.entries] == [901, 501]
    assert all(not e.compensated for e in ledger.entries)
    # Honest outcome: completed, but NOT a clean success, and no rollback note.
    assert out.outcome == RestoreOutcome.COMPLETED_WITH_FAILURES
    assert not any("rollback" in note.lower() for note in out.notes)
    assert any("logo" in note.lower() for note in out.notes)


# ---------------------------------------------------------------------------
# 10. EPG-download wait wiring (kxcjf — the 0i2vt.11 acceptance item)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_epg_apply_step_waits_for_created_sources(tmp_path):
    # The apply registry's EPG step polls the created sources' rows after the
    # import; a terminal row (status=success) means zero sleeping.
    from dbas.restore_orchestrator import _epg_step_with_download_wait

    async def _epg_import(ctx: ApplyContext):
        cat = ctx.report.category(EntityType.EPG_SOURCE)
        cat.created += 1
        ctx.ledger.record_created(EntityType.EPG_SOURCE, 701, "epg-1")
        return None

    client = _client()
    client.refresh_epg_source = AsyncMock(return_value={})
    client.get_epg_source = AsyncMock(
        return_value={"id": 701, "status": "success", "epg_count": 42}
    )
    report = _report()
    out = await run_restore(
        plan=_plan(_cat(EntityType.EPG_SOURCE, [{"id": 1, "name": "EPG"}])),
        client=client,
        steps=[ImporterStep(EntityType.EPG_SOURCE, _epg_step_with_download_wait(_epg_import))],
        report=report,
        ledger=_ledger(),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert out.outcome == RestoreOutcome.SUCCESS
    client.refresh_epg_source.assert_awaited_once_with(701)
    client.get_epg_source.assert_awaited_once_with(701)
    # Download completed — no incomplete-wait note.
    assert not any("did not finish" in n for n in out.notes)


@pytest.mark.asyncio
async def test_epg_apply_step_timeout_is_nonfatal_and_noted(tmp_path):
    # A source that never reaches a terminal state must NOT hang or fail the
    # restore — bounded polls, WARN note in the report, restore continues.
    from dbas.restore_orchestrator import _epg_step_with_download_wait
    from dbas.importers import epg_sources as epg_mod

    async def _epg_import(ctx: ApplyContext):
        ctx.report.category(EntityType.EPG_SOURCE).created += 1
        ctx.ledger.record_created(EntityType.EPG_SOURCE, 702, "epg-2")
        return None

    client = _client()
    client.refresh_epg_source = AsyncMock(return_value={})
    client.get_epg_source = AsyncMock(
        return_value={"id": 702, "status": "fetching", "epg_count": 0}
    )

    # Patch the wait's defaults down so the bounded timeout is instant in-test.
    orig_wait = epg_mod.wait_for_epg_downloads

    async def _fast_wait(**kwargs):
        async def _no_sleep(_seconds):
            return None

        kwargs.setdefault("sleep_fn", _no_sleep)
        kwargs.setdefault("max_polls", 3)
        return await orig_wait(**kwargs)

    from unittest.mock import patch

    with patch.object(epg_mod, "wait_for_epg_downloads", _fast_wait):
        out = await run_restore(
            plan=_plan(_cat(EntityType.EPG_SOURCE, [{"id": 1, "name": "EPG"}])),
            client=client,
            steps=[ImporterStep(EntityType.EPG_SOURCE, _epg_step_with_download_wait(_epg_import))],
            report=_report(),
            ledger=_ledger(),
            remap=IdRemapTable(),
            confirm_apply=True,
            ledger_dir=tmp_path,
        )
    assert out.outcome == RestoreOutcome.SUCCESS  # non-fatal
    assert any("did not finish" in n and "702" in n for n in out.notes)


@pytest.mark.asyncio
async def test_epg_wait_skipped_on_dry_run_and_when_nothing_created(tmp_path):
    # Dry-run: the wrapper is a pass-through — no trigger, no poll, no wait.
    from dbas.restore_orchestrator import _epg_step_with_download_wait

    async def _epg_import(ctx: ApplyContext):
        ctx.report.category(EntityType.EPG_SOURCE).would_create += 1
        return None

    client = _client()
    client.refresh_epg_source = AsyncMock(return_value={})
    client.get_epg_source = AsyncMock(return_value={})
    report = RestoreReport(is_dry_run=True)
    await run_restore(
        plan=_plan(_cat(EntityType.EPG_SOURCE, [{"id": 1, "name": "EPG"}])),
        client=client,
        steps=[ImporterStep(EntityType.EPG_SOURCE, _epg_step_with_download_wait(_epg_import))],
        report=report,
        ledger=_ledger(),
        remap=IdRemapTable(),
        confirm_apply=False,
        ledger_dir=tmp_path,
    )
    client.refresh_epg_source.assert_not_awaited()
    client.get_epg_source.assert_not_awaited()

    # Apply with zero CREATED sources (e.g. all already existed): no wait either.
    async def _epg_import_skip(ctx: ApplyContext):
        ctx.report.category(EntityType.EPG_SOURCE).skipped += 1
        return None

    client2 = _client()
    client2.refresh_epg_source = AsyncMock(return_value={})
    client2.get_epg_source = AsyncMock(return_value={})
    await run_restore(
        plan=_plan(_cat(EntityType.EPG_SOURCE, [{"id": 1, "name": "EPG"}])),
        client=client2,
        steps=[ImporterStep(EntityType.EPG_SOURCE, _epg_step_with_download_wait(_epg_import_skip))],
        report=_report(),
        ledger=_ledger(),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    client2.refresh_epg_source.assert_not_awaited()
    client2.get_epg_source.assert_not_awaited()


# ---------------------------------------------------------------------------
# Deferred-phase note accuracy (bead 7ipq2.2 — live validation finding)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deferred_note_reflects_what_the_apply_fn_actually_applied(tmp_path):
    """The 'deferred auto-sync applied' note must count what the apply fn
    RETURNED (per-account summaries), not what was queued. On the sync path the
    injected apply fn (``tasks.dbas_sync_engine._no_deferred_apply``, ADR-013
    S9) suppresses the deferred phase and returns ``[]`` — the live-validation
    run showed the report still claimed 'deferred auto-sync applied for 1
    account(s)', a false statement in an operator-facing audit surface."""

    def _creating_step(entity_type, dest_id, defers=None):
        async def _importer(ctx):
            cat = ctx.report.category(entity_type)
            cat.created += 1
            ctx.ledger.record_created(entity_type, dest_id, "x")
            return defers

        return ImporterStep(entity_type, _importer)

    async def _suppressing_apply(*, deferred, client):
        return []  # sync-path posture: drop the deferred settings on the floor

    plan = _plan(_cat(EntityType.M3U_ACCOUNT, [{"id": 1, "name": "Prov"}]))
    report = _report()
    out = await run_restore(
        plan=plan,
        client=_client(),
        steps=[
            _creating_step(
                EntityType.M3U_ACCOUNT,
                901,
                defers=[{"m3u_account_id": 901, "settings": {}}],
            )
        ],
        report=report,
        ledger=_ledger(),
        remap=IdRemapTable(),
        confirm_apply=True,
        deferred_apply_fn=_suppressing_apply,
        ledger_dir=tmp_path,
    )
    assert out.outcome == RestoreOutcome.SUCCESS
    assert not any("deferred auto-sync applied" in n for n in out.notes)

    # And an apply fn that DID apply reports the applied count.
    async def _applying_apply(*, deferred, client):
        return [{"m3u_account_id": e["m3u_account_id"]} for e in deferred]

    report2 = _report()
    out2 = await run_restore(
        plan=plan,
        client=_client(),
        steps=[
            _creating_step(
                EntityType.M3U_ACCOUNT,
                902,
                defers=[{"m3u_account_id": 902, "settings": {}}],
            )
        ],
        report=report2,
        ledger=_ledger(),
        remap=IdRemapTable(),
        confirm_apply=True,
        deferred_apply_fn=_applying_apply,
        ledger_dir=tmp_path,
    )
    assert any(
        "deferred auto-sync applied for 1 account(s)" in n for n in out2.notes
    )
