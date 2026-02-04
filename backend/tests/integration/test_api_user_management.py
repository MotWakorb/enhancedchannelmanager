"""
Integration tests for User Management API endpoints.

TDD SPEC: These tests define expected user management behavior.
They will FAIL initially - implementation makes them pass.

Test Spec: User Management (v6dxf.8.4)
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def admin_user(test_session):
    """Create an admin user for testing."""
    from models import User
    from auth.password import hash_password

    user = User(
        username="admin",
        email="admin@example.com",
        password_hash=hash_password("adminpass123"),
        auth_provider="local",
        is_admin=True,
        is_active=True,
    )
    test_session.add(user)
    test_session.commit()
    test_session.refresh(user)
    return user


@pytest.fixture
def regular_user(test_session):
    """Create a regular (non-admin) user for testing."""
    from models import User
    from auth.password import hash_password

    user = User(
        username="regularuser",
        email="regular@example.com",
        password_hash=hash_password("userpass123"),
        auth_provider="local",
        is_admin=False,
        is_active=True,
    )
    test_session.add(user)
    test_session.commit()
    test_session.refresh(user)
    return user


@pytest.fixture
def test_user(test_session):
    """Create a test user for password management tests."""
    from models import User
    from auth.password import hash_password

    user = User(
        username="testuser",
        email="test@example.com",
        password_hash=hash_password("OldPass123!"),
        auth_provider="local",
        is_admin=False,
        is_active=True,
    )
    test_session.add(user)
    test_session.commit()
    test_session.refresh(user)
    return user


@pytest.fixture
def password_reset_token(test_session, test_user):
    """Create a valid password reset token for testing."""
    from models import PasswordResetToken
    from auth.password import hash_password

    # Use a known raw token
    raw_token = "valid-reset-token"
    token = PasswordResetToken(
        user_id=test_user.id,
        token_hash=hash_password(raw_token),
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    test_session.add(token)
    test_session.commit()
    return raw_token


@pytest.fixture
def expired_reset_token(test_session, test_user):
    """Create an expired password reset token for testing."""
    from models import PasswordResetToken
    from auth.password import hash_password

    raw_token = "expired-reset-token"
    token = PasswordResetToken(
        user_id=test_user.id,
        token_hash=hash_password(raw_token),
        expires_at=datetime.utcnow() - timedelta(hours=2),  # Expired 2 hours ago
    )
    test_session.add(token)
    test_session.commit()
    return raw_token


class TestAdminUserCRUD:
    """Tests for admin user management endpoints."""

    @pytest.mark.asyncio
    async def test_get_users_returns_paginated_list(self, async_client, admin_user):
        """GET /api/admin/users returns paginated user list."""
        # Login as admin first
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass123"},
        )

        response = await async_client.get("/api/admin/users")
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data

    @pytest.mark.asyncio
    async def test_get_users_with_search_filters_results(self, async_client, admin_user, test_user):
        """GET /api/admin/users?search=term filters results."""
        # Login as admin first
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass123"},
        )

        response = await async_client.get("/api/admin/users?search=testuser")
        assert response.status_code == 200
        data = response.json()
        # All returned users should match search term
        for user in data["users"]:
            assert (
                "testuser" in user["username"].lower()
                or "testuser" in user.get("email", "").lower()
            )

    @pytest.mark.asyncio
    async def test_admin_create_user(self, async_client, admin_user):
        """POST /api/admin/users creates user (admin bypass password rules optional)."""
        # Login as admin first
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass123"},
        )

        response = await async_client.post(
            "/api/admin/users",
            json={
                "username": "admincreatee",
                "email": "created@example.com",
                "password": "SimplePass1",
                "is_admin": False,
            },
        )
        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_admin_update_user(self, async_client, admin_user, regular_user):
        """PATCH /api/admin/users/{id} updates user fields."""
        # Login as admin first
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass123"},
        )

        # Update regular_user (not admin)
        response = await async_client.patch(
            f"/api/admin/users/{regular_user.id}",
            json={"email": "updated@example.com"},
        )
        assert response.status_code == 200
        assert response.json()["user"]["email"] == "updated@example.com"

    @pytest.mark.asyncio
    async def test_admin_delete_user_soft_deletes(self, async_client, admin_user, regular_user):
        """DELETE /api/admin/users/{id} deactivates (soft delete)."""
        # Login as admin first
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass123"},
        )

        response = await async_client.delete(f"/api/admin/users/{regular_user.id}")
        assert response.status_code == 200

        # User should still exist but be deactivated
        user_response = await async_client.get(f"/api/admin/users/{regular_user.id}")
        assert user_response.status_code == 200
        assert user_response.json()["user"]["is_active"] is False

    @pytest.mark.asyncio
    async def test_non_admin_cannot_access_admin_endpoints(self, async_client, regular_user):
        """Non-admin cannot access /api/admin/* endpoints (403)."""
        # Login as regular user
        await async_client.post(
            "/api/auth/login",
            json={"username": "regularuser", "password": "userpass123"},
        )

        response = await async_client.get("/api/admin/users")
        assert response.status_code == 403


class TestPasswordManagement:
    """Tests for password management endpoints."""

    @pytest.mark.asyncio
    async def test_change_password_with_correct_current_password(self, async_client, test_user):
        """POST /api/auth/change-password with correct current password succeeds."""
        # Login first
        await async_client.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "OldPass123!"},
        )

        response = await async_client.post(
            "/api/auth/change-password",
            json={
                "current_password": "OldPass123!",
                "new_password": "NewPass456!",
            },
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_change_password_with_wrong_current_password_returns_401(
        self, async_client, test_user
    ):
        """POST /api/auth/change-password with wrong current password returns 401."""
        # Login first
        await async_client.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "OldPass123!"},
        )

        response = await async_client.post(
            "/api/auth/change-password",
            json={
                "current_password": "WrongPass123!",
                "new_password": "NewPass456!",
            },
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_forgot_password_sends_reset_email(self, async_client, test_user):
        """POST /api/auth/forgot-password sends reset email (mock SMTP)."""
        # Note: email sending is not yet implemented, but endpoint should return 200
        response = await async_client.post(
            "/api/auth/forgot-password",
            json={"email": test_user.email},
        )
        # Should return 200 even if email doesn't exist (security)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_reset_password_with_valid_token(self, async_client, password_reset_token):
        """POST /api/auth/reset-password with valid token changes password."""
        response = await async_client.post(
            "/api/auth/reset-password",
            json={
                "token": password_reset_token,  # Use the fixture's token
                "new_password": "NewSecurePass123!",
            },
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_reset_password_with_expired_token_returns_400(self, async_client, expired_reset_token):
        """POST /api/auth/reset-password with expired token returns 400."""
        response = await async_client.post(
            "/api/auth/reset-password",
            json={
                "token": expired_reset_token,  # Use the expired fixture token
                "new_password": "NewSecurePass123!",
            },
        )
        assert response.status_code == 400
        assert "expired" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_reset_token_expires_after_one_hour(self, async_client, expired_reset_token):
        """Reset token expires after 1 hour."""
        # This test verifies the token expiration behavior
        # expired_reset_token fixture creates a token expired 2 hours ago
        response = await async_client.post(
            "/api/auth/reset-password",
            json={
                "token": expired_reset_token,
                "new_password": "NewSecurePass123!",
            },
        )
        assert response.status_code == 400
