from unittest.mock import MagicMock, patch

import pytest
import config
from stream_prober import StreamProber


@pytest.mark.asyncio
async def test_settings_persist_live_apply_and_preserve_on_omit(async_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "MCP_SECRETS_DIR", tmp_path)
    monkeypatch.setattr(config, "MCP_KEY_FILE", tmp_path / config.MCP_KEY_FILENAME)
    config.clear_settings_cache()
    config.save_settings(config.DispatcharrSettings())
    prober = StreamProber(MagicMock())
    try:
        with patch("routers.settings.reset_client"), patch("routers.settings.get_prober", return_value=prober):
            response = await async_client.post("/api/settings", json={"url": "", "username": "",
                "low_bitrate_threshold": 0.5, "deprioritize_low_bitrate": True,
                "failed_stream_sort_order": ["low_bitrate", "failed", "low_fps", "black_screen"]})
            assert response.status_code == 200, response.text
            assert prober.low_bitrate_threshold == 0.5
            assert prober._sort_settings_snapshot()["deprioritize_low_bitrate"] is True
            response = await async_client.post("/api/settings", json={"url": "", "username": ""})
            assert response.status_code == 200, response.text
        config.clear_settings_cache()
        with patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")
        assert response.status_code == 200
        assert response.json()["low_bitrate_threshold"] == 0.5
        assert response.json()["deprioritize_low_bitrate"] is True
        assert response.json()["failed_stream_sort_order"] == ["low_bitrate", "failed", "low_fps", "black_screen"]
    finally:
        config.clear_settings_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize("threshold", [0, -1, "NaN", "Infinity", None])
async def test_invalid_threshold_rejected_by_api(async_client, threshold):
    response = await async_client.post("/api/settings", json={"url": "", "username": "", "low_bitrate_threshold": threshold})
    assert response.status_code == 422
