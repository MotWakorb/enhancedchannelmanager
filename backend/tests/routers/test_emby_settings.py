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

from tests.conftest import patch_ssrf_dns as _patch_ssrf_dns


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


class TestEmbyTestConnectionLoopbackDenylist:
    """Host SSRF policy on Test Connection — SEC-2 follow-up (bd-fbc50),
    re-pointed at the mode-aware chokepoint by GH #754 / bead ``0yh70``.

    The scheme allowlist alone still permitted an authenticated admin to
    point Test Connection at the ECM host's own loopback services or at
    the cloud instance-metadata IP (169.254.169.254). ``_sanitize_base_url``
    used to answer that with a hardcoded, non-mode-aware loopback +
    link-local denylist; it now delegates to
    ``security.ssrf.validate_outbound_url`` under the persisted
    ``ssrf_outbound_mode``, so:

    * link-local / IMDS stay denied in BOTH modes (always-on denylist),
    * loopback and RFC1918 follow the wizard toggle — allowed under
      ``lan_friendly`` (the shipped default, ADR-012 D4), denied under
      ``public_only``,

    and this endpoint now agrees with what POST /api/settings will accept.

    Denial coverage lives here on the shared helper (Emby path). Plex and
    Jellyfin get a single smoke each in their own files, since all three
    endpoints call the same ``_sanitize_base_url``.
    """

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata IMDS
            "http://169.254.1.1",                        # 169.254.0.0/16
            "http://[fe80::1]",                          # IPv6 link-local
            "http://[fd12:3456:789a::1]",                # IPv6 ULA
            "http://100.64.0.1",                         # CGNAT
        ],
    )
    @pytest.mark.parametrize("mode", ["lan_friendly", "public_only"])
    @pytest.mark.asyncio
    async def test_rejects_always_on_denylist_in_both_modes(
        self, async_client, base_url, mode,
    ):
        """Always-on denied hosts are rejected inline before EmbyClient is
        ever constructed — no HTTP probe is issued, in EITHER mode."""
        from security.ssrf import SSRFMode
        emby_constructor = AsyncMock()
        with patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode(mode)), \
             patch("routers.settings.EmbyClient", side_effect=emby_constructor):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={"base_url": base_url, "api_key": "k"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "host" in body["error"].lower()
        emby_constructor.assert_not_called()

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://127.0.0.1",
            "http://127.0.0.5:8096",  # anywhere in 127.0.0.0/8
            "http://[::1]",
            "http://192.168.1.50:8096",
        ],
    )
    @pytest.mark.asyncio
    async def test_rejects_toggled_band_in_public_only(self, async_client, base_url):
        """Loopback + RFC1918 are refused under the ``public_only`` opt-in."""
        from security.ssrf import SSRFMode
        emby_constructor = AsyncMock()
        with patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode.PUBLIC_ONLY), \
             patch("routers.settings.EmbyClient", side_effect=emby_constructor):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={"base_url": base_url, "api_key": "k"},
            )

        assert response.status_code == 200
        assert response.json()["ok"] is False
        emby_constructor.assert_not_called()

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://127.0.0.1",
            "http://127.0.0.5:8096",
            "http://[::1]",
        ],
    )
    @pytest.mark.asyncio
    async def test_allows_loopback_in_lan_friendly(self, async_client, base_url):
        """GH #754: loopback is reachable under the shipped default.

        An Emby sharing ECM's network namespace (gluetun, ``network_mode:
        host``, a sidecar) is only addressable as loopback. This is the
        same policy POST /api/settings now applies, so Test Connection can
        no longer prove a connection the save path would refuse.
        """
        from security.ssrf import SSRFMode
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        captured: dict = {}

        def constructor_spy(base_url_arg, api_key):
            captured["base_url"] = base_url_arg
            return mock_client

        with patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode.LAN_FRIENDLY), \
             patch("routers.settings.EmbyClient", side_effect=constructor_spy):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={"base_url": base_url, "api_key": "k"},
            )

        assert response.status_code == 200, response.json()
        assert response.json() == {"ok": True}
        assert captured["base_url"] == base_url

    @pytest.mark.asyncio
    async def test_rejects_metadata_ip_explicitly(self, async_client):
        """Explicit guard on the cloud instance-metadata IP — the
        canonical SSRF-to-credential-theft pivot."""
        emby_constructor = AsyncMock()
        with patch("routers.settings.EmbyClient", side_effect=emby_constructor):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={"base_url": "http://169.254.169.254", "api_key": "k"},
            )

        assert response.status_code == 200
        assert response.json()["ok"] is False
        emby_constructor.assert_not_called()

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://192.168.1.50:8096",  # 192.168.0.0/16
            "http://10.0.0.5:8096",  # 10.0.0.0/8
            "http://172.16.3.4:8920",  # 172.16.0.0/12
        ],
    )
    @pytest.mark.asyncio
    async def test_allows_rfc1918_private_ranges(self, async_client, base_url):
        """RFC1918 LAN ranges MUST remain reachable — Plex / Emby /
        Jellyfin legitimately run on the operator's local network.
        Blocking these would break the primary use case."""
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        captured: dict = {}

        def constructor_spy(base_url_arg, api_key):
            captured["base_url"] = base_url_arg
            return mock_client

        with patch("routers.settings.EmbyClient", side_effect=constructor_spy):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={"base_url": base_url, "api_key": "k"},
            )

        assert response.status_code == 200, response.json()
        assert response.json() == {"ok": True}
        assert captured["base_url"] == base_url

    @pytest.mark.asyncio
    async def test_allows_public_host(self, async_client):
        """A normal public host passes the denylist (sanity)."""
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        captured: dict = {}

        def constructor_spy(base_url_arg, api_key):
            captured["base_url"] = base_url_arg
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

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["lan_friendly", "public_only"])
    @pytest.mark.asyncio
    async def test_resolved_name_pointing_at_metadata_is_blocked(self, async_client, mode):
        """A hostname that RESOLVES to IMDS is blocked in BOTH modes.

        Defence against DNS pointing an innocuous name at the metadata
        endpoint. We patch the chokepoint's resolver so the test does not
        depend on live DNS. (Was ``..._pointing_at_loopback_...`` and patched
        ``routers.settings.socket.getaddrinfo``; the router no longer resolves
        anything itself — ``security.ssrf`` owns resolution — and loopback is
        now mode-dependent, so the always-denied target is what proves the
        rebinding guard unconditionally. The loopback half is below.)
        """
        from security.ssrf import SSRFMode
        emby_constructor = AsyncMock()
        with patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode(mode)), \
             _patch_ssrf_dns("169.254.169.254"), \
             patch("routers.settings.EmbyClient", side_effect=emby_constructor):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={"base_url": "http://sneaky.attacker.example", "api_key": "k"},
            )

        assert response.status_code == 200
        assert response.json()["ok"] is False
        emby_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolved_name_pointing_at_loopback_is_blocked_in_public_only(
        self, async_client,
    ):
        """The same rebinding guard covers the toggled band under public_only."""
        from security.ssrf import SSRFMode
        emby_constructor = AsyncMock()
        with patch("security.ssrf.get_ssrf_mode", return_value=SSRFMode.PUBLIC_ONLY), \
             _patch_ssrf_dns("127.0.0.1"), \
             patch("routers.settings.EmbyClient", side_effect=emby_constructor):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={"base_url": "http://sneaky.attacker.example", "api_key": "k"},
            )

        assert response.status_code == 200
        assert response.json()["ok"] is False
        emby_constructor.assert_not_called()

    @pytest.mark.asyncio
    async def test_one_denied_record_rejects_the_whole_host(self, async_client):
        """Split-horizon DNS: one public + one IMDS record -> rejected.

        §9.4 item 3 — never "pick the allowed one". Asserted here because the
        settings surface now shares the chokepoint rather than carrying its
        own resolver loop.
        """
        emby_constructor = AsyncMock()
        with _patch_ssrf_dns("93.184.216.34", "169.254.169.254"), \
             patch("routers.settings.EmbyClient", side_effect=emby_constructor):
            response = await async_client.post(
                "/api/settings/emby/test-connection",
                json={"base_url": "http://split.attacker.example", "api_key": "k"},
            )

        assert response.status_code == 200
        assert response.json()["ok"] is False
        emby_constructor.assert_not_called()
