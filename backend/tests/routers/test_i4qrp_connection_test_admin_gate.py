"""bead i4qrp — the four credential-carrying connection-test endpoints must be
driven by a human operator admin.

THE GAP
-------

``POST /api/settings/test``, ``/test-smtp``, ``/test-discord`` and
``/test-telegram`` carried NO route dependency, while the sibling Emby / Plex /
Jellyfin test endpoints carried ``RequireAdminIfEnabled``. All four reach the
network with operator-supplied or STORED credentials and echo the upstream
result back, and all four were reachable by any authenticated caller including
the static MCP API key (``main.auth_middleware`` accepts it directly). That
contradicts kgz3k / 6n76m, which deny the MCP principal the ability to change
these same outbound targets and secrets.

WHY ``RequireHumanAdminForOutboundTest`` AND NOT ``RequireAdminIfEnabled``
-------------------------------------------------------------------------

``_build_mcp_service_principal`` sets ``is_admin=True``, so the plain admin gate
ADMITS the MCP key — it would close the non-admin half of the bead and leave
the half the bead is actually about wide open. The human-admin variant is the
existing dependency for exactly this (it gates the backup-restore endpoints for
the same reason).

FIRST-RUN PATH
--------------

Verified, not assumed. ``require_admin_if_enabled`` returns None (allows) when
``require_auth`` is False or ``setup_complete`` is False, so a genuine first run
is untouched — pinned by ``TestSetupModeStillAllowed`` below. The only frontend
callers are ``SettingsModal`` (rendered inside ``ProtectedRoute``'s children, so
post-authentication) and the Settings tab; ``SetupPage.tsx`` calls only
``completeSetup``.
"""
from unittest.mock import AsyncMock, patch

import pytest

from config import DispatcharrSettings
from models import User


MCP_KEY = "mcp-secret-key-i4qrp"

# (path, body) for each of the four ungated sinks.
#
# The credential VALUES below are angle-bracket placeholders, not
# credential-shaped strings. Every test here asserts on the gate, which
# answers before the handler ever reads a body field, so no value is
# consumed; writing them as visible placeholders keeps that fact obvious to a
# reader and keeps the file free of anything a scanner has to reason about.
TEST_ENDPOINTS = [
    (
        "/api/settings/test",
        {
            "url": "http://dispatcharr:8000",
            "auth_method": "password",
            "username": "admin",
            "password": "<synthetic-dispatcharr-password>",
        },
    ),
    (
        "/api/settings/test-smtp",
        {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_user": "alerts@example.com",
            "smtp_password": "<synthetic-smtp-password>",
            "smtp_from_email": "alerts@example.com",
            "smtp_from_name": "ECM",
            "smtp_use_tls": True,
            "smtp_use_ssl": False,
            "to_email": "operator@example.com",
        },
    ),
    (
        "/api/settings/test-discord",
        {"webhook_url": "https://discord.com/api/webhooks/1/abcdefghijklmnop"},
    ),
    (
        "/api/settings/test-telegram",
        {"bot_token": "123456789:AAEabcdefghij", "chat_id": "-1001234567890"},
    ),
]

ENDPOINT_IDS = [path.rsplit("/", 1)[-1] or "test" for path, _ in TEST_ENDPOINTS]


def _mcp_runtime_settings():
    """Config settings carrying the static MCP key so ``_is_mcp_service_token``
    matches the Bearer token and ``get_current_user`` returns the principal."""
    return DispatcharrSettings(
        url="http://dispatcharr:8000",
        username="u",
        password="p",
        mcp_api_key=MCP_KEY,
    )


def _non_admin_user():
    return User(
        id=5150,
        username="regular-user",
        is_admin=False,
        is_active=True,
        auth_provider="local",
    )


def _admin_user():
    return User(
        id=5151,
        username="admin-user",
        is_admin=True,
        is_active=True,
        auth_provider="local",
    )


def _outbound_mocks():
    """Patch every outbound client the four handlers could reach.

    The handlers import ``httpx`` / ``smtplib`` / ``aiohttp`` inside the
    function body, so the patch targets are the source modules. A refused
    request must never construct any of them; a test that WRONGLY reaches a
    handler body then fails on the assertion rather than on a real socket.
    """
    return (
        patch("httpx.AsyncClient"),
        patch("smtplib.SMTP"),
        patch("aiohttp.ClientSession"),
    )


# ---------------------------------------------------------------------------
# Non-admin is refused
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path,body", TEST_ENDPOINTS, ids=ENDPOINT_IDS)
@pytest.mark.asyncio
async def test_non_admin_refused_on_connection_test(async_client, path, body):
    """An authenticated non-admin gets 403 and no outbound request is made."""
    m1, m2, m3 = _outbound_mocks()
    with patch("auth.dependencies.get_auth_settings") as auth_mock, \
         patch("auth.dependencies.get_current_user",
               new=AsyncMock(return_value=_non_admin_user())), \
         m1 as httpx_client, m2 as smtp, m3 as http_session:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await async_client.post(path, json=body)

    assert response.status_code == 403, response.json()
    httpx_client.assert_not_called()
    smtp.assert_not_called()
    http_session.assert_not_called()


# ---------------------------------------------------------------------------
# The MCP service principal is refused
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path,body", TEST_ENDPOINTS, ids=ENDPOINT_IDS)
@pytest.mark.asyncio
async def test_mcp_principal_refused_on_connection_test(async_client, path, body):
    """The static MCP key is admin-equivalent for channel work but must not be
    able to drive a credential-carrying outbound probe."""
    m1, m2, m3 = _outbound_mocks()
    with patch("auth.dependencies.get_settings", return_value=_mcp_runtime_settings()), \
         patch("auth.dependencies.get_auth_settings") as auth_mock, \
         m1 as httpx_client, m2 as smtp, m3 as http_session:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await async_client.post(
            path, json=body, headers={"Authorization": f"Bearer {MCP_KEY}"},
        )

    assert response.status_code == 403, response.json()
    assert "MCP service principal" in response.json()["detail"]
    httpx_client.assert_not_called()
    smtp.assert_not_called()
    http_session.assert_not_called()


# ---------------------------------------------------------------------------
# Positive controls — an operator admin, and setup mode, still work
# ---------------------------------------------------------------------------
class TestAdminStillAllowed:
    @pytest.mark.asyncio
    async def test_admin_reaches_dispatcharr_test_handler(self, async_client):
        """An admin is not blocked: the handler runs and returns its own
        verdict (here the SSRF guard's, which proves the body was reached)."""
        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
             patch("auth.dependencies.get_current_user",
                   new=AsyncMock(return_value=_admin_user())), \
             patch("routers.settings.get_settings",
                   return_value=DispatcharrSettings(ssrf_outbound_mode="public_only")):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post(
                "/api/settings/test",
                json={
                    "url": "ftp://dispatcharr:8000",
                    "auth_method": "password",
                    "username": "admin",
                    "password": "<synthetic-dispatcharr-password>",
                },
            )

        assert response.status_code == 200, response.json()
        assert response.json()["success"] is False
        assert "scheme" in response.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_admin_reaches_discord_test_handler(self, async_client):
        """Same for test-discord — the handler's own format check answers,
        which can only happen if the gate admitted the caller."""
        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
             patch("auth.dependencies.get_current_user",
                   new=AsyncMock(return_value=_admin_user())):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post(
                "/api/settings/test-discord",
                json={"webhook_url": "https://evil.example.com/hook"},
            )

        assert response.status_code == 200, response.json()
        assert response.json()["success"] is False
        assert "Invalid Discord webhook URL format" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_admin_reaches_telegram_test_handler(self, async_client):
        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
             patch("auth.dependencies.get_current_user",
                   new=AsyncMock(return_value=_admin_user())):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post(
                "/api/settings/test-telegram",
                json={"bot_token": "not-a-token", "chat_id": "123"},
            )

        assert response.status_code == 200, response.json()
        assert response.json()["success"] is False
        assert "Invalid bot token format" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_admin_reaches_smtp_test_handler(self, async_client):
        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
             patch("auth.dependencies.get_current_user",
                   new=AsyncMock(return_value=_admin_user())):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post(
                "/api/settings/test-smtp",
                json={
                    "smtp_host": "",
                    "smtp_port": 587,
                    "smtp_from_email": "alerts@example.com",
                    "to_email": "operator@example.com",
                },
            )

        assert response.status_code == 200, response.json()
        assert response.json()["success"] is False
        assert "SMTP host is required" in response.json()["message"]


class TestSetupModeStillAllowed:
    """The blocker the bead warned about: a first-run install must not lock
    itself out of the connection test that configures it."""

    @pytest.mark.parametrize(
        "require_auth,setup_complete",
        [(False, True), (True, False), (False, False)],
        ids=["auth-disabled", "setup-incomplete", "both"],
    )
    @pytest.mark.asyncio
    async def test_anonymous_caller_reaches_handler_in_setup_mode(
        self, async_client, require_auth, setup_complete
    ):
        with patch("auth.dependencies.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = require_auth
            auth_mock.return_value.setup_complete = setup_complete
            response = await async_client.post(
                "/api/settings/test-discord",
                json={"webhook_url": "https://evil.example.com/hook"},
            )

        assert response.status_code == 200, response.json()
        assert "Invalid Discord webhook URL format" in response.json()["message"]
