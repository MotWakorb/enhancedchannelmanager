"""
Unit tests for EPG endpoints.

Tests: 12 endpoints covering EPG sources CRUD, refresh, import,
       EPG data listing, grid, and LCN lookup.
Mocks: get_client() to isolate from Dispatcharr.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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


class TestTriggerEPGImport:
    """Tests for POST /api/epg/import."""

    @pytest.mark.asyncio
    async def test_triggers_import(self, async_client):
        """Triggers EPG data import."""
        mock_client = AsyncMock()
        mock_client.trigger_epg_import.return_value = {"status": "importing"}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post("/api/epg/import")

        assert response.status_code == 200


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
        """Returns EPG grid data."""
        mock_client = AsyncMock()
        mock_client.get_epg_grid.return_value = {"channels": [], "programmes": []}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/grid")

        assert response.status_code == 200

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
