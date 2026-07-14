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
        """GET /api/stream-preview/{id} with nonexistent ID returns a JSON error, never a stream.

        The exact status is environment-dependent and cannot be pinned to 404
        alone here: with Dispatcharr connected the upstream client raises on an
        unknown id → 500 ("Failed to get stream"); with a clean not-found →
        404; with Dispatcharr disconnected → 503. (The clean 404/503/400 paths
        are pinned deterministically with mocks in
        tests/integration/test_api_stream_preview.py.) The environment-
        independent invariant this e2e guards: a nonexistent id must NEVER
        return a successful 2xx stream, and the failure must be a structured API
        error carrying a ``detail`` — not a crash or the SPA catch-all.
        """
        response = e2e_client.get("/api/stream-preview/999999")
        skip_if_not_api(response)
        assert response.status_code in (400, 404, 500, 503)
        assert "detail" in response.json()


class TestChannelPreview:
    """Tests for /api/channel-preview/{channel_id} endpoint."""

    def test_channel_preview_nonexistent(self, e2e_client):
        """GET /api/channel-preview/{id} with nonexistent ID returns a JSON error, never a stream.

        Same environment-dependent status caveat as the stream-preview case
        above (500 when Dispatcharr is connected and the upstream raises, 404 on
        a clean not-found, 503 when disconnected). Invariant guarded: never a 2xx
        stream for a nonexistent id, and a structured ``detail`` error body.
        """
        response = e2e_client.get("/api/channel-preview/999999")
        skip_if_not_api(response)
        assert response.status_code in (400, 404, 500, 503)
        assert "detail" in response.json()
