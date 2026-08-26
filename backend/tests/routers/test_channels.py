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

    @pytest.mark.asyncio
    @pytest.mark.parametrize("page", [0, -1])
    async def test_invalid_page_returns_422_not_500(self, async_client, page):
        """page < 1 is rejected by validation (422), never passed upstream to
        become a 500 (bead 1a5mf)."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0}

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels", params={"page": page})

        assert response.status_code == 422
        # The upstream client must never be invoked with invalid pagination.
        mock_client.get_channels.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("page_size", [0, -5, 10001])
    async def test_invalid_page_size_returns_422_not_500(self, async_client, page_size):
        """page_size out of [1, 10000] is rejected by validation (422)."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0}

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels", params={"page_size": page_size}
            )

        assert response.status_code == 422
        mock_client.get_channels.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_pagination_still_works(self, async_client):
        """A valid page/page_size (including the frontend's large page_size=5000)
        passes through unchanged."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0}

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels", params={"page": 1, "page_size": 5000}
            )

        assert response.status_code == 200
        mock_client.get_channels.assert_called_once_with(
            page=1, page_size=5000, search=None, channel_group=None,
        )


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
        # Journal entry still logged on success. Written through the shared
        # `write_journal_rows`, which uses the BATCH api and checks its return
        # value, rather than the fire-and-forget `log_entry` this endpoint used
        # to call (bead enhancedchannelmanager-ftidn).
        mock_journal.log_entries.assert_called_once()
        rows = mock_journal.log_entries.call_args.args[0]
        assert [row["action_type"] for row in rows] == ["stream_reorder"]

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
    @pytest.mark.parametrize("page", [0, -1])
    async def test_invalid_page_returns_422_not_500(self, async_client, page):
        """page < 1 is rejected by validation (422), never passed upstream to
        become a 500 (bead enhancedchannelmanager-g4z2h, systemic sibling of
        1a5mf)."""
        mock_client = AsyncMock()
        mock_client.get_logos.return_value = {"results": [], "count": 0}

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos", params={"page": page}
            )

        assert response.status_code == 422
        mock_client.get_logos.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("page_size", [0, -5, 10001])
    async def test_invalid_page_size_returns_422_not_500(self, async_client, page_size):
        """page_size out of [1, 10000] is rejected by validation (422)."""
        mock_client = AsyncMock()
        mock_client.get_logos.return_value = {"results": [], "count": 0}

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos", params={"page_size": page_size}
            )

        assert response.status_code == 422
        mock_client.get_logos.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_pagination_still_works(self, async_client):
        """A valid page/page_size (including the frontend's large
        page_size=10000, getAllLogos()) passes through unchanged."""
        mock_client = AsyncMock()
        mock_client.get_logos.return_value = {"results": [], "count": 0}

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos", params={"page": 1, "page_size": 10000}
            )

        assert response.status_code == 200
        mock_client.get_logos.assert_called_once_with(
            page=1, page_size=10000, search=None,
        )

    @pytest.mark.asyncio
    async def test_emits_per_request_info_diagnostic(self, async_client, caplog):
        """Emits the bd-nh50y operator-grepable INFO line per request.

        Contract: every successful GET /logos call must emit a single INFO
        line tagged "[CHANNELS-LOGO] GET /logos" that names page, page_size,
        search, the result count, elapsed ms, and the next-page flag. This
        is the line operators grep when triaging "logos not loading" reports
        — it lets us correlate the backend response to the frontend log
        sequence emitted by getAllLogos() in services/api.ts.

        A non-empty ``search`` now routes through the local aggregate-and-
        filter path (bead 09x38.13): Dispatcharr's LogoViewSet.get_queryset
        never reads a ``search`` param — only ``name``/``used``/``ids``
        (confirmed by reading apps/channels/api_views.py in the live
        dispatcharr container). The previous version of this test mocked
        ``get_logos`` to return already-filtered results, which papered
        over the fact that upstream ``search`` was actually a no-op — it
        never exercised real filtering. This version uses a raw, unfiltered
        fixture and asserts the ECM-side filter narrows it for real.
        """
        import logging
        mock_client = AsyncMock()
        mock_client.get_all_logos_raw.return_value = [
            {"id": 1, "name": "ESPN", "channel_count": 1},
            {"id": 2, "name": "FOX", "channel_count": 1},
        ]

        with patch("routers.channels.get_client", return_value=mock_client):
            with caplog.at_level(logging.INFO, logger="routers.channels"):
                response = await async_client.get(
                    "/api/channels/logos?page=1&page_size=250&search=ESPN",
                )

        assert response.status_code == 200
        data = response.json()
        assert [l["name"] for l in data["results"]] == ["ESPN"]
        mock_client.get_logos.assert_not_called()
        info_lines = [
            r.getMessage() for r in caplog.records
            if r.levelno == logging.INFO and "[CHANNELS-LOGO]" in r.getMessage()
        ]
        assert len(info_lines) == 1, f"Expected exactly one INFO diagnostic, got: {info_lines}"
        line = info_lines[0]
        # Required fields — operators grep for these
        assert "GET /logos" in line
        assert "page=1" in line
        assert "page_size=250" in line
        assert "search=ESPN" in line
        assert "returned 1 logos" in line
        assert "next=false" in line
        # Elapsed-ms field is present and formatted as a number followed by "ms"
        import re
        assert re.search(r"in \d+\.\dms", line), f"Missing elapsed-ms field in: {line}"


class TestGetLogosSortAndFilter:
    """Tests for GET /api/channels/logos sort_by / sort_order / unused_only
    (bead enhancedchannelmanager-09x38.13).

    Dispatcharr's LogoViewSet has no ordering support at all (confirmed by
    reading apps/channels/api_views.py in the live dispatcharr container:
    get_queryset() always ends with ``.order_by('name')``, and the only
    REST_FRAMEWORK filter backend configured is DjangoFilterBackend with no
    filterset_fields declared on the view — there is no ``ordering`` param
    to forward). So whenever sort_by, unused_only, or search is requested,
    ECM fetches the complete logo list from Dispatcharr in one call via the
    ``no_pagination=true`` escape hatch Dispatcharr's LogoPagination already
    supports, then sorts/filters/paginates locally in Python before
    returning the same paginated envelope shape. Requests using none of
    these three params keep taking the original zero-overhead passthrough
    path (locked by test_uses_passthrough_when_no_sort_filter_or_search).
    """

    @staticmethod
    def _raw_logos():
        return [
            {"id": 1, "name": "ESPN", "channel_count": 3},
            {"id": 2, "name": "abc Sports", "channel_count": 0},
            {"id": 3, "name": "Zed TV", "channel_count": 1},
            {"id": 4, "name": "Fox News", "channel_count": 0},
        ]

    @pytest.mark.asyncio
    async def test_sort_by_name_ascending(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_all_logos_raw.return_value = self._raw_logos()

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos", params={"sort_by": "name", "sort_order": "asc"},
            )

        assert response.status_code == 200
        names = [l["name"] for l in response.json()["results"]]
        assert names == ["abc Sports", "ESPN", "Fox News", "Zed TV"]

    @pytest.mark.asyncio
    async def test_sort_by_name_descending(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_all_logos_raw.return_value = self._raw_logos()

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos", params={"sort_by": "name", "sort_order": "desc"},
            )

        assert response.status_code == 200
        names = [l["name"] for l in response.json()["results"]]
        assert names == ["Zed TV", "Fox News", "ESPN", "abc Sports"]

    @pytest.mark.asyncio
    async def test_sort_by_channel_count_ascending_surfaces_unused_first(self, async_client):
        """The live-verification scenario: sorting used-by count ascending
        must surface unused (channel_count=0) logos first."""
        mock_client = AsyncMock()
        mock_client.get_all_logos_raw.return_value = self._raw_logos()

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos",
                params={"sort_by": "channel_count", "sort_order": "asc"},
            )

        assert response.status_code == 200
        counts = [l["channel_count"] for l in response.json()["results"]]
        assert counts == sorted(counts)
        assert counts[0] == 0

    @pytest.mark.asyncio
    async def test_sort_by_channel_count_descending(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_all_logos_raw.return_value = self._raw_logos()

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos",
                params={"sort_by": "channel_count", "sort_order": "desc"},
            )

        assert response.status_code == 200
        counts = [l["channel_count"] for l in response.json()["results"]]
        assert counts == sorted(counts, reverse=True)

    @pytest.mark.asyncio
    async def test_unused_only_filters_to_zero_channel_count(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_all_logos_raw.return_value = self._raw_logos()

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos", params={"unused_only": "true"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert all(l["channel_count"] == 0 for l in data["results"])

    @pytest.mark.asyncio
    async def test_unused_only_composes_with_search(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_all_logos_raw.return_value = self._raw_logos()

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos",
                params={"unused_only": "true", "search": "fox"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["results"][0]["name"] == "Fox News"

    @pytest.mark.asyncio
    async def test_sort_and_filter_paginate_correctly(self, async_client):
        """Pagination is computed AFTER sort/filter, over the full dataset —
        not per-Dispatcharr-page — so results/count/next are truthful."""
        mock_client = AsyncMock()
        mock_client.get_all_logos_raw.return_value = self._raw_logos()

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos",
                params={"sort_by": "name", "sort_order": "asc", "page": 2, "page_size": 2},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 4
        assert [l["name"] for l in data["results"]] == ["Fox News", "Zed TV"]
        assert data["next"] is None
        assert data["previous"] is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sort_by", ["bogus", "url", ""])
    async def test_invalid_sort_by_returns_422(self, async_client, sort_by):
        mock_client = AsyncMock()
        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos", params={"sort_by": sort_by},
            )
        assert response.status_code == 422
        mock_client.get_all_logos_raw.assert_not_called()
        mock_client.get_logos.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_sort_order_returns_422(self, async_client):
        mock_client = AsyncMock()
        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos",
                params={"sort_by": "name", "sort_order": "sideways"},
            )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_uses_passthrough_when_no_sort_filter_or_search(self, async_client):
        """Locks backward compatibility: existing callers (LogoModal picker,
        AutoSyncSettingsModal, GuideTab, getAllLogos()) that never pass
        sort_by/unused_only/search must keep hitting the cheap single-page
        Dispatcharr passthrough, not the full-dataset aggregate path."""
        mock_client = AsyncMock()
        mock_client.get_logos.return_value = {"results": [], "count": 0, "next": None}

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos", params={"page": 2, "page_size": 50},
            )

        assert response.status_code == 200
        mock_client.get_logos.assert_called_once_with(page=2, page_size=50, search=None)
        mock_client.get_all_logos_raw.assert_not_called()


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


async def _commit_and_wait(async_client, body, *, max_polls=50):
    """POST a bulk commit and poll until terminal, returning the result envelope.

    Mirrors what the frontend client does (POST → 202 → poll). The shape
    returned here matches the pre-bd-ggxks synchronous response body, so the
    existing assertions on per-operation execution still apply unchanged.
    """
    import asyncio as _asyncio

    response = await async_client.post("/api/channels/bulk-commit", json=body)
    if response.status_code == 200:
        # validateOnly path is still synchronous.
        return response, response.json()
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    for _ in range(max_polls):
        await _asyncio.sleep(0)
        poll = await async_client.get(f"/api/channels/bulk-commit/{job_id}")
        assert poll.status_code == 200, poll.text
        payload = poll.json()
        if payload["status"] == "completed":
            return response, payload["result"]
        if payload["status"] == "failed":
            return response, payload
    raise AssertionError(f"bulk-commit job {job_id} did not terminate in {max_polls} polls")


class TestBulkCommit:
    """Tests for POST /api/channels/bulk-commit (202+poll, bd-ggxks)."""

    @pytest.fixture(autouse=True)
    def _clear_jobs(self):
        # Each test starts with an empty job dict so state never leaks across
        # tests (the dict is module-level by design so the in-memory job
        # lookup survives between requests within a single process).
        from routers import channels as router_module

        router_module._BULK_COMMIT_JOBS.clear()
        yield
        router_module._BULK_COMMIT_JOBS.clear()

    @pytest.mark.asyncio
    async def test_empty_operations(self, async_client):
        """Processes empty operations list."""
        mock_client = AsyncMock()

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(async_client, {"operations": []})

        assert response.status_code == 202
        assert data["success"] is True
        assert data["operationsApplied"] == 0

    @pytest.mark.asyncio
    async def test_create_then_add_stream_resolves_temp_id_in_same_request(
        self, async_client
    ):
        """A later operation in one bulk request can target a newly created channel."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [], "count": 0, "next": None,
        }
        mock_client.create_channel.return_value = {
            "id": 101, "name": "New channel", "channel_number": 500, "streams": [],
        }
        mock_client.get_channel.return_value = {
            "id": 101, "name": "New channel", "channel_number": 500, "streams": [],
        }
        mock_client.update_channel.return_value = {
            "id": 101, "name": "New channel", "streams": [77],
        }

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(async_client, {
                "operations": [
                    {
                        "type": "createChannel",
                        "tempId": -1,
                        "name": "New channel",
                        "channelNumber": 500,
                    },
                    {
                        "type": "addStreamToChannel",
                        "channelId": -1,
                        "streamId": 77,
                    },
                ],
            })

        assert response.status_code == 202
        assert data["success"] is True
        assert data["operationsApplied"] == 2
        assert data["operationsFailed"] == 0
        assert data["tempIdMap"] == {"-1": 101}
        mock_client.get_channel.assert_awaited_once_with(101)
        mock_client.update_channel.assert_awaited_once_with(101, {"streams": [77]})

    @pytest.mark.asyncio
    async def test_validate_only(self, async_client):
        """Returns validation results synchronously without executing."""
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

        # Validate-only stays sync — single round-trip, no job_id envelope.
        assert response.status_code == 200
        data = response.json()
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
            response, data = await _commit_and_wait(async_client, {
                "operations": [
                    {"type": "deleteChannel", "channelId": 1},
                ],
            })

        assert response.status_code == 202
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
            response, data = await _commit_and_wait(async_client, {
                "operations": [
                    {"type": "reorderChannelStreams", "channelId": 1,
                     "streamIds": [5003, 5001, 5002]},
                ],
            })

        assert response.status_code == 202
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
            response, data = await _commit_and_wait(async_client, {
                "operations": [
                    # Omits 5001 -> would detach it under replace-semantics.
                    {"type": "reorderChannelStreams", "channelId": 1,
                     "streamIds": [5002]},
                ],
                "continueOnError": True,
            })

        assert response.status_code == 202
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


class TestBulkCommitLogoIndex:
    """Tests for the per-run logo index in _run_bulk_commit (bd-raehx).

    Previously every createChannel op with logoUrl + no logoId called
    find_logo_by_url(), which re-paginated the ENTIRE Dispatcharr logo
    catalog. For an N-channel batch against a ~25-page catalog that was
    O(N * pages) GET calls — the 30s budget was exhausted around op 10-15.

    The fix builds a url->logo index ONCE per run (lazily, on first need),
    so the catalog is paginated a single time and reused for every op.
    """

    @pytest.fixture(autouse=True)
    def _clear_jobs(self):
        from routers import channels as router_module

        router_module._BULK_COMMIT_JOBS.clear()
        yield
        router_module._BULK_COMMIT_JOBS.clear()

    @staticmethod
    def _paginated_logos(pages):
        """Build a get_logos side_effect that returns ``pages`` catalog pages.

        Each element of ``pages`` is the ``results`` list for that page; the
        ``next`` field is set on every page but the last, mirroring how
        find_logo_by_url follows pagination.
        """
        responses = []
        for i, results in enumerate(pages):
            is_last = i == len(pages) - 1
            responses.append({
                "results": results,
                "count": sum(len(p) for p in pages),
                "next": None if is_last else f"page-{i + 2}",
            })

        async def _side_effect(page=1, page_size=500, search=None):
            # 1-indexed pages, matching the client.
            return responses[page - 1]

        return _side_effect

    @pytest.mark.asyncio
    async def test_catalog_paginated_once_for_n_channels(self, async_client):
        """N createChannel ops (all logoUrl, no logoId) paginate the catalog
        ONCE — get_logos is called once per catalog PAGE, not N * pages."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        # 3-page catalog; none of the requested URLs exist in it.
        mock_client.get_logos.side_effect = self._paginated_logos([
            [{"id": 1, "url": "http://logos/existing-a.png"}],
            [{"id": 2, "url": "http://logos/existing-b.png"}],
            [{"id": 3, "url": "http://logos/existing-c.png"}],
        ])
        created_logo_ids = iter(range(1000, 2000))
        mock_client.create_logo.side_effect = (
            lambda data: {"id": next(created_logo_ids), "url": data["url"]}
        )
        created_channel_ids = iter(range(1, 1000))
        mock_client.create_channel.side_effect = (
            lambda data: {"id": next(created_channel_ids), "name": data["name"]}
        )

        ops = [
            {"type": "createChannel", "tempId": -(i + 1), "name": f"Ch{i}",
             "logoUrl": f"http://logos/new-{i}.png"}
            for i in range(5)
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        assert response.status_code == 202
        assert data["operationsApplied"] == 5
        # Catalog has 3 pages -> exactly 3 get_logos calls for the WHOLE batch,
        # not 3 * 5 = 15 (the old per-channel re-pagination).
        assert mock_client.get_logos.await_count == 3
        # Each new URL was created exactly once.
        assert mock_client.create_logo.await_count == 5

    @pytest.mark.asyncio
    async def test_shared_logo_url_created_once(self, async_client):
        """Two createChannel ops sharing the same logoUrl create the logo only
        once — the second op reuses the index entry inserted by the first."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        # Empty catalog (single page) — URL is not pre-existing.
        mock_client.get_logos.side_effect = self._paginated_logos([[]])
        mock_client.create_logo.return_value = {"id": 7777, "url": "http://logos/shared.png"}
        created_channel_ids = iter(range(1, 1000))
        mock_client.create_channel.side_effect = (
            lambda data: {"id": next(created_channel_ids), "name": data["name"]}
        )

        ops = [
            {"type": "createChannel", "tempId": -1, "name": "A",
             "logoUrl": "http://logos/shared.png"},
            {"type": "createChannel", "tempId": -2, "name": "B",
             "logoUrl": "http://logos/shared.png"},
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        assert response.status_code == 202
        assert data["operationsApplied"] == 2
        # Catalog paginated once, logo created once despite two channels.
        assert mock_client.get_logos.await_count == 1
        assert mock_client.create_logo.await_count == 1
        # Both channels were created with the same shared logo_id.
        logo_ids = [
            call.args[0].get("logo_id")
            for call in mock_client.create_channel.await_args_list
        ]
        assert logo_ids == [7777, 7777]

    @pytest.mark.asyncio
    async def test_existing_logo_reused_without_create(self, async_client):
        """A createChannel op whose logoUrl already exists in the catalog uses
        the existing logo id and does NOT call create_logo."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_logos.side_effect = self._paginated_logos([
            [{"id": 42, "url": "http://logos/espn.png"}],
        ])
        mock_client.create_channel.return_value = {"id": 1, "name": "ESPN"}

        ops = [
            {"type": "createChannel", "tempId": -1, "name": "ESPN",
             "logoUrl": "http://logos/espn.png"},
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        assert response.status_code == 202
        assert data["operationsApplied"] == 1
        assert mock_client.get_logos.await_count == 1
        mock_client.create_logo.assert_not_awaited()
        assert mock_client.create_channel.await_args.args[0].get("logo_id") == 42

    @pytest.mark.asyncio
    async def test_validate_only_never_fetches_logos(self, async_client):
        """validateOnly batches build no logo index — get_logos is never
        called (lazy build only fires on a real createChannel logo lookup)."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}

        ops = [
            {"type": "createChannel", "tempId": -1, "name": "A",
             "logoUrl": "http://logos/a.png"},
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/bulk-commit", json={
                "operations": ops, "validateOnly": True,
            })

        assert response.status_code == 200
        mock_client.get_logos.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_only_without_logos_never_fetches_logos(self, async_client):
        """createChannel ops with no logoUrl never trigger a catalog fetch."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        created_channel_ids = iter(range(1, 1000))
        mock_client.create_channel.side_effect = (
            lambda data: {"id": next(created_channel_ids), "name": data["name"]}
        )

        ops = [
            {"type": "createChannel", "tempId": -(i + 1), "name": f"Ch{i}"}
            for i in range(3)
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        assert response.status_code == 202
        assert data["operationsApplied"] == 3
        mock_client.get_logos.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_logo_failure_still_creates_channel(self, async_client):
        """If logo resolution raises, the channel is still created without a
        logo (preserves the existing try/except fallthrough behavior)."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        # Pagination itself fails -> index build raises -> fall through, no logo.
        mock_client.get_logos.side_effect = Exception("Dispatcharr 500")
        mock_client.create_channel.return_value = {"id": 1, "name": "A"}

        ops = [
            {"type": "createChannel", "tempId": -1, "name": "A",
             "logoUrl": "http://logos/a.png"},
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        assert response.status_code == 202
        assert data["operationsApplied"] == 1
        # Channel created with NO logo_id key (logo step swallowed the error).
        assert "logo_id" not in mock_client.create_channel.await_args.args[0]


class TestBulkCommitStagedGroupIds:
    """A negative (staging) channel_group_id must never reach Dispatcharr.

    Bead ``enhancedchannelmanager-udq1j``. Drill run 2026-08-09-run18 staged 12
    channels in one Edit Mode session, one of them into a group that was still
    PENDING in the same batch. The frontend put that group's negative staging
    id on the wire verbatim and Dispatcharr answered::

        400 {"channel_group_id": ["Invalid pk \\"-1000\\" - object does not
             exist."]}

    The frontend now resolves staged groups by name before posting. These tests
    are the server-side backstop for a client that does not — an older build, a
    scripted caller, or a future regression — so the failure is named in ECM's
    own error instead of relayed as an opaque upstream 400, and above all so it
    is never forwarded as if it were a real group id.
    """

    @pytest.fixture(autouse=True)
    def _clear_jobs(self):
        from routers import channels as router_module

        router_module._BULK_COMMIT_JOBS.clear()
        yield
        router_module._BULK_COMMIT_JOBS.clear()

    @staticmethod
    def _mock_client():
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.create_channel.return_value = {"id": 101, "name": "created"}
        mock_client.update_channel.return_value = {"id": 7}
        return mock_client

    @pytest.mark.asyncio
    async def test_create_channel_with_staging_group_id_is_refused(self, async_client):
        """createChannel carrying a negative groupId fails without calling out."""
        mock_client = self._mock_client()

        ops = [{
            "type": "createChannel", "tempId": -1, "name": "TX | Dallas | PBS KERA",
            "groupId": -1000,
        }]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        assert response.status_code == 202
        assert data["operationsApplied"] == 0
        assert data["operationsFailed"] == 1
        assert "-1000" in data["errors"][0]["error"]
        assert "groupsToCreate" in data["errors"][0]["error"]
        # The point of the guard: nothing was posted upstream.
        mock_client.create_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_channel_with_staging_group_id_is_refused(self, async_client):
        """A channel moved into a still-pending group is refused the same way."""
        mock_client = self._mock_client()

        ops = [{
            "type": "updateChannel", "channelId": 7,
            "data": {"channel_group_id": -1000},
        }]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        assert data["operationsFailed"] == 1
        assert "-1000" in data["errors"][0]["error"]
        mock_client.update_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_new_group_referenced_by_name_still_works(self, async_client):
        """The supported path: groupsToCreate + newGroupName resolves to a real id."""
        mock_client = self._mock_client()
        mock_client.create_channel_group.return_value = {"id": 376, "name": "Drill Locals"}

        body = {
            "operations": [{
                "type": "createChannel", "tempId": -1, "name": "TX | Dallas | PBS KERA",
                "newGroupName": "Drill Locals",
            }],
            "groupsToCreate": [{"name": "Drill Locals"}],
            "continueOnError": True,
        }

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(async_client, body)

        assert data["operationsFailed"] == 0
        assert data["groupIdMap"] == {"Drill Locals": 376}
        payload = mock_client.create_channel.await_args.args[0]
        assert payload["channel_group_id"] == 376

    @pytest.mark.asyncio
    async def test_real_group_id_is_untouched(self, async_client):
        """A positive group id is forwarded exactly as sent."""
        mock_client = self._mock_client()

        ops = [{
            "type": "createChannel", "tempId": -1, "name": "A", "groupId": 376,
        }]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        assert data["operationsFailed"] == 0
        assert mock_client.create_channel.await_args.args[0]["channel_group_id"] == 376


class TestBulkCommitPartialSuccess:
    """Tests for partial-success result semantics in _run_bulk_commit (bd-5xciq).

    A continueOnError batch where some ops apply and some fail must surface a
    distinct PARTIAL outcome — applied/failed counts, the errors list, and a
    ``partial`` flag — so the frontend can render it as 'X applied, Y failed'
    instead of a flat failure that prompts duplicate-creating retries.
    """

    @pytest.fixture(autouse=True)
    def _clear_jobs(self):
        from routers import channels as router_module

        router_module._BULK_COMMIT_JOBS.clear()
        yield
        router_module._BULK_COMMIT_JOBS.clear()

    @pytest.mark.asyncio
    async def test_partial_success_envelope(self, async_client):
        """Mixed success/failure with continueOnError returns applied + failed
        counts, a populated errors list, the tempIdMap of what committed, and a
        ``partial`` flag — NOT a flat success=false-with-nothing-applied."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}

        # First create succeeds, second raises, third succeeds.
        results = iter([
            {"id": 101, "name": "A"},
            Exception("Dispatcharr rejected B"),
            {"id": 103, "name": "C"},
        ])

        def _create(data):
            nxt = next(results)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        mock_client.create_channel.side_effect = _create

        ops = [
            {"type": "createChannel", "tempId": -1, "name": "A"},
            {"type": "createChannel", "tempId": -2, "name": "B"},
            {"type": "createChannel", "tempId": -3, "name": "C"},
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        assert response.status_code == 202
        assert data["operationsApplied"] == 2
        assert data["operationsFailed"] == 1
        assert len(data["errors"]) == 1
        # The two channels that committed are in the tempIdMap so the client
        # can reconcile what's already in Dispatcharr (no blind retry).
        assert data["tempIdMap"] == {"-1": 101, "-3": 103}
        # Distinct partial outcome, not a flat failure.
        assert data["partial"] is True

    @pytest.mark.asyncio
    async def test_full_success_not_partial(self, async_client):
        """An all-succeed batch reports success=True and partial=False."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        created = iter(range(1, 100))
        mock_client.create_channel.side_effect = (
            lambda data: {"id": next(created), "name": data["name"]}
        )

        ops = [
            {"type": "createChannel", "tempId": -1, "name": "A"},
            {"type": "createChannel", "tempId": -2, "name": "B"},
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        assert response.status_code == 202
        assert data["success"] is True
        assert data["operationsFailed"] == 0
        assert data["partial"] is False

    @pytest.mark.asyncio
    async def test_total_failure_not_partial(self, async_client):
        """A batch where every op fails reports partial=False (nothing applied),
        so the client treats it as a true failure, not a partial."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.create_channel.side_effect = Exception("Dispatcharr down")

        ops = [
            {"type": "createChannel", "tempId": -1, "name": "A"},
            {"type": "createChannel", "tempId": -2, "name": "B"},
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        assert response.status_code == 202
        assert data["operationsApplied"] == 0
        assert data["operationsFailed"] == 2
        assert data["success"] is False
        assert data["partial"] is False


class _DispatcharrNonNullGroupError(Exception):
    """What Dispatcharr 0.28.2 returns for ``channel_group_id: null``.

    Measured against the live drill instance on 2026-08-09::

        PATCH /api/channels/channels/1/ {"channel_group_id": null}
          -> 400 {"channel_group_id":["This field may not be null."]}
        PATCH /api/channels/channels/1/ {"channel_group_id": 378}
          -> 200  (the channel really moved)

    A bare ``AsyncMock`` accepts anything, which is how the first cut of the
    …-ayfn9 reparent passed every unit test and then 400'd on the very first live
    Delete Group. Any double standing in for ``update_channel`` in this module
    enforces the constraint the real API enforces.
    """


def _channel_patch_double(recorder: list | None = None):
    """An ``update_channel`` double that rejects a null ``channel_group_id``."""

    def _patch(channel_id: int, data: dict):
        if "channel_group_id" in data and data["channel_group_id"] is None:
            raise _DispatcharrNonNullGroupError(
                "Client error '400 Bad Request' for url "
                "'http://dispatcharr:9191/api/channels/channels/%s/': "
                '{"channel_group_id":["This field may not be null."]}' % channel_id
            )
        if recorder is not None:
            recorder.append(("patch", channel_id, data))
        return {"id": channel_id, **data}

    return _patch


# Dispatcharr's baseline group, confirmed present exactly once on the live drill
# instance (`id=1, name='Default Group'`, 378 groups total). The production code
# resolves it BY NAME, so these fixtures deliberately give it a non-obvious id:
# a test that passes only because the id happens to be 1 proves nothing.
_DEFAULT_GROUP = {"id": 42, "name": "Default Group"}


class TestBulkCommitDeleteChannelGroup:
    """deleteChannelGroup reparents before it deletes (bead …-ayfn9).

    Backup/restore drill run 2026-08-08-run17. ECM's Delete Group confirm dialog
    promises "This group contains 6 channels. The channels will be moved to
    'Ungrouped'." Edit Mode then issued a BARE delete, which Dispatcharr refuses::

        DELETE /api/channels/groups/377/ -> 400
        {"error":"Cannot delete group with associated channels"}

    Verified directly on the drill instance: DELETE on the 6-channel group 377
    returned 400, DELETE on the EMPTY group 378 returned 204. The delete can only
    ever succeed on an already-empty group, and the reparenting that was supposed
    to empty it never happened.

    THE SECOND ROUND (live re-drive of the same UI path, 2026-08-09). Reparenting
    to ``None`` cannot work either: a Dispatcharr channel row REQUIRES a group.
    "Ungrouped" is a state ECM can READ (the frontend buckets
    ``channel_group_id === null``) but never one it can WRITE. The move therefore
    targets Dispatcharr's own baseline group, resolved by NAME.
    """

    @pytest.fixture(autouse=True)
    def _clear_jobs(self):
        from routers import channels as router_module

        router_module._BULK_COMMIT_JOBS.clear()
        yield
        router_module._BULK_COMMIT_JOBS.clear()

    @pytest.mark.asyncio
    async def test_members_are_reparented_to_the_default_group_before_the_delete(
        self, async_client
    ):
        """Members move to a REAL group id, THEN the group is deleted."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 11, "name": "PBS 1", "channel_group_id": 377},
                {"id": 12, "name": "PBS 2", "channel_group_id": 377},
                {"id": 13, "name": "Elsewhere", "channel_group_id": 999},
            ],
            "count": 3,
            "next": None,
        }
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_channel_groups.return_value = [
            {"id": 377, "name": "Drill17 PBS West"},
            _DEFAULT_GROUP,
        ]

        calls: list = []
        mock_client.update_channel.side_effect = _channel_patch_double(calls)
        mock_client.delete_channel_group.side_effect = (
            lambda gid: calls.append(("delete", gid, None))
        )

        ops = [{"type": "deleteChannelGroup", "groupId": 377}]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(async_client, {"operations": ops})

        assert response.status_code == 202
        assert data["success"] is True
        assert data["operationsFailed"] == 0
        # Both members moved to the RESOLVED group id (42, not the literal 1 and
        # emphatically not None); the outsider was never touched.
        assert calls == [
            ("patch", 11, {"channel_group_id": 42}),
            ("patch", 12, {"channel_group_id": 42}),
            ("delete", 377, None),
        ]

    @pytest.mark.asyncio
    async def test_a_channel_moved_out_of_the_group_since_the_read_is_left_alone(
        self, async_client
    ):
        """The concurrent-move guard is in the SHARED helper, so it holds here too.

        The mirror of
        ``tests/routers/test_channel_groups.py::TestDeleteChannelGroupReparentsMembers``
        ``::test_a_channel_moved_out_of_the_group_since_the_read_is_left_alone``.
        The whole point of bead …-auocn is that these two delete paths must not
        drift, and a guard that only one of them has would be exactly that drift.
        """
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 11, "name": "PBS 1", "channel_group_id": 377},
                {"id": 12, "name": "PBS 2", "channel_group_id": 377},
            ],
            "count": 2,
            "next": None,
        }
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_channel_groups.return_value = [
            {"id": 377, "name": "Drill17 PBS West"},
            _DEFAULT_GROUP,
        ]
        # Another operator moved PBS 1 to group 500 after the list read.
        mock_client.get_channel.side_effect = lambda channel_id: {
            11: {"id": 11, "name": "PBS 1", "channel_group_id": 500},
            12: {"id": 12, "name": "PBS 2", "channel_group_id": 377},
        }[channel_id]

        calls: list = []
        mock_client.update_channel.side_effect = _channel_patch_double(calls)
        mock_client.delete_channel_group.side_effect = (
            lambda gid: calls.append(("delete", gid, None))
        )

        ops = [{"type": "deleteChannelGroup", "groupId": 377}]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(async_client, {"operations": ops})

        assert response.status_code == 202
        assert data["operationsFailed"] == 0
        assert calls == [
            ("patch", 12, {"channel_group_id": 42}),
            ("delete", 377, None),
        ]

    @pytest.mark.asyncio
    async def test_the_target_group_is_resolved_by_name_not_by_id(self, async_client):
        """Nothing may depend on the baseline group's id being 1.

        It is 1 on a fresh 0.28.2 install and on the drill instance, but that is
        an observation about a default, not a contract.
        """
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 11, "name": "PBS 1", "channel_group_id": 377}],
            "count": 1,
            "next": None,
        }
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "Some Operator's Own Group"},
            {"id": 907, "name": "  default group  "},  # trimmed + case-insensitive
        ]
        mock_client.update_channel.side_effect = _channel_patch_double()

        ops = [{"type": "deleteChannelGroup", "groupId": 377}]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            _, data = await _commit_and_wait(async_client, {"operations": ops})

        assert data["success"] is True
        mock_client.update_channel.assert_awaited_once_with(11, {"channel_group_id": 907})

    @pytest.mark.asyncio
    async def test_a_null_channel_group_id_is_never_sent(self, async_client):
        """The regression guard: the double 400s on null exactly as the API does.

        Without this the previous cut of the fix passed four green unit tests and
        failed on the first live click.
        """
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 11, "name": "PBS 1", "channel_group_id": 377}],
            "count": 1,
            "next": None,
        }
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_channel_groups.return_value = [_DEFAULT_GROUP]
        mock_client.update_channel.side_effect = _channel_patch_double()

        ops = [{"type": "deleteChannelGroup", "groupId": 377}]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            _, data = await _commit_and_wait(async_client, {"operations": ops})

        assert data["success"] is True
        for call in mock_client.update_channel.await_args_list:
            assert call.args[1]["channel_group_id"] is not None

    @pytest.mark.asyncio
    async def test_no_default_group_fails_the_operation_with_a_usable_message(
        self, async_client
    ):
        """An instance without the baseline group gets a reason, not a null PATCH.

        A channel cannot be left group-less, so there is genuinely nowhere to put
        these channels. Saying so beats sending a request the API will reject.
        """
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 11, "name": "PBS 1", "channel_group_id": 377}],
            "count": 1,
            "next": None,
        }
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_channel_groups.return_value = [
            {"id": 377, "name": "Drill17 PBS West"},
        ]
        mock_client.update_channel.side_effect = _channel_patch_double()

        ops = [{"type": "deleteChannelGroup", "groupId": 377}]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            _, data = await _commit_and_wait(async_client, {"operations": ops})

        assert data["success"] is False
        assert data["operationsFailed"] == 1
        message = data["errors"][0]["error"]
        assert "Default Group" in message
        assert "1 channel" in message
        # Nothing was attempted upstream: no null PATCH, no doomed delete.
        mock_client.update_channel.assert_not_awaited()
        mock_client.delete_channel_group.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deleting_the_default_group_itself_fails_rather_than_no_oping(
        self, async_client
    ):
        """Moving a group's channels INTO that same group empties nothing."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 11, "name": "PBS 1", "channel_group_id": 42}],
            "count": 1,
            "next": None,
        }
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_channel_groups.return_value = [_DEFAULT_GROUP]
        mock_client.update_channel.side_effect = _channel_patch_double()

        ops = [{"type": "deleteChannelGroup", "groupId": 42}]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            _, data = await _commit_and_wait(async_client, {"operations": ops})

        assert data["success"] is False
        assert "Default Group" in data["errors"][0]["error"]
        mock_client.update_channel.assert_not_awaited()
        mock_client.delete_channel_group.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_empty_group_is_deleted_without_any_patch(self, async_client):
        """No members means no reparenting — the bare delete was always correct.

        It also means the baseline group need not exist: an empty group deletes
        cleanly on an instance that has no "Default Group" at all.
        """
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 13, "name": "Elsewhere", "channel_group_id": 999}],
            "count": 1,
            "next": None,
        }
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_channel_groups.return_value = []
        mock_client.update_channel.side_effect = _channel_patch_double()

        ops = [{"type": "deleteChannelGroup", "groupId": 378}]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(async_client, {"operations": ops})

        assert data["operationsApplied"] == 1
        mock_client.update_channel.assert_not_awaited()
        mock_client.delete_channel_group.assert_awaited_once_with(378)

    @pytest.mark.asyncio
    async def test_a_reparent_failure_fails_the_operation_and_skips_the_delete(
        self, async_client
    ):
        """A member that will not move must NOT be followed by a delete.

        Deleting anyway is how a group with channels reached Dispatcharr's 400 in
        the first place; failing the operation is what puts the reason in front of
        the operator.
        """
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 11, "name": "PBS 1", "channel_group_id": 377}],
            "count": 1,
            "next": None,
        }
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_channel_groups.return_value = [_DEFAULT_GROUP]
        mock_client.update_channel.side_effect = Exception("Dispatcharr refused the PATCH")

        ops = [{"type": "deleteChannelGroup", "groupId": 377}]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(async_client, {"operations": ops})

        assert data["success"] is False
        assert data["operationsFailed"] == 1
        assert data["errors"][0]["operationType"] == "deleteChannelGroup"
        mock_client.delete_channel_group.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_operation_is_never_reported_as_overall_success(
        self, async_client
    ):
        """VISIBILITY half of …-ayfn9: continueOnError must not launder a failure.

        The drill's operator saw "Apply All" report success while an operation had
        raised, with the only trace a server-side ERROR log. `continueOnError`
        means "keep going", never "call it a win": a batch with any failed
        operation reports `success=False`, and `partial` is what distinguishes
        "some of it landed" from "none of it did".
        """
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}

        results = iter([{"id": 101, "name": "A"}, Exception("Dispatcharr rejected B")])

        def _create(data):
            nxt = next(results)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        mock_client.create_channel.side_effect = _create

        ops = [
            {"type": "createChannel", "tempId": -1, "name": "A"},
            {"type": "createChannel", "tempId": -2, "name": "B"},
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response, data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        assert data["operationsApplied"] == 1
        assert data["operationsFailed"] == 1
        assert data["success"] is False
        assert data["partial"] is True
        assert len(data["errors"]) == 1


class TestBulkCommitAsync:
    """Tests for the 202+poll lifecycle of POST /api/channels/bulk-commit
    and its sibling GET /api/channels/bulk-commit/{job_id} (bd-ggxks).

    The bulk commit was previously synchronous and bounded by the 30s
    ECM_REQUEST_TIMEOUT_SECONDS middleware budget. On a 441-channel SiriusXM
    batch the timeout fired mid-flight, returning 504 to the operator while
    the handler kept running — duplicates piled up across retries. The new
    pattern returns 202 + job_id immediately so a slow batch can never hit
    the request-budget cliff.
    """

    @pytest.fixture(autouse=True)
    def _clear_jobs(self):
        from routers import channels as router_module

        router_module._BULK_COMMIT_JOBS.clear()
        yield
        router_module._BULK_COMMIT_JOBS.clear()

    @pytest.mark.asyncio
    async def test_post_returns_202_with_job_id_and_running_status(self, async_client):
        """A non-validateOnly POST immediately returns 202 + {job_id, status}."""
        import asyncio as _asyncio

        mock_client = AsyncMock()
        gate = _asyncio.Event()

        async def slow_run(req, batch_id=None):
            await gate.wait()
            return {
                "success": True,
                "operationsApplied": 0,
                "operationsFailed": 0,
                "errors": [],
                "tempIdMap": {},
                "groupIdMap": {},
            }

        try:
            with patch("routers.channels.get_client", return_value=mock_client), \
                 patch("routers.channels._run_bulk_commit", side_effect=slow_run):
                response = await async_client.post(
                    "/api/channels/bulk-commit", json={"operations": []}
                )
                assert response.status_code == 202
                body = response.json()
                assert body["job_id"]
                assert body["status"] == "running"

                # Job is registered as still running before the worker finishes.
                from routers.channels import _BULK_COMMIT_JOBS
                assert body["job_id"] in _BULK_COMMIT_JOBS
                assert _BULK_COMMIT_JOBS[body["job_id"]].status == "running"
        finally:
            gate.set()
            for _ in range(20):
                await _asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_get_while_running_returns_running_status(self, async_client):
        """GET /{job_id} reports running until the worker finishes."""
        import asyncio as _asyncio

        mock_client = AsyncMock()
        gate = _asyncio.Event()

        async def slow_run(req, batch_id=None):
            await gate.wait()
            return {
                "success": True,
                "operationsApplied": 0,
                "operationsFailed": 0,
                "errors": [],
                "tempIdMap": {},
                "groupIdMap": {},
            }

        try:
            with patch("routers.channels.get_client", return_value=mock_client), \
                 patch("routers.channels._run_bulk_commit", side_effect=slow_run):
                enqueue = await async_client.post(
                    "/api/channels/bulk-commit", json={"operations": []}
                )
                job_id = enqueue.json()["job_id"]

                poll = await async_client.get(f"/api/channels/bulk-commit/{job_id}")
                assert poll.status_code == 200
                body = poll.json()
                assert body["job_id"] == job_id
                assert body["status"] == "running"
                # Running poll envelope must NOT leak a result yet.
                assert "result" not in body
        finally:
            gate.set()
            for _ in range(20):
                await _asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_get_after_completion_returns_result_and_evicts_job(self, async_client):
        """Once the worker completes, GET returns the full BulkCommitResponse
        and the job row is dropped from the dict (single-shot retrieval)."""
        import asyncio as _asyncio

        mock_client = AsyncMock()

        async def fast_run(req, batch_id=None):
            return {
                "success": True,
                "operationsApplied": 3,
                "operationsFailed": 0,
                "errors": [],
                "tempIdMap": {-1: 100, -2: 101},
                "groupIdMap": {},
            }

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels._run_bulk_commit", side_effect=fast_run):
            enqueue = await async_client.post(
                "/api/channels/bulk-commit", json={"operations": []}
            )
            job_id = enqueue.json()["job_id"]
            for _ in range(50):
                await _asyncio.sleep(0)

            poll = await async_client.get(f"/api/channels/bulk-commit/{job_id}")
            assert poll.status_code == 200
            body = poll.json()
            assert body["status"] == "completed"
            assert body["result"]["operationsApplied"] == 3
            assert body["result"]["tempIdMap"] == {"-1": 100, "-2": 101}

            # Second GET of the same job returns 404 — the job was evicted
            # so RAM stays bounded for short-lived ops.
            from routers.channels import _BULK_COMMIT_JOBS
            assert job_id not in _BULK_COMMIT_JOBS
            second = await async_client.get(f"/api/channels/bulk-commit/{job_id}")
            assert second.status_code == 404

    @pytest.mark.asyncio
    async def test_get_failed_job_returns_failed_status(self, async_client):
        """A worker that raises is marked failed and exposed via GET status."""
        import asyncio as _asyncio

        mock_client = AsyncMock()

        async def boom(req, batch_id=None):
            raise RuntimeError("dispatcharr unreachable")

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels._run_bulk_commit", side_effect=boom):
            enqueue = await async_client.post(
                "/api/channels/bulk-commit", json={"operations": []}
            )
            job_id = enqueue.json()["job_id"]
            for _ in range(50):
                await _asyncio.sleep(0)

            poll = await async_client.get(f"/api/channels/bulk-commit/{job_id}")
            assert poll.status_code == 200
            body = poll.json()
            assert body["status"] == "failed"
            assert "dispatcharr unreachable" in body["error"]
            # Failed jobs stay in the dict until TTL prune so the operator
            # can re-poll and read the error (matches debug-bundle pattern).
            from routers.channels import _BULK_COMMIT_JOBS
            assert job_id in _BULK_COMMIT_JOBS

    @pytest.mark.asyncio
    async def test_get_unknown_job_id_returns_404(self, async_client):
        response = await async_client.get("/api/channels/bulk-commit/does-not-exist")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_post_returns_within_timeout_budget(self, async_client):
        """The handler itself must return fast — a slow worker must never
        block the enqueue path. The whole point of bd-ggxks is to make
        bulk-commit not synchronous on large batches."""
        import asyncio as _asyncio
        import time as _time

        mock_client = AsyncMock()
        gate = _asyncio.Event()

        async def slow_run(req, batch_id=None):
            await gate.wait()
            return {
                "success": True,
                "operationsApplied": 0,
                "operationsFailed": 0,
                "errors": [],
                "tempIdMap": {},
                "groupIdMap": {},
            }

        try:
            with patch("routers.channels.get_client", return_value=mock_client), \
                 patch("routers.channels._run_bulk_commit", side_effect=slow_run):
                start = _time.monotonic()
                response = await async_client.post(
                    "/api/channels/bulk-commit", json={"operations": []}
                )
                elapsed = _time.monotonic() - start

            assert response.status_code == 202
            assert elapsed < 5.0, (
                f"enqueue took {elapsed:.2f}s — handler is not async-enqueuing"
            )
        finally:
            gate.set()
            for _ in range(20):
                await _asyncio.sleep(0)

    def test_prune_drops_expired_jobs(self):
        """_prune_old_bulk_commit_jobs evicts jobs older than the TTL."""
        from routers import channels as router_module

        old = router_module._BulkCommitJob()
        old.created_at = (
            router_module.time.time() - (router_module._BULK_COMMIT_JOB_TTL_SECONDS + 60)
        )
        fresh = router_module._BulkCommitJob()
        router_module._BULK_COMMIT_JOBS["old"] = old
        router_module._BULK_COMMIT_JOBS["fresh"] = fresh

        router_module._prune_old_bulk_commit_jobs()

        assert "old" not in router_module._BULK_COMMIT_JOBS
        assert "fresh" in router_module._BULK_COMMIT_JOBS


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


# ---------------------------------------------------------------------------
# Admin gating (bd-um30y) — destructive / bulk operator-level channel
# endpoints carry RequireAdminIfEnabled, matching the bd-757hc gate on
# channel_pipeline.py and backup.py's create_backup / restore_backup.
#
# SECURITY FINDING: POST /api/channels/clear-auto-created (and its
# destructive/bulk siblings) were only authenticated via the global
# middleware but NOT authorized to admin. ECM has a real non-admin
# authenticated role (Dispatcharr-federated users are created with
# is_admin=False — auth/routes.py), so an authenticated NON-admin principal
# (or a narrowly-scoped MCP static-key session) could invoke destructive
# bulk ops (clear-auto-created, merge/bulk-merge that delete channels,
# bulk-commit, assign-numbers, import-csv). These tests prove the gate is
# now in place.
#
# Pattern mirrors test_channel_pipeline.py::TestAutoCreationAdminGating: the
# default `async_client` fixture runs with auth DISABLED (so
# RequireAdminIfEnabled is a no-op → returns None, and the existing
# happy-path tests above already prove behavior is unchanged when auth is
# off). Here we override the prebuilt dependency to simulate auth-enabled
# non-admin (403) and auth-enabled admin (pass-through).
#
# bd-v7n9f overturns the original assumption: ECM is OPERATOR-ONLY (federated
# Dispatcharr users default is_admin=False), so ALL channel-config writes now
# require admin — single-resource CRUD (create/update/delete channel, add/
# add-streams/remove/reorder a single channel's streams, logo CRUD incl. upload)
# is gated alongside the destructive/bulk class um30y already covered. Only
# read-only GETs and the preview/validation scan endpoints (preview-csv,
# normalize-preview-batch, find-duplicates) stay ungated — the global auth
# middleware already requires authentication for those.
# ---------------------------------------------------------------------------

# (path, http_method, request_kwargs) for every channels endpoint now admin-gated.
# Bodies are well-formed enough to pass FastAPI request parsing — the admin
# dependency raises BEFORE the handler runs, so handler internals are never
# reached when the gate rejects.
_GATED_CHANNEL_ENDPOINTS = [
    ("/api/channels/clear-auto-created", "post", {"json": {"group_ids": [1]}}),
    ("/api/channels/assign-numbers", "post",
     {"json": {"channel_ids": [1, 2], "starting_number": 100}}),
    ("/api/channels/bulk-commit", "post", {"json": {"operations": []}}),
    ("/api/channels/merge", "post",
     {"json": {"source_channel_ids": [1, 2], "target_name": "Merged"}}),
    ("/api/channels/bulk-merge", "post",
     {"json": {"merges": [{"target_channel_id": 1, "source_channel_ids": [2]}]}}),
    ("/api/channels/import-csv", "post",
     {"files": {"file": ("channels.csv", b"name\nESPN\n", "text/csv")}}),
    # bd-v7n9f: routine single-resource channel-config writes, now also gated.
    ("/api/channels", "post", {"json": {"name": "ESPN"}}),
    ("/api/channels/1", "patch", {"json": {"name": "ESPN HD"}}),
    ("/api/channels/1", "delete", {}),
    ("/api/channels/1/add-stream", "post", {"json": {"stream_id": 7}}),
    ("/api/channels/1/add-streams", "post", {"json": {"stream_ids": [7, 8]}}),
    ("/api/channels/1/remove-stream", "post", {"json": {"stream_id": 7}}),
    ("/api/channels/1/reorder-streams", "post", {"json": {"stream_ids": [8, 7]}}),
    ("/api/channels/logos", "post",
     {"json": {"name": "ESPN", "url": "http://x/logo.png"}}),
    ("/api/channels/logos/upload", "post",
     {"files": {"file": ("logo.png", b"\x89PNG", "image/png")}}),
    ("/api/channels/logos/1", "patch", {"json": {"name": "ESPN HD"}}),
    ("/api/channels/logos/1", "delete", {}),
]


class TestChannelsAdminGating:
    """Destructive / bulk channel endpoints require admin when auth is enabled;
    read endpoints and routine single-resource mutations stay reachable.
    Mirrors test_channel_pipeline.py::TestAutoCreationAdminGating."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path, method, kwargs", _GATED_CHANNEL_ENDPOINTS)
    async def test_non_admin_is_forbidden_when_auth_enabled(
        self, async_client, path, method, kwargs
    ):
        """Auth enabled + non-admin principal → 403 on every gated endpoint.

        Overriding RequireAdminIfEnabled.dependency to raise 403 simulates an
        authenticated-but-non-admin caller regardless of the test's auth state."""
        from fastapi import HTTPException, status
        from main import app
        from auth import RequireAdminIfEnabled as _prebuilt

        async def _reject() -> None:
            # Parameterless so FastAPI's DI introspection doesn't pull query args.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )

        app.dependency_overrides[_prebuilt.dependency] = _reject
        try:
            response = await getattr(async_client, method)(path, **kwargs)
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)

        assert response.status_code == 403, (
            f"{method.upper()} {path} should be admin-gated but returned "
            f"{response.status_code}"
        )
        assert "admin" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_admin_principal_can_clear_auto_created_when_auth_enabled(
        self, async_client
    ):
        """Auth enabled + admin principal → the gate passes through and the
        destructive clear-auto-created (the headline finding) executes
        normally."""
        from main import app
        from auth import RequireAdminIfEnabled as _prebuilt

        async def _allow_admin():
            # Stand in for an authenticated admin User; the handler ignores the
            # returned value (param is the unused `_admin`).
            return MagicMock(is_admin=True, username="admin")

        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 5, "name": "Auto ESPN", "channel_number": 5,
                 "channel_group_id": 1, "auto_created": True},
            ],
            "next": None,
        }
        mock_client.update_channel.return_value = {"id": 5, "auto_created": False}

        app.dependency_overrides[_prebuilt.dependency] = _allow_admin
        try:
            with patch("routers.channels.get_client", return_value=mock_client), \
                 patch("routers.channels.journal"):
                response = await async_client.post(
                    "/api/channels/clear-auto-created",
                    json={"group_ids": [1]},
                )
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)

        assert response.status_code == 200
        mock_client.update_channel.assert_called()

    @pytest.mark.asyncio
    async def test_clear_auto_created_allowed_when_auth_disabled(self, async_client):
        """Auth disabled (default async_client) → RequireAdminIfEnabled is a
        no-op and the endpoint behaves exactly as before the gate was added."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 5, "name": "Auto ESPN", "channel_number": 5,
                 "channel_group_id": 1, "auto_created": True},
            ],
            "next": None,
        }
        mock_client.update_channel.return_value = {"id": 5, "auto_created": False}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post(
                "/api/channels/clear-auto-created",
                json={"group_ids": [1]},
            )

        assert response.status_code == 200
        mock_client.update_channel.assert_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path, method, kwargs",
        [
            ("/api/channels", "post", {"json": {"name": "ESPN"}}),
            ("/api/channels/1", "delete", {}),
            ("/api/channels/1/add-stream", "post", {"json": {"stream_id": 7}}),
        ],
    )
    async def test_admin_principal_can_perform_routine_writes_when_auth_enabled(
        self, async_client, path, method, kwargs
    ):
        """Auth enabled + admin principal → the bd-v7n9f gate passes through and
        the newly-gated routine writes (create + delete + add-stream) execute
        normally."""
        from main import app
        from auth import RequireAdminIfEnabled as _prebuilt

        async def _allow_admin():
            return MagicMock(is_admin=True, username="admin")

        mock_client = AsyncMock()
        mock_client.create_channel.return_value = {"id": 1, "name": "ESPN"}
        mock_client.get_channel.return_value = {
            "id": 1, "name": "ESPN", "channel_number": 5, "streams": [],
        }
        mock_client.delete_channel.return_value = None
        mock_client.update_channel.return_value = {"id": 1, "streams": [7]}

        app.dependency_overrides[_prebuilt.dependency] = _allow_admin
        try:
            with patch("routers.channels.get_client", return_value=mock_client), \
                 patch("routers.channels.journal"):
                response = await getattr(async_client, method)(path, **kwargs)
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)

        assert response.status_code == 200, (
            f"{method.upper()} {path} should succeed for admin but returned "
            f"{response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_create_channel_allowed_when_auth_disabled(self, async_client):
        """Auth disabled (default async_client) → RequireAdminIfEnabled is a
        no-op and a representative newly-gated write (create channel) behaves
        exactly as before the bd-v7n9f gate was added."""
        mock_client = AsyncMock()
        mock_client.create_channel.return_value = {"id": 1, "name": "ESPN"}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post(
                "/api/channels", json={"name": "ESPN"}
            )

        assert response.status_code == 200
        mock_client.create_channel.assert_called_once()

    @pytest.mark.asyncio
    async def test_read_and_preview_endpoints_not_admin_gated(self, async_client):
        """Read-only GETs and the preview/validation scan endpoints stay
        reachable for a non-admin principal even when auth is enabled — only
        state-mutating writes are gated (bd-v7n9f). We override the admin
        dependency to reject; these endpoints don't depend on it, so they must
        still succeed (not 403)."""
        from fastapi import HTTPException, status
        from main import app
        from auth import RequireAdminIfEnabled as _prebuilt

        async def _reject() -> None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )

        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0}

        app.dependency_overrides[_prebuilt.dependency] = _reject
        try:
            with patch("routers.channels.get_client", return_value=mock_client), \
                 patch("routers.channels.journal"):
                list_resp = await async_client.get("/api/channels")
                dup_resp = await async_client.post("/api/channels/find-duplicates")
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)

        # Read endpoint and the read-only duplicate scan are NOT admin-gated,
        # so the reject override must not turn them into 403s.
        assert list_resp.status_code == 200
        assert dup_resp.status_code != 403


class TestFindDuplicateChannelsScope:
    """Tests for POST /api/channels/find-duplicates optional channel_ids scope
    (enhancedchannelmanager-uahp6). Two normalized-name duplicate pairs are
    seeded in every test: 'ESPN' (ids 1, 2) and 'Fox Sports' (ids 3, 4, one
    lowercased to prove case-insensitive grouping still works)."""

    @staticmethod
    def _single_page(results):
        return {"results": results, "count": len(results), "next": None}

    @staticmethod
    def _channel(cid, name):
        return {
            "id": cid,
            "name": name,
            "channel_number": cid,
            "streams": [],
            "channel_group_id": None,
            "channel_group_name": "",
        }

    def _seeded_channels(self):
        return [
            self._channel(1, "ESPN"),
            self._channel(2, "ESPN"),
            self._channel(3, "Fox Sports"),
            self._channel(4, "fox sports"),
        ]

    @pytest.mark.asyncio
    async def test_absent_body_scans_globally(self, async_client):
        """No JSON body at all -> global scan, both duplicate pairs found."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = self._single_page(self._seeded_channels())

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post("/api/channels/find-duplicates")

        assert response.status_code == 200
        data = response.json()
        assert data["total_groups"] == 2
        assert data["total_duplicate_channels"] == 4
        found_ids = {tuple(sorted(c["id"] for c in g["channels"])) for g in data["groups"]}
        assert found_ids == {(1, 2), (3, 4)}

    @pytest.mark.asyncio
    async def test_null_channel_ids_field_scans_globally(self, async_client):
        """{"channel_ids": null} is equivalent to an absent body -> global."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = self._single_page(self._seeded_channels())

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/find-duplicates", json={"channel_ids": None}
            )

        assert response.status_code == 200
        assert response.json()["total_groups"] == 2

    @pytest.mark.asyncio
    async def test_scoped_scan_finds_duplicate_pair_within_given_ids(self, async_client):
        """Scoping to [1, 2, 3] (both ESPN dupes + one lone Fox Sports) finds
        exactly the ESPN pair — the lone Fox Sports channel has no partner
        inside the requested scope."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = self._single_page(self._seeded_channels())

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/find-duplicates", json={"channel_ids": [1, 2, 3]}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total_groups"] == 1
        assert data["total_duplicate_channels"] == 2
        assert sorted(c["id"] for c in data["groups"][0]["channels"]) == [1, 2]

    @pytest.mark.asyncio
    async def test_scoped_scan_excludes_out_of_scope_duplicate_partner(self, async_client):
        """Scoping to [1, 3] (one member of each dup pair, but not both)
        must find ZERO groups — the fix's core guarantee that the scan only
        considers the selected ids, not their unselected duplicates."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = self._single_page(self._seeded_channels())

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/find-duplicates", json={"channel_ids": [1, 3]}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total_groups"] == 0
        assert data["groups"] == []

    @pytest.mark.asyncio
    async def test_empty_channel_ids_list_returns_empty_result_without_fetching(self, async_client):
        """An explicit empty list is a valid scope of 'nothing' — it must
        NOT fall back to a global scan (that would silently ignore the
        caller's scoping intent), and should short-circuit before ever
        calling Dispatcharr."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = self._single_page(self._seeded_channels())

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/find-duplicates", json={"channel_ids": []}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total_groups"] == 0
        assert data["groups"] == []
        mock_client.get_channels.assert_not_called()

    @pytest.mark.asyncio
    async def test_scoped_scan_stops_paginating_once_all_ids_found(self, async_client):
        """Scoped scan finds its (single-page) targets and does not fetch
        a second page it doesn't need, even though one exists."""
        mock_client = AsyncMock()

        async def _side_effect(page=1, page_size=500, search=None):
            if page == 1:
                return {
                    "results": [self._channel(1, "ESPN"), self._channel(2, "ESPN")],
                    "count": 4,
                    "next": "page-2",
                }
            return {
                "results": [self._channel(3, "Fox Sports"), self._channel(4, "fox sports")],
                "count": 4,
                "next": None,
            }

        mock_client.get_channels.side_effect = _side_effect

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/find-duplicates", json={"channel_ids": [1, 2]}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total_groups"] == 1
        assert mock_client.get_channels.call_count == 1

    @pytest.mark.asyncio
    async def test_scoped_scan_logs_scope_with_counts(self, async_client, caplog):
        """The scoped log line names both the found count and the requested
        selection count, distinct from the global log line's wording."""
        import logging

        mock_client = AsyncMock()
        mock_client.get_channels.return_value = self._single_page(self._seeded_channels())

        with patch("routers.channels.get_client", return_value=mock_client), \
             caplog.at_level(logging.INFO, logger="routers.channels"):
            response = await async_client.post(
                "/api/channels/find-duplicates", json={"channel_ids": [1, 2, 3]}
            )

        assert response.status_code == 200
        assert any(
            "scanning 3 channels (scoped to 3 selected)" in record.message
            for record in caplog.records
        )


class TestFindDuplicatesFoldMatchKey:
    """GH #645 / bead enhancedchannelmanager-0vao3: opt-in whitespace/case
    folding for POST /api/channels/find-duplicates.

    ``fold_match_key: true`` groups channels by the shared canonicalized key
    (casefold + strip ALL whitespace via ``match_fold.fold_match_key``) so
    the Find Duplicates surface matches what an opted-in auto-creation rule
    would merge. Default (absent/false) preserves the current grouping.
    """

    @staticmethod
    def _single_page(results):
        return {"results": results, "count": len(results), "next": None}

    @staticmethod
    def _channel(cid, name):
        return {
            "id": cid,
            "name": name,
            "channel_number": cid,
            "streams": [],
            "channel_group_id": None,
            "channel_group_name": "",
        }

    def _seeded_channels(self):
        # The exact four spellings from the GH #645 report.
        return [
            self._channel(1, "eurosport 2"),
            self._channel(2, "Eurosport 2"),
            self._channel(3, "Eurosport2"),
            self._channel(4, "eurosport2"),
            # Must never group with the above, fold or no fold.
            self._channel(5, "Eurosport 3"),
        ]

    @pytest.mark.asyncio
    async def test_default_grouping_unchanged_without_flag(self, async_client):
        """No fold flag -> case-insensitive exact grouping only: two pairs."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = self._single_page(self._seeded_channels())

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post("/api/channels/find-duplicates")

        assert response.status_code == 200
        data = response.json()
        found_ids = {tuple(sorted(c["id"] for c in g["channels"])) for g in data["groups"]}
        assert found_ids == {(1, 2), (3, 4)}

    @pytest.mark.asyncio
    async def test_fold_groups_whitespace_variants_together(self, async_client):
        """fold_match_key: true -> all four spellings form ONE group; the
        'Eurosport 3' near-miss stays out."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = self._single_page(self._seeded_channels())

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/find-duplicates", json={"fold_match_key": True}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total_groups"] == 1
        assert sorted(c["id"] for c in data["groups"][0]["channels"]) == [1, 2, 3, 4]
        # Visible names are untouched — the fold is a comparison key only.
        names = {c["name"] for c in data["groups"][0]["channels"]}
        assert names == {"eurosport 2", "Eurosport 2", "Eurosport2", "eurosport2"}

    @pytest.mark.asyncio
    async def test_fold_false_explicit_matches_default(self, async_client):
        """An explicit fold_match_key: false behaves exactly like the default."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = self._single_page(self._seeded_channels())

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/find-duplicates", json={"fold_match_key": False}
            )

        assert response.status_code == 200
        found_ids = {tuple(sorted(c["id"] for c in g["channels"]))
                     for g in response.json()["groups"]}
        assert found_ids == {(1, 2), (3, 4)}

    @pytest.mark.asyncio
    async def test_fold_composes_with_channel_ids_scope(self, async_client):
        """Fold + scope: only in-scope channels are grouped."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = self._single_page(self._seeded_channels())

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/find-duplicates",
                json={"fold_match_key": True, "channel_ids": [1, 3, 5]},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total_groups"] == 1
        assert sorted(c["id"] for c in data["groups"][0]["channels"]) == [1, 3]
