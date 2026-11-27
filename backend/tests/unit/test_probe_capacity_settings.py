"""Public capacity-wait contract and legacy config defaults (88yso)."""

import pytest
from pydantic import ValidationError

from config import DispatcharrSettings as Settings
from routers.settings import SettingsResponse, SettingsRequest as SettingsUpdate


def test_capacity_wait_default_is_five_minutes():
    assert Settings().probe_capacity_wait_timeout == 300
    assert SettingsUpdate(url="").probe_capacity_wait_timeout == 300
    assert "probe_capacity_wait_timeout" in SettingsResponse.model_fields


@pytest.mark.parametrize("seconds", [-1, 3601])
def test_capacity_wait_rejects_out_of_range(seconds):
    for model in (Settings, SettingsUpdate):
        with pytest.raises(ValidationError):
            model(url="", probe_capacity_wait_timeout=seconds)


def test_capacity_wait_can_be_disabled_explicitly():
    assert SettingsUpdate(url="", probe_capacity_wait_timeout=0).probe_capacity_wait_timeout == 0


@pytest.mark.asyncio
async def test_capacity_wait_persists_roundtrips_and_updates_live_prober(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    import config
    import httpx
    from fastapi import FastAPI
    from routers import settings
    from stream_prober import StreamProber

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "MCP_SECRETS_DIR", tmp_path / "mcp")
    monkeypatch.setattr(config, "MCP_KEY_FILE", tmp_path / "mcp" / "api-key")
    (tmp_path / "mcp").mkdir(mode=0o700)
    prober = StreamProber(AsyncMock())
    monkeypatch.setattr(settings, "get_prober", lambda: prober)
    monkeypatch.setattr(settings, "_has_discord_alert_method", lambda: False)
    config.clear_settings_cache()
    try:
        config.save_settings(Settings())
        app = FastAPI()
        app.include_router(settings.router)
        app.dependency_overrides[settings._resolve_settings_admin] = lambda: True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.post("/api/settings", json={"url": "", "probe_capacity_wait_timeout": 120})
            assert response.status_code == 200, response.text
            assert prober.probe_capacity_wait_timeout == 120
            config.clear_settings_cache()
            assert config.get_settings().probe_capacity_wait_timeout == 120
            response = await client.get("/api/settings")
            assert response.status_code == 200, response.text
            assert response.json()["probe_capacity_wait_timeout"] == 120
    finally:
        config.clear_settings_cache()
