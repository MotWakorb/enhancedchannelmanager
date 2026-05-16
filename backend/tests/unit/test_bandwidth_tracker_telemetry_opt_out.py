"""Unit tests for the operator-facing Stats v2 telemetry opt-out
(bead ``enhancedchannelmanager-tp1pd``).

This is the *fast-follow* to skqln.3 step (d). Step (d) retired the
transitional ``ECM_SESSION_TELEMETRY_WRITE_ENABLED`` kill-switch because
its only job was the additive-write→single-write transition gate, and
once legacy writes were gone there was nothing left for it to gate.

tp1pd re-introduces a switch — but in a different role:

* **Name**: ``ECM_STATS_TELEMETRY_OPT_OUT`` (default ``false``).
* **Audience**: operators who want full Stats v2 data-collection opt-out
  for privacy reasons. Not engineers; not a transition flag.
* **Scope**: when ON, the entire Stats v2 write path is short-circuited:
  the provider resolver does NOT issue Dispatcharr calls, the buffer-
  event ingest does NOT issue Dispatcharr calls, and zero rows land in
  ``session_telemetry``. Legacy stats writes (``ChannelBandwidth``,
  ``BandwidthDaily``, ``UniqueClientConnection``) are NOT affected — they
  pre-date Stats v2 and are not part of the new data flow being opted
  out of.

These tests live in their own module rather than extending the existing
``test_bandwidth_tracker_session_telemetry.py`` suite because the opt-out
behavior is conceptually distinct from the unconditional-write semantics
that suite is the regression guard for. Two adjacent modules make the
test surface readable; the conflict surface with other parallel beads
(bd-7i2vv rollup tables, bd-skqln.9 user-guide writer) stays minimal.

Synthetic identities only — ``docs/security/threat_model_stats_v2.md``
§7.7.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

import bandwidth_tracker
import database
from bandwidth_tracker import BandwidthTracker
from models import (
    BandwidthDaily,
    ChannelBandwidth,
    SessionTelemetry,
    UniqueClientConnection,
    User,
)


# ---------------------------------------------------------------------------
# Fixtures — mirror the ones in test_bandwidth_tracker_session_telemetry.py
# so the two suites read alike. Kept local to this module so a refactor of
# either set of tests does not propagate side-effects across files.
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
    """Stub the Dispatcharr client surface BandwidthTracker calls.

    Defaults: empty channels list and empty system-events feed. Individual
    tests override ``get_channel_stats`` per poll. The opt-out tests use
    ``await_count`` on ``get_streams_by_ids`` and ``get_system_events`` to
    prove the short-circuit avoided the round-trip.
    """
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
    return BandwidthTracker(client=mock_client, poll_interval=10)


@pytest.fixture
def seed_synthetic_user(patched_session_local):
    """Insert one synthetic user so session_telemetry.user_id FKs satisfy."""
    session = patched_session_local()
    try:
        user = User(
            id=43,
            username="synthetic-tp1pd-user",
            email="synthetic-tp1pd@example.invalid",
            auth_provider="local",
            is_active=True,
        )
        session.add(user)
        session.commit()
        return user.id
    finally:
        session.close()


def _channel_payload(
    *,
    channel_uuid: str = "ch-uuid-1",
    channel_number: int = 101,
    total_bytes: int = 0,
    client_count: int = 1,
    client_ips: list[str] | None = None,
    client_user_ids: dict[str, int] | None = None,
    avg_bitrate_kbps: int = 1000,
    name: str = "Test Channel",
    stream_id: int | None = 555,
) -> dict:
    """Single ``channels[]`` entry as Dispatcharr's /proxy/ts/status surfaces
    it. Defaults match a single-viewer stream; ``stream_id`` defaults to a
    non-None value so the opt-out tests can prove the resolver is skipped
    (a None stream_id short-circuits the resolver for an unrelated reason
    and would mask the opt-out short-circuit).
    """
    if client_ips is None:
        client_ips = ["10.0.0.1"]
    if client_user_ids is None:
        client_user_ids = {}
    clients = [
        {"ip_address": ip, "user_id": client_user_ids.get(ip)}
        for ip in client_ips
    ]
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
    return payload


async def _drive_two_polls(tracker, mock_client, first_payload, second_payload):
    """Run ``_collect_stats`` twice so the second poll has a real per-channel
    byte delta and an open active-connection row. Mirrors the helper in the
    sibling test module.
    """
    mock_client.get_channel_stats.return_value = {"channels": [first_payload]}
    await tracker._collect_stats()
    mock_client.get_channel_stats.return_value = {"channels": [second_payload]}
    await tracker._collect_stats()


# ---------------------------------------------------------------------------
# Env-var parsing
# ---------------------------------------------------------------------------

def test_opt_out_parser_default_is_false(monkeypatch):
    """Default behavior: env var unset → opt-out OFF → telemetry writes.

    This is the "no operator action" path. The same default skqln.3 step
    (d) made permanent (writes happen) is preserved here as a parser-
    level invariant.
    """
    monkeypatch.delenv("ECM_STATS_TELEMETRY_OPT_OUT", raising=False)
    assert bandwidth_tracker._telemetry_opt_out_enabled() is False


@pytest.mark.parametrize(
    "value",
    ["true", "True", "TRUE", "1", "yes", "on", "  true  "],
)
def test_opt_out_parser_truthy_values_enable_opt_out(monkeypatch, value):
    """The truthy set mirrors the precedent set by
    ``_confusables_fold_enabled`` (``normalization_engine.py``) — ``true``,
    ``1``, ``yes``, ``on`` (case-insensitive, whitespace-tolerant).
    """
    monkeypatch.setenv("ECM_STATS_TELEMETRY_OPT_OUT", value)
    assert bandwidth_tracker._telemetry_opt_out_enabled() is True


@pytest.mark.parametrize(
    "value",
    ["false", "False", "0", "no", "off", "", "garbage", "  false  "],
)
def test_opt_out_parser_falsy_values_keep_writes_on(monkeypatch, value):
    """Anything outside the truthy enum is treated as opt-out OFF."""
    monkeypatch.setenv("ECM_STATS_TELEMETRY_OPT_OUT", value)
    assert bandwidth_tracker._telemetry_opt_out_enabled() is False


# ---------------------------------------------------------------------------
# Short-circuit behavior in _collect_stats
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_opt_out_off_default_telemetry_writes_happen(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    monkeypatch,
):
    """Regression guard: env var unset → behavior is identical to
    pre-tp1pd. session_telemetry rows are written, the resolver IS called,
    and the system-events feed IS fetched.
    """
    monkeypatch.delenv("ECM_STATS_TELEMETRY_OPT_OUT", raising=False)
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    # Poll 1 opens the connection and writes one row; poll 2 writes the
    # second row. Both polls produce rows because opt-out is off.
    assert len(rows) == 2
    # Provider resolver and system-events feed were both consulted (once
    # per poll); the Dispatcharr round-trips happened.
    assert mock_client.get_streams_by_ids.await_count == 2
    assert mock_client.get_system_events.await_count == 2


@pytest.mark.asyncio
async def test_opt_out_on_writes_zero_session_telemetry_rows(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    monkeypatch,
):
    """Opt-out ON: no rows land in ``session_telemetry``. This is the
    primary acceptance criterion for the bead — "toggle OFF → no
    session_telemetry rows written".
    """
    monkeypatch.setenv("ECM_STATS_TELEMETRY_OPT_OUT", "true")
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows == [], (
        "session_telemetry must be empty under opt-out. Got: "
        f"{[(r.session_id, r.channel_id, r.bytes_delta) for r in rows]}"
    )


@pytest.mark.asyncio
async def test_opt_out_on_legacy_writes_still_happen(
    patched_session_local,
    tracker,
    mock_client,
    monkeypatch,
):
    """Opt-out ON: the legacy stats path is UNAFFECTED. ``ChannelBandwidth``,
    ``BandwidthDaily``, ``UniqueClientConnection`` continue to be written
    because they pre-date Stats v2 — the operator opted out of the new
    data collection, not the existing one.
    """
    monkeypatch.setenv("ECM_STATS_TELEMETRY_OPT_OUT", "true")
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=1_500_000)

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        connections = session.query(UniqueClientConnection).all()
        bw_daily = session.query(BandwidthDaily).all()
        ch_bw = session.query(ChannelBandwidth).all()
    finally:
        session.close()
    assert connections, (
        "UniqueClientConnection must still be written under opt-out — "
        "the legacy stats path pre-dates Stats v2 and is not part of the "
        "opt-out surface."
    )
    assert bw_daily, "BandwidthDaily must still be written under opt-out."
    assert ch_bw, "ChannelBandwidth must still be written under opt-out."


@pytest.mark.asyncio
async def test_opt_out_on_provider_resolver_does_not_call_dispatcharr(
    patched_session_local,
    tracker,
    mock_client,
    monkeypatch,
):
    """Opt-out ON: the provider resolver does NOT issue
    ``get_streams_by_ids`` calls. Saving the round-trip is half the point
    of the env-var — an opted-out deployment should not pay the cost of
    resolving data that will never be written.
    """
    monkeypatch.setenv("ECM_STATS_TELEMETRY_OPT_OUT", "true")
    first = _channel_payload(total_bytes=1_000_000, stream_id=12345)
    second = _channel_payload(total_bytes=2_000_000, stream_id=12345)

    await _drive_two_polls(tracker, mock_client, first, second)

    assert mock_client.get_streams_by_ids.await_count == 0, (
        "Provider resolver must skip the Dispatcharr round-trip when "
        "telemetry opt-out is ENABLED."
    )


@pytest.mark.asyncio
async def test_opt_out_on_buffer_event_ingest_does_not_call_dispatcharr(
    patched_session_local,
    tracker,
    mock_client,
    monkeypatch,
):
    """Opt-out ON: buffer-event ingest does NOT issue
    ``get_system_events`` calls. Same rationale as the resolver — no
    consumer downstream of the call means no need to make it.
    """
    monkeypatch.setenv("ECM_STATS_TELEMETRY_OPT_OUT", "true")
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=1_500_000)

    await _drive_two_polls(tracker, mock_client, first, second)

    assert mock_client.get_system_events.await_count == 0, (
        "Buffer-event ingest must skip the Dispatcharr round-trip when "
        "telemetry opt-out is ENABLED."
    )


@pytest.mark.asyncio
async def test_opt_out_runtime_flip_takes_effect_on_next_poll(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    monkeypatch,
):
    """The env var is read per-poll, not cached at import time. An
    operator who flips the flag at runtime (e.g., docker exec + export +
    container restart-as-they-go) sees the new value on the next poll
    cycle.

    The per-poll read is a deliberate cost/correctness trade — env-var
    parsing is cheap (microseconds) and the latency-sensitive path is
    Dispatcharr's network round-trip, not a string compare.
    """
    # Poll 1: opt-out OFF, writes happen.
    monkeypatch.delenv("ECM_STATS_TELEMETRY_OPT_OUT", raising=False)
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )
    mock_client.get_channel_stats.return_value = {"channels": [first]}
    await tracker._collect_stats()

    # Poll 2: operator flips opt-out ON. Same channel, more bytes — but
    # no new session_telemetry row.
    monkeypatch.setenv("ECM_STATS_TELEMETRY_OPT_OUT", "true")
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )
    mock_client.get_channel_stats.return_value = {"channels": [second]}
    await tracker._collect_stats()

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    # Only the poll-1 row exists; poll 2's writes were short-circuited.
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Startup log
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_log_emitted_when_opt_out_enabled(
    patched_session_local,
    tracker,
    mock_client,
    monkeypatch,
    caplog,
):
    """When the env var is ON at ``start()`` time, the tracker emits one
    INFO-level log line announcing the opt-out. Operators reading
    ``docker logs`` see immediately that Stats v2 is silenced — there's
    no other surface for that state (no row count to look at, no API
    response to compare).

    The line must use the ``[STATS_V2]`` prefix so it groups with the
    rest of the Stats v2 log surface in container logs.
    """
    monkeypatch.setenv("ECM_STATS_TELEMETRY_OPT_OUT", "true")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bandwidth_tracker"):
        await tracker.start()
        try:
            opt_out_log_lines = [
                r for r in caplog.records
                if "[STATS_V2]" in r.message and "opt-out" in r.message.lower()
            ]
        finally:
            await tracker.stop()
    assert opt_out_log_lines, (
        "Expected one [STATS_V2] startup log line when opt-out is enabled. "
        f"Got: {[r.message for r in caplog.records]}"
    )
    # Exactly one announcement — not per-poll spam.
    assert len(opt_out_log_lines) == 1


@pytest.mark.asyncio
async def test_startup_log_silent_when_opt_out_disabled(
    patched_session_local,
    tracker,
    mock_client,
    monkeypatch,
    caplog,
):
    """When the env var is OFF (default), the opt-out announcement does
    NOT appear. Log noise on the default path is a no-go — operators who
    aren't opting out should not see a line about a feature they aren't
    using.
    """
    monkeypatch.delenv("ECM_STATS_TELEMETRY_OPT_OUT", raising=False)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bandwidth_tracker"):
        await tracker.start()
        try:
            opt_out_log_lines = [
                r for r in caplog.records
                if "[STATS_V2]" in r.message and "opt-out" in r.message.lower()
            ]
        finally:
            await tracker.stop()
    assert opt_out_log_lines == [], (
        "Opt-out announcement must NOT appear when the flag is off. "
        f"Got: {[r.message for r in opt_out_log_lines]}"
    )


@pytest.mark.asyncio
async def test_startup_log_not_repeated_across_poll_cycles(
    patched_session_local,
    tracker,
    mock_client,
    monkeypatch,
    caplog,
):
    """The opt-out announcement is a one-shot at ``start()``. Polls do
    NOT re-emit it — that would flood ``docker logs`` with a steady drip
    of identical lines (one per ``poll_interval`` second, default every
    10s).
    """
    monkeypatch.setenv("ECM_STATS_TELEMETRY_OPT_OUT", "true")
    # Drive a couple of poll cycles directly (no start/stop loop — keeps
    # the test deterministic without sleeping the wall clock).
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=1_500_000)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bandwidth_tracker"):
        await _drive_two_polls(tracker, mock_client, first, second)
    opt_out_log_lines = [
        r for r in caplog.records
        if "[STATS_V2]" in r.message and "opt-out" in r.message.lower()
    ]
    assert opt_out_log_lines == [], (
        "_collect_stats must not re-emit the opt-out announcement on "
        "each poll. The announcement belongs in start() exactly once. "
        f"Got: {[r.message for r in opt_out_log_lines]}"
    )
