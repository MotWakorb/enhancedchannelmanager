"""
Integration tests for the cloud-target endpoints (``routers/cloud_targets.py``).

The Export tab (playlist profiles, generate/preview/download, publish,
history) was removed (beads vrrxv / 1w428) and its cloud-target endpoints were
relocated from ``/api/export/cloud-targets`` to ``/api/cloud-targets``. The
cloud-target surface remains because DBAS backup and the ``list_cloud_targets``
MCP tool depend on it; these tests cover that surviving surface.
"""
import json
import pytest
from unittest.mock import AsyncMock, patch


class TestCloudTargetIntegration:
    """Test cloud target CRUD and connection testing."""

    @pytest.mark.asyncio
    @patch("routers.cloud_targets.journal")
    @patch("routers.cloud_targets.encrypt_credentials", return_value="encrypted")
    @patch("routers.cloud_targets.decrypt_credentials", return_value={"bucket_name": "test", "access_key_id": "AKIA1234"})
    async def test_create_lists_with_masked_creds(self, mock_decrypt, mock_encrypt, mock_journal, async_client):
        """Created target should appear in list with masked credentials."""
        resp = await async_client.post("/api/cloud-targets", json={
            "name": "Test S3",
            "provider_type": "s3",
            "credentials": {"bucket_name": "test", "access_key_id": "AKIA12345678"},
            "upload_path": "/exports",
        })
        assert resp.status_code == 201
        target_id = resp.json()["id"]

        resp = await async_client.get("/api/cloud-targets")
        assert resp.status_code == 200
        targets = resp.json()
        assert len(targets) == 1
        assert targets[0]["name"] == "Test S3"
        # Credentials should be masked
        creds = targets[0]["credentials"]
        assert "AKIA12345678" not in json.dumps(creds)

        # Delete
        resp = await async_client.delete(f"/api/cloud-targets/{target_id}")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    @patch("routers.cloud_targets.get_adapter")
    async def test_test_connection_inline(self, mock_get_adapter, async_client):
        """Inline connection test should use provided credentials."""
        from cloud_storage import ConnectionTestResult
        mock_adapter = AsyncMock()
        mock_adapter.test_connection.return_value = ConnectionTestResult(
            success=True, message="Connected", provider_info={"bucket": "test"}
        )
        mock_get_adapter.return_value = mock_adapter

        resp = await async_client.post("/api/cloud-targets/test", json={
            "provider_type": "s3",
            "credentials": {"bucket_name": "test", "access_key_id": "key", "secret_access_key": "secret"},
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
