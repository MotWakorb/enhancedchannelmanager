"""bead 9kwzp.7 — Emby / Plex / Jellyfin test-connection must be human-admin.

THE GAP
-------

These three carried ``RequireAdminIfEnabled`` and were cited in bead i4qrp's
description as the CORRECT pattern that ``/api/settings/test`` was missing.
That premise was only half right. ``RequireAdminIfEnabled`` closes the
non-admin half but NOT the MCP half, because
``_build_mcp_service_principal`` sets ``is_admin=True``. The static MCP key
still reached all three, each of which POSTs an operator-supplied secret to a
caller-named host and echoes the upstream result back — the status-code oracle
and in-band port scanner that i4qrp closed for ``/test``, ``/test-smtp``,
``/test-discord`` and ``/test-telegram``.

The fix is a one-line dependency swap per endpoint to
``RequireHumanAdminForOutboundTest``, with i4qrp's four-case shape here.

The SSRF guard (``_sanitize_base_url``) is a SECOND control on these routes,
not a substitute for the first: it constrains WHICH hosts can be reached, not
WHO may reach them, and its LAN-friendly default mode deliberately permits
private ranges. The gate is what decides the principal.
"""
from unittest.mock import AsyncMock, patch

import pytest

from config import DispatcharrSettings
from models import User


MCP_KEY = "mcp-secret-key-9kwzp7"

# (path, body) for the three media-server test-connection endpoints.
#
# The credential VALUES below are angle-bracket placeholders, not
# credential-shaped strings. The refusal tests assert on the gate, which
# answers first, and the positive control rewrites ``base_url`` so the SSRF
# guard answers before any client is built, so no value is ever consumed;
# writing them as visible placeholders keeps that fact obvious to a reader and
# keeps the file free of anything a scanner has to reason about.
MEDIA_SERVER_ENDPOINTS = [
    (
        "/api/settings/emby/test-connection",
        {"base_url": "http://emby.example.com:8096", "api_key": "<synthetic-emby-key>"},
    ),
    (
        "/api/settings/plex/test-connection",
        {"base_url": "http://plex.example.com:32400", "token": "<synthetic-plex-token>"},
    ),
    (
        "/api/settings/jellyfin/test-connection",
        {"base_url": "http://jellyfin.example.com:8096", "api_key": "<synthetic-jellyfin-key>"},
    ),
]

MEDIA_SERVER_IDS = ["emby", "plex", "jellyfin"]


def _mcp_runtime_settings():
    return DispatcharrSettings(
        url="http://dispatcharr:8000",
        username="u",
        password="p",
        mcp_api_key=MCP_KEY,
    )


def _non_admin_user():
    return User(
        id=7150,
        username="regular-user",
        is_admin=False,
        is_active=True,
        auth_provider="local",
    )


def _admin_user():
    return User(
        id=7151,
        username="admin-user",
        is_admin=True,
        is_active=True,
        auth_provider="local",
    )


def _client_mocks():
    """Patch the three media-server clients at their import site in the
    settings router. A refused request must construct none of them."""
    return (
        patch("routers.settings.EmbyClient"),
        patch("routers.settings.PlexClient"),
        patch("routers.settings.JellyfinClient"),
    )


# ---------------------------------------------------------------------------
# Non-admin is refused (this half already worked — pinned so it stays working)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path,body", MEDIA_SERVER_ENDPOINTS, ids=MEDIA_SERVER_IDS)
@pytest.mark.asyncio
async def test_non_admin_refused_on_media_server_test(async_client, path, body):
    m1, m2, m3 = _client_mocks()
    with patch("auth.dependencies.get_auth_settings") as auth_mock, \
         patch("auth.dependencies.get_current_user",
               new=AsyncMock(return_value=_non_admin_user())), \
         m1 as emby, m2 as plex, m3 as jellyfin:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await async_client.post(path, json=body)

    assert response.status_code == 403, response.json()
    emby.assert_not_called()
    plex.assert_not_called()
    jellyfin.assert_not_called()


# ---------------------------------------------------------------------------
# The MCP service principal is refused (the half RequireAdminIfEnabled missed)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path,body", MEDIA_SERVER_ENDPOINTS, ids=MEDIA_SERVER_IDS)
@pytest.mark.asyncio
async def test_mcp_principal_refused_on_media_server_test(async_client, path, body):
    m1, m2, m3 = _client_mocks()
    with patch("auth.dependencies.get_settings", return_value=_mcp_runtime_settings()), \
         patch("auth.dependencies.get_auth_settings") as auth_mock, \
         m1 as emby, m2 as plex, m3 as jellyfin:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await async_client.post(
            path, json=body, headers={"Authorization": f"Bearer {MCP_KEY}"},
        )

    assert response.status_code == 403, response.json()
    assert "MCP service principal" in response.json()["detail"]
    emby.assert_not_called()
    plex.assert_not_called()
    jellyfin.assert_not_called()


# ---------------------------------------------------------------------------
# Positive controls
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path,body", MEDIA_SERVER_ENDPOINTS, ids=MEDIA_SERVER_IDS)
@pytest.mark.asyncio
async def test_admin_reaches_media_server_handler(async_client, path, body):
    """The SSRF guard's own verdict answers, which can only happen if the gate
    admitted the caller."""
    bad_body = dict(body)
    bad_body["base_url"] = "ftp://media.example.com"
    with patch("auth.dependencies.get_auth_settings") as auth_mock, \
         patch("auth.dependencies.get_current_user",
               new=AsyncMock(return_value=_admin_user())):
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await async_client.post(path, json=bad_body)

    assert response.status_code == 200, response.json()
    assert response.json()["ok"] is False
    assert "scheme" in response.json()["error"].lower()


class TestSetupModeStillAllowed:
    """First-run configuration must keep working: the wizard configures a
    media server before auth exists."""

    @pytest.mark.parametrize("path,body", MEDIA_SERVER_ENDPOINTS, ids=MEDIA_SERVER_IDS)
    @pytest.mark.parametrize(
        "require_auth,setup_complete",
        [(False, True), (True, False), (False, False)],
        ids=["auth-disabled", "setup-incomplete", "both"],
    )
    @pytest.mark.asyncio
    async def test_anonymous_caller_reaches_handler_in_setup_mode(
        self, async_client, path, body, require_auth, setup_complete
    ):
        bad_body = dict(body)
        bad_body["base_url"] = "ftp://media.example.com"
        with patch("auth.dependencies.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = require_auth
            auth_mock.return_value.setup_complete = setup_complete
            response = await async_client.post(path, json=bad_body)

        assert response.status_code == 200, response.json()
        assert response.json()["ok"] is False
        assert "scheme" in response.json()["error"].lower()
