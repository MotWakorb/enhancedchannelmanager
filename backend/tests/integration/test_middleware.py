"""
Integration tests for middleware (CORS, request timing, validation error handler).

Tests: Verify CORS headers, request timing middleware, validation error handling.
"""
import logging

import pytest


class TestCORSMiddleware:
    """Verify CORS headers on responses."""

    @pytest.mark.asyncio
    async def test_cors_allows_localhost_5173(self, async_client):
        """CORS should allow requests from localhost:5173."""
        response = await async_client.get(
            "/api/health",
            headers={"Origin": "http://localhost:5173"},
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"

    @pytest.mark.asyncio
    async def test_cors_allows_localhost_3000(self, async_client):
        """CORS should allow requests from localhost:3000."""
        response = await async_client.get(
            "/api/health",
            headers={"Origin": "http://localhost:3000"},
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"

    @pytest.mark.asyncio
    async def test_cors_denies_unknown_origin(self, async_client):
        """CORS should not set allow-origin for unknown origins."""
        response = await async_client.get(
            "/api/health",
            headers={"Origin": "http://evil.example.com"},
        )
        assert response.status_code == 200
        # Should NOT have the evil origin in allow-origin
        allow_origin = response.headers.get("access-control-allow-origin")
        assert allow_origin != "http://evil.example.com"

    @pytest.mark.asyncio
    async def test_cors_preflight_returns_methods(self, async_client):
        """CORS preflight should return allowed methods."""
        response = await async_client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.status_code == 200
        allowed = response.headers.get("access-control-allow-methods", "")
        assert "POST" in allowed or "*" in allowed

    @pytest.mark.asyncio
    async def test_cors_allows_credentials(self, async_client):
        """CORS should allow credentials."""
        response = await async_client.get(
            "/api/health",
            headers={"Origin": "http://localhost:5173"},
        )
        assert response.headers.get("access-control-allow-credentials") == "true"


class TestRequestTimingMiddleware:
    """Verify request timing middleware behavior."""

    @pytest.mark.asyncio
    async def test_requests_succeed_through_middleware(self, async_client):
        """Timing middleware is transparent: it must not corrupt or swallow responses.

        Distinct from the auth and timeout tests: this assertion verifies the
        *timing-middleware* property — the middleware passes the handler's response
        through unchanged (status code and body intact), without altering the
        response or raising an unhandled exception.
        """
        response = await async_client.get("/api/health")
        assert response.status_code == 200
        # Timing-middleware-specific check: the handler's response body must
        # arrive intact — the middleware must not consume or corrupt it.
        data = response.json()
        assert data["status"] == "healthy"
        assert "service" in data

    @pytest.mark.asyncio
    async def test_multiple_requests_succeed(self, async_client):
        """Multiple sequential requests should all succeed."""
        for _ in range(5):
            response = await async_client.get("/api/health")
            assert response.status_code == 200


class TestValidationErrorHandler:
    """Verify the custom validation error handler."""

    @pytest.mark.asyncio
    async def test_invalid_json_returns_422(self, async_client):
        """Sending invalid JSON should trigger validation error handler."""
        response = await async_client.post(
            "/api/settings",
            content=b"not valid json",
            headers={"Content-Type": "application/json"},
        )
        # Should get 422 (validation error) or 400 (bad request)
        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_wrong_type_returns_error(self, async_client):
        """Sending wrong types should trigger an error response."""
        # Use notifications endpoint which does its own validation
        response = await async_client.post(
            "/api/notifications",
            json={"message": "", "notification_type": "invalid_type"},
        )
        # Should get a client error (400 from endpoint validation)
        assert response.status_code == 400


class TestDeprecatedAdminRouterLogging:
    """Verify the bd-d53lz deprecation logging on every /api/admin/* hit.

    ``auth.admin_routes`` (mounted at ``/api/admin``) fully duplicates the
    canonical ``/api/auth/admin/*`` user-CRUD surface the frontend actually
    calls. Step 1 of the deprecation is a WARNING-level log on every hit —
    including ones the route's own auth dependency rejects with 401/403 —
    so a follow-on bead can confirm a zero-traffic window before deleting
    the router.
    """

    @staticmethod
    def _deprecation_records(caplog):
        return [
            r for r in caplog.records
            if "DEPRECATED-ADMIN-ROUTER" in r.getMessage()
        ]

    @pytest.mark.asyncio
    async def test_logs_warning_on_unauthenticated_hit(self, async_client, caplog):
        """An unauthenticated hit still gets exactly one deprecation record.

        This is the case the bead cares about most: the middleware must fire
        even though ``get_current_active_admin`` rejects the request with
        401 before the route body ever runs — that 401 IS the unreached
        attack-surface traffic this log exists to catch.
        """
        with caplog.at_level(logging.WARNING):
            response = await async_client.get("/api/admin/users")

        assert response.status_code == 401
        records = self._deprecation_records(caplog)
        assert len(records) == 1
        message = records[0].getMessage()
        assert "GET" in message
        assert "/api/admin/users" in message
        # No credentials/token leak — only method, path, IP, and username.
        assert "Bearer" not in message

    @pytest.mark.asyncio
    async def test_logs_warning_with_resolved_username_on_authenticated_hit(
        self, async_client, test_session, caplog,
    ):
        """An authenticated hit resolves and logs the real username.

        Builds a genuine admin user + JWT (not a dependency override) so the
        middleware's own independent token decode — it runs at the ASGI
        layer, outside FastAPI's dependency graph — is exercised end to end.
        """
        from auth.password import hash_password
        from auth.tokens import create_access_token
        from models import User

        admin = User(
            username="dep-log-admin",
            email="dep-log-admin@example.com",
            password_hash=hash_password("Sup3r$ecure!Passw0rd"),
            is_admin=True,
            is_active=True,
            auth_provider="local",
        )
        test_session.add(admin)
        test_session.commit()
        test_session.refresh(admin)

        token = create_access_token(admin.id, admin.username)

        with caplog.at_level(logging.WARNING):
            response = await async_client.get(
                "/api/admin/users",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 200
        records = self._deprecation_records(caplog)
        assert len(records) == 1
        message = records[0].getMessage()
        assert "GET" in message
        assert "/api/admin/users" in message
        assert admin.username in message
        assert token not in message

    @pytest.mark.asyncio
    async def test_does_not_log_for_unrelated_paths(self, async_client, caplog):
        """A hit on an unrelated route must not emit the deprecation record."""
        with caplog.at_level(logging.WARNING):
            response = await async_client.get("/api/health")

        assert response.status_code == 200
        assert self._deprecation_records(caplog) == []
