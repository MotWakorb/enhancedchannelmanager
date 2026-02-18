"""
Unit tests for FFMPEG builder endpoints.

Tests: 19 FFMPEG endpoints covering capabilities, probe, validate,
       generate-command, configs CRUD, jobs CRUD, queue config,
       and profiles CRUD.
Mocks: ffmpeg_builder modules (probe, validation, command_generator).
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


class TestGetCapabilities:
    """Tests for GET /api/ffmpeg/capabilities."""

    @pytest.mark.asyncio
    async def test_returns_capabilities(self, async_client):
        """Returns system FFmpeg capabilities."""
        mock_caps = {
            "codecs": {"h264": True},
            "formats": ["mp4", "mkv"],
            "hwaccel": [],
        }

        with patch("routers.ffmpeg.ffmpeg_detect_capabilities", return_value=mock_caps):
            response = await async_client.get("/api/ffmpeg/capabilities")

        assert response.status_code == 200
        data = response.json()
        assert "codecs" in data


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
    """Tests for POST /api/ffmpeg/validate."""

    @pytest.mark.asyncio
    async def test_validates_config(self, async_client):
        """Validates an FFMPEG configuration."""
        mock_result = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "command": "ffmpeg -i input.ts output.mp4",
        }

        with patch("routers.ffmpeg.ffmpeg_validate_config", return_value=mock_result):
            response = await async_client.post("/api/ffmpeg/validate", json={
                "input": {"source": "http://example.com/stream"},
                "output": {"format": "mp4"},
            })

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True

    @pytest.mark.asyncio
    async def test_returns_errors(self, async_client):
        """Returns validation errors."""
        mock_result = {
            "valid": False,
            "errors": ["No input specified"],
            "warnings": [],
            "command": "",
        }

        with patch("routers.ffmpeg.ffmpeg_validate_config", return_value=mock_result):
            response = await async_client.post("/api/ffmpeg/validate", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0


class TestGenerateCommand:
    """Tests for POST /api/ffmpeg/generate-command."""

    @pytest.mark.asyncio
    async def test_generates_command(self, async_client):
        """Generates an annotated FFmpeg command."""
        mock_result = {
            "command": "ffmpeg -i input.ts -c:v copy output.mp4",
            "annotations": [
                {"flag": "-c:v copy", "explanation": "Copy video codec", "category": "video"},
            ],
        }

        with patch("routers.ffmpeg.ffmpeg_generate_command", return_value=mock_result):
            response = await async_client.post("/api/ffmpeg/generate-command", json={
                "input": {"source": "input.ts"},
            })

        assert response.status_code == 200
        data = response.json()
        assert "command" in data


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
        """Creates a config (stub — returns input)."""
        response = await async_client.post("/api/ffmpeg/configs", json={
            "name": "Test Config",
            "settings": {"codec": "h264"},
        })

        assert response.status_code == 201


class TestGetConfig:
    """Tests for GET /api/ffmpeg/configs/{config_id}."""

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 for nonexistent config (stub always returns None)."""
        response = await async_client.get("/api/ffmpeg/configs/1")

        assert response.status_code == 404


class TestDeleteConfig:
    """Tests for DELETE /api/ffmpeg/configs/{config_id}."""

    @pytest.mark.asyncio
    async def test_deletes_config(self, async_client):
        """Deletes a config (stub)."""
        response = await async_client.delete("/api/ffmpeg/configs/1")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"


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
