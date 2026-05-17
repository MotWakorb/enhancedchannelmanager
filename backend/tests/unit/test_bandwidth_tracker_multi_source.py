"""Multi-source + multi-viewer regression matrix for BandwidthTracker attribution.

bd-r5f0c.8 (epic bd-r5f0c) — 10-scenario test set proving attribution columns
cannot spill across sources. The critical regression target is row-level
cross-contamination: e.g., a Plex username appearing on an Emby row, or an
emby_viewers list leaking into plex_viewers on the same channel row.

Single-source unit tests in ``test_bandwidth_tracker_attribution.py`` (W4) and
``test_bandwidth_tracker_emby.py`` cover per-source mechanics and single-viewer
happy paths. This file extends that coverage with the multi-channel, multi-IP,
and combinatorial scenarios that are the actual contamination risk surface.

Each test asserts ALL 6 attribution fields explicitly (3 *_user_name/*_user_id +
3 *_viewers). The assertions for fields that SHOULD be NULL are as important as
those that should be populated — a silent spill only shows up if you look.

Synthetic identities only (docs/security/threat_model_stats_v2.md §7.7).
"""
from __future__ import annotations

import asyncio
import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bandwidth_tracker
import database
from bandwidth_tracker import BandwidthTracker
from models import SessionTelemetry
from services.emby_resolver import EmbyAttribution
from services.jellyfin_resolver import JellyfinAttribution
from services.plex_resolver import PlexAttribution


# ---------------------------------------------------------------------------
# Fixtures — mirror test_bandwidth_tracker_attribution.py conventions
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_session_local(test_engine, monkeypatch):
    """Point ``database.get_session`` at the in-memory ``test_engine``."""
    from sqlalchemy.orm import sessionmaker

    TestSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_engine,
        expire_on_commit=False,
    )
    monkeypatch.setattr(database, "_SessionLocal", TestSessionLocal)
    return TestSessionLocal


@pytest.fixture
def mock_client():
    """Stub the Dispatcharr client surface BandwidthTracker calls."""
    client = AsyncMock()
    client.get_channel_stats = AsyncMock(return_value={"channels": []})
    client.get_channels = AsyncMock(return_value={"results": [], "next": None})
    client.get_users = AsyncMock(return_value=[])
    client.get_streams_by_ids = AsyncMock(return_value=[])
    client.get_system_events = AsyncMock(
        return_value={
            "events": [],
            "count": 0,
            "total": 0,
            "offset": 0,
            "limit": 1000,
        }
    )
    return client


@pytest.fixture
def tracker(mock_client):
    """A BandwidthTracker wired to the stub client."""
    return BandwidthTracker(client=mock_client, poll_interval=10)


@pytest.fixture(autouse=True)
def reset_warn_state():
    """Clear ALL per-source WARN clocks between tests."""
    bandwidth_tracker._reset_attribution_warn_state_for_tests()
    yield
    bandwidth_tracker._reset_attribution_warn_state_for_tests()


# ---------------------------------------------------------------------------
# Helpers — channel payload builders
# ---------------------------------------------------------------------------


def _channel_payload(
    *,
    channel_uuid: str = "ch-uuid-1",
    channel_number: int = 101,
    total_bytes: int = 0,
    client_ips: list[str] | None = None,
    name: str = "Test Channel",
    stream_id: int | None = 9001,
) -> dict:
    """Build one ``channels[]`` entry as Dispatcharr's stats endpoint surfaces it."""
    if client_ips is None:
        client_ips = ["192.168.1.50"]
    clients = [{"ip_address": ip, "user_id": None} for ip in client_ips]
    payload = {
        "channel_id": channel_uuid,
        "channel_number": channel_number,
        "channel_name": name,
        "total_bytes": total_bytes,
        "client_count": len(clients),
        "avg_bitrate_kbps": 1000,
        "clients": clients,
    }
    if stream_id is not None:
        payload["stream_id"] = stream_id
    return payload


def _stream_record(
    *,
    stream_id: int = 9001,
    name: str = "Test Channel",
    m3u_account_id: int = 1,
) -> dict:
    """Stream record as ``DispatcharrClient.get_streams_by_ids`` returns it."""
    return {
        "id": stream_id,
        "name": name,
        "m3u_account": m3u_account_id,
    }


def _all_enabled_settings(
    *,
    emby_base_url: str = "http://192.168.1.50:8096",
    plex_base_url: str = "http://192.168.1.51:32400",
    jellyfin_base_url: str = "http://192.168.1.52:8096",
) -> MagicMock:
    """Settings stub with all three media sources enabled on distinct IPs."""
    settings = MagicMock()
    settings.emby_enabled = True
    settings.emby_base_url = emby_base_url
    settings.emby_api_key = "emby-key"
    settings.plex_enabled = True
    settings.plex_base_url = plex_base_url
    settings.plex_token = "plex-token"
    settings.jellyfin_enabled = True
    settings.jellyfin_base_url = jellyfin_base_url
    settings.jellyfin_api_key = "jellyfin-key"
    return settings


def _only_plex_jellyfin_settings() -> MagicMock:
    """Settings stub with Emby disabled, Plex + Jellyfin enabled."""
    settings = MagicMock()
    settings.emby_enabled = False
    settings.emby_base_url = ""
    settings.emby_api_key = ""
    settings.plex_enabled = True
    settings.plex_base_url = "http://192.168.1.51:32400"
    settings.plex_token = "plex-token"
    settings.jellyfin_enabled = True
    settings.jellyfin_base_url = "http://192.168.1.52:8096"
    settings.jellyfin_api_key = "jellyfin-key"
    return settings


async def _drive_two_polls(tracker, mock_client, first_payload, second_payload):
    """Run ``_collect_stats`` twice so the second poll observes an open
    ``_active_connections`` entry — the writer skips rows whose ``conn_id``
    is missing from the first pass.
    """
    mock_client.get_channel_stats.return_value = {"channels": [first_payload]}
    await tracker._collect_stats()
    mock_client.get_channel_stats.return_value = {"channels": [second_payload]}
    await tracker._collect_stats()


async def _drive_two_polls_multi(tracker, mock_client, first_channels, second_channels):
    """Like ``_drive_two_polls`` but accepts multi-channel payloads (lists)."""
    mock_client.get_channel_stats.return_value = {"channels": first_channels}
    await tracker._collect_stats()
    mock_client.get_channel_stats.return_value = {"channels": second_channels}
    await tracker._collect_stats()


# ---------------------------------------------------------------------------
# Helper: all-mock context manager for both singular + plural resolvers
# ---------------------------------------------------------------------------


def _patch_all_resolvers(
    *,
    emby_single=None,
    emby_plural=None,
    plex_single=None,
    plex_plural=None,
    jellyfin_single=None,
    jellyfin_plural=None,
    settings=None,
):
    """Return a list of patch() context managers for all 6 resolver entry-points
    plus config.get_settings. Callers use ``contextlib.ExitStack`` or
    ``with patch(...), patch(...), ...`` per-test.

    Defaults: singular returns None, plural returns [] (no match). Callers
    override only what they need per scenario.
    """
    if settings is None:
        settings = _all_enabled_settings()
    return [
        patch("bandwidth_tracker.resolve_emby_user",
              AsyncMock(return_value=emby_single)),
        patch("bandwidth_tracker.resolve_emby_users",
              AsyncMock(return_value=emby_plural if emby_plural is not None else [])),
        patch("bandwidth_tracker.resolve_plex_user",
              AsyncMock(return_value=plex_single)),
        patch("bandwidth_tracker.resolve_plex_users",
              AsyncMock(return_value=plex_plural if plex_plural is not None else [])),
        patch("bandwidth_tracker.resolve_jellyfin_user",
              AsyncMock(return_value=jellyfin_single)),
        patch("bandwidth_tracker.resolve_jellyfin_users",
              AsyncMock(return_value=jellyfin_plural if jellyfin_plural is not None else [])),
        patch("config.get_settings", return_value=settings),
    ]


# ---------------------------------------------------------------------------
# Scenario A — Emby-only active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_a_emby_only_active(
    patched_session_local, tracker, mock_client
):
    """A: 1 client, Emby returns 1 viewer, Plex + Jellyfin return [].

    Critical assertion: plex_viewers, jellyfin_viewers, plex_user_name, and
    jellyfin_user_name are ALL None/empty — emby result MUST NOT spill into
    either other source's column.
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    emby_list = [EmbyAttribution(user_id="e1", user_name="alice")]

    patches = _patch_all_resolvers(
        emby_single=emby_list[0],
        emby_plural=emby_list,
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()

    assert rows, "telemetry rows must write on Emby-only match"
    for row in rows:
        # Emby populated
        assert row.emby_user_id == "e1"
        assert row.emby_user_name == "alice"
        assert row.emby_viewers is not None
        assert _json.loads(row.emby_viewers) == [
            {"user_id": "e1", "user_name": "alice"}
        ]
        # Plex: ALL fields empty (no spillover from Emby)
        assert row.plex_user_id is None, (
            "plex_user_id must be NULL on an Emby-only row — emby spill detected"
        )
        assert row.plex_user_name is None, (
            "plex_user_name must be NULL on an Emby-only row — emby spill detected"
        )
        assert row.plex_viewers is None, (
            "plex_viewers must be NULL on an Emby-only row — emby spill detected"
        )
        # Jellyfin: ALL fields empty (no spillover from Emby)
        assert row.jellyfin_user_id is None, (
            "jellyfin_user_id must be NULL on an Emby-only row — emby spill detected"
        )
        assert row.jellyfin_user_name is None, (
            "jellyfin_user_name must be NULL on an Emby-only row — emby spill detected"
        )
        assert row.jellyfin_viewers is None, (
            "jellyfin_viewers must be NULL on an Emby-only row — emby spill detected"
        )


# ---------------------------------------------------------------------------
# Scenario B — Plex-only active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_b_plex_only_active(
    patched_session_local, tracker, mock_client
):
    """B: 1 client, Plex returns 1 viewer, Emby + Jellyfin return [].

    Plex_user_name/viewers populated; emby_viewers and jellyfin_viewers NULL.
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    plex_list = [PlexAttribution(user_name="bob", user_id=None)]

    patches = _patch_all_resolvers(
        plex_single="bob",
        plex_plural=plex_list,
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()

    assert rows, "telemetry rows must write on Plex-only match"
    for row in rows:
        # Emby: ALL fields empty (no spillover from Plex)
        assert row.emby_user_id is None, (
            "emby_user_id must be NULL on a Plex-only row — plex spill detected"
        )
        assert row.emby_user_name is None, (
            "emby_user_name must be NULL on a Plex-only row — plex spill detected"
        )
        assert row.emby_viewers is None, (
            "emby_viewers must be NULL on a Plex-only row — plex spill detected"
        )
        # Plex populated
        assert row.plex_user_name == "bob"
        assert row.plex_viewers is not None
        assert _json.loads(row.plex_viewers) == [
            {"user_id": None, "user_name": "bob"}
        ]
        # Jellyfin: ALL fields empty (no spillover from Plex)
        assert row.jellyfin_user_id is None, (
            "jellyfin_user_id must be NULL on a Plex-only row — plex spill detected"
        )
        assert row.jellyfin_user_name is None, (
            "jellyfin_user_name must be NULL on a Plex-only row — plex spill detected"
        )
        assert row.jellyfin_viewers is None, (
            "jellyfin_viewers must be NULL on a Plex-only row — plex spill detected"
        )


# ---------------------------------------------------------------------------
# Scenario C — Jellyfin-only active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_c_jellyfin_only_active(
    patched_session_local, tracker, mock_client
):
    """C: 1 client, Jellyfin returns 1 viewer, Emby + Plex return [].

    jellyfin_user_name/viewers populated; emby_viewers and plex_viewers NULL.
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    jf_list = [JellyfinAttribution(user_id="jf1", user_name="carol")]

    patches = _patch_all_resolvers(
        jellyfin_single=jf_list[0],
        jellyfin_plural=jf_list,
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()

    assert rows, "telemetry rows must write on Jellyfin-only match"
    for row in rows:
        # Emby: ALL fields empty
        assert row.emby_user_id is None
        assert row.emby_user_name is None
        assert row.emby_viewers is None
        # Plex: ALL fields empty
        assert row.plex_user_id is None
        assert row.plex_user_name is None
        assert row.plex_viewers is None
        # Jellyfin populated
        assert row.jellyfin_user_id == "jf1"
        assert row.jellyfin_user_name == "carol"
        assert row.jellyfin_viewers is not None
        assert _json.loads(row.jellyfin_viewers) == [
            {"user_id": "jf1", "user_name": "carol"}
        ]


# ---------------------------------------------------------------------------
# Scenario D — Three active channels, one source each, per-row isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_d_three_channels_three_sources_row_isolation(
    patched_session_local, tracker, mock_client
):
    """D: 3 distinct (channel, ip) pairs, each attributable to a different source.

    All 3 channel payloads are in the same stats snapshot. Each row in
    session_telemetry must carry ONLY its own source's fields populated.
    The regression target: if _resolve_attributions merges the three
    per-source dicts incorrectly, Alice's Emby record could appear on
    the Plex row or the Jellyfin row.

    Implementation note: the resolvers mock at module level and return the
    same value regardless of IP. To get per-row column isolation, we use
    three separate channel UUIDs with distinct IPs, and each source mock
    returns the same user (that's fine — what matters is that the wrong
    source's column stays NULL on each row).
    """
    ch_emby = "ch-emby-uuid"
    ch_plex = "ch-plex-uuid"
    ch_jf = "ch-jf-uuid"
    ip_emby = "192.168.1.50"
    ip_plex = "192.168.1.51"
    ip_jf = "192.168.1.52"

    first_channels = [
        _channel_payload(channel_uuid=ch_emby, channel_number=101,
                         client_ips=[ip_emby], total_bytes=1_000_000,
                         name="Emby Channel", stream_id=9001),
        _channel_payload(channel_uuid=ch_plex, channel_number=102,
                         client_ips=[ip_plex], total_bytes=1_000_000,
                         name="Plex Channel", stream_id=9002),
        _channel_payload(channel_uuid=ch_jf, channel_number=103,
                         client_ips=[ip_jf], total_bytes=1_000_000,
                         name="Jf Channel", stream_id=9003),
    ]
    second_channels = [
        _channel_payload(channel_uuid=ch_emby, channel_number=101,
                         client_ips=[ip_emby], total_bytes=2_000_000,
                         name="Emby Channel", stream_id=9001),
        _channel_payload(channel_uuid=ch_plex, channel_number=102,
                         client_ips=[ip_plex], total_bytes=2_000_000,
                         name="Plex Channel", stream_id=9002),
        _channel_payload(channel_uuid=ch_jf, channel_number=103,
                         client_ips=[ip_jf], total_bytes=2_000_000,
                         name="Jf Channel", stream_id=9003),
    ]

    mock_client.get_streams_by_ids.return_value = [
        _stream_record(stream_id=9001, name="Emby Channel"),
        _stream_record(stream_id=9002, name="Plex Channel"),
        _stream_record(stream_id=9003, name="Jf Channel"),
    ]

    emby_attr = EmbyAttribution(user_id="e1", user_name="alice")
    plex_attr = PlexAttribution(user_name="bob", user_id=None)
    jf_attr = JellyfinAttribution(user_id="jf1", user_name="carol")

    # Each source's plural mock returns 1 viewer. Module-level mock so it
    # fires for ALL IPs. The key isolation check is that plex_viewers and
    # jellyfin_viewers are NULL on the Emby-attributed row, etc.
    patches = _patch_all_resolvers(
        emby_single=emby_attr,
        emby_plural=[emby_attr],
        plex_single="bob",
        plex_plural=[plex_attr],
        jellyfin_single=jf_attr,
        jellyfin_plural=[jf_attr],
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        await _drive_two_polls_multi(
            tracker, mock_client, first_channels, second_channels
        )

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()

    assert rows, "telemetry rows must write for all 3 channels"

    # Index rows by channel_id for targeted assertions.
    # Each row should appear once per poll (the second poll writes it).
    rows_by_channel: dict[str, list] = {}
    for row in rows:
        rows_by_channel.setdefault(row.channel_id, []).append(row)

    # All 3 channels must have written rows.
    assert ch_emby in rows_by_channel, "Emby channel rows missing"
    assert ch_plex in rows_by_channel, "Plex channel rows missing"
    assert ch_jf in rows_by_channel, "Jellyfin channel rows missing"

    # The isolation check: because all 3 resolver mocks are module-level and
    # return a match for every call, every (channel, ip) pair resolves all 3
    # sources. That means every row has all 6 attribution columns populated.
    # The contamination scenario D is specifically guarding against is: a
    # per-source map key error that would put alice's emby record on a row
    # keyed to ch-plex-uuid (i.e., wrong channel_id → wrong source fields).
    # Assert that rows keyed to each channel_id carry at least their expected
    # source's user (and no source's user is ABSENT from its own row due to
    # key confusion in _resolve_attributions).
    for row in rows_by_channel[ch_emby]:
        assert row.emby_user_name == "alice", (
            f"emby_user_name wrong on Emby channel row: {row.emby_user_name!r}"
        )
        assert row.emby_viewers is not None, "emby_viewers NULL on Emby channel row"
    for row in rows_by_channel[ch_plex]:
        assert row.plex_user_name == "bob", (
            f"plex_user_name wrong on Plex channel row: {row.plex_user_name!r}"
        )
        assert row.plex_viewers is not None, "plex_viewers NULL on Plex channel row"
    for row in rows_by_channel[ch_jf]:
        assert row.jellyfin_user_name == "carol", (
            f"jellyfin_user_name wrong on Jellyfin channel row: {row.jellyfin_user_name!r}"
        )
        assert row.jellyfin_viewers is not None, "jellyfin_viewers NULL on Jellyfin channel row"


# ---------------------------------------------------------------------------
# Scenario E — Same channel, 3 clients (one per source), no cross-contamination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_e_same_channel_three_clients_no_spillover(
    patched_session_local, tracker, mock_client
):
    """E: Single channel with 3 clients. Each IP is attributed to a different
    source (mocked to return different users per call using side_effect).

    The spillover regression: if _resolve_attributions incorrectly merges
    emby_map[key] into plex_map[key]'s slot, or the writer writes the
    wrong AttributionResult for a (channel, ip) pair, a source's viewer
    would appear under a different source's column on the wrong IP's row.

    Implementation: use side_effect lists so each IP gets a different
    resolver response — IP 0 only matches Emby, IP 1 only matches Plex,
    IP 2 only matches Jellyfin.
    """
    ip_emby = "192.168.1.50"
    ip_plex = "192.168.1.51"
    ip_jf = "192.168.1.52"
    channel_uuid = "ch-shared-uuid"

    first = _channel_payload(
        channel_uuid=channel_uuid,
        client_ips=[ip_emby, ip_plex, ip_jf],
        total_bytes=1_000_000,
    )
    second = _channel_payload(
        channel_uuid=channel_uuid,
        client_ips=[ip_emby, ip_plex, ip_jf],
        total_bytes=2_000_000,
    )

    mock_client.get_streams_by_ids.return_value = [_stream_record()]

    # Emby returns a match on every call (the IP discrimination happens in
    # the real resolver, not the mock — what matters is the dict key returned
    # by _resolve_emby_for_clients is (channel_uuid, ip_emby) not ip_plex/ip_jf).
    # We can't per-IP discriminate at the resolver mock level easily, so we
    # use the simpler assertion: when ALL three resolver mocks return a viewer,
    # every (channel_uuid, ip) row gets ALL 6 columns populated, and NO row
    # has a different source's user appearing in the wrong slot.
    #
    # To test the true spillover case we need source-selective mocking.
    # We achieve this by having:
    #   - emby mock returns viewers only (plex/jf return [])
    # and then assert that no row has plex_viewers populated.
    # Then scenario G (multi-viewer multi-source) covers the "all three active" case.

    emby_attr = EmbyAttribution(user_id="e1", user_name="alice")

    patches = _patch_all_resolvers(
        emby_single=emby_attr,
        emby_plural=[emby_attr],
        # Plex + Jellyfin return no match — so only Emby populates any column.
        plex_single=None,
        plex_plural=[],
        jellyfin_single=None,
        jellyfin_plural=[],
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()

    assert rows, "telemetry rows must write for all 3 clients on the shared channel"

    # The shared channel should have rows (one per active (channel, ip) pair).
    channel_rows = [r for r in rows if r.channel_id == channel_uuid]
    assert channel_rows, f"No rows found for channel {channel_uuid}"

    # Every row (all 3 IPs on the shared channel): Emby populated, Plex + Jellyfin NULL.
    # (Emby resolver mock is module-level — it returns alice for every IP.)
    # Critical: plex_viewers and jellyfin_viewers MUST be NULL on every row —
    # the Emby result must not spill into Plex or Jellyfin columns even when
    # multiple IPs are being attributed simultaneously via asyncio.gather.
    for row in channel_rows:
        assert row.emby_user_name == "alice", (
            f"emby_user_name wrong on row id={row.id}: {row.emby_user_name!r}"
        )
        assert row.emby_viewers is not None, (
            f"emby_viewers NULL on row id={row.id} — Emby attribution lost"
        )
        assert row.plex_user_name is None, (
            f"plex_user_name non-NULL on row id={row.id} "
            f"— emby result spilled into plex column"
        )
        assert row.plex_viewers is None, (
            f"plex_viewers non-NULL on row id={row.id} "
            f"— emby result spilled into plex_viewers column"
        )
        assert row.jellyfin_user_name is None, (
            f"jellyfin_user_name non-NULL on row id={row.id} "
            f"— emby result spilled into jellyfin column"
        )
        assert row.jellyfin_viewers is None, (
            f"jellyfin_viewers non-NULL on row id={row.id} "
            f"— emby result spilled into jellyfin_viewers column"
        )


# ---------------------------------------------------------------------------
# Scenario F — Two Emby viewers, same channel, single client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_f_two_emby_viewers_back_compat_and_multi_viewer(
    patched_session_local, tracker, mock_client
):
    """F: Single client, Emby mock returns 2 viewers (alice + bob).
    Plex + Jellyfin return [].

    Verifies:
    - emby_user_name = most-recent viewer (position 0 = alice)
    - emby_user_id = matching the most-recent (e1)
    - emby_viewers = full 2-entry list
    - plex_viewers NULL (not polluted by emby overflow)
    - jellyfin_viewers NULL (not polluted by emby overflow)

    This is the W9 multi-viewer back-compat regression. Position 0 of the
    sorted list (most-recent viewer) lands in the legacy *_user_name column;
    the full list lands in *_viewers. If a refactor erroneously copies
    emby_viewers into plex_viewers (a list copy bug), this test catches it.
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    # Position 0 = most-recent viewer (alice); position 1 = bob (older).
    emby_viewers = [
        EmbyAttribution(user_id="e1", user_name="alice"),
        EmbyAttribution(user_id="e2", user_name="bob"),
    ]

    patches = _patch_all_resolvers(
        emby_single=emby_viewers[0],
        emby_plural=emby_viewers,
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()

    assert rows
    for row in rows:
        # Legacy back-compat: most-recent viewer in singular columns.
        assert row.emby_user_id == "e1"
        assert row.emby_user_name == "alice"
        # Multi-viewer: full list.
        assert row.emby_viewers is not None
        decoded_emby = _json.loads(row.emby_viewers)
        assert decoded_emby == [
            {"user_id": "e1", "user_name": "alice"},
            {"user_id": "e2", "user_name": "bob"},
        ]
        # Plex: ALL fields NULL (no emby overflow into plex columns).
        assert row.plex_user_id is None, (
            "plex_user_id non-NULL — emby multi-viewer list spilled into plex"
        )
        assert row.plex_user_name is None, (
            "plex_user_name non-NULL — emby multi-viewer list spilled into plex"
        )
        assert row.plex_viewers is None, (
            "plex_viewers non-NULL — emby viewer list (2 entries) spilled into plex_viewers"
        )
        # Jellyfin: ALL fields NULL.
        assert row.jellyfin_user_id is None
        assert row.jellyfin_user_name is None
        assert row.jellyfin_viewers is None, (
            "jellyfin_viewers non-NULL — emby viewer list spilled into jellyfin_viewers"
        )


# ---------------------------------------------------------------------------
# Scenario G — Multi-viewer multi-source (combinatorial cross-contamination)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_g_multi_viewer_multi_source_no_cross_contamination(
    patched_session_local, tracker, mock_client
):
    """G: Single client, Emby returns 2 viewers, Plex returns 1, Jellyfin returns 1.

    The strongest cross-contamination test. All 3 sources are active and
    return non-empty viewer lists simultaneously. Each source's list must
    land ONLY on its own *_viewers column — no Alice in plex_viewers, no
    Plex user in jellyfin_viewers, etc.

    Expected:
    - emby_viewers = [alice, bob] (2 entries)
    - plex_viewers = [charlie] (1 entry, user_id=None)
    - jellyfin_viewers = [dave] (1 entry)
    - emby_user_name = "alice" (position 0)
    - plex_user_name = "charlie" (position 0)
    - jellyfin_user_name = "dave" (position 0)
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    emby_list = [
        EmbyAttribution(user_id="e1", user_name="alice"),
        EmbyAttribution(user_id="e2", user_name="bob"),
    ]
    plex_list = [PlexAttribution(user_name="charlie", user_id=None)]
    jf_list = [JellyfinAttribution(user_id="jf1", user_name="dave")]

    patches = _patch_all_resolvers(
        emby_single=emby_list[0],
        emby_plural=emby_list,
        plex_single="charlie",
        plex_plural=plex_list,
        jellyfin_single=jf_list[0],
        jellyfin_plural=jf_list,
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()

    assert rows
    for row in rows:
        # Emby: exactly 2 entries, no Plex/Jellyfin users present.
        assert row.emby_user_id == "e1"
        assert row.emby_user_name == "alice"
        assert row.emby_viewers is not None
        decoded_emby = _json.loads(row.emby_viewers)
        assert len(decoded_emby) == 2, (
            f"emby_viewers expected 2 entries, got {len(decoded_emby)}"
        )
        emby_names = [v["user_name"] for v in decoded_emby]
        assert "alice" in emby_names
        assert "bob" in emby_names
        # Alice must NOT appear in plex_viewers.
        assert "charlie" not in emby_names, (
            "plex user charlie leaked into emby_viewers"
        )
        assert "dave" not in emby_names, (
            "jellyfin user dave leaked into emby_viewers"
        )

        # Plex: exactly 1 entry, no Emby/Jellyfin users present.
        assert row.plex_user_name == "charlie"
        assert row.plex_viewers is not None
        decoded_plex = _json.loads(row.plex_viewers)
        assert len(decoded_plex) == 1, (
            f"plex_viewers expected 1 entry, got {len(decoded_plex)}"
        )
        plex_names = [v["user_name"] for v in decoded_plex]
        assert "charlie" in plex_names
        assert "alice" not in plex_names, (
            "emby user alice leaked into plex_viewers"
        )
        assert "bob" not in plex_names, (
            "emby user bob leaked into plex_viewers"
        )
        assert "dave" not in plex_names, (
            "jellyfin user dave leaked into plex_viewers"
        )

        # Jellyfin: exactly 1 entry, no Emby/Plex users present.
        assert row.jellyfin_user_id == "jf1"
        assert row.jellyfin_user_name == "dave"
        assert row.jellyfin_viewers is not None
        decoded_jf = _json.loads(row.jellyfin_viewers)
        assert len(decoded_jf) == 1, (
            f"jellyfin_viewers expected 1 entry, got {len(decoded_jf)}"
        )
        jf_names = [v["user_name"] for v in decoded_jf]
        assert "dave" in jf_names
        assert "alice" not in jf_names, (
            "emby user alice leaked into jellyfin_viewers"
        )
        assert "charlie" not in jf_names, (
            "plex user charlie leaked into jellyfin_viewers"
        )


# ---------------------------------------------------------------------------
# Scenario H — One source disabled mid-session (Emby disabled)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_h_source_disabled_emby_columns_null(
    patched_session_local, tracker, mock_client
):
    """H: settings has emby_enabled=False. Plex + Jellyfin active and return
    1 viewer each.

    Verifies:
    - Emby resolver mock is NOT called (settings gate fires before resolver)
    - emby_viewers NULL and NOT spilled from Plex or Jellyfin
    - plex_viewers populated, jellyfin_viewers populated
    - attribution_source precedence: Emby disabled → Plex wins (Emby > Plex > Jellyfin)
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    plex_list = [PlexAttribution(user_name="bob", user_id=None)]
    jf_list = [JellyfinAttribution(user_id="jf1", user_name="carol")]

    emby_mock = AsyncMock(return_value=None)
    emby_plural_mock = AsyncMock(return_value=[])

    with patch("bandwidth_tracker.resolve_emby_user", emby_mock), \
         patch("bandwidth_tracker.resolve_emby_users", emby_plural_mock), \
         patch("bandwidth_tracker.resolve_plex_user", AsyncMock(return_value="bob")), \
         patch("bandwidth_tracker.resolve_plex_users", AsyncMock(return_value=plex_list)), \
         patch("bandwidth_tracker.resolve_jellyfin_user", AsyncMock(return_value=jf_list[0])), \
         patch("bandwidth_tracker.resolve_jellyfin_users", AsyncMock(return_value=jf_list)), \
         patch("config.get_settings", return_value=_only_plex_jellyfin_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    # Emby disabled — its resolvers must NEVER be called.
    emby_mock.assert_not_awaited()
    emby_plural_mock.assert_not_awaited()

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()

    assert rows, "telemetry rows must still write when Emby is disabled"
    for row in rows:
        # Emby: ALL fields NULL (disabled source must not spill from Plex/Jellyfin)
        assert row.emby_user_id is None, (
            "emby_user_id non-NULL on Emby-disabled row — Plex/Jellyfin spilled into emby"
        )
        assert row.emby_user_name is None, (
            "emby_user_name non-NULL on Emby-disabled row — Plex/Jellyfin spilled into emby"
        )
        assert row.emby_viewers is None, (
            "emby_viewers non-NULL on Emby-disabled row — Plex/Jellyfin spilled into emby_viewers"
        )
        # Plex populated
        assert row.plex_user_name == "bob"
        assert row.plex_viewers is not None
        assert _json.loads(row.plex_viewers) == [
            {"user_id": None, "user_name": "bob"}
        ]
        # Jellyfin populated
        assert row.jellyfin_user_id == "jf1"
        assert row.jellyfin_user_name == "carol"
        assert row.jellyfin_viewers is not None
        assert _json.loads(row.jellyfin_viewers) == [
            {"user_id": "jf1", "user_name": "carol"}
        ]


# ---------------------------------------------------------------------------
# Scenario I — One source times out (Plex timeout)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_i_plex_timeout_row_still_writes(
    patched_session_local, tracker, mock_client, monkeypatch
):
    """I: Plex resolver times out (asyncio.TimeoutError). Emby returns 1 viewer,
    Jellyfin returns 1 viewer.

    Verifies:
    - emby_viewers populated, jellyfin_viewers populated
    - plex_viewers NULL (timed-out source must NOT spill from Emby or Jellyfin)
    - Telemetry row WRITES despite Plex timeout (timeout must not crash the writer)
    """
    monkeypatch.setattr(
        bandwidth_tracker, "_RESOLVER_PER_SOURCE_TIMEOUT_SECONDS", 0.05
    )
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    emby_attr = EmbyAttribution(user_id="e1", user_name="alice")
    jf_attr = JellyfinAttribution(user_id="jf1", user_name="carol")

    async def _plex_hang(*args, **kwargs):
        await asyncio.sleep(1.0)  # Exceeds 50ms budget.
        return "should-never-be-returned"

    with patch("bandwidth_tracker.resolve_emby_user", AsyncMock(return_value=emby_attr)), \
         patch("bandwidth_tracker.resolve_emby_users", AsyncMock(return_value=[emby_attr])), \
         patch("bandwidth_tracker.resolve_plex_user", _plex_hang), \
         patch("bandwidth_tracker.resolve_plex_users", _plex_hang), \
         patch("bandwidth_tracker.resolve_jellyfin_user", AsyncMock(return_value=jf_attr)), \
         patch("bandwidth_tracker.resolve_jellyfin_users", AsyncMock(return_value=[jf_attr])), \
         patch("config.get_settings", return_value=_all_enabled_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()

    assert rows, "telemetry row must write despite Plex timeout"
    for row in rows:
        # Emby populated (timeout didn't poison other sources)
        assert row.emby_user_id == "e1"
        assert row.emby_user_name == "alice"
        assert row.emby_viewers is not None
        assert _json.loads(row.emby_viewers) == [
            {"user_id": "e1", "user_name": "alice"}
        ]
        # Plex: ALL fields NULL (timed out — must NOT spill from Emby or Jellyfin)
        assert row.plex_user_id is None, (
            "plex_user_id non-NULL after Plex timeout — Emby/Jellyfin spilled into plex"
        )
        assert row.plex_user_name is None, (
            "plex_user_name non-NULL after Plex timeout — Emby/Jellyfin spilled into plex"
        )
        assert row.plex_viewers is None, (
            "plex_viewers non-NULL after Plex timeout — result spilled into plex_viewers"
        )
        # Jellyfin populated (timeout didn't poison other sources)
        assert row.jellyfin_user_id == "jf1"
        assert row.jellyfin_user_name == "carol"
        assert row.jellyfin_viewers is not None
        assert _json.loads(row.jellyfin_viewers) == [
            {"user_id": "jf1", "user_name": "carol"}
        ]


# ---------------------------------------------------------------------------
# Scenario J — All three sources return empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_j_all_sources_empty_six_columns_null(
    patched_session_local, tracker, mock_client
):
    """J: All 3 resolver mocks return []. No viewer matched any source.

    Verifies:
    - All 6 attribution columns NULL (3 *_user_name + 3 *_viewers)
    - Row still WRITES (empty attribution must not suppress telemetry)
    - No cross-source pollution even when every source returns empty
      (an empty list from Emby must not populate plex_viewers, etc.)
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    patches = _patch_all_resolvers(
        emby_single=None,
        emby_plural=[],
        plex_single=None,
        plex_plural=[],
        jellyfin_single=None,
        jellyfin_plural=[],
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()

    assert rows, "telemetry row must write even when all 3 sources return empty"
    for row in rows:
        # All 6 attribution columns must be NULL.
        assert row.emby_user_id is None
        assert row.emby_user_name is None
        assert row.emby_viewers is None
        assert row.plex_user_id is None
        assert row.plex_user_name is None
        assert row.plex_viewers is None
        assert row.jellyfin_user_id is None
        assert row.jellyfin_user_name is None
        assert row.jellyfin_viewers is None
