"""
Unit tests for settings endpoints.

Tests: GET /api/settings, POST /api/settings, POST /api/settings/test,
       POST /api/settings/test-smtp, POST /api/settings/test-discord,
       POST /api/settings/test-telegram, POST /api/settings/restart-services,
       POST /api/settings/reset-stats
Mocks: get_settings(), save_settings(), get_client(), get_prober(), get_tracker(),
       httpx, smtplib, aiohttp.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _mock_settings(**overrides):
    """Create a mock settings object with sensible defaults."""
    defaults = {
        "url": "http://dispatcharr:8000",
        "username": "admin",
        "password": "secret",
        "auto_rename_channel_number": False,
        "include_channel_number_in_name": False,
        "channel_number_separator": "-",
        "remove_country_prefix": False,
        "include_country_in_name": False,
        "country_separator": "|",
        "timezone_preference": "both",
        "show_stream_urls": False,
        "hide_auto_sync_groups": True,
        "hide_ungrouped_streams": True,
        "hide_epg_urls": True,
        "hide_m3u_urls": True,
        "gracenote_conflict_mode": "prefer_gracenote",
        "theme": "dark",
        "default_channel_profile_ids": [],
        "linked_m3u_accounts": [],
        "epg_auto_match_threshold": 80,
        "custom_network_prefixes": [],
        "custom_network_suffixes": [],
        "stats_poll_interval": 30,
        "user_timezone": "UTC",
        "backend_log_level": "INFO",
        "frontend_log_level": "INFO",
        "vlc_open_behavior": "stream",
        "stream_probe_batch_size": 50,
        "stream_probe_timeout": 30,
        "stream_probe_schedule_time": "03:00",
        "bitrate_sample_duration": 5,
        "parallel_probing_enabled": False,
        "max_concurrent_probes": 5,
        "profile_distribution_strategy": "round_robin",
        "skip_recently_probed_hours": 24,
        "refresh_m3us_before_probe": True,
        "auto_reorder_after_probe": False,
        "probe_retry_count": 0,
        "probe_retry_delay": 5,
        "stream_fetch_page_limit": 100,
        "stream_sort_priority": ["resolution"],
        "stream_sort_enabled": {"resolution": True},
        "m3u_account_priorities": {},
        "deprioritize_failed_streams": False,
        "strike_threshold": 3,
        "disabled_builtin_tags": [],
        "custom_normalization_tags": [],
        "normalize_on_channel_create": False,
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_password": "",
        "smtp_from_email": "",
        "smtp_from_name": "ECM Alerts",
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
        "discord_webhook_url": "",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "stream_preview_mode": "passthrough",
        "auto_creation_excluded_terms": [],
        "auto_creation_excluded_groups": [],
        "auto_creation_exclude_auto_sync_groups": False,
    }
    defaults.update(overrides)
    mock = MagicMock()
    for key, value in defaults.items():
        setattr(mock, key, value)
    mock.is_configured.return_value = True
    mock.is_smtp_configured.return_value = False
    mock.is_discord_configured.return_value = False
    mock.is_telegram_configured.return_value = False
    return mock


class TestGetSettings:
    """Tests for GET /api/settings."""

    @pytest.mark.asyncio
    async def test_returns_settings(self, async_client):
        """Returns current settings with password masked."""
        mock = _mock_settings()

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200
        data = response.json()
        assert data["url"] == "http://dispatcharr:8000"
        assert data["configured"] is True
        assert "password" not in data


class TestUpdateSettings:
    """Tests for POST /api/settings."""

    @pytest.mark.asyncio
    async def test_updates_settings(self, async_client):
        """Updates settings successfully."""
        current = _mock_settings()

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache:
            mock_cache.return_value = MagicMock()
            response = await async_client.post("/api/settings", json={
                "url": "http://dispatcharr:8000",
                "username": "admin",
            })

        assert response.status_code == 200
        assert response.json()["status"] == "saved"

    @pytest.mark.asyncio
    async def test_requires_password_when_changing_url(self, async_client):
        """Returns 400 when changing URL without providing password."""
        current = _mock_settings(url="http://old-server:8000")

        with patch("routers.settings.get_settings", return_value=current):
            response = await async_client.post("/api/settings", json={
                "url": "http://new-server:8000",
                "username": "admin",
            })

        assert response.status_code == 400
        assert "password" in response.json()["detail"].lower()


class TestTestConnection:
    """Tests for POST /api/settings/test."""

    @pytest.mark.asyncio
    async def test_successful_connection(self, async_client):
        """Returns success for valid connection."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            response = await async_client.post("/api/settings/test", json={
                "url": "http://dispatcharr:8000",
                "username": "admin",
                "password": "secret",
            })

        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_failed_auth(self, async_client):
        """Returns failure for bad credentials."""
        mock_response = MagicMock()
        mock_response.status_code = 401

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            response = await async_client.post("/api/settings/test", json={
                "url": "http://dispatcharr:8000",
                "username": "admin",
                "password": "wrong",
            })

        assert response.status_code == 200
        assert response.json()["success"] is False


class TestTestSMTP:
    """Tests for POST /api/settings/test-smtp."""

    @pytest.mark.asyncio
    async def test_rejects_missing_host(self, async_client):
        """Returns failure when host is empty."""
        response = await async_client.post("/api/settings/test-smtp", json={
            "smtp_host": "",
            "smtp_from_email": "test@example.com",
            "to_email": "recipient@example.com",
        })
        assert response.status_code == 200
        assert response.json()["success"] is False

    @pytest.mark.asyncio
    async def test_rejects_missing_from(self, async_client):
        """Returns failure when from_email is empty."""
        response = await async_client.post("/api/settings/test-smtp", json={
            "smtp_host": "smtp.example.com",
            "smtp_from_email": "",
            "to_email": "recipient@example.com",
        })
        assert response.status_code == 200
        assert response.json()["success"] is False


class TestTestDiscord:
    """Tests for POST /api/settings/test-discord."""

    @pytest.mark.asyncio
    async def test_rejects_empty_url(self, async_client):
        """Returns failure when webhook URL is empty."""
        response = await async_client.post("/api/settings/test-discord", json={
            "webhook_url": "",
        })
        assert response.status_code == 200
        assert response.json()["success"] is False

    @pytest.mark.asyncio
    async def test_rejects_invalid_url(self, async_client):
        """Returns failure for non-Discord URL."""
        response = await async_client.post("/api/settings/test-discord", json={
            "webhook_url": "https://example.com/webhook",
        })
        assert response.status_code == 200
        assert response.json()["success"] is False


class TestTestTelegram:
    """Tests for POST /api/settings/test-telegram."""

    @pytest.mark.asyncio
    async def test_rejects_missing_token(self, async_client):
        """Returns failure when bot token is empty."""
        response = await async_client.post("/api/settings/test-telegram", json={
            "bot_token": "",
            "chat_id": "12345",
        })
        assert response.status_code == 200
        assert response.json()["success"] is False

    @pytest.mark.asyncio
    async def test_rejects_missing_chat_id(self, async_client):
        """Returns failure when chat ID is empty."""
        response = await async_client.post("/api/settings/test-telegram", json={
            "bot_token": "123:abc",
            "chat_id": "",
        })
        assert response.status_code == 200
        assert response.json()["success"] is False


class TestRestartServices:
    """Tests for POST /api/settings/restart-services."""

    @pytest.mark.asyncio
    async def test_returns_not_configured(self, async_client):
        """Returns failure when settings not configured."""
        mock = _mock_settings()
        mock.is_configured.return_value = False

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings.get_tracker", return_value=None), \
             patch("routers.settings.get_prober", return_value=None):
            response = await async_client.post("/api/settings/restart-services")

        assert response.status_code == 200
        assert response.json()["success"] is False

    @pytest.mark.asyncio
    async def test_restarts_services(self, async_client):
        """Restarts tracker and prober when configured."""
        mock = _mock_settings()
        mock.is_configured.return_value = True

        mock_tracker = AsyncMock()
        mock_prober = AsyncMock()

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings.get_tracker", return_value=mock_tracker), \
             patch("routers.settings.get_prober", return_value=mock_prober), \
             patch("routers.settings.get_client", return_value=AsyncMock()), \
             patch("routers.settings.BandwidthTracker") as MockTracker, \
             patch("routers.settings.StreamProber") as MockProber, \
             patch("routers.settings.set_tracker"), \
             patch("routers.settings.set_prober"), \
             patch("routers.settings.create_notification_internal"), \
             patch("routers.settings.update_notification_internal"), \
             patch("routers.settings.delete_notifications_by_source_internal"):
            MockTracker.return_value = AsyncMock()
            new_prober = MagicMock()
            new_prober.start = AsyncMock()
            MockProber.return_value = new_prober
            response = await async_client.post("/api/settings/restart-services")

        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_tracker.stop.assert_called_once()
        mock_prober.stop.assert_called_once()


class TestResetStats:
    """Tests for POST /api/settings/reset-stats."""

    @pytest.mark.asyncio
    async def test_resets_all_stats(self, async_client):
        """Clears all stats tables."""
        response = await async_client.post("/api/settings/reset-stats")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
