"""Unit tests for the multi-source attribution wiring inside
``BandwidthTracker`` (bead ``enhancedchannelmanager-r5f0c.4``, parent
epic ``enhancedchannelmanager-r5f0c``).

Behavior contract
-----------------
``_collect_stats`` fans out to all three media-source resolvers (Emby,
Plex, Jellyfin) via :func:`asyncio.gather` with a per-source timeout
and populates the corresponding ``session_telemetry.*_user_id`` /
``..._user_name`` columns on every active (channel, ip) pair. Every
behavior in this suite maps to a failure mode the bd-r5f0c.4 brief
explicitly mandates:

* All 3 resolvers are called concurrently when every source is
  enabled.
* Settings gate: a source disabled in settings does NOT call its
  resolver — even when the others are enabled.
* Per-source timeout isolation: one source times out, the other two
  still resolve.
* Partial failure: one source raises a generic exception, the other
  two still resolve.
* Per-source WARN rate-limit independence: a sustained Plex failure
  does NOT silence Jellyfin WARN lines.
* Multi-source happy path: an active (channel, ip) that matches both
  Plex and Jellyfin gets BOTH source's columns populated on the
  written row.

The legacy Emby-only contract lives in
``test_bandwidth_tracker_emby.py``; this file extends it without
breaking it. The Emby regression suite is the highest-risk surface in
W4 (operators are using it now) — both suites must stay green.

Synthetic identities only — ``docs/security/threat_model_stats_v2.md``
§7.7.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bandwidth_tracker
import database
from bandwidth_tracker import BandwidthTracker
from models import SessionTelemetry
from services.emby_resolver import EmbyAttribution
from services.jellyfin_resolver import JellyfinAttribution


# ---------------------------------------------------------------------------
# Fixtures — mirror test_bandwidth_tracker_emby.py so the suites read alike
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
    """Clear ALL per-source WARN clocks between tests.

    Per-source rate-limit guards persist across test functions; without
    this reset, a sustained-failure test would suppress WARN lines in
    a subsequent test that wants to assert the WARN surfaces again.
    Production code does not call the reset helper.
    """
    bandwidth_tracker._reset_attribution_warn_state_for_tests()
    yield
    bandwidth_tracker._reset_attribution_warn_state_for_tests()


def _channel_payload(
    *,
    channel_uuid: str = "ch-uuid-1",
    channel_number: int = 101,
    total_bytes: int = 0,
    client_count: int = 1,
    client_ips: list[str] | None = None,
    avg_bitrate_kbps: int = 1000,
    name: str = "Test Channel",
    stream_id: int | None = 9001,
    url: str | None = None,
) -> dict:
    """Build one ``channels[]`` entry as Dispatcharr's stats endpoint
    surfaces it. Defaults to a single-viewer stream on a media-server IP.
    """
    if client_ips is None:
        client_ips = ["192.168.1.50"]
    clients = [{"ip_address": ip, "user_id": None} for ip in client_ips]
    payload = {
        "channel_id": channel_uuid,
        "channel_number": channel_number,
        "channel_name": name,
        "total_bytes": total_bytes,
        "client_count": client_count,
        "avg_bitrate_kbps": avg_bitrate_kbps,
        "clients": clients,
    }
    if stream_id is not None:
        payload["stream_id"] = stream_id
    if url is not None:
        payload["url"] = url
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
    plex_base_url: str = "http://192.168.1.50:32400",
    jellyfin_base_url: str = "http://192.168.1.50:8096",
) -> MagicMock:
    """Settings stub with all three media sources enabled."""
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


def _only_emby_enabled_settings() -> MagicMock:
    """Settings stub with only Emby enabled (legacy single-source posture)."""
    settings = MagicMock()
    settings.emby_enabled = True
    settings.emby_base_url = "http://192.168.1.50:8096"
    settings.emby_api_key = "emby-key"
    settings.plex_enabled = False
    settings.plex_base_url = ""
    settings.plex_token = ""
    settings.jellyfin_enabled = False
    settings.jellyfin_base_url = ""
    settings.jellyfin_api_key = ""
    return settings


def _all_disabled_settings() -> MagicMock:
    """Settings stub with every media source disabled."""
    settings = MagicMock()
    settings.emby_enabled = False
    settings.emby_base_url = ""
    settings.emby_api_key = ""
    settings.plex_enabled = False
    settings.plex_base_url = ""
    settings.plex_token = ""
    settings.jellyfin_enabled = False
    settings.jellyfin_base_url = ""
    settings.jellyfin_api_key = ""
    return settings


async def _drive_two_polls(tracker, mock_client, first_payload, second_payload):
    """Run ``_collect_stats`` twice so the second poll observes an open
    ``_active_connections`` entry — the writer skips rows whose ``conn_id``
    is missing.
    """
    mock_client.get_channel_stats.return_value = {"channels": [first_payload]}
    await tracker._collect_stats()
    mock_client.get_channel_stats.return_value = {"channels": [second_payload]}
    await tracker._collect_stats()


# ---------------------------------------------------------------------------
# All three resolvers awaited concurrently when every source is enabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_three_resolvers_called_when_all_enabled(
    patched_session_local, tracker, mock_client
):
    """All three resolvers receive at least one call per poll when all
    sources are enabled. The fan-out pattern in ``_resolve_attributions``
    is ``asyncio.gather`` so the three loops run concurrently."""
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    emby_mock = AsyncMock(return_value=None)
    plex_mock = AsyncMock(return_value=None)
    jellyfin_mock = AsyncMock(return_value=None)
    with patch("bandwidth_tracker.resolve_emby_user", emby_mock), \
         patch("bandwidth_tracker.resolve_plex_user", plex_mock), \
         patch("bandwidth_tracker.resolve_jellyfin_user", jellyfin_mock), \
         patch("config.get_settings", return_value=_all_enabled_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    assert emby_mock.await_count >= 1, "Emby resolver must be called when emby_enabled=True"
    assert plex_mock.await_count >= 1, "Plex resolver must be called when plex_enabled=True"
    assert jellyfin_mock.await_count >= 1, "Jellyfin resolver must be called when jellyfin_enabled=True"


# ---------------------------------------------------------------------------
# Settings gate — disabled source's resolver is NEVER called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_source_resolver_not_called(
    patched_session_local, tracker, mock_client
):
    """When only Emby is enabled, the Plex and Jellyfin resolver mocks
    MUST NOT be awaited — the per-source settings gate fires before the
    per-(channel, ip) loop."""
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    emby_mock = AsyncMock(return_value=None)
    plex_mock = AsyncMock(return_value=None)
    jellyfin_mock = AsyncMock(return_value=None)
    with patch("bandwidth_tracker.resolve_emby_user", emby_mock), \
         patch("bandwidth_tracker.resolve_plex_user", plex_mock), \
         patch("bandwidth_tracker.resolve_jellyfin_user", jellyfin_mock), \
         patch("config.get_settings", return_value=_only_emby_enabled_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    assert emby_mock.await_count >= 1
    plex_mock.assert_not_awaited()
    jellyfin_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_all_disabled_short_circuits_every_resolver(
    patched_session_local, tracker, mock_client
):
    """When every source is disabled (the common posture on installs
    without a media server), NONE of the resolvers are awaited and the
    telemetry row writes with all six attribution columns NULL."""
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    emby_mock = AsyncMock(return_value=None)
    plex_mock = AsyncMock(return_value=None)
    jellyfin_mock = AsyncMock(return_value=None)
    with patch("bandwidth_tracker.resolve_emby_user", emby_mock), \
         patch("bandwidth_tracker.resolve_plex_user", plex_mock), \
         patch("bandwidth_tracker.resolve_jellyfin_user", jellyfin_mock), \
         patch("config.get_settings", return_value=_all_disabled_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    emby_mock.assert_not_awaited()
    plex_mock.assert_not_awaited()
    jellyfin_mock.assert_not_awaited()

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows, "telemetry rows MUST still write when all sources are disabled"
    for row in rows:
        assert row.emby_user_id is None
        assert row.emby_user_name is None
        assert row.plex_user_id is None
        assert row.plex_user_name is None
        assert row.jellyfin_user_id is None
        assert row.jellyfin_user_name is None


# ---------------------------------------------------------------------------
# Per-source timeout isolation — one source hangs, others still resolve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plex_timeout_does_not_block_emby_or_jellyfin(
    patched_session_local, tracker, mock_client, monkeypatch
):
    """A Plex resolver that exceeds the per-source timeout must NOT
    block Emby and Jellyfin from completing. The timeout fires inside
    ``asyncio.wait_for`` and the merged map carries Emby + Jellyfin
    attributions — Plex columns stay NULL."""
    # Tighten the timeout so the test is fast without being flaky.
    monkeypatch.setattr(
        bandwidth_tracker, "_RESOLVER_PER_SOURCE_TIMEOUT_SECONDS", 0.05
    )
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    async def _hang(*args, **kwargs):
        # Sleep significantly past the 50ms test budget.
        await asyncio.sleep(1.0)
        return "should-never-be-returned"

    emby_attr = EmbyAttribution(user_id="emby-uid", user_name="emby-alice")
    jellyfin_attr = JellyfinAttribution(
        user_id="jf-uid", user_name="jellyfin-alice",
    )
    with patch("bandwidth_tracker.resolve_emby_user", AsyncMock(return_value=emby_attr)), \
         patch("bandwidth_tracker.resolve_plex_user", _hang), \
         patch("bandwidth_tracker.resolve_jellyfin_user", AsyncMock(return_value=jellyfin_attr)), \
         patch("config.get_settings", return_value=_all_enabled_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows
    for row in rows:
        assert row.emby_user_name == "emby-alice"
        assert row.jellyfin_user_name == "jellyfin-alice"
        # Plex timed out → columns stay NULL.
        assert row.plex_user_name is None


# ---------------------------------------------------------------------------
# Partial failure — one resolver raises, others still produce results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jellyfin_exception_does_not_poison_emby_or_plex(
    patched_session_local, tracker, mock_client
):
    """A Jellyfin resolver raise must NOT prevent Emby and Plex
    attributions from landing on the written rows."""
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    emby_attr = EmbyAttribution(user_id="emby-uid", user_name="emby-bob")
    plex_user = "plex-bob"
    with patch("bandwidth_tracker.resolve_emby_user", AsyncMock(return_value=emby_attr)), \
         patch("bandwidth_tracker.resolve_plex_user", AsyncMock(return_value=plex_user)), \
         patch(
             "bandwidth_tracker.resolve_jellyfin_user",
             AsyncMock(side_effect=RuntimeError("simulated Jellyfin fault")),
         ), \
         patch("config.get_settings", return_value=_all_enabled_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows
    for row in rows:
        assert row.emby_user_name == "emby-bob"
        assert row.plex_user_name == "plex-bob"
        assert row.jellyfin_user_name is None


# ---------------------------------------------------------------------------
# Per-source WARN rate-limit independence — Plex outage does NOT silence
# Jellyfin warnings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_source_warn_rate_limit_independence(
    patched_session_local, tracker, mock_client, caplog
):
    """A sustained Plex failure produces exactly ONE [PLEX] WARN inside
    the rate-limit window. A simultaneous Jellyfin failure produces
    exactly ONE [JELLYFIN] WARN — the Plex clock does NOT suppress
    Jellyfin warnings. This is the SRE failure-isolation requirement
    that motivated the per-source clocks in bd-r5f0c.4.
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(
        total_bytes=1_000_000,
        client_count=2,
        client_ips=["192.168.1.50", "192.168.1.51"],
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_count=2,
        client_ips=["192.168.1.50", "192.168.1.51"],
    )

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="bandwidth_tracker"), \
         patch(
             "bandwidth_tracker.resolve_emby_user",
             AsyncMock(return_value=None),
         ), \
         patch(
             "bandwidth_tracker.resolve_plex_user",
             AsyncMock(side_effect=RuntimeError("plex simulated")),
         ), \
         patch(
             "bandwidth_tracker.resolve_jellyfin_user",
             AsyncMock(side_effect=RuntimeError("jellyfin simulated")),
         ), \
         patch("config.get_settings", return_value=_all_enabled_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    plex_warns = [
        r for r in caplog.records
        if "[BANDWIDTH] [PLEX]" in r.message and r.levelno >= logging.WARNING
    ]
    jellyfin_warns = [
        r for r in caplog.records
        if "[BANDWIDTH] [JELLYFIN]" in r.message and r.levelno >= logging.WARNING
    ]
    # 2 IPs × 2 polls = 4 calls per source; each per-source clock
    # collapses those to one WARN inside the 60s window. The clocks
    # are independent — both sources emit their own WARN.
    assert len(plex_warns) == 1, (
        f"expected exactly one [BANDWIDTH] [PLEX] WARN; "
        f"got {len(plex_warns)}"
    )
    assert len(jellyfin_warns) == 1, (
        f"expected exactly one [BANDWIDTH] [JELLYFIN] WARN even with "
        f"a concurrent Plex failure; got {len(jellyfin_warns)}"
    )


# ---------------------------------------------------------------------------
# Multi-source happy path — Plex + Jellyfin both populate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_source_attribution_populates_all_columns(
    patched_session_local, tracker, mock_client
):
    """An active (channel, ip) pair that matches Emby, Plex, AND
    Jellyfin (rare but legal — operator running every media server on
    the same host) gets ALL SIX columns populated on the written row."""
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    emby_attr = EmbyAttribution(user_id="emby-uid", user_name="alice@emby")
    plex_user = "alice@plex"
    jellyfin_attr = JellyfinAttribution(
        user_id="jf-uid", user_name="alice@jellyfin",
    )
    with patch("bandwidth_tracker.resolve_emby_user", AsyncMock(return_value=emby_attr)), \
         patch("bandwidth_tracker.resolve_plex_user", AsyncMock(return_value=plex_user)), \
         patch("bandwidth_tracker.resolve_jellyfin_user", AsyncMock(return_value=jellyfin_attr)), \
         patch("config.get_settings", return_value=_all_enabled_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows
    for row in rows:
        assert row.emby_user_id == "emby-uid"
        assert row.emby_user_name == "alice@emby"
        # Plex resolver returns name-only — user_id stays NULL until the
        # resolver is upgraded (documented on AttributionResult).
        assert row.plex_user_id is None
        assert row.plex_user_name == "alice@plex"
        assert row.jellyfin_user_id == "jf-uid"
        assert row.jellyfin_user_name == "alice@jellyfin"


# ---------------------------------------------------------------------------
# Plex-only match: Plex columns populated, Emby + Jellyfin NULL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plex_only_match_leaves_other_sources_null(
    patched_session_local, tracker, mock_client
):
    """Common multi-source posture: operator runs Plex AND Jellyfin,
    only Plex matches a specific stream. Plex columns populated;
    Jellyfin columns NULL (resolver returned None); Emby columns NULL
    (source disabled).
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    # Plex + Jellyfin enabled, Emby disabled.
    settings = MagicMock()
    settings.emby_enabled = False
    settings.emby_base_url = ""
    settings.emby_api_key = ""
    settings.plex_enabled = True
    settings.plex_base_url = "http://192.168.1.50:32400"
    settings.plex_token = "plex-token"
    settings.jellyfin_enabled = True
    settings.jellyfin_base_url = "http://192.168.1.50:8096"
    settings.jellyfin_api_key = "jellyfin-key"

    emby_mock = AsyncMock(return_value=None)
    with patch("bandwidth_tracker.resolve_emby_user", emby_mock), \
         patch("bandwidth_tracker.resolve_plex_user", AsyncMock(return_value="plex-charlie")), \
         patch("bandwidth_tracker.resolve_jellyfin_user", AsyncMock(return_value=None)), \
         patch("config.get_settings", return_value=settings):
        await _drive_two_polls(tracker, mock_client, first, second)

    # Emby disabled — its resolver must never be awaited.
    emby_mock.assert_not_awaited()

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows
    for row in rows:
        assert row.emby_user_id is None
        assert row.emby_user_name is None
        assert row.plex_user_name == "plex-charlie"
        assert row.jellyfin_user_id is None
        assert row.jellyfin_user_name is None
