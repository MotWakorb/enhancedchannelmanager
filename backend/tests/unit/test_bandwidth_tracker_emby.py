"""Unit tests for the Emby user-attribution wiring inside
``BandwidthTracker`` (bead ``enhancedchannelmanager-gih6d``, parent epic
``enhancedchannelmanager-2cenq``).

Behavior contract
-----------------
``_collect_stats`` cross-references each active (channel, ip) pair
against the live Emby session list — when Emby is enabled — and
populates ``session_telemetry.emby_user_id`` /
``session_telemetry.emby_user_name`` on the matching rows. Every
behavior in this suite maps to a failure mode the bd-gih6d brief
explicitly mandates:

* Resolver returns an attribution → row has both Emby columns
  populated.
* Resolver returns ``None`` → row has both Emby columns NULL.
* ``settings.emby_enabled`` is False → resolver is NOT called and rows
  have both Emby columns NULL (the cheap settings-gate optimization
  the bead spec explicitly calls out).
* Resolver raises → row still writes with both Emby columns NULL AND a
  single ``[BANDWIDTH] [EMBY]`` WARN log is emitted.
* Multiple consecutive resolver failures inside the rate-limit window
  collapse to exactly ONE WARN (the rate-limit guard).
* End-to-end realism: a Dispatcharr poll returning 1 active session +
  a matching cached Emby session produces 1 telemetry row with the
  resolved Emby attribution.

The tests live in their own module rather than extending
``test_bandwidth_tracker_session_telemetry.py`` because the Emby
wiring is conceptually distinct from the unconditional-write
semantics that suite is the regression guard for — same rationale as
``test_bandwidth_tracker_exclude_users.py``.

Synthetic identities only — ``docs/security/threat_model_stats_v2.md``
§7.7.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bandwidth_tracker
import database
from bandwidth_tracker import BandwidthTracker
from models import SessionTelemetry
from services.emby_resolver import EmbyAttribution


# ---------------------------------------------------------------------------
# Fixtures — mirror the sibling test modules so the suites read alike
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

    The default returns no-op so individual tests can override only
    the call shape they care about.
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
    """A BandwidthTracker wired to the stub client.

    Tracker is not ``start()``ed — tests drive ``_collect_stats``
    directly so they can fix the poll-by-poll sequence without
    sleeping.
    """
    return BandwidthTracker(client=mock_client, poll_interval=10)


@pytest.fixture(autouse=True)
def reset_emby_warn_state():
    """Clear the module-level rate-limit timestamp between tests.

    Without this the first WARN-rate-limit test would poison every
    subsequent rate-limit test by holding the timestamp inside the
    60s window. Production code does not call the reset helper.
    """
    bandwidth_tracker._reset_emby_warn_state_for_tests()
    yield
    bandwidth_tracker._reset_emby_warn_state_for_tests()


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
    surfaces it.

    Defaults to a single-viewer stream on the Emby server IP so
    individual tests only need to override what they care about.
    ``stream_id`` defaults to a non-None value so the provider
    resolver returns a populated ``ProviderResolution`` — the Emby
    resolver call site reads ``resolution.stream_name`` and skips
    when it's missing.
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
    """Stream record as ``DispatcharrClient.get_streams_by_ids`` returns it.

    The provider resolver consumes this to populate
    ``ProviderResolution.stream_name`` — the field the Emby resolver
    matches against the cached Emby session's now-playing name.
    Dispatcharr's ``get_streams_by_ids`` returns the provider id under
    the key ``m3u_account`` (extracted by ``extract_m3u_account_id``);
    the kwarg keeps the test reading naturally while mirroring the
    upstream payload shape.
    """
    return {
        "id": stream_id,
        "name": name,
        "m3u_account": m3u_account_id,
    }


def _enabled_settings(base_url: str = "http://192.168.1.50:8096") -> MagicMock:
    """Settings stub with Emby enabled and an IP-literal base URL.

    The IP-literal base URL means the resolver does not need to mock
    ``socket.gethostbyname`` — the hostname extractor returns the
    literal directly. Hostname-cased tests (none in this suite) would
    override ``base_url`` and patch DNS separately.
    """
    settings = MagicMock()
    settings.emby_enabled = True
    settings.emby_base_url = base_url
    settings.emby_api_key = "key-test"
    return settings


def _disabled_settings() -> MagicMock:
    """Settings stub with Emby explicitly disabled.

    The wiring's settings-gate optimization must skip the resolver
    call entirely on this state — tests assert the resolver mock is
    never called.
    """
    settings = MagicMock()
    settings.emby_enabled = False
    settings.emby_base_url = ""
    settings.emby_api_key = ""
    return settings


async def _drive_two_polls(tracker, mock_client, first_payload, second_payload):
    """Run ``_collect_stats`` twice so the second poll observes an open
    ``_active_connections`` entry — the writer skips rows whose
    ``conn_id`` is missing, so a one-poll-only test would assert
    against an empty ``session_telemetry`` table.
    """
    mock_client.get_channel_stats.return_value = {"channels": [first_payload]}
    await tracker._collect_stats()
    mock_client.get_channel_stats.return_value = {"channels": [second_payload]}
    await tracker._collect_stats()


# ---------------------------------------------------------------------------
# Resolver returns EmbyAttribution → row carries emby columns populated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emby_attribution_populated_on_match(
    patched_session_local,
    tracker,
    mock_client,
):
    """End-to-end happy path: the resolver returns an attribution → the
    written ``session_telemetry`` row has ``emby_user_id`` and
    ``emby_user_name`` populated with the resolved values.
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    attribution = EmbyAttribution(user_id="uid-alice", user_name="alice")
    # ``_resolve_emby_attributions`` imports ``get_settings`` locally
    # via ``from config import get_settings``; patching ``config.get_settings``
    # rebinds the symbol the local import resolves to.
    with patch(
        "bandwidth_tracker.resolve_emby_user",
        AsyncMock(return_value=attribution),
    ), patch("config.get_settings", return_value=_enabled_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert len(rows) == 2, f"expected 2 telemetry rows; got {len(rows)}"
    for row in rows:
        assert row.emby_user_id == "uid-alice"
        assert row.emby_user_name == "alice"


# ---------------------------------------------------------------------------
# Resolver returns None → row carries NULL emby columns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emby_columns_null_when_resolver_returns_none(
    patched_session_local,
    tracker,
    mock_client,
):
    """Resolver returns ``None`` for every (channel, ip) pair (no
    matching Emby session) → the row still writes, with both Emby
    columns NULL. The vast majority of rows on real installs land
    here — most clients are not Emby-mediated.
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    with patch(
        "bandwidth_tracker.resolve_emby_user", AsyncMock(return_value=None)
    ), patch("config.get_settings", return_value=_enabled_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert len(rows) == 2
    for row in rows:
        assert row.emby_user_id is None
        assert row.emby_user_name is None


# ---------------------------------------------------------------------------
# Settings gate — Emby disabled → resolver NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_not_called_when_emby_disabled(
    patched_session_local,
    tracker,
    mock_client,
):
    """``settings.emby_enabled = False`` → the resolver mock MUST NOT be
    awaited. This is the cheap settings-gate optimization the bead
    spec explicitly calls out: skipping the per-(channel, ip) loop and
    the resolver function-call overhead on the common disabled-Emby
    install.
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    resolver_mock = AsyncMock(
        return_value=EmbyAttribution(user_id="uid-x", user_name="x"),
    )
    with patch("bandwidth_tracker.resolve_emby_user", resolver_mock), patch(
        "config.get_settings", return_value=_disabled_settings()
    ):
        await _drive_two_polls(tracker, mock_client, first, second)

    resolver_mock.assert_not_awaited()

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert len(rows) == 2
    for row in rows:
        assert row.emby_user_id is None
        assert row.emby_user_name is None


# ---------------------------------------------------------------------------
# Resolver raises → row writes anyway with NULL emby columns + ONE WARN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_failure_writes_row_with_null_emby_and_warns(
    patched_session_local,
    tracker,
    mock_client,
    caplog,
):
    """Resolver raises ``EmbyClientError`` → the row still writes (Emby
    columns NULL) AND a single ``[BANDWIDTH] [EMBY]`` WARN is emitted.
    Telemetry writes MUST NEVER block on Emby failure — this is the
    hot-path discipline the bead spec mandates.
    """
    from emby_client import EmbyClientError

    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    resolver_mock = AsyncMock(side_effect=EmbyClientError("simulated cache fault"))
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="bandwidth_tracker"), \
         patch("bandwidth_tracker.resolve_emby_user", resolver_mock), \
         patch("config.get_settings", return_value=_enabled_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert len(rows) == 2, "telemetry rows MUST write even when Emby resolver fails"
    for row in rows:
        assert row.emby_user_id is None
        assert row.emby_user_name is None

    warn_lines = [
        r for r in caplog.records
        if "[BANDWIDTH] [EMBY]" in r.message and r.levelno >= logging.WARNING
    ]
    # Resolver was called once per (channel, ip) per poll = 2 calls;
    # the rate-limit guard collapses both to ONE WARN within the 60s
    # window. This protects the operator's log against per-poll spam
    # in a sustained-failure mode.
    assert len(warn_lines) == 1, (
        f"expected exactly one [BANDWIDTH] [EMBY] WARN; got "
        f"{[r.message for r in warn_lines]}"
    )


# ---------------------------------------------------------------------------
# Rate-limit guard — multiple failures inside the window → exactly ONE WARN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_collapses_many_failures_to_one_warn(
    patched_session_local,
    tracker,
    mock_client,
    caplog,
):
    """Five consecutive resolver failures inside the same 60s window
    collapse to exactly ONE ``[BANDWIDTH] [EMBY]`` WARN.

    The wiring calls the resolver once per (channel, ip) per poll. A
    busy poll with multiple channels and clients would otherwise emit
    one WARN per pair — the rate-limit guard exists specifically to
    keep the WARN line a single per-minute operational signal.
    """
    from emby_client import EmbyClientError

    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    # 3 clients on one channel = 3 resolver calls per poll; two polls
    # = 6 total calls. All should raise; only the first should WARN.
    first = _channel_payload(
        total_bytes=1_000_000,
        client_count=3,
        client_ips=["192.168.1.50", "192.168.1.51", "192.168.1.52"],
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_count=3,
        client_ips=["192.168.1.50", "192.168.1.51", "192.168.1.52"],
    )

    resolver_mock = AsyncMock(side_effect=EmbyClientError("simulated"))
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="bandwidth_tracker"), \
         patch("bandwidth_tracker.resolve_emby_user", resolver_mock), \
         patch("config.get_settings", return_value=_enabled_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    # Resolver awaited many times (6 = 3 ips × 2 polls); guard
    # collapses every WARN inside the window to ONE.
    assert resolver_mock.await_count == 6
    warn_lines = [
        r for r in caplog.records
        if "[BANDWIDTH] [EMBY]" in r.message and r.levelno >= logging.WARNING
    ]
    assert len(warn_lines) == 1, (
        f"rate-limit guard should collapse {resolver_mock.await_count} "
        f"failures to 1 WARN; got {len(warn_lines)}"
    )


# ---------------------------------------------------------------------------
# Realistic scenario: Dispatcharr poll + Emby session match → attribution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_realistic_scenario_one_emby_session_one_match(
    patched_session_local,
    tracker,
    mock_client,
):
    """Realistic end-to-end shape:

    * Dispatcharr stats poll returns one channel with one client whose
      IP is the configured Emby server's IP.
    * The Emby cache (mocked at the resolver layer) returns one
      matching session for user "carol".
    * The resulting ``session_telemetry`` row carries
      ``emby_user_id`` / ``emby_user_name`` populated with carol's
      identity AND retains all the Stats v2 fields the non-Emby
      writer already populates (``channel_id``, ``stream_id``,
      ``stream_name``, etc.).

    This is the regression guard for the bead's acceptance
    criterion — every other test in this suite isolates one failure
    mode; this one proves the whole pipeline works end-to-end.
    """
    mock_client.get_streams_by_ids.return_value = [
        _stream_record(stream_id=9001, name="CNN HD")
    ]
    first = _channel_payload(
        total_bytes=1_000_000,
        client_ips=["192.168.1.50"],
        name="CNN HD",
        stream_id=9001,
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_ips=["192.168.1.50"],
        name="CNN HD",
        stream_id=9001,
    )

    attribution = EmbyAttribution(user_id="uid-carol", user_name="carol")
    resolver_mock = AsyncMock(return_value=attribution)
    with patch("bandwidth_tracker.resolve_emby_user", resolver_mock), \
         patch("config.get_settings", return_value=_enabled_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    # Resolver was awaited at least once with the correct ip + stream name.
    assert resolver_mock.await_count >= 1
    call_args = resolver_mock.await_args_list[0].args
    assert call_args == ("192.168.1.50", "CNN HD")

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
    for row in rows:
        assert row.channel_id == "ch-uuid-1"
        assert row.stream_id == 9001
        assert row.stream_name == "CNN HD"
        assert row.emby_user_id == "uid-carol"
        assert row.emby_user_name == "carol"


# ---------------------------------------------------------------------------
# Sparse map semantics — non-matching ip gets NULL, matching ip gets value
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_ip_attribution_sparse_map(
    patched_session_local,
    tracker,
    mock_client,
):
    """Per-(channel, ip) attribution: two clients on one channel, only
    one resolves to an Emby user. The matching client's row gets the
    attribution; the non-matching client's row keeps NULL Emby columns.

    This is the regression guard for the sparse-map lookup contract:
    a stray ``(channel, ip)`` miss in the resolver map must collapse
    to NULL on its row WITHOUT spilling another row's attribution.
    """
    mock_client.get_streams_by_ids.return_value = [_stream_record()]
    first = _channel_payload(
        total_bytes=1_000_000,
        client_count=2,
        client_ips=["192.168.1.50", "10.0.0.5"],
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_count=2,
        client_ips=["192.168.1.50", "10.0.0.5"],
    )

    attribution = EmbyAttribution(user_id="uid-dave", user_name="dave")

    async def resolver_side_effect(ecm_session_ip, ecm_stream_name):
        # Only the Emby-server IP resolves; the other IP returns None
        # (as the real resolver would once its IP-mismatch check fires).
        if ecm_session_ip == "192.168.1.50":
            return attribution
        return None

    with patch(
        "bandwidth_tracker.resolve_emby_user", side_effect=resolver_side_effect
    ), patch("config.get_settings", return_value=_enabled_settings()):
        await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    # 2 clients × 2 polls = 4 rows. We grouped by emby_user_id below
    # rather than by session_id because the (channel, ip) keying is
    # opaque to the test — the assertion is "exactly the Emby-IP rows
    # carry the attribution".
    assert len(rows) == 4
    emby_rows = [r for r in rows if r.emby_user_id == "uid-dave"]
    null_rows = [r for r in rows if r.emby_user_id is None]
    assert len(emby_rows) == 2, (
        f"expected 2 rows attributed to Emby user dave; got {len(emby_rows)}"
    )
    assert len(null_rows) == 2, (
        f"expected 2 rows with NULL Emby attribution; got {len(null_rows)}"
    )
    for row in emby_rows:
        assert row.emby_user_name == "dave"
    for row in null_rows:
        assert row.emby_user_name is None
