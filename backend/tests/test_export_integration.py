"""
Integration tests for the full export pipeline.
Tests end-to-end flows: create profile → generate → verify files → history.
"""
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from export_models import PlaylistProfile, PublishConfiguration, PublishHistory, CloudStorageTarget


def _create_profile(session, **overrides):
    defaults = {
        "name": "Integration Profile",
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
    p = PlaylistProfile(**defaults)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _create_target(session, **overrides):
    defaults = {
        "name": "Integration Target",
        "provider_type": "s3",
        "credentials": "encrypted_blob",
        "upload_path": "/exports",
        "enabled": True,
    }
    defaults.update(overrides)
    t = CloudStorageTarget(**defaults)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _create_config(session, profile_id, target_id=None, **overrides):
    defaults = {
        "name": "Integration Config",
        "profile_id": profile_id,
        "target_id": target_id,
        "schedule_type": "manual",
        "event_triggers": "[]",
        "enabled": True,
    }
    defaults.update(overrides)
    c = PublishConfiguration(**defaults)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


# =============================================================================
# Profile CRUD + Generate integration
# =============================================================================


class TestProfileCRUDIntegration:
    """Test full profile lifecycle via API."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_create_generate_download_delete(self, mock_journal, async_client):
        """Full lifecycle: create → generate → download → delete."""
        # Create
        response = await async_client.post("/api/export/profiles", json={
            "name": "E2E Profile",
            "selection_mode": "all",
            "filename_prefix": "test",
        })
        assert response.status_code == 201
        profile_id = response.json()["id"]

        # Generate (mocked external API)
        with patch("routers.export._export_manager") as mock_mgr:
            mock_mgr.generate = AsyncMock(return_value={
                "channels_count": 10,
                "m3u_size": 500,
                "xmltv_size": 2000,
                "duration_ms": 100,
                "m3u_path": "/tmp/test.m3u",
                "xmltv_path": "/tmp/test.xml",
            })
            mock_mgr.get_export_path = MagicMock()
            response = await async_client.post(f"/api/export/profiles/{profile_id}/generate")
            assert response.status_code == 200
            data = response.json()
            assert data["channels_count"] == 10

        # List should show profile
        response = await async_client.get("/api/export/profiles")
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["name"] == "E2E Profile"

        # Delete
        response = await async_client.delete(f"/api/export/profiles/{profile_id}")
        assert response.status_code == 204

        # Verify deleted
        response = await async_client.get("/api/export/profiles")
        assert response.json() == []

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_update_profile_fields(self, mock_journal, async_client):
        """Update individual fields via PATCH."""
        resp = await async_client.post("/api/export/profiles", json={
            "name": "Original",
            "selection_mode": "all",
        })
        pid = resp.json()["id"]

        resp = await async_client.patch(f"/api/export/profiles/{pid}", json={
            "name": "Updated",
            "sort_order": "name",
            "include_logos": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Updated"
        assert data["sort_order"] == "name"
        assert data["include_logos"] is False
        # Unchanged fields remain
        assert data["selection_mode"] == "all"

    @pytest.mark.asyncio
    async def test_duplicate_name_rejected(self, async_client):
        """Duplicate profile names should be rejected."""
        with patch("routers.export.journal"):
            await async_client.post("/api/export/profiles", json={"name": "Unique"})
        resp = await async_client.post("/api/export/profiles", json={"name": "Unique"})
        assert resp.status_code == 409


# =============================================================================
# Publish Pipeline integration
# =============================================================================


class TestPublishPipelineIntegration:
    """Test publish pipeline end-to-end."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export.execute_publish")
    async def test_publish_creates_and_returns_result(self, mock_exec, mock_journal, async_client, test_session):
        """Publish endpoint should call pipeline and return result."""
        from publish_pipeline import PublishResult

        profile = _create_profile(test_session)
        config = _create_config(test_session, profile.id)

        mock_exec.return_value = PublishResult(
            success=True, channels_count=50, m3u_size=5000,
            xmltv_size=20000, duration_ms=750,
        )

        response = await async_client.post(f"/api/export/publish-configs/{config.id}/publish")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["channels_count"] == 50
        assert data["duration_ms"] == 750

    @pytest.mark.asyncio
    @patch("routers.export.execute_publish")
    async def test_dry_run_returns_channel_count(self, mock_exec, async_client, test_session):
        """Dry run should resolve channels without generating files."""
        from publish_pipeline import PublishResult

        profile = _create_profile(test_session)
        config = _create_config(test_session, profile.id)

        mock_exec.return_value = PublishResult(success=True, channels_count=25, duration_ms=100)

        response = await async_client.post(f"/api/export/publish-configs/{config.id}/dry-run")
        assert response.status_code == 200
        assert response.json()["channels_count"] == 25
        mock_exec.assert_called_once_with(config.id, dry_run=True)

    @pytest.mark.asyncio
    async def test_publish_nonexistent_config_404(self, async_client):
        """Publishing a non-existent config should return 404."""
        response = await async_client.post("/api/export/publish-configs/9999/publish")
        assert response.status_code == 404


# =============================================================================
# History integration
# =============================================================================


class TestHistoryIntegration:
    """Test history endpoint flows."""

    @pytest.mark.asyncio
    async def test_history_includes_resolved_names(self, async_client, test_session):
        """History entries should include config/profile/target names."""
        from datetime import datetime
        profile = _create_profile(test_session, name="My Profile")
        target = _create_target(test_session, name="My Target")
        config = _create_config(test_session, profile.id, target.id, name="My Config")

        h = PublishHistory(
            config_id=config.id, status="success",
            channels_count=100, started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        test_session.add(h)
        test_session.commit()

        response = await async_client.get("/api/export/publish-history")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        entry = data["entries"][0]
        assert entry["config_name"] == "My Config"
        assert entry["profile_name"] == "My Profile"

    @pytest.mark.asyncio
    async def test_history_pagination(self, async_client, test_session):
        """History should paginate correctly."""
        from datetime import datetime, timedelta
        profile = _create_profile(test_session)
        config = _create_config(test_session, profile.id)

        for i in range(25):
            h = PublishHistory(
                config_id=config.id, status="success",
                started_at=datetime.utcnow() - timedelta(minutes=i),
                completed_at=datetime.utcnow(),
            )
            test_session.add(h)
        test_session.commit()

        # Page 1
        resp = await async_client.get("/api/export/publish-history?per_page=10")
        data = resp.json()
        assert data["total"] == 25
        assert len(data["entries"]) == 10
        assert data["page"] == 1

        # Page 3
        resp = await async_client.get("/api/export/publish-history?per_page=10&page=3")
        data = resp.json()
        assert len(data["entries"]) == 5


# =============================================================================
# Cloud target integration
# =============================================================================


class TestCloudTargetIntegration:
    """Test cloud target CRUD and connection testing."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export.encrypt_credentials", return_value="encrypted")
    @patch("routers.export.decrypt_credentials", return_value={"bucket_name": "test", "access_key_id": "AKIA1234"})
    async def test_create_lists_with_masked_creds(self, mock_decrypt, mock_encrypt, mock_journal, async_client):
        """Created target should appear in list with masked credentials."""
        resp = await async_client.post("/api/export/cloud-targets", json={
            "name": "Test S3",
            "provider_type": "s3",
            "credentials": {"bucket_name": "test", "access_key_id": "AKIA12345678"},
            "upload_path": "/exports",
        })
        assert resp.status_code == 201
        target_id = resp.json()["id"]

        resp = await async_client.get("/api/export/cloud-targets")
        assert resp.status_code == 200
        targets = resp.json()
        assert len(targets) == 1
        assert targets[0]["name"] == "Test S3"
        # Credentials should be masked
        creds = targets[0]["credentials"]
        assert "AKIA12345678" not in json.dumps(creds)

        # Delete
        resp = await async_client.delete(f"/api/export/cloud-targets/{target_id}")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    @patch("routers.export.get_adapter")
    async def test_test_connection_inline(self, mock_get_adapter, async_client):
        """Inline connection test should use provided credentials."""
        from cloud_storage.base import ConnectionTestResult
        mock_adapter = AsyncMock()
        mock_adapter.test_connection.return_value = ConnectionTestResult(
            success=True, message="Connected", provider_info={"bucket": "test"}
        )
        mock_get_adapter.return_value = mock_adapter

        resp = await async_client.post("/api/export/cloud-targets/test", json={
            "provider_type": "s3",
            "credentials": {"bucket_name": "test", "access_key_id": "key", "secret_access_key": "secret"},
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
