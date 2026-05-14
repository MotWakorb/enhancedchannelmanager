"""Integration tests for GH-59 provider-stats read API (bd-skqln.16).

Four endpoints in ``routers/stats.py``:

* ``GET /api/stats/providers/buffering`` — time-series of buffer_event_count
  grouped by ``(provider_id, time_bucket)``. Params: ``window`` (7d/30d/90d),
  ``bucket`` (hour/day).
* ``GET /api/stats/providers/watch-time`` — total watch time per provider
  over the window. ``SUM(poll_interval_ms)`` grouped by provider_id, with
  the same DISTINCT-(channel, observed_at) collapse pattern skqln.3/skqln.5
  use to avoid multi-client overcount.
* ``GET /api/stats/providers/channel-heatmap`` — 2D grid:
  rows=providers, cols=channels, cell=``SUM(bytes_delta)``. Capped at top-N
  channels (config: N=50 default) by total bytes.
* ``GET /api/stats/providers/bitrate`` — derived bitrate time-series per
  provider: ``SUM(bytes_delta) * 8 / SUM(poll_interval_ms)`` per
  ``(provider_id, time_bucket)``, using the same DISTINCT-(channel,
  observed_at) collapse so poll_interval_ms denominator is not multiplied
  by concurrent-client count.

Critical correctness properties (mirror skqln.5):

1. **Multi-client overcount guard**: a channel with N concurrent clients in
   one poll must contribute exactly ONE poll interval (and ONE bytes_delta)
   to per-provider aggregates, NOT N.
2. **NULL provider_id surfaces as a bucket** — not silently excluded.
   The bead description: "Operators need to see the attribution gap."
3. **Property test**: SUM of per-provider watch-time over the universe
   equals SUM of per-user watch-time over the same universe (the bead's
   "Property-based test" acceptance row).
4. **Top-N cap on heatmap** payload bounded to 50 channels by default.

Auth posture (PO directive 2026-05-13):

* Watch-time/provider-stats endpoints are admin-only. Non-admin callers
  receive 403 from ALL 4 endpoints regardless of query params. Same
  ``get_watch_time_caller`` test seam skqln.5 established — reused here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from models import SessionTelemetry, UniqueClientConnection, User


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ms(dt: datetime) -> int:
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
    session.flush()
    return user


def _add_telemetry(
    session,
    *,
    user_id: int | None,
    provider_id: int | None,
    channel_id: str,
    observed_at_ms: int,
    poll_interval_ms: int = 10_000,
    bytes_delta: int = 1000,
    buffer_event_count: int = 0,
    session_id: str | None = None,
    stream_id: int | None = None,
    stream_name: str | None = None,
) -> None:
    session.add(
        SessionTelemetry(
            session_id=session_id
            or f"sess-u{user_id}-p{provider_id}-{channel_id}-{observed_at_ms}",
            observed_at=observed_at_ms,
            user_id=user_id,
            provider_id=provider_id,
            channel_id=channel_id,
            bytes_delta=bytes_delta,
            buffer_event_count=buffer_event_count,
            poll_interval_ms=poll_interval_ms,
            stream_id=stream_id,
            stream_name=stream_name,
        )
    )


# Common base time for tests — well inside any 7d window from "now".
NOW = datetime.now(timezone.utc).replace(microsecond=0)
BASE = NOW - timedelta(days=1)


# ---------------------------------------------------------------------------
# GET /api/stats/providers/buffering
# ---------------------------------------------------------------------------


class TestProvidersBufferingEnvelope:
    """Envelope shape + happy-path aggregation."""

    @pytest.mark.asyncio
    async def test_empty_returns_valid_envelope(self, async_client, test_session):
        response = await async_client.get("/api/stats/providers/buffering")
        assert response.status_code == 200, response.text
        body = response.json()
        assert "data" in body
        assert "meta" in body
        assert "pagination" in body
        assert body["data"] == []
        assert body["meta"]["window"] == "7d"
        assert body["meta"]["bucket"] == "hour"
        assert body["meta"]["total_rows"] == 0

    @pytest.mark.asyncio
    async def test_aggregates_buffer_events_per_provider_bucket(
        self, async_client, test_session
    ):
        """Provider 1 has 5 buffer events in hour-bucket A and 3 in bucket B.
        Provider 2 has 2 in bucket A. Expect 3 rows."""
        _add_user(test_session, user_id=10, username="alice")
        # hour-bucket A: BASE rounded to hour
        bucket_a = BASE.replace(minute=0, second=0, microsecond=0)
        bucket_b = bucket_a + timedelta(hours=1)

        # provider 1, bucket A: 2 polls with buffer events totalling 5
        _add_telemetry(
            test_session, user_id=10, provider_id=1, channel_id="ch-a",
            observed_at_ms=_ms(bucket_a + timedelta(minutes=5)), buffer_event_count=3,
        )
        _add_telemetry(
            test_session, user_id=10, provider_id=1, channel_id="ch-a",
            observed_at_ms=_ms(bucket_a + timedelta(minutes=20)), buffer_event_count=2,
        )
        # provider 1, bucket B: 1 poll, 3 events
        _add_telemetry(
            test_session, user_id=10, provider_id=1, channel_id="ch-a",
            observed_at_ms=_ms(bucket_b + timedelta(minutes=5)), buffer_event_count=3,
        )
        # provider 2, bucket A: 1 poll, 2 events
        _add_telemetry(
            test_session, user_id=10, provider_id=2, channel_id="ch-b",
            observed_at_ms=_ms(bucket_a + timedelta(minutes=10)), buffer_event_count=2,
        )
        test_session.commit()

        response = await async_client.get(
            "/api/stats/providers/buffering?window=7d&bucket=hour"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        # Build a (provider_id, bucket) → count map.
        agg = {(r["provider_id"], r["time_bucket"]): r["buffer_event_count"] for r in body["data"]}
        bucket_a_iso = bucket_a.isoformat().replace("+00:00", "Z")
        bucket_b_iso = bucket_b.isoformat().replace("+00:00", "Z")
        assert agg[(1, bucket_a_iso)] == 5
        assert agg[(1, bucket_b_iso)] == 3
        assert agg[(2, bucket_a_iso)] == 2

    @pytest.mark.asyncio
    async def test_day_bucket_groups_polls_into_utc_days(self, async_client, test_session):
        """``bucket=day`` collapses hourly polls into UTC-day buckets."""
        _add_user(test_session, user_id=10, username="alice")
        day1 = BASE.replace(hour=0, minute=0, second=0, microsecond=0)
        # 3 polls same provider, same day, different hours — should fold to one row
        for h in (1, 5, 13):
            _add_telemetry(
                test_session, user_id=10, provider_id=1, channel_id="ch-a",
                observed_at_ms=_ms(day1 + timedelta(hours=h)), buffer_event_count=1,
            )
        test_session.commit()

        response = await async_client.get(
            "/api/stats/providers/buffering?window=7d&bucket=day"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["meta"]["bucket"] == "day"
        # Exactly one row, sum == 3
        rows = [r for r in body["data"] if r["provider_id"] == 1]
        assert len(rows) == 1
        assert rows[0]["buffer_event_count"] == 3
        # Bucket string is the day at midnight UTC
        assert rows[0]["time_bucket"].startswith(day1.date().isoformat())

    @pytest.mark.asyncio
    async def test_null_provider_surfaces_as_row(self, async_client, test_session):
        """Rows with NULL provider_id must appear as their own bucket
        (provider_id=null), not be silently excluded."""
        _add_user(test_session, user_id=10, username="alice")
        bucket_a = BASE.replace(minute=0, second=0, microsecond=0)
        _add_telemetry(
            test_session, user_id=10, provider_id=None, channel_id="ch-a",
            observed_at_ms=_ms(bucket_a + timedelta(minutes=5)), buffer_event_count=7,
        )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/buffering")
        assert response.status_code == 200, response.text
        body = response.json()
        null_rows = [r for r in body["data"] if r["provider_id"] is None]
        assert len(null_rows) == 1
        assert null_rows[0]["buffer_event_count"] == 7

    @pytest.mark.asyncio
    async def test_invalid_window_returns_400(self, async_client, test_session):
        response = await async_client.get("/api/stats/providers/buffering?window=42d")
        assert response.status_code == 400, response.text

    @pytest.mark.asyncio
    async def test_invalid_bucket_returns_400(self, async_client, test_session):
        response = await async_client.get("/api/stats/providers/buffering?bucket=minute")
        assert response.status_code == 400, response.text


# ---------------------------------------------------------------------------
# GET /api/stats/providers/watch-time
# ---------------------------------------------------------------------------


class TestProvidersWatchTime:
    @pytest.mark.asyncio
    async def test_empty_returns_envelope(self, async_client, test_session):
        response = await async_client.get("/api/stats/providers/watch-time")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["window"] == "7d"
        assert body["meta"]["total_rows"] == 0

    @pytest.mark.asyncio
    async def test_aggregates_watch_time_per_provider(self, async_client, test_session):
        """Two providers, multiple polls — SUM(poll_interval_ms)/1000 per provider."""
        _add_user(test_session, user_id=10, username="alice")
        # provider 1: 3 polls * 10s = 30s
        for i in range(3):
            _add_telemetry(
                test_session, user_id=10, provider_id=1, channel_id="ch-a",
                observed_at_ms=_ms(BASE + timedelta(seconds=10 * i)),
            )
        # provider 2: 5 polls * 10s = 50s, two channels
        for i in range(2):
            _add_telemetry(
                test_session, user_id=10, provider_id=2, channel_id="ch-b",
                observed_at_ms=_ms(BASE + timedelta(seconds=100 + 10 * i)),
            )
        for i in range(3):
            _add_telemetry(
                test_session, user_id=10, provider_id=2, channel_id="ch-c",
                observed_at_ms=_ms(BASE + timedelta(seconds=200 + 10 * i)),
            )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/watch-time")
        assert response.status_code == 200, response.text
        body = response.json()
        rows = {r["provider_id"]: r for r in body["data"]}
        assert rows[1]["total_watch_seconds"] == 30
        assert rows[2]["total_watch_seconds"] == 50

    @pytest.mark.asyncio
    async def test_multi_client_overcount_collapsed(self, async_client, test_session):
        """3 concurrent clients on one channel in one poll → ONE poll interval
        credited to provider, not 3. Mirrors skqln.5's correctness guarantee."""
        _add_user(test_session, user_id=10, username="alice")
        _add_user(test_session, user_id=20, username="bob")
        _add_user(test_session, user_id=30, username="carol")
        observed_at = _ms(BASE)
        for uid in (10, 20, 30):
            _add_telemetry(
                test_session, user_id=uid, provider_id=1, channel_id="ch-multi",
                observed_at_ms=observed_at,
                session_id=f"sess-{uid}",
            )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/watch-time")
        assert response.status_code == 200, response.text
        body = response.json()
        rows = {r["provider_id"]: r for r in body["data"]}
        # 1 poll interval = 10s, NOT 30 (which would be the bug).
        assert rows[1]["total_watch_seconds"] == 10, (
            "Multi-client overcount: a (channel, poll) tuple with N concurrent "
            "clients must contribute ONE poll interval to per-provider totals, "
            "not N. Check the DISTINCT-by-(provider_id, channel_id, observed_at) "
            "subquery in stats.py."
        )

    @pytest.mark.asyncio
    async def test_null_provider_surfaces_as_unknown(self, async_client, test_session):
        """Rows with NULL provider_id surface as a `provider_id: null` row."""
        _add_user(test_session, user_id=10, username="alice")
        _add_telemetry(
            test_session, user_id=10, provider_id=None, channel_id="ch-a",
            observed_at_ms=_ms(BASE), poll_interval_ms=10_000,
        )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/watch-time")
        assert response.status_code == 200, response.text
        body = response.json()
        null_rows = [r for r in body["data"] if r["provider_id"] is None]
        assert len(null_rows) == 1
        assert null_rows[0]["total_watch_seconds"] == 10

    @pytest.mark.asyncio
    async def test_window_filter_excludes_old_rows(self, async_client, test_session):
        """``window=7d`` filters rows older than 7 days; ``window=30d`` picks
        up more history.

        Offsets are chosen to safely fall inside/outside each window even with
        seconds of skew between fixture-seed time and request time:
        - Row A: 20 days ago → outside 7d, inside 30d
        - Row B: 2 days ago → inside both
        """
        _add_user(test_session, user_id=10, username="alice")
        _add_telemetry(
            test_session, user_id=10, provider_id=1, channel_id="ch-a",
            observed_at_ms=_ms(NOW - timedelta(days=20)),
        )
        _add_telemetry(
            test_session, user_id=10, provider_id=1, channel_id="ch-a",
            observed_at_ms=_ms(NOW - timedelta(days=2)),
        )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/watch-time?window=7d")
        assert response.status_code == 200, response.text
        body = response.json()
        rows = {r["provider_id"]: r for r in body["data"]}
        # Only the recent row in-window: 10s
        assert rows[1]["total_watch_seconds"] == 10

        # And window=30d picks up both — 20s
        response = await async_client.get("/api/stats/providers/watch-time?window=30d")
        body = response.json()
        rows = {r["provider_id"]: r for r in body["data"]}
        assert rows[1]["total_watch_seconds"] == 20

    @pytest.mark.asyncio
    async def test_response_row_shape_matches_documented_contract(
        self, async_client, test_session
    ):
        """bd-tknci (2026-05-13): the documented row contract is
        ``{provider_id, total_watch_seconds}``. The frontend
        ``ProviderWatchTimeRow`` TypeScript type and the panel's bar
        chart depend on those exact field names — drift here would
        break the panel silently (charts render zeros instead of
        crashing). Pin the contract.
        """
        _add_user(test_session, user_id=10, username="alice")
        _add_telemetry(
            test_session,
            user_id=10,
            provider_id=1,
            channel_id="ch-a",
            observed_at_ms=_ms(BASE),
        )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/watch-time")
        body = response.json()
        assert isinstance(body["data"], list) and body["data"], (
            "Expected at least one row for the seeded provider; check fixture"
        )
        row = body["data"][0]
        assert set(row.keys()) == {"provider_id", "total_watch_seconds"}, (
            "Provider watch-time row contract drifted — the frontend bar "
            "chart binds to these exact field names"
        )
        assert isinstance(row["total_watch_seconds"], int)


# ---------------------------------------------------------------------------
# GET /api/stats/providers/channel-heatmap
# ---------------------------------------------------------------------------


class TestProvidersChannelHeatmap:
    @pytest.mark.asyncio
    async def test_empty_returns_envelope(self, async_client, test_session):
        response = await async_client.get("/api/stats/providers/channel-heatmap")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["top_n"] == 50
        assert body["meta"]["total_rows"] == 0

    @pytest.mark.asyncio
    async def test_aggregates_bytes_per_provider_channel(self, async_client, test_session):
        """Heatmap row = (provider_id, channel_id, bytes). Sums bytes_delta per cell."""
        _add_user(test_session, user_id=10, username="alice")
        # provider 1, ch-a: 3 polls * 1000 bytes
        for i in range(3):
            _add_telemetry(
                test_session, user_id=10, provider_id=1, channel_id="ch-a",
                observed_at_ms=_ms(BASE + timedelta(seconds=10 * i)), bytes_delta=1000,
            )
        # provider 1, ch-b: 2 polls * 500 bytes
        for i in range(2):
            _add_telemetry(
                test_session, user_id=10, provider_id=1, channel_id="ch-b",
                observed_at_ms=_ms(BASE + timedelta(seconds=100 + 10 * i)), bytes_delta=500,
            )
        # provider 2, ch-a: 1 poll * 2000 bytes
        _add_telemetry(
            test_session, user_id=10, provider_id=2, channel_id="ch-a",
            observed_at_ms=_ms(BASE + timedelta(seconds=200)), bytes_delta=2000,
        )
        # Side-load names
        test_session.add(UniqueClientConnection(
            ip_address="10.0.0.1", channel_id="ch-a", channel_name="Alpha",
            user_id=10, username="alice", date=BASE.date(), connected_at=BASE,
            watch_seconds=30,
        ))
        test_session.add(UniqueClientConnection(
            ip_address="10.0.0.1", channel_id="ch-b", channel_name="Bravo",
            user_id=10, username="alice", date=BASE.date(), connected_at=BASE,
            watch_seconds=20,
        ))
        test_session.commit()

        response = await async_client.get("/api/stats/providers/channel-heatmap")
        assert response.status_code == 200, response.text
        body = response.json()
        cells = {(r["provider_id"], r["channel_id"]): r for r in body["data"]}
        assert cells[(1, "ch-a")]["bytes"] == 3000
        assert cells[(1, "ch-a")]["channel_name"] == "Alpha"
        assert cells[(1, "ch-b")]["bytes"] == 1000
        assert cells[(1, "ch-b")]["channel_name"] == "Bravo"
        assert cells[(2, "ch-a")]["bytes"] == 2000

    @pytest.mark.asyncio
    async def test_top_n_caps_channels(self, async_client, test_session):
        """With top_n=3, only the top 3 channels by total bytes appear (across
        all providers). Channels outside the top-N are excluded entirely."""
        _add_user(test_session, user_id=10, username="alice")
        # 5 channels: ch-1 has 5000 bytes, ch-2 has 4000, ch-3 has 3000,
        # ch-4 has 2000, ch-5 has 1000. With top_n=3 → only ch-1, ch-2, ch-3.
        for ch_num, bytes_total in [(1, 5000), (2, 4000), (3, 3000), (4, 2000), (5, 1000)]:
            _add_telemetry(
                test_session, user_id=10, provider_id=1, channel_id=f"ch-{ch_num}",
                observed_at_ms=_ms(BASE + timedelta(seconds=ch_num * 10)),
                bytes_delta=bytes_total,
            )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/channel-heatmap?top_n=3")
        assert response.status_code == 200, response.text
        body = response.json()
        channel_ids = {r["channel_id"] for r in body["data"]}
        assert channel_ids == {"ch-1", "ch-2", "ch-3"}
        assert body["meta"]["top_n"] == 3

    @pytest.mark.asyncio
    async def test_default_top_n_is_50(self, async_client, test_session):
        """Default ``top_n=50`` truncates a 55-channel fixture to 50 rows."""
        _add_user(test_session, user_id=10, username="alice")
        # 55 channels, each with a unique bytes_delta so ordering is stable
        for i in range(55):
            _add_telemetry(
                test_session, user_id=10, provider_id=1, channel_id=f"ch-{i:03d}",
                observed_at_ms=_ms(BASE + timedelta(seconds=i)),
                bytes_delta=(55 - i) * 100,  # ch-000 highest, ch-054 lowest
            )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/channel-heatmap")
        assert response.status_code == 200, response.text
        body = response.json()
        channel_ids = {r["channel_id"] for r in body["data"]}
        # 50 channels in output, 5 channels excluded (the bottom 5)
        assert len(channel_ids) == 50
        # ch-000 (highest) is in; ch-054 (lowest) is not
        assert "ch-000" in channel_ids
        assert "ch-054" not in channel_ids

    @pytest.mark.asyncio
    async def test_null_provider_surfaces_in_heatmap(self, async_client, test_session):
        _add_user(test_session, user_id=10, username="alice")
        _add_telemetry(
            test_session, user_id=10, provider_id=None, channel_id="ch-orphan",
            observed_at_ms=_ms(BASE), bytes_delta=12345,
        )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/channel-heatmap")
        assert response.status_code == 200, response.text
        body = response.json()
        null_rows = [r for r in body["data"] if r["provider_id"] is None]
        assert len(null_rows) == 1
        assert null_rows[0]["bytes"] == 12345
        assert null_rows[0]["channel_id"] == "ch-orphan"

    @pytest.mark.asyncio
    async def test_heatmap_surfaces_latest_stream_identity(
        self, async_client, test_session
    ):
        """bd-kh23e: each (provider, channel) cell carries
        ``latest_stream_id`` + ``latest_stream_name`` from the row with
        ``MAX(observed_at)`` in the (provider_id, channel_id) bucket.

        Seeds two stream identities on (provider=1, channel=ch-a).
        The newer one (higher observed_at) must surface in the cell.
        """
        _add_user(test_session, user_id=10, username="alice")
        # (provider=1, ch-a): older identity then newer identity
        _add_telemetry(
            test_session, user_id=10, provider_id=1, channel_id="ch-a",
            observed_at_ms=_ms(BASE),
            bytes_delta=1000,
            stream_id=100, stream_name="US: TNT (older)",
        )
        _add_telemetry(
            test_session, user_id=10, provider_id=1, channel_id="ch-a",
            observed_at_ms=_ms(BASE + timedelta(seconds=30)),
            bytes_delta=2000,
            stream_id=200, stream_name="US: TNT (newer)",
        )
        # (provider=2, ch-a): independent cell with its own identity
        _add_telemetry(
            test_session, user_id=10, provider_id=2, channel_id="ch-a",
            observed_at_ms=_ms(BASE + timedelta(seconds=60)),
            bytes_delta=500,
            stream_id=300, stream_name="US: TNT (failover)",
        )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/channel-heatmap")
        assert response.status_code == 200, response.text
        body = response.json()
        cells = {(r["provider_id"], r["channel_id"]): r for r in body["data"]}
        # Newer identity wins per (provider, channel) bucket.
        assert cells[(1, "ch-a")]["latest_stream_id"] == 200
        assert cells[(1, "ch-a")]["latest_stream_name"] == "US: TNT (newer)"
        # (provider=2, ch-a) is its own bucket with its own identity.
        assert cells[(2, "ch-a")]["latest_stream_id"] == 300
        assert cells[(2, "ch-a")]["latest_stream_name"] == "US: TNT (failover)"

    @pytest.mark.asyncio
    async def test_heatmap_stream_identity_nullable(
        self, async_client, test_session
    ):
        """Cells with no stream identity (pre-kh23e rows / resolver miss)
        surface ``latest_stream_id`` and ``latest_stream_name`` as ``null``
        — the cell is NOT excluded."""
        _add_user(test_session, user_id=10, username="alice")
        _add_telemetry(
            test_session, user_id=10, provider_id=1, channel_id="ch-a",
            observed_at_ms=_ms(BASE), bytes_delta=1000,
            stream_id=None, stream_name=None,
        )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/channel-heatmap")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["data"]) == 1
        row = body["data"][0]
        assert row["latest_stream_id"] is None
        assert row["latest_stream_name"] is None


# ---------------------------------------------------------------------------
# GET /api/stats/providers/bitrate
# ---------------------------------------------------------------------------


class TestProvidersBitrate:
    @pytest.mark.asyncio
    async def test_empty_returns_envelope(self, async_client, test_session):
        response = await async_client.get("/api/stats/providers/bitrate")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["window"] == "7d"
        assert body["meta"]["bucket"] == "hour"

    @pytest.mark.asyncio
    async def test_computes_bitrate_per_provider_bucket(self, async_client, test_session):
        """bitrate_bps = SUM(bytes_delta) * 8 / SUM(poll_interval_ms) * 1000
        (* 1000 to convert ms → s in denominator)."""
        _add_user(test_session, user_id=10, username="alice")
        bucket_a = BASE.replace(minute=0, second=0, microsecond=0)
        # provider 1 in hour A: 2 polls, total 20_000 bytes over 20 seconds
        # → 20_000 * 8 / 20 = 8000 bps
        _add_telemetry(
            test_session, user_id=10, provider_id=1, channel_id="ch-a",
            observed_at_ms=_ms(bucket_a + timedelta(minutes=5)),
            bytes_delta=10_000, poll_interval_ms=10_000,
        )
        _add_telemetry(
            test_session, user_id=10, provider_id=1, channel_id="ch-a",
            observed_at_ms=_ms(bucket_a + timedelta(minutes=10)),
            bytes_delta=10_000, poll_interval_ms=10_000,
        )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/bitrate")
        assert response.status_code == 200, response.text
        body = response.json()
        bucket_a_iso = bucket_a.isoformat().replace("+00:00", "Z")
        rows = [r for r in body["data"] if r["provider_id"] == 1 and r["time_bucket"] == bucket_a_iso]
        assert len(rows) == 1
        assert rows[0]["bitrate_bps"] == 8000

    @pytest.mark.asyncio
    async def test_bitrate_multi_client_collapse(self, async_client, test_session):
        """Two clients on same (channel, poll-tick): bytes_delta is summed once,
        poll_interval_ms counted once. Bitrate must not be skewed by the
        multi-client overcount.

        Setup: 2 clients on ch-a at observed_at=T0 reporting bytes=10000 each,
        plus 1 client on ch-b at T0 reporting bytes=5000. All same poll.
        Expected: 10000 + 5000 = 15000 bytes / 10s = 12000 bps.
        (NOT 25000 bytes / 30s, which would happen with multi-counting.)
        """
        _add_user(test_session, user_id=10, username="alice")
        _add_user(test_session, user_id=20, username="bob")
        bucket_a = BASE.replace(minute=0, second=0, microsecond=0)
        observed_at = _ms(bucket_a + timedelta(minutes=5))
        # 2 sessions on ch-a, same observed_at — must collapse to one
        _add_telemetry(
            test_session, user_id=10, provider_id=1, channel_id="ch-a",
            observed_at_ms=observed_at, bytes_delta=10_000, poll_interval_ms=10_000,
            session_id="sess-alice",
        )
        _add_telemetry(
            test_session, user_id=20, provider_id=1, channel_id="ch-a",
            observed_at_ms=observed_at, bytes_delta=10_000, poll_interval_ms=10_000,
            session_id="sess-bob",
        )
        # 1 session on ch-b same observed_at
        _add_telemetry(
            test_session, user_id=10, provider_id=1, channel_id="ch-b",
            observed_at_ms=observed_at, bytes_delta=5_000, poll_interval_ms=10_000,
            session_id="sess-alice-b",
        )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/bitrate")
        assert response.status_code == 200, response.text
        body = response.json()
        bucket_a_iso = bucket_a.isoformat().replace("+00:00", "Z")
        rows = [r for r in body["data"] if r["provider_id"] == 1 and r["time_bucket"] == bucket_a_iso]
        assert len(rows) == 1
        # bytes: ch-a (10000 from collapsed, both clients agree) + ch-b (5000)
        #      = 15000 bytes
        # interval: ch-a one tick (10s) + ch-b one tick (10s) = 20s
        # bitrate = 15000 * 8 / 20 = 6000 bps
        assert rows[0]["bitrate_bps"] == 6000, (
            "Bitrate multi-client overcount: a (channel, observed_at) tuple "
            "with N concurrent clients must contribute ONE bytes_delta and "
            "ONE poll interval, not N. Check the DISTINCT-by-(provider, "
            "channel, observed_at) subquery in stats.py."
        )

    @pytest.mark.asyncio
    async def test_null_provider_bitrate_surfaces(self, async_client, test_session):
        _add_user(test_session, user_id=10, username="alice")
        bucket_a = BASE.replace(minute=0, second=0, microsecond=0)
        _add_telemetry(
            test_session, user_id=10, provider_id=None, channel_id="ch-a",
            observed_at_ms=_ms(bucket_a + timedelta(minutes=5)),
            bytes_delta=5000, poll_interval_ms=10_000,
        )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/bitrate")
        assert response.status_code == 200, response.text
        body = response.json()
        null_rows = [r for r in body["data"] if r["provider_id"] is None]
        assert len(null_rows) == 1
        # 5000 * 8 / 10 = 4000 bps
        assert null_rows[0]["bitrate_bps"] == 4000

    @pytest.mark.asyncio
    async def test_day_bucket_for_bitrate(self, async_client, test_session):
        _add_user(test_session, user_id=10, username="alice")
        day1 = BASE.replace(hour=0, minute=0, second=0, microsecond=0)
        for h in (1, 5):
            _add_telemetry(
                test_session, user_id=10, provider_id=1, channel_id="ch-a",
                observed_at_ms=_ms(day1 + timedelta(hours=h)),
                bytes_delta=10_000, poll_interval_ms=10_000,
            )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/bitrate?bucket=day")
        assert response.status_code == 200, response.text
        body = response.json()
        rows = [r for r in body["data"] if r["provider_id"] == 1]
        assert len(rows) == 1
        # 20000 bytes / 20 s = 8000 bps
        assert rows[0]["bitrate_bps"] == 8000

    @pytest.mark.asyncio
    async def test_response_row_shape_matches_documented_contract(
        self, async_client, test_session
    ):
        """bd-zrk05 (2026-05-13): the documented row contract is
        ``{provider_id, time_bucket, bitrate_bps}``. The frontend
        ``ProviderBitrateRow`` TypeScript type and the bitrate line
        chart pivot bind to those exact field names. Pin the contract
        so accidental renames in the SQL select break the test, not
        production.
        """
        _add_user(test_session, user_id=10, username="alice")
        _add_telemetry(
            test_session,
            user_id=10,
            provider_id=1,
            channel_id="ch-a",
            observed_at_ms=_ms(BASE),
            bytes_delta=1000,
            poll_interval_ms=10_000,
        )
        test_session.commit()

        response = await async_client.get("/api/stats/providers/bitrate")
        body = response.json()
        assert isinstance(body["data"], list) and body["data"], (
            "Expected at least one bitrate row for the seeded provider"
        )
        row = body["data"][0]
        assert set(row.keys()) == {"provider_id", "time_bucket", "bitrate_bps"}, (
            "Provider bitrate row contract drifted — the frontend line "
            "chart binds to these exact field names"
        )
        # bitrate_bps must be a positive integer (formula:
        # ``bytes * 8 * 1000 / ms``). 1000 bytes / 10s = 800 bps.
        assert isinstance(row["bitrate_bps"], int)
        assert row["bitrate_bps"] > 0


# ---------------------------------------------------------------------------
# Property test — per-provider watch-time SUM = per-user watch-time SUM
# ---------------------------------------------------------------------------


class TestProviderUserWatchTimeConsistency:
    """The bead's acceptance row: SUM of per-provider watch-time = SUM of
    per-user watch-time over the same fixture universe."""

    @pytest.mark.asyncio
    async def test_provider_sum_equals_user_sum(self, async_client, test_session):
        """Mixed fixture: 3 users, 2 providers, multi-poll across days.
        Both aggregations must converge on the same grand total."""
        _add_user(test_session, user_id=10, username="alice")
        _add_user(test_session, user_id=20, username="bob")
        _add_user(test_session, user_id=30, username="carol")
        # Sprinkle 12 polls across (user, provider, channel) tuples — including
        # one multi-client (channel, observed_at) collision per provider.
        seed = [
            # (user_id, provider_id, channel_id, offset_seconds, session_id_tag)
            (10, 1, "ch-a",   0, "s1"),
            (10, 1, "ch-a",  10, "s1"),
            (10, 1, "ch-b",  20, "s1"),
            (10, 2, "ch-c",  30, "s2"),
            (20, 1, "ch-a",  40, "s3"),
            (20, 1, "ch-a",  40, "s4"),  # multi-client collision on (ch-a, 40s)
            (20, 2, "ch-d",  50, "s5"),
            (20, 2, "ch-d",  60, "s5"),
            (30, 1, "ch-e",  70, "s6"),
            (30, 2, "ch-f",  80, "s7"),
            (30, 2, "ch-f",  90, "s7"),
            (30, 2, "ch-f", 100, "s7"),
        ]
        for uid, pid, ch, off, tag in seed:
            _add_telemetry(
                test_session, user_id=uid, provider_id=pid, channel_id=ch,
                observed_at_ms=_ms(BASE + timedelta(seconds=off)),
                session_id=f"sess-{uid}-{tag}-{off}",
            )
        test_session.commit()

        prov_response = await async_client.get("/api/stats/providers/watch-time")
        user_response = await async_client.get("/api/stats/watch-time")
        assert prov_response.status_code == 200, prov_response.text
        assert user_response.status_code == 200, user_response.text
        prov_total = sum(r["total_watch_seconds"] for r in prov_response.json()["data"])
        user_total = sum(r["total_watch_seconds"] for r in user_response.json()["data"])
        assert prov_total == user_total, (
            "Per-provider SUM and per-user SUM diverged. Both aggregations "
            "must collapse multi-client (channel, observed_at) tuples to "
            "ONE poll interval — divergence implies one side double-counts."
        )


# ---------------------------------------------------------------------------
# Auth — admin-only on all 4 endpoints (PO directive 2026-05-13)
# ---------------------------------------------------------------------------


class TestProviderStatsAuth:
    """Watch-time/provider-stats endpoints are admin-only. Non-admin caller
    receives 403 from ALL 4 endpoints regardless of query params."""

    PROVIDER_ENDPOINTS = [
        "/api/stats/providers/buffering",
        "/api/stats/providers/watch-time",
        "/api/stats/providers/channel-heatmap",
        "/api/stats/providers/bitrate",
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", PROVIDER_ENDPOINTS)
    async def test_non_admin_gets_403(self, async_client, test_session, endpoint):
        from main import app
        from routers.stats import get_watch_time_caller

        alice = _add_user(test_session, user_id=10, username="alice", is_admin=False)
        test_session.commit()

        async def _override_caller():
            return alice

        app.dependency_overrides[get_watch_time_caller] = _override_caller
        try:
            response = await async_client.get(endpoint)
        finally:
            app.dependency_overrides.pop(get_watch_time_caller, None)
        assert response.status_code == 403, (
            f"{endpoint} should 403 for non-admin, got {response.status_code}: "
            f"{response.text}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", PROVIDER_ENDPOINTS)
    async def test_admin_gets_200(self, async_client, test_session, endpoint):
        from main import app
        from routers.stats import get_watch_time_caller

        admin = _add_user(test_session, user_id=1, username="root", is_admin=True)
        test_session.commit()

        async def _override_caller():
            return admin

        app.dependency_overrides[get_watch_time_caller] = _override_caller
        try:
            response = await async_client.get(endpoint)
        finally:
            app.dependency_overrides.pop(get_watch_time_caller, None)
        assert response.status_code == 200, response.text
