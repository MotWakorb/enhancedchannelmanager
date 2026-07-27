"""
Tests for GET /api/stats/providers/stream-usage (GH-482, bd-n5cwp).

Per-provider stream-assignment usage: how many of a provider's streams are
wired into ECM channels — configuration data, not viewing telemetry. Two
metrics: ``assigned_streams`` (distinct streams assigned to >=1 channel,
primary) and ``total_assignments`` (SUM of channel-memberships, secondary —
counts reuse across channels).

The endpoint (routers/stats.py) reuses
``routers.streams._get_stream_channel_index`` for the reverse
stream_id -> [channel_id] index, so mocks must cover BOTH
``routers.stats.get_client``/``get_cache`` (the endpoint's own calls: M3U
accounts, per-provider count, by-ids resolution, response cache) AND
``routers.streams.get_client``/``get_cache`` (the imported index-builder's
own client/cache calls — it lives in a different module namespace).

Mocks: get_client(), get_cache() at both router module levels (per
backend/CLAUDE.md).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _channels_page(channels, count=None):
    """Build a single-page get_channels() response envelope."""
    return {"count": count if count is not None else len(channels), "results": channels}


def _passthrough_cache():
    """A MagicMock cache whose get() always misses (forces a fresh fetch)."""
    cache = MagicMock()
    cache.get.return_value = None
    return cache


def _patch_both(mock_client, mock_cache):
    """Patch get_client/get_cache on both routers.stats and routers.streams
    (the endpoint calls routers.stats directly; the imported
    _get_stream_channel_index helper resolves get_client/get_cache from its
    OWN module, routers.streams)."""
    return (
        patch("routers.stats.get_client", return_value=mock_client),
        patch("routers.stats.get_cache", return_value=mock_cache),
        patch("routers.streams.get_client", return_value=mock_client),
        patch("routers.streams.get_cache", return_value=mock_cache),
    )


class TestProviderStreamUsageEmpty:
    @pytest.mark.asyncio
    async def test_no_accounts_returns_empty_data(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_m3u_accounts.return_value = []
        mock_client.get_channels.return_value = _channels_page([])
        mock_cache = _passthrough_cache()

        p1, p2, p3, p4 = _patch_both(mock_client, mock_cache)
        with p1, p2, p3, p4:
            response = await async_client.get("/api/stats/providers/stream-usage")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total_rows"] == 0
        assert body["pagination"] is None


class TestProviderStreamUsageBasic:
    @pytest.mark.asyncio
    async def test_multi_provider_counts(self, async_client):
        """Two providers, each with a mix of assigned and unassigned streams."""
        mock_client = AsyncMock()
        mock_client.get_m3u_accounts.return_value = [
            {"id": 1, "name": "Provider A"},
            {"id": 2, "name": "Provider B"},
        ]

        # Provider A has 5 streams total, Provider B has 3.
        async def fake_get_streams(m3u_account=None, page_size=None, **kwargs):
            counts = {1: 5, 2: 3}
            return {"count": counts.get(m3u_account, 0), "results": []}

        mock_client.get_streams.side_effect = fake_get_streams

        # Channel 100 has stream 10 (provider A) and stream 20 (provider B).
        # Channel 200 has stream 10 again (provider A) — reuse.
        mock_client.get_channels.return_value = _channels_page([
            {"id": 100, "streams": [10, 20]},
            {"id": 200, "streams": [10]},
        ])
        mock_client.get_streams_by_ids.return_value = [
            {"id": 10, "m3u_account": 1},
            {"id": 20, "m3u_account": 2},
        ]
        mock_cache = _passthrough_cache()

        p1, p2, p3, p4 = _patch_both(mock_client, mock_cache)
        with p1, p2, p3, p4:
            response = await async_client.get("/api/stats/providers/stream-usage")

        assert response.status_code == 200
        data = response.json()["data"]
        by_id = {row["provider_id"]: row for row in data}

        # Provider A: 5 total streams, 1 distinct assigned stream (id 10),
        # but 2 total assignment slots (channel 100 AND channel 200).
        assert by_id[1]["provider_name"] == "Provider A"
        assert by_id[1]["total_streams"] == 5
        assert by_id[1]["assigned_streams"] == 1
        assert by_id[1]["total_assignments"] == 2
        assert by_id[1]["utilization_pct"] == 20.0

        # Provider B: 3 total streams, 1 distinct assigned stream (id 20),
        # 1 total assignment slot.
        assert by_id[2]["provider_name"] == "Provider B"
        assert by_id[2]["total_streams"] == 3
        assert by_id[2]["assigned_streams"] == 1
        assert by_id[2]["total_assignments"] == 1
        # Endpoint rounds utilization_pct to 1 decimal place.
        assert by_id[2]["utilization_pct"] == round(100 / 3, 1)

        # Sorted by assigned_streams desc (tie -> name); both tied at 1, so
        # "Provider A" sorts before "Provider B".
        assert [row["provider_id"] for row in data] == [1, 2]

    @pytest.mark.asyncio
    async def test_no_assigned_streams_for_a_provider(self, async_client):
        """A provider with streams but none assigned to any channel."""
        mock_client = AsyncMock()
        mock_client.get_m3u_accounts.return_value = [{"id": 1, "name": "Idle Provider"}]
        mock_client.get_streams.return_value = {"count": 10, "results": []}
        mock_client.get_channels.return_value = _channels_page([])
        mock_client.get_streams_by_ids.return_value = []
        mock_cache = _passthrough_cache()

        p1, p2, p3, p4 = _patch_both(mock_client, mock_cache)
        with p1, p2, p3, p4:
            response = await async_client.get("/api/stats/providers/stream-usage")

        data = response.json()["data"]
        assert len(data) == 1
        assert data[0]["total_streams"] == 10
        assert data[0]["assigned_streams"] == 0
        assert data[0]["total_assignments"] == 0
        assert data[0]["utilization_pct"] == 0.0


class TestProviderStreamUsageUnknownBucket:
    @pytest.mark.asyncio
    async def test_assigned_stream_with_no_m3u_account_is_unknown_bucket(self, async_client):
        """A stream assigned to a channel but with no m3u_account (or one not
        in the current accounts list) surfaces as an explicit 'Unknown'
        bucket rather than being silently dropped."""
        mock_client = AsyncMock()
        mock_client.get_m3u_accounts.return_value = [{"id": 1, "name": "Provider A"}]
        mock_client.get_streams.return_value = {"count": 5, "results": []}
        mock_client.get_channels.return_value = _channels_page([
            {"id": 100, "streams": [99]},
        ])
        mock_client.get_streams_by_ids.return_value = [
            {"id": 99, "m3u_account": None},
        ]
        mock_cache = _passthrough_cache()

        p1, p2, p3, p4 = _patch_both(mock_client, mock_cache)
        with p1, p2, p3, p4:
            response = await async_client.get("/api/stats/providers/stream-usage")

        data = response.json()["data"]
        unknown = [row for row in data if row["provider_id"] is None]
        assert len(unknown) == 1
        assert unknown[0]["provider_name"] == "Unknown"
        assert unknown[0]["assigned_streams"] == 1
        assert unknown[0]["total_assignments"] == 1
        # Unknown bucket has no catalog-size context — total_streams stays 0.
        assert unknown[0]["total_streams"] == 0


class TestProviderStreamUsageCaching:
    @pytest.mark.asyncio
    async def test_returns_cached_result_without_fetching(self, async_client):
        cached_envelope = {
            "data": [{"provider_id": 1, "provider_name": "Cached", "total_streams": 1,
                      "assigned_streams": 1, "total_assignments": 1, "utilization_pct": 100.0}],
            "meta": {"total_rows": 1},
            "pagination": None,
        }
        mock_cache = MagicMock()
        mock_cache.get.return_value = cached_envelope
        mock_client = AsyncMock()

        with patch("routers.stats.get_client", return_value=mock_client), \
             patch("routers.stats.get_cache", return_value=mock_cache):
            response = await async_client.get("/api/stats/providers/stream-usage")

        assert response.status_code == 200
        assert response.json() == cached_envelope
        mock_client.get_m3u_accounts.assert_not_called()

    @pytest.mark.asyncio
    async def test_bypass_cache_forces_refetch(self, async_client):
        """With bypass_cache=true, a stale cached envelope under THIS
        endpoint's own key must be ignored — but the shared cache mock must
        still miss cleanly for the unrelated assignment-index key so
        _get_stream_channel_index computes a fresh index rather than
        misinterpreting the stale envelope as an index."""
        from routers.stats import PROVIDER_STREAM_USAGE_CACHE_KEY

        stale_envelope = {"data": [{"provider_id": 1}], "meta": {}, "pagination": None}

        def cache_get(key, ttl=None):
            return stale_envelope if key == PROVIDER_STREAM_USAGE_CACHE_KEY else None

        mock_cache = MagicMock()
        mock_cache.get.side_effect = cache_get
        mock_client = AsyncMock()
        mock_client.get_m3u_accounts.return_value = []
        mock_client.get_channels.return_value = _channels_page([])

        p1, p2, p3, p4 = _patch_both(mock_client, mock_cache)
        with p1, p2, p3, p4:
            response = await async_client.get(
                "/api/stats/providers/stream-usage?bypass_cache=true"
            )

        assert response.status_code == 200
        mock_client.get_m3u_accounts.assert_called_once()
        # bypass_cache=true means the response is NOT the stale envelope.
        assert response.json() != stale_envelope


class TestProviderStreamUsageResilience:
    @pytest.mark.asyncio
    async def test_per_provider_count_failure_defaults_to_zero(self, async_client):
        """One provider's count-only fetch raises — the endpoint still
        returns 200 with that provider's total_streams at 0, rather than
        failing the whole response."""
        mock_client = AsyncMock()
        mock_client.get_m3u_accounts.return_value = [
            {"id": 1, "name": "Flaky Provider"},
            {"id": 2, "name": "Healthy Provider"},
        ]

        async def fake_get_streams(m3u_account=None, page_size=None, **kwargs):
            if m3u_account == 1:
                raise Exception("upstream timeout")
            return {"count": 7, "results": []}

        mock_client.get_streams.side_effect = fake_get_streams
        mock_client.get_channels.return_value = _channels_page([])
        mock_cache = _passthrough_cache()

        p1, p2, p3, p4 = _patch_both(mock_client, mock_cache)
        with p1, p2, p3, p4:
            response = await async_client.get("/api/stats/providers/stream-usage")

        assert response.status_code == 200
        data = response.json()["data"]
        by_id = {row["provider_id"]: row for row in data}
        assert by_id[1]["total_streams"] == 0
        assert by_id[2]["total_streams"] == 7
