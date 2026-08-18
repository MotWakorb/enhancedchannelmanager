"""
Unit tests for stream/channel preview endpoints.

Tests: GET /api/stream-preview/{stream_id}, GET /api/channel-preview/{channel_id}
Mocks: get_client(), get_settings(), subprocess, httpx.
Focus on error paths and setup logic (streaming responses tested via status codes).
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from security.ssrf import SSRFError
from routers.stream_preview import stream_generator


class _DeniedInputContext:
    async def __aenter__(self):
        raise SSRFError("denied")

    async def __aexit__(self, *_args):
        return False


@asynccontextmanager
async def _allowed_direct_input(url, **_kwargs):
    yield SimpleNamespace(argument=url, response=None, is_http_relay=False)


@asynccontextmanager
async def _allowed_relay_input(_url, **_kwargs):
    yield SimpleNamespace(argument="http://127.0.0.1:1234/resource/0", response=None, is_http_relay=True)


@pytest.mark.asyncio
async def test_cancelled_preview_closes_http_relay_and_process():
    class InputContext:
        exited = False

        async def __aexit__(self, *_args):
            self.exited = True

    process = MagicMock()
    process.stdout.read.return_value = b"chunk"
    context = InputContext()
    generator = stream_generator(
        process, input_context=context
    )

    assert await anext(generator) == b"chunk"
    await generator.aclose()

    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=5)
    assert context.exited


@asynccontextmanager
async def _mock_stream_response(*_args, **_kwargs):
    response = MagicMock(status_code=200)

    async def aiter_bytes(chunk_size):
        yield b"mock stream data"

    response.aiter_bytes = aiter_bytes
    response.raise_for_status = MagicMock()
    yield response


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
             patch("routers.stream_preview.get_client", return_value=mock_client), \
             patch("routers.stream_preview.prepare_stream_http_url"), \
             patch("routers.stream_preview.stream_request", _mock_stream_response):
            response = await async_client.get("/api/stream-preview/1")

        # StreamingResponse returns 200 (the generator will fail on actual stream but headers are set)
        assert response.status_code == 200
        assert response.headers.get("content-type") == "video/mp2t"

    @pytest.mark.asyncio
    async def test_passthrough_denied_destination_returns_403_before_streaming(
        self, async_client
    ):
        mock_settings = MagicMock(stream_preview_mode="passthrough")
        mock_client = AsyncMock()
        mock_client.get_stream.return_value = {
            "id": 1,
            "url": "http://169.254.169.254/latest/meta-data",
        }

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=mock_client), \
             patch(
                 "routers.stream_preview.prepare_stream_http_url",
                 side_effect=SSRFError("denied"),
             ):
            response = await async_client.get("/api/stream-preview/1")

        assert response.status_code == 403
        assert response.json()["detail"] == "Stream destination is not permitted"

    @pytest.mark.asyncio
    async def test_transcode_ffmpeg_not_found(self, async_client):
        """Returns 500 when FFmpeg is not installed (transcode mode)."""
        mock_settings = MagicMock()
        mock_settings.stream_preview_mode = "transcode"

        mock_client = AsyncMock()
        mock_client.get_stream.return_value = {"id": 1, "url": "http://example.com/stream"}

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=mock_client), \
             patch("routers.stream_preview.validated_subprocess_input", _allowed_direct_input), \
             patch("subprocess.Popen", side_effect=FileNotFoundError("ffmpeg")):
            response = await async_client.get("/api/stream-preview/1")

        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["transcode", "video_only"])
    async def test_ffmpeg_modes_deny_before_spawn(self, async_client, mode):
        mock_settings = MagicMock(stream_preview_mode=mode)
        mock_client = AsyncMock()
        mock_client.get_stream.return_value = {
            "id": 1,
            "url": "http://169.254.169.254/latest/meta-data",
        }

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=mock_client), \
             patch(
                 "routers.stream_preview.validated_subprocess_input",
                 return_value=_DeniedInputContext(),
             ) as validate, \
             patch("subprocess.Popen") as spawn:
            response = await async_client.get("/api/stream-preview/1")

        assert response.status_code == 403
        validate.assert_called_once()
        spawn.assert_not_called()


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
             patch("routers.stream_preview.validated_subprocess_input", _allowed_direct_input), \
             patch("subprocess.Popen", side_effect=FileNotFoundError("ffmpeg")):
            response = await async_client.get("/api/channel-preview/1")

        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["transcode", "video_only"])
    async def test_ffmpeg_modes_deny_before_spawn(self, async_client, mode):
        mock_settings = MagicMock(stream_preview_mode=mode, url="http://dispatcharr:8000")
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "uuid": "abc-123"}
        mock_client._ensure_authenticated = AsyncMock()
        mock_client.access_token = "synthetic-test-token"

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=mock_client), \
             patch(
                 "routers.stream_preview.validated_subprocess_input",
                 return_value=_DeniedInputContext(),
             ) as validate, \
             patch("subprocess.Popen") as spawn:
            response = await async_client.get("/api/channel-preview/1")

        assert response.status_code == 403
        validate.assert_called_once_with(
            "http://dispatcharr:8000/proxy/ts/stream/abc-123",
            headers={"Authorization": "Bearer synthetic-test-token"},
        )
        spawn.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["transcode", "video_only"])
    async def test_channel_bearer_never_reaches_ffmpeg_arguments(
        self, async_client, mode
    ):
        mock_settings = MagicMock(stream_preview_mode=mode, url="http://dispatcharr:8000")
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "uuid": "abc-123"}
        mock_client._ensure_authenticated = AsyncMock()
        mock_client.access_token = "synthetic-test-token"

        with patch("routers.stream_preview.get_settings", return_value=mock_settings), \
             patch("routers.stream_preview.get_client", return_value=mock_client), \
             patch(
                 "routers.stream_preview.validated_subprocess_input",
                 _allowed_relay_input,
             ), \
             patch("subprocess.Popen", side_effect=FileNotFoundError) as spawn:
            response = await async_client.get("/api/channel-preview/1")

        assert response.status_code == 500
        command = spawn.call_args.args[0]
        assert "http://127.0.0.1:1234/resource/0" in command
        assert command[command.index("-protocol_whitelist") + 1] == "http,tcp"
        assert all("synthetic-test-token" not in str(argument) for argument in command)
        assert "-headers" not in command
