"""
Unit tests for M3U endpoints.

Tests: 26 M3U endpoints covering account CRUD, refresh, filters,
       profiles, group settings, and server groups.
Mocks: get_client() to isolate from Dispatcharr.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestGetM3UAccount:
    """Tests for GET /api/m3u/accounts/{account_id}."""

    @pytest.mark.asyncio
    async def test_returns_account(self, async_client):
        """Returns an M3U account."""
        mock_client = AsyncMock()
        mock_client.get_m3u_account.return_value = {"id": 1, "name": "IPTV"}

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.get("/api/m3u/accounts/1")

        assert response.status_code == 200
        mock_client.get_m3u_account.assert_called_once_with(1)


class TestCreateM3UAccount:
    """Tests for POST /api/m3u/accounts."""

    @pytest.mark.asyncio
    async def test_creates_account(self, async_client):
        """Creates an M3U account."""
        mock_client = AsyncMock()
        mock_client.create_m3u_account.return_value = {"id": 3, "name": "New M3U"}

        with patch("routers.m3u.get_client", return_value=mock_client), \
             patch("routers.m3u.journal"):
            response = await async_client.post("/api/m3u/accounts", json={
                "name": "New M3U",
                "url": "http://example.com/m3u",
            })

        assert response.status_code == 200
        assert response.json()["name"] == "New M3U"


class TestUpdateM3UAccount:
    """Tests for PUT /api/m3u/accounts/{account_id}."""

    @pytest.mark.asyncio
    async def test_updates_account(self, async_client):
        """Updates an M3U account (full replace)."""
        mock_client = AsyncMock()
        mock_client.get_m3u_account.return_value = {"id": 1, "name": "Old"}
        mock_client.update_m3u_account.return_value = {"id": 1, "name": "New"}

        with patch("routers.m3u.get_client", return_value=mock_client), \
             patch("routers.m3u.journal"):
            response = await async_client.put("/api/m3u/accounts/1", json={
                "name": "New",
            })

        assert response.status_code == 200


class TestPatchM3UAccount:
    """Tests for PATCH /api/m3u/accounts/{account_id}."""

    @pytest.mark.asyncio
    async def test_patches_account(self, async_client):
        """Patches an M3U account (partial update)."""
        mock_client = AsyncMock()
        mock_client.get_m3u_account.return_value = {"id": 1, "name": "Original"}
        mock_client.patch_m3u_account.return_value = {"id": 1, "name": "Original", "enabled": False}

        with patch("routers.m3u.get_client", return_value=mock_client), \
             patch("routers.m3u.journal"):
            response = await async_client.patch("/api/m3u/accounts/1", json={
                "enabled": False,
            })

        assert response.status_code == 200


class TestDeleteM3UAccount:
    """Tests for DELETE /api/m3u/accounts/{account_id}."""

    @pytest.mark.asyncio
    async def test_deletes_account(self, async_client):
        """Deletes an M3U account."""
        mock_client = AsyncMock()
        mock_client.get_m3u_account.return_value = {"id": 1, "name": "IPTV"}
        mock_client.delete_m3u_account.return_value = None

        with patch("routers.m3u.get_client", return_value=mock_client), \
             patch("routers.m3u.journal"):
            response = await async_client.delete("/api/m3u/accounts/1")

        assert response.status_code == 200


class TestRefreshAll:
    """Tests for POST /api/m3u/refresh."""

    @pytest.mark.asyncio
    async def test_refreshes_all(self, async_client):
        """Triggers refresh for all M3U accounts."""
        mock_client = AsyncMock()
        mock_client.refresh_all_m3u_accounts.return_value = {"status": "refreshing"}

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.post("/api/m3u/refresh")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_client_error(self, async_client):
        """Returns 500 on client error."""
        mock_client = AsyncMock()
        mock_client.refresh_all_m3u_accounts.side_effect = Exception("Timeout")

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.post("/api/m3u/refresh")

        assert response.status_code == 500


class TestRefreshSingle:
    """Tests for POST /api/m3u/refresh/{account_id}."""

    @pytest.mark.asyncio
    async def test_refreshes_account(self, async_client):
        """Triggers refresh for a single M3U account."""
        mock_client = AsyncMock()
        mock_client.get_m3u_account.return_value = {
            "id": 1, "name": "IPTV", "updated_at": "2024-01-01",
        }
        mock_client.refresh_m3u_account.return_value = {"status": "refreshing"}

        with patch("routers.m3u.get_client", return_value=mock_client), \
             patch("asyncio.create_task"):
            response = await async_client.post("/api/m3u/refresh/1")

        assert response.status_code == 200


class TestRefreshVOD:
    """Tests for POST /api/m3u/accounts/{account_id}/refresh-vod."""

    @pytest.mark.asyncio
    async def test_refreshes_vod(self, async_client):
        """Triggers VOD refresh for an account."""
        mock_client = AsyncMock()
        mock_client.refresh_m3u_vod.return_value = {"status": "refreshing"}

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.post("/api/m3u/accounts/1/refresh-vod")

        assert response.status_code == 200


class TestGetFilters:
    """Tests for GET /api/m3u/accounts/{account_id}/filters."""

    @pytest.mark.asyncio
    async def test_returns_filters(self, async_client):
        """Returns filters for an account."""
        mock_client = AsyncMock()
        mock_client.get_m3u_filters.return_value = [{"id": 1, "name": "Sports"}]

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.get("/api/m3u/accounts/1/filters")

        assert response.status_code == 200


class TestCreateFilter:
    """Tests for POST /api/m3u/accounts/{account_id}/filters."""

    @pytest.mark.asyncio
    async def test_creates_filter(self, async_client):
        """Creates a filter for an account."""
        mock_client = AsyncMock()
        mock_client.create_m3u_filter.return_value = {"id": 2, "name": "New Filter"}

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.post("/api/m3u/accounts/1/filters", json={
                "name": "New Filter",
            })

        assert response.status_code == 200


class TestUpdateFilter:
    """Tests for PUT /api/m3u/accounts/{account_id}/filters/{filter_id}."""

    @pytest.mark.asyncio
    async def test_updates_filter(self, async_client):
        """Updates a filter."""
        mock_client = AsyncMock()
        mock_client.update_m3u_filter.return_value = {"id": 1, "name": "Updated"}

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.put("/api/m3u/accounts/1/filters/1", json={
                "name": "Updated",
            })

        assert response.status_code == 200


class TestDeleteFilter:
    """Tests for DELETE /api/m3u/accounts/{account_id}/filters/{filter_id}."""

    @pytest.mark.asyncio
    async def test_deletes_filter(self, async_client):
        """Deletes a filter."""
        mock_client = AsyncMock()
        mock_client.delete_m3u_filter.return_value = None

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.delete("/api/m3u/accounts/1/filters/1")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"


class TestGetProfiles:
    """Tests for GET /api/m3u/accounts/{account_id}/profiles/."""

    @pytest.mark.asyncio
    async def test_returns_profiles(self, async_client):
        """Returns profiles for an account."""
        mock_client = AsyncMock()
        mock_client.get_m3u_profiles.return_value = [{"id": 1, "name": "Default"}]

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.get("/api/m3u/accounts/1/profiles/")

        assert response.status_code == 200


class TestCreateProfile:
    """Tests for POST /api/m3u/accounts/{account_id}/profiles/."""

    @pytest.mark.asyncio
    async def test_creates_profile(self, async_client):
        """Creates a profile for an account."""
        mock_client = AsyncMock()
        mock_client.create_m3u_profile.return_value = {"id": 2, "name": "New Profile"}

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.post("/api/m3u/accounts/1/profiles/", json={
                "name": "New Profile",
            })

        assert response.status_code == 200


class TestGetProfile:
    """Tests for GET /api/m3u/accounts/{account_id}/profiles/{profile_id}/."""

    @pytest.mark.asyncio
    async def test_returns_profile(self, async_client):
        """Returns a single profile."""
        mock_client = AsyncMock()
        mock_client.get_m3u_profile.return_value = {"id": 1, "name": "Default"}

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.get("/api/m3u/accounts/1/profiles/1/")

        assert response.status_code == 200


class TestUpdateProfile:
    """Tests for PATCH /api/m3u/accounts/{account_id}/profiles/{profile_id}/."""

    @pytest.mark.asyncio
    async def test_updates_profile(self, async_client):
        """Updates a profile."""
        mock_client = AsyncMock()
        mock_client.update_m3u_profile.return_value = {"id": 1, "name": "Updated"}

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.patch("/api/m3u/accounts/1/profiles/1/", json={
                "name": "Updated",
            })

        assert response.status_code == 200


class TestDeleteProfile:
    """Tests for DELETE /api/m3u/accounts/{account_id}/profiles/{profile_id}/."""

    @pytest.mark.asyncio
    async def test_deletes_profile(self, async_client):
        """Deletes a profile."""
        mock_client = AsyncMock()
        mock_client.delete_m3u_profile.return_value = None

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.delete("/api/m3u/accounts/1/profiles/1/")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"


class TestUpdateGroupSettings:
    """Tests for PATCH /api/m3u/accounts/{account_id}/group-settings."""

    @pytest.mark.asyncio
    async def test_updates_group_settings(self, async_client):
        """Updates M3U group settings."""
        mock_client = AsyncMock()
        mock_client.get_m3u_account.return_value = {"id": 1, "name": "IPTV", "server_groups": []}
        mock_client.update_m3u_group_settings.return_value = {"id": 1, "server_groups": []}

        with patch("routers.m3u.get_client", return_value=mock_client), \
             patch("routers.m3u.journal"):
            response = await async_client.patch("/api/m3u/accounts/1/group-settings", json={
                "auto_channel_sync": True,
            })

        assert response.status_code == 200


class TestGetServerGroups:
    """Tests for GET /api/m3u/server-groups."""

    @pytest.mark.asyncio
    async def test_returns_groups(self, async_client):
        """Returns server groups."""
        mock_client = AsyncMock()
        mock_client.get_server_groups.return_value = [{"id": 1, "name": "Sports"}]

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.get("/api/m3u/server-groups")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_client_error(self, async_client):
        """Returns 500 on client error."""
        mock_client = AsyncMock()
        mock_client.get_server_groups.side_effect = Exception("Error")

        with patch("routers.m3u.get_client", return_value=mock_client):
            response = await async_client.get("/api/m3u/server-groups")

        assert response.status_code == 500


class TestCreateServerGroup:
    """Tests for POST /api/m3u/server-groups."""

    @pytest.mark.asyncio
    async def test_creates_group(self, async_client):
        """Creates a server group."""
        mock_client = AsyncMock()
        mock_client.create_server_group.return_value = {"id": 2, "name": "News"}

        with patch("routers.m3u.get_client", return_value=mock_client), \
             patch("routers.m3u.journal"):
            response = await async_client.post("/api/m3u/server-groups", json={
                "name": "News",
            })

        assert response.status_code == 200


class TestUpdateServerGroup:
    """Tests for PATCH /api/m3u/server-groups/{group_id}."""

    @pytest.mark.asyncio
    async def test_updates_group(self, async_client):
        """Updates a server group."""
        mock_client = AsyncMock()
        mock_client.get_server_groups.return_value = [{"id": 1, "name": "Old"}]
        mock_client.update_server_group.return_value = {"id": 1, "name": "New"}

        with patch("routers.m3u.get_client", return_value=mock_client), \
             patch("routers.m3u.journal"):
            response = await async_client.patch("/api/m3u/server-groups/1", json={
                "name": "New",
            })

        assert response.status_code == 200


class TestDeleteServerGroup:
    """Tests for DELETE /api/m3u/server-groups/{group_id}."""

    @pytest.mark.asyncio
    async def test_deletes_group(self, async_client):
        """Deletes a server group."""
        mock_client = AsyncMock()
        mock_client.get_server_groups.return_value = [{"id": 1, "name": "Sports"}]
        mock_client.delete_server_group.return_value = None

        with patch("routers.m3u.get_client", return_value=mock_client), \
             patch("routers.m3u.journal"):
            response = await async_client.delete("/api/m3u/server-groups/1")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
