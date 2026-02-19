"""
Unit tests for health and cache endpoints.

Tests: GET /api/health, POST /api/cache/invalidate, GET /api/cache/stats
Mocks: get_cache() to isolate from real cache state.
"""
import pytest
from unittest.mock import MagicMock, patch


class TestHealthCheck:
    """Tests for GET /api/health endpoint."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, async_client):
        """GET /api/health returns 200 with healthy status."""
        response = await async_client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "enhanced-channel-manager"

    @pytest.mark.asyncio
    async def test_health_includes_version_fields(self, async_client):
        """GET /api/health returns version, release_channel, git_commit."""
        response = await async_client.get("/api/health")
        data = response.json()
        assert "version" in data
        assert "release_channel" in data
        assert "git_commit" in data

    @pytest.mark.asyncio
    async def test_health_reads_env_vars(self, async_client):
        """GET /api/health reads version info from environment variables."""
        with patch.dict("os.environ", {
            "ECM_VERSION": "1.2.3",
            "RELEASE_CHANNEL": "beta",
            "GIT_COMMIT": "abc123",
        }):
            response = await async_client.get("/api/health")
            data = response.json()
            assert data["version"] == "1.2.3"
            assert data["release_channel"] == "beta"
            assert data["git_commit"] == "abc123"


class TestCacheStats:
    """Tests for GET /api/cache/stats endpoint."""

    @pytest.mark.asyncio
    async def test_cache_stats_returns_200(self, async_client):
        """GET /api/cache/stats returns 200."""
        response = await async_client.get("/api/cache/stats")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_cache_stats_returns_expected_fields(self, async_client):
        """GET /api/cache/stats returns entry_count, hits, misses, hit_rate_percent, entries."""
        with patch("routers.health.get_cache") as mock_get_cache:
            mock_cache = MagicMock()
            mock_cache.stats.return_value = {
                "entry_count": 5,
                "hits": 10,
                "misses": 3,
                "hit_rate_percent": 76.9,
                "entries": [],
            }
            mock_get_cache.return_value = mock_cache

            response = await async_client.get("/api/cache/stats")
            assert response.status_code == 200
            data = response.json()
            assert data["entry_count"] == 5
            assert data["hits"] == 10
            assert data["misses"] == 3
            assert data["hit_rate_percent"] == 76.9
            assert data["entries"] == []


class TestCacheInvalidate:
    """Tests for POST /api/cache/invalidate endpoint."""

    @pytest.mark.asyncio
    async def test_invalidate_all(self, async_client):
        """POST /api/cache/invalidate with no prefix clears entire cache."""
        with patch("routers.health.get_cache") as mock_get_cache:
            mock_cache = MagicMock()
            mock_cache.clear.return_value = 7
            mock_get_cache.return_value = mock_cache

            response = await async_client.post("/api/cache/invalidate")
            assert response.status_code == 200
            data = response.json()
            assert "7" in data["message"]
            mock_cache.clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalidate_with_prefix(self, async_client):
        """POST /api/cache/invalidate?prefix=streams invalidates matching keys."""
        with patch("routers.health.get_cache") as mock_get_cache:
            mock_cache = MagicMock()
            mock_cache.invalidate_prefix.return_value = 3
            mock_get_cache.return_value = mock_cache

            response = await async_client.post(
                "/api/cache/invalidate",
                params={"prefix": "streams"},
            )
            assert response.status_code == 200
            data = response.json()
            assert "3" in data["message"]
            assert "streams" in data["message"]
            mock_cache.invalidate_prefix.assert_called_once_with("streams")
