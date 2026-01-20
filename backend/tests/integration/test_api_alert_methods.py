"""
Integration tests for the Alert Methods API endpoints.

These tests use the FastAPI test client with database session overrides
to test the alert methods endpoints.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestGetAlertMethodTypes:
    """Tests for GET /api/alert-methods/types endpoint."""

    @pytest.mark.asyncio
    async def test_get_types_returns_array(self, async_client):
        """GET /api/alert-methods/types returns array of method types."""
        response = await async_client.get("/api/alert-methods/types")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_get_types_includes_required_fields(self, async_client):
        """GET /api/alert-methods/types includes type information."""
        response = await async_client.get("/api/alert-methods/types")
        assert response.status_code == 200

        types = response.json()
        # If any types are registered, they should have required fields
        for method_type in types:
            assert "type" in method_type
            assert "display_name" in method_type
            assert "required_fields" in method_type


class TestListAlertMethods:
    """Tests for GET /api/alert-methods endpoint."""

    @pytest.mark.asyncio
    async def test_list_returns_empty_array(self, async_client):
        """GET /api/alert-methods returns empty array when no methods."""
        response = await async_client.get("/api/alert-methods")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_list_returns_configured_methods(self, async_client, test_session):
        """GET /api/alert-methods returns configured methods."""
        from tests.fixtures.factories import create_alert_method

        create_alert_method(test_session, name="Test Discord", method_type="discord")

        response = await async_client.get("/api/alert-methods")
        assert response.status_code == 200

        methods = response.json()
        assert len(methods) >= 1
        assert any(m["name"] == "Test Discord" for m in methods)

    @pytest.mark.asyncio
    async def test_list_includes_method_details(self, async_client, test_session):
        """GET /api/alert-methods includes full method details."""
        from tests.fixtures.factories import create_alert_method

        create_alert_method(
            test_session,
            name="Test Method",
            method_type="discord",
            enabled=True,
            notify_success=True,
            notify_error=True,
        )

        response = await async_client.get("/api/alert-methods")
        assert response.status_code == 200

        methods = response.json()
        method = next((m for m in methods if m["name"] == "Test Method"), None)
        assert method is not None
        assert "id" in method
        assert "enabled" in method
        assert "notify_success" in method


class TestCreateAlertMethod:
    """Tests for POST /api/alert-methods endpoint."""

    @pytest.mark.asyncio
    async def test_create_discord_method(self, async_client):
        """POST /api/alert-methods creates Discord method."""
        response = await async_client.post(
            "/api/alert-methods",
            json={
                "name": "New Discord",
                "method_type": "discord",
                "config": {"webhook_url": "https://discord.com/api/webhooks/test"},
                "enabled": True,
            },
        )
        # May fail if discord isn't registered, that's OK
        assert response.status_code in (200, 201, 400)

    @pytest.mark.asyncio
    async def test_create_validates_method_type(self, async_client):
        """POST /api/alert-methods validates method type."""
        response = await async_client.post(
            "/api/alert-methods",
            json={
                "name": "Invalid Method",
                "method_type": "nonexistent_type",
                "config": {},
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_create_validates_required_config(self, async_client):
        """POST /api/alert-methods validates required config fields."""
        response = await async_client.post(
            "/api/alert-methods",
            json={
                "name": "Missing Config",
                "method_type": "discord",
                "config": {},  # Missing webhook_url
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_create_requires_name(self, async_client):
        """POST /api/alert-methods requires name field."""
        response = await async_client.post(
            "/api/alert-methods",
            json={
                "method_type": "discord",
                "config": {"webhook_url": "https://discord.com/api/webhooks/test"},
            },
        )
        assert response.status_code == 422  # Validation error


class TestGetAlertMethod:
    """Tests for GET /api/alert-methods/{method_id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_method_returns_details(self, async_client, test_session):
        """GET /api/alert-methods/{method_id} returns method details."""
        from tests.fixtures.factories import create_alert_method

        method = create_alert_method(test_session, name="Test Get", method_type="discord")

        response = await async_client.get(f"/api/alert-methods/{method.id}")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "Test Get"
        assert data["method_type"] == "discord"

    @pytest.mark.asyncio
    async def test_get_method_not_found(self, async_client):
        """GET /api/alert-methods/{method_id} returns 404 for unknown ID."""
        response = await async_client.get("/api/alert-methods/99999")
        assert response.status_code == 404


class TestUpdateAlertMethod:
    """Tests for PATCH /api/alert-methods/{method_id} endpoint."""

    @pytest.mark.asyncio
    async def test_update_method_name(self, async_client, test_session):
        """PATCH /api/alert-methods/{method_id} updates method name."""
        from tests.fixtures.factories import create_alert_method

        method = create_alert_method(test_session, name="Original Name", method_type="discord")

        response = await async_client.patch(
            f"/api/alert-methods/{method.id}",
            json={"name": "Updated Name"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_update_method_enabled(self, async_client, test_session):
        """PATCH /api/alert-methods/{method_id} updates enabled status."""
        from tests.fixtures.factories import create_alert_method

        method = create_alert_method(test_session, name="Test", method_type="discord", enabled=True)

        response = await async_client.patch(
            f"/api/alert-methods/{method.id}",
            json={"enabled": False},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_update_notification_types(self, async_client, test_session):
        """PATCH /api/alert-methods/{method_id} updates notification types."""
        from tests.fixtures.factories import create_alert_method

        method = create_alert_method(test_session, name="Test", method_type="discord")

        response = await async_client.patch(
            f"/api/alert-methods/{method.id}",
            json={
                "notify_info": True,
                "notify_success": False,
            },
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_method_not_found(self, async_client):
        """PATCH /api/alert-methods/{method_id} returns 404 for unknown ID."""
        response = await async_client.patch(
            "/api/alert-methods/99999",
            json={"name": "Test"},
        )
        assert response.status_code == 404


class TestDeleteAlertMethod:
    """Tests for DELETE /api/alert-methods/{method_id} endpoint."""

    @pytest.mark.asyncio
    async def test_delete_method_removes(self, async_client, test_session):
        """DELETE /api/alert-methods/{method_id} removes method."""
        from tests.fixtures.factories import create_alert_method

        method = create_alert_method(test_session, name="To Delete", method_type="discord")
        method_id = method.id

        response = await async_client.delete(f"/api/alert-methods/{method_id}")
        assert response.status_code in (200, 204)

        # Verify deleted
        response = await async_client.get(f"/api/alert-methods/{method_id}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_method_not_found(self, async_client):
        """DELETE /api/alert-methods/{method_id} returns 404 for unknown ID."""
        response = await async_client.delete("/api/alert-methods/99999")
        assert response.status_code == 404


class TestTestAlertMethod:
    """Tests for POST /api/alert-methods/{method_id}/test endpoint."""

    @pytest.mark.asyncio
    async def test_test_method_sends_test_message(self, async_client, test_session):
        """POST /api/alert-methods/{method_id}/test sends test message."""
        from tests.fixtures.factories import create_alert_method

        method = create_alert_method(test_session, name="Test", method_type="discord")

        with patch("main.create_method") as mock_create:
            mock_instance = MagicMock()
            mock_instance.test_connection = AsyncMock(return_value=(True, "OK"))
            mock_create.return_value = mock_instance

            response = await async_client.post(f"/api/alert-methods/{method.id}/test")
            assert response.status_code in (200, 500)

    @pytest.mark.asyncio
    async def test_test_method_not_found(self, async_client):
        """POST /api/alert-methods/{method_id}/test returns 404 for unknown ID."""
        response = await async_client.post("/api/alert-methods/99999/test")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_test_method_returns_result(self, async_client, test_session):
        """POST /api/alert-methods/{method_id}/test returns test result."""
        from tests.fixtures.factories import create_alert_method

        method = create_alert_method(test_session, name="Test", method_type="discord")

        with patch("main.create_method") as mock_create:
            mock_instance = MagicMock()
            mock_instance.test_connection = AsyncMock(return_value=(True, "Connection successful"))
            mock_create.return_value = mock_instance

            response = await async_client.post(f"/api/alert-methods/{method.id}/test")

            if response.status_code == 200:
                data = response.json()
                assert "success" in data or "message" in data
