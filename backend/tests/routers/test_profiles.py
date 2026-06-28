"""
Unit tests for stream profile and channel profile endpoints.

Tests: GET/POST /api/stream-profiles, GET/POST /api/channel-profiles,
       GET/PATCH/DELETE /api/channel-profiles/{id},
       PATCH /api/channel-profiles/{id}/channels/bulk-update,
       PATCH /api/channel-profiles/{id}/channels/{channel_id}
Mocks: get_client() to isolate from Dispatcharr.
"""
import httpx
import pytest
from unittest.mock import AsyncMock, patch


class TestStreamProfiles:
    """Tests for stream profile endpoints."""

    @pytest.mark.asyncio
    async def test_get_stream_profiles(self, async_client):
        """GET /api/stream-profiles returns profiles from client."""
        mock_client = AsyncMock()
        mock_client.get_stream_profiles.return_value = [
            {"id": 1, "name": "Direct"},
            {"id": 2, "name": "FFmpeg"},
        ]

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-profiles")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["name"] == "Direct"

    @pytest.mark.asyncio
    async def test_get_stream_profiles_client_error(self, async_client):
        """GET /api/stream-profiles returns 500 on client error."""
        mock_client = AsyncMock()
        mock_client.get_stream_profiles.side_effect = Exception("Connection refused")

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-profiles")

        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_create_stream_profile(self, async_client):
        """POST /api/stream-profiles creates a profile via client."""
        mock_client = AsyncMock()
        mock_client.create_stream_profile.return_value = {
            "id": 3, "name": "New Profile",
        }

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/stream-profiles",
                json={"name": "New Profile", "command": "ffmpeg"},
            )

        assert response.status_code == 200
        assert response.json()["name"] == "New Profile"
        mock_client.create_stream_profile.assert_called_once()


class TestGetChannelProfiles:
    """Tests for GET /api/channel-profiles."""

    @pytest.mark.asyncio
    async def test_returns_profiles(self, async_client):
        """Returns list of channel profiles forwarded verbatim from client."""
        mock_client = AsyncMock()
        mock_client.get_channel_profiles.return_value = [
            {"id": 1, "name": "Default"},
            {"id": 2, "name": "Kids"},
        ]

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-profiles")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0] == {"id": 1, "name": "Default"}
        assert data[1] == {"id": 2, "name": "Kids"}
        mock_client.get_channel_profiles.assert_called_once()

    @pytest.mark.asyncio
    async def test_client_error(self, async_client):
        """Returns 500 on client error."""
        mock_client = AsyncMock()
        mock_client.get_channel_profiles.side_effect = Exception("Timeout")

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-profiles")

        assert response.status_code == 500


class TestCreateChannelProfile:
    """Tests for POST /api/channel-profiles."""

    @pytest.mark.asyncio
    async def test_creates_profile(self, async_client):
        """Creates a new channel profile, forwarding the client result verbatim."""
        mock_client = AsyncMock()
        mock_client.create_channel_profile.return_value = {
            "id": 3, "name": "New Profile",
        }

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channel-profiles",
                json={"name": "New Profile"},
            )

        assert response.status_code == 200
        assert response.json() == {"id": 3, "name": "New Profile"}
        mock_client.create_channel_profile.assert_called_once()


class TestGetChannelProfile:
    """Tests for GET /api/channel-profiles/{profile_id}."""

    @pytest.mark.asyncio
    async def test_returns_profile(self, async_client):
        """Returns a single channel profile."""
        mock_client = AsyncMock()
        mock_client.get_channel_profile.return_value = {
            "id": 1, "name": "Default", "channels": [],
        }

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-profiles/1")

        assert response.status_code == 200
        assert response.json()["name"] == "Default"
        mock_client.get_channel_profile.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_client_error(self, async_client):
        """Returns 500 when client raises."""
        mock_client = AsyncMock()
        mock_client.get_channel_profile.side_effect = Exception("Not found")

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-profiles/999")

        assert response.status_code == 500


class TestUpdateChannelProfile:
    """Tests for PATCH /api/channel-profiles/{profile_id}."""

    @pytest.mark.asyncio
    async def test_updates_profile(self, async_client):
        """Updates a channel profile."""
        mock_client = AsyncMock()
        mock_client.update_channel_profile.return_value = {
            "id": 1, "name": "Updated Name",
        }

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.patch(
                "/api/channel-profiles/1",
                json={"name": "Updated Name"},
            )

        assert response.status_code == 200
        mock_client.update_channel_profile.assert_called_once_with(1, {"name": "Updated Name"})


class TestDeleteChannelProfile:
    """Tests for DELETE /api/channel-profiles/{profile_id}."""

    @pytest.mark.asyncio
    async def test_deletes_profile(self, async_client):
        """Deletes a channel profile."""
        mock_client = AsyncMock()

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-profiles/1")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
        mock_client.delete_channel_profile.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_client_error(self, async_client):
        """Returns 500 when client raises."""
        mock_client = AsyncMock()
        mock_client.delete_channel_profile.side_effect = Exception("Error")

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channel-profiles/999")

        assert response.status_code == 500


class TestBulkUpdateProfileChannels:
    """Tests for PATCH /api/channel-profiles/{profile_id}/channels/bulk-update."""

    @pytest.mark.asyncio
    async def test_bulk_updates(self, async_client):
        """Bulk enables/disables channels for a profile."""
        mock_client = AsyncMock()
        mock_client.bulk_update_profile_channels.return_value = {"updated": 5}

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.patch(
                "/api/channel-profiles/1/channels/bulk-update",
                json={"channel_ids": [1, 2, 3], "enabled": True},
            )

        assert response.status_code == 200
        mock_client.bulk_update_profile_channels.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_profile_returns_404_not_500(self, async_client):
        """Bulk-updating channels on a nonexistent profile surfaces upstream 404
        as 404, not 500 (bd-lq38l.4)."""
        request = httpx.Request("PATCH", "http://disp/api/channels/profiles/999/channels/bulk-update/")
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.bulk_update_profile_channels.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.patch(
                "/api/channel-profiles/999/channels/bulk-update",
                json={"channel_ids": [1, 2, 3], "enabled": True},
            )

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_bad_channel_id_surfaces_400_not_500(self, async_client):
        """An invalid channel id is rejected upstream as 4xx -> 400 with detail,
        not an opaque 500 (bd-lq38l.4)."""
        request = httpx.Request("PATCH", "http://disp/api/channels/profiles/1/channels/bulk-update/")
        upstream = httpx.Response(
            400, request=request,
            text='{"channels": ["Invalid pk \\"999\\" - object does not exist."]}',
        )
        mock_client = AsyncMock()
        mock_client.bulk_update_profile_channels.side_effect = httpx.HTTPStatusError(
            "400 Client Error", request=request, response=upstream
        )

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.patch(
                "/api/channel-profiles/1/channels/bulk-update",
                json={"channel_ids": [999], "enabled": True},
            )

        assert response.status_code == 400
        assert "does not exist" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_genuine_server_error_still_500(self, async_client):
        """A non-upstream error stays a 500 (bd-lq38l.4)."""
        mock_client = AsyncMock()
        mock_client.bulk_update_profile_channels.side_effect = RuntimeError("boom")

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.patch(
                "/api/channel-profiles/1/channels/bulk-update",
                json={"channel_ids": [1], "enabled": True},
            )

        assert response.status_code == 500


class TestUpdateProfileChannel:
    """Tests for PATCH /api/channel-profiles/{profile_id}/channels/{channel_id}."""

    @pytest.mark.asyncio
    async def test_updates_channel(self, async_client):
        """Updates a single channel for a profile."""
        mock_client = AsyncMock()
        mock_client.update_profile_channel.return_value = {"status": "updated"}

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.patch(
                "/api/channel-profiles/1/channels/42",
                json={"enabled": False},
            )

        assert response.status_code == 200
        mock_client.update_profile_channel.assert_called_once_with(1, 42, {"enabled": False})

    @pytest.mark.asyncio
    async def test_client_error(self, async_client):
        """Returns 500 when client raises."""
        mock_client = AsyncMock()
        mock_client.update_profile_channel.side_effect = Exception("Error")

        with patch("routers.profiles.get_client", return_value=mock_client):
            response = await async_client.patch(
                "/api/channel-profiles/1/channels/42",
                json={"enabled": False},
            )

        assert response.status_code == 500
