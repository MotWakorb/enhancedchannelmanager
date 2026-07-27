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
from bandwidth_tracker import (
    LOCAL_TUNER_PROVIDER_ID,
    LOCAL_TUNER_PROVIDER_NAME,
    BandwidthTracker,
    _coerce_session_user_id,
)
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
    """Stub the Dispatcharr client surface BandwidthTracker calls.

    Default returns are picked so the tracker's per-poll fetches all
    no-op when individual tests don't override them — system_events
    returns an empty events payload (bd-skqln.15) so the buffer ingest
    path is a clean no-op by default.
    """
    client = AsyncMock()
    client.get_channel_stats = AsyncMock(return_value={"channels": []})
    client.get_channels = AsyncMock(return_value={"results": [], "next": None})
    client.get_users = AsyncMock(return_value=[])
    # bd-gy5nd: resolver fetches the M3U account list once per call to
    # support URL-hostname → provider matching. Default to an empty list
    # so tests not interested in this path degrade to "no M3U match"
    # (which surfaces the bare hostname as ``provider_name``). Tests
    # that want to exercise the match path override this on the
    # specific test.
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.get_system_events = AsyncMock(
        return_value={
            "events": [],
            "count": 0,
            "total": 0,
            "offset": 0,
            "limit": 1000,
        }
    )
    # ADR-013 §D2: real settings so telemetry_write_interval resolves to its
    # default (10s) rather than a Mock attribute (bead 312nk.3).
    from config import DispatcharrSettings
    client.settings = DispatcharrSettings()
    return client


@pytest.fixture
def tracker(mock_client, telemetry_clock):
    """A BandwidthTracker wired to the stub client.

    Tracker is not ``start()``ed — tests drive ``_collect_stats`` directly
    so they can fix the poll-by-poll sequence without sleeping.
    """
    return BandwidthTracker(client=mock_client, poll_interval=10, now_fn=telemetry_clock)


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
    url: str | None = None,
) -> dict:
    """Build a single ``channels[]`` entry as Dispatcharr's stats endpoint
    surfaces it. Defaults match a single-viewer stream.

    ``stream_id`` mirrors the per-channel ``stream_id`` field Dispatcharr's
    ``/proxy/ts/status`` payload surfaces — the integer ID of the stream
    currently being served. This is the input the provider resolver (bead
    skqln.14) uses to map the active stream back to its ``m3u_account_id``.

    ``url`` mirrors the per-channel ``url`` field Dispatcharr surfaces in
    the same payload. The trailing path-segment integer before ``.ts`` is
    the Dispatcharr stream row id — the resolver's URL-fallback path
    (bead kbgey) parses it when ``stream_id`` is absent.
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
    if url is not None:
        payload["url"] = url
    return payload


async def _drive_two_polls(tracker, mock_client, first_payload, second_payload):
    """Run ``_collect_stats`` twice so the second poll has a per-channel
    byte delta and the ``_active_connections`` map is populated (the first
    poll opens connections, the second poll counts as ``still_active`` and
    is what the telemetry helper observes).

    enhancedchannelmanager-1qmn0: the provider resolver no longer fetches
    ``/api/m3u/accounts/`` inside ``_collect_stats``; the tracker caches
    the accounts list and refreshes it on a 300s TTL via
    ``_maybe_refresh_m3u_accounts`` (called from ``_poll_loop`` in
    production). Prime that cache here so the resolver sees the configured
    accounts, mirroring the real poll loop.
    """
    await tracker._maybe_refresh_m3u_accounts()
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
        # bd-skqln.12 downgraded this log line from ERROR (exception) to
        # WARN — the helper still swallows the failure, but the bead's
        # observability spec calls for WARN-with-trace_id so SRE's
        # alerting rules differentiate "swallowed observation failure"
        # from "unrecoverable error". Capture at WARN to see it.
        with caplog.at_level(_logging.WARNING, logger="bandwidth_tracker"):
            try:
                await tracker._collect_stats()
            except Exception:  # pragma: no cover — defensive only
                pytest.fail(
                    "_collect_stats must not propagate exceptions from "
                    "_write_session_telemetry"
                )
        # The failure was logged at WARN level with the [STATS_V2] prefix.
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
    """No ``stream_id`` on the channel payload AND no URL — resolver
    cannot identify an active stream. Row is still written with
    ``provider_id=NULL``. bd-gy5nd: the previous
    ``[STATS_V2] provider_resolution_failed`` WARN is gone — the
    legitimate "no stream id, no URL" case is normal data, not an
    error. The SLI summary line still carries the unresolved count.
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
    # bd-gy5nd: WARN-level "provider_resolution_failed" is retired.
    assert not any(
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
    """``get_streams_by_ids`` returns an empty list (Dispatcharr 404'd
    or the stream was deleted). Resolver returns NULL for every channel
    that has no URL-hostname fallback; the row is still written.
    bd-gy5nd: the per-channel ``stream_not_found`` WARN is retired
    (now DEBUG ``provider_unresolved``). The legitimate "stream id
    pointed to a deleted record" case is no longer noisy.
    """
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
    # bd-gy5nd: WARN-level "provider_resolution_failed" is retired
    # for per-channel reasons (stream_not_found / stream_has_no_provider
    # / no_stream_id). Only the bulk lookup_raised path still WARNs
    # (one line per call, not per channel).
    assert not any(
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
    # Second poll: NO additional fetch — ADR-013 §D3 (bead 312nk.4) makes the
    # stream->provider cache process-lived (not per-poll), so the same stream
    # ids (100, 200) are served from cache. ``get_streams_by_ids`` is now rare:
    # it fires on cold start, on genuinely new ids, and right after a
    # stream_rehash / channels_created invalidation.
    assert call_count_after_second == 1

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
async def test_resolver_provider_hop_picked_up_after_invalidation(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """ADR-013 §D3 (bead 312nk.4): the stream->provider cache is process-lived,
    NOT per-poll. A stream's provider hop is therefore NOT picked up
    automatically on the next poll — it is signaled by the WS
    ``stream_rehash`` / ``channels_created`` event (which clears the cache) or,
    on the degraded poll-fallback path, bounded by the safety TTL.

    This replaces the old per-poll-reset contract (the legacy
    ``ChannelStreamsCache`` was per-invocation; the new stream->provider cache
    is the rare-fetch cache the ADR mandates).
    """
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

    # Two polls with NO invalidation between them: the cache serves the
    # poll-1 provider on poll 2 (one fetch total). The provider hop is NOT
    # observed yet — this is the bounded-staleness the ADR accepts.
    await _drive_two_polls(tracker, mock_client, first, second)
    assert mock_client.get_streams_by_ids.await_count == 1

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
    assert rows[1].provider_id == 1  # served from cache — hop not yet observed

    # Now the WS stream_rehash event fires → cache cleared → the NEXT poll
    # re-fetches and observes the new provider (2).
    tracker.invalidate_stream_provider_cache(reason="stream_rehash")
    third = _channel_payload(
        total_bytes=3_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=555,
    )
    mock_client.get_channel_stats.return_value = {"channels": [third]}
    await tracker._collect_stats()
    assert mock_client.get_streams_by_ids.await_count == 2

    session = patched_session_local()
    try:
        latest = (
            session.query(SessionTelemetry)
            .order_by(SessionTelemetry.id.desc())
            .first()
        )
    finally:
        session.close()
    assert latest.provider_id == 2  # hop observed after invalidation


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
async def test_resolver_tags_null_m3u_account_stream_as_local_tuner(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    caplog,
):
    """Stream exists in Dispatcharr but ``m3u_account`` is None — a
    non-M3U / local-tuner source (HDHomeRun, direct tuner, custom
    stream, or an orphaned stream whose provider was deleted).

    bd-oj02b: this is a RESOLVED outcome, not an attribution failure.
    The writer tags such rows with the synthetic local-tuner
    ``provider_id`` (LOCAL_TUNER_PROVIDER_ID) so the provider-stats
    panel surfaces them as their own "HD HomeRun" series instead of
    folding them into the genuinely-unknown NULL "Unknown" bucket.
    (Pre-bd-oj02b these rows wrote ``provider_id=NULL``.)
    bd-gy5nd: no per-channel WARN fires.
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
    assert rows and all(
        r.provider_id == LOCAL_TUNER_PROVIDER_ID for r in rows
    ), [r.provider_id for r in rows]
    # bd-gy5nd: the per-channel WARN is retired.
    assert not any(
        "[STATS_V2] provider_resolution_failed" in r.message
        for r in caplog.records
    ), [r.message for r in caplog.records]


@pytest.mark.asyncio
async def test_resolver_distinguishes_local_tuner_from_genuinely_unknown(
    patched_session_local,
    tracker,
):
    """bd-oj02b: the resolver must give a found-but-no-m3u_account stream
    the synthetic local-tuner identity (provider_id + "HD HomeRun" name),
    but leave a genuinely-not-found stream as the all-NULL resolution.
    Driven directly against ``_resolve_provider_ids`` to assert the
    resolution shape (the writer just persists ``.provider_id``)."""
    # ch-local: stream 700 is in the catalogue with no m3u_account.
    # ch-missing: stream 999 requested but absent from the response.
    tracker.client.get_streams_by_ids = AsyncMock(
        return_value=[{"id": 700, "m3u_account": None, "name": "Local CBS"}]
    )
    snapshot = [
        {"channel_uuid": "ch-local", "stream_id": 700},
        {"channel_uuid": "ch-missing", "stream_id": 999},
    ]

    result = await tracker._resolve_provider_ids(snapshot)

    local = result["ch-local"]
    assert local.provider_id == LOCAL_TUNER_PROVIDER_ID
    assert local.provider_name == LOCAL_TUNER_PROVIDER_NAME
    assert local.stream_id == 700
    assert local.stream_name == "Local CBS"

    missing = result["ch-missing"]
    assert missing.provider_id is None
    assert missing.provider_name is None


# ---------------------------------------------------------------------------
# URL-fallback resolver tests (bd-kbgey)
# ---------------------------------------------------------------------------
#
# Dispatcharr's ``/proxy/ts/status`` payload INCONSISTENTLY populates
# ``stream_id`` per channel — 214 of 235 polls observed in dev surfaced
# the field missing on active connections (see bd-kbgey description for
# real payload evidence). The URL field is reliably present, and its
# trailing path segment before ``.ts`` is the same Dispatcharr stream row
# id that ``stream_id`` would have carried. The resolver derives the id
# from the URL when ``stream_id`` is missing, then routes the result
# through the SAME batched ``get_streams_by_ids`` call — no extra API
# round-trip.


@pytest.mark.asyncio
async def test_resolver_url_fallback_when_stream_id_missing(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """``stream_id`` absent but ``url`` present — resolver parses the
    trailing ``.../<stream_id>.ts`` integer from the URL, feeds it into
    the same batched lookup, and writes the resulting provider_id.

    This is the dev-observed Infinity case: TNT active, ``stream_id``
    missing, URL ``https://infinity.gives/live/mot/16118141/85796.ts``.
    """
    stream_id = 85796
    provider_id = 9
    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[{"id": stream_id, "m3u_account": provider_id}]
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=f"https://infinity.gives/live/mot/16118141/{stream_id}.ts",
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=f"https://infinity.gives/live/mot/16118141/{stream_id}.ts",
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).order_by(SessionTelemetry.id).all()
    finally:
        session.close()
    assert rows, "session_telemetry rows must still be written"
    assert all(r.provider_id == provider_id for r in rows), [
        (r.id, r.provider_id) for r in rows
    ]


@pytest.mark.asyncio
async def test_resolver_url_fallback_malformed_url_falls_through_to_null(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    caplog,
):
    """URL present but missing the trailing ``<n>.ts`` segment — the
    stream-id direct path can't run. bd-gy5nd: the resolver still
    parses the URL hostname for ``provider_name`` enrichment, and
    session_telemetry rows write with ``provider_id=None`` because no
    M3U account is configured. No WARN fires (the legitimate
    "no-stream-id, no-M3U-match" case is normal data, not an error).
    """
    import logging as _logging

    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_m3u_accounts = AsyncMock(return_value=[])
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url="https://example.com/foo/bar",
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url="https://example.com/foo/bar",
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
    # bd-gy5nd: the WARN spam this test used to lock is the primary
    # symptom this bead fixes. The structured failure log is gone.
    assert not any(
        "[STATS_V2] provider_resolution_failed" in r.message
        for r in caplog.records
    ), [r.message for r in caplog.records]


@pytest.mark.asyncio
async def test_resolver_url_fallback_with_query_string(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Dispatcharr URLs occasionally carry a query string after ``.ts``
    (session token, transcode hint). The regex must still extract the
    stream id from ``.../85796.ts?session=abc123``.
    """
    stream_id = 85796
    provider_id = 9
    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[{"id": stream_id, "m3u_account": provider_id}]
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=f"https://infinity.gives/live/mot/16118141/{stream_id}.ts?session=abc123",
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=f"https://infinity.gives/live/mot/16118141/{stream_id}.ts?session=abc123",
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows and all(r.provider_id == provider_id for r in rows)


@pytest.mark.asyncio
async def test_resolver_mixed_batch_url_and_stream_id_share_one_lookup(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Mixed poll: one channel has ``stream_id`` directly, one needs URL
    parsing. Both ids end up in the SAME batched
    ``get_streams_by_ids`` call — the URL-fallback path must not
    multiply the API round-trip count.
    """
    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[
            {"id": 100, "m3u_account": 1},
            {"id": 85796, "m3u_account": 9},
        ]
    )
    ch_a_first = _channel_payload(
        channel_uuid="ch-direct",
        channel_number=101,
        total_bytes=1_000_000,
        client_ips=["10.0.0.1"],
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=100,
    )
    ch_b_first = _channel_payload(
        channel_uuid="ch-url",
        channel_number=102,
        total_bytes=1_000_000,
        client_ips=["10.0.0.2"],
        url="https://infinity.gives/live/mot/16118141/85796.ts",
    )
    ch_a_second = _channel_payload(
        channel_uuid="ch-direct",
        channel_number=101,
        total_bytes=2_000_000,
        client_ips=["10.0.0.1"],
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        stream_id=100,
    )
    ch_b_second = _channel_payload(
        channel_uuid="ch-url",
        channel_number=102,
        total_bytes=2_000_000,
        client_ips=["10.0.0.2"],
        url="https://infinity.gives/live/mot/16118141/85796.ts",
    )

    mock_client.get_channel_stats.return_value = {
        "channels": [ch_a_first, ch_b_first]
    }
    await tracker._collect_stats()
    call_count_after_first = mock_client.get_streams_by_ids.call_count
    mock_client.get_channel_stats.return_value = {
        "channels": [ch_a_second, ch_b_second]
    }
    await tracker._collect_stats()
    call_count_after_second = mock_client.get_streams_by_ids.call_count

    # One call on the FIRST poll regardless of how many channels needed URL
    # parsing (direct + URL-derived ids share the batch). ADR-013 §D3 (bead
    # 312nk.4): the second poll hits the process-lived stream->provider cache
    # for both ids → NO second fetch.
    assert call_count_after_first == 1
    assert call_count_after_second == 1

    # And the single first-poll call covered BOTH stream ids — the set passed
    # to the lookup must contain 100 (direct) and 85796 (URL-derived).
    first_poll_call_args = mock_client.get_streams_by_ids.call_args_list[0]
    ids_arg = first_poll_call_args.args[0]
    assert set(ids_arg) == {100, 85796}, ids_arg

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    provider_by_channel = {(r.channel_id, r.provider_id) for r in rows}
    assert ("ch-direct", 1) in provider_by_channel
    assert ("ch-url", 9) in provider_by_channel


@pytest.mark.asyncio
async def test_resolver_url_derived_stream_not_found_falls_back_to_url_hostname_match(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    caplog,
):
    """When the URL-derived id is not a Dispatcharr stream id (the
    upstream-provider-id case), the resolver falls back to bd-gy5nd's
    URL-hostname match against configured M3U accounts. Retargeted
    from the bd-5g7kx ``channel_streams_no_match`` flow — that path
    called ``get_channel_streams(<channel_uuid>)`` with a
    proxy-session UUID, which Dispatcharr served as the SPA HTML
    rather than 404, generating the 10s WARN spam this bead fixes.
    The replacement: parse the URL hostname and match against
    ``m3u_accounts[*].server_url`` hostnames.

    Pinned here so the kbgey-era ``stream_not_found_url_derived``
    WARNING code doesn't leak back in — operators triaging the Stats
    v2 Providers panel need a single, stable set of failure-reason
    codes.
    """
    import logging as _logging

    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    # M3U account configured with the URL's hostname — bd-gy5nd
    # URL-hostname match resolves to the M3U account id + name.
    mock_client.get_m3u_accounts = AsyncMock(
        return_value=[
            {"id": 17, "name": "Infinity TV", "server_url": "https://infinity.gives/list.m3u"},
        ]
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url="https://infinity.gives/live/mot/16118141/85796.ts",
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url="https://infinity.gives/live/mot/16118141/85796.ts",
    )

    caplog.clear()
    with caplog.at_level(_logging.WARNING, logger="bandwidth_tracker"):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows and all(r.provider_id == 17 for r in rows), [
        (r.id, r.provider_id) for r in rows
    ]
    # The kbgey-era warning code must not appear (preserved
    # invariant — the failure-reason taxonomy must stay stable).
    assert not any(
        "reason=stream_not_found_url_derived" in r.message
        for r in caplog.records
    ), "stream_not_found_url_derived must remain retired"
    # The bd-5g7kx ``channel_streams_no_match`` WARN must not appear
    # — bd-gy5nd removed the path that emitted it and the 10s WARN
    # spam was the primary symptom this bead fixed.
    assert not any(
        "reason=channel_streams_no_match" in r.message
        for r in caplog.records
    ), "channel_streams_no_match WARN should no longer fire under bd-gy5nd"


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
# Channel-streams fallback resolver tests (bd-5g7kx)
# ---------------------------------------------------------------------------
#
# kbgey's URL-fallback parses the trailing ``<id>.ts`` integer from the
# active stream URL and routes it through ``get_streams_by_ids``. But that
# integer is the upstream M3U provider's stream id, NOT Dispatcharr's row
# id — so ``get_streams_by_ids([upstream_id])`` returns nothing on most
# providers. (Coincidental wins happen when the upstream id happens to
# collide with a Dispatcharr id.)
#
# 5g7kx replaces the terminal ``stream_not_found_url_derived`` failure with
# a second-stage fallback: ``GET /channels/<channel_id>/streams`` returns
# the channel's stream list, and the resolver finds the stream whose
# ``url`` matches the active URL — that stream's ``m3u_account_id`` is the
# answer.
#
# Two cache layers:
#   * Per-poll: scoped to a single ``_resolve_provider_ids`` invocation, so
#     multiple unresolved channels with the same channel_uuid don't hit
#     Dispatcharr twice.
#   * Cross-poll LRU on ``BandwidthTracker``: channel stream lists are
#     relatively stable. Capped at 200 entries; TTL is poll-count-based
#     (30 polls = ~5 min at the 10s default poll interval).


@pytest.mark.asyncio
async def test_resolver_url_hostname_match_when_url_derived_id_misses(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """The PO-reproducer (bd-gy5nd): stream_id absent, URL carries the
    upstream-provider id (85796), ``get_streams_by_ids([85796])``
    returns nothing because 85796 is Infinity's id, not Dispatcharr's.
    The bd-gy5nd URL-hostname-match fallback parses the URL's
    ``infinity.gives`` hostname and matches it to the configured M3U
    account's ``server_url`` hostname, writing the matched account's
    id as ``provider_id`` and its name as ``provider_name``.

    Retargeted from the bd-5g7kx ``channel-streams`` fallback test —
    that path called ``GET /channels/<channel_uuid>/streams/`` with a
    proxy-session UUID, which never resolved correctly.
    """
    channel_uuid = "0b433f49-channel"
    active_url = "https://infinity.gives/live/mot/16118141/85796.ts"
    provider_id = 17

    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_m3u_accounts = AsyncMock(
        return_value=[
            {"id": provider_id, "name": "Infinity TV", "server_url": "https://infinity.gives/list.m3u"},
            # A second M3U account whose hostname doesn't match — the
            # resolver must NOT pick it.
            {"id": 99, "name": "Other Provider", "server_url": "https://other.example/list.m3u"},
        ]
    )

    first = _channel_payload(
        channel_uuid=channel_uuid,
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=active_url,
    )
    second = _channel_payload(
        channel_uuid=channel_uuid,
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=active_url,
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).order_by(SessionTelemetry.id).all()
    finally:
        session.close()
    assert rows, "session_telemetry rows must still be written"
    assert all(r.provider_id == provider_id for r in rows), [
        (r.id, r.provider_id) for r in rows
    ]


@pytest.mark.asyncio
async def test_resolver_url_hostname_match_zero_per_channel_http_calls(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """bd-gy5nd contract: the URL-hostname-match fallback issues ZERO
    per-channel HTTP calls — it consults the cached M3U accounts list in
    memory. Replaces the bd-5g7kx per-channel-streams cache test (which
    existed because that fallback did one HTTP call per channel and
    needed cache layers to avoid blowing the Dispatcharr budget).

    enhancedchannelmanager-1qmn0: the accounts list is now refreshed on
    the tracker's 300s TTL (primed once here) rather than fetched inside
    each poll, so two polls within one TTL window issue exactly ONE
    ``get_m3u_accounts`` call regardless of how many channels need the
    hostname match.

    Two channels with different uuids both fall through to the
    hostname-match path; assert ``get_channel_streams`` is never
    called (the broken endpoint) and ``get_m3u_accounts`` is called
    once for the TTL-window refresh.
    """
    channel_uuid_a = "uuid-a"
    channel_uuid_b = "uuid-b"
    active_url = "https://infinity.gives/live/mot/16118141/85796.ts"

    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_channel_streams = AsyncMock(return_value=[])
    mock_client.get_m3u_accounts = AsyncMock(
        return_value=[
            {"id": 17, "name": "Infinity TV", "server_url": "https://infinity.gives/list.m3u"},
        ]
    )

    ch_a_first = _channel_payload(
        channel_uuid=channel_uuid_a,
        channel_number=101,
        total_bytes=1_000_000,
        client_ips=["10.0.0.1"],
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=active_url,
    )
    ch_b_first = _channel_payload(
        channel_uuid=channel_uuid_b,
        channel_number=102,
        total_bytes=1_000_000,
        client_ips=["10.0.0.2"],
        url=active_url,
    )
    ch_a_second = _channel_payload(
        channel_uuid=channel_uuid_a,
        channel_number=101,
        total_bytes=2_000_000,
        client_ips=["10.0.0.1"],
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=active_url,
    )
    ch_b_second = _channel_payload(
        channel_uuid=channel_uuid_b,
        channel_number=102,
        total_bytes=2_000_000,
        client_ips=["10.0.0.2"],
        url=active_url,
    )

    # enhancedchannelmanager-1qmn0: prime the TTL-gated accounts cache
    # (what _poll_loop does in production) so the resolver reads the
    # cached snapshot instead of fetching per poll.
    await tracker._maybe_refresh_m3u_accounts()
    mock_client.get_channel_stats.return_value = {
        "channels": [ch_a_first, ch_b_first]
    }
    await tracker._collect_stats()
    mock_client.get_channel_stats.return_value = {
        "channels": [ch_a_second, ch_b_second]
    }
    await tracker._collect_stats()

    # bd-gy5nd: the broken per-channel endpoint is never called.
    assert mock_client.get_channel_streams.call_count == 0, (
        "get_channel_streams must not be called with proxy-session UUIDs — "
        "the endpoint returns SPA HTML for that identifier shape"
    )
    # enhancedchannelmanager-1qmn0: one M3U accounts fetch for the TTL
    # window (the primed refresh), regardless of poll/channel count.
    assert mock_client.get_m3u_accounts.call_count == 1, (
        "expected one get_m3u_accounts call for the TTL window, got %d"
        % mock_client.get_m3u_accounts.call_count
    )


@pytest.mark.asyncio
async def test_resolver_url_hostname_match_uses_ttl_cached_accounts(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """enhancedchannelmanager-1qmn0: the M3U accounts list is NO LONGER
    fetched inside the resolver on every poll. It is refreshed on the
    tracker's 300s TTL (``_maybe_refresh_m3u_accounts``, primed here once
    via ``_drive_two_polls``) and the resolver reads the cached snapshot.
    Two polls within one TTL window therefore issue exactly ONE
    ``get_m3u_accounts`` call, and the provider still resolves from the
    cache. (Was bd-gy5nd's ``per_poll_fetch``; 1qmn0 inverts the per-poll
    contract to eliminate the ~734KB-per-poll fetch.)
    """
    channel_uuid = "stable-channel"
    active_url = "https://infinity.gives/live/mot/16118141/85796.ts"
    provider_id = 17

    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_m3u_accounts = AsyncMock(
        return_value=[
            {"id": provider_id, "name": "Infinity TV", "server_url": "https://infinity.gives/list.m3u"},
        ]
    )

    first = _channel_payload(
        channel_uuid=channel_uuid,
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=active_url,
    )
    second = _channel_payload(
        channel_uuid=channel_uuid,
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=active_url,
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    # 2 polls within one TTL window = ONE get_m3u_accounts call (the
    # primed refresh). No per-channel HTTP fan-out.
    assert mock_client.get_m3u_accounts.call_count == 1
    assert mock_client.get_channel_streams.call_count == 0

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows and all(r.provider_id == provider_id for r in rows)


@pytest.mark.asyncio
async def test_resolver_url_hostname_no_match_returns_bare_hostname(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    caplog,
):
    """No M3U account hostname matches the active URL's hostname (the
    operator hasn't configured an M3U source for the upstream
    provider). bd-gy5nd contract: the resolver still surfaces the bare
    hostname as ``provider_name`` so the operator sees ``infinity.gives``
    instead of "Unknown". ``provider_id`` stays NULL (no M3U id to
    write); ``session_telemetry.provider_id`` lands NULL — analytics
    consumers continue to treat that as the Unknown bucket.

    Retargeted from the bd-5g7kx ``channel_streams_no_match`` test —
    the WARN spam this generated is the primary symptom bd-gy5nd
    fixes, so the new path no longer emits it.
    """
    import logging as _logging

    channel_uuid = "no-m3u-match"
    active_url = "https://infinity.gives/live/mot/16118141/85796.ts"

    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    # No M3U account configured for ``infinity.gives``.
    mock_client.get_m3u_accounts = AsyncMock(
        return_value=[
            {"id": 1, "name": "Other Provider", "server_url": "https://other.example/list.m3u"},
        ]
    )

    first = _channel_payload(
        channel_uuid=channel_uuid,
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=active_url,
    )
    second = _channel_payload(
        channel_uuid=channel_uuid,
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=active_url,
    )

    caplog.clear()
    with caplog.at_level(_logging.WARNING, logger="bandwidth_tracker"):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    # No M3U match → provider_id stays NULL on session_telemetry rows.
    assert rows and all(r.provider_id is None for r in rows)
    # bd-gy5nd: the 10s WARN spam is gone. The legitimate "no M3U
    # match" case is NOT an error condition — the bare hostname is
    # still a meaningful provider label.
    assert not any(
        "[STATS_V2] provider_resolution_failed" in r.message
        for r in caplog.records
    ), [r.message for r in caplog.records]


@pytest.mark.asyncio
async def test_resolver_url_hostname_match_m3u_accounts_fetch_raises(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    caplog,
):
    """``get_m3u_accounts`` raises (network, 5xx, timeout). bd-gy5nd
    contract: the polling cycle continues, every channel falls back to
    bare-hostname ``provider_name``, session_telemetry rows still
    write — a single Dispatcharr endpoint outage cannot block the
    telemetry path.
    """
    import logging as _logging

    channel_uuid = "fetch-raises"
    active_url = "https://infinity.gives/live/mot/16118141/85796.ts"

    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_m3u_accounts = AsyncMock(
        side_effect=RuntimeError("dispatcharr 503 m3u accounts")
    )

    first = _channel_payload(
        channel_uuid=channel_uuid,
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=active_url,
    )
    second = _channel_payload(
        channel_uuid=channel_uuid,
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=active_url,
    )

    caplog.clear()
    with caplog.at_level(_logging.WARNING, logger="bandwidth_tracker"):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    # Polling cycle continues, rows write with NULL provider_id (no
    # M3U list available to match against).
    assert rows, "polling cycle must continue despite m3u_accounts fetch raise"
    assert all(r.provider_id is None for r in rows)


@pytest.mark.asyncio
async def test_resolver_url_hostname_match_with_query_string(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Active URL carries a session/transcode query suffix
    (``.ts?session=abc``). The URL-hostname match must normalize the
    query off before extracting the hostname, so the match succeeds
    against the configured M3U account's ``server_url``.

    Retargeted from the bd-5g7kx ``channel_streams_url_match_with_
    query_string`` test — the underlying matcher is now URL-hostname
    rather than stream-URL exact-match, but query-string tolerance is
    still load-bearing.
    """
    channel_uuid = "qs-channel"
    active_url = "https://infinity.gives/live/mot/16118141/85796.ts?session=tok123&transcode=h264"
    provider_id = 17

    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_m3u_accounts = AsyncMock(
        return_value=[
            {"id": provider_id, "name": "Infinity TV", "server_url": "https://infinity.gives/list.m3u"},
        ]
    )

    first = _channel_payload(
        channel_uuid=channel_uuid,
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=active_url,
    )
    second = _channel_payload(
        channel_uuid=channel_uuid,
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        url=active_url,
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert rows and all(r.provider_id == provider_id for r in rows)


@pytest.mark.asyncio
async def test_resolver_three_paths_resolve_in_one_poll(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Mixed batch hitting all three resolution paths in one poll:

    * ``ch-direct`` — surfaces ``stream_id`` directly; resolves via
      ``get_streams_by_ids``.
    * ``ch-url`` — no ``stream_id``, URL parses to a Dispatcharr-known
      id; resolves via ``get_streams_by_ids``.
    * ``ch-fallback`` — no ``stream_id``, URL parses to an upstream id
      Dispatcharr doesn't have; resolves via bd-gy5nd URL-hostname
      match against configured M3U accounts.

    All three must produce session_telemetry rows with the correct
    ``provider_id``.
    """
    direct_stream_id = 100
    url_derived_stream_id = 200
    fallback_upstream_id = 85796  # Infinity's id, NOT in Dispatcharr.
    fallback_active_url = (
        "https://infinity.gives/live/mot/16118141/85796.ts"
    )

    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[
            {"id": direct_stream_id, "m3u_account": 1},
            {"id": url_derived_stream_id, "m3u_account": 2},
            # fallback_upstream_id (85796) intentionally absent — that's
            # the whole point of the fallback test.
        ]
    )
    # bd-gy5nd: third path resolves via URL-hostname match against
    # configured M3U accounts (formerly the bd-5g7kx
    # ``get_channel_streams`` URL match, which was broken for
    # proxy-session UUIDs).
    mock_client.get_m3u_accounts = AsyncMock(
        return_value=[
            {"id": 3, "name": "Infinity TV", "server_url": "https://infinity.gives/list.m3u"},
        ]
    )

    def build(ch_uuid, ch_num, total, ip, **kw):
        return _channel_payload(
            channel_uuid=ch_uuid,
            channel_number=ch_num,
            total_bytes=total,
            client_ips=[ip],
            client_user_ids={ip: seed_synthetic_user},
            **kw,
        )

    poll1 = [
        build("ch-direct", 101, 1_000_000, "10.0.0.1", stream_id=direct_stream_id),
        build(
            "ch-url",
            102,
            1_000_000,
            "10.0.0.2",
            url=f"https://provider.example/path/{url_derived_stream_id}.ts",
        ),
        build(
            "ch-fallback",
            103,
            1_000_000,
            "10.0.0.3",
            url=fallback_active_url,
        ),
    ]
    poll2 = [
        build("ch-direct", 101, 2_000_000, "10.0.0.1", stream_id=direct_stream_id),
        build(
            "ch-url",
            102,
            2_000_000,
            "10.0.0.2",
            url=f"https://provider.example/path/{url_derived_stream_id}.ts",
        ),
        build(
            "ch-fallback",
            103,
            2_000_000,
            "10.0.0.3",
            url=fallback_active_url,
        ),
    ]

    # enhancedchannelmanager-1qmn0: prime the TTL-gated accounts cache so
    # the URL-hostname-match path (ch-fallback) reads the configured
    # accounts from the cache instead of a per-poll fetch.
    await tracker._maybe_refresh_m3u_accounts()
    mock_client.get_channel_stats.return_value = {"channels": poll1}
    await tracker._collect_stats()
    mock_client.get_channel_stats.return_value = {"channels": poll2}
    await tracker._collect_stats()

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    provider_by_channel = {(r.channel_id, r.provider_id) for r in rows}
    assert ("ch-direct", 1) in provider_by_channel, provider_by_channel
    assert ("ch-url", 2) in provider_by_channel, provider_by_channel
    assert ("ch-fallback", 3) in provider_by_channel, provider_by_channel


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
    event_type: str = "channel_buffering",
    ip_address: str | None = None,
    timestamp: str = "2026-05-13T15:00:00Z",
) -> dict:
    """Build a single system-event payload as Dispatcharr's
    ``/api/core/system-events/`` endpoint surfaces it.

    ``event_id`` is the dedup key — Dispatcharr assigns a monotonically
    increasing integer ``id`` to every event. ``channel_id`` is normalized
    to string at the ingest site so both Dispatcharr's numeric-id channels
    and ECM's UUID-string channels are matched.

    Default ``event_type`` is ``channel_buffering`` because (a) Dispatcharr's
    upstream ts_proxy emits the raw event_type ``channel_buffering`` (not
    the abbreviation ``buffering`` that the pre-bd-ov5vb API-side filter
    was passing) and (b) preserving "buffering" semantics keeps the
    skqln.15-era buffer-event tests reading naturally — their assertions
    on ``buffer_event_count`` still hold because the new ingest helper
    buckets ``channel_buffering`` into that column.
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

    # First poll → exactly ONE resolver call, no extras from buffer ingest
    # (the buffer ingest shares the resolver's result, never re-fetches).
    assert after_first == 1
    # Second poll → still one call total: ADR-013 §D3 (bead 312nk.4) serves the
    # same stream_id from the process-lived cache. The buffer ingest still adds
    # no extra round-trip.
    assert after_second == 1


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
    # bd-ov5vb: helper renamed and log prefix updated from
    # ``buffer_event_*`` → ``channel_event_*`` to reflect the broadened
    # ingest (channel_buffering + channel_reconnect + channel_error +
    # stream_switch). The [STATS_V2] prefix is preserved.
    assert any(
        "[STATS_V2]" in r.message and "channel_event" in r.message
        for r in caplog.records
    ), [r.message for r in caplog.records]


# ---------------------------------------------------------------------------
# Broadened channel-event ingest (bd-ov5vb)
# ---------------------------------------------------------------------------
#
# bd-ov5vb broadens the pre-existing buffer-only ingest to cover the four
# Dispatcharr ts_proxy event_types that actually represent channel-health
# problems on real installs: channel_buffering (rare, ffmpeg-speed
# threshold), channel_reconnect (operator-asked-for), channel_error
# (operator-asked-for), stream_switch (provider-failover indicator).
#
# The pre-bd-ov5vb helper passed event_type=buffering as an API-side
# filter. Live verification on the PO's instance found that filter was
# returning zero on every poll because Dispatcharr's channel_buffering
# event is rare on real installs — the operationally-meaningful events
# (reconnect/error/switch) were being dropped at the API call. The
# broadened helper drops the API filter, buckets client-side by
# event_type, and writes each bucket to its own per-poll column
# (migration 0013).


@pytest.mark.asyncio
async def test_collect_channel_events_buckets_mixed_event_types_by_type(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """bd-ov5vb happy path: a single poll surfaces a mixed bag of
    channel_buffering, channel_reconnect, channel_error, and stream_switch
    events. Each lands on its own per-type counter on the session_telemetry
    row; one row per active connection per channel; first-row-wins
    attribution holds across every column.
    """
    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_system_events = AsyncMock(
        side_effect=[
            # Poll 1: empty system-events (no events to seed dedup).
            {"events": [], "count": 0, "total": 0, "offset": 0, "limit": 1000},
            # Poll 2: 1 of each type for ch-uuid-1.
            {
                "events": [
                    _system_event(event_id=7001, channel_id="ch-uuid-1", event_type="channel_buffering"),
                    _system_event(event_id=7002, channel_id="ch-uuid-1", event_type="channel_reconnect"),
                    _system_event(event_id=7003, channel_id="ch-uuid-1", event_type="channel_error"),
                    _system_event(event_id=7004, channel_id="ch-uuid-1", event_type="stream_switch"),
                ],
                "count": 4, "total": 4, "offset": 0, "limit": 1000,
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
    # Poll 1: zeros across the board (no events).
    assert rows[0].buffer_event_count == 0
    assert rows[0].reconnect_event_count == 0
    assert rows[0].error_event_count == 0
    assert rows[0].switch_event_count == 0
    # Poll 2: one event per type, all attributed to the single row.
    assert rows[1].buffer_event_count == 1
    assert rows[1].reconnect_event_count == 1
    assert rows[1].error_event_count == 1
    assert rows[1].switch_event_count == 1


@pytest.mark.asyncio
async def test_collect_channel_events_dedups_across_event_types_globally(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """bd-ov5vb: the dedup LRU is keyed on Dispatcharr's monotonic
    event.id, NOT on (id, event_type). Re-delivery of the same event id
    across polls counts ONCE total regardless of which type bucket it
    falls into — because the same event.id can never carry two different
    event_types in practice (Dispatcharr assigns the id at emission
    time), the global keying is the correct dedup semantics.

    Poll 1: id=8001 (reconnect) + id=8002 (error). Poll 2: re-delivers
    8001 + 8002 (must dedup) plus new 8003 (switch). Totals:
    reconnect=1, error=1, switch=1, buffer=0.
    """
    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_system_events = AsyncMock(
        side_effect=[
            {
                "events": [
                    _system_event(event_id=8001, channel_id="ch-uuid-1", event_type="channel_reconnect"),
                    _system_event(event_id=8002, channel_id="ch-uuid-1", event_type="channel_error"),
                ],
                "count": 2, "total": 2, "offset": 0, "limit": 1000,
            },
            {
                "events": [
                    _system_event(event_id=8001, channel_id="ch-uuid-1", event_type="channel_reconnect"),
                    _system_event(event_id=8002, channel_id="ch-uuid-1", event_type="channel_error"),
                    _system_event(event_id=8003, channel_id="ch-uuid-1", event_type="stream_switch"),
                ],
                "count": 3, "total": 3, "offset": 0, "limit": 1000,
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
    # Poll 1: id=8001 (reconnect) + id=8002 (error) attribute to the row.
    assert rows[0].reconnect_event_count == 1
    assert rows[0].error_event_count == 1
    assert rows[0].switch_event_count == 0
    # Poll 2: 8001/8002 are deduped (already seen); only 8003 (switch) lands.
    assert rows[1].reconnect_event_count == 0
    assert rows[1].error_event_count == 0
    assert rows[1].switch_event_count == 1
    # Buffer counter is untouched in this scenario.
    assert all(r.buffer_event_count == 0 for r in rows)


@pytest.mark.asyncio
async def test_collect_channel_events_drops_noise_event_types_before_lru(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """bd-ov5vb: noise event types (login_success, client_connect,
    epg_refresh, m3u_refresh, channel_start/stop) are dropped at the
    type-filter BEFORE the LRU is consulted — they neither pollute the
    bounded dedup set nor surface as attributed counts.

    The acceptance criterion is twofold:

    1. Per-type counts on the resulting session_telemetry row reflect
       ONLY the four tracked types; noise events are zero.
    2. The dedup LRU does not grow by the number of noise events — the
       cap is bounded and SRE's working-set discipline relies on the
       set tracking only the four health types.
    """
    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_system_events = AsyncMock(
        return_value={
            "events": [
                _system_event(event_id=9001, channel_id="ch-uuid-1", event_type="login_success"),
                _system_event(event_id=9002, channel_id="ch-uuid-1", event_type="client_connect"),
                _system_event(event_id=9003, channel_id="ch-uuid-1", event_type="client_disconnect"),
                _system_event(event_id=9004, channel_id="ch-uuid-1", event_type="epg_refresh"),
                _system_event(event_id=9005, channel_id="ch-uuid-1", event_type="m3u_refresh"),
                _system_event(event_id=9006, channel_id="ch-uuid-1", event_type="channel_start"),
                _system_event(event_id=9007, channel_id="ch-uuid-1", event_type="channel_stop"),
                # One real event mixed in so the test row is non-trivial.
                _system_event(event_id=9099, channel_id="ch-uuid-1", event_type="channel_reconnect"),
            ],
            "count": 8, "total": 8, "offset": 0, "limit": 1000,
        }
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )

    mock_client.get_channel_stats.return_value = {"channels": [first]}
    # Snapshot the LRU size BEFORE the poll so we can prove the noise
    # events did not pollute it.
    lru_size_before = len(tracker._seen_buffer_event_ids)
    await tracker._collect_stats()
    lru_size_after = len(tracker._seen_buffer_event_ids)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert len(rows) == 1
    # Only the reconnect event surfaced as a count.
    assert rows[0].reconnect_event_count == 1
    assert rows[0].buffer_event_count == 0
    assert rows[0].error_event_count == 0
    assert rows[0].switch_event_count == 0
    # The LRU grew by exactly 1 (the reconnect event), not by the 7
    # noise events that were filtered before the LRU was touched.
    assert lru_size_after - lru_size_before == 1, (
        f"LRU should grow by 1 (reconnect only); grew by "
        f"{lru_size_after - lru_size_before} — noise events leaked past "
        f"the type-filter."
    )


@pytest.mark.asyncio
async def test_collect_channel_events_sli_log_carries_per_type_breakdown(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    caplog,
):
    """bd-ov5vb: the per-poll SLI line is emitted at INFO with stable
    substring shape ``[STATS_V2] channel_event_ingest fetched=X
    deduped=Y attributed_buffer=A attributed_reconnect=B
    attributed_error=C attributed_switch=D``. SRE's log-derived
    counter parses on this shape; the pre-bd-ov5vb shape
    ``buffer_event_ingest fetched=X deduped=Y attributed=Z`` is no
    longer emitted.
    """
    import logging as _logging

    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_system_events = AsyncMock(
        return_value={
            "events": [
                _system_event(event_id=10001, channel_id="ch-uuid-1", event_type="channel_buffering"),
                _system_event(event_id=10002, channel_id="ch-uuid-1", event_type="channel_reconnect"),
                _system_event(event_id=10003, channel_id="ch-uuid-1", event_type="channel_reconnect"),
                _system_event(event_id=10004, channel_id="ch-uuid-1", event_type="channel_error"),
            ],
            "count": 4, "total": 4, "offset": 0, "limit": 1000,
        }
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )

    mock_client.get_channel_stats.return_value = {"channels": [first]}
    caplog.clear()
    with caplog.at_level(_logging.INFO, logger="bandwidth_tracker"):
        await tracker._collect_stats()

    sli_lines = [
        r.message for r in caplog.records
        if "[STATS_V2] channel_event_ingest" in r.message
    ]
    assert len(sli_lines) == 1, (
        f"Expected exactly one channel_event_ingest SLI line; got "
        f"{len(sli_lines)}: {sli_lines!r}"
    )
    line = sli_lines[0]
    assert "fetched=4" in line
    assert "deduped=0" in line
    assert "attributed_buffer=1" in line
    assert "attributed_reconnect=2" in line
    assert "attributed_error=1" in line
    assert "attributed_switch=0" in line
    # Pre-bd-ov5vb shape must not be emitted concurrently.
    legacy = [
        r.message for r in caplog.records
        if "buffer_event_ingest" in r.message
    ]
    assert legacy == [], (
        f"Legacy buffer_event_ingest SLI line should be retired; saw {legacy!r}"
    )


@pytest.mark.asyncio
async def test_collect_channel_events_no_event_type_filter_passed_to_dispatcharr(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """bd-ov5vb regression guard: the helper must call
    ``get_system_events`` WITHOUT an ``event_type`` kwarg. Pre-bd-ov5vb
    the helper passed ``event_type='buffering'`` which silently filtered
    out the operationally-meaningful events at the API call. If a future
    refactor accidentally re-adds the filter, this test pins the gap
    shut.
    """
    mock_client.get_streams_by_ids = AsyncMock(return_value=[])
    mock_client.get_system_events = AsyncMock(
        return_value={"events": [], "count": 0, "total": 0, "offset": 0, "limit": 1000}
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )

    mock_client.get_channel_stats.return_value = {"channels": [first]}
    await tracker._collect_stats()

    # At least one call landed during the poll cycle.
    assert mock_client.get_system_events.await_count >= 1
    # Every call must NOT pass event_type (the load-bearing change).
    for call in mock_client.get_system_events.await_args_list:
        assert "event_type" not in call.kwargs, (
            f"get_system_events called with event_type={call.kwargs.get('event_type')!r} — "
            f"bd-ov5vb removed the API-side filter; the broadened ingest "
            f"buckets client-side instead."
        )


# ---------------------------------------------------------------------------
# bd-gbxmj — user_id coercion + batch-commit FK safety net
# ---------------------------------------------------------------------------

class TestCoerceSessionUserId:
    """Unit tests for ``_coerce_session_user_id`` (bead gbxmj).

    The helper is the primary fix for the anonymous-viewer FK violation
    that poisoned ``session_telemetry`` batches on dev. Schema contract:
    ``session_telemetry.user_id`` is ``INTEGER`` with an FK to
    ``users.id`` (ON DELETE SET NULL). Anonymous viewers come through
    as ``0`` / ``"0"`` from Dispatcharr — neither value points at a real
    ``users`` row, so without coercion the FK fires at commit() and
    rolls back the whole batch.
    """

    def test_none_returns_none(self):
        """``None`` input → ``None`` output (Dispatcharr surfaced no user id)."""
        assert _coerce_session_user_id(None) is None

    def test_empty_string_returns_none(self):
        """Empty string is the same anonymous sentinel as ``None``."""
        assert _coerce_session_user_id("") is None

    def test_zero_int_returns_none(self):
        """Anonymous-viewer sentinel ``0`` → ``None`` (FK-safe NULL write)."""
        assert _coerce_session_user_id(0) is None

    def test_zero_string_returns_none(self):
        """Anonymous-viewer sentinel ``"0"`` → ``None`` (FK-safe NULL write).

        This is the exact value Dispatcharr returned on dev that surfaced
        the bug — the JSON payload had ``user_id`` as a string.
        """
        assert _coerce_session_user_id("0") is None

    def test_positive_int_passes_through(self):
        """A real positive ``int`` is returned verbatim."""
        assert _coerce_session_user_id(42) == 42

    def test_positive_int_string_is_parsed(self):
        """A string that ``int()`` parses cleanly → that int."""
        assert _coerce_session_user_id("42") == 42

    def test_garbage_string_returns_none(self):
        """Unparseable junk → ``None`` (FK-safe NULL write, no raise)."""
        assert _coerce_session_user_id("abc") is None

    def test_negative_int_returns_none(self):
        """Negative ids are not valid ``users.id`` values → ``None``."""
        assert _coerce_session_user_id(-1) is None

    def test_negative_int_string_returns_none(self):
        """Negative-int strings also coerce to ``None``."""
        assert _coerce_session_user_id("-7") is None

    def test_float_returns_none_strict(self):
        """Floats coerce to ``None`` — strict choice (documented in
        the helper docstring). Dispatcharr does not send floats; silently
        truncating ``42.0`` would mask an upstream payload-shape bug.
        """
        assert _coerce_session_user_id(42.0) is None

    def test_float_string_returns_none(self):
        """``"42.0"`` is not a clean ``int()`` parse → ``None``."""
        assert _coerce_session_user_id("42.0") is None

    def test_bool_true_returns_none(self):
        """Booleans are a subclass of ``int`` in Python; ``True`` would
        silently coerce to user_id=1. Reject explicitly so a stray
        boolean cannot attribute telemetry to "user 1".
        """
        assert _coerce_session_user_id(True) is None

    def test_bool_false_returns_none(self):
        """``False == 0`` would already filter, but assert explicitly so
        the contract is unambiguous if the falsy short-circuit ever
        moves around.
        """
        assert _coerce_session_user_id(False) is None


@pytest.mark.asyncio
async def test_anonymous_user_id_zero_string_writes_null_not_fk_violation(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Regression — bd-gbxmj.

    The actual symptom the PO hit on dev: a batch with one
    anonymous-viewer row (``user_id="0"`` from Dispatcharr) and one
    real-user row (``user_id=42``) wrote ZERO rows because the
    anonymous row's FK violation rolled back the whole transaction.

    With the helper in place, the anonymous row writes ``user_id=NULL``
    and the real-user row writes ``user_id=42`` — both land.
    """
    first = _channel_payload(
        client_count=2,
        client_ips=["10.0.0.1", "10.0.0.2"],
        client_user_ids={
            "10.0.0.1": "0",                  # anonymous sentinel
            "10.0.0.2": seed_synthetic_user,  # real user_id=42
        },
        total_bytes=1_000_000,
    )
    second = _channel_payload(
        client_count=2,
        client_ips=["10.0.0.1", "10.0.0.2"],
        client_user_ids={
            "10.0.0.1": "0",
            "10.0.0.2": seed_synthetic_user,
        },
        total_bytes=2_000_000,
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

    # 2 clients × 2 polls = 4 rows. Pre-fix this was 0 because the
    # anonymous row's FK violation poisoned the batch.
    assert len(rows) == 4, (
        "Anonymous user_id='0' must NOT poison the batch — every row "
        f"in the poll cycle should land. Got: {len(rows)}"
    )
    # Anonymous rows write user_id=NULL; real-user rows write 42. The
    # split is by client_ip so we look at user_id per row.
    user_ids = {r.user_id for r in rows}
    assert user_ids == {None, seed_synthetic_user}, (
        "Expected a mix of NULL (anonymous) and the seeded user id. "
        f"Got: {user_ids}"
    )


@pytest.mark.asyncio
async def test_anonymous_user_id_zero_int_writes_null(
    patched_session_local,
    tracker,
    mock_client,
):
    """``user_id`` returned as integer ``0`` (vs. string ``"0"``) also
    coerces to NULL. ``_collect_stats``'s upstream filter at
    ``if ip and uid:`` already drops ``uid=0`` before it reaches the
    telemetry helper, but the helper-level coercion is the canonical
    defense and must be exercised end-to-end so a future refactor of
    that upstream filter does not silently re-introduce the bug.
    """
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": 0},  # int sentinel
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": 0},
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    # Both poll rows land; both with user_id=NULL.
    assert len(rows) == 2
    assert all(r.user_id is None for r in rows)


def test_write_session_telemetry_user_id_no_longer_constrained_post_gsn3r(
    patched_session_local,
    tracker,
):
    """bd-gsn3r REVERSAL of the bd-gbxmj FK-violation scenario.

    Migration 0011 dropped the FK from ``session_telemetry.user_id`` to
    ``users.id`` (ECM and Dispatcharr ``users`` are different namespaces;
    the FK was a structural lie). The bd-gbxmj defense-in-depth catch
    in ``_write_session_telemetry`` for ``IntegrityError`` at commit
    time stays in the writer — it's a future-proof safety net for any
    field that might grow a constraint later — but ``user_id`` itself
    can no longer trip it because there is no longer an FK to violate.

    This test locks in the new contract: an arbitrary integer that
    previously would have failed FK validation now lands cleanly. The
    coercion helper still scrubs the anonymous ``0`` sentinel
    (``_coerce_session_user_id`` returns ``None`` for it), so analytics
    queries see ``NULL`` rather than the noise value — that behavior
    is exercised by ``TestCoerceSessionUserId`` above.
    """
    import bandwidth_tracker as bt_module

    tracker._active_connections[("ch-uuid-1", "10.0.0.1")] = 12345

    channel_snapshot = [
        {
            "channel_uuid": "ch-uuid-1",
            "client_ips": ["10.0.0.1"],
            "client_user_map": {"10.0.0.1": 999_999},  # opaque viewer id
            "channel_bytes_delta": 1_000_000,
        }
    ]

    # Bypass coercion (which would have NULL'd this if it failed
    # parsing) so the raw integer reaches the row verbatim — that's the
    # surface the FK used to guard against. Post-bd-gsn3r the write
    # succeeds.
    with patch.object(
        bt_module,
        "_coerce_session_user_id",
        lambda raw: raw,
    ):
        tracker._write_session_telemetry(
            channel_snapshot=channel_snapshot,
            observed_at_ms=1_700_000_000_000,
        )

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert len(rows) == 1, (
        "Post-bd-gsn3r the user_id has no FK constraint — the write "
        "must land cleanly even with a Dispatcharr-side viewer id that "
        "would never have matched any ECM users.id row."
    )
    assert rows[0].user_id == 999_999


# ---------------------------------------------------------------------------
# Stream identity capture (bd-kh23e)
# ---------------------------------------------------------------------------
#
# bd-kh23e extends the resolver's batch-lookup output to ALSO surface the
# stream's ``name`` (from the same ``get_streams_by_ids`` response) and to
# persist both ``stream_id`` and ``stream_name`` on every
# ``session_telemetry`` row. The display format ratified by the PO on
# 2026-05-14 is ``[<provider>] - <stream_name>`` — the provider name
# side-loads on the frontend, so the backend writes raw identity only.
#
# Failure-mode parity with provider_id: every condition that produces
# ``provider_id=NULL`` also produces ``stream_id=NULL`` and
# ``stream_name=NULL`` on the same row. The resolver returns a
# ``ProviderResolution(provider_id, stream_id, stream_name)`` NamedTuple
# per channel — a resolution failure is the ``(None, None, None)`` triple.


@pytest.mark.asyncio
async def test_resolver_returns_namedtuple_with_stream_identity(
    patched_session_local,
    tracker,
    mock_client,
):
    """The resolver's per-channel value is a NamedTuple carrying
    ``provider_id``, ``stream_id``, and ``stream_name``. The stream id /
    name come from the same batched ``get_streams_by_ids`` response that
    already powers provider attribution — zero extra round-trips."""
    from bandwidth_tracker import ProviderResolution

    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[
            {"id": 555, "m3u_account": 7, "name": "US: TNT"},
        ]
    )
    snapshot = [{"channel_uuid": "ch-a", "stream_id": 555}]

    result = await tracker._resolve_provider_ids(snapshot)

    assert "ch-a" in result
    resolution = result["ch-a"]
    assert isinstance(resolution, ProviderResolution)
    assert resolution.provider_id == 7
    assert resolution.stream_id == 555
    assert resolution.stream_name == "US: TNT"


@pytest.mark.asyncio
async def test_resolver_returns_null_triple_for_no_stream_id(
    patched_session_local,
    tracker,
    mock_client,
):
    """No ``stream_id`` on the snapshot → resolver returns the all-None
    triple. The row will write with all three columns NULL — same failure
    semantic as the pre-kh23e provider-only path."""
    from bandwidth_tracker import ProviderResolution

    snapshot = [{"channel_uuid": "ch-a", "stream_id": None}]

    result = await tracker._resolve_provider_ids(snapshot)

    resolution = result["ch-a"]
    assert isinstance(resolution, ProviderResolution)
    assert resolution.provider_id is None
    assert resolution.stream_id is None
    assert resolution.stream_name is None


@pytest.mark.asyncio
async def test_write_path_persists_stream_id_and_stream_name(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """End-to-end: a resolver hit lands the stream id + name on every
    ``session_telemetry`` row for that channel."""
    stream_id = 555
    stream_name = "US: TNT"
    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[
            {"id": stream_id, "m3u_account": 7, "name": stream_name},
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
        rows = session.query(SessionTelemetry).order_by(SessionTelemetry.id).all()
    finally:
        session.close()
    assert len(rows) == 2
    assert all(r.stream_id == stream_id for r in rows)
    assert all(r.stream_name == stream_name for r in rows)
    # provider_id keeps working too — kh23e is additive.
    assert all(r.provider_id == 7 for r in rows)


@pytest.mark.asyncio
async def test_write_path_null_stream_when_resolver_fails(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """When the resolver returns the all-None triple (no stream_id on the
    payload), every row writes ``stream_id=NULL`` and ``stream_name=NULL``
    — matching the provider_id failure semantic."""
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
        # No stream_id — resolver returns the (None, None, None) triple.
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
    assert all(r.provider_id is None for r in rows)
    assert all(r.stream_id is None for r in rows)
    assert all(r.stream_name is None for r in rows)


@pytest.mark.asyncio
async def test_write_path_stream_id_without_name_when_stream_response_lacks_name(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Defensive: Dispatcharr's stream record returns the id and provider
    but omits ``name``. The resolver propagates ``stream_id`` and falls
    back to ``stream_name=NULL`` rather than synthesising a label —
    presentation is the frontend's job."""
    stream_id = 555
    mock_client.get_streams_by_ids = AsyncMock(
        return_value=[
            # No ``name`` field — older Dispatcharr versions or partial responses.
            {"id": stream_id, "m3u_account": 7},
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
    assert rows
    assert all(r.stream_id == stream_id for r in rows)
    assert all(r.stream_name is None for r in rows)
    assert all(r.provider_id == 7 for r in rows)


# ---------------------------------------------------------------------------
# dispatcharr_username write path (bd-gsn3r / bd-qgafp)
# ---------------------------------------------------------------------------
#
# The per-poll ``get_users()`` call populates a ``dispatcharr_user_map``
# (str(user_id) → username) that ``_write_session_telemetry`` uses to stamp
# ``dispatcharr_username`` at write time.  Existing tests hold
# ``client.get_users = AsyncMock(return_value=[])`` so the map is always
# empty and the column always writes NULL.  These tests exercise the two
# non-trivial paths:
#
# 1. Non-empty map → username lands in ``dispatcharr_username``.
# 2. Empty-string username coerced to NULL (``... or None`` at the
#    call site in bandwidth_tracker).


@pytest.mark.asyncio
async def test_dispatcharr_username_populated_from_user_map(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """When ``get_users()`` returns a non-empty list that includes the
    viewer's Dispatcharr user_id, ``_write_session_telemetry`` writes the
    corresponding username into ``session_telemetry.dispatcharr_username``.

    Setup mirrors the bead spec:
    - ``mock_client.get_users`` returns ``[{'id': 42, 'username': 'alice'}]``
    - Channel payload carries ``client_user_ids={'10.0.0.1': 42}``
    - Asserts ``rows[0].dispatcharr_username == 'alice'``

    ``seed_synthetic_user`` inserts ECM User(id=42) so the FK constraint on
    ``session_telemetry.user_id`` is satisfied.
    """
    mock_client.get_users = AsyncMock(
        return_value=[{"id": 42, "username": "alice"}]
    )
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},  # seed_synthetic_user == 42
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

    assert len(rows) == 2, f"expected 2 rows, got {len(rows)}"
    assert rows[0].dispatcharr_username == "alice", (
        f"expected 'alice', got {rows[0].dispatcharr_username!r}"
    )
    assert rows[1].dispatcharr_username == "alice"


@pytest.mark.asyncio
async def test_dispatcharr_username_empty_string_coerced_to_null(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """When ``get_users()`` returns a user whose ``username`` is an empty
    string, the ``... or None`` coercion in ``_write_session_telemetry``
    must land the column as NULL — not as an empty string.

    Empty-string usernames are valid Dispatcharr API responses for
    partially-configured accounts; writing ``''`` to the column would break
    the UI's "Unknown viewer" fallback which gates on ``IS NULL``.
    """
    mock_client.get_users = AsyncMock(
        return_value=[{"id": 42, "username": ""}]
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

    assert len(rows) == 2, f"expected 2 rows, got {len(rows)}"
    assert rows[0].dispatcharr_username is None, (
        f"expected NULL (empty string coerced), "
        f"got {rows[0].dispatcharr_username!r}"
    )
    assert rows[1].dispatcharr_username is None
