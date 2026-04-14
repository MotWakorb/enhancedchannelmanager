"""Unit tests for StreamProber._push_stats_to_dispatcharr.

Covers the "reflect probe stats back to Dispatcharr" feature (issue #57):
setting gate, GET-then-merge-then-PATCH flow, field-name mapping,
and non-fatal error handling.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stream_prober import StreamProber


def _make_prober(client):
    return StreamProber(client=client)


def _successful_stats(**overrides):
    base = {
        "probe_status": "success",
        "resolution": "1920x1080",
        "fps": "29.97",
        "video_codec": "h264",
        "audio_codec": "aac",
        "audio_channels": 2,
        "video_bitrate": 5000000,
        "stream_type": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_push_respects_setting_disabled():
    client = AsyncMock()
    prober = _make_prober(client)

    mock_settings = MagicMock()
    mock_settings.push_stream_stats_to_dispatcharr = False

    with patch("config.get_settings", return_value=mock_settings):
        await prober._push_stats_to_dispatcharr(42, _successful_stats())

    client.get_stream.assert_not_called()
    client.update_stream.assert_not_called()


@pytest.mark.asyncio
async def test_push_skips_non_success_probes():
    client = AsyncMock()
    prober = _make_prober(client)

    mock_settings = MagicMock()
    mock_settings.push_stream_stats_to_dispatcharr = True

    with patch("config.get_settings", return_value=mock_settings):
        await prober._push_stats_to_dispatcharr(42, _successful_stats(probe_status="failed"))

    client.get_stream.assert_not_called()
    client.update_stream.assert_not_called()


@pytest.mark.asyncio
async def test_push_merges_with_existing_stats_and_maps_fps():
    """GET existing stream_stats, merge ECM fields on top, PATCH back.

    ECM's 'fps' must be written as Dispatcharr's 'source_fps'. Keys Dispatcharr
    already wrote that ECM doesn't know (e.g. pixel_format) must be preserved.
    """
    client = AsyncMock()
    client.get_stream.return_value = {
        "id": 42,
        "stream_stats": {
            "pixel_format": "yuv420p",
            "video_codec": "mpeg2video",  # stale — should be overwritten by ECM
        },
    }
    prober = _make_prober(client)

    mock_settings = MagicMock()
    mock_settings.push_stream_stats_to_dispatcharr = True

    with patch("config.get_settings", return_value=mock_settings):
        await prober._push_stats_to_dispatcharr(42, _successful_stats())

    client.get_stream.assert_awaited_once_with(42)
    client.update_stream.assert_awaited_once()
    sent_id, sent_payload = client.update_stream.await_args.args
    assert sent_id == 42
    merged = sent_payload["stream_stats"]
    # Preserved
    assert merged["pixel_format"] == "yuv420p"
    # ECM overwrote the stale codec
    assert merged["video_codec"] == "h264"
    # fps -> source_fps mapping
    assert merged["source_fps"] == "29.97"
    assert "fps" not in merged
    # Other ECM fields present
    assert merged["resolution"] == "1920x1080"
    assert merged["audio_codec"] == "aac"
    assert merged["audio_channels"] == "stereo"
    assert merged["ffmpeg_output_bitrate"] == 5000.0
    # Timestamp written
    assert "stream_stats_updated_at" in sent_payload


@pytest.mark.asyncio
async def test_push_omits_none_values_to_avoid_clobbering():
    """ECM stats with None fields must not blank out real Dispatcharr data."""
    client = AsyncMock()
    client.get_stream.return_value = {
        "id": 42,
        "stream_stats": {"resolution": "1280x720", "audio_bitrate": 128000},
    }
    prober = _make_prober(client)

    mock_settings = MagicMock()
    mock_settings.push_stream_stats_to_dispatcharr = True

    # Only video_codec was probed; everything else is None
    partial = {
        "probe_status": "success",
        "resolution": None,
        "fps": None,
        "video_codec": "hevc",
        "audio_codec": None,
        "audio_channels": None,
        "video_bitrate": None,
        "stream_type": None,
    }

    with patch("config.get_settings", return_value=mock_settings):
        await prober._push_stats_to_dispatcharr(42, partial)

    sent = client.update_stream.await_args.args[1]["stream_stats"]
    assert sent["video_codec"] == "hevc"
    # Preserved from Dispatcharr
    assert sent["resolution"] == "1280x720"
    assert sent["audio_bitrate"] == 128000


@pytest.mark.asyncio
async def test_push_noop_when_all_fields_none():
    client = AsyncMock()
    prober = _make_prober(client)

    mock_settings = MagicMock()
    mock_settings.push_stream_stats_to_dispatcharr = True

    empty = {"probe_status": "success"}

    with patch("config.get_settings", return_value=mock_settings):
        await prober._push_stats_to_dispatcharr(42, empty)

    client.get_stream.assert_not_called()
    client.update_stream.assert_not_called()


@pytest.mark.asyncio
async def test_push_swallows_get_stream_errors():
    client = AsyncMock()
    client.get_stream.side_effect = Exception("boom")
    prober = _make_prober(client)

    mock_settings = MagicMock()
    mock_settings.push_stream_stats_to_dispatcharr = True

    with patch("config.get_settings", return_value=mock_settings):
        # Must not raise
        await prober._push_stats_to_dispatcharr(42, _successful_stats())

    client.update_stream.assert_not_called()


@pytest.mark.asyncio
async def test_push_swallows_patch_errors():
    client = AsyncMock()
    client.get_stream.return_value = {"id": 42, "stream_stats": {}}
    client.update_stream.side_effect = Exception("patch failed")
    prober = _make_prober(client)

    mock_settings = MagicMock()
    mock_settings.push_stream_stats_to_dispatcharr = True

    with patch("config.get_settings", return_value=mock_settings):
        # Must not raise — probing is never failed by a push error.
        await prober._push_stats_to_dispatcharr(42, _successful_stats())
