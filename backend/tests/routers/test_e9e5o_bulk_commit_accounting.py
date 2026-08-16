"""The bulk-commit envelope's accounting never contradicts itself.

Bead `enhancedchannelmanager-e9e5o`, fix round 4.

Rounds 1-3 each closed one reproduction of the same class of defect and left
the property unenforced a level down. This file asserts the property instead,
over a generated scenario matrix rather than over the single reproduction the
reviewer supplied:

    {normalization succeeded, failed}
      x {create succeeded, threw, returned malformed}
      x {first, middle, last in batch}

plus the batch-boundary case (Edit Mode posts operations in batches of 200) and
the early-abort case (`continueOnError=false`).

The invariant under test lives in `backend/bulk_commit_accounting.py`. The
demonstrated defect — a create Dispatcharr PERSISTED but answered without a
usable id, reported as `operationsApplied=0, operationsFailed=1, partial=false`
and listed in both `errors` and `normalizationFailures` — is one cell of the
matrix, not the specification.
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

# --------------------------------------------------------------------------
# Scenario vocabulary
# --------------------------------------------------------------------------

#: What `client.create_channel` does for the operation under test.
CREATE_OK = "create-succeeded"
CREATE_THREW = "create-threw"
CREATE_MALFORMED_NO_ID = "create-returned-no-id-key"
CREATE_MALFORMED_NULL_ID = "create-returned-null-id"
CREATE_MALFORMED_STR_ID = "create-returned-non-numeric-id"

MALFORMED_CREATES = (
    CREATE_MALFORMED_NO_ID,
    CREATE_MALFORMED_NULL_ID,
    CREATE_MALFORMED_STR_ID,
)
ALL_CREATES = (CREATE_OK, CREATE_THREW) + MALFORMED_CREATES

BATCH_LENGTH = 3
POSITIONS = (0, 1, 2)  # first, middle, last


def _create_response(behaviour: str, channel_id: int, name: str):
    """The value (or exception) `client.create_channel` produces."""
    if behaviour == CREATE_OK:
        return {"id": channel_id, "name": name}
    if behaviour == CREATE_THREW:
        return RuntimeError("Dispatcharr rejected the create")
    if behaviour == CREATE_MALFORMED_NO_ID:
        return {"name": name}
    if behaviour == CREATE_MALFORMED_NULL_ID:
        return {"id": None, "name": name}
    if behaviour == CREATE_MALFORMED_STR_ID:
        return {"id": "not-a-number", "name": name}
    raise AssertionError(f"unknown create behaviour {behaviour!r}")


def _make_norm_result(original: str, normalized: str):
    mock = MagicMock()
    mock.original = original
    mock.normalized = normalized
    mock.rules_applied = []
    mock.transformations = []
    return mock


async def _commit_and_wait(async_client, body, *, max_polls=200):
    """POST a bulk commit and poll until terminal, returning the envelope."""
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


def assert_envelope_is_self_consistent(data, *, total_operations, aborted=False):
    """The invariant, checked against the envelope as an integrator sees it.

    Deliberately re-derives `applied_create_temp_ids` from the RESPONSE rather
    than from the ledger the executor used: an audit that trusts the thing it is
    auditing checks nothing. A createChannel operation counts as applied when it
    produced no unapplied error entry.
    """
    errors = data.get("errors") or []
    unapplied_op_ids = {
        e.get("operationId") for e in errors if e.get("applied") is not True
    }
    applied_temp_ids = {
        temp_id
        for temp_id, op_id in data["_expectedCreateOpIds"].items()
        if op_id not in unapplied_op_ids
    }
    violations = bulk_commit_accounting_violations(
        data,
        total_operations=total_operations,
        aborted=aborted,
        applied_create_temp_ids=applied_temp_ids,
    )
    assert violations == [], violations


# --------------------------------------------------------------------------
# The generated matrix, end to end through the real executor
# --------------------------------------------------------------------------

class TestBulkCommitAccountingMatrix:
    """Every cell of {normalization} x {create outcome} x {position}."""

    @pytest.fixture(autouse=True)
    def _clear_jobs(self):
        from routers import channels as router_module

        router_module._BULK_COMMIT_JOBS.clear()
        yield
        router_module._BULK_COMMIT_JOBS.clear()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("normalization_ok", [True, False], ids=["norm-ok", "norm-broken"])
    @pytest.mark.parametrize("create_behaviour", ALL_CREATES)
    @pytest.mark.parametrize("position", POSITIONS, ids=["first", "middle", "last"])
    async def test_envelope_is_consistent_for_every_scenario(
        self, async_client, normalization_ok, create_behaviour, position
    ):
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}

        names = [f"US: CHANNEL {i}" for i in range(BATCH_LENGTH)]
        responses = []
        for idx in range(BATCH_LENGTH):
            behaviour = create_behaviour if idx == position else CREATE_OK
            responses.append(_create_response(behaviour, 100 + idx, names[idx]))
        mock_client.create_channel.side_effect = responses

        ops = [
            {
                "type": "createChannel",
                "tempId": -(idx + 1),
                "name": names[idx],
                "normalize": True,
            }
            for idx in range(BATCH_LENGTH)
        ]

        engine_patch = (
            patch(
                "routers.channels.get_normalization_engine",
                side_effect=RuntimeError("engine offline"),
            )
            if not normalization_ok
            else patch(
                "routers.channels.get_normalization_engine",
                return_value=_working_engine(),
            )
        )

        with patch("routers.channels.get_client", return_value=mock_client), \
             engine_patch, \
             patch("routers.channels.journal"):
            data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        data["_expectedCreateOpIds"] = {
            -(idx + 1): f"op-{idx}-createChannel" for idx in range(BATCH_LENGTH)
        }
        assert_envelope_is_self_consistent(data, total_operations=BATCH_LENGTH)

        # --- and the specific facts each cell must report -------------------
        threw = create_behaviour == CREATE_THREW
        malformed = create_behaviour in MALFORMED_CREATES

        expected_failed = 1 if threw else 0
        expected_applied = BATCH_LENGTH - expected_failed
        assert data["operationsFailed"] == expected_failed
        assert data["operationsApplied"] == expected_applied

        # A create that PERSISTED is never a total failure, whatever the
        # response body looked like. This is the rule an integrator's retry
        # logic depends on: reporting it failed duplicates the channel.
        target_op_id = f"op-{position}-createChannel"
        target_errors = [
            e for e in (data["errors"] or []) if e.get("operationId") == target_op_id
        ]
        if malformed:
            assert len(target_errors) == 1, data["errors"]
            assert target_errors[0]["applied"] is True
            assert data["success"] is False
            assert data["partial"] is True
        elif threw:
            assert len(target_errors) == 1, data["errors"]
            assert target_errors[0].get("applied") is not True
            assert data["success"] is False
        else:
            assert target_errors == []
            assert data["success"] is True
            assert data["partial"] is False

        # Every normalizationFailures entry names a channel that EXISTS.
        listed = {f["tempId"] for f in data["normalizationFailures"]}
        if normalization_ok:
            assert listed == set()
        else:
            expected_listed = {
                -(idx + 1)
                for idx in range(BATCH_LENGTH)
                if not (idx == position and threw)
            }
            assert listed == expected_listed

    @pytest.mark.asyncio
    async def test_a_malformed_create_maps_no_temp_id_rather_than_a_null_one(
        self, async_client
    ):
        """The unusable id never reaches `tempIdMap`.

        `{"id": null}` used to be accepted silently: the op was counted as
        applied and `tempIdMap[-1] = null` was handed to the frontend, which
        then posted `channelId: null` on every follow-up operation.
        """
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.create_channel.return_value = {"id": None, "name": "CNN"}

        ops = [{"type": "createChannel", "tempId": -1, "name": "CNN"}]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        assert data["tempIdMap"] == {}
        assert data["operationsApplied"] == 1
        assert data["operationsFailed"] == 0
        assert data["success"] is False
        assert data["partial"] is True
        assert data["errors"][0]["applied"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("position", [0, 100, 200], ids=["first", "middle", "last"])
    async def test_accounting_holds_across_a_full_frontend_batch(
        self, async_client, position
    ):
        """Edit Mode posts 200 operations per request; the boundary is not special."""
        total = 201
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.create_channel.side_effect = [
            _create_response(
                CREATE_MALFORMED_NO_ID if idx == position else CREATE_OK,
                1000 + idx,
                f"CH {idx}",
            )
            for idx in range(total)
        ]

        ops = [
            {"type": "createChannel", "tempId": -(idx + 1), "name": f"CH {idx}"}
            for idx in range(total)
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        data["_expectedCreateOpIds"] = {
            -(idx + 1): f"op-{idx}-createChannel" for idx in range(total)
        }
        assert_envelope_is_self_consistent(data, total_operations=total)
        assert data["operationsApplied"] == total
        assert data["operationsFailed"] == 0
        assert len(data["tempIdMap"]) == total - 1

    @pytest.mark.asyncio
    async def test_an_early_abort_never_claims_operations_it_did_not_attempt(
        self, async_client
    ):
        """`continueOnError=false` stops the run; the counts say so honestly."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.create_channel.side_effect = [
            {"id": 1, "name": "A"},
            RuntimeError("Dispatcharr rejected the create"),
            {"id": 3, "name": "C"},
        ]

        ops = [
            {"type": "createChannel", "tempId": -(idx + 1), "name": name}
            for idx, name in enumerate(["A", "B", "C"])
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": False}
            )

        data["_expectedCreateOpIds"] = {
            -(idx + 1): f"op-{idx}-createChannel" for idx in range(3)
        }
        assert_envelope_is_self_consistent(data, total_operations=3, aborted=True)
        assert data["operationsApplied"] == 1
        assert data["operationsFailed"] == 1
        # The third op was never attempted, so it is claimed by neither counter.
        assert data["operationsApplied"] + data["operationsFailed"] == 2

    @pytest.mark.asyncio
    async def test_every_operation_type_resolves_to_exactly_one_outcome(
        self, async_client
    ):
        """All thirteen types, one batch. Nothing is counted twice or not at all.

        The old arrangement incremented `operationsApplied` from inside each
        branch, so a type nobody handled was counted zero times and a branch that
        raised after incrementing was counted twice.
        """
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 7, "name": "Existing", "channel_number": 7, "streams": [5]}],
            "count": 1,
            "next": None,
        }
        mock_client.get_streams.return_value = {
            "results": [{"id": 5, "name": "Stream 5"}],
            "count": 1,
            "next": None,
        }
        mock_client.get_channel.return_value = {"id": 7, "name": "Existing", "streams": [5]}
        mock_client.create_channel.return_value = {"id": 99, "name": "New"}
        mock_client.create_channel_group.return_value = {"id": 42, "name": "Fresh Group"}

        ops = [
            {"type": "createChannel", "tempId": -1, "name": "New"},
            {"type": "updateChannel", "channelId": 7, "data": {"name": "Renamed"}},
            {"type": "addStreamToChannel", "channelId": 7, "streamId": 5},
            {"type": "removeStreamFromChannel", "channelId": 7, "streamId": 5},
            {"type": "reorderChannelStreams", "channelId": 7, "streamIds": [5]},
            {"type": "bulkAssignChannelNumbers", "channelIds": [7], "startingNumber": 3},
            {"type": "createGroup", "name": "Fresh Group"},
            {"type": "renameChannelGroup", "groupId": 42, "newName": "Renamed Group"},
            {"type": "deleteChannelGroup", "groupId": 43},
            {"type": "setProfileMembership", "profileId": 1, "channelId": 7, "enabled": True},
            {"type": "restoreChannelGroup", "groupId": 44},
            {"type": "clearStreamStats", "streamIds": [5]},
            {"type": "deleteChannel", "channelId": 7},
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.reparent_group_channels", AsyncMock(return_value=0)), \
             patch("routers.channels.journal"):
            data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        data["_expectedCreateOpIds"] = {-1: "op-0-createChannel"}
        assert_envelope_is_self_consistent(data, total_operations=len(ops))
        assert data["operationsApplied"] + data["operationsFailed"] == len(ops)


def _working_engine():
    engine = MagicMock()
    engine.normalize.side_effect = lambda text: _make_norm_result(
        text, text.replace("US: ", "")
    )
    return engine


# --------------------------------------------------------------------------
# The enforcement code tests itself
# --------------------------------------------------------------------------

class TestAccountingAuditCatchesWhatItClaimsTo:
    """Fixture-based self-test of the audit, per the engineering discipline.

    A check that cannot fail while the thing is broken is not a check, so each
    case here hands the audit an envelope with exactly one thing wrong and
    asserts it is named.
    """

    def _clean(self):
        return {
            "success": True,
            "operationsApplied": 2,
            "operationsFailed": 0,
            "partial": False,
            "errors": [],
            "normalizationFailures": [],
        }

    def test_a_consistent_envelope_has_no_violations(self):
        assert bulk_commit_accounting_violations(
            self._clean(),
            total_operations=2,
            aborted=False,
            applied_create_temp_ids=set(),
        ) == []

    def test_counts_that_do_not_add_up_are_named(self):
        result = self._clean()
        result["operationsApplied"] = 1
        violations = bulk_commit_accounting_violations(
            result, total_operations=2, aborted=False, applied_create_temp_ids=set()
        )
        assert any("were submitted" in v for v in violations), violations

    def test_success_contradicting_the_failure_count_is_named(self):
        result = self._clean()
        result["operationsFailed"] = 1
        result["operationsApplied"] = 1
        result["errors"] = [{"operationId": "op-1-createChannel", "error": "boom"}]
        result["partial"] = True
        violations = bulk_commit_accounting_violations(
            result, total_operations=2, aborted=False, applied_create_temp_ids=set()
        )
        assert any("success is True" in v for v in violations), violations

    def test_partial_contradicting_the_counts_is_named(self):
        result = self._clean()
        result["operationsApplied"] = 1
        result["operationsFailed"] = 1
        result["success"] = False
        result["errors"] = [{"operationId": "op-1-createChannel", "error": "boom"}]
        result["partial"] = False
        violations = bulk_commit_accounting_violations(
            result, total_operations=2, aborted=False, applied_create_temp_ids=set()
        )
        assert any("partial is False" in v for v in violations), violations

    def test_a_normalization_failure_for_an_unapplied_create_is_named(self):
        result = self._clean()
        result["operationsApplied"] = 1
        result["operationsFailed"] = 1
        result["success"] = False
        result["partial"] = True
        result["errors"] = [{"operationId": "op-1-createChannel", "error": "boom"}]
        result["normalizationFailures"] = [
            {"tempId": -2, "name": "X", "nameApplied": "X", "error": "engine offline"}
        ]
        violations = bulk_commit_accounting_violations(
            result, total_operations=2, aborted=False, applied_create_temp_ids={-1}
        )
        assert any("normalizationFailures names tempId -2" in v for v in violations), violations

    def test_an_error_count_that_does_not_match_the_error_list_is_named(self):
        result = self._clean()
        result["operationsApplied"] = 1
        result["operationsFailed"] = 1
        result["success"] = False
        result["partial"] = True
        violations = bulk_commit_accounting_violations(
            result, total_operations=2, aborted=False, applied_create_temp_ids=set()
        )
        assert any("error entries describe" in v for v in violations), violations

    def test_an_aborted_run_may_attempt_fewer_than_it_was_given(self):
        result = self._clean()
        result["operationsApplied"] = 1
        assert bulk_commit_accounting_violations(
            result, total_operations=5, aborted=True, applied_create_temp_ids=set()
        ) == []


class TestOperationLedgerRefusesToMiscount:
    """The ledger makes the double- and zero-count arrangements unrepresentable."""

    def test_an_outcome_recorded_twice_raises(self):
        ledger = OperationLedger(1)
        ledger.begin()
        ledger.record_applied()
        with pytest.raises(BulkCommitAccountingError):
            ledger.record_failed()

    def test_opening_without_closing_raises(self):
        ledger = OperationLedger(2)
        ledger.begin()
        with pytest.raises(BulkCommitAccountingError):
            ledger.begin()

    def test_a_persisted_operation_can_only_close_as_applied_by_the_caller(self):
        """`persisted` is what the executor reads to keep rule 3."""
        ledger = OperationLedger(1)
        ledger.begin()
        assert ledger.persisted is False
        ledger.record_persisted(create_temp_id=-1)
        assert ledger.persisted is True
        ledger.record_applied(incomplete=True)
        assert ledger.applied == 1
        assert ledger.failed == 0
        assert ledger.incomplete == 1
        assert ledger.applied_create_temp_ids == {-1}

    def test_finalize_raises_rather_than_returning_a_contradictory_envelope(self):
        ledger = OperationLedger(2)
        ledger.begin()
        ledger.record_applied()
        result = {"errors": [], "normalizationFailures": []}
        # One operation of two resolved, and the run did not abort.
        with pytest.raises(BulkCommitAccountingError):
            finalize_bulk_commit_result(result, ledger)

    def test_finalize_derives_success_and_partial_from_the_ledger(self):
        ledger = OperationLedger(2)
        ledger.begin()
        ledger.record_persisted()
        ledger.record_applied(incomplete=True)
        ledger.begin()
        ledger.record_applied()
        result = {
            "errors": [{"operationId": "op-0-createChannel", "applied": True, "error": "x"}],
            "normalizationFailures": [],
        }
        finalize_bulk_commit_result(result, ledger)
        assert result["operationsApplied"] == 2
        assert result["operationsFailed"] == 0
        assert result["success"] is False
        assert result["partial"] is True
