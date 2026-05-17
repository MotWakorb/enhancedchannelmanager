"""
Unit tests for the Jellyfin API client (bd-r5f0c.3, epic bd-r5f0c).

Jellyfin is an Emby fork. The client mirrors emby_client.py in structure
(async httpx, ``[JELLYFIN]`` log prefix, dataclass session shape,
``JellyfinClientError`` on any auth/network/non-2xx failure) but with the
critical auth-header difference:

  Jellyfin: ``Authorization: MediaBrowser Token="<api_key>"``
  Emby:     ``X-Emby-Token: <api_key>``

The test that asserts the Authorization header format is the key Jellyfin-
specific contract test that proves the fork is correct.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from jellyfin_client import JellyfinClient, JellyfinClientError, JellyfinSession

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "jellyfin"


def _load_fixture(name: str) -> list[dict]:
    return json.loads((_FIXTURE_DIR / name).read_text())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _response(status_code: int, json_body=None) -> AsyncMock:
    """Build a mock ``httpx.Response`` with the given status + JSON body."""
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = lambda: json_body if json_body is not None else []
    return resp


def _jellyfin_sessions_payload() -> list[dict]:
    """Canned Jellyfin /Sessions response. Jellyfin's NowPlayingItem.Name
    for live-TV is often just the channel name (e.g. "ESPN") WITHOUT the
    "<number> | " pipe prefix that Emby uses. This is the primary
    Jellyfin-specific difference from Emby in the response shape."""
    return [
        {
            "Id": "jf-session-abc-123",
            "UserId": "jf-user-uuid-1",
            "UserName": "alice",
            "RemoteEndPoint": "192.168.1.50",
            "NowPlayingItem": {
                "Name": "ESPN",
                "Type": "TvChannel",
                "ChannelNumber": "408",
            },
            "LastActivityDate": "2026-05-17T10:32:01.0000000Z",
        },
        {
            "Id": "jf-session-def-456",
            "UserId": "jf-user-uuid-2",
            "UserName": "bob",
            "RemoteEndPoint": "192.168.1.51",
            # Idle session — no NowPlayingItem at all.
            "LastActivityDate": "2026-05-17T10:30:00.0000000Z",
        },
    ]


# ---------------------------------------------------------------------------
# Construction & URL normalization
# ---------------------------------------------------------------------------


def test_base_url_trailing_slash_is_stripped():
    """The client must normalize ``http://jf/`` and ``http://jf`` to the
    same canonical form so downstream string concat (``base + "/Sessions"``)
    never produces ``http://jf//Sessions``."""
    client_with_slash = JellyfinClient(base_url="http://jellyfin.local:8096/", api_key="k")
    client_without_slash = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="k")
    assert client_with_slash.base_url == "http://jellyfin.local:8096"
    assert client_without_slash.base_url == "http://jellyfin.local:8096"


def test_base_url_strips_only_trailing_slash_not_path():
    """If the operator configures a sub-path (reverse-proxy setups),
    only the trailing slash gets stripped — the path itself is preserved."""
    client = JellyfinClient(base_url="http://proxy.example.com/jellyfin/", api_key="k")
    assert client.base_url == "http://proxy.example.com/jellyfin"


# ---------------------------------------------------------------------------
# get_sessions — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sessions_maps_jellyfin_payload_to_dataclass():
    """The PascalCase Jellyfin payload is mapped to the snake_case
    ``JellyfinSession`` dataclass. A live-TV session has NowPlayingItem.Name
    as bare channel name (no pipe prefix — Jellyfin-specific). Idle sessions
    have no NowPlayingItem at all."""
    client = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="key-xyz")
    try:
        fake_resp = _response(200, _jellyfin_sessions_payload())
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()

        assert len(sessions) == 2

        # Playing session — live-TV item with Jellyfin's bare channel name
        # format (no "<channel_number> | " prefix, unlike Emby).
        alice = sessions[0]
        assert isinstance(alice, JellyfinSession)
        assert alice.session_id == "jf-session-abc-123"
        assert alice.user_id == "jf-user-uuid-1"
        assert alice.user_name == "alice"
        assert alice.remote_endpoint == "192.168.1.50"
        assert alice.now_playing_item_name == "ESPN"
        assert alice.now_playing_channel_name is None
        assert alice.channel_number == "408"
        assert alice.last_activity_date == "2026-05-17T10:32:01.0000000Z"

        # Idle session — NowPlayingItem absent → every now-playing field None.
        bob = sessions[1]
        assert bob.session_id == "jf-session-def-456"
        assert bob.user_name == "bob"
        assert bob.now_playing_item_name is None
        assert bob.now_playing_channel_name is None
        assert bob.channel_number is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_loads_fixture_one_active():
    """The sessions_one_active.json fixture loads correctly and produces
    a JellyfinSession with a bare channel name."""
    client = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="k")
    try:
        payload = _load_fixture("sessions_one_active.json")
        fake_resp = _response(200, payload)
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()
        assert len(sessions) == 1
        assert sessions[0].now_playing_item_name == "ESPN"
        assert sessions[0].user_name == "alice"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_loads_fixture_multi_user_same_channel():
    """The sessions_multi_user_same_channel.json fixture produces two
    sessions on the same channel — the disambiguation fixture."""
    client = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="k")
    try:
        payload = _load_fixture("sessions_multi_user_same_channel.json")
        fake_resp = _response(200, payload)
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()
        assert len(sessions) == 2
        assert {s.user_name for s in sessions} == {"bob", "carol"}
        assert all(s.now_playing_item_name == "ESPN" for s in sessions)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_loads_fixture_idle_only():
    """The sessions_idle_only.json fixture is an empty array — idle server."""
    client = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="k")
    try:
        payload = _load_fixture("sessions_idle_only.json")
        fake_resp = _response(200, payload)
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()
        assert sessions == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_playing_item_without_channel_number_is_none():
    """VOD playback populates ``NowPlayingItem.Name`` but NOT ``ChannelNumber``
    — the resulting ``JellyfinSession.channel_number`` must be ``None`` so
    the resolver's tier-2 channel_number match does not false-positive."""
    client = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="k")
    try:
        payload = [
            {
                "Id": "vod-1",
                "UserId": "user-vod",
                "UserName": "vod_viewer",
                "RemoteEndPoint": "192.168.1.99",
                "NowPlayingItem": {
                    "Name": "The Matrix",
                },
                "LastActivityDate": "2026-05-17T15:00:00.0000000Z",
            },
        ]
        fake_resp = _response(200, payload)
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()
        assert len(sessions) == 1
        assert sessions[0].now_playing_item_name == "The Matrix"
        assert sessions[0].channel_number is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_empty_response_returns_empty_list():
    """An empty Jellyfin response (no active sessions) must produce ``[]``,
    not raise — downstream resolver code expects an iterable."""
    client = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="k")
    try:
        fake_resp = _response(200, [])
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()
        assert sessions == []
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Auth header contract (Jellyfin-specific — the critical fork test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sessions_sends_mediabrowser_token_header_and_hits_sessions_path():
    """The outbound request must carry ``Authorization: MediaBrowser Token="<key>"``
    and target ``<base>/Sessions``.

    This is THE critical Jellyfin-specific test that proves the fork from
    Emby is correct. Emby uses ``X-Emby-Token: <key>``; Jellyfin requires
    ``Authorization: MediaBrowser Token="<key>"`` with the value quoted.
    A wrong header would result in 401 responses from the Jellyfin server.
    """
    client = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="jf-api-key-xyz")
    try:
        fake_resp = _response(200, [])
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            await client.get_sessions()

        call = request_mock.await_args
        assert call.args[0] == "GET"
        assert call.args[1] == "http://jellyfin.local:8096/Sessions"
        # The auth header is set on the client instance (default_headers),
        # not passed per-request kwargs. Verify the client's headers include it.
        assert client._client.headers.get("authorization") == 'MediaBrowser Token="jf-api-key-xyz"'
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_auth_header_quotes_key_value():
    """The Authorization header must quote the API key value with double
    quotes: ``MediaBrowser Token="<key>"``. Jellyfin's server strictly
    requires the quotes around the token value; omitting them produces 401."""
    client = JellyfinClient(base_url="http://jf.local", api_key="my-secret-key")
    try:
        auth_header = client._client.headers.get("authorization")
        # Must be exactly: MediaBrowser Token="my-secret-key"
        assert auth_header == 'MediaBrowser Token="my-secret-key"'
        # And must NOT be the Emby format
        assert "X-Emby-Token" not in dict(client._client.headers)
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# get_sessions — error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sessions_raises_on_401_unauthorized():
    """A 401 from Jellyfin (bad/expired API key) must surface as
    ``JellyfinClientError`` so the caller can flag the connection as broken."""
    client = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="bad-key")
    try:
        fake_resp = _response(401, {"error": "unauthorized"})
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            with pytest.raises(JellyfinClientError) as exc_info:
                await client.get_sessions()
        assert "401" in str(exc_info.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_raises_on_non_2xx_response():
    """Non-2xx responses other than 401 (e.g., 500 from Jellyfin) must also
    surface as ``JellyfinClientError``."""
    client = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="k")
    try:
        fake_resp = _response(500, {"error": "internal"})
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            with pytest.raises(JellyfinClientError) as exc_info:
                await client.get_sessions()
        assert "500" in str(exc_info.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_raises_on_network_error():
    """Network-level failures (httpx.ConnectError, ReadTimeout, etc.) must
    be wrapped as ``JellyfinClientError`` with the original exception preserved
    in the cause chain."""
    client = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="k")
    try:
        underlying = httpx.ConnectError("connection refused")
        request_mock = AsyncMock(side_effect=underlying)
        with patch.object(client._client, "request", request_mock):
            with pytest.raises(JellyfinClientError) as exc_info:
                await client.get_sessions()
        assert exc_info.value.__cause__ is underlying
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_connection_returns_true_on_success():
    """True means 'creds work' — the Settings UI renders a success state."""
    client = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="k")
    try:
        fake_resp = _response(200, [])
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            ok = await client.test_connection()
        assert ok is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_test_connection_returns_false_on_jellyfin_client_error():
    """Any failure that ``get_sessions`` raises must be swallowed by
    ``test_connection`` and reported as ``False``."""
    client = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="bad-key")
    try:
        fake_resp = _response(401)
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            ok = await client.test_connection()
        assert ok is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_test_connection_returns_false_on_network_error():
    """Network-error path also collapses to ``False`` for the Settings UI."""
    client = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="k")
    try:
        request_mock = AsyncMock(side_effect=httpx.ConnectError("nope"))
        with patch.object(client._client, "request", request_mock):
            ok = await client.test_connection()
        assert ok is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_close_releases_connection_pool():
    """``close()`` calls ``aclose()`` on the underlying httpx client so
    sockets are not leaked in test teardown or process shutdown."""
    client = JellyfinClient(base_url="http://jellyfin.local:8096", api_key="k")
    with patch.object(client._client, "aclose", new_callable=AsyncMock) as aclose_mock:
        await client.close()
    aclose_mock.assert_awaited_once()
