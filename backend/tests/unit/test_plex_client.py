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
        # bd-2zcvf: ``@grandparentTitle`` carries the Plex Live TV channel
        # surface; the fixture's grandparentTitle="ESPN" must land on
        # ``now_playing_channel_name``. ``@parentTitle`` is absent in this
        # fixture so the parent_title field is None.
        assert alice.now_playing_channel_name == "ESPN"
        assert alice.now_playing_parent_title is None
        # lastViewedAt epoch 1747396800 → 2025-05-16T12:00:00+00:00
        assert isinstance(alice.last_activity_date, datetime)
        assert alice.last_activity_date == datetime(2025, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_live_tv_program_in_title_with_channel_in_grandparent():
    """bd-2zcvf regression target: Plex Live TV from a DVR/Tuner reports
    the PROGRAM currently airing in ``Video/@title`` and the CHANNEL name
    in ``Video/@grandparentTitle``. Without extracting ``@grandparentTitle``,
    the downstream resolver has nothing to compare an operator's ECM
    ``channel_name`` against and falls through to "User #N" in Stats.

    This regression target locks in the extraction shape: the program
    title lands on ``now_playing_item_name`` (back-compat), the channel
    name lands on ``now_playing_channel_name`` (new), and the parent
    title lands on ``now_playing_parent_title`` (new). Subsequent
    resolver tests (``TestTier1ChannelNameMatch`` /
    ``TestTier3FuzzyMatch``) exercise the matching against the new
    fields directly.
    """
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    # Live TV from Plex DVR: title is the program, grandparentTitle is
    # the channel name as Plex sees it. parentTitle in some setups is
    # the "season" Plex synthesizes for Live TV grouping, often the
    # channel name again — included here so the test covers both
    # candidate channel-name surfaces.
    xml = (
        '<MediaContainer size="1">'
        '<Video ratingKey="55555" type="episode" '
        'title="Saturday Night Live" '
        'grandparentTitle="NBC" '
        'parentTitle="Season 50" '
        'lastViewedAt="1747396800">'
        '<User id="user-bob-2" title="bob"/>'
        '<Player address="192.168.1.51" state="playing"/>'
        '</Video>'
        '</MediaContainer>'
    )
    try:
        fake_resp = _xml_response(200, xml)
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()
        assert len(sessions) == 1
        session = sessions[0]
        assert session.now_playing_item_name == "Saturday Night Live"
        assert session.now_playing_channel_name == "NBC"
        assert session.now_playing_parent_title == "Season 50"
        assert session.user_name == "bob"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_sessions_vod_has_no_grandparent_or_parent_titles():
    """VOD (movies) lack ``@grandparentTitle`` / ``@parentTitle`` —
    confirm the extraction lands ``None`` for both rather than
    raising on a missing attribute."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    xml = _load_fixture("sessions_vod.xml")
    try:
        fake_resp = _xml_response(200, xml)
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            sessions = await client.get_sessions()
        assert len(sessions) == 1
        assert sessions[0].now_playing_item_name == "The Matrix"
        assert sessions[0].now_playing_channel_name is None
        assert sessions[0].now_playing_parent_title is None
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


# ---------------------------------------------------------------------------
# bd-ma6r3 EPG-tier tests — DVR/section discovery + EPG entry parsing
# ---------------------------------------------------------------------------


from plex_client import (  # noqa: E402 — group bd-ma6r3 additions below
    PlexEpgEntry,
    _epg_entry_is_current,
    _parse_epg_xml,
)


# ----- PlexEpgEntry parsing


def test_parse_epg_xml_emits_one_entry_per_media_element():
    """The parser must flatten each ``<Media>`` under each ``<Video>``
    into a separate :class:`PlexEpgEntry`. A single Video carrying four
    upcoming airings must produce four entries — the resolver's
    time-window filter then keeps only the one currently airing.
    """
    xml = _load_fixture("epg_section_multi_media_per_video.xml")
    entries = _parse_epg_xml(xml)
    assert len(entries) == 4
    # All four entries share the same grandparent_title and call_sign
    # (operator's "Fox 11 News at Five" airs at four times on 11.1).
    for entry in entries:
        assert entry.grandparent_title == "Fox 11 News at Five"
        assert entry.channel_call_sign == "11.1 | FOX: WLUK Green Bay"
        assert entry.channel_identifier == "11.1"
        assert entry.channel_vcn == "11.1"
        assert entry.protocol == "livetv"
        assert entry.begins_at is not None
        assert entry.ends_at is not None


def test_parse_epg_xml_handles_empty_container():
    """An empty ``<MediaContainer/>`` (section has no airings) must
    produce ``[]`` without raising — this is the normal state of the
    Movies section in the PO's all-Dispatcharr setup."""
    xml = '<?xml version="1.0"?><MediaContainer size="0"/>'
    assert _parse_epg_xml(xml) == []


def test_parse_epg_xml_skips_videos_without_grandparent_title():
    """A ``<Video>`` element missing ``@grandparentTitle`` cannot be
    cross-referenced (the resolver's lookup key is grandparent_title),
    so the parser must skip it entirely rather than emit entries with
    an empty grandparent."""
    xml = (
        '<?xml version="1.0"?>'
        '<MediaContainer size="1">'
        '<Video ratingKey="orphan" type="episode" title="Orphan">'
        '<Media id="0" channelCallSign="X" protocol="livetv"'
        ' beginsAt="1779148800" endsAt="1779159600" />'
        '</Video>'
        '</MediaContainer>'
    )
    assert _parse_epg_xml(xml) == []


def test_parse_epg_xml_preserves_individual_media_time_windows():
    """When a Video has multiple Media elements with DIFFERENT begins/ends,
    the parser must preserve each pair on its own entry. The resolver
    then independently filters each entry's time window."""
    xml = _load_fixture("epg_section_multi_media_per_video.xml")
    entries = _parse_epg_xml(xml)
    begins = sorted(e.begins_at for e in entries)
    # All four begin times distinct (24h spacing in the fixture).
    assert len({b for b in begins}) == 4


def test_parse_epg_xml_raises_on_malformed_xml():
    """Malformed XML must raise :class:`PlexClientError` for the cache
    layer to absorb via stale-fallback — not return ``[]`` silently."""
    with pytest.raises(PlexClientError):
        _parse_epg_xml("<not really xml")


# ----- Time-window filter


def test_epg_entry_is_current_accepts_window_match():
    """An entry whose time window covers ``now`` and protocol is
    ``"livetv"`` is current."""
    now = datetime(2026, 5, 19, 0, 0, 0, tzinfo=timezone.utc)
    entry = PlexEpgEntry(
        grandparent_title="MLB Baseball",
        channel_call_sign="8.1 | NBC: KGW Portland",
        channel_identifier="8.1",
        channel_title="8.1 NBC",
        channel_vcn="8.1",
        begins_at=datetime(2026, 5, 18, 22, 0, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 5, 19, 1, 0, 0, tzinfo=timezone.utc),
        protocol="livetv",
    )
    assert _epg_entry_is_current(entry, now=now) is True


def test_epg_entry_is_current_rejects_protocol_mismatch():
    """An entry whose protocol is not ``"livetv"`` is never current —
    defensive against mixed-provider Plex setups that surface non-live
    EPG entries."""
    now = datetime(2026, 5, 19, 0, 0, 0, tzinfo=timezone.utc)
    entry = PlexEpgEntry(
        grandparent_title="Some Show",
        channel_call_sign="X",
        channel_identifier="X",
        channel_title="X",
        channel_vcn="X",
        begins_at=datetime(2026, 5, 18, 22, 0, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 5, 19, 1, 0, 0, tzinfo=timezone.utc),
        protocol="vod",
    )
    assert _epg_entry_is_current(entry, now=now) is False


def test_epg_entry_is_current_rejects_outside_window():
    """An entry whose window is entirely in the past (or future) is not
    current."""
    now = datetime(2026, 5, 19, 0, 0, 0, tzinfo=timezone.utc)
    past = PlexEpgEntry(
        grandparent_title="Past Show",
        channel_call_sign="X",
        channel_identifier="X",
        channel_title="X",
        channel_vcn="X",
        begins_at=datetime(2026, 5, 18, 18, 0, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 5, 18, 19, 0, 0, tzinfo=timezone.utc),
        protocol="livetv",
    )
    future = PlexEpgEntry(
        grandparent_title="Future Show",
        channel_call_sign="X",
        channel_identifier="X",
        channel_title="X",
        channel_vcn="X",
        begins_at=datetime(2026, 5, 19, 3, 0, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 5, 19, 4, 0, 0, tzinfo=timezone.utc),
        protocol="livetv",
    )
    assert _epg_entry_is_current(past, now=now) is False
    assert _epg_entry_is_current(future, now=now) is False


def test_epg_entry_is_current_rejects_missing_times():
    """Missing begins_at OR ends_at can't be window-checked — defensively
    treat the entry as not-current."""
    now = datetime(2026, 5, 19, 0, 0, 0, tzinfo=timezone.utc)
    entry = PlexEpgEntry(
        grandparent_title="X",
        channel_call_sign="X",
        channel_identifier="X",
        channel_title="X",
        channel_vcn="X",
        begins_at=None,
        ends_at=None,
        protocol="livetv",
    )
    assert _epg_entry_is_current(entry, now=now) is False


# ----- DVR/section discovery


@pytest.mark.asyncio
async def test_get_dvr_keys_returns_keys_from_livetv_dvrs():
    """``get_dvr_keys`` extracts the ``key`` attribute from each
    ``<Dvr>`` element under ``<MediaContainer>``."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    try:
        xml = _load_fixture("livetv_dvrs.xml")
        fake_resp = _xml_response(200, xml)
        with patch.object(client._client, "request",
                          AsyncMock(return_value=fake_resp)):
            keys = await client.get_dvr_keys()
        assert keys == ["7"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_dvr_keys_returns_empty_for_empty_container():
    """When ``/livetv/dvrs`` returns an empty container (no DVRs
    configured), the method returns ``[]`` without raising — this is
    a normal state on non-LiveTV Plex installs."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    try:
        fake_resp = _xml_response(200, '<MediaContainer size="0"/>')
        with patch.object(client._client, "request",
                          AsyncMock(return_value=fake_resp)):
            keys = await client.get_dvr_keys()
        assert keys == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_dvr_section_keys_returns_keys_for_dvr():
    """``get_dvr_section_keys(dvr_key)`` returns the section
    ``<Directory key="...">`` attributes for the provided DVR."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    try:
        xml = _load_fixture("dvr_sections.xml")
        fake_resp = _xml_response(200, xml)
        with patch.object(client._client, "request",
                          AsyncMock(return_value=fake_resp)) as request_mock:
            keys = await client.get_dvr_section_keys("7")
        # Per the fixture: Movies=1, News=4, Shows=2, Sports=3.
        assert sorted(keys) == ["1", "2", "3", "4"]
        # The URL must include the DVR-keyed EPG provider path so a
        # typo in path construction would be caught here.
        request_mock.assert_awaited_once()
        called_url = request_mock.await_args.args[1]
        assert "tv.plex.providers.epg.xmltv:7/sections" in called_url
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_section_epg_entries_parses_fixture():
    """``get_section_epg_entries`` returns one entry per ``<Media>``
    element across all ``<Video>`` elements in the section, with no
    time-window filtering — that's the caller's responsibility."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    try:
        xml = _load_fixture("epg_section_one_current_airing.xml")
        fake_resp = _xml_response(200, xml)
        with patch.object(client._client, "request",
                          AsyncMock(return_value=fake_resp)) as request_mock:
            entries = await client.get_section_epg_entries(
                dvr_key="7", section_key="3",
            )
        # Three entries (one per Video, each Video has one Media).
        assert len(entries) == 3
        # Ensure the ?type=4 query was sent.
        called_kwargs = request_mock.await_args.kwargs
        assert called_kwargs.get("params") == {"type": "4"}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_section_epg_entries_raises_on_non_2xx():
    """Non-2xx propagates as :class:`PlexClientError` so the cache
    layer can wrap with stale-fallback."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    try:
        fake_resp = _xml_response(503, "")
        with patch.object(client._client, "request",
                          AsyncMock(return_value=fake_resp)):
            with pytest.raises(PlexClientError):
                await client.get_section_epg_entries(
                    dvr_key="7", section_key="3",
                )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_current_live_epg_channels_filters_to_current_airings():
    """``get_current_live_epg_channels`` orchestrates DVR + section
    discovery and applies the time-window filter. Across the fixture's
    three airings only the middle one (NBC, beginsAt=1779148800,
    endsAt=1779159600) covers ``now=1779150000``."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    try:
        # Stage three responses in order: DVRs, sections, airings.
        dvrs_xml = _load_fixture("livetv_dvrs.xml")
        sections_xml = (
            '<?xml version="1.0"?><MediaContainer size="1">'
            '<Directory key="3" type="show" title="Sports" />'
            '</MediaContainer>'
        )
        airings_xml = _load_fixture("epg_section_one_current_airing.xml")
        responses = [
            _xml_response(200, dvrs_xml),
            _xml_response(200, sections_xml),
            _xml_response(200, airings_xml),
        ]
        with patch.object(client._client, "request",
                          AsyncMock(side_effect=responses)):
            now = datetime(2026, 5, 19, 0, 20, 0, tzinfo=timezone.utc)
            entries = await client.get_current_live_epg_channels(now=now)
        assert len(entries) == 1
        assert entries[0].grandparent_title == "MLB Baseball"
        assert entries[0].channel_call_sign == "8.1 | NBC: KGW Portland"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_current_live_epg_channels_returns_empty_when_no_dvrs():
    """No DVRs configured → empty list (no error). The resolver's
    EPG-tier gate then quietly does nothing."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    try:
        fake_resp = _xml_response(200, '<MediaContainer size="0"/>')
        with patch.object(client._client, "request",
                          AsyncMock(return_value=fake_resp)):
            entries = await client.get_current_live_epg_channels()
        assert entries == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_current_live_epg_channels_continues_on_per_dvr_section_failure():
    """When one DVR's section listing fails, the sweep must log + continue
    so a broken DVR doesn't disable EPG attribution for the others —
    multi-DVR friendliness for the operator. Here we simulate the
    single-DVR case with the sections call failing; the result should
    be an empty list (no airings discovered) but NO exception."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    try:
        dvrs_xml = _load_fixture("livetv_dvrs.xml")
        responses = [
            _xml_response(200, dvrs_xml),
            _xml_response(500, ""),  # sections call fails
        ]
        with patch.object(client._client, "request",
                          AsyncMock(side_effect=responses)):
            entries = await client.get_current_live_epg_channels()
        assert entries == []
    finally:
        await client.close()


# ----- is_live extraction


@pytest.mark.asyncio
async def test_get_sessions_extracts_is_live_flag_from_video_live_attribute():
    """bd-ma6r3: ``@live="1"`` on the ``<Video>`` element marks Plex Live
    TV sessions. The resolver's EPG-tier gate runs only for sessions
    with ``is_live=True``."""
    client = PlexClient(base_url="http://plex.local:32400", api_key="token")
    try:
        xml = (
            '<?xml version="1.0"?>'
            '<MediaContainer size="2">'
            '<Video ratingKey="live-1" type="episode"'
            ' title="MLB Baseball" grandparentTitle="MLB Baseball"'
            ' live="1" lastViewedAt="1779150000">'
            '<User id="2" title="MotWakorb" />'
            '<Player address="172.16.0.2" />'
            '</Video>'
            '<Video ratingKey="vod-1" type="movie"'
            ' title="Dune" lastViewedAt="1779150000">'
            '<User id="1" title="alice" />'
            '<Player address="172.16.0.3" />'
            '</Video>'
            '</MediaContainer>'
        )
        fake_resp = _xml_response(200, xml)
        with patch.object(client._client, "request",
                          AsyncMock(return_value=fake_resp)):
            sessions = await client.get_sessions()
        live = next(s for s in sessions if s.session_id == "live-1")
        vod = next(s for s in sessions if s.session_id == "vod-1")
        assert live.is_live is True
        assert vod.is_live is False
    finally:
        await client.close()
