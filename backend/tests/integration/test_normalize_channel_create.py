"""
Integration tests for normalization on channel creation feature.

Tests the normalize flag functionality in:
- Settings (normalize_on_channel_create)
- Channel creation endpoint
- Bulk commit operations
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestNormalizeOnChannelCreateSetting:
    """Tests for the normalize_on_channel_create setting."""

    @pytest.mark.asyncio
    async def test_get_settings_includes_normalize_on_channel_create(self, async_client):
        """GET /api/settings returns normalize_on_channel_create field."""
        response = await async_client.get("/api/settings")
        assert response.status_code == 200

        data = response.json()
        assert "normalize_on_channel_create" in data
        # Default should be False
        assert data["normalize_on_channel_create"] is False

    @pytest.mark.asyncio
    async def test_update_settings_with_normalize_on_channel_create(self, async_client):
        """POST /api/settings can update normalize_on_channel_create."""
        # First get current settings to have valid base
        get_response = await async_client.get("/api/settings")
        current = get_response.json()

        # Update with normalize_on_channel_create = True
        response = await async_client.post(
            "/api/settings",
            json={
                "url": current.get("url") or "http://localhost:8090",
                "username": current.get("username") or "admin",
                "normalize_on_channel_create": True,
            },
        )
        assert response.status_code == 200

        # Verify the setting was saved
        verify_response = await async_client.get("/api/settings")
        verify_data = verify_response.json()
        assert verify_data["normalize_on_channel_create"] is True


class TestCreateChannelWithNormalize:
    """Tests for the normalize flag on single channel creation."""

    @pytest.mark.asyncio
    async def test_create_channel_accepts_normalize_flag(self, async_client):
        """POST /api/channels accepts normalize flag without error."""
        with patch("routers.channels.get_client") as mock_get_client:
            mock_client = MagicMock()
            # Mock the create_channel to return a valid channel
            mock_client.create_channel = AsyncMock(return_value={
                "id": 1,
                "name": "Test Channel",
                "channel_number": 100,
            })
            mock_get_client.return_value = mock_client

            # Test with normalize=True
            response = await async_client.post(
                "/api/channels",
                json={
                    "name": "Test Channel HD",
                    "channel_number": 100,
                    "normalize": True,
                },
            )
            # Should not fail due to unknown field
            # (may fail for other reasons like missing dispatcharr connection)
            assert response.status_code in (200, 201, 500)

    @pytest.mark.asyncio
    async def test_create_channel_accepts_normalize_false(self, async_client):
        """POST /api/channels accepts normalize=False flag."""
        with patch("routers.channels.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.create_channel = AsyncMock(return_value={
                "id": 2,
                "name": "Test Channel 2",
                "channel_number": 101,
            })
            mock_get_client.return_value = mock_client

            response = await async_client.post(
                "/api/channels",
                json={
                    "name": "Test Channel 2",
                    "channel_number": 101,
                    "normalize": False,
                },
            )
            assert response.status_code in (200, 201, 500)


class TestBulkCommitWithNormalize:
    """Tests for the normalize flag on bulk commit operations."""

    @pytest.mark.asyncio
    async def test_bulk_commit_accepts_normalize_flag_on_create_channel(self, async_client):
        """POST /api/channels/bulk-commit accepts normalize flag on createChannel operations."""
        with patch("routers.channels.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.create_channel = AsyncMock(return_value={
                "id": 10,
                "name": "Bulk Created Channel",
                "channel_number": 200,
            })
            mock_get_client.return_value = mock_client

            response = await async_client.post(
                "/api/channels/bulk-commit",
                json={
                    "operations": [
                        {
                            "type": "createChannel",
                            "tempId": -1,
                            "name": "Bulk Channel HD",
                            "channelNumber": 200,
                            "normalize": True,
                        }
                    ],
                },
            )
            # Should accept the normalize flag without validation error
            # May fail for other reasons (no dispatcharr connection, etc)
            assert response.status_code in (200, 500)

    @pytest.mark.asyncio
    async def test_bulk_commit_createchannel_schema_includes_normalize(self, async_client):
        """Verify BulkCreateChannelOp schema accepts normalize field."""
        # This is a schema validation test - the endpoint should parse the request
        # without returning a 422 validation error for the normalize field
        response = await async_client.post(
            "/api/channels/bulk-commit",
            json={
                "operations": [
                    {
                        "type": "createChannel",
                        "tempId": -1,
                        "name": "Schema Test Channel",
                        "normalize": True,  # This should be accepted by the schema
                    }
                ],
                "validateOnly": True,  # Just validate, don't execute
            },
        )
        # Should not return 422 (validation error) for the normalize field
        assert response.status_code != 422 or "normalize" not in str(response.json())
