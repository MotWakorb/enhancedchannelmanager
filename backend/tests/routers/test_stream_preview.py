"""
Unit tests for stream/channel preview endpoints.

Tests: GET /api/stream-preview/{stream_id}, GET /api/channel-preview/{channel_id}
Mocks: get_client(), get_settings(), subprocess, httpx.
Focus on error paths and setup logic (streaming responses tested via status codes).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestStreamPreview:
    """Tests for GET /api/stream-preview/{stream_id}."""

    @pytest.mark.asyncio
    async def test_returns_404_when_stream_not_found(self, async_client):
        """Returns 404 when stream doesn't exist."""
        mock_settings = MagicMock()
        mock_settings.stream_preview_mode = "passthrough"

        mock_client = AsyncMock()
        mock_client.get_stream.return_value = None

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-preview/99999")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_404_when_no_url(self, async_client):
        """Returns 404 when stream has no URL."""
        mock_settings = MagicMock()
        mock_settings.stream_preview_mode = "passthrough"

        mock_client = AsyncMock()
        mock_client.get_stream.return_value = {"id": 1, "url": None}

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-preview/1")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_503_when_no_client(self, async_client):
        """Returns 503 when not connected to Dispatcharr."""
        mock_settings = MagicMock()
        mock_settings.stream_preview_mode = "passthrough"

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=None):
            response = await async_client.get("/api/stream-preview/1")

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_rejects_invalid_mode(self, async_client):
        """Returns 400 for invalid preview mode."""
        mock_settings = MagicMock()
        mock_settings.stream_preview_mode = "invalid"

        mock_client = AsyncMock()
        mock_client.get_stream.return_value = {"id": 1, "url": "http://example.com/stream"}

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-preview/1")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_passthrough_returns_streaming(self, async_client):
        """Passthrough mode returns StreamingResponse."""
        mock_settings = MagicMock()
        mock_settings.stream_preview_mode = "passthrough"

        mock_client = AsyncMock()
        mock_client.get_stream.return_value = {"id": 1, "url": "http://example.com/stream"}

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-preview/1")

        # StreamingResponse returns 200 (the generator will fail on actual stream but headers are set)
        assert response.status_code == 200
        assert response.headers.get("content-type") == "video/mp2t"

    @pytest.mark.asyncio
    async def test_transcode_ffmpeg_not_found(self, async_client):
        """Returns 500 when FFmpeg is not installed (transcode mode)."""
        mock_settings = MagicMock()
        mock_settings.stream_preview_mode = "transcode"

        mock_client = AsyncMock()
        mock_client.get_stream.return_value = {"id": 1, "url": "http://example.com/stream"}

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=mock_client), \
             patch("subprocess.Popen", side_effect=FileNotFoundError("ffmpeg")):
            response = await async_client.get("/api/stream-preview/1")

        assert response.status_code == 500
        assert "FFmpeg" in response.json()["detail"]


class TestChannelPreview:
    """Tests for GET /api/channel-preview/{channel_id}."""

    @pytest.mark.asyncio
    async def test_returns_404_when_channel_not_found(self, async_client):
        """Returns 404 when channel doesn't exist."""
        mock_settings = MagicMock()
        mock_settings.stream_preview_mode = "passthrough"

        mock_client = AsyncMock()
        mock_client.get_channel.return_value = None

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-preview/99999")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_404_when_no_uuid(self, async_client):
        """Returns 404 when channel has no UUID."""
        mock_settings = MagicMock()
        mock_settings.stream_preview_mode = "passthrough"

        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "uuid": None}

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-preview/1")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_503_when_no_client(self, async_client):
        """Returns 503 when not connected to Dispatcharr."""
        mock_settings = MagicMock()
        mock_settings.stream_preview_mode = "passthrough"

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=None):
            response = await async_client.get("/api/channel-preview/1")

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_rejects_invalid_mode(self, async_client):
        """Returns 400 for invalid preview mode."""
        mock_settings = MagicMock()
        mock_settings.stream_preview_mode = "invalid"
        mock_settings.url = "http://dispatcharr:8000"

        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "uuid": "abc-123"}
        mock_client._ensure_authenticated = AsyncMock()
        mock_client.access_token = "fake-token"

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-preview/1")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_transcode_ffmpeg_not_found(self, async_client):
        """Returns 500 when FFmpeg is not installed (transcode mode)."""
        mock_settings = MagicMock()
        mock_settings.stream_preview_mode = "transcode"
        mock_settings.url = "http://dispatcharr:8000"

        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "uuid": "abc-123"}
        mock_client._ensure_authenticated = AsyncMock()
        mock_client.access_token = "fake-token"

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=mock_client), \
             patch("subprocess.Popen", side_effect=FileNotFoundError("ffmpeg")):
            response = await async_client.get("/api/channel-preview/1")

        assert response.status_code == 500
        assert "FFmpeg" in response.json()["detail"]
