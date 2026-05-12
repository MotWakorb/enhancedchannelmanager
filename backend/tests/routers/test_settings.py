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
        "auth_method": "password",
        "username": "admin",
        "password": "secret",
        "api_key": "",
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
        "mcp_api_key": "",
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

    @pytest.mark.asyncio
    async def test_switch_to_api_key_saves_successfully(self, async_client):
        """Switching auth_method to api_key with a fresh key saves without crashing."""
        current = _mock_settings(auth_method="password")

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache:
            mock_cache.return_value = MagicMock()
            response = await async_client.post("/api/settings", json={
                "url": current.url,
                "auth_method": "api_key",
                "username": current.username,
                "api_key": "newly-generated-key",
            })

        assert response.status_code == 200, response.json()
        assert response.json()["status"] == "saved"

    @pytest.mark.asyncio
    async def test_api_key_mode_preserves_stored_key_when_omitted(self, async_client):
        """Saving in api_key mode without re-sending the key keeps the stored one."""
        current = _mock_settings(auth_method="api_key", api_key="stored-key")

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache:
            mock_cache.return_value = MagicMock()
            response = await async_client.post("/api/settings", json={
                "url": current.url,
                "auth_method": "api_key",
                "username": current.username,
            })

        assert response.status_code == 200, response.json()

    @pytest.mark.asyncio
    async def test_api_key_mode_rejects_empty_key_on_switch(self, async_client):
        """Switching to api_key without providing a key is rejected with 400."""
        current = _mock_settings(auth_method="password", api_key="")

        with patch("routers.settings.get_settings", return_value=current):
            response = await async_client.post("/api/settings", json={
                "url": current.url,
                "auth_method": "api_key",
                "username": current.username,
            })

        assert response.status_code == 400
        assert "api key" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_partial_update_preserves_mcp_api_key(self, async_client):
        """bd-vj8n9: POST with a partial body must NOT clear the stored mcp_api_key.

        Reproduction of the bug: SettingsRequest doesn't accept mcp_api_key, so
        every POST that omits it would construct DispatcharrSettings(...) with
        the field defaulting to "" and silently revoke the key.
        """
        current = _mock_settings(mcp_api_key="stored-mcp-key-abc123")
        captured = {}

        def capture_save(new_settings):
            captured["mcp_api_key"] = new_settings.mcp_api_key

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings", side_effect=capture_save), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache:
            mock_cache.return_value = MagicMock()
            response = await async_client.post("/api/settings", json={
                "url": current.url,
                "username": current.username,
                "telemetry_client_errors_enabled": False,
            })

        assert response.status_code == 200, response.json()
        assert captured["mcp_api_key"] == "stored-mcp-key-abc123", (
            "Partial POST cleared mcp_api_key — sensitive field not preserved"
        )

    @pytest.mark.asyncio
    async def test_partial_update_preserves_smtp_password(self, async_client):
        """bd-vj8n9: POST with a partial body must NOT clear the stored smtp_password.

        Companion to mcp_api_key test — verifies the existing preserve-on-omit
        contract for smtp_password still holds. Regression guard.
        """
        current = _mock_settings(smtp_password="stored-smtp-password-xyz")
        captured = {}

        def capture_save(new_settings):
            captured["smtp_password"] = new_settings.smtp_password

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings", side_effect=capture_save), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache:
            mock_cache.return_value = MagicMock()
            response = await async_client.post("/api/settings", json={
                "url": current.url,
                "username": current.username,
                "telemetry_client_errors_enabled": False,
            })

        assert response.status_code == 200, response.json()
        assert captured["smtp_password"] == "stored-smtp-password-xyz", (
            "Partial POST cleared smtp_password — sensitive field not preserved"
        )


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

    @pytest.mark.asyncio
    async def test_password_mode_429_reports_rate_limit(self, async_client):
        """Dispatcharr 429 on the token endpoint surfaces a human-readable message."""
        mock_response = MagicMock()
        mock_response.status_code = 429

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            response = await async_client.post("/api/settings/test", json={
                "url": "http://dispatcharr:8000",
                "auth_method": "password",
                "username": "admin",
                "password": "secret",
            })

        body = response.json()
        assert body["success"] is False
        assert "rate-limit" in body["message"].lower() or "rate limit" in body["message"].lower()

    @pytest.mark.asyncio
    async def test_api_key_mode_success(self, async_client):
        """API-key mode probes /users/me/ with X-API-Key and treats 2xx as success."""
        me_response = MagicMock()
        me_response.status_code = 200

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.get = AsyncMock(return_value=me_response)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            response = await async_client.post("/api/settings/test", json={
                "url": "http://dispatcharr:8000",
                "auth_method": "api_key",
                "api_key": "abc123",
            })

        assert response.status_code == 200
        assert response.json()["success"] is True
        # Header should have been set on the GET.
        get_kwargs = mock_http_client.get.await_args.kwargs
        assert get_kwargs["headers"]["X-API-Key"] == "abc123"

    @pytest.mark.asyncio
    async def test_api_key_mode_invalid_key(self, async_client):
        """401 from /users/me/ in api_key mode reports invalid key."""
        me_response = MagicMock()
        me_response.status_code = 401

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.get = AsyncMock(return_value=me_response)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            response = await async_client.post("/api/settings/test", json={
                "url": "http://dispatcharr:8000",
                "auth_method": "api_key",
                "api_key": "bad-key",
            })

        body = response.json()
        assert body["success"] is False
        assert "invalid api key" in body["message"].lower()

    @pytest.mark.asyncio
    async def test_api_key_mode_missing_key(self, async_client):
        """api_key mode without a key returns a clear error without hitting the network."""
        response = await async_client.post("/api/settings/test", json={
            "url": "http://dispatcharr:8000",
            "auth_method": "api_key",
            "api_key": "",
        })

        body = response.json()
        assert body["success"] is False
        assert "api key" in body["message"].lower()


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


class TestMCPApiKeyGenerate:
    """Tests for POST /api/settings/mcp-api-key."""

    @pytest.mark.asyncio
    async def test_generates_key(self, async_client):
        """Generates a new MCP API key."""
        mock = _mock_settings()

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings.save_settings") as save_mock, \
             patch("routers.settings.clear_settings_cache"):
            response = await async_client.post("/api/settings/mcp-api-key")

        assert response.status_code == 200
        data = response.json()
        assert "mcp_api_key" in data
        assert len(data["mcp_api_key"]) > 20  # token_urlsafe(32) produces 43 chars
        save_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_replaces_existing_key(self, async_client):
        """Generating a new key replaces the old one."""
        mock = _mock_settings(mcp_api_key="old-key-value")

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings.save_settings") as save_mock, \
             patch("routers.settings.clear_settings_cache"):
            response = await async_client.post("/api/settings/mcp-api-key")

        assert response.status_code == 200
        data = response.json()
        assert data["mcp_api_key"] != "old-key-value"
        save_mock.assert_called_once()


class TestMCPApiKeyRevoke:
    """Tests for DELETE /api/settings/mcp-api-key."""

    @pytest.mark.asyncio
    async def test_revokes_key(self, async_client):
        """Revokes the MCP API key."""
        mock = _mock_settings(mcp_api_key="existing-key")

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings.save_settings") as save_mock, \
             patch("routers.settings.clear_settings_cache"):
            response = await async_client.delete("/api/settings/mcp-api-key")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "revoked"
        save_mock.assert_called_once()
        # Verify the key was cleared on the mock
        assert mock.mcp_api_key == ""

    @pytest.mark.asyncio
    async def test_revoke_when_no_key(self, async_client):
        """Revoking when no key exists still succeeds."""
        mock = _mock_settings(mcp_api_key="")

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"):
            response = await async_client.delete("/api/settings/mcp-api-key")

        assert response.status_code == 200


class TestMCPApiKeyConfiguredInResponse:
    """Tests that mcp_api_key_configured appears in GET /api/settings."""

    @pytest.mark.asyncio
    async def test_shows_configured_true(self, async_client):
        """Settings response shows mcp_api_key_configured=true when key exists."""
        mock = _mock_settings(mcp_api_key="some-key")

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200
        assert response.json()["mcp_api_key_configured"] is True

    @pytest.mark.asyncio
    async def test_shows_configured_false(self, async_client):
        """Settings response shows mcp_api_key_configured=false when no key."""
        mock = _mock_settings(mcp_api_key="")

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200
        assert response.json()["mcp_api_key_configured"] is False


class TestMCPApiKeyAuthMiddleware:
    """Tests that the auth middleware accepts MCP API key as Bearer token."""

    @pytest.mark.asyncio
    async def test_api_key_authenticates(self, async_client):
        """Valid MCP API key in Authorization header passes auth middleware."""
        from config import DispatcharrSettings

        settings = DispatcharrSettings(
            url="http://test", username="u", password="p",
            mcp_api_key="test-mcp-key-123",
        )

        with patch("main.get_settings", return_value=settings), \
             patch("main.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True

            response = await async_client.get(
                "/api/settings",
                headers={"Authorization": "Bearer test-mcp-key-123"},
            )

        # Should not be 401 — the API key should have passed auth
        assert response.status_code != 401

    @pytest.mark.asyncio
    async def test_invalid_api_key_rejected(self, async_client):
        """Invalid API key is rejected with 401."""
        from config import DispatcharrSettings

        settings = DispatcharrSettings(
            url="http://test", username="u", password="p",
            mcp_api_key="real-key",
        )

        with patch("main.get_settings", return_value=settings), \
             patch("main.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True

            response = await async_client.get(
                "/api/settings",
                headers={"Authorization": "Bearer wrong-key"},
            )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_mcp_key_does_not_match(self, async_client):
        """When mcp_api_key is empty, Bearer tokens don't match it."""
        from config import DispatcharrSettings

        settings = DispatcharrSettings(
            url="http://test", username="u", password="p",
            mcp_api_key="",  # Not configured
        )

        with patch("main.get_settings", return_value=settings), \
             patch("main.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True

            response = await async_client.get(
                "/api/settings",
                headers={"Authorization": "Bearer some-random-token"},
            )

        assert response.status_code == 401


class TestMCPStatusSanitization:
    """CodeQL py/stack-trace-exposure (#1415): GET /api/settings/mcp-status
    MUST sanitize the underlying httpx exception. The MCP server URL +
    connection error text could leak internal port/network topology.
    """

    @pytest.mark.asyncio
    async def test_mcp_status_unreachable_returns_class_only(self, async_client):
        """When the MCP server is unreachable, error contains only the class."""
        import httpx

        secret_msg = (
            "All connection attempts failed: "
            "http://10.0.5.42:6101/health (network unreachable)"
        )

        class _BoomClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def get(self, *args, **kwargs):
                raise httpx.ConnectError(secret_msg)

        with patch("httpx.AsyncClient", _BoomClient):
            response = await async_client.get("/api/settings/mcp-status")

        assert response.status_code == 200
        body = response.json()
        assert body["reachable"] is False
        # Sanitization: only the class name leaks.
        assert body["error"] == "ConnectError"
        assert "10.0.5.42" not in body["error"]
        assert "network unreachable" not in body["error"]
