"""
Unit tests for Export router endpoints.

Tests: Profile CRUD, generate, preview, download, validation, journal logging.
Mocks: Dispatcharr client, ExportManager internals.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from export_models import PlaylistProfile


def _create_profile(session, **overrides):
    """Helper to create a PlaylistProfile with sensible defaults."""
    defaults = {
        "name": "Test Export",
        "description": "Test profile",
        "selection_mode": "all",
        "selected_groups": "[]",
        "selected_channels": "[]",
        "stream_url_mode": "direct",
        "include_logos": True,
        "include_epg_ids": True,
        "include_channel_numbers": True,
        "sort_order": "number",
        "filename_prefix": "playlist",
    }
    defaults.update(overrides)
    profile = PlaylistProfile(**defaults)
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


# =============================================================================
# Profile CRUD — List
# =============================================================================


class TestListProfiles:
    """Tests for GET /api/export/profiles."""

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, async_client):
        response = await async_client.get("/api/export/profiles")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_returns_profiles(self, async_client, test_session):
        _create_profile(test_session, name="Profile A")
        _create_profile(test_session, name="Profile B")
        response = await async_client.get("/api/export/profiles")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        names = {p["name"] for p in data}
        assert names == {"Profile A", "Profile B"}

    @pytest.mark.asyncio
    async def test_includes_has_generated_field(self, async_client, test_session):
        _create_profile(test_session)
        response = await async_client.get("/api/export/profiles")
        data = response.json()
        assert data[0]["has_generated"] is False


# =============================================================================
# Profile CRUD — Create
# =============================================================================


class TestCreateProfile:
    """Tests for POST /api/export/profiles."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_creates_profile(self, mock_journal, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "My Export",
            "selection_mode": "all",
            "filename_prefix": "myexport",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "My Export"
        assert data["selection_mode"] == "all"
        assert data["filename_prefix"] == "myexport"
        assert data["id"] is not None

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_logs_journal_entry(self, mock_journal, async_client):
        await async_client.post("/api/export/profiles", json={"name": "JournalTest"})
        mock_journal.log_entry.assert_called_once()
        call_kwargs = mock_journal.log_entry.call_args
        assert call_kwargs[1]["category"] == "export"
        assert call_kwargs[1]["action_type"] == "create"

    @pytest.mark.asyncio
    async def test_duplicate_name_returns_409(self, async_client, test_session):
        _create_profile(test_session, name="Duplicate")
        response = await async_client.post("/api/export/profiles", json={"name": "Duplicate"})
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_invalid_selection_mode_returns_422(self, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Bad", "selection_mode": "invalid",
        })
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_sort_order_returns_422(self, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Bad", "sort_order": "invalid",
        })
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_filename_prefix_returns_422(self, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Bad", "filename_prefix": "has spaces!",
        })
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_groups_mode_requires_selected_groups(self, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Bad", "selection_mode": "groups", "selected_groups": [],
        })
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_channels_mode_requires_selected_channels(self, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Bad", "selection_mode": "channels", "selected_channels": [],
        })
        assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_groups_mode_with_selection(self, mock_journal, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Groups", "selection_mode": "groups", "selected_groups": [1, 2, 3],
        })
        assert response.status_code == 201
        data = response.json()
        assert data["selected_groups"] == [1, 2, 3]


# =============================================================================
# Profile CRUD — Update
# =============================================================================


class TestUpdateProfile:
    """Tests for PATCH /api/export/profiles/{id}."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_updates_name(self, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        response = await async_client.patch(f"/api/export/profiles/{profile.id}", json={
            "name": "Updated Name",
        })
        assert response.status_code == 200
        assert response.json()["name"] == "Updated Name"

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, async_client):
        response = await async_client.patch("/api/export/profiles/9999", json={"name": "X"})
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_duplicate_name_returns_409(self, async_client, test_session):
        _create_profile(test_session, name="First")
        second = _create_profile(test_session, name="Second")
        response = await async_client.patch(f"/api/export/profiles/{second.id}", json={
            "name": "First",
        })
        assert response.status_code == 409

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_partial_update_preserves_other_fields(self, mock_journal, async_client, test_session):
        profile = _create_profile(test_session, description="Original desc")
        response = await async_client.patch(f"/api/export/profiles/{profile.id}", json={
            "include_logos": False,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["include_logos"] is False
        assert data["description"] == "Original desc"


# =============================================================================
# Profile CRUD — Delete
# =============================================================================


class TestDeleteProfile:
    """Tests for DELETE /api/export/profiles/{id}."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export._export_manager")
    async def test_deletes_profile(self, mock_mgr, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        response = await async_client.delete(f"/api/export/profiles/{profile.id}")
        assert response.status_code == 204
        mock_mgr.cleanup.assert_called_once_with(profile.id)

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, async_client):
        response = await async_client.delete("/api/export/profiles/9999")
        assert response.status_code == 404


# =============================================================================
# Generate
# =============================================================================


class TestGenerate:
    """Tests for POST /api/export/profiles/{id}/generate."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export._export_manager")
    async def test_generates_successfully(self, mock_mgr, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        mock_mgr.generate = AsyncMock(return_value={
            "channels_count": 50,
            "m3u_path": "/config/exports/1/playlist.m3u",
            "xmltv_path": "/config/exports/1/playlist.xml",
            "m3u_size": 5000,
            "xmltv_size": 20000,
        })
        response = await async_client.post(f"/api/export/profiles/{profile.id}/generate")
        assert response.status_code == 200
        data = response.json()
        assert data["channels_count"] == 50
        assert "duration_ms" in data

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, async_client):
        response = await async_client.post("/api/export/profiles/9999/generate")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export._export_manager")
    async def test_generation_failure_returns_500(self, mock_mgr, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        mock_mgr.generate = AsyncMock(side_effect=Exception("Connection refused"))
        response = await async_client.post(f"/api/export/profiles/{profile.id}/generate")
        assert response.status_code == 500
        assert "Connection refused" in response.json()["detail"]

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export._export_manager")
    async def test_logs_journal_on_success(self, mock_mgr, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        mock_mgr.generate = AsyncMock(return_value={"channels_count": 10})
        await async_client.post(f"/api/export/profiles/{profile.id}/generate")
        # Should have generate_started and generate_completed entries
        calls = mock_journal.log_entry.call_args_list
        action_types = [c[1]["action_type"] for c in calls]
        assert "generate_started" in action_types
        assert "generate_completed" in action_types

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export._export_manager")
    async def test_logs_journal_on_failure(self, mock_mgr, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        mock_mgr.generate = AsyncMock(side_effect=Exception("Timeout"))
        await async_client.post(f"/api/export/profiles/{profile.id}/generate")
        calls = mock_journal.log_entry.call_args_list
        action_types = [c[1]["action_type"] for c in calls]
        assert "generate_started" in action_types
        assert "generate_failed" in action_types


# =============================================================================
# Preview
# =============================================================================


class TestPreview:
    """Tests for GET /api/export/profiles/{id}/preview."""

    @pytest.mark.asyncio
    @patch("routers.export._export_manager")
    @patch("routers.export.get_client")
    async def test_returns_preview(self, mock_get_client, mock_mgr, async_client, test_session):
        profile = _create_profile(test_session)
        channels = [
            {"id": i, "name": f"Channel {i}", "channel_number": i,
             "channel_group_name": "Test", "tvg_id": f"ch{i}", "logo_url": "",
             "streams": [100 + i]}
            for i in range(1, 16)
        ]
        mock_mgr._fetch_channels = AsyncMock(return_value=channels)
        mock_mgr._sort_channels = MagicMock(return_value=channels)

        response = await async_client.get(f"/api/export/profiles/{profile.id}/preview")
        assert response.status_code == 200
        data = response.json()
        assert data["total_channels"] == 15
        assert len(data["preview_channels"]) == 10  # Max 10 preview

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, async_client):
        response = await async_client.get("/api/export/profiles/9999/preview")
        assert response.status_code == 404


# =============================================================================
# Download
# =============================================================================


class TestDownloadM3U:
    """Tests for GET /api/export/profiles/{id}/download/m3u."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export._export_manager")
    async def test_downloads_m3u(self, mock_mgr, mock_journal, async_client, test_session, tmp_path):
        profile = _create_profile(test_session)
        export_dir = tmp_path / str(profile.id)
        export_dir.mkdir()
        m3u_file = export_dir / "playlist.m3u"
        m3u_file.write_text("#EXTM3U\n#EXTINF:-1,Test\nhttp://test\n")
        mock_mgr.get_export_path.return_value = export_dir

        response = await async_client.get(f"/api/export/profiles/{profile.id}/download/m3u")
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/x-mpegurl"
        assert "playlist.m3u" in response.headers["content-disposition"]
        assert "#EXTM3U" in response.text

    @pytest.mark.asyncio
    @patch("routers.export._export_manager")
    async def test_not_generated_returns_404(self, mock_mgr, async_client, test_session, tmp_path):
        profile = _create_profile(test_session)
        export_dir = tmp_path / str(profile.id)
        export_dir.mkdir()
        mock_mgr.get_export_path.return_value = export_dir
        response = await async_client.get(f"/api/export/profiles/{profile.id}/download/m3u")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_profile_not_found_returns_404(self, async_client):
        response = await async_client.get("/api/export/profiles/9999/download/m3u")
        assert response.status_code == 404


class TestDownloadXMLTV:
    """Tests for GET /api/export/profiles/{id}/download/xmltv."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export._export_manager")
    async def test_downloads_xmltv(self, mock_mgr, mock_journal, async_client, test_session, tmp_path):
        profile = _create_profile(test_session)
        export_dir = tmp_path / str(profile.id)
        export_dir.mkdir()
        xml_file = export_dir / "playlist.xml"
        xml_file.write_text('<?xml version="1.0"?>\n<tv></tv>\n')
        mock_mgr.get_export_path.return_value = export_dir

        response = await async_client.get(f"/api/export/profiles/{profile.id}/download/xmltv")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/xml"
        assert "playlist.xml" in response.headers["content-disposition"]

    @pytest.mark.asyncio
    @patch("routers.export._export_manager")
    async def test_not_generated_returns_404(self, mock_mgr, async_client, test_session, tmp_path):
        profile = _create_profile(test_session)
        export_dir = tmp_path / str(profile.id)
        export_dir.mkdir()
        mock_mgr.get_export_path.return_value = export_dir
        response = await async_client.get(f"/api/export/profiles/{profile.id}/download/xmltv")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export._export_manager")
    async def test_regenerate_flag(self, mock_mgr, mock_journal, async_client, test_session, tmp_path):
        profile = _create_profile(test_session)
        export_dir = tmp_path / str(profile.id)
        export_dir.mkdir()
        xml_file = export_dir / "playlist.xml"
        xml_file.write_text("<tv></tv>")
        mock_mgr.get_export_path.return_value = export_dir
        mock_mgr.generate = AsyncMock()

        response = await async_client.get(
            f"/api/export/profiles/{profile.id}/download/xmltv?regenerate=true"
        )
        assert response.status_code == 200
        mock_mgr.generate.assert_called_once()


# =============================================================================
# Validation edge cases
# =============================================================================


class TestValidation:
    """Additional validation tests."""

    @pytest.mark.asyncio
    async def test_invalid_url_mode_returns_422(self, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Bad", "stream_url_mode": "invalid",
        })
        assert response.status_code == 422

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_filename_prefix_allows_hyphens_underscores(self, mock_journal, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Good", "filename_prefix": "my-export_v2",
        })
        assert response.status_code == 201

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_update_switching_to_groups_requires_selection(self, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        response = await async_client.patch(f"/api/export/profiles/{profile.id}", json={
            "selection_mode": "groups",
        })
        assert response.status_code == 400
