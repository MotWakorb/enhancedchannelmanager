"""
Integration tests for First-Run Setup API endpoints.

TDD SPEC: These tests define expected first-run setup behavior.
They will FAIL initially - implementation makes them pass.

Test Spec: First-Run Setup (v6dxf.8.5)
"""
import pytest


class TestSetupDetection:
    """Tests for setup status detection."""

    @pytest.mark.asyncio
    async def test_setup_required_returns_true_when_no_users(self, async_client):
        """GET /api/auth/setup-required returns {required: true} when no users exist."""
        # With empty database, setup should be required
        response = await async_client.get("/api/auth/setup-required")
        assert response.status_code == 200
        data = response.json()
        assert data["required"] is True

    @pytest.mark.asyncio
    async def test_setup_required_returns_false_when_users_exist(self, async_client):
        """GET /api/auth/setup-required returns {required: false} when users exist."""
        # First create a user via setup
        await async_client.post(
            "/api/auth/setup",
            json={
                "username": "admin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )

        # Now setup should not be required
        response = await async_client.get("/api/auth/setup-required")
        assert response.status_code == 200
        data = response.json()
        assert data["required"] is False

    @pytest.mark.asyncio
    async def test_setup_required_endpoint_is_public(self, async_client):
        """GET /api/auth/setup-required does not require authentication."""
        # Should work without any auth token
        response = await async_client.get("/api/auth/setup-required")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_protected_endpoints_indicate_setup_needed(self, async_client):
        """All protected endpoints redirect/indicate setup when no users exist."""
        # With no users, protected endpoints should indicate setup is needed
        response = await async_client.get("/api/settings")
        # Could be 401 with setup_required flag, or redirect, or special status
        assert response.status_code in (200, 401, 403, 503)
        # If 401/403, should indicate setup is required
        if response.status_code in (401, 403):
            data = response.json()
            assert data.get("setup_required") is True or "setup" in data.get(
                "detail", ""
            ).lower()


class TestAdminCreation:
    """Tests for initial admin creation via setup."""

    @pytest.mark.asyncio
    async def test_setup_creates_first_admin_user(self, async_client):
        """POST /api/auth/setup creates first admin user."""
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert "user" in data
        assert data["user"]["username"] == "firstadmin"
        assert data["user"]["is_admin"] is True

    @pytest.mark.asyncio
    async def test_setup_only_works_when_no_users_exist(self, async_client):
        """POST /api/auth/setup only works when no users exist."""
        # First setup succeeds
        response1 = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "admin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )
        assert response1.status_code == 201

        # Second setup should fail
        response2 = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "anotheradmin",
                "email": "another@example.com",
                "password": "AnotherPass123!",
            },
        )
        assert response2.status_code == 403

    @pytest.mark.asyncio
    async def test_setup_returns_403_if_users_already_exist(self, async_client):
        """POST /api/auth/setup returns 403 if users already exist."""
        # Create first user
        await async_client.post(
            "/api/auth/setup",
            json={
                "username": "existingadmin",
                "email": "existing@example.com",
                "password": "ExistingPass123!",
            },
        )

        # Try setup again
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "newadmin",
                "email": "new@example.com",
                "password": "NewPass123!",
            },
        )
        assert response.status_code == 403
        assert "already" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_first_user_is_automatically_admin(self, async_client):
        """First user is automatically is_admin=True."""
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "thefirstuser",
                "email": "first@example.com",
                "password": "FirstPass123!",
            },
        )
        assert response.status_code == 201
        data = response.json()
        # First user via setup MUST be admin
        assert data["user"]["is_admin"] is True

    @pytest.mark.asyncio
    async def test_after_setup_normal_auth_works(self, async_client):
        """After setup, normal auth flow works."""
        # Complete setup
        setup_response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "setupadmin",
                "email": "setup@example.com",
                "password": "SetupPass123!",
            },
        )
        assert setup_response.status_code == 201

        # Now login should work
        login_response = await async_client.post(
            "/api/auth/login",
            json={
                "username": "setupadmin",
                "password": "SetupPass123!",
            },
        )
        assert login_response.status_code == 200
        assert "access_token" in login_response.cookies

    @pytest.mark.asyncio
    async def test_setup_validates_password_strength(self, async_client):
        """POST /api/auth/setup validates password strength."""
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "admin",
                "email": "admin@example.com",
                "password": "weak",
            },
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_setup_requires_all_fields(self, async_client):
        """POST /api/auth/setup requires username, email, and password."""
        # Missing username
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "email": "admin@example.com",
                "password": "ValidPass123!",
            },
        )
        assert response.status_code == 422

        # Missing email
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "admin",
                "password": "ValidPass123!",
            },
        )
        assert response.status_code == 422

        # Missing password
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "admin",
                "email": "admin@example.com",
            },
        )
        assert response.status_code == 422
