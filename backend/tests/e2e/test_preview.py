"""
E2E tests for stream/channel preview endpoints.

Endpoints: /api/stream-preview/{stream_id}, /api/channel-preview/{channel_id}

Note: These endpoints start subprocesses and return streaming responses.
We test that the endpoints exist and return expected error codes for
invalid IDs without consuming actual streams.
"""
from tests.e2e.conftest import skip_if_not_api


class TestStreamPreview:
    """Tests for /api/stream-preview/{stream_id} endpoint."""

    def test_stream_preview_nonexistent(self, e2e_client):
        """GET /api/stream-preview/{id} with nonexistent ID returns error."""
        response = e2e_client.get("/api/stream-preview/999999")
        skip_if_not_api(response)
        # Should fail for nonexistent stream
        assert response.status_code in (400, 404, 500)


class TestChannelPreview:
    """Tests for /api/channel-preview/{channel_id} endpoint."""

    def test_channel_preview_nonexistent(self, e2e_client):
        """GET /api/channel-preview/{id} with nonexistent ID returns error."""
        response = e2e_client.get("/api/channel-preview/999999")
        skip_if_not_api(response)
        assert response.status_code in (400, 404, 500)
