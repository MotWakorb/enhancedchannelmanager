"""
Integration tests for normalization on channel creation feature.

Tests the normalize flag functionality in:
- Settings (normalize_on_channel_create)
- Channel creation endpoint
- Bulk commit operations
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import config


@pytest.fixture
def isolated_settings_file():
    """Isolate the persisted settings file for a single test.

    The two settings tests below exercise the *real* settings round-trip
    (GET /api/settings → POST /api/settings → GET /api/settings) without
    mocking ``get_settings``/``save_settings``. Both therefore read and
    mutate the shared on-disk settings file (``config.CONFIG_FILE``, i.e.
    ``$CONFIG_DIR/settings.json``).

    Without isolation these tests are order-dependent (bead
    enhancedchannelmanager-o076m):

    - ``test_get_settings_includes_normalize_on_channel_create`` asserts the
      default is ``False``, but fails in full-suite runs where an earlier
      test left ``normalize_on_channel_create: True`` in the file.
    - ``test_update_settings_with_normalize_on_channel_create`` failed in
      isolation because, starting from the empty default URL/username, any
      earlier-leftover state changed whether the auth guard demanded a
      password.

    This fixture stashes the existing file, removes it (so the test starts
    from a known-clean default), clears the settings cache, and restores the
    original file + cache afterwards so the test neither depends on nor
    pollutes shared state.
    """
    config_file = config.CONFIG_FILE
    backup = config_file.read_bytes() if config_file.exists() else None
    if config_file.exists():
        config_file.unlink()
    config.clear_settings_cache()
    try:
        yield
    finally:
        if backup is not None:
            config_file.write_bytes(backup)
        elif config_file.exists():
            config_file.unlink()
        config.clear_settings_cache()


class TestNormalizeOnChannelCreateSetting:
    """Tests for the normalize_on_channel_create setting."""

    @pytest.mark.asyncio
    async def test_get_settings_includes_normalize_on_channel_create(
        self, async_client, isolated_settings_file
    ):
        """GET /api/settings returns normalize_on_channel_create field."""
        response = await async_client.get("/api/settings")
        assert response.status_code == 200

        data = response.json()
        assert "normalize_on_channel_create" in data
        # Default should be False on a clean install.
        assert data["normalize_on_channel_create"] is False

    @pytest.mark.asyncio
    async def test_update_settings_with_normalize_on_channel_create(
        self, async_client, isolated_settings_file
    ):
        """POST /api/settings can update normalize_on_channel_create."""
        # Starting from a clean settings file, GET returns the defaults
        # (empty url/username). Send a complete, valid auth payload —
        # including a password — because the settings endpoint correctly
        # rejects a URL/username change in password mode without one
        # ("password required when changing auth mode, URL or username").
        # Use a non-loopback host: kgz3k now SSRF-validates a changed
        # Dispatcharr URL on save, and ``localhost`` is a blocked loopback
        # host. ``dispatcharr.example`` does not resolve, so the save is
        # allowed (the runtime client re-validates before connecting) — which
        # keeps this test focused on the normalize flag, not URL policy.
        response = await async_client.post(
            "/api/settings",
            json={
                "url": "http://dispatcharr.example:8090",
                "auth_method": "password",
                "username": "admin",
                "password": "test-password",
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
            # Non-validateOnly path returns 202 + job_id (bd-ggxks); the schema
            # would have raised 422 BEFORE the dispatch if the normalize field
            # were rejected, so the 202 here proves acceptance.
            assert response.status_code in (202, 500)

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
        # The schema must accept the normalize field — a 422 here means schema rejection.
        # The or-form of the prior assertion was always-True: this form fails if the
        # endpoint returns 422 for any reason, which is the correct guard for schema acceptance.
        # Mutation check: removing the normalize field from BulkCreateChannelOp would cause
        # Pydantic to reject it with 422, failing this assertion.
        assert response.status_code != 422, (
            f"Schema rejected the normalize field — response: {response.json()}"
        )
