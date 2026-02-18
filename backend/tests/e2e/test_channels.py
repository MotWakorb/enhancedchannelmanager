"""
E2E tests for channel and channel-group endpoints.

Endpoints: /api/channels/*, /api/channel-groups/*, /api/channel-profiles/*
"""
from tests.e2e.conftest import skip_if_not_api


class TestListChannels:
    """Tests for GET /api/channels."""

    def test_list_channels_returns_200(self, e2e_client):
        """GET /api/channels returns 200."""
        response = e2e_client.get("/api/channels")
        assert response.status_code == 200

    def test_list_channels_with_pagination(self, e2e_client):
        """GET /api/channels supports pagination params."""
        response = e2e_client.get("/api/channels", params={"page": 1, "page_size": 5})
        assert response.status_code == 200


class TestChannelCSV:
    """Tests for CSV endpoints."""

    def test_csv_template(self, e2e_client):
        """GET /api/channels/csv-template returns CSV template."""
        response = e2e_client.get("/api/channels/csv-template")
        assert response.status_code == 200


class TestChannelGroups:
    """Tests for /api/channel-groups endpoints."""

    def test_list_groups(self, e2e_client):
        """GET /api/channel-groups returns groups."""
        response = e2e_client.get("/api/channel-groups")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_hidden_groups(self, e2e_client):
        """GET /api/channel-groups/hidden returns hidden groups."""
        response = e2e_client.get("/api/channel-groups/hidden")
        skip_if_not_api(response)
        assert response.status_code == 200

    def test_orphaned_groups(self, e2e_client):
        """GET /api/channel-groups/orphaned returns orphaned groups."""
        response = e2e_client.get("/api/channel-groups/orphaned")
        assert response.status_code == 200

    def test_auto_created_groups(self, e2e_client):
        """GET /api/channel-groups/auto-created returns auto-created data."""
        response = e2e_client.get("/api/channel-groups/auto-created")
        assert response.status_code == 200

    def test_groups_with_streams(self, e2e_client):
        """GET /api/channel-groups/with-streams returns groups with streams."""
        response = e2e_client.get("/api/channel-groups/with-streams")
        assert response.status_code == 200


class TestChannelProfiles:
    """Tests for /api/channel-profiles endpoints."""

    def test_list_profiles(self, e2e_client):
        """GET /api/channel-profiles returns profiles."""
        response = e2e_client.get("/api/channel-profiles")
        assert response.status_code == 200
