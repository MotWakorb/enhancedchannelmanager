"""
Integration tests for Authentication API endpoints.

TDD SPEC: These tests define expected auth behavior.
They will FAIL initially - implementation makes them pass.

Test Spec: Core Auth Flow (v6dxf.8.1)
"""
import pytest


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
        password_hash=hash_password("validpassword123"),
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
    """Create a regular user for testing."""
    from models import User
    from auth.password import hash_password

    user = User(
        username="testuser",
        email="test@example.com",
        password_hash=hash_password("testpass123"),
        auth_provider="local",
        is_admin=False,
        is_active=True,
    )
    test_session.add(user)
    test_session.commit()
    test_session.refresh(user)
    return user


class TestLoginFlow:
    """Tests for POST /api/auth/login endpoint."""

    @pytest.mark.asyncio
    async def test_login_with_valid_credentials_returns_200(self, async_client, admin_user):
        """POST /api/auth/login with valid credentials returns 200 + user data."""
        response = await async_client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "validpassword123",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "user" in data
        assert "username" in data["user"]
        assert data["user"]["username"] == "admin"

    @pytest.mark.asyncio
    async def test_login_sets_jwt_cookie(self, async_client, admin_user):
        """POST /api/auth/login sets JWT access token in httpOnly cookie."""
        response = await async_client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "validpassword123",
            },
        )
        assert response.status_code == 200
        # Check for access_token cookie
        assert "access_token" in response.cookies

    @pytest.mark.asyncio
    async def test_login_with_invalid_credentials_returns_401(self, async_client):
        """POST /api/auth/login with invalid credentials returns 401."""
        response = await async_client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "wrongpassword",
            },
        )
        assert response.status_code == 401
        data = response.json()
        assert "detail" in data

    @pytest.mark.asyncio
    async def test_login_with_nonexistent_user_returns_401(self, async_client):
        """POST /api/auth/login with nonexistent user returns 401."""
        response = await async_client.post(
            "/api/auth/login",
            json={
                "username": "nonexistent",
                "password": "anypassword",
            },
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_login_with_missing_username_returns_422(self, async_client):
        """POST /api/auth/login with missing username returns 422."""
        response = await async_client.post(
            "/api/auth/login",
            json={
                "password": "somepassword",
            },
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_login_with_missing_password_returns_422(self, async_client):
        """POST /api/auth/login with missing password returns 422."""
        response = await async_client.post(
            "/api/auth/login",
            json={
                "username": "admin",
            },
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_login_with_empty_body_returns_422(self, async_client):
        """POST /api/auth/login with empty body returns 422."""
        response = await async_client.post(
            "/api/auth/login",
            json={},
        )
        assert response.status_code == 422


class TestSessionManagement:
    """Tests for session management endpoints."""

    @pytest.mark.asyncio
    async def test_me_with_valid_token_returns_user(self, async_client, admin_user):
        """GET /api/auth/me with valid token returns current user."""
        # First login to get a valid token
        login_response = await async_client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "validpassword123",
            },
        )
        assert login_response.status_code == 200

        # Use the cookie from login
        response = await async_client.get("/api/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert "user" in data
        assert "username" in data["user"]

    @pytest.mark.asyncio
    async def test_me_without_token_returns_401(self, async_client):
        """GET /api/auth/me without token returns 401."""
        response = await async_client.get("/api/auth/me")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_me_with_invalid_token_returns_401(self, async_client):
        """GET /api/auth/me with invalid token returns 401."""
        response = await async_client.get(
            "/api/auth/me",
            cookies={"access_token": "invalid.jwt.token"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_me_with_expired_token_returns_401(self, async_client):
        """GET /api/auth/me with expired token returns 401."""
        # Create an expired JWT token for testing
        expired_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbiIsImV4cCI6MH0.invalid"
        response = await async_client.get(
            "/api/auth/me",
            cookies={"access_token": expired_token},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_with_valid_token_returns_new_access_token(self, async_client, admin_user):
        """POST /api/auth/refresh with valid refresh token returns new access token."""
        # First login to get tokens
        login_response = await async_client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "validpassword123",
            },
        )
        assert login_response.status_code == 200

        # Refresh the token
        response = await async_client.post("/api/auth/refresh")
        assert response.status_code == 200
        # New access token should be set
        assert "access_token" in response.cookies

    @pytest.mark.asyncio
    async def test_refresh_without_token_returns_401(self, async_client):
        """POST /api/auth/refresh without token returns 401."""
        response = await async_client.post("/api/auth/refresh")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_clears_session(self, async_client, admin_user):
        """POST /api/auth/logout clears session cookie."""
        # First login
        login_response = await async_client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "validpassword123",
            },
        )
        assert login_response.status_code == 200

        # Logout
        response = await async_client.post("/api/auth/logout")
        assert response.status_code == 200

        # Verify session is cleared - me should fail
        me_response = await async_client.get("/api/auth/me")
        assert me_response.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_without_session_returns_200(self, async_client):
        """POST /api/auth/logout without active session still returns 200."""
        response = await async_client.post("/api/auth/logout")
        # Logout should succeed even without session (idempotent)
        assert response.status_code == 200


class TestAccessTokenExpiryMetadata:
    """bd-3ymo4: auth responses expose read-only access-token expiry metadata
    so the frontend can schedule a proactive refresh (kills the recurring
    30-minute 401 on background polls)."""

    @pytest.mark.asyncio
    async def test_login_reports_access_token_lifetime(self, async_client, admin_user):
        """POST /api/auth/login returns access_token_expires_in = full configured lifetime."""
        response = await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "validpassword123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token_expires_in" in data
        # Freshly minted token: full configured lifetime, a positive number
        assert isinstance(data["access_token_expires_in"], int)
        assert data["access_token_expires_in"] > 0

    @pytest.mark.asyncio
    async def test_me_reports_remaining_token_lifetime(self, async_client, admin_user):
        """GET /api/auth/me returns remaining seconds <= full lifetime."""
        login_response = await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "validpassword123"},
        )
        assert login_response.status_code == 200
        full_lifetime = login_response.json()["access_token_expires_in"]

        response = await async_client.get("/api/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert "access_token_expires_in" in data
        remaining = data["access_token_expires_in"]
        assert isinstance(remaining, int)
        assert 0 < remaining <= full_lifetime

    @pytest.mark.asyncio
    async def test_refresh_reports_access_token_lifetime(self, async_client, admin_user):
        """POST /api/auth/refresh returns access_token_expires_in for the new token."""
        login_response = await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "validpassword123"},
        )
        assert login_response.status_code == 200

        response = await async_client.post("/api/auth/refresh")
        assert response.status_code == 200
        data = response.json()
        assert "access_token_expires_in" in data
        assert isinstance(data["access_token_expires_in"], int)
        assert data["access_token_expires_in"] > 0


class TestRefreshedAccessTokenIdentity:
    """bd-suuoh: the access token minted by /auth/refresh identifies the
    account, not a ``user_<id>`` placeholder.

    ``rotate_refresh_token`` used to fabricate the ``username`` claim from the
    subject id because it never loaded the user. The refresh handler already
    has the real ``User`` row in hand, so it now passes the name through. The
    claim is not an authorization input (``get_current_user`` resolves the
    caller from ``sub``), but ``main.py``'s deprecated-admin-router warning
    logs it verbatim as the acting operator, so the placeholder misattributed
    every post-refresh request in that security log.
    """

    @pytest.mark.asyncio
    async def test_refreshed_access_token_carries_real_username(
        self, async_client, admin_user
    ):
        from auth.tokens import decode_token

        login_response = await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "validpassword123"},
        )
        assert login_response.status_code == 200

        response = await async_client.post("/api/auth/refresh")
        assert response.status_code == 200

        refreshed = async_client.cookies.get("access_token")
        assert decode_token(refreshed)["username"] == "admin"

    @pytest.mark.asyncio
    async def test_graced_refresh_also_carries_real_username(
        self, async_client, admin_user, test_session
    ):
        """The rotation-grace path mints its own access token too."""
        from auth.tokens import decode_token

        login_response = await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "validpassword123"},
        )
        assert login_response.status_code == 200
        pre_rotation = async_client.cookies.get("refresh_token")

        assert (await async_client.post("/api/auth/refresh")).status_code == 200

        # Replay the immediately-prior token: answered inside the grace window.
        async_client.cookies.set("refresh_token", pre_rotation)
        graced = await async_client.post("/api/auth/refresh")
        assert graced.status_code == 200

        refreshed = async_client.cookies.get("access_token")
        assert decode_token(refreshed)["username"] == "admin"


class TestRefreshRotationGraceWindow:
    """bd-x67qe: server-side rotation grace window for the cross-tab refresh
    race.

    /auth/refresh rotates the refresh token one-time-use. Two tabs of one
    session crossing the access-token expiry boundary can both POST
    /auth/refresh with the same pre-rotation cookie; without a grace window
    the loser gets 'Session not found or revoked' and hard-logs-out the tab.

    Grace semantics under test:
    - the immediately-prior token is accepted for a short window after
      rotation and answered idempotently (fresh access token, SAME session,
      NO second rotation, NO refresh cookie)
    - the grace window never extends total session lifetime
    - the graced token cannot chain (only one generation is kept)
    - revocation (logout) kills current AND graced tokens immediately
    - the window itself expires
    """

    async def _login(self, async_client):
        """Login and return the refresh token the server set."""
        response = await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "validpassword123"},
        )
        assert response.status_code == 200
        return response.cookies["refresh_token"]

    async def _refresh_with(self, async_client, refresh_token):
        """POST /api/auth/refresh presenting exactly the given token."""
        # Clear the jar so the explicit cookie is the only one sent —
        # otherwise the jar's rotated cookie would mask the token under test.
        async_client.cookies.clear()
        return await async_client.post(
            "/api/auth/refresh",
            cookies={"refresh_token": refresh_token},
        )

    def _session_row(self, test_session, user_id):
        from models import UserSession

        test_session.expire_all()
        return (
            test_session.query(UserSession)
            .filter(UserSession.user_id == user_id)
            .one()
        )

    @pytest.mark.asyncio
    async def test_concurrent_double_refresh_both_succeed_one_session(
        self, async_client, admin_user, test_session
    ):
        """Two racing refreshes with the same pre-rotation token BOTH get
        200, and exactly one valid (non-revoked) session remains."""
        import asyncio
        from models import UserSession

        old_token = await self._login(async_client)
        async_client.cookies.clear()

        first, second = await asyncio.gather(
            async_client.post(
                "/api/auth/refresh", cookies={"refresh_token": old_token}
            ),
            async_client.post(
                "/api/auth/refresh", cookies={"refresh_token": old_token}
            ),
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text

        # Exactly ONE rotation happened: one response carries a new refresh
        # cookie (the winner), the graced loser gets an access token only —
        # it must NOT push a stale refresh token back into the shared jar.
        refresh_cookie_count = sum(
            1 for r in (first, second) if "refresh_token" in r.cookies
        )
        assert refresh_cookie_count == 1
        assert all("access_token" in r.cookies for r in (first, second))

        test_session.expire_all()
        live_sessions = (
            test_session.query(UserSession)
            .filter(
                UserSession.user_id == admin_user.id,
                UserSession.is_revoked == False,  # noqa: E712
            )
            .all()
        )
        assert len(live_sessions) == 1

    @pytest.mark.asyncio
    async def test_winner_token_still_works_after_graced_refresh(
        self, async_client, admin_user
    ):
        """A graced (loser) refresh must not invalidate the winner's chain."""
        old_token = await self._login(async_client)

        winner = await self._refresh_with(async_client, old_token)
        assert winner.status_code == 200
        winner_token = winner.cookies["refresh_token"]

        loser = await self._refresh_with(async_client, old_token)
        assert loser.status_code == 200

        follow_up = await self._refresh_with(async_client, winner_token)
        assert follow_up.status_code == 200
        assert "refresh_token" in follow_up.cookies

    @pytest.mark.asyncio
    async def test_graced_token_fails_after_window_expiry(
        self, async_client, admin_user, test_session
    ):
        """The prior token is only honored INSIDE the grace window."""
        from datetime import datetime, timedelta

        old_token = await self._login(async_client)
        response = await self._refresh_with(async_client, old_token)
        assert response.status_code == 200

        # Age the rotation far past the grace window.
        row = self._session_row(test_session, admin_user.id)
        row.rotated_at = datetime.utcnow() - timedelta(seconds=300)
        test_session.commit()

        expired_grace = await self._refresh_with(async_client, old_token)
        assert expired_grace.status_code == 401

    @pytest.mark.asyncio
    async def test_graced_token_cannot_chain(self, async_client, admin_user):
        """Grace covers only the LATEST predecessor — one generation deep.
        After a second rotation, the oldest token must fail even though it
        was graced a moment ago."""
        gen0 = await self._login(async_client)

        first = await self._refresh_with(async_client, gen0)
        assert first.status_code == 200
        gen1 = first.cookies["refresh_token"]

        second = await self._refresh_with(async_client, gen1)
        assert second.status_code == 200

        chained = await self._refresh_with(async_client, gen0)
        assert chained.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_revokes_current_and_graced_tokens_immediately(
        self, async_client, admin_user
    ):
        """Logout kills the session for BOTH the rotated token and its
        graced predecessor — revocation always beats grace."""
        old_token = await self._login(async_client)
        rotated = await self._refresh_with(async_client, old_token)
        assert rotated.status_code == 200
        current_token = rotated.cookies["refresh_token"]

        async_client.cookies.clear()
        logout = await async_client.post(
            "/api/auth/logout", cookies={"refresh_token": current_token}
        )
        assert logout.status_code == 200

        graced_after_logout = await self._refresh_with(async_client, old_token)
        assert graced_after_logout.status_code == 401

        current_after_logout = await self._refresh_with(
            async_client, current_token
        )
        assert current_after_logout.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_with_graced_token_also_revokes_session(
        self, async_client, admin_user, test_session
    ):
        """Logout presented with the graced PREDECESSOR still revokes the
        session — a stale tab logging out must not leave the session live."""
        old_token = await self._login(async_client)
        rotated = await self._refresh_with(async_client, old_token)
        assert rotated.status_code == 200
        current_token = rotated.cookies["refresh_token"]

        async_client.cookies.clear()
        logout = await async_client.post(
            "/api/auth/logout", cookies={"refresh_token": old_token}
        )
        assert logout.status_code == 200

        current_after_logout = await self._refresh_with(
            async_client, current_token
        )
        assert current_after_logout.status_code == 401

    @pytest.mark.asyncio
    async def test_grace_respects_session_expiry(
        self, async_client, admin_user, test_session
    ):
        """An expired session is not resurrected by the grace window."""
        from datetime import datetime, timedelta

        old_token = await self._login(async_client)
        response = await self._refresh_with(async_client, old_token)
        assert response.status_code == 200

        row = self._session_row(test_session, admin_user.id)
        row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        test_session.commit()

        graced = await self._refresh_with(async_client, old_token)
        assert graced.status_code == 401

    @pytest.mark.asyncio
    async def test_graced_refresh_does_not_extend_session_lifetime(
        self, async_client, admin_user, test_session
    ):
        """The grace path answers idempotently: no second rotation, and
        expires_at (total session lifetime) is untouched."""
        old_token = await self._login(async_client)
        response = await self._refresh_with(async_client, old_token)
        assert response.status_code == 200

        row = self._session_row(test_session, admin_user.id)
        expires_before = row.expires_at
        hash_before = row.refresh_token_hash

        graced = await self._refresh_with(async_client, old_token)
        assert graced.status_code == 200

        row = self._session_row(test_session, admin_user.id)
        assert row.expires_at == expires_before
        assert row.refresh_token_hash == hash_before


class TestProtectedEndpoints:
    """Tests for authentication on protected endpoints."""

    @pytest.mark.asyncio
    async def test_health_endpoint_is_public(self, async_client):
        """GET /api/health does not require authentication.

        Distinct from the middleware tests: this assertion verifies the *auth* property
        — the endpoint is accessible without credentials, not merely that it returns 200.
        """
        response = await async_client.get("/api/health")
        assert response.status_code == 200
        # Auth-specific check: the health endpoint must not redirect to login
        # or return 401/403 even when no credentials are provided.
        assert response.status_code not in (401, 403)

    @pytest.mark.asyncio
    async def test_auth_login_is_public(self, async_client):
        """POST /api/auth/login is reachable without credentials and returns 401 for invalid creds.

        This endpoint must NOT require prior authentication (it would be impossible to log in).
        The nonexistent user "test" makes the result deterministic: always 401, not 403.
        Mutation check: if the endpoint required prior auth and returned 403, this would fail.
        """
        response = await async_client.post(
            "/api/auth/login",
            json={"username": "test", "password": "test"},
        )
        # 401 = invalid credentials (user not found) — not 403 (auth required before login)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_settings_endpoint_is_public_in_setup_mode(self, async_client):
        """GET /api/settings returns 200 when auth is in setup mode (no users configured).

        The settings endpoint uses RequireAdminIfEnabled which bypasses auth when
        setup_complete=False (the test environment always starts in setup mode).
        Making this test assert == 401 deterministically would require seeding a
        fully-configured auth state and enabling require_auth — that is large scaffolding
        not warranted here. This test instead asserts the known setup-mode behavior:
        the endpoint is publicly accessible. A separate test (test_invalid_token_on_protected_endpoint_returns_401)
        uses /api/auth/me which always validates tokens.

        WHAT THIS TEST DOES NOT PROVE, and why that used to be a blind spot
        (bead enhancedchannelmanager-ne2yy). It says nothing about whether
        /api/settings is protected when auth IS on — the enforcement that would
        answer that is the exempt-set membership check in ``main.auth_middleware``,
        and that set is now pinned by
        ``tests/test_auth_exempt_paths_snapshot.py``. Read the two together:
        this one records the setup-mode behaviour, that one records which paths
        are allowed to skip the gate at all.

        NOR is 200-in-setup-mode a general rule any more (bead
        enhancedchannelmanager-jy006). Three identity primitives — POST
        /api/backup/restore-initial, POST/DELETE /api/settings/mcp-api-key, and
        the /api/tls certificate and key material — refuse an anonymous caller
        even in this mode once the instance has an operator identity. This
        endpoint is deliberately NOT one of them: /api/settings staying open
        under ``require_auth: false`` is the documented half of that decision,
        not an accident. See ``docs/auth_middleware.md`` →
        "What ``require_auth: false`` permits".
        """
        response = await async_client.get("/api/settings")
        assert response.status_code == 200


    @pytest.mark.asyncio
    async def test_invalid_token_on_protected_endpoint_returns_401(self, async_client):
        """Protected endpoints that always validate tokens return 401 for an invalid JWT.

        /api/auth/me uses get_current_user directly (not RequireAuthIfEnabled) so it
        always validates the token regardless of auth setup state — making the outcome
        deterministic even in the test environment's setup mode.
        Mutation check: if get_current_user stopped rejecting malformed JWTs this would fail.
        """
        response = await async_client.get(
            "/api/auth/me",
            cookies={"access_token": "invalid.token.here"},
        )
        assert response.status_code == 401
