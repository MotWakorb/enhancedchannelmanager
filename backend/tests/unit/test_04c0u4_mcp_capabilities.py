"""04c0u.4: the static MCP key is a limited service principal."""

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from auth.mcp_capabilities import (
    MCP_ALLOWED_ROUTES,
    MCP_HUMAN_ONLY_ROUTES,
    is_mcp_route_allowed,
)


def _endpoint_contracts():
    path = Path(__file__).parents[3] / "mcp-server" / "_endpoint_contracts.py"
    spec = importlib.util.spec_from_file_location("mcp_endpoint_contracts", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.ENDPOINTS


def test_every_declared_mcp_endpoint_has_an_explicit_backend_verdict():
    declared = {(endpoint.method, endpoint.path) for endpoint in _endpoint_contracts().values()}
    classified = MCP_ALLOWED_ROUTES | MCP_HUMAN_ONLY_ROUTES
    assert declared <= classified, sorted(declared - classified)
    assert not (MCP_ALLOWED_ROUTES & MCP_HUMAN_ONLY_ROUTES)


def test_every_allowed_capability_matches_an_actual_registered_fastapi_route():
    """Aliases in the service contract must not silently fail closed at runtime."""
    from main import app

    registered = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert MCP_ALLOWED_ROUTES <= registered, sorted(MCP_ALLOWED_ROUTES - registered)


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/cloud-targets"),
        ("PATCH", "/api/cloud-targets/{target_id}"),
        ("DELETE", "/api/cloud-targets/{target_id}"),
        ("POST", "/api/sync-targets"),
        ("PUT", "/api/sync-targets/{target_id}"),
        ("DELETE", "/api/sync-targets/{target_id}"),
        ("POST", "/api/epg/sources"),
        ("PATCH", "/api/epg/sources/{source_id}"),
        ("DELETE", "/api/epg/sources/{source_id}"),
        ("POST", "/api/m3u/accounts"),
        ("PATCH", "/api/m3u/accounts/{account_id}"),
        ("DELETE", "/api/m3u/accounts/{account_id}"),
        ("POST", "/api/backup/restore-saved"),
        ("POST", "/api/backup/restore-dbas-saved"),
    ],
)
def test_credential_identity_and_outbound_mutations_are_human_only(method, path):
    assert (method, path) in MCP_HUMAN_ONLY_ROUTES


def test_unknown_backend_route_is_denied_by_default():
    assert not is_mcp_route_allowed("GET", "/api/future-admin-surface")


def test_normal_channel_automation_remains_allowed():
    assert is_mcp_route_allowed("GET", "/api/channels")
    assert is_mcp_route_allowed("PATCH", "/api/channels/{channel_id}")
    assert is_mcp_route_allowed("POST", "/api/settings")


@pytest.mark.asyncio
async def test_mcp_cannot_run_dbas_backup_directly():
    from routers.tasks import TaskRunRequest, run_task

    with pytest.raises(HTTPException) as exc:
        await run_task(
            "dbas_backup",
            TaskRunRequest(parameters={"include_credentials": False}),
            is_admin=True,
            caller_is_mcp=True,
        )
    assert exc.value.status_code == 403
    assert "MCP service principal" in exc.value.detail
