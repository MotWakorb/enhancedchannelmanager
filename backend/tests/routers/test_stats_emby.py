"""Integration tests for Emby user-attribution surfaces on the Stats API.

bd-fm23o (final bead of EPIC bd-2cenq — Emby user attribution).

Covers the operator-visible read APIs that expose the Emby username
captured by ``BandwidthTracker._collect_emby_attributions``:

* ``/api/stats/watch-time``: emby_user_name takes precedence over
  dispatcharr_username as the display name; ``attribution_source``
  discriminator surfaces the chain so the UI can render a "via Emby"
  badge.
* ``/api/stats/channels`` (Active Channels): per-channel
  ``emby_user_name`` field populated when the resolver attributes a
  client to an Emby user.
* ``/api/stats/users/dispatcharr/{id}``: source-prefixed alias for the
  legacy ``/watch-time/{user_id}`` endpoint.
* ``/api/stats/users/emby/{id}``: per-channel breakdown filtered by
  Emby user GUID.
* ``/api/stats/users/{user_id}``: deprecated alias that routes to the
  Dispatcharr behavior and emits a WARN log per call.

The Active Channels resolver path is exercised with the live
:func:`services.emby_resolver.resolve_emby_user` function patched at
``routers.stats`` import scope — the writer-side enrichment is already
covered by ``tests/unit/test_bandwidth_tracker_emby.py``; this file
focuses on the read-API surfaces.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from models import SessionTelemetry, UniqueClientConnection
from services.emby_resolver import EmbyAttribution


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _add_telemetry(
    session,
    *,
    user_id,
    channel_id: str,
    observed_at_ms: int,
    poll_interval_ms: int = 10_000,
    session_id: str | None = None,
    dispatcharr_username: str | None = None,
    emby_user_id: str | None = None,
    emby_user_name: str | None = None,
    stream_id: int | None = None,
    stream_name: str | None = None,
) -> None:
    """Insert one ``session_telemetry`` row with optional Emby fields."""
    session.add(
        SessionTelemetry(
            session_id=session_id
            or f"sess-u{user_id}-{channel_id}-{observed_at_ms}",
            observed_at=observed_at_ms,
            user_id=user_id,
            dispatcharr_username=dispatcharr_username,
            provider_id=None,
            channel_id=channel_id,
            bytes_delta=1000,
            buffer_event_count=0,
            poll_interval_ms=poll_interval_ms,
            stream_id=stream_id,
            stream_name=stream_name,
            emby_user_id=emby_user_id,
            emby_user_name=emby_user_name,
        )
    )


# ---------------------------------------------------------------------------
# /api/stats/watch-time — emby_user_name precedence + attribution_source
# ---------------------------------------------------------------------------


class TestWatchTimeEmbyAttribution:
    """``/api/stats/watch-time`` prefers ``emby_user_name`` over
    ``dispatcharr_username`` and surfaces an ``attribution_source``
    discriminator so the frontend can render a "via Emby" badge.
    """

    @pytest.mark.asyncio
    async def test_emby_user_name_wins_over_dispatcharr_when_present(
        self, async_client, test_session
    ):
        """When a user has an Emby-attributed row, the display name is the
        Emby username and ``attribution_source`` is ``"emby"``."""
        base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(3):
            _add_telemetry(
                test_session,
                user_id=10,
                channel_id="ch-a",
                observed_at_ms=_ms(base + timedelta(seconds=10 * i)),
                dispatcharr_username="api-proxy",
                emby_user_id="uid-alice",
                emby_user_name="alice",
            )
        test_session.commit()

        response = await async_client.get("/api/stats/watch-time")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["data"]) == 1
        row = body["data"][0]
        assert row["username"] == "alice"
        assert row["attribution_source"] == "emby"
        assert row["total_watch_seconds"] == 30

    @pytest.mark.asyncio
    async def test_dispatcharr_username_used_when_emby_field_is_null(
        self, async_client, test_session
    ):
        """When telemetry rows have NULL emby_user_name, the display name
        falls back to ``dispatcharr_username`` and ``attribution_source``
        is ``"dispatcharr"`` — the legacy posture for non-Emby sessions.
        """
        base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        _add_telemetry(
            test_session,
            user_id=20,
            channel_id="ch-b",
            observed_at_ms=_ms(base),
            dispatcharr_username="bob",
            # No Emby fields — null on the row.
        )
        test_session.commit()

        response = await async_client.get("/api/stats/watch-time")
        assert response.status_code == 200, response.text
        body = response.json()
        rows_by_user = {r["user_id"]: r for r in body["data"]}
        assert rows_by_user[20]["username"] == "bob"
        assert rows_by_user[20]["attribution_source"] == "dispatcharr"

    @pytest.mark.asyncio
    async def test_attribution_source_set_per_user_independently(
        self, async_client, test_session
    ):
        """Two users in the same response — one Emby-attributed, one not —
        each carries its own ``attribution_source``. Proves the field is
        per-row, not a response-level flag.
        """
        base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        # User 10 has Emby attribution.
        _add_telemetry(
            test_session,
            user_id=10,
            channel_id="ch-a",
            observed_at_ms=_ms(base),
            dispatcharr_username="api-proxy",
            emby_user_id="uid-alice",
            emby_user_name="alice",
        )
        # User 20 has no Emby attribution.
        _add_telemetry(
            test_session,
            user_id=20,
            channel_id="ch-b",
            observed_at_ms=_ms(base + timedelta(seconds=10)),
            dispatcharr_username="bob",
        )
        test_session.commit()

        response = await async_client.get("/api/stats/watch-time")
        assert response.status_code == 200, response.text
        body = response.json()
        rows_by_user = {r["user_id"]: r for r in body["data"]}
        assert rows_by_user[10]["username"] == "alice"
        assert rows_by_user[10]["attribution_source"] == "emby"
        assert rows_by_user[20]["username"] == "bob"
        assert rows_by_user[20]["attribution_source"] == "dispatcharr"

    @pytest.mark.asyncio
    async def test_group_by_day_also_carries_attribution_source(
        self, async_client, test_session
    ):
        """``group_by=day`` rows surface the same fields. Operators may
        drill into the daily trend by user and need to know the source on
        each row."""
        day = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        _add_telemetry(
            test_session,
            user_id=10,
            channel_id="ch-a",
            observed_at_ms=_ms(day),
            dispatcharr_username="api-proxy",
            emby_user_id="uid-alice",
            emby_user_name="alice",
        )
        test_session.commit()

        response = await async_client.get("/api/stats/watch-time?group_by=day")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["data"]) == 1
        row = body["data"][0]
        assert row["username"] == "alice"
        assert row["attribution_source"] == "emby"


# ---------------------------------------------------------------------------
# /api/stats/channels — emby_user_name per channel
# ---------------------------------------------------------------------------


class TestActiveChannelsEmbyEnrichment:
    """``/api/stats/channels`` surfaces ``emby_user_name`` per channel when
    the live resolver attributes at least one client to an Emby user.
    """

    @pytest.mark.asyncio
    async def test_emby_user_name_populated_when_resolver_matches(
        self, async_client
    ):
        """Live resolver returns an attribution → channel payload carries
        ``emby_user_name``."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-emby",
                    "channel_name": "TNT HD",
                    "stream_id": 555,
                    "clients": [{"ip_address": "10.0.0.42", "user_id": 7}],
                },
            ],
        }
        mock_client.get_streams_by_ids.return_value = [
            {"id": 555, "name": "TNT HD", "m3u_account": 6},
        ]

        # Patch the resolver to attribute the one client to "alice".
        async def _fake_resolver(ip, stream_name):
            assert ip == "10.0.0.42"
            assert stream_name == "TNT HD"
            return EmbyAttribution(user_id="uid-alice", user_name="alice")

        with (
            patch("routers.stats.get_client", return_value=mock_client),
            patch(
                "services.emby_resolver.resolve_emby_user",
                side_effect=_fake_resolver,
            ),
            patch(
                "config.get_settings",
                return_value=type("S", (), {"emby_enabled": True, "emby_base_url": "http://emby"})(),
            ),
        ):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200, response.text
        body = response.json()
        ch = body["channels"][0]
        assert ch["emby_user_name"] == "alice"

    @pytest.mark.asyncio
    async def test_emby_user_name_null_when_resolver_returns_none(
        self, async_client
    ):
        """No Emby match → field is null but still present on the payload
        (documented presence is part of the shape contract)."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-noemby",
                    "channel_name": "CNN",
                    "stream_id": 600,
                    "clients": [{"ip_address": "10.0.0.99", "user_id": 8}],
                },
            ],
        }
        mock_client.get_streams_by_ids.return_value = [
            {"id": 600, "name": "CNN", "m3u_account": 6},
        ]

        async def _no_match(ip, stream_name):
            return None

        with (
            patch("routers.stats.get_client", return_value=mock_client),
            patch(
                "services.emby_resolver.resolve_emby_user",
                side_effect=_no_match,
            ),
            patch(
                "config.get_settings",
                return_value=type("S", (), {"emby_enabled": True, "emby_base_url": "http://emby"})(),
            ),
        ):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200, response.text
        body = response.json()
        ch = body["channels"][0]
        # Field is present but null — documented shape so the frontend
        # can render against a stable contract.
        assert ch["emby_user_name"] is None

    @pytest.mark.asyncio
    async def test_emby_user_name_null_when_emby_disabled(self, async_client):
        """``emby_enabled=False`` → resolver is never called; field still
        present-and-null."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-disabled",
                    "channel_name": "ESPN",
                    "stream_id": 700,
                    "clients": [{"ip_address": "10.0.0.50", "user_id": 9}],
                },
            ],
        }
        mock_client.get_streams_by_ids.return_value = [
            {"id": 700, "name": "ESPN", "m3u_account": 6},
        ]

        resolver_calls = []

        async def _track_calls(ip, stream_name):
            resolver_calls.append((ip, stream_name))
            return None

        with (
            patch("routers.stats.get_client", return_value=mock_client),
            patch(
                "services.emby_resolver.resolve_emby_user",
                side_effect=_track_calls,
            ),
            patch(
                "config.get_settings",
                return_value=type("S", (), {"emby_enabled": False, "emby_base_url": ""})(),
            ),
        ):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200, response.text
        body = response.json()
        ch = body["channels"][0]
        assert ch["emby_user_name"] is None
        # Resolver short-circuited on the disabled gate — never called.
        assert resolver_calls == []


# ---------------------------------------------------------------------------
# /api/stats/users/dispatcharr/{id} — renamed route, dispatcharr behavior
# ---------------------------------------------------------------------------


class TestUsersDispatcharrRoute:
    @pytest.mark.asyncio
    async def test_dispatcharr_route_returns_per_channel_breakdown(
        self, async_client, test_session
    ):
        """``/api/stats/users/dispatcharr/{id}`` mirrors the legacy
        ``/watch-time/{user_id}`` per-channel breakdown."""
        base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(3):
            _add_telemetry(
                test_session,
                user_id=10,
                channel_id="ch-a",
                observed_at_ms=_ms(base + timedelta(seconds=10 * i)),
            )
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
        test_session.commit()

        response = await async_client.get("/api/stats/users/dispatcharr/10")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["data"]) == 1
        row = body["data"][0]
        assert row["channel_id"] == "ch-a"
        assert row["channel_name"] == "Alpha"
        assert row["total_watch_seconds"] == 30


# ---------------------------------------------------------------------------
# /api/stats/users/emby/{id} — Emby-attributed sessions only
# ---------------------------------------------------------------------------


class TestUsersEmbyRoute:
    @pytest.mark.asyncio
    async def test_emby_route_returns_only_emby_attributed_rows(
        self, async_client, test_session
    ):
        """Endpoint filters telemetry by ``emby_user_id`` — rows without an
        Emby attribution are excluded from the aggregation."""
        base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        # Three Emby-attributed polls for alice on ch-a.
        for i in range(3):
            _add_telemetry(
                test_session,
                user_id=10,
                channel_id="ch-a",
                observed_at_ms=_ms(base + timedelta(seconds=10 * i)),
                emby_user_id="uid-alice",
                emby_user_name="alice",
            )
        # A non-Emby poll for the same ECM user_id — must NOT count
        # against the emby_user_id total.
        _add_telemetry(
            test_session,
            user_id=10,
            channel_id="ch-other",
            observed_at_ms=_ms(base + timedelta(seconds=100)),
        )
        # A different Emby user on a different channel — must be excluded.
        _add_telemetry(
            test_session,
            user_id=11,
            channel_id="ch-b",
            observed_at_ms=_ms(base + timedelta(seconds=200)),
            emby_user_id="uid-bob",
            emby_user_name="bob",
        )
        test_session.commit()

        response = await async_client.get("/api/stats/users/emby/uid-alice")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["data"]) == 1
        row = body["data"][0]
        assert row["channel_id"] == "ch-a"
        assert row["total_watch_seconds"] == 30

    @pytest.mark.asyncio
    async def test_emby_route_empty_for_unknown_emby_user_id(
        self, async_client, test_session
    ):
        """Querying an Emby user_id with no rows returns an empty envelope,
        not a 404 — same contract as the dispatcharr-source endpoint."""
        response = await async_client.get("/api/stats/users/emby/uid-nobody")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total_rows"] == 0


# ---------------------------------------------------------------------------
# /api/stats/users/{user_id} — deprecated alias
# ---------------------------------------------------------------------------


class TestUsersDeprecatedAlias:
    @pytest.mark.asyncio
    async def test_deprecated_alias_routes_to_dispatcharr_behavior(
        self, async_client, test_session
    ):
        """The deprecated alias returns the same payload as
        ``/api/stats/users/dispatcharr/{id}``."""
        base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        _add_telemetry(
            test_session,
            user_id=10,
            channel_id="ch-a",
            observed_at_ms=_ms(base),
        )
        test_session.commit()

        alias_resp = await async_client.get("/api/stats/users/10")
        canonical_resp = await async_client.get(
            "/api/stats/users/dispatcharr/10"
        )
        assert alias_resp.status_code == 200, alias_resp.text
        assert canonical_resp.status_code == 200, canonical_resp.text
        assert alias_resp.json()["data"] == canonical_resp.json()["data"]

    @pytest.mark.asyncio
    async def test_deprecated_alias_logs_warn(
        self, async_client, test_session, caplog
    ):
        """Each call emits a WARN-level log so operators / log analysis can
        track whether anything is still using the alias.

        The log message must include the called URL fragment so a grep
        across log lines reveals the consumer cadence.
        """
        with caplog.at_level(logging.WARNING, logger="routers.stats"):
            response = await async_client.get("/api/stats/users/10")
        assert response.status_code == 200, response.text
        warn_msgs = [
            r.message for r in caplog.records if r.levelno == logging.WARNING
        ]
        # The handler builds the log via lazy %-formatting; testing on
        # the rendered message is the operator-facing contract.
        rendered = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
        ]
        assert any("Deprecated alias" in m and "users/10" in m for m in rendered), (
            f"Expected a deprecation WARN; got: {warn_msgs!r} / {rendered!r}"
        )
