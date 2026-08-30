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
    async def test_predecessor_refresh_also_carries_real_username(
        self, async_client, admin_user, test_session
    ):
        """The predecessor path mints its own access token too.

        Property 5. The rotation is aged well past the deleted 10-second
        window so this exercises rotation confirmation, not the old grace.
        """
        from datetime import datetime, timedelta
        from auth.tokens import decode_token
        from models import UserSession

        login_response = await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "validpassword123"},
        )
        assert login_response.status_code == 200
        pre_rotation = async_client.cookies.get("refresh_token")

        assert (await async_client.post("/api/auth/refresh")).status_code == 200

        test_session.expire_all()
        row = (
            test_session.query(UserSession)
            .filter(UserSession.user_id == admin_user.id)
            .one()
        )
        row.rotated_at = datetime.utcnow() - timedelta(hours=6)
        test_session.commit()

        # Replay the immediately-prior token: its successor was never used.
        async_client.cookies.set("refresh_token", pre_rotation)
        from_predecessor = await async_client.post("/api/auth/refresh")
        assert from_predecessor.status_code == 200

        refreshed = async_client.cookies.get("access_token")
        assert decode_token(refreshed)["username"] == "admin"


class TestRefreshRotationConfirmation:
    """bead upkp1 (replacing bd-x67qe's 10-second wall-clock grace window):
    the immediately-prior refresh token stays acceptable until its successor
    is actually used.

    /auth/refresh rotates the refresh token one-time-use. The wall-clock
    window this suite used to pin stranded any client whose rotated response
    never arrived: a tab that navigated mid-flight, an aborted request, or
    browser automation, all of which lose the response while the server has
    already rotated. Ten seconds later that client was locked out with no
    non-interactive way back.

    Rotation confirmation replaces the clock with the row itself. Accepting a
    successor overwrites ``prior_refresh_token_hash``, which is precisely
    when the predecessor stops matching. So:

    - the predecessor is accepted for as long as its successor goes unused,
      and answered idempotently (fresh access token, SAME session, NO second
      rotation, NO refresh cookie)
    - it never extends total session lifetime, which is the outer bound that
      replaces the window
    - it cannot chain: exactly one generation is retained
    - revocation (logout) kills the current token and its predecessor at once
    - a predecessor whose successor HAS been used is refused

    Tests that need to prove independence from the deleted window age
    ``rotated_at`` into the past, which is exactly the condition the removed
    code compared against. One test (the QA reproduction) sleeps for real.
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

    def _age_rotation(self, test_session, user_id, seconds):
        """Push ``rotated_at`` into the past.

        This is the wall-clock condition the fix deletes, so aging the row is
        equivalent to waiting that long for every assertion about it, without
        the test sleeping. ``expires_at`` is deliberately left alone: it is a
        separate bound and still applies.
        """
        from datetime import datetime, timedelta

        row = self._session_row(test_session, user_id)
        row.rotated_at = datetime.utcnow() - timedelta(seconds=seconds)
        test_session.commit()
        return row

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
        # cookie (the winner), the loser is answered from the predecessor and
        # gets an access token only. It must NOT push a superseded refresh
        # token back into the shared jar.
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
    async def test_winner_token_still_works_after_a_predecessor_answer(
        self, async_client, admin_user
    ):
        """A predecessor answer must not invalidate the successor's chain."""
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
    async def test_qa_reproduction_succeeds_well_past_the_deleted_window(
        self, async_client, admin_user
    ):
        """Property 1, the acceptance test for bead upkp1.

        This is QA's reproduction verbatim: login, capture R1, refresh with
        R1 and DISCARD the response (which is what a navigation or an aborted
        request does to a browser), then retry R1 at t+1s and again well
        past the ten seconds the deleted window allowed. Both retries must
        now return 200; the second one used to return 401 'Session not found
        or revoked', and the delay was the only variable between them.

        The sleep is real, not an aged row, precisely because a fix that
        merely widened the constant would still pass an aged-row test.
        """
        import asyncio

        r1 = await self._login(async_client)

        discarded = await self._refresh_with(async_client, r1)
        assert discarded.status_code == 200  # the client never sees this

        early_retry = await self._refresh_with(async_client, r1)
        assert early_retry.status_code == 200, early_retry.text

        await asyncio.sleep(13)

        late_retry = await self._refresh_with(async_client, r1)
        assert late_retry.status_code == 200, late_retry.text
        assert "refresh_token" not in late_retry.cookies

    @pytest.mark.asyncio
    async def test_predecessor_accepted_after_an_arbitrarily_long_wait(
        self, async_client, admin_user, test_session
    ):
        """Property 2: no wall-clock bound remains on predecessor acceptance.

        Thirty days of age on the rotation, far beyond any window anyone
        would configure, and the answer is still 200 because the successor
        was never used. The session's own ``expires_at`` is untouched and
        still applies (property 9 pins that half).
        """
        r1 = await self._login(async_client)
        assert (await self._refresh_with(async_client, r1)).status_code == 200

        self._age_rotation(test_session, admin_user.id, 30 * 24 * 3600)

        response = await self._refresh_with(async_client, r1)
        assert response.status_code == 200, response.text

    @pytest.mark.asyncio
    async def test_predecessor_answer_carries_an_access_cookie_only(
        self, async_client, admin_user, test_session
    ):
        """Property 3: access-token cookie, and no refresh cookie at all.

        Issuing a refresh cookie here would fork the chain: the server does
        not hold the plaintext successor, so it could only mint a NEW one and
        strand whoever holds the real successor.
        """
        r1 = await self._login(async_client)
        assert (await self._refresh_with(async_client, r1)).status_code == 200
        self._age_rotation(test_session, admin_user.id, 3600)

        response = await self._refresh_with(async_client, r1)
        assert response.status_code == 200, response.text
        assert "access_token" in response.cookies
        assert "refresh_token" not in response.cookies

    @pytest.mark.asyncio
    async def test_predecessor_refused_once_the_successor_has_been_used(
        self, async_client, admin_user
    ):
        """Property 6, the security assertion of the set.

        The predecessor's whole lease is 'until the successor is used'. Use
        it: rotate R1 to R2 and then R2 to R3, and R1 must be refused. The
        second rotation is what overwrites R1's hash out of the row, which is
        the mechanism, not a window elapsing.
        """
        r1 = await self._login(async_client)

        first = await self._refresh_with(async_client, r1)
        assert first.status_code == 200
        r2 = first.cookies["refresh_token"]

        second = await self._refresh_with(async_client, r2)
        assert second.status_code == 200
        assert "refresh_token" in second.cookies

        replayed = await self._refresh_with(async_client, r1)
        assert replayed.status_code == 401

    @pytest.mark.asyncio
    async def test_predecessor_cannot_chain(self, async_client, admin_user):
        """Property 7: exactly one generation is retained.

        After a second rotation the oldest token is refused. The MECHANISM is
        the point: gen0 fails because gen1's use overwrote
        ``prior_refresh_token_hash``, not because any window elapsed. Under
        the deleted rule this test could pass for the wrong reason, since it
        runs inside ten seconds.
        """
        gen0 = await self._login(async_client)

        first = await self._refresh_with(async_client, gen0)
        assert first.status_code == 200
        gen1 = first.cookies["refresh_token"]

        second = await self._refresh_with(async_client, gen1)
        assert second.status_code == 200

        chained = await self._refresh_with(async_client, gen0)
        assert chained.status_code == 401

    @pytest.mark.asyncio
    async def test_predecessor_survives_its_own_jti_blacklisting(
        self, async_client, admin_user, test_session
    ):
        """Property 16: the ``ignore_revocation=True`` fallback is the normal
        path now, and this test fails if anyone tidies it away.

        Rotation adds the presented jti to the in-process blacklist, so the
        predecessor's jti is revoked in ``auth.tokens`` the instant its
        successor is minted. The test asserts that membership directly, so
        the acceptance below can only be reached through the fallback, and
        the C-5 regression cannot hide behind a decode that happened to
        succeed.
        """
        from auth.tokens import _revoked_tokens, decode_token

        r1 = await self._login(async_client)
        jti = decode_token(r1, ignore_revocation=True)["jti"]

        assert (await self._refresh_with(async_client, r1)).status_code == 200
        assert jti in _revoked_tokens, (
            "precondition: rotation must blacklist the presented jti, "
            "otherwise this test proves nothing about the fallback"
        )

        self._age_rotation(test_session, admin_user.id, 7200)

        response = await self._refresh_with(async_client, r1)
        assert response.status_code == 200, response.text

    @pytest.mark.asyncio
    async def test_repeated_predecessor_answers_are_idempotent(
        self, async_client, admin_user, test_session
    ):
        """Property 21: presenting the predecessor N times accumulates no
        state. Same single row, same hash pair, same rotation timestamp."""
        from models import UserSession

        r1 = await self._login(async_client)
        assert (await self._refresh_with(async_client, r1)).status_code == 200
        self._age_rotation(test_session, admin_user.id, 3600)

        row = self._session_row(test_session, admin_user.id)
        before = (
            row.refresh_token_hash,
            row.prior_refresh_token_hash,
            row.rotated_at,
            row.expires_at,
        )

        for attempt in range(5):
            response = await self._refresh_with(async_client, r1)
            assert response.status_code == 200, f"attempt {attempt}: {response.text}"
            assert "refresh_token" not in response.cookies

        row = self._session_row(test_session, admin_user.id)
        assert (
            row.refresh_token_hash,
            row.prior_refresh_token_hash,
            row.rotated_at,
            row.expires_at,
        ) == before

        test_session.expire_all()
        assert (
            test_session.query(UserSession)
            .filter(UserSession.user_id == admin_user.id)
            .count()
            == 1
        )

    @pytest.mark.asyncio
    async def test_predecessor_answers_over_time_leave_the_successor_chain_intact(
        self, async_client, admin_user, test_session
    ):
        """Property 15: any number of predecessor answers, spread over time,
        leave the successor able to keep rotating normally."""
        r1 = await self._login(async_client)

        winner = await self._refresh_with(async_client, r1)
        assert winner.status_code == 200
        r2 = winner.cookies["refresh_token"]

        for elapsed in (60, 3600, 86400):
            self._age_rotation(test_session, admin_user.id, elapsed)
            answer = await self._refresh_with(async_client, r1)
            assert answer.status_code == 200, f"at {elapsed}s: {answer.text}"
            assert "refresh_token" not in answer.cookies

        follow_up = await self._refresh_with(async_client, r2)
        assert follow_up.status_code == 200
        assert "refresh_token" in follow_up.cookies

    @pytest.mark.asyncio
    async def test_second_refresh_long_after_the_first_still_gets_an_answer(
        self, async_client, admin_user, test_session
    ):
        """Property 14, long-separation variant of the concurrency case.

        The two requests that share a pre-rotation token no longer have to be
        simultaneous: separated by an hour, the second still gets 200 with an
        access cookie only, and exactly one live session row remains.
        """
        from models import UserSession

        r1 = await self._login(async_client)

        first = await self._refresh_with(async_client, r1)
        assert first.status_code == 200
        assert "refresh_token" in first.cookies

        self._age_rotation(test_session, admin_user.id, 3600)

        second = await self._refresh_with(async_client, r1)
        assert second.status_code == 200, second.text
        assert "access_token" in second.cookies
        assert "refresh_token" not in second.cookies

        test_session.expire_all()
        live = (
            test_session.query(UserSession)
            .filter(
                UserSession.user_id == admin_user.id,
                UserSession.is_revoked == False,  # noqa: E712
            )
            .all()
        )
        assert len(live) == 1

    @pytest.mark.asyncio
    async def test_predecessor_of_a_different_user_is_refused(
        self, async_client, admin_user, regular_user, test_session
    ):
        """Property 11: the predecessor lookup is filtered by ``user_id``, so
        a token whose subject is another account cannot match a session row
        even when the stored hash does."""
        from datetime import datetime
        from auth.tokens import create_refresh_token, hash_token

        await self._login(async_client)

        other_users_token = create_refresh_token(user_id=regular_user.id)
        row = self._session_row(test_session, admin_user.id)
        row.prior_refresh_token_hash = hash_token(other_users_token)
        row.rotated_at = datetime.utcnow()
        test_session.commit()

        response = await self._refresh_with(async_client, other_users_token)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_predecessor_of_a_deactivated_user_is_refused(
        self, async_client, admin_user, test_session
    ):
        """Property 12: deactivating the account refuses the predecessor,
        however long its successor stays unused.

        The positive control matters: without it this test would pass under a
        build that refuses the aged predecessor for the wrong reason.
        """
        r1 = await self._login(async_client)
        assert (await self._refresh_with(async_client, r1)).status_code == 200
        self._age_rotation(test_session, admin_user.id, 3600)

        while_active = await self._refresh_with(async_client, r1)
        assert while_active.status_code == 200, while_active.text

        admin_user.is_active = False
        test_session.commit()

        response = await self._refresh_with(async_client, r1)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_revokes_current_and_predecessor_tokens_immediately(
        self, async_client, admin_user
    ):
        """Property 8: logout kills the session for BOTH the current token
        and its predecessor. Revocation always wins, and it is what bounds a
        predecessor that now outlives any window: one row is the whole chain,
        so revoking it refuses both tokens together."""
        old_token = await self._login(async_client)
        rotated = await self._refresh_with(async_client, old_token)
        assert rotated.status_code == 200
        current_token = rotated.cookies["refresh_token"]

        async_client.cookies.clear()
        logout = await async_client.post(
            "/api/auth/logout", cookies={"refresh_token": current_token}
        )
        assert logout.status_code == 200

        predecessor_after_logout = await self._refresh_with(
            async_client, old_token
        )
        assert predecessor_after_logout.status_code == 401

        current_after_logout = await self._refresh_with(
            async_client, current_token
        )
        assert current_after_logout.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_with_the_predecessor_token_also_revokes_session(
        self, async_client, admin_user, test_session
    ):
        """Logout presented with the PREDECESSOR still revokes the session:
        a stale tab logging out must not leave the session live."""
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
    async def test_predecessor_refused_when_the_session_has_expired(
        self, async_client, admin_user, test_session
    ):
        """Property 9: an expired session is not resurrected by presenting
        the predecessor. This is half of the outer bound that replaces the
        deleted window, so the positive control below is part of the claim:
        the same aged predecessor is accepted right up until ``expires_at``
        passes, and refused after."""
        from datetime import datetime, timedelta

        old_token = await self._login(async_client)
        response = await self._refresh_with(async_client, old_token)
        assert response.status_code == 200
        self._age_rotation(test_session, admin_user.id, 3600)

        while_live = await self._refresh_with(async_client, old_token)
        assert while_live.status_code == 200, while_live.text

        row = self._session_row(test_session, admin_user.id)
        row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        test_session.commit()

        refused = await self._refresh_with(async_client, old_token)
        assert refused.status_code == 401

    @pytest.mark.asyncio
    async def test_predecessor_refresh_does_not_extend_session_lifetime(
        self, async_client, admin_user, test_session
    ):
        """Property 4: the predecessor answer mutates nothing that decides
        acceptance. ``refresh_token_hash``, ``prior_refresh_token_hash``,
        ``rotated_at`` and ``expires_at`` all come back identical.

        ``expires_at`` staying put is what guarantees a client living on
        predecessor answers is eventually sent back through interactive
        login: rotation slides the session forward, this path never does.
        """
        old_token = await self._login(async_client)
        response = await self._refresh_with(async_client, old_token)
        assert response.status_code == 200
        self._age_rotation(test_session, admin_user.id, 3600)

        row = self._session_row(test_session, admin_user.id)
        before = (
            row.refresh_token_hash,
            row.prior_refresh_token_hash,
            row.rotated_at,
            row.expires_at,
        )
        last_used_before = row.last_used_at

        answered = await self._refresh_with(async_client, old_token)
        assert answered.status_code == 200

        row = self._session_row(test_session, admin_user.id)
        assert (
            row.refresh_token_hash,
            row.prior_refresh_token_hash,
            row.rotated_at,
            row.expires_at,
        ) == before
        # last_used_at is the one field this path is allowed to advance.
        assert row.last_used_at >= last_used_before

    def _freeze_now(self, monkeypatch, instant):
        """Pin ``datetime.utcnow()`` as the auth router sees it.

        The expiry boundary is one microsecond wide, so it is not observable
        against a running clock. The token's own ``exp`` is decided in
        ``auth.tokens`` against the real clock and is deliberately left
        alone, so these tests turn on the session row and nothing else.
        """
        from datetime import datetime as real_datetime
        from auth import routes as auth_routes

        class _FrozenDatetime(real_datetime):
            @classmethod
            def utcnow(cls):
                return instant

        monkeypatch.setattr(auth_routes, "datetime", _FrozenDatetime)

    @pytest.mark.asyncio
    async def test_a_session_expiring_exactly_now_is_refused_as_current(
        self, async_client, admin_user, test_session, monkeypatch
    ):
        """The session is live while ``expires_at > now``, so the expiry
        instant itself is outside it and the current token is refused there.

        The handler compared ``expires_at < now``, which accepts the
        boundary. Nothing observable turned on it against a real clock, but
        the predicate is what the rule is written in, and the two refresh
        paths have to agree on it.
        """
        from datetime import timedelta

        current = await self._login(async_client)
        instant = self._session_row(test_session, admin_user.id).expires_at

        self._freeze_now(monkeypatch, instant)
        refused = await self._refresh_with(async_client, current)
        assert refused.status_code == 401

        # Positive control: one microsecond earlier the same request is fine,
        # so the 401 above is the boundary and not a broken fixture.
        self._freeze_now(monkeypatch, instant - timedelta(microseconds=1))
        allowed = await self._refresh_with(async_client, current)
        assert allowed.status_code == 200, allowed.text

    @pytest.mark.asyncio
    async def test_a_session_expiring_exactly_now_is_refused_as_predecessor(
        self, async_client, admin_user, test_session, monkeypatch
    ):
        """The same boundary on the predecessor path, which is the one that
        now matters: this path can be reached for the session's whole life
        rather than for ten seconds after a rotation."""
        from datetime import timedelta

        old_token = await self._login(async_client)
        assert (await self._refresh_with(async_client, old_token)).status_code == 200
        self._age_rotation(test_session, admin_user.id, 3600)
        instant = self._session_row(test_session, admin_user.id).expires_at

        # Positive control first: the answer is available right up to the
        # boundary, and this path mutates nothing that the 401 below needs.
        self._freeze_now(monkeypatch, instant - timedelta(microseconds=1))
        allowed = await self._refresh_with(async_client, old_token)
        assert allowed.status_code == 200, allowed.text

        self._freeze_now(monkeypatch, instant)
        refused = await self._refresh_with(async_client, old_token)
        assert refused.status_code == 401


class TestRefreshCryptographicGate:
    """bead upkp1: signature and expiry are decided BEFORE any session lookup,
    and rotation confirmation does not soften that.

    The predecessor now lives for the session's lifetime rather than ten
    seconds, so the checks that run before the row is ever consulted are
    carrying more weight than they used to. These tests assert both the 401
    and the absence of a ``user_sessions`` query, because 'refused' and
    'refused without touching the database' are different claims.
    """

    @staticmethod
    def _refresh_token_with_exp(user_id, delta):
        """A signed refresh token whose ``exp`` is ``delta`` from now."""
        import secrets
        from datetime import datetime, timedelta
        import jwt
        from auth.tokens import ALGORITHM, _get_secret_key

        now = datetime.utcnow()
        return jwt.encode(
            {
                "sub": str(user_id),
                "type": "refresh",
                "exp": now + delta,
                "iat": now - timedelta(minutes=1),
                "jti": secrets.token_urlsafe(16),
            },
            _get_secret_key(),
            algorithm=ALGORITHM,
        )

    @staticmethod
    def _session_statements(test_engine):
        """Context manager collecting SQL that touches ``user_sessions``."""
        import contextlib
        from sqlalchemy import event

        @contextlib.contextmanager
        def _collect():
            seen = []

            def _record(conn, cursor, statement, parameters, context, many):
                if "user_sessions" in statement.lower():
                    seen.append(statement)

            event.listen(test_engine, "before_cursor_execute", _record)
            try:
                yield seen
            finally:
                event.remove(test_engine, "before_cursor_execute", _record)

        return _collect()

    @pytest.mark.asyncio
    async def test_expired_refresh_token_refused_before_any_session_lookup(
        self, async_client, admin_user, test_engine
    ):
        """Property 10: the presented JWT's own ``exp`` is one of the two
        bounds that replace the deleted window, and it is enforced first."""
        from datetime import timedelta

        expired = self._refresh_token_with_exp(
            admin_user.id, timedelta(days=-1)
        )

        async_client.cookies.clear()
        with self._session_statements(test_engine) as statements:
            response = await async_client.post(
                "/api/auth/refresh", cookies={"refresh_token": expired}
            )

        assert response.status_code == 401
        assert statements == []

    @pytest.mark.asyncio
    async def test_tampered_signature_refused_before_any_session_lookup(
        self, async_client, admin_user, test_engine
    ):
        """Property 13."""
        login = await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "validpassword123"},
        )
        assert login.status_code == 200
        tampered = login.cookies["refresh_token"][:-6] + "AAAAAA"

        async_client.cookies.clear()
        with self._session_statements(test_engine) as statements:
            response = await async_client.post(
                "/api/auth/refresh", cookies={"refresh_token": tampered}
            )

        assert response.status_code == 401
        assert statements == []

    @pytest.mark.asyncio
    async def test_token_signed_with_a_foreign_key_refused_at_the_signature(
        self, async_client, admin_user, test_engine
    ):
        """Property 20: a well-formed, unexpired refresh token minted by a
        different signing key never reaches the session lookup."""
        import secrets
        from datetime import datetime, timedelta
        import jwt
        from auth.tokens import ALGORITHM

        now = datetime.utcnow()
        foreign = jwt.encode(
            {
                "sub": str(admin_user.id),
                "type": "refresh",
                "exp": now + timedelta(days=7),
                "iat": now,
                "jti": secrets.token_urlsafe(16),
            },
            "<synthetic-signing-key-that-is-not-the-configured-one>",
            algorithm=ALGORITHM,
        )

        async_client.cookies.clear()
        with self._session_statements(test_engine) as statements:
            response = await async_client.post(
                "/api/auth/refresh", cookies={"refresh_token": foreign}
            )

        assert response.status_code == 401
        assert statements == []


class TestRefreshFailureLogging:
    """bead yhk3r: /api/auth/refresh used to refuse in complete silence.

    38 hours of container logs held zero refresh lines of any kind, success
    or failure, which is the direct reason bead upkp1 had to be diagnosed
    from a browser's error body rather than from the server that produced the
    401. This matters more under rotation confirmation than it did before:
    the design deliberately trades an automated response to a replayed token
    (there is none: unknown credentials are refused and logged, never
    revoked) for the ability to SEE one, and that visibility is these lines.
    """

    LOGGER = "auth.routes"

    @pytest.fixture(autouse=True)
    def _clear_denial_dedupe(self):
        """Refusal logging is deduped per (reason, client) in module-global
        state, so a test asserting on it must not inherit another test's."""
        from auth import routes as auth_routes

        auth_routes._reset_refresh_denial_log_state()
        yield
        auth_routes._reset_refresh_denial_log_state()

    def _warnings(self, caplog):
        import logging

        return [
            r
            for r in caplog.records
            if r.name == self.LOGGER and r.levelno == logging.WARNING
        ]

    @pytest.mark.asyncio
    async def test_a_refusal_emits_exactly_one_warning_naming_reason_and_client(
        self, async_client, caplog
    ):
        """Property 17."""
        import logging

        async_client.cookies.clear()
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            response = await async_client.post("/api/auth/refresh")

        assert response.status_code == 401
        warnings = self._warnings(caplog)
        assert len(warnings) == 1, [r.getMessage() for r in warnings]
        message = warnings[0].getMessage()
        assert "no_credential" in message
        assert "client=127.0.0.1" in message

    @pytest.mark.asyncio
    async def test_unknown_credential_refusal_has_its_own_message(
        self, async_client, admin_user, caplog
    ):
        """Property 18: valid signature but no matching session gets a
        message of its own, distinct from 'no token' and from a malformed
        one, because it is the only replay-shaped signal this server can
        emit."""
        import logging
        from auth.tokens import create_refresh_token

        orphan = create_refresh_token(user_id=admin_user.id)

        async_client.cookies.clear()
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            unknown = await async_client.post(
                "/api/auth/refresh", cookies={"refresh_token": orphan}
            )
        assert unknown.status_code == 401
        unknown_messages = [r.getMessage() for r in self._warnings(caplog)]
        assert len(unknown_messages) == 1
        assert "unknown_session" in unknown_messages[0]
        assert "matches no live session" in unknown_messages[0]

        caplog.clear()
        async_client.cookies.clear()
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            malformed = await async_client.post(
                "/api/auth/refresh",
                cookies={"refresh_token": "not.a.jwt"},
            )
        assert malformed.status_code == 401
        malformed_messages = [r.getMessage() for r in self._warnings(caplog)]
        assert len(malformed_messages) == 1
        assert "unknown_session" not in malformed_messages[0]
        assert "matches no live session" not in malformed_messages[0]

    @pytest.mark.asyncio
    async def test_repeated_refusals_are_deduped_and_counted_not_dropped(
        self, async_client, caplog
    ):
        """L-5: this endpoint is unauthenticated and unrate-limited, so one
        WARNING per refusal is a disk-fill vector. Repeats inside the
        interval are counted rather than emitted, and the count rides on the
        next line that is emitted."""
        import logging
        from auth import routes as auth_routes

        async_client.cookies.clear()
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            for _ in range(5):
                assert (
                    await async_client.post("/api/auth/refresh")
                ).status_code == 401

        assert len(self._warnings(caplog)) == 1
        assert "skipped_since_last=0" in self._warnings(caplog)[0].getMessage()

        # Roll the interval over and the skipped four are reported, not lost.
        state = auth_routes._refresh_denial_log_state
        key = next(iter(state))
        state[key] = (
            state[key][0] - auth_routes._REFRESH_DENIAL_LOG_INTERVAL_SECONDS - 1,
            state[key][1],
        )
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            assert (
                await async_client.post("/api/auth/refresh")
            ).status_code == 401

        assert "skipped_since_last=4" in self._warnings(caplog)[0].getMessage()

    @pytest.mark.asyncio
    async def test_no_refresh_log_line_carries_a_token_or_its_hash(
        self, async_client, admin_user, caplog
    ):
        """Property 19, asserted as a negative over every captured record.

        Covers all three outcomes in one run: a successful rotation, a
        predecessor answer, and a refusal.

        Every request here also puts the secrets in the two log fields the
        CALLER controls, which is the hole the first version of this test
        missed: it only ever placed secrets in the cookie, so it proved that
        ECM does not log the values it chose to read and proved nothing about
        the values someone else chose to send. ``X-Forwarded-For`` was logged
        verbatim and unbounded, and the sha256 hex digest used below is 64
        characters, so it fits inside the 80-character ``User-Agent`` cut
        whole. Both are asserted on every outcome, not only on the refusal,
        because a caller does not get to pick which branch their request
        takes.
        """
        import logging
        from auth.tokens import hash_token

        login = await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "validpassword123"},
        )
        assert login.status_code == 200
        r1 = login.cookies["refresh_token"]
        # Chosen so the header carries the secret in the shape that actually
        # fits: the digest, not the (much longer) token.
        smuggled = {
            "X-Forwarded-For": hash_token(r1),
            "User-Agent": hash_token(r1),
        }

        with caplog.at_level(logging.DEBUG):
            async_client.cookies.clear()
            rotated = await async_client.post(
                "/api/auth/refresh",
                cookies={"refresh_token": r1},
                headers=smuggled,
            )
            assert rotated.status_code == 200
            r2 = rotated.cookies["refresh_token"]

            async_client.cookies.clear()
            answered = await async_client.post(
                "/api/auth/refresh",
                cookies={"refresh_token": r1},
                headers=smuggled,
            )
            assert answered.status_code == 200

            async_client.cookies.clear()
            refused = await async_client.post(
                "/api/auth/refresh",
                cookies={"refresh_token": "not.a.jwt"},
                headers={
                    "X-Forwarded-For": hash_token(r2),
                    "User-Agent": hash_token(r2),
                },
            )
            assert refused.status_code == 401

        messages = [r.getMessage() for r in caplog.records]
        # Non-vacuous: the refresh path did log during this block.
        assert any("[AUTH] Refresh" in m or "Token refreshed" in m for m in messages)

        for secret_value in (r1, r2, hash_token(r1), hash_token(r2)):
            for message in messages:
                assert secret_value not in message

    @pytest.mark.asyncio
    async def test_a_forged_forwarded_address_is_not_echoed_into_the_log(
        self, async_client, caplog
    ):
        """L-4 over the address field specifically.

        The refusal line's ``client=`` value must be an address ECM rendered
        or the socket peer, never a substring of the header. A header that is
        not an address is discarded rather than trimmed, so the fallback
        value appears instead — which also means no caller-chosen text can
        reach the line to forge a second one.
        """
        import logging

        async_client.cookies.clear()
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            refused = await async_client.post(
                "/api/auth/refresh",
                headers={"X-Forwarded-For": "not-an-address-" + "A" * 400},
            )

        assert refused.status_code == 401
        message = self._warnings(caplog)[0].getMessage()
        assert "not-an-address" not in message
        assert "AAAA" not in message
        assert "client=127.0.0.1" in message

    @pytest.mark.asyncio
    async def test_a_well_formed_forwarded_address_is_still_reported(
        self, async_client, caplog
    ):
        """The positive control for the test above.

        Discarding malformed values would be worthless if it also discarded
        real ones: the field exists so an operator behind a reverse proxy
        sees the LAN client rather than the proxy. Without this, a
        ``_client_address`` that always returned the peer would pass.
        """
        import logging

        async_client.cookies.clear()
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            refused = await async_client.post(
                "/api/auth/refresh",
                headers={"X-Forwarded-For": "192.168.4.31, 10.0.0.1"},
            )

        assert refused.status_code == 401
        assert "client=192.168.4.31" in self._warnings(caplog)[0].getMessage()


# A sha256 hex digest's shape (64 characters, hex alphabet) with none of its
# entropy: built from a repeated run per docs/pytest_conventions.md, so the
# secrets ratchet does not have to distinguish it from a real one.
_FAKE_DIGEST = "abcdef0123" * 6 + "abcd"


class TestAuthLogFieldSanitization:
    """The two auth-log fields the caller supplies, tested at the helper.

    The integration tests above prove the end-to-end behaviour but cannot
    reach every input: ``h11`` rejects a header value containing CR or LF
    before the application sees it, so the forged-log-line case is not
    expressible through a real request. Asserting it here means the guarantee
    does not rest on the HTTP parser in front of ECM continuing to reject
    that byte, and it covers the address forms a proxy actually emits.

    These helpers also feed the login-failure lines, which have read the same
    unchecked header since long before rotation confirmation.
    """

    @pytest.mark.parametrize(
        "header,expected",
        [
            ("192.168.4.31", "192.168.4.31"),
            ("  192.168.4.31  ", "192.168.4.31"),
            # A proxy that appends the source port, v4 and bracketed v6.
            ("192.168.4.31:41234", "192.168.4.31"),
            ("[2001:db8::1]:41234", "2001:db8::1"),
            ("[2001:db8::1]", "2001:db8::1"),
            # Bare IPv6 has more than one colon, so port-stripping must not
            # eat it.
            ("2001:db8::1", "2001:db8::1"),
            # Canonicalized from the parsed value, never echoed.
            ("2001:0db8:0000:0000:0000:0000:0000:0001", "2001:db8::1"),
        ],
    )
    def test_real_forwarded_addresses_survive(self, header, expected):
        from auth.routes import _forwarded_address

        assert _forwarded_address(header) == expected

    @pytest.mark.parametrize(
        "header",
        [
            "",
            "   ",
            "localhost",
            "192.168.4.999",
            # The finding: a credential's digest, and a whole JWT. Both are
            # assembled from patterned runs per docs/pytest_conventions.md so
            # they carry the shape without resembling a real secret.
            _FAKE_DIGEST,
            "eyJ" + "a" * 24 + "." + "b" * 24 + "." + "c" * 24,
            # Forged log line. Unreachable through h11, rejected here anyway.
            "192.168.4.31\nWARNING [AUTH] Login succeeded for admin",
            "192.168.4.31\r\n[AUTH] fabricated",
            # Unbounded padding, the disk-fill shape.
            "1" * 10000,
        ],
    )
    def test_anything_that_is_not_an_address_is_discarded(self, header):
        from auth.routes import _forwarded_address

        assert _forwarded_address(header) is None

    def test_a_user_agent_cannot_smuggle_a_digest_or_a_newline(self):
        from auth.routes import _log_safe_agent

        assert len(_FAKE_DIGEST) == 64  # the shape that fits inside the cap
        agent = _log_safe_agent(
            "Mozilla/5.0 " + _FAKE_DIGEST + "\nWARNING [AUTH] fabricated\r\x1b[2J"
        )

        assert _FAKE_DIGEST not in agent
        assert "[redacted]" in agent
        assert "\n" not in agent and "\r" not in agent and "\x1b" not in agent
        assert len(agent) <= 80

    def test_a_real_user_agent_survives_redaction(self):
        """Positive control: a helper that redacted everything would pass the
        test above and destroy the field's only reason to exist."""
        from auth.routes import _log_safe_agent

        real = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
        )
        agent = _log_safe_agent(real)

        assert "[redacted]" not in agent
        assert agent == real[:80]

    def test_a_missing_user_agent_is_empty_not_none(self):
        from auth.routes import _log_safe_agent

        assert _log_safe_agent(None) == ""
        assert _log_safe_agent("") == ""


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
