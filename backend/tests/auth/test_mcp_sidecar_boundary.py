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
    load_mcp_service_credentials,
    rotate_mcp_service_credentials,
    verify_mcp_service_claim,
)
from config import DispatcharrSettings, save_settings


def test_internal_credentials_are_distinct_private_and_not_in_settings(tmp_path: Path):
    settings = tmp_path / "settings.json"
    external_key = "<EXTERNAL_MCP_CLIENT_KEY>"
    settings.write_text(json.dumps({"mcp_api_key": external_key}))
    projection = tmp_path / "mcp-service.json"

    credentials = ensure_mcp_service_credentials(projection)

    assert credentials.backend_key != external_key
    assert credentials.confirmation_key != credentials.backend_key
    assert projection.stat().st_mode & 0o777 == 0o600
    assert "backend_key" not in settings.read_text()

    rotated = rotate_mcp_service_credentials(projection)
    assert rotated != credentials
    assert ensure_mcp_service_credentials(projection) == rotated
    assert projection.stat().st_mode & 0o777 == 0o600


def test_external_key_rotation_projects_settings_atomically(tmp_path: Path):
    target = tmp_path / "settings.json"
    target.write_text('{"mcp_api_key":"<OLD_MCP_CLIENT_KEY>"}')
    settings = DispatcharrSettings(mcp_api_key="<NEW_MCP_CLIENT_KEY>")
    with patch("config.CONFIG_FILE", target), patch("config.ensure_config_dir"):
        save_settings(settings)
    assert json.loads(target.read_text())["mcp_api_key"] == "<NEW_MCP_CLIENT_KEY>"
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


class TestUnwritableProjectionDegradesInsteadOfKillingECM:
    """…-04c0u.8: the projection must never be able to take ECM down.

    ``ensure_mcp_service_credentials`` is called from two liveness paths — the
    FastAPI startup handler and the auth middleware, which runs on every
    non-exempt request. An exception out of a startup handler aborts the ASGI
    lifespan (uvicorn logs "Application startup failed. Exiting."), and an
    exception out of the middleware is a 500 on every authenticated request.
    Those paths go through ``load_mcp_service_credentials``, which reports the
    failure and returns ``None`` — no sidecar principal, ordinary 401 — rather
    than raising.
    """

    def test_an_unwritable_projection_directory_returns_none(self, tmp_path: Path):
        projection_dir = tmp_path / "ecm-mcp"
        projection_dir.mkdir()
        projection_dir.chmod(0o500)
        try:
            with pytest.raises(PermissionError):
                ensure_mcp_service_credentials(projection_dir / "mcp-service.json")

            assert (
                load_mcp_service_credentials(projection_dir / "mcp-service.json")
                is None
            )
        finally:
            projection_dir.chmod(0o700)

    def test_a_malformed_projection_returns_none(self, tmp_path: Path):
        projection = tmp_path / "mcp-service.json"
        projection.write_text("not json at all")

        with pytest.raises(RuntimeError):
            ensure_mcp_service_credentials(projection)

        assert load_mcp_service_credentials(projection) is None

    def test_a_healthy_projection_is_returned_unchanged(self, tmp_path: Path):
        projection = tmp_path / "mcp-service.json"

        created = load_mcp_service_credentials(projection)

        assert created is not None
        assert created == ensure_mcp_service_credentials(projection)

    def test_a_symlinked_projection_is_refused_rather_than_chmodded(
        self, tmp_path: Path
    ):
        """finding 10 — the re-chmod must not follow a link to its target."""
        target = tmp_path / "unrelated.json"
        target.write_text(json.dumps({"backend_key": "a" * 40, "confirmation_key": "b" * 40}))
        target.chmod(0o644)
        projection = tmp_path / "mcp-service.json"
        projection.symlink_to(target)

        assert load_mcp_service_credentials(projection) is None
        assert target.stat().st_mode & 0o777 == 0o644
