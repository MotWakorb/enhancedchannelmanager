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
        # u8qr6.5: the manager MUST be told to reload the new method (id) or the
        # new alert config never takes effect live. Assert the side effect, not
        # just that get_alert_manager was patched.
        mock_manager.reload_method.assert_called_once_with(data["id"])

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
        # u8qr6.5: update must notify the manager to reload this method.
        mock_manager.reload_method.assert_called_once_with(method.id)

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
        # u8qr6.5: enabling/disabling must notify the manager to reload.
        mock_manager.reload_method.assert_called_once_with(method.id)

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
        # u8qr6.5: deletion must notify the manager to drop the method live.
        mock_manager.reload_method.assert_called_once_with(method_id)

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 when deleting nonexistent method."""
        response = await async_client.delete("/api/alert-methods/99999")
        assert response.status_code == 404


class TestSMTPToEmailsCanonicalization:
    """Tests for SMTP to_emails canonicalization on POST/PATCH (bd-9vz32)."""

    @pytest.mark.asyncio
    async def test_post_persists_list_input_as_list(self, async_client, test_session):
        """POST with list to_emails persists canonical list shape."""
        from models import AlertMethod as AlertMethodModel

        response = await async_client.post(
            "/api/alert-methods",
            json={
                "name": "Email Alerts",
                "method_type": "smtp",
                "config": {"to_emails": ["alice@example.com", "bob@example.com"]},
            },
        )
        assert response.status_code == 200
        data = response.json()

        # Read the row back from the DB and confirm canonical shape persisted.
        row = test_session.query(AlertMethodModel).filter_by(id=data["id"]).one()
        persisted = json.loads(row.config)
        assert isinstance(persisted["to_emails"], list)
        assert persisted["to_emails"] == ["alice@example.com", "bob@example.com"]

    @pytest.mark.asyncio
    async def test_post_normalizes_string_input_to_list(self, async_client, test_session):
        """POST with comma-joined string to_emails normalizes to list shape."""
        from models import AlertMethod as AlertMethodModel

        response = await async_client.post(
            "/api/alert-methods",
            json={
                "name": "Email Alerts",
                "method_type": "smtp",
                "config": {"to_emails": "alice@example.com, bob@example.com"},
            },
        )
        assert response.status_code == 200
        data = response.json()

        row = test_session.query(AlertMethodModel).filter_by(id=data["id"]).one()
        persisted = json.loads(row.config)
        assert isinstance(persisted["to_emails"], list)
        assert persisted["to_emails"] == ["alice@example.com", "bob@example.com"]

    @pytest.mark.asyncio
    async def test_patch_normalizes_string_input_to_list(self, async_client, test_session):
        """PATCH with string to_emails normalizes to canonical list shape."""
        method = _create_alert_method(
            test_session,
            name="Email",
            method_type="smtp",
            config=json.dumps({"to_emails": ["old@example.com"]}),
        )
        mock_manager = MagicMock()

        with patch("routers.alert_methods.get_alert_manager", return_value=mock_manager):
            response = await async_client.patch(
                f"/api/alert-methods/{method.id}",
                json={"config": {"to_emails": "new1@example.com,new2@example.com"}},
            )

        assert response.status_code == 200
        test_session.refresh(method)
        persisted = json.loads(method.config)
        assert persisted["to_emails"] == ["new1@example.com", "new2@example.com"]

    @pytest.mark.asyncio
    async def test_round_trip_preserves_list_shape(self, async_client, test_session):
        """List input round-trips back through GET as a list."""
        post_response = await async_client.post(
            "/api/alert-methods",
            json={
                "name": "Email Alerts",
                "method_type": "smtp",
                "config": {"to_emails": ["alice@example.com"]},
            },
        )
        method_id = post_response.json()["id"]

        get_response = await async_client.get(f"/api/alert-methods/{method_id}")
        assert get_response.status_code == 200
        config = get_response.json()["config"]
        assert isinstance(config["to_emails"], list)
        assert config["to_emails"] == ["alice@example.com"]

    @pytest.mark.asyncio
    async def test_legacy_string_row_reads_as_string(self, async_client, test_session):
        """Existing rows persisted as a string still load successfully (read-tolerant)."""
        # Simulate a pre-bd-9vz32 row whose config was persisted with a string.
        method = _create_alert_method(
            test_session,
            name="Legacy SMTP",
            method_type="smtp",
            config=json.dumps({"to_emails": "old@example.com"}),
        )

        response = await async_client.get(f"/api/alert-methods/{method.id}")
        assert response.status_code == 200
        # Read returns whatever was stored — legacy rows still expose a string.
        # The SMTP runtime path handles this via _coerce_to_emails_to_list.
        config = response.json()["config"]
        assert config["to_emails"] == "old@example.com"


class TestSMTPValidateConfig:
    """Tests for SMTPMethod.validate_config defense-in-depth (bd-6e8gv)."""

    @pytest.mark.asyncio
    async def test_rejects_token_with_carriage_return(self, async_client):
        """POST with CR in to_emails entry returns 400."""
        response = await async_client.post(
            "/api/alert-methods",
            json={
                "name": "Bad Email",
                "method_type": "smtp",
                "config": {"to_emails": ["alice@example.com\rinjected"]},
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_token_with_line_feed(self, async_client):
        """POST with LF in to_emails entry returns 400."""
        response = await async_client.post(
            "/api/alert-methods",
            json={
                "name": "Bad Email",
                "method_type": "smtp",
                "config": {"to_emails": ["alice@example.com\nBcc: attacker@evil.com"]},
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_token_with_angle_bracket(self, async_client):
        """POST with < in to_emails entry returns 400."""
        response = await async_client.post(
            "/api/alert-methods",
            json={
                "name": "Bad Email",
                "method_type": "smtp",
                "config": {"to_emails": ["<alice@example.com>"]},
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_invalid_email_format(self, async_client):
        """POST with malformed email address returns 400."""
        response = await async_client.post(
            "/api/alert-methods",
            json={
                "name": "Bad Email",
                "method_type": "smtp",
                "config": {"to_emails": ["not-an-email"]},
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_accepts_valid_plus_addressed_email(self, async_client):
        """POST with valid `+tag` plus-addressed email is accepted."""
        response = await async_client.post(
            "/api/alert-methods",
            json={
                "name": "Plus Addr",
                "method_type": "smtp",
                "config": {"to_emails": ["alice+tag@x.co"]},
            },
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_accepts_legacy_string_input(self, async_client):
        """POST with legacy comma-joined string is accepted and validated per token."""
        response = await async_client.post(
            "/api/alert-methods",
            json={
                "name": "Legacy",
                "method_type": "smtp",
                "config": {"to_emails": "alice@example.com, bob@example.com"},
            },
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_rejects_legacy_string_with_invalid_token(self, async_client):
        """POST with legacy string containing a bad token returns 400."""
        response = await async_client.post(
            "/api/alert-methods",
            json={
                "name": "Bad Legacy",
                "method_type": "smtp",
                "config": {"to_emails": "alice@example.com, not-an-email"},
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_empty_list(self, async_client):
        """POST with an empty list returns 400 (presence-check fails first)."""
        response = await async_client.post(
            "/api/alert-methods",
            json={
                "name": "Empty",
                "method_type": "smtp",
                "config": {"to_emails": []},
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_patch_validates_new_config(self, async_client, test_session):
        """PATCH with malformed token rejects with 400."""
        method = _create_alert_method(
            test_session,
            name="Email",
            method_type="smtp",
            config=json.dumps({"to_emails": ["good@example.com"]}),
        )

        response = await async_client.patch(
            f"/api/alert-methods/{method.id}",
            json={"config": {"to_emails": ["alice@example.com\rinjected"]}},
        )
        assert response.status_code == 400


class TestValidateAlertSources:
    """Direct-function tests for validate_alert_sources (u8qr6.4c — was uncovered).

    The alert_sources granular-filtering validator (epg_refresh / m3u_refresh /
    probe_failures — filter_mode allowlist + shape/type checks) had zero
    coverage anywhere in the suite. These exercise every validation branch it
    guards; the endpoint-wiring tests below prove the router actually calls it.
    """

    def test_none_is_valid(self):
        from routers.alert_methods import validate_alert_sources
        assert validate_alert_sources(None) is None

    def test_empty_dict_is_valid(self):
        from routers.alert_methods import validate_alert_sources
        assert validate_alert_sources({}) is None

    def test_valid_full_structure(self):
        from routers.alert_methods import validate_alert_sources
        alert_sources = {
            "epg_refresh": {"filter_mode": "only_selected", "source_ids": [1, 2]},
            "m3u_refresh": {"filter_mode": "all_except", "account_ids": [3]},
            "probe_failures": {"min_failures": 5},
        }
        assert validate_alert_sources(alert_sources) is None

    def test_all_valid_filter_modes_accepted(self):
        from routers.alert_methods import validate_alert_sources
        for mode in ("all", "only_selected", "all_except"):
            assert validate_alert_sources({"epg_refresh": {"filter_mode": mode}}) is None

    # --- epg_refresh branch ---
    def test_epg_refresh_not_object(self):
        from routers.alert_methods import validate_alert_sources
        err = validate_alert_sources({"epg_refresh": "nope"})
        assert err == "epg_refresh must be an object"

    def test_epg_refresh_bad_filter_mode(self):
        from routers.alert_methods import validate_alert_sources
        err = validate_alert_sources({"epg_refresh": {"filter_mode": "bogus"}})
        assert err is not None and "filter_mode" in err

    def test_epg_refresh_source_ids_not_array(self):
        from routers.alert_methods import validate_alert_sources
        err = validate_alert_sources({"epg_refresh": {"source_ids": "1,2"}})
        assert err == "epg_refresh.source_ids must be an array"

    # --- m3u_refresh branch ---
    def test_m3u_refresh_not_object(self):
        from routers.alert_methods import validate_alert_sources
        err = validate_alert_sources({"m3u_refresh": [1, 2]})
        assert err == "m3u_refresh must be an object"

    def test_m3u_refresh_bad_filter_mode(self):
        from routers.alert_methods import validate_alert_sources
        err = validate_alert_sources({"m3u_refresh": {"filter_mode": "sometimes"}})
        assert err is not None and "filter_mode" in err

    def test_m3u_refresh_account_ids_not_array(self):
        from routers.alert_methods import validate_alert_sources
        err = validate_alert_sources({"m3u_refresh": {"account_ids": {"a": 1}}})
        assert err == "m3u_refresh.account_ids must be an array"

    # --- probe_failures branch ---
    def test_probe_failures_not_object(self):
        from routers.alert_methods import validate_alert_sources
        err = validate_alert_sources({"probe_failures": "5"})
        assert err == "probe_failures must be an object"

    def test_probe_failures_negative_min(self):
        from routers.alert_methods import validate_alert_sources
        err = validate_alert_sources({"probe_failures": {"min_failures": -1}})
        assert err == "probe_failures.min_failures must be a non-negative integer"

    def test_probe_failures_non_int_min(self):
        from routers.alert_methods import validate_alert_sources
        err = validate_alert_sources({"probe_failures": {"min_failures": "many"}})
        assert err == "probe_failures.min_failures must be a non-negative integer"

    def test_probe_failures_valid_min(self):
        from routers.alert_methods import validate_alert_sources
        assert validate_alert_sources({"probe_failures": {"min_failures": 0}}) is None


class TestAlertSourcesEndpointWiring:
    """Endpoint tests proving create/update actually enforce validate_alert_sources."""

    def _discord_patches(self):
        """Register discord + a passing config validator + stub manager."""
        mock_method = MagicMock()
        mock_method.validate_config.return_value = (True, None)
        return [
            patch("routers.alert_methods.get_method_types", return_value=[
                {"type": "discord", "display_name": "Discord", "required_fields": []},
            ]),
            patch("routers.alert_methods.create_method", return_value=mock_method),
            patch("routers.alert_methods.get_alert_manager", return_value=MagicMock()),
        ]

    @pytest.mark.asyncio
    async def test_create_accepts_valid_alert_sources(self, async_client, test_session):
        """POST with a well-formed alert_sources persists it and round-trips on GET."""
        import contextlib
        with contextlib.ExitStack() as stack:
            for p in self._discord_patches():
                stack.enter_context(p)
            response = await async_client.post("/api/alert-methods", json={
                "name": "Filtered Discord",
                "method_type": "discord",
                "config": {"webhook_url": "https://discord.com/api/webhooks/1"},
                "alert_sources": {
                    "epg_refresh": {"filter_mode": "only_selected", "source_ids": [7]},
                    "probe_failures": {"min_failures": 3},
                },
            })

        assert response.status_code == 200
        method_id = response.json()["id"]

        get_response = await async_client.get(f"/api/alert-methods/{method_id}")
        stored = get_response.json()["alert_sources"]
        assert stored["epg_refresh"] == {"filter_mode": "only_selected", "source_ids": [7]}
        assert stored["probe_failures"] == {"min_failures": 3}

    @pytest.mark.asyncio
    async def test_create_rejects_invalid_alert_sources(self, async_client):
        """POST with a bad epg_refresh.filter_mode is rejected 400 by the router."""
        import contextlib
        with contextlib.ExitStack() as stack:
            for p in self._discord_patches():
                stack.enter_context(p)
            response = await async_client.post("/api/alert-methods", json={
                "name": "Bad Sources",
                "method_type": "discord",
                "config": {"webhook_url": "https://discord.com/api/webhooks/1"},
                "alert_sources": {"epg_refresh": {"filter_mode": "whenever"}},
            })

        assert response.status_code == 400
        assert "filter_mode" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_rejects_invalid_alert_sources(self, async_client, test_session):
        """PATCH with a bad m3u_refresh.account_ids type is rejected 400."""
        method = _create_alert_method(test_session, name="Filter Me")

        with patch("routers.alert_methods.get_alert_manager", return_value=MagicMock()):
            response = await async_client.patch(
                f"/api/alert-methods/{method.id}",
                json={"alert_sources": {"m3u_refresh": {"account_ids": "not-a-list"}}},
            )

        assert response.status_code == 400
        assert "account_ids" in response.json()["detail"]


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
