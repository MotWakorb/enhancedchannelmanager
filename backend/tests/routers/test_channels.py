"""
Unit tests for channel endpoints.

Tests: 22 channel endpoints covering channel CRUD, logos, CSV import/export,
       stream management, number assignment, bulk-commit, and clear-auto-created.
Mocks: get_client() for all Dispatcharr API calls, csv_handler for CSV operations.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestGetChannels:
    """Tests for GET /api/channels."""

    @pytest.mark.asyncio
    async def test_returns_channels(self, async_client):
        """Returns paginated channel list."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 1, "name": "ESPN"}],
            "count": 1,
        }

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1

    @pytest.mark.asyncio
    async def test_passes_filters(self, async_client):
        """Passes search and group filters to client."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0}

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels", params={
                "search": "ESPN", "channel_group": 5, "page": 2,
            })

        assert response.status_code == 200
        mock_client.get_channels.assert_called_once_with(
            page=2, page_size=100, search="ESPN", channel_group=5,
        )

    @pytest.mark.asyncio
    async def test_client_error(self, async_client):
        """Returns 500 on client error."""
        mock_client = AsyncMock()
        mock_client.get_channels.side_effect = Exception("Error")

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels")

        assert response.status_code == 500


class TestCreateChannel:
    """Tests for POST /api/channels."""

    @pytest.mark.asyncio
    async def test_creates_channel(self, async_client):
        """Creates a new channel."""
        mock_client = AsyncMock()
        mock_client.create_channel.return_value = {"id": 1, "name": "ESPN", "channel_number": 100}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels", json={
                "name": "ESPN",
                "channel_number": 100,
            })

        assert response.status_code == 200
        assert response.json()["name"] == "ESPN"

    @pytest.mark.asyncio
    async def test_creates_with_optional_fields(self, async_client):
        """Creates a channel with all optional fields."""
        mock_client = AsyncMock()
        mock_client.create_channel.return_value = {"id": 1, "name": "ESPN"}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels", json={
                "name": "ESPN",
                "channel_group_id": 5,
                "logo_id": 10,
                "tvg_id": "ESPN.us",
            })

        assert response.status_code == 200
        call_data = mock_client.create_channel.call_args[0][0]
        assert call_data["channel_group_id"] == 5
        assert call_data["logo_id"] == 10
        assert call_data["tvg_id"] == "ESPN.us"


class TestGetChannel:
    """Tests for GET /api/channels/{channel_id}."""

    @pytest.mark.asyncio
    async def test_returns_channel(self, async_client):
        """Returns a channel by ID."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN"}

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/1")

        assert response.status_code == 200
        mock_client.get_channel.assert_called_once_with(1)


class TestUpdateChannel:
    """Tests for PATCH /api/channels/{channel_id}."""

    @pytest.mark.asyncio
    async def test_updates_channel(self, async_client):
        """Updates a channel and logs changes."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "Old", "channel_number": 1}
        mock_client.update_channel.return_value = {"id": 1, "name": "New", "channel_number": 1}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.patch("/api/channels/1", json={
                "name": "New",
            })

        assert response.status_code == 200
        mock_client.update_channel.assert_called_once_with(1, {"name": "New"})


class TestDeleteChannel:
    """Tests for DELETE /api/channels/{channel_id}."""

    @pytest.mark.asyncio
    async def test_deletes_channel(self, async_client):
        """Deletes a channel and logs it."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "channel_number": 100}
        mock_client.delete_channel.return_value = None

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.delete("/api/channels/1")

        assert response.status_code == 200
        assert response.json()["success"] is True


class TestGetChannelStreams:
    """Tests for GET /api/channels/{channel_id}/streams."""

    @pytest.mark.asyncio
    async def test_returns_streams(self, async_client):
        """Returns streams for a channel."""
        mock_client = AsyncMock()
        mock_client.get_channel_streams.return_value = [{"id": 10, "name": "ESPN HD"}]

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/1/streams")

        assert response.status_code == 200
        mock_client.get_channel_streams.assert_called_once_with(1)


class TestAddStream:
    """Tests for POST /api/channels/{channel_id}/add-stream."""

    @pytest.mark.asyncio
    async def test_adds_stream(self, async_client):
        """Adds a stream to a channel."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5]}
        mock_client.update_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5, 10]}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/add-stream", json={
                "stream_id": 10,
            })

        assert response.status_code == 200
        mock_client.update_channel.assert_called_once_with(1, {"streams": [5, 10]})

    @pytest.mark.asyncio
    async def test_skips_duplicate(self, async_client):
        """Returns channel as-is if stream already present."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": [10]}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/add-stream", json={
                "stream_id": 10,
            })

        assert response.status_code == 200
        mock_client.update_channel.assert_not_called()


class TestRemoveStream:
    """Tests for POST /api/channels/{channel_id}/remove-stream."""

    @pytest.mark.asyncio
    async def test_removes_stream(self, async_client):
        """Removes a stream from a channel."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5, 10]}
        mock_client.update_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5]}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/remove-stream", json={
                "stream_id": 10,
            })

        assert response.status_code == 200
        mock_client.update_channel.assert_called_once_with(1, {"streams": [5]})

    @pytest.mark.asyncio
    async def test_skips_missing(self, async_client):
        """Returns channel as-is if stream not present."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5]}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/remove-stream", json={
                "stream_id": 99,
            })

        assert response.status_code == 200
        mock_client.update_channel.assert_not_called()


class TestReorderStreams:
    """Tests for POST /api/channels/{channel_id}/reorder-streams."""

    @pytest.mark.asyncio
    async def test_reorders_streams(self, async_client):
        """Reorders streams in a channel."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5, 10]}
        mock_client.update_channel.return_value = {"id": 1, "name": "ESPN", "streams": [10, 5]}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/reorder-streams", json={
                "stream_ids": [10, 5],
            })

        assert response.status_code == 200
        mock_client.update_channel.assert_called_once_with(1, {"streams": [10, 5]})


class TestGetLogos:
    """Tests for GET /api/channels/logos."""

    @pytest.mark.asyncio
    async def test_returns_logos(self, async_client):
        """Returns logos list."""
        mock_client = AsyncMock()
        mock_client.get_logos.return_value = {"results": [{"id": 1, "name": "ESPN"}], "count": 1}

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/logos")

        assert response.status_code == 200


class TestGetLogo:
    """Tests for GET /api/channels/logos/{logo_id}."""

    @pytest.mark.asyncio
    async def test_returns_logo(self, async_client):
        """Returns a single logo."""
        mock_client = AsyncMock()
        mock_client.get_logo.return_value = {"id": 1, "name": "ESPN", "url": "http://example.com/logo.png"}

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/logos/1")

        assert response.status_code == 200
        mock_client.get_logo.assert_called_once_with(1)


class TestCreateLogo:
    """Tests for POST /api/channels/logos."""

    @pytest.mark.asyncio
    async def test_creates_logo(self, async_client):
        """Creates a logo from URL."""
        mock_client = AsyncMock()
        mock_client.create_logo.return_value = {"id": 1, "name": "ESPN", "url": "http://example.com/logo.png"}

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post("/api/channels/logos", json={
                "name": "ESPN",
                "url": "http://example.com/logo.png",
            })

        assert response.status_code == 200


class TestUpdateLogo:
    """Tests for PATCH /api/channels/logos/{logo_id}."""

    @pytest.mark.asyncio
    async def test_updates_logo(self, async_client):
        """Updates a logo."""
        mock_client = AsyncMock()
        mock_client.update_logo.return_value = {"id": 1, "name": "Updated"}

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.patch("/api/channels/logos/1", json={
                "name": "Updated",
            })

        assert response.status_code == 200
        mock_client.update_logo.assert_called_once_with(1, {"name": "Updated"})


class TestDeleteLogo:
    """Tests for DELETE /api/channels/logos/{logo_id}."""

    @pytest.mark.asyncio
    async def test_deletes_logo(self, async_client):
        """Deletes a logo."""
        mock_client = AsyncMock()
        mock_client.delete_logo.return_value = None

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.delete("/api/channels/logos/1")

        assert response.status_code == 200
        assert response.json()["success"] is True


class TestCSVTemplate:
    """Tests for GET /api/channels/csv-template."""

    @pytest.mark.asyncio
    async def test_returns_template(self, async_client):
        """Returns CSV template with correct headers."""
        response = await async_client.get("/api/channels/csv-template")

        assert response.status_code == 200
        assert response.headers.get("content-type") == "text/csv; charset=utf-8"


class TestExportCSV:
    """Tests for GET /api/channels/export-csv."""

    @pytest.mark.asyncio
    async def test_exports_csv(self, async_client):
        """Exports channels as CSV."""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = [{"id": 1, "name": "Sports"}]
        mock_client.get_channels.return_value = {
            "results": [{
                "id": 1, "name": "ESPN", "channel_number": 100,
                "channel_group_id": 1, "tvg_id": "", "tvc_guide_stationid": "",
                "logo_url": "", "streams": [], "auto_created": False,
            }],
            "next": None,
        }

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/export-csv")

        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")


class TestPreviewCSV:
    """Tests for POST /api/channels/preview-csv."""

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_content(self, async_client):
        """Returns empty results for empty content."""
        response = await async_client.post("/api/channels/preview-csv", json={
            "content": "",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["rows"] == []
        assert data["errors"] == []

    @pytest.mark.asyncio
    async def test_parses_valid_csv(self, async_client):
        """Parses valid CSV content."""
        csv_content = "channel_number,name,group_name,tvg_id,gracenote_id,logo_url,stream_urls\n100,ESPN,Sports,ESPN.us,,,\n"

        response = await async_client.post("/api/channels/preview-csv", json={
            "content": csv_content,
        })

        assert response.status_code == 200
        data = response.json()
        assert len(data["rows"]) == 1
        assert data["rows"][0]["name"] == "ESPN"


class TestAssignNumbers:
    """Tests for POST /api/channels/assign-numbers."""

    @pytest.mark.asyncio
    async def test_assigns_numbers(self, async_client):
        """Assigns channel numbers in bulk."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "channel_number": 1}
        mock_client.assign_channel_numbers.return_value = {"success": True}
        mock_settings = MagicMock()
        mock_settings.auto_rename_channel_number = False

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.get_settings", return_value=mock_settings), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/assign-numbers", json={
                "channel_ids": [1],
                "starting_number": 100,
            })

        assert response.status_code == 200
        mock_client.assign_channel_numbers.assert_called_once_with([1], 100)


class TestBulkCommit:
    """Tests for POST /api/channels/bulk-commit."""

    @pytest.mark.asyncio
    async def test_empty_operations(self, async_client):
        """Processes empty operations list."""
        mock_client = AsyncMock()

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/bulk-commit", json={
                "operations": [],
            })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["operationsApplied"] == 0

    @pytest.mark.asyncio
    async def test_validate_only(self, async_client):
        """Returns validation results without executing."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/bulk-commit", json={
                "operations": [
                    {"type": "updateChannel", "channelId": 999, "data": {"name": "New"}},
                ],
                "validateOnly": True,
            })

        assert response.status_code == 200
        data = response.json()
        # Validate-only doesn't execute operations
        assert data["operationsApplied"] == 0

    @pytest.mark.asyncio
    async def test_delete_channel_operation(self, async_client):
        """Processes a delete channel operation."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 1, "name": "ESPN", "streams": []}],
            "count": 1, "next": None,
        }
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "channel_number": 100}
        mock_client.delete_channel.return_value = None

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/bulk-commit", json={
                "operations": [
                    {"type": "deleteChannel", "channelId": 1},
                ],
            })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestClearAutoCreated:
    """Tests for POST /api/channels/clear-auto-created."""

    @pytest.mark.asyncio
    async def test_rejects_empty_groups(self, async_client):
        """Returns 400 for empty group_ids."""
        mock_client = AsyncMock()

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post("/api/channels/clear-auto-created", json={
                "group_ids": [],
            })

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_clears_flags(self, async_client):
        """Clears auto_created flag from matching channels."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 1, "name": "ESPN", "auto_created": True, "channel_group_id": 5, "channel_number": 100},
                {"id": 2, "name": "CNN", "auto_created": False, "channel_group_id": 5, "channel_number": 101},
            ],
            "next": None,
        }
        mock_client.update_channel.return_value = None

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/clear-auto-created", json={
                "group_ids": [5],
            })

        assert response.status_code == 200
        data = response.json()
        assert data["updated_count"] == 1
        # Only the auto_created channel should be updated
        mock_client.update_channel.assert_called_once_with(1, {
            "auto_created": False,
            "auto_created_by": None,
        })

    @pytest.mark.asyncio
    async def test_no_matching_channels(self, async_client):
        """Returns zero count when no auto_created channels found."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 1, "name": "ESPN", "auto_created": False, "channel_group_id": 5},
            ],
            "next": None,
        }

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/clear-auto-created", json={
                "group_ids": [5],
            })

        assert response.status_code == 200
        data = response.json()
        assert data["updated_count"] == 0
