from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from auth import RequireAdminIfEnabled, ResolveIsMcpServicePrincipalIfEnabled
from routers import channel_pipeline
from routers.emby import ClearEmbyLogosRequest, clear_emby_logos
from routers import emby, normalization
from routers.normalization import ApplyToChannelsRequest, apply_normalization_to_channels


@pytest.mark.asyncio
async def test_mcp_cannot_use_legacy_emby_execute_without_plan():
    with pytest.raises(HTTPException) as caught:
        await clear_emby_logos(
            ClearEmbyLogosRequest(logo_types=["Primary"]),
            _admin=None,
            caller_is_mcp=True,
        )
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_mcp_cannot_use_legacy_normalization_execute_without_plan():
    with pytest.raises(HTTPException) as caught:
        await apply_normalization_to_channels(
            AsyncMock(), dry_run=False, body=ApplyToChannelsRequest(actions=[]),
            _admin=None, caller_is_mcp=True,
        )
    assert caught.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "body"), [
    ("/api/channel-pipeline/run", {"dry_run": False}),
    ("/api/normalization/apply-to-channels?dry_run=false", {"actions": []}),
    ("/api/emby/clear-logos", {"logo_types": ["Primary"]}),
])
async def test_direct_asgi_mcp_legacy_mutations_require_prepared_plan(path, body):
    app = FastAPI()
    app.include_router(channel_pipeline.router, prefix="/api/channel-pipeline")
    app.include_router(normalization.router)
    app.include_router(emby.router)
    app.dependency_overrides[RequireAdminIfEnabled.dependency] = lambda: None
    app.dependency_overrides[ResolveIsMcpServicePrincipalIfEnabled.dependency] = lambda: True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(path, json=body)
    assert response.status_code == 409
    assert "requires" in response.json()["detail"]
