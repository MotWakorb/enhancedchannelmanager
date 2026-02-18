"""
Integration tests for middleware (CORS, request timing, validation error handler).

Tests: Verify CORS headers, request timing middleware, validation error handling.
"""
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
        """Requests should pass through timing middleware without error."""
        response = await async_client.get("/api/health")
        assert response.status_code == 200

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
