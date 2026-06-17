"""Tests for the ADR-013 WebSocket ``channel_stats`` subscriber
(bead enhancedchannelmanager-v5ihf / 312nk.2).

The ``ChannelStatsSubscriber`` maintains one WS connection to Dispatcharr's
``"updates"`` group and feeds ``channel_stats`` events into the tracker's
``_process_channel_snapshot`` — a drop-in for the ``/proxy/ts/status`` poll.
These tests pin the bead's gate contracts WITHOUT touching a real Dispatcharr:

* URL derivation: http base -> ``ws://``, https base -> ``wss://``, with the
  ``/ws/?token=<JWT>`` path and the live access token.
* ``channel_stats`` parsing: the nested JSON-**string** ``stats`` field is
  ``json.loads``-ed, ``channels`` extracted, and pushed into
  ``_process_channel_snapshot`` with a millisecond ``observed_at`` stamp.
* Unknown event types (``stream_rehash``, ``channels_created``, anything
  else) are ignored without crashing and do NOT drive the snapshot.
* Watchdog staleness flips ``ws_healthy`` False and increments the
  fallback-activation counter.
* Backoff grows exponentially (capped) and resets to base on a successful
  event.
* With ``use_ws_channel_stats=False`` the tracker never constructs/starts a
  subscriber and behaves exactly as the poll-only path does today.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from channel_stats_subscriber import ChannelStatsSubscriber
from config import DispatcharrSettings


def _make_client(url: str = "http://disp.local:9191", token: str = "tok-abc"):
    """A stand-in DispatcharrClient with just the attributes the
    subscriber reads (base_url, access_token, _refresh_access_token)."""
    client = MagicMock()
    client.base_url = url.rstrip("/")
    client.access_token = token
    client.settings = DispatcharrSettings(url=url)
    client._refresh_access_token = AsyncMock()
    client._ensure_authenticated = AsyncMock()
    return client


def _make_subscriber(client=None, tracker=None, **kwargs):
    client = client or _make_client()
    tracker = tracker or MagicMock()
    tracker._process_channel_snapshot = AsyncMock()
    return (
        ChannelStatsSubscriber(client=client, tracker=tracker, **kwargs),
        client,
        tracker,
    )


# --------------------------------------------------------------------------
# URL derivation
# --------------------------------------------------------------------------


def test_url_derivation_http_to_ws():
    sub, _, _ = _make_subscriber(_make_client("http://disp.local:9191", "JWT1"))
    assert sub._build_ws_url() == "ws://disp.local:9191/ws/?token=JWT1"


def test_url_derivation_https_to_wss():
    sub, _, _ = _make_subscriber(_make_client("https://disp.example.com", "JWT2"))
    assert sub._build_ws_url() == "wss://disp.example.com/ws/?token=JWT2"


def test_url_derivation_strips_trailing_slash_and_path():
    # Base URL may carry a path or trailing slash; the WS endpoint is rooted.
    sub, _, _ = _make_subscriber(_make_client("http://10.0.0.5:9191/", "JWT3"))
    assert sub._build_ws_url() == "ws://10.0.0.5:9191/ws/?token=JWT3"


def test_url_derivation_uses_live_token():
    client = _make_client("http://disp.local:9191", "old")
    sub, _, _ = _make_subscriber(client)
    client.access_token = "rotated"
    # Token is read at build time, not cached at construction.
    assert sub._build_ws_url() == "ws://disp.local:9191/ws/?token=rotated"


# --------------------------------------------------------------------------
# channel_stats parsing -> _process_channel_snapshot
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_stats_event_drives_snapshot():
    sub, _, tracker = _make_subscriber()
    channels = [{"channel_id": "c1", "total_bytes": 100}, {"channel_id": "c2", "total_bytes": 5}]
    event = {"type": "channel_stats", "stats": json.dumps({"channels": channels, "count": 2})}

    with patch("channel_stats_subscriber.time.time", return_value=1234.0):
        await sub._handle_message(json.dumps(event))

    tracker._process_channel_snapshot.assert_awaited_once()
    args, _ = tracker._process_channel_snapshot.call_args
    assert args[0] == channels
    assert args[1] == 1234000  # observed_at_ms = int(time.time()*1000)
    assert sub.events_received == 1
    assert sub.ws_healthy is True


@pytest.mark.asyncio
async def test_channel_stats_missing_channels_key_is_empty_list():
    sub, _, tracker = _make_subscriber()
    event = {"type": "channel_stats", "stats": json.dumps({"count": 0})}
    await sub._handle_message(json.dumps(event))
    args, _ = tracker._process_channel_snapshot.call_args
    assert args[0] == []


@pytest.mark.asyncio
async def test_channel_stats_records_last_snapshot_summary():
    """The cross-validation drift line (ADR-013 §D5) needs the last WS
    snapshot's channel count + total bytes; the subscriber must retain it."""
    sub, _, _ = _make_subscriber()
    channels = [{"channel_id": "c1", "total_bytes": 100}, {"channel_id": "c2", "total_bytes": 50}]
    event = {"type": "channel_stats", "stats": json.dumps({"channels": channels})}
    await sub._handle_message(json.dumps(event))
    assert sub.last_snapshot_channel_count == 2
    assert sub.last_snapshot_total_bytes == 150


# --------------------------------------------------------------------------
# Unknown / ignored event types
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("etype", ["stream_rehash", "channels_created", "something_new", None])
async def test_unknown_event_types_ignored(etype):
    sub, _, tracker = _make_subscriber()
    event = {"type": etype, "payload": "whatever"}
    # Must not raise and must not drive the snapshot.
    await sub._handle_message(json.dumps(event))
    tracker._process_channel_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_json_does_not_crash():
    sub, _, tracker = _make_subscriber()
    await sub._handle_message("{not valid json")
    tracker._process_channel_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_stats_with_non_string_stats_does_not_crash():
    # Defensive: if 'stats' arrives already-parsed (or is a bad type), no crash.
    sub, _, tracker = _make_subscriber()
    event = {"type": "channel_stats", "stats": 12345}
    await sub._handle_message(json.dumps(event))
    tracker._process_channel_snapshot.assert_not_awaited()


# --------------------------------------------------------------------------
# Watchdog / staleness
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_flips_healthy_false_when_stale():
    sub, _, _ = _make_subscriber(ws_staleness_timeout=6.0)
    # Simulate a healthy socket that received an event at t=100.
    sub._ws_healthy = True
    sub._last_event_at = 100.0
    # Now it's t=110 — 10s of silence, past the 6s timeout.
    with patch("channel_stats_subscriber.time.monotonic", return_value=110.0):
        went_stale = sub._check_staleness()
    assert went_stale is True
    assert sub.ws_healthy is False
    assert sub.fallback_activations == 1


@pytest.mark.asyncio
async def test_watchdog_keeps_healthy_when_fresh():
    sub, _, _ = _make_subscriber(ws_staleness_timeout=6.0)
    sub._ws_healthy = True
    sub._last_event_at = 100.0
    with patch("channel_stats_subscriber.time.monotonic", return_value=104.0):
        went_stale = sub._check_staleness()
    assert went_stale is False
    assert sub.ws_healthy is True
    assert sub.fallback_activations == 0


@pytest.mark.asyncio
async def test_watchdog_does_not_double_count_already_unhealthy():
    sub, _, _ = _make_subscriber(ws_staleness_timeout=6.0)
    sub._ws_healthy = False  # already down
    sub._last_event_at = 100.0
    with patch("channel_stats_subscriber.time.monotonic", return_value=200.0):
        sub._check_staleness()
    assert sub.fallback_activations == 0  # no new activation; was already off


# --------------------------------------------------------------------------
# Backoff growth + reset
# --------------------------------------------------------------------------


def test_backoff_grows_exponentially_capped():
    sub, _, _ = _make_subscriber(backoff_base=1.0, backoff_cap=30.0)
    # Disable jitter so the deterministic ceiling is checkable.
    with patch("channel_stats_subscriber.random.uniform", side_effect=lambda a, b: b):
        delays = [sub._next_backoff() for _ in range(8)]
    # 1,2,4,8,16,30(cap),30,30 — each <= cap, monotonic up to the cap.
    assert delays[0] == pytest.approx(1.0)
    assert delays[1] == pytest.approx(2.0)
    assert delays[2] == pytest.approx(4.0)
    assert all(d <= 30.0 for d in delays)
    assert delays[-1] == pytest.approx(30.0)
    assert delays[5] == pytest.approx(30.0)


def test_backoff_includes_jitter_within_bounds():
    sub, _, _ = _make_subscriber(backoff_base=1.0, backoff_cap=30.0)
    # Real jitter: each delay must stay within [0, current_ceiling].
    sub._backoff_attempt = 2  # ceiling = min(1*2^2, 30) = 4
    d = sub._next_backoff()
    assert 0.0 <= d <= 4.0


def test_backoff_resets_to_base():
    sub, _, _ = _make_subscriber(backoff_base=1.0, backoff_cap=30.0)
    for _ in range(5):
        sub._next_backoff()
    assert sub._backoff_attempt > 0
    sub._reset_backoff()
    assert sub._backoff_attempt == 0


@pytest.mark.asyncio
async def test_successful_event_resets_backoff():
    sub, _, _ = _make_subscriber()
    sub._backoff_attempt = 4
    event = {"type": "channel_stats", "stats": json.dumps({"channels": []})}
    await sub._handle_message(json.dumps(event))
    assert sub._backoff_attempt == 0


# --------------------------------------------------------------------------
# Auth-class close detection
# --------------------------------------------------------------------------


def test_auth_close_code_detected():
    sub, _, _ = _make_subscriber()
    # 4001-style policy/auth codes and 1008 (policy violation) are auth-class.
    assert sub._is_auth_close(4001) is True
    assert sub._is_auth_close(1008) is True
    assert sub._is_auth_close(1000) is False  # normal close
    assert sub._is_auth_close(1011) is False  # server error, not auth


# --------------------------------------------------------------------------
# Tracker integration: default OFF == poll-only, no behavior change
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tracker_does_not_start_subscriber_when_setting_off():
    from bandwidth_tracker import BandwidthTracker

    client = _make_client()
    client.settings = DispatcharrSettings(url="http://disp.local:9191", use_ws_channel_stats=False)
    tracker = BandwidthTracker(client, poll_interval=10)
    # No subscriber should be constructed/started while the flag is OFF.
    with patch.object(tracker, "_initialize_channel_maps", AsyncMock()), \
         patch.object(tracker, "_cleanup_stale_connections", MagicMock()):
        await tracker.start()
        try:
            assert tracker._ws_subscriber is None
            assert tracker._ws_task is None
        finally:
            await tracker.stop()


@pytest.mark.asyncio
async def test_tracker_starts_subscriber_when_setting_on():
    from bandwidth_tracker import BandwidthTracker

    client = _make_client()
    client.settings = DispatcharrSettings(url="http://disp.local:9191", use_ws_channel_stats=True)
    tracker = BandwidthTracker(client, poll_interval=10)
    with patch.object(tracker, "_initialize_channel_maps", AsyncMock()), \
         patch.object(tracker, "_cleanup_stale_connections", MagicMock()), \
         patch("channel_stats_subscriber.ChannelStatsSubscriber.run", AsyncMock()):
        await tracker.start()
        try:
            assert tracker._ws_subscriber is not None
            assert tracker._ws_task is not None
        finally:
            await tracker.stop()
        assert tracker._ws_task is None


@pytest.mark.asyncio
async def test_poll_path_unchanged_when_ws_off():
    """With WS OFF, _collect_stats fetches and processes exactly as today."""
    from bandwidth_tracker import BandwidthTracker

    client = _make_client()
    client.settings = DispatcharrSettings(url="http://disp.local:9191", use_ws_channel_stats=False)
    client.get_channel_stats = AsyncMock(return_value={"channels": [{"channel_id": "c1"}]})
    tracker = BandwidthTracker(client, poll_interval=10)
    tracker._process_channel_snapshot = AsyncMock()
    await tracker._collect_stats()
    client.get_channel_stats.assert_awaited_once()
    tracker._process_channel_snapshot.assert_awaited_once()


# --------------------------------------------------------------------------
# Poll suppression / cross-validation (ADR-013 §D5 + PO decision #3)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_suppressed_when_ws_healthy_and_suppress_enabled():
    """use_ws + ws_healthy + suppress_poll_when_healthy => poll skips fetch."""
    from bandwidth_tracker import BandwidthTracker

    client = _make_client()
    client.settings = DispatcharrSettings(
        url="http://disp.local:9191",
        use_ws_channel_stats=True,
        ws_suppress_poll_when_healthy=True,
    )
    client.get_channel_stats = AsyncMock(return_value={"channels": []})
    tracker = BandwidthTracker(client, poll_interval=10)
    tracker._process_channel_snapshot = AsyncMock()
    # Stand up a healthy subscriber.
    sub = MagicMock()
    sub.ws_healthy = True
    tracker._ws_subscriber = sub

    await tracker._collect_stats()
    client.get_channel_stats.assert_not_awaited()
    tracker._process_channel_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_cross_validates_when_ws_healthy_and_suppress_disabled():
    """Soak default: poll STILL fetches but does NOT process (no double-write);
    it logs a cross-validation line instead."""
    from bandwidth_tracker import BandwidthTracker

    client = _make_client()
    client.settings = DispatcharrSettings(
        url="http://disp.local:9191",
        use_ws_channel_stats=True,
        ws_suppress_poll_when_healthy=False,
    )
    client.get_channel_stats = AsyncMock(
        return_value={"channels": [{"channel_id": "c1", "total_bytes": 100}]}
    )
    tracker = BandwidthTracker(client, poll_interval=10)
    tracker._process_channel_snapshot = AsyncMock()
    sub = MagicMock()
    sub.ws_healthy = True
    sub.last_snapshot_channel_count = 1
    sub.last_snapshot_total_bytes = 100
    tracker._ws_subscriber = sub

    await tracker._collect_stats()
    client.get_channel_stats.assert_awaited_once()  # fetched for cross-validation
    tracker._process_channel_snapshot.assert_not_awaited()  # but NOT processed


@pytest.mark.asyncio
async def test_poll_processes_when_ws_unhealthy():
    """WS enabled but unhealthy => poll behaves exactly as today (fetch+process)."""
    from bandwidth_tracker import BandwidthTracker

    client = _make_client()
    client.settings = DispatcharrSettings(
        url="http://disp.local:9191",
        use_ws_channel_stats=True,
        ws_suppress_poll_when_healthy=True,
    )
    client.get_channel_stats = AsyncMock(return_value={"channels": [{"channel_id": "c1"}]})
    tracker = BandwidthTracker(client, poll_interval=10)
    tracker._process_channel_snapshot = AsyncMock()
    sub = MagicMock()
    sub.ws_healthy = False  # WS down -> poll is the live driver
    tracker._ws_subscriber = sub

    await tracker._collect_stats()
    client.get_channel_stats.assert_awaited_once()
    tracker._process_channel_snapshot.assert_awaited_once()
