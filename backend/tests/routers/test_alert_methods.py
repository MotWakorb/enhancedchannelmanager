"""
Unit tests for alert method endpoints.

Tests: GET /api/alert-methods/types, GET /api/alert-methods, POST /api/alert-methods,
       GET /api/alert-methods/{id}, PATCH /api/alert-methods/{id},
       DELETE /api/alert-methods/{id}, POST /api/alert-methods/{id}/test
Mocks: get_session(), alert_methods.* module functions.
"""
import json

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from models import AlertMethod


def _create_alert_method(session, **overrides):
    """Helper to create an AlertMethod with sensible defaults."""
    defaults = {
        "name": "Test Discord",
        "method_type": "discord",
        "enabled": True,
        "config": json.dumps({"webhook_url": "https://discord.com/api/webhooks/test"}),
        "notify_info": False,
        "notify_success": True,
        "notify_warning": True,
        "notify_error": True,
    }
    defaults.update(overrides)
    method = AlertMethod(**defaults)
    session.add(method)
    session.commit()
    session.refresh(method)
    return method


class TestGetAlertMethodTypes:
    """Tests for GET /api/alert-methods/types."""

    @pytest.mark.asyncio
    async def test_returns_types_list(self, async_client):
        """Returns list of available alert method types."""
        with patch("routers.alert_methods.get_method_types", return_value=[
            {"type": "discord", "display_name": "Discord", "required_fields": ["webhook_url"]},
            {"type": "telegram", "display_name": "Telegram", "required_fields": ["bot_token", "chat_id"]},
        ]):
            response = await async_client.get("/api/alert-methods/types")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["type"] == "discord"
        assert data[1]["type"] == "telegram"

    @pytest.mark.asyncio
    async def test_returns_500_on_error(self, async_client):
        """Returns 500 when get_method_types raises."""
        with patch("routers.alert_methods.get_method_types", side_effect=Exception("Module error")):
            response = await async_client.get("/api/alert-methods/types")

        assert response.status_code == 500


class TestListAlertMethods:
    """Tests for GET /api/alert-methods."""

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, async_client):
        """Returns empty list when no methods configured."""
        response = await async_client.get("/api/alert-methods")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_returns_configured_methods(self, async_client, test_session):
        """Returns all configured alert methods."""
        _create_alert_method(test_session, name="Discord Alerts")
        _create_alert_method(test_session, name="Telegram Alerts", method_type="telegram",
                           config=json.dumps({"bot_token": "tok", "chat_id": "123"}))

        response = await async_client.get("/api/alert-methods")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        names = {m["name"] for m in data}
        assert "Discord Alerts" in names
        assert "Telegram Alerts" in names

    @pytest.mark.asyncio
    async def test_includes_all_fields(self, async_client, test_session):
        """Response includes all expected fields."""
        _create_alert_method(test_session)

        response = await async_client.get("/api/alert-methods")
        data = response.json()
        method = data[0]
        assert "id" in method
        assert "name" in method
        assert "method_type" in method
        assert "enabled" in method
        assert "config" in method
        assert "notify_info" in method
        assert "notify_success" in method
        assert "notify_warning" in method
        assert "notify_error" in method


class TestCreateAlertMethod:
    """Tests for POST /api/alert-methods."""

    @pytest.mark.asyncio
    async def test_creates_method(self, async_client):
        """Creates a new alert method successfully."""
        mock_method = MagicMock()
        mock_method.validate_config.return_value = (True, None)
        mock_manager = MagicMock()

        with patch("routers.alert_methods.get_method_types", return_value=[
            {"type": "discord", "display_name": "Discord", "required_fields": ["webhook_url"]},
        ]), \
             patch("routers.alert_methods.create_method", return_value=mock_method), \
             patch("routers.alert_methods.get_alert_manager", return_value=mock_manager):
            response = await async_client.post("/api/alert-methods", json={
                "name": "My Discord",
                "method_type": "discord",
                "config": {"webhook_url": "https://discord.com/api/webhooks/123"},
            })

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "My Discord"
        assert data["method_type"] == "discord"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_rejects_unknown_type(self, async_client):
        """Returns 400 for unknown method type."""
        with patch("routers.alert_methods.get_method_types", return_value=[
            {"type": "discord", "display_name": "Discord", "required_fields": []},
        ]):
            response = await async_client.post("/api/alert-methods", json={
                "name": "Bad Method",
                "method_type": "carrier_pigeon",
                "config": {},
            })

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_invalid_config(self, async_client):
        """Returns 400 when config validation fails."""
        mock_method = MagicMock()
        mock_method.validate_config.return_value = (False, "Missing webhook_url")

        with patch("routers.alert_methods.get_method_types", return_value=[
            {"type": "discord", "display_name": "Discord", "required_fields": ["webhook_url"]},
        ]), \
             patch("routers.alert_methods.create_method", return_value=mock_method):
            response = await async_client.post("/api/alert-methods", json={
                "name": "Bad Config",
                "method_type": "discord",
                "config": {},
            })

        assert response.status_code == 400
        assert "webhook_url" in response.json()["detail"]


class TestGetAlertMethod:
    """Tests for GET /api/alert-methods/{method_id}."""

    @pytest.mark.asyncio
    async def test_returns_method(self, async_client, test_session):
        """Returns a specific alert method by ID."""
        method = _create_alert_method(test_session, name="Discord Test")

        response = await async_client.get(f"/api/alert-methods/{method.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == method.id
        assert data["name"] == "Discord Test"

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 when method doesn't exist."""
        response = await async_client.get("/api/alert-methods/99999")
        assert response.status_code == 404


class TestUpdateAlertMethod:
    """Tests for PATCH /api/alert-methods/{method_id}."""

    @pytest.mark.asyncio
    async def test_updates_name(self, async_client, test_session):
        """Updates the method name."""
        method = _create_alert_method(test_session, name="Old Name")
        mock_manager = MagicMock()

        with patch("routers.alert_methods.get_alert_manager", return_value=mock_manager):
            response = await async_client.patch(
                f"/api/alert-methods/{method.id}",
                json={"name": "New Name"},
            )

        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_updates_enabled(self, async_client, test_session):
        """Updates the enabled status."""
        method = _create_alert_method(test_session, enabled=True)
        mock_manager = MagicMock()

        with patch("routers.alert_methods.get_alert_manager", return_value=mock_manager):
            response = await async_client.patch(
                f"/api/alert-methods/{method.id}",
                json={"enabled": False},
            )

        assert response.status_code == 200

        # Verify in DB
        test_session.refresh(method)
        assert method.enabled is False

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 when updating nonexistent method."""
        response = await async_client.patch(
            "/api/alert-methods/99999",
            json={"name": "Ghost"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_validates_new_config(self, async_client, test_session):
        """Validates config when updating."""
        method = _create_alert_method(test_session)
        mock_method_instance = MagicMock()
        mock_method_instance.validate_config.return_value = (False, "Invalid URL")

        with patch("routers.alert_methods.create_method", return_value=mock_method_instance):
            response = await async_client.patch(
                f"/api/alert-methods/{method.id}",
                json={"config": {"webhook_url": "bad"}},
            )

        assert response.status_code == 400


class TestDeleteAlertMethod:
    """Tests for DELETE /api/alert-methods/{method_id}."""

    @pytest.mark.asyncio
    async def test_deletes_method(self, async_client, test_session):
        """Deletes an alert method."""
        method = _create_alert_method(test_session, name="Delete Me")
        method_id = method.id
        mock_manager = MagicMock()

        with patch("routers.alert_methods.get_alert_manager", return_value=mock_manager):
            response = await async_client.delete(f"/api/alert-methods/{method_id}")

        assert response.status_code == 200
        assert response.json()["success"] is True

        # Verify deleted from DB
        result = test_session.query(AlertMethod).filter(AlertMethod.id == method_id).first()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 when deleting nonexistent method."""
        response = await async_client.delete("/api/alert-methods/99999")
        assert response.status_code == 404


class TestTestAlertMethod:
    """Tests for POST /api/alert-methods/{method_id}/test."""

    @pytest.mark.asyncio
    async def test_sends_test_message(self, async_client, test_session):
        """Sends a test message and returns success."""
        method = _create_alert_method(test_session)
        mock_method_instance = MagicMock()
        mock_method_instance.test_connection = AsyncMock(return_value=(True, "Sent OK"))

        with patch("routers.alert_methods.create_method", return_value=mock_method_instance):
            response = await async_client.post(f"/api/alert-methods/{method.id}/test")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Sent OK"

    @pytest.mark.asyncio
    async def test_returns_failure(self, async_client, test_session):
        """Returns failure when test message fails."""
        method = _create_alert_method(test_session)
        mock_method_instance = MagicMock()
        mock_method_instance.test_connection = AsyncMock(return_value=(False, "Connection refused"))

        with patch("routers.alert_methods.create_method", return_value=mock_method_instance):
            response = await async_client.post(f"/api/alert-methods/{method.id}/test")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 when testing nonexistent method."""
        response = await async_client.post("/api/alert-methods/99999/test")
        assert response.status_code == 404
