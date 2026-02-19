"""
E2E tests for stats, stream-stats, and activity endpoints.

Endpoints: /api/stats/*, /api/stream-stats/*
"""
from tests.e2e.conftest import skip_if_not_api


class TestStats:
    """Tests for /api/stats endpoints."""

    def test_activity(self, e2e_client):
        """GET /api/stats/activity returns activity data."""
        response = e2e_client.get("/api/stats/activity")
        skip_if_not_api(response)
        assert response.status_code == 200

    def test_bandwidth_stats(self, e2e_client):
        """GET /api/stats/bandwidth returns bandwidth data."""
        response = e2e_client.get("/api/stats/bandwidth")
        assert response.status_code == 200

    def test_channels_stats(self, e2e_client):
        """GET /api/stats/channels returns channel stats."""
        response = e2e_client.get("/api/stats/channels")
        assert response.status_code == 200

    def test_top_watched(self, e2e_client):
        """GET /api/stats/top-watched returns top watched data."""
        response = e2e_client.get("/api/stats/top-watched")
        skip_if_not_api(response)
        assert response.status_code == 200

    def test_unique_viewers(self, e2e_client):
        """GET /api/stats/unique-viewers returns viewer data."""
        response = e2e_client.get("/api/stats/unique-viewers")
        skip_if_not_api(response)
        assert response.status_code == 200

    def test_popularity_rankings(self, e2e_client):
        """GET /api/stats/popularity/rankings returns rankings."""
        response = e2e_client.get("/api/stats/popularity/rankings")
        skip_if_not_api(response)
        assert response.status_code == 200


class TestStreamStats:
    """Tests for /api/stream-stats endpoints."""

    def test_get_stream_stats(self, e2e_client):
        """GET /api/stream-stats returns stream statistics."""
        response = e2e_client.get("/api/stream-stats")
        assert response.status_code == 200

    def test_stream_stats_summary(self, e2e_client):
        """GET /api/stream-stats/summary returns summary."""
        response = e2e_client.get("/api/stream-stats/summary")
        skip_if_not_api(response)
        assert response.status_code == 200

    def test_probe_progress(self, e2e_client):
        """GET /api/stream-stats/probe/progress returns probe progress."""
        response = e2e_client.get("/api/stream-stats/probe/progress")
        skip_if_not_api(response)
        assert response.status_code == 200

    def test_probe_history(self, e2e_client):
        """GET /api/stream-stats/probe/history returns probe history."""
        response = e2e_client.get("/api/stream-stats/probe/history")
        skip_if_not_api(response)
        assert response.status_code == 200

    def test_probe_results(self, e2e_client):
        """GET /api/stream-stats/probe/results returns probe results."""
        response = e2e_client.get("/api/stream-stats/probe/results")
        skip_if_not_api(response)
        assert response.status_code == 200
