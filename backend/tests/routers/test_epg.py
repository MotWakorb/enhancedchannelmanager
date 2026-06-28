"""
Unit tests for EPG endpoints.

Tests: 12 endpoints covering EPG sources CRUD, refresh, import,
       EPG data listing, grid, and LCN lookup.
Mocks: get_client() to isolate from Dispatcharr.
"""
import httpx
import pytest
from unittest.mock import AsyncMock, patch


class TestGetEPGSources:
    """Tests for GET /api/epg/sources."""

    @pytest.mark.asyncio
    async def test_returns_sources(self, async_client):
        """Returns EPG sources from client."""
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = [
            {"id": 1, "name": "XMLTV"},
            {"id": 2, "name": "Gracenote"},
        ]

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/sources")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_client_error(self, async_client):
        """Returns 500 on client error."""
        mock_client = AsyncMock()
        mock_client.get_epg_sources.side_effect = Exception("Timeout")

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/sources")

        assert response.status_code == 500


class TestGetEPGSource:
    """Tests for GET /api/epg/sources/{source_id}."""

    @pytest.mark.asyncio
    async def test_returns_source(self, async_client):
        """Returns a single EPG source."""
        mock_client = AsyncMock()
        mock_client.get_epg_source.return_value = {"id": 1, "name": "XMLTV"}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/sources/1")

        assert response.status_code == 200
        mock_client.get_epg_source.assert_called_once_with(1)


class TestCreateEPGSource:
    """Tests for POST /api/epg/sources."""

    @pytest.mark.asyncio
    async def test_creates_source(self, async_client):
        """Creates an EPG source."""
        mock_client = AsyncMock()
        mock_client.create_epg_source.return_value = {"id": 3, "name": "New EPG"}

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post("/api/epg/sources", json={
                "name": "New EPG",
                "url": "http://example.com/epg.xml",
            })

        assert response.status_code == 200
        assert response.json()["name"] == "New EPG"

    @pytest.mark.asyncio
    async def test_upstream_4xx_surfaces_400_not_500(self, async_client):
        """A Dispatcharr 4xx on create (bad body / missing required field)
        maps to 400 with the upstream detail, not an opaque 500 (bd-1wq7z.22).

        create_epg_source raises httpx.HTTPStatusError via raise_for_status().
        """
        mock_client = AsyncMock()
        request = httpx.Request("POST", "http://disp/api/epg/sources/")
        upstream = httpx.Response(
            400, request=request,
            text='{"source_type": ["This field is required."]}',
        )
        mock_client.create_epg_source.side_effect = httpx.HTTPStatusError(
            "400 Client Error", request=request, response=upstream
        )

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post("/api/epg/sources", json={
                "name": "New EPG",
                "url": "http://example.com/epg.xml",
            })

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "source_type" in detail
        assert "required" in detail

    @pytest.mark.asyncio
    async def test_genuine_server_error_still_500(self, async_client):
        """A non-upstream error on create stays a 500 (bd-1wq7z.22)."""
        mock_client = AsyncMock()
        mock_client.create_epg_source.side_effect = RuntimeError("boom")

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post("/api/epg/sources", json={
                "name": "New EPG",
                "url": "http://example.com/epg.xml",
            })

        assert response.status_code == 500


class TestUpdateEPGSource:
    """Tests for PATCH /api/epg/sources/{source_id}."""

    @pytest.mark.asyncio
    async def test_updates_source(self, async_client):
        """Updates an EPG source."""
        mock_client = AsyncMock()
        mock_client.get_epg_source.return_value = {"id": 1, "name": "Old Name"}
        mock_client.update_epg_source.return_value = {"id": 1, "name": "New Name"}

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.patch("/api/epg/sources/1", json={
                "name": "New Name",
            })

        assert response.status_code == 200
        mock_client.update_epg_source.assert_called_once_with(1, {"name": "New Name"})

    @pytest.mark.asyncio
    async def test_missing_source_returns_404_not_500(self, async_client):
        """Updating a nonexistent EPG source surfaces upstream 404 as 404, not 500
        (bd-lq38l.4). The before-state get_epg_source raises 404."""
        request = httpx.Request("GET", "http://disp/api/epg/sources/999/")
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.get_epg_source.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.patch("/api/epg/sources/999", json={"name": "New Name"})

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_genuine_server_error_still_500(self, async_client):
        """A non-upstream error stays a 500 (bd-lq38l.4)."""
        mock_client = AsyncMock()
        mock_client.get_epg_source.side_effect = RuntimeError("boom")

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.patch("/api/epg/sources/1", json={"name": "New Name"})

        assert response.status_code == 500


class TestDeleteEPGSource:
    """Tests for DELETE /api/epg/sources/{source_id}."""

    @pytest.mark.asyncio
    async def test_deletes_source(self, async_client):
        """Deletes an EPG source."""
        mock_client = AsyncMock()
        mock_client.get_epg_source.return_value = {"id": 1, "name": "XMLTV"}
        mock_client.delete_epg_source.return_value = None

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.delete("/api/epg/sources/1")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_missing_source_returns_404_not_500(self, async_client):
        """Deleting a nonexistent EPG source surfaces upstream 404 as 404, not 500
        (bd-lq38l.4)."""
        request = httpx.Request("GET", "http://disp/api/epg/sources/999/")
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.get_epg_source.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.delete("/api/epg/sources/999")

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]


class TestRefreshEPGSource:
    """Tests for POST /api/epg/sources/{source_id}/refresh."""

    @pytest.mark.asyncio
    async def test_triggers_refresh(self, async_client):
        """Triggers EPG source refresh."""
        mock_client = AsyncMock()
        mock_client.get_epg_source.return_value = {
            "id": 1, "name": "XMLTV", "updated_at": "2024-01-01T00:00:00Z",
        }
        mock_client.refresh_epg_source.return_value = {"status": "refreshing"}

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.asyncio.create_task"):
            response = await async_client.post("/api/epg/sources/1/refresh")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_source_returns_404_not_500(self, async_client):
        """Refreshing a nonexistent EPG source surfaces upstream 404 as 404, not
        500 (bd-lq38l.4). The initial get_epg_source raises 404."""
        request = httpx.Request("GET", "http://disp/api/epg/sources/999/")
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.get_epg_source.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.send_alert", new=AsyncMock()), \
             patch("routers.epg.asyncio.create_task"):
            response = await async_client.post("/api/epg/sources/999/refresh")

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]


class TestTriggerEPGImport:
    """Tests for POST /api/epg/import."""

    @pytest.mark.asyncio
    async def test_triggers_import(self, async_client):
        """Triggers EPG data import and returns the forwarded result."""
        mock_client = AsyncMock()
        mock_client.trigger_epg_import.return_value = {"status": "importing"}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post("/api/epg/import")

        assert response.status_code == 200
        assert response.json() == {"status": "importing"}
        mock_client.trigger_epg_import.assert_called_once_with()


class TestGetEPGData:
    """Tests for GET /api/epg/data."""

    @pytest.mark.asyncio
    async def test_returns_data(self, async_client):
        """Returns EPG data with pagination."""
        mock_client = AsyncMock()
        mock_client.get_epg_data.return_value = {
            "results": [], "count": 0,
        }

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/data")

        assert response.status_code == 200
        mock_client.get_epg_data.assert_called_once_with(
            page=1, page_size=100, search=None, epg_source=None,
        )

    @pytest.mark.asyncio
    async def test_passes_filters(self, async_client):
        """Passes search and source filters."""
        mock_client = AsyncMock()
        mock_client.get_epg_data.return_value = {"results": [], "count": 0}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/data", params={
                "search": "ESPN", "epg_source": 1,
            })

        assert response.status_code == 200
        mock_client.get_epg_data.assert_called_once_with(
            page=1, page_size=100, search="ESPN", epg_source=1,
        )


class TestGetEPGDataById:
    """Tests for GET /api/epg/data/{data_id}."""

    @pytest.mark.asyncio
    async def test_returns_entry(self, async_client):
        """Returns a single EPG data entry."""
        mock_client = AsyncMock()
        mock_client.get_epg_data_by_id.return_value = {"id": 42, "name": "ESPN"}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/data/42")

        assert response.status_code == 200
        mock_client.get_epg_data_by_id.assert_called_once_with(42)


class TestGetEPGGrid:
    """Tests for GET /api/epg/grid."""

    @pytest.mark.asyncio
    async def test_returns_grid(self, async_client):
        """Returns EPG grid data forwarded verbatim from the client."""
        mock_client = AsyncMock()
        mock_client.get_epg_grid.return_value = {"channels": ["ch1"], "programmes": ["prog1"]}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/grid")

        assert response.status_code == 200
        assert response.json() == {"channels": ["ch1"], "programmes": ["prog1"]}
        mock_client.get_epg_grid.assert_called_once_with(start=None, end=None)

    @pytest.mark.asyncio
    async def test_handles_timeout(self, async_client):
        """Returns 504 on ReadTimeout."""
        import httpx as httpx_mod
        mock_client = AsyncMock()
        mock_client.get_epg_grid.side_effect = httpx_mod.ReadTimeout("Timed out")

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/grid")

        assert response.status_code == 504


class TestGetEPGLCN:
    """Tests for GET /api/epg/lcn."""

    @pytest.mark.asyncio
    async def test_returns_404_when_no_xmltv_sources(self, async_client):
        """Returns 404 when no XMLTV sources exist."""
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = [
            {"id": 1, "source_type": "gracenote", "url": None},
        ]

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/lcn", params={"tvg_id": "ESPN.us"})

        assert response.status_code == 404


class TestBatchLCN:
    """Tests for POST /api/epg/lcn/batch."""

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_items(self, async_client):
        """Returns empty results for empty items list."""
        response = await async_client.post("/api/epg/lcn/batch", json={
            "items": [],
        })

        assert response.status_code == 200
        assert response.json()["results"] == {}


class TestLinkChannelToEPG:
    """Tests for POST /api/epg/channels/{channel_id}/link.

    Closes the Scenario 6 seam: picking a 'multiple candidate' tvg_id/epg_data_id
    and establishing the channel's epg_data link (so set_logo_from_epg works).
    """

    @pytest.mark.asyncio
    async def test_links_by_explicit_epg_data_id(self, async_client):
        """Sets the channel's epg_data_id via update_channel when given an id."""
        mock_client = AsyncMock()
        mock_client.update_channel.return_value = {
            "id": 7, "name": "ESPN", "epg_data_id": 42,
        }

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post(
                "/api/epg/channels/7/link", json={"epg_data_id": 42},
            )

        assert response.status_code == 200
        # The link is established with the exact-match mechanism: PATCH epg_data_id.
        mock_client.update_channel.assert_called_once_with(7, {"epg_data_id": 42})
        mock_client.get_epg_data.assert_not_called()
        assert response.json()["epg_data_id"] == 42

    @pytest.mark.asyncio
    async def test_links_by_tvg_id_resolves_to_epg_data_row(self, async_client):
        """Resolves a tvg_id to its EPG data row id, then sets epg_data_id."""
        mock_client = AsyncMock()
        mock_client.get_epg_data.return_value = [
            {"id": 11, "tvg_id": "CNN.us", "name": "CNN"},
            {"id": 99, "tvg_id": "ESPN.us", "name": "ESPN"},
        ]
        mock_client.update_channel.return_value = {
            "id": 7, "name": "ESPN", "epg_data_id": 99,
        }

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post(
                "/api/epg/channels/7/link", json={"tvg_id": "ESPN.us"},
            )

        assert response.status_code == 200
        mock_client.get_epg_data.assert_called_once_with(search="ESPN.us")
        mock_client.update_channel.assert_called_once_with(7, {"epg_data_id": 99})

    @pytest.mark.asyncio
    async def test_epg_data_id_wins_over_tvg_id(self, async_client):
        """When both are supplied, epg_data_id is used (no tvg_id resolution)."""
        mock_client = AsyncMock()
        mock_client.update_channel.return_value = {"id": 7, "epg_data_id": 5}

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post(
                "/api/epg/channels/7/link",
                json={"epg_data_id": 5, "tvg_id": "ESPN.us"},
            )

        assert response.status_code == 200
        mock_client.get_epg_data.assert_not_called()
        mock_client.update_channel.assert_called_once_with(7, {"epg_data_id": 5})

    @pytest.mark.asyncio
    async def test_missing_both_returns_400(self, async_client):
        """Returns 400 when neither epg_data_id nor tvg_id is provided."""
        mock_client = AsyncMock()

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post("/api/epg/channels/7/link", json={})

        assert response.status_code == 400
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_unresolvable_tvg_id_returns_404(self, async_client):
        """Returns 404 when no EPG data row matches the given tvg_id."""
        mock_client = AsyncMock()
        mock_client.get_epg_data.return_value = [
            {"id": 11, "tvg_id": "CNN.us", "name": "CNN"},
        ]

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/epg/channels/7/link", json={"tvg_id": "NOPE.us"},
            )

        assert response.status_code == 404
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_upstream_4xx_surfaces_400_not_500(self, async_client):
        """A Dispatcharr 4xx on the PATCH maps to a clean 4xx, not an opaque 500."""
        request = httpx.Request("PATCH", "http://x/api/channels/channels/7/")
        bad_response = httpx.Response(400, request=request, text="bad epg_data_id")
        mock_client = AsyncMock()
        mock_client.update_channel.side_effect = httpx.HTTPStatusError(
            "400", request=request, response=bad_response,
        )

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/epg/channels/7/link", json={"epg_data_id": 42},
            )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_genuine_server_error_still_500(self, async_client):
        """A non-HTTP error still surfaces as 500."""
        mock_client = AsyncMock()
        mock_client.update_channel.side_effect = Exception("boom")

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/epg/channels/7/link", json={"epg_data_id": 42},
            )

        assert response.status_code == 500


class TestSDLineups:
    """Tests for the Schedules Direct lineup proxy endpoints."""

    @pytest.mark.asyncio
    async def test_lists_lineups(self, async_client):
        """GET forwards to the client and returns Dispatcharr's payload."""
        mock_client = AsyncMock()
        mock_client.get_sd_lineups.return_value = {
            "lineups": [{"lineup": "USA-NJ29486-X", "name": "Comcast NJ"}],
            "max_lineups": 4,
            "changes_remaining": 5,
        }

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/sources/1/sd-lineups")

        assert response.status_code == 200
        body = response.json()
        assert body["max_lineups"] == 4
        assert body["lineups"][0]["lineup"] == "USA-NJ29486-X"
        mock_client.get_sd_lineups.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_adds_lineup_forwards_body(self, async_client):
        """POST forwards the lineup id and journals the change."""
        mock_client = AsyncMock()
        mock_client.add_sd_lineup.return_value = {"success": True}

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post(
                "/api/epg/sources/1/sd-lineups", json={"lineup": "USA-NJ29486-X"},
            )

        assert response.status_code == 200
        mock_client.add_sd_lineup.assert_called_once_with(1, "USA-NJ29486-X")

    @pytest.mark.asyncio
    async def test_removes_lineup_forwards_body(self, async_client):
        """DELETE forwards the lineup id."""
        mock_client = AsyncMock()
        mock_client.delete_sd_lineup.return_value = {"success": True}

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.request(
                "DELETE", "/api/epg/sources/1/sd-lineups", json={"lineup": "USA-NJ29486-X"},
            )

        assert response.status_code == 200
        mock_client.delete_sd_lineup.assert_called_once_with(1, "USA-NJ29486-X")

    @pytest.mark.asyncio
    async def test_searches_lineups_forwards_location(self, async_client):
        """POST search forwards country + postalcode."""
        mock_client = AsyncMock()
        mock_client.search_sd_lineups.return_value = {"lineups": []}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/epg/sources/1/sd-lineups/search",
                json={"country": "USA", "postalcode": "07030"},
            )

        assert response.status_code == 200
        mock_client.search_sd_lineups.assert_called_once_with(1, "USA", "07030")

    @pytest.mark.asyncio
    async def test_upstream_4xx_surfaces_as_4xx(self, async_client):
        """A Dispatcharr 4xx (e.g. SD daily add limit) maps to a 4xx, not 500."""
        mock_client = AsyncMock()
        request = httpx.Request("POST", "http://disp/api/epg/sources/1/sd-lineups/")
        upstream = httpx.Response(
            400, request=request, text='{"detail": "Daily lineup change limit reached"}',
        )
        mock_client.add_sd_lineup.side_effect = httpx.HTTPStatusError(
            "400 Client Error", request=request, response=upstream
        )

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post(
                "/api/epg/sources/1/sd-lineups", json={"lineup": "USA-NJ29486-X"},
            )

        assert response.status_code == 400
        assert "limit" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_genuine_server_error_still_500(self, async_client):
        """A non-upstream error stays a 500."""
        mock_client = AsyncMock()
        mock_client.get_sd_lineups.side_effect = RuntimeError("boom")

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/sources/1/sd-lineups")

        assert response.status_code == 500


class TestProgramPoster:
    """Tests for GET /api/epg/programs/{program_id}/poster."""

    @pytest.mark.asyncio
    async def test_proxies_poster_bytes_and_content_type(self, async_client):
        """Streams Dispatcharr's bytes through with its Content-Type."""
        mock_client = AsyncMock()
        upstream = httpx.Response(
            200, content=b"\xff\xd8\xff-image-bytes",
            headers={"content-type": "image/png"},
        )
        mock_client.get_program_poster.return_value = upstream

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/programs/42/poster")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == b"\xff\xd8\xff-image-bytes"
        mock_client.get_program_poster.assert_called_once_with(42)


class TestMatchChannelsToEPG:
    """Tests for POST /api/epg/match — server-side source-priority ranking."""

    @staticmethod
    def _client(sources):
        """Mock client: one ESPN channel, two tying ESPN.us EPG entries."""
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = sources
        mock_client.get_channels.return_value = {
            "results": [{"id": 1, "name": "ESPN", "streams": [1]}],
        }
        mock_client.get_streams.return_value = {
            "results": [{"id": 1, "name": "US | ESPN",
                         "channel_group_name": "US Sports"}],
            "next": None,
        }
        mock_client.get_epg_data.return_value = [
            {"id": 100, "name": "ESPN", "tvg_id": "ESPN.us",
             "epg_source": {"id": 2, "name": "Backup"}},
            {"id": 101, "name": "ESPN", "tvg_id": "ESPN.us",
             "epg_source": {"id": 1, "name": "Primary"}},
        ]
        return mock_client

    @staticmethod
    def _best(data):
        bucket = data["multiple"] or data["exact"]
        return bucket[0]["matches"][0]

    @pytest.mark.asyncio
    async def test_priority_resolved_server_side(self, async_client):
        """The preferred source (higher priority) wins the tie, from the server."""
        from cache import get_cache
        get_cache().clear()
        sources = [
            {"id": 1, "name": "Primary", "priority": 10},
            {"id": 2, "name": "Backup", "priority": 1},
        ]
        mock_client = self._client(sources)
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post("/api/epg/match", json={
                "channel_ids": [1],
            })
        assert response.status_code == 200
        mock_client.get_epg_sources.assert_awaited()
        assert self._best(response.json())["epg_source"]["id"] == 1

    @pytest.mark.asyncio
    async def test_priority_change_busts_cache(self, async_client):
        """Reordering source priority changes the cache key and the winner."""
        from cache import get_cache
        get_cache().clear()
        # First call: source 1 preferred.
        mock_a = self._client([
            {"id": 1, "name": "Primary", "priority": 10},
            {"id": 2, "name": "Backup", "priority": 1},
        ])
        with patch("routers.epg.get_client", return_value=mock_a):
            first = await async_client.post("/api/epg/match", json={"channel_ids": [1]})
        assert self._best(first.json())["epg_source"]["id"] == 1

        # Second call: priorities flipped — must NOT serve the stale cached result.
        mock_b = self._client([
            {"id": 1, "name": "Primary", "priority": 1},
            {"id": 2, "name": "Backup", "priority": 10},
        ])
        with patch("routers.epg.get_client", return_value=mock_b):
            second = await async_client.post("/api/epg/match", json={"channel_ids": [1]})
        # Fresh matching ran (cache busted) and the new preferred source wins.
        mock_b.get_epg_data.assert_awaited()
        assert self._best(second.json())["epg_source"]["id"] == 2

    @pytest.mark.asyncio
    async def test_deprecated_source_order_ignored(self, async_client):
        """A client-sent source_order is ignored; server priority still wins."""
        from cache import get_cache
        get_cache().clear()
        sources = [
            {"id": 1, "name": "Primary", "priority": 10},
            {"id": 2, "name": "Backup", "priority": 1},
        ]
        mock_client = self._client(sources)
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post("/api/epg/match", json={
                "channel_ids": [1],
                # Deprecated: tries to make source 2 win — must be ignored.
                "source_order": [2, 1],
            })
        assert response.status_code == 200
        assert self._best(response.json())["epg_source"]["id"] == 1
