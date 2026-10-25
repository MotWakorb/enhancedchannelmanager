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
    reclassified = []
    prober.reclassify_unstable = lambda: reclassified.append(prober.playback_reconnect_threshold)
    try:
        with patch("routers.settings.reset_client"), patch("routers.settings.get_prober", return_value=prober):
            response = await async_client.post("/api/settings", json={"url": "", "username": "",
                "playback_reconnect_threshold": 12.5, "playback_start_failure_threshold": 3, "deprioritize_unstable": True,
                "failed_stream_sort_order": ["unstable", "failed", "black_screen", "low_fps", "low_bitrate"]})
            assert response.status_code == 200, response.text
            assert prober.playback_reconnect_threshold == 12.5
            assert prober.playback_start_failure_threshold == 3
            assert reclassified == [12.5]
            assert prober._sort_settings_snapshot()["deprioritize_unstable"] is True
            response = await async_client.post("/api/settings", json={"url": "", "username": ""})
            assert response.status_code == 200, response.text
            assert reclassified == [12.5]
        config.clear_settings_cache()
        with patch("routers.settings._has_discord_alert_method", return_value=False):
            response = await async_client.get("/api/settings")
        assert response.status_code == 200
        body = response.json()
        assert body["playback_reconnect_threshold"] == 12.5
        assert body["playback_start_failure_threshold"] == 3
        assert body["deprioritize_unstable"] is True
        assert body["failed_stream_sort_order"] == ["unstable", "failed", "black_screen", "low_fps", "low_bitrate"]
    finally:
        config.clear_settings_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize("threshold", [0, -1, "NaN", "Infinity", None])
async def test_invalid_threshold_rejected_by_api(async_client, threshold):
    response = await async_client.post("/api/settings", json={"url": "", "username": "", "playback_reconnect_threshold": threshold})
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("threshold", [0, -1, None])
async def test_invalid_start_failure_threshold_rejected_by_api(async_client, threshold):
    response = await async_client.post("/api/settings", json={"url": "", "username": "", "playback_start_failure_threshold": threshold})
    assert response.status_code == 422
