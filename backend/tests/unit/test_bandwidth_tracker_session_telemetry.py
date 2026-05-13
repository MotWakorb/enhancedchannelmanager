"""Unit tests for the additive ``session_telemetry`` write inside
``BandwidthTracker`` (bead ``enhancedchannelmanager-skqln.3`` step (a)).

Step (a) goals (mirrored by the test names below):

* Flag OFF: no row is written to ``session_telemetry`` — the feature is
  default-OFF in production. This is the no-op-in-prod guarantee.
* Flag ON: rows appear with the documented column shape (one per active
  client connection per poll, session_id namespaced ``conn-<id>``, etc.).
* Flag ON: the legacy ``ChannelWatchStats`` write still happens — no
  regression in the 4 pre-existing writes (the keystone of "single-write
  refactor that can't break what already works").
* Flag ON, helper raises: the legacy writes have already committed, and
  the new write's exception is swallowed inside ``_write_session_telemetry``
  so it never escapes ``_collect_stats``.

The tests drive ``_collect_stats`` end-to-end through a stubbed
``DispatcharrClient`` so the BandwidthTracker's own per-channel rollup
code paths are exercised (the helper is fed the snapshot the real
production path builds, not a hand-rolled fixture). The session_telemetry
schema (`models.SessionTelemetry`) is created from `Base.metadata` in the
existing in-memory `test_engine` fixture from `tests/conftest.py`.

Synthetic identities only — `docs/security/threat_model_stats_v2.md` §7.7.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import database
from bandwidth_tracker import BandwidthTracker
from models import ChannelWatchStats, SessionTelemetry, User


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
) -> dict:
    """Build a single ``channels[]`` entry as Dispatcharr's stats endpoint
    surfaces it. Defaults match a single-viewer stream."""
    if client_ips is None:
        client_ips = ["10.0.0.1"]
    if client_user_ids is None:
        client_user_ids = {}
    clients = [
        {"ip_address": ip, "user_id": client_user_ids.get(ip)}
        for ip in client_ips
    ]
    return {
        "channel_id": channel_uuid,
        "channel_number": channel_number,
        "channel_name": name,
        "total_bytes": total_bytes,
        "client_count": client_count,
        "avg_bitrate_kbps": avg_bitrate_kbps,
        "clients": clients,
    }


async def _drive_two_polls(tracker, mock_client, first_payload, second_payload):
    """Run ``_collect_stats`` twice so the second poll has a per-channel
    byte delta and the ``_active_connections`` map is populated (the first
    poll opens connections via ``_update_watch_counts``, the second poll
    counts as ``still_active`` and is what the telemetry helper observes).
    """
    mock_client.get_channel_stats.return_value = {"channels": [first_payload]}
    await tracker._collect_stats()
    mock_client.get_channel_stats.return_value = {"channels": [second_payload]}
    await tracker._collect_stats()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flag_off_writes_no_session_telemetry_rows(
    patched_session_local,
    tracker,
    mock_client,
    monkeypatch,
):
    """Flag OFF (default): zero rows in session_telemetry after a poll cycle.

    This is the no-op-in-prod guarantee — the first PR for step (a) must
    not change observable behavior.
    """
    monkeypatch.delenv("ECM_SESSION_TELEMETRY_WRITE_ENABLED", raising=False)
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        count = session.query(SessionTelemetry).count()
    finally:
        session.close()
    assert count == 0


@pytest.mark.asyncio
async def test_flag_on_writes_session_telemetry_row_per_connection(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    monkeypatch,
):
    """Flag ON: one row per active client connection per poll, populated
    with the documented step-(a) column shape.

    Two polls drive the tracker. Poll 1 opens the connection and records
    a presence row (``bytes_delta=0`` — no prior cumulative-bytes value
    means the delta is by definition zero). Poll 2 records a row with
    the actual transferred bytes_delta. Both rows attribute to the same
    ``session_id`` (the connection lives across polls).
    """
    monkeypatch.setenv("ECM_SESSION_TELEMETRY_WRITE_ENABLED", "true")
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
            # Dispatcharr channel UUID (String(64)) — schema corrected by
            # migration 0007. See the bead body for the step-(a) correction.
            assert row.channel_id == "ch-uuid-1"
            # provider_id remains NULL until skqln.14 lands the resolver.
            assert row.provider_id is None
            assert row.buffer_event_count == 0

        # Poll 1: zero per-channel byte delta (first observation),
        # Poll 2: 1_500_000 split equally across one client.
        assert rows[0].bytes_delta == 0
        assert rows[1].bytes_delta == 1_500_000
    finally:
        session.close()


@pytest.mark.asyncio
async def test_flag_on_splits_bytes_delta_equally_across_clients(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
    monkeypatch,
):
    """Flag ON: a single channel with two concurrent clients yields one
    session_telemetry row per client per poll; the channel byte delta is
    split equally (integer floor)."""
    monkeypatch.setenv("ECM_SESSION_TELEMETRY_WRITE_ENABLED", "true")
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
        # Every row carries the Dispatcharr channel UUID — migration 0007
        # made channel_id a NOT NULL String(64). All four rows share the
        # same channel.
        channel_ids = {r.channel_id for r in rows}
        assert channel_ids == {"ch-uuid-1"}
    finally:
        session.close()


@pytest.mark.asyncio
async def test_flag_on_does_not_regress_legacy_channel_watch_stats_write(
    patched_session_local,
    tracker,
    mock_client,
    monkeypatch,
):
    """Flag ON: the legacy ``ChannelWatchStats`` row is still written.

    Guards the "single-write refactor that can't break what already works"
    keystone — step (a) is additive only, the legacy path stays intact.
    """
    monkeypatch.setenv("ECM_SESSION_TELEMETRY_WRITE_ENABLED", "true")
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=1_500_000)

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        watch_stats = session.query(ChannelWatchStats).all()
        assert len(watch_stats) == 1
        assert watch_stats[0].watch_count >= 1
        assert watch_stats[0].total_watch_seconds > 0
    finally:
        session.close()


@pytest.mark.asyncio
async def test_flag_on_helper_internal_failure_is_swallowed(
    patched_session_local,
    tracker,
    mock_client,
    monkeypatch,
    caplog,
):
    """Flag ON: when the helper's internal write path raises, the in-helper
    try/except logs the failure and prevents the exception from escaping
    ``_collect_stats``. The legacy writes (which ran before the helper)
    must still be committed.

    Sabotage point: patch the ``bandwidth_tracker.SessionTelemetry``
    module-level reference so constructing a row inside the helper raises.
    This proves the helper's internal try/except — the keystone of "the
    refactor cannot break what already works" — actually fires.
    """
    import logging as _logging

    monkeypatch.setenv("ECM_SESSION_TELEMETRY_WRITE_ENABLED", "true")
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=1_500_000)

    # Poll 1 runs cleanly (we want the helper to also attempt and fail on
    # this poll so we exercise the rollback path on the very first row).
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

    # Legacy ChannelWatchStats survived both polls.
    session = patched_session_local()
    try:
        watch_stats = session.query(ChannelWatchStats).all()
        assert len(watch_stats) == 1
        assert watch_stats[0].total_watch_seconds > 0
        # Poll 1 (before the patch) wrote one session_telemetry row.
        # Poll 2 (under the patch) raised inside the helper and was
        # swallowed — no second row was committed. So the table reflects
        # only what poll 1 wrote, proving the failure on poll 2 was
        # contained without disturbing earlier durable writes.
        telemetry_rows = session.query(SessionTelemetry).all()
        assert len(telemetry_rows) == 1
    finally:
        session.close()
