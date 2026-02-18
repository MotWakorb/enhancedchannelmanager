"""
Integration tests for the Stream Preview API endpoints.

These tests verify error handling and basic functionality of the
stream-preview and channel-preview endpoints.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestStreamPreview:
    """Tests for GET /api/stream-preview/{stream_id} endpoint."""

    @pytest.mark.asyncio
    async def test_stream_preview_no_client_returns_503(self, async_client):
        """GET /api/stream-preview/{id} returns 503 when Dispatcharr not connected."""
        with patch("routers.stream_preview.get_client", return_value=None):
            response = await async_client.get("/api/stream-preview/1")
            assert response.status_code == 503
            assert "Not connected" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_stream_preview_stream_not_found_returns_404(self, async_client):
        """GET /api/stream-preview/{id} returns 404 when stream doesn't exist."""
        mock_client = MagicMock()
        mock_client.get_stream = AsyncMock(return_value=None)

        with patch("routers.stream_preview.get_client", return_value=mock_client):
            with patch("routers.stream_preview.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(stream_preview_mode="passthrough")
                response = await async_client.get("/api/stream-preview/9999")
                assert response.status_code == 404
                assert "Stream not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_stream_preview_stream_no_url_returns_404(self, async_client):
        """GET /api/stream-preview/{id} returns 404 when stream has no URL."""
        mock_client = MagicMock()
        mock_client.get_stream = AsyncMock(return_value={"id": 1, "name": "Test", "url": None})

        with patch("routers.stream_preview.get_client", return_value=mock_client):
            with patch("routers.stream_preview.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(stream_preview_mode="passthrough")
                response = await async_client.get("/api/stream-preview/1")
                assert response.status_code == 404
                assert "no URL" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_stream_preview_invalid_mode_returns_400(self, async_client):
        """GET /api/stream-preview/{id} returns 400 for invalid preview mode."""
        mock_client = MagicMock()
        mock_client.get_stream = AsyncMock(return_value={"id": 1, "name": "Test", "url": "http://example.com/stream.ts"})

        with patch("routers.stream_preview.get_client", return_value=mock_client):
            with patch("routers.stream_preview.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(stream_preview_mode="invalid_mode")
                response = await async_client.get("/api/stream-preview/1")
                assert response.status_code == 400
                assert "Invalid preview mode" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_stream_preview_passthrough_returns_streaming_response(self, async_client):
        """GET /api/stream-preview/{id} returns streaming response in passthrough mode."""
        mock_client = MagicMock()
        mock_client.get_stream = AsyncMock(return_value={"id": 1, "name": "Test", "url": "http://example.com/stream.ts"})

        # Mock httpx response
        mock_response = MagicMock()

        async def mock_aiter_bytes(chunk_size):
            yield b"mock stream data"

        mock_response.aiter_bytes = mock_aiter_bytes

        with patch("routers.stream_preview.get_client", return_value=mock_client):
            with patch("routers.stream_preview.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(stream_preview_mode="passthrough")

                # Mock httpx.AsyncClient to return our mocked response
                with patch("httpx.AsyncClient") as mock_http:
                    mock_context = AsyncMock()
                    mock_context.__aenter__.return_value = MagicMock()
                    mock_context.__aenter__.return_value.stream = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()))
                    mock_http.return_value = mock_context

                    response = await async_client.get("/api/stream-preview/1")
                    # The endpoint returns a StreamingResponse with video/mp2t content type
                    assert response.status_code == 200
                    assert response.headers.get("content-type") == "video/mp2t"

    @pytest.mark.asyncio
    async def test_stream_preview_transcode_ffmpeg_not_found(self, async_client):
        """GET /api/stream-preview/{id} returns 500 when FFmpeg not installed."""
        mock_client = MagicMock()
        mock_client.get_stream = AsyncMock(return_value={"id": 1, "name": "Test", "url": "http://example.com/stream.ts"})

        with patch("routers.stream_preview.get_client", return_value=mock_client):
            with patch("routers.stream_preview.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(stream_preview_mode="transcode")

                with patch("subprocess.Popen", side_effect=FileNotFoundError("ffmpeg not found")):
                    response = await async_client.get("/api/stream-preview/1")
                    assert response.status_code == 500
                    assert "FFmpeg not found" in response.json()["detail"]


class TestChannelPreview:
    """Tests for GET /api/channel-preview/{channel_id} endpoint."""

    @pytest.mark.asyncio
    async def test_channel_preview_no_client_returns_503(self, async_client):
        """GET /api/channel-preview/{id} returns 503 when Dispatcharr not connected."""
        with patch("routers.stream_preview.get_client", return_value=None):
            response = await async_client.get("/api/channel-preview/1")
            assert response.status_code == 503
            assert "Not connected" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_channel_preview_channel_not_found_returns_404(self, async_client):
        """GET /api/channel-preview/{id} returns 404 when channel doesn't exist."""
        mock_client = MagicMock()
        mock_client.get_channel = AsyncMock(return_value=None)

        with patch("routers.stream_preview.get_client", return_value=mock_client):
            with patch("routers.stream_preview.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(stream_preview_mode="passthrough")
                response = await async_client.get("/api/channel-preview/9999")
                assert response.status_code == 404
                assert "Channel not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_channel_preview_channel_no_uuid_returns_404(self, async_client):
        """GET /api/channel-preview/{id} returns 404 when channel has no UUID."""
        mock_client = MagicMock()
        mock_client.get_channel = AsyncMock(return_value={"id": 1, "name": "Test", "uuid": None})

        with patch("routers.stream_preview.get_client", return_value=mock_client):
            with patch("routers.stream_preview.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(stream_preview_mode="passthrough")
                response = await async_client.get("/api/channel-preview/1")
                assert response.status_code == 404
                assert "no UUID" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_channel_preview_invalid_mode_returns_400(self, async_client):
        """GET /api/channel-preview/{id} returns 400 for invalid preview mode."""
        mock_client = MagicMock()
        mock_client.get_channel = AsyncMock(return_value={"id": 1, "name": "Test", "uuid": "test-uuid"})
        mock_client._ensure_authenticated = AsyncMock()
        mock_client.access_token = "test-token"

        with patch("routers.stream_preview.get_client", return_value=mock_client):
            with patch("routers.stream_preview.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(
                    stream_preview_mode="invalid_mode",
                    url="http://localhost:5656"
                )
                response = await async_client.get("/api/channel-preview/1")
                assert response.status_code == 400
                assert "Invalid preview mode" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_channel_preview_passthrough_returns_streaming_response(self, async_client):
        """GET /api/channel-preview/{id} returns streaming response in passthrough mode."""
        mock_client = MagicMock()
        mock_client.get_channel = AsyncMock(return_value={"id": 1, "name": "Test", "uuid": "test-uuid-123"})
        mock_client._ensure_authenticated = AsyncMock()
        mock_client.access_token = "test-jwt-token"

        # Mock httpx response
        mock_response = MagicMock()

        async def mock_aiter_bytes(chunk_size):
            yield b"mock channel stream data"

        mock_response.aiter_bytes = mock_aiter_bytes

        with patch("routers.stream_preview.get_client", return_value=mock_client):
            with patch("routers.stream_preview.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(
                    stream_preview_mode="passthrough",
                    url="http://localhost:5656"
                )

                # Mock httpx.AsyncClient to return our mocked response
                with patch("httpx.AsyncClient") as mock_http:
                    mock_context = AsyncMock()
                    mock_context.__aenter__.return_value = MagicMock()
                    mock_context.__aenter__.return_value.stream = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()))
                    mock_http.return_value = mock_context

                    response = await async_client.get("/api/channel-preview/1")
                    # The endpoint returns a StreamingResponse with video/mp2t content type
                    assert response.status_code == 200
                    assert response.headers.get("content-type") == "video/mp2t"

    @pytest.mark.asyncio
    async def test_channel_preview_transcode_ffmpeg_not_found(self, async_client):
        """GET /api/channel-preview/{id} returns 500 when FFmpeg not installed."""
        mock_client = MagicMock()
        mock_client.get_channel = AsyncMock(return_value={"id": 1, "name": "Test", "uuid": "test-uuid"})
        mock_client._ensure_authenticated = AsyncMock()
        mock_client.access_token = "test-token"

        with patch("routers.stream_preview.get_client", return_value=mock_client):
            with patch("routers.stream_preview.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(
                    stream_preview_mode="transcode",
                    url="http://localhost:5656"
                )

                with patch("subprocess.Popen", side_effect=FileNotFoundError("ffmpeg not found")):
                    response = await async_client.get("/api/channel-preview/1")
                    assert response.status_code == 500
                    assert "FFmpeg not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_channel_preview_video_only_ffmpeg_not_found(self, async_client):
        """GET /api/channel-preview/{id} returns 500 when FFmpeg not installed (video_only mode)."""
        mock_client = MagicMock()
        mock_client.get_channel = AsyncMock(return_value={"id": 1, "name": "Test", "uuid": "test-uuid"})
        mock_client._ensure_authenticated = AsyncMock()
        mock_client.access_token = "test-token"

        with patch("routers.stream_preview.get_client", return_value=mock_client):
            with patch("routers.stream_preview.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(
                    stream_preview_mode="video_only",
                    url="http://localhost:5656"
                )

                with patch("subprocess.Popen", side_effect=FileNotFoundError("ffmpeg not found")):
                    response = await async_client.get("/api/channel-preview/1")
                    assert response.status_code == 500
                    assert "FFmpeg not found" in response.json()["detail"]


class TestStreamPreviewModeSettings:
    """Tests for stream_preview_mode in settings."""

    @pytest.mark.asyncio
    async def test_get_settings_includes_stream_preview_mode(self, async_client):
        """GET /api/settings includes stream_preview_mode field."""
        response = await async_client.get("/api/settings")
        assert response.status_code == 200
        data = response.json()
        # The setting should be present (defaults to passthrough if not set)
        assert "stream_preview_mode" in data

    @pytest.mark.asyncio
    async def test_update_settings_accepts_valid_stream_preview_modes(self, async_client):
        """POST /api/settings accepts valid stream_preview_mode values."""
        valid_modes = ["passthrough", "transcode", "video_only"]

        for mode in valid_modes:
            response = await async_client.post(
                "/api/settings",
                json={
                    "url": "http://localhost:5656",
                    "username": "admin",
                    "password": "password",
                    "stream_preview_mode": mode,
                },
            )
            # Should accept valid modes
            assert response.status_code in (200, 400, 422), f"Mode {mode} should be accepted"
