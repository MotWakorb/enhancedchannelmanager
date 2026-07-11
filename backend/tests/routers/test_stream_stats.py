"""
Unit tests for stream-stats endpoints.

Tests: 19 endpoints covering stream probe stats, probe operations,
       dismiss/clear, struck-out streams, compute-sort, and probe lifecycle.
Mocks: StreamProber, get_prober(), get_client(), get_settings(), get_session().
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from models import StreamStats
from tests.conftest import closing_create_task_mock


def _create_stream_stats(session, stream_id, **overrides):
    """Helper to create a StreamStats row."""
    defaults = {
        "stream_id": stream_id,
        "stream_name": f"Stream {stream_id}",
        "probe_status": "success",
        "consecutive_failures": 0,
        "created_at": datetime.utcnow(),
    }
    defaults.update(overrides)
    stats = StreamStats(**defaults)
    session.add(stats)
    session.commit()
    session.refresh(stats)
    return stats


class TestGetAllStreamStats:
    """Tests for GET /api/stream-stats."""

    @pytest.mark.asyncio
    async def test_returns_stats(self, async_client):
        """Returns all stream stats."""
        with patch("routers.stream_stats.StreamProber.get_all_stats", return_value={"streams": []}):
            response = await async_client.get("/api/stream-stats")

        assert response.status_code == 200
        assert response.json()["streams"] == []

    @pytest.mark.asyncio
    async def test_returns_500_on_error(self, async_client):
        """Returns 500 when StreamProber raises."""
        with patch("routers.stream_stats.StreamProber.get_all_stats", side_effect=Exception("DB error")):
            response = await async_client.get("/api/stream-stats")

        assert response.status_code == 500


class TestGetStreamStatsSummary:
    """Tests for GET /api/stream-stats/summary."""

    @pytest.mark.asyncio
    async def test_returns_summary(self, async_client):
        """Returns stats summary."""
        with patch("routers.stream_stats.StreamProber.get_stats_summary", return_value={
            "total": 100, "success": 80, "failed": 15, "pending": 5,
        }):
            response = await async_client.get("/api/stream-stats/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 100
        assert data["success"] == 80


class TestGetStruckOutStreams:
    """Tests for GET /api/stream-stats/struck-out."""

    @pytest.mark.asyncio
    async def test_disabled_when_threshold_zero(self, async_client):
        """Returns empty list and enabled=False when threshold is 0."""
        mock_settings = MagicMock()
        mock_settings.strike_threshold = 0

        with patch("routers.stream_stats.get_settings", return_value=mock_settings):
            response = await async_client.get("/api/stream-stats/struck-out")

        assert response.status_code == 200
        data = response.json()
        assert data["streams"] == []
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_returns_struck_out_with_channels(self, async_client, test_session):
        """Returns struck-out streams with channel info."""
        _create_stream_stats(test_session, 10, consecutive_failures=5)

        mock_settings = MagicMock()
        mock_settings.strike_threshold = 3

        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 1, "name": "ESPN", "streams": [10, 20]}],
            "count": 1,
        }

        with patch("routers.stream_stats.get_settings", return_value=mock_settings), \
             patch("routers.stream_stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-stats/struck-out")

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["threshold"] == 3
        assert len(data["streams"]) == 1
        assert data["streams"][0]["channels"][0]["name"] == "ESPN"

    @pytest.mark.asyncio
    async def test_returns_empty_when_none_struck(self, async_client, test_session):
        """Returns empty list when no streams exceed threshold."""
        _create_stream_stats(test_session, 10, consecutive_failures=1)

        mock_settings = MagicMock()
        mock_settings.strike_threshold = 5

        with patch("routers.stream_stats.get_settings", return_value=mock_settings):
            response = await async_client.get("/api/stream-stats/struck-out")

        assert response.status_code == 200
        data = response.json()
        assert data["streams"] == []
        assert data["enabled"] is True


class TestRemoveStruckOutStreams:
    """Tests for POST /api/stream-stats/struck-out/remove."""

    @pytest.mark.asyncio
    async def test_removes_from_channels(self, async_client, test_session):
        """Removes struck-out streams from channels and resets failures."""
        _create_stream_stats(test_session, 10, consecutive_failures=5)

        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [{"id": 1, "name": "ESPN", "streams": [10, 20]}],
            "count": 1,
        }
        mock_client.update_channel.return_value = {}

        with patch("routers.stream_stats.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/stream-stats/struck-out/remove",
                json={"stream_ids": [10]},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["removed_from_channels"] == 1
        mock_client.update_channel.assert_called_once_with(1, {"streams": [20]})

        # Verify failure count was reset
        test_session.expire_all()
        stats = test_session.query(StreamStats).filter_by(stream_id=10).first()
        assert stats.consecutive_failures == 0


def _mock_client_for_stale(streams=None, channels=None):
    """Build an AsyncMock client for the /stale endpoint's two upstream calls."""
    mock_client = AsyncMock()
    mock_client.get_streams.return_value = {"results": streams or [], "count": len(streams or [])}
    mock_client.get_channels.return_value = {"results": channels or [], "count": len(channels or [])}
    return mock_client


class TestGetStaleStreams:
    """Tests for GET /api/stream-stats/stale."""

    @pytest.mark.asyncio
    async def test_returns_never_probed_streams(self, async_client, test_session):
        """A stream with no last_probed is stale regardless of days."""
        _create_stream_stats(test_session, 10, last_probed=None)

        mock_client = _mock_client_for_stale(
            streams=[{"id": 10, "name": "Stream 10", "is_stale": False}],
        )

        with patch("routers.stream_stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-stats/stale")

        assert response.status_code == 200
        data = response.json()
        assert data["threshold_days"] == 7
        assert len(data["streams"]) == 1
        assert data["streams"][0]["stream_id"] == 10
        assert data["streams"][0]["reasons"] == ["not_probed_recently"]
        assert data["streams"][0]["provider_last_seen"] is None

    @pytest.mark.asyncio
    async def test_returns_old_probes_with_channels(self, async_client, test_session):
        """Streams last probed beyond the threshold are stale, with channel info."""
        _create_stream_stats(test_session, 10, last_probed=datetime.utcnow() - timedelta(days=10))

        mock_client = _mock_client_for_stale(
            streams=[{"id": 10, "name": "Stream 10", "is_stale": False}],
            channels=[{"id": 1, "name": "ESPN", "streams": [10, 20]}],
        )

        with patch("routers.stream_stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-stats/stale")

        assert response.status_code == 200
        data = response.json()
        assert len(data["streams"]) == 1
        assert data["streams"][0]["channels"][0]["name"] == "ESPN"

    @pytest.mark.asyncio
    async def test_excludes_orphaned_stream_stats_no_longer_in_provider(self, async_client, test_session):
        """A StreamStats row for a stream_id Dispatcharr no longer has at all is
        dropped from not_probed_recently — there's nothing left to re-probe, so
        it isn't an actionable stale stream (just unswept probe history for a
        deleted stream). Regression guard for a live-deployment finding: this
        project's own StreamStats table had ~4857 rows against ~2620 live
        Dispatcharr streams before this cross-check existed.
        """
        _create_stream_stats(test_session, 10, last_probed=None)  # still exists upstream
        _create_stream_stats(test_session, 999, last_probed=None)  # deleted upstream

        mock_client = _mock_client_for_stale(
            streams=[{"id": 10, "name": "Stream 10", "is_stale": False}],
        )

        with patch("routers.stream_stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-stats/stale")

        assert response.status_code == 200
        data = response.json()
        assert [s["stream_id"] for s in data["streams"]] == [10]

    @pytest.mark.asyncio
    async def test_excludes_recently_probed_streams(self, async_client, test_session):
        """Streams probed within the threshold are not stale."""
        _create_stream_stats(test_session, 10, last_probed=datetime.utcnow() - timedelta(days=1))

        mock_client = _mock_client_for_stale()

        with patch("routers.stream_stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-stats/stale")

        assert response.status_code == 200
        assert response.json()["streams"] == []

    @pytest.mark.asyncio
    async def test_respects_custom_days_param(self, async_client, test_session):
        """A custom `days` query param changes the cutoff."""
        _create_stream_stats(test_session, 10, last_probed=datetime.utcnow() - timedelta(days=2))

        mock_client = _mock_client_for_stale(
            streams=[{"id": 10, "name": "Stream 10", "is_stale": False}],
        )

        with patch("routers.stream_stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-stats/stale?days=1")

        assert response.status_code == 200
        data = response.json()
        assert data["threshold_days"] == 1
        assert len(data["streams"]) == 1

    @pytest.mark.asyncio
    async def test_rejects_non_positive_days(self, async_client):
        """Rejects a zero or negative days value."""
        response = await async_client.get("/api/stream-stats/stale?days=0")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_includes_provider_reported_stale_streams(self, async_client, test_session):
        """A stream Dispatcharr flags is_stale surfaces even if ECM probed it recently."""
        _create_stream_stats(test_session, 10, last_probed=datetime.utcnow() - timedelta(hours=1))

        mock_client = _mock_client_for_stale(
            streams=[
                {"id": 10, "name": "ESPN Feed", "is_stale": False, "last_seen": "2026-07-02T00:00:00Z"},
                {"id": 99, "name": "Orphaned Feed", "is_stale": True, "last_seen": "2026-06-01T00:00:00Z"},
            ],
        )

        with patch("routers.stream_stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-stats/stale")

        assert response.status_code == 200
        data = response.json()
        assert len(data["streams"]) == 1
        stream = data["streams"][0]
        assert stream["stream_id"] == 99
        assert stream["stream_name"] == "Orphaned Feed"
        assert stream["reasons"] == ["provider_stale"]
        assert stream["provider_last_seen"] == "2026-06-01T00:00:00Z"
        # No StreamStats row exists for stream 99 — the response shape must still
        # include last_probed (as null), not omit the key, so API consumers can
        # rely on a consistent shape regardless of which signal(s) fired.
        assert "last_probed" in stream
        assert stream["last_probed"] is None

    @pytest.mark.asyncio
    async def test_merges_both_reasons_for_same_stream(self, async_client, test_session):
        """A stream flagged by both signals reports both reasons, deduplicated."""
        _create_stream_stats(test_session, 10, last_probed=None)

        mock_client = _mock_client_for_stale(
            streams=[{"id": 10, "name": "ESPN Feed", "is_stale": True, "last_seen": "2026-06-01T00:00:00Z"}],
        )

        with patch("routers.stream_stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-stats/stale")

        assert response.status_code == 200
        data = response.json()
        assert len(data["streams"]) == 1
        stream = data["streams"][0]
        assert set(stream["reasons"]) == {"not_probed_recently", "provider_stale"}
        assert stream["provider_last_seen"] == "2026-06-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_paginates_across_multiple_provider_stream_pages(self, async_client, test_session):
        """The Dispatcharr-streams pagination loop accumulates across 2+ pages.

        Forces get_streams to return a first page whose `count` exceeds the
        number of results on that page, so the endpoint must fetch a second
        page to see the rest. Regression guard: a mock that always returns a
        single page (.return_value) can't exercise this loop's continuation
        branch at all.
        """
        _create_stream_stats(test_session, 10, last_probed=None)

        mock_client = AsyncMock()
        mock_client.get_streams.side_effect = [
            {"results": [{"id": 10, "name": "Stream 10", "is_stale": False}], "count": 2},
            {"results": [{"id": 20, "name": "Stream 20", "is_stale": True, "last_seen": "2026-06-01T00:00:00Z"}], "count": 2},
        ]
        mock_client.get_channels.return_value = {"results": [], "count": 0}

        with patch("routers.stream_stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-stats/stale")

        assert response.status_code == 200
        data = response.json()
        stream_ids = {s["stream_id"] for s in data["streams"]}
        # stream 10 comes from page 1 (not_probed_recently); stream 20's
        # is_stale flag only exists on page 2 — both must be present.
        assert stream_ids == {10, 20}
        assert mock_client.get_streams.call_count == 2
        stream_20 = next(s for s in data["streams"] if s["stream_id"] == 20)
        assert stream_20["reasons"] == ["provider_stale"]

    @pytest.mark.asyncio
    async def test_paginates_across_multiple_channel_pages(self, async_client, test_session):
        """The channels pagination loop accumulates across 2+ pages.

        Forces get_channels to return a first page whose `count` exceeds the
        number of channels on that page, so the endpoint must fetch a second
        page to find the channel association. Regression guard: a mock that
        always returns a single page can't exercise this loop's continuation
        branch, and would silently miss channel associations living on later
        pages.
        """
        _create_stream_stats(test_session, 10, last_probed=None)

        mock_client = AsyncMock()
        mock_client.get_streams.return_value = {
            "results": [{"id": 10, "name": "Stream 10", "is_stale": False}], "count": 1,
        }
        mock_client.get_channels.side_effect = [
            {"results": [{"id": 1, "name": "Channel 1", "streams": []}], "count": 2},
            {"results": [{"id": 2, "name": "Channel 2", "streams": [10]}], "count": 2},
        ]

        with patch("routers.stream_stats.get_client", return_value=mock_client):
            response = await async_client.get("/api/stream-stats/stale")

        assert response.status_code == 200
        data = response.json()
        assert mock_client.get_channels.call_count == 2
        assert len(data["streams"]) == 1
        # The channel association only exists on page 2 of get_channels.
        assert data["streams"][0]["channels"] == [{"id": 2, "name": "Channel 2"}]


class TestComputeSort:
    """Tests for POST /api/stream-stats/compute-sort."""

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_channels(self, async_client):
        """Returns empty results for empty channel list."""
        mock_settings = MagicMock()
        mock_settings.stream_sort_priority = ["resolution"]
        mock_settings.stream_sort_enabled = {"resolution": True}

        with patch("routers.stream_stats.get_settings", return_value=mock_settings):
            response = await async_client.post("/api/stream-stats/compute-sort", json={
                "channels": [],
                "mode": "smart",
            })

        assert response.status_code == 200
        assert response.json()["results"] == []

    @pytest.mark.asyncio
    async def test_rejects_invalid_mode(self, async_client):
        """Returns 400 for invalid sort mode."""
        mock_settings = MagicMock()

        with patch("routers.stream_stats.get_settings", return_value=mock_settings):
            response = await async_client.post("/api/stream-stats/compute-sort", json={
                "channels": [{"channel_id": 1, "stream_ids": [10]}],
                "mode": "invalid",
            })

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_smart_sort(self, async_client, test_session):
        """Smart sort uses server settings and returns sorted IDs."""
        _create_stream_stats(test_session, 10, resolution="1920x1080", bitrate=5000000)
        _create_stream_stats(test_session, 20, resolution="1280x720", bitrate=3000000)

        mock_settings = MagicMock()
        mock_settings.stream_sort_priority = ["resolution"]
        mock_settings.stream_sort_enabled = {"resolution": True}
        mock_settings.m3u_account_priorities = {}
        mock_settings.deprioritize_failed_streams = False

        with patch("routers.stream_stats.get_settings", return_value=mock_settings), \
             patch("stream_prober.smart_sort_streams", return_value=[10, 20]) as mock_sort:
            response = await async_client.post("/api/stream-stats/compute-sort", json={
                "channels": [{"channel_id": 1, "stream_ids": [20, 10]}],
                "mode": "smart",
            })

        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 1
        assert results[0]["channel_id"] == 1
        mock_sort.assert_called_once()


class TestGetStreamStatsById:
    """Tests for GET /api/stream-stats/{stream_id}."""

    @pytest.mark.asyncio
    async def test_returns_stats(self, async_client):
        """Returns stats for a specific stream."""
        with patch("routers.stream_stats.StreamProber.get_stats_by_stream_id", return_value={
            "stream_id": 42, "probe_status": "success",
        }):
            response = await async_client.get("/api/stream-stats/42")

        assert response.status_code == 200
        assert response.json()["stream_id"] == 42

    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(self, async_client):
        """Returns 404 when stream stats don't exist."""
        with patch("routers.stream_stats.StreamProber.get_stats_by_stream_id", return_value=None):
            response = await async_client.get("/api/stream-stats/99999")

        assert response.status_code == 404


class TestGetStreamStatsByIds:
    """Tests for POST /api/stream-stats/by-ids."""

    @pytest.mark.asyncio
    async def test_returns_bulk_stats(self, async_client):
        """Returns stats for multiple streams."""
        with patch("routers.stream_stats.StreamProber.get_stats_by_stream_ids", return_value={
            "10": {"stream_id": 10}, "20": {"stream_id": 20},
        }):
            response = await async_client.post(
                "/api/stream-stats/by-ids",
                json={"stream_ids": [10, 20]},
            )

        assert response.status_code == 200


class TestProbeBulkStreams:
    """Tests for POST /api/stream-stats/probe/bulk.

    The bulk endpoint is now ASYNC (enhancedchannelmanager-znc76.5): it starts a
    background probe of the requested stream_ids using the SAME prober
    job/progress/results-envelope machinery probe/all uses, and returns
    immediately instead of probing each stream synchronously (which 504'd at
    batches >=~3). Callers poll /probe/progress + /probe/results for the outcome.
    """

    @pytest.mark.asyncio
    async def test_starts_background_probe_without_blocking(self, async_client):
        """Returns immediately with status=started; does NOT block on N probes.

        The endpoint must NOT call probe_stream / probe_streams_by_ids inline —
        it only schedules a background task. We assert the response comes back
        before any probing happens (asyncio.create_task is patched out).
        """
        mock_prober = MagicMock()
        mock_prober._probing_in_progress = False

        with patch("routers.stream_stats.ensure_prober", return_value=mock_prober), \
             patch("asyncio.create_task", new=closing_create_task_mock()) as mock_create_task:
            response = await async_client.post(
                "/api/stream-stats/probe/bulk",
                json={"stream_ids": [10, 11, 12]},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["total"] == 3
        # A background task was scheduled rather than probing inline.
        mock_create_task.assert_called_once()
        # The endpoint itself never probed synchronously.
        mock_prober.probe_streams_by_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_concurrent_start_when_probe_running(self, async_client):
        """Honors single-probe-at-a-time: refuses to start over a running probe."""
        mock_prober = MagicMock()
        mock_prober._probing_in_progress = True

        with patch("routers.stream_stats.ensure_prober", return_value=mock_prober), \
             patch("asyncio.create_task") as mock_create_task:
            response = await async_client.post(
                "/api/stream-stats/probe/bulk",
                json={"stream_ids": [10, 11]},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "already_running"
        # Must NOT have scheduled a second probe.
        mock_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_background_task_calls_probe_streams_by_ids(self, async_client):
        """The scheduled background task drives probe_streams_by_ids with the IDs.

        We capture the coroutine handed to create_task and await it to confirm it
        delegates to the shared prober method (which owns the results envelope).
        """
        mock_prober = MagicMock()
        mock_prober._probing_in_progress = False
        mock_prober.probe_streams_by_ids = AsyncMock(
            return_value={"status": "completed", "probed": 2, "total": 2, "success": 2, "failed": 0}
        )

        captured = {}

        def fake_create_task(coro):
            captured["coro"] = coro
            return MagicMock()

        with patch("routers.stream_stats.ensure_prober", return_value=mock_prober), \
             patch("asyncio.create_task", side_effect=fake_create_task):
            response = await async_client.post(
                "/api/stream-stats/probe/bulk",
                json={"stream_ids": [10, 11]},
            )

        assert response.status_code == 200
        # Drive the captured background coroutine to completion.
        await captured["coro"]
        # start_send_alerts is the gated value for the stream_probe task; with no
        # ScheduledTask row seeded it defaults to False (info alerts opt-in, GH #462).
        mock_prober.probe_streams_by_ids.assert_awaited_once_with(
            [10, 11], start_send_alerts=False
        )

    @pytest.mark.asyncio
    async def test_returns_503_when_prober_unavailable(self, async_client):
        """Returns 503 when prober is not available."""
        with patch("routers.stream_stats.ensure_prober", return_value=None):
            response = await async_client.post(
                "/api/stream-stats/probe/bulk",
                json={"stream_ids": [10]},
            )

        assert response.status_code == 503


class TestProbeAllStreams:
    """Tests for POST /api/stream-stats/probe/all."""

    @pytest.mark.asyncio
    async def test_starts_background_probe(self, async_client):
        """Starts a background probe task."""
        mock_prober = MagicMock()
        mock_prober._probing_in_progress = False

        with patch("routers.stream_stats.ensure_prober", return_value=mock_prober), \
             patch("asyncio.create_task", new=closing_create_task_mock()):
            response = await async_client.post("/api/stream-stats/probe/all")

        assert response.status_code == 200
        assert response.json()["status"] == "started"

    @pytest.mark.asyncio
    async def test_resets_stuck_probe(self, async_client):
        """Resets stuck probe state before starting new one."""
        mock_prober = MagicMock()
        mock_prober._probing_in_progress = True

        with patch("routers.stream_stats.ensure_prober", return_value=mock_prober), \
             patch("asyncio.create_task", new=closing_create_task_mock()):
            response = await async_client.post("/api/stream-stats/probe/all")

        assert response.status_code == 200
        mock_prober.force_reset_probe_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_503_when_prober_unavailable(self, async_client):
        """Returns 503 when prober is not available."""
        with patch("routers.stream_stats.ensure_prober", return_value=None):
            response = await async_client.post("/api/stream-stats/probe/all")

        assert response.status_code == 503


class TestGetProbeProgress:
    """Tests for GET /api/stream-stats/probe/progress."""

    @pytest.mark.asyncio
    async def test_returns_progress(self, async_client):
        """Returns current probe progress."""
        mock_prober = MagicMock()
        mock_prober.get_probe_progress.return_value = {
            "in_progress": True, "total": 100, "completed": 50,
        }

        with patch("routers.stream_stats.ensure_prober", return_value=mock_prober):
            response = await async_client.get("/api/stream-stats/probe/progress")

        assert response.status_code == 200
        assert response.json()["completed"] == 50

    @pytest.mark.asyncio
    async def test_returns_503_when_prober_unavailable(self, async_client):
        """Returns 503 when prober is not available."""
        with patch("routers.stream_stats.ensure_prober", return_value=None):
            response = await async_client.get("/api/stream-stats/probe/progress")

        assert response.status_code == 503


class TestGetProbeResults:
    """Tests for GET /api/stream-stats/probe/results."""

    @pytest.mark.asyncio
    async def test_returns_results(self, async_client):
        """Returns last probe results forwarded verbatim from prober."""
        mock_prober = MagicMock()
        mock_prober.get_probe_results.return_value = {"results": [{"id": 1}], "summary": {"total": 1}}

        with patch("routers.stream_stats.ensure_prober", return_value=mock_prober):
            response = await async_client.get("/api/stream-stats/probe/results")

        assert response.status_code == 200
        assert response.json() == {"results": [{"id": 1}], "summary": {"total": 1}}
        mock_prober.get_probe_results.assert_called_once()


class TestGetProbeHistory:
    """Tests for GET /api/stream-stats/probe/history."""

    @pytest.mark.asyncio
    async def test_returns_history(self, async_client):
        """Returns probe run history forwarded verbatim from prober."""
        mock_prober = MagicMock()
        mock_prober.get_probe_history.return_value = [
            {"run_id": 1, "started_at": "2024-01-01T00:00:00Z"},
        ]

        with patch("routers.stream_stats.ensure_prober", return_value=mock_prober):
            response = await async_client.get("/api/stream-stats/probe/history")

        assert response.status_code == 200
        assert response.json() == [{"run_id": 1, "started_at": "2024-01-01T00:00:00Z"}]
        mock_prober.get_probe_history.assert_called_once()


class TestCancelProbe:
    """Tests for POST /api/stream-stats/probe/cancel."""

    @pytest.mark.asyncio
    async def test_cancels_probe(self, async_client):
        """Cancels an in-progress probe."""
        mock_prober = MagicMock()
        mock_prober.cancel_probe.return_value = {"status": "cancelled"}

        with patch("routers.stream_stats.ensure_prober", return_value=mock_prober):
            response = await async_client.post("/api/stream-stats/probe/cancel")

        assert response.status_code == 200
        mock_prober.cancel_probe.assert_called_once()


class TestPauseProbe:
    """Tests for POST /api/stream-stats/probe/pause."""

    @pytest.mark.asyncio
    async def test_pauses_in_progress_probe(self, async_client):
        """Pauses an in-progress probe by delegating to the prober."""
        mock_prober = MagicMock()
        mock_prober.pause_probe.return_value = {"status": "paused", "message": "Probe paused"}

        with patch("routers.stream_stats.ensure_prober", return_value=mock_prober):
            response = await async_client.post("/api/stream-stats/probe/pause")

        assert response.status_code == 200
        assert response.json() == {"status": "paused", "message": "Probe paused"}
        mock_prober.pause_probe.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_503_when_prober_unavailable(self, async_client):
        """Returns 503 when the stream prober singleton isn't initialized."""
        with patch("routers.stream_stats.ensure_prober", return_value=None):
            response = await async_client.post("/api/stream-stats/probe/pause")

        assert response.status_code == 503


class TestResumeProbe:
    """Tests for POST /api/stream-stats/probe/resume."""

    @pytest.mark.asyncio
    async def test_resumes_paused_probe(self, async_client):
        """Resumes a paused probe by delegating to the prober."""
        mock_prober = MagicMock()
        mock_prober.resume_probe.return_value = {"status": "resumed", "message": "Probe resumed"}

        with patch("routers.stream_stats.ensure_prober", return_value=mock_prober):
            response = await async_client.post("/api/stream-stats/probe/resume")

        assert response.status_code == 200
        assert response.json() == {"status": "resumed", "message": "Probe resumed"}
        mock_prober.resume_probe.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_503_when_prober_unavailable(self, async_client):
        """Returns 503 when the stream prober singleton isn't initialized."""
        with patch("routers.stream_stats.ensure_prober", return_value=None):
            response = await async_client.post("/api/stream-stats/probe/resume")

        assert response.status_code == 503


class TestResetProbeState:
    """Tests for POST /api/stream-stats/probe/reset."""

    @pytest.mark.asyncio
    async def test_resets_state(self, async_client):
        """Force resets probe state."""
        mock_prober = MagicMock()
        mock_prober.force_reset_probe_state.return_value = {"status": "reset"}

        with patch("routers.stream_stats.ensure_prober", return_value=mock_prober):
            response = await async_client.post("/api/stream-stats/probe/reset")

        assert response.status_code == 200
        mock_prober.force_reset_probe_state.assert_called_once()


class TestDismissStreamStats:
    """Tests for POST /api/stream-stats/dismiss."""

    @pytest.mark.asyncio
    async def test_dismisses_failures(self, async_client, test_session):
        """Dismisses probe failures for specified streams."""
        _create_stream_stats(test_session, 10, probe_status="failed")
        _create_stream_stats(test_session, 20, probe_status="failed")

        response = await async_client.post(
            "/api/stream-stats/dismiss",
            json={"stream_ids": [10, 20]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["dismissed"] == 2

        # Verify dismissed_at was set
        test_session.expire_all()
        s = test_session.query(StreamStats).filter_by(stream_id=10).first()
        assert s.dismissed_at is not None

    @pytest.mark.asyncio
    async def test_rejects_empty_stream_ids(self, async_client):
        """Returns 400 when stream_ids is empty."""
        response = await async_client.post(
            "/api/stream-stats/dismiss",
            json={"stream_ids": []},
        )

        assert response.status_code == 400


class TestClearStreamStats:
    """Tests for POST /api/stream-stats/clear."""

    @pytest.mark.asyncio
    async def test_clears_stats(self, async_client, test_session):
        """Clears stats for specified streams."""
        _create_stream_stats(test_session, 10)
        _create_stream_stats(test_session, 20)

        response = await async_client.post(
            "/api/stream-stats/clear",
            json={"stream_ids": [10]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["cleared"] == 1

        # Verify stream 10 was deleted, 20 remains
        assert test_session.query(StreamStats).filter_by(stream_id=10).first() is None
        assert test_session.query(StreamStats).filter_by(stream_id=20).first() is not None

    @pytest.mark.asyncio
    async def test_rejects_empty_stream_ids(self, async_client):
        """Returns 400 when stream_ids is empty."""
        response = await async_client.post(
            "/api/stream-stats/clear",
            json={"stream_ids": []},
        )

        assert response.status_code == 400


class TestClearAllStreamStats:
    """Tests for POST /api/stream-stats/clear-all."""

    @pytest.mark.asyncio
    async def test_clears_all(self, async_client, test_session):
        """Clears all stream stats."""
        _create_stream_stats(test_session, 10)
        _create_stream_stats(test_session, 20)
        _create_stream_stats(test_session, 30)

        response = await async_client.post("/api/stream-stats/clear-all")

        assert response.status_code == 200
        data = response.json()
        assert data["cleared"] == 3

        assert test_session.query(StreamStats).count() == 0


class TestGetDismissedStreamStats:
    """Tests for GET /api/stream-stats/dismissed.

    NOTE: In the monolith, this route is shadowed by GET /api/stream-stats/{stream_id}
    because the parameterized route is defined first. FastAPI matches routes in definition order,
    so "dismissed" is parsed as stream_id (and fails validation).
    This will be fixed during router extraction by reordering static routes before dynamic ones.
    """

    @pytest.mark.asyncio
    async def test_dismissed_route_resolves_correctly(self, async_client, test_session):
        """Static /dismissed route resolves before /{stream_id} parameter."""
        response = await async_client.get("/api/stream-stats/dismissed")
        assert response.status_code == 200
        assert "dismissed_stream_ids" in response.json()


class TestProbeSingleStream:
    """Tests for POST /api/stream-stats/probe/{stream_id}."""

    @pytest.mark.asyncio
    async def test_probes_stream(self, async_client):
        """Probes a single stream by ID."""
        mock_prober = AsyncMock()
        mock_prober._fetch_all_streams.return_value = [
            {"id": 42, "url": "http://example.com/42", "name": "ESPN"},
        ]
        mock_prober.probe_stream.return_value = {
            "stream_id": 42, "status": "success",
        }

        with patch("routers.stream_stats.ensure_prober", return_value=mock_prober):
            response = await async_client.post("/api/stream-stats/probe/42")

        assert response.status_code == 200
        mock_prober.probe_stream.assert_called_once_with(42, "http://example.com/42", "ESPN")

    @pytest.mark.asyncio
    async def test_returns_404_when_stream_not_found(self, async_client):
        """Returns 404 when stream doesn't exist."""
        mock_prober = AsyncMock()
        mock_prober._fetch_all_streams.return_value = []

        with patch("routers.stream_stats.ensure_prober", return_value=mock_prober):
            response = await async_client.post("/api/stream-stats/probe/99999")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_503_when_prober_unavailable(self, async_client):
        """Returns 503 when prober is not available."""
        with patch("routers.stream_stats.ensure_prober", return_value=None):
            response = await async_client.post("/api/stream-stats/probe/42")

        assert response.status_code == 503
