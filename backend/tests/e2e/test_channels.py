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

    def test_list_channels_page_size_respected(self, e2e_client):
        """GET /api/channels honours page_size — at most that many results per page."""
        response = e2e_client.get("/api/channels", params={"page": 1, "page_size": 1})
        assert response.status_code == 200
        data = response.json()
        # DRF-style envelope: results is capped at page_size, count is the total.
        assert len(data["results"]) <= 1

    def test_list_channels_filter_nonexistent_group_is_empty(self, e2e_client):
        """GET /api/channels?channel_group=<unknown> returns an empty page, not an error.

        Filtering on a channel_group id that does not exist is a valid query
        that simply matches nothing — the endpoint must return 200 with an empty
        result set, not 404/500. The prior smoke test never exercised any filter
        or error/edge path for this route.
        """
        response = e2e_client.get("/api/channels", params={"channel_group": 99999999})
        assert response.status_code == 200
        data = response.json()
        assert data["results"] == []

    def test_list_channels_search_no_match_is_empty(self, e2e_client):
        """GET /api/channels?search=<no match> returns an empty page, not an error."""
        response = e2e_client.get(
            "/api/channels", params={"search": "zzzz-no-such-channel-xyz"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["results"] == []


class TestChannelCSV:
    """Tests for CSV endpoints."""

    def test_csv_template(self, e2e_client):
        """GET /api/channels/csv-template returns a populated CSV template body.

        The prior test only checked the status code. Inspect the body: it must be
        served as CSV and contain the documented header/guidance so the import UI
        has a real template to hand the operator, not an empty 200.
        """
        response = e2e_client.get("/api/channels/csv-template")
        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")
        body = response.text
        assert body.strip(), "CSV template body must not be empty"
        # The template documents the required 'name' field in its comment header.
        assert "name" in body.lower()


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
