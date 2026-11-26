"""``BulkCommitResponse`` declares every field ``_run_bulk_commit`` returns.

The model is documentation, not a ``response_model``: the route returns a
202 job envelope on the default path and the completed result reaches the
caller wrapped in the poll response. So nothing validates the envelope at
runtime, and the model drifts unless a test compares it with real results.
"""
from unittest.mock import AsyncMock, patch

import pytest

from routers.channels import BulkCommitRequest, BulkCommitResponse, _run_bulk_commit


def make_client():
    channels = [{"id": 1, "name": "Alpha", "channel_number": 50}]
    mock_client = AsyncMock()
    mock_client.get_channels.return_value = {"results": [dict(c) for c in channels], "next": None}
    mock_client.get_streams_by_ids.side_effect = lambda ids: []
    mock_client.get_channel_groups.return_value = []
    by_id = {ch["id"]: ch for ch in channels}
    mock_client.get_channel.side_effect = lambda cid: dict(by_id.get(cid, {"id": cid}))
    mock_client.assign_channel_numbers.return_value = {"success": True}
    mock_client.update_channel.return_value = {}
    return mock_client


async def run(operations, **kwargs):
    request = BulkCommitRequest(operations=operations, **kwargs)
    with patch("routers.channels.get_client", return_value=make_client()), \
         patch("routers.channels.journal"):
        return await _run_bulk_commit(request, batch_id="batch-1")


RENUMBER = [{"type": "bulkAssignChannelNumbers", "channelIds": [1], "startingNumber": 7}]
MISSING = [{"type": "updateChannel", "channelId": 999, "data": {"name": "x"}}]


def assert_declared(result):
    undeclared = set(result) - set(BulkCommitResponse.model_fields)
    assert not undeclared, f"returned but not declared: {sorted(undeclared)}"
    # Types and optionality agree with what was returned, and validating loses
    # nothing the handler put in the envelope.
    dumped = BulkCommitResponse.model_validate(result).model_dump(exclude_unset=True)
    assert set(dumped) == set(result)


class TestEveryReturnedFieldIsDeclared:

    @pytest.mark.asyncio
    async def test_applied_run(self):
        result = await run(RENUMBER)
        assert result["success"] is True
        assert_declared(result)

    @pytest.mark.asyncio
    async def test_applied_run_with_settled_indexes(self):
        result = await run(RENUMBER, consolidate=True, returnSettledOperationIndexes=True)
        assert "settledOperationIndexes" in result
        assert "settledChannelNumbers" in result
        assert_declared(result)

    @pytest.mark.asyncio
    async def test_validate_only(self):
        result = await run(RENUMBER, validateOnly=True)
        assert_declared(result)

    @pytest.mark.asyncio
    async def test_refused_by_validation(self):
        result = await run(MISSING)
        assert result["success"] is False
        assert result["validationIssues"]
        assert_declared(result)

    @pytest.mark.asyncio
    async def test_failed_operation(self):
        client = make_client()
        client.assign_channel_numbers.side_effect = Exception("dispatcharr said no")
        request = BulkCommitRequest(operations=RENUMBER, continueOnError=True)
        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal"):
            result = await _run_bulk_commit(request, batch_id="batch-1")
        assert result["operationsFailed"] == 1
        assert_declared(result)

    @pytest.mark.asyncio
    async def test_destination_mismatch_early_return(self):
        request = BulkCommitRequest(
            operations=RENUMBER, profileRecoveryDestination="elsewhere",
        )
        with patch("routers.channels.get_client", return_value=make_client()), \
             patch("routers.channels.recovery_destination_matches", return_value=False), \
             patch("routers.channels.journal"):
            result = await _run_bulk_commit(
                request, batch_id="batch-1", profile_recovery_operator="operator-a",
            )
        assert result["errors"][0]["operationId"] == "profile-recovery-destination"
        assert_declared(result)


def test_every_envelope_key_the_executor_initialises_is_declared():
    """The always-present keys, read from the model rather than a hand list."""
    always_present = {
        "success", "operationsApplied", "operationsFailed",
        "operationsPartiallyApplied", "errors", "tempIdMap", "groupIdMap",
        "validationIssues", "validationPassed", "partial",
        "normalizationFailures", "journalRowsUnwritten", "numberingRecovery",
    }
    conditional = {
        "profileRecoveryBinding", "settledOperationIndexes", "settledChannelNumbers",
    }
    assert set(BulkCommitResponse.model_fields) == always_present | conditional
