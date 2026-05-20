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
from services.jellyfin_resolver import JellyfinAttribution


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
    """Build a Dispatcharr channels[] entry with per-client metadata.

    Each spec may carry a ``user_id`` to model a genuine Dispatcharr account
    (positive int) vs. an anonymous media-server pull (default ``None``,
    bd-rools).
    """
    clients = [
        {
            "ip_address": spec["ip"],
            "client_id": spec.get("client_id", spec["ip"]),
            "connected_at": spec.get("connected_at"),
            "user_id": spec.get("user_id"),
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


# ---------------------------------------------------------------------------
# B1 — mixed server-proxy + browser-direct on the SAME channel (persisted)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b1_mixed_proxy_plus_browser_direct_both_attributed_persisted(
    patched_session_local, tracker, mock_client
):
    """bd-mlcla B1 (persisted path): a channel with BOTH the server-proxy pull
    (source IP == Emby server 172.16.0.19) AND a browser-direct viewer
    (NAT'd 172.18.0.1) writes a telemetry row for EACH — the browser-direct
    gets its distinct name, the proxy carries the remainder. Before the B1 fix
    the proxy early-return dropped the browser-direct row to User #0."""
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    ch_uuid = "ch-mixed"
    specs = [
        {"ip": "172.16.0.19", "client_id": "proxy", "connected_at": 1.0},
        {"ip": "172.18.0.1", "client_id": "browser", "connected_at": 2.0},
    ]
    first = _channel(channel_uuid=ch_uuid, client_specs=specs, total_bytes=1_000_000,
                     url="http://dispatcharr/proxy/ts/stream/ch-mixed")
    second = _channel(channel_uuid=ch_uuid, client_specs=specs, total_bytes=2_000_000,
                      url="http://dispatcharr/proxy/ts/stream/ch-mixed")

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
    # The browser-direct viewer (172.18.0.1) is NEVER User #0 — it gets the
    # top-ranked unconsumed user (alice).
    browser_names = {
        r.emby_user_name for r in rows if r.session_id and "172.18.0.1" not in (r.session_id or "")
    }
    # Distinguish rows by which IP they belong to via the assigned name set.
    attributed = {r.emby_user_name for r in rows if r.emby_user_name}
    assert "alice" in attributed, attributed  # browser-direct attributed
    assert "bob" in attributed, attributed    # proxy carries the remainder
    # Neither row dropped to User #0 (NULL emby_user_name) while users existed.
    assert all(r.emby_user_name is not None for r in rows), (
        [(r.session_id, r.emby_user_name) for r in rows]
    )


# ---------------------------------------------------------------------------
# N1 — persisted-path same-NAT-IP collapse (documented limitation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_n1_same_nat_ip_collapses_to_one_telemetry_row(
    patched_session_local, tracker, mock_client
):
    """bd-mlcla N1: the PERSISTED path keys ``session_telemetry`` by
    ``(channel, ip)``, so two browser-direct viewers behind the SAME NAT IP
    collapse to ONE telemetry row — the persisted path lacks the live path's
    full ``client_id`` granularity.

    This is a documented limitation (docs/architecture.md "User Attribution
    Pipeline" §persisted-path), NOT a bug. This test ASSERTS the collapse so
    the limitation cannot silently drift: if a future change gives the
    persisted path per-client_id granularity, this test fails and forces a
    docs + test update rather than letting the behavior change unnoticed.

    Two distinct client_ids on the SAME IP (172.18.0.1), two Emby users → the
    persisted path builds ONE reconciler Connection (keyed by the distinct
    IP), so it is the ``users > connections`` case: the single (channel, ip)
    telemetry identity carries ONE user (the top-ranked match); the second
    viewer is NOT persisted on this path. The live Stats path, by contrast,
    has full client_id granularity and would surface the Option-B rollup for
    the same two devices."""
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    ch_uuid = "ch-samenat"
    specs = [
        {"ip": "172.18.0.1", "client_id": "device-a", "connected_at": 1.0},
        {"ip": "172.18.0.1", "client_id": "device-b", "connected_at": 2.0},
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
    # The DOCUMENTED collapse: per poll there is exactly ONE distinct
    # (channel, ip) telemetry identity for this channel, because both devices
    # share 172.18.0.1. (Two polls write two rows for that one identity; the
    # collapse is on the IP dimension, asserted via the session_id which the
    # writer derives from (channel, ip).)
    distinct_session_ids = {r.session_id for r in rows}
    assert len(distinct_session_ids) == 1, (
        "persisted path must collapse same-NAT-IP devices to one telemetry "
        f"identity (N1 limitation); got {distinct_session_ids}"
    )
    # The collapsed identity carries exactly ONE attributed user across all
    # its rows — NOT a two-viewer rollup. Same-NAT-IP devices have only one
    # (channel, ip) Connection on the persisted path, so the second viewer is
    # not persisted (the documented N1 loss). The top-ranked match (alice)
    # wins; a rollup would only appear if there were 2+ distinct connections.
    attributed_names = {r.emby_user_name for r in rows if r.emby_user_name}
    assert attributed_names == {"alice"}, (
        f"persisted same-NAT-IP must collapse to one user, not a rollup; "
        f"got {attributed_names}"
    )
    for r in rows:
        assert not (r.emby_user_name or "").startswith("2 viewers:"), (
            f"persisted path must NOT show the live-path rollup for same-NAT "
            f"devices (N1 limitation); got {r.emby_user_name}"
        )


# ---------------------------------------------------------------------------
# bd-4w9w6 — XC-credentialed UPSTREAM channel URL must not suppress
# media-server reconciliation on the persisted path either.
#
# The #1 test above used a non-credentialed Dispatcharr proxy URL. In
# production ``channel["url"]`` is the upstream provider URL, which for an XC
# provider embeds the operator's account credentials. The old code excluded
# such channels from reconciliation, dropping the live Jellyfin match to
# User #0 / NULL telemetry. This test drives the realistic XC upstream URL
# through ``_collect_stats`` and asserts the persisted row attributes the
# Jellyfin user even though Emby + Plex run and return nothing.
# ---------------------------------------------------------------------------


def _all_sources_settings():
    s = MagicMock()
    s.emby_enabled = True
    s.emby_base_url = "http://172.16.0.19:8096"
    s.emby_api_key = "k"
    s.plex_enabled = True
    s.plex_base_url = "http://172.16.0.19:32400"
    s.plex_token = "t"
    s.jellyfin_enabled = True
    s.jellyfin_base_url = "http://172.16.0.19:8096"
    s.jellyfin_api_key = "k"
    s.trusted_media_networks = []
    return s


@pytest.mark.asyncio
async def test_xc_upstream_url_jellyfin_match_persisted_bd4w9w6(
    patched_session_local, tracker, mock_client
):
    """bd-4w9w6 persisted regression: a channel whose UPSTREAM url embeds XC
    credentials, watched by a NAT'd Jellyfin viewer, still writes a telemetry
    row attributed to that Jellyfin user. Emby + Plex run and match nothing;
    their empty results must not suppress the Jellyfin attribution.

    FAILS pre-fix (URL-identity exclusion → NULL jellyfin_user_name);
    PASSES post-fix.
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    ch_uuid = "ch-xc-upstream"
    specs = [{"ip": "172.18.0.1", "client_id": "browser-jf"}]
    xc_url = "https://infinity.gives/live/mot/16118141/108495.ts"
    first = _channel(channel_uuid=ch_uuid, client_specs=specs,
                     total_bytes=1_000_000, url=xc_url)
    second = _channel(channel_uuid=ch_uuid, client_specs=specs,
                      total_bytes=2_000_000, url=xc_url)

    jf = [JellyfinAttribution(user_id="uid-mw", user_name="MotWakorb")]
    with patch("bandwidth_tracker.resolve_emby_users", AsyncMock(return_value=[])), \
         patch("bandwidth_tracker.resolve_emby_user", AsyncMock(return_value=None)), \
         patch("bandwidth_tracker.resolve_plex_users", AsyncMock(return_value=[])), \
         patch("bandwidth_tracker.resolve_plex_user", AsyncMock(return_value=None)), \
         patch("bandwidth_tracker.resolve_jellyfin_users", AsyncMock(return_value=jf)), \
         patch("bandwidth_tracker.resolve_jellyfin_user", AsyncMock(return_value=jf[0])), \
         patch("config.get_settings", return_value=_all_sources_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    rows = _rows(patched_session_local, ch_uuid)
    assert rows, "expected persisted telemetry rows for the XC-sourced channel"
    assert all(r.jellyfin_user_name == "MotWakorb" for r in rows), (
        "XC-upstream-URL channel watched via Jellyfin must attribute: "
        f"{[(r.session_id, r.jellyfin_user_name) for r in rows]}"
    )
    # Empty Emby/Plex results did not overwrite or block the Jellyfin match.
    assert all(r.emby_user_name is None for r in rows)
    assert all(r.plex_user_name is None for r in rows)


# ---------------------------------------------------------------------------
# bd-rools — per-connection Dispatcharr-account discriminator (persisted path).
# Re-fix for the bd-cat70 direct-client cross-attribution that the bd-4w9w6
# unconditional has_url_identity=False re-exposed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nat_browser_direct_no_account_stays_eligible_persisted_bd_rools(
    patched_session_local, tracker, mock_client
):
    """bd-rools: a NAT'd media-server browser-direct connection with NO
    Dispatcharr account (``user_id`` absent / anonymous) stays ELIGIBLE and
    attributes the media-server user on the persisted path.

    This is the User#0-fix invariant: the per-connection account discriminator
    must NOT exclude an anonymous browser-direct pull.
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    ch_uuid = "ch-rools-anon"
    # No user_id key → anonymous media-server pull (NAT'd browser-direct).
    specs = [{"ip": "172.18.0.1", "client_id": "browser-anon"}]
    first = _channel(channel_uuid=ch_uuid, client_specs=specs, total_bytes=1_000_000,
                     url="https://infinity.gives/live/mot/16118141/108495.ts")
    second = _channel(channel_uuid=ch_uuid, client_specs=specs, total_bytes=2_000_000,
                      url="https://infinity.gives/live/mot/16118141/108495.ts")

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
        "anonymous NAT'd browser-direct must stay eligible and attribute: "
        f"{[(r.session_id, r.emby_user_name) for r in rows]}"
    )


@pytest.mark.asyncio
async def test_direct_subscriber_does_not_absorb_media_viewer_persisted_bd_rools(
    patched_session_local, tracker, mock_client
):
    """bd-rools (re-fix bd-cat70, persisted path): a genuine direct XC
    subscriber (Dispatcharr account ``user_id=3``, kmfelmer) sharing a channel
    with an anonymous media-server pull must NOT absorb the media-server user.

    The subscriber is excluded from media-server reconciliation (account
    identity), so the media-server viewer (MotWakorb) attributes to the
    anonymous pull's (channel, ip) row and the subscriber's IP row carries NO
    media-server name — never cross-attributed.
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    ch_uuid = "ch-rools-mixed"
    specs = [
        # Anonymous media-server pull at the Emby server IP (the real viewer).
        {"ip": "172.16.0.19", "client_id": "anon-pull", "connected_at": 1.0},
        # Genuine direct XC subscriber, Dispatcharr account user_id=3, NAT'd.
        {"ip": "172.16.0.50", "client_id": "kmfelmer", "connected_at": 2.0,
         "user_id": 3},
    ]
    first = _channel(channel_uuid=ch_uuid, client_specs=specs, total_bytes=1_000_000,
                     url="https://infinity.gives/live/mot/16118141/108495.ts")
    second = _channel(channel_uuid=ch_uuid, client_specs=specs, total_bytes=2_000_000,
                      url="https://infinity.gives/live/mot/16118141/108495.ts")

    # REALISTIC non-IP-gated resolver: returns MotWakorb for ANY ip.
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
    # SessionTelemetry rows carry the Dispatcharr account ``user_id`` (coerced:
    # the genuine subscriber row = 3, the anonymous pull row = None). The
    # media-server viewer (MotWakorb) must land ONLY on the anonymous (user_id
    # IS NULL) row — never on the genuine subscriber's (user_id == 3) row.
    attributed = [r for r in rows if r.emby_user_name == "MotWakorb"]
    assert attributed, f"media-server viewer must attribute somewhere: {rows}"
    # No row belonging to the genuine subscriber (user_id == 3) was attributed
    # the media-server user — the bd-cat70 cross-attribution is prevented.
    subscriber_absorbed = [r for r in attributed if r.user_id == 3]
    assert not subscriber_absorbed, (
        "genuine direct subscriber (user_id=3) must NOT absorb the "
        "media-server viewer (bd-cat70 cross-attribution); got "
        f"{[(r.session_id, r.user_id, r.emby_user_name) for r in subscriber_absorbed]}"
    )
    # The MotWakorb attribution belongs to the anonymous pull (user_id IS NULL).
    assert all(r.user_id is None for r in attributed), (
        "media-server viewer must attribute to the anonymous pull row "
        f"(user_id NULL); got {[(r.session_id, r.user_id) for r in attributed]}"
    )
