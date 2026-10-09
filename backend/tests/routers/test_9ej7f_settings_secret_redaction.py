"""bead 9ej7f — GET /api/settings must not hand notification credentials to a
caller that is not allowed to write them.

THE ORIGINAL GAP
----------------

``discord_webhook_url``, ``telegram_bot_token`` and ``telegram_chat_id`` were
returned verbatim by GET /api/settings to any authenticated non-admin and to
the static MCP service key, while ``_resolve_settings_admin`` denied those same
principals WRITE on the same fields. 9ej7f redacted them on read for exactly
the callers who could not write them.

WHAT GH #1026 CHANGED
---------------------

Discord and Telegram destinations are Alert Methods rows now
(``notification_migration`` moves the legacy values on startup and after a
restore), so the three fields left the /api/settings contract entirely. That
is a strictly stronger form of 9ej7f's guarantee: NO caller — admin,
non-admin, MCP principal or auth-disabled — reads them from GET, and no POST
writes them. The credentials are reachable only through the human-admin-gated
alert-method routes, which mask them (bead 9kwzp.13).

What this file now pins:

* GET never carries the three fields, for every caller class, even while the
  settings model still holds legacy values.
* ``discord_configured`` / ``telegram_configured`` stay, and report enabled
  alert-method rows.
* POST ignores the three fields from every caller and carries the stored
  values through, so a save can never wipe a not-yet-migrated value.
* The backup redaction partition (``config.ADMIN_ONLY_READ_REDACTED_FIELDS``)
  still names all three, because old settings and old backups carry them.
"""
from unittest.mock import patch

import pytest

from config import ADMIN_ONLY_READ_REDACTED_FIELDS, DispatcharrSettings

from .test_settings import _full_payload, _mock_settings, _save_mocks


WEBHOOK = "https://discord.com/api/webhooks/1/abcdefghijklmnop"
# Telegram issues ``<bot id>:<exactly 35 characters>``. The 35 characters here
# are just the alphabet followed by digits, and the value is assembled from
# parts so no token-shaped literal exists on any single line. Same convention
# as ``docs/pytest_conventions.md``: keep the real SHAPE, look like nothing.
BOT_TOKEN = "123456789:" + "AAEabcdefghijklmno" + "pqrstuvwxyz012345"
CHAT_ID = "-1001234567890"

MCP_KEY = "mcp-secret-key-9ej7f"

RETIRED_FIELDS = ("discord_webhook_url", "telegram_bot_token", "telegram_chat_id")


def _configured_settings(**overrides):
    """Stored settings still holding all three legacy notification values."""
    base = {
        "discord_webhook_url": WEBHOOK,
        "telegram_bot_token": BOT_TOKEN,
        "telegram_chat_id": CHAT_ID,
    }
    base.update(overrides)
    return _mock_settings(**base)


def _mcp_runtime_settings():
    """Config settings carrying the static MCP key so ``_is_mcp_service_token``
    matches the Bearer token and ``get_current_user`` returns the principal."""
    return DispatcharrSettings(
        url="http://dispatcharr:8000",
        username="u",
        password="p",
        mcp_api_key=MCP_KEY,
    )


def _assert_no_retired_field(data: dict) -> None:
    for field in RETIRED_FIELDS:
        assert field not in data, f"GET /api/settings exposed {field}"
    body = repr(data)
    for secret in (WEBHOOK, BOT_TOKEN, CHAT_ID):
        assert secret not in body


# ---------------------------------------------------------------------------
# Read: no caller sees the retired fields
# ---------------------------------------------------------------------------
class TestRetiredNotificationFieldsAbsentOnRead:
    @pytest.mark.asyncio
    async def test_non_admin_get_has_none_of_the_three(self, non_admin_client):
        with patch("routers.settings.get_settings", return_value=_configured_settings()), \
             patch("routers.settings._has_discord_alert_method", return_value=False), \
             patch("routers.settings._has_telegram_alert_method", return_value=False):
            response = await non_admin_client.get("/api/settings")

        assert response.status_code == 200, response.json()
        _assert_no_retired_field(response.json())

    @pytest.mark.asyncio
    async def test_mcp_principal_get_has_none_of_the_three(self, async_client):
        with patch("auth.dependencies.get_settings", return_value=_mcp_runtime_settings()), \
             patch("routers.settings.get_auth_settings") as auth_mock, \
             patch("routers.settings.get_settings", return_value=_configured_settings()), \
             patch("routers.settings._has_discord_alert_method", return_value=False), \
             patch("routers.settings._has_telegram_alert_method", return_value=False):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.get(
                "/api/settings",
                headers={"Authorization": f"Bearer {MCP_KEY}"},
            )

        assert response.status_code == 200, response.json()
        _assert_no_retired_field(response.json())

    @pytest.mark.asyncio
    async def test_admin_get_has_none_of_the_three(self, admin_client):
        """Admins manage Discord / Telegram in Alert Methods now, so even an
        admin's GET no longer carries the legacy values."""
        with patch("routers.settings.get_settings", return_value=_configured_settings()), \
             patch("routers.settings._has_discord_alert_method", return_value=False), \
             patch("routers.settings._has_telegram_alert_method", return_value=False):
            response = await admin_client.get("/api/settings")

        assert response.status_code == 200, response.json()
        _assert_no_retired_field(response.json())

    @pytest.mark.asyncio
    async def test_auth_disabled_get_has_none_of_the_three(self, async_client):
        with patch("routers.settings.get_settings", return_value=_configured_settings()), \
             patch("routers.settings._has_discord_alert_method", return_value=False), \
             patch("routers.settings._has_telegram_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200, response.json()
        _assert_no_retired_field(response.json())

    @pytest.mark.asyncio
    @pytest.mark.parametrize("discord_row,telegram_row", [(True, False), (False, True), (False, False)])
    async def test_configured_booleans_follow_alert_method_rows(
        self, non_admin_client, discord_row, telegram_row
    ):
        """The badges are driven by enabled Alert Methods rows, not by the
        legacy settings values (which the model may still hold)."""
        with patch("routers.settings.get_settings", return_value=_configured_settings()), \
             patch("routers.settings._has_discord_alert_method", return_value=discord_row), \
             patch("routers.settings._has_telegram_alert_method", return_value=telegram_row):
            response = await non_admin_client.get("/api/settings")

        data = response.json()
        assert data["discord_configured"] is discord_row
        assert data["telegram_configured"] is telegram_row


# ---------------------------------------------------------------------------
# Write: no caller writes the retired fields, and none can wipe them
# ---------------------------------------------------------------------------
class TestRetiredNotificationFieldsIgnoredOnWrite:
    @pytest.mark.asyncio
    async def test_non_admin_save_echoing_empty_values_keeps_stored(self, non_admin_client):
        current = _configured_settings(theme="dark")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload.update({"theme": "light", **{f: "" for f in RETIRED_FIELDS}})
            response = await non_admin_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        saved = mock_save.call_args[0][0]
        assert saved.discord_webhook_url == WEBHOOK
        assert saved.telegram_bot_token == BOT_TOKEN
        assert saved.telegram_chat_id == CHAT_ID
        assert saved.theme == "light"

    @pytest.mark.asyncio
    async def test_non_admin_new_token_is_ignored(self, non_admin_client):
        current = _configured_settings()
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["telegram_bot_token"] = "999999999:" + "ZZZattackercontrolledtoken"
            response = await non_admin_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        assert mock_save.call_args[0][0].telegram_bot_token == BOT_TOKEN

    @pytest.mark.asyncio
    async def test_admin_blank_does_not_wipe_an_unmigrated_value(self, admin_client):
        """Clearing Discord is done by deleting its alert method now. A
        settings save, even an admin's, can never discard a legacy value the
        migration has not moved yet."""
        current = _configured_settings()
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["discord_webhook_url"] = ""
            response = await admin_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        assert mock_save.call_args[0][0].discord_webhook_url == WEBHOOK

    @pytest.mark.asyncio
    async def test_mcp_principal_cannot_set_a_webhook(self, async_client):
        current = _configured_settings()
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("auth.dependencies.get_settings", return_value=_mcp_runtime_settings()), \
             patch("routers.settings.get_auth_settings") as auth_mock, \
             patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            payload = _full_payload(current)
            payload["discord_webhook_url"] = "https://discord.com/api/webhooks/9/zzz"
            response = await async_client.post(
                "/api/settings",
                json=payload,
                headers={"Authorization": f"Bearer {MCP_KEY}"},
            )

        # The payload changes no admin-only field (the webhook is not one any
        # more), so the save goes through, and the webhook is untouched.
        assert response.status_code == 200, response.json()
        assert mock_save.call_args[0][0].discord_webhook_url == WEBHOOK


# ---------------------------------------------------------------------------
# The field partition itself
# ---------------------------------------------------------------------------
class TestRetiredFieldPartition:
    def test_backup_partition_still_names_the_three_notification_credentials(self):
        """Old settings files and old backups still carry these values, so
        every artifact must keep redacting them."""
        assert ADMIN_ONLY_READ_REDACTED_FIELDS == frozenset(RETIRED_FIELDS)

    def test_settings_contract_no_longer_carries_them(self):
        from routers.settings import (
            SettingsRequest,
            SettingsResponse,
            _ADMIN_ONLY_SETTINGS_FIELDS,
        )

        for field in RETIRED_FIELDS:
            assert field not in SettingsRequest.model_fields
            assert field not in SettingsResponse.model_fields
            assert field not in _ADMIN_ONLY_SETTINGS_FIELDS
