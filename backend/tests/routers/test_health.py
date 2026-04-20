"""
Unit tests for health and cache endpoints.

Tests: GET /api/health, GET /api/health/ready, POST /api/cache/invalidate,
GET /api/cache/stats
Mocks: get_cache() to isolate from real cache state; subsystem probes for
readiness are mocked at the router module level.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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


class TestSchemaVersionEndpoint:
    """Tests for GET /api/health/schema (bd-c5wf5)."""

    @pytest.mark.asyncio
    async def test_schema_endpoint_returns_200(self, async_client):
        """The endpoint is reachable and returns 200."""
        response = await async_client.get("/api/health/schema")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_schema_endpoint_reports_head_revision(self, async_client):
        """head_revision reflects the Alembic head declared in versions/."""
        import database

        response = await async_client.get("/api/health/schema")
        data = response.json()
        assert data["head_revision"] == database.get_alembic_head_revision()
        assert isinstance(data["head_revision"], str) and data["head_revision"]

    @pytest.mark.asyncio
    async def test_schema_endpoint_reports_foreign_keys(self, async_client):
        """foreign_keys_enabled must be True so FK constraints are enforced."""
        response = await async_client.get("/api/health/schema")
        data = response.json()
        # test engine uses SQLite + database.py's connect listener → FK=ON
        assert data["foreign_keys_enabled"] is True

    @pytest.mark.asyncio
    async def test_schema_endpoint_includes_journal_mode(self, async_client):
        """journal_mode is surfaced so ops can verify WAL / delete state."""
        response = await async_client.get("/api/health/schema")
        data = response.json()
        assert "journal_mode" in data
        assert data["journal_mode"] in {"delete", "wal", "memory", "truncate", "persist", "off"}


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


# ============================================================================
# Readiness tests — GET /api/health/ready
# ============================================================================
@pytest.fixture(autouse=True)
def _reset_readiness_state():
    """Reset the readiness module globals between tests to prevent bleed-through.

    The Dispatcharr cache is module-level state that persists across tests,
    and the ``_last_ready_state`` tracker similarly survives. Both need to be
    pristine for each test or ordering becomes load-bearing.
    """
    from routers import health as health_module
    health_module._reset_dispatcharr_cache()
    health_module._last_ready_state = None
    yield
    health_module._reset_dispatcharr_cache()
    health_module._last_ready_state = None


def _mk_dispatcharr_client_mock(*, response_status: int = 200, raise_exc=None):
    """Build a mock that mirrors the DispatcharrClient's inner httpx client.

    The readiness probe calls ``client._client.get(base_url)``, so we need a
    mock with a ``base_url`` attribute and an async ``_client.get``.
    """
    mock_client = MagicMock()
    mock_client.base_url = "http://dispatcharr.example:9191"
    inner = MagicMock()
    if raise_exc is not None:
        inner.get = AsyncMock(side_effect=raise_exc)
    else:
        mock_response = MagicMock()
        mock_response.status_code = response_status
        inner.get = AsyncMock(return_value=mock_response)
    mock_client._client = inner
    return mock_client


class TestReadiness:
    """Tests for GET /api/health/ready endpoint."""

    @pytest.mark.asyncio
    async def test_ready_returns_200_when_all_ok(self, async_client):
        """Ready endpoint returns 200 with status=ready when all subsystems OK."""
        mock_settings = MagicMock()
        mock_settings.url = "http://dispatcharr.example:9191"
        mock_client = _mk_dispatcharr_client_mock(response_status=200)

        with patch("routers.health.get_settings", return_value=mock_settings), \
             patch("routers.health.get_client", return_value=mock_client), \
             patch("routers.health.shutil.which", return_value="/usr/bin/ffprobe"):
            response = await async_client.get("/api/health/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["checks"]["database"]["status"] == "ok"
        assert data["checks"]["dispatcharr"]["status"] == "ok"
        assert data["checks"]["ffprobe"]["status"] == "ok"
        assert data["checks"]["ffprobe"]["detail"] == "/usr/bin/ffprobe"

    @pytest.mark.asyncio
    async def test_ready_returns_503_when_db_fails(self, async_client):
        """Ready endpoint returns 503 when the DB check fails."""
        mock_settings = MagicMock()
        mock_settings.url = ""  # dispatcharr skipped
        mock_session = MagicMock()
        mock_session.execute.side_effect = RuntimeError("database is locked")

        with patch("routers.health.get_session", return_value=mock_session), \
             patch("routers.health.get_settings", return_value=mock_settings), \
             patch("routers.health.shutil.which", return_value="/usr/bin/ffprobe"):
            response = await async_client.get("/api/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["database"]["status"] == "fail"
        assert "database is locked" in data["checks"]["database"]["detail"]

    @pytest.mark.asyncio
    async def test_ready_returns_503_when_dispatcharr_times_out(self, async_client):
        """Ready endpoint returns 503 when Dispatcharr ping times out."""
        mock_settings = MagicMock()
        mock_settings.url = "http://dispatcharr.example:9191"
        mock_client = _mk_dispatcharr_client_mock(
            raise_exc=asyncio.TimeoutError("timed out"),
        )

        with patch("routers.health.get_settings", return_value=mock_settings), \
             patch("routers.health.get_client", return_value=mock_client), \
             patch("routers.health.shutil.which", return_value="/usr/bin/ffprobe"):
            response = await async_client.get("/api/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["dispatcharr"]["status"] == "fail"
        assert "timeout" in data["checks"]["dispatcharr"]["detail"]

    @pytest.mark.asyncio
    async def test_ready_skips_dispatcharr_when_not_configured(self, async_client):
        """When no Dispatcharr URL is set, dispatcharr status is 'skipped' and overall is ready."""
        mock_settings = MagicMock()
        mock_settings.url = ""

        with patch("routers.health.get_settings", return_value=mock_settings), \
             patch("routers.health.shutil.which", return_value="/usr/bin/ffprobe"):
            response = await async_client.get("/api/health/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["checks"]["dispatcharr"]["status"] == "skipped"
        assert data["checks"]["dispatcharr"]["detail"] == "not configured"

    @pytest.mark.asyncio
    async def test_ready_returns_503_when_ffprobe_missing(self, async_client):
        """Ready endpoint returns 503 when ffprobe is not on PATH."""
        mock_settings = MagicMock()
        mock_settings.url = ""

        with patch("routers.health.get_settings", return_value=mock_settings), \
             patch("routers.health.shutil.which", return_value=None):
            response = await async_client.get("/api/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["checks"]["ffprobe"]["status"] == "fail"

    @pytest.mark.asyncio
    async def test_dispatcharr_check_is_cached(self, async_client):
        """Two rapid readiness calls should only ping Dispatcharr once (30s cache)."""
        mock_settings = MagicMock()
        mock_settings.url = "http://dispatcharr.example:9191"
        mock_client = _mk_dispatcharr_client_mock(response_status=200)

        with patch("routers.health.get_settings", return_value=mock_settings), \
             patch("routers.health.get_client", return_value=mock_client), \
             patch("routers.health.shutil.which", return_value="/usr/bin/ffprobe"):
            r1 = await async_client.get("/api/health/ready")
            r2 = await async_client.get("/api/health/ready")

        assert r1.status_code == 200
        assert r2.status_code == 200
        # Only the first call should have actually pinged Dispatcharr.
        assert mock_client._client.get.await_count == 1
