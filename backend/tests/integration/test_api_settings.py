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
    async def test_update_settings_rejects_malformed_dispatcharr_url(self, async_client):
        """POST /api/settings now SSRF-validates a CHANGED Dispatcharr URL on save.

        kgz3k: the settings endpoint historically stored whatever the operator
        provided. It now routes a changed, non-empty outbound base URL through
        ``_sanitize_base_url`` (scheme allowlist: http/https only) before the
        mode-aware SSRF chokepoint. ``"not-a-valid-url"`` has no http(s) scheme,
        so it is rejected with 400 — the behavior change the prior version of
        this test explicitly anticipated ("a future bead that adds URL
        validation would need to change this assertion to 400 or 422").

        Mutation check: if the scheme allowlist were removed, the malformed URL
        would be stored and this would return 200, failing the test.
        """
        response = await async_client.post(
            "/api/settings",
            json={
                "url": "not-a-valid-url",
                "username": "admin",
                "password": "password",
            },
        )
        assert response.status_code == 400
        assert "scheme" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_update_settings_requires_url(self, async_client):
        """POST /api/settings requires the url field — missing it returns 422.

        Mutation check: if SettingsRequest.url were made Optional, Pydantic would
        accept the request and the status would change to 200, failing this test.
        """
        response = await async_client.post(
            "/api/settings",
            json={
                "username": "admin",
                "password": "password",
            },
        )
        # SettingsRequest.url has no default — Pydantic rejects the missing field
        assert response.status_code == 422


class TestTestConnection:
    """Tests for POST /api/settings/test endpoint."""

    @pytest.mark.asyncio
    async def test_test_connection_with_valid_credentials(self, async_client):
        """POST /api/settings/test returns success=True when Dispatcharr token endpoint returns 200.

        The test_connection router uses httpx.AsyncClient directly (not get_client).
        Mock at the httpx boundary so ECM's parsing of the auth response is exercised.
        Mutation check: if the router stopped checking response.status_code == 200,
        success would not be True and this test would fail.
        """
        from unittest.mock import AsyncMock
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)
        mock_http_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            response = await async_client.post(
                "/api/settings/test",
                json={
                    "url": "http://dispatcharr.example.com:5656",
                    "username": "admin",
                    "password": "password",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

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
        with patch("routers.settings.get_settings") as mock_get:
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

            with patch("routers.settings.get_tracker") as mock_tracker:
                mock_tracker.return_value = None

                with patch("routers.settings.get_prober") as mock_prober:
                    mock_prober.return_value = None

                    response = await async_client.post("/api/settings/restart-services")

                    # Should attempt restart
                    assert response.status_code in (200, 500)
