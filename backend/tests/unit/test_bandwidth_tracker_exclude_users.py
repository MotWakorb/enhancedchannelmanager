"""Unit tests for the operator-facing ``ECM_TELEMETRY_EXCLUDE_USERS``
filter inside ``BandwidthTracker._write_session_telemetry`` (bead
``enhancedchannelmanager-uqbob``).

Motivation
----------
ECM's local ``users`` table and Dispatcharr's ``users`` table are
separate namespaces whose integer IDs sometimes collide. The Stats >
User Watch Time panel joins ``session_telemetry.user_id`` against the
ECM ``users`` table — so a Dispatcharr-side user_id of ``3`` (a real
human viewer like "kmfelmer" on one operator's rig) renders as the
LOCAL user with the same id (``"claude"`` — the ECM admin/API account
in that operator's database). The result: a human user does not appear
to exist in the panel, and the ECM admin account inexplicably shows up
as one of the busiest viewers.

The fix is an operator-facing filter, not a schema change — Dispatcharr
ids are stable enough that asking the operator to enumerate the
admin/API username (or its FK-coerced numeric id) gives the right
escape hatch without forcing a multi-namespace user model on every
operator.

Behavior contract
-----------------
* Env var ``ECM_TELEMETRY_EXCLUDE_USERS`` is a comma-separated token
  list. Each token is matched against BOTH the Dispatcharr-side
  ``user_id`` (string-coerced via the FK helper) AND the resolved
  Dispatcharr username (case-insensitive). EITHER axis matching drops
  the row.
* Empty / unset env var = no filtering. Default-OFF posture preserved
  from the pre-uqbob behavior.
* Skipped rows DO increment a Prometheus counter
  (``session_telemetry_rows_excluded_total`` labeled
  ``reason=excluded_user``). The brief explicitly forbids silent
  zeroing — honest accounting only.
* The filter runs AFTER the FK-coercion helper, so anonymous sentinels
  (``0`` / ``"0"`` → ``None``) cannot match a numeric token.

These tests live in their own module rather than extending the existing
``test_bandwidth_tracker_session_telemetry.py`` suite because the
exclude filter is conceptually distinct from the unconditional-write
semantics that suite is the regression guard for. Two adjacent modules
make the test surface readable; the conflict surface with other
parallel beads (bd-7i2vv, bd-skqln.9) stays minimal.

Synthetic identities only — ``docs/security/threat_model_stats_v2.md``
§7.7.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

import bandwidth_tracker
import database
from bandwidth_tracker import (
    BandwidthTracker,
    _is_excluded_telemetry_user,
    _parse_telemetry_exclude_users,
)
from models import SessionTelemetry, User


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

    The exclude filter tests need ``get_users`` to return a deterministic
    user list so the username-axis match is testable; individual tests
    override it as needed.
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
def seed_synthetic_users(patched_session_local):
    """Insert two synthetic users so session_telemetry.user_id FKs satisfy.

    Returns ``(included_user_id, excluded_user_id)``. The naming reflects
    test intent: the "included" user is the human viewer whose rows
    should survive the filter; the "excluded" user is the admin/API
    account whose rows should be dropped.
    """
    session = patched_session_local()
    try:
        included = User(
            id=44,
            username="synthetic-uqbob-viewer",
            email="synthetic-uqbob-viewer@example.invalid",
            auth_provider="local",
            is_active=True,
        )
        excluded = User(
            id=99,
            username="synthetic-uqbob-admin",
            email="synthetic-uqbob-admin@example.invalid",
            auth_provider="local",
            is_active=True,
        )
        session.add_all([included, excluded])
        session.commit()
        return included.id, excluded.id
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
    """Run ``_collect_stats`` twice so the second poll observes an open
    active-connection row and produces a per-channel byte delta.
    """
    mock_client.get_channel_stats.return_value = {"channels": [first_payload]}
    await tracker._collect_stats()
    mock_client.get_channel_stats.return_value = {"channels": [second_payload]}
    await tracker._collect_stats()


# ---------------------------------------------------------------------------
# Env-var parsing
# ---------------------------------------------------------------------------

def test_parser_default_is_empty_set(monkeypatch):
    """Default: env var unset → empty token set → no filtering.

    This is the pre-uqbob behavior preserved as a parser invariant. The
    empty set is the sentinel ``_write_session_telemetry`` short-circuits
    on before paying for any per-row lookup.
    """
    monkeypatch.delenv("ECM_TELEMETRY_EXCLUDE_USERS", raising=False)
    assert _parse_telemetry_exclude_users() == frozenset()


def test_parser_empty_string_is_empty_set(monkeypatch):
    """Explicit empty string treated identically to "unset"."""
    monkeypatch.setenv("ECM_TELEMETRY_EXCLUDE_USERS", "")
    assert _parse_telemetry_exclude_users() == frozenset()


def test_parser_single_token_is_lower_cased(monkeypatch):
    """A single token is whitespace-stripped and lower-cased."""
    monkeypatch.setenv("ECM_TELEMETRY_EXCLUDE_USERS", "  Claude  ")
    assert _parse_telemetry_exclude_users() == frozenset({"claude"})


def test_parser_multi_token_splits_on_comma(monkeypatch):
    """Multiple comma-separated tokens parse into a set; mixed alpha + numeric."""
    monkeypatch.setenv("ECM_TELEMETRY_EXCLUDE_USERS", "3,claude,  kmfelmer ")
    assert _parse_telemetry_exclude_users() == frozenset(
        {"3", "claude", "kmfelmer"}
    )


def test_parser_drops_empty_tokens(monkeypatch):
    """Leading / trailing / repeated commas produce empty tokens, which
    are dropped — they would otherwise match nothing useful and waste
    set membership checks.
    """
    monkeypatch.setenv("ECM_TELEMETRY_EXCLUDE_USERS", ",,claude,,3,,")
    assert _parse_telemetry_exclude_users() == frozenset({"claude", "3"})


# ---------------------------------------------------------------------------
# _is_excluded_telemetry_user matcher
# ---------------------------------------------------------------------------

def test_matcher_empty_token_set_never_matches():
    """The empty set is the "no filtering" sentinel — short-circuit before
    any string compare.
    """
    assert _is_excluded_telemetry_user(
        user_id=3, username="claude", exclude_tokens=frozenset()
    ) is False


def test_matcher_numeric_user_id_token_match():
    """A numeric token matches the stringified user_id."""
    assert _is_excluded_telemetry_user(
        user_id=3, username=None, exclude_tokens=frozenset({"3"})
    ) is True


def test_matcher_username_token_match_case_insensitive():
    """Username tokens match case-insensitively against the resolved name."""
    assert _is_excluded_telemetry_user(
        user_id=42, username="Claude", exclude_tokens=frozenset({"claude"})
    ) is True


def test_matcher_neither_axis_matches():
    """Neither id nor username in the token set → no match."""
    assert _is_excluded_telemetry_user(
        user_id=42, username="kmfelmer", exclude_tokens=frozenset({"claude", "3"})
    ) is False


def test_matcher_none_user_id_no_numeric_match():
    """An anonymous (None) user_id cannot match a numeric token even if
    the token would otherwise stringify-compare against ``"None"``.
    """
    assert _is_excluded_telemetry_user(
        user_id=None, username=None, exclude_tokens=frozenset({"3"})
    ) is False


def test_matcher_either_axis_match_is_sufficient():
    """Match on user_id alone (username missing) — row drops."""
    assert _is_excluded_telemetry_user(
        user_id=3, username=None, exclude_tokens=frozenset({"3", "claude"})
    ) is True


# ---------------------------------------------------------------------------
# End-to-end _collect_stats behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exclude_unset_writes_all_rows(
    patched_session_local,
    seed_synthetic_users,
    tracker,
    mock_client,
    monkeypatch,
):
    """Regression guard: env var unset → behavior identical to pre-uqbob.

    Every active connection produces a session_telemetry row regardless
    of which user_id is attributed.
    """
    monkeypatch.delenv("ECM_TELEMETRY_EXCLUDE_USERS", raising=False)
    included_uid, excluded_uid = seed_synthetic_users
    first = _channel_payload(
        total_bytes=1_000_000,
        client_count=2,
        client_ips=["10.0.0.1", "10.0.0.2"],
        client_user_ids={"10.0.0.1": included_uid, "10.0.0.2": excluded_uid},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_count=2,
        client_ips=["10.0.0.1", "10.0.0.2"],
        client_user_ids={"10.0.0.1": included_uid, "10.0.0.2": excluded_uid},
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    # 2 clients × 2 polls = 4 rows; both user_ids represented.
    assert len(rows) == 4
    user_ids = {r.user_id for r in rows}
    assert user_ids == {included_uid, excluded_uid}


@pytest.mark.asyncio
async def test_exclude_by_username_drops_matching_rows(
    patched_session_local,
    seed_synthetic_users,
    tracker,
    mock_client,
    monkeypatch,
):
    """Username-axis match: env var lists the Dispatcharr-side username
    ``claude``. Rows whose Dispatcharr user_id resolves to that username
    are filtered out; the included viewer's rows survive.
    """
    monkeypatch.setenv("ECM_TELEMETRY_EXCLUDE_USERS", "claude")
    included_uid, excluded_uid = seed_synthetic_users
    # Dispatcharr-side users feed: id=excluded_uid maps to "claude".
    mock_client.get_users.return_value = [
        {"id": included_uid, "username": "kmfelmer"},
        {"id": excluded_uid, "username": "claude"},
    ]
    first = _channel_payload(
        total_bytes=1_000_000,
        client_count=2,
        client_ips=["10.0.0.1", "10.0.0.2"],
        client_user_ids={"10.0.0.1": included_uid, "10.0.0.2": excluded_uid},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_count=2,
        client_ips=["10.0.0.1", "10.0.0.2"],
        client_user_ids={"10.0.0.1": included_uid, "10.0.0.2": excluded_uid},
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    # Only the included viewer's rows survive — 1 client × 2 polls = 2 rows.
    assert len(rows) == 2
    assert all(r.user_id == included_uid for r in rows)


@pytest.mark.asyncio
async def test_exclude_by_user_id_drops_matching_rows(
    patched_session_local,
    seed_synthetic_users,
    tracker,
    mock_client,
    monkeypatch,
):
    """user_id-axis match: a numeric token matches the coerced user_id
    even when the Dispatcharr users feed is empty (so the username axis
    cannot fire). Survives the case where ECM cannot resolve usernames.
    """
    monkeypatch.setenv("ECM_TELEMETRY_EXCLUDE_USERS", str(seed_synthetic_users[1]))
    included_uid, excluded_uid = seed_synthetic_users
    # Dispatcharr's get_users feed is empty — only the numeric axis can fire.
    mock_client.get_users.return_value = []
    first = _channel_payload(
        total_bytes=1_000_000,
        client_count=2,
        client_ips=["10.0.0.1", "10.0.0.2"],
        client_user_ids={"10.0.0.1": included_uid, "10.0.0.2": excluded_uid},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_count=2,
        client_ips=["10.0.0.1", "10.0.0.2"],
        client_user_ids={"10.0.0.1": included_uid, "10.0.0.2": excluded_uid},
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    assert len(rows) == 2
    assert all(r.user_id == included_uid for r in rows)


@pytest.mark.asyncio
async def test_exclude_does_not_affect_anonymous_rows(
    patched_session_local,
    seed_synthetic_users,
    tracker,
    mock_client,
    monkeypatch,
):
    """Anonymous viewers (Dispatcharr surfaces user_id=0/None) are NOT
    filtered. The FK-coercion helper turns those into ``None`` before
    the matcher runs, and ``None`` cannot match a numeric token. This
    preserves the existing anonymous-write semantics — the exclude
    filter only deals with attributed rows.
    """
    monkeypatch.setenv("ECM_TELEMETRY_EXCLUDE_USERS", "0,claude")
    # Both clients are anonymous: Dispatcharr-side user_id=0.
    first = _channel_payload(
        total_bytes=1_000_000,
        client_count=2,
        client_ips=["10.0.0.1", "10.0.0.2"],
        client_user_ids={"10.0.0.1": 0, "10.0.0.2": 0},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_count=2,
        client_ips=["10.0.0.1", "10.0.0.2"],
        client_user_ids={"10.0.0.1": 0, "10.0.0.2": 0},
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    session = patched_session_local()
    try:
        rows = session.query(SessionTelemetry).all()
    finally:
        session.close()
    # 2 clients × 2 polls = 4 rows; all anonymous (user_id is NULL).
    assert len(rows) == 4
    assert all(r.user_id is None for r in rows)


@pytest.mark.asyncio
async def test_exclude_logs_debug_per_skipped_row(
    patched_session_local,
    seed_synthetic_users,
    tracker,
    mock_client,
    monkeypatch,
    caplog,
):
    """Each filtered row emits a debug log line tagged ``[BANDWIDTH]
    Skipped telemetry write for excluded user``. Operator-facing
    traceability — the brief mandates this so the on-call can see which
    user is being suppressed without having to grep production traffic.
    """
    monkeypatch.setenv("ECM_TELEMETRY_EXCLUDE_USERS", "claude")
    included_uid, excluded_uid = seed_synthetic_users
    mock_client.get_users.return_value = [
        {"id": included_uid, "username": "kmfelmer"},
        {"id": excluded_uid, "username": "claude"},
    ]
    first = _channel_payload(
        total_bytes=1_000_000,
        client_count=1,
        client_ips=["10.0.0.2"],
        client_user_ids={"10.0.0.2": excluded_uid},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_count=1,
        client_ips=["10.0.0.2"],
        client_user_ids={"10.0.0.2": excluded_uid},
    )

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="bandwidth_tracker"):
        await _drive_two_polls(tracker, mock_client, first, second)

    # At least one skip-debug line was emitted (poll 2 has the open
    # connection so the helper reaches the per-row filter).
    skip_lines = [
        r for r in caplog.records
        if "[BANDWIDTH] Skipped telemetry write for excluded user" in r.message
    ]
    assert skip_lines, (
        "Expected at least one '[BANDWIDTH] Skipped telemetry write for "
        f"excluded user' debug log; got: {[r.message for r in caplog.records]}"
    )
    # The reason is structured into the line for log-aggregation parsing.
    assert any("reason=excluded_user" in r.message for r in skip_lines)


@pytest.mark.asyncio
async def test_exclude_increments_rows_excluded_counter(
    patched_session_local,
    seed_synthetic_users,
    tracker,
    mock_client,
    monkeypatch,
):
    """The brief explicitly requires that skipped writes are NOT silently
    dropped — they must increment a Prometheus counter so observability
    stays honest.

    Asserts the ``ecm_session_telemetry_rows_excluded_total`` counter
    increases by the number of rows the filter dropped, labeled
    ``reason=excluded_user``.
    """
    from observability import get_metric

    monkeypatch.setenv("ECM_TELEMETRY_EXCLUDE_USERS", "claude")
    included_uid, excluded_uid = seed_synthetic_users
    mock_client.get_users.return_value = [
        {"id": included_uid, "username": "kmfelmer"},
        {"id": excluded_uid, "username": "claude"},
    ]
    counter = get_metric("session_telemetry_rows_excluded_total").labels(
        reason="excluded_user"
    )
    before = counter._value.get()

    first = _channel_payload(
        total_bytes=1_000_000,
        client_count=1,
        client_ips=["10.0.0.2"],
        client_user_ids={"10.0.0.2": excluded_uid},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_count=1,
        client_ips=["10.0.0.2"],
        client_user_ids={"10.0.0.2": excluded_uid},
    )
    await _drive_two_polls(tracker, mock_client, first, second)

    after = counter._value.get()
    # Poll 1 opens the connection AFTER the helper runs, so its per-row
    # loop sees no eligible row to exclude (conn_id is None → continue
    # fires before the filter). Poll 2 has the open connection so the
    # helper reaches the filter and skips exactly one row.
    assert after - before >= 1, (
        f"rows_excluded counter should advance by at least 1; before={before} "
        f"after={after}"
    )


@pytest.mark.asyncio
async def test_exclude_does_not_skip_get_users_when_configured(
    patched_session_local,
    seed_synthetic_users,
    tracker,
    mock_client,
    monkeypatch,
):
    """When the exclude list is configured AND the snapshot carries user
    ids, the helper MUST call ``get_users`` to resolve the username axis
    — even when the watch-history flow would otherwise skip it.

    Failure mode this guards against: a future refactor of the
    watch-history username resolution that skips the round-trip when
    only the exclude filter needs it. The filter would silently
    degrade to user_id-only matching.
    """
    monkeypatch.setenv("ECM_TELEMETRY_EXCLUDE_USERS", "claude")
    included_uid, excluded_uid = seed_synthetic_users
    mock_client.get_users.return_value = [
        {"id": excluded_uid, "username": "claude"},
    ]
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": excluded_uid},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": excluded_uid},
    )

    await _drive_two_polls(tracker, mock_client, first, second)

    # ``get_users`` was invoked on each poll so the username axis is
    # populated for the filter.
    assert mock_client.get_users.await_count == 2
