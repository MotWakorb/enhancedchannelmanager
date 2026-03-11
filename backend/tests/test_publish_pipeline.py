"""
Tests for publish pipeline, publish config CRUD, and history endpoints.
"""
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from export_models import PlaylistProfile, CloudStorageTarget, PublishConfiguration, PublishHistory
from cloud_storage.base import ConnectionTestResult


def _create_profile(session, **overrides):
    defaults = {
        "name": "Test Export",
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
        "name": "Test S3",
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
        "name": "Test Publish",
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


def _create_history(session, config_id, **overrides):
    defaults = {
        "config_id": config_id,
        "status": "success",
        "channels_count": 50,
        "started_at": datetime.utcnow(),
        "completed_at": datetime.utcnow(),
    }
    defaults.update(overrides)
    h = PublishHistory(**defaults)
    session.add(h)
    session.commit()
    session.refresh(h)
    return h


# =============================================================================
# Publish Pipeline unit tests
# =============================================================================


def _patch_pipeline_db(test_session):
    """Return a patch for publish_pipeline.get_session that returns the test session."""
    return patch("publish_pipeline.get_session", return_value=test_session)


class TestExecutePublish:
    @pytest.mark.asyncio
    @patch("publish_pipeline.journal")
    @patch("publish_pipeline._export_manager")
    async def test_local_only_publish(self, mock_mgr, mock_journal, test_session):
        """Publish with no cloud target — generate files only."""
        from publish_pipeline import execute_publish

        profile = _create_profile(test_session)
        config = _create_config(test_session, profile.id)

        mock_mgr.generate = AsyncMock(return_value={
            "channels_count": 25,
            "m3u_size": 1000,
            "xmltv_size": 5000,
            "m3u_path": "/tmp/test.m3u",
            "xmltv_path": "/tmp/test.xml",
        })

        with _patch_pipeline_db(test_session):
            result = await execute_publish(config.id)
        assert result.success is True
        assert result.channels_count == 25
        mock_mgr.generate.assert_called_once()

    @pytest.mark.asyncio
    @patch("publish_pipeline.journal")
    @patch("publish_pipeline._export_manager")
    async def test_dry_run(self, mock_mgr, mock_journal, test_session):
        """Dry run should resolve channels but not generate."""
        from publish_pipeline import execute_publish

        profile = _create_profile(test_session)
        config = _create_config(test_session, profile.id)

        mock_mgr._fetch_channels = AsyncMock(return_value=[{"id": 1}, {"id": 2}])

        with _patch_pipeline_db(test_session), \
             patch("dispatcharr_client.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_get_client.return_value = mock_client
            result = await execute_publish(config.id, dry_run=True)

        assert result.success is True
        assert result.channels_count == 2
        mock_mgr.generate.assert_not_called()

    @pytest.mark.asyncio
    @patch("publish_pipeline.journal")
    @patch("publish_pipeline._export_manager")
    async def test_config_not_found(self, mock_mgr, mock_journal, test_session):
        from publish_pipeline import execute_publish
        with _patch_pipeline_db(test_session):
            result = await execute_publish(9999)
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    @patch("publish_pipeline.journal")
    @patch("publish_pipeline._export_manager")
    async def test_generation_failure_records_history(self, mock_mgr, mock_journal, test_session):
        from publish_pipeline import execute_publish

        profile = _create_profile(test_session)
        config = _create_config(test_session, profile.id)
        mock_mgr.generate = AsyncMock(side_effect=Exception("API timeout"))

        with _patch_pipeline_db(test_session):
            result = await execute_publish(config.id)
        assert result.success is False
        assert "API timeout" in result.error

        # Check history was recorded
        history = test_session.query(PublishHistory).filter(
            PublishHistory.config_id == config.id
        ).first()
        assert history is not None
        assert history.status == "failed"


# =============================================================================
# Publish Config CRUD
# =============================================================================


class TestPublishConfigCRUD:
    @pytest.mark.asyncio
    async def test_list_empty(self, async_client):
        response = await async_client.get("/api/export/publish-configs")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_create_config(self, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        response = await async_client.post("/api/export/publish-configs", json={
            "name": "Nightly",
            "profile_id": profile.id,
            "schedule_type": "cron",
            "cron_expression": "0 3 * * *",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Nightly"
        assert data["schedule_type"] == "cron"

    @pytest.mark.asyncio
    async def test_create_invalid_profile(self, async_client):
        response = await async_client.post("/api/export/publish-configs", json={
            "name": "Bad", "profile_id": 9999,
        })
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_create_invalid_target(self, async_client, test_session):
        profile = _create_profile(test_session)
        response = await async_client.post("/api/export/publish-configs", json={
            "name": "Bad", "profile_id": profile.id, "target_id": 9999,
        })
        assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_create_with_event_triggers(self, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        response = await async_client.post("/api/export/publish-configs", json={
            "name": "On M3U Refresh",
            "profile_id": profile.id,
            "schedule_type": "event",
            "event_triggers": ["m3u_refresh", "epg_refresh"],
        })
        assert response.status_code == 201
        assert response.json()["event_triggers"] == ["m3u_refresh", "epg_refresh"]

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_update_config(self, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        config = _create_config(test_session, profile.id)
        response = await async_client.patch(f"/api/export/publish-configs/{config.id}", json={
            "name": "Updated Name",
            "enabled": False,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_update_not_found(self, async_client):
        response = await async_client.patch("/api/export/publish-configs/9999", json={"name": "X"})
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_delete_config(self, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        config = _create_config(test_session, profile.id)
        response = await async_client.delete(f"/api/export/publish-configs/{config.id}")
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_not_found(self, async_client):
        response = await async_client.delete("/api/export/publish-configs/9999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_list_with_names(self, mock_journal, async_client, test_session):
        profile = _create_profile(test_session, name="My Profile")
        config = _create_config(test_session, profile.id, name="My Config")
        response = await async_client.get("/api/export/publish-configs")
        data = response.json()
        assert len(data) == 1
        assert data[0]["profile_name"] == "My Profile"
        assert data[0]["target_name"] is None


# =============================================================================
# Publish / Dry-run endpoints
# =============================================================================


class TestPublishEndpoints:
    @pytest.mark.asyncio
    @patch("routers.export.execute_publish")
    async def test_publish_now(self, mock_exec, async_client, test_session):
        from publish_pipeline import PublishResult
        profile = _create_profile(test_session)
        config = _create_config(test_session, profile.id)
        mock_exec.return_value = PublishResult(
            success=True, channels_count=30, m3u_size=2000, xmltv_size=8000, duration_ms=500,
        )
        response = await async_client.post(f"/api/export/publish-configs/{config.id}/publish")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["channels_count"] == 30

    @pytest.mark.asyncio
    async def test_publish_not_found(self, async_client):
        response = await async_client.post("/api/export/publish-configs/9999/publish")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("routers.export.execute_publish")
    async def test_dry_run(self, mock_exec, async_client, test_session):
        from publish_pipeline import PublishResult
        profile = _create_profile(test_session)
        config = _create_config(test_session, profile.id)
        mock_exec.return_value = PublishResult(success=True, channels_count=15, duration_ms=100)
        response = await async_client.post(f"/api/export/publish-configs/{config.id}/dry-run")
        assert response.status_code == 200
        data = response.json()
        assert data["channels_count"] == 15
        mock_exec.assert_called_once_with(config.id, dry_run=True)


# =============================================================================
# Publish History
# =============================================================================


class TestPublishHistory:
    @pytest.mark.asyncio
    async def test_list_empty(self, async_client):
        response = await async_client.get("/api/export/publish-history")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["entries"] == []

    @pytest.mark.asyncio
    async def test_list_with_entries(self, async_client, test_session):
        profile = _create_profile(test_session)
        config = _create_config(test_session, profile.id)
        _create_history(test_session, config.id)
        _create_history(test_session, config.id, status="failed")

        response = await async_client.get("/api/export/publish-history")
        data = response.json()
        assert data["total"] == 2
        assert len(data["entries"]) == 2

    @pytest.mark.asyncio
    async def test_filter_by_status(self, async_client, test_session):
        profile = _create_profile(test_session)
        config = _create_config(test_session, profile.id)
        _create_history(test_session, config.id, status="success")
        _create_history(test_session, config.id, status="failed")

        response = await async_client.get("/api/export/publish-history?status=failed")
        data = response.json()
        assert data["total"] == 1
        assert data["entries"][0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_filter_by_config(self, async_client, test_session):
        profile = _create_profile(test_session)
        config1 = _create_config(test_session, profile.id, name="Config A")
        config2 = _create_config(test_session, profile.id, name="Config B")
        _create_history(test_session, config1.id)
        _create_history(test_session, config2.id)

        response = await async_client.get(f"/api/export/publish-history?config_id={config1.id}")
        data = response.json()
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_delete_entry(self, async_client, test_session):
        profile = _create_profile(test_session)
        config = _create_config(test_session, profile.id)
        h = _create_history(test_session, config.id)

        response = await async_client.delete(f"/api/export/publish-history/{h.id}")
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_not_found(self, async_client):
        response = await async_client.delete("/api/export/publish-history/9999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_bulk_delete(self, async_client, test_session):
        profile = _create_profile(test_session)
        config = _create_config(test_session, profile.id)
        _create_history(test_session, config.id,
                        started_at=datetime.utcnow() - timedelta(days=60))
        _create_history(test_session, config.id)  # Recent

        response = await async_client.delete("/api/export/publish-history?older_than_days=30")
        assert response.status_code == 200
        assert response.json()["deleted"] == 1

    @pytest.mark.asyncio
    async def test_entries_include_names(self, async_client, test_session):
        profile = _create_profile(test_session, name="Named Profile")
        config = _create_config(test_session, profile.id, name="Named Config")
        _create_history(test_session, config.id)

        response = await async_client.get("/api/export/publish-history")
        entry = response.json()["entries"][0]
        assert entry["config_name"] == "Named Config"
        assert entry["profile_name"] == "Named Profile"


# =============================================================================
# Event triggers
# =============================================================================


class TestEventTriggers:
    @pytest.mark.asyncio
    async def test_fire_event_finds_matching_configs(self, test_session):
        from publish_pipeline import fire_event, _debounce_tasks

        profile = _create_profile(test_session)
        config = _create_config(
            test_session, profile.id,
            schedule_type="event",
            event_triggers=json.dumps(["m3u_refresh"]),
        )

        with _patch_pipeline_db(test_session), \
             patch("publish_pipeline.execute_publish", new_callable=AsyncMock) as mock_exec:
            await fire_event("m3u_refresh")
            # Wait for debounce (5s) — cancel instead to test scheduling
            assert config.id in _debounce_tasks
            # Clean up
            _debounce_tasks[config.id].cancel()
            _debounce_tasks.pop(config.id, None)

    @pytest.mark.asyncio
    async def test_fire_event_ignores_invalid_type(self, test_session):
        from publish_pipeline import fire_event, _debounce_tasks
        await fire_event("invalid_event")
        assert len(_debounce_tasks) == 0


# =============================================================================
# Webhook
# =============================================================================


class TestWebhook:
    @pytest.mark.asyncio
    async def test_sends_webhook_on_success(self):
        from publish_pipeline import _send_webhook, PublishResult

        config = {"name": "Test", "webhook_url": "https://hooks.example.com/test"}
        result = PublishResult(success=True, channels_count=10, duration_ms=500)

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            await _send_webhook(config, result)
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_webhook_when_no_url(self):
        from publish_pipeline import _send_webhook, PublishResult
        config = {"name": "Test", "webhook_url": None}
        result = PublishResult(success=True)
        # Should not raise or make any HTTP calls
        await _send_webhook(config, result)
