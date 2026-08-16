"""
Unit tests for channel group endpoints.

Tests: 10 channel-group endpoints covering group CRUD, hide/restore,
       orphaned detection/deletion, auto-created detection, and with-streams.
Mocks: get_client() for Dispatcharr API, get_session() via conftest for HiddenChannelGroup.
"""
import httpx
import pytest
from unittest.mock import AsyncMock, patch

from models import HiddenChannelGroup


def _create_hidden_group(session, group_id, group_name="Hidden Group"):
    """Insert a hidden channel group record."""
    record = HiddenChannelGroup(group_id=group_id, group_name=group_name)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


class TestGetChannelGroups:
    """Tests for GET /api/channel-groups."""

    @pytest.mark.asyncio
    async def test_returns_groups(self, async_client, test_session):
        """Returns channel groups excluding hidden ones."""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "Sports"},
            {"id": 2, "name": "News"},
        ]
        mock_client.get_all_m3u_group_settings.return_value = {}

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-groups")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_excludes_hidden(self, async_client, test_session):
        """Excludes hidden groups from results."""
        _create_hidden_group(test_session, group_id=2, group_name="News")

        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "Sports"},
            {"id": 2, "name": "News"},
        ]
        mock_client.get_all_m3u_group_settings.return_value = {}

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-groups")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Sports"

    @pytest.mark.asyncio
    async def test_prunes_stale_hidden_records_after_id_reassignment(self, async_client, test_session):
        """A hidden_channel_groups record whose stored name no longer matches
        Dispatcharr's current group at that ID is stale (e.g. after moving to
        a new Dispatcharr server) — it must be pruned and the live group must
        be returned, not filtered out."""
        # Old server hid group ID 2 named "News".
        # On the new server, ID 2 belongs to "My Teams" instead.
        _create_hidden_group(test_session, group_id=2, group_name="News")

        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "Sports"},
            {"id": 2, "name": "My Teams"},
        ]
        mock_client.get_all_m3u_group_settings.return_value = {}

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-groups")

        assert response.status_code == 200
        data = response.json()
        names = sorted(g["name"] for g in data)
        assert names == ["My Teams", "Sports"]

        # Stale record was pruned from the DB.
        test_session.expire_all()
        assert test_session.query(HiddenChannelGroup).filter_by(group_id=2).first() is None

    @pytest.mark.asyncio
    async def test_adds_auto_sync_flag(self, async_client, test_session):
        """Adds is_auto_sync flag to groups with auto_channel_sync."""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "Sports"},
        ]
        mock_client.get_all_m3u_group_settings.return_value = {
            1: {"auto_channel_sync": True},
        }

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-groups")

        assert response.status_code == 200
        data = response.json()
        assert data[0]["is_auto_sync"] is True


class TestCreateChannelGroup:
    """Tests for POST /api/channel-groups."""

    @pytest.mark.asyncio
    async def test_creates_group(self, async_client):
        """Creates a new channel group."""
        mock_client = AsyncMock()
        mock_client.create_channel_group.return_value = {"id": 3, "name": "Movies"}

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.post("/api/channel-groups", json={
                "name": "Movies",
            })

        assert response.status_code == 200
        assert response.json()["name"] == "Movies"

    @pytest.mark.asyncio
    async def test_returns_existing_on_duplicate(self, async_client):
        """Returns existing group when creating a duplicate."""
        mock_client = AsyncMock()
        mock_client.create_channel_group.side_effect = Exception("400: group already exists")
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "Sports"},
        ]

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.post("/api/channel-groups", json={
                "name": "Sports",
            })

        assert response.status_code == 200
        assert response.json()["name"] == "Sports"


class TestUpdateChannelGroup:
    """Tests for PATCH /api/channel-groups/{group_id}."""

    @pytest.mark.asyncio
    async def test_updates_group(self, async_client):
        """Updates a channel group."""
        mock_client = AsyncMock()
        mock_client.update_channel_group.return_value = {"id": 1, "name": "Updated"}

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.patch("/api/channel-groups/1", json={
                "name": "Updated",
            })

        assert response.status_code == 200
        mock_client.update_channel_group.assert_called_once_with(1, {"name": "Updated"})


class _DispatcharrNonNullGroupError(Exception):
    """What Dispatcharr 0.28.2 returns for ``channel_group_id: null``.

    Measured against the live drill instance on 2026-08-09::

        PATCH /api/channels/channels/1/ {"channel_group_id": null}
          -> 400 {"channel_group_id":["This field may not be null."]}
        PATCH /api/channels/channels/1/ {"channel_group_id": 378}
          -> 200  (the channel really moved)

    A bare ``AsyncMock`` accepts anything, which is how the first cut of the
    …-ayfn9 reparent passed every unit test and then 400'd on the very first
    live Delete Group. Any double standing in for ``update_channel`` in this
    module enforces the constraint the real API enforces. Deliberately the same
    double as ``tests/routers/test_channels.py`` uses for the other delete path.
    """


def _channel_patch_double(recorder: list | None = None):
    """An ``update_channel`` double that rejects a null ``channel_group_id``."""

    def _patch(channel_id: int, data: dict):
        if "channel_group_id" in data and data["channel_group_id"] is None:
            raise _DispatcharrNonNullGroupError(
                "Client error '400 Bad Request' for url "
                "'http://dispatcharr:9191/api/channels/channels/%s/': "
                '{"channel_group_id":["This field may not be null."]}' % channel_id
            )
        if recorder is not None:
            recorder.append(("patch", channel_id, data))
        return {"id": channel_id, **data}

    return _patch


def _channel_page(channels, next_page=None):
    return {"results": channels, "count": len(channels), "next": next_page}


# Dispatcharr's baseline group, resolved BY NAME by the production code, so the
# fixtures give it a non-obvious id: a test that passes only because the id
# happens to be 1 proves nothing.
_DEFAULT_GROUP = {"id": 42, "name": "Default Group"}


class TestDeleteChannelGroup:
    """Tests for DELETE /api/channel-groups/{group_id}."""

    @pytest.mark.asyncio
    async def test_deletes_group_without_m3u(self, async_client, test_session):
        """Deletes group when no M3U sync active."""
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {}
        mock_client.get_channels.return_value = _channel_page([])
        mock_client.get_channel_groups.return_value = [_DEFAULT_GROUP]
        mock_client.delete_channel_group.return_value = None

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-groups/1")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
        mock_client.delete_channel_group.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_hides_group_with_m3u(self, async_client, test_session):
        """Hides group instead of deleting when M3U sync active."""
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {
            1: {"auto_channel_sync": True},
        }
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "Sports"},
        ]

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-groups/1")

        assert response.status_code == 200
        assert response.json()["status"] == "hidden"

        # Verify hidden record was created in DB
        hidden = test_session.query(HiddenChannelGroup).filter_by(group_id=1).first()
        assert hidden is not None
        assert hidden.group_name == "Sports"

    @pytest.mark.asyncio
    async def test_upstream_4xx_surfaces_400_not_500(self, async_client, test_session):
        """A Dispatcharr 4xx on delete (e.g. protected/non-empty group) maps to
        400 with the upstream detail, not an opaque 500 (bd-1wq7z.22).

        delete_channel_group raises httpx.HTTPStatusError via raise_for_status().
        """
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {}
        mock_client.get_channels.return_value = _channel_page([])
        mock_client.get_channel_groups.return_value = [_DEFAULT_GROUP]
        request = httpx.Request("DELETE", "http://disp/api/channels/groups/1/")
        upstream = httpx.Response(
            400, request=request,
            text='{"detail": "Cannot delete group with channels."}',
        )
        mock_client.delete_channel_group.side_effect = httpx.HTTPStatusError(
            "400 Client Error", request=request, response=upstream
        )

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-groups/1")

        assert response.status_code == 400
        assert "Cannot delete group" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_genuine_server_error_still_500(self, async_client, test_session):
        """A non-upstream error on delete stays a 500 (bd-1wq7z.22)."""
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {}
        mock_client.get_channels.return_value = _channel_page([])
        mock_client.get_channel_groups.return_value = [_DEFAULT_GROUP]
        mock_client.delete_channel_group.side_effect = RuntimeError("boom")

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-groups/1")

        assert response.status_code == 500


class TestDeleteChannelGroupReparentsMembers:
    """The immediate delete keeps the dialog's promise (bead …-auocn).

    Split out of …-ayfn9, which fixed only the Edit Mode bulk-commit path. Both
    paths reach the SAME confirm dialog, which tells the operator "This group
    contains N channels. The channels will be moved to 'Default Group'." This
    one then issued a bare delete, which Dispatcharr refuses::

        DELETE /api/channels/groups/377/ -> 400
        {"error":"Cannot delete group with associated channels"}

    Unlike …-ayfn9 the failure was visible — ``upstream_http_exception`` surfaces
    the 400 — but it was still unachievable: deleting a non-empty group from this
    path always failed, with no in-product way to succeed short of emptying the
    group by hand. These cases pin the same semantics as
    ``tests/routers/test_channels.py::TestBulkCommitDeleteChannelGroup``, because
    the point of the bead is that the two paths cannot be allowed to drift.
    """

    @pytest.mark.asyncio
    async def test_members_are_reparented_to_the_default_group_before_the_delete(
        self, async_client, test_session
    ):
        """Members move to a REAL group id, THEN the group is deleted."""
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {}
        mock_client.get_channels.return_value = _channel_page([
            {"id": 11, "name": "PBS 1", "channel_group_id": 377},
            {"id": 12, "name": "PBS 2", "channel_group_id": 377},
            {"id": 13, "name": "Elsewhere", "channel_group_id": 999},
        ])
        mock_client.get_channel_groups.return_value = [
            {"id": 377, "name": "Drill17 PBS West"},
            _DEFAULT_GROUP,
        ]
        calls: list = []
        mock_client.update_channel.side_effect = _channel_patch_double(calls)
        mock_client.delete_channel_group.side_effect = (
            lambda gid: calls.append(("delete", gid, None))
        )

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-groups/377")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
        assert response.json()["channels_moved"] == 2
        # Both members moved to the RESOLVED id (42, not the literal 1 and
        # emphatically not None), the outsider was never touched, and the delete
        # came last.
        assert calls == [
            ("patch", 11, {"channel_group_id": 42}),
            ("patch", 12, {"channel_group_id": 42}),
            ("delete", 377, None),
        ]

    @pytest.mark.asyncio
    async def test_the_target_group_is_resolved_by_name_not_by_id(
        self, async_client, test_session
    ):
        """Nothing may depend on the baseline group's id being 1."""
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {}
        mock_client.get_channels.return_value = _channel_page([
            {"id": 11, "name": "PBS 1", "channel_group_id": 377},
        ])
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "Some Operator's Own Group"},
            {"id": 907, "name": "  default group  "},  # trimmed + case-insensitive
        ]
        mock_client.update_channel.side_effect = _channel_patch_double()

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-groups/377")

        assert response.status_code == 200
        mock_client.update_channel.assert_awaited_once_with(11, {"channel_group_id": 907})

    @pytest.mark.asyncio
    async def test_a_null_channel_group_id_is_never_sent(self, async_client, test_session):
        """The regression guard: the double 400s on null exactly as the API does."""
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {}
        mock_client.get_channels.return_value = _channel_page([
            {"id": 11, "name": "PBS 1", "channel_group_id": 377},
        ])
        mock_client.get_channel_groups.return_value = [_DEFAULT_GROUP]
        mock_client.update_channel.side_effect = _channel_patch_double()

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-groups/377")

        assert response.status_code == 200
        for call in mock_client.update_channel.await_args_list:
            assert call.args[1]["channel_group_id"] is not None

    @pytest.mark.asyncio
    async def test_no_default_group_answers_400_with_a_usable_message(
        self, async_client, test_session
    ):
        """An instance without the baseline group gets a reason, not a null PATCH.

        400, not 500: the operator can act on this, and the reason is the whole
        value of the answer.
        """
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {}
        mock_client.get_channels.return_value = _channel_page([
            {"id": 11, "name": "PBS 1", "channel_group_id": 377},
        ])
        mock_client.get_channel_groups.return_value = [
            {"id": 377, "name": "Drill17 PBS West"},
        ]
        mock_client.update_channel.side_effect = _channel_patch_double()

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-groups/377")

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "Default Group" in detail
        assert "1 channel" in detail
        # Nothing was attempted upstream: no null PATCH, no doomed delete.
        mock_client.update_channel.assert_not_awaited()
        mock_client.delete_channel_group.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deleting_the_default_group_itself_answers_400(
        self, async_client, test_session
    ):
        """Moving a group's channels INTO that same group empties nothing."""
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {}
        mock_client.get_channels.return_value = _channel_page([
            {"id": 11, "name": "PBS 1", "channel_group_id": 42},
        ])
        mock_client.get_channel_groups.return_value = [_DEFAULT_GROUP]
        mock_client.update_channel.side_effect = _channel_patch_double()

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-groups/42")

        assert response.status_code == 400
        assert "Default Group" in response.json()["detail"]
        mock_client.update_channel.assert_not_awaited()
        mock_client.delete_channel_group.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_empty_group_is_deleted_without_any_patch(
        self, async_client, test_session
    ):
        """No members means no reparenting — the bare delete was always correct.

        It also means the baseline group need not exist: an empty group deletes
        cleanly on an instance that has no "Default Group" at all.
        """
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {}
        mock_client.get_channels.return_value = _channel_page([
            {"id": 13, "name": "Elsewhere", "channel_group_id": 999},
        ])
        mock_client.get_channel_groups.return_value = []
        mock_client.update_channel.side_effect = _channel_patch_double()

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-groups/378")

        assert response.status_code == 200
        assert response.json()["channels_moved"] == 0
        mock_client.update_channel.assert_not_awaited()
        mock_client.delete_channel_group.assert_awaited_once_with(378)

    @pytest.mark.asyncio
    async def test_members_are_found_across_every_page(self, async_client, test_session):
        """A member on page two is still a member. Paging stops at `next: null`."""
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {}
        pages = [
            _channel_page(
                [{"id": 11, "name": "PBS 1", "channel_group_id": 377}], next_page="page2"
            ),
            _channel_page([{"id": 12, "name": "PBS 2", "channel_group_id": 377}]),
        ]
        mock_client.get_channels.side_effect = lambda page, page_size: pages[page - 1]
        mock_client.get_channel_groups.return_value = [_DEFAULT_GROUP]
        mock_client.update_channel.side_effect = _channel_patch_double()

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-groups/377")

        assert response.status_code == 200
        assert response.json()["channels_moved"] == 2

    @pytest.mark.asyncio
    async def test_a_channel_moved_out_of_the_group_since_the_read_is_left_alone(
        self, async_client, test_session
    ):
        """Another operator's move is not overwritten by ours.

        The member list is read live, but the read is not atomic with respect to
        the writes that follow it: the remaining pages, the group resolution and
        every earlier PATCH all sit between a given channel's row being read and
        that channel being written. Another operator who moves a channel to a
        third group inside that span used to have their move silently replaced
        by ``Default Group``.

        Dispatcharr offers no conditional update to close this — the live 0.28.2
        schema declares no ``If-Match``/``ETag``/``412`` anywhere, and its Channel
        serializer carries no version or modified-at field — so the write is
        preceded by a re-read and skipped when the channel has already left. That
        is a check, not atomicity: a move landing between the re-read and the
        PATCH is still lost. What it removes is the long window, which is the one
        a real operator is exposed to.
        """
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {}
        mock_client.get_channels.return_value = _channel_page([
            {"id": 11, "name": "PBS 1", "channel_group_id": 377},
            {"id": 12, "name": "PBS 2", "channel_group_id": 377},
        ])
        mock_client.get_channel_groups.return_value = [
            {"id": 377, "name": "Drill17 PBS West"},
            _DEFAULT_GROUP,
        ]
        # Between the list read and the write loop, someone moved PBS 1 to
        # group 500. PBS 2 is where we left it.
        mock_client.get_channel.side_effect = lambda channel_id: {
            11: {"id": 11, "name": "PBS 1", "channel_group_id": 500},
            12: {"id": 12, "name": "PBS 2", "channel_group_id": 377},
        }[channel_id]
        calls: list = []
        mock_client.update_channel.side_effect = _channel_patch_double(calls)

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-groups/377")

        assert response.status_code == 200
        # PBS 1 keeps the group the other operator gave it; only PBS 2 moves,
        # and the reported count is what actually moved, not what was read.
        assert calls == [("patch", 12, {"channel_group_id": 42})]
        assert response.json()["channels_moved"] == 1

    @pytest.mark.asyncio
    async def test_an_unreadable_re_read_still_moves_the_channel(
        self, async_client, test_session
    ):
        """The guard is best-effort: when it cannot run, behaviour is unchanged.

        A transient failure on the re-read tells us nothing about where the
        channel is. Refusing the write on no evidence would leave the group
        non-empty and turn a working delete into Dispatcharr's "Cannot delete
        group with associated channels", so the guard only ever WITHHOLDS a
        write on positive evidence that the channel has moved.
        """
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {}
        mock_client.get_channels.return_value = _channel_page([
            {"id": 11, "name": "PBS 1", "channel_group_id": 377},
        ])
        mock_client.get_channel_groups.return_value = [
            {"id": 377, "name": "Drill17 PBS West"},
            _DEFAULT_GROUP,
        ]
        mock_client.get_channel.side_effect = RuntimeError("upstream hiccup")
        calls: list = []
        mock_client.update_channel.side_effect = _channel_patch_double(calls)

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-groups/377")

        assert response.status_code == 200
        assert calls == [("patch", 11, {"channel_group_id": 42})]
        assert response.json()["channels_moved"] == 1

    @pytest.mark.asyncio
    async def test_an_m3u_synced_group_is_hidden_without_touching_its_channels(
        self, async_client, test_session
    ):
        """Hiding is not deleting, so nothing is reparented on that branch."""
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {
            377: {"auto_channel_sync": True},
        }
        mock_client.get_channel_groups.return_value = [
            {"id": 377, "name": "Drill17 PBS West"},
            _DEFAULT_GROUP,
        ]
        mock_client.update_channel.side_effect = _channel_patch_double()

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-groups/377")

        assert response.status_code == 200
        assert response.json()["status"] == "hidden"
        mock_client.update_channel.assert_not_awaited()
        mock_client.get_channels.assert_not_awaited()
        mock_client.delete_channel_group.assert_not_awaited()


class TestRestoreChannelGroup:
    """Tests for POST /api/channel-groups/{group_id}/restore."""

    @pytest.mark.asyncio
    async def test_restores_hidden_group(self, async_client, test_session):
        """Restores a hidden group."""
        _create_hidden_group(test_session, group_id=5, group_name="Sports")

        response = await async_client.post("/api/channel-groups/5/restore")

        assert response.status_code == 200
        assert response.json()["status"] == "restored"

        # Verify removed from hidden list
        hidden = test_session.query(HiddenChannelGroup).filter_by(group_id=5).first()
        assert hidden is None

    @pytest.mark.asyncio
    async def test_404_for_not_hidden(self, async_client):
        """Returns 404 when group is not in hidden list."""
        response = await async_client.post("/api/channel-groups/999/restore")

        assert response.status_code == 404


class TestGetHiddenGroups:
    """Tests for GET /api/channel-groups/hidden.

    NOTE: In the monolith, this route is shadowed by
    DELETE /api/channel-groups/orphaned which is defined before it.
    The GET method works because route shadowing only affects same-method routes.
    """

    @pytest.mark.asyncio
    async def test_returns_hidden_groups(self, async_client, test_session):
        """Returns list of hidden groups."""
        _create_hidden_group(test_session, group_id=1, group_name="Sports")
        _create_hidden_group(test_session, group_id=2, group_name="News")

        response = await async_client.get("/api/channel-groups/hidden")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_returns_empty(self, async_client):
        """Returns empty list when no groups hidden."""
        response = await async_client.get("/api/channel-groups/hidden")

        assert response.status_code == 200
        assert response.json() == []


class TestGetOrphanedGroups:
    """Tests for GET /api/channel-groups/orphaned."""

    @pytest.mark.asyncio
    async def test_returns_orphaned_groups(self, async_client):
        """Returns groups with no streams, channels, or M3U association."""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "Empty Group"},
            {"id": 2, "name": "Active Group"},
        ]
        mock_client.get_all_m3u_group_settings.return_value = {}
        # No streams at all
        mock_client.get_streams.return_value = {"results": [], "count": 0}
        # One channel in group 2
        mock_client.get_channels.return_value = {
            "results": [{"id": 1, "channel_group_id": 2}],
            "count": 1,
        }

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-groups/orphaned")

        assert response.status_code == 200
        data = response.json()
        assert len(data["orphaned_groups"]) == 1
        assert data["orphaned_groups"][0]["name"] == "Empty Group"


class TestDeleteOrphanedGroups:
    """Tests for DELETE /api/channel-groups/orphaned."""

    @pytest.mark.asyncio
    async def test_deletes_orphaned(self, async_client):
        """Deletes orphaned channel groups."""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "Empty Group"},
        ]
        mock_client.get_all_m3u_group_settings.return_value = {}
        mock_client.get_streams.return_value = {"results": [], "count": 0}
        mock_client.get_channels.return_value = {"results": [], "count": 0}
        mock_client.delete_channel_group.return_value = None

        with patch("routers.channel_groups.get_client", return_value=mock_client), \
             patch("routers.channel_groups.journal"):
            response = await async_client.request("DELETE", "/api/channel-groups/orphaned", json={"group_ids": [1]})

        assert response.status_code == 200


class TestGetAutoCreatedGroups:
    """Tests for GET /api/channel-groups/auto-created."""

    @pytest.mark.asyncio
    async def test_returns_groups_with_auto_created(self, async_client):
        """Returns groups containing auto-created channels."""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "Sports"},
        ]
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 10, "name": "ESPN", "channel_group_id": 1, "auto_created": True,
                 "channel_number": 100, "auto_created_by": 1, "auto_created_by_name": "Rule 1"},
            ],
            "next": None,
        }

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-groups/auto-created")

        assert response.status_code == 200
        data = response.json()
        assert data["total_auto_created_channels"] == 1
        assert len(data["groups"]) == 1
        assert data["groups"][0]["auto_created_count"] == 1

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_auto_created(self, async_client):
        """Returns empty when no auto-created channels exist."""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = [{"id": 1, "name": "Sports"}]
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 10, "name": "ESPN", "channel_group_id": 1, "auto_created": False},
            ],
            "next": None,
        }

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-groups/auto-created")

        assert response.status_code == 200
        data = response.json()
        assert data["total_auto_created_channels"] == 0


class TestGetGroupsWithStreams:
    """Tests for GET /api/channel-groups/with-streams."""

    @pytest.mark.asyncio
    async def test_returns_groups_with_streams(self, async_client):
        """Returns groups that have channels with streams."""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "Sports"},
            {"id": 2, "name": "Empty"},
        ]
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 10, "name": "ESPN", "channel_group_id": 1, "streams": [100, 101],
                 "channel_number": 1, "auto_created": False},
            ],
            "next": None,
        }

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-groups/with-streams")

        assert response.status_code == 200
        data = response.json()
        # Should have at least the group with streams
        assert len(data) >= 1
