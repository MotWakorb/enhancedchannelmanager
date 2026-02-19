"""
Integration tests for POST /api/stream-stats/compute-sort endpoint.

Tests the full HTTP round-trip including Pydantic validation,
settings loading, and sort computation.
"""
import pytest
from unittest.mock import patch, AsyncMock
from models import StreamStats


@pytest.fixture
def seed_stream_stats(test_session):
    """Seed StreamStats rows for testing."""
    def _seed(*stats_data):
        for data in stats_data:
            stat = StreamStats(
                stream_id=data["stream_id"],
                stream_name=data.get("stream_name", f"Stream {data['stream_id']}"),
                resolution=data.get("resolution"),
                bitrate=data.get("bitrate"),
                video_bitrate=data.get("video_bitrate"),
                fps=data.get("fps"),
                audio_channels=data.get("audio_channels"),
                probe_status=data.get("probe_status", "success"),
            )
            test_session.add(stat)
        test_session.commit()
    return _seed


@pytest.fixture
def mock_settings():
    """Patch get_settings to return controlled sort settings."""
    from unittest.mock import MagicMock
    mock = MagicMock()
    mock.stream_sort_priority = ["resolution", "bitrate", "framerate"]
    mock.stream_sort_enabled = {"resolution": True, "bitrate": True, "framerate": True}
    mock.m3u_account_priorities = {}
    mock.deprioritize_failed_streams = True
    return mock


@pytest.mark.asyncio
async def test_compute_sort_single_channel_smart(async_client, test_session, seed_stream_stats, mock_settings):
    """Single channel sorted by resolution descending."""
    seed_stream_stats(
        {"stream_id": 1, "resolution": "1280x720", "bitrate": 3000000, "fps": "30"},
        {"stream_id": 2, "resolution": "1920x1080", "bitrate": 5000000, "fps": "60"},
    )

    with patch("routers.stream_stats.get_settings", return_value=mock_settings):
        response = await async_client.post("/api/stream-stats/compute-sort", json={
            "channels": [{"channel_id": 10, "stream_ids": [1, 2]}],
            "mode": "smart"
        })

    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["sorted_stream_ids"] == [2, 1]
    assert data["results"][0]["changed"] is True


@pytest.mark.asyncio
async def test_compute_sort_already_sorted(async_client, test_session, seed_stream_stats, mock_settings):
    """Streams already in correct order return changed=False."""
    seed_stream_stats(
        {"stream_id": 1, "resolution": "1920x1080", "bitrate": 5000000, "fps": "60"},
        {"stream_id": 2, "resolution": "1280x720", "bitrate": 3000000, "fps": "30"},
    )

    with patch("routers.stream_stats.get_settings", return_value=mock_settings):
        response = await async_client.post("/api/stream-stats/compute-sort", json={
            "channels": [{"channel_id": 10, "stream_ids": [1, 2]}],
            "mode": "smart"
        })

    assert response.status_code == 200
    data = response.json()
    assert data["results"][0]["changed"] is False
    assert data["results"][0]["sorted_stream_ids"] == [1, 2]


@pytest.mark.asyncio
async def test_compute_sort_bulk_channels(async_client, test_session, seed_stream_stats, mock_settings):
    """Multiple channels each get sorted independently."""
    seed_stream_stats(
        {"stream_id": 1, "resolution": "1280x720", "bitrate": 3000000, "fps": "30"},
        {"stream_id": 2, "resolution": "1920x1080", "bitrate": 5000000, "fps": "60"},
        {"stream_id": 3, "resolution": "3840x2160", "bitrate": 8000000, "fps": "30"},
        {"stream_id": 4, "resolution": "1920x1080", "bitrate": 5000000, "fps": "30"},
    )

    with patch("routers.stream_stats.get_settings", return_value=mock_settings):
        response = await async_client.post("/api/stream-stats/compute-sort", json={
            "channels": [
                {"channel_id": 10, "stream_ids": [1, 2]},
                {"channel_id": 20, "stream_ids": [3, 4]},
                {"channel_id": 30, "stream_ids": [4, 3]},  # already sorted (4K > 1080p)
            ],
            "mode": "smart"
        })

    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 3
    # Channel 10: 1080p before 720p
    assert data["results"][0]["sorted_stream_ids"] == [2, 1]
    assert data["results"][0]["changed"] is True
    # Channel 20: 4K before 1080p
    assert data["results"][1]["sorted_stream_ids"] == [3, 4]
    assert data["results"][1]["changed"] is False  # already [3, 4]
    # Channel 30: needs reorder from [4, 3] to [3, 4]
    assert data["results"][2]["sorted_stream_ids"] == [3, 4]
    assert data["results"][2]["changed"] is True


@pytest.mark.asyncio
async def test_compute_sort_mode_resolution(async_client, test_session, seed_stream_stats, mock_settings):
    """mode=resolution sorts by resolution only."""
    seed_stream_stats(
        {"stream_id": 1, "resolution": "1280x720", "bitrate": 8000000},
        {"stream_id": 2, "resolution": "1920x1080", "bitrate": 3000000},
    )

    with patch("routers.stream_stats.get_settings", return_value=mock_settings):
        response = await async_client.post("/api/stream-stats/compute-sort", json={
            "channels": [{"channel_id": 10, "stream_ids": [1, 2]}],
            "mode": "resolution"
        })

    assert response.status_code == 200
    # Higher resolution first despite lower bitrate
    assert response.json()["results"][0]["sorted_stream_ids"] == [2, 1]


@pytest.mark.asyncio
async def test_compute_sort_mode_bitrate(async_client, test_session, seed_stream_stats, mock_settings):
    """mode=bitrate sorts by bitrate only."""
    seed_stream_stats(
        {"stream_id": 1, "resolution": "1920x1080", "bitrate": 3000000},
        {"stream_id": 2, "resolution": "1280x720", "bitrate": 8000000},
    )

    with patch("routers.stream_stats.get_settings", return_value=mock_settings):
        response = await async_client.post("/api/stream-stats/compute-sort", json={
            "channels": [{"channel_id": 10, "stream_ids": [1, 2]}],
            "mode": "bitrate"
        })

    assert response.status_code == 200
    # Higher bitrate first despite lower resolution
    assert response.json()["results"][0]["sorted_stream_ids"] == [2, 1]


@pytest.mark.asyncio
async def test_compute_sort_mode_framerate(async_client, test_session, seed_stream_stats, mock_settings):
    """mode=framerate sorts by framerate only."""
    seed_stream_stats(
        {"stream_id": 1, "fps": "30"},
        {"stream_id": 2, "fps": "60"},
    )

    with patch("routers.stream_stats.get_settings", return_value=mock_settings):
        response = await async_client.post("/api/stream-stats/compute-sort", json={
            "channels": [{"channel_id": 10, "stream_ids": [1, 2]}],
            "mode": "framerate"
        })

    assert response.status_code == 200
    assert response.json()["results"][0]["sorted_stream_ids"] == [2, 1]


@pytest.mark.asyncio
async def test_compute_sort_mode_audio_channels(async_client, test_session, seed_stream_stats, mock_settings):
    """mode=audio_channels sorts by audio channel count."""
    seed_stream_stats(
        {"stream_id": 1, "audio_channels": 2},
        {"stream_id": 2, "audio_channels": 6},
        {"stream_id": 3, "audio_channels": 1},
    )

    with patch("routers.stream_stats.get_settings", return_value=mock_settings):
        response = await async_client.post("/api/stream-stats/compute-sort", json={
            "channels": [{"channel_id": 10, "stream_ids": [1, 2, 3]}],
            "mode": "audio_channels"
        })

    assert response.status_code == 200
    assert response.json()["results"][0]["sorted_stream_ids"] == [2, 1, 3]


@pytest.mark.asyncio
async def test_compute_sort_mode_m3u_priority(async_client, test_session, seed_stream_stats, mock_settings):
    """mode=m3u_priority sorts by M3U account priority."""
    seed_stream_stats(
        {"stream_id": 1},
        {"stream_id": 2},
    )
    mock_settings.m3u_account_priorities = {"1": 100, "2": 50}

    mock_client = AsyncMock()
    mock_client.get_streams_by_ids = AsyncMock(return_value=[
        {"id": 1, "m3u_account": 2},   # priority 50
        {"id": 2, "m3u_account": 1},   # priority 100
    ])

    with patch("routers.stream_stats.get_settings", return_value=mock_settings), \
         patch("routers.stream_stats.get_client", return_value=mock_client):
        response = await async_client.post("/api/stream-stats/compute-sort", json={
            "channels": [{"channel_id": 10, "stream_ids": [1, 2]}],
            "mode": "m3u_priority"
        })

    assert response.status_code == 200
    assert response.json()["results"][0]["sorted_stream_ids"] == [2, 1]


@pytest.mark.asyncio
async def test_compute_sort_empty_channels(async_client, mock_settings):
    """Empty channels list returns empty results."""
    with patch("routers.stream_stats.get_settings", return_value=mock_settings):
        response = await async_client.post("/api/stream-stats/compute-sort", json={
            "channels": [],
            "mode": "smart"
        })

    assert response.status_code == 200
    assert response.json()["results"] == []


@pytest.mark.asyncio
async def test_compute_sort_no_stats(async_client, mock_settings):
    """Stream IDs with no StreamStats rows don't crash."""
    with patch("routers.stream_stats.get_settings", return_value=mock_settings):
        response = await async_client.post("/api/stream-stats/compute-sort", json={
            "channels": [{"channel_id": 10, "stream_ids": [999, 998]}],
            "mode": "smart"
        })

    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 1
    # No stats -> original order preserved
    assert data["results"][0]["sorted_stream_ids"] == [999, 998]
    assert data["results"][0]["changed"] is False


@pytest.mark.asyncio
async def test_compute_sort_respects_deprioritize_failed(async_client, test_session, seed_stream_stats, mock_settings):
    """Failed streams pushed to bottom when deprioritize_failed_streams is enabled."""
    seed_stream_stats(
        {"stream_id": 1, "resolution": "1280x720", "probe_status": "success"},
        {"stream_id": 2, "resolution": "1920x1080", "probe_status": "failed"},
    )

    with patch("routers.stream_stats.get_settings", return_value=mock_settings):
        response = await async_client.post("/api/stream-stats/compute-sort", json={
            "channels": [{"channel_id": 10, "stream_ids": [2, 1]}],
            "mode": "resolution"
        })

    assert response.status_code == 200
    # Failed stream at bottom despite higher resolution
    assert response.json()["results"][0]["sorted_stream_ids"] == [1, 2]


@pytest.mark.asyncio
async def test_compute_sort_invalid_mode(async_client, mock_settings):
    """Invalid sort mode returns 400."""
    with patch("routers.stream_stats.get_settings", return_value=mock_settings):
        response = await async_client.post("/api/stream-stats/compute-sort", json={
            "channels": [{"channel_id": 10, "stream_ids": [1, 2]}],
            "mode": "invalid_mode"
        })

    assert response.status_code == 400
