"""
Unit tests for channel endpoints.

Tests: 22 channel endpoints covering channel CRUD, logos, CSV import/export,
       stream management, number assignment, bulk-commit, and clear-auto-created.
Mocks: get_client() for all Dispatcharr API calls, csv_handler for CSV operations.
"""
import httpx
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

    @pytest.mark.asyncio
    async def test_upstream_4xx_surfaces_400_not_500(self, async_client):
        """Dispatcharr 4xx (e.g. bad channel_group_id) maps to 400 with detail,
        not an opaque 500 (bd-1wq7z.22).

        The client embeds the upstream status + body in a bare Exception
        message: ``"Channel creation failed: 400 - <json body>"``.
        """
        mock_client = AsyncMock()
        mock_client.create_channel.side_effect = Exception(
            'Channel creation failed: 400 - '
            '{"channel_group_id": ["Invalid pk \\"999\\" - object does not exist."]}'
        )

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels", json={
                "name": "ESPN",
                "channel_group_id": 999,
            })

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "channel_group_id" in detail
        assert "does not exist" in detail

    @pytest.mark.asyncio
    async def test_genuine_server_error_still_500(self, async_client):
        """A non-upstream error (no embedded 4xx) stays a 500 (bd-1wq7z.22)."""
        mock_client = AsyncMock()
        mock_client.create_channel.side_effect = RuntimeError("boom")

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels", json={"name": "ESPN"})

        assert response.status_code == 500


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

    @pytest.mark.asyncio
    async def test_missing_channel_returns_404_not_500(self, async_client):
        """A missing channel id surfaces upstream 404 as 404, not 500
        (bd-1wq7z.22). The client raises httpx.HTTPStatusError via
        raise_for_status()."""
        request = httpx.Request("GET", "http://disp/api/channels/channels/999/")
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.get_channel.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/999")

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]


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

    @pytest.mark.asyncio
    async def test_missing_channel_returns_404_not_500(self, async_client):
        """Updating a nonexistent channel surfaces upstream 404 as 404, not 500
        (bd-lq38l.4). The before-state get_channel raises HTTPStatusError."""
        request = httpx.Request("GET", "http://disp/api/channels/channels/999/")
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.get_channel.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.patch("/api/channels/999", json={"name": "New"})

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_bad_group_id_surfaces_400_not_500(self, async_client):
        """A bad channel_group_id is rejected upstream as 4xx -> 400 with detail,
        not an opaque 500 (bd-lq38l.4)."""
        request = httpx.Request("PATCH", "http://disp/api/channels/channels/1/")
        upstream = httpx.Response(
            400, request=request,
            text='{"channel_group_id": ["Invalid pk \\"999\\" - object does not exist."]}',
        )
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "Old"}
        mock_client.update_channel.side_effect = httpx.HTTPStatusError(
            "400 Client Error", request=request, response=upstream
        )

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.patch("/api/channels/1", json={"channel_group_id": 999})

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "channel_group_id" in detail
        assert "does not exist" in detail

    @pytest.mark.asyncio
    async def test_genuine_server_error_still_500(self, async_client):
        """A non-upstream error stays a 500 (bd-lq38l.4)."""
        mock_client = AsyncMock()
        mock_client.get_channel.side_effect = RuntimeError("boom")

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.patch("/api/channels/1", json={"name": "New"})

        assert response.status_code == 500


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

    @pytest.mark.asyncio
    async def test_missing_channel_returns_404_not_500(self, async_client):
        """Deleting a nonexistent channel surfaces upstream 404 as 404, not 500
        (bd-lq38l.4)."""
        request = httpx.Request("GET", "http://disp/api/channels/channels/999/")
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.get_channel.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.delete("/api/channels/999")

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]


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

    @pytest.mark.asyncio
    async def test_missing_channel_returns_404_not_500(self, async_client):
        """A missing channel id surfaces upstream 404 as 404, not an opaque 500
        (bd-8w1ba). The client's get_channel_streams raises httpx.HTTPStatusError
        via raise_for_status() when Dispatcharr 404s the unknown channel."""
        request = httpx.Request(
            "GET", "http://disp/api/channels/channels/999999/streams/"
        )
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.get_channel_streams.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/999999/streams")

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_non_upstream_error_stays_500(self, async_client):
        """A non-upstream error (no embedded 4xx) stays a 500 (bd-8w1ba)."""
        mock_client = AsyncMock()
        mock_client.get_channel_streams.side_effect = RuntimeError("boom")

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/1/streams")

        assert response.status_code == 500


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

    @pytest.mark.asyncio
    async def test_missing_channel_returns_404_not_500(self, async_client):
        """Adding a stream to a nonexistent channel surfaces upstream 404 as 404,
        not 500 (bd-lq38l.4)."""
        request = httpx.Request("GET", "http://disp/api/channels/channels/999/")
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.get_channel.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/999/add-stream", json={
                "stream_id": 10,
            })

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_bad_stream_id_surfaces_400_not_500(self, async_client):
        """A foreign/nonexistent stream id is rejected upstream as 4xx -> 400 with
        detail, not an opaque 500 (bd-lq38l.4)."""
        request = httpx.Request("PATCH", "http://disp/api/channels/channels/1/")
        upstream = httpx.Response(
            400, request=request,
            text='{"streams": ["Invalid pk \\"999\\" - object does not exist."]}',
        )
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5]}
        mock_client.update_channel.side_effect = httpx.HTTPStatusError(
            "400 Client Error", request=request, response=upstream
        )

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/add-stream", json={
                "stream_id": 999,
            })

        assert response.status_code == 400
        assert "does not exist" in response.json()["detail"]


class TestAddStreams:
    """Tests for POST /api/channels/{channel_id}/add-streams (bulk add, bd-02xjj / GH #223)."""

    @pytest.mark.asyncio
    async def test_adds_multiple_streams_in_one_roundtrip(self, async_client):
        """Appends all new streams with a single get_channel + update_channel."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5]}
        mock_client.update_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5, 10, 11, 12]}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/add-streams", json={
                "stream_ids": [10, 11, 12],
            })

        assert response.status_code == 200
        data = response.json()
        assert data["added"] == [10, 11, 12]
        assert data["skipped"] == []
        # Exactly one update_channel call regardless of batch size.
        mock_client.update_channel.assert_called_once_with(1, {"streams": [5, 10, 11, 12]})
        assert mock_client.get_channel.call_count == 1

    @pytest.mark.asyncio
    async def test_dedups_against_existing_streams(self, async_client):
        """Streams already on the channel are skipped, order preserved."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5, 10]}
        mock_client.update_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5, 10, 11]}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/add-streams", json={
                "stream_ids": [10, 11, 5],
            })

        assert response.status_code == 200
        data = response.json()
        assert data["added"] == [11]
        assert sorted(data["skipped"]) == [5, 10]
        mock_client.update_channel.assert_called_once_with(1, {"streams": [5, 10, 11]})

    @pytest.mark.asyncio
    async def test_noop_when_all_already_present(self, async_client):
        """No update_channel call when every requested stream is already on the channel."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5, 10]}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/add-streams", json={
                "stream_ids": [5, 10],
            })

        assert response.status_code == 200
        data = response.json()
        assert data["added"] == []
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_client_error(self, async_client):
        """Returns 500 on Dispatcharr client error."""
        mock_client = AsyncMock()
        mock_client.get_channel.side_effect = Exception("boom")

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/add-streams", json={
                "stream_ids": [10],
            })

        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_missing_channel_returns_404_not_500(self, async_client):
        """Bulk-adding streams to a nonexistent channel surfaces upstream 404 as
        404, not 500 (bd-lq38l.4)."""
        request = httpx.Request("GET", "http://disp/api/channels/channels/999/")
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.get_channel.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/999/add-streams", json={
                "stream_ids": [10],
            })

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]


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

    @pytest.mark.asyncio
    async def test_missing_channel_returns_404_not_500(self, async_client):
        """Removing a stream from a nonexistent channel surfaces upstream 404 as
        404, not 500 (bd-lq38l.4)."""
        request = httpx.Request("GET", "http://disp/api/channels/channels/999/")
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.get_channel.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/999/remove-stream", json={
                "stream_id": 10,
            })

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]


class TestReorderStreams:
    """Tests for POST /api/channels/{channel_id}/reorder-streams."""

    @pytest.mark.asyncio
    async def test_reorders_streams(self, async_client):
        """Reorders streams in a channel."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5, 10]}
        mock_client.update_channel.return_value = {"id": 1, "name": "ESPN", "streams": [10, 5]}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal") as mock_journal:
            response = await async_client.post("/api/channels/1/reorder-streams", json={
                "stream_ids": [10, 5],
            })

        assert response.status_code == 200
        mock_client.update_channel.assert_called_once_with(1, {"streams": [10, 5]})
        # Journal entry still logged on success.
        mock_journal.log_entry.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_partial_list_that_would_detach(self, async_client):
        """A partial list missing a current stream is rejected (data-loss guard).

        bd-1wq7z.3: reorder-streams REPLACES the channel's stream set. A partial
        list silently detached the omitted streams. The guard must reject and
        NOT call update_channel.
        """
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5001, 5002]}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/reorder-streams", json={
                "stream_ids": [5002],
            })

        assert response.status_code == 400
        detail = response.json()["detail"]
        # Names the stream that would be detached so the operator can act.
        assert "5001" in detail
        # The core guarantee: no detach happened.
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_unknown_stream_not_on_channel(self, async_client):
        """A stream id not on the channel is rejected."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5, 10]}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/reorder-streams", json={
                "stream_ids": [5, 10, 99],
            })

        assert response.status_code == 400
        assert "99" in response.json()["detail"]
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_duplicate_stream_id(self, async_client):
        """A duplicated id in the list is rejected."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5, 10]}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/reorder-streams", json={
                "stream_ids": [5, 5],
            })

        assert response.status_code == 400
        assert "5" in response.json()["detail"]
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_empty_list_on_channel_with_streams(self, async_client):
        """An empty list on a channel that has streams is rejected (would detach all)."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5, 10]}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/reorder-streams", json={
                "stream_ids": [],
            })

        assert response.status_code == 400
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_list_on_empty_channel_is_noop(self, async_client):
        """Empty list on a channel with no streams is a sensible no-op (200)."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 1, "name": "ESPN", "streams": []}
        mock_client.update_channel.return_value = {"id": 1, "name": "ESPN", "streams": []}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/reorder-streams", json={
                "stream_ids": [],
            })

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_streams_returned_as_objects_still_validates(self, async_client):
        """Defensive: if upstream returns stream objects (dicts) rather than bare
        ints, the guard still extracts ids and validates correctly."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {
            "id": 1, "name": "ESPN",
            "streams": [{"id": 5001}, {"id": 5002}],
        }
        mock_client.update_channel.return_value = {"id": 1, "name": "ESPN", "streams": [5002, 5001]}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/1/reorder-streams", json={
                "stream_ids": [5002, 5001],
            })

        assert response.status_code == 200
        mock_client.update_channel.assert_called_once_with(1, {"streams": [5002, 5001]})

    @pytest.mark.asyncio
    async def test_missing_channel_returns_404_not_500(self, async_client):
        """Reordering streams on a nonexistent channel surfaces upstream 404 as
        404, not 500 (bd-lq38l.4). The before-state get_channel raises 404."""
        request = httpx.Request("GET", "http://disp/api/channels/channels/999/")
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.get_channel.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/999/reorder-streams", json={
                "stream_ids": [10, 5],
            })

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]


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

    @pytest.mark.asyncio
    async def test_emits_per_request_info_diagnostic(self, async_client, caplog):
        """Emits the bd-nh50y operator-grepable INFO line per request.

        Contract: every successful GET /logos call must emit a single INFO
        line tagged "[CHANNELS-LOGO] GET /logos" that names page, page_size,
        search, the result count, elapsed ms, and the next-page flag. This
        is the line operators grep when triaging "logos not loading" reports
        — it lets us correlate the backend response to the frontend log
        sequence emitted by getAllLogos() in services/api.ts.
        """
        import logging
        mock_client = AsyncMock()
        mock_client.get_logos.return_value = {
            "results": [{"id": 1, "name": "ESPN"}, {"id": 2, "name": "FOX"}],
            "count": 2,
            "next": None,
        }

        with patch("routers.channels.get_client", return_value=mock_client):
            with caplog.at_level(logging.INFO, logger="routers.channels"):
                response = await async_client.get(
                    "/api/channels/logos?page=3&page_size=250&search=ESPN",
                )

        assert response.status_code == 200
        info_lines = [
            r.getMessage() for r in caplog.records
            if r.levelno == logging.INFO and "[CHANNELS-LOGO]" in r.getMessage()
        ]
        assert len(info_lines) == 1, f"Expected exactly one INFO diagnostic, got: {info_lines}"
        line = info_lines[0]
        # Required fields — operators grep for these
        assert "GET /logos" in line
        assert "page=3" in line
        assert "page_size=250" in line
        assert "search=ESPN" in line
        assert "returned 2 logos" in line
        assert "next=false" in line
        # Elapsed-ms field is present and formatted as a number followed by "ms"
        import re
        assert re.search(r"in \d+\.\dms", line), f"Missing elapsed-ms field in: {line}"


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

    @pytest.mark.asyncio
    async def test_reorder_full_permutation_succeeds(self, async_client):
        """A reorderChannelStreams op whose streamIds is a full permutation of
        the channel's current streams reorders successfully."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 1, "name": "ESPN", "streams": [5001, 5002, 5003]}],
            "count": 1, "next": None,
        }
        mock_client.get_streams_by_ids.return_value = [
            {"id": 5001, "name": "s1"}, {"id": 5002, "name": "s2"},
            {"id": 5003, "name": "s3"},
        ]
        mock_client.get_channel.return_value = {
            "id": 1, "name": "ESPN", "streams": [5001, 5002, 5003],
        }
        mock_client.update_channel.return_value = {"id": 1}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/bulk-commit", json={
                "operations": [
                    {"type": "reorderChannelStreams", "channelId": 1,
                     "streamIds": [5003, 5001, 5002]},
                ],
            })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["operationsApplied"] == 1
        assert data["operationsFailed"] == 0
        # The reordered (full permutation) set is written through.
        mock_client.update_channel.assert_awaited_once_with(
            1, {"streams": [5003, 5001, 5002]}
        )

    @pytest.mark.asyncio
    async def test_reorder_partial_streamids_does_not_detach(self, async_client):
        """A reorderChannelStreams op with a PARTIAL streamIds list (omits a
        currently-attached stream) must NOT silently detach the omitted stream.

        bd-1wq7z.25: the op is reported as a per-op error and update_channel is
        never called with the lossy set, mirroring the single-channel reorder
        guard (bd-1wq7z.3)."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 1, "name": "ESPN", "streams": [5001, 5002]}],
            "count": 1, "next": None,
        }
        mock_client.get_streams_by_ids.return_value = [
            {"id": 5002, "name": "s2"},
        ]
        mock_client.get_channel.return_value = {
            "id": 1, "name": "ESPN", "streams": [5001, 5002],
        }

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/bulk-commit", json={
                "operations": [
                    # Omits 5001 -> would detach it under replace-semantics.
                    {"type": "reorderChannelStreams", "channelId": 1,
                     "streamIds": [5002]},
                ],
                "continueOnError": True,
            })

        assert response.status_code == 200
        data = response.json()
        # The lossy op is rejected: reported as an error, not applied.
        assert data["operationsApplied"] == 0
        assert data["operationsFailed"] == 1
        assert len(data["errors"]) == 1
        # Critically: update_channel was NEVER called with the lossy set
        # (no silent detach of stream 5001).
        for call in mock_client.update_channel.await_args_list:
            written = call.args[1] if len(call.args) > 1 else call.kwargs.get("data", {})
            assert written.get("streams") != [5002]
        # Strongest assertion: no replace happened at all for this channel.
        mock_client.update_channel.assert_not_awaited()


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


class TestBulkMergeSanitization:
    """Tests for POST /api/channels/bulk-merge response sanitization.

    CodeQL py/stack-trace-exposure (#1413): per-group failures MUST not echo
    str(e) — Dispatcharr client errors can include backend URLs, internal
    IDs, and JSON fragments. The "error" field is replaced by the exception
    class name; full detail goes to the structured log under the request's
    trace id.
    """

    @pytest.mark.asyncio
    async def test_bulk_merge_failure_returns_exception_class_only(
        self, async_client
    ):
        """Failed merge group returns exception class, not str(e)."""
        secret = (
            "ConnectionError: 502 Bad Gateway from "
            "http://internal-dispatcharr.svc.cluster.local:9191/api/channels/42"
        )
        mock_client = AsyncMock()
        mock_client.get_channel.side_effect = RuntimeError(secret)

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post(
                "/api/channels/bulk-merge",
                json={
                    "merges": [
                        {
                            "target_channel_id": 1,
                            "source_channel_ids": [2, 3],
                        }
                    ]
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["failed"] == 1
        assert data["merged"] == 0
        result = data["results"][0]
        assert result["success"] is False
        # Sanitization contract: only class name leaks.
        assert result["error"] == "RuntimeError"
        assert "internal-dispatcharr" not in result["error"]
        assert "Bad Gateway" not in result["error"]


class TestMergeChannelsStaleSourceIds:
    """Tests for POST /api/channels/merge stale-source-ID handling.

    bd-ct9wl: when the UI submits a merge request with a stale source ID
    (e.g., a ghost row that survived a previous merge), the upstream
    get_channel returns 404. Surface 422 with a refresh hint instead of
    falling through to a generic 500.
    """

    @pytest.mark.asyncio
    async def test_returns_422_when_source_id_no_longer_exists(self, async_client):
        """Stale source ID returns 422 with refresh hint, not 500."""
        mock_client = AsyncMock()
        # First source is fine; second is gone (404 from Dispatcharr).
        not_found_response = MagicMock(spec=httpx.Response)
        not_found_response.status_code = 404
        not_found_response.text = "Not found"
        not_found_request = MagicMock(spec=httpx.Request)
        mock_client.get_channel.side_effect = [
            {"id": 100, "name": "Live A", "streams": [10]},
            httpx.HTTPStatusError(
                "404 Not Found",
                request=not_found_request,
                response=not_found_response,
            ),
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post(
                "/api/channels/merge",
                json={
                    "source_channel_ids": [100, 999],
                    "target_name": "Merged",
                },
            )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "999" in detail
        assert "refresh" in detail.lower()
        # Did not proceed to create the merged channel.
        mock_client.create_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_bad_target_group_surfaces_400_not_500(self, async_client):
        """A Dispatcharr 4xx while creating the merged channel (e.g. bad target
        group id) maps to 400 with detail, not an opaque 500 (bd-lq38l.4)."""
        mock_client = AsyncMock()
        mock_client.get_channel.side_effect = [
            {"id": 100, "name": "Live A", "streams": [10]},
            {"id": 200, "name": "Live B", "streams": [20]},
        ]
        request = httpx.Request("POST", "http://disp/api/channels/channels/")
        upstream = httpx.Response(
            400, request=request,
            text='{"channel_group_id": ["Invalid pk \\"999\\" - object does not exist."]}',
        )
        mock_client.create_channel.side_effect = httpx.HTTPStatusError(
            "400 Client Error", request=request, response=upstream
        )

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post(
                "/api/channels/merge",
                json={
                    "source_channel_ids": [100, 200],
                    "target_name": "Merged",
                    "target_channel_group_id": 999,
                },
            )

        assert response.status_code == 400
        assert "does not exist" in response.json()["detail"]


class TestBulkMergeChannelsStaleSourceIds:
    """Tests for POST /api/channels/bulk-merge stale-source-ID handling.

    bd-ozhkf (follow-up to bd-ct9wl): the bulk path had the same footgun as
    the single-channel merge endpoint — bare except swallowed get_channel 404s
    and then called delete_channel anyway, producing DELETE 404 log noise.
    Pre-validation now runs before the delete loop and returns 422 with a
    refresh hint when any source ID no longer exists.
    """

    @pytest.mark.asyncio
    async def test_returns_422_when_source_id_no_longer_exists(self, async_client):
        """Stale source ID in bulk merge returns 422 with refresh hint, not 200 + failed count."""
        mock_client = AsyncMock()
        # target fetch succeeds; first source fine; second source is gone (404).
        not_found_response = MagicMock(spec=httpx.Response)
        not_found_response.status_code = 404
        not_found_response.text = "Not found"
        not_found_request = MagicMock(spec=httpx.Request)
        mock_client.get_channel.side_effect = [
            {"id": 1, "name": "Target", "streams": [10]},   # target
            {"id": 2, "name": "Source A", "streams": [20]}, # source 2 OK
            httpx.HTTPStatusError(                           # source 999 gone
                "404 Not Found",
                request=not_found_request,
                response=not_found_response,
            ),
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post(
                "/api/channels/bulk-merge",
                json={
                    "merges": [
                        {
                            "target_channel_id": 1,
                            "source_channel_ids": [2, 999],
                        }
                    ]
                },
            )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "999" in detail
        assert "refresh" in detail.lower()
        # Did not proceed to delete source channels.
        mock_client.delete_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_detail_string_matches_single_channel_endpoint(self, async_client):
        """Bulk-merge 422 detail is identical in structure to merge_channels (operator-facing copy consistency)."""
        mock_client = AsyncMock()
        not_found_response = MagicMock(spec=httpx.Response)
        not_found_response.status_code = 404
        not_found_response.text = "Not found"
        not_found_request = MagicMock(spec=httpx.Request)
        stale_id = 888
        mock_client.get_channel.side_effect = [
            {"id": 1, "name": "Target", "streams": []},
            httpx.HTTPStatusError(
                "404 Not Found",
                request=not_found_request,
                response=not_found_response,
            ),
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post(
                "/api/channels/bulk-merge",
                json={
                    "merges": [
                        {
                            "target_channel_id": 1,
                            "source_channel_ids": [stale_id],
                        }
                    ]
                },
            )

        assert response.status_code == 422
        detail = response.json()["detail"]
        # Must contain the stale ID and the refresh hint phrase used by merge_channels.
        assert str(stale_id) in detail
        assert "no longer exist" in detail
        assert "refresh the channels list and try again" in detail

    @pytest.mark.asyncio
    async def test_per_item_upstream_4xx_detail_surfaced(self, async_client):
        """When a per-merge item fails with an upstream 4xx (e.g. bad stream id on
        the target update), the actionable upstream detail is surfaced in the
        per-item error instead of the bare exception type name (bd-lq38l.4)."""
        mock_client = AsyncMock()
        mock_client.get_channel.side_effect = [
            {"id": 1, "name": "Target", "streams": [10]},  # target
            {"id": 2, "name": "Source A", "streams": [20]},  # source
        ]
        request = httpx.Request("PATCH", "http://disp/api/channels/channels/1/")
        upstream = httpx.Response(
            400, request=request,
            text='{"streams": ["Invalid pk \\"20\\" - object does not exist."]}',
        )
        mock_client.update_channel.side_effect = httpx.HTTPStatusError(
            "400 Client Error", request=request, response=upstream
        )

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post(
                "/api/channels/bulk-merge",
                json={
                    "merges": [
                        {"target_channel_id": 1, "source_channel_ids": [2]},
                    ]
                },
            )

        # Endpoint contract is partial-success: HTTP 200 with per-item results.
        assert response.status_code == 200
        body = response.json()
        assert body["failed"] == 1
        item = body["results"][0]
        assert item["success"] is False
        # The upstream detail (not "HTTPStatusError") is surfaced.
        assert "does not exist" in item["error"]

    @pytest.mark.asyncio
    async def test_returns_422_when_target_id_no_longer_exists(self, async_client):
        """Stale TARGET ID in bulk merge returns 422 with refresh hint, matching the
        source-ID path — not a generic per-item failure (bd-4xxax)."""
        mock_client = AsyncMock()
        not_found_response = MagicMock(spec=httpx.Response)
        not_found_response.status_code = 404
        not_found_response.text = "Not found"
        not_found_request = MagicMock(spec=httpx.Request)
        stale_target = 777
        mock_client.get_channel.side_effect = [
            httpx.HTTPStatusError(  # target gone
                "404 Not Found",
                request=not_found_request,
                response=not_found_response,
            ),
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post(
                "/api/channels/bulk-merge",
                json={
                    "merges": [
                        {
                            "target_channel_id": stale_target,
                            "source_channel_ids": [2],
                        }
                    ]
                },
            )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert str(stale_target) in detail
        assert "no longer exists" in detail
        assert "refresh the channels list and try again" in detail
        # Did not proceed to mutate or delete anything.
        mock_client.update_channel.assert_not_called()
        mock_client.delete_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_404_source_error_reraises_not_swallowed_as_422(self, async_client):
        """A non-404 HTTPStatusError fetching a SOURCE (e.g. 410 Gone) must propagate
        to the per-item catch-all, NOT be misclassified as a stale-ID 422 (bd-4xxax)."""
        mock_client = AsyncMock()
        gone_response = MagicMock(spec=httpx.Response)
        gone_response.status_code = 410
        gone_response.text = "Gone"
        gone_request = MagicMock(spec=httpx.Request)
        mock_client.get_channel.side_effect = [
            {"id": 1, "name": "Target", "streams": [10]},  # target OK
            httpx.HTTPStatusError(  # source 410 Gone — not a 404
                "410 Gone",
                request=gone_request,
                response=gone_response,
            ),
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post(
                "/api/channels/bulk-merge",
                json={
                    "merges": [
                        {"target_channel_id": 1, "source_channel_ids": [2]},
                    ]
                },
            )

        # Partial-success contract: HTTP 200 with the item marked failed.
        assert response.status_code == 200
        body = response.json()
        assert body["failed"] == 1
        item = body["results"][0]
        assert item["success"] is False
        # Crucially NOT a 422 stale-ID rejection.
        assert response.status_code != 422
        # Never reached the delete loop.
        mock_client.delete_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_404_target_error_reraises_not_swallowed_as_422(self, async_client):
        """A non-404 HTTPStatusError fetching the TARGET (e.g. 500) must propagate to
        the per-item catch-all, NOT be misclassified as a stale-ID 422 (bd-4xxax)."""
        mock_client = AsyncMock()
        server_error_response = MagicMock(spec=httpx.Response)
        server_error_response.status_code = 500
        server_error_response.text = "Server error"
        server_error_request = MagicMock(spec=httpx.Request)
        mock_client.get_channel.side_effect = [
            httpx.HTTPStatusError(  # target 500 — not a 404
                "500 Server Error",
                request=server_error_request,
                response=server_error_response,
            ),
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post(
                "/api/channels/bulk-merge",
                json={
                    "merges": [
                        {"target_channel_id": 1, "source_channel_ids": [2]},
                    ]
                },
            )

        # Partial-success contract: HTTP 200, item failed, NOT a 422.
        assert response.status_code == 200
        body = response.json()
        assert body["failed"] == 1
        item = body["results"][0]
        assert item["success"] is False
        mock_client.delete_channel.assert_not_called()
