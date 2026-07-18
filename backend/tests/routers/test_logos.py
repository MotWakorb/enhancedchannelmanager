"""
Tests for the logo image proxy and cache_url rewriting
(bead enhancedchannelmanager-hhmat, GH #662).

Dispatcharr builds each logo's ``cache_url`` from the Host header it saw on
ECM's server-side request (e.g. ``http://172.16.0.20:9191/...`` or a
docker-internal hostname). Browsers that cannot resolve/reach that host lose
every logo. ECM therefore:

1. Proxies logo image bytes via GET /api/channels/logos/{id}/image
   (authenticated by default — NOT in AUTH_EXEMPT_PATHS; same-origin <img>
   carries the httpOnly access_token cookie automatically).
2. Rewrites ``cache_url`` in logo responses to an absolute ECM-origin URL
   pointing at that proxy, preserving Dispatcharr's ``?v=<hash>``
   cache-buster. Absolute (not relative) because LogoModal.tsx's preview
   sanitizer only accepts http(s)/blob/data URLs.

Live-observed Dispatcharr behavior (2026-07-18, container this rig talks to):
- /api/channels/logos/{id}/cache/ returns 200 + image bytes for BOTH
  local-file logos and external-URL logos (it fetches and caches remotes
  server-side). Cache-Control observed: ``public, max-age=3600`` (remote)
  and ``public, max-age=14400`` (local). No ETag emitted.
- Unknown id returns 404 JSON: {"detail": "No Logo matches the given query."}

Mocks at routers.channels.get_client level per backend/CLAUDE.md.
"""
import httpx
import pytest
from unittest.mock import AsyncMock, patch


def _upstream_image_response(
    status_code: int = 200,
    content: bytes = b"\x89PNG-fake-bytes",
    headers: dict | None = None,
) -> httpx.Response:
    """Build a real httpx.Response like dispatcharr_client._request returns."""
    if headers is None:
        headers = {
            "content-type": "image/png",
            "cache-control": "public, max-age=3600",
        }
    return httpx.Response(status_code=status_code, content=content, headers=headers)


class TestLogoImageProxy:
    """Tests for GET /api/channels/logos/{logo_id}/image."""

    @pytest.mark.asyncio
    async def test_returns_image_bytes_with_content_type_and_cache_headers(
        self, async_client
    ):
        """Happy path: upstream bytes, content-type, and Cache-Control pass
        through to the browser."""
        mock_client = AsyncMock()
        mock_client._request.return_value = _upstream_image_response()

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/logos/1276/image")

        assert response.status_code == 200
        assert response.content == b"\x89PNG-fake-bytes"
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "public, max-age=3600"
        mock_client._request.assert_called_once()
        args, kwargs = mock_client._request.call_args
        assert args[0] == "GET"
        assert args[1] == "/api/channels/logos/1276/cache/"

    @pytest.mark.asyncio
    async def test_default_cache_control_when_upstream_omits_it(self, async_client):
        """Without an upstream Cache-Control, ECM sets a sane long max-age so
        channel-list renders don't round-trip ECM->Dispatcharr per logo."""
        mock_client = AsyncMock()
        mock_client._request.return_value = _upstream_image_response(
            headers={"content-type": "image/jpeg"}
        )

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/logos/5/image")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "public, max-age=86400"

    @pytest.mark.asyncio
    async def test_etag_passthrough_when_upstream_provides_one(self, async_client):
        """If a future Dispatcharr version emits ETags, they pass through."""
        mock_client = AsyncMock()
        mock_client._request.return_value = _upstream_image_response(
            headers={
                "content-type": "image/png",
                "cache-control": "public, max-age=3600",
                "etag": '"abc123"',
            }
        )

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/logos/5/image")

        assert response.status_code == 200
        assert response.headers["etag"] == '"abc123"'

    @pytest.mark.asyncio
    async def test_forwards_if_none_match_and_mirrors_304(self, async_client):
        """Conditional requests forward to Dispatcharr; an upstream 304 is
        mirrored without a body."""
        mock_client = AsyncMock()
        mock_client._request.return_value = httpx.Response(304)

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos/5/image",
                headers={"If-None-Match": '"abc123"'},
            )

        assert response.status_code == 304
        assert response.content == b""
        _, kwargs = mock_client._request.call_args
        assert kwargs["headers"].get("If-None-Match") == '"abc123"'

    @pytest.mark.asyncio
    async def test_upstream_404_maps_to_404(self, async_client):
        """Unknown logo id: Dispatcharr's 404 surfaces as ECM 404, not 500."""
        mock_client = AsyncMock()
        mock_client._request.return_value = httpx.Response(
            404,
            json={"detail": "No Logo matches the given query."},
        )

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/logos/99999999/image")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_other_upstream_error_maps_to_502(self, async_client):
        """Non-404 upstream failures surface as 502 (bad gateway), matching
        the m3u.py upstream-fetch precedent — never a masked 500."""
        mock_client = AsyncMock()
        mock_client._request.return_value = httpx.Response(500, text="boom")

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/logos/5/image")

        assert response.status_code == 502

    @pytest.mark.asyncio
    async def test_transport_error_maps_to_502(self, async_client):
        """Dispatcharr unreachable surfaces as 502."""
        mock_client = AsyncMock()
        mock_client._request.side_effect = httpx.ConnectError("unreachable")

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/logos/5/image")

        assert response.status_code == 502

    @pytest.mark.asyncio
    async def test_requires_auth_when_auth_enabled(self, async_client):
        """The proxy is NOT in AUTH_EXEMPT_PATHS: with auth enabled and no
        token, the global middleware rejects with 401. Same-origin <img>
        tags send the httpOnly cookie, so real browsers are unaffected."""

        class _FakeAuthSettings:
            require_auth = True
            setup_complete = True

        mock_client = AsyncMock()
        mock_client._request.return_value = _upstream_image_response()

        with patch("main.get_auth_settings", return_value=_FakeAuthSettings()), \
             patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/logos/5/image")

        assert response.status_code == 401
        mock_client._request.assert_not_called()


class TestLogoCacheUrlRewrite:
    """cache_url in logo responses is rewritten to the ECM-origin proxy."""

    @pytest.mark.asyncio
    async def test_get_logos_passthrough_branch_rewrites_cache_url(
        self, async_client
    ):
        """Plain paginated GET /logos (no sort/filter/search): every logo's
        cache_url becomes an absolute ECM-origin proxy URL preserving the
        ?v= cache-buster; ``url`` is left untouched."""
        mock_client = AsyncMock()
        mock_client.get_logos.return_value = {
            "count": 2,
            "next": None,
            "previous": None,
            "results": [
                {
                    "id": 1276,
                    "name": "PBS",
                    "url": "https://cdn.example.com/logos/PBS.png",
                    "cache_url": "http://172.16.0.20:9191/api/channels/logos/1276/cache/?v=6df99181",
                },
                {
                    "id": 2948,
                    "name": "Local",
                    "url": "/data/logos/local.jpg",
                    "cache_url": "http://dispatcharr:9191/api/channels/logos/2948/cache/",
                },
            ],
        }

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/logos")

        assert response.status_code == 200
        results = response.json()["results"]
        assert results[0]["cache_url"] == (
            "http://test/api/channels/logos/1276/image?v=6df99181"
        )
        assert results[0]["url"] == "https://cdn.example.com/logos/PBS.png"
        # No ?v= on upstream cache_url -> no query on the rewrite
        assert results[1]["cache_url"] == "http://test/api/channels/logos/2948/image"
        assert results[1]["url"] == "/data/logos/local.jpg"

    @pytest.mark.asyncio
    async def test_get_logos_local_sort_branch_rewrites_cache_url(self, async_client):
        """The aggregate-and-sort branch (sort_by/unused_only/search) rewrites
        too — both code paths must stay consistent."""
        mock_client = AsyncMock()
        mock_client.get_all_logos_raw.return_value = [
            {
                "id": 2,
                "name": "FOX",
                "url": "https://cdn.example.com/fox.png",
                "cache_url": "http://172.16.0.20:9191/api/channels/logos/2/cache/?v=aa11",
                "channel_count": 3,
            },
            {
                "id": 1,
                "name": "ESPN",
                "url": "https://cdn.example.com/espn.png",
                "cache_url": "http://172.16.0.20:9191/api/channels/logos/1/cache/?v=bb22",
                "channel_count": 1,
            },
        ]

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos", params={"sort_by": "name"}
            )

        assert response.status_code == 200
        results = response.json()["results"]
        assert [l["name"] for l in results] == ["ESPN", "FOX"]
        assert results[0]["cache_url"] == "http://test/api/channels/logos/1/image?v=bb22"
        assert results[1]["cache_url"] == "http://test/api/channels/logos/2/image?v=aa11"
        mock_client.get_logos.assert_not_called()

    @pytest.mark.asyncio
    async def test_null_cache_url_stays_null(self, async_client):
        """A logo without an upstream cache_url keeps it falsy so the frontend
        falls back to logo.url (which may be browser-reachable)."""
        mock_client = AsyncMock()
        mock_client.get_logos.return_value = {
            "count": 1,
            "next": None,
            "previous": None,
            "results": [
                {
                    "id": 7,
                    "name": "NoCache",
                    "url": "https://cdn.example.com/nc.png",
                    "cache_url": None,
                },
            ],
        }

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/logos")

        assert response.status_code == 200
        assert response.json()["results"][0]["cache_url"] is None

    @pytest.mark.asyncio
    async def test_get_logo_single_rewrites_cache_url(self, async_client):
        """GET /logos/{id} rewrites the single logo the same way."""
        mock_client = AsyncMock()
        mock_client.get_logo.return_value = {
            "id": 42,
            "name": "HBO",
            "url": "https://cdn.example.com/hbo.png",
            "cache_url": "http://172.16.0.20:9191/api/channels/logos/42/cache/?v=cafe01",
        }

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/logos/42")

        assert response.status_code == 200
        data = response.json()
        assert data["cache_url"] == "http://test/api/channels/logos/42/image?v=cafe01"
        assert data["url"] == "https://cdn.example.com/hbo.png"

    @pytest.mark.asyncio
    async def test_honors_x_forwarded_proto_and_host(self, async_client):
        """Behind a reverse proxy the rewritten origin must reflect the
        browser-facing scheme/host (X-Forwarded-*), not the direct uvicorn
        socket — mirrors main.py's X-Forwarded-For convention."""
        mock_client = AsyncMock()
        mock_client.get_logos.return_value = {
            "count": 1,
            "next": None,
            "previous": None,
            "results": [
                {
                    "id": 9,
                    "name": "P",
                    "url": "https://cdn.example.com/p.png",
                    "cache_url": "http://dispatcharr:9191/api/channels/logos/9/cache/?v=99",
                },
            ],
        }

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/channels/logos",
                headers={
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "ecm.example.com",
                },
            )

        assert response.status_code == 200
        assert response.json()["results"][0]["cache_url"] == (
            "https://ecm.example.com/api/channels/logos/9/image?v=99"
        )

    @pytest.mark.asyncio
    async def test_create_logo_response_rewrites_cache_url(self, async_client):
        """POST /logos returns the created logo with a proxied cache_url so a
        just-created logo renders immediately (ChannelsPane and
        AutoSyncSettingsModal consume the response object directly)."""
        mock_client = AsyncMock()
        mock_client.create_logo.return_value = {
            "id": 3001,
            "name": "New",
            "url": "https://cdn.example.com/new.png",
            "cache_url": "http://dispatcharr:9191/api/channels/logos/3001/cache/?v=01",
        }

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/logos",
                json={"name": "New", "url": "https://cdn.example.com/new.png"},
            )

        assert response.status_code == 200
        assert response.json()["cache_url"] == (
            "http://test/api/channels/logos/3001/image?v=01"
        )

    @pytest.mark.asyncio
    async def test_update_logo_response_rewrites_cache_url(self, async_client):
        """PATCH /logos/{id} responses are rewritten for the same reason."""
        mock_client = AsyncMock()
        mock_client.update_logo.return_value = {
            "id": 42,
            "name": "Renamed",
            "url": "https://cdn.example.com/hbo.png",
            "cache_url": "http://dispatcharr:9191/api/channels/logos/42/cache/?v=02",
        }

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.patch(
                "/api/channels/logos/42", json={"name": "Renamed"}
            )

        assert response.status_code == 200
        assert response.json()["cache_url"] == (
            "http://test/api/channels/logos/42/image?v=02"
        )
