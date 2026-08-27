"""Every mutation that lands upstream produces a journal row, on every exit path.

Bead ``enhancedchannelmanager-kz089``, fix round 2. Two findings from the
external review, stated as invariants rather than as their reproductions:

* **Journalling.** A mutation that LANDED upstream produces a journal row on
  every exit path — the Phase 1 early return, an exception anywhere, a partial
  batch failure, and the flush itself failing. The reproduction was
  ``groupsToCreate=[A, B]`` where A is created and B fails: A existed upstream,
  the early return skipped ``journal.log_entries`` and the summary write
  entirely, and the envelope said ``success: false, operationsFailed: 0``. That
  is one exit path out of four, so this file walks all four.

* **Validation.** ``setProfileMembership``, ``restoreChannelGroup`` and
  ``clearStreamStats`` validate their input as strictly as the oldest
  operations do — constrained models AND Phase 0 resource resolution. They used
  to take unconstrained ``int`` / ``list[int]`` and were enumerated by neither
  Phase 0 loop, so caller-provided ids reached an upstream PATCH and two local
  DELETEs with nothing resolved.

``journal.log_entries`` reports failure by RETURNING ``False`` rather than by
raising, which is why the "flush failure" cases below drive the return value and
not an exception.
"""
import asyncio as _asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bulk_commit_accounting import bulk_commit_accounting_violations


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


def _journal_double(*, entries_ok=True, entry_ok=True):
    """A stand-in for the journal module with controllable write outcomes."""
    double = MagicMock()
    double.log_entries.return_value = True if entries_ok else False
    double.log_entry.return_value = MagicMock() if entry_ok else None
    double.get_request_batch_id.return_value = "batch-kz089"
    return double


def _entity_rows(journal_double):
    """Every per-entity row the run handed to the journal, batch or per-row."""
    rows = []
    for call in journal_double.log_entries.call_args_list:
        rows.extend(call.args[0])
    for call in journal_double.log_entry.call_args_list:
        if call.kwargs.get("action_type") != "bulk_commit":
            rows.append(call.kwargs)
    return rows


def _summary_calls(journal_double):
    return [
        call for call in journal_double.log_entry.call_args_list
        if call.kwargs.get("action_type") == "bulk_commit"
    ]


def _base_client():
    client = AsyncMock()
    client.get_channels.return_value = {
        "results": [{"id": 7, "name": "Existing", "channel_number": 7, "streams": []}],
        "count": 1,
        "next": None,
    }
    client.get_streams_by_ids.return_value = []
    client.get_channel_profiles.return_value = [{"id": 3, "name": "Kids"}]
    client.get_channel.return_value = {"id": 7, "name": "Existing", "streams": []}
    client.create_channel_group.return_value = {"id": 42, "name": "A"}
    return client


@pytest.fixture(autouse=True)
def _clear_jobs():
    from routers import channels as router_module

    router_module._BULK_COMMIT_JOBS.clear()
    yield
    router_module._BULK_COMMIT_JOBS.clear()


# --------------------------------------------------------------------------
# Invariant: every landed mutation is journalled, on EVERY exit path
# --------------------------------------------------------------------------

class TestEveryLandedMutationIsJournalled:

    @pytest.mark.asyncio
    async def test_phase1_group_failure_journals_the_group_it_did_create(
        self, async_client
    ):
        """The reviewer's reproduction: A created, B fails, A must have a row.

        The early return used to skip the journal flush AND the summary write,
        so group A existed upstream with nothing recording that ECM made it.
        """
        client = _base_client()
        client.create_channel_group.side_effect = [
            {"id": 42, "name": "A"},
            RuntimeError("500 Server Error from Dispatcharr"),
        ]
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            data = await _commit_and_wait(async_client, {
                "operations": [
                    {"type": "updateChannel", "channelId": 7, "data": {"name": "N"}},
                ],
                "groupsToCreate": [{"name": "A"}, {"name": "B"}],
            })

        rows = _entity_rows(journal_double)
        assert [r["entity_name"] for r in rows] == ["A"], rows
        assert rows[0]["action_type"] == "group_create"
        assert rows[0]["entity_id"] == 42
        assert len(_summary_calls(journal_double)) == 1

    @pytest.mark.asyncio
    async def test_phase1_group_failure_leaves_consistent_accounting(
        self, async_client
    ):
        """`success: false` with `operationsFailed: 0` and no counted failure.

        No operation was attempted, so `operationsFailed` is legitimately 0 —
        but the run is not a success and the error entry naming the failed
        group has to be accounted for by something. It is a setup failure.
        """
        client = _base_client()
        client.create_channel_group.side_effect = [
            {"id": 42, "name": "A"},
            RuntimeError("500 Server Error from Dispatcharr"),
        ]

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", _journal_double()):
            data = await _commit_and_wait(async_client, {
                "operations": [
                    {"type": "updateChannel", "channelId": 7, "data": {"name": "N"}},
                ],
                "groupsToCreate": [{"name": "A"}, {"name": "B"}],
            })

        assert data["success"] is False
        assert data["operationsApplied"] == 0
        assert data["operationsFailed"] == 0
        assert [e["operationId"] for e in data["errors"]] == ["create-group-B"]
        # The envelope's own audit, with the setup failure declared.
        assert bulk_commit_accounting_violations(
            data,
            total_operations=1,
            aborted=True,
            applied_create_temp_ids=set(),
            setup_failures=1,
        ) == []

    @pytest.mark.asyncio
    async def test_a_crash_after_the_operations_still_writes_their_rows(
        self, async_client
    ):
        """Everything landed, then the run fell over. The rows must survive.

        Driven through the accounting finalizer because that is the one thing
        between the last operation and the old journal writes that can raise.
        """
        client = _base_client()
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double), \
             patch(
                 "routers.channels.finalize_bulk_commit_result",
                 side_effect=RuntimeError("accounting blew up"),
             ):
            data = await _commit_and_wait(async_client, {
                "operations": [
                    {"type": "updateChannel", "channelId": 7, "data": {"name": "N"}},
                ],
            })

        rows = _entity_rows(journal_double)
        assert [r["action_type"] for r in rows] == ["update"], rows
        assert data["operationsApplied"] == 1
        assert data["success"] is False

    @pytest.mark.asyncio
    async def test_a_failed_batch_write_is_retried_one_row_at_a_time(
        self, async_client
    ):
        """`log_entries` returning False must not lose the whole audit trail.

        Its return value used to be discarded, so a failed batch write took
        every row with it in silence.
        """
        client = _base_client()
        journal_double = _journal_double(entries_ok=False)

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            data = await _commit_and_wait(async_client, {
                "operations": [
                    {"type": "updateChannel", "channelId": 7, "data": {"name": "N"}},
                ],
            })

        per_row = [
            call for call in journal_double.log_entry.call_args_list
            if call.kwargs.get("action_type") == "update"
        ]
        assert len(per_row) == 1, journal_double.log_entry.call_args_list
        assert data["journalRowsUnwritten"] == 0
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_rows_that_cannot_be_written_are_named_in_the_envelope(
        self, async_client
    ):
        """The journal is the thing that failed; say so, and do not fail the op.

        The mutation LANDED. Reporting it as failed is what makes an integrator
        retry and apply it twice, so it stays counted as applied while the
        envelope says the audit trail is incomplete.
        """
        client = _base_client()
        journal_double = _journal_double(entries_ok=False, entry_ok=False)

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            data = await _commit_and_wait(async_client, {
                "operations": [
                    {"type": "updateChannel", "channelId": 7, "data": {"name": "N"}},
                ],
            })

        assert data["operationsApplied"] == 1
        assert data["operationsFailed"] == 0
        assert data["journalRowsUnwritten"] == 2  # the entity row and the summary
        assert data["success"] is False
        assert [e["operationId"] for e in data["errors"]] == ["bulk-commit-journal"]
        assert bulk_commit_accounting_violations(
            data,
            total_operations=1,
            aborted=False,
            applied_create_temp_ids=set(),
            setup_failures=1,
        ) == []

    @pytest.mark.asyncio
    async def test_a_dry_run_still_leaves_no_journal_trace(self, async_client):
        """PIN — passes before and after the fix, deliberately.

        The flush became unconditional, so the one exemption that must survive
        it gets a guard: a dry run is not a commit and leaves no trace.
        """
        client = _base_client()
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            response = await async_client.post("/api/channels/bulk-commit", json={
                "operations": [
                    {"type": "updateChannel", "channelId": 7, "data": {"name": "N"}},
                ],
                "validateOnly": True,
            })

        assert response.status_code == 200, response.text
        journal_double.log_entries.assert_not_called()
        journal_double.log_entry.assert_not_called()


# --------------------------------------------------------------------------
# Invariant: the three newer operations validate as strictly as the oldest
# --------------------------------------------------------------------------

class TestNewOperationModelsAreConstrained:
    """Model-level contract. These never reach the executor at all."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("operation", [
        pytest.param(
            {"type": "setProfileMembership", "profileId": 0, "channelId": 7, "enabled": True},
            id="profile-id-zero",
        ),
        pytest.param(
            {"type": "setProfileMembership", "profileId": -3, "channelId": 7, "enabled": True},
            id="profile-id-negative",
        ),
        pytest.param(
            {"type": "restoreChannelGroup", "groupId": -1000},
            id="group-id-negative",
        ),
        pytest.param(
            {"type": "restoreChannelGroup", "groupId": 0},
            id="group-id-zero",
        ),
        pytest.param(
            {"type": "clearStreamStats", "streamIds": []},
            id="stream-ids-empty",
        ),
        pytest.param(
            {"type": "clearStreamStats", "streamIds": [5, 5]},
            id="stream-ids-duplicated",
        ),
        pytest.param(
            {"type": "clearStreamStats", "streamIds": [-5]},
            id="stream-ids-negative",
        ),
    ])
    async def test_rejected_before_the_executor_sees_it(self, async_client, operation):
        response = await async_client.post(
            "/api/channels/bulk-commit",
            json={"operations": [operation], "validateOnly": True},
        )
        assert response.status_code == 422, response.text


class TestNewOperationsResolveTheirResources:
    """Phase 0 contract — the checks every older operation already got."""

    async def _validate(self, async_client, client, operations):
        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", _journal_double()):
            response = await async_client.post(
                "/api/channels/bulk-commit",
                json={"operations": operations, "validateOnly": True},
            )
        assert response.status_code == 200, response.text
        return response.json()

    @pytest.mark.asyncio
    async def test_profile_membership_for_a_missing_channel_is_an_error(
        self, async_client
    ):
        data = await self._validate(async_client, _base_client(), [
            {"type": "setProfileMembership", "profileId": 3, "channelId": 999, "enabled": True},
        ])
        assert data["validationPassed"] is False
        assert any(
            issue["type"] == "missing_channel" and issue["channelId"] == 999
            for issue in data["validationIssues"]
        ), data["validationIssues"]

    @pytest.mark.asyncio
    async def test_profile_membership_for_a_missing_profile_is_an_error(
        self, async_client
    ):
        data = await self._validate(async_client, _base_client(), [
            {"type": "setProfileMembership", "profileId": 77, "channelId": 7, "enabled": True},
        ])
        assert data["validationPassed"] is False
        assert any(
            "profile 77" in issue["message"] for issue in data["validationIssues"]
        ), data["validationIssues"]

    @pytest.mark.asyncio
    async def test_an_unresolvable_profile_lookup_accuses_nobody(self, async_client):
        """PIN — vacuously true before the fix (nothing validated profiles).

        It guards the new check against the failure mode the OLDER checks have:
        a lookup that fails leaves its catalog empty, and an emptied catalog
        makes every referenced id look missing. A broken lookup is not evidence
        the profile is missing.
        """
        client = _base_client()
        client.get_channel_profiles.side_effect = RuntimeError("upstream down")
        data = await self._validate(async_client, client, [
            {"type": "setProfileMembership", "profileId": 77, "channelId": 7, "enabled": True},
        ])
        assert data["validationPassed"] is True
        assert data["validationIssues"] == []

    @pytest.mark.asyncio
    async def test_restoring_a_group_that_is_not_hidden_warns(self, async_client):
        """A warning, not an error — another session restoring it first is a race."""
        data = await self._validate(async_client, _base_client(), [
            {"type": "restoreChannelGroup", "groupId": 44},
        ])
        assert data["validationPassed"] is True
        assert [i["severity"] for i in data["validationIssues"]] == ["warning"]
        assert "not hidden" in data["validationIssues"][0]["message"]

    @pytest.mark.asyncio
    async def test_clearing_stats_for_an_unknown_stream_warns(self, async_client):
        """Probe stats outlive their stream, so this must stay possible."""
        data = await self._validate(async_client, _base_client(), [
            {"type": "clearStreamStats", "streamIds": [5]},
        ])
        assert data["validationPassed"] is True
        assert [i["streamId"] for i in data["validationIssues"]] == [5]
        assert data["validationIssues"][0]["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_an_unresolved_temp_channel_id_never_reaches_dispatcharr(
        self, async_client
    ):
        """A negative channel id no createChannel produced is refused by name."""
        client = _base_client()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", _journal_double()):
            response = await async_client.post("/api/channels/bulk-commit", json={
                "operations": [
                    {"type": "setProfileMembership", "profileId": 3, "channelId": -7,
                     "enabled": True},
                ],
            })

        client.update_profile_channel.assert_not_called()
        assert response.status_code == 422
        assert "exactly one earlier createChannel" in response.text
