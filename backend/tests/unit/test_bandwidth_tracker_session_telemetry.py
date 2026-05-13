"""Unit tests for the unconditional ``session_telemetry`` write inside
``BandwidthTracker`` (bead ``enhancedchannelmanager-skqln.3`` step (d)).

Step (d) flipped the additive-write into the only-write: the legacy
``ChannelWatchStats`` write inside ``_collect_stats`` is gone, and the
``ECM_SESSION_TELEMETRY_WRITE_ENABLED`` feature flag has been retired.
The kill-switch's only purpose was the (a)→(d) transition; once legacy
writes are removed there is no off-state to gate.

Step (d) goals (mirrored by the test names below):

* ``session_telemetry`` rows are written unconditionally — no env-var
  guard, no fallback path. Every poll with active viewing connections
  produces one row per connection.
* The legacy ``ChannelWatchStats`` write inside ``_collect_stats`` is
  gone. No row is created in that table by a polling cycle.
* The helper still wraps its own work in try/except so an internal
  failure (constructor raise, schema mismatch, etc.) cannot propagate
  out of ``_collect_stats``. The legacy ``UniqueClientConnection`` /
  ``ChannelBandwidth`` writes that ran *before* the helper survive.

The tests drive ``_collect_stats`` end-to-end through a stubbed
``DispatcharrClient`` so the BandwidthTracker's own per-channel rollup
code paths are exercised. The session_telemetry schema
(``models.SessionTelemetry``) is created from ``Base.metadata`` in the
existing in-memory ``test_engine`` fixture from ``tests/conftest.py``.

Synthetic identities only — ``docs/security/threat_model_stats_v2.md``
§7.7.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import database
from bandwidth_tracker import BandwidthTracker
from models import ChannelWatchStats, SessionTelemetry, UniqueClientConnection, User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def patched_session_local(test_engine, monkeypatch):
    """Point ``database.get_session`` at the in-memory ``test_engine``.

    BandwidthTracker calls ``get_session()`` directly (not via FastAPI's
    DI), so we route ``database._SessionLocal`` to a sessionmaker bound to
    the test engine. ``expire_on_commit=False`` lets tests inspect ORM
    objects after the production code commits.
    """
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
    return client


@pytest.fixture
def tracker(mock_client):
    """A BandwidthTracker wired to the stub client.

    Tracker is not ``start()``ed — tests drive ``_collect_stats`` directly
    so they can fix the poll-by-poll sequence without sleeping.
    """
    return BandwidthTracker(client=mock_client, poll_interval=10)


@pytest.fixture
def seed_synthetic_user(patched_session_local):
    """Insert one synthetic ``users`` row so the ``session_telemetry.user_id``
    FK can be satisfied when tests want a non-NULL ``user_id``. Returns the
    inserted user's id.

    Synthetic only — see ``docs/security/threat_model_stats_v2.md`` §7.7.
    """
    session = patched_session_local()
    try:
        user = User(
            id=42,
            username="synthetic-skqln3-user",
            email="synthetic-skqln3@example.invalid",
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
    stream_id: int | None = None,
) -> dict:
    """Build a single ``channels[]`` entry as Dispatcharr's stats endpoint
    surfaces it. Defaults match a single-viewer stream.

    ``stream_id`` mirrors the per-channel ``stream_id`` field Dispatcharr's
    ``/proxy/ts/status`` payload surfaces — the integer ID of the stream
    currently being served. This is the input the provider resolver (bead
    skqln.14) uses to map the active stream back to its ``m3u_account_id``.
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
    """Run ``_collect_stats`` twice so the second poll has a per-channel
    byte delta and the ``_active_connections`` map is populated (the first
    poll opens connections, the second poll counts as ``still_active`` and
    is what the telemetry helper observes).
    """
    mock_client.get_channel_stats.return_value = {"channels": [first_payload]}
    await tracker._collect_stats()
    mock_client.get_channel_stats.return_value = {"channels": [second_payload]}
    await tracker._collect_stats()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_writes_session_telemetry_unconditionally_no_flag(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    monkeypatch,
):
    """Step (d): no env-var gate. Writes happen on every poll cycle.

    Sets the legacy flag env-var to ``false`` (the historical "off"
    value). The retirement means that setting has no effect — rows are
    still written. The test deliberately sets the var rather than
    unsetting it to prove the absence of any vestigial gate.
    """
    monkeypatch.setenv("ECM_SESSION_TELEMETRY_WRITE_ENABLED", "false")
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
    # second row. Both polls produce rows regardless of any env-var state.
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_writes_session_telemetry_row_per_connection(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """One row per active client connection per poll, with the documented
    step-(a) column shape preserved.

    Two polls drive the tracker. Poll 1 opens the connection and records
    a presence row (``bytes_delta=0`` — no prior cumulative-bytes value
    means the delta is by definition zero). Poll 2 records a row with
    the actual transferred bytes_delta. Both rows attribute to the same
    ``session_id`` (the connection lives across polls).
    """
    first = _channel_payload(
        total_bytes=1_000_000,
        client_ips=["10.0.0.1"],
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )
    second = _channel_payload(
        total_bytes=2_500_000,
        client_ips=["10.0.0.1"],
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = (
            session.query(SessionTelemetry)
            .order_by(SessionTelemetry.id)
            .all()
        )
        assert len(rows) == 2
        # Both rows belong to the same connection.
        session_ids = {r.session_id for r in rows}
        assert len(session_ids) == 1
        assert next(iter(session_ids)).startswith("conn-")

        # Common shape on every row.
        for row in rows:
            assert row.observed_at > 0
            assert row.poll_interval_ms == 10_000  # 10s poll × 1000
            assert row.user_id == seed_synthetic_user
            assert row.channel_id == "ch-uuid-1"
            assert row.provider_id is None
            assert row.buffer_event_count == 0

        # Poll 1: zero per-channel byte delta (first observation),
        # Poll 2: 1_500_000 split equally across one client.
        assert rows[0].bytes_delta == 0
        assert rows[1].bytes_delta == 1_500_000
    finally:
        session.close()


@pytest.mark.asyncio
async def test_splits_bytes_delta_equally_across_clients(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """A single channel with two concurrent clients yields one
    session_telemetry row per client per poll; the channel byte delta is
    split equally (integer floor)."""
    # Two synthetic clients on one channel. user_id mapping intentionally
    # left empty so the rows write with user_id=NULL — keeps the test
    # focused on the per-client byte-split contract without depending on
    # extra synthetic User fixtures.
    first = _channel_payload(
        total_bytes=1_000_000,
        client_count=2,
        client_ips=["10.0.0.1", "10.0.0.2"],
    )
    second = _channel_payload(
        total_bytes=3_000_000,
        client_count=2,
        client_ips=["10.0.0.1", "10.0.0.2"],
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
        # 2 connections × 2 polls = 4 rows.
        assert len(rows) == 4
        # Poll-2 rows (bytes_delta > 0). Each client gets the floor of an
        # equal share: 2_000_000 // 2 = 1_000_000.
        poll2_bytes = sorted(
            r.bytes_delta for r in rows if r.bytes_delta > 0
        )
        assert poll2_bytes == [1_000_000, 1_000_000]
        # Each row carries the same observed_at as its poll-mates.
        observed_set = {r.observed_at for r in rows}
        assert len(observed_set) == 2  # one timestamp per poll
        channel_ids = {r.channel_id for r in rows}
        assert channel_ids == {"ch-uuid-1"}
    finally:
        session.close()


@pytest.mark.asyncio
async def test_legacy_channel_watch_stats_is_not_written(
    patched_session_local,
    tracker,
    mock_client,
):
    """Step (d) removed the ``ChannelWatchStats`` write inside ``_collect_stats``.

    A polling cycle that *would have* created a legacy row in step (a)/(c)
    must now leave that table empty. The non-aggregate sibling tables
    (``UniqueClientConnection``) are still written — only the lifetime
    aggregate is gone.

    This is the keystone "legacy write retired" regression: if a later
    change re-introduces the legacy write (defensively, or via revert),
    this test catches it.
    """
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=1_500_000)

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        legacy_rows = session.query(ChannelWatchStats).all()
        assert legacy_rows == [], (
            "ChannelWatchStats must not be written from _collect_stats "
            "after bd-skqln.3 step (d). Got: "
            f"{[(r.channel_id, r.watch_count, r.total_watch_seconds) for r in legacy_rows]}"
        )
        # Sanity: the non-legacy sibling writes still happen.
        connections = session.query(UniqueClientConnection).all()
        telemetry = session.query(SessionTelemetry).all()
        assert connections, "UniqueClientConnection must still be written"
        assert telemetry, "session_telemetry must still be written"
    finally:
        session.close()


@pytest.mark.asyncio
async def test_helper_internal_failure_is_swallowed(
    patched_session_local,
    tracker,
    mock_client,
    caplog,
):
    """Helper's internal try/except: an exception inside the
    session_telemetry write path must not propagate out of
    ``_collect_stats``. The sibling writes that ran *before* the helper
    (UniqueClientConnection) survive.

    Sabotage point: patch the ``bandwidth_tracker.SessionTelemetry``
    module-level reference so constructing a row inside the helper raises.
    """
    import logging as _logging

    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=1_500_000)

    # Poll 1 runs cleanly.
    mock_client.get_channel_stats.return_value = {"channels": [first]}
    await tracker._collect_stats()

    with patch(
        "bandwidth_tracker.SessionTelemetry",
        side_effect=RuntimeError("simulated row build failure"),
    ):
        mock_client.get_channel_stats.return_value = {"channels": [second]}
        caplog.clear()
        with caplog.at_level(_logging.ERROR, logger="bandwidth_tracker"):
            try:
                await tracker._collect_stats()
            except Exception:  # pragma: no cover — defensive only
                pytest.fail(
                    "_collect_stats must not propagate exceptions from "
                    "_write_session_telemetry"
                )
        # The failure was logged at ERROR level with the [STATS_V2] prefix.
        assert any(
            "[STATS_V2]" in record.message and "session_telemetry write failed" in record.message
            for record in caplog.records
        )

    # Sibling writes survived both polls.
    session = patched_session_local()
    try:
        connections = session.query(UniqueClientConnection).all()
        assert connections, (
            "UniqueClientConnection must survive a session_telemetry helper "
            "failure — sibling writes commit before the helper runs."
        )
        # Poll 1 (before the patch) wrote one session_telemetry row.
        # Poll 2 (under the patch) raised inside the helper and was
        # swallowed — no second row was committed.
        telemetry_rows = session.query(SessionTelemetry).all()
        assert len(telemetry_rows) == 1
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Provider resolver tests (bd-skqln.14)
# ---------------------------------------------------------------------------
#
# Step (a)–(d) of skqln.3 left ``provider_id`` permanently NULL because the
# active-stream → provider mapping did not exist. skqln.14 wires that in:
#
# * Dispatcharr's ``/proxy/ts/status`` payload surfaces ``stream_id`` per
#   channel (the integer ID of the stream currently being served — the
#   ``StatsTab`` frontend already renders this from the same payload).
# * ``DispatcharrClient.get_streams_by_ids`` returns each stream's
#   ``m3u_account`` (either a bare int or ``{"id": N, ...}`` —
#   ``stream_prober.extract_m3u_account_id`` normalizes both shapes).
# * The resolver fetches the (stream_id → m3u_account_id) map ONCE per
#   poll (batched single API call, not N-per-channel) and caches it for
#   the duration of one ``_collect_stats`` invocation. The cache is
#   intentionally scoped to a single poll so a stream's provider can
#   change between polls without staleness.
#
# Failure modes are non-fatal: a row missing ``stream_id`` (Dispatcharr
# didn't surface it), a 404/exception on ``get_streams_by_ids``, or a
# stream whose ``m3u_account`` is None — all produce ``provider_id=NULL``
# plus a structured ``[STATS_V2] provider_resolution_failed`` log so
# skqln.12 can derive a Prometheus metric later. The
# ``session_telemetry`` row is still written; the column is just NULL.


@pytest.mark.asyncio
async def test_resolver_attaches_provider_id_for_single_stream_channel(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Happy path — channel surfaces a ``stream_id``; resolver fetches the
    stream's ``m3u_account`` and writes it as ``provider_id``."""
    stream_id = 555
    provider_id = 7
    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[{"id": stream_id, "m3u_account": provider_id}]
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=stream_id,
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=stream_id,
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).order_by(SessionTelemetry.id).all()
    finally:
        session.close()
    assert len(rows) == 2
    assert all(r.provider_id == provider_id for r in rows), [
        (r.id, r.provider_id) for r in rows
    ]


@pytest.mark.asyncio
async def test_resolver_handles_failover_active_stream(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Channel with multiple failover streams reports only the active one
    in ``stream_id``. The resolver picks up that stream's provider — NOT
    the failover-list head — proving it keys on what Dispatcharr says is
    live, not the channel's stream-priority list.

    Three streams, three providers. Poll 1 reports stream_id=200 (provider
    2). Poll 2 reports stream_id=300 (provider 3 — failover hopped). The
    two rows must carry the providers that were active at observation
    time, not a single static value.
    """
    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[
            {"id": 100, "m3u_account": 1},
            {"id": 200, "m3u_account": 2},
            {"id": 300, "m3u_account": 3},
        ]
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=200,
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=300,
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).order_by(SessionTelemetry.id).all()
    finally:
        session.close()
    assert len(rows) == 2
    assert rows[0].provider_id == 2
    assert rows[1].provider_id == 3


@pytest.mark.asyncio
async def test_resolver_nested_m3u_account_object(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Dispatcharr historically returns ``m3u_account`` as either a bare
    int or a nested ``{"id": N, "name": ...}`` object — the canonical
    ``extract_m3u_account_id`` helper at ``stream_prober.py`` already
    normalizes both. The resolver must use that helper (not re-parse the
    field locally) so the schema-shape contract is owned in exactly one
    place.
    """
    stream_id = 555
    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[
            {
                "id": stream_id,
                "m3u_account": {"id": 9, "name": "Provider Nine"},
            }
        ]
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=stream_id,
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=stream_id,
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows and all(r.provider_id == 9 for r in rows)


@pytest.mark.asyncio
async def test_resolver_returns_null_when_no_stream_id(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    caplog,
):
    """No ``stream_id`` on the channel payload — resolver cannot identify
    an active stream. Row is still written with ``provider_id=NULL`` and
    a structured ``[STATS_V2] provider_resolution_failed`` log is emitted.
    """
    import logging as _logging

    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )

    caplog.clear()
    with caplog.at_level(_logging.WARNING, logger="bandwidth_tracker"):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows, "session_telemetry rows must still be written"
    assert all(r.provider_id is None for r in rows)
    assert any(
        "[STATS_V2] provider_resolution_failed" in record.message
        for record in caplog.records
    ), [r.message for r in caplog.records]


@pytest.mark.asyncio
async def test_resolver_returns_null_when_stream_lookup_returns_empty(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    caplog,
):
    """``get_streams_by_ids`` returns an empty list (Dispatcharr 404'd or
    the stream was deleted). Resolver returns NULL for every channel; the
    row is still written and ``[STATS_V2] provider_resolution_failed``
    fires once per unresolved channel."""
    import logging as _logging

    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=555,
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=555,
    )

    caplog.clear()
    with caplog.at_level(_logging.WARNING, logger="bandwidth_tracker"):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows and all(r.provider_id is None for r in rows)
    assert any(
        "[STATS_V2] provider_resolution_failed" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_resolver_returns_null_when_lookup_raises(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    caplog,
):
    """The Dispatcharr round-trip raises (timeout, 5xx, network error).
    Resolver falls back to NULL and the polling cycle continues; rows
    are still written.
    """
    import logging as _logging

    mock_client.get_streams_by_ids = AsyncMock(side_effect=RuntimeError("boom"))
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=555,
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=555,
    )

    caplog.clear()
    with caplog.at_level(_logging.WARNING, logger="bandwidth_tracker"):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows and all(r.provider_id is None for r in rows)
    assert any(
        "[STATS_V2] provider_resolution_failed" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_resolver_batches_lookup_once_per_poll(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Multiple channels in one poll share a single ``get_streams_by_ids``
    call — cheap path. Two channels, two streams, two providers: still
    one API call per poll. The bead's per-poll performance constraint
    rejects N-channels-times-N-Dispatcharr-calls.
    """
    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[
            {"id": 100, "m3u_account": 1},
            {"id": 200, "m3u_account": 2},
        ]
    )
    ch_a_first = _channel_payload(
        channel_uuid="ch-a",
        channel_number=101,
        total_bytes=1_000_000,
        client_ips=["10.0.0.1"],
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=100,
    )
    ch_b_first = _channel_payload(
        channel_uuid="ch-b",
        channel_number=102,
        total_bytes=1_000_000,
        client_ips=["10.0.0.2"],
        stream_id=200,
    )
    ch_a_second = _channel_payload(
        channel_uuid="ch-a",
        channel_number=101,
        total_bytes=2_000_000,
        client_ips=["10.0.0.1"],
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=100,
    )
    ch_b_second = _channel_payload(
        channel_uuid="ch-b",
        channel_number=102,
        total_bytes=2_000_000,
        client_ips=["10.0.0.2"],
        stream_id=200,
    )

    mock_client.get_channel_stats.return_value = {
        "channels": [ch_a_first, ch_b_first]
    }
    await tracker._collect_stats()
    # Capture call count after the first poll, then drive a second poll.
    call_count_after_first = mock_client.get_streams_by_ids.call_count
    mock_client.get_channel_stats.return_value = {
        "channels": [ch_a_second, ch_b_second]
    }
    await tracker._collect_stats()
    call_count_after_second = mock_client.get_streams_by_ids.call_count

    # First poll: one fetch even with two channels (the writes happen on
    # both polls because step (a) writes the connection-open row too, so
    # the fetch happens on the first poll for both channels).
    assert call_count_after_first == 1
    # Second poll: one additional fetch (cache resets per poll).
    assert call_count_after_second == 2

    session = patched_session_local()
    try:
        rows = (
            session.query(SessionTelemetry)
            .order_by(SessionTelemetry.id)
            .all()
        )
    finally:
        session.close()
    provider_by_channel = {(r.channel_id, r.provider_id) for r in rows}
    assert ("ch-a", 1) in provider_by_channel
    assert ("ch-b", 2) in provider_by_channel


@pytest.mark.asyncio
async def test_resolver_cache_does_not_leak_across_polls(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Cache is scoped to a single ``_collect_stats`` invocation. A
    stream that newly hops providers between polls picks up the new
    mapping immediately — the previous-poll cache is not consulted.
    """
    # Poll 1 + Poll 2 — both look up stream_id=555. The stream's provider
    # changes between polls (provider 1 → provider 2). Without a per-poll
    # cache reset, the second poll would still report provider 1.
    poll1_response = [{"id": 555, "m3u_account": 1}]
    poll2_response = [{"id": 555, "m3u_account": 2}]
    mock_client.get_streams_by_ids = AsyncMock(
        side_effect=[poll1_response, poll2_response]
    )

    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=555,
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=555,
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = (
            session.query(SessionTelemetry)
            .order_by(SessionTelemetry.id)
            .all()
        )
    finally:
        session.close()
    assert len(rows) == 2
    assert rows[0].provider_id == 1
    assert rows[1].provider_id == 2


@pytest.mark.asyncio
async def test_resolver_emits_data_consistency_sli_log(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    caplog,
):
    """Per-poll structured log line surfaces ``(resolved_count,
    unresolved_count)`` so skqln.12 can derive a Prometheus
    ``stats_v2_provider_resolution_total{result=...}`` metric without
    plumbing a new code path through. The log prefix is
    ``[STATS_V2] provider_resolution`` — distinct from the failure
    log so it does not collide on a substring search.

    Two channels, one resolvable, one unresolvable (missing ``stream_id``):
    expect a single SLI line with resolved=1 unresolved=1.
    """
    import logging as _logging

    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[{"id": 555, "m3u_account": 7}]
    )
    ch_a = _channel_payload(
        channel_uuid="ch-a",
        channel_number=101,
        total_bytes=1_000_000,
        client_ips=["10.0.0.1"],
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=555,
    )
    ch_b = _channel_payload(
        channel_uuid="ch-b",
        channel_number=102,
        total_bytes=1_000_000,
        client_ips=["10.0.0.2"],
        # No stream_id — unresolvable.
    )
    ch_a_second = _channel_payload(
        channel_uuid="ch-a",
        channel_number=101,
        total_bytes=2_000_000,
        client_ips=["10.0.0.1"],
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=555,
    )
    ch_b_second = _channel_payload(
        channel_uuid="ch-b",
        channel_number=102,
        total_bytes=2_000_000,
        client_ips=["10.0.0.2"],
    )

    mock_client.get_channel_stats.return_value = {"channels": [ch_a, ch_b]}
    await tracker._collect_stats()
    caplog.clear()
    mock_client.get_channel_stats.return_value = {
        "channels": [ch_a_second, ch_b_second]
    }
    with caplog.at_level(_logging.INFO, logger="bandwidth_tracker"):
        await tracker._collect_stats()

    # SLI line carries both counts; assert the substring shape so the
    # metric-extractor (skqln.12) has a stable contract.
    sli_lines = [
        r.message
        for r in caplog.records
        if "[STATS_V2] provider_resolution " in r.message
    ]
    assert sli_lines, (
        "expected at least one [STATS_V2] provider_resolution SLI log line; "
        f"got: {[r.message for r in caplog.records]}"
    )
    # The exact line shape: "[STATS_V2] provider_resolution resolved=N unresolved=M"
    assert any(
        "resolved=1" in m and "unresolved=1" in m for m in sli_lines
    ), sli_lines


@pytest.mark.asyncio
async def test_resolver_skips_lookup_when_no_resolvable_streams(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """If no channel in the poll surfaced a ``stream_id``, the resolver
    must not issue a ``get_streams_by_ids`` call — saves a round-trip
    on degraded-stats payloads."""
    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    assert mock_client.get_streams_by_ids.await_count == 0


@pytest.mark.asyncio
async def test_resolver_handles_null_m3u_account_on_stream(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    caplog,
):
    """Stream exists in Dispatcharr but ``m3u_account`` is None (orphaned
    stream — provider was deleted). ``provider_id`` stays NULL, failure
    log fires.
    """
    import logging as _logging

    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[{"id": 555, "m3u_account": None}]
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=555,
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=555,
    )

    caplog.clear()
    with caplog.at_level(_logging.WARNING, logger="bandwidth_tracker"):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows and all(r.provider_id is None for r in rows)
    assert any(
        "[STATS_V2] provider_resolution_failed" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_resolver_integration_three_providers_distribution(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """End-to-end: three channels mapping to three different providers in
    one poll cycle produce session_telemetry rows whose provider_id
    distribution matches the seeded stream→provider mapping.
    """
    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[
            {"id": 11, "m3u_account": 1},
            {"id": 22, "m3u_account": 2},
            {"id": 33, "m3u_account": 3},
        ]
    )
    ch_a = _channel_payload(
        channel_uuid="ch-a",
        channel_number=101,
        total_bytes=1_000_000,
        client_ips=["10.0.0.1"],
        stream_id=11,
    )
    ch_b = _channel_payload(
        channel_uuid="ch-b",
        channel_number=102,
        total_bytes=1_000_000,
        client_ips=["10.0.0.2"],
        stream_id=22,
    )
    ch_c = _channel_payload(
        channel_uuid="ch-c",
        channel_number=103,
        total_bytes=1_000_000,
        client_ips=["10.0.0.3"],
        stream_id=33,
    )
    ch_a_2 = _channel_payload(
        channel_uuid="ch-a",
        channel_number=101,
        total_bytes=2_000_000,
        client_ips=["10.0.0.1"],
        stream_id=11,
    )
    ch_b_2 = _channel_payload(
        channel_uuid="ch-b",
        channel_number=102,
        total_bytes=2_000_000,
        client_ips=["10.0.0.2"],
        stream_id=22,
    )
    ch_c_2 = _channel_payload(
        channel_uuid="ch-c",
        channel_number=103,
        total_bytes=2_000_000,
        client_ips=["10.0.0.3"],
        stream_id=33,
    )

    mock_client.get_channel_stats.return_value = {
        "channels": [ch_a, ch_b, ch_c]
    }
    await tracker._collect_stats()
    mock_client.get_channel_stats.return_value = {
        "channels": [ch_a_2, ch_b_2, ch_c_2]
    }
    await tracker._collect_stats()

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
        # 3 channels × 2 polls = 6 rows. Distribution: 2 rows per provider.
        from collections import Counter
        distribution = Counter(r.provider_id for r in rows)
    finally:
        session.close()
    assert distribution == Counter({1: 2, 2: 2, 3: 2}), distribution


# ---------------------------------------------------------------------------
# Buffer-event ingest tests (bd-skqln.15)
# ---------------------------------------------------------------------------
#
# skqln.3/.14 wrote ``buffer_event_count = 0`` on every session_telemetry row
# because the buffer-event source was not wired in. skqln.15 wires it: every
# poll fetches Dispatcharr's ``/api/core/system-events/?event_type=buffering``
# feed, de-duplicates the surfaced events by their integer ``event.id`` against
# a bounded LRU that persists across polls, and attributes each surviving
# event to the channel's session_telemetry rows for the current poll.
#
# Attribution model (the GH-59 chart sums ``buffer_event_count`` per
# ``(provider_id, time_bucket)``):
# * Each surviving buffer event applies to a single channel.
# * Per channel, the deduplicated event count is written to the FIRST
#   session_telemetry row emitted for that channel this poll (sorted by
#   client_ip for determinism). Other rows for the same channel poll write
#   ``buffer_event_count = 0``. ``SUM(buffer_event_count) GROUP BY provider,
#   time_bucket`` then returns the correct count without per-client double-
#   counting.
# * Channel with buffer events but NO active client rows this poll: count is
#   dropped (logged at WARNING). Rare — between client disconnect and channel
#   stop. Acceptable per acceptance criteria.
#
# Provider attribution at event time:
# * Each poll re-runs the resolver. The buffer-event count for poll N
#   attributes to whatever provider is active in poll N's resolver result.
# * Cross-poll failover: poll N+1 re-resolves to the NEW provider, so events
#   that arrived in poll N+1 attribute to the new provider. This is the
#   per-poll guarantee documented in the bead.


def _system_event(
    *,
    event_id: int,
    channel_id: str | int = "ch-uuid-1",
    event_type: str = "buffering",
    ip_address: str | None = None,
    timestamp: str = "2026-05-13T15:00:00Z",
) -> dict:
    """Build a single system-event payload as Dispatcharr's
    ``/api/core/system-events/`` endpoint surfaces it.

    ``event_id`` is the dedup key — Dispatcharr assigns a monotonically
    increasing integer ``id`` to every event. ``channel_id`` is normalized
    to string at the ingest site so both Dispatcharr's numeric-id channels
    and ECM's UUID-string channels are matched.
    """
    payload: dict = {
        "id": event_id,
        "event_type": event_type,
        "channel_id": channel_id,
        "timestamp": timestamp,
    }
    if ip_address is not None:
        payload["ip_address"] = ip_address
    return payload


@pytest.mark.asyncio
async def test_buffer_event_count_attributes_to_session_telemetry_row(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Happy path: one channel, one client, one buffer event delivered in
    poll 2 → the session_telemetry row for that poll carries
    ``buffer_event_count == 1``. Poll 1's row carries ``buffer_event_count
    == 0`` because no buffer event was reported on that cycle.
    """
    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    # Poll 1: no system events.
    # Poll 2: one buffering event for our channel.
    mock_client.get_system_events = AsyncMock(
        side_effect=[
            {"events": [], "count": 0, "total": 0, "offset": 0, "limit": 1000},
            {
                "events": [
                    _system_event(event_id=1001, channel_id="ch-uuid-1"),
                ],
                "count": 1,
                "total": 1,
                "offset": 0,
                "limit": 1000,
            },
        ]
    )
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
        rows = (
            session.query(SessionTelemetry)
            .order_by(SessionTelemetry.id)
            .all()
        )
    finally:
        session.close()
    assert len(rows) == 2
    assert rows[0].buffer_event_count == 0
    assert rows[1].buffer_event_count == 1


@pytest.mark.asyncio
async def test_buffer_event_dedup_across_polls(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """The system-events feed re-delivers events that haven't yet aged out
    of Dispatcharr's window. The same ``event.id`` returned in two
    successive polls counts ONCE — the dedup LRU persists across polls.

    Two polls. Poll 1 surfaces event_id=2001 (counted). Poll 2 surfaces the
    SAME event_id=2001 (re-delivered) plus event_id=2002 (new). The total
    across rows for this channel is 2, not 3.
    """
    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    redelivered = _system_event(event_id=2001, channel_id="ch-uuid-1")
    new_event = _system_event(event_id=2002, channel_id="ch-uuid-1")
    mock_client.get_system_events = AsyncMock(
        side_effect=[
            {"events": [redelivered], "count": 1, "total": 1, "offset": 0, "limit": 1000},
            {
                "events": [new_event, redelivered],
                "count": 2,
                "total": 2,
                "offset": 0,
                "limit": 1000,
            },
        ]
    )
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
    total = sum(r.buffer_event_count for r in rows)
    assert total == 2, (
        f"Expected 2 distinct events counted across rows; got {total} "
        f"(rows={[(r.observed_at, r.buffer_event_count) for r in rows]})"
    )


@pytest.mark.asyncio
async def test_buffer_event_attributes_to_active_provider_at_event_time(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """A channel hops providers between polls. Buffer events delivered in
    poll N carry the provider_id resolved in poll N's resolver pass.

    Poll 1: channel served by stream_id=100 → provider 1. Buffer event
    arrives → its row carries ``provider_id=1``.
    Poll 2: channel hopped to stream_id=200 → provider 2. New buffer event
    arrives → its row carries ``provider_id=2``.
    """
    mock_client.get_streams_by_ids = AsyncMock(
        side_effect=[
            [{"id": 100, "m3u_account": 1}],
            [{"id": 200, "m3u_account": 2}],
        ]
    )
    mock_client.get_system_events = AsyncMock(
        side_effect=[
            {
                "events": [_system_event(event_id=3001, channel_id="ch-uuid-1")],
                "count": 1, "total": 1, "offset": 0, "limit": 1000,
            },
            {
                "events": [_system_event(event_id=3002, channel_id="ch-uuid-1")],
                "count": 1, "total": 1, "offset": 0, "limit": 1000,
            },
        ]
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=100,
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=200,
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = (
            session.query(SessionTelemetry)
            .order_by(SessionTelemetry.id)
            .all()
        )
    finally:
        session.close()
    assert len(rows) == 2
    # Poll-1 buffer event → provider 1.
    assert rows[0].provider_id == 1
    assert rows[0].buffer_event_count == 1
    # Poll-2 buffer event → provider 2 (new active stream).
    assert rows[1].provider_id == 2
    assert rows[1].buffer_event_count == 1


@pytest.mark.asyncio
async def test_buffer_event_for_unresolved_channel_writes_with_null_provider(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Channel with no resolvable stream (resolver returns NULL for
    provider). Buffer event still attributes to a row; the row's
    ``provider_id`` is NULL — the count is preserved, the attribution gap
    is honest.
    """
    # No stream_id on channel → resolver returns None.
    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_system_events = AsyncMock(
        side_effect=[
            {"events": [], "count": 0, "total": 0, "offset": 0, "limit": 1000},
            {
                "events": [_system_event(event_id=4001, channel_id="ch-uuid-1")],
                "count": 1, "total": 1, "offset": 0, "limit": 1000,
            },
        ]
    )
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
        rows = (
            session.query(SessionTelemetry)
            .order_by(SessionTelemetry.id)
            .all()
        )
    finally:
        session.close()
    assert len(rows) == 2
    assert rows[1].provider_id is None
    assert rows[1].buffer_event_count == 1


@pytest.mark.asyncio
async def test_no_buffer_events_writes_zero_count(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """A poll with zero buffer events writes the usual session_telemetry
    rows with ``buffer_event_count = 0`` — no buffer-only row is
    synthesized when there's nothing to attribute.
    """
    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_system_events = AsyncMock(
        return_value={"events": [], "count": 0, "total": 0, "offset": 0, "limit": 1000}
    )
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
    assert rows
    assert all(r.buffer_event_count == 0 for r in rows)


@pytest.mark.asyncio
async def test_buffer_event_count_split_across_multi_client_channel(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Channel with multiple clients: buffer events are channel-level, so
    the count attributes to exactly ONE row per (channel, poll) — the
    first-emitted row (sorted by client_ip for determinism). Sibling rows
    carry ``buffer_event_count = 0``.

    Acceptance criterion: ``SUM(buffer_event_count)`` GROUP BY (channel,
    poll) equals the number of distinct events seen for that channel this
    poll. With per-client double-counting suppressed this way, GH-59's
    "buffering events by provider" SUM works without further per-row
    arithmetic.
    """
    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_system_events = AsyncMock(
        side_effect=[
            {"events": [], "count": 0, "total": 0, "offset": 0, "limit": 1000},
            {
                "events": [
                    _system_event(event_id=5001, channel_id="ch-uuid-1"),
                    _system_event(event_id=5002, channel_id="ch-uuid-1"),
                ],
                "count": 2, "total": 2, "offset": 0, "limit": 1000,
            },
        ]
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_count=2,
        client_ips=["10.0.0.2", "10.0.0.1"],
    )
    second = _channel_payload(
        total_bytes=3_000_000,
        client_count=2,
        client_ips=["10.0.0.2", "10.0.0.1"],
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = (
            session.query(SessionTelemetry)
            .order_by(SessionTelemetry.id)
            .all()
        )
    finally:
        session.close()
    # 2 clients × 2 polls = 4 rows.
    assert len(rows) == 4
    # The two poll-2 rows together count exactly 2 events for the channel.
    poll2_observed = max(r.observed_at for r in rows)
    poll2_rows = [r for r in rows if r.observed_at == poll2_observed]
    assert sum(r.buffer_event_count for r in poll2_rows) == 2
    # Exactly one row carries the count; the other(s) are zero.
    nonzero_counts = [r.buffer_event_count for r in poll2_rows if r.buffer_event_count > 0]
    assert len(nonzero_counts) == 1
    assert nonzero_counts[0] == 2


@pytest.mark.asyncio
async def test_resolver_called_once_per_poll_with_buffer_ingest(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """The provider resolver shares its result between the session_telemetry
    write path (skqln.14) and the buffer-event attribution (this bead).
    ``get_streams_by_ids`` is invoked at most ONCE per poll cycle — never
    re-called by the buffer ingest. Regression-guards against duplicated
    Dispatcharr round-trips.
    """
    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[{"id": 100, "m3u_account": 1}]
    )
    mock_client.get_system_events = AsyncMock(
        return_value={
            "events": [_system_event(event_id=6001, channel_id="ch-uuid-1")],
            "count": 1, "total": 1, "offset": 0, "limit": 1000,
        }
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=100,
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=100,
    )

    mock_client.get_channel_stats.return_value = {"channels": [first]}
    await tracker._collect_stats()
    after_first = mock_client.get_streams_by_ids.await_count
    mock_client.get_channel_stats.return_value = {"channels": [second]}
    await tracker._collect_stats()
    after_second = mock_client.get_streams_by_ids.await_count

    # Two polls → exactly two resolver calls, no extras from buffer ingest.
    assert after_first == 1
    assert after_second == 2


@pytest.mark.asyncio
async def test_system_events_fetch_failure_does_not_propagate(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    caplog,
):
    """Dispatcharr's system-events endpoint raises (timeout, 5xx). The
    polling cycle continues; session_telemetry rows are still written with
    ``buffer_event_count = 0``. Failure is logged with the
    ``[STATS_V2]`` prefix so skqln.12 can derive a Prometheus counter.
    """
    import logging as _logging

    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_system_events = AsyncMock(
        side_effect=RuntimeError("simulated 503 from Dispatcharr")
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )

    caplog.clear()
    with caplog.at_level(_logging.WARNING, logger="bandwidth_tracker"):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows, "polling cycle must continue even when system-events fetch fails"
    assert all(r.buffer_event_count == 0 for r in rows)
    assert any(
        "[STATS_V2]" in r.message and "buffer_event" in r.message
        for r in caplog.records
    ), [r.message for r in caplog.records]
