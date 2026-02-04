"""
Integration tests for Dispatcharr Authentication.

TDD SPEC: These tests define expected Dispatcharr auth behavior.
They will FAIL initially - implementation makes them pass.

Test Spec: Dispatcharr Authentication (v6dxf.8.6)
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from auth.settings import AuthSettings, DispatcharrAuthSettings, LocalAuthSettings
from auth.providers.dispatcharr import DispatcharrAuthResult, DispatcharrAuthenticationError


def mock_dispatcharr_enabled_settings():
    """Create mock settings with Dispatcharr enabled."""
    settings = MagicMock(spec=AuthSettings)
    settings.dispatcharr = MagicMock(spec=DispatcharrAuthSettings)
    settings.dispatcharr.enabled = True
    settings.local = MagicMock(spec=LocalAuthSettings)
    settings.local.enabled = True
    settings.oidc = MagicMock(enabled=False)
    settings.saml = MagicMock(enabled=False)
    settings.ldap = MagicMock(enabled=False)
    settings.jwt = MagicMock()
    settings.jwt.refresh_token_expire_days = 7
    return settings


def mock_dispatcharr_disabled_settings():
    """Create mock settings with Dispatcharr disabled."""
    settings = MagicMock(spec=AuthSettings)
    settings.dispatcharr = MagicMock(spec=DispatcharrAuthSettings)
    settings.dispatcharr.enabled = False
    settings.local = MagicMock(spec=LocalAuthSettings)
    settings.local.enabled = True
    settings.oidc = MagicMock(enabled=False)
    settings.saml = MagicMock(enabled=False)
    settings.ldap = MagicMock(enabled=False)
    settings.jwt = MagicMock()
    settings.jwt.refresh_token_expire_days = 7
    return settings


def create_mock_dispatcharr_client(auth_result=None, auth_error=None):
    """Create a mock DispatcharrClient that works as async context manager."""
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    if auth_error:
        mock_client.authenticate = AsyncMock(side_effect=auth_error)
    else:
        mock_client.authenticate = AsyncMock(return_value=auth_result)

    return mock_client


class TestDispatcharrAuthentication:
    """Tests for Dispatcharr authentication flow."""

    @pytest.mark.asyncio
    async def test_dispatcharr_login_calls_dispatcharr_api(self, async_client):
        """POST /api/auth/dispatcharr/login with valid creds calls Dispatcharr token endpoint."""
        auth_result = DispatcharrAuthResult(
            user_id="disp-123",
            username="dispuser",
            email="user@dispatcharr.local",
        )
        mock_client = create_mock_dispatcharr_client(auth_result=auth_result)

        with patch("auth.routes.get_auth_settings", return_value=mock_dispatcharr_enabled_settings()):
            with patch("auth.providers.dispatcharr.DispatcharrClient", return_value=mock_client):
                response = await async_client.post(
                    "/api/auth/dispatcharr/login",
                    json={
                        "username": "dispuser",
                        "password": "disppassword",
                    },
                )

                assert response.status_code == 200
                mock_client.authenticate.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatcharr_auth_creates_local_user(self, async_client):
        """Successful Dispatcharr auth creates/updates local user with auth_provider='dispatcharr'."""
        auth_result = DispatcharrAuthResult(
            user_id="disp-456",
            username="newdispuser",
            email="new@dispatcharr.local",
        )
        mock_client = create_mock_dispatcharr_client(auth_result=auth_result)

        with patch("auth.routes.get_auth_settings", return_value=mock_dispatcharr_enabled_settings()):
            with patch("auth.providers.dispatcharr.DispatcharrClient", return_value=mock_client):
                response = await async_client.post(
                    "/api/auth/dispatcharr/login",
                    json={
                        "username": "newdispuser",
                        "password": "password123",
                    },
                )

                assert response.status_code == 200
                data = response.json()
                assert data["user"]["auth_provider"] == "dispatcharr"
                assert data["user"]["username"] == "newdispuser"

    @pytest.mark.asyncio
    async def test_dispatcharr_user_external_id_stored(self, async_client):
        """Dispatcharr user external_id is stored."""
        auth_result = DispatcharrAuthResult(
            user_id="disp-external-789",
            username="extuser",
            email="ext@dispatcharr.local",
        )
        mock_client = create_mock_dispatcharr_client(auth_result=auth_result)

        with patch("auth.routes.get_auth_settings", return_value=mock_dispatcharr_enabled_settings()):
            with patch("auth.providers.dispatcharr.DispatcharrClient", return_value=mock_client):
                response = await async_client.post(
                    "/api/auth/dispatcharr/login",
                    json={
                        "username": "extuser",
                        "password": "password",
                    },
                )

                assert response.status_code == 200
                data = response.json()
                assert data["user"]["external_id"] == "disp-external-789"

    @pytest.mark.asyncio
    async def test_dispatcharr_auth_failure_returns_401(self, async_client):
        """Failed Dispatcharr auth returns 401 with clear error."""
        mock_client = create_mock_dispatcharr_client(
            auth_error=DispatcharrAuthenticationError("Invalid credentials")
        )

        with patch("auth.routes.get_auth_settings", return_value=mock_dispatcharr_enabled_settings()):
            with patch("auth.providers.dispatcharr.DispatcharrClient", return_value=mock_client):
                response = await async_client.post(
                    "/api/auth/dispatcharr/login",
                    json={
                        "username": "baduser",
                        "password": "wrongpassword",
                    },
                )

                assert response.status_code == 401
                assert "detail" in response.json()

    @pytest.mark.asyncio
    async def test_dispatcharr_timeout_returns_503(self, async_client):
        """Dispatcharr connection timeout returns 503."""
        mock_client = create_mock_dispatcharr_client(
            auth_error=TimeoutError("Connection timed out")
        )

        with patch("auth.routes.get_auth_settings", return_value=mock_dispatcharr_enabled_settings()):
            with patch("auth.providers.dispatcharr.DispatcharrClient", return_value=mock_client):
                response = await async_client.post(
                    "/api/auth/dispatcharr/login",
                    json={
                        "username": "user",
                        "password": "password",
                    },
                )

                assert response.status_code == 503
                assert "timeout" in response.json()["detail"].lower()


class TestDispatcharrUserSync:
    """Tests for Dispatcharr user synchronization."""

    @pytest.mark.asyncio
    async def test_dispatcharr_user_info_synced_on_login(self, async_client):
        """Dispatcharr user info is synced on each login."""
        # First login with old email
        auth_result1 = DispatcharrAuthResult(
            user_id="sync-user-1",
            username="syncuser",
            email="old@email.com",
        )
        mock_client1 = create_mock_dispatcharr_client(auth_result=auth_result1)

        with patch("auth.routes.get_auth_settings", return_value=mock_dispatcharr_enabled_settings()):
            with patch("auth.providers.dispatcharr.DispatcharrClient", return_value=mock_client1):
                await async_client.post(
                    "/api/auth/dispatcharr/login",
                    json={"username": "syncuser", "password": "pass"},
                )

        # Second login with updated email
        auth_result2 = DispatcharrAuthResult(
            user_id="sync-user-1",
            username="syncuser",
            email="new@email.com",
        )
        mock_client2 = create_mock_dispatcharr_client(auth_result=auth_result2)

        with patch("auth.routes.get_auth_settings", return_value=mock_dispatcharr_enabled_settings()):
            with patch("auth.providers.dispatcharr.DispatcharrClient", return_value=mock_client2):
                response = await async_client.post(
                    "/api/auth/dispatcharr/login",
                    json={"username": "syncuser", "password": "pass"},
                )

                assert response.status_code == 200
                # Email should be updated
                assert response.json()["user"]["email"] == "new@email.com"

    @pytest.mark.asyncio
    async def test_dispatcharr_user_cannot_use_local_password(self, async_client):
        """Dispatcharr user can't use local password login."""
        # First create a Dispatcharr user
        auth_result = DispatcharrAuthResult(
            user_id="disp-no-local",
            username="disponly",
            email="disponly@test.com",
        )
        mock_client = create_mock_dispatcharr_client(auth_result=auth_result)

        with patch("auth.routes.get_auth_settings", return_value=mock_dispatcharr_enabled_settings()):
            with patch("auth.providers.dispatcharr.DispatcharrClient", return_value=mock_client):
                await async_client.post(
                    "/api/auth/dispatcharr/login",
                    json={"username": "disponly", "password": "disppass"},
                )

        # Try local login - should fail
        response = await async_client.post(
            "/api/auth/login",
            json={"username": "disponly", "password": "anypassword"},
        )
        assert response.status_code == 401
        # Error should indicate user must use their auth provider
        detail = response.json()["detail"].lower()
        assert "authentication provider" in detail or "dispatcharr" in detail


class TestDispatcharrConfiguration:
    """Tests for Dispatcharr configuration."""

    @pytest.mark.asyncio
    async def test_dispatcharr_auth_only_when_enabled(self, async_client):
        """Dispatcharr auth only available when enabled in settings."""
        # When disabled, endpoint should return 404 or indicate not available
        with patch("auth.routes.get_auth_settings", return_value=mock_dispatcharr_disabled_settings()):
            response = await async_client.post(
                "/api/auth/dispatcharr/login",
                json={"username": "user", "password": "pass"},
            )
            assert response.status_code in (404, 400)

    @pytest.mark.asyncio
    async def test_auth_providers_includes_dispatcharr_when_enabled(self, async_client):
        """GET /api/auth/providers includes dispatcharr when enabled."""
        with patch("auth.routes.get_auth_settings", return_value=mock_dispatcharr_enabled_settings()):
            response = await async_client.get("/api/auth/providers")
            assert response.status_code == 200
            providers = response.json()["providers"]
            assert any(p["type"] == "dispatcharr" for p in providers)

    @pytest.mark.asyncio
    async def test_auth_providers_excludes_dispatcharr_when_disabled(self, async_client):
        """GET /api/auth/providers excludes dispatcharr when disabled."""
        with patch("auth.routes.get_auth_settings", return_value=mock_dispatcharr_disabled_settings()):
            response = await async_client.get("/api/auth/providers")
            assert response.status_code == 200
            providers = response.json()["providers"]
            assert not any(p["type"] == "dispatcharr" for p in providers)
