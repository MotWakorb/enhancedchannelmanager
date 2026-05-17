"""
Unit tests for the Emby API client (bd-6c0g6).

The Emby client mirrors the dispatcharr_client structure (async httpx,
``[EMBY]`` log prefix, dataclass session shape, ``EmbyClientError`` on
any auth/network/non-2xx failure) but is purpose-built for reading the
operator's Emby ``/Sessions`` feed used downstream by the user-attribution
resolver (epic bd-2cenq).

These tests are the foundational contract — bd-gpeot (TTL cache) and
bd-8wc6q (Settings UI test-connection wiring) both depend on them.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from emby_client import EmbyClient, EmbyClientError, EmbySession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _response(status_code: int, json_body=None) -> AsyncMock:
    """Build a mock ``httpx.Response`` with the given status + JSON body."""
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = lambda: json_body if json_body is not None else []
    return resp


def _emby_sessions_payload() -> list[dict]:
    """Canned Emby /Sessions response. Field names match the upstream Emby
    API exactly (PascalCase) — the client's job is to map them onto the
    snake_case dataclass."""
    return [
        {
            "Id": "session-abc-123",
            "UserId": "user-uuid-1",
            "UserName": "alice",
            "RemoteEndPoint": "192.168.1.50",
            "NowPlayingItem": {
                "Name": "408 | ESPN",
                "ChannelName": "BBC One HD",
                "ChannelNumber": "408",
            },
            "LastActivityDate": "2026-05-16T14:32:01.0000000Z",
        },
        {
            "Id": "session-def-456",
            "UserId": "user-uuid-2",
            "UserName": "bob",
            "RemoteEndPoint": "192.168.1.51",
            # Idle session — no NowPlayingItem at all.
            "LastActivityDate": "2026-05-16T14:30:00.0000000Z",
        },
    ]


# ---------------------------------------------------------------------------
# Construction & URL normalization
# ---------------------------------------------------------------------------


def test_base_url_trailing_slash_is_stripped():
    """The client must normalize ``http://emby/`` and ``http://emby`` to the
    same canonical form so downstream string concat (``base + "/Sessions"``)
    never produces ``http://emby//Sessions``."""
    client_with_slash = EmbyClient(base_url="http://emby.local:8096/", api_key="k")
    client_without_slash = EmbyClient(base_url="http://emby.local:8096", api_key="k")
    assert client_with_slash.base_url == "http://emby.local:8096"
    assert client_without_slash.base_url == "http://emby.local:8096"


def test_base_url_strips_only_trailing_slash_not_path():
    """If the operator configures a sub-path (some reverse-proxy setups),
    only the trailing slash gets stripped — the path itself is preserved."""
    client = EmbyClient(base_url="http://proxy.example.com/emby/", api_key="k")
    assert client.base_url == "http://proxy.example.com/emby"


# ---------------------------------------------------------------------------
# get_sessions — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sessions_maps_emby_payload_to_dataclass():
    """The PascalCase Emby payload is mapped to the snake_case
    ``EmbySession`` dataclass, including the nested ``NowPlayingItem``
    fields and the ``None`` defaults for idle sessions."""
    client = EmbyClient(base_url="http://emby.local:8096", api_key="key-xyz")
    try:
        fake_resp = _response(200, _emby_sessions_payload())
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()

        assert len(sessions) == 2
        # Playing session — live-TV item with the
        # ``"<channel_number> | <channel_name>"`` Name format Emby uses
        # for live channels. ChannelNumber is extracted as a string
        # (preserved verbatim from upstream) to support sub-channel
        # numbers like ``"408.1"``.
        alice = sessions[0]
        assert isinstance(alice, EmbySession)
        assert alice.session_id == "session-abc-123"
        assert alice.user_id == "user-uuid-1"
        assert alice.user_name == "alice"
        assert alice.remote_endpoint == "192.168.1.50"
        assert alice.now_playing_item_name == "408 | ESPN"
        assert alice.now_playing_channel_name == "BBC One HD"
        assert alice.channel_number == "408"
        assert alice.last_activity_date == "2026-05-16T14:32:01.0000000Z"

        # Idle session — NowPlayingItem absent → every now-playing
        # field defaults to None, including channel_number.
        bob = sessions[1]
        assert bob.session_id == "session-def-456"
        assert bob.user_name == "bob"
        assert bob.now_playing_item_name is None
        assert bob.now_playing_channel_name is None
        assert bob.channel_number is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_playing_item_without_channel_number_is_none():
    """VOD playback (movies / episodes) populates ``NowPlayingItem.Name``
    but NOT ``ChannelNumber`` — the resulting ``EmbySession.channel_number``
    must be ``None`` so the resolver's tier-2 channel_number match does
    not false-positive on a movie session.
    """
    client = EmbyClient(base_url="http://emby.local:8096", api_key="k")
    try:
        payload = [
            {
                "Id": "vod-1",
                "UserId": "user-vod",
                "UserName": "vod_viewer",
                "RemoteEndPoint": "192.168.1.99",
                "NowPlayingItem": {
                    "Name": "The Matrix",
                    # No ChannelName, no ChannelNumber — VOD
                },
                "LastActivityDate": "2026-05-16T15:00:00.0000000Z",
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
async def test_get_sessions_sends_x_emby_token_header_and_hits_sessions_path():
    """The outbound request must carry ``X-Emby-Token: <api_key>`` and
    target ``<base>/Sessions`` — the contract the Emby server enforces."""
    client = EmbyClient(base_url="http://emby.local:8096", api_key="key-xyz")
    try:
        fake_resp = _response(200, [])
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            await client.get_sessions()

        # Verify URL + headers
        call = request_mock.await_args
        assert call.args[0] == "GET"
        assert call.args[1] == "http://emby.local:8096/Sessions"
        sent_headers = call.kwargs["headers"]
        assert sent_headers["X-Emby-Token"] == "key-xyz"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_empty_response_returns_empty_list():
    """An empty Emby response (no active sessions) must produce ``[]``,
    not raise — downstream resolver code expects an iterable."""
    client = EmbyClient(base_url="http://emby.local:8096", api_key="k")
    try:
        fake_resp = _response(200, [])
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()
        assert sessions == []
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# get_sessions — error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sessions_raises_on_401_unauthorized():
    """A 401 from Emby (bad/expired API key) must surface as
    ``EmbyClientError`` so the caller can flag the connection as broken
    rather than treat an empty list as 'no sessions playing'."""
    client = EmbyClient(base_url="http://emby.local:8096", api_key="bad-key")
    try:
        fake_resp = _response(401, {"error": "unauthorized"})
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            with pytest.raises(EmbyClientError) as exc_info:
                await client.get_sessions()
        # The 401 status should appear in the error message so the operator
        # can disambiguate auth failure from network failure in logs.
        assert "401" in str(exc_info.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_raises_on_non_2xx_response():
    """Non-2xx responses other than 401 (e.g., 500 from Emby) must also
    surface as ``EmbyClientError`` — the client never silently returns
    empty on a server error."""
    client = EmbyClient(base_url="http://emby.local:8096", api_key="k")
    try:
        fake_resp = _response(500, {"error": "internal"})
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            with pytest.raises(EmbyClientError) as exc_info:
                await client.get_sessions()
        assert "500" in str(exc_info.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_raises_on_network_error():
    """Network-level failures (httpx.ConnectError, ReadTimeout, etc.) must
    be wrapped as ``EmbyClientError`` with the original exception preserved
    in the cause chain so loggers can still capture root cause."""
    client = EmbyClient(base_url="http://emby.local:8096", api_key="k")
    try:
        underlying = httpx.ConnectError("connection refused")
        request_mock = AsyncMock(side_effect=underlying)
        with patch.object(client._client, "request", request_mock):
            with pytest.raises(EmbyClientError) as exc_info:
                await client.get_sessions()
        # The underlying httpx exception must be in the cause chain so we
        # don't lose root-cause diagnostics.
        assert exc_info.value.__cause__ is underlying
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_connection_returns_true_on_success():
    """The Settings UI's 'Test Connection' button calls this — True means
    'creds work', False means 'something is wrong, show error'."""
    client = EmbyClient(base_url="http://emby.local:8096", api_key="k")
    try:
        fake_resp = _response(200, [])
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            ok = await client.test_connection()
        assert ok is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_test_connection_returns_false_on_emby_client_error():
    """Any failure that ``get_sessions`` raises must be swallowed by
    ``test_connection`` and reported as ``False`` — never propagate to the
    Settings UI handler, which only knows how to render bool."""
    client = EmbyClient(base_url="http://emby.local:8096", api_key="bad-key")
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
    """Network-error path also collapses to ``False`` for the Settings UI —
    the operator sees a single 'connection failed' regardless of whether
    the cause was DNS, auth, or a 5xx."""
    client = EmbyClient(base_url="http://emby.local:8096", api_key="k")
    try:
        request_mock = AsyncMock(side_effect=httpx.ConnectError("nope"))
        with patch.object(client._client, "request", request_mock):
            ok = await client.test_connection()
        assert ok is False
    finally:
        await client.close()
