"""
Unit tests for settings endpoints.

Tests: GET /api/settings, POST /api/settings, POST /api/settings/test,
       POST /api/settings/test-smtp, POST /api/settings/test-discord,
       POST /api/settings/test-telegram, POST /api/settings/restart-services,
       POST /api/settings/reset-stats
Mocks: get_settings(), save_settings(), get_client(), get_prober(), get_tracker(),
       httpx, smtplib, aiohttp.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import closing_create_task_mock
from tests.conftest import patch_ssrf_dns as _patch_ssrf_dns


def _mock_settings(**overrides):
    """Create a mock settings object with sensible defaults."""
    defaults = {
        "url": "http://dispatcharr:8000",
        "auth_method": "password",
        "username": "admin",
        "password": "secret",
        # bd-jmi1c (GH #273): canonical Dispatcharr REST API token field.
        # ``api_key`` is the legacy alias retained for one release.
        "dispatcharr_api_key": "",
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
        # GH #591 / bd-dgs64: opt-in override for the frontend-only guard that
        # locks a channel group's auto-sync controls when another M3U account
        # already auto-syncs the same (global) Dispatcharr channel_group ID.
        # Real bool (not a MagicMock auto-attr) so the admin-field gate tests
        # compare real values. Default False preserves today's locked behavior.
        "allow_multi_provider_auto_sync": False,
        "hide_ungrouped_streams": True,
        "hide_epg_urls": True,
        "hide_m3u_urls": True,
        "gracenote_conflict_mode": "prefer_gracenote",
        "theme": "dark",
        "date_format": "auto",
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
        # GH #473 safety-valve caps (skg35). Explicit ints (not MagicMock
        # auto-attrs) so the admin-field gate compares real values and existing
        # admin-field tests don't trip on an incidental "change".
        "max_auto_created_channels_per_run": 500,
        "max_auto_creation_log_entries": 500,
        "mcp_api_key": "",
        # Internal one-time-heal marker (GH #484); real bool so the POST handler,
        # which preserves it into the rebuilt settings model, gets a valid value.
        "league_delimiter_heal_applied": False,
        # Emby integration (bd-8wc6q, epic bd-2cenq). Defaults disabled so
        # existing tests can ignore the fields entirely.
        "emby_enabled": False,
        "emby_base_url": "",
        "emby_api_key": "",
        # Plex + Jellyfin integration (bd-r5f0c.4, epic bd-r5f0c). Same
        # disabled-default posture as Emby so existing test cases continue
        # ignoring these fields; the bd-r5f0c.4 suites in
        # test_plex_settings.py / test_jellyfin_settings.py / the
        # multi-source attribution suites cover the enabled paths.
        "plex_enabled": False,
        "plex_base_url": "",
        "plex_token": "",
        "jellyfin_enabled": False,
        "jellyfin_base_url": "",
        "jellyfin_api_key": "",
        # bd-mlcla: soft IP-ranking trusted networks (ranking only, never a
        # gate). Empty default so existing tests ignore it.
        "trusted_media_networks": [],
        # nngkg (bead 0i2vt.5 seam): DBAS outbound SSRF policy mode. Default
        # LAN-friendly so existing tests ignore it; the security endpoint +
        # preserve-on-save suites below exercise both modes.
        "ssrf_outbound_mode": "lan_friendly",
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

    @pytest.mark.asyncio
    async def test_exposes_date_format(self, async_client):
        """GET exposes the global date_format preference (bd-8j47e)."""
        mock = _mock_settings(date_format="dmy")

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200
        assert response.json()["date_format"] == "dmy"

    @pytest.mark.asyncio
    async def test_exposes_auto_creation_caps(self, async_client):
        """skg35: GET surfaces both GH #473 safety-valve caps so the UI can
        show + edit them (previously settings.json-only, undiscoverable)."""
        mock = _mock_settings(
            max_auto_created_channels_per_run=750,
            max_auto_creation_log_entries=300,
        )

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200
        data = response.json()
        assert data["max_auto_created_channels_per_run"] == 750
        assert data["max_auto_creation_log_entries"] == 300

    @pytest.mark.asyncio
    async def test_exposes_allow_multi_provider_auto_sync(self, async_client):
        """bd-dgs64 (GH #591): GET surfaces the multi-provider auto-sync override."""
        mock = _mock_settings(allow_multi_provider_auto_sync=True)

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200
        assert response.json()["allow_multi_provider_auto_sync"] is True

    @pytest.mark.asyncio
    async def test_defaults_allow_multi_provider_auto_sync_false(self, async_client):
        """bd-dgs64: default is False — preserves today's single-owner lock."""
        mock = _mock_settings(allow_multi_provider_auto_sync=False)

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200
        assert response.json()["allow_multi_provider_auto_sync"] is False


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
    async def test_persists_date_format(self, async_client):
        """POST threads date_format into the saved settings (bd-8j47e)."""
        current = _mock_settings()

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings") as mock_save, \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache:
            mock_cache.return_value = MagicMock()
            response = await async_client.post("/api/settings", json={
                "url": "http://dispatcharr:8000",
                "username": "admin",
                "date_format": "iso",
            })

        assert response.status_code == 200
        saved = mock_save.call_args[0][0]
        assert saved.date_format == "iso"

    @pytest.mark.asyncio
    async def test_persists_allow_multi_provider_auto_sync(self, async_client):
        """bd-dgs64 (GH #591): POST threads the override into saved settings."""
        current = _mock_settings(allow_multi_provider_auto_sync=False)

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings") as mock_save, \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache:
            mock_cache.return_value = MagicMock()
            response = await async_client.post("/api/settings", json={
                "url": "http://dispatcharr:8000",
                "username": "admin",
                "allow_multi_provider_auto_sync": True,
            })

        assert response.status_code == 200
        saved = mock_save.call_args[0][0]
        assert saved.allow_multi_provider_auto_sync is True

    @pytest.mark.asyncio
    async def test_logs_and_journals_allow_multi_provider_auto_sync_change(self, async_client):
        """bd-dgs64 (GH #591): a value change logs a line and writes a
        before/after journal entry — mirrors the group-settings PATCH journal
        convention in routers/m3u.py (no other settings.py field journals
        today, so this introduces the "settings" journal category)."""
        current = _mock_settings(allow_multi_provider_auto_sync=False)

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache, \
             patch("routers.settings.journal.log_entry") as mock_journal:
            mock_cache.return_value = MagicMock()
            response = await async_client.post("/api/settings", json={
                "url": "http://dispatcharr:8000",
                "username": "admin",
                "allow_multi_provider_auto_sync": True,
            })

        assert response.status_code == 200
        mock_journal.assert_called_once()
        call_kwargs = mock_journal.call_args.kwargs
        assert call_kwargs["category"] == "settings"
        assert call_kwargs["action_type"] == "update"
        assert call_kwargs["before_value"] == {"allow_multi_provider_auto_sync": False}
        assert call_kwargs["after_value"] == {"allow_multi_provider_auto_sync": True}

    @pytest.mark.asyncio
    async def test_no_journal_entry_when_allow_multi_provider_auto_sync_unchanged(self, async_client):
        """No journal noise when the field is echoed back unchanged (the
        common case — every settings save round-trips this field)."""
        current = _mock_settings(allow_multi_provider_auto_sync=False)

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache, \
             patch("routers.settings.journal.log_entry") as mock_journal:
            mock_cache.return_value = MagicMock()
            response = await async_client.post("/api/settings", json={
                "url": "http://dispatcharr:8000",
                "username": "admin",
                "allow_multi_provider_auto_sync": False,
            })

        assert response.status_code == 200
        mock_journal.assert_not_called()

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

        # bd-snryv: mode_changed=True here makes connection_changed True,
        # which now schedules a background-service rebuild via
        # asyncio.create_task(). Patch create_task out with the project's
        # standard fire-and-forget test helper (see tests/conftest.py) so the
        # rebuild's own real Dispatcharr/BandwidthTracker/StreamProber calls
        # never run in this unrelated unit test.
        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache, \
             patch("asyncio.create_task", new=closing_create_task_mock()):
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
    async def test_update_preserves_league_delimiter_heal_marker(self, async_client):
        """GH #484: the internal one-time-heal marker is never sent by the UI, so
        a settings POST must preserve it. If it reset to False, the next boot
        would re-run the heal and re-clobber the user's require_delimiter choice.
        """
        current = _mock_settings(league_delimiter_heal_applied=True)
        captured = {}

        def capture_save(new_settings):
            captured["marker"] = new_settings.league_delimiter_heal_applied

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
        assert captured["marker"] is True, (
            "Settings POST reset league_delimiter_heal_applied — heal would re-arm"
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

    @pytest.mark.asyncio
    async def test_accepts_canonical_dispatcharr_api_key_field(self, async_client):
        """bd-jmi1c (GH #273): POST with canonical ``dispatcharr_api_key``
        field name persists to the canonical attribute on the saved settings."""
        current = _mock_settings(auth_method="password")
        captured = {}

        def capture_save(new_settings):
            captured["dispatcharr_api_key"] = new_settings.dispatcharr_api_key

        # bd-snryv: mode_changed=True (password -> api_key) makes
        # connection_changed True, which now schedules a background-service
        # rebuild via asyncio.create_task() — patch it out with the project's
        # standard fire-and-forget test helper (tests/conftest.py) so the
        # rebuild's own real Dispatcharr/BandwidthTracker/StreamProber calls
        # never run in this unrelated unit test.
        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings", side_effect=capture_save), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache, \
             patch("asyncio.create_task", new=closing_create_task_mock()):
            mock_cache.return_value = MagicMock()
            response = await async_client.post("/api/settings", json={
                "url": current.url,
                "auth_method": "api_key",
                "username": current.username,
                "dispatcharr_api_key": "new-canonical-key",
            })

        assert response.status_code == 200, response.json()
        assert captured["dispatcharr_api_key"] == "new-canonical-key"

    @pytest.mark.asyncio
    async def test_accepts_legacy_api_key_field_for_back_compat(self, async_client):
        """bd-jmi1c (GH #273): POST with legacy ``api_key`` field name still
        works — an older frontend bundle in a cached browser tab continues to
        function. The value is persisted into the canonical attribute on the
        saved settings (legacy in, canonical out)."""
        current = _mock_settings(auth_method="password")
        captured = {}

        def capture_save(new_settings):
            captured["dispatcharr_api_key"] = new_settings.dispatcharr_api_key

        # bd-snryv: mode_changed=True (password -> api_key) makes
        # connection_changed True, which now schedules a background-service
        # rebuild via asyncio.create_task() — patch it out with the project's
        # standard fire-and-forget test helper (tests/conftest.py) so the
        # rebuild's own real Dispatcharr/BandwidthTracker/StreamProber calls
        # never run in this unrelated unit test.
        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings", side_effect=capture_save), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache, \
             patch("asyncio.create_task", new=closing_create_task_mock()):
            mock_cache.return_value = MagicMock()
            response = await async_client.post("/api/settings", json={
                "url": current.url,
                "auth_method": "api_key",
                "username": current.username,
                "api_key": "legacy-from-old-bundle",
            })

        assert response.status_code == 200, response.json()
        # Legacy field on the request body maps to the canonical attribute
        # on the persisted settings — operators never see the legacy name
        # re-emerge as the source of truth on disk.
        assert captured["dispatcharr_api_key"] == "legacy-from-old-bundle"

    @pytest.mark.asyncio
    async def test_canonical_field_wins_when_both_fields_in_request_body(self, async_client):
        """bd-jmi1c (GH #273): if a confused client sends BOTH field names,
        the canonical wins so a stale value in the legacy slot cannot
        override an intentionally-rotated canonical value."""
        current = _mock_settings(auth_method="password")
        captured = {}

        def capture_save(new_settings):
            captured["dispatcharr_api_key"] = new_settings.dispatcharr_api_key

        # bd-snryv: mode_changed=True (password -> api_key) makes
        # connection_changed True, which now schedules a background-service
        # rebuild via asyncio.create_task() — patch it out with the project's
        # standard fire-and-forget test helper (tests/conftest.py) so the
        # rebuild's own real Dispatcharr/BandwidthTracker/StreamProber calls
        # never run in this unrelated unit test.
        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings", side_effect=capture_save), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache, \
             patch("asyncio.create_task", new=closing_create_task_mock()):
            mock_cache.return_value = MagicMock()
            response = await async_client.post("/api/settings", json={
                "url": current.url,
                "auth_method": "api_key",
                "username": current.username,
                "dispatcharr_api_key": "canonical-wins",
                "api_key": "legacy-loses",
            })

        assert response.status_code == 200, response.json()
        assert captured["dispatcharr_api_key"] == "canonical-wins"

    @pytest.mark.asyncio
    async def test_post_warns_when_both_fields_differ(self, async_client, caplog):
        """bd-jmi1c P1-1 / bd-46g4t: when the POST body contains both
        ``dispatcharr_api_key`` and ``api_key`` with differing values, log a
        WARN so the silent-discard of the legacy value is auditable. POST is
        rare enough that per-request logging is acceptable — no flag-gating
        is needed."""
        import logging
        current = _mock_settings(auth_method="password")

        # bd-snryv: mode_changed=True (password -> api_key) makes
        # connection_changed True, which now schedules a background-service
        # rebuild via asyncio.create_task() — patch it out with the project's
        # standard fire-and-forget test helper (tests/conftest.py) so the
        # rebuild's own real Dispatcharr/BandwidthTracker/StreamProber calls
        # never run in this unrelated unit test.
        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache, \
             patch("asyncio.create_task", new=closing_create_task_mock()):
            mock_cache.return_value = MagicMock()
            with caplog.at_level(logging.WARNING, logger="routers.settings"):
                response = await async_client.post("/api/settings", json={
                    "url": current.url,
                    "auth_method": "api_key",
                    "username": current.username,
                    "dispatcharr_api_key": "canonical-keep-me",
                    "api_key": "legacy-drop-me",
                })

        assert response.status_code == 200, response.json()
        conflict_warns = [
            record.getMessage()
            for record in caplog.records
            if "differing 'dispatcharr_api_key' and 'api_key'" in record.getMessage()
        ]
        assert len(conflict_warns) == 1, (
            f"Expected one conflict WARN; got: {conflict_warns}"
        )
        assert "bd-jmi1c" in conflict_warns[0]

    @pytest.mark.asyncio
    async def test_post_no_warn_when_both_fields_identical(self, async_client, caplog):
        """If a client double-sends the same value into both field names
        (e.g., for back-compat with an older backend), there's no conflict
        and no WARN — the WARN is reserved for the genuine discard case."""
        import logging
        current = _mock_settings(auth_method="password")

        # bd-snryv: mode_changed=True (password -> api_key) makes
        # connection_changed True, which now schedules a background-service
        # rebuild via asyncio.create_task() — patch it out with the project's
        # standard fire-and-forget test helper (tests/conftest.py) so the
        # rebuild's own real Dispatcharr/BandwidthTracker/StreamProber calls
        # never run in this unrelated unit test.
        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache, \
             patch("asyncio.create_task", new=closing_create_task_mock()):
            mock_cache.return_value = MagicMock()
            with caplog.at_level(logging.WARNING, logger="routers.settings"):
                response = await async_client.post("/api/settings", json={
                    "url": current.url,
                    "auth_method": "api_key",
                    "username": current.username,
                    "dispatcharr_api_key": "same-value",
                    "api_key": "same-value",
                })

        assert response.status_code == 200, response.json()
        assert not any(
            "differing 'dispatcharr_api_key' and 'api_key'" in record.getMessage()
            for record in caplog.records
        ), "Conflict WARN fired despite identical values"

    @pytest.mark.asyncio
    async def test_response_exposes_both_indicators(self, async_client):
        """bd-jmi1c (GH #273): GET /api/settings returns both
        ``dispatcharr_api_key_configured`` (canonical) and the legacy
        ``api_key_configured`` for one release of frontend back-compat. Both
        track the same underlying state — a key is either configured or not."""
        current = _mock_settings(dispatcharr_api_key="stored-canonical-key")

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200
        data = response.json()
        assert data["dispatcharr_api_key_configured"] is True
        assert data["api_key_configured"] is True


class TestUpdateSettingsRebuildsBackgroundServices:
    """bd-snryv: POST /api/settings must rebuild the standing BandwidthTracker
    / StreamProber (and reconnect the prober-dependent task instances) when a
    Dispatcharr connection-relevant field changes — not just swap the
    get_client() singleton via reset_client(). Otherwise the background
    tracker/prober keep talking to the OLD (stale-credential) client forever,
    until an operator separately discovers and hits restart-services or the
    container restarts.
    """

    @pytest.mark.asyncio
    async def test_rebuilds_when_password_changes_alone(self, async_client):
        """A password rotation with url/username/mode unchanged is NOT
        caught by ``auth_changed`` — it must still trigger a rebuild."""
        current = _mock_settings(auth_method="password", password="old-secret")

        mock_old_tracker = AsyncMock()
        mock_old_prober = AsyncMock()
        new_prober_instance = MagicMock()
        new_prober_instance.start = AsyncMock()
        mock_task_instance = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get_task_instance.return_value = mock_task_instance

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_client", return_value=AsyncMock()), \
             patch("routers.settings.get_tracker", return_value=mock_old_tracker), \
             patch("routers.settings.get_prober", return_value=mock_old_prober), \
             patch("routers.settings.BandwidthTracker") as MockTracker, \
             patch("routers.settings.StreamProber") as MockProber, \
             patch("routers.settings.set_tracker") as mock_set_tracker, \
             patch("routers.settings.set_prober") as mock_set_prober, \
             patch("routers.settings.get_cache") as mock_cache, \
             patch("task_registry.get_registry", return_value=mock_registry):
            MockTracker.return_value = AsyncMock()
            MockProber.return_value = new_prober_instance
            mock_cache.return_value = MagicMock()

            response = await async_client.post("/api/settings", json={
                "url": current.url,
                "auth_method": "password",
                "username": current.username,
                "password": "new-secret",
            })

        assert response.status_code == 200, response.json()
        assert response.json()["status"] == "saved"
        mock_old_tracker.stop.assert_called_once()
        mock_old_prober.stop.assert_called_once()
        mock_set_tracker.assert_called_once()
        mock_set_prober.assert_called_once_with(new_prober_instance)
        assert mock_task_instance.set_prober.call_count == 3
        mock_task_instance.set_prober.assert_called_with(new_prober_instance)

    @pytest.mark.asyncio
    async def test_rebuilds_when_api_key_changes_alone(self, async_client):
        """Rotating the API key in api_key mode (url/mode unchanged) is NOT
        caught by ``auth_changed`` — it must still trigger a rebuild."""
        current = _mock_settings(
            auth_method="api_key", dispatcharr_api_key="old-key", api_key="old-key",
        )

        mock_old_tracker = AsyncMock()
        mock_old_prober = AsyncMock()
        new_prober_instance = MagicMock()
        new_prober_instance.start = AsyncMock()
        mock_task_instance = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get_task_instance.return_value = mock_task_instance

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_client", return_value=AsyncMock()), \
             patch("routers.settings.get_tracker", return_value=mock_old_tracker), \
             patch("routers.settings.get_prober", return_value=mock_old_prober), \
             patch("routers.settings.BandwidthTracker") as MockTracker, \
             patch("routers.settings.StreamProber") as MockProber, \
             patch("routers.settings.set_tracker") as mock_set_tracker, \
             patch("routers.settings.set_prober") as mock_set_prober, \
             patch("routers.settings.get_cache") as mock_cache, \
             patch("task_registry.get_registry", return_value=mock_registry):
            MockTracker.return_value = AsyncMock()
            MockProber.return_value = new_prober_instance
            mock_cache.return_value = MagicMock()

            response = await async_client.post("/api/settings", json={
                "url": current.url,
                "auth_method": "api_key",
                "username": current.username,
                "dispatcharr_api_key": "new-key",
            })

        assert response.status_code == 200, response.json()
        assert response.json()["status"] == "saved"
        mock_set_tracker.assert_called_once()
        mock_set_prober.assert_called_once_with(new_prober_instance)
        assert mock_task_instance.set_prober.call_count == 3

    @pytest.mark.asyncio
    async def test_rebuild_failure_does_not_break_the_save(self, async_client, caplog):
        """If the background-service rebuild raises, the settings save must
        still succeed (200, status=saved) — the rebuild failure is logged,
        not surfaced as a 500."""
        import logging
        current = _mock_settings(auth_method="password", password="old-secret")

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch(
                 "routers.settings._restart_background_services",
                 side_effect=RuntimeError("boom"),
             ), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache:
            mock_cache.return_value = MagicMock()
            with caplog.at_level(logging.WARNING, logger="routers.settings"):
                response = await async_client.post("/api/settings", json={
                    "url": current.url,
                    "auth_method": "password",
                    "username": current.username,
                    "password": "new-secret",
                })

        assert response.status_code == 200, response.json()
        assert response.json()["status"] == "saved"
        assert any(
            "Failed to rebuild background services" in record.getMessage()
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_no_rebuild_when_no_connection_field_changes(self, async_client):
        """Toggling an unrelated preference (e.g. auto_reorder_after_probe)
        must NOT tear down and restart the tracker/prober — that would be an
        unnecessary restart storm on every minor settings tweak."""
        current = _mock_settings(auth_method="password", password="unchanged-secret")

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings._restart_background_services") as mock_rebuild, \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache:
            mock_cache.return_value = MagicMock()

            response = await async_client.post("/api/settings", json={
                "url": current.url,
                "auth_method": "password",
                "username": current.username,
                "auto_reorder_after_probe": True,
            })

        assert response.status_code == 200, response.json()
        assert response.json()["status"] == "saved"
        mock_rebuild.assert_not_called()

    @pytest.mark.asyncio
    async def test_rebuild_is_fire_and_forget_does_not_block_response(self, async_client):
        """bd-snryv: the rebuild does a real paginated Dispatcharr fetch
        (BandwidthTracker._initialize_channel_maps()) plus an ffprobe
        availability check — it must be scheduled via asyncio.create_task(),
        not awaited inline, so a slow/unreachable Dispatcharr host during a
        credential rotation can't hang the settings-save HTTP response.

        bd-z6trk: this used to mock ``_restart_background_services`` with a
        fixed ``asyncio.sleep(0.3)`` and assert the response returned in
        ``<0.2s`` wall-clock. That flaked on busy CI runners (observed on run
        29630277919: the response itself took 0.424s — fire-and-forget was
        working, the "Rebuilt background services" log landed ~300ms AFTER
        the response, but the request just took longer than the hard-coded
        budget on a loaded shared runner). Any wall-clock threshold flakes
        under load, so this version has none: the mocked rebuild is gated on
        an ``asyncio.Event`` the test controls instead of a timed sleep. The
        gate stays closed for the whole POST, so if the rebuild were awaited
        inline the response could never arrive — the response arriving at
        all, under a generous ``wait_for`` timeout, is the proof, and it
        holds regardless of runner speed. Releasing the gate afterward and
        waiting on a second Event both lets the detached task run to
        completion inside the mocks (matching the old sleep's intent) and
        confirms the rebuild actually executes.
        """
        current = _mock_settings(auth_method="password", password="old-secret")

        release_rebuild = asyncio.Event()
        rebuild_finished = asyncio.Event()

        async def gated_rebuild(settings):
            await release_rebuild.wait()
            rebuild_finished.set()
            return {"success": True, "message": "restarted"}

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings._restart_background_services", side_effect=gated_rebuild), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache:
            mock_cache.return_value = MagicMock()

            try:
                response = await asyncio.wait_for(
                    async_client.post("/api/settings", json={
                        "url": current.url,
                        "auth_method": "password",
                        "username": current.username,
                        "password": "new-secret",
                    }),
                    timeout=10,
                )
            except asyncio.TimeoutError:
                pytest.fail(
                    "settings save never returned while the rebuild was "
                    "gated — rebuild is being awaited inline instead of "
                    "fire-and-forget"
                )

            # The response arrived while the gate is still closed — proof
            # the rebuild wasn't awaited inline.
            assert not rebuild_finished.is_set()

            # Release the gate and let the detached task run to completion
            # inside the mocks before they're torn down on exit — this also
            # verifies the rebuild actually runs.
            release_rebuild.set()
            await asyncio.wait_for(rebuild_finished.wait(), timeout=5)

        assert response.status_code == 200, response.json()
        assert response.json()["status"] == "saved"

    @pytest.mark.asyncio
    async def test_rebuild_reported_failure_logs_warning_not_info(self, async_client, caplog):
        """bd-snryv: ``_restart_background_services()`` catches its own
        construction/start failures internally and RETURNS
        ``{"success": False, ...}`` instead of raising. Before this fix the
        fire-and-forget wrapper logged that dict unconditionally at INFO
        ("Rebuilt background services..."), so a failed rebuild read as
        routine success in the logs. Assert the WARNING branch fires
        instead when ``success`` is False, and the (misleading) INFO success
        line does not."""
        import logging
        current = _mock_settings(auth_method="password", password="old-secret")

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings"), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch(
                 "routers.settings._restart_background_services",
                 return_value={"success": False, "message": "Failed to restart services"},
             ), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache:
            mock_cache.return_value = MagicMock()
            with caplog.at_level(logging.INFO, logger="routers.settings"):
                response = await async_client.post("/api/settings", json={
                    "url": current.url,
                    "auth_method": "password",
                    "username": current.username,
                    "password": "new-secret",
                })
                # Let the fire-and-forget rebuild task run to completion
                # before the mocks above are torn down.
                await asyncio.sleep(0)

        assert response.status_code == 200, response.json()
        assert response.json()["status"] == "saved"
        warning_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "did not succeed" in r.getMessage()
        ]
        assert len(warning_records) == 1, (
            f"Expected one WARNING for the failed rebuild; got: {caplog.records}"
        )
        assert not any(
            "Rebuilt background services" in r.getMessage() and r.levelno == logging.INFO
            for r in caplog.records
        ), "Failed rebuild was logged as a routine INFO success"


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


class TestTestConnectionOutboundPolicy:
    """POST /api/settings/test enforces the SAME outbound policy as save.

    GH #754 / bead ``0yh70``. The reporter's Test Connection SUCCEEDED against
    ``http://localhost:9191`` and the save was then refused — because this
    endpoint carried its own inline scheme+netloc check and never consulted
    the host policy at all, while POST /api/settings ran the full validator.
    Proving a connection works and then being refused permission to store it
    is its own defect; so is the security half of the same gap — this endpoint
    POSTs the operator-supplied username/password (or GETs with ``X-API-Key``)
    to any host the caller names, with no host policy whatsoever.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["lan_friendly", "public_only"])
    async def test_metadata_host_rejected_without_probing(self, async_client, mode):
        """IMDS is refused in BOTH modes and no HTTP request is issued."""
        from security.ssrf import SSRFMode
        http_client = MagicMock()
        with patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode(mode)), \
             patch("httpx.AsyncClient", return_value=http_client) as ctor:
            response = await async_client.post("/api/settings/test", json={
                "url": "http://169.254.169.254",
                "auth_method": "api_key",
                "api_key": "abc123",
            })

        body = response.json()
        assert body["success"] is False
        assert "host" in body["message"].lower()
        # The credential never left the process.
        ctor.assert_not_called()

    @pytest.mark.asyncio
    async def test_localhost_accepted_in_lan_friendly(self, async_client):
        """The GH #754 URL still tests fine under the shipped default."""
        from security.ssrf import SSRFMode
        me_response = MagicMock()
        me_response.status_code = 200

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.get = AsyncMock(return_value=me_response)

        with patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode.LAN_FRIENDLY), \
             _patch_ssrf_dns("::1", "127.0.0.1"), \
             patch("httpx.AsyncClient", return_value=mock_http_client):
            response = await async_client.post("/api/settings/test", json={
                "url": "http://localhost:9191",
                "auth_method": "api_key",
                "api_key": "abc123",
            })

        assert response.json()["success"] is True
        assert mock_http_client.get.await_args[0][0].startswith("http://localhost:9191/")

    @pytest.mark.asyncio
    async def test_localhost_rejected_in_public_only(self, async_client):
        """...and is refused under ``public_only``, matching the save path."""
        from security.ssrf import SSRFMode
        with patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode.PUBLIC_ONLY), \
             _patch_ssrf_dns("::1", "127.0.0.1"), \
             patch("httpx.AsyncClient") as ctor:
            response = await async_client.post("/api/settings/test", json={
                "url": "http://localhost:9191",
                "auth_method": "api_key",
                "api_key": "abc123",
            })

        assert response.json()["success"] is False
        ctor.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_http_scheme_rejected(self, async_client):
        """Pre-existing scheme allowlist survives the rewire."""
        with patch("httpx.AsyncClient") as ctor:
            response = await async_client.post("/api/settings/test", json={
                "url": "file:///etc/passwd",
                "username": "admin",
                "password": "secret",
            })

        body = response.json()
        assert body["success"] is False
        assert "scheme" in body["message"].lower()
        ctor.assert_not_called()

    @pytest.mark.asyncio
    async def test_unresolvable_host_is_not_a_policy_rejection(self, async_client):
        """An offline/unknown host must still be testable (fail-open on DNS).

        The chokepoint fails CLOSED on resolution failure, which is right for
        the connect path but wrong here: the operator is testing a host that
        may legitimately be down. It must reach the probe and report a normal
        connection error, not a policy refusal.
        """
        import socket as _socket

        import httpx

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.post = AsyncMock(side_effect=httpx.ConnectError("nope"))

        from security import ssrf as _ssrf

        def _boom(host, port):
            raise _socket.gaierror("Name or service not known")

        with patch.object(_ssrf, "_resolve", _boom), \
             patch("httpx.AsyncClient", return_value=mock_http_client):
            response = await async_client.post("/api/settings/test", json={
                "url": "http://dispatcharr.offline:8000",
                "username": "admin",
                "password": "secret",
            })

        body = response.json()
        assert body["success"] is False
        assert "could not connect" in body["message"].lower()


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

    @pytest.mark.asyncio
    async def test_falls_back_to_stored_password_when_omitted(self, async_client):
        """gh-380: an empty smtp_password reuses the saved one so the test
        authenticates instead of failing with 530 Authentication Required.

        The Settings UI never re-sends the stored password (masked + cleared
        on load), so a test request normally arrives with smtp_password="".
        The endpoint must mirror update_settings' preserve-on-omit contract.
        """
        mock = _mock_settings(smtp_user="user@example.com", smtp_password="stored-secret")
        mock_server = MagicMock()

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("smtplib.SMTP", return_value=mock_server) as MockSMTP:
            response = await async_client.post("/api/settings/test-smtp", json={
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
                "smtp_user": "user@example.com",
                "smtp_password": "",  # masked: UI never resends it
                "smtp_from_email": "user@example.com",
                "smtp_use_tls": True,
                "smtp_use_ssl": False,
                "to_email": "recipient@example.com",
            })

        assert response.status_code == 200
        assert response.json()["success"] is True
        MockSMTP.assert_called_once()
        mock_server.login.assert_called_once_with("user@example.com", "stored-secret")
        mock_server.sendmail.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_request_password_over_stored(self, async_client):
        """A password typed into the test form takes precedence over the
        stored one (operator validating credentials before saving)."""
        mock = _mock_settings(smtp_user="user@example.com", smtp_password="stored-secret")
        mock_server = MagicMock()

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("smtplib.SMTP", return_value=mock_server):
            response = await async_client.post("/api/settings/test-smtp", json={
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
                "smtp_user": "user@example.com",
                "smtp_password": "typed-secret",
                "smtp_from_email": "user@example.com",
                "smtp_use_tls": True,
                "smtp_use_ssl": False,
                "to_email": "recipient@example.com",
            })

        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_server.login.assert_called_once_with("user@example.com", "typed-secret")


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
    async def test_clears_tracker_and_prober_singletons_when_not_configured(self, async_client):
        """bd-snryv: StreamProber.stop() only flips a cancellation flag — a
        later probe entry point resets it back to False — so the
        just-stopped tracker/prober objects stay truthy and reusable.
        Without explicitly clearing the singletons on this early-return
        path, a caller like channel_pipeline_engine's
        ``prober = get_prober(); if not prober: skip`` guard would pass and
        proceed against a client for settings that are no longer configured.
        """
        mock = _mock_settings()
        mock.is_configured.return_value = False
        mock_old_tracker = AsyncMock()
        mock_old_prober = AsyncMock()

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings.get_tracker", return_value=mock_old_tracker), \
             patch("routers.settings.get_prober", return_value=mock_old_prober), \
             patch("routers.settings.set_tracker") as mock_set_tracker, \
             patch("routers.settings.set_prober") as mock_set_prober:
            response = await async_client.post("/api/settings/restart-services")

        assert response.status_code == 200
        assert response.json()["success"] is False
        mock_old_tracker.stop.assert_called_once()
        mock_old_prober.stop.assert_called_once()
        mock_set_tracker.assert_called_once_with(None)
        mock_set_prober.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_restart_resets_channel_pipeline_engine_singleton(self, async_client):
        """bd-snryv: ChannelPipelineEngine has the identical stale-client bug as
        BandwidthTracker/StreamProber — it captures ``self.client`` once and
        never re-fetches get_client(). The rebuild must clear the engine
        singleton so the next ``_ensure_engine()`` call in
        routers/channel_pipeline.py naturally rebuilds it against the fresh
        client, instead of an auto-creation rule run hitting Dispatcharr with
        stale credentials indefinitely after a rotation."""
        mock = _mock_settings()
        mock.is_configured.return_value = True

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings.get_tracker", return_value=None), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_client", return_value=AsyncMock()), \
             patch("routers.settings.BandwidthTracker") as MockTracker, \
             patch("routers.settings.StreamProber") as MockProber, \
             patch("routers.settings.set_tracker"), \
             patch("routers.settings.set_prober"), \
             patch("routers.settings.create_notification_internal"), \
             patch("routers.settings.update_notification_internal"), \
             patch("routers.settings.delete_notifications_by_source_internal"), \
             patch("channel_pipeline_engine.reset_channel_pipeline_engine") as mock_reset_engine:
            MockTracker.return_value = AsyncMock()
            new_prober = MagicMock()
            new_prober.start = AsyncMock()
            MockProber.return_value = new_prober
            response = await async_client.post("/api/settings/restart-services")

        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_reset_engine.assert_called_once()

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
    async def test_resets_all_stats(self, async_client, test_session):
        """Clears all 7 stats tables and reports the deleted count.

        Mutation guard: if any of the 7 db.query(...).delete() lines were removed, the
        corresponding table would still contain rows after the request — the per-table
        count assertion would fail, AND the total cleared count would be wrong.
        """
        from models import (
            HiddenChannelGroup,
            ChannelWatchStats,
            ChannelBandwidth,
            StreamStats,
            ChannelPopularityScore,
            SessionTelemetry,
            UniqueClientConnection,
        )
        from datetime import datetime, date

        # Seed one row per stats table so we can verify all 7 deletes actually fire.
        test_session.add(HiddenChannelGroup(group_id=1, group_name="Group A"))
        test_session.add(ChannelWatchStats(channel_id="ch-1", channel_name="ESPN", watch_count=5))
        test_session.add(ChannelBandwidth(
            channel_id="ch-1", channel_name="ESPN",
            date=date.today(),
        ))
        test_session.add(StreamStats(stream_id=1, stream_name="ESPN HD"))
        test_session.add(ChannelPopularityScore(
            channel_id="ch-1", channel_name="ESPN",
            score=75.0, rank=1, trend="stable", trend_percent=0.0,
            calculated_at=datetime.utcnow(),
        ))
        test_session.add(UniqueClientConnection(
            ip_address="10.0.0.1", channel_id="ch-1", channel_name="ESPN",
            date=date.today(),
            connected_at=datetime.utcnow(),
        ))
        test_session.add(SessionTelemetry(
            session_id="sess-1", observed_at=1000000,
            channel_id="ch-1", bytes_delta=0, buffer_event_count=0, poll_interval_ms=10000,
        ))
        test_session.commit()

        response = await async_client.post("/api/settings/reset-stats")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # All 7 tables must be empty after the reset.
        assert test_session.query(HiddenChannelGroup).count() == 0
        assert test_session.query(ChannelWatchStats).count() == 0
        assert test_session.query(ChannelBandwidth).count() == 0
        assert test_session.query(StreamStats).count() == 0
        assert test_session.query(ChannelPopularityScore).count() == 0
        assert test_session.query(UniqueClientConnection).count() == 0
        assert test_session.query(SessionTelemetry).count() == 0

        # Response details must account for all deleted rows.
        assert data["details"]["hidden_groups"] == 1
        assert data["details"]["watch_stats"] == 1
        assert data["details"]["bandwidth_records"] == 1
        assert data["details"]["stream_stats"] == 1
        assert data["details"]["popularity_scores"] == 1
        assert data["details"]["client_connections"] == 1
        assert data["details"]["session_telemetry"] == 1
        assert "Cleared 7 records" in data["message"]


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


class TestEmbySettingsPersistence:
    """bd-8wc6q: GET / POST plumbing for the Emby integration settings fields.

    Covers the three new fields (``emby_enabled`` / ``emby_base_url`` /
    ``emby_api_key``) appearing in the GET response (with the key masked),
    a full POST persisting all three, defaults when the fields are absent
    from the POST body, and the preserve-on-omit contract for the API key
    so a partial POST cannot silently clear the stored secret (parity with
    ``smtp_password`` and ``mcp_api_key``).
    """

    @pytest.mark.asyncio
    async def test_get_returns_emby_enabled_and_url(self, async_client):
        """GET /api/settings exposes emby_enabled and emby_base_url verbatim."""
        mock = _mock_settings(
            emby_enabled=True,
            emby_base_url="http://emby.local:8096",
            emby_api_key="secret-token",
        )

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200
        data = response.json()
        assert data["emby_enabled"] is True
        assert data["emby_base_url"] == "http://emby.local:8096"

    @pytest.mark.asyncio
    async def test_get_masks_emby_api_key(self, async_client):
        """The API key itself is never returned — only emby_api_key_configured."""
        mock = _mock_settings(emby_api_key="secret-token")

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200
        data = response.json()
        assert "emby_api_key" not in data
        assert data["emby_api_key_configured"] is True

    @pytest.mark.asyncio
    async def test_get_reports_unconfigured_when_no_key(self, async_client):
        """emby_api_key_configured=false when the key is empty."""
        mock = _mock_settings(emby_api_key="")

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200
        assert response.json()["emby_api_key_configured"] is False

    @pytest.mark.asyncio
    async def test_post_persists_all_three_fields(self, async_client):
        """POST /api/settings with the three Emby fields persists them via save_settings."""
        current = _mock_settings()
        captured: dict = {}

        def capture_save(new_settings):
            captured["emby_enabled"] = new_settings.emby_enabled
            captured["emby_base_url"] = new_settings.emby_base_url
            captured["emby_api_key"] = new_settings.emby_api_key

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
                "emby_enabled": True,
                "emby_base_url": "http://emby.local:8096",
                "emby_api_key": "fresh-emby-token",
            })

        assert response.status_code == 200, response.json()
        assert captured["emby_enabled"] is True
        assert captured["emby_base_url"] == "http://emby.local:8096"
        assert captured["emby_api_key"] == "fresh-emby-token"

    @pytest.mark.asyncio
    async def test_post_defaults_when_fields_absent(self, async_client):
        """When the Emby fields are omitted from the POST body, defaults apply
        (emby_enabled=False, emby_base_url="") — except the API key, which is
        preserved per the preserve-on-omit contract below."""
        current = _mock_settings(
            emby_enabled=True,
            emby_base_url="http://stale.example",
            emby_api_key="",
        )
        captured: dict = {}

        def capture_save(new_settings):
            captured["emby_enabled"] = new_settings.emby_enabled
            captured["emby_base_url"] = new_settings.emby_base_url

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
            })

        assert response.status_code == 200, response.json()
        # The Pydantic model defaults are applied when the fields are absent.
        assert captured["emby_enabled"] is False
        assert captured["emby_base_url"] == ""

    @pytest.mark.asyncio
    async def test_partial_post_preserves_stored_emby_api_key(self, async_client):
        """bd-8wc6q: a POST that omits ``emby_api_key`` must NOT clear the
        stored key. Same preserve-on-omit contract as ``smtp_password`` and
        ``mcp_api_key`` (bd-vj8n9) — operators can save the toggle and base
        URL without re-entering the secret on every save."""
        current = _mock_settings(emby_api_key="stored-emby-key-xyz")
        captured: dict = {}

        def capture_save(new_settings):
            captured["emby_api_key"] = new_settings.emby_api_key

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
                "emby_enabled": True,
                "emby_base_url": "http://emby.local:8096",
                # emby_api_key intentionally omitted
            })

        assert response.status_code == 200, response.json()
        assert captured["emby_api_key"] == "stored-emby-key-xyz", (
            "Partial POST cleared emby_api_key — preserve-on-omit broken"
        )


class TestPlexJellyfinSecretPreserveOnOmit:
    """bd-mlcla N3: symmetric preserve-on-omit tests for ``plex_token`` and
    ``jellyfin_api_key``.

    Only ``emby_api_key`` had this coverage. The settings router applies the
    same truthiness-based preserve-on-omit contract to all three media-server
    secrets (``routers/settings.py`` — ``request.X if request.X else
    current.X``): a partial POST that omits the secret keeps the stored value
    so the Settings UI can save non-secret fields without re-entering the key.
    """

    @pytest.mark.asyncio
    async def test_partial_post_preserves_stored_plex_token(self, async_client):
        """A POST that omits ``plex_token`` must NOT clear the stored token."""
        current = _mock_settings(plex_token="stored-plex-token-xyz")
        captured: dict = {}

        def capture_save(new_settings):
            captured["plex_token"] = new_settings.plex_token

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
                "plex_enabled": True,
                "plex_base_url": "http://plex.local:32400",
                # plex_token intentionally omitted
            })

        assert response.status_code == 200, response.json()
        assert captured["plex_token"] == "stored-plex-token-xyz", (
            "Partial POST cleared plex_token — preserve-on-omit broken"
        )

    @pytest.mark.asyncio
    async def test_partial_post_preserves_stored_jellyfin_api_key(self, async_client):
        """A POST that omits ``jellyfin_api_key`` must NOT clear the key."""
        current = _mock_settings(jellyfin_api_key="stored-jellyfin-key-xyz")
        captured: dict = {}

        def capture_save(new_settings):
            captured["jellyfin_api_key"] = new_settings.jellyfin_api_key

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
                "jellyfin_enabled": True,
                "jellyfin_base_url": "http://jellyfin.local:8096",
                # jellyfin_api_key intentionally omitted
            })

        assert response.status_code == 200, response.json()
        assert captured["jellyfin_api_key"] == "stored-jellyfin-key-xyz", (
            "Partial POST cleared jellyfin_api_key — preserve-on-omit broken"
        )


class TestTrustedMediaNetworksPersistence:
    """bd-mlcla N3: router-level preserve-on-omit + clear semantics for
    ``trusted_media_networks``.

    Unlike the secret fields (which preserve on BOTH ``None`` and ``""``),
    ``trusted_media_networks`` uses ``is not None`` semantics: ``None``
    (omitted, e.g. an older frontend bundle) preserves the stored list, but an
    explicit ``[]`` CLEARS it. This list is a soft IP-ranking hint only — it
    never gates attribution — so the clear path is safe."""

    @pytest.mark.asyncio
    async def test_omitted_field_preserves_existing_list(self, async_client):
        """An older frontend bundle omits the field (None) → stored list kept."""
        current = _mock_settings(
            trusted_media_networks=["172.18.0.0/16", "10.0.0.5"]
        )
        captured: dict = {}

        def capture_save(new_settings):
            captured["trusted_media_networks"] = new_settings.trusted_media_networks

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
                # trusted_media_networks intentionally omitted
            })

        assert response.status_code == 200, response.json()
        assert captured["trusted_media_networks"] == ["172.18.0.0/16", "10.0.0.5"], (
            "Omitted trusted_media_networks must preserve the stored list"
        )

    @pytest.mark.asyncio
    async def test_explicit_empty_list_clears_existing(self, async_client):
        """An explicit ``[]`` clears the stored list (the operator emptied it)."""
        current = _mock_settings(
            trusted_media_networks=["172.18.0.0/16", "10.0.0.5"]
        )
        captured: dict = {}

        def capture_save(new_settings):
            captured["trusted_media_networks"] = new_settings.trusted_media_networks

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
                "trusted_media_networks": [],
            })

        assert response.status_code == 200, response.json()
        assert captured["trusted_media_networks"] == [], (
            "Explicit empty list must CLEAR trusted_media_networks"
        )

    @pytest.mark.asyncio
    async def test_explicit_list_persists(self, async_client):
        """A non-empty list is persisted verbatim."""
        current = _mock_settings(trusted_media_networks=[])
        captured: dict = {}

        def capture_save(new_settings):
            captured["trusted_media_networks"] = new_settings.trusted_media_networks

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
                "trusted_media_networks": ["192.168.1.0/24"],
            })

        assert response.status_code == 200, response.json()
        assert captured["trusted_media_networks"] == ["192.168.1.0/24"]


class TestMCPStatusHostResolution:
    """bd-d2171: GET /api/settings/mcp-status must target the MCP container
    via MCP_HOST (default 'ecm-mcp' for canonical Docker bridge networking),
    not 'localhost' (which resolves to the ECM container itself under bridge
    networking). Operators using network_mode: host can set MCP_HOST=localhost.
    """

    def _make_captor_client(self, captured):
        """Build a stub AsyncClient that records the URL passed to .get()."""
        class _CaptorClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def get(self, url, *args, **kwargs):
                captured["url"] = url
                response = MagicMock()
                response.raise_for_status = MagicMock()
                response.json = MagicMock(return_value={"status": "ok"})
                return response

        return _CaptorClient

    @pytest.mark.asyncio
    async def test_default_host_is_ecm_mcp(self, async_client, monkeypatch):
        """Unset MCP_HOST -> URL targets the canonical compose service name."""
        monkeypatch.delenv("MCP_HOST", raising=False)
        monkeypatch.delenv("MCP_PORT", raising=False)

        captured = {}
        with patch("httpx.AsyncClient", self._make_captor_client(captured)):
            response = await async_client.get("/api/settings/mcp-status")

        assert response.status_code == 200
        assert response.json()["reachable"] is True
        assert captured["url"] == "http://ecm-mcp:6101/health"

    @pytest.mark.asyncio
    async def test_mcp_host_override_localhost(self, async_client, monkeypatch):
        """MCP_HOST=localhost restores host-networking back-compat shape."""
        monkeypatch.setenv("MCP_HOST", "localhost")
        monkeypatch.delenv("MCP_PORT", raising=False)

        captured = {}
        with patch("httpx.AsyncClient", self._make_captor_client(captured)):
            response = await async_client.get("/api/settings/mcp-status")

        assert response.status_code == 200
        assert response.json()["reachable"] is True
        assert captured["url"] == "http://localhost:6101/health"


class TestSecuritySettings:
    """Tests for the DBAS outbound-policy mode (nngkg).

    The ``ssrf_outbound_mode`` field is the persistence seam consumed by
    ``security/ssrf.py``. The frontend "first-run wizard" + Settings > Security
    section read/write it through:
      * GET /api/settings  (exposes the current mode)
      * PATCH /api/settings/security  (dedicated, preserve-everything write)
    A full POST /api/settings must NOT silently reset the mode (latent bug:
    SettingsRequest never accepted it, so it defaulted on every save).
    """

    @pytest.mark.asyncio
    async def test_get_exposes_ssrf_outbound_mode(self, async_client):
        """GET surfaces the persisted outbound mode for the Settings UI."""
        mock = _mock_settings(ssrf_outbound_mode="public_only")

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200
        assert response.json()["ssrf_outbound_mode"] == "public_only"

    @pytest.mark.asyncio
    async def test_get_defaults_lan_friendly(self, async_client):
        """The default mode (LAN-friendly) is surfaced when unset."""
        mock = _mock_settings()

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.json()["ssrf_outbound_mode"] == "lan_friendly"

    @pytest.mark.asyncio
    async def test_full_post_preserves_ssrf_outbound_mode(self, async_client):
        """A partial POST /api/settings must NOT reset the outbound mode.

        Reproduction: SettingsRequest doesn't accept ssrf_outbound_mode, so a
        normal settings save would rebuild DispatcharrSettings(...) with the
        field defaulting to "lan_friendly" — silently reverting an operator who
        had chosen public-only.
        """
        current = _mock_settings(ssrf_outbound_mode="public_only")
        captured = {}

        def capture_save(new_settings):
            captured["mode"] = new_settings.ssrf_outbound_mode

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
        assert captured["mode"] == "public_only", (
            "Partial POST reset ssrf_outbound_mode — operator choice not preserved"
        )

    @pytest.mark.asyncio
    async def test_patch_security_updates_mode(self, async_client):
        """PATCH /api/settings/security sets the mode and preserves everything else."""
        current = _mock_settings(ssrf_outbound_mode="lan_friendly", mcp_api_key="keep-me")
        captured = {}

        def capture_save(new_settings):
            captured["settings"] = new_settings

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings", side_effect=capture_save), \
             patch("routers.settings.clear_settings_cache"):
            response = await async_client.patch(
                "/api/settings/security",
                json={"ssrf_outbound_mode": "public_only"},
            )

        assert response.status_code == 200, response.json()
        assert response.json()["ssrf_outbound_mode"] == "public_only"
        # The same settings object is mutated + saved — other fields untouched.
        assert captured["settings"].ssrf_outbound_mode == "public_only"
        assert captured["settings"].mcp_api_key == "keep-me"

    @pytest.mark.asyncio
    async def test_patch_security_rejects_unknown_mode(self, async_client):
        """An unrecognised mode is a 400 — the field is a closed enum."""
        current = _mock_settings()

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings") as mock_save, \
             patch("routers.settings.clear_settings_cache"):
            response = await async_client.patch(
                "/api/settings/security",
                json={"ssrf_outbound_mode": "wide_open"},
            )

        assert response.status_code == 400
        mock_save.assert_not_called()


def _full_payload(mock):
    """Build a POST /api/settings body that mirrors a ``_mock_settings`` mock.

    Sending the stored values back means the field-level admin gate (kgz3k)
    sees NO admin-field change — so a test can flip exactly one field and
    assert only that field's behaviour. Maps the mock attributes the handler
    reads back into the SettingsRequest field names.
    """
    return {
        "url": mock.url,
        "auth_method": mock.auth_method,
        "username": mock.username,
        "theme": mock.theme,
        "date_format": mock.date_format,
        "user_timezone": mock.user_timezone,
        "timezone_preference": mock.timezone_preference,
        "emby_enabled": mock.emby_enabled,
        "emby_base_url": mock.emby_base_url,
        "plex_enabled": mock.plex_enabled,
        "plex_base_url": mock.plex_base_url,
        "jellyfin_enabled": mock.jellyfin_enabled,
        "jellyfin_base_url": mock.jellyfin_base_url,
        "discord_webhook_url": mock.discord_webhook_url,
        "telegram_bot_token": mock.telegram_bot_token,
        "telegram_chat_id": mock.telegram_chat_id,
        "smtp_host": mock.smtp_host,
        "smtp_port": mock.smtp_port,
        "smtp_user": mock.smtp_user,
        "smtp_from_email": mock.smtp_from_email,
        "smtp_from_name": mock.smtp_from_name,
        "smtp_use_tls": mock.smtp_use_tls,
        "smtp_use_ssl": mock.smtp_use_ssl,
        # skg35: echo the stored caps so the admin-field gate sees NO change
        # unless a test deliberately flips them.
        "max_auto_created_channels_per_run": mock.max_auto_created_channels_per_run,
        "max_auto_creation_log_entries": mock.max_auto_creation_log_entries,
        # bd-dgs64 (GH #591): echo the stored value so the admin-field gate
        # sees NO change unless a test deliberately flips it.
        "allow_multi_provider_auto_sync": mock.allow_multi_provider_auto_sync,
    }


def _save_mocks():
    """The standard patch set so a POST /api/settings reaches save without IO."""
    return (
        patch("routers.settings.save_settings"),
        patch("routers.settings.clear_settings_cache"),
        patch("routers.settings.reset_client"),
        patch("routers.settings.get_prober", return_value=None),
        patch("routers.settings.get_cache", return_value=MagicMock()),
    )


class TestSettingsAdminFieldGate:
    """kgz3k (A): field-level admin gate on POST /api/settings.

    A non-admin (auth enabled) — INCLUDING the MCP service principal — may
    change only per-user preferences; any attempt to change an admin-only
    field (outbound URLs, secrets, notification credentials, connection) is
    rejected 403. An admin may change them. These tests use the real
    ``non_admin_client`` / ``admin_client`` fixtures so the gate actually bites.
    """

    @pytest.mark.asyncio
    async def test_non_admin_can_change_only_prefs(self, non_admin_client):
        """Non-admin changing ONLY theme/date_format/timezone -> 200."""
        current = _mock_settings(theme="dark", date_format="auto", user_timezone="UTC")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload.update({"theme": "light", "date_format": "iso", "user_timezone": "America/Chicago"})
            response = await non_admin_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        saved = mock_save.call_args[0][0]
        assert saved.theme == "light"
        assert saved.date_format == "iso"
        assert saved.user_timezone == "America/Chicago"

    @pytest.mark.asyncio
    async def test_non_admin_changing_dispatcharr_url_rejected(self, non_admin_client):
        """Non-admin changing the Dispatcharr URL (admin-only) -> 403, no save."""
        current = _mock_settings(url="http://dispatcharr:8000")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["url"] = "http://evil.example.com:9999"
            response = await non_admin_client.post("/api/settings", json=payload)

        assert response.status_code == 403
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_admin_changing_emby_base_url_rejected(self, non_admin_client):
        """Non-admin changing an outbound media-server base URL -> 403."""
        current = _mock_settings(emby_enabled=True, emby_base_url="http://emby.lan:8096")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["emby_base_url"] = "http://169.254.169.254"
            response = await non_admin_client.post("/api/settings", json=payload)

        assert response.status_code == 403
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_admin_supplying_secret_rejected(self, non_admin_client):
        """Non-admin supplying a non-empty integration secret -> 403."""
        current = _mock_settings()
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["emby_api_key"] = "stolen-or-injected-key"
            response = await non_admin_client.post("/api/settings", json=payload)

        assert response.status_code == 403
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_admin_changing_discord_webhook_rejected(self, non_admin_client):
        """Non-admin changing the Discord webhook (POST-SSRF sink) -> 403."""
        current = _mock_settings(discord_webhook_url="https://discord.com/api/webhooks/1/abc")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["discord_webhook_url"] = "https://discord.com/api/webhooks/2/xyz"
            response = await non_admin_client.post("/api/settings", json=payload)

        assert response.status_code == 403
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_admin_changing_channel_cap_rejected(self, non_admin_client):
        """skg35: non-admin raising the auto-creation channel cap -> 403, no save.

        The cap is the GH #473 runaway-creation OOM safety valve; lowering or
        disabling it is an admin-only action, so it joins the admin-only field
        partition. A non-admin supplying a different value is rejected.
        """
        current = _mock_settings(max_auto_created_channels_per_run=500)
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["max_auto_created_channels_per_run"] = 5000
            response = await non_admin_client.post("/api/settings", json=payload)

        assert response.status_code == 403
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_admin_disabling_channel_cap_rejected(self, non_admin_client):
        """skg35: non-admin DISABLING the cap (0) is the dangerous case -> 403."""
        current = _mock_settings(max_auto_created_channels_per_run=500)
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["max_auto_created_channels_per_run"] = 0
            response = await non_admin_client.post("/api/settings", json=payload)

        assert response.status_code == 403
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_admin_changing_log_entries_cap_rejected(self, non_admin_client):
        """skg35: the sibling log-entries cap is admin-only too -> 403."""
        current = _mock_settings(max_auto_creation_log_entries=500)
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["max_auto_creation_log_entries"] = 2000
            response = await non_admin_client.post("/api/settings", json=payload)

        assert response.status_code == 403
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_admin_can_change_channel_cap_and_persists(self, admin_client):
        """skg35: an admin CAN raise the cap and the new value reaches save."""
        current = _mock_settings(max_auto_created_channels_per_run=500)
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["max_auto_created_channels_per_run"] = 5000
            response = await admin_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        saved = mock_save.call_args[0][0]
        assert saved.max_auto_created_channels_per_run == 5000

    @pytest.mark.asyncio
    async def test_non_admin_changing_multi_provider_auto_sync_rejected(self, non_admin_client):
        """bd-dgs64 (GH #591): non-admin flipping the override -> 403, no save.

        Enabling it removes the frontend guard that prevents auto-syncing the
        same (global) Dispatcharr channel group from two M3U providers at once
        — an install-wide duplicate-channel risk, so it joins the admin-only
        field partition alongside the auto-creation safety caps.
        """
        current = _mock_settings(allow_multi_provider_auto_sync=False)
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["allow_multi_provider_auto_sync"] = True
            response = await non_admin_client.post("/api/settings", json=payload)

        assert response.status_code == 403
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_admin_can_change_multi_provider_auto_sync_and_persists(self, admin_client):
        """bd-dgs64: an admin CAN enable the override and it reaches save."""
        current = _mock_settings(allow_multi_provider_auto_sync=False)
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["allow_multi_provider_auto_sync"] = True
            response = await admin_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        saved = mock_save.call_args[0][0]
        assert saved.allow_multi_provider_auto_sync is True

    @pytest.mark.asyncio
    async def test_admin_can_change_admin_field(self, admin_client):
        """Positive control: an admin (auth enabled) CAN change an admin field."""
        current = _mock_settings(emby_enabled=True, emby_base_url="http://emby.lan:8096")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            # Change to another resolvable-or-unresolvable LAN host (allowed in
            # lan_friendly + unresolvable-on-save is permitted).
            payload["emby_base_url"] = "http://emby2.lan:8096"
            response = await admin_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        mock_save.assert_called_once()


class TestSettingsMcpKeyGate:
    """kgz3k (A): the MCP service principal is treated as NON-admin for the
    settings admin-field gate, even though it carries is_admin=True elsewhere.
    """

    @pytest.mark.asyncio
    async def test_mcp_principal_changing_admin_field_rejected(self, async_client):
        """MCP-key-only caller changing an admin field -> 403, no save.

        Exercises the real auth path: auth enabled at the dependency layer +
        a static MCP key presented as the Bearer token. ``get_current_user``
        returns the MCP service principal, and ``_resolve_settings_admin``
        must classify it as non-admin for this endpoint.
        """
        from config import DispatcharrSettings

        current = _mock_settings(url="http://dispatcharr:8000")
        runtime_settings = DispatcharrSettings(
            url="http://dispatcharr:8000", username="u", password="p",
            mcp_api_key="mcp-secret-key",
        )
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             patch("auth.dependencies.get_settings", return_value=runtime_settings), \
             patch("routers.settings.get_auth_settings") as auth_mock, \
             s1 as mock_save, s2, s3, s4, s5:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            payload = _full_payload(current)
            payload["url"] = "http://evil.example.com:9999"
            response = await async_client.post(
                "/api/settings",
                json=payload,
                headers={"Authorization": "Bearer mcp-secret-key"},
            )

        assert response.status_code == 403
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_mcp_principal_changing_channel_cap_rejected(self, async_client):
        """skg35: MCP-key-only caller cannot disable/raise the auto-creation cap.

        The MCP key is a channel-management automation credential, not an
        operator identity. Letting it disable the GH #473 OOM safety valve would
        re-arm the runaway-creation crash, so it must be classified non-admin
        for this field (same posture as the connection/secret fields).
        """
        from config import DispatcharrSettings

        current = _mock_settings(max_auto_created_channels_per_run=500)
        runtime_settings = DispatcharrSettings(
            url="http://dispatcharr:8000", username="u", password="p",
            mcp_api_key="mcp-secret-key",
        )
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             patch("auth.dependencies.get_settings", return_value=runtime_settings), \
             patch("routers.settings.get_auth_settings") as auth_mock, \
             s1 as mock_save, s2, s3, s4, s5:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            payload = _full_payload(current)
            payload["max_auto_created_channels_per_run"] = 0
            response = await async_client.post(
                "/api/settings",
                json=payload,
                headers={"Authorization": "Bearer mcp-secret-key"},
            )

        assert response.status_code == 403
        mock_save.assert_not_called()


class TestSettingsSsrfOnSave:
    """kgz3k (B): every CHANGED, non-empty outbound base URL is SSRF-validated
    on save. Loopback/metadata/RFC1918-in-public-only -> 400; valid -> 200;
    empty -> allowed (disables integration).
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field,enable_field", [
        ("emby_base_url", "emby_enabled"),
        ("plex_base_url", "plex_enabled"),
        ("jellyfin_base_url", "jellyfin_enabled"),
        ("url", None),
    ])
    async def test_metadata_host_rejected_for_each_url_field(self, async_client, field, enable_field):
        """Saving http://169.254.169.254 into any base URL field -> 400."""
        current = _mock_settings()
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload[field] = "http://169.254.169.254"
            if enable_field:
                payload[enable_field] = True
            response = await async_client.post("/api/settings", json=payload)

        assert response.status_code == 400, response.json()
        assert "169.254" in response.json()["detail"] or "denied" in response.json()["detail"].lower()
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["lan_friendly", "public_only"])
    @pytest.mark.parametrize("field,enable_field", [
        ("emby_base_url", "emby_enabled"),
        ("url", None),
    ])
    async def test_link_local_metadata_rejected_in_both_modes(
        self, async_client, mode, field, enable_field,
    ):
        """IMDS stays blocked in BOTH modes — GH #754 / ``0yh70``.

        This is the half that is easy to lose while making loopback work.
        Reasoning for the both-modes call is on
        ``test_ssrf_guard.TestAlwaysOnDenylist
        .test_link_local_denied_in_both_modes_deliberately``.
        """
        from security.ssrf import SSRFMode
        current = _mock_settings(ssrf_outbound_mode=mode)
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode(mode)), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload[field] = "http://169.254.169.254"
            # Changing the connection URL re-requires the password; supplying
            # it keeps the 400 attributable to the SSRF policy, not the gate.
            payload["password"] = "secret"
            if enable_field:
                payload[enable_field] = True
            response = await async_client.post("/api/settings", json=payload)

        assert response.status_code == 400, response.json()
        detail = response.json()["detail"]
        assert "169.254" in detail or "denied" in detail.lower(), detail
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_gh754_localhost_dispatcharr_url_saves_in_lan_friendly(self, async_client):
        """GH #754: ``http://localhost:9191`` saves under the shipped default.

        The reporter runs Dispatcharr behind a shared gluetun network, so
        ``localhost`` is the only address that reaches it. Test Connection
        succeeded and the running app polls that URL happily — only the save
        validator refused it.

        DNS is patched with the record set a real container returns
        (``::1`` first, then ``127.0.0.1`` — captured from ``docker run --rm
        python:3.12-slim``), because Docker's generated ``/etc/hosts`` maps
        ``localhost`` dual-stack and §9.4 item 3 rejects the whole request if
        ANY record is denied. A v4-only fixture would pass without proving the
        reported case.
        """
        from security.ssrf import SSRFMode
        current = _mock_settings(ssrf_outbound_mode="lan_friendly")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode.LAN_FRIENDLY), \
             _patch_ssrf_dns("::1", "127.0.0.1"), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["url"] = "http://localhost:9191"
            # Changing the connection URL re-requires the password.
            payload["password"] = "secret"
            response = await async_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        saved = mock_save.call_args[0][0]
        # Round-trips verbatim — scheme + netloc, port preserved.
        assert saved.url == "http://localhost:9191"

    @pytest.mark.asyncio
    async def test_gh754_localhost_dispatcharr_url_rejected_in_public_only(self, async_client):
        """The same URL is still refused under ``public_only`` (the opt-in)."""
        from security.ssrf import SSRFMode
        current = _mock_settings(ssrf_outbound_mode="public_only")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode.PUBLIC_ONLY), \
             _patch_ssrf_dns("::1", "127.0.0.1"), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["url"] = "http://localhost:9191"
            payload["password"] = "secret"
            response = await async_client.post("/api/settings", json=payload)

        assert response.status_code == 400, response.json()
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("base_url", [
        "http://127.0.0.1:8096",
        "http://127.0.0.5:8096",
        "http://[::1]:8096",
    ])
    async def test_loopback_literal_allowed_in_lan_friendly(self, async_client, base_url):
        """Literal loopback saves under the shipped default (ADR-012 D4)."""
        from security.ssrf import SSRFMode
        current = _mock_settings(ssrf_outbound_mode="lan_friendly")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode.LAN_FRIENDLY), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["emby_enabled"] = True
            payload["emby_base_url"] = base_url
            response = await async_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        assert mock_save.call_args[0][0].emby_base_url == base_url

    @pytest.mark.asyncio
    @pytest.mark.parametrize("base_url", [
        "http://127.0.0.1:8096",
        "http://127.0.0.5:8096",
        "http://[::1]:8096",
    ])
    async def test_loopback_literal_rejected_in_public_only(self, async_client, base_url):
        """...and is still refused under ``public_only``."""
        from security.ssrf import SSRFMode
        current = _mock_settings(ssrf_outbound_mode="public_only")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode.PUBLIC_ONLY), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["emby_enabled"] = True
            payload["emby_base_url"] = base_url
            response = await async_client.post("/api/settings", json=payload)

        assert response.status_code == 400, response.json()
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_rfc1918_allowed_in_lan_friendly(self, async_client):
        """A private LAN base URL saves under lan_friendly (the default)."""
        from security.ssrf import SSRFMode
        current = _mock_settings(ssrf_outbound_mode="lan_friendly")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode.LAN_FRIENDLY), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["emby_enabled"] = True
            payload["emby_base_url"] = "http://192.168.1.50:8096"
            response = await async_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        saved = mock_save.call_args[0][0]
        # Path-stripped, scheme+netloc only.
        assert saved.emby_base_url == "http://192.168.1.50:8096"

    @pytest.mark.asyncio
    async def test_rfc1918_rejected_in_public_only(self, async_client):
        """The same private LAN base URL is rejected under public_only."""
        from security.ssrf import SSRFMode
        current = _mock_settings(ssrf_outbound_mode="public_only")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode.PUBLIC_ONLY), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["emby_enabled"] = True
            payload["emby_base_url"] = "http://192.168.1.50:8096"
            response = await async_client.post("/api/settings", json=payload)

        assert response.status_code == 400, response.json()
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_http_scheme_rejected(self, async_client):
        """A file:// base URL -> 400 (scheme allowlist)."""
        current = _mock_settings()
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["plex_enabled"] = True
            payload["plex_base_url"] = "file:///etc/passwd"
            response = await async_client.post("/api/settings", json=payload)

        assert response.status_code == 400
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_base_url_allowed_disables_integration(self, async_client):
        """Clearing a base URL (empty) is allowed — operator disabling it."""
        current = _mock_settings(emby_enabled=True, emby_base_url="http://192.168.1.50:8096")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["emby_enabled"] = False
            payload["emby_base_url"] = ""
            response = await async_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        saved = mock_save.call_args[0][0]
        assert saved.emby_base_url == ""

    @pytest.mark.asyncio
    async def test_runtime_caller_never_hits_blocked_host(self, async_client):
        """After a rejected save, no outbound request is issued to the host.

        Wraps the SSRF resolver so we can assert the blocked host is never
        connected to: the save is rejected before any media-server client is
        constructed, so the runtime poller keeps the OLD (unset) base URL and
        never GETs the attacker host.
        """
        import respx
        current = _mock_settings()
        s1, s2, s3, s4, s5 = _save_mocks()
        with respx.mock(assert_all_called=False, assert_all_mocked=False) as respx_mock:
            metadata_route = respx_mock.get("http://169.254.169.254/Sessions")
            with patch("routers.settings.get_settings", return_value=current), \
                 s1 as mock_save, s2, s3, s4, s5:
                payload = _full_payload(current)
                payload["emby_enabled"] = True
                payload["emby_base_url"] = "http://169.254.169.254"
                response = await async_client.post("/api/settings", json=payload)

            assert response.status_code == 400
            mock_save.assert_not_called()
            # The metadata endpoint was NEVER called — the save was rejected
            # before any media-server client could be constructed.
            assert not metadata_route.called
            assert len(respx_mock.calls) == 0


class TestSettingsDiscordWebhookOnSave:
    """kgz3k (C): discord_webhook_url is validated against the Discord host
    allowlist on save (POST-SSRF sink). Path is preserved (NOT _sanitize_base_url).
    """

    @pytest.mark.asyncio
    async def test_valid_discord_webhook_saves(self, async_client):
        current = _mock_settings()
        s1, s2, s3, s4, s5 = _save_mocks()
        url = "https://discord.com/api/webhooks/123456/abcdefg"
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["discord_webhook_url"] = url
            response = await async_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        saved = mock_save.call_args[0][0]
        # Path is PRESERVED (webhooks need it).
        assert saved.discord_webhook_url == url

    @pytest.mark.asyncio
    async def test_non_discord_host_rejected(self, async_client):
        """A non-Discord webhook host -> 400 (POST-SSRF block)."""
        current = _mock_settings()
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["discord_webhook_url"] = "https://evil.example.com/api/webhooks/1/x"
            response = await async_client.post("/api/settings", json=payload)

        assert response.status_code == 400
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_discord_webhook_allowed(self, async_client):
        """Clearing the webhook (empty) is allowed — disables Discord."""
        current = _mock_settings(discord_webhook_url="https://discord.com/api/webhooks/1/old")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["discord_webhook_url"] = ""
            response = await async_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        saved = mock_save.call_args[0][0]
        assert saved.discord_webhook_url == ""
