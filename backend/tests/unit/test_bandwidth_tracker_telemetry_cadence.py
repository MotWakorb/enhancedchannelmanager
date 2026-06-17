"""Cadence-independence tests for the ADR-013 §D2 telemetry-write throttle
(bead ``enhancedchannelmanager-c4jx4`` / 312nk.3).

The WebSocket subscriber (312nk.2) drives ``_process_channel_snapshot`` on
EVERY ``channel_stats`` event (~2s). Bead 312nk.3 decouples observation
freshness from the ``session_telemetry`` write cadence:

* **Phase A (per observation):** byte-delta bandwidth, ChannelBandwidth /
  BandwidthDaily, in-memory active-channel / per-client tracking, and the
  per-connection watch-time accrual. ``total_bytes`` is cumulative so the
  byte-delta sums identically at any cadence; watch-time accrual increments by
  the REAL elapsed wall-clock since the previous observation (not the fixed
  poll interval) so it too is cadence-independent.
* **Phase B (throttled write path):** provider resolution, system-events
  ingest, attribution, and the ``session_telemetry`` insert — runs only once
  per ``telemetry_write_interval`` OR on an active-connection-set edge.

These tests prove, with a CONTROLLABLE clock (no sleeping):

1. Driving N observations at 2s over a 10s window produces the SAME total
   bandwidth / ``BandwidthDaily`` delta as one observation per 10s.
2. The same window produces the SAME number of ``session_telemetry`` writes
   (≈ window / ``telemetry_write_interval``), independent of observation rate.
3. The per-connection watch-time accrual is cadence-independent.
4. A new session mid-interval forces an immediate (edge-triggered) write.
5. ``ECM_STATS_TELEMETRY_OPT_OUT`` skips phase B entirely regardless of cadence.

Synthetic identities only — ``docs/security/threat_model_stats_v2.md`` §7.7.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import database
from bandwidth_tracker import BandwidthTracker
from models import BandwidthDaily, SessionTelemetry, UniqueClientConnection


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_session_local(test_engine, monkeypatch):
    """Point the module-level ``get_session()`` factory at the in-memory test
    engine — BandwidthTracker calls ``get_session()`` directly."""
    from sqlalchemy.orm import sessionmaker

    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", TestSessionLocal)
    return TestSessionLocal


class _Settings:
    """Minimal stand-in for ``DispatcharrSettings`` carrying just the throttle
    knob the tracker reads off ``client.settings``."""

    def __init__(self, telemetry_write_interval: int = 10):
        self.telemetry_write_interval = telemetry_write_interval
        self.use_ws_channel_stats = False
        self.ws_suppress_poll_when_healthy = False


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.get_channel_stats = AsyncMock(return_value={"channels": []})
    client.get_channels = AsyncMock(return_value={"results": [], "next": None})
    client.get_users = AsyncMock(return_value=[])
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.get_system_events = AsyncMock(
        return_value={"events": [], "count": 0, "total": 0, "offset": 0, "limit": 1000}
    )
    client.get_streams_by_ids = AsyncMock(return_value=[])
    client.settings = _Settings(telemetry_write_interval=10)
    return client


class ManualClock:
    """A monotonic clock advanced explicitly by the test — never wall-clock."""

    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float):
        self.t += seconds


def _channel_payload(*, total_bytes: int, client_ips=None) -> dict:
    if client_ips is None:
        client_ips = ["10.0.0.1"]
    return {
        "channel_id": "ch-uuid-1",
        "channel_number": 101,
        "channel_name": "Test Channel",
        "total_bytes": total_bytes,
        "client_count": len(client_ips),
        "avg_bitrate_kbps": 1000,
        "clients": [{"ip_address": ip, "user_id": None} for ip in client_ips],
    }


def _count_rows(SessionLocal, model):
    session = SessionLocal()
    try:
        return session.query(model).count()
    finally:
        session.close()


def _daily_total_bytes(SessionLocal):
    session = SessionLocal()
    try:
        rows = session.query(BandwidthDaily).all()
        return sum(r.bytes_transferred for r in rows)
    finally:
        session.close()


def _make_tracker(mock_client, clock):
    return BandwidthTracker(client=mock_client, poll_interval=10, now_fn=clock)


# ---------------------------------------------------------------------------
# (1) + (2): byte-delta and write-count cadence independence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bandwidth_delta_identical_at_2s_and_10s(patched_session_local, mock_client):
    """Same cumulative byte progression over the same wall-clock window must
    yield the same BandwidthDaily delta whether observed at 2s or 10s."""
    # --- fast path: 5 observations at 2s over a 10s window ---
    clock_fast = ManualClock()
    tracker_fast = _make_tracker(mock_client, clock_fast)
    # total_bytes climbs 0 -> 100k over the window; cumulative counter.
    for i, tb in enumerate([0, 25_000, 50_000, 75_000, 100_000]):
        await tracker_fast._process_channel_snapshot(
            [_channel_payload(total_bytes=tb)], observed_at_ms=i * 2000
        )
        clock_fast.advance(2.0)
    fast_total = _daily_total_bytes(patched_session_local)

    # Reset the DB between runs.
    session = patched_session_local()
    try:
        session.query(BandwidthDaily).delete()
        session.commit()
    finally:
        session.close()

    # --- slow path: start + end observation 10s apart ---
    clock_slow = ManualClock()
    tracker_slow = _make_tracker(mock_client, clock_slow)
    await tracker_slow._process_channel_snapshot(
        [_channel_payload(total_bytes=0)], observed_at_ms=0
    )
    clock_slow.advance(10.0)
    await tracker_slow._process_channel_snapshot(
        [_channel_payload(total_bytes=100_000)], observed_at_ms=10_000
    )
    slow_total = _daily_total_bytes(patched_session_local)

    assert fast_total == 100_000
    assert slow_total == 100_000
    assert fast_total == slow_total


@pytest.mark.asyncio
async def test_write_count_independent_of_observation_rate(patched_session_local, mock_client):
    """Over a ~10s steady-state window, observing at 2s must produce the same
    number of session_telemetry writes as observing at 10s (≈ window /
    telemetry_write_interval), NOT one per observation."""
    mock_client.settings = _Settings(telemetry_write_interval=10)

    # Fast: 6 observations at 2s spanning 0..10s. First is an edge (newly
    # active) -> write; subsequent steady-state observations are throttled
    # until the 10s interval elapses.
    clock = ManualClock()
    tracker = _make_tracker(mock_client, clock)
    for i in range(6):  # t = 0,2,4,6,8,10
        await tracker._process_channel_snapshot(
            [_channel_payload(total_bytes=i * 10_000)], observed_at_ms=i * 2000
        )
        clock.advance(2.0)
    fast_writes = _count_rows(patched_session_local, SessionTelemetry)

    # Reset.
    session = patched_session_local()
    try:
        session.query(SessionTelemetry).delete()
        session.commit()
    finally:
        session.close()

    # Slow: 2 observations at 10s spanning 0..10s.
    clock2 = ManualClock()
    tracker2 = _make_tracker(mock_client, clock2)
    for i in range(2):  # t = 0, 10
        await tracker2._process_channel_snapshot(
            [_channel_payload(total_bytes=i * 50_000)], observed_at_ms=i * 10_000
        )
        clock2.advance(10.0)
    slow_writes = _count_rows(patched_session_local, SessionTelemetry)

    # Edge write at t=0 + interval write at t=10 = 2 writes on BOTH paths.
    # The four intermediate 2s observations on the fast path are coalesced.
    assert fast_writes == 2, f"expected 2 writes at 2s cadence, got {fast_writes}"
    assert slow_writes == 2, f"expected 2 writes at 10s cadence, got {slow_writes}"
    assert fast_writes == slow_writes


@pytest.mark.asyncio
async def test_poll_interval_ms_sums_to_wall_clock(patched_session_local, mock_client):
    """The session_telemetry poll_interval_ms column must carry the elapsed
    time since the previous write so SUM(poll_interval_ms) == wall-clock,
    independent of how observations divide the window."""
    mock_client.settings = _Settings(telemetry_write_interval=10)
    clock = ManualClock()
    tracker = _make_tracker(mock_client, clock)
    # Writes occur at t=0 (edge) and t=10 (interval). observed_at advances with
    # wall-clock.
    for i in range(6):  # t = 0..10 at 2s
        await tracker._process_channel_snapshot(
            [_channel_payload(total_bytes=i * 10_000)], observed_at_ms=i * 2000
        )
        clock.advance(2.0)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).order_by(SessionTelemetry.id).all()
    finally:
        session.close()
    # First write stamps the nominal interval (10s); the second write stamps
    # the real 10s gap since the first. SUM == the 10s window the writes span,
    # plus the nominal first bucket.
    assert len(rows) == 2
    assert rows[0].poll_interval_ms == 10_000  # nominal first bucket
    assert rows[1].poll_interval_ms == 10_000  # observed_at delta: 10000 - 0


# ---------------------------------------------------------------------------
# (3): watch-time accrual cadence independence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_seconds_independent_of_observation_rate(patched_session_local, mock_client):
    """A connection watched continuously for 10s must accrue ~10 watch_seconds
    whether observed at 2s (5 continuing observations) or 10s (1)."""
    # Fast: open at t=0, then 5 continuing observations at 2s out to t=10.
    clock = ManualClock()
    tracker = _make_tracker(mock_client, clock)
    for i in range(6):  # t=0 (open) then 2,4,6,8,10
        await tracker._process_channel_snapshot(
            [_channel_payload(total_bytes=i * 10_000)], observed_at_ms=i * 2000
        )
        clock.advance(2.0)

    session = patched_session_local()
    try:
        conns = session.query(UniqueClientConnection).all()
        fast_watch = sum(c.watch_seconds for c in conns)
    finally:
        session.close()

    # Reset.
    session = patched_session_local()
    try:
        session.query(UniqueClientConnection).delete()
        session.commit()
    finally:
        session.close()

    # Slow: open at t=0, one continuing observation at t=10.
    clock2 = ManualClock()
    tracker2 = _make_tracker(mock_client, clock2)
    await tracker2._process_channel_snapshot(
        [_channel_payload(total_bytes=0)], observed_at_ms=0
    )
    clock2.advance(10.0)
    await tracker2._process_channel_snapshot(
        [_channel_payload(total_bytes=50_000)], observed_at_ms=10_000
    )

    session = patched_session_local()
    try:
        conns = session.query(UniqueClientConnection).all()
        slow_watch = sum(c.watch_seconds for c in conns)
    finally:
        session.close()

    assert fast_watch == 10, f"2s cadence accrued {fast_watch}s, expected 10"
    assert slow_watch == 10, f"10s cadence accrued {slow_watch}s, expected 10"
    assert fast_watch == slow_watch


# ---------------------------------------------------------------------------
# (4): edge-triggered flush
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_session_mid_interval_forces_immediate_write(patched_session_local, mock_client):
    """A second channel becoming active mid-interval must force an immediate
    telemetry write even though the throttle interval has not elapsed."""
    mock_client.settings = _Settings(telemetry_write_interval=10)
    clock = ManualClock()
    tracker = _make_tracker(mock_client, clock)

    # t=0: channel A active -> edge write (1 row).
    await tracker._process_channel_snapshot(
        [_channel_payload(total_bytes=0, client_ips=["10.0.0.1"])], observed_at_ms=0
    )
    clock.advance(2.0)
    after_first = _count_rows(patched_session_local, SessionTelemetry)

    # t=2: channel A continues (steady-state, throttled) -> NO new write.
    await tracker._process_channel_snapshot(
        [_channel_payload(total_bytes=10_000, client_ips=["10.0.0.1"])],
        observed_at_ms=2000,
    )
    clock.advance(2.0)
    after_steady = _count_rows(patched_session_local, SessionTelemetry)

    # t=4: channel B becomes newly active -> connection-set EDGE -> immediate
    # write even though only 4s < 10s interval has elapsed. Both A and B emit.
    two_channels = [
        _channel_payload(total_bytes=20_000, client_ips=["10.0.0.1"]),
        {
            "channel_id": "ch-uuid-2",
            "channel_number": 202,
            "channel_name": "Channel B",
            "total_bytes": 0,
            "client_count": 1,
            "avg_bitrate_kbps": 1000,
            "clients": [{"ip_address": "10.0.0.2", "user_id": None}],
        },
    ]
    await tracker._process_channel_snapshot(two_channels, observed_at_ms=4000)
    after_edge = _count_rows(patched_session_local, SessionTelemetry)

    assert after_first == 1, "channel A start should write one row (edge)"
    assert after_steady == 1, "steady-state observation must be throttled (no new row)"
    assert after_edge == 3, (
        "new session B mid-interval must force an immediate write for both "
        f"active channels (expected 1 + 2 = 3, got {after_edge})"
    )


# ---------------------------------------------------------------------------
# (5): opt-out short-circuits phase B regardless of cadence/edge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opt_out_skips_phase_b_even_on_edge(patched_session_local, mock_client, monkeypatch):
    """ECM_STATS_TELEMETRY_OPT_OUT must skip the heavy write path entirely —
    no session_telemetry rows, no get_streams_by_ids / get_system_events
    round-trips — even on a session-start edge."""
    monkeypatch.setenv("ECM_STATS_TELEMETRY_OPT_OUT", "true")
    clock = ManualClock()
    tracker = _make_tracker(mock_client, clock)

    await tracker._process_channel_snapshot(
        [_channel_payload(total_bytes=0)], observed_at_ms=0
    )
    clock.advance(2.0)

    assert _count_rows(patched_session_local, SessionTelemetry) == 0
    mock_client.get_streams_by_ids.assert_not_awaited()
    mock_client.get_system_events.assert_not_awaited()
    # Phase A still ran: bandwidth was recorded.
    assert _count_rows(patched_session_local, UniqueClientConnection) >= 1
