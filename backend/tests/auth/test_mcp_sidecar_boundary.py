"""Regression tests for the MCP client/sidecar/backend credential boundary."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from auth.mcp_service import (
    MCP_CLAIM_HEADER,
    ensure_mcp_service_credentials,
    issue_test_claim,
    rotate_mcp_service_credentials,
    verify_mcp_service_claim,
)
from config import DispatcharrSettings, save_settings


def test_internal_credentials_are_distinct_private_and_not_in_settings(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"mcp_api_key": "operator-client-key"}))
    projection = tmp_path / "mcp-service.json"

    credentials = ensure_mcp_service_credentials(projection)

    assert credentials.backend_key != "operator-client-key"
    assert credentials.confirmation_key != credentials.backend_key
    assert projection.stat().st_mode & 0o777 == 0o600
    assert "backend_key" not in settings.read_text()

    rotated = rotate_mcp_service_credentials(projection)
    assert rotated != credentials
    assert ensure_mcp_service_credentials(projection) == rotated
    assert projection.stat().st_mode & 0o777 == 0o600


def test_external_key_rotation_projects_settings_atomically(tmp_path: Path):
    target = tmp_path / "settings.json"
    target.write_text('{"mcp_api_key":"old"}')
    settings = DispatcharrSettings(mcp_api_key="new")
    with patch("config.CONFIG_FILE", target), patch("config.ensure_config_dir"):
        save_settings(settings)
    assert json.loads(target.read_text())["mcp_api_key"] == "new"
    assert target.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.asyncio
async def test_claim_is_bound_to_request_and_single_use(tmp_path: Path):
    projection = tmp_path / "mcp-service.json"
    credentials = ensure_mcp_service_credentials(projection)
    app = FastAPI()

    @app.post("/write")
    async def write(request: Request):
        await verify_mcp_service_claim(request, credentials)
        return {"ok": True}

    body = {"ids": [2, 1]}
    claim = issue_test_claim(credentials, "POST", "/write", body)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/write", json=body, headers={MCP_CLAIM_HEADER: claim})
        replay = await client.post("/write", json=body, headers={MCP_CLAIM_HEADER: claim})
        drift = await client.post(
            "/write", json={"ids": [1]},
            headers={MCP_CLAIM_HEADER: issue_test_claim(credentials, "POST", "/write", body)},
        )

    assert first.status_code == 200
    assert replay.status_code == 403
    assert drift.status_code == 403
