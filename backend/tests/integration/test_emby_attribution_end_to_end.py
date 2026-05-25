"""End-to-end Emby attribution integration test (bd-jn30g, epic bd-2cenq).

Every existing Emby test mocks at exactly ONE layer boundary:

* ``tests/unit/test_emby_client.py`` mocks the httpx transport but never
  touches the cache or resolver.
* ``tests/services/test_emby_cache.py`` mocks ``EmbyClient`` and never
  touches the resolver.
* ``tests/services/test_emby_resolver.py`` mocks
  ``get_cached_emby_sessions`` and never touches the client.
* ``tests/unit/test_bandwidth_tracker_emby.py`` mocks ``resolve_emby_user``
  and never touches the cache or client.

That layering is good unit hygiene, but it leaves ONE thing unverified:
that the layers actually COMPOSE. A bug in the PascalCase→snake_case
mapping, the settings gate, the cache key, or the tier-match thresholds
could pass every per-layer suite and still break the real pipeline,
because no test drives a raw Emby ``/Sessions`` JSON payload all the way
through to a resolved attribution.

This file closes that gap. It wires:

    settings (real get_settings stub)
      → resolve_emby_users / resolve_emby_user   [REAL — services.emby_resolver]
        → get_cached_emby_sessions               [REAL — services.emby_cache]
          → EmbyClient.get_sessions              [MOCKED — httpx transport only]

so only the outermost HTTP boundary is faked. The Emby server is a
mocked ``httpx.AsyncClient.request`` returning a canned ``/Sessions``
payload; everything between the JSON and the ``EmbyAttribution`` is the
production code path.

The two bead-mandated scenarios:

1. **Full attribution flow** — a mocked Emby ``/Sessions`` payload +
   matching ECM stream/channel produces the correct
   ``EmbyAttribution`` through the un-mocked resolver + cache.
2. **Settings-disabled path** — ``emby_enabled=False`` issues ZERO Emby
   HTTP requests (the transport mock is never awaited).
"""
from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import observability
from services import emby_cache, emby_resolver
from services.emby_resolver import EmbyAttribution, resolve_emby_user, resolve_emby_users


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_emby_module_state():
    """Clear cache + resolver module-level state before and after each test.

    The cache holds a single-slot module global and the resolver holds a
    module-level DNS cache + no-match WARN rate-limit map. Without a reset
    a populated cache from one test produces a false cache-hit in the
    next, and a resolved hostname leaks an IP into a later test that
    expects a different one. Observability metrics are reset so the
    cache's ``.inc()`` / ``.time()`` calls have a live registry to write
    to (the cache references metrics unconditionally on the hit path).
    """
    emby_cache._reset_for_tests()
    emby_resolver._reset_for_tests()
    observability.reset_for_tests()
    observability.install_metrics()
    yield
    emby_cache._reset_for_tests()
    emby_resolver._reset_for_tests()
    observability.reset_for_tests()


def _enabled_settings(base_url: str = "http://192.168.1.50:8096") -> MagicMock:
    """Settings stub: Emby enabled with an IP-literal base URL.

    IP-literal so the resolver's server-IP extraction does not need a DNS
    mock — ``_resolve_emby_server_ip`` returns the literal directly. The
    cache reads ``emby_enabled`` / ``emby_base_url`` / ``emby_api_key``;
    the resolver reads ``emby_base_url`` for the server-IP gate.
    """
    settings = MagicMock()
    settings.emby_enabled = True
    settings.emby_base_url = base_url
    settings.emby_api_key = "integration-key"
    return settings


def _disabled_settings() -> MagicMock:
    settings = MagicMock()
    settings.emby_enabled = False
    settings.emby_base_url = ""
    settings.emby_api_key = ""
    return settings


def _sessions_payload() -> list[dict]:
    """Canned Emby ``/Sessions`` response (upstream PascalCase shape).

    Two sessions: ``alice`` is live-watching ESPN (the live-TV
    ``"<number> | <name>"`` item-name format Emby uses); ``bob`` is idle
    (no ``NowPlayingItem``). Field names match the real Emby API so the
    client's mapping layer is exercised verbatim.
    """
    return [
        {
            "Id": "sess-alice",
            "UserId": "uid-alice",
            "UserName": "alice",
            "RemoteEndPoint": "203.0.113.7",
            "NowPlayingItem": {
                "Name": "408 | ESPN",
                "ChannelName": "ESPN",
                "ChannelNumber": "408",
            },
            "LastActivityDate": "2026-05-16T14:32:01.0000000Z",
        },
        {
            "Id": "sess-bob",
            "UserId": "uid-bob",
            "UserName": "bob",
            "RemoteEndPoint": "203.0.113.8",
            "LastActivityDate": "2026-05-16T14:30:00.0000000Z",
        },
    ]


def _mock_emby_transport(payload: list[dict], status_code: int = 200) -> AsyncMock:
    """Patch ``httpx.AsyncClient.request`` so the REAL ``EmbyClient`` runs
    against a faked Emby ``/Sessions`` HTTP boundary.

    Returns the request mock so callers can assert call count (e.g. the
    settings-disabled test asserts it was never awaited). Only the
    transport is faked — ``EmbyClient.get_sessions`` (status checks, JSON
    decode, mapping), the cache, and the resolver all run for real.
    """
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json = lambda: payload
    return AsyncMock(return_value=response)


@contextlib.contextmanager
def _stack(settings: MagicMock, request_mock: AsyncMock):
    """Patch the full Emby attribution stack for an end-to-end run.

    Both :mod:`services.emby_cache` and :mod:`services.emby_resolver` bind
    ``get_settings`` at import time via ``from config import get_settings``,
    so patching ``config.get_settings`` would NOT rebind the names those
    modules already resolved — each module's local binding must be patched
    directly (the same module-level patch discipline ``backend/CLAUDE.md``
    documents for routers). The httpx transport is patched so the REAL
    ``EmbyClient`` issues its request against the canned payload.
    """
    with patch.object(emby_cache, "get_settings", return_value=settings), patch.object(
        emby_resolver, "get_settings", return_value=settings
    ), patch.object(httpx.AsyncClient, "request", request_mock):
        yield


# ---------------------------------------------------------------------------
# Scenario 1 — full attribution flow through the real cache + resolver
# ---------------------------------------------------------------------------


class TestEmbyAttributionEndToEnd:
    """Mocked Emby server + real cache + real resolver → attribution."""

    @pytest.mark.asyncio
    async def test_live_tv_match_resolves_attribution_through_full_stack(self):
        """A raw Emby /Sessions payload flows through the un-mocked cache +
        resolver to the correct EmbyAttribution.

        The ECM session is keyed on the Emby server IP + channel "ESPN" /
        number "408"; the resolver's Tier-1 (channel-name) match must
        accept alice's ``"408 | ESPN"`` session. This proves the whole
        chain composes: JSON mapping → cache store → tier match →
        attribution — none of which is exercised together by any
        per-layer suite.
        """
        request_mock = _mock_emby_transport(_sessions_payload())

        with _stack(_enabled_settings(), request_mock):
            attribution = await resolve_emby_user(
                "192.168.1.50",  # == Emby server IP
                "US: ESPN FHD",  # Dispatcharr stream name (fuzzy-only, not needed for Tier 1)
                ecm_channel_name="ESPN",
                ecm_channel_number="408",
            )

        assert attribution is not None
        assert attribution.user_id == "uid-alice"
        assert attribution.user_name == "alice"
        # client_ip is the REAL requesting-device IP from RemoteEndPoint,
        # normalized to a bare IP — proves the mapping carried it through.
        assert attribution.client_ip == "203.0.113.7"
        # Exactly one upstream HTTP request was issued through the real
        # client (the cache fired it once on the cold miss).
        request_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_idle_sessions_produce_no_match(self):
        """A payload of only idle sessions (no NowPlayingItem) resolves to
        None — the resolver must not attribute an idle viewer to a channel.
        """
        idle_only = [
            {
                "Id": "sess-bob",
                "UserId": "uid-bob",
                "UserName": "bob",
                "RemoteEndPoint": "203.0.113.8",
                "LastActivityDate": "2026-05-16T14:30:00.0000000Z",
            }
        ]
        request_mock = _mock_emby_transport(idle_only)

        with _stack(_enabled_settings(), request_mock):
            attribution = await resolve_emby_user(
                "192.168.1.50",
                "ESPN",
                ecm_channel_name="ESPN",
                ecm_channel_number="408",
            )

        assert attribution is None

    @pytest.mark.asyncio
    async def test_multi_viewer_same_channel_returns_all_through_full_stack(self):
        """Two Emby sessions on the same channel → the plural resolver
        returns BOTH viewers, most-recent first — proving the bd-r5f0c.9
        multi-viewer path composes with the real cache + JSON mapping.
        """
        payload = [
            {
                "Id": "sess-alice",
                "UserId": "uid-alice",
                "UserName": "alice",
                "RemoteEndPoint": "203.0.113.7",
                "NowPlayingItem": {"Name": "408 | ESPN", "ChannelName": "ESPN"},
                "LastActivityDate": "2026-05-16T14:30:00.0000000Z",
            },
            {
                "Id": "sess-carol",
                "UserId": "uid-carol",
                "UserName": "carol",
                "RemoteEndPoint": "203.0.113.9",
                "NowPlayingItem": {"Name": "408 | ESPN", "ChannelName": "ESPN"},
                # More recent — must sort to position 0.
                "LastActivityDate": "2026-05-16T14:35:00.0000000Z",
            },
        ]
        request_mock = _mock_emby_transport(payload)

        with _stack(_enabled_settings(), request_mock):
            viewers = await resolve_emby_users(
                "192.168.1.50",
                "ESPN",
                ecm_channel_name="ESPN",
            )

        assert [v.user_name for v in viewers] == ["carol", "alice"]
        assert {v.user_id for v in viewers} == {"uid-carol", "uid-alice"}

    @pytest.mark.asyncio
    async def test_cache_collapses_repeated_resolves_to_one_http_call(self):
        """Two resolves inside the TTL window issue exactly ONE Emby HTTP
        request — the real cache's TTL is exercised end-to-end, not just
        in the cache unit suite with a mocked client.
        """
        request_mock = _mock_emby_transport(_sessions_payload())

        with _stack(_enabled_settings(), request_mock):
            first = await resolve_emby_user(
                "192.168.1.50", "ESPN", ecm_channel_name="ESPN"
            )
            second = await resolve_emby_user(
                "192.168.1.50", "ESPN", ecm_channel_name="ESPN"
            )

        assert first is not None and second is not None
        assert first.user_name == second.user_name == "alice"
        # Cache hit on the second resolve → still exactly one HTTP request.
        request_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# Scenario 2 — settings-disabled path issues ZERO Emby HTTP requests
# ---------------------------------------------------------------------------


class TestEmbyDisabledIssuesNoHttp:
    """``emby_enabled=False`` short-circuits before any Emby network I/O."""

    @pytest.mark.asyncio
    async def test_disabled_resolver_issues_zero_http_requests(self):
        """With Emby disabled, resolving must return None AND never touch
        the Emby HTTP transport. The settings gate lives in the cache; this
        proves it holds when reached through the real resolver entrypoint.
        """
        request_mock = _mock_emby_transport(_sessions_payload())

        with _stack(_disabled_settings(), request_mock):
            attribution = await resolve_emby_user(
                "192.168.1.50",
                "ESPN",
                ecm_channel_name="ESPN",
                ecm_channel_number="408",
            )

        assert attribution is None
        request_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disabled_cache_entrypoint_issues_zero_http_requests(self):
        """The cache entrypoint itself returns [] with zero HTTP when
        disabled — the lowest level of the settings gate, verified with a
        real (un-mocked) EmbyClient that would otherwise fire a request.
        """
        request_mock = _mock_emby_transport(_sessions_payload())

        with _stack(_disabled_settings(), request_mock):
            sessions = await emby_cache.get_cached_emby_sessions()

        assert sessions == []
        request_mock.assert_not_awaited()
