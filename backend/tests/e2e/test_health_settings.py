"""
E2E tests for health, cache, and settings endpoints.

Endpoints: /api/health, /api/cache/*, /api/settings/*
"""
from tests.e2e.conftest import skip_if_not_api


class TestHealth:
    """Tests for GET /api/health."""

    def test_health_returns_200(self, e2e_client):
        """Health endpoint returns 200."""
        response = e2e_client.get("/api/health")
        assert response.status_code == 200

    def test_health_response_shape(self, e2e_client):
        """Health response has expected structure."""
        data = e2e_client.get("/api/health").json()
        assert "status" in data
        assert data["status"] == "healthy"

    def test_health_has_version(self, e2e_client):
        """Health response includes version."""
        data = e2e_client.get("/api/health").json()
        assert "version" in data


class TestCache:
    """Tests for /api/cache/* endpoints."""

    def test_cache_stats(self, e2e_client):
        """GET /api/cache/stats returns cache statistics with the documented shape."""
        response = e2e_client.get("/api/cache/stats")
        skip_if_not_api(response)
        assert response.status_code == 200
        data = response.json()
        # The endpoint is named/documented as returning cache statistics — inspect
        # the body rather than only the status code. These counters/shape fields
        # are the contract the frontend cache panel reads.
        assert isinstance(data["entry_count"], int)
        assert isinstance(data["hits"], int)
        assert isinstance(data["misses"], int)
        assert "hit_rate_percent" in data
        assert isinstance(data["entries"], list)

    def test_cache_invalidate(self, e2e_client):
        """POST /api/cache/invalidate clears the cache."""
        response = e2e_client.post("/api/cache/invalidate")
        skip_if_not_api(response)
        assert response.status_code == 200


class TestSettings:
    """Tests for /api/settings endpoints."""

    def test_get_settings(self, e2e_client):
        """GET /api/settings returns current settings."""
        response = e2e_client.get("/api/settings")
        assert response.status_code == 200
        data = response.json()
        assert "configured" in data
        assert "url" in data

    def test_settings_consistent(self, e2e_client):
        """Reading settings twice returns consistent data."""
        r1 = e2e_client.get("/api/settings")
        r2 = e2e_client.get("/api/settings")
        assert r1.json()["configured"] == r2.json()["configured"]
