"""
Unit tests for stream-stats endpoints.

Tests: 19 endpoints covering stream probe stats, probe operations,
       dismiss/clear, struck-out streams, compute-sort, and probe lifecycle.
Mocks: StreamProber, get_prober(), get_client(), get_settings(), get_session().
"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from models import StreamStats


def _create_stream_stats(session, stream_id, **overrides):
    """Helper to create a StreamStats row."""
    defaults = {
        "stream_id": stream_id,
        "stream_name": f"Stream {stream_id}",
        "probe_status": "success",
        "consecutive_failures": 0,
        "created_at": datetime.utcnow(),
    }
    defaults.update(overrides)
    stats = StreamStats(**defaults)
    session.add(stats)
    session.commit()
    session.refresh(stats)
    return stats


class TestGetAllStreamStats:
    """Tests for GET /api/stream-stats."""

    @pytest.mark.asyncio
    async def test_returns_stats(self, async_client):
        """Returns all stream stats."""
        with patch("routers.stream_stats.StreamProber.get_all_stats", return_value={"streams": []}):
            response = await async_client.get("/api/stream-stats")

        assert response.status_code == 200
        assert response.json()["streams"] == []

    @pytest.mark.asyncio
    async def test_returns_500_on_error(self, async_client):
        """Returns 500 when StreamProber raises."""
        with patch("routers.stream_stats.StreamProber.get_all_stats", side_effect=Exception("DB error")):
            response = await async_client.get("/api/stream-stats")

        assert response.status_code == 500


class TestGetStreamStatsSummary:
    """Tests for GET /api/stream-stats/summary."""

    @pytest.mark.asyncio
    async def test_returns_summary(self, async_client):
        """Returns stats summary."""
        with patch("routers.stream_stats.StreamProber.get_stats_summary", return_value={
            "total": 100, "success": 80, "failed": 15, "pending": 5,
        }):
            response = await async_client.get("/api/stream-stats/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 100
        assert data["success"] == 80


class TestGetStruckOutStreams:
    """Tests for GET /api/stream-stats/struck-out."""

    @pytest.mark.asyncio
    async def test_disabled_when_threshold_zero(self, async_client):
        """Returns empty list and enabled=False when threshold is 0."""
        mock_settings = MagicMock()
        mock_settings.strike_threshold = 0

        with patch("routers.stream_stats.get_settings", return_value=mock_settings):
            response = await async_client.get("/api/stream-stats/struck-out")

        assert response.status_code == 200
        data = response.json()
        assert data["streams"] == []
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_returns_struck_out_with_channels(self, async_client, test_session):
        """Returns struck-out streams with channel info."""
        _create_stream_stats(test_session, 10, consecutive_failures=5)

        mock_settings = MagicMock()
        mock_settings.strike_threshold = 3

        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 1, "name": "ESPN", "streams": [10, 20]}],
            "count": 1,
        }

        with patch("routers.stream_stats.get_settings", return_value=mock_settings), \
             patch("routers.stream_stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-stats/struck-out")

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["threshold"] == 3
        assert len(data["streams"]) == 1
        assert data["streams"][0]["channels"][0]["name"] == "ESPN"

    @pytest.mark.asyncio
    async def test_returns_empty_when_none_struck(self, async_client, test_session):
        """Returns empty list when no streams exceed threshold."""
        _create_stream_stats(test_session, 10, consecutive_failures=1)

        mock_settings = MagicMock()
        mock_settings.strike_threshold = 5

        with patch("routers.stream_stats.get_settings", return_value=mock_settings):
            response = await async_client.get("/api/stream-stats/struck-out")

        assert response.status_code == 200
        data = response.json()
        assert data["streams"] == []
        assert data["enabled"] is True


class TestRemoveStruckOutStreams:
    """Tests for POST /api/stream-stats/struck-out/remove."""

    @pytest.mark.asyncio
    async def test_removes_from_channels(self, async_client, test_session):
        """Removes struck-out streams from channels and resets failures."""
        _create_stream_stats(test_session, 10, consecutive_failures=5)

        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 1, "name": "ESPN", "streams": [10, 20]}],
            "count": 1,
        }
        mock_client.update_channel.return_value = {}

        with patch("routers.stream_stats.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/stream-stats/struck-out/remove",
                json={"stream_ids": [10]},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["removed_from_channels"] == 1
        mock_client.update_channel.assert_called_once_with(1, {"streams": [20]})

        # Verify failure count was reset
        test_session.expire_all()
        stats = test_session.query(StreamStats).filter_by(stream_id=10).first()
        assert stats.consecutive_failures == 0


class TestComputeSort:
    """Tests for POST /api/stream-stats/compute-sort."""

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_channels(self, async_client):
        """Returns empty results for empty channel list."""
        mock_settings = MagicMock()
        mock_settings.stream_sort_priority = ["resolution"]
        mock_settings.stream_sort_enabled = {"resolution": True}

        with patch("routers.stream_stats.get_settings", return_value=mock_settings):
            response = await async_client.post("/api/stream-stats/compute-sort", json={
                "channels": [],
                "mode": "smart",
            })

        assert response.status_code == 200
        assert response.json()["results"] == []

    @pytest.mark.asyncio
    async def test_rejects_invalid_mode(self, async_client):
        """Returns 400 for invalid sort mode."""
        mock_settings = MagicMock()

        with patch("routers.stream_stats.get_settings", return_value=mock_settings):
            response = await async_client.post("/api/stream-stats/compute-sort", json={
                "channels": [{"channel_id": 1, "stream_ids": [10]}],
                "mode": "invalid",
            })

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_smart_sort(self, async_client, test_session):
        """Smart sort uses server settings and returns sorted IDs."""
        _create_stream_stats(test_session, 10, resolution="1920x1080", bitrate=5000000)
        _create_stream_stats(test_session, 20, resolution="1280x720", bitrate=3000000)

        mock_settings = MagicMock()
        mock_settings.stream_sort_priority = ["resolution"]
        mock_settings.stream_sort_enabled = {"resolution": True}
        mock_settings.m3u_account_priorities = {}
        mock_settings.deprioritize_failed_streams = False

        with patch("routers.stream_stats.get_settings", return_value=mock_settings), \
             patch("stream_prober.smart_sort_streams", return_value=[10, 20]) as mock_sort:
            response = await async_client.post("/api/stream-stats/compute-sort", json={
                "channels": [{"channel_id": 1, "stream_ids": [20, 10]}],
                "mode": "smart",
            })

        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 1
        assert results[0]["channel_id"] == 1
        mock_sort.assert_called_once()


class TestGetStreamStatsById:
    """Tests for GET /api/stream-stats/{stream_id}."""

    @pytest.mark.asyncio
    async def test_returns_stats(self, async_client):
        """Returns stats for a specific stream."""
        with patch("routers.stream_stats.StreamProber.get_stats_by_stream_id", return_value={
            "stream_id": 42, "probe_status": "success",
        }):
            response = await async_client.get("/api/stream-stats/42")

        assert response.status_code == 200
        assert response.json()["stream_id"] == 42

    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(self, async_client):
        """Returns 404 when stream stats don't exist."""
        with patch("routers.stream_stats.StreamProber.get_stats_by_stream_id", return_value=None):
            response = await async_client.get("/api/stream-stats/99999")

        assert response.status_code == 404


class TestGetStreamStatsByIds:
    """Tests for POST /api/stream-stats/by-ids."""

    @pytest.mark.asyncio
    async def test_returns_bulk_stats(self, async_client):
        """Returns stats for multiple streams."""
        with patch("routers.stream_stats.StreamProber.get_stats_by_stream_ids", return_value={
            "10": {"stream_id": 10}, "20": {"stream_id": 20},
        }):
            response = await async_client.post(
                "/api/stream-stats/by-ids",
                json={"stream_ids": [10, 20]},
            )

        assert response.status_code == 200


class TestProbeBulkStreams:
    """Tests for POST /api/stream-stats/probe/bulk."""

    @pytest.mark.asyncio
    async def test_probes_streams(self, async_client):
        """Probes requested streams and returns results."""
        mock_prober = AsyncMock()
        mock_prober._fetch_all_streams.return_value = [
            {"id": 10, "url": "http://example.com/10", "name": "Stream 10"},
        ]
        mock_prober.probe_stream.return_value = {"stream_id": 10, "status": "success"}

        with patch("routers.stream_stats.get_prober", return_value=mock_prober):
            response = await async_client.post(
                "/api/stream-stats/probe/bulk",
                json={"stream_ids": [10]},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["probed"] == 1
        mock_prober.probe_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_503_when_prober_unavailable(self, async_client):
        """Returns 503 when prober is not available."""
        with patch("routers.stream_stats.get_prober", return_value=None):
            response = await async_client.post(
                "/api/stream-stats/probe/bulk",
                json={"stream_ids": [10]},
            )

        assert response.status_code == 503


class TestProbeAllStreams:
    """Tests for POST /api/stream-stats/probe/all."""

    @pytest.mark.asyncio
    async def test_starts_background_probe(self, async_client):
        """Starts a background probe task."""
        mock_prober = MagicMock()
        mock_prober._probing_in_progress = False

        with patch("routers.stream_stats.get_prober", return_value=mock_prober), \
             patch("asyncio.create_task"):
            response = await async_client.post("/api/stream-stats/probe/all")

        assert response.status_code == 200
        assert response.json()["status"] == "started"

    @pytest.mark.asyncio
    async def test_resets_stuck_probe(self, async_client):
        """Resets stuck probe state before starting new one."""
        mock_prober = MagicMock()
        mock_prober._probing_in_progress = True

        with patch("routers.stream_stats.get_prober", return_value=mock_prober), \
             patch("asyncio.create_task"):
            response = await async_client.post("/api/stream-stats/probe/all")

        assert response.status_code == 200
        mock_prober.force_reset_probe_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_503_when_prober_unavailable(self, async_client):
        """Returns 503 when prober is not available."""
        with patch("routers.stream_stats.get_prober", return_value=None):
            response = await async_client.post("/api/stream-stats/probe/all")

        assert response.status_code == 503


class TestGetProbeProgress:
    """Tests for GET /api/stream-stats/probe/progress."""

    @pytest.mark.asyncio
    async def test_returns_progress(self, async_client):
        """Returns current probe progress."""
        mock_prober = MagicMock()
        mock_prober.get_probe_progress.return_value = {
            "in_progress": True, "total": 100, "completed": 50,
        }

        with patch("routers.stream_stats.get_prober", return_value=mock_prober):
            response = await async_client.get("/api/stream-stats/probe/progress")

        assert response.status_code == 200
        assert response.json()["completed"] == 50

    @pytest.mark.asyncio
    async def test_returns_503_when_prober_unavailable(self, async_client):
        """Returns 503 when prober is not available."""
        with patch("routers.stream_stats.get_prober", return_value=None):
            response = await async_client.get("/api/stream-stats/probe/progress")

        assert response.status_code == 503


class TestGetProbeResults:
    """Tests for GET /api/stream-stats/probe/results."""

    @pytest.mark.asyncio
    async def test_returns_results(self, async_client):
        """Returns last probe results."""
        mock_prober = MagicMock()
        mock_prober.get_probe_results.return_value = {"results": [], "summary": {}}

        with patch("routers.stream_stats.get_prober", return_value=mock_prober):
            response = await async_client.get("/api/stream-stats/probe/results")

        assert response.status_code == 200


class TestGetProbeHistory:
    """Tests for GET /api/stream-stats/probe/history."""

    @pytest.mark.asyncio
    async def test_returns_history(self, async_client):
        """Returns probe run history."""
        mock_prober = MagicMock()
        mock_prober.get_probe_history.return_value = [
            {"run_id": 1, "started_at": "2024-01-01T00:00:00Z"},
        ]

        with patch("routers.stream_stats.get_prober", return_value=mock_prober):
            response = await async_client.get("/api/stream-stats/probe/history")

        assert response.status_code == 200


class TestCancelProbe:
    """Tests for POST /api/stream-stats/probe/cancel."""

    @pytest.mark.asyncio
    async def test_cancels_probe(self, async_client):
        """Cancels an in-progress probe."""
        mock_prober = MagicMock()
        mock_prober.cancel_probe.return_value = {"status": "cancelled"}

        with patch("routers.stream_stats.get_prober", return_value=mock_prober):
            response = await async_client.post("/api/stream-stats/probe/cancel")

        assert response.status_code == 200
        mock_prober.cancel_probe.assert_called_once()


class TestResetProbeState:
    """Tests for POST /api/stream-stats/probe/reset."""

    @pytest.mark.asyncio
    async def test_resets_state(self, async_client):
        """Force resets probe state."""
        mock_prober = MagicMock()
        mock_prober.force_reset_probe_state.return_value = {"status": "reset"}

        with patch("routers.stream_stats.get_prober", return_value=mock_prober):
            response = await async_client.post("/api/stream-stats/probe/reset")

        assert response.status_code == 200
        mock_prober.force_reset_probe_state.assert_called_once()


class TestDismissStreamStats:
    """Tests for POST /api/stream-stats/dismiss."""

    @pytest.mark.asyncio
    async def test_dismisses_failures(self, async_client, test_session):
        """Dismisses probe failures for specified streams."""
        _create_stream_stats(test_session, 10, probe_status="failed")
        _create_stream_stats(test_session, 20, probe_status="failed")

        response = await async_client.post(
            "/api/stream-stats/dismiss",
            json={"stream_ids": [10, 20]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["dismissed"] == 2

        # Verify dismissed_at was set
        test_session.expire_all()
        s = test_session.query(StreamStats).filter_by(stream_id=10).first()
        assert s.dismissed_at is not None

    @pytest.mark.asyncio
    async def test_rejects_empty_stream_ids(self, async_client):
        """Returns 400 when stream_ids is empty."""
        response = await async_client.post(
            "/api/stream-stats/dismiss",
            json={"stream_ids": []},
        )

        assert response.status_code == 400


class TestClearStreamStats:
    """Tests for POST /api/stream-stats/clear."""

    @pytest.mark.asyncio
    async def test_clears_stats(self, async_client, test_session):
        """Clears stats for specified streams."""
        _create_stream_stats(test_session, 10)
        _create_stream_stats(test_session, 20)

        response = await async_client.post(
            "/api/stream-stats/clear",
            json={"stream_ids": [10]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["cleared"] == 1

        # Verify stream 10 was deleted, 20 remains
        assert test_session.query(StreamStats).filter_by(stream_id=10).first() is None
        assert test_session.query(StreamStats).filter_by(stream_id=20).first() is not None

    @pytest.mark.asyncio
    async def test_rejects_empty_stream_ids(self, async_client):
        """Returns 400 when stream_ids is empty."""
        response = await async_client.post(
            "/api/stream-stats/clear",
            json={"stream_ids": []},
        )

        assert response.status_code == 400


class TestClearAllStreamStats:
    """Tests for POST /api/stream-stats/clear-all."""

    @pytest.mark.asyncio
    async def test_clears_all(self, async_client, test_session):
        """Clears all stream stats."""
        _create_stream_stats(test_session, 10)
        _create_stream_stats(test_session, 20)
        _create_stream_stats(test_session, 30)

        response = await async_client.post("/api/stream-stats/clear-all")

        assert response.status_code == 200
        data = response.json()
        assert data["cleared"] == 3

        assert test_session.query(StreamStats).count() == 0


class TestGetDismissedStreamStats:
    """Tests for GET /api/stream-stats/dismissed.

    NOTE: In the monolith, this route is shadowed by GET /api/stream-stats/{stream_id}
    because the parameterized route is defined first. FastAPI matches routes in definition order,
    so "dismissed" is parsed as stream_id (and fails validation).
    This will be fixed during router extraction by reordering static routes before dynamic ones.
    """

    @pytest.mark.asyncio
    async def test_dismissed_route_resolves_correctly(self, async_client, test_session):
        """Static /dismissed route resolves before /{stream_id} parameter."""
        response = await async_client.get("/api/stream-stats/dismissed")
        assert response.status_code == 200
        assert "dismissed_stream_ids" in response.json()


class TestProbeSingleStream:
    """Tests for POST /api/stream-stats/probe/{stream_id}."""

    @pytest.mark.asyncio
    async def test_probes_stream(self, async_client):
        """Probes a single stream by ID."""
        mock_prober = AsyncMock()
        mock_prober._fetch_all_streams.return_value = [
            {"id": 42, "url": "http://example.com/42", "name": "ESPN"},
        ]
        mock_prober.probe_stream.return_value = {
            "stream_id": 42, "status": "success",
        }

        with patch("routers.stream_stats.get_prober", return_value=mock_prober):
            response = await async_client.post("/api/stream-stats/probe/42")

        assert response.status_code == 200
        mock_prober.probe_stream.assert_called_once_with(42, "http://example.com/42", "ESPN")

    @pytest.mark.asyncio
    async def test_returns_404_when_stream_not_found(self, async_client):
        """Returns 404 when stream doesn't exist."""
        mock_prober = AsyncMock()
        mock_prober._fetch_all_streams.return_value = []

        with patch("routers.stream_stats.get_prober", return_value=mock_prober):
            response = await async_client.post("/api/stream-stats/probe/99999")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_503_when_prober_unavailable(self, async_client):
        """Returns 503 when prober is not available."""
        with patch("routers.stream_stats.get_prober", return_value=None):
            response = await async_client.post("/api/stream-stats/probe/42")

        assert response.status_code == 503
