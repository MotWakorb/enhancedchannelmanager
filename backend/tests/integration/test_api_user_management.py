"""
Integration tests for User Management API endpoints.

TDD SPEC: These tests define expected user management behavior.
They will FAIL initially - implementation makes them pass.

Test Spec: User Management (v6dxf.8.4)
"""
import pytest
from datetime import datetime, timedelta


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

    @pytest.mark.asyncio
    async def test_forgot_password_send_failure_never_logs_the_raw_token(
        self, async_client, test_user, caplog, monkeypatch,
    ):
        """The email-failure branch must not log the live reset token (cb1e1).

        The token minted by /api/auth/forgot-password is a working credential:
        whoever reads it can reset that account's password. An email outage is
        an ordinary operational failure, and ECM logs get pasted into GitHub
        issues, so the failure branch may record the failure and the user id
        and nothing else.

        The assertion is a negative over the log records the request actually
        emitted, not over the arguments of the logging call. The positive
        user_id assertion at the end is what keeps it from passing vacuously:
        if the branch never ran, or caplog captured nothing, that one fails.
        """
        import logging

        import auth.routes as auth_routes

        minted = {}

        def fail_to_send(to_email, reset_token, base_url):
            minted["token"] = reset_token
            return False

        monkeypatch.setattr(auth_routes, "send_password_reset_email", fail_to_send)

        with caplog.at_level(logging.DEBUG):
            response = await async_client.post(
                "/api/auth/forgot-password",
                json={"email": test_user.email},
            )

        assert response.status_code == 200
        # The endpoint really did mint a token and really did take the failure
        # branch, so there was a live credential available to leak.
        assert len(minted.get("token", "")) > 20

        emitted = " ".join(
            f"{record.getMessage()} {record.msg!r} {record.args!r}"
            for record in caplog.records
        )
        assert minted["token"] not in emitted
        # The operator still learns that the send failed, and for whom.
        assert f"user_id={test_user.id}" in emitted

    @pytest.mark.asyncio
    async def test_forgot_password_success_never_logs_the_account_email(
        self, async_client, test_session, caplog, monkeypatch,
    ):
        """A successful reset must not write the subscriber's address (5u5h9).

        Bead cb1e1 moved the FAILURE branch of this flow to a ``user_id=``
        shape and deliberately left the success branch alone, because at the
        time the same event was ALSO logged inside
        ``send_password_reset_email``: changing one while the other still named
        the address would have made the pair more confusing, not less. So a
        successful reset wrote the address into the log twice, in the same log
        operators paste into GitHub issues, and it made one send read as two to
        anyone counting the line.

        The whole flow runs for real, down to ``smtplib.SMTP.sendmail``, so the
        helper's own logging is exercised rather than mocked away: patching
        ``send_password_reset_email`` would have proved only the handler's half
        and left the duplicate line invisible. The assertion is a negative over
        every record the request emitted, and the positive ``user_id``
        assertions after it are what keep it from passing vacuously.
        """
        import logging
        from unittest.mock import MagicMock, patch

        from config import DispatcharrSettings
        from models import User
        from auth.password import hash_password

        # Distinctive enough that a leak anywhere in the log is findable, and
        # unlike ``test_user``'s address it cannot be emitted by anything else.
        subscriber_email = "subscriber-5u5h9-distinctive@example.com"
        user = User(
            username="5u5h9-subscriber",
            email=subscriber_email,
            password_hash=hash_password("<synthetic-5u5h9-user-password>"),
            auth_provider="local",
            is_admin=False,
            is_active=True,
        )
        test_session.add(user)
        test_session.commit()
        test_session.refresh(user)

        smtp_settings = DispatcharrSettings(
            smtp_host="smtp.5u5h9-mail.example.com",
            smtp_from_email="ecm@example.com",
        )
        monkeypatch.setattr(
            "auth.routes.get_settings", lambda: smtp_settings, raising=True
        )

        sent = {}
        server = MagicMock()
        server.sendmail.side_effect = lambda *args: sent.update(delivered=True)

        with patch("auth.routes.smtplib.SMTP", return_value=server), \
                caplog.at_level(logging.DEBUG):
            response = await async_client.post(
                "/api/auth/forgot-password",
                json={"email": subscriber_email},
            )

        assert response.status_code == 200
        # The send really succeeded, so the success branch really ran and there
        # was an address available to leak.
        assert sent.get("delivered") is True

        emitted = [
            f"{record.getMessage()} {record.msg!r} {record.args!r}"
            for record in caplog.records
        ]
        joined = " ".join(emitted)
        assert subscriber_email not in joined
        # One line for one event, not two, so the log is not also a wrong count.
        success_lines = [
            line for line in emitted if "Password reset email sent" in line
        ]
        assert len(success_lines) == 1, success_lines
        assert f"user_id={user.id}" in success_lines[0]
