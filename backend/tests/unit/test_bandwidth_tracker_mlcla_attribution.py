"""bd-mlcla persisted-path reconciliation tests for ``BandwidthTracker``.

The networking-agnostic attribution redesign moves anti-collapse /
anti-broadcast from the resolver IP gate into a per-channel set
reconciliation (``services.attribution_reconciler``). This suite pins the
PO constraints on the PERSISTED path (``session_telemetry`` rows written by
``_collect_stats``):

* #1 bridge-NAT source attributes (the live TSN5/MotWakorb case).
* #3 anti-collapse — two distinct browser-direct viewers never collapse
  onto one user.
* #4 anti-broadcast — one user never written to two distinct connections.
* #7 Option-B rollup for genuinely-ambiguous browser-direct groups.

The reconciler unit tests (``test_attribution_reconciler``) cover the pure
logic; this suite proves the wiring writes the right telemetry rows.

Synthetic identities only — ``docs/security/threat_model_stats_v2.md`` §7.7.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bandwidth_tracker
import database
from bandwidth_tracker import BandwidthTracker
from models import SessionTelemetry
from services.emby_resolver import EmbyAttribution


# ---------------------------------------------------------------------------
# Fixtures (mirror test_bandwidth_tracker_attribution.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_session_local(test_engine, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False,
        bind=test_engine, expire_on_commit=False,
    )
    monkeypatch.setattr(database, "_SessionLocal", TestSessionLocal)
    return TestSessionLocal


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.get_channel_stats = AsyncMock(return_value={"channels": []})
    client.get_channels = AsyncMock(return_value={"results": [], "next": None})
    client.get_users = AsyncMock(return_value=[])
    client.get_streams_by_ids = AsyncMock(return_value=[])
    client.get_system_events = AsyncMock(
        return_value={"events": [], "count": 0, "total": 0, "offset": 0, "limit": 1000}
    )
    return client


@pytest.fixture
def tracker(mock_client):
    return BandwidthTracker(client=mock_client, poll_interval=10)


@pytest.fixture(autouse=True)
def reset_warn_state():
    bandwidth_tracker._reset_attribution_warn_state_for_tests()
    yield
    bandwidth_tracker._reset_attribution_warn_state_for_tests()


def _emby_only_settings(base_url="http://172.16.0.19:8096"):
    s = MagicMock()
    s.emby_enabled = True
    s.emby_base_url = base_url
    s.emby_api_key = "k"
    s.plex_enabled = False
    s.jellyfin_enabled = False
    s.trusted_media_networks = []
    return s


def _channel(*, channel_uuid, client_specs, total_bytes, url=None):
    """Build a Dispatcharr channels[] entry with per-client metadata."""
    clients = [
        {
            "ip_address": spec["ip"],
            "client_id": spec.get("client_id", spec["ip"]),
            "connected_at": spec.get("connected_at"),
            "user_id": None,
        }
        for spec in client_specs
    ]
    payload = {
        "channel_id": channel_uuid,
        "channel_number": 200,
        "channel_name": "TSN5",
        "total_bytes": total_bytes,
        "client_count": len(clients),
        "avg_bitrate_kbps": 1000,
        "clients": clients,
        "stream_id": 9001,
    }
    if url is not None:
        payload["url"] = url
    return payload


def _stream_record():
    return {"id": 9001, "name": "CA: TSN5", "m3u_account": 1}


async def _drive_two_polls(tracker, mock_client, first, second):
    mock_client.get_channel_stats.return_value = {"channels": [first]}
    await tracker._collect_stats()
    mock_client.get_channel_stats.return_value = {"channels": [second]}
    await tracker._collect_stats()


def _rows(session_local, channel_uuid):
    session = session_local()
    try:
        return [
            r for r in session.query(SessionTelemetry).all()
            if r.channel_id == channel_uuid
        ]
    finally:
        session.close()


# ---------------------------------------------------------------------------
# #1 — bridge-NAT source attributes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_nat_source_attributes_persisted(patched_session_local, tracker, mock_client):
    """A connection NAT'd through 172.18.0.1 (NOT the configured Emby server
    172.16.0.19) writes a telemetry row attributed to the matched user."""
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    ch_uuid = "ch-nat"
    specs = [{"ip": "172.18.0.1", "client_id": "browser1"}]
    first = _channel(channel_uuid=ch_uuid, client_specs=specs, total_bytes=1_000_000,
                     url="http://dispatcharr/proxy/ts/stream/ch-nat")
    second = _channel(channel_uuid=ch_uuid, client_specs=specs, total_bytes=2_000_000,
                      url="http://dispatcharr/proxy/ts/stream/ch-nat")

    viewers = [EmbyAttribution(user_id="uid-mw", user_name="MotWakorb")]
    with patch("bandwidth_tracker.resolve_emby_users", AsyncMock(return_value=viewers)), \
         patch("bandwidth_tracker.resolve_emby_user", AsyncMock(return_value=viewers[0])), \
         patch("bandwidth_tracker.resolve_plex_users", AsyncMock(return_value=[])), \
         patch("bandwidth_tracker.resolve_plex_user", AsyncMock(return_value=None)), \
         patch("bandwidth_tracker.resolve_jellyfin_users", AsyncMock(return_value=[])), \
         patch("bandwidth_tracker.resolve_jellyfin_user", AsyncMock(return_value=None)), \
         patch("config.get_settings", return_value=_emby_only_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    rows = _rows(patched_session_local, ch_uuid)
    assert rows
    assert all(r.emby_user_name == "MotWakorb" for r in rows), (
        f"NAT'd browser-direct must attribute: {[(r.session_id, r.emby_user_name) for r in rows]}"
    )


# ---------------------------------------------------------------------------
# #3 anti-collapse + #7 Option-B rollup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_distinct_browser_viewers_rollup_not_collapse(
    patched_session_local, tracker, mock_client
):
    """Two distinct browser-direct connections (two NAT'd IPs in the same
    UNKNOWN priority bucket) + two Emby users → Option-B rollup on BOTH
    rows; the same single user is NEVER pinned to both (anti-collapse, the
    jkaisersoze regression).

    Note: the PERSISTED path keys telemetry rows by (channel, ip), so two
    connections behind the SAME NAT IP collapse to one row there — the
    per-connection rollup for same-IP connections is only fully expressed
    on the live Stats path (full client_id granularity). Here we use two
    distinct same-bucket IPs, the realistic two-device browser-direct case.
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    ch_uuid = "ch-amb"
    specs = [
        {"ip": "172.18.0.1", "client_id": "a", "connected_at": 1.0},
        {"ip": "172.19.0.1", "client_id": "b", "connected_at": 2.0},
    ]
    first = _channel(channel_uuid=ch_uuid, client_specs=specs, total_bytes=1_000_000,
                     url="http://dispatcharr/proxy/ts/stream/x")
    second = _channel(channel_uuid=ch_uuid, client_specs=specs, total_bytes=2_000_000,
                      url="http://dispatcharr/proxy/ts/stream/x")

    viewers = [
        EmbyAttribution(user_id="u1", user_name="alice"),
        EmbyAttribution(user_id="u2", user_name="bob"),
    ]
    with patch("bandwidth_tracker.resolve_emby_users", AsyncMock(return_value=viewers)), \
         patch("bandwidth_tracker.resolve_emby_user", AsyncMock(return_value=viewers[0])), \
         patch("bandwidth_tracker.resolve_plex_users", AsyncMock(return_value=[])), \
         patch("bandwidth_tracker.resolve_plex_user", AsyncMock(return_value=None)), \
         patch("bandwidth_tracker.resolve_jellyfin_users", AsyncMock(return_value=[])), \
         patch("bandwidth_tracker.resolve_jellyfin_user", AsyncMock(return_value=None)), \
         patch("config.get_settings", return_value=_emby_only_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    rows = _rows(patched_session_local, ch_uuid)
    assert rows
    # Both rows carry the rollup label — never a single pinned (possibly
    # wrong) name, and the same user never collapses onto both.
    for r in rows:
        assert r.emby_user_name is not None
        assert r.emby_user_name.startswith("2 viewers:"), r.emby_user_name
        assert "alice" in r.emby_user_name and "bob" in r.emby_user_name


# ---------------------------------------------------------------------------
# #4 anti-broadcast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_user_not_broadcast_across_distinct_nat_connections(
    patched_session_local, tracker, mock_client
):
    """One Emby user, two distinct browser-direct connections (different NAT
    IPs) → the user is written to exactly ONE connection per poll; the other
    stays User #0. Never broadcast to both (bd-cat70 regression)."""
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    ch_uuid = "ch-bcast"
    specs = [
        {"ip": "172.18.0.1", "client_id": "a", "connected_at": 1.0},
        {"ip": "172.19.0.1", "client_id": "b", "connected_at": 2.0},
    ]
    first = _channel(channel_uuid=ch_uuid, client_specs=specs, total_bytes=1_000_000,
                     url="http://dispatcharr/proxy/ts/stream/x")
    second = _channel(channel_uuid=ch_uuid, client_specs=specs, total_bytes=2_000_000,
                      url="http://dispatcharr/proxy/ts/stream/x")

    viewers = [EmbyAttribution(user_id="u1", user_name="solo")]
    with patch("bandwidth_tracker.resolve_emby_users", AsyncMock(return_value=viewers)), \
         patch("bandwidth_tracker.resolve_emby_user", AsyncMock(return_value=viewers[0])), \
         patch("bandwidth_tracker.resolve_plex_users", AsyncMock(return_value=[])), \
         patch("bandwidth_tracker.resolve_plex_user", AsyncMock(return_value=None)), \
         patch("bandwidth_tracker.resolve_jellyfin_users", AsyncMock(return_value=[])), \
         patch("bandwidth_tracker.resolve_jellyfin_user", AsyncMock(return_value=None)), \
         patch("config.get_settings", return_value=_emby_only_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    rows = _rows(patched_session_local, ch_uuid)
    assert rows
    # Per poll, exactly one of the two IPs carries 'solo'. Across both polls
    # 'solo' must always land on the SAME session, never both.
    solo_sessions = {r.session_id for r in rows if r.emby_user_name == "solo"}
    assert len(solo_sessions) == 1, (
        f"single user broadcast across connections: "
        f"{[(r.session_id, r.emby_user_name) for r in rows]}"
    )
