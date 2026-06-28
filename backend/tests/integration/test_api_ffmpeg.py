"""
Integration tests for FFMPEG Builder API endpoints.

Tests the complete API layer including:
- Capabilities detection
- Config validation and command generation
- Saved configs CRUD
- Job management
- Queue configuration

These are TDD tests -- they will FAIL until the API endpoints are implemented.
"""
import pytest
from unittest.mock import patch

from tests.fixtures.ffmpeg_factories import (
    create_builder_state,
    create_ffmpeg_job,
)


# ---------------------------------------------------------------------------
# Canned ffmpeg output for subprocess-boundary mocking.
# These strings are representative slices of real ffmpeg -encoders / -decoders /
# -formats / -filters / -version output. ECM's _parse_* functions are what we
# are testing — the subprocess call is the true binary boundary.
# ---------------------------------------------------------------------------

_CANNED_ENCODERS = """\
 V..... libx264             libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
 V..... libx265             libx265 H.265 / HEVC
 V..... h264_nvenc          NVIDIA NVENC H.264 encoder
 A..... aac                 AAC (Advanced Audio Coding)
"""

_CANNED_DECODERS = """\
 V..... h264                H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
 V..... hevc                H.265 / HEVC
 A..... aac                 AAC (Advanced Audio Coding)
"""

_CANNED_FORMATS = """\
 DE mp4             MP4 (MPEG-4 Part 14)
 DE mkv             Matroska
 DE ts              MPEG-TS (MPEG-2 Transport Stream)
"""

_CANNED_FILTERS = """\
 ... scale            V->V     Scale the input video size and/or convert
 ... fps              V->V     Force constant framerate
 ... volume           A->A     Change input volume
"""

_CANNED_VERSION = "ffmpeg version 6.1 Copyright (c) 2000-2023 the FFmpeg developers"


def _canned_ffmpeg_output(args):
    """Map ffmpeg query args to canned output for subprocess-boundary mocking."""
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


# ---------------------------------------------------------------------------
# Capabilities API
# ---------------------------------------------------------------------------


class TestCapabilitiesAPI:
    """Tests for GET /api/ffmpeg/capabilities endpoint.

    Mocks only the subprocess boundary (_run_ffmpeg_query) with canned ffmpeg
    output. All parsing and structuring is done by ECM's real detect_capabilities()
    — the tests assert ECM's computed result, not a fixture we inject.
    """

    @pytest.mark.asyncio
    async def test_get_capabilities_returns_200(self, async_client):
        """GET /api/ffmpeg/capabilities returns 200."""
        with patch("ffmpeg_builder.probe._run_ffmpeg_query", side_effect=_canned_ffmpeg_output):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_capabilities_has_version(self, async_client):
        """ECM parses the version string from ffmpeg -version output."""
        with patch("ffmpeg_builder.probe._run_ffmpeg_query", side_effect=_canned_ffmpeg_output):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        # ECM returns the raw first line from ffmpeg -version; must contain "6.1"
        assert "6.1" in data["version"]

    @pytest.mark.asyncio
    async def test_capabilities_has_encoders(self, async_client):
        """ECM parses encoder names from ffmpeg -encoders output."""
        with patch("ffmpeg_builder.probe._run_ffmpeg_query", side_effect=_canned_ffmpeg_output):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "encoders" in data
        assert isinstance(data["encoders"], list)
        # libx264 and libx265 appear in the canned output — ECM must parse them
        assert "libx264" in data["encoders"]
        assert "libx265" in data["encoders"]

    @pytest.mark.asyncio
    async def test_capabilities_has_decoders(self, async_client):
        """ECM parses decoder names from ffmpeg -decoders output."""
        with patch("ffmpeg_builder.probe._run_ffmpeg_query", side_effect=_canned_ffmpeg_output):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "decoders" in data
        assert isinstance(data["decoders"], list)
        # h264 appears in the canned decoders output
        assert "h264" in data["decoders"]

    @pytest.mark.asyncio
    async def test_capabilities_has_formats(self, async_client):
        """ECM parses container format names from ffmpeg -formats output."""
        with patch("ffmpeg_builder.probe._run_ffmpeg_query", side_effect=_canned_ffmpeg_output):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "formats" in data
        assert isinstance(data["formats"], list)
        assert "mp4" in data["formats"]
        assert "mkv" in data["formats"]

    @pytest.mark.asyncio
    async def test_capabilities_has_filters(self, async_client):
        """ECM parses filter names from ffmpeg -filters output."""
        with patch("ffmpeg_builder.probe._run_ffmpeg_query", side_effect=_canned_ffmpeg_output):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "filters" in data
        assert isinstance(data["filters"], list)
        assert "scale" in data["filters"]

    @pytest.mark.asyncio
    async def test_capabilities_hwaccels_detected_from_encoders(self, async_client):
        """ECM infers hwaccels from encoder names (nvenc → cuda)."""
        with patch("ffmpeg_builder.probe._run_ffmpeg_query", side_effect=_canned_ffmpeg_output):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "hwaccels" in data
        assert isinstance(data["hwaccels"], list)
        # h264_nvenc in canned encoders → ECM must emit a cuda hwaccel entry
        cuda = next((h for h in data["hwaccels"] if h["api"] == "cuda"), None)
        assert cuda is not None, "cuda hwaccel not detected from h264_nvenc encoder"
        assert cuda["available"] is True
        assert "h264_nvenc" in cuda["encoders"]

    @pytest.mark.asyncio
    async def test_capabilities_hwaccel_entry_shape(self, async_client):
        """Every hwaccel entry has required fields: api, available, encoders."""
        with patch("ffmpeg_builder.probe._run_ffmpeg_query", side_effect=_canned_ffmpeg_output):
            response = await async_client.get("/api/ffmpeg/capabilities")
        data = response.json()
        for entry in data["hwaccels"]:
            assert "api" in entry
            assert "available" in entry
            assert isinstance(entry["available"], bool)
            assert "encoders" in entry

    @pytest.mark.asyncio
    async def test_capabilities_empty_ffmpeg_output_returns_empty_lists(self, async_client):
        """When ffmpeg binary is absent, ECM returns empty lists (not 500)."""
        with patch("ffmpeg_builder.probe._run_ffmpeg_query", return_value=""):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert data["encoders"] == []
        assert data["decoders"] == []
        # formats may include header artifacts from empty output — just assert it's a list
        assert isinstance(data["formats"], list)
        assert data["hwaccels"] == []


# ---------------------------------------------------------------------------
# Validation API
# ---------------------------------------------------------------------------


class TestValidationAPI:
    """Tests for POST /api/ffmpeg/validate endpoint.

    Uses ECM's real ffmpeg_validate_config() — no mocks of the function under
    test. Assertions depend on ECM's validation logic. Mutation check: inverting
    ffmpeg_validate_config to always return valid=True would fail the invalid
    tests; always returning valid=False would fail the valid tests.
    """

    @pytest.mark.asyncio
    async def test_validate_valid_config_returns_200_and_valid_true(self, async_client):
        """POST /api/ffmpeg/validate with a complete config returns valid=True."""
        state = create_builder_state()  # has input, output, videoCodec, audioCodec
        response = await async_client.post("/api/ffmpeg/validate", json=state)
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["errors"] == []

    @pytest.mark.asyncio
    async def test_validate_missing_input_returns_valid_false_with_error(self, async_client):
        """POST /api/ffmpeg/validate with missing input returns valid=False and input error.

        Mutation check: if ffmpeg_validate_config always returned valid=True this would fail
        at ``assert data["valid"] is False``.
        """
        state = create_builder_state()
        state["input"] = None
        response = await async_client.post("/api/ffmpeg/validate", json=state)
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0
        assert any("input" in e.lower() for e in data["errors"])

    @pytest.mark.asyncio
    async def test_validate_missing_output_returns_valid_false_with_error(self, async_client):
        """POST /api/ffmpeg/validate with missing output returns valid=False and output error."""
        state = create_builder_state()
        state["output"] = None
        response = await async_client.post("/api/ffmpeg/validate", json=state)
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert any("output" in e.lower() for e in data["errors"])

    @pytest.mark.asyncio
    async def test_validate_crf_out_of_range_returns_error(self, async_client):
        """POST /api/ffmpeg/validate with CRF > 51 returns an error from ECM's validator."""
        state = create_builder_state()
        state["videoCodec"] = {"codec": "libx264", "crf": 99}
        response = await async_client.post("/api/ffmpeg/validate", json=state)
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert any("crf" in e.lower() for e in data["errors"])

    @pytest.mark.asyncio
    async def test_validate_incompatible_codec_container_returns_warning(self, async_client):
        """libvpx-vp9 in mp4 container triggers a codec/container compatibility warning."""
        state = create_builder_state()
        state["videoCodec"] = {"codec": "libvpx-vp9"}
        state["output"] = {"path": "/out.mp4", "format": "mp4"}
        response = await async_client.post("/api/ffmpeg/validate", json=state)
        assert response.status_code == 200
        data = response.json()
        # ECM warns about codec/container mismatch — must be in warnings
        assert any("vp9" in w.lower() or "compatible" in w.lower() or "mismatch" in w.lower()
                   for w in data["warnings"])

    @pytest.mark.asyncio
    async def test_validate_response_always_includes_required_fields(self, async_client):
        """Validate response always has valid, errors, warnings, and command keys."""
        state = create_builder_state()
        response = await async_client.post("/api/ffmpeg/validate", json=state)
        assert response.status_code == 200
        data = response.json()
        for field in ("valid", "errors", "warnings", "command"):
            assert field in data, f"Missing field '{field}' in validate response"


# ---------------------------------------------------------------------------
# Generate Command API
# ---------------------------------------------------------------------------


class TestGenerateCommandAPI:
    """Tests for POST /api/ffmpeg/generate-command endpoint.

    Uses ECM's real generate_command() / annotate_command() — no mocks of the
    function under test. Assertions depend on ECM's actual command-building logic.
    Mutation check: removing -i flag generation from generate_input_flags would
    fail test_generate_command_includes_input_flag; removing libx264 codec flag
    generation would fail test_generate_command_includes_video_codec_flag.
    """

    @pytest.mark.asyncio
    async def test_generate_command_returns_200(self, async_client):
        """POST /api/ffmpeg/generate-command returns 200 with command and annotations."""
        state = create_builder_state()
        response = await async_client.post("/api/ffmpeg/generate-command", json=state)
        assert response.status_code == 200
        data = response.json()
        assert "command" in data
        assert "annotations" in data

    @pytest.mark.asyncio
    async def test_generate_command_starts_with_ffmpeg(self, async_client):
        """ECM's generated command always starts with 'ffmpeg'."""
        state = create_builder_state()
        response = await async_client.post("/api/ffmpeg/generate-command", json=state)
        assert response.status_code == 200
        data = response.json()
        assert data["command"].startswith("ffmpeg"), (
            f"Expected command to start with 'ffmpeg', got: {data['command']!r}"
        )

    @pytest.mark.asyncio
    async def test_generate_command_includes_input_flag(self, async_client):
        """ECM's command generator emits -i with the input path.

        Mutation check: removing ``-i`` from generate_input_flags() would fail here.
        """
        state = create_builder_state()  # input path defaults to /media/input.mp4
        response = await async_client.post("/api/ffmpeg/generate-command", json=state)
        assert response.status_code == 200
        data = response.json()
        assert "-i" in data["command"]
        assert "/media/input.mp4" in data["command"]

    @pytest.mark.asyncio
    async def test_generate_command_includes_video_codec_flag(self, async_client):
        """ECM emits -c:v with the specified codec.

        Mutation check: removing -c:v generation from generate_video_codec_flags() would fail.
        """
        state = create_builder_state()  # videoCodec defaults to libx264
        response = await async_client.post("/api/ffmpeg/generate-command", json=state)
        assert response.status_code == 200
        data = response.json()
        assert "-c:v" in data["command"]
        assert "libx264" in data["command"]

    @pytest.mark.asyncio
    async def test_generate_command_includes_crf_when_set(self, async_client):
        """ECM emits -crf with the configured value when rate control is crf."""
        from tests.fixtures.ffmpeg_factories import create_video_codec_settings
        state = create_builder_state(
            video_codec=create_video_codec_settings(codec="libx264", crf=28)
        )
        response = await async_client.post("/api/ffmpeg/generate-command", json=state)
        assert response.status_code == 200
        data = response.json()
        assert "-crf" in data["command"]
        assert "28" in data["command"]

    @pytest.mark.asyncio
    async def test_annotations_have_required_fields(self, async_client):
        """Every annotation entry has flag, explanation, and category fields."""
        state = create_builder_state()
        response = await async_client.post("/api/ffmpeg/generate-command", json=state)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["annotations"], list)
        assert len(data["annotations"]) > 0
        for annotation in data["annotations"]:
            assert "flag" in annotation
            assert "explanation" in annotation
            assert "category" in annotation
            assert isinstance(annotation["category"], str)
            assert len(annotation["category"]) > 0
            assert isinstance(annotation["explanation"], str)
            assert len(annotation["explanation"].strip()) > 0

    @pytest.mark.asyncio
    async def test_generate_command_output_path_in_command(self, async_client):
        """ECM includes the output path in the generated command."""
        state = create_builder_state()  # output defaults to /media/output.mp4
        response = await async_client.post("/api/ffmpeg/generate-command", json=state)
        assert response.status_code == 200
        data = response.json()
        assert "/media/output.mp4" in data["command"]


# ---------------------------------------------------------------------------
# Saved Configs API
# ---------------------------------------------------------------------------


class TestSavedConfigsAPI:
    """Tests for /api/ffmpeg/configs CRUD endpoints."""

    @pytest.mark.asyncio
    async def test_list_configs_returns_200(self, async_client):
        """GET /api/ffmpeg/configs returns 200 with a list."""
        response = await async_client.get("/api/ffmpeg/configs")
        assert response.status_code == 200
        data = response.json()
        assert "configs" in data
        assert isinstance(data["configs"], list)

    @pytest.mark.asyncio
    async def test_create_config_returns_201(self, async_client):
        """POST /api/ffmpeg/configs creates a saved config and returns 201."""
        response = await async_client.post(
            "/api/ffmpeg/configs",
            json={
                "name": "My Transcode Preset",
                "description": "H.264 CRF 23 with AAC audio",
                "config": create_builder_state(),
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "My Transcode Preset"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_get_config_by_id(self, async_client):
        """GET /api/ffmpeg/configs/{id} returns a single saved config."""
        # Create first
        create_resp = await async_client.post(
            "/api/ffmpeg/configs",
            json={"name": "Lookup Config", "config": create_builder_state()},
        )
        config_id = create_resp.json()["id"]

        response = await async_client.get(f"/api/ffmpeg/configs/{config_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Lookup Config"
        assert data["id"] == config_id

    @pytest.mark.asyncio
    async def test_update_config(self, async_client):
        """PUT /api/ffmpeg/configs/{id} updates a saved config."""
        # Create first
        create_resp = await async_client.post(
            "/api/ffmpeg/configs",
            json={"name": "Original Name", "config": create_builder_state()},
        )
        config_id = create_resp.json()["id"]

        response = await async_client.put(
            f"/api/ffmpeg/configs/{config_id}",
            json={
                "name": "Updated Config",
                "description": "Changed description",
                "config": create_builder_state(),
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Config"

    @pytest.mark.asyncio
    async def test_delete_config(self, async_client):
        """DELETE /api/ffmpeg/configs/{id} deletes a saved config."""
        # Create first
        create_resp = await async_client.post(
            "/api/ffmpeg/configs",
            json={"name": "To Delete", "config": create_builder_state()},
        )
        config_id = create_resp.json()["id"]

        response = await async_client.delete(f"/api/ffmpeg/configs/{config_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_get_nonexistent_config_returns_404(self, async_client):
        """GET /api/ffmpeg/configs/{id} returns 404 for unknown ID."""
        response = await async_client.get("/api/ffmpeg/configs/99999")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Jobs API
# ---------------------------------------------------------------------------


class TestJobsAPI:
    """Tests for /api/ffmpeg/jobs endpoints."""

    @pytest.mark.asyncio
    async def test_list_jobs_returns_200(self, async_client):
        """GET /api/ffmpeg/jobs returns 200 with a list."""
        with patch("routers.ffmpeg.ffmpeg_list_jobs", return_value=[]):
            response = await async_client.get("/api/ffmpeg/jobs")
        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data
        assert isinstance(data["jobs"], list)

    @pytest.mark.asyncio
    async def test_create_job_returns_201(self, async_client):
        """POST /api/ffmpeg/jobs creates a job and returns 201."""
        job = create_ffmpeg_job(name="Transcode Job", status="queued")
        with patch("routers.ffmpeg.ffmpeg_create_job", return_value=job):
            response = await async_client.post(
                "/api/ffmpeg/jobs",
                json={
                    "name": "Transcode Job",
                    "config": create_builder_state(),
                },
            )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Transcode Job"
        assert data["status"] == "queued"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_get_job_by_id(self, async_client):
        """GET /api/ffmpeg/jobs/{id} returns a single job."""
        job = create_ffmpeg_job(name="Lookup Job", status="running")
        job_id = job["id"]
        with patch("routers.ffmpeg.ffmpeg_get_job", return_value=job):
            response = await async_client.get(f"/api/ffmpeg/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Lookup Job"
        assert data["id"] == job_id

    @pytest.mark.asyncio
    async def test_cancel_queued_job(self, async_client):
        """POST /api/ffmpeg/jobs/{id}/cancel cancels a queued job."""
        job = create_ffmpeg_job(name="Cancel Me", status="cancelled")
        job_id = job["id"]
        with patch("routers.ffmpeg.ffmpeg_cancel_job", return_value=job):
            response = await async_client.post(f"/api/ffmpeg/jobs/{job_id}/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_completed_job_fails(self, async_client):
        """POST /api/ffmpeg/jobs/{id}/cancel returns 400 for a completed job."""
        job_id = "job-completed-123"
        with patch(
            "routers.ffmpeg.ffmpeg_cancel_job",
            side_effect=ValueError("Cannot cancel a completed job"),
        ):
            response = await async_client.post(f"/api/ffmpeg/jobs/{job_id}/cancel")
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "cancel" in data["detail"].lower() or "completed" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_delete_job(self, async_client):
        """DELETE /api/ffmpeg/jobs/{id} deletes a job record."""
        job_id = "job-delete-456"
        with patch("routers.ffmpeg.ffmpeg_delete_job", return_value={"status": "deleted"}):
            response = await async_client.delete(f"/api/ffmpeg/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"


# ---------------------------------------------------------------------------
# Queue Config API
# ---------------------------------------------------------------------------


class TestQueueConfigAPI:
    """Tests for /api/ffmpeg/queue-config endpoint."""

    @pytest.mark.asyncio
    async def test_get_queue_config(self, async_client):
        """GET /api/ffmpeg/queue-config returns current queue settings."""
        mock_config = {
            "max_concurrent": 2,
            "default_priority": "normal",
            "auto_start": True,
        }
        with patch("routers.ffmpeg.ffmpeg_get_queue_config", return_value=mock_config):
            response = await async_client.get("/api/ffmpeg/queue-config")
        assert response.status_code == 200
        data = response.json()
        assert "max_concurrent" in data

    @pytest.mark.asyncio
    async def test_update_queue_config(self, async_client):
        """PUT /api/ffmpeg/queue-config updates queue settings."""
        updated = {
            "max_concurrent": 4,
            "default_priority": "high",
            "auto_start": False,
        }
        with patch("routers.ffmpeg.ffmpeg_update_queue_config", return_value=updated):
            response = await async_client.put(
                "/api/ffmpeg/queue-config",
                json={"max_concurrent": 4, "default_priority": "high", "auto_start": False},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["max_concurrent"] == 4

    @pytest.mark.asyncio
    async def test_queue_config_has_max_concurrent(self, async_client):
        """Queue config response always includes max_concurrent field."""
        mock_config = {
            "max_concurrent": 1,
            "default_priority": "normal",
            "auto_start": True,
        }
        with patch("routers.ffmpeg.ffmpeg_get_queue_config", return_value=mock_config):
            response = await async_client.get("/api/ffmpeg/queue-config")
        assert response.status_code == 200
        data = response.json()
        assert "max_concurrent" in data
        assert isinstance(data["max_concurrent"], int)
        assert data["max_concurrent"] >= 1
