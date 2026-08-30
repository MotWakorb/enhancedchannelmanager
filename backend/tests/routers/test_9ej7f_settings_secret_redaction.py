"""bead 9ej7f — GET /api/settings must not hand notification credentials to a
caller that is not allowed to write them.

THE GAP
-------

``_ADMIN_ONLY_PROTECTED_FIELDS`` (password, dispatcharr_api_key, emby_api_key,
plex_token, jellyfin_api_key, smtp_password) are surfaced on read as
``*_configured`` booleans only. ``discord_webhook_url``,
``telegram_bot_token`` and ``telegram_chat_id`` sat in
``_ADMIN_ONLY_SETTINGS_FIELDS`` instead, which gates WRITE only, and were
returned verbatim by a handler that carried no dependency at all. So any
authenticated non-admin, and the static MCP service key, could read a live
Discord webhook and Telegram bot token — while ``_resolve_settings_admin``
goes to real lengths to deny those same principals WRITE on those same fields.

THE FIX AND WHY THIS SHAPE
--------------------------

Read is now gated by the SAME predicate as write (``_resolve_settings_admin``):
a field you may not write, you may not read. A caller that predicate calls
non-admin — an authenticated non-admin OR the MCP service principal — gets an
empty string plus the ``discord_configured`` / ``telegram_configured`` booleans
that were already in the response.

The round-trip hazard is what rules out a partial mask (``***abcd``). The
Settings UI loads these fields into text inputs and POSTs them straight back,
so any non-empty placeholder would overwrite a working webhook with the
placeholder on the next save. An empty redaction cannot: the write path takes
these three from STORED settings for a non-admin, and the admin-field gate
treats an empty value from a non-admin as the redacted placeholder coming
back, not a request to clear the field.

An admin keeps literal read AND literal write semantics, so clearing a webhook
to disable the integration still works — which the ``*_configured``
preserve-on-omit shape would have taken away.
"""
from unittest.mock import patch

import pytest

from config import DispatcharrSettings

from .test_settings import _full_payload, _mock_settings, _save_mocks


WEBHOOK = "https://discord.com/api/webhooks/1/abcdefghijklmnop"
# Telegram issues ``<bot id>:<exactly 35 characters>``. The 35 characters here
# are just the alphabet followed by digits, and the value is assembled from
# parts so no token-shaped literal exists on any single line. Same convention
# as ``docs/pytest_conventions.md``: keep the real SHAPE, look like nothing.
BOT_TOKEN = "123456789:" + "AAEabcdefghijklmno" + "pqrstuvwxyz012345"
CHAT_ID = "-1001234567890"

MCP_KEY = "mcp-secret-key-9ej7f"


def _configured_settings(**overrides):
    """Stored settings with all three notification credentials populated.

    ``_mock_settings`` pins the ``is_*_configured()`` predicates to False, so
    set them here too — a redaction test is only meaningful against settings
    the instance itself reports as configured.
    """
    base = {
        "discord_webhook_url": WEBHOOK,
        "telegram_bot_token": BOT_TOKEN,
        "telegram_chat_id": CHAT_ID,
    }
    base.update(overrides)
    mock = _mock_settings(**base)
    mock.is_discord_configured.return_value = bool(mock.discord_webhook_url)
    mock.is_telegram_configured.return_value = bool(
        mock.telegram_bot_token and mock.telegram_chat_id
    )
    return mock


def _mcp_runtime_settings():
    """Config settings carrying the static MCP key so ``_is_mcp_service_token``
    matches the Bearer token and ``get_current_user`` returns the principal."""
    return DispatcharrSettings(
        url="http://dispatcharr:8000",
        username="u",
        password="p",
        mcp_api_key=MCP_KEY,
        discord_webhook_url=WEBHOOK,
        telegram_bot_token=BOT_TOKEN,
        telegram_chat_id=CHAT_ID,
    )


# ---------------------------------------------------------------------------
# Read redaction
# ---------------------------------------------------------------------------
class TestNotificationCredentialsRedactedOnRead:
    @pytest.mark.asyncio
    async def test_non_admin_get_redacts_all_three(self, non_admin_client):
        """An authenticated non-admin reads empty strings, never the secrets."""
        mock = _configured_settings()

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await non_admin_client.get("/api/settings")

        assert response.status_code == 200, response.json()
        data = response.json()
        assert data["discord_webhook_url"] == ""
        assert data["telegram_bot_token"] == ""
        assert data["telegram_chat_id"] == ""

    @pytest.mark.asyncio
    async def test_non_admin_get_still_reports_configured_booleans(
        self, non_admin_client
    ):
        """Redaction hides the VALUES, not the fact that they are configured —
        the Settings UI badge is driven by these booleans."""
        mock = _configured_settings()

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await non_admin_client.get("/api/settings")

        data = response.json()
        assert data["discord_configured"] is True
        assert data["telegram_configured"] is True

    @pytest.mark.asyncio
    async def test_mcp_principal_get_redacts_all_three(self, async_client):
        """The static MCP service key is admin-equivalent elsewhere but is
        deliberately NON-admin for settings (kgz3k) — so it reads empty too."""
        mock = _configured_settings()

        with patch("auth.dependencies.get_settings", return_value=_mcp_runtime_settings()), \
             patch("routers.settings.get_auth_settings") as auth_mock, \
             patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.get(
                "/api/settings",
                headers={"Authorization": f"Bearer {MCP_KEY}"},
            )

        assert response.status_code == 200, response.json()
        data = response.json()
        assert data["discord_webhook_url"] == ""
        assert data["telegram_bot_token"] == ""
        assert data["telegram_chat_id"] == ""

    @pytest.mark.asyncio
    async def test_admin_get_returns_real_values(self, admin_client):
        """Positive control — an operator admin still sees what they manage."""
        mock = _configured_settings()

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await admin_client.get("/api/settings")

        assert response.status_code == 200, response.json()
        data = response.json()
        assert data["discord_webhook_url"] == WEBHOOK
        assert data["telegram_bot_token"] == BOT_TOKEN
        assert data["telegram_chat_id"] == CHAT_ID

    @pytest.mark.asyncio
    async def test_auth_disabled_get_returns_real_values(self, async_client):
        """Auth-disabled / setup-incomplete installs are unchanged: the same
        predicate that lets that caller WRITE these fields lets it read them."""
        mock = _configured_settings()

        with patch("routers.settings.get_settings", return_value=mock), \
             patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200, response.json()
        data = response.json()
        assert data["discord_webhook_url"] == WEBHOOK
        assert data["telegram_bot_token"] == BOT_TOKEN
        assert data["telegram_chat_id"] == CHAT_ID


# ---------------------------------------------------------------------------
# The redacted value must not round-trip back as a wipe
# ---------------------------------------------------------------------------
class TestRedactedValueCannotWipeStoredCredentials:
    @pytest.mark.asyncio
    async def test_non_admin_save_with_empty_values_is_allowed(
        self, non_admin_client
    ):
        """A non-admin saving personal prefs echoes the REDACTED (empty)
        values back. That must not be read as an attempt to change an
        admin-only field — it is the placeholder returning."""
        current = _configured_settings(theme="dark")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload.update({
                "theme": "light",
                "discord_webhook_url": "",
                "telegram_bot_token": "",
                "telegram_chat_id": "",
            })
            response = await non_admin_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_admin_save_preserves_stored_credentials(
        self, non_admin_client
    ):
        """The stored webhook / bot token / chat id survive a non-admin save
        that echoed empty values back."""
        current = _configured_settings(theme="dark")
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload.update({
                "theme": "light",
                "discord_webhook_url": "",
                "telegram_bot_token": "",
                "telegram_chat_id": "",
            })
            await non_admin_client.post("/api/settings", json=payload)

        saved = mock_save.call_args[0][0]
        assert saved.discord_webhook_url == WEBHOOK
        assert saved.telegram_bot_token == BOT_TOKEN
        assert saved.telegram_chat_id == CHAT_ID
        assert saved.theme == "light"

    @pytest.mark.asyncio
    async def test_non_admin_supplying_new_telegram_token_still_rejected(
        self, non_admin_client
    ):
        """Empty is the placeholder; a NON-empty value is still a real change
        attempt and is still refused."""
        current = _configured_settings()
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["telegram_bot_token"] = "999999999:ZZZattackercontrolledtoken"
            response = await non_admin_client.post("/api/settings", json=payload)

        assert response.status_code == 403
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_admin_can_still_clear_the_discord_webhook(self, admin_client):
        """An admin blanking the field is a genuine 'disable the integration'
        action and must still clear the stored value — the reason read stays
        literal for admins instead of adopting preserve-on-omit."""
        current = _configured_settings()
        s1, s2, s3, s4, s5 = _save_mocks()
        with patch("routers.settings.get_settings", return_value=current), \
             s1 as mock_save, s2, s3, s4, s5:
            payload = _full_payload(current)
            payload["discord_webhook_url"] = ""
            response = await admin_client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.json()
        saved = mock_save.call_args[0][0]
        assert saved.discord_webhook_url == ""


# ---------------------------------------------------------------------------
# MCP principal write path (already gated) stays gated
# ---------------------------------------------------------------------------
class TestMcpPrincipalWriteStillRejected:
    @pytest.mark.asyncio
    async def test_mcp_principal_setting_webhook_rejected(self, async_client):
        """Regression guard: relaxing the empty-value case in the admin-field
        gate must not open a real write for the MCP key."""
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

        assert response.status_code == 403, response.json()
        mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# The field partition itself
# ---------------------------------------------------------------------------
class TestRedactedFieldPartition:
    def test_partition_names_exactly_the_three_notification_credentials(self):
        from routers.settings import _ADMIN_ONLY_READ_REDACTED_FIELDS

        assert _ADMIN_ONLY_READ_REDACTED_FIELDS == frozenset({
            "discord_webhook_url",
            "telegram_bot_token",
            "telegram_chat_id",
        })

    def test_every_redacted_field_is_also_admin_only_on_write(self):
        """Read-redaction is derived from the write gate, so a field can never
        be hidden from a caller that is nonetheless allowed to change it."""
        from routers.settings import (
            _ADMIN_ONLY_READ_REDACTED_FIELDS,
            _ADMIN_ONLY_SETTINGS_FIELDS,
        )

        assert _ADMIN_ONLY_READ_REDACTED_FIELDS <= set(_ADMIN_ONLY_SETTINGS_FIELDS)
