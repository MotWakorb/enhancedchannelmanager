"""TDD tests for enhancedchannelmanager-jcj0f — cloud-target and sync-target
CRUD tools.

Covers create/update/delete/test_cloud_target (tools/cloud_targets.py, which
already had list_cloud_targets) and list/create/update/delete_sync_target
(new tools/sync_targets.py). Both domains encrypt credentials at rest and
mask them in every response — these tests assert the MCP tools never echo a
full plaintext credential value back, only whatever the (already-masking)
backend response contains.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _cloud_mcp():
    from mcp.server.fastmcp import FastMCP
    from tools.cloud_targets import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _sync_mcp():
    from mcp.server.fastmcp import FastMCP
    from tools.sync_targets import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


# ---------------------------------------------------------------------------
# Cloud targets
# ---------------------------------------------------------------------------
class TestCreateCloudTarget:
    @pytest.mark.asyncio
    async def test_creates_target(self):
        mcp = _cloud_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "id": 1, "name": "Main S3", "provider_type": "s3",
            "credentials": {"access_key": "***WXYZ", "secret_key": "***"},
        }

        with patch("tools.cloud_targets.get_ecm_client", return_value=client):
            result = await mcp.call_tool("create_cloud_target", {
                "name": "Main S3", "provider_type": "s3",
                "credentials": {"access_key": "AKIAWXYZ", "secret_key": "supersecret"},
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["name"] == "Main S3"
        assert body["provider_type"] == "s3"
        assert body["credentials"] == {"access_key": "AKIAWXYZ", "secret_key": "supersecret"}
        assert body["upload_path"] == "/"
        assert body["enabled"] is True
        text = _text(result)
        assert "id=1" in text
        # The tool must not fabricate/echo the plaintext secret in its own message.
        assert "supersecret" not in text


class TestUpdateCloudTarget:
    @pytest.mark.asyncio
    async def test_forwards_only_provided_fields(self):
        mcp = _cloud_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 1, "name": "Main S3"}

        with patch("tools.cloud_targets.get_ecm_client", return_value=client):
            await mcp.call_tool("update_cloud_target", {"target_id": 1, "enabled": False})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"enabled": False}

    @pytest.mark.asyncio
    async def test_no_changes_short_circuits(self):
        mcp = _cloud_mcp()
        client = AsyncMock()

        with patch("tools.cloud_targets.get_ecm_client", return_value=client):
            result = await mcp.call_tool("update_cloud_target", {"target_id": 1})

        assert "No changes specified" in _text(result)
        client.call_endpoint.assert_not_called()


class TestDeleteCloudTarget:
    @pytest.mark.asyncio
    async def test_preview_on_confirm_false_deletes_nothing(self):
        mcp = _cloud_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = [
            {"id": 1, "name": "Main S3", "provider_type": "s3"},
            {"id": 2, "name": "Backup GDrive", "provider_type": "gdrive"},
        ]

        with patch("tools.cloud_targets.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_cloud_target", {"target_id": 1})

        text = _text(result)
        assert "Main S3" in text
        assert "s3" in text
        assert "confirm=True" in text
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["cloud_list_targets"]

    @pytest.mark.asyncio
    async def test_confirm_true_deletes(self):
        mcp = _cloud_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = None

        with patch("tools.cloud_targets.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_cloud_target", {"target_id": 1, "confirm": True})

        assert "deleted" in _text(result).lower()
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["cloud_delete_target"]


class TestTestCloudTarget:
    @pytest.mark.asyncio
    async def test_saved_target_by_id(self):
        mcp = _cloud_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"success": True, "message": "Connected", "provider_info": {}}

        with patch("tools.cloud_targets.get_ecm_client", return_value=client):
            result = await mcp.call_tool("test_cloud_target", {"target_id": 1})

        assert "SUCCESS" in _text(result)
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["cloud_test_target"]

    @pytest.mark.asyncio
    async def test_inline_credentials(self):
        mcp = _cloud_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"success": False, "message": "Invalid key"}

        with patch("tools.cloud_targets.get_ecm_client", return_value=client):
            result = await mcp.call_tool("test_cloud_target", {
                "provider_type": "s3", "credentials": {"access_key": "bad"},
            })

        assert "FAILED" in _text(result)
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["cloud_test_target_inline"]

    @pytest.mark.asyncio
    async def test_neither_target_id_nor_inline_creds_refused(self):
        mcp = _cloud_mcp()
        client = AsyncMock()

        with patch("tools.cloud_targets.get_ecm_client", return_value=client):
            result = await mcp.call_tool("test_cloud_target", {})

        assert "provide" in _text(result).lower()
        client.call_endpoint.assert_not_called()


# ---------------------------------------------------------------------------
# Sync targets
# ---------------------------------------------------------------------------
class TestListSyncTargets:
    @pytest.mark.asyncio
    async def test_lists_targets(self):
        mcp = _sync_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = [
            {"id": 1, "name": "DR Site", "enabled": True, "base_url": "https://dr.example.com", "last_outcome": "success"},
        ]

        with patch("tools.sync_targets.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_sync_targets", {})

        text = _text(result)
        assert "DR Site" in text
        assert "dr.example.com" in text
        assert "success" in text

    @pytest.mark.asyncio
    async def test_none_configured(self):
        mcp = _sync_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = []

        with patch("tools.sync_targets.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_sync_targets", {})

        assert "No sync targets" in _text(result)


class TestCreateSyncTarget:
    @pytest.mark.asyncio
    async def test_creates_target(self):
        mcp = _sync_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 5, "name": "DR Site"}

        with patch("tools.sync_targets.get_ecm_client", return_value=client):
            result = await mcp.call_tool("create_sync_target", {
                "name": "DR Site", "base_url": "https://dr.example.com",
                "credentials": {"token": "abc123"},
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["name"] == "DR Site"
        assert body["base_url"] == "https://dr.example.com"
        assert body["credentials"] == {"token": "abc123"}
        assert body["enabled"] is True
        assert body["insecure"] is False
        text = _text(result)
        assert "id=5" in text
        assert "abc123" not in text

    @pytest.mark.asyncio
    async def test_omits_credentials_when_not_given(self):
        mcp = _sync_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 6, "name": "No Creds"}

        with patch("tools.sync_targets.get_ecm_client", return_value=client):
            await mcp.call_tool("create_sync_target", {"name": "No Creds", "base_url": "https://x.example.com"})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert "credentials" not in body


class TestUpdateSyncTarget:
    @pytest.mark.asyncio
    async def test_forwards_only_provided_fields(self):
        mcp = _sync_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 5, "name": "DR Site"}

        with patch("tools.sync_targets.get_ecm_client", return_value=client):
            await mcp.call_tool("update_sync_target", {"target_id": 5, "enabled": False})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"enabled": False}

    @pytest.mark.asyncio
    async def test_no_changes_short_circuits(self):
        mcp = _sync_mcp()
        client = AsyncMock()

        with patch("tools.sync_targets.get_ecm_client", return_value=client):
            result = await mcp.call_tool("update_sync_target", {"target_id": 5})

        assert "No changes specified" in _text(result)
        client.call_endpoint.assert_not_called()


class TestDeleteSyncTarget:
    @pytest.mark.asyncio
    async def test_preview_on_confirm_false_deletes_nothing(self):
        mcp = _sync_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 5, "name": "DR Site", "base_url": "https://dr.example.com"}

        with patch("tools.sync_targets.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_sync_target", {"target_id": 5})

        text = _text(result)
        assert "DR Site" in text
        assert "confirm=True" in text
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["sync_get_target"]

    @pytest.mark.asyncio
    async def test_confirm_true_deletes(self):
        mcp = _sync_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = None

        with patch("tools.sync_targets.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_sync_target", {"target_id": 5, "confirm": True})

        assert "deleted" in _text(result).lower()
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["sync_delete_target"]
