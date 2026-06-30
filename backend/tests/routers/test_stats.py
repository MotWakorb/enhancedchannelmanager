"""
Unit tests for stats, enhanced stats, and popularity endpoints.

Tests: 15 endpoints covering channel stats, activity, bandwidth,
       watch history, unique viewers, popularity rankings.
Mocks: get_client(), BandwidthTracker, PopularityCalculator.
"""
import pytest
from unittest.mock import AsyncMock, patch


class TestChannelStats:
    """Tests for GET /api/stats/channels."""

    @pytest.mark.asyncio
    async def test_returns_stats(self, async_client):
        """Returns channel stats from client."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "active_channels": 5, "total_clients": 3,
        }

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        assert response.json()["active_channels"] == 5

    @pytest.mark.asyncio
    async def test_client_error(self, async_client):
        """Returns 500 on client error."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.side_effect = Exception("Timeout")

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 500


class TestChannelStatsStreamEnrichment:
    """Tests for stream identity enrichment on GET /api/stats/channels (bd-ox5q8).

    The endpoint feeds the Stats v2 Active Channels live view. Each
    active channel must surface ``stream_name`` and ``m3u_account_id``
    so the UI can render the ``[<provider>] - <stream_name>`` badge.
    Source of truth is the live resolver (same logic as
    ``BandwidthTracker._resolve_provider_ids``) — not the persisted
    ``session_telemetry`` row, because operators expect the live view
    to be immediately accurate (no poll-cycle lag).
    """

    @pytest.mark.asyncio
    async def test_enriches_direct_stream_id_channel(self, async_client):
        """Channel surfaces ``stream_id`` directly → endpoint populates
        ``stream_name`` and ``m3u_account_id`` from the streams batch."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-1",
                    "channel_name": "300 | TNT",
                    "stream_id": 555,
                    "clients": [],
                },
            ],
        }
        mock_client.get_streams_by_ids.return_value = [
            {"id": 555, "name": "US: TNT", "m3u_account": 6},
        ]

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        body = response.json()
        ch = body["channels"][0]
        assert ch["stream_name"] == "US: TNT"
        assert ch["m3u_account_id"] == 6

    @pytest.mark.asyncio
    async def test_enriches_url_derived_stream_id_channel(self, async_client):
        """Channel has no ``stream_id`` but the URL's trailing path
        segment is a Dispatcharr stream id — resolver's URL fallback
        (bd-kbgey) kicks in and enrichment completes."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-2",
                    "channel_name": "Channel 2",
                    "url": "https://example.gives/live/x/y/777.ts",
                    "clients": [],
                },
            ],
        }
        mock_client.get_streams_by_ids.return_value = [
            {"id": 777, "name": "Discovery", "m3u_account": 6},
        ]

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        ch = response.json()["channels"][0]
        assert ch["stream_name"] == "Discovery"
        assert ch["m3u_account_id"] == 6

    @pytest.mark.asyncio
    async def test_enriches_url_hostname_match_channel(self, async_client):
        """URL-derived id misses the batched lookup → bd-gy5nd
        URL-hostname-match path resolves the provider id from the
        configured M3U account's ``server_url`` hostname. Retargeted
        from the bd-5g7kx ``channel-streams`` fallback test — that
        path called ``GET /channels/<channel_uuid>/streams/`` with a
        proxy-session UUID, which never resolved correctly.
        """
        mock_client = AsyncMock()
        active_url = "https://infinity.gives/live/mot/16118141/85796.ts"
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-3",
                    "channel_name": "300 | TNT",
                    "url": active_url,
                    "clients": [],
                },
            ],
        }
        # URL-derived id is the upstream provider id, not in the batch
        # response. The bd-gy5nd URL-hostname match attributes via the
        # configured M3U account.
        mock_client.get_streams_by_ids.return_value = []
        mock_client.get_m3u_accounts.return_value = [
            {"id": 6, "name": "Infinity TV", "server_url": "https://infinity.gives/list.m3u"},
        ]

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        ch = response.json()["channels"][0]
        assert ch["m3u_account_id"] == 6
        # bd-gy5nd: ``provider_name`` carries the M3U account name and
        # ``provider_hostname`` the URL hostname.
        assert ch["provider_name"] == "Infinity TV"
        assert ch["provider_hostname"] == "infinity.gives"

    @pytest.mark.asyncio
    async def test_enriches_multiple_channels_in_one_response(self, async_client):
        """All three resolver paths in one endpoint response (the
        operator's reality: heterogeneous active channels). Each row's
        identity is populated correctly without cross-contamination.
        bd-gy5nd: the third path is now URL-hostname match against
        configured M3U accounts (replaced the bd-5g7kx
        channel-streams fallback).
        """
        mock_client = AsyncMock()
        active_url_c3 = "https://infinity.gives/live/mot/16118141/85796.ts"
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-1",
                    "stream_id": 555,
                    "clients": [],
                },
                {
                    "channel_id": "uuid-2",
                    "url": "https://example.gives/live/x/y/777.ts",
                    "clients": [],
                },
                {
                    "channel_id": "uuid-3",
                    "url": active_url_c3,
                    "clients": [],
                },
            ],
        }
        mock_client.get_streams_by_ids.return_value = [
            {"id": 555, "name": "ESPN", "m3u_account": 1},
            {"id": 777, "name": "Discovery", "m3u_account": 2},
        ]
        # bd-gy5nd: uuid-3's URL parses to upstream id 85796 (not in
        # Dispatcharr's stream table); the URL-hostname match attributes
        # via the configured M3U account.
        mock_client.get_m3u_accounts.return_value = [
            {"id": 6, "name": "Infinity TV", "server_url": "https://infinity.gives/list.m3u"},
        ]

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        by_uuid = {c["channel_id"]: c for c in response.json()["channels"]}
        assert by_uuid["uuid-1"]["stream_name"] == "ESPN"
        assert by_uuid["uuid-1"]["m3u_account_id"] == 1
        assert by_uuid["uuid-2"]["stream_name"] == "Discovery"
        assert by_uuid["uuid-2"]["m3u_account_id"] == 2
        # uuid-3 resolves via URL-hostname match — provider_name is the
        # M3U account name, m3u_account_id is the matched account's id.
        # stream_name stays None (no Dispatcharr stream row matched).
        assert by_uuid["uuid-3"]["m3u_account_id"] == 6
        assert by_uuid["uuid-3"]["provider_name"] == "Infinity TV"
        assert by_uuid["uuid-3"]["provider_hostname"] == "infinity.gives"

    @pytest.mark.asyncio
    async def test_unresolvable_channel_writes_nulls(self, async_client):
        """A channel with no stream_id and no URL is unresolvable. The
        endpoint still returns the row, with ``stream_name`` and
        ``m3u_account_id`` set to ``None``. The frontend renders the
        bare channel name with no badge."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {"channel_id": "uuid-1", "channel_name": "Unknown", "clients": []},
            ],
        }
        mock_client.get_streams_by_ids.return_value = []

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        ch = response.json()["channels"][0]
        assert ch["stream_name"] is None
        assert ch["m3u_account_id"] is None

    @pytest.mark.asyncio
    async def test_resolver_failure_does_not_break_endpoint(self, async_client):
        """If the resolver raises (Dispatcharr lookup error), the
        endpoint still returns successfully — enrichment is best-effort.
        Active Channels rendering must not depend on resolver success."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {"channel_id": "uuid-1", "stream_id": 555, "clients": []},
            ],
        }
        mock_client.get_streams_by_ids.side_effect = Exception("Dispatcharr timeout")

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        ch = response.json()["channels"][0]
        # Resolver raised → identity columns NULL but row still present.
        assert ch.get("stream_name") is None
        assert ch.get("m3u_account_id") is None

    @pytest.mark.asyncio
    async def test_skips_resolver_round_trip_when_no_channels(self, async_client):
        """No active channels → no Dispatcharr round-trip for stream
        resolution. Avoids one wasted HTTP call per poll-equivalent
        endpoint hit when nothing is streaming."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {"channels": []}

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        mock_client.get_streams_by_ids.assert_not_called()


class TestChannelStatsBadgeSumInvariant:
    """Tests for the bd-lhxfu provider-attribution invariant on
    ``GET /api/stats/channels``.

    The PO observed: 5 active channels, but the per-provider live-stats
    badges summed to 4 (one channel missing from every provider bucket).
    Root cause: the resolver couldn't attribute the missing channel to
    a provider, so the backend's response had ``m3u_account_id`` set to
    ``None`` for that row — and the frontend silently dropped null-
    provider rows from the badge sum.

    These tests lock the **endpoint contract** the frontend relies on
    to enforce its own sum invariant (provider badges + Unknown bucket
    == active-channel count):

    1. Every channel in the response has the ``m3u_account_id`` key
       present (possibly ``None``) — never absent. The frontend's
       Unknown-bucket logic depends on being able to detect "resolver
       returned but provider was None" vs "field missing entirely".
    2. The number of channels in the response equals
       ``count(channels)`` — no silent dropping of unattributed rows
       from the response itself.
    3. Resolver-failure mode (Dispatcharr lookup raises) leaves rows
       in the response with ``m3u_account_id == None`` so they can be
       routed to the Unknown bucket, instead of stripping the row.
    """

    @pytest.mark.asyncio
    async def test_every_channel_has_m3u_account_id_key_when_resolver_succeeds(
        self, async_client
    ):
        """Mixed-attribution payload: 2 resolved + 1 unresolved. The
        endpoint MUST surface every row with the ``m3u_account_id``
        key present (resolved id or ``None``) so the frontend can
        bucket the unresolved row into Unknown instead of dropping
        it from the badge sum."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {"channel_id": "uuid-1", "stream_id": 555, "clients": []},
                {"channel_id": "uuid-2", "stream_id": 777, "clients": []},
                # No stream_id, no URL — resolver can't attribute.
                {"channel_id": "uuid-3", "channel_name": "Mystery", "clients": []},
            ],
        }
        mock_client.get_streams_by_ids.return_value = [
            {"id": 555, "name": "ESPN", "m3u_account": 1},
            {"id": 777, "name": "Discovery", "m3u_account": 2},
        ]

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        channels = response.json()["channels"]
        # Invariant lock: response surfaces every active channel.
        assert len(channels) == 3
        # Invariant lock: m3u_account_id key present on every row,
        # even when resolution failed (the frontend keys off this).
        for ch in channels:
            assert "m3u_account_id" in ch, (
                f"channel {ch.get('channel_id')!r} missing m3u_account_id key — "
                "frontend cannot route to Unknown bucket"
            )
        # Spot-check the resolved-vs-unresolved partition.
        by_uuid = {c["channel_id"]: c for c in channels}
        assert by_uuid["uuid-1"]["m3u_account_id"] == 1
        assert by_uuid["uuid-2"]["m3u_account_id"] == 2
        assert by_uuid["uuid-3"]["m3u_account_id"] is None

    @pytest.mark.asyncio
    async def test_every_channel_has_m3u_account_id_key_when_resolver_fails(
        self, async_client
    ):
        """Worst-case attribution: Dispatcharr lookup raises mid-batch.
        The endpoint MUST still return every active row, with
        ``m3u_account_id`` set to ``None`` — never strip the row, never
        omit the key. This is what guarantees that "active channels in
        response" stays equal to "active channels Dispatcharr reported"
        even when the entire resolver chain is down."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {"channel_id": "uuid-1", "stream_id": 555, "clients": []},
                {"channel_id": "uuid-2", "stream_id": 777, "clients": []},
            ],
        }
        # Resolver chain falls over completely.
        mock_client.get_streams_by_ids.side_effect = Exception("Dispatcharr down")

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        channels = response.json()["channels"]
        assert len(channels) == 2
        for ch in channels:
            assert "m3u_account_id" in ch
            # Resolver failure → key present, value None — frontend
            # bucket-sorts both rows into Unknown so the badge sum
            # still equals the active-channel count.
            assert ch["m3u_account_id"] is None


class TestChannelStatsUrlProvider:
    """Tests for bd-gy5nd URL-hostname provider resolution contract on
    ``GET /api/stats/channels``.

    The Stats page showed "Unknown" for the provider on the channel the
    PO was watching. Root cause: ECM's resolver was calling the wrong
    Dispatcharr endpoint with a proxy-session UUID — Dispatcharr returned
    the SPA ``index.html`` rather than 404, generating ``provider_
    resolution_failed`` WARN spam every 10s and never resolving the
    provider. bd-gy5nd replaces that path with URL-hostname matching
    against configured M3U accounts.

    These tests lock the endpoint contract:

    1. ``provider_name`` is the matched M3U account's ``name`` when the
       URL's hostname equals one of the configured accounts' ``server_
       url`` hostnames.
    2. ``provider_name`` is the bare URL hostname when no M3U account
       matches the URL's hostname (operator hasn't configured a source
       for this upstream).
    3. ``stream_name`` still resolves via the stream-id direct path
       (the M3U URL match is a fallback, not a replacement).
    4. The broken ``get_channel_streams(<channel_uuid>)`` call site is
       gone — the endpoint is never invoked from the stats resolver
       hot path.
    """

    @pytest.mark.asyncio
    async def test_provider_name_from_m3u_account_match(self, async_client):
        """URL hostname matches a configured M3U account's ``server_url``
        hostname → ``provider_name`` is the M3U account's ``name``,
        ``m3u_account_id`` is the matched account's ``id``,
        ``provider_hostname`` is the parsed URL hostname.
        """
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-1",
                    "channel_name": "494 | TSN 5",
                    "url": "https://infinity.gives/live/mot/16118141/108495.ts",
                    "clients": [],
                },
            ],
        }
        mock_client.get_streams_by_ids.return_value = []
        mock_client.get_m3u_accounts.return_value = [
            {"id": 6, "name": "Infinity TV", "server_url": "https://infinity.gives/list.m3u"},
        ]

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        ch = response.json()["channels"][0]
        assert ch["m3u_account_id"] == 6
        assert ch["provider_name"] == "Infinity TV"
        assert ch["provider_hostname"] == "infinity.gives"
        # bd-gy5nd: the broken endpoint is never called.
        mock_client.get_channel_streams.assert_not_called()

    @pytest.mark.asyncio
    async def test_provider_name_falls_back_to_bare_hostname(self, async_client):
        """URL parses to a hostname but no M3U account is configured for
        it (operator hasn't connected the upstream provider to ECM).
        bd-gy5nd contract: ``provider_name`` is the bare hostname so
        the operator sees ``infinity.gives`` in the badge instead of
        "Unknown". ``m3u_account_id`` stays NULL.
        """
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-1",
                    "channel_name": "494 | TSN 5",
                    "url": "https://infinity.gives/live/mot/16118141/108495.ts",
                    "clients": [],
                },
            ],
        }
        mock_client.get_streams_by_ids.return_value = []
        # No M3U account configured for ``infinity.gives``.
        mock_client.get_m3u_accounts.return_value = []

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        ch = response.json()["channels"][0]
        assert ch["m3u_account_id"] is None
        assert ch["provider_name"] == "infinity.gives"
        assert ch["provider_hostname"] == "infinity.gives"

    @pytest.mark.asyncio
    async def test_stream_name_still_resolves_via_stream_id_direct_path(self, async_client):
        """The stream-id direct path is not affected by bd-gy5nd —
        ``stream_name`` still resolves from ``get_streams_by_ids`` when
        a valid integer ``stream_id`` is present. ``provider_name``
        enriches from the M3U accounts side-load when ``m3u_account_id``
        resolves to a known account.
        """
        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-1",
                    "stream_id": 12345,
                    "url": "https://infinity.gives/live/mot/16118141/12345.ts",
                    "clients": [],
                },
            ],
        }
        mock_client.get_streams_by_ids.return_value = [
            {"id": 12345, "name": "TSN 5", "m3u_account": 6},
        ]
        mock_client.get_m3u_accounts.return_value = [
            {"id": 6, "name": "Infinity TV", "server_url": "https://infinity.gives/list.m3u"},
        ]

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        ch = response.json()["channels"][0]
        # Stream-id direct path: stream_name + m3u_account_id from the
        # batched lookup.
        assert ch["stream_name"] == "TSN 5"
        assert ch["m3u_account_id"] == 6
        # bd-gy5nd: provider_name enriches from the m3u_accounts side-load.
        assert ch["provider_name"] == "Infinity TV"
        assert ch["provider_hostname"] == "infinity.gives"

    @pytest.mark.asyncio
    async def test_no_resolution_failed_warn_for_url_hostname_path(
        self, async_client, caplog
    ):
        """bd-gy5nd: the 10s ``[STATS_V2] provider_resolution_failed``
        WARN spam is the primary symptom this bead fixes. Triggering
        the PO's exact data shape (URL-derived stream id absent from
        Dispatcharr, no M3U match) must NOT emit the WARN — the
        legitimate "no M3U source configured for this hostname" case
        is normal data, not an error.
        """
        import logging as _logging

        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {
                    "channel_id": "uuid-1",
                    "url": "https://infinity.gives/live/mot/16118141/85796.ts",
                    "clients": [],
                },
            ],
        }
        mock_client.get_streams_by_ids.return_value = []
        mock_client.get_m3u_accounts.return_value = []

        caplog.clear()
        with patch("routers.stats.get_client", return_value=mock_client):
            with caplog.at_level(_logging.WARNING, logger="bandwidth_tracker"):
                response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        assert not any(
            "[STATS_V2] provider_resolution_failed" in record.message
            for record in caplog.records
        ), [r.message for r in caplog.records]
        # And the broken endpoint must not have been called.
        mock_client.get_channel_streams.assert_not_called()


class TestChannelStatsUnknownProviderCounter:
    """Tests for ecm_stats_active_channels_unknown_provider_total counter (bd-w89ox).

    Counter increments once per unattributed channel per /api/stats/channels call
    so SLO-8 dashboards can trend the Unknown-bucket rate without scraping the UI.
    """

    @pytest.mark.asyncio
    async def test_increments_counter_for_unattributed_channel(self, async_client):
        """Counter increments by 1 for a channel whose m3u_account_id resolves
        to None after the live resolver runs."""
        import observability

        observability.reset_for_tests()
        observability.install_metrics()

        mock_client = AsyncMock()
        # Channel has no stream_id and no URL — resolver returns no attribution.
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {"channel_id": "uuid-1", "channel_name": "Unattributed", "clients": []},
            ],
        }
        mock_client.get_streams_by_ids.return_value = []

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        ch = response.json()["channels"][0]
        assert ch["m3u_account_id"] is None

        metric = observability.get_metric("stats_active_channels_unknown_provider_total")
        samples = list(metric.collect())
        total_value = None
        for mf in samples:
            for sample in mf.samples:
                if sample.name == "ecm_stats_active_channels_unknown_provider_total":
                    total_value = sample.value
                    break
        assert total_value is not None and total_value >= 1, (
            "Expected ecm_stats_active_channels_unknown_provider_total to have been incremented"
        )

    @pytest.mark.asyncio
    async def test_does_not_increment_for_attributed_channel(self, async_client):
        """Counter does NOT increment when m3u_account_id is resolved (non-None)."""
        import observability

        observability.reset_for_tests()
        observability.install_metrics()

        mock_client = AsyncMock()
        mock_client.get_channel_stats.return_value = {
            "channels": [
                {"channel_id": "uuid-1", "stream_id": 555, "clients": []},
            ],
        }
        mock_client.get_streams_by_ids.return_value = [
            {"id": 555, "name": "US: ESPN", "m3u_account": 6},
        ]
        mock_client.get_m3u_accounts.return_value = [
            {"id": 6, "name": "Provider A"},
        ]

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels")

        assert response.status_code == 200
        ch = response.json()["channels"][0]
        # Attributed channels must not increment the unknown counter.
        assert ch["m3u_account_id"] is not None

        metric = observability.get_metric("stats_active_channels_unknown_provider_total")
        samples = list(metric.collect())
        total_value = 0.0
        for mf in samples:
            for sample in mf.samples:
                if sample.name == "ecm_stats_active_channels_unknown_provider_total":
                    total_value = sample.value
                    break
        assert total_value == 0.0, (
            f"Expected counter at 0 for attributed channel, got {total_value}"
        )


class TestChannelStatsDetail:
    """Tests for GET /api/stats/channels/{channel_id}."""

    @pytest.mark.asyncio
    async def test_returns_detail(self, async_client):
        """Returns detailed stats for a channel."""
        mock_client = AsyncMock()
        mock_client.get_channel_stats_detail.return_value = {
            "channel_id": 42, "clients": [],
        }

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/channels/42")

        assert response.status_code == 200
        mock_client.get_channel_stats_detail.assert_called_once_with(42)


class TestSystemEvents:
    """Tests for GET /api/stats/activity."""

    @pytest.mark.asyncio
    async def test_returns_events(self, async_client):
        """Returns system events."""
        mock_client = AsyncMock()
        mock_client.get_system_events.return_value = {"events": []}

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/activity")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_passes_filters(self, async_client):
        """Passes limit, offset, event_type filters."""
        mock_client = AsyncMock()
        mock_client.get_system_events.return_value = {"events": []}

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/activity", params={
                "limit": 50, "offset": 10, "event_type": "channel_start",
            })

        assert response.status_code == 200
        mock_client.get_system_events.assert_called_once_with(
            limit=50, offset=10, event_type="channel_start",
        )

    @pytest.mark.asyncio
    async def test_clamps_limit(self, async_client):
        """Limit is clamped to 1000."""
        mock_client = AsyncMock()
        mock_client.get_system_events.return_value = {"events": []}

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/activity", params={"limit": 5000})

        assert response.status_code == 200
        call_args = mock_client.get_system_events.call_args
        assert call_args[1]["limit"] == 1000

    @pytest.mark.asyncio
    async def test_enriches_events_with_username(self, async_client, test_session):
        """Each event's client IP is resolved to a streaming username (#2).

        The IP nested under ``details.client_ip`` is also lifted to a stable
        top-level ``ip_address``.
        """
        from datetime import datetime, date
        from models import UniqueClientConnection

        test_session.add(UniqueClientConnection(
            ip_address="10.0.0.5", channel_id="ch-1", channel_name="ESPN",
            user_id=7, username="alice", date=date(2026, 6, 30),
            connected_at=datetime(2026, 6, 30, 12, 0, 0), watch_seconds=60,
        ))
        test_session.commit()

        mock_client = AsyncMock()
        mock_client.get_system_events.return_value = {
            "events": [
                {"id": 1, "event_type": "client_connect", "channel_name": "ESPN",
                 "details": {"client_ip": "10.0.0.5"}},
            ],
            "count": 1, "total": 1,
        }

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/activity")

        assert response.status_code == 200
        event = response.json()["events"][0]
        assert event["username"] == "alice"
        assert event["ip_address"] == "10.0.0.5"

    @pytest.mark.asyncio
    async def test_event_username_falls_back_to_ip_when_unresolved(self, async_client, test_session):
        """An event whose IP has no connection row gets ``username: None`` (#2).

        The frontend renders the IP as the fallback; the backend keeps username
        honestly null.
        """
        mock_client = AsyncMock()
        mock_client.get_system_events.return_value = {
            "events": [
                {"id": 2, "event_type": "client_disconnect",
                 "details": {"client_ip": "203.0.113.9"}},
            ],
            "count": 1, "total": 1,
        }

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stats/activity")

        assert response.status_code == 200
        event = response.json()["events"][0]
        assert event["username"] is None
        assert event["ip_address"] == "203.0.113.9"


class TestStopChannel:
    """Tests for POST /api/stats/channels/{channel_id}/stop."""

    @pytest.mark.asyncio
    async def test_stops_channel(self, async_client):
        """Stops a channel."""
        mock_client = AsyncMock()
        mock_client.stop_channel.return_value = {"status": "stopped"}

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.post("/api/stats/channels/42/stop")

        assert response.status_code == 200


class TestStopClient:
    """Tests for POST /api/stats/channels/{channel_id}/stop-client."""

    @pytest.mark.asyncio
    async def test_stops_client(self, async_client):
        """Stops a client connection."""
        mock_client = AsyncMock()
        mock_client.stop_client.return_value = {"status": "stopped"}

        with patch("routers.stats.get_client", return_value=mock_client):
            response = await async_client.post("/api/stats/channels/42/stop-client")

        assert response.status_code == 200


class TestBandwidthStats:
    """Tests for GET /api/stats/bandwidth."""

    @pytest.mark.asyncio
    async def test_returns_bandwidth(self, async_client):
        """Returns bandwidth summary forwarded verbatim from BandwidthTracker."""
        with patch("routers.stats.BandwidthTracker.get_bandwidth_summary", return_value={
            "today": {"bytes_in": 1000},
        }):
            response = await async_client.get("/api/stats/bandwidth")

        assert response.status_code == 200
        assert response.json() == {"today": {"bytes_in": 1000}}


class TestTopWatched:
    """Tests for GET /api/stats/top-watched."""

    @pytest.mark.asyncio
    async def test_returns_top_channels(self, async_client):
        """Returns top watched channels forwarded verbatim from BandwidthTracker."""
        with patch("routers.stats.BandwidthTracker.get_top_watched_channels", return_value=[
            {"channel_name": "ESPN", "watch_count": 100},
        ]):
            response = await async_client.get("/api/stats/top-watched")

        assert response.status_code == 200
        assert response.json() == [{"channel_name": "ESPN", "watch_count": 100}]

    @pytest.mark.asyncio
    async def test_passes_params(self, async_client):
        """Passes limit and sort_by params."""
        with patch("routers.stats.BandwidthTracker.get_top_watched_channels", return_value=[]) as mock:
            response = await async_client.get("/api/stats/top-watched", params={
                "limit": 5, "sort_by": "time",
            })

        assert response.status_code == 200
        mock.assert_called_once_with(limit=5, sort_by="time")


class TestUniqueViewers:
    """Tests for GET /api/stats/unique-viewers."""

    @pytest.mark.asyncio
    async def test_returns_summary(self, async_client):
        """Returns unique viewers summary forwarded verbatim from BandwidthTracker."""
        with patch("routers.stats.BandwidthTracker.get_unique_viewers_summary", return_value={
            "total_unique": 42,
        }):
            response = await async_client.get("/api/stats/unique-viewers")

        assert response.status_code == 200
        assert response.json() == {"total_unique": 42}

    @pytest.mark.asyncio
    async def test_defaults_group_by_ip(self, async_client):
        """Without ?group_by, the tracker is called with group_by='ip' (#3)."""
        with patch("routers.stats.BandwidthTracker.get_unique_viewers_summary", return_value={}) as mock:
            response = await async_client.get("/api/stats/unique-viewers")

        assert response.status_code == 200
        mock.assert_called_once_with(days=7, group_by="ip")

    @pytest.mark.asyncio
    async def test_forwards_group_by_user(self, async_client):
        """?group_by=user is forwarded to the tracker (#3)."""
        with patch("routers.stats.BandwidthTracker.get_unique_viewers_summary", return_value={}) as mock:
            response = await async_client.get(
                "/api/stats/unique-viewers", params={"days": 30, "group_by": "user"}
            )

        assert response.status_code == 200
        mock.assert_called_once_with(days=30, group_by="user")


class TestChannelBandwidth:
    """Tests for GET /api/stats/channel-bandwidth."""

    @pytest.mark.asyncio
    async def test_returns_stats(self, async_client):
        """Returns per-channel bandwidth stats forwarded verbatim from BandwidthTracker."""
        with patch("routers.stats.BandwidthTracker.get_channel_bandwidth_stats", return_value=[
            {"channel_id": "abc", "bytes": 5000},
        ]):
            response = await async_client.get("/api/stats/channel-bandwidth")

        assert response.status_code == 200
        assert response.json() == [{"channel_id": "abc", "bytes": 5000}]


class TestUniqueViewersByChannel:
    """Tests for GET /api/stats/unique-viewers-by-channel."""

    @pytest.mark.asyncio
    async def test_returns_data(self, async_client):
        """Returns unique viewers per channel forwarded verbatim from BandwidthTracker."""
        with patch("routers.stats.BandwidthTracker.get_unique_viewers_by_channel", return_value=[
            {"channel_id": "abc", "unique_viewers": 7},
        ]):
            response = await async_client.get("/api/stats/unique-viewers-by-channel")

        assert response.status_code == 200
        assert response.json() == [{"channel_id": "abc", "unique_viewers": 7}]

    @pytest.mark.asyncio
    async def test_defaults_group_by_ip(self, async_client):
        """Without ?group_by, the tracker is called with group_by='ip' (#4)."""
        with patch("routers.stats.BandwidthTracker.get_unique_viewers_by_channel", return_value=[]) as mock:
            response = await async_client.get("/api/stats/unique-viewers-by-channel")

        assert response.status_code == 200
        mock.assert_called_once_with(days=7, limit=20, group_by="ip")

    @pytest.mark.asyncio
    async def test_forwards_group_by_user(self, async_client):
        """?group_by=user is forwarded to the tracker (#4)."""
        with patch("routers.stats.BandwidthTracker.get_unique_viewers_by_channel", return_value=[]) as mock:
            response = await async_client.get(
                "/api/stats/unique-viewers-by-channel",
                params={"days": 14, "limit": 5, "group_by": "user"},
            )

        assert response.status_code == 200
        mock.assert_called_once_with(days=14, limit=5, group_by="user")


class TestWatchHistory:
    """Tests for GET /api/stats/watch-history."""

    @pytest.mark.asyncio
    async def test_returns_empty_history(self, async_client):
        """Returns empty history with pagination."""
        response = await async_client.get("/api/stats/watch-history")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["history"] == []
        assert "summary" in data


class TestPopularityRankings:
    """Tests for GET /api/stats/popularity/rankings."""

    @pytest.mark.asyncio
    async def test_returns_rankings(self, async_client):
        """Returns popularity rankings forwarded verbatim from PopularityCalculator."""
        with patch("popularity_calculator.PopularityCalculator.get_rankings", return_value={
            "rankings": [{"channel_id": "abc", "rank": 1}], "total": 1,
        }):
            response = await async_client.get("/api/stats/popularity/rankings")

        assert response.status_code == 200
        assert response.json() == {"rankings": [{"channel_id": "abc", "rank": 1}], "total": 1}


class TestChannelPopularity:
    """Tests for GET /api/stats/popularity/channel/{channel_id}."""

    @pytest.mark.asyncio
    async def test_returns_score(self, async_client):
        """Returns popularity score for a channel."""
        with patch("popularity_calculator.PopularityCalculator.get_channel_score", return_value={
            "channel_id": "abc", "score": 85.5,
        }):
            response = await async_client.get("/api/stats/popularity/channel/abc")

        assert response.status_code == 200
        assert response.json()["score"] == 85.5

    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(self, async_client):
        """Returns 404 when no score exists."""
        with patch("popularity_calculator.PopularityCalculator.get_channel_score", return_value=None):
            response = await async_client.get("/api/stats/popularity/channel/unknown")

        assert response.status_code == 404


class TestTrendingChannels:
    """Tests for GET /api/stats/popularity/trending."""

    @pytest.mark.asyncio
    async def test_returns_trending(self, async_client):
        """Returns trending channels forwarded verbatim from PopularityCalculator."""
        with patch("popularity_calculator.PopularityCalculator.get_trending_channels", return_value=[
            {"channel_id": "abc", "trend": "up"},
        ]):
            response = await async_client.get("/api/stats/popularity/trending")

        assert response.status_code == 200
        assert response.json() == [{"channel_id": "abc", "trend": "up"}]

    @pytest.mark.asyncio
    async def test_rejects_invalid_direction(self, async_client):
        """Returns 400 for invalid direction."""
        response = await async_client.get("/api/stats/popularity/trending", params={
            "direction": "sideways",
        })
        assert response.status_code == 400


class TestCalculatePopularity:
    """Tests for POST /api/stats/popularity/calculate."""

    @pytest.mark.asyncio
    async def test_triggers_calculation(self, async_client):
        """Triggers popularity calculation."""
        with patch("popularity_calculator.calculate_popularity", return_value={
            "calculated": 50, "period_days": 7,
        }):
            response = await async_client.post("/api/stats/popularity/calculate")

        assert response.status_code == 200
        assert response.json()["calculated"] == 50
