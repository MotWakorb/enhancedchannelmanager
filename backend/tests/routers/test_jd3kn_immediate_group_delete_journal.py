"""The immediate delete-group path leaves the same trail the bulk path does.

Bead ``enhancedchannelmanager-jd3kn``. ``DELETE /api/channel-groups/{id}`` — the
immediate, non-Edit-Mode path fixed under bead ``…-auocn`` — wrote NO journal row
at all. Not for the channels it reparents, not for the deletion. The bulk-commit
path writes both, one update row per reparented channel plus the ``group_delete``
row, all under the run's batch id, because ``…-kz089`` round 3 made the journal
row a required argument of recording a landed write.

So the same operator-visible action produced a full trail through one route and
silence through the other — and the silent route is the one the MCP
``delete_channel_group`` tool and any direct API client take, since the Edit Mode
UI menu for it is currently gated off.
``docs/user_guide/channels-streams/the-journal.md`` tells operators the journal
is how they trace a channel's history by name; for this action through this
route, it was not.

WHY THE CALLBACK ALONE WOULD NOT HAVE DONE IT. ``reparent_group_channels`` grew
an ``on_channel_moved`` hook for exactly this shape, and wiring only that would
produce HALF a trail: rows for the moved channels and nothing for the delete,
which reads as a completed move with no explanation — arguably worse than
silence. The properties pinned here are the whole discipline, not the hookup:

1. Every channel that MOVES gets a row, on every exit, including the exits where
   the delete never happens and where the request ends in a 4xx or 5xx.
2. The delete gets its own row, and only when it actually landed.
3. The moves and the delete are correlated, by one batch id, as the bulk path
   correlates them.
4. A failure to WRITE those rows is reported to the caller rather than absorbed.
5. A delete that fails after moves have landed says so, rather than reporting a
   bare failure against a precondition that has already silently changed.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _upstream_400(body: str) -> httpx.HTTPStatusError:
    """The shape ``dispatcharr_client.delete_channel_group`` really raises."""
    request = httpx.Request("DELETE", "http://dispatcharr/api/channels/groups/42/")
    response = httpx.Response(400, text=body, request=request)
    return httpx.HTTPStatusError("400", request=request, response=response)


def _client(members=((10, "Ten"), (11, "Eleven"))):
    """Group 42 holds ``members``; 'Default Group' (id 1) is the move target."""
    client = AsyncMock()
    client.get_all_m3u_group_settings.return_value = {}
    client.get_channel_groups.return_value = [{"id": 1, "name": "Default Group"}]
    client.get_channels.return_value = {
        "results": [
            {"id": cid, "name": name, "channel_group_id": 42}
            for cid, name in members
        ],
        "count": len(members),
        "next": None,
    }

    async def _get_channel(channel_id):
        return {"id": channel_id, "channel_group_id": 42}

    client.get_channel.side_effect = _get_channel
    client.update_channel.return_value = None
    client.delete_channel_group.return_value = None
    return client


def _journal_double():
    double = MagicMock()
    double.log_entries.return_value = True
    double.log_entry.return_value = MagicMock()
    return double


def _rows(journal_double):
    """Every row handed to the journal, whether batched or retried per row."""
    rows = []
    for call in journal_double.log_entries.call_args_list:
        rows.extend(call.args[0])
    for call in journal_double.log_entry.call_args_list:
        rows.append(call.kwargs)
    return rows


class TestTheImmediateDeleteWritesTheWholeTrail:

    @pytest.mark.asyncio
    async def test_the_happy_path_journals_every_move_and_the_delete(
        self, async_client
    ):
        """The bulk path's shape, on the route an MCP client actually takes."""
        client = _client()
        journal_double = _journal_double()

        with patch("routers.channel_groups.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            response = await async_client.delete("/api/channel-groups/42")

        assert response.status_code == 200, response.text
        assert response.json()["channels_moved"] == 2

        rows = _rows(journal_double)
        assert sorted(r["entity_id"] for r in rows) == [10, 11, 42]
        group_rows = [r for r in rows if r["action_type"] == "group_delete"]
        assert len(group_rows) == 1
        assert group_rows[0]["entity_id"] == 42

    @pytest.mark.asyncio
    async def test_the_moves_and_the_delete_share_one_batch_id(self, async_client):
        """Correlated, as the bulk path correlates them.

        Without this an operator reading the journal sees three unrelated rows
        and has to infer from timestamps that they were one action.
        """
        client = _client()
        journal_double = _journal_double()

        with patch("routers.channel_groups.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            response = await async_client.delete("/api/channel-groups/42")

        assert response.status_code == 200, response.text
        rows = _rows(journal_double)
        batch_ids = {r.get("batch_id") for r in rows}
        assert len(batch_ids) == 1, rows
        assert batch_ids != {None}

    @pytest.mark.asyncio
    async def test_a_moved_channels_row_names_where_it_went(self, async_client):
        client = _client()
        journal_double = _journal_double()

        with patch("routers.channel_groups.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            await async_client.delete("/api/channel-groups/42")

        moved = [r for r in _rows(journal_double) if r["entity_id"] == 10]
        assert len(moved) == 1
        assert moved[0]["before_value"]["channel_group_id"] == 42
        assert moved[0]["after_value"]["channel_group_id"] == 1
        # By NAME, which is how the operator guide says a channel is traced.
        assert moved[0]["entity_name"] == "Ten"

    @pytest.mark.asyncio
    async def test_an_empty_group_journals_the_delete_and_nothing_else(
        self, async_client
    ):
        client = _client(members=())
        journal_double = _journal_double()

        with patch("routers.channel_groups.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            response = await async_client.delete("/api/channel-groups/42")

        assert response.status_code == 200, response.text
        rows = _rows(journal_double)
        assert [r["action_type"] for r in rows] == ["group_delete"]


class TestARowSurvivesEveryExit:
    """Invariant 1, which the callback hookup on its own would not give.

    A move that landed is a fact whatever happens next, and "next" here
    includes the request ending as a 4xx or a 5xx.
    """

    @pytest.mark.asyncio
    async def test_a_reparent_that_fails_partway_still_journals_what_moved(
        self, async_client
    ):
        """Channel 10 moves; channel 11 raises. Ten is genuinely elsewhere now."""
        client = _client()
        client.update_channel.side_effect = [
            None,
            RuntimeError("500 Server Error from Dispatcharr"),
        ]
        journal_double = _journal_double()

        with patch("routers.channel_groups.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            response = await async_client.delete("/api/channel-groups/42")

        assert response.status_code >= 400
        rows = _rows(journal_double)
        moved = [r for r in rows if r["entity_id"] == 10]
        assert len(moved) == 1, rows
        # The group was never deleted, so nothing may claim it was.
        assert not [r for r in rows if r["action_type"] == "group_delete"]

    @pytest.mark.asyncio
    async def test_a_failed_delete_after_landed_moves_journals_the_moves(
        self, async_client
    ):
        """Both channels move; the delete then fails. The moves stay moved."""
        client = _client()
        client.delete_channel_group.side_effect = RuntimeError(
            "400 Cannot delete group with associated channels"
        )
        journal_double = _journal_double()

        with patch("routers.channel_groups.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            response = await async_client.delete("/api/channel-groups/42")

        assert response.status_code >= 400
        rows = _rows(journal_double)
        assert sorted(r["entity_id"] for r in rows) == [10, 11]
        assert not [r for r in rows if r["action_type"] == "group_delete"]

    @pytest.mark.asyncio
    async def test_a_failed_delete_tells_the_caller_the_channels_moved(
        self, async_client
    ):
        """Invariant 5, and the same family as the bulk envelope's
        ``operationsPartiallyApplied``.

        A bare "cannot delete group" sends the operator back to a group whose
        membership has already silently changed under them.

        The upstream failure is a real ``httpx.HTTPStatusError``, which is the
        shape ``dispatcharr_client.delete_channel_group`` actually raises — it
        calls ``raise_for_status()``. A hand-built ``RuntimeError`` would not
        map to a 4xx, and this assertion would then be pinning the 500 path by
        accident (where ``main.sanitized_http_exception_handler`` replaces every
        detail with "Internal server error" on purpose).
        """
        client = _client()
        client.delete_channel_group.side_effect = _upstream_400(
            '{"error":"Cannot delete group with associated channels"}'
        )
        journal_double = _journal_double()

        with patch("routers.channel_groups.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            response = await async_client.delete("/api/channel-groups/42")

        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        assert "2" in detail
        assert "moved" in detail.lower()
        assert "Default Group" in detail
        # The upstream reason survives alongside the advisory — it is still the
        # thing the operator has to act on.
        assert "Cannot delete group" in detail

    @pytest.mark.asyncio
    async def test_a_server_fault_says_it_in_the_log_since_the_body_cannot(
        self, async_client
    ):
        """Invariant 5's other half, where there is no body to carry it.

        ``main.sanitized_http_exception_handler`` replaces the detail of EVERY
        500 to keep internals off the wire, so a genuine server fault cannot
        carry the advisory to the caller. It is logged at ERROR instead —
        raised somewhere a human will see it — rather than dropped because the
        usual channel was unavailable.
        """
        client = _client()
        client.delete_channel_group.side_effect = RuntimeError("connection reset")
        journal_double = _journal_double()

        with patch("routers.channel_groups.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double), \
             patch("routers.channel_groups.logger") as log:
            response = await async_client.delete("/api/channel-groups/42")

        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error"
        logged = " ".join(
            str(call.args) for call in log.error.call_args_list
        )
        assert "42" in logged
        assert "moved" in logged.lower()

    @pytest.mark.asyncio
    async def test_a_refused_delete_that_moved_nothing_says_nothing_moved(
        self, async_client
    ):
        """The advisory must be able to be ABSENT.

        A group with nowhere to move its channels is refused BEFORE any write,
        so nothing may suggest the operator has anything to reconcile.
        """
        client = _client()
        client.get_channel_groups.return_value = []  # no "Default Group"
        journal_double = _journal_double()

        with patch("routers.channel_groups.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            response = await async_client.delete("/api/channel-groups/42")

        assert response.status_code == 400, response.text
        assert "had already been moved" not in response.json()["detail"]
        assert _rows(journal_double) == []
        client.update_channel.assert_not_awaited()


class TestAFailedJournalWriteReachesTheCaller:
    """Invariant 4. The same advisory the bulk envelope and the PATCH carry.

    ``journal.log_entries`` reports failure by returning ``False`` and
    ``journal.log_entry`` by returning ``None``; neither raises. A path that
    discards both cannot tell a journalled delete from an unjournalled one.
    """

    @pytest.mark.asyncio
    async def test_an_unwritable_journal_is_reported_on_the_200(self, async_client):
        client = _client()
        journal_double = _journal_double()
        journal_double.log_entries.return_value = False
        journal_double.log_entry.return_value = None

        with patch("routers.channel_groups.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            response = await async_client.delete("/api/channel-groups/42")

        # The delete LANDED. Reporting a failure to a caller whose change
        # already applied is what makes an integrator retry it.
        assert response.status_code == 200, response.text
        client.delete_channel_group.assert_awaited_once_with(42)
        assert response.json()["journalRowsUnwritten"] == 3

    @pytest.mark.asyncio
    async def test_a_writable_journal_reports_zero_rather_than_omitting_the_key(
        self, async_client
    ):
        """Always present, so a caller checks the number rather than probing."""
        client = _client()
        journal_double = _journal_double()

        with patch("routers.channel_groups.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            response = await async_client.delete("/api/channel-groups/42")

        assert response.status_code == 200, response.text
        assert response.json()["journalRowsUnwritten"] == 0

    @pytest.mark.asyncio
    async def test_a_batch_failure_is_retried_row_by_row(self, async_client):
        """This path reuses the shared writer, not a second implementation."""
        client = _client()
        journal_double = _journal_double()
        journal_double.log_entries.return_value = False
        journal_double.log_entry.return_value = MagicMock()

        with patch("routers.channel_groups.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            response = await async_client.delete("/api/channel-groups/42")

        assert response.status_code == 200, response.text
        assert response.json()["journalRowsUnwritten"] == 0
        assert journal_double.log_entries.call_count == 1
        assert journal_double.log_entry.call_count == 3

    @pytest.mark.asyncio
    async def test_the_hidden_path_is_unaffected(self, async_client):
        """An M3U-synced group is HIDDEN, not deleted — no upstream mutation."""
        client = _client()
        client.get_all_m3u_group_settings.return_value = {42: {"name": "Sports"}}
        client.get_channel_groups.return_value = [{"id": 42, "name": "Sports"}]
        journal_double = _journal_double()

        with patch("routers.channel_groups.get_client", return_value=client), \
             patch("routers.channels.journal", journal_double):
            response = await async_client.delete("/api/channel-groups/42")

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "hidden"
        client.delete_channel_group.assert_not_awaited()
