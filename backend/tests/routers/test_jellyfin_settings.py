"""Tests for the Jellyfin Settings test-connection endpoint (bd-r5f0c.4).

Covers ``POST /api/settings/jellyfin/test-connection``:
  - Success: JellyfinClient.get_sessions returns → {ok: True}.
  - Auth failure: JellyfinClient raises JellyfinClientError → {ok: False, error: <msg>}.
  - Unexpected exception: wrapped to {ok: False, error: 'Unexpected error: ...'}.
  - SSRF mitigation (security finding SEC-2):
      * URL with path/query/fragment is sanitized — only scheme + netloc
        survive to JellyfinClient.
      * Disallowed schemes (file://, gopher://, ftp://) return
        {ok: False, error: <scheme-rejected message>} without
        constructing a JellyfinClient.
      * Missing hostname returns {ok: False, error: <no-hostname message>}.
  - Invalid request body: missing required fields → 422.
  - Always closes JellyfinClient even on error.

Mocks: routers.settings.JellyfinClient so no real HTTP is issued.
"""
from unittest.mock import AsyncMock, patch

import pytest


class TestJellyfinTestConnection:
    """Tests for POST /api/settings/jellyfin/test-connection (bd-r5f0c.4)."""

    @pytest.mark.asyncio
    async def test_returns_ok_true_on_success(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        with patch("routers.settings.JellyfinClient", return_value=mock_client):
            response = await async_client.post(
                "/api/settings/jellyfin/test-connection",
                json={
                    "base_url": "http://jellyfin.local:8096",
                    "api_key": "valid-key",
                },
            )

        assert response.status_code == 200, response.json()
        assert response.json() == {"ok": True}
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_ok_false_on_auth_failure(self, async_client):
        from jellyfin_client import JellyfinClientError

        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(
            side_effect=JellyfinClientError(
                "Jellyfin /Sessions returned 401 unauthorized — check API key"
            )
        )
        mock_client.close = AsyncMock()

        with patch("routers.settings.JellyfinClient", return_value=mock_client):
            response = await async_client.post(
                "/api/settings/jellyfin/test-connection",
                json={
                    "base_url": "http://jellyfin.local:8096",
                    "api_key": "bad-key",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "401" in body["error"] or "unauthorized" in body["error"].lower()

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_class_name(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(side_effect=RuntimeError("boom"))
        mock_client.close = AsyncMock()

        with patch("routers.settings.JellyfinClient", return_value=mock_client):
            response = await async_client.post(
                "/api/settings/jellyfin/test-connection",
                json={
                    "base_url": "http://jellyfin.local:8096",
                    "api_key": "any-key",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "RuntimeError" in body["error"]
        assert "boom" not in body["error"]

    @pytest.mark.asyncio
    async def test_uses_inline_credentials_not_saved_settings(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        captured: dict = {}

        def constructor_spy(base_url, api_key):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            return mock_client

        with patch("routers.settings.JellyfinClient", side_effect=constructor_spy):
            response = await async_client.post(
                "/api/settings/jellyfin/test-connection",
                json={
                    "base_url": "http://fresh-jellyfin:8096",
                    "api_key": "fresh-key",
                },
            )

        assert response.status_code == 200, response.json()
        assert captured["base_url"] == "http://fresh-jellyfin:8096"
        assert captured["api_key"] == "fresh-key"

    @pytest.mark.asyncio
    async def test_rejects_missing_base_url(self, async_client):
        response = await async_client.post(
            "/api/settings/jellyfin/test-connection",
            json={"api_key": "any-key"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_missing_api_key(self, async_client):
        response = await async_client.post(
            "/api/settings/jellyfin/test-connection",
            json={"base_url": "http://jellyfin.local:8096"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_closes_client_even_on_error(self, async_client):
        from jellyfin_client import JellyfinClientError

        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(
            side_effect=JellyfinClientError("network down")
        )
        mock_client.close = AsyncMock()

        with patch("routers.settings.JellyfinClient", return_value=mock_client):
            await async_client.post(
                "/api/settings/jellyfin/test-connection",
                json={
                    "base_url": "http://jellyfin.local:8096",
                    "api_key": "any-key",
                },
            )

        mock_client.close.assert_awaited_once()


class TestJellyfinTestConnectionSsrfMitigation:
    """SSRF mitigation regression suite — security finding SEC-2."""

    @pytest.mark.asyncio
    async def test_strips_path_query_and_fragment(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        captured: dict = {}

        def constructor_spy(base_url, api_key):
            captured["base_url"] = base_url
            return mock_client

        with patch("routers.settings.JellyfinClient", side_effect=constructor_spy):
            response = await async_client.post(
                "/api/settings/jellyfin/test-connection",
                json={
                    "base_url": "http://attacker.com/foo?bar=baz#qux",
                    "api_key": "k",
                },
            )

        assert response.status_code == 200, response.json()
        assert captured["base_url"] == "http://attacker.com"

    @pytest.mark.asyncio
    async def test_rejects_file_scheme(self, async_client):
        jellyfin_constructor = AsyncMock()
        with patch("routers.settings.JellyfinClient", side_effect=jellyfin_constructor):
            response = await async_client.post(
                "/api/settings/jellyfin/test-connection",
                json={
                    "base_url": "file:///etc/passwd",
                    "api_key": "k",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "scheme" in body["error"].lower()
        jellyfin_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_gopher_scheme(self, async_client):
        jellyfin_constructor = AsyncMock()
        with patch("routers.settings.JellyfinClient", side_effect=jellyfin_constructor):
            response = await async_client.post(
                "/api/settings/jellyfin/test-connection",
                json={
                    "base_url": "gopher://attacker.com",
                    "api_key": "k",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "scheme" in body["error"].lower()
        jellyfin_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_ftp_scheme(self, async_client):
        jellyfin_constructor = AsyncMock()
        with patch("routers.settings.JellyfinClient", side_effect=jellyfin_constructor):
            response = await async_client.post(
                "/api/settings/jellyfin/test-connection",
                json={
                    "base_url": "ftp://attacker.com",
                    "api_key": "k",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "scheme" in body["error"].lower()
        jellyfin_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_missing_hostname(self, async_client):
        jellyfin_constructor = AsyncMock()
        with patch("routers.settings.JellyfinClient", side_effect=jellyfin_constructor):
            response = await async_client.post(
                "/api/settings/jellyfin/test-connection",
                json={
                    "base_url": "http://",
                    "api_key": "k",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "hostname" in body["error"].lower()
        jellyfin_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_empty_base_url(self, async_client):
        jellyfin_constructor = AsyncMock()
        with patch("routers.settings.JellyfinClient", side_effect=jellyfin_constructor):
            response = await async_client.post(
                "/api/settings/jellyfin/test-connection",
                json={
                    "base_url": "",
                    "api_key": "k",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        jellyfin_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_https_scheme_allowed(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        captured: dict = {}

        def constructor_spy(base_url, api_key):
            captured["base_url"] = base_url
            return mock_client

        with patch("routers.settings.JellyfinClient", side_effect=constructor_spy):
            response = await async_client.post(
                "/api/settings/jellyfin/test-connection",
                json={
                    "base_url": "https://jellyfin.example.com:8920",
                    "api_key": "k",
                },
            )

        assert response.status_code == 200, response.json()
        assert response.json() == {"ok": True}
        assert captured["base_url"] == "https://jellyfin.example.com:8920"
