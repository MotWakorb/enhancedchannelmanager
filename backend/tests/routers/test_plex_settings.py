"""Tests for the Plex Settings test-connection endpoint (bd-r5f0c.4).

Covers ``POST /api/settings/plex/test-connection``:
  - Success: PlexClient.get_sessions returns → {ok: True}.
  - Auth failure: PlexClient raises PlexClientError → {ok: False, error: <msg>}.
  - Unexpected exception: wrapped to {ok: False, error: 'Unexpected error: ...'}.
  - SSRF mitigation (security finding SEC-2):
      * URL with path/query/fragment is sanitized — only scheme + netloc
        survive to PlexClient.
      * Disallowed schemes (file://, gopher://, ftp://) return
        {ok: False, error: <scheme-rejected message>} without
        constructing a PlexClient.
      * Missing hostname returns {ok: False, error: <no-hostname message>}.
  - Invalid request body: missing required fields → 422.
  - Always closes PlexClient (releases connection pool) even on error.

Mocks: routers.settings.PlexClient so no real HTTP is issued.
"""
from unittest.mock import AsyncMock, patch

import pytest


class TestPlexTestConnection:
    """Tests for POST /api/settings/plex/test-connection (bd-r5f0c.4)."""

    @pytest.mark.asyncio
    async def test_returns_ok_true_on_success(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        with patch("routers.settings.PlexClient", return_value=mock_client):
            response = await async_client.post(
                "/api/settings/plex/test-connection",
                json={
                    "base_url": "http://plex.local:32400",
                    "token": "valid-plex-token",
                },
            )

        assert response.status_code == 200, response.json()
        assert response.json() == {"ok": True}
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_ok_false_on_auth_failure(self, async_client):
        from plex_client import PlexClientError

        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(
            side_effect=PlexClientError(
                "Plex /status/sessions returned 401 unauthorized — check token"
            )
        )
        mock_client.close = AsyncMock()

        with patch("routers.settings.PlexClient", return_value=mock_client):
            response = await async_client.post(
                "/api/settings/plex/test-connection",
                json={
                    "base_url": "http://plex.local:32400",
                    "token": "bad-token",
                },
            )

        # DELIBERATE: 200 (not 4xx/5xx) so the operator sees the message
        # inline rather than as a generic HTTP error.
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "401" in body["error"] or "unauthorized" in body["error"].lower()

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_class_name(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(side_effect=RuntimeError("boom"))
        mock_client.close = AsyncMock()

        with patch("routers.settings.PlexClient", return_value=mock_client):
            response = await async_client.post(
                "/api/settings/plex/test-connection",
                json={
                    "base_url": "http://plex.local:32400",
                    "token": "any-token",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "RuntimeError" in body["error"]
        # The exception message itself is NOT surfaced — only the class
        # name — so internals can't leak to the UI.
        assert "boom" not in body["error"]

    @pytest.mark.asyncio
    async def test_uses_inline_credentials_not_saved_settings(self, async_client):
        """The endpoint constructs PlexClient with the request-body
        credentials, not the saved settings — operators must be able to
        test BEFORE saving."""
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        captured: dict = {}

        def constructor_spy(base_url, token):
            captured["base_url"] = base_url
            captured["token"] = token
            return mock_client

        with patch("routers.settings.PlexClient", side_effect=constructor_spy):
            response = await async_client.post(
                "/api/settings/plex/test-connection",
                json={
                    "base_url": "http://fresh-plex:32400",
                    "token": "fresh-token",
                },
            )

        assert response.status_code == 200, response.json()
        # SSRF mitigation: the path-free URL survived intact.
        assert captured["base_url"] == "http://fresh-plex:32400"
        assert captured["token"] == "fresh-token"

    @pytest.mark.asyncio
    async def test_rejects_missing_base_url(self, async_client):
        response = await async_client.post(
            "/api/settings/plex/test-connection",
            json={"token": "any-token"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_missing_token(self, async_client):
        response = await async_client.post(
            "/api/settings/plex/test-connection",
            json={"base_url": "http://plex.local:32400"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_closes_client_even_on_error(self, async_client):
        from plex_client import PlexClientError

        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(
            side_effect=PlexClientError("network down")
        )
        mock_client.close = AsyncMock()

        with patch("routers.settings.PlexClient", return_value=mock_client):
            await async_client.post(
                "/api/settings/plex/test-connection",
                json={
                    "base_url": "http://plex.local:32400",
                    "token": "any-token",
                },
            )

        mock_client.close.assert_awaited_once()


class TestPlexTestConnectionSsrfMitigation:
    """SSRF mitigation regression suite — security finding SEC-2.

    The endpoint MUST sanitize the operator-supplied base URL before
    handing it to PlexClient: scheme allowlist (http/https only) and
    netloc-only reconstruction (paths / queries / fragments stripped).
    """

    @pytest.mark.asyncio
    async def test_strips_path_query_and_fragment(self, async_client):
        """A URL with embedded path / query / fragment must be reduced
        to scheme + netloc only before PlexClient sees it."""
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        captured: dict = {}

        def constructor_spy(base_url, token):
            captured["base_url"] = base_url
            return mock_client

        with patch("routers.settings.PlexClient", side_effect=constructor_spy):
            response = await async_client.post(
                "/api/settings/plex/test-connection",
                json={
                    "base_url": "http://attacker.com/foo?bar=baz#qux",
                    "token": "tkn",
                },
            )

        assert response.status_code == 200, response.json()
        # The PlexClient constructor must have received scheme +
        # netloc ONLY — no path, no query, no fragment.
        assert captured["base_url"] == "http://attacker.com"

    @pytest.mark.asyncio
    async def test_rejects_file_scheme(self, async_client):
        """A ``file://`` URL must be rejected before PlexClient is
        constructed — the endpoint returns the SSRF error message
        inline and never makes an HTTP probe."""
        plex_constructor = AsyncMock()
        with patch("routers.settings.PlexClient", side_effect=plex_constructor):
            response = await async_client.post(
                "/api/settings/plex/test-connection",
                json={
                    "base_url": "file:///etc/passwd",
                    "token": "tkn",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "scheme" in body["error"].lower()
        plex_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_gopher_scheme(self, async_client):
        plex_constructor = AsyncMock()
        with patch("routers.settings.PlexClient", side_effect=plex_constructor):
            response = await async_client.post(
                "/api/settings/plex/test-connection",
                json={
                    "base_url": "gopher://attacker.com",
                    "token": "tkn",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "scheme" in body["error"].lower()
        plex_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_ftp_scheme(self, async_client):
        plex_constructor = AsyncMock()
        with patch("routers.settings.PlexClient", side_effect=plex_constructor):
            response = await async_client.post(
                "/api/settings/plex/test-connection",
                json={
                    "base_url": "ftp://attacker.com",
                    "token": "tkn",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "scheme" in body["error"].lower()
        plex_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_missing_hostname(self, async_client):
        plex_constructor = AsyncMock()
        with patch("routers.settings.PlexClient", side_effect=plex_constructor):
            response = await async_client.post(
                "/api/settings/plex/test-connection",
                json={
                    "base_url": "http://",
                    "token": "tkn",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "hostname" in body["error"].lower()
        plex_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_empty_base_url(self, async_client):
        """A non-empty base_url is required for the SSRF guard to
        reason about scheme/host — an empty string is rejected."""
        plex_constructor = AsyncMock()
        with patch("routers.settings.PlexClient", side_effect=plex_constructor):
            response = await async_client.post(
                "/api/settings/plex/test-connection",
                json={
                    "base_url": "",
                    "token": "tkn",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        plex_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_https_scheme_allowed(self, async_client):
        """Sanity: https is on the allowlist alongside http."""
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        captured: dict = {}

        def constructor_spy(base_url, token):
            captured["base_url"] = base_url
            return mock_client

        with patch("routers.settings.PlexClient", side_effect=constructor_spy):
            response = await async_client.post(
                "/api/settings/plex/test-connection",
                json={
                    "base_url": "https://plex.example.com:32400",
                    "token": "tkn",
                },
            )

        assert response.status_code == 200, response.json()
        assert response.json() == {"ok": True}
        assert captured["base_url"] == "https://plex.example.com:32400"
