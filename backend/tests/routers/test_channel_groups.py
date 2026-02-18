"""
Unit tests for channel group endpoints.

Tests: 10 channel-group endpoints covering group CRUD, hide/restore,
       orphaned detection/deletion, auto-created detection, and with-streams.
Mocks: get_client() for Dispatcharr API, get_session() via conftest for HiddenChannelGroup.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestDeleteChannelGroup:
    """Tests for DELETE /api/channel-groups/{group_id}."""

    @pytest.mark.asyncio
    async def test_deletes_group_without_m3u(self, async_client, test_session):
        """Deletes group when no M3U sync active."""
        mock_client = AsyncMock()
        mock_client.get_all_m3u_group_settings.return_value = {}
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
