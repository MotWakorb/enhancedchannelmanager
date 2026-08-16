"""A journal row is QUEUED at the moment its write lands, not on the way out.

Bead ``enhancedchannelmanager-kz089``, fix round 3. Round 2 put the journal
*flush* behind a single unavoidable ``finish()``. That was necessary and not
sufficient: ``finish()`` can only write rows that were already queued, so every
path that mutated upstream and then failed before reaching the row construction
still lost the row. Three of the reviewer's five findings were that one gap.

The invariants this file pins, as properties rather than as the three
reproductions that exposed them:

1. Every upstream mutation that LANDS produces a journal row, whatever fails
   afterwards, on every path — including a helper that performs several
   independent writes and fails partway (``reparent_group_channels``) and a
   write that happens *inside* an operation which then fails as a whole (the
   catalog logo a ``createChannel`` creates before the channel).
2. It is not expressible in code to record a mutation as persisted without
   queueing its journal row: :meth:`OperationLedger.record_persisted` and
   :meth:`OperationLedger.record_write` both REQUIRE a ``journal_row``, and the
   ledger owns the queue, so there is no second way to enqueue one and no way
   to skip it by accident. Saying "this write has nothing to journal" is a
   deliberate, named statement — ``nothing_to_journal(reason)`` — not an
   omission.
4. A lookup that FAILED never accuses an operation of referencing a missing
   entity. Round 2 gave the profile and hidden-group lookups that guard; the
   channel and stream lookups still read their own emptiness as proof of
   absence.

(Invariant 3 is the frontend's, pinned in
``frontend/src/hooks/useEditMode.stagedGroups.test.ts``.)
"""
import asyncio as _asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bulk_commit_accounting import (
    BulkCommitAccountingError,
    OperationLedger,
    nothing_to_journal,
)


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


def _journal_double():
    double = MagicMock()
    double.log_entries.return_value = True
    double.log_entry.return_value = MagicMock()
    double.get_request_batch_id.return_value = "batch-kz089-r3"
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


def _base_client():
    client = AsyncMock()
    client.get_channels.return_value = {"results": [], "count": 0, "next": None}
    client.get_streams_by_ids.return_value = []
    client.get_channel_profiles.return_value = [{"id": 3, "name": "Kids"}]
    client.get_channel_groups.return_value = [{"id": 1, "name": "Default Group"}]
    client.get_logos.return_value = {"results": [], "next": None}
    return client


@pytest.fixture(autouse=True)
def _clear_jobs():
    from routers import channels as router_module

    router_module._BULK_COMMIT_JOBS.clear()
    yield
    router_module._BULK_COMMIT_JOBS.clear()


# --------------------------------------------------------------------------
# Invariant 2, at the chokepoint itself
# --------------------------------------------------------------------------

class TestTheLedgerIsTheChokepoint:
    """PIN. The ledger is what makes invariant 1 structural rather than a habit.

    These assert the SHAPE of the API, not a behaviour of the executor: if a
    future edit gives ``journal_row`` a default, or adds a second way to
    enqueue a row, these fail. That is the whole point — three of the five
    review findings were "somebody forgot to call ``add_journal_row``", and a
    parameter you cannot forget is the only fix that generalises.
    """

    def test_record_persisted_cannot_be_called_without_a_journal_row(self):
        ledger = OperationLedger(1)
        ledger.begin()
        with pytest.raises(TypeError):
            ledger.record_persisted()

    def test_record_write_cannot_be_called_without_a_journal_row(self):
        ledger = OperationLedger(1)
        with pytest.raises(TypeError):
            ledger.record_write()

    def test_the_ledger_owns_the_only_row_queue(self):
        ledger = OperationLedger(1)
        ledger.begin()
        ledger.record_persisted(journal_row={"action_type": "update", "entity_id": 7})
        assert ledger.drain_journal_rows() == [
            {"action_type": "update", "entity_id": 7}
        ]
        # Drained, not copied: the flush takes the rows away so a second flush
        # cannot write them twice.
        assert ledger.drain_journal_rows() == []

    def test_several_rows_from_one_write_are_queued_together(self):
        """One ``assign_channel_numbers`` call is N per-channel facts."""
        ledger = OperationLedger(1)
        ledger.begin()
        ledger.record_persisted(journal_row=[{"entity_id": 1}, {"entity_id": 2}])
        assert len(ledger.drain_journal_rows()) == 2

    def test_an_empty_row_list_is_refused(self):
        """``journal_row=[]`` would be exactly the silent omission being fixed."""
        ledger = OperationLedger(1)
        ledger.begin()
        with pytest.raises(BulkCommitAccountingError):
            ledger.record_persisted(journal_row=[])

    def test_saying_there_is_nothing_to_journal_needs_a_reason(self):
        ledger = OperationLedger(1)
        ledger.begin()
        ledger.record_persisted(
            journal_row=nothing_to_journal("the channel was already gone upstream")
        )
        assert ledger.persisted is True
        assert ledger.drain_journal_rows() == []
        with pytest.raises(BulkCommitAccountingError):
            nothing_to_journal("")


# --------------------------------------------------------------------------
# F1 — a multi-write helper that fails partway
# --------------------------------------------------------------------------

class TestAMultiWriteHelperJournalsWhatItMoved:

    def _group_client(self):
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

    @pytest.mark.asyncio
    async def test_a_landed_reparent_is_journalled_even_though_the_op_fails(
        self, async_client
    ):
        """The reviewer's reproduction, as an example of invariant 1.

        Group 42 holds channels 10 and 11. Moving 10 succeeds; moving 11
        raises, so the group is never deleted and the operation is reported as
        a failure — correctly, because the group still exists. Channel 10 moved
        anyway, and that is a fact only the journal can carry.
        """
        client = self._group_client()
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
        assert data["operationsApplied"] == 0
        # Channel 10's PATCH went upstream and returned. This is the fact the
        # row has to carry, and asserting it here is what makes the red proof
        # "a landed move with no row" rather than "a code path is missing".
        assert client.update_channel.await_count == 2
        moved_rows = [r for r in _entity_rows(journal_double) if r["entity_id"] == 10]
        assert len(moved_rows) == 1, _entity_rows(journal_double)
        assert moved_rows[0]["after_value"]["channel_group_id"] == 1
        assert moved_rows[0]["before_value"]["channel_group_id"] == 42
        # The group delete never happened, so nothing may claim it did.
        assert not [
            r for r in _entity_rows(journal_double)
            if r["action_type"] == "group_delete"
        ]

    @pytest.mark.asyncio
    async def test_every_move_of_a_successful_delete_is_journalled_too(
        self, async_client
    ):
        """The happy path carries the same per-channel facts, not just a count."""
        client = self._group_client()
        client.update_channel.return_value = None
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            data = await _commit_and_wait(async_client, {
                "operations": [{"type": "deleteChannelGroup", "groupId": 42}],
            })

        assert data["success"] is True
        rows = _entity_rows(journal_double)
        assert sorted(r["entity_id"] for r in rows) == [10, 11, 42]
        assert [r for r in rows if r["action_type"] == "group_delete"]


# --------------------------------------------------------------------------
# F2 — bookkeeping that fails AFTER the write lands
# --------------------------------------------------------------------------

class TestBookkeepingFailureAfterTheWriteKeepsTheRow:

    @pytest.mark.asyncio
    async def test_a_malformed_create_response_still_journals_the_channel(
        self, async_client
    ):
        """Dispatcharr accepted the create and answered without a usable id.

        The ledger already reported this honestly as applied-but-incomplete.
        The journal had only the bulk summary, because the row was built three
        statements after the ledger call and the raise sits between them.
        """
        client = _base_client()
        client.create_channel.return_value = {"id": None, "name": "Ghost"}
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            data = await _commit_and_wait(async_client, {
                "operations": [
                    {"type": "createChannel", "tempId": -1, "name": "Ghost"},
                ],
                "continueOnError": True,
            })

        assert data["operationsApplied"] == 1
        assert data["success"] is False
        rows = [r for r in _entity_rows(journal_double) if r["action_type"] == "create"]
        assert len(rows) == 1, _entity_rows(journal_double)
        assert rows[0]["entity_name"] == "Ghost"

    @pytest.mark.asyncio
    async def test_a_malformed_group_create_response_still_journals_the_group(
        self, async_client
    ):
        """The same shape one branch over — ``new_group["id"]`` after the write.

        Named separately because the review found only the channel one. A fix
        scoped to the reproduction would leave this live.
        """
        client = _base_client()
        client.create_channel_group.return_value = {"name": "Sports"}
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            await _commit_and_wait(async_client, {
                "operations": [{"type": "createGroup", "name": "Sports"}],
                "continueOnError": True,
            })

        rows = [
            r for r in _entity_rows(journal_double)
            if r["action_type"] == "group_create"
        ]
        assert len(rows) == 1, _entity_rows(journal_double)
        assert rows[0]["entity_name"] == "Sports"


# --------------------------------------------------------------------------
# F3 — a write inside an operation that then fails as a whole
# --------------------------------------------------------------------------

class TestAWriteInsideAFailedOperationIsStillJournalled:

    @pytest.mark.asyncio
    async def test_a_catalog_logo_created_for_a_failed_create_is_journalled(
        self, async_client
    ):
        """The logo is created upstream before the channel POST is even sent.

        The settled product decision that catalog logo additions are immediate
        and additive is NOT in dispute here — the logo legitimately survives a
        failed create. What it may not do is survive it invisibly.
        """
        client = _base_client()
        client.create_logo.return_value = {
            "id": 55, "name": "Ghost", "url": "http://logos/ghost.png",
        }
        client.create_channel.side_effect = RuntimeError("500 Server Error")
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            data = await _commit_and_wait(async_client, {
                "operations": [{
                    "type": "createChannel",
                    "tempId": -1,
                    "name": "Ghost",
                    "logoUrl": "http://logos/ghost.png",
                }],
                "continueOnError": True,
            })

        # The channel does not exist, so the operation is a failure and nothing
        # about the logo may change that.
        assert data["operationsFailed"] == 1
        assert data["operationsApplied"] == 0
        rows = [
            r for r in _entity_rows(journal_double)
            if r["action_type"] == "logo_create"
        ]
        assert len(rows) == 1, _entity_rows(journal_double)
        assert rows[0]["entity_id"] == 55
        assert rows[0]["after_value"]["url"] == "http://logos/ghost.png"

    @pytest.mark.asyncio
    async def test_a_reused_catalog_logo_writes_no_row(self, async_client):
        """Nothing was created, so there is nothing to journal."""
        client = _base_client()
        client.get_logos.return_value = {
            "results": [{"id": 55, "url": "http://logos/ghost.png"}], "next": None,
        }
        client.create_channel.side_effect = RuntimeError("500 Server Error")
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            await _commit_and_wait(async_client, {
                "operations": [{
                    "type": "createChannel",
                    "tempId": -1,
                    "name": "Ghost",
                    "logoUrl": "http://logos/ghost.png",
                }],
                "continueOnError": True,
            })

        client.create_logo.assert_not_awaited()
        assert not [
            r for r in _entity_rows(journal_double)
            if r["action_type"] == "logo_create"
        ]


# --------------------------------------------------------------------------
# F5 — a lookup that failed must not accuse an operation
# --------------------------------------------------------------------------

class TestAFailedLookupNeverAccusesAnOperation:

    @pytest.mark.asyncio
    async def test_a_failed_channel_lookup_does_not_report_a_missing_channel(
        self, async_client
    ):
        """Profile 3 and channel 7 both exist; the channel page read times out.

        Round 2 gave the profile lookup this guard and left the channel lookup
        reading its own emptiness as proof of absence. With
        ``continueOnError=false`` the whole run is then refused on the
        deliberately traceless pre-execution path, so the operator is told
        their channel does not exist and nothing at all is written.
        """
        client = _base_client()
        client.get_channels.side_effect = RuntimeError("upstream read timed out")
        journal_double = _journal_double()

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            data = await _commit_and_wait(async_client, {
                "operations": [{
                    "type": "setProfileMembership",
                    "profileId": 3,
                    "channelId": 7,
                    "enabled": True,
                }],
                "continueOnError": False,
            })

        assert data["validationIssues"] == []
        assert data["validationPassed"] is True
        assert data["operationsApplied"] == 1
        client.update_profile_channel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_successful_channel_lookup_still_reports_a_missing_channel(
        self, async_client
    ):
        """The guard must not blind the check it is guarding. PIN."""
        client = _base_client()
        client.get_channels.return_value = {
            "results": [{"id": 9, "name": "Nine"}], "count": 1, "next": None,
        }

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", _journal_double()):
            data = await _commit_and_wait(async_client, {
                "operations": [{
                    "type": "setProfileMembership",
                    "profileId": 3,
                    "channelId": 7,
                    "enabled": True,
                }],
                "continueOnError": False,
            })

        assert data["validationPassed"] is False
        assert [i["type"] for i in data["validationIssues"]] == ["missing_channel"]

    @pytest.mark.asyncio
    async def test_a_failed_stream_lookup_does_not_report_a_missing_stream(
        self, async_client
    ):
        """The same defect one lookup over, found by stating the invariant."""
        client = _base_client()
        client.get_channels.return_value = {
            "results": [{"id": 7, "name": "Seven", "streams": []}],
            "count": 1,
            "next": None,
        }
        client.get_streams_by_ids.side_effect = RuntimeError("upstream read timed out")
        client.get_channel.return_value = {"id": 7, "name": "Seven", "streams": []}

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", _journal_double()):
            data = await _commit_and_wait(async_client, {
                "operations": [{
                    "type": "addStreamToChannel", "channelId": 7, "streamId": 4,
                }],
                "continueOnError": False,
            })

        assert data["validationIssues"] == []
        assert data["validationPassed"] is True
        assert data["operationsApplied"] == 1

    @pytest.mark.asyncio
    async def test_a_successful_stream_lookup_still_reports_a_missing_stream(
        self, async_client
    ):
        """PIN, the counterpart of the channel one above."""
        client = _base_client()
        client.get_channels.return_value = {
            "results": [{"id": 7, "name": "Seven", "streams": []}],
            "count": 1,
            "next": None,
        }
        client.get_streams_by_ids.return_value = []

        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal", _journal_double()):
            data = await _commit_and_wait(async_client, {
                "operations": [{
                    "type": "addStreamToChannel", "channelId": 7, "streamId": 4,
                }],
                "continueOnError": False,
            })

        assert data["validationPassed"] is False
        assert [i["type"] for i in data["validationIssues"]] == ["missing_stream"]
