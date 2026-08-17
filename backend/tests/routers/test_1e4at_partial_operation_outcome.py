"""An operation with two upstream side effects can land the first and fail the second.

Bead ``enhancedchannelmanager-1e4at``. The bulk-commit envelope carried exactly
one outcome per operation, applied or failed. ``deleteChannelGroup`` reparents
the group's member channels and then deletes the now-empty group; when the
reparent lands and the delete fails, the operation was reported as FAILED. That
is the safer of the two available lies — marking it applied would say the group
is gone when it is not — but the channels really did move and they stay moved.
An operator retrying the "failed" delete finds them already moved, and an MCP
agent acting on the envelope has no way to learn that a reparent landed.

THE CHOICE, stated as a decision rather than left to be inferred: a
partial-outcome CATEGORY, not decomposition of the operation into two reported
operations. Decomposition would break the identity between the operations the
caller SUBMITTED and the operations the envelope REPORTS, and that identity is
what rule 1 of the accounting invariant is written on
(``operationsApplied + operationsFailed == len(operations)``) and what
``errors[].operationId`` correlates against. A caller who staged five things
would read six outcomes.

The category is DERIVED, not declared. The ledger already sees the fact: a
``record_write`` inside an open operation is by definition an upstream write
that is not that operation's own outcome (that is what ``record_persisted`` is
for). An operation that closes as failed having recorded one is partially
applied, and no branch has to remember to say so — the same "cannot be got
wrong by forgetting" property the rest of this module was built for.

The invariants pinned here, as properties rather than as the reproduction:

1. An operation that failed having landed upstream writes is reported as such,
   on every branch, whatever the writes were.
2. ``operationsApplied + operationsFailed`` still equals the attempted count.
   A partially-applied operation is counted in ``operationsFailed`` — its own
   outcome did not happen — and named additionally, never instead.
3. The audit still RAISES on a genuinely inconsistent envelope, including on
   the new field.
4. It is not expressible to close an operation as applied without either
   recording a write or saying in words that it wrote nothing. (Bead
   ``enhancedchannelmanager-jd3kn``'s "second, smaller hole".)
"""
import asyncio as _asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bulk_commit_accounting import (
    BulkCommitAccountingError,
    OperationLedger,
    bulk_commit_accounting_violations,
    finalize_bulk_commit_result,
)


async def _commit_and_wait(async_client, body, *, max_polls=200):
    response = await async_client.post("/api/channels/bulk-commit", json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    for _ in range(max_polls):
        await _asyncio.sleep(0)
        poll = await async_client.get(f"/api/channels/bulk-commit/{job_id}")
        assert poll.status_code == 200, poll.text
        payload = poll.json()
        if payload["status"] == "completed":
            return payload["result"]
        if payload["status"] == "failed":
            raise AssertionError(f"bulk-commit job {job_id} failed: {payload}")
    raise AssertionError(f"bulk-commit job {job_id} did not terminate")


def _journal_double():
    double = MagicMock()
    double.log_entries.return_value = True
    double.log_entry.return_value = MagicMock()
    double.get_request_batch_id.return_value = "batch-1e4at"
    return double


def _base_client():
    client = AsyncMock()
    client.get_channels.return_value = {"results": [], "count": 0, "next": None}
    client.get_streams_by_ids.return_value = []
    client.get_channel_profiles.return_value = [{"id": 3, "name": "Kids"}]
    client.get_channel_groups.return_value = [{"id": 1, "name": "Default Group"}]
    client.get_logos.return_value = {"results": [], "next": None}
    return client


def _group_client():
    """Group 42 holds channels 10 and 11; 'Default Group' (1) is the target."""
    client = _base_client()
    client.get_channels.return_value = {
        "results": [
            {"id": 10, "name": "Ten", "channel_group_id": 42},
            {"id": 11, "name": "Eleven", "channel_group_id": 42},
        ],
        "count": 2,
        "next": None,
    }

    async def _get_channel(channel_id):
        return {"id": channel_id, "channel_group_id": 42}

    client.get_channel.side_effect = _get_channel
    return client


@pytest.fixture(autouse=True)
def _clear_jobs():
    from routers import channels as router_module

    router_module._BULK_COMMIT_JOBS.clear()
    yield
    router_module._BULK_COMMIT_JOBS.clear()


# --------------------------------------------------------------------------
# The reproduction, and the property it is an example of
# --------------------------------------------------------------------------

class TestAFailedOperationThatLandedWritesSaysSo:

    @pytest.mark.asyncio
    async def test_a_reparent_that_landed_before_a_failed_delete_is_reported(
        self, async_client
    ):
        """The bead's reproduction: both channels move, the delete fails.

        Reported as failed — the group still exists — AND named as partially
        applied, because two channels moved and stay moved. Reporting only the
        failure is what sends an operator to retry a delete whose visible
        precondition has already silently changed.
        """
        client = _group_client()
        client.update_channel.return_value = None
        client.delete_channel_group.side_effect = RuntimeError(
            "400 Cannot delete group with associated channels"
        )
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            data = await _commit_and_wait(async_client, {
                "operations": [{"type": "deleteChannelGroup", "groupId": 42}],
                "continueOnError": True,
            })

        # The operation's own outcome did not happen.
        assert data["operationsFailed"] == 1
        assert data["operationsApplied"] == 0
        # …and two upstream writes did.
        assert client.update_channel.await_count == 2
        assert data["operationsPartiallyApplied"] == 1
        assert data["errors"][0]["sideEffectsLanded"] is True
        # `partial` is the flag that tells a caller to reconcile rather than
        # blindly retry, and this is exactly that situation.
        assert data["partial"] is True
        assert data["success"] is False

    @pytest.mark.asyncio
    async def test_a_reparent_that_failed_partway_is_partially_applied_too(
        self, async_client
    ):
        """Channel 10 moves, channel 11 raises. Same category, earlier stop."""
        client = _group_client()
        client.update_channel.side_effect = [
            None,
            RuntimeError("500 Server Error from Dispatcharr"),
        ]
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            data = await _commit_and_wait(async_client, {
                "operations": [{"type": "deleteChannelGroup", "groupId": 42}],
                "continueOnError": True,
            })

        assert data["operationsFailed"] == 1
        assert data["operationsPartiallyApplied"] == 1
        assert data["errors"][0]["sideEffectsLanded"] is True

    @pytest.mark.asyncio
    async def test_a_failure_with_no_landed_write_is_not_partially_applied(
        self, async_client
    ):
        """The category must be able to be ABSENT while a failure is present.

        A group whose FIRST reparent raises has moved nothing, so nothing may
        suggest the operator has anything to reconcile. Without this the new
        field would be satisfied by every failure and would carry no
        information.
        """
        client = _group_client()
        client.update_channel.side_effect = RuntimeError("500 Server Error")
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            data = await _commit_and_wait(async_client, {
                "operations": [{"type": "deleteChannelGroup", "groupId": 42}],
                "continueOnError": True,
            })

        assert data["operationsFailed"] == 1
        assert data["operationsPartiallyApplied"] == 0
        assert "sideEffectsLanded" not in data["errors"][0]
        assert data["partial"] is False

    @pytest.mark.asyncio
    async def test_a_clean_run_reports_the_field_as_zero_not_absent(
        self, async_client
    ):
        """Always present, so a caller checks the number rather than probing.

        Same contract as ``journalRowsUnwritten`` and
        ``normalizationFailures``: a key that appears only on the bad path puts
        the burden back on the caller to know which shape they are holding.
        """
        client = _group_client()
        client.update_channel.return_value = None
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            data = await _commit_and_wait(async_client, {
                "operations": [{"type": "deleteChannelGroup", "groupId": 42}],
            })

        assert data["success"] is True
        assert data["operationsPartiallyApplied"] == 0


# --------------------------------------------------------------------------
# The ledger, at the chokepoint
# --------------------------------------------------------------------------

class TestTheLedgerDerivesTheCategory:
    """PIN. No branch declares "partially applied"; the ledger observes it."""

    def test_a_write_then_a_failure_is_a_partial_application(self):
        ledger = OperationLedger(1)
        ledger.begin()
        ledger.record_write(journal_row={"action_type": "update", "entity_id": 10})
        ledger.record_failed()
        assert ledger.failed == 1
        assert ledger.partially_applied == 1
        # Counted in `failed`, never instead of it: the operation's own
        # outcome did not happen.
        assert ledger.applied == 0
        assert ledger.attempted == 1

    def test_a_failure_with_no_write_is_not_a_partial_application(self):
        ledger = OperationLedger(1)
        ledger.begin()
        ledger.record_failed()
        assert ledger.partially_applied == 0

    def test_side_effects_do_not_leak_between_operations(self):
        """The counter resets with the operation, or the next failure inherits it."""
        ledger = OperationLedger(2)
        ledger.begin()
        ledger.record_write(journal_row={"entity_id": 10})
        ledger.record_failed()
        ledger.begin()
        ledger.record_failed()
        assert ledger.partially_applied == 1

    def test_a_write_with_nothing_to_journal_still_counts_as_a_side_effect(self):
        """The write LANDED. Whether it left a readable row is a separate fact.

        ``nothing_to_journal`` says "this write has no row", not "there was no
        write" — reading it as the latter would let a landed mutation go
        unreported to the caller for want of anything to print.
        """
        from bulk_commit_accounting import nothing_to_journal

        ledger = OperationLedger(1)
        ledger.begin()
        ledger.record_write(
            journal_row=nothing_to_journal("the channel was already gone upstream")
        )
        ledger.record_failed()
        assert ledger.partially_applied == 1

    def test_the_open_operation_reports_its_side_effects_before_it_closes(self):
        """The executor needs the fact while building the error entry."""
        ledger = OperationLedger(1)
        ledger.begin()
        assert ledger.side_effects_landed is False
        ledger.record_write(journal_row={"entity_id": 10})
        assert ledger.side_effects_landed is True


# --------------------------------------------------------------------------
# The audit still raises
# --------------------------------------------------------------------------

class TestTheAuditStillRaisesOnAnInconsistentEnvelope:

    def _envelope(self, **overrides):
        envelope = {
            "operationsApplied": 0,
            "operationsFailed": 1,
            "operationsPartiallyApplied": 1,
            "errors": [{"operationId": 0, "error": "boom", "sideEffectsLanded": True}],
            "success": False,
            "partial": True,
            "normalizationFailures": [],
        }
        envelope.update(overrides)
        return envelope

    def _violations(self, envelope, **kwargs):
        params = {
            "total_operations": 1,
            "aborted": False,
            "applied_create_temp_ids": set(),
            "partially_applied": envelope.get("operationsPartiallyApplied", 0),
        }
        params.update(kwargs)
        return bulk_commit_accounting_violations(envelope, **params)

    def test_a_consistent_envelope_has_no_violations(self):
        assert self._violations(self._envelope()) == []

    def test_more_partially_applied_than_failed_is_a_violation(self):
        """A partially-applied operation is one of the failures, so it cannot
        outnumber them."""
        envelope = self._envelope(operationsPartiallyApplied=2)
        violations = self._violations(envelope, partially_applied=2)
        assert violations, "an impossible count must be reported"
        assert any("partially applied" in v.lower() for v in violations)

    def test_a_partial_count_with_no_marked_error_entry_is_a_violation(self):
        """Every partially-applied operation names itself in ``errors``.

        Without this the count could be right while nothing in the envelope
        told the operator WHICH operation left work behind, which is the whole
        value of the category.
        """
        envelope = self._envelope(
            errors=[{"operationId": 0, "error": "boom"}],
        )
        violations = self._violations(envelope)
        assert violations
        assert any("sideEffectsLanded" in v for v in violations)

    def test_partial_must_be_true_when_something_landed_and_the_op_failed(self):
        envelope = self._envelope(partial=False)
        violations = self._violations(envelope)
        assert violations
        assert any("partial is" in v for v in violations)

    def test_finalize_raises_rather_than_logging(self):
        """PIN. A contradictory envelope must not be able to leave looking normal."""
        ledger = OperationLedger(1)
        ledger.begin()
        ledger.record_write(journal_row={"entity_id": 10})
        ledger.record_failed()
        result = {"errors": [], "normalizationFailures": []}
        with pytest.raises(BulkCommitAccountingError):
            finalize_bulk_commit_result(result, ledger)

    def test_finalize_accepts_the_envelope_the_executor_actually_builds(self):
        ledger = OperationLedger(1)
        ledger.begin()
        ledger.record_write(journal_row={"entity_id": 10})
        ledger.record_failed()
        result = {
            "errors": [{"operationId": 0, "error": "boom", "sideEffectsLanded": True}],
            "normalizationFailures": [],
        }
        finalize_bulk_commit_result(result, ledger)
        assert result["operationsPartiallyApplied"] == 1
        assert result["partial"] is True
        assert result["success"] is False


# --------------------------------------------------------------------------
# jd3kn's "second, smaller hole": applied without saying anything at all
# --------------------------------------------------------------------------

class TestApplyingWithoutWritingIsSaidOutLoud:
    """``record_applied`` used to accept an operation that told the ledger nothing.

    A branch that wrote upstream and forgot to say so was therefore
    expressible, with every round-3 mechanism intact — the required
    ``journal_row`` argument only binds a call that is actually made. Three
    branches legitimately apply WITHOUT writing (``createGroup`` on a name that
    already exists, add-stream and remove-stream when the stream is already in
    the desired state), so the fix is a second sentinel, not a bare
    requirement: applied-without-writing has to be distinguishable from
    applied-and-forgot-to-say.
    """

    def test_record_applied_without_a_write_or_a_reason_raises(self):
        ledger = OperationLedger(1)
        ledger.begin()
        with pytest.raises(BulkCommitAccountingError) as excinfo:
            ledger.record_applied()
        assert "wrote nothing" in str(excinfo.value)

    def test_a_persisted_write_closes_the_operation_normally(self):
        ledger = OperationLedger(1)
        ledger.begin()
        ledger.record_persisted(journal_row={"entity_id": 7})
        ledger.record_applied()
        assert ledger.applied == 1

    def test_applied_without_writing_needs_a_reason(self):
        ledger = OperationLedger(1)
        ledger.begin()
        with pytest.raises(BulkCommitAccountingError):
            ledger.applied_without_writing("")

    def test_applied_without_writing_lets_the_operation_close(self):
        ledger = OperationLedger(1)
        ledger.begin()
        ledger.applied_without_writing("the stream was already on the channel")
        ledger.record_applied()
        assert ledger.applied == 1

    def test_the_declaration_does_not_leak_into_the_next_operation(self):
        ledger = OperationLedger(2)
        ledger.begin()
        ledger.applied_without_writing("nothing to do")
        ledger.record_applied()
        ledger.begin()
        with pytest.raises(BulkCommitAccountingError):
            ledger.record_applied()

    def test_the_declaration_is_part_of_the_ledger_api(self):
        """PIN on the shape.

        A statement about the OPEN operation, so a method rather than a value
        passed in like ``nothing_to_journal`` — there is no required argument
        on ``record_applied`` for a sentinel to occupy. If a future edit gives
        the reason a default, this fails, which is the point: three of the
        findings this module exists for were "somebody forgot to say".
        """
        import inspect

        signature = inspect.signature(OperationLedger.applied_without_writing)
        assert signature.parameters["reason"].default is inspect.Parameter.empty


class TestTheThreeLegitimateNoWriteBranchesStillApply:
    """The sentinel must not turn a correct no-op into a failure.

    Each of these operations is genuinely applied — the requested end state
    holds — and genuinely wrote nothing upstream.
    """

    def _channel_client(self, streams):
        client = _base_client()
        channel = {"id": 5, "name": "Five", "streams": list(streams)}
        client.get_channels.return_value = {
            "results": [channel], "count": 1, "next": None,
        }
        client.get_channel.return_value = dict(channel)
        client.get_streams_by_ids.return_value = [{"id": 99, "name": "Stream 99"}]
        return client

    @pytest.mark.asyncio
    async def test_add_stream_that_is_already_on_the_channel(self, async_client):
        client = self._channel_client([99])
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            data = await _commit_and_wait(async_client, {
                "operations": [
                    {"type": "addStreamToChannel", "channelId": 5, "streamId": 99}
                ],
            })

        assert data["success"] is True
        assert data["operationsApplied"] == 1
        client.update_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_remove_stream_that_is_not_on_the_channel(self, async_client):
        client = self._channel_client([])
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            data = await _commit_and_wait(async_client, {
                "operations": [
                    {"type": "removeStreamFromChannel", "channelId": 5, "streamId": 99}
                ],
            })

        assert data["success"] is True
        assert data["operationsApplied"] == 1
        client.update_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_group_whose_name_already_resolved(self, async_client):
        """Two createGroup ops for one name: the second finds it in the map.

        This is the branch, reached the way the executor actually reaches it —
        ``groupIdMap`` already holds the name, so the operation is applied and
        writes nothing.
        """
        client = _base_client()
        client.create_channel_group.return_value = {"id": 77, "name": "Sports"}
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            data = await _commit_and_wait(async_client, {
                "operations": [
                    {"type": "createGroup", "name": "Sports"},
                    {"type": "createGroup", "name": "Sports"},
                ],
            })

        assert data["success"] is True, data
        assert data["operationsApplied"] == 2
        assert client.create_channel_group.await_count == 1
