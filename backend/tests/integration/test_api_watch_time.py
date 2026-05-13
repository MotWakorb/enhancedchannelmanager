"""Integration tests for GH-62 watch-time-by-user read API (bd-skqln.5).

Two endpoints in ``routers/stats.py``:

* ``GET /api/stats/watch-time`` — per-user totals; ``group_by=total`` (default)
  or ``group_by=day``. Optional ``user_id`` filter. Optional ``from`` / ``to``
  ISO-8601 UTC range.
* ``GET /api/stats/watch-time/{user_id}`` — per-user breakdown by channel.

Both read directly from ``session_telemetry`` (the per-poll fact table from
bd-skqln.2 / .3). The ``channel_watch_stats_v`` view (bd-skqln.3 step (b))
is aggregated per-channel and does NOT expose ``user_id``, so it cannot
satisfy these queries — the view is out of scope for this bead.

Critical SQL correctness property: a channel with N concurrent clients in
one poll must contribute exactly ONE poll interval, not N. This matches the
legacy ``BandwidthTracker._update_watch_time`` semantic and the
``channel_watch_stats_v`` DISTINCT-by-(channel, observed_at) subquery
(migration 0008). The tests below seed multi-client polls and assert the
no-overcount guarantee.

Auth posture (PO directive 2026-05-13):

* Endpoints are protected by the global auth middleware. **Watch-time
  stats are admin-only.** Non-admin callers receive 403 regardless of
  which ``user_id`` they query — including their own. Admin callers can
  query any user_id or omit the filter. When auth is disabled (tests'
  default ``async_client`` posture), no caller identity is enforced and
  the seeding tests below exercise the data-correctness paths.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from models import SessionTelemetry, UniqueClientConnection, User


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ms(dt: datetime) -> int:
    """Convert a UTC datetime to unix-epoch milliseconds."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _add_user(session, *, user_id: int, username: str, is_admin: bool = False) -> User:
    user = User(
        id=user_id,
        username=username,
        email=f"{username}@example.invalid",
        auth_provider="local",
        is_active=True,
        is_admin=is_admin,
    )
    session.add(user)
    # Flush so subsequent ``session_telemetry`` inserts (FK -> users.id) see
    # the row — SQLite checks FKs at insert time and the autoflush=False
    # fixture session doesn't auto-flush before related inserts.
    session.flush()
    return user


def _add_telemetry(
    session,
    *,
    user_id: int | None,
    channel_id: str,
    observed_at_ms: int,
    poll_interval_ms: int = 10_000,
    session_id: str | None = None,
    bytes_delta: int = 1000,
) -> None:
    session.add(
        SessionTelemetry(
            session_id=session_id or f"sess-u{user_id}-{channel_id}-{observed_at_ms}",
            observed_at=observed_at_ms,
            user_id=user_id,
            provider_id=None,
            channel_id=channel_id,
            bytes_delta=bytes_delta,
            buffer_event_count=0,
            poll_interval_ms=poll_interval_ms,
        )
    )


# ---------------------------------------------------------------------------
# GET /api/stats/watch-time — envelope + happy path
# ---------------------------------------------------------------------------


class TestWatchTimeListEnvelope:
    """Response envelope shape and basic happy-path aggregation."""

    @pytest.mark.asyncio
    async def test_empty_returns_valid_envelope(self, async_client, test_session):
        """No telemetry, no users — empty data list, valid envelope."""
        response = await async_client.get("/api/stats/watch-time")
        assert response.status_code == 200, response.text
        body = response.json()
        assert "data" in body
        assert "meta" in body
        assert "pagination" in body
        assert body["data"] == []
        assert body["meta"]["group_by"] == "total"
        assert body["meta"]["total_rows"] == 0

    @pytest.mark.asyncio
    async def test_empty_range_from_equals_to_returns_empty_data(
        self, async_client, test_session
    ):
        """``from == to`` is a valid empty interval; envelope still well-formed."""
        # Seed data so a non-empty query would return rows.
        _add_user(test_session, user_id=10, username="alice")
        now = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        _add_telemetry(test_session, user_id=10, channel_id="ch-a", observed_at_ms=_ms(now))
        test_session.commit()

        # Empty range — from == to, zero-width window.
        iso = now.isoformat().replace("+00:00", "Z")
        response = await async_client.get(
            f"/api/stats/watch-time?from={iso}&to={iso}"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total_rows"] == 0

    @pytest.mark.asyncio
    async def test_total_aggregates_per_user(self, async_client, test_session):
        """Two users, multiple polls each — totals sum poll_interval seconds per user."""
        _add_user(test_session, user_id=10, username="alice")
        _add_user(test_session, user_id=20, username="bob")
        base = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        # alice: 3 polls * 10s = 30s on ch-a
        for i in range(3):
            _add_telemetry(
                test_session,
                user_id=10,
                channel_id="ch-a",
                observed_at_ms=_ms(base + timedelta(seconds=10 * i)),
            )
        # bob: 5 polls * 10s = 50s, split across two channels
        for i in range(2):
            _add_telemetry(
                test_session,
                user_id=20,
                channel_id="ch-a",
                observed_at_ms=_ms(base + timedelta(seconds=10 * i)),
            )
        for i in range(3):
            _add_telemetry(
                test_session,
                user_id=20,
                channel_id="ch-b",
                observed_at_ms=_ms(base + timedelta(seconds=100 + 10 * i)),
            )
        test_session.commit()

        response = await async_client.get("/api/stats/watch-time")
        assert response.status_code == 200, response.text
        body = response.json()
        rows_by_user = {r["user_id"]: r for r in body["data"]}
        assert rows_by_user[10]["username"] == "alice"
        assert rows_by_user[10]["total_watch_seconds"] == 30
        assert rows_by_user[20]["username"] == "bob"
        assert rows_by_user[20]["total_watch_seconds"] == 50
        # last_watched is the max observed_at, ISO-8601 with Z suffix
        assert rows_by_user[10]["last_watched"].endswith("Z")

    @pytest.mark.asyncio
    async def test_user_id_filter_returns_only_matching_user(
        self, async_client, test_session
    ):
        """``?user_id=42`` filters down to that user's row only."""
        _add_user(test_session, user_id=10, username="alice")
        _add_user(test_session, user_id=42, username="zoe")
        base = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        _add_telemetry(
            test_session, user_id=10, channel_id="ch-a", observed_at_ms=_ms(base)
        )
        _add_telemetry(
            test_session,
            user_id=42,
            channel_id="ch-b",
            observed_at_ms=_ms(base + timedelta(seconds=10)),
        )
        _add_telemetry(
            test_session,
            user_id=42,
            channel_id="ch-c",
            observed_at_ms=_ms(base + timedelta(seconds=20)),
        )
        test_session.commit()

        response = await async_client.get("/api/stats/watch-time?user_id=42")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["user_id"] == 42
        assert body["data"][0]["username"] == "zoe"
        # 2 polls * 10s = 20s
        assert body["data"][0]["total_watch_seconds"] == 20


# ---------------------------------------------------------------------------
# Multi-client overcount guard — the SQL correctness invariant
# ---------------------------------------------------------------------------


class TestWatchTimeMultiClientCollapse:
    """A channel with N concurrent clients in one poll must contribute one
    poll interval to total_watch_seconds, NOT N.

    Mirrors ``test_view_collapses_multiple_clients_into_one_poll`` in
    ``test_channel_watch_stats_view.py`` — same correctness guarantee,
    different aggregation axis (per-user vs per-channel).
    """

    @pytest.mark.asyncio
    async def test_three_concurrent_clients_one_user_count_once(
        self, async_client, test_session
    ):
        """User has 3 sessions (different ``session_id``s) on the same channel in
        the same poll. The user must be credited ONE poll interval, not three.
        """
        _add_user(test_session, user_id=10, username="alice")
        observed_at = _ms(datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc))
        for sid in ("sess-1", "sess-2", "sess-3"):
            _add_telemetry(
                test_session,
                user_id=10,
                channel_id="ch-multi",
                observed_at_ms=observed_at,
                session_id=sid,
            )
        test_session.commit()

        response = await async_client.get("/api/stats/watch-time?user_id=10")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["data"]) == 1
        # 1 poll interval = 10 seconds — NOT 30 (which would be the bug).
        assert body["data"][0]["total_watch_seconds"] == 10, (
            "Multi-client overcount: a user with N concurrent sessions in one "
            "poll must be credited ONE poll interval, not N. Check the "
            "DISTINCT-by-(channel_id, observed_at) subquery in stats.py."
        )

    @pytest.mark.asyncio
    async def test_per_user_breakdown_collapses_multi_client_polls(
        self, async_client, test_session
    ):
        """Same correctness guarantee applied to the per-channel breakdown
        endpoint — a user with 3 concurrent sessions on a channel in one poll
        must see that channel credited ONE poll interval.
        """
        _add_user(test_session, user_id=10, username="alice")
        observed_at = _ms(datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc))
        for sid in ("sess-1", "sess-2", "sess-3"):
            _add_telemetry(
                test_session,
                user_id=10,
                channel_id="ch-multi",
                observed_at_ms=observed_at,
                session_id=sid,
            )
        # Side-load channel name from UniqueClientConnection.
        test_session.add(
            UniqueClientConnection(
                ip_address="10.0.0.1",
                channel_id="ch-multi",
                channel_name="Channel Multi",
                user_id=10,
                username="alice",
                date=datetime(2026, 4, 1).date(),
                connected_at=datetime(2026, 4, 1, 12, 0, 0),
                watch_seconds=10,
            )
        )
        test_session.commit()

        response = await async_client.get("/api/stats/watch-time/10")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["channel_id"] == "ch-multi"
        assert body["data"][0]["total_watch_seconds"] == 10


# ---------------------------------------------------------------------------
# group_by=day
# ---------------------------------------------------------------------------


class TestWatchTimeGroupByDay:
    @pytest.mark.asyncio
    async def test_group_by_day_emits_one_row_per_user_day(
        self, async_client, test_session
    ):
        """Same user, polls across two distinct UTC days → two rows."""
        _add_user(test_session, user_id=10, username="alice")
        day1 = datetime(2026, 4, 1, 23, 59, 50, tzinfo=timezone.utc)
        day2 = datetime(2026, 4, 2, 0, 0, 0, tzinfo=timezone.utc)
        _add_telemetry(test_session, user_id=10, channel_id="ch-a", observed_at_ms=_ms(day1))
        _add_telemetry(test_session, user_id=10, channel_id="ch-a", observed_at_ms=_ms(day2))
        test_session.commit()

        response = await async_client.get("/api/stats/watch-time?group_by=day")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["meta"]["group_by"] == "day"
        days = sorted(r["day"] for r in body["data"])
        assert days == ["2026-04-01", "2026-04-02"]
        for row in body["data"]:
            assert row["user_id"] == 10
            assert row["username"] == "alice"
            assert row["watch_seconds"] == 10

    @pytest.mark.asyncio
    async def test_invalid_group_by_returns_400(self, async_client, test_session):
        """``group_by`` only accepts ``total`` or ``day``."""
        response = await async_client.get("/api/stats/watch-time?group_by=hour")
        assert response.status_code == 400, response.text


# ---------------------------------------------------------------------------
# GET /api/stats/watch-time/{user_id} — per-channel breakdown
# ---------------------------------------------------------------------------


class TestWatchTimePerUserBreakdown:
    @pytest.mark.asyncio
    async def test_per_user_breakdown_by_channel(self, async_client, test_session):
        """User 10 has watched two channels — endpoint returns one row per channel
        with channel_name side-loaded from UniqueClientConnection."""
        _add_user(test_session, user_id=10, username="alice")
        base = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        # ch-a: 3 polls * 10s = 30s
        for i in range(3):
            _add_telemetry(
                test_session,
                user_id=10,
                channel_id="ch-a",
                observed_at_ms=_ms(base + timedelta(seconds=10 * i)),
            )
        # ch-b: 5 polls * 10s = 50s
        for i in range(5):
            _add_telemetry(
                test_session,
                user_id=10,
                channel_id="ch-b",
                observed_at_ms=_ms(base + timedelta(seconds=100 + 10 * i)),
            )
        # Side-load names.
        test_session.add(
            UniqueClientConnection(
                ip_address="10.0.0.1",
                channel_id="ch-a",
                channel_name="Alpha",
                user_id=10,
                username="alice",
                date=base.date(),
                connected_at=base,
                watch_seconds=30,
            )
        )
        test_session.add(
            UniqueClientConnection(
                ip_address="10.0.0.1",
                channel_id="ch-b",
                channel_name="Bravo",
                user_id=10,
                username="alice",
                date=base.date(),
                connected_at=base,
                watch_seconds=50,
            )
        )
        test_session.commit()

        response = await async_client.get("/api/stats/watch-time/10")
        assert response.status_code == 200, response.text
        body = response.json()
        rows_by_channel = {r["channel_id"]: r for r in body["data"]}
        assert rows_by_channel["ch-a"]["channel_name"] == "Alpha"
        assert rows_by_channel["ch-a"]["total_watch_seconds"] == 30
        assert rows_by_channel["ch-b"]["channel_name"] == "Bravo"
        assert rows_by_channel["ch-b"]["total_watch_seconds"] == 50
        # session_count = COUNT(DISTINCT session_id)
        assert rows_by_channel["ch-a"]["session_count"] >= 1
        # last_watched ISO-8601 with Z
        assert rows_by_channel["ch-a"]["last_watched"].endswith("Z")

    @pytest.mark.asyncio
    async def test_per_user_breakdown_channel_name_fallback(
        self, async_client, test_session
    ):
        """When ``UniqueClientConnection`` has no row for the channel, fall back to
        ``f"Channel {uuid[:8]}..."`` per the skqln.3 precedent."""
        _add_user(test_session, user_id=10, username="alice")
        long_uuid = "abcdef0123456789-deadbeef-cafef00d"
        base = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        _add_telemetry(
            test_session, user_id=10, channel_id=long_uuid, observed_at_ms=_ms(base)
        )
        test_session.commit()

        response = await async_client.get("/api/stats/watch-time/10")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["data"]) == 1
        # Fallback shape: "Channel <first-8-chars>..."
        assert body["data"][0]["channel_name"] == f"Channel {long_uuid[:8]}..."

    @pytest.mark.asyncio
    async def test_per_user_breakdown_empty_returns_envelope(
        self, async_client, test_session
    ):
        """User exists but has no telemetry — empty data, valid envelope."""
        _add_user(test_session, user_id=10, username="alice")
        test_session.commit()
        response = await async_client.get("/api/stats/watch-time/10")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total_rows"] == 0


# ---------------------------------------------------------------------------
# Auth enforcement — watch-time stats are admin-only (PO directive 2026-05-13)
# ---------------------------------------------------------------------------


class TestWatchTimeAuth:
    """Watch-time stats are admin-only. PO directive 2026-05-13: non-admins
    do not see stats — including their own. Both endpoints return 403 to
    any non-admin caller regardless of which user_id is queried.

    When global auth is disabled (the test-default posture and operator-only
    deployments), the handler treats no-caller as admin-equivalent — that's
    what the existing seeding-and-querying tests in TestWatchTimeAggregation
    cover.

    Test seam: ``get_watch_time_caller`` is a module-level dependency in
    ``routers.stats``. Override it via ``app.dependency_overrides`` to inject
    a fake "currently-authenticated user" without standing up a full JWT
    token + auth settings fixture.
    """

    @pytest.mark.asyncio
    async def test_non_admin_per_user_endpoint_other_user_id_gets_403(
        self, async_client, test_session
    ):
        """Authenticated as user 10 (non-admin), requesting user 20's data -> 403."""
        from main import app
        from routers.stats import get_watch_time_caller

        alice = _add_user(test_session, user_id=10, username="alice", is_admin=False)
        _add_user(test_session, user_id=20, username="bob", is_admin=False)
        test_session.commit()

        async def _override_caller():
            return alice

        app.dependency_overrides[get_watch_time_caller] = _override_caller
        try:
            response = await async_client.get("/api/stats/watch-time/20")
        finally:
            app.dependency_overrides.pop(get_watch_time_caller, None)
        assert response.status_code == 403, response.text

    @pytest.mark.asyncio
    async def test_non_admin_per_user_endpoint_own_user_id_gets_403(
        self, async_client, test_session
    ):
        """Authenticated as user 10 (non-admin), requesting OWN data -> 403.

        Watch-time stats are admin-only — non-admins cannot see stats even
        about themselves.
        """
        from main import app
        from routers.stats import get_watch_time_caller

        alice = _add_user(test_session, user_id=10, username="alice", is_admin=False)
        base = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        _add_telemetry(
            test_session, user_id=10, channel_id="ch-a", observed_at_ms=_ms(base)
        )
        test_session.commit()

        async def _override_caller():
            return alice

        app.dependency_overrides[get_watch_time_caller] = _override_caller
        try:
            response = await async_client.get("/api/stats/watch-time/10")
        finally:
            app.dependency_overrides.pop(get_watch_time_caller, None)
        assert response.status_code == 403, response.text

    @pytest.mark.asyncio
    async def test_admin_can_query_any_user_id(self, async_client, test_session):
        """Admin caller can request another user's breakdown."""
        from main import app
        from routers.stats import get_watch_time_caller

        admin = _add_user(test_session, user_id=1, username="root", is_admin=True)
        _add_user(test_session, user_id=10, username="alice", is_admin=False)
        base = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        _add_telemetry(
            test_session, user_id=10, channel_id="ch-a", observed_at_ms=_ms(base)
        )
        test_session.commit()

        async def _override_caller():
            return admin

        app.dependency_overrides[get_watch_time_caller] = _override_caller
        try:
            response = await async_client.get("/api/stats/watch-time/10")
        finally:
            app.dependency_overrides.pop(get_watch_time_caller, None)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["channel_id"] == "ch-a"

    @pytest.mark.asyncio
    async def test_non_admin_list_endpoint_gets_403(
        self, async_client, test_session
    ):
        """``GET /api/stats/watch-time`` with a non-admin caller -> 403,
        regardless of whether a ``user_id`` filter is supplied."""
        from main import app
        from routers.stats import get_watch_time_caller

        alice = _add_user(test_session, user_id=10, username="alice", is_admin=False)
        _add_user(test_session, user_id=20, username="bob", is_admin=False)
        base = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        _add_telemetry(
            test_session, user_id=10, channel_id="ch-a", observed_at_ms=_ms(base)
        )
        _add_telemetry(
            test_session, user_id=20, channel_id="ch-b", observed_at_ms=_ms(base)
        )
        test_session.commit()

        async def _override_caller():
            return alice

        app.dependency_overrides[get_watch_time_caller] = _override_caller
        try:
            # Unfiltered list
            response = await async_client.get("/api/stats/watch-time")
            assert response.status_code == 403, response.text
            # With own user_id filter — still 403
            response = await async_client.get("/api/stats/watch-time?user_id=10")
            assert response.status_code == 403, response.text
        finally:
            app.dependency_overrides.pop(get_watch_time_caller, None)
