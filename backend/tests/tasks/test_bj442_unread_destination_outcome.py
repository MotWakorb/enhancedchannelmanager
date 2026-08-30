"""A cycle that never read its destination RECORDS that, not just says it
(bead ``…-bj442``).

THE DEFECT this file pins
-------------------------
Bead ``…-jqfxm`` made a run against an unreachable or unauthenticated
destination fail correctly *at the task layer*: ``_result_from_report`` reads
:attr:`RestoreReport.destination_unreadable`, overrides ``success=False`` and
replaces the counts in the operator's message. Every operator-facing surface
is right.

``report.outcome`` was never part of that decision, so it kept whatever
``compute_outcome`` derived from the counts — and the counts are CLEAN, because
every importer degrades a failed destination read to ``existing = []`` ("B is
empty") and then creates everything successfully. Measured on this tree at
``02c2a312`` with the readback gate passing and one later category read failing
(HTTP 503) on a confirmed APPLY::

    destination_unreadable = "get_m3u_accounts could not be read — the
                              destination returned a server error (HTTP 503)"
    report.outcome          = RestoreOutcome.SUCCESS
    sync_target.last_outcome    = "success"
    sync_target.last_full_sync_at = 2026-08-21T18:56:03Z   <-- "B is current"
    every category .failed  = 0

So the fix corrected what the operator is TOLD without correcting what the
system RECORDS. ``details.outcome`` (the task-history row an API or MCP
consumer reads), the ``sync_outbound`` journal row's ``result``, and the
persisted ``sync_targets.last_outcome`` / ``last_full_sync_at`` columns that
``GET /api/sync-targets`` publishes and ``ECMSyncStalledTargetDrift`` alerts on
all read off that one field.

THE INVARIANT (the specification; the 503 above is one example of it)
---------------------------------------------------------------------
Every surface reporting a cycle's outcome agrees. A realized cycle that could
not read the destination it describes records an outcome that no consumer can
read as success — and it records the SAME one everywhere, because ONE decision
(``dbas.restore_orchestrator.compute_outcome``) feeds all of them.

WHY IT IS A SIBLING OF THE DELIVERY-SHORTFALL SET, NOT A MEMBER OF IT
---------------------------------------------------------------------
:attr:`RestoreReport.DELIVERY_SHORTFALL_FIELDS` (bead ``…-posm1``) means "the
source had this and the replica does not" — a LOSS from a cycle that ran, whose
applied state is real, kept and reasonable-about. Every member resolves to
``COMPLETED_WITH_FAILURES``, which :attr:`RestoreOutcome.is_degraded_not_failed`
maps to a ``warning``.

An unread destination is not that. Nothing was lost; the cycle never read the
thing it describes, so it knows neither what B carries nor what it applied —
which is the definition ``FAILED_ROLLBACK_INCOMPLETE`` already carries
("indeterminate state"). Bead ``…-jqfxm`` deliberately treats it as an ERROR,
never a degraded warning an operator can opt out of. Making it a shortfall
member would therefore downgrade a hard failure into a warning, which is the
inverse of this bug — so it is evaluated BESIDE the set, resolving to its own
outcome.

THE ``…-cwmid`` PROPERTY IS PRESERVED. Severity is still read off the OUTCOME
alone; no condition is ever consulted for one. That is asserted directly, for
both conditions, in ``test_severity_is_still_read_off_the_outcome_alone``.

WHICH STRUCTURE THESE TESTS ASSERT ON
-------------------------------------
This subsystem records conditions three ways — ``skip_details``/``SkipReason``
and ``failure_details``/``FailureReason`` (both per-ENTITY records the
importers write) and top-level ``int`` aggregates on :class:`RestoreReport` —
and asserting on the wrong one yields a test that passes against broken code.
These tests assert on NONE of the three. They assert on:

* :attr:`RestoreReport.destination_unreadable` — the top-level ``str | None``
  marker whose PRESENCE is the "I never read B" fact (bead ``…-jqfxm``);
* :attr:`RestoreReport.outcome` — the ``RestoreOutcome | None`` enum field, the
  decision itself, which is what this bead moves; and
* the RECORDED surfaces that read off it — ``TaskResult.details["outcome"]``,
  ``TaskResult.completed_degraded`` / ``completion_notification_type``, the
  ``sync_outbound`` journal row's ``result``, and the persisted
  ``sync_targets.last_outcome`` / ``last_full_sync_at`` columns.

Asserting on ``TaskResult.success`` ALONE would prove nothing: bead ``…-jqfxm``
already made it ``False``, so such a test is green against the broken tree.
It is asserted here only as a companion to the recorded fields.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dbas.restore_contracts import EntityType, RestoreOutcome, RestoreReport
from dbas.restore_orchestrator import compute_outcome, outcome_for_unread_destination
from routers import backup as backup_mod
from task_scheduler import completion_notification_type
from tasks import dbas_sync_engine as engine
from tasks.dbas_sync import DbasSyncTask
from tests.tasks.test_dbas_sync_engine import (
    _empty_dest_client,
    _source_client,
    _sync_target,
)
from tests.tasks.test_dbas_sync_task import _make_target
# Re-exported so pytest resolves it as a fixture in THIS module (fixtures are
# module-scoped names, not globals).
from tests.tasks.test_dbas_sync_unplayable_alert import _wire_db  # noqa: F401


# ---------------------------------------------------------------------------
# Builders — the destination fails the way Dispatcharr really fails.
# ---------------------------------------------------------------------------


def _http_error(status: int) -> httpx.HTTPStatusError:
    """The exception ``DispatcharrClient`` really raises for an HTTP failure."""
    request = httpx.Request("GET", "http://dr-box.lan:9191/api/channels/m3u/accounts/")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


async def _run_cycle(target, dest, tmp_path, *, confirm_apply: bool):
    """One REAL engine cycle A -> B (real ``run_restore``, real importers)."""
    with patch.object(backup_mod, "get_client", return_value=_source_client()), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        return await engine.run_sync(
            target,
            confirm_apply=confirm_apply,
            session=MagicMock(),
            ledger_dir=tmp_path,
        )


def _dest_that_passes_the_gate_then_fails_a_read() -> AsyncMock:
    """B answers the readback gate's probe, then refuses the M3U category read.

    This is the ONLY shape that reaches ``compute_outcome`` at all: a
    destination that fails the gate aborts before ``run_restore`` and comes back
    with ``outcome=None``. The importers' ``except Exception: existing = []``
    fallback is what turns the refusal into a full would-create/create plan with
    zero failures.
    """
    dest = _empty_dest_client()
    dest.get_m3u_accounts = AsyncMock(side_effect=_http_error(503))
    return dest


def _delivery_shortfall_report() -> RestoreReport:
    """The ``…-posm1``/``…-daziw`` shape: a cycle that RAN and lost something.

    Clean per-category counts, a real applied state on B, and one channel left
    with no URL-bearing stream. The control that this bead does not flatten the
    shortfall verdict into the unread-destination one.
    """
    report = RestoreReport(is_dry_run=False)
    report.category(EntityType.CHANNEL).created = 32
    report.record_stream_reattach_needed(
        name="Drill KERA Dallas",
        channel_id=12,
        placeholder_streams=["ECM placeholder"],
        has_playable_stream=False,
    )
    report.outcome = compute_outcome(
        report=report, failure_occurred=False, rollback=None
    )
    return report


async def _run_sync_task(target_id: int, report, *, confirm_apply: bool = True):
    """Run the UNBOUND task against a patched ``run_sync`` returning ``report``."""
    from tasks import dbas_sync

    async def _fake_run_sync(sync_target, **_kw):
        return report

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        task = DbasSyncTask()
        task.update_config(
            {"sync_target_id": target_id, "confirm_apply": confirm_apply}
        )
        return await task.execute()


# ---------------------------------------------------------------------------
# 1. WHICH STRUCTURE CARRIES THE SIGNAL — the false-green trap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_unread_signal_is_a_marker_not_a_skip_or_failure_detail(tmp_path):
    """Nothing failed and nothing was skipped — that is the whole defect.

    Keying a test (or a fix) on ``cat.failed``, ``failure_details`` or
    ``skip_details`` would pass against the broken tree, because a run that
    could not read B produces exactly the shape of a run against an empty B.
    """
    target = _sync_target()

    report = await _run_cycle(
        target, _dest_that_passes_the_gate_then_fails_a_read(), tmp_path,
        confirm_apply=True,
    )

    assert report.destination_unreadable is not None
    assert "503" in report.destination_unreadable
    assert sum(cat.failed for cat in report.categories) == 0
    assert all(not cat.failure_details for cat in report.categories)
    assert all(not cat.skip_details for cat in report.categories)
    # Nor is it a delivery shortfall: nothing was LOST, the cycle never ran.
    assert report.delivery_shortfalls() == {}


# ---------------------------------------------------------------------------
# 2. THE DECISION — report.outcome itself, which is what this bead moves
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_apply_that_never_read_the_destination_records_no_success(tmp_path):
    """THE red assertion. Against ``02c2a312`` this reports
    ``RestoreOutcome.SUCCESS`` while the marker is set."""
    target = _sync_target()

    report = await _run_cycle(
        target, _dest_that_passes_the_gate_then_fails_a_read(), tmp_path,
        confirm_apply=True,
    )

    assert report.destination_unreadable is not None
    assert report.outcome is not RestoreOutcome.SUCCESS
    assert report.outcome is RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE


@pytest.mark.asyncio
async def test_the_persisted_target_state_never_says_b_is_current(tmp_path):
    """``sync_targets.last_outcome`` / ``last_full_sync_at`` — the columns
    ``GET /api/sync-targets`` publishes and ``ECMSyncStalledTargetDrift`` keys
    on. Against ``02c2a312`` these read ``"success"`` and a fresh timestamp for
    a cycle that never read B."""
    target = _sync_target()
    target.last_outcome = None
    target.last_full_sync_at = None

    report = await _run_cycle(
        target, _dest_that_passes_the_gate_then_fails_a_read(), tmp_path,
        confirm_apply=True,
    )

    assert target.last_outcome != "success"
    assert target.last_outcome == report.outcome.value
    assert target.last_full_sync_at is None


@pytest.mark.asyncio
async def test_the_journal_row_records_the_same_outcome(tmp_path):
    """The ``sync_outbound`` audit row is a RECORD, consulted later. It reads
    ``report.outcome`` directly, so it inherited the same false success."""
    target = _sync_target()

    with patch.object(engine.journal, "log_entry") as log_entry:
        report = await _run_cycle(
            target, _dest_that_passes_the_gate_then_fails_a_read(), tmp_path,
            confirm_apply=True,
        )

    rows = [
        call.kwargs for call in log_entry.call_args_list
        if call.kwargs.get("action_type") == "sync_run"
    ]
    assert rows, "the cycle must still leave a sync_outbound audit row"
    recorded = rows[-1]["after_value"]["result"]
    assert recorded != "success"
    assert recorded == report.outcome.value


# ---------------------------------------------------------------------------
# 3. THE RECORDED TASK ROW — details.outcome, what an API/MCP consumer reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_task_history_row_agrees_with_the_task_result(_wire_db, tmp_path):
    """``TaskResult.success`` was already correct (bead ``…-jqfxm``); the row
    beside it said ``success``. Both are asserted, and the row is the one that
    was broken."""
    session = _wire_db()
    target_id = _make_target(session).id
    session.close()

    report = await _run_cycle(
        _sync_target(), _dest_that_passes_the_gate_then_fails_a_read(), tmp_path,
        confirm_apply=True,
    )
    result = await _run_sync_task(target_id, report)

    assert result.details["outcome"] != "success"
    assert result.details["outcome"] == report.outcome.value
    assert result.details["sync_report"]["outcome"] == report.outcome.value
    assert result.success is False
    assert result.error == "SYNC_DESTINATION_UNREADABLE"


# ---------------------------------------------------------------------------
# 4. THE DISTINCTION — an error, never a degraded warning (…-jqfxm)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unread_destination_is_an_error_not_a_degraded_warning(
    _wire_db, tmp_path
):
    """The inverse-of-the-bug guard. Folding this into
    ``DELIVERY_SHORTFALL_FIELDS`` would resolve it to
    ``COMPLETED_WITH_FAILURES`` — a ``warning`` with a per-task opt-out — for a
    run that knows neither what B carries nor what it applied."""
    session = _wire_db()
    target_id = _make_target(session).id
    session.close()

    report = await _run_cycle(
        _sync_target(), _dest_that_passes_the_gate_then_fails_a_read(), tmp_path,
        confirm_apply=True,
    )
    result = await _run_sync_task(target_id, report)

    assert report.outcome.is_degraded_not_failed is False
    assert result.completed_degraded is False
    assert completion_notification_type(result) == "error"


@pytest.mark.asyncio
async def test_severity_is_still_read_off_the_outcome_alone(tmp_path):
    """The ``…-cwmid`` property, asserted as a property.

    Bead ``…-cwmid`` had to UNDO a narrower keying after a drill measured the
    severity ordering INVERTED. The guard against a repeat is that NO condition
    is ever consulted for a severity: every condition resolves to one outcome,
    and the severity reads off that outcome. Asserted for BOTH conditions this
    bead puts in ``compute_outcome``.
    """
    unread = await _run_cycle(
        _sync_target(), _dest_that_passes_the_gate_then_fails_a_read(), tmp_path,
        confirm_apply=True,
    )
    shortfall = _delivery_shortfall_report()

    for report in (unread, shortfall):
        assert DbasSyncTask._degraded_not_failed(report, False) is (
            report.outcome.is_degraded_not_failed
        ), report.outcome


@pytest.mark.asyncio
async def test_a_delivery_shortfall_is_still_degraded_not_flattened(_wire_db):
    """``…-posm1`` non-regression: a cycle that RAN and lost a playable stream
    still resolves to the degraded ``warning``, not to the unread-destination
    error. The two conditions stay distinct."""
    session = _wire_db()
    target_id = _make_target(session).id
    session.close()

    report = _delivery_shortfall_report()
    result = await _run_sync_task(target_id, report)

    assert report.destination_unreadable is None
    assert report.outcome is RestoreOutcome.COMPLETED_WITH_FAILURES
    assert result.details["outcome"] == "completed_with_failures"
    assert result.completed_degraded is True
    assert completion_notification_type(result) == "warning"


# ---------------------------------------------------------------------------
# 5. THE CONTROLS — no false-fail, and a preview is still a preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_readable_destination_still_records_a_clean_success(tmp_path):
    """The gate must not turn every apply red."""
    target = _sync_target()
    target.last_outcome = None
    target.last_full_sync_at = None

    report = await _run_cycle(
        target, _empty_dest_client(), tmp_path, confirm_apply=True,
    )

    assert report.destination_unreadable is None
    assert report.outcome is RestoreOutcome.SUCCESS
    assert target.last_outcome == "success"
    assert target.last_full_sync_at is not None


@pytest.mark.asyncio
async def test_a_preview_that_never_read_the_destination_keeps_a_null_outcome(
    tmp_path,
):
    """A DRY RUN has no realized outcome to record (the ``…-kxuj2`` contract:
    ``outcome`` is ``None`` on a preview), and nothing was applied to be
    indeterminate. The marker still makes the preview a failure at the task
    layer — that half is bead ``…-jqfxm``'s and is unchanged here."""
    report = await _run_cycle(
        _sync_target(), _dest_that_passes_the_gate_then_fails_a_read(), tmp_path,
        confirm_apply=False,
    )

    assert report.is_dry_run is True
    assert report.destination_unreadable is not None
    assert report.outcome is None


def test_the_rule_itself_answers_only_for_a_realized_unread_run():
    """The decision function's own contract, asserted DIRECTLY.

    Round-one mutation testing found the dry-run guard surviving every
    engine-level test: ``run_restore`` sets ``outcome = None`` on a preview
    before ``compute_outcome`` is consulted at all, so no cycle can reach the
    guard through the engine. It is still the rule any future caller reads, so
    it is pinned where it lives rather than left as an untested claim.
    """
    unread = "authentication to the destination was rejected (HTTP 401)"

    applied = RestoreReport(is_dry_run=False)
    applied.destination_unreadable = unread
    preview = RestoreReport(is_dry_run=True)
    preview.destination_unreadable = unread

    assert outcome_for_unread_destination(applied) is (
        RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE
    )
    # A prediction is not a failure, and nothing was applied to be indeterminate.
    assert outcome_for_unread_destination(preview) is None
    # And a run that DID read its destination is never touched by this rule.
    assert outcome_for_unread_destination(RestoreReport(is_dry_run=False)) is None
