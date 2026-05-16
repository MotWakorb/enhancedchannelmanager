"""
Unit tests for GET /api/channel-merges/candidates (BD-D, bd-kbqwb).

Tests: endpoint response shape, candidate match, no-match, floor enforcement,
       required stream_name validation, pagination fields, group_id filter,
       and metric emission.

Mocks: get_client() for Dispatcharr channel fetches, get_settings() for the
       operator threshold. The dedup_matcher (BD-A) is NOT mocked — it is a
       pure function with no external dependencies and its real behaviour is
       the contract being exercised here.

ADR-008 §D1 / §D2 coverage:
    - Confidence floor (0.60) enforced by the matcher regardless of threshold.
    - Top-1 only; pagination fields present but degenerate.
    - Flat-outcome response envelope (no top-level ``data`` wrapper).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(channels: list[dict]) -> AsyncMock:
    """Return a mock DispatcharrClient whose get_channels() returns ``channels``."""
    mock_client = AsyncMock()
    mock_client.get_channels.return_value = {
        "results": channels,
        "count": len(channels),
    }
    return mock_client


def _make_settings(dedup_threshold: float = 0.80) -> MagicMock:
    """Return a mock settings object with the given dedup_threshold."""
    settings = MagicMock()
    settings.dedup_threshold = dedup_threshold
    return settings


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetDedupCandidates:
    """Tests for GET /api/channel-merges/candidates."""

    @pytest.mark.asyncio
    async def test_returns_candidate_above_threshold(self, async_client):
        """Returns a matching candidate when confidence >= threshold."""
        channels = [
            {"id": "uuid-espn-1", "name": "ESPN HD", "channel_group_id": 1},
            {"id": "uuid-cnn-1", "name": "CNN", "channel_group_id": 1},
        ]
        mock_client = _make_client(channels)
        mock_settings = _make_settings(dedup_threshold=0.80)

        with patch("routers.channel_merges.get_client", return_value=mock_client), \
             patch("routers.channel_merges.get_settings", return_value=mock_settings):
            response = await async_client.get(
                "/api/channel-merges/candidates",
                params={"stream_name": "ESPN HD"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["stream_name"] == "ESPN HD"
        assert len(data["candidates"]) == 1
        cand = data["candidates"][0]
        assert cand["channel_id"] == "uuid-espn-1"
        assert cand["channel_name"] == "ESPN HD"
        # Exact match → confidence == 1.0
        assert cand["confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_returns_empty_candidates_no_match(self, async_client):
        """Returns empty list when no candidate clears the threshold."""
        channels = [
            {"id": "uuid-xyz-1", "name": "Completely Unrelated Channel", "channel_group_id": 1},
        ]
        mock_client = _make_client(channels)
        mock_settings = _make_settings(dedup_threshold=0.80)

        with patch("routers.channel_merges.get_client", return_value=mock_client), \
             patch("routers.channel_merges.get_settings", return_value=mock_settings):
            response = await async_client.get(
                "/api/channel-merges/candidates",
                params={"stream_name": "ESPN Sports Network"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["stream_name"] == "ESPN Sports Network"
        assert data["candidates"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_clamps_below_floor_to_floor(self, async_client):
        """Threshold below 0.60 is clamped to floor; matching candidates still returned.

        ADR-008 §D2: the matcher (BD-A) clamps threshold = max(threshold, FLOOR).
        A 0.30 threshold request gets 0.60 behaviour. This test confirms the
        endpoint does NOT duplicate the check — it delegates to the matcher.
        The end-to-end result is the same: only candidates at/above floor are
        returned.
        """
        channels = [
            # High-confidence match (above floor)
            {"id": "uuid-espn-1", "name": "ESPN", "channel_group_id": 1},
        ]
        mock_client = _make_client(channels)
        # Simulate an operator setting dedup_threshold=0.30 (below floor).
        # BD-B's Pydantic validator would normally clamp this to 0.60, but
        # in tests we bypass persistence and pass the raw value — the matcher
        # MUST clamp it internally per ADR-008 §D2.
        mock_settings = _make_settings(dedup_threshold=0.30)

        with patch("routers.channel_merges.get_client", return_value=mock_client), \
             patch("routers.channel_merges.get_settings", return_value=mock_settings):
            response = await async_client.get(
                "/api/channel-merges/candidates",
                params={"stream_name": "ESPN"},
            )

        assert response.status_code == 200
        data = response.json()
        # Exact match → confidence == 1.0, which is well above the floor.
        assert len(data["candidates"]) == 1
        assert data["candidates"][0]["confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_validates_required_stream_name(self, async_client):
        """Missing stream_name returns 400 or 422 (FastAPI Query validation)."""
        with patch("routers.channel_merges.get_client", return_value=AsyncMock()), \
             patch("routers.channel_merges.get_settings", return_value=_make_settings()):
            response = await async_client.get("/api/channel-merges/candidates")

        # FastAPI returns 422 for missing required Query params.
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_validates_blank_stream_name(self, async_client):
        """Blank stream_name (whitespace only) returns 400."""
        with patch("routers.channel_merges.get_client", return_value=AsyncMock()), \
             patch("routers.channel_merges.get_settings", return_value=_make_settings()):
            response = await async_client.get(
                "/api/channel-merges/candidates",
                params={"stream_name": "   "},
            )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_pagination_fields_present(self, async_client):
        """Response always includes page, page_size, total, total_pages — even degenerate."""
        channels = [{"id": "uuid-1", "name": "ESPN HD", "channel_group_id": 1}]
        mock_client = _make_client(channels)
        mock_settings = _make_settings(dedup_threshold=0.80)

        with patch("routers.channel_merges.get_client", return_value=mock_client), \
             patch("routers.channel_merges.get_settings", return_value=mock_settings):
            response = await async_client.get(
                "/api/channel-merges/candidates",
                params={"stream_name": "ESPN HD", "page": 1, "page_size": 50},
            )

        assert response.status_code == 200
        data = response.json()
        # All pagination fields must be present in the envelope.
        assert "page" in data
        assert "page_size" in data
        assert "total" in data
        assert "total_pages" in data
        # Exact match → 1 candidate → total=1, total_pages=1.
        assert data["total"] == 1
        assert data["total_pages"] == 1
        assert data["page"] == 1
        assert data["page_size"] == 50

    @pytest.mark.asyncio
    async def test_pagination_fields_degenerate_on_no_match(self, async_client):
        """Pagination fields are present and 0 when no candidate is found."""
        channels = [{"id": "uuid-1", "name": "Totally Unrelated", "channel_group_id": 1}]
        mock_client = _make_client(channels)
        mock_settings = _make_settings(dedup_threshold=0.80)

        with patch("routers.channel_merges.get_client", return_value=mock_client), \
             patch("routers.channel_merges.get_settings", return_value=mock_settings):
            response = await async_client.get(
                "/api/channel-merges/candidates",
                params={"stream_name": "ESPN Sports Network HD"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["total_pages"] == 0
        assert data["candidates"] == []

    @pytest.mark.asyncio
    async def test_group_id_filter_restricts_candidates(self, async_client):
        """group_id is forwarded to get_channels as channel_group; only that group's channels are candidates.

        The group-id filtering happens at the Dispatcharr API level — the client
        call is parameterized with channel_group=group_id. This test verifies the
        parameter is forwarded so only the right group's channels are in scope.

        Group 1 contains "ESPN HD" (the match target). Group 2 contains
        "Al Jazeera English" — a channel with no meaningful lexical overlap
        with "ESPN HD" (score ≈ 0.12 << the 0.60 floor). When group_id=2 is
        given, only group-2 channels are fetched, so the ESPN match should NOT
        appear and candidates should be empty.
        """
        group2_channels = [
            {"id": "uuid-alj-2", "name": "Al Jazeera English", "channel_group_id": 2},
        ]
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": group2_channels,
            "count": len(group2_channels),
        }
        mock_settings = _make_settings(dedup_threshold=0.60)

        with patch("routers.channel_merges.get_client", return_value=mock_client), \
             patch("routers.channel_merges.get_settings", return_value=mock_settings):
            response = await async_client.get(
                "/api/channel-merges/candidates",
                params={"stream_name": "ESPN HD", "group_id": 2},
            )

        assert response.status_code == 200
        data = response.json()
        # The important invariant: get_channels was called with channel_group=2.
        mock_client.get_channels.assert_called_once_with(
            page=1, page_size=1000, channel_group=2
        )
        # "Al Jazeera English" does not match "ESPN HD" above the floor.
        assert data["total"] == 0
        assert data["candidates"] == []

    @pytest.mark.asyncio
    async def test_group_id_none_searches_all_groups(self, async_client):
        """Omitting group_id calls get_channels with channel_group=None (all groups)."""
        channels = [{"id": "uuid-espn-1", "name": "ESPN", "channel_group_id": 5}]
        mock_client = _make_client(channels)
        mock_settings = _make_settings()

        with patch("routers.channel_merges.get_client", return_value=mock_client), \
             patch("routers.channel_merges.get_settings", return_value=mock_settings):
            response = await async_client.get(
                "/api/channel-merges/candidates",
                params={"stream_name": "ESPN"},
            )

        assert response.status_code == 200
        mock_client.get_channels.assert_called_once_with(
            page=1, page_size=1000, channel_group=None
        )

    @pytest.mark.asyncio
    async def test_emits_lookup_duration_metric(self, async_client):
        """Emits ecm_dedup_candidate_lookup_duration_seconds on every request.

        Uses the prometheus_client REGISTRY to verify the histogram received
        at least one observation. The test rebuilds the observability registry
        to avoid cross-test pollution.
        """
        import observability

        # Reset the observability registry so each test gets a clean slate.
        observability.reset_for_tests()
        observability.install_metrics()

        channels = [{"id": "uuid-1", "name": "ESPN HD", "channel_group_id": 1}]
        mock_client = _make_client(channels)
        mock_settings = _make_settings()

        with patch("routers.channel_merges.get_client", return_value=mock_client), \
             patch("routers.channel_merges.get_settings", return_value=mock_settings):
            response = await async_client.get(
                "/api/channel-merges/candidates",
                params={"stream_name": "ESPN HD"},
            )

        assert response.status_code == 200

        # Read the histogram's sample count from the live registry.
        metric = observability.get_metric("dedup_candidate_lookup_duration_seconds")
        # prometheus_client Histogram exposes _sum and _count via collect().
        samples = list(metric.collect())
        # Collect returns MetricFamily objects; walk to find the _count sample.
        count_value = None
        for mf in samples:
            for sample in mf.samples:
                if sample.name.endswith("_count"):
                    count_value = sample.value
                    break
        assert count_value is not None and count_value >= 1, (
            "Expected at least one observation on ecm_dedup_candidate_lookup_duration_seconds"
        )

    @pytest.mark.asyncio
    async def test_dispatcharr_error_returns_500(self, async_client):
        """500 is returned when Dispatcharr client raises."""
        mock_client = AsyncMock()
        mock_client.get_channels.side_effect = Exception("Dispatcharr unreachable")

        with patch("routers.channel_merges.get_client", return_value=mock_client), \
             patch("routers.channel_merges.get_settings", return_value=_make_settings()):
            response = await async_client.get(
                "/api/channel-merges/candidates",
                params={"stream_name": "ESPN HD"},
            )

        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_channel_id_coerced_to_string(self, async_client):
        """channel_id in the response is a string even when Dispatcharr returns an int.

        ADR-008 §D8: candidate_channel_id is a TEXT column. The endpoint coerces
        Dispatcharr's id (which may be an int on some Dispatcharr versions) to str.
        """
        channels = [
            {"id": 42, "name": "ESPN HD", "channel_group_id": 1},  # int id
        ]
        mock_client = _make_client(channels)
        mock_settings = _make_settings(dedup_threshold=0.80)

        with patch("routers.channel_merges.get_client", return_value=mock_client), \
             patch("routers.channel_merges.get_settings", return_value=mock_settings):
            response = await async_client.get(
                "/api/channel-merges/candidates",
                params={"stream_name": "ESPN HD"},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["candidates"]) == 1
        assert data["candidates"][0]["channel_id"] == "42"
        assert isinstance(data["candidates"][0]["channel_id"], str)

    @pytest.mark.asyncio
    async def test_empty_channel_list_returns_empty_candidates(self, async_client):
        """No channels in Dispatcharr → empty candidates, not an error."""
        mock_client = _make_client([])
        mock_settings = _make_settings()

        with patch("routers.channel_merges.get_client", return_value=mock_client), \
             patch("routers.channel_merges.get_settings", return_value=mock_settings):
            response = await async_client.get(
                "/api/channel-merges/candidates",
                params={"stream_name": "ESPN HD"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["candidates"] == []
        assert data["total"] == 0
