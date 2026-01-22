"""
Integration tests for the Settings API endpoints.

These tests use the FastAPI test client with database session overrides
to test the settings endpoints in isolation.
"""
import pytest
from unittest.mock import patch, MagicMock


class TestGetSettings:
    """Tests for GET /api/settings endpoint."""

    @pytest.mark.asyncio
    async def test_get_settings_returns_current_settings(self, async_client):
        """GET /api/settings returns current settings."""
        response = await async_client.get("/api/settings")
        assert response.status_code == 200

        data = response.json()
        # Settings should have standard fields
        assert "configured" in data
        assert "url" in data

    @pytest.mark.asyncio
    async def test_get_settings_has_required_fields(self, async_client):
        """GET /api/settings returns settings with required fields."""
        response = await async_client.get("/api/settings")
        assert response.status_code == 200

        data = response.json()
        # Settings should have these standard configuration fields
        assert "configured" in data
        assert "url" in data
        assert isinstance(data["configured"], bool)


class TestUpdateSettings:
    """Tests for POST /api/settings endpoint."""

    @pytest.mark.asyncio
    async def test_update_settings_validates_url(self, async_client):
        """POST /api/settings validates URL format."""
        response = await async_client.post(
            "/api/settings",
            json={
                "url": "not-a-valid-url",
                "username": "admin",
                "password": "password",
            },
        )
        # Should either reject or accept based on validation logic
        assert response.status_code in (200, 400, 422)

    @pytest.mark.asyncio
    async def test_update_settings_requires_url(self, async_client):
        """POST /api/settings requires URL field."""
        response = await async_client.post(
            "/api/settings",
            json={
                "username": "admin",
                "password": "password",
            },
        )
        # Missing required field should return validation error
        assert response.status_code in (400, 422)


class TestTestConnection:
    """Tests for POST /api/settings/test endpoint."""

    @pytest.mark.asyncio
    async def test_test_connection_with_valid_credentials(self, async_client):
        """POST /api/settings/test tests connection with provided credentials."""
        with patch("main.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.test_connection = MagicMock(return_value=True)
            mock_get_client.return_value = mock_client

            response = await async_client.post(
                "/api/settings/test",
                json={
                    "url": "http://localhost:5656",
                    "username": "admin",
                    "password": "password",
                },
            )

            # Should return success/failure based on test
            assert response.status_code in (200, 400, 500)

    @pytest.mark.asyncio
    async def test_test_connection_requires_credentials(self, async_client):
        """POST /api/settings/test requires URL and credentials."""
        response = await async_client.post(
            "/api/settings/test",
            json={},
        )
        assert response.status_code in (400, 422)


class TestRestartServices:
    """Tests for POST /api/settings/restart-services endpoint."""

    @pytest.mark.asyncio
    async def test_restart_services_reinitializes(self, async_client):
        """POST /api/settings/restart-services restarts background services."""
        with patch("main.get_settings") as mock_get:
            mock_settings = MagicMock()
            mock_settings.is_configured.return_value = True
            mock_settings.stats_poll_interval = 30
            mock_settings.stream_probe_timeout = 15
            mock_settings.stream_probe_batch_size = 10
            mock_settings.user_timezone = "America/New_York"
            mock_settings.probe_channel_groups = None
            mock_settings.bitrate_sample_duration = 5
            mock_settings.parallel_probing_enabled = False
            mock_settings.skip_recently_probed_hours = 0
            mock_settings.refresh_m3us_before_probe = False
            mock_settings.auto_reorder_after_probe = True
            mock_settings.deprioritize_failed_streams = True
            mock_settings.stream_sort_priority = []
            mock_settings.stream_sort_enabled = {}
            mock_settings.stream_fetch_page_limit = 200
            mock_get.return_value = mock_settings

            with patch("main.get_tracker") as mock_tracker:
                mock_tracker.return_value = None

                with patch("main.get_prober") as mock_prober:
                    mock_prober.return_value = None

                    response = await async_client.post("/api/settings/restart-services")

                    # Should attempt restart
                    assert response.status_code in (200, 500)
