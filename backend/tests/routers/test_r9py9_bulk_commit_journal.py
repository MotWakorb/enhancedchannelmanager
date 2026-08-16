"""Per-channel journal rows for the Edit Mode bulk-commit path
(bead enhancedchannelmanager-r9py9).

Filtering the Journal to the Channel category after an Edit Mode session used
to show nothing but ``Bulk Commit`` summary rows: "Applied N operations in bulk
commit", entity ``Bulk Commit``, an AFTER block of counters, and no channel
name anywhere. A channel's history could not be traced by name, which is the
headline task of ``docs/user_guide/channels-streams/the-journal.md``.

The capability was never missing. The repository's own tracked documentation
screenshot shows exactly those per-channel rows, and every one of them is
sourced AI: an MCP agent drives the SINGLE-channel endpoints
(``PATCH /api/channels/{id}``, ``POST /api/channels/{id}/add-stream`` ...), and
each of those handlers journals per entity. ``_run_bulk_commit`` talked to
Dispatcharr directly and journaled only the summary. So one write path used the
journal and the other did not.

These tests pin the two paths writing the same shape, plus the counter defect
found in the same payload (`groups_created` counted groups the run merely
RESOLVED by name, not groups it created). Two tests here are pins on behaviour
that was already correct and say so in their docstrings.
"""
from unittest.mock import AsyncMock, patch

import pytest

from routers.channels import BulkCommitRequest, _run_bulk_commit, describe_channel_update


def make_client(channels=None, groups=None, streams=None):
    """A Dispatcharr client whose catalog is `channels` (a list of dicts)."""
    channels = channels or []
    mock_client = AsyncMock()
    mock_client.get_channels.return_value = {"results": list(channels), "next": None}
    mock_client.get_streams_by_ids.side_effect = (
        lambda ids: [{"id": sid, "name": f"Stream {sid}"} for sid in ids]
        if streams is None
        else list(streams)
    )
    mock_client.get_channel_groups.return_value = groups or []
    by_id = {ch["id"]: ch for ch in channels}
    mock_client.get_channel.side_effect = lambda cid: dict(by_id.get(cid, {"id": cid}))
    mock_client.update_channel.return_value = {}
    mock_client.delete_channel.return_value = None
    return mock_client


def rows_from(mock_journal):
    """Flatten every per-entity row handed to journal.log_entries."""
    rows = []
    for call in mock_journal.log_entries.call_args_list:
        rows.extend(call.args[0] if call.args else call.kwargs["entries"])
    return rows


def summary_of(mock_journal):
    """The Bulk Commit summary row's kwargs, or None if none was written."""
    for call in mock_journal.log_entry.call_args_list:
        if call.kwargs.get("action_type") == "bulk_commit":
            return call.kwargs
    return None


async def run_ops(mock_client, operations, **request_kwargs):
    request = BulkCommitRequest(operations=operations, **request_kwargs)
    with patch("routers.channels.get_client", return_value=mock_client), \
         patch("routers.channels.journal") as mock_journal:
        result = await _run_bulk_commit(request, batch_id="batch-1")
    return result, mock_journal


class TestPerChannelRows:
    """One row per entity actually changed, named the way the operator sees it."""

    @pytest.mark.asyncio
    async def test_update_writes_a_row_carrying_the_channel_name(self):
        client = make_client([
            {"id": 7, "name": "Doctest ABC", "channel_number": 100, "tvg_id": "old.id"},
        ])
        _result, journal_mock = await run_ops(client, [
            {"type": "updateChannel", "channelId": 7, "data": {"tvg_id": None}},
        ])

        rows = rows_from(journal_mock)
        assert len(rows) == 1
        row = rows[0]
        assert row["category"] == "channel"
        assert row["action_type"] == "update"
        assert row["entity_id"] == 7
        assert row["entity_name"] == "Doctest ABC"
        # Same phrasing the single-channel PATCH handler produces, so a
        # channel's history reads identically whichever surface changed it.
        assert row["description"] == "Updated channel: cleared EPG mapping"
        assert row["before_value"] == {"tvg_id": "old.id"}
        assert row["after_value"] == {"tvg_id": None}

    @pytest.mark.asyncio
    async def test_update_that_changes_nothing_writes_no_row(self):
        client = make_client([{"id": 7, "name": "Doctest ABC", "channel_number": 100}])
        _result, journal_mock = await run_ops(client, [
            {"type": "updateChannel", "channelId": 7, "data": {"channel_number": 100}},
        ])

        assert rows_from(journal_mock) == []

    @pytest.mark.asyncio
    async def test_create_writes_a_row_naming_the_new_channel(self):
        client = make_client()
        client.create_channel.return_value = {"id": 55, "name": "Doctest NBC", "channel_number": 101}
        _result, journal_mock = await run_ops(client, [
            {"type": "createChannel", "tempId": -1, "name": "Doctest NBC", "channelNumber": 101},
        ])

        rows = rows_from(journal_mock)
        assert len(rows) == 1
        assert rows[0]["action_type"] == "create"
        assert rows[0]["entity_id"] == 55
        assert rows[0]["entity_name"] == "Doctest NBC"
        assert rows[0]["description"] == "Created channel 'Doctest NBC' with number 101"

    @pytest.mark.asyncio
    async def test_delete_writes_a_row_naming_the_removed_channel(self):
        client = make_client([{"id": 9, "name": "Gone Soon", "channel_number": 4}])
        _result, journal_mock = await run_ops(client, [
            {"type": "deleteChannel", "channelId": 9},
        ])

        rows = rows_from(journal_mock)
        assert len(rows) == 1
        assert rows[0]["action_type"] == "delete"
        assert rows[0]["entity_name"] == "Gone Soon"
        assert rows[0]["description"] == "Deleted channel 'Gone Soon'"
        assert rows[0]["before_value"] == {"name": "Gone Soon", "channel_number": 4}

    @pytest.mark.asyncio
    async def test_delete_of_an_already_gone_channel_writes_no_row(self):
        """The op still succeeds, but nothing changed, so nothing is recorded."""
        client = make_client([{"id": 9, "name": "Gone Soon", "channel_number": 4}])
        client.delete_channel.side_effect = Exception("404 not found")
        _result, journal_mock = await run_ops(client, [
            {"type": "deleteChannel", "channelId": 9},
        ])

        assert rows_from(journal_mock) == []

    @pytest.mark.asyncio
    async def test_add_stream_writes_a_row_in_the_endpoint_shape(self):
        client = make_client([{"id": 3, "name": "Doctest ABC", "streams": [11]}])
        _result, journal_mock = await run_ops(client, [
            {"type": "addStreamToChannel", "channelId": 3, "streamId": 22},
        ])

        rows = rows_from(journal_mock)
        assert len(rows) == 1
        assert rows[0]["action_type"] == "stream_add"
        assert rows[0]["description"] == "Added stream to channel 'Doctest ABC'"
        assert rows[0]["before_value"] == {"streams": [11]}
        assert rows[0]["after_value"] == {"streams": [11, 22]}

    @pytest.mark.asyncio
    async def test_add_stream_already_present_writes_no_row(self):
        client = make_client([{"id": 3, "name": "Doctest ABC", "streams": [22]}])
        _result, journal_mock = await run_ops(client, [
            {"type": "addStreamToChannel", "channelId": 3, "streamId": 22},
        ])

        assert rows_from(journal_mock) == []

    @pytest.mark.asyncio
    async def test_remove_stream_writes_a_row(self):
        client = make_client([{"id": 3, "name": "Doctest ABC", "streams": [11, 22]}])
        _result, journal_mock = await run_ops(client, [
            {"type": "removeStreamFromChannel", "channelId": 3, "streamId": 11},
        ])

        rows = rows_from(journal_mock)
        assert len(rows) == 1
        assert rows[0]["action_type"] == "stream_remove"
        assert rows[0]["description"] == "Removed stream from channel 'Doctest ABC'"

    @pytest.mark.asyncio
    async def test_reorder_streams_writes_a_row(self):
        client = make_client([{"id": 3, "name": "Doctest ABC", "streams": [11, 22]}])
        _result, journal_mock = await run_ops(client, [
            {"type": "reorderChannelStreams", "channelId": 3, "streamIds": [22, 11]},
        ])

        rows = rows_from(journal_mock)
        assert len(rows) == 1
        assert rows[0]["action_type"] == "stream_reorder"
        assert rows[0]["after_value"] == {"streams": [22, 11]}

    @pytest.mark.asyncio
    async def test_bulk_assign_numbers_writes_one_row_per_channel(self):
        """Mirrors POST /assign-numbers, the in-repo precedent: renumbering is
        N per-channel facts, not one aggregate nobody can search by name."""
        client = make_client([
            {"id": 1, "name": "Alpha", "channel_number": 50},
            {"id": 2, "name": "Bravo", "channel_number": 51},
        ])
        _result, journal_mock = await run_ops(client, [
            {"type": "bulkAssignChannelNumbers", "channelIds": [1, 2], "startingNumber": 10},
        ])

        rows = rows_from(journal_mock)
        assert [r["entity_name"] for r in rows] == ["Alpha", "Bravo"]
        assert rows[0]["description"] == "Changed channel number from 50 to 10"
        assert rows[1]["description"] == "Changed channel number from 51 to 11"

    @pytest.mark.asyncio
    async def test_a_failed_operation_contributes_no_row(self):
        client = make_client([{"id": 7, "name": "Doctest ABC", "channel_number": 1}])
        client.update_channel.side_effect = Exception("dispatcharr said no")
        result, journal_mock = await run_ops(client, [
            {"type": "updateChannel", "channelId": 7, "data": {"name": "New Name"}},
        ])

        assert result["operationsFailed"] == 1
        assert rows_from(journal_mock) == []

    @pytest.mark.asyncio
    async def test_a_later_op_names_a_channel_created_earlier_in_the_batch(self):
        client = make_client()
        client.create_channel.return_value = {"id": 55, "name": "Doctest NBC", "channel_number": 101}
        _result, journal_mock = await run_ops(client, [
            {"type": "createChannel", "tempId": -1, "name": "Doctest NBC", "channelNumber": 101},
            {"type": "updateChannel", "channelId": -1, "data": {"name": "Doctest NBC HD"}},
        ])

        rows = rows_from(journal_mock)
        assert rows[1]["action_type"] == "update"
        assert rows[1]["entity_id"] == 55
        assert rows[1]["entity_name"] == "Doctest NBC"


class TestBatchCorrelationAndVolume:
    @pytest.mark.asyncio
    async def test_every_row_carries_the_run_batch_id(self):
        client = make_client([
            {"id": 1, "name": "Alpha", "channel_number": 1},
            {"id": 2, "name": "Bravo", "channel_number": 2},
        ])
        _result, journal_mock = await run_ops(client, [
            {"type": "updateChannel", "channelId": 1, "data": {"name": "Alpha HD"}},
            {"type": "updateChannel", "channelId": 2, "data": {"name": "Bravo HD"}},
        ])

        assert {r["batch_id"] for r in rows_from(journal_mock)} == {"batch-1"}
        assert summary_of(journal_mock)["batch_id"] == "batch-1"

    @pytest.mark.asyncio
    async def test_all_rows_are_written_in_one_transaction(self):
        """A commit touching hundreds of channels must not become hundreds of
        transactions. `log_entries` is a single commit for N rows."""
        catalog = [{"id": i, "name": f"Ch {i}", "channel_number": i} for i in range(1, 51)]
        client = make_client(catalog)
        _result, journal_mock = await run_ops(client, [
            {"type": "updateChannel", "channelId": i, "data": {"name": f"Ch {i} HD"}}
            for i in range(1, 51)
        ])

        assert journal_mock.log_entries.call_count == 1
        assert len(rows_from(journal_mock)) == 50

    @pytest.mark.asyncio
    async def test_the_bulk_commit_summary_row_is_still_written(self):
        """The operator guide describes both; the summary is not replaced."""
        client = make_client([{"id": 1, "name": "Alpha", "channel_number": 1}])
        _result, journal_mock = await run_ops(client, [
            {"type": "updateChannel", "channelId": 1, "data": {"name": "Alpha HD"}},
        ])

        summary = summary_of(journal_mock)
        assert summary["entity_name"] == "Bulk Commit"
        assert summary["description"] == "Applied 1 operations in bulk commit"


class TestDryRunAndCounters:
    @pytest.mark.asyncio
    async def test_validate_only_writes_nothing_at_all(self):
        """PIN, not a fix. A dry run executes nothing and must leave no trace.
        This already held before the bead (validateOnly returns before Phase 1),
        and it is pinned because the journal writes are now the last thing
        _run_bulk_commit does, which is exactly where a future early-exit could
        start recording a commit that never happened."""
        client = make_client([{"id": 1, "name": "Alpha", "channel_number": 1}])
        _result, journal_mock = await run_ops(
            client,
            [{"type": "updateChannel", "channelId": 1, "data": {"name": "Alpha HD"}}],
            validateOnly=True,
        )

        assert journal_mock.log_entries.call_count == 0
        assert journal_mock.log_entry.call_count == 0

    @pytest.mark.asyncio
    async def test_channels_created_counts_the_channels_this_run_created(self):
        """PIN on the common case. `len(tempIdMap)` happened to agree here; the
        counter replaces it because the map misses a createChannel whose temp id
        was not negative, and because deriving a "created" count from an id map
        is what made `groups_created` wrong (red-proven below)."""
        client = make_client()
        client.create_channel.side_effect = [
            {"id": 55, "name": "A", "channel_number": 1},
            {"id": 56, "name": "B", "channel_number": 2},
        ]
        _result, journal_mock = await run_ops(client, [
            {"type": "createChannel", "tempId": -1, "name": "A", "channelNumber": 1},
            {"type": "createChannel", "tempId": -2, "name": "B", "channelNumber": 2},
        ])

        assert summary_of(journal_mock)["after_value"]["channels_created"] == 2

    @pytest.mark.asyncio
    async def test_groups_created_excludes_a_group_that_already_existed(self):
        """`groupIdMap` also collects PRE-EXISTING groups resolved by name, so
        counting it reported groups the run did not create."""
        client = make_client(groups=[{"id": 4, "name": "Doctest Locals"}])
        client.create_channel_group.side_effect = Exception("400 already exists")
        request = BulkCommitRequest(
            operations=[], groupsToCreate=[{"name": "Doctest Locals"}]
        )
        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal") as journal_mock:
            result = await _run_bulk_commit(request, batch_id="batch-1")

        assert result["groupIdMap"] == {"Doctest Locals": 4}
        assert summary_of(journal_mock)["after_value"]["groups_created"] == 0
        assert rows_from(journal_mock) == []

    @pytest.mark.asyncio
    async def test_a_genuinely_new_group_counts_and_gets_its_own_row(self):
        client = make_client()
        client.create_channel_group.return_value = {"id": 12, "name": "Doctest Locals"}
        request = BulkCommitRequest(
            operations=[], groupsToCreate=[{"name": "Doctest Locals"}]
        )
        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal") as journal_mock:
            await _run_bulk_commit(request, batch_id="batch-1")

        assert summary_of(journal_mock)["after_value"]["groups_created"] == 1
        rows = rows_from(journal_mock)
        assert len(rows) == 1
        assert rows[0]["description"] == "Created channel group 'Doctest Locals'"


class TestDescribeChannelUpdate:
    """The differ both write paths share."""

    def test_absent_field_is_not_a_change(self):
        changes, before, after = describe_channel_update({"name": "A"}, {})
        assert (changes, before, after) == ([], {}, {})

    def test_equal_value_is_not_a_change(self):
        changes, _b, _a = describe_channel_update({"name": "A"}, {"name": "A"})
        assert changes == []

    def test_unknown_before_treats_every_supplied_field_as_new(self):
        changes, before, after = describe_channel_update({}, {"name": "A"})
        assert changes == ["name to 'A'"]
        assert before == {"name": None}
        assert after == {"name": "A"}

    def test_clearing_a_field_uses_the_cleared_phrase(self):
        changes, _b, _a = describe_channel_update({"logo_id": 3}, {"logo_id": None})
        assert changes == ["cleared logo"]

    def test_describes_the_edit_mode_fields_the_patch_handler_used_to_drop(self):
        """Edit Mode stages group moves, EPG links, stream profiles and
        Gracenote ids. The old inline differ knew only four fields, so those
        edits produced "no changes detected" and no row at all."""
        changes, _b, _a = describe_channel_update(
            {"channel_group_id": 1, "epg_data_id": None, "stream_profile_id": None,
             "tvc_guide_stationid": None},
            {"channel_group_id": 2, "epg_data_id": 9, "stream_profile_id": 4,
             "tvc_guide_stationid": "12345"},
        )
        assert changes == [
            "group to 2",
            "EPG source to 9",
            "stream profile to 4",
            "Gracenote ID to '12345'",
        ]
