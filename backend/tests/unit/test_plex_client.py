"""Unit tests for the Plex API client (bd-r5f0c.2).

The Plex client mirrors the emby_client structure (async httpx, ``[PLEX]``
log prefix, dataclass session shape, ``PlexClientError`` on any
auth/network/non-2xx/XML failure) but is purpose-built for reading the
operator's Plex ``/status/sessions`` feed used downstream by the
user-attribution resolver (epic bd-r5f0c).

Key Plex-specific differences from Emby:
- Auth header: ``X-Plex-Token`` (not ``X-Emby-Token``)
- Endpoint: ``/status/sessions`` (not ``/Sessions``)
- Response format: XML (not JSON)
- ``PlexSession.last_activity_date`` is a ``datetime`` (not ISO string)
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from plex_client import PlexClient, PlexClientError, PlexSession

# Path to disk fixtures per project convention (docs/pytest_conventions.md).
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "plex"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _xml_response(status_code: int, xml_body: str = "") -> AsyncMock:
    """Build a mock ``httpx.Response`` with the given status + XML text."""
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = xml_body
    return resp


def _load_fixture(name: str) -> str:
    """Load an XML fixture from the plex fixtures directory."""
    return (FIXTURES_DIR / name).read_text()


# ---------------------------------------------------------------------------
# Construction & URL normalization
# ---------------------------------------------------------------------------


def test_base_url_trailing_slash_is_stripped():
    """The client must normalize ``http://plex/`` and ``http://plex`` to the
    same canonical form so downstream string concat
    (``base + "/status/sessions"``) never produces double slashes."""
    client_with_slash = PlexClient(base_url="http://plex.local:32400/", api_key="token")
    client_without_slash = PlexClient(base_url="http://plex.local:32400", api_key="token")
    assert client_with_slash.base_url == "http://plex.local:32400"
    assert client_without_slash.base_url == "http://plex.local:32400"


def test_base_url_strips_only_trailing_slash_not_path():
    """If the operator configures a sub-path (reverse-proxy setups),
    only the trailing slash gets stripped — the path itself is preserved."""
    client = PlexClient(base_url="http://proxy.example.com/plex/", api_key="token")
    assert client.base_url == "http://proxy.example.com/plex"


# ---------------------------------------------------------------------------
# get_sessions — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sessions_maps_xml_to_dataclass():
    """The Plex XML response is mapped to the PlexSession dataclass
    correctly, including parsing lastViewedAt epoch seconds into datetime."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token-xyz")
    xml = _load_fixture("sessions_one_active.xml")
    try:
        fake_resp = _xml_response(200, xml)
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()

        assert len(sessions) == 1
        alice = sessions[0]
        assert isinstance(alice, PlexSession)
        assert alice.session_id == "12345"
        assert alice.user_id == "user-alice-1"
        assert alice.user_name == "alice"
        assert alice.remote_endpoint == "192.168.1.50"
        assert alice.now_playing_item_name == "408 | ESPN"
        # lastViewedAt epoch 1747396800 → 2025-05-16T12:00:00+00:00
        assert isinstance(alice.last_activity_date, datetime)
        assert alice.last_activity_date == datetime(2025, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_multi_user_fixture():
    """Two-user fixture yields two PlexSession instances."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    xml = _load_fixture("sessions_multi_user_same_channel.xml")
    try:
        fake_resp = _xml_response(200, xml)
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()

        assert len(sessions) == 2
        names = {s.user_name for s in sessions}
        assert names == {"alice", "bob"}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_sends_x_plex_token_header_and_hits_correct_path():
    """The outbound request must carry ``X-Plex-Token: <api_key>`` and
    target ``<base>/status/sessions`` — the contract the Plex server enforces."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="my-token")
    try:
        fake_resp = _xml_response(200, "<MediaContainer size=\"0\"/>")
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            await client.get_sessions()

        call = request_mock.await_args
        assert call.args[0] == "GET"
        assert call.args[1] == "http://plex.local:32400/status/sessions"
        sent_headers = call.kwargs["headers"]
        assert sent_headers["X-Plex-Token"] == "my-token"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_empty_mediascontainer_returns_empty_list():
    """An empty ``<MediaContainer/>`` (no active sessions) must produce ``[]``,
    not raise — downstream resolver code expects an iterable."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    xml = _load_fixture("sessions_idle_only.xml")
    try:
        fake_resp = _xml_response(200, xml)
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()
        assert sessions == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_vod_session_parsed():
    """VOD session (movie title, no pipe-suffix) is parsed correctly.
    ``now_playing_item_name`` captures the movie title."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    xml = _load_fixture("sessions_vod.xml")
    try:
        fake_resp = _xml_response(200, xml)
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()
        assert len(sessions) == 1
        assert sessions[0].now_playing_item_name == "The Matrix"
        assert sessions[0].user_name == "carol"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_skips_element_without_user_child():
    """A ``<Video>`` without a ``<User>`` child (anonymous local session) must
    be skipped — the resolver cannot attribute an anonymous session."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    xml = """<MediaContainer size="1">
      <Video ratingKey="1" title="ESPN" lastViewedAt="1747396800">
        <Player address="192.168.1.50" state="playing"/>
      </Video>
    </MediaContainer>"""
    try:
        fake_resp = _xml_response(200, xml)
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()
        assert sessions == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_missing_last_viewed_at_yields_none_datetime():
    """A ``<Video>`` without ``lastViewedAt`` attribute produces
    ``PlexSession.last_activity_date is None`` — tiebreak still works
    (None loses to any populated datetime)."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    xml = """<MediaContainer size="1">
      <Video ratingKey="2" title="CNN HD">
        <User id="uid-1" title="dave"/>
        <Player address="192.168.1.55" state="playing"/>
      </Video>
    </MediaContainer>"""
    try:
        fake_resp = _xml_response(200, xml)
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()
        assert len(sessions) == 1
        assert sessions[0].last_activity_date is None
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# get_sessions — error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sessions_raises_on_401_unauthorized():
    """A 401 from Plex (bad/expired token) must surface as
    ``PlexClientError`` so the caller can flag the connection as broken."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="bad-token")
    try:
        fake_resp = _xml_response(401, "")
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            with pytest.raises(PlexClientError) as exc_info:
                await client.get_sessions()
        assert "401" in str(exc_info.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_raises_on_non_2xx_response():
    """Non-2xx responses other than 401 (e.g. 500 from Plex) must also
    surface as ``PlexClientError`` — the client never silently returns
    empty on a server error."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    try:
        fake_resp = _xml_response(500, "")
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            with pytest.raises(PlexClientError) as exc_info:
                await client.get_sessions()
        assert "500" in str(exc_info.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_raises_on_network_error():
    """Network-level failures (httpx.ConnectError, ReadTimeout, etc.) must
    be wrapped as ``PlexClientError`` with the original exception preserved
    in the cause chain."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    try:
        underlying = httpx.ConnectError("connection refused")
        request_mock = AsyncMock(side_effect=underlying)
        with patch.object(client._client, "request", request_mock):
            with pytest.raises(PlexClientError) as exc_info:
                await client.get_sessions()
        assert exc_info.value.__cause__ is underlying
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_raises_on_malformed_xml():
    """A response that is not valid XML must surface as
    ``PlexClientError`` — the Plex server can return error pages as HTML."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    try:
        fake_resp = _xml_response(200, "<not valid xml <<<<")
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            with pytest.raises(PlexClientError) as exc_info:
                await client.get_sessions()
        assert "malformed" in str(exc_info.value).lower() or "xml" in str(exc_info.value).lower()
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_connection_returns_true_on_success():
    """The Settings UI 'Test Connection' button calls this — True means
    'token works', False means 'something is wrong, show error'."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    try:
        fake_resp = _xml_response(200, "<MediaContainer size=\"0\"/>")
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            ok = await client.test_connection()
        assert ok is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_test_connection_returns_false_on_plex_client_error():
    """Any failure that ``get_sessions`` raises must be swallowed by
    ``test_connection`` and reported as ``False``."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="bad-token")
    try:
        fake_resp = _xml_response(401, "")
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            ok = await client.test_connection()
        assert ok is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_test_connection_returns_false_on_network_error():
    """Network-error path also collapses to ``False`` for the Settings UI."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    try:
        request_mock = AsyncMock(side_effect=httpx.ConnectError("nope"))
        with patch.object(client._client, "request", request_mock):
            ok = await client.test_connection()
        assert ok is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_close_releases_pool():
    """``close()`` must call ``aclose()`` on the underlying httpx client
    to release the connection pool."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    with patch.object(client._client, "aclose", new_callable=AsyncMock) as aclose_mock:
        await client.close()
    aclose_mock.assert_awaited_once()
