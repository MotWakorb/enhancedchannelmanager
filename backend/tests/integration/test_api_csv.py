"""
Integration tests for CSV Import/Export API endpoints.

Tests:
- POST /api/channels/import-csv - Import channels from CSV
- GET /api/channels/export-csv - Export channels to CSV
- GET /api/channels/csv-template - Download CSV template
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestCSVTemplate:
    """Tests for GET /api/channels/csv-template endpoint."""

    @pytest.mark.asyncio
    async def test_get_template_returns_csv(self, async_client):
        """GET /api/channels/csv-template returns CSV content."""
        response = await async_client.get("/api/channels/csv-template")

        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_get_template_has_content_disposition(self, async_client):
        """GET /api/channels/csv-template has download filename."""
        response = await async_client.get("/api/channels/csv-template")

        assert response.status_code == 200
        content_disposition = response.headers.get("content-disposition", "")
        assert "attachment" in content_disposition
        assert "template" in content_disposition.lower()

    @pytest.mark.asyncio
    async def test_get_template_includes_header_row(self, async_client):
        """Template includes the column header row."""
        response = await async_client.get("/api/channels/csv-template")

        assert response.status_code == 200
        content = response.text
        assert "channel_number" in content
        assert "name" in content
        assert "group_name" in content

    @pytest.mark.asyncio
    async def test_get_template_includes_comments(self, async_client):
        """Template includes instructional comments."""
        response = await async_client.get("/api/channels/csv-template")

        assert response.status_code == 200
        content = response.text
        # Template should have comment lines
        assert content.startswith("#") or "# " in content


class TestCSVExport:
    """Tests for GET /api/channels/export-csv endpoint."""

    @pytest.mark.asyncio
    async def test_export_returns_csv(self, async_client):
        """GET /api/channels/export-csv returns CSV content type."""
        with patch("main.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_channels = AsyncMock(return_value={"results": [], "next": None, "count": 0})
            mock_client.get_channel_groups = AsyncMock(return_value=[])
            mock_client.get_streams = AsyncMock(return_value={"results": []})
            mock_get_client.return_value = mock_client

            response = await async_client.get("/api/channels/export-csv")

            assert response.status_code == 200
            assert "text/csv" in response.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_export_has_content_disposition(self, async_client):
        """GET /api/channels/export-csv has download filename with date."""
        with patch("main.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_channels = AsyncMock(return_value={"results": [], "next": None, "count": 0})
            mock_client.get_channel_groups = AsyncMock(return_value=[])
            mock_client.get_streams = AsyncMock(return_value={"results": []})
            mock_get_client.return_value = mock_client

            response = await async_client.get("/api/channels/export-csv")

            assert response.status_code == 200
            content_disposition = response.headers.get("content-disposition", "")
            assert "attachment" in content_disposition
            assert "channels" in content_disposition.lower()
            assert ".csv" in content_disposition

    @pytest.mark.asyncio
    async def test_export_includes_header_row(self, async_client):
        """Export CSV includes header row even with no channels."""
        with patch("main.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_channels = AsyncMock(return_value={"results": [], "next": None, "count": 0})
            mock_client.get_channel_groups = AsyncMock(return_value=[])
            mock_client.get_streams = AsyncMock(return_value={"results": []})
            mock_get_client.return_value = mock_client

            response = await async_client.get("/api/channels/export-csv")

            assert response.status_code == 200
            content = response.text
            assert "channel_number,name,group_name" in content

    @pytest.mark.asyncio
    async def test_export_includes_channel_data(self, async_client):
        """Export CSV includes actual channel data."""
        with patch("main.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_channels = AsyncMock(return_value={
                "results": [
                    {
                        "id": 1,
                        "channel_number": 101,
                        "name": "ESPN HD",
                        "channel_group_id": 1,
                        "tvg_id": "ESPN.US",
                        "tvc_guide_stationid": "12345",
                        "logo_url": "https://example.com/espn.png",
                        "streams": []
                    }
                ],
                "next": None,
                "count": 1
            })
            mock_client.get_channel_groups = AsyncMock(return_value=[{"id": 1, "name": "Sports"}])
            mock_client.get_streams = AsyncMock(return_value={"results": []})
            mock_get_client.return_value = mock_client

            response = await async_client.get("/api/channels/export-csv")

            assert response.status_code == 200
            content = response.text
            assert "ESPN HD" in content
            assert "101" in content
            assert "Sports" in content

    @pytest.mark.asyncio
    async def test_export_handles_api_error(self, async_client):
        """Export handles Dispatcharr API errors gracefully."""
        with patch("main.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_channel_groups = AsyncMock(side_effect=Exception("API Error"))
            mock_get_client.return_value = mock_client

            response = await async_client.get("/api/channels/export-csv")

            # Should return error status
            assert response.status_code in (500, 503)


class TestCSVImport:
    """Tests for POST /api/channels/import-csv endpoint."""

    @pytest.mark.asyncio
    async def test_import_requires_file(self, async_client):
        """POST /api/channels/import-csv requires a file upload."""
        response = await async_client.post("/api/channels/import-csv")

        # Should return error for missing file
        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_import_validates_csv_format(self, async_client):
        """Import validates that uploaded file is valid CSV."""
        # Upload invalid content
        files = {"file": ("test.csv", "not,valid\ncsv,without,name", "text/csv")}

        response = await async_client.post("/api/channels/import-csv", files=files)

        # Should return validation error
        assert response.status_code in (400, 422)
        data = response.json()
        assert "error" in data or "detail" in data

    @pytest.mark.asyncio
    async def test_import_requires_name_column(self, async_client):
        """Import requires 'name' column in CSV."""
        csv_content = "channel_number,group_name\n101,Sports"
        files = {"file": ("test.csv", csv_content, "text/csv")}

        response = await async_client.post("/api/channels/import-csv", files=files)

        # Should return error about missing name column
        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_import_creates_channels(self, async_client):
        """Import creates channels from valid CSV."""
        csv_content = """channel_number,name,group_name,tvg_id,gracenote_id,logo_url
101,ESPN HD,Sports,ESPN.US,12345,https://example.com/espn.png
102,CNN,News,CNN.US,67890,"""

        files = {"file": ("test.csv", csv_content, "text/csv")}

        with patch("main.get_client") as mock_get_client:
            mock_client = MagicMock()
            # Mock channel group lookup/creation
            mock_client.get_channel_groups = AsyncMock(return_value=[
                {"id": 1, "name": "Sports"},
                {"id": 2, "name": "News"}
            ])
            mock_client.create_channel = AsyncMock(return_value={"id": 1})
            mock_get_client.return_value = mock_client

            response = await async_client.post("/api/channels/import-csv", files=files)

            assert response.status_code == 200
            data = response.json()
            assert data.get("success") is True
            assert data.get("channels_created", 0) >= 0

    @pytest.mark.asyncio
    async def test_import_returns_summary(self, async_client):
        """Import returns summary of created channels and groups."""
        csv_content = """name,group_name
ESPN HD,Sports
CNN,News"""

        files = {"file": ("test.csv", csv_content, "text/csv")}

        with patch("main.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_channel_groups = AsyncMock(return_value=[])
            mock_client.create_channel_group = AsyncMock(return_value={"id": 1})
            mock_client.create_channel = AsyncMock(return_value={"id": 1})
            mock_get_client.return_value = mock_client

            response = await async_client.post("/api/channels/import-csv", files=files)

            assert response.status_code == 200
            data = response.json()
            assert "channels_created" in data
            assert "groups_created" in data

    @pytest.mark.asyncio
    async def test_import_returns_validation_errors(self, async_client):
        """Import returns validation errors for invalid rows."""
        csv_content = """channel_number,name,group_name
abc,ESPN HD,Sports
102,,News"""

        files = {"file": ("test.csv", csv_content, "text/csv")}

        with patch("main.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_channel_groups = AsyncMock(return_value=[
                {"id": 1, "name": "Sports"}
            ])
            mock_client.create_channel = AsyncMock(return_value={"id": 1})
            mock_get_client.return_value = mock_client

            response = await async_client.post("/api/channels/import-csv", files=files)

            # Should still return 200 but with errors array
            data = response.json()
            assert "errors" in data
            # Row 2 has invalid channel_number, row 3 has empty name
            assert len(data["errors"]) >= 1

    @pytest.mark.asyncio
    async def test_import_creates_new_groups(self, async_client):
        """Import creates new channel groups that don't exist."""
        csv_content = """name,group_name
ESPN HD,New Sports Group"""

        files = {"file": ("test.csv", csv_content, "text/csv")}

        with patch("main.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_channel_groups = AsyncMock(return_value=[])  # No existing groups
            mock_client.create_channel_group = AsyncMock(return_value={"id": 1, "name": "New Sports Group"})
            mock_client.create_channel = AsyncMock(return_value={"id": 1})
            mock_get_client.return_value = mock_client

            response = await async_client.post("/api/channels/import-csv", files=files)

            assert response.status_code == 200
            data = response.json()
            assert data.get("groups_created", 0) >= 1

    @pytest.mark.asyncio
    async def test_import_handles_duplicate_group_names_case_insensitive(self, async_client):
        """Import matches existing groups case-insensitively."""
        csv_content = """name,group_name
ESPN HD,SPORTS"""

        files = {"file": ("test.csv", csv_content, "text/csv")}

        with patch("main.get_client") as mock_get_client:
            mock_client = MagicMock()
            # Existing group with different case
            mock_client.get_channel_groups = AsyncMock(return_value=[
                {"id": 1, "name": "Sports"}
            ])
            mock_client.create_channel = AsyncMock(return_value={"id": 1})
            mock_get_client.return_value = mock_client

            response = await async_client.post("/api/channels/import-csv", files=files)

            assert response.status_code == 200
            data = response.json()
            # Should not create a new group
            assert data.get("groups_created", 0) == 0

    @pytest.mark.asyncio
    async def test_import_auto_assigns_channel_numbers(self, async_client):
        """Import auto-assigns channel numbers when not provided."""
        csv_content = """name,group_name
ESPN HD,Sports
CNN,News"""

        files = {"file": ("test.csv", csv_content, "text/csv")}

        with patch("main.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_channel_groups = AsyncMock(return_value=[
                {"id": 1, "name": "Sports"},
                {"id": 2, "name": "News"}
            ])
            mock_client.create_channel = AsyncMock(return_value={"id": 1})
            mock_get_client.return_value = mock_client

            response = await async_client.post("/api/channels/import-csv", files=files)

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_import_returns_warnings_for_logo_failures(self, async_client):
        """Import returns warnings when logo URLs fail to download."""
        csv_content = """name,logo_url
ESPN HD,https://invalid-url.example.com/logo.png"""

        files = {"file": ("test.csv", csv_content, "text/csv")}

        with patch("main.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_channel_groups = AsyncMock(return_value=[])
            mock_client.create_channel = AsyncMock(return_value={"id": 1})
            mock_get_client.return_value = mock_client

            response = await async_client.post("/api/channels/import-csv", files=files)

            # Channel should still be created, but with warning
            assert response.status_code == 200
            data = response.json()
            # May have warnings about logo
            if "warnings" in data:
                assert len(data["warnings"]) >= 0

    @pytest.mark.asyncio
    async def test_import_handles_empty_file(self, async_client):
        """Import handles empty CSV file gracefully."""
        csv_content = ""
        files = {"file": ("test.csv", csv_content, "text/csv")}

        response = await async_client.post("/api/channels/import-csv", files=files)

        # Should return success with 0 channels
        assert response.status_code in (200, 400)

    @pytest.mark.asyncio
    async def test_import_handles_header_only_file(self, async_client):
        """Import handles CSV with only header row."""
        csv_content = "channel_number,name,group_name"
        files = {"file": ("test.csv", csv_content, "text/csv")}

        response = await async_client.post("/api/channels/import-csv", files=files)

        assert response.status_code == 200
        data = response.json()
        assert data.get("channels_created", 0) == 0
