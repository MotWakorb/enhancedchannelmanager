"""Tests for the Emby Settings endpoints (bd-8wc6q, epic bd-2cenq).

Covers ``POST /api/settings/emby/test-connection``:
  - Success: EmbyClient.get_sessions returns → {ok: True}.
  - Auth failure: EmbyClient.get_sessions raises EmbyClientError → {ok: False, error: <msg>}.
  - Network failure: EmbyClientError with transport-level cause → {ok: False, error: <msg>}.
  - Unexpected exception: wrapped to {ok: False, error: 'Unexpected error: ...'}.
  - Invalid request body: missing required fields → 422 (FastAPI Pydantic).
  - Connection failure does NOT raise HTTPException — the operator wants to
    SEE the error message inline in the UI, not get a generic 500.

Mocks: routers.settings.EmbyClient so no real HTTP is issued.
"""
from unittest.mock import AsyncMock, patch

import pytest


class TestEmbyTestConnection:
    """Tests for POST /api/settings/emby/test-connection (bd-8wc6q)."""

    @pytest.mark.asyncio
    async def test_returns_ok_true_on_success(self, async_client):
        """A successful EmbyClient.get_sessions call returns {ok: True}."""
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        with patch("routers.settings.EmbyClient", return_value=mock_client):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={
                    "base_url": "http://emby.local:8096",
                    "api_key": "valid-token",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body == {"ok": True}
        # Always close the client to release the connection pool, even on
        # success — verifies the ``finally`` branch executes.
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_ok_false_with_error_on_auth_failure(self, async_client):
        """EmbyClientError surfaces as {ok: False, error: <message>}."""
        from emby_client import EmbyClientError

        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(
            side_effect=EmbyClientError(
                "Emby /Sessions returned 401 unauthorized — check API key"
            )
        )
        mock_client.close = AsyncMock()

        with patch("routers.settings.EmbyClient", return_value=mock_client):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={
                    "base_url": "http://emby.local:8096",
                    "api_key": "bad-token",
                },
            )

        # DELIBERATE: 200 (not 4xx/5xx) so the operator sees the message
        # inline rather than as a generic HTTP error.
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "401" in body["error"] or "unauthorized" in body["error"].lower()

    @pytest.mark.asyncio
    async def test_returns_ok_false_on_network_failure(self, async_client):
        """A transport-level failure (wrapped in EmbyClientError) surfaces inline."""
        from emby_client import EmbyClientError

        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(
            side_effect=EmbyClientError("Emby request failed: connect refused")
        )
        mock_client.close = AsyncMock()

        with patch("routers.settings.EmbyClient", return_value=mock_client):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={
                    "base_url": "http://unreachable:8096",
                    "api_key": "any-token",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "connect" in body["error"].lower() or "failed" in body["error"].lower()

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_class_name(self, async_client):
        """Unexpected exception class is rendered inline rather than 500."""
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(side_effect=RuntimeError("boom"))
        mock_client.close = AsyncMock()

        with patch("routers.settings.EmbyClient", return_value=mock_client):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={
                    "base_url": "http://emby.local:8096",
                    "api_key": "any-token",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "RuntimeError" in body["error"]
        # The exception message itself is NOT surfaced — only the class name —
        # so an unexpected exception cannot leak internals to the UI.
        assert "boom" not in body["error"]

    @pytest.mark.asyncio
    async def test_uses_inline_credentials_not_saved_settings(self, async_client):
        """The endpoint constructs EmbyClient with the request-body credentials,
        not the saved settings — operators must be able to test BEFORE saving."""
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        captured: dict = {}

        def constructor_spy(base_url, api_key):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            return mock_client

        with patch("routers.settings.EmbyClient", side_effect=constructor_spy):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={
                    "base_url": "http://fresh-url:8096",
                    "api_key": "fresh-key-not-yet-saved",
                },
            )

        assert response.status_code == 200, response.json()
        assert captured["base_url"] == "http://fresh-url:8096"
        assert captured["api_key"] == "fresh-key-not-yet-saved"

    @pytest.mark.asyncio
    async def test_rejects_missing_base_url(self, async_client):
        """Missing required field surfaces FastAPI's 422 validation error."""
        response = await async_client.post(
            "/api/settings/emby/test-connection",
            json={"api_key": "any-token"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_missing_api_key(self, async_client):
        """Missing required field surfaces FastAPI's 422 validation error."""
        response = await async_client.post(
            "/api/settings/emby/test-connection",
            json={"base_url": "http://emby.local:8096"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_closes_client_even_on_error(self, async_client):
        """Verifies the ``finally: await client.close()`` branch — the
        underlying httpx connection pool must be released regardless of
        outcome to avoid leaking sockets across repeated test-connection
        clicks."""
        from emby_client import EmbyClientError

        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(
            side_effect=EmbyClientError("network down")
        )
        mock_client.close = AsyncMock()

        with patch("routers.settings.EmbyClient", return_value=mock_client):
            await async_client.post(
                "/api/settings/emby/test-connection",
                json={
                    "base_url": "http://emby.local:8096",
                    "api_key": "any-token",
                },
            )

        mock_client.close.assert_awaited_once()


class TestEmbyTestConnectionSsrfMitigation:
    """SSRF mitigation regression suite — security finding SEC-2.

    bd-r5f0c.4 backfill: the bd-8wc6q-shipped endpoint was missing the
    scheme allowlist + netloc-only reconstruction guard that the
    Dispatcharr ``/test`` endpoint already had. This suite is the
    regression target for the backfilled mitigation; the same guard
    pattern is exercised on Plex + Jellyfin in their respective
    test files.
    """

    @pytest.mark.asyncio
    async def test_strips_path_query_and_fragment(self, async_client):
        """An operator-supplied base URL with embedded path / query /
        fragment must be reduced to scheme + netloc only before
        EmbyClient sees it (security finding SEC-2)."""
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        captured: dict = {}

        def constructor_spy(base_url, api_key):
            captured["base_url"] = base_url
            return mock_client

        with patch("routers.settings.EmbyClient", side_effect=constructor_spy):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={
                    "base_url": "http://attacker.com/foo?bar=baz#qux",
                    "api_key": "k",
                },
            )

        assert response.status_code == 200, response.json()
        # The EmbyClient constructor must have received scheme +
        # netloc ONLY — no path, no query, no fragment.
        assert captured["base_url"] == "http://attacker.com"

    @pytest.mark.asyncio
    async def test_rejects_file_scheme(self, async_client):
        """A ``file://`` URL must be rejected before EmbyClient is
        constructed — the endpoint returns the SSRF error inline and
        never makes an HTTP probe."""
        emby_constructor = AsyncMock()
        with patch("routers.settings.EmbyClient", side_effect=emby_constructor):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={
                    "base_url": "file:///etc/passwd",
                    "api_key": "k",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "scheme" in body["error"].lower()
        emby_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_gopher_scheme(self, async_client):
        emby_constructor = AsyncMock()
        with patch("routers.settings.EmbyClient", side_effect=emby_constructor):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={
                    "base_url": "gopher://attacker.com",
                    "api_key": "k",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "scheme" in body["error"].lower()
        emby_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_ftp_scheme(self, async_client):
        emby_constructor = AsyncMock()
        with patch("routers.settings.EmbyClient", side_effect=emby_constructor):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={
                    "base_url": "ftp://attacker.com",
                    "api_key": "k",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "scheme" in body["error"].lower()
        emby_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_missing_hostname(self, async_client):
        emby_constructor = AsyncMock()
        with patch("routers.settings.EmbyClient", side_effect=emby_constructor):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={
                    "base_url": "http://",
                    "api_key": "k",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "hostname" in body["error"].lower()
        emby_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_empty_base_url(self, async_client):
        emby_constructor = AsyncMock()
        with patch("routers.settings.EmbyClient", side_effect=emby_constructor):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={
                    "base_url": "",
                    "api_key": "k",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        emby_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_https_scheme_allowed(self, async_client):
        """Sanity: https is on the allowlist alongside http."""
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        captured: dict = {}

        def constructor_spy(base_url, api_key):
            captured["base_url"] = base_url
            return mock_client

        with patch("routers.settings.EmbyClient", side_effect=constructor_spy):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={
                    "base_url": "https://emby.example.com:8920",
                    "api_key": "k",
                },
            )

        assert response.status_code == 200, response.json()
        assert response.json() == {"ok": True}
        assert captured["base_url"] == "https://emby.example.com:8920"
