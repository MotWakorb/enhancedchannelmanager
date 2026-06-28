"""
Unit tests for FFMPEG builder endpoints.

Tests: 19 FFMPEG endpoints covering capabilities, probe, validate,
       generate-command, configs CRUD, jobs CRUD, queue config,
       and profiles CRUD.
Mocks: Only subprocess/binary boundaries (probe_source, _run_ffmpeg_query).
       The ECM parsing and command-generation logic runs unpatched.
       Configs/jobs/queue-config are stubs (not backed by DB yet).
       Profiles use get_session() via conftest.
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from models import FFmpegProfile


def _create_profile(session, name="Test Profile", **overrides):
    """Insert an FFmpegProfile record for testing."""
    config = overrides.pop("config", {"codec": "h264"})
    record = FFmpegProfile(
        name=name,
        config=json.dumps(config),
        **overrides,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


# Canned ffmpeg output for subprocess-boundary mocking (shared with integration tests)
_CANNED_ENCODERS = """\
 V..... libx264             libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
 V..... h264_nvenc          NVIDIA NVENC H.264 encoder
 A..... aac                 AAC (Advanced Audio Coding)
"""
_CANNED_DECODERS = """\
 V..... h264                H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
 A..... aac                 AAC (Advanced Audio Coding)
"""
_CANNED_FORMATS = """\
 DE mp4             MP4 (MPEG-4 Part 14)
 DE mkv             Matroska
"""
_CANNED_FILTERS = " ... scale            V->V     Scale the input video\n"
_CANNED_VERSION = "ffmpeg version 6.1 Copyright"


def _canned_ffmpeg_output(args):
    if args == ["-version"]:
        return _CANNED_VERSION
    if args == ["-encoders"]:
        return _CANNED_ENCODERS
    if args == ["-decoders"]:
        return _CANNED_DECODERS
    if args == ["-formats"]:
        return _CANNED_FORMATS
    if args == ["-filters"]:
        return _CANNED_FILTERS
    return ""


class TestGetCapabilities:
    """Tests for GET /api/ffmpeg/capabilities.

    Mocks only the subprocess boundary. ECM's detect_capabilities() runs real.
    Mutation check: removing h264_nvenc from canned encoders would make cuda
    hwaccel absent — test_detects_cuda_from_nvenc_encoder would catch that.
    """

    @pytest.mark.asyncio
    async def test_returns_capabilities_200(self, async_client):
        """Returns 200 with capabilities dict from ECM parsing."""
        with patch("ffmpeg_builder.probe._run_ffmpeg_query", side_effect=_canned_ffmpeg_output):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        # ECM must parse libx264 from the canned encoder list
        assert "libx264" in data["encoders"]
        assert "mp4" in data["formats"]
        assert "scale" in data["filters"]

    @pytest.mark.asyncio
    async def test_detects_cuda_from_nvenc_encoder(self, async_client):
        """ECM infers cuda hwaccel from h264_nvenc in canned encoder list.

        Mutation check: if _detect_hwaccels() were deleted or broken, this would fail.
        """
        with patch("ffmpeg_builder.probe._run_ffmpeg_query", side_effect=_canned_ffmpeg_output):
            response = await async_client.get("/api/ffmpeg/capabilities")
        data = response.json()
        cuda = next((h for h in data["hwaccels"] if h["api"] == "cuda"), None)
        assert cuda is not None
        assert cuda["available"] is True


class TestProbeSource:
    """Tests for POST /api/ffmpeg/probe."""

    @pytest.mark.asyncio
    async def test_probes_successfully(self, async_client):
        """Returns probe results for a valid source."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.streams = [{"codec_type": "video", "codec_name": "h264"}]
        mock_result.format_name = "mpegts"
        mock_result.duration = 0.0
        mock_result.bit_rate = 5000000
        mock_result.size = 0

        with patch("routers.ffmpeg.probe_source", return_value=mock_result):
            response = await async_client.post("/api/ffmpeg/probe", json={
                "path": "http://example.com/stream.ts",
            })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["streams"]) == 1

    @pytest.mark.asyncio
    async def test_returns_400_on_failure(self, async_client):
        """Returns 400 when probe fails."""
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error = "Connection refused"

        with patch("routers.ffmpeg.probe_source", return_value=mock_result):
            response = await async_client.post("/api/ffmpeg/probe", json={
                "path": "http://unreachable/stream.ts",
            })

        assert response.status_code == 400


class TestValidateConfig:
    """Tests for POST /api/ffmpeg/validate.

    Uses ECM's real ffmpeg_validate_config(). No mock of the function under test.
    Mutation check: inverting validate_config to always return valid=False would
    fail test_validates_complete_config; always returning valid=True would fail
    test_returns_error_for_missing_input.
    """

    @pytest.mark.asyncio
    async def test_validates_complete_config(self, async_client):
        """ECM validates a complete config as valid=True."""
        state = {
            "input": {"type": "file", "path": "/media/input.mp4"},
            "output": {"path": "/media/output.mp4", "format": "mp4"},
            "videoCodec": {"codec": "libx264", "crf": 23},
            "audioCodec": {"codec": "aac"},
        }
        response = await async_client.post("/api/ffmpeg/validate", json=state)
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["errors"] == []

    @pytest.mark.asyncio
    async def test_returns_error_for_missing_input(self, async_client):
        """ECM marks config invalid and returns error when input is absent.

        Mutation check: if validate_config were replaced with a stub returning
        valid=True, this assert would fail.
        """
        response = await async_client.post("/api/ffmpeg/validate", json={
            "output": {"path": "/out.mp4", "format": "mp4"},
        })
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0
        assert any("input" in e.lower() for e in data["errors"])


class TestGenerateCommand:
    """Tests for POST /api/ffmpeg/generate-command.

    Uses ECM's real generate_command() / annotate_command(). No mock of the
    function under test. Mutation check: removing -i flag generation would fail
    test_generates_command_with_input_flag.
    """

    @pytest.mark.asyncio
    async def test_generates_command_with_input_flag(self, async_client):
        """ECM generates a command that includes the -i flag with the input path.

        Mutation check: removing the -i flag from generate_input_flags() breaks this.
        """
        response = await async_client.post("/api/ffmpeg/generate-command", json={
            "input": {"type": "file", "path": "/media/input.ts"},
            "output": {"path": "/media/output.mp4", "format": "mp4"},
        })
        assert response.status_code == 200
        data = response.json()
        assert "command" in data
        assert data["command"].startswith("ffmpeg")
        assert "-i" in data["command"]
        assert "/media/input.ts" in data["command"]


class TestListConfigs:
    """Tests for GET /api/ffmpeg/configs."""

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, async_client):
        """Returns empty configs list (stub)."""
        response = await async_client.get("/api/ffmpeg/configs")

        assert response.status_code == 200
        assert response.json()["configs"] == []


class TestCreateConfig:
    """Tests for POST /api/ffmpeg/configs."""

    @pytest.mark.asyncio
    async def test_creates_config(self, async_client):
        """Creates a config via persistence layer."""
        response = await async_client.post("/api/ffmpeg/configs", json={
            "name": "Test Config",
            "config": {"input": {}, "output": {}, "videoCodec": {}, "audioCodec": {}},
        })

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Test Config"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_creates_config_invalid_returns_400(self, async_client):
        """Returns 400 for invalid config (missing required keys)."""
        response = await async_client.post("/api/ffmpeg/configs", json={
            "name": "Bad Config",
            "config": {"codec": "h264"},
        })

        assert response.status_code == 400


class TestGetConfig:
    """Tests for GET /api/ffmpeg/configs/{config_id}."""

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 for nonexistent config."""
        response = await async_client.get("/api/ffmpeg/configs/99999")

        assert response.status_code == 404


class TestDeleteConfig:
    """Tests for DELETE /api/ffmpeg/configs/{config_id}."""

    @pytest.mark.asyncio
    async def test_deletes_config(self, async_client):
        """Creates then deletes a config."""
        # Create first
        create_resp = await async_client.post("/api/ffmpeg/configs", json={
            "name": "To Delete",
            "config": {"input": {}, "output": {}, "videoCodec": {}, "audioCodec": {}},
        })
        config_id = create_resp.json()["id"]

        response = await async_client.delete(f"/api/ffmpeg/configs/{config_id}")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, async_client):
        """Returns 404 for deleting nonexistent config."""
        response = await async_client.delete("/api/ffmpeg/configs/99999")

        assert response.status_code == 404


class TestListJobs:
    """Tests for GET /api/ffmpeg/jobs."""

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, async_client):
        """Returns empty jobs list (stub)."""
        response = await async_client.get("/api/ffmpeg/jobs")

        assert response.status_code == 200
        assert response.json()["jobs"] == []


class TestGetJob:
    """Tests for GET /api/ffmpeg/jobs/{job_id}."""

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 for nonexistent job (stub always returns None)."""
        response = await async_client.get("/api/ffmpeg/jobs/abc")

        assert response.status_code == 404


class TestCancelJob:
    """Tests for POST /api/ffmpeg/jobs/{job_id}/cancel."""

    @pytest.mark.asyncio
    async def test_cancels_job(self, async_client):
        """Cancels a job (stub)."""
        response = await async_client.post("/api/ffmpeg/jobs/abc/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"


class TestDeleteJob:
    """Tests for DELETE /api/ffmpeg/jobs/{job_id}."""

    @pytest.mark.asyncio
    async def test_deletes_job(self, async_client):
        """Deletes a job (stub)."""
        response = await async_client.delete("/api/ffmpeg/jobs/abc")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"


class TestGetQueueConfig:
    """Tests for GET /api/ffmpeg/queue-config."""

    @pytest.mark.asyncio
    async def test_returns_config(self, async_client):
        """Returns queue configuration (stub)."""
        response = await async_client.get("/api/ffmpeg/queue-config")

        assert response.status_code == 200
        data = response.json()
        assert data["max_concurrent"] == 2


class TestUpdateQueueConfig:
    """Tests for PUT /api/ffmpeg/queue-config."""

    @pytest.mark.asyncio
    async def test_updates_config(self, async_client):
        """Updates queue configuration (stub — returns input)."""
        response = await async_client.put("/api/ffmpeg/queue-config", json={
            "max_concurrent": 4,
        })

        assert response.status_code == 200


class TestListProfiles:
    """Tests for GET /api/ffmpeg/profiles."""

    @pytest.mark.asyncio
    async def test_returns_profiles(self, async_client, test_session):
        """Returns saved profiles."""
        _create_profile(test_session, name="Profile 1")
        _create_profile(test_session, name="Profile 2")

        response = await async_client.get("/api/ffmpeg/profiles")

        assert response.status_code == 200
        data = response.json()
        assert len(data["profiles"]) == 2

    @pytest.mark.asyncio
    async def test_returns_empty(self, async_client):
        """Returns empty list when no profiles exist."""
        response = await async_client.get("/api/ffmpeg/profiles")

        assert response.status_code == 200
        assert response.json()["profiles"] == []


class TestCreateProfile:
    """Tests for POST /api/ffmpeg/profiles."""

    @pytest.mark.asyncio
    async def test_creates_profile(self, async_client):
        """Creates a new profile."""
        response = await async_client.post("/api/ffmpeg/profiles", json={
            "name": "My Profile",
            "config": {"codec": "h264", "bitrate": 5000},
        })

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "My Profile"
        assert data["config"]["codec"] == "h264"

    @pytest.mark.asyncio
    async def test_rejects_empty_name(self, async_client):
        """Returns 400 when name is empty."""
        response = await async_client.post("/api/ffmpeg/profiles", json={
            "name": "",
            "config": {"codec": "h264"},
        })

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_missing_config(self, async_client):
        """Returns 400 when config is missing."""
        response = await async_client.post("/api/ffmpeg/profiles", json={
            "name": "No Config",
        })

        assert response.status_code == 400


class TestDeleteProfile:
    """Tests for DELETE /api/ffmpeg/profiles/{profile_id}."""

    @pytest.mark.asyncio
    async def test_deletes_profile(self, async_client, test_session):
        """Deletes a profile."""
        profile = _create_profile(test_session, name="To Delete")

        response = await async_client.delete(f"/api/ffmpeg/profiles/{profile.id}")

        assert response.status_code == 200
        assert response.json()["success"] is True

        # Verify deleted from DB
        remaining = test_session.query(FFmpegProfile).filter_by(id=profile.id).first()
        assert remaining is None

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 for nonexistent profile."""
        response = await async_client.delete("/api/ffmpeg/profiles/999")

        assert response.status_code == 404
