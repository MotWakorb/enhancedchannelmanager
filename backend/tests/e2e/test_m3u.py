"""
E2E tests for M3U endpoints.

Endpoints: /api/m3u/*, /api/m3u/snapshots
"""
from tests.e2e.conftest import skip_if_not_api


class TestM3UServerGroups:
    """Tests for M3U server group endpoints."""

    def test_list_server_groups(self, e2e_client):
        """GET /api/m3u/server-groups returns server groups."""
        response = e2e_client.get("/api/m3u/server-groups")
        skip_if_not_api(response)
        assert response.status_code == 200


class TestM3USnapshots:
    """Tests for /api/m3u/snapshots endpoint."""

    def test_list_snapshots(self, e2e_client):
        """GET /api/m3u/snapshots returns snapshots."""
        response = e2e_client.get("/api/m3u/snapshots")
        skip_if_not_api(response)
        assert response.status_code == 200
