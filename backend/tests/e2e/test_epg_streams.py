"""
E2E tests for EPG, streams, providers, and profile endpoints.

Endpoints: /api/epg/*, /api/streams/*, /api/providers/*, /api/*-profiles/*
"""

class TestEPGSources:
    """Tests for /api/epg endpoints."""

    def test_list_epg_sources(self, e2e_client):
        """GET /api/epg/sources returns EPG sources."""
        response = e2e_client.get("/api/epg/sources")
        assert response.status_code == 200

    def test_epg_grid(self, e2e_client):
        """GET /api/epg/grid returns EPG grid data."""
        response = e2e_client.get("/api/epg/grid")
        assert response.status_code == 200


class TestStreams:
    """Tests for /api/streams endpoints."""

    def test_list_streams(self, e2e_client):
        """GET /api/streams returns streams."""
        response = e2e_client.get("/api/streams")
        assert response.status_code == 200


class TestProviders:
    """Tests for /api/providers endpoints."""

    def test_list_providers(self, e2e_client):
        """GET /api/providers returns providers."""
        response = e2e_client.get("/api/providers")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestStreamProfiles:
    """Tests for /api/stream-profiles endpoints."""

    def test_list_stream_profiles(self, e2e_client):
        """GET /api/stream-profiles returns stream profiles."""
        response = e2e_client.get("/api/stream-profiles")
        assert response.status_code == 200

    def test_stream_profiles_response_shape(self, e2e_client):
        """Stream profiles response is a list."""
        data = e2e_client.get("/api/stream-profiles").json()
        assert isinstance(data, list)


class TestChannelProfiles:
    """Tests for /api/channel-profiles endpoints."""

    def test_list_channel_profiles(self, e2e_client):
        """GET /api/channel-profiles returns channel profiles."""
        response = e2e_client.get("/api/channel-profiles")
        assert response.status_code == 200
