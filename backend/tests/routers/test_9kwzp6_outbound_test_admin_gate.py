"""bead 9kwzp.6 — the remaining outbound-test sinks, plus restart-services.

THE GAP
-------

``POST /api/alert-methods/{id}/test``, ``POST /api/m3u/digest/test``,
``POST /api/cloud-targets/{id}/test`` and ``POST /api/cloud-targets/test``
carried NO route dependency at all. Every one of them reaches the network on
the caller's say-so and reports the upstream result back, and the first three
send with STORED credentials the caller never had to know — the same class as
``/api/settings/test-smtp``, which bead i4qrp closed. All four were reachable
by any authenticated caller AND by the static MCP API key, which
``main.auth_middleware`` accepts directly for any ``/api/*`` path.

WHY ``RequireHumanAdminForOutboundTest`` AND NOT ``RequireAdminIfEnabled``
-------------------------------------------------------------------------

``_build_mcp_service_principal`` sets ``is_admin=True``, so the plain admin
gate ADMITS the MCP key. Using it here would close the non-admin half and
leave the MCP half — the half this bead is about — wide open.

``POST /api/settings/restart-services`` IS A DIFFERENT CLASS
------------------------------------------------------------

It rides along in the same bead but gets a different gate, on its own merits.
It is not a credential sink: it names no host, sends no secret, and echoes no
upstream status, so none of the reasons that deny the MCP principal an
outbound probe apply to it. It is also not destructive — it stops and restarts
the bandwidth tracker and stream prober from ALREADY-SAVED settings, which is
exactly what ``update_settings`` already schedules on its own via
``_rebuild_background_services_after_settings_change``. What it was missing is
the ordinary admin tier: an authenticated non-admin could churn the background
services at will. So it takes plain ``RequireAdminIfEnabled``, and the MCP
principal — a legitimate admin automation surface for ordinary operational
work — is deliberately still admitted. That admission is pinned below, not
merely left untested, so a later blanket swap to the human-admin dependency
trips a test instead of silently changing the contract.

FIRST-RUN PATH
--------------

``require_admin_if_enabled`` returns None (allows) when ``require_auth`` is
False or ``setup_complete`` is False, so a genuine first run is untouched.
Pinned by ``TestSetupModeStillAllowed``.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import DispatcharrSettings
from models import User


MCP_KEY = "mcp-secret-key-9kwzp6"

# (path, body) for each of the four ungated outbound sinks.
#
# The credential VALUES below are angle-bracket placeholders, not
# credential-shaped strings. Both tests that use this table assert on the
# gate, which answers before the handler ever reads a body field, so no value
# is consumed; writing them as visible placeholders keeps that fact obvious to
# a reader and keeps the file free of anything a scanner has to reason about.
OUTBOUND_ENDPOINTS = [
    ("/api/alert-methods/1/test", None),
    ("/api/m3u/digest/test", None),
    ("/api/cloud-targets/1/test", None),
    (
        "/api/cloud-targets/test",
        {
            "provider_type": "s3",
            "credentials": {
                "access_key": "<synthetic-s3-access-key>",
                "secret_key": "<synthetic-s3-secret-key>",
            },
        },
    ),
]

OUTBOUND_IDS = ["alert-method", "m3u-digest", "cloud-target-saved", "cloud-target-inline"]


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
        id=6150,
        username="regular-user",
        is_admin=False,
        is_active=True,
        auth_provider="local",
    )


def _admin_user():
    return User(
        id=6151,
        username="admin-user",
        is_admin=True,
        is_active=True,
        auth_provider="local",
    )


def _sink_mocks():
    """Patch the outbound entry point of every handler under test.

    A refused request must never construct any of them. A test that WRONGLY
    reaches a handler body then fails on the assertion rather than opening a
    real socket.
    """
    return (
        patch("routers.alert_methods.create_method"),
        patch("tasks.m3u_digest.M3UDigestTask"),
        patch("routers.cloud_targets.get_adapter"),
    )


# ---------------------------------------------------------------------------
# Non-admin is refused
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path,body", OUTBOUND_ENDPOINTS, ids=OUTBOUND_IDS)
@pytest.mark.asyncio
async def test_non_admin_refused_on_outbound_test(async_client, path, body):
    """An authenticated non-admin gets 403 and no outbound call is made."""
    m1, m2, m3 = _sink_mocks()
    with patch("auth.dependencies.get_auth_settings") as auth_mock, \
         patch("auth.dependencies.get_current_user",
               new=AsyncMock(return_value=_non_admin_user())), \
         m1 as alert_method, m2 as digest_task, m3 as cloud_adapter:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await async_client.post(path, json=body)

    assert response.status_code == 403, response.json()
    alert_method.assert_not_called()
    digest_task.assert_not_called()
    cloud_adapter.assert_not_called()


# ---------------------------------------------------------------------------
# The MCP service principal is refused
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path,body", OUTBOUND_ENDPOINTS, ids=OUTBOUND_IDS)
@pytest.mark.asyncio
async def test_mcp_principal_refused_on_outbound_test(async_client, path, body):
    """The static MCP key is admin-equivalent for channel work but must not be
    able to drive a credential-carrying outbound probe."""
    m1, m2, m3 = _sink_mocks()
    with patch("auth.dependencies.get_settings", return_value=_mcp_runtime_settings()), \
         patch("auth.dependencies.get_auth_settings") as auth_mock, \
         m1 as alert_method, m2 as digest_task, m3 as cloud_adapter:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await async_client.post(
            path, json=body, headers={"Authorization": f"Bearer {MCP_KEY}"},
        )

    assert response.status_code == 403, response.json()
    assert "MCP service principal" in response.json()["detail"]
    alert_method.assert_not_called()
    digest_task.assert_not_called()
    cloud_adapter.assert_not_called()


# ---------------------------------------------------------------------------
# Positive controls — an operator admin still reaches every handler
# ---------------------------------------------------------------------------
class TestAdminStillAllowed:
    """Each assertion is a verdict only the handler body can produce, so it
    can only be reached if the gate admitted the caller."""

    @pytest.mark.asyncio
    async def test_admin_reaches_alert_method_test_handler(self, async_client):
        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
             patch("auth.dependencies.get_current_user",
                   new=AsyncMock(return_value=_admin_user())):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post("/api/alert-methods/424242/test")

        assert response.status_code == 404, response.json()
        assert response.json()["detail"] == "Alert method not found"

    @pytest.mark.asyncio
    async def test_admin_reaches_m3u_digest_test_handler(self, async_client):
        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
             patch("auth.dependencies.get_current_user",
                   new=AsyncMock(return_value=_admin_user())):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post("/api/m3u/digest/test")

        assert response.status_code == 400, response.json()
        assert "No notification targets configured" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_admin_reaches_saved_cloud_target_test_handler(self, async_client):
        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
             patch("auth.dependencies.get_current_user",
                   new=AsyncMock(return_value=_admin_user())):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post("/api/cloud-targets/424242/test")

        assert response.status_code == 404, response.json()
        assert response.json()["detail"] == "Cloud target not found"

    @pytest.mark.asyncio
    async def test_admin_reaches_inline_cloud_target_test_handler(self, async_client):
        """``onedrive`` is a DEFERRED provider, so the handler's own
        ``_deferred_provider_result`` answers before any adapter is built."""
        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
             patch("auth.dependencies.get_current_user",
                   new=AsyncMock(return_value=_admin_user())):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post(
                "/api/cloud-targets/test",
                json={"provider_type": "onedrive", "credentials": {}},
            )

        assert response.status_code == 200, response.json()
        assert response.json()["success"] is False
        assert "not supported" in response.json()["message"]


class TestSetupModeStillAllowed:
    """A first-run install must not lock itself out of the tests that
    configure it."""

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
                "/api/cloud-targets/test",
                json={"provider_type": "onedrive", "credentials": {}},
            )

        assert response.status_code == 200, response.json()
        assert "not supported" in response.json()["message"]


# ---------------------------------------------------------------------------
# restart-services — the different-class fifth endpoint
# ---------------------------------------------------------------------------
class TestRestartServicesGate:
    """Plain admin tier, MCP principal DELIBERATELY still admitted.

    See the module docstring for why this endpoint does not take the outbound
    dependency: it names no host, carries no credential, and does the same
    work ``update_settings`` already schedules for itself.
    """

    @pytest.mark.asyncio
    async def test_non_admin_refused(self, async_client):
        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
             patch("auth.dependencies.get_current_user",
                   new=AsyncMock(return_value=_non_admin_user())), \
             patch("routers.settings._restart_background_services",
                   new=AsyncMock()) as restart:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post("/api/settings/restart-services")

        assert response.status_code == 403, response.json()
        assert response.json()["detail"] == "Admin access required"
        restart.assert_not_called()

    @pytest.mark.asyncio
    async def test_admin_reaches_handler(self, async_client):
        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
             patch("auth.dependencies.get_current_user",
                   new=AsyncMock(return_value=_admin_user())), \
             patch("routers.settings.get_settings", return_value=MagicMock()), \
             patch("routers.settings._restart_background_services",
                   new=AsyncMock(return_value={"success": True, "message": "ok"})) as restart:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post("/api/settings/restart-services")

        assert response.status_code == 200, response.json()
        assert response.json()["success"] is True
        restart.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mcp_principal_still_admitted(self, async_client):
        """Deliberate: restarting background services from saved settings is
        ordinary admin automation, not an outbound credential sink."""
        with patch("auth.dependencies.get_settings", return_value=_mcp_runtime_settings()), \
             patch("auth.dependencies.get_auth_settings") as auth_mock, \
             patch("routers.settings.get_settings", return_value=MagicMock()), \
             patch("routers.settings._restart_background_services",
                   new=AsyncMock(return_value={"success": True, "message": "ok"})) as restart:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post(
                "/api/settings/restart-services",
                headers={"Authorization": f"Bearer {MCP_KEY}"},
            )

        assert response.status_code == 200, response.json()
        restart.assert_awaited_once()

    @pytest.mark.parametrize(
        "require_auth,setup_complete",
        [(False, True), (True, False), (False, False)],
        ids=["auth-disabled", "setup-incomplete", "both"],
    )
    @pytest.mark.asyncio
    async def test_setup_mode_still_allowed(
        self, async_client, require_auth, setup_complete
    ):
        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
             patch("routers.settings.get_settings", return_value=MagicMock()), \
             patch("routers.settings._restart_background_services",
                   new=AsyncMock(return_value={"success": True, "message": "ok"})) as restart:
            auth_mock.return_value.require_auth = require_auth
            auth_mock.return_value.setup_complete = setup_complete
            response = await async_client.post("/api/settings/restart-services")

        assert response.status_code == 200, response.json()
        restart.assert_awaited_once()
