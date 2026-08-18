"""Direct-ASGI adversarial checks for the MCP service-principal boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


MCP_KEY = "<Synthetic-MCP-Service-Key-04c0u4>"


def _auth_on():
    return SimpleNamespace(require_auth=True, setup_complete=True)


def _runtime_settings():
    return SimpleNamespace(mcp_api_key=MCP_KEY)


@pytest.mark.parametrize(
    "method,path,json",
    [
        ("POST", "/api/cloud-targets", {"name": "x"}),
        ("PATCH", "/api/cloud-targets/7", {"enabled": True}),
        ("DELETE", "/api/cloud-targets/7", None),
        ("POST", "/api/sync-targets", {"name": "x"}),
        ("PUT", "/api/sync-targets/7", {"enabled": True}),
        ("DELETE", "/api/sync-targets/7", None),
        ("POST", "/api/m3u/accounts", {"username": "u", "password": "p"}),
        ("POST", "/api/epg/sources", {"username": "u", "password": "p"}),
        ("GET", "/api/auth/admin/users", None),
        ("POST", "/api/settings/mcp-api-key", None),
        ("POST", "/api/tls/upload-cert", {}),
        ("GET", "/api/not-yet-classified", None),
    ],
)
@pytest.mark.asyncio
async def test_direct_backend_call_with_mcp_key_is_refused(
    async_client, method, path, json
):
    with (
        patch("main.get_auth_settings", return_value=_auth_on()),
        patch("main.get_settings", return_value=_runtime_settings()),
    ):
        response = await async_client.request(
            method,
            path,
            json=json,
            headers={"Authorization": f"Bearer {MCP_KEY}"},
        )
    assert response.status_code == 403, response.text
    assert "MCP service principal" in response.json()["detail"]


@pytest.mark.asyncio
async def test_direct_mcp_task_call_cannot_export_credentials(async_client):
    with (
        patch("main.get_auth_settings", return_value=_auth_on()),
        patch("main.get_settings", return_value=_runtime_settings()),
        patch("auth.dependencies.get_auth_settings", return_value=_auth_on()),
        patch("auth.dependencies.get_settings", return_value=_runtime_settings()),
    ):
        response = await async_client.post(
            "/api/tasks/dbas_backup/run",
            json={"parameters": {"include_credentials": True}},
            headers={"Authorization": f"Bearer {MCP_KEY}"},
        )
    assert response.status_code == 403, response.text
    assert "cannot export credentials" in response.json()["detail"]


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/tasks/dbas_restore/run", {}),
        ("/api/tasks/dbas_sync_7/run", {}),
        (
            "/api/tasks/dbas_sync_7/schedules",
            {"schedule_type": "daily", "schedule_time": "03:00"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_direct_mcp_call_cannot_activate_outbound_or_restore_tasks(
    async_client, path, body
):
    with (
        patch("main.get_auth_settings", return_value=_auth_on()),
        patch("main.get_settings", return_value=_runtime_settings()),
        patch("auth.dependencies.get_auth_settings", return_value=_auth_on()),
        patch("auth.dependencies.get_settings", return_value=_runtime_settings()),
    ):
        response = await async_client.post(
            path,
            json=body,
            headers={"Authorization": f"Bearer {MCP_KEY}"},
        )
    assert response.status_code == 403, response.text
    assert "MCP service principal" in response.json()["detail"]


@pytest.mark.asyncio
async def test_direct_mcp_call_reaches_explicit_safe_capability(async_client):
    client = SimpleNamespace(
        get_channels=AsyncMock(return_value={"results": [], "count": 0})
    )
    with (
        patch("main.get_auth_settings", return_value=_auth_on()),
        patch("main.get_settings", return_value=_runtime_settings()),
        patch("auth.dependencies.get_auth_settings", return_value=_auth_on()),
        patch("auth.dependencies.get_settings", return_value=_runtime_settings()),
        patch("routers.channels.get_client", return_value=client),
    ):
        response = await async_client.get(
            "/api/channels",
            headers={"Authorization": f"Bearer {MCP_KEY}"},
        )
    assert response.status_code == 200, response.text
    client.get_channels.assert_awaited_once()
