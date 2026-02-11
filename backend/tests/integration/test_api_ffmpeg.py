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
from unittest.mock import patch, MagicMock, AsyncMock

from tests.fixtures.ffmpeg_factories import (
    create_builder_state,
    create_saved_config,
    create_ffmpeg_job,
    create_capabilities,
    create_validation_result,
)


# ---------------------------------------------------------------------------
# Capabilities API
# ---------------------------------------------------------------------------


class TestCapabilitiesAPI:
    """Tests for GET /api/ffmpeg/capabilities endpoint."""

    @pytest.mark.asyncio
    async def test_get_capabilities_returns_200(self, async_client):
        """GET /api/ffmpeg/capabilities returns 200."""
        caps = create_capabilities()
        with patch("main.ffmpeg_detect_capabilities", return_value=caps):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_capabilities_has_version(self, async_client):
        """GET /api/ffmpeg/capabilities response includes version string."""
        caps = create_capabilities(version="6.1")
        with patch("main.ffmpeg_detect_capabilities", return_value=caps):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert data["version"] == "6.1"

    @pytest.mark.asyncio
    async def test_capabilities_has_encoders(self, async_client):
        """GET /api/ffmpeg/capabilities response includes encoders list."""
        caps = create_capabilities()
        with patch("main.ffmpeg_detect_capabilities", return_value=caps):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "encoders" in data
        assert isinstance(data["encoders"], list)
        assert len(data["encoders"]) > 0
        assert "libx264" in data["encoders"]

    @pytest.mark.asyncio
    async def test_capabilities_has_decoders(self, async_client):
        """GET /api/ffmpeg/capabilities response includes decoders list."""
        caps = create_capabilities()
        with patch("main.ffmpeg_detect_capabilities", return_value=caps):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "decoders" in data
        assert isinstance(data["decoders"], list)
        assert len(data["decoders"]) > 0
        assert "h264" in data["decoders"]

    @pytest.mark.asyncio
    async def test_capabilities_has_formats(self, async_client):
        """GET /api/ffmpeg/capabilities response includes formats list."""
        caps = create_capabilities()
        with patch("main.ffmpeg_detect_capabilities", return_value=caps):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "formats" in data
        assert isinstance(data["formats"], list)
        assert "mp4" in data["formats"]
        assert "mkv" in data["formats"]

    @pytest.mark.asyncio
    async def test_capabilities_has_filters(self, async_client):
        """GET /api/ffmpeg/capabilities response includes filters list."""
        caps = create_capabilities()
        with patch("main.ffmpeg_detect_capabilities", return_value=caps):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "filters" in data
        assert isinstance(data["filters"], list)
        assert "scale" in data["filters"]

    @pytest.mark.asyncio
    async def test_capabilities_has_hwaccels(self, async_client):
        """GET /api/ffmpeg/capabilities response includes hwaccels list."""
        caps = create_capabilities()
        with patch("main.ffmpeg_detect_capabilities", return_value=caps):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "hwaccels" in data
        assert isinstance(data["hwaccels"], list)
        assert len(data["hwaccels"]) > 0

    @pytest.mark.asyncio
    async def test_hwaccels_include_cuda_status(self, async_client):
        """Hardware acceleration list includes CUDA availability status."""
        caps = create_capabilities()
        with patch("main.ffmpeg_detect_capabilities", return_value=caps):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        cuda = next((h for h in data["hwaccels"] if h["api"] == "cuda"), None)
        assert cuda is not None
        assert "available" in cuda
        assert isinstance(cuda["available"], bool)

    @pytest.mark.asyncio
    async def test_hwaccels_include_qsv_status(self, async_client):
        """Hardware acceleration list includes QSV availability status."""
        caps = create_capabilities()
        with patch("main.ffmpeg_detect_capabilities", return_value=caps):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        qsv = next((h for h in data["hwaccels"] if h["api"] == "qsv"), None)
        assert qsv is not None
        assert "available" in qsv
        assert isinstance(qsv["available"], bool)

    @pytest.mark.asyncio
    async def test_hwaccels_include_vaapi_status(self, async_client):
        """Hardware acceleration list includes VAAPI availability status."""
        caps = create_capabilities()
        with patch("main.ffmpeg_detect_capabilities", return_value=caps):
            response = await async_client.get("/api/ffmpeg/capabilities")
        assert response.status_code == 200
        data = response.json()
        vaapi = next((h for h in data["hwaccels"] if h["api"] == "vaapi"), None)
        assert vaapi is not None
        assert "available" in vaapi
        assert isinstance(vaapi["available"], bool)


# ---------------------------------------------------------------------------
# Validation API
# ---------------------------------------------------------------------------


class TestValidationAPI:
    """Tests for POST /api/ffmpeg/validate endpoint."""

    @pytest.mark.asyncio
    async def test_validate_valid_config_returns_200(self, async_client):
        """POST /api/ffmpeg/validate with valid config returns 200."""
        state = create_builder_state()
        result = create_validation_result(valid=True)
        with patch("main.ffmpeg_validate_config", return_value=result):
            response = await async_client.post("/api/ffmpeg/validate", json=state)
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True

    @pytest.mark.asyncio
    async def test_validate_returns_command_string(self, async_client):
        """POST /api/ffmpeg/validate returns generated command string."""
        state = create_builder_state()
        result = create_validation_result(
            valid=True,
            command="ffmpeg -i input.mp4 -c:v libx264 -crf 23 output.mp4",
        )
        with patch("main.ffmpeg_validate_config", return_value=result):
            response = await async_client.post("/api/ffmpeg/validate", json=state)
        assert response.status_code == 200
        data = response.json()
        assert "command" in data
        assert "ffmpeg" in data["command"]

    @pytest.mark.asyncio
    async def test_validate_invalid_config_returns_errors(self, async_client):
        """POST /api/ffmpeg/validate with invalid config returns errors list."""
        state = create_builder_state()
        state["input"] = None  # Missing input makes it invalid
        result = create_validation_result(
            valid=False,
            errors=["Input source is required"],
            command="",
        )
        with patch("main.ffmpeg_validate_config", return_value=result):
            response = await async_client.post("/api/ffmpeg/validate", json=state)
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert "errors" in data
        assert len(data["errors"]) > 0

    @pytest.mark.asyncio
    async def test_validate_returns_warnings(self, async_client):
        """POST /api/ffmpeg/validate returns warnings for questionable settings."""
        state = create_builder_state()
        result = create_validation_result(
            valid=True,
            warnings=["VP9 codec in MP4 container may cause compatibility issues"],
        )
        with patch("main.ffmpeg_validate_config", return_value=result):
            response = await async_client.post("/api/ffmpeg/validate", json=state)
        assert response.status_code == 200
        data = response.json()
        assert "warnings" in data
        assert len(data["warnings"]) > 0

    @pytest.mark.asyncio
    async def test_validate_missing_input_returns_error(self, async_client):
        """POST /api/ffmpeg/validate with no input source returns validation error."""
        state = create_builder_state()
        state["input"] = None
        result = create_validation_result(
            valid=False,
            errors=["Input source is required"],
            command="",
        )
        with patch("main.ffmpeg_validate_config", return_value=result):
            response = await async_client.post("/api/ffmpeg/validate", json=state)
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert any("input" in e.lower() for e in data["errors"])

    @pytest.mark.asyncio
    async def test_validate_missing_output_returns_error(self, async_client):
        """POST /api/ffmpeg/validate with no output config returns validation error."""
        state = create_builder_state()
        state["output"] = None
        result = create_validation_result(
            valid=False,
            errors=["Output path is required"],
            command="",
        )
        with patch("main.ffmpeg_validate_config", return_value=result):
            response = await async_client.post("/api/ffmpeg/validate", json=state)
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert any("output" in e.lower() for e in data["errors"])


# ---------------------------------------------------------------------------
# Generate Command API
# ---------------------------------------------------------------------------


class TestGenerateCommandAPI:
    """Tests for POST /api/ffmpeg/generate-command endpoint."""

    @pytest.mark.asyncio
    async def test_generate_command_returns_200(self, async_client):
        """POST /api/ffmpeg/generate-command returns 200 with command string."""
        state = create_builder_state()
        mock_result = {
            "command": "ffmpeg -i /media/input.mp4 -c:v libx264 -crf 23 -c:a aac -b:a 192k /media/output.mp4",
            "annotations": [
                {
                    "flag": "-i",
                    "value": "/media/input.mp4",
                    "category": "input",
                    "explanation": "Input file path",
                },
                {
                    "flag": "-c:v",
                    "value": "libx264",
                    "category": "video",
                    "explanation": "H.264 software encoder",
                },
            ],
        }
        with patch("main.ffmpeg_generate_command", return_value=mock_result):
            response = await async_client.post("/api/ffmpeg/generate-command", json=state)
        assert response.status_code == 200
        data = response.json()
        assert "command" in data
        assert "ffmpeg" in data["command"]

    @pytest.mark.asyncio
    async def test_generate_command_returns_annotations(self, async_client):
        """POST /api/ffmpeg/generate-command returns annotated command segments."""
        state = create_builder_state()
        mock_result = {
            "command": "ffmpeg -i /media/input.mp4 -c:v libx264 -crf 23 /media/output.mp4",
            "annotations": [
                {
                    "flag": "-i",
                    "value": "/media/input.mp4",
                    "category": "input",
                    "explanation": "Input file path",
                },
                {
                    "flag": "-c:v",
                    "value": "libx264",
                    "category": "video",
                    "explanation": "H.264 software encoder",
                },
                {
                    "flag": "-crf",
                    "value": "23",
                    "category": "video",
                    "explanation": "Constant Rate Factor (quality level)",
                },
            ],
        }
        with patch("main.ffmpeg_generate_command", return_value=mock_result):
            response = await async_client.post("/api/ffmpeg/generate-command", json=state)
        assert response.status_code == 200
        data = response.json()
        assert "annotations" in data
        assert isinstance(data["annotations"], list)
        assert len(data["annotations"]) > 0

    @pytest.mark.asyncio
    async def test_annotations_have_categories(self, async_client):
        """Each annotation includes a category field."""
        state = create_builder_state()
        mock_result = {
            "command": "ffmpeg -i /media/input.mp4 -c:v libx264 /media/output.mp4",
            "annotations": [
                {
                    "flag": "-i",
                    "value": "/media/input.mp4",
                    "category": "input",
                    "explanation": "Input file path",
                },
                {
                    "flag": "-c:v",
                    "value": "libx264",
                    "category": "video",
                    "explanation": "H.264 software encoder",
                },
            ],
        }
        with patch("main.ffmpeg_generate_command", return_value=mock_result):
            response = await async_client.post("/api/ffmpeg/generate-command", json=state)
        assert response.status_code == 200
        data = response.json()
        for annotation in data["annotations"]:
            assert "category" in annotation
            assert isinstance(annotation["category"], str)
            assert len(annotation["category"]) > 0

    @pytest.mark.asyncio
    async def test_annotations_have_explanations(self, async_client):
        """Each annotation includes a non-empty explanation string."""
        state = create_builder_state()
        mock_result = {
            "command": "ffmpeg -i /media/input.mp4 -c:v libx264 /media/output.mp4",
            "annotations": [
                {
                    "flag": "-i",
                    "value": "/media/input.mp4",
                    "category": "input",
                    "explanation": "Input file path",
                },
                {
                    "flag": "-c:v",
                    "value": "libx264",
                    "category": "video",
                    "explanation": "H.264 software encoder",
                },
            ],
        }
        with patch("main.ffmpeg_generate_command", return_value=mock_result):
            response = await async_client.post("/api/ffmpeg/generate-command", json=state)
        assert response.status_code == 200
        data = response.json()
        for annotation in data["annotations"]:
            assert "explanation" in annotation
            assert isinstance(annotation["explanation"], str)
            assert len(annotation["explanation"].strip()) > 0


# ---------------------------------------------------------------------------
# Saved Configs API
# ---------------------------------------------------------------------------


class TestSavedConfigsAPI:
    """Tests for /api/ffmpeg/configs CRUD endpoints."""

    @pytest.mark.asyncio
    async def test_list_configs_returns_200(self, async_client):
        """GET /api/ffmpeg/configs returns 200 with a list."""
        with patch("main.ffmpeg_list_configs", return_value=[]):
            response = await async_client.get("/api/ffmpeg/configs")
        assert response.status_code == 200
        data = response.json()
        assert "configs" in data
        assert isinstance(data["configs"], list)

    @pytest.mark.asyncio
    async def test_create_config_returns_201(self, async_client):
        """POST /api/ffmpeg/configs creates a saved config and returns 201."""
        saved = create_saved_config(name="My Transcode Preset")
        with patch("main.ffmpeg_create_config", return_value=saved):
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
        saved = create_saved_config(name="Lookup Config")
        config_id = saved["id"]
        with patch("main.ffmpeg_get_config", return_value=saved):
            response = await async_client.get(f"/api/ffmpeg/configs/{config_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Lookup Config"
        assert data["id"] == config_id

    @pytest.mark.asyncio
    async def test_update_config(self, async_client):
        """PUT /api/ffmpeg/configs/{id} updates a saved config."""
        saved = create_saved_config(name="Updated Config")
        config_id = saved["id"]
        with patch("main.ffmpeg_update_config", return_value=saved):
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
        config_id = 42
        with patch("main.ffmpeg_delete_config", return_value={"status": "deleted"}):
            response = await async_client.delete(f"/api/ffmpeg/configs/{config_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_get_nonexistent_config_returns_404(self, async_client):
        """GET /api/ffmpeg/configs/{id} returns 404 for unknown ID."""
        with patch("main.ffmpeg_get_config", return_value=None):
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
        with patch("main.ffmpeg_list_jobs", return_value=[]):
            response = await async_client.get("/api/ffmpeg/jobs")
        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data
        assert isinstance(data["jobs"], list)

    @pytest.mark.asyncio
    async def test_create_job_returns_201(self, async_client):
        """POST /api/ffmpeg/jobs creates a job and returns 201."""
        job = create_ffmpeg_job(name="Transcode Job", status="queued")
        with patch("main.ffmpeg_create_job", return_value=job):
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
        with patch("main.ffmpeg_get_job", return_value=job):
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
        with patch("main.ffmpeg_cancel_job", return_value=job):
            response = await async_client.post(f"/api/ffmpeg/jobs/{job_id}/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_completed_job_fails(self, async_client):
        """POST /api/ffmpeg/jobs/{id}/cancel returns 400 for a completed job."""
        job_id = "job-completed-123"
        with patch(
            "main.ffmpeg_cancel_job",
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
        with patch("main.ffmpeg_delete_job", return_value={"status": "deleted"}):
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
        with patch("main.ffmpeg_get_queue_config", return_value=mock_config):
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
        with patch("main.ffmpeg_update_queue_config", return_value=updated):
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
        with patch("main.ffmpeg_get_queue_config", return_value=mock_config):
            response = await async_client.get("/api/ffmpeg/queue-config")
        assert response.status_code == 200
        data = response.json()
        assert "max_concurrent" in data
        assert isinstance(data["max_concurrent"], int)
        assert data["max_concurrent"] >= 1
