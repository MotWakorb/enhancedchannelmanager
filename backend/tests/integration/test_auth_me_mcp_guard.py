"""
Integration tests: self-mutation auth routes reject the transient MCP principal.

bd-1wq7z.24 (c): The static MCP key authenticates an admin-equivalent service
principal that is a TRANSIENT, non-persisted ``User`` (``auth_provider="mcp"``,
synthetic negative id). The global ``auth_middleware`` accepts it for any
``/api/*`` path, so it can reach the self-mutation routes ``PUT /api/auth/me``
and ``POST /api/auth/change-password``. Those routes mutate ``current_user`` and
call ``session.refresh(current_user)`` / ``session.commit()`` — on a transient
principal that raises an opaque 500.

GUARD: ``reject_mcp_service_principal_mutation`` rejects the MCP principal with a
clean 403 BEFORE any ORM op. A real authenticated user is unaffected.

These tests assert:
- MCP principal -> 403 (not 500) on ``PUT /api/auth/me``.
- MCP principal -> 403 (not 500) on ``POST /api/auth/change-password``.
- A real logged-in user can still update their profile (200) — no regression.
"""
import pytest

from auth import dependencies as deps


MCP_KEY = "mcp-static-secret-key-self-mutation-guard-123456"


@pytest.fixture
def mcp_key_configured(monkeypatch):
    """Configure a non-empty static MCP key on the auth-dependency settings."""
    class _Settings:
        mcp_api_key = MCP_KEY

    monkeypatch.setattr(deps, "get_settings", lambda: _Settings(), raising=False)
    return MCP_KEY


@pytest.fixture
def real_user(test_session):
    """A real, persisted local user with a known password."""
    from models import User
    from auth.password import hash_password

    user = User(
        username="realuser",
        email="realuser@example.com",
        password_hash=hash_password("validpassword123"),
        auth_provider="local",
        is_admin=False,
        is_active=True,
    )
    test_session.add(user)
    test_session.commit()
    test_session.refresh(user)
    return user


class TestMcpPrincipalSelfMutationRejected:
    """The transient MCP principal gets a clean 403 on self-mutation routes."""

    @pytest.mark.asyncio
    async def test_put_me_with_mcp_key_returns_403(
        self, async_client, mcp_key_configured
    ):
        """PUT /api/auth/me with the MCP key -> 403, never a 500."""
        response = await async_client.put(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {MCP_KEY}"},
            json={"display_name": "should-not-apply"},
        )
        assert response.status_code == 403
        assert response.status_code != 500
        assert "MCP service principal" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_change_password_with_mcp_key_returns_403(
        self, async_client, mcp_key_configured
    ):
        """POST /api/auth/change-password with the MCP key -> 403, never 500."""
        response = await async_client.post(
            "/api/auth/change-password",
            headers={"Authorization": f"Bearer {MCP_KEY}"},
            json={
                "current_password": "irrelevant",
                "new_password": "AnotherValidPass123!",
            },
        )
        assert response.status_code == 403
        assert response.status_code != 500
        assert "MCP service principal" in response.json()["detail"]


class TestRealUserSelfMutationStillWorks:
    """A real authenticated user keeps full self-mutation access (no regression)."""

    @pytest.mark.asyncio
    async def test_put_me_real_user_succeeds(
        self, async_client, mcp_key_configured, real_user
    ):
        """A logged-in real user can still update their own profile (200)."""
        login = await async_client.post(
            "/api/auth/login",
            json={"username": "realuser", "password": "validpassword123"},
        )
        assert login.status_code == 200

        response = await async_client.put(
            "/api/auth/me",
            json={"display_name": "New Display Name"},
        )
        assert response.status_code == 200
        assert response.json()["user"]["display_name"] == "New Display Name"

    @pytest.mark.asyncio
    async def test_change_password_real_user_succeeds(
        self, async_client, mcp_key_configured, real_user
    ):
        """A logged-in real user can still change their own password (200)."""
        login = await async_client.post(
            "/api/auth/login",
            json={"username": "realuser", "password": "validpassword123"},
        )
        assert login.status_code == 200

        response = await async_client.post(
            "/api/auth/change-password",
            json={
                "current_password": "validpassword123",
                "new_password": "BrandNewValidPass456!",
            },
        )
        assert response.status_code == 200
