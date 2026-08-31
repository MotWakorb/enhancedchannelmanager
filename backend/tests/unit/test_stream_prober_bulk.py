"""Unit tests for StreamProber.probe_streams_by_ids — async bulk probe.

Covers the redesign tracked in enhancedchannelmanager-znc76.5: the on-demand
bulk probe must run through the SAME progress / results / history envelope as
probe_all_streams, so a manual bulk run shows up in get_probe_progress /
get_probe_results just like a scheduled probe-all run (the "manual probes don't
feed the results envelope" half of the bug).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stream_prober import StreamProber
from smart_sort_evaluator import PointRule


def _make_prober(client, **kwargs):
    # Patch history persistence to disk so unit tests don't touch /config.
    prober = StreamProber(client=client, **kwargs)
    prober._persist_probe_history = lambda: None
    return prober


@pytest.mark.asyncio
async def test_bulk_run_populates_results_envelope():
    """probe_streams_by_ids writes the shared envelope that get_probe_results reads."""
    client = AsyncMock()
    prober = _make_prober(client)

    streams = [
        {"id": 10, "url": "http://example.com/10", "name": "Stream 10"},
        {"id": 11, "url": "http://example.com/11", "name": "Stream 11"},
        {"id": 12, "url": "http://example.com/12", "name": "Stream 12"},
    ]
    outcomes = {
        10: {"stream_id": 10, "probe_status": "success"},
        11: {"stream_id": 11, "probe_status": "failed", "error_message": "404"},
        12: {"stream_id": 12, "probe_status": "timeout", "error_message": "timed out"},
    }

    async def fake_probe_stream(sid, url, name):
        return outcomes[sid]

    with patch.object(prober, "_fetch_all_streams", AsyncMock(return_value=streams)), \
         patch.object(prober, "probe_stream", side_effect=fake_probe_stream):
        result = await prober.probe_streams_by_ids([10, 11, 12])

    # Envelope tallies on the return value.
    assert result["status"] == "completed"
    assert result["total"] == 3
    assert result["success"] == 1
    assert result["failed"] == 2  # failed + timeout

    # The SHARED envelope (what get_probe_results / get_probe_progress expose)
    # reflects this bulk run.
    envelope = prober.get_probe_results()
    assert envelope["success_count"] == 1
    assert envelope["failed_count"] == 2
    assert {s["id"] for s in envelope["success_streams"]} == {10}
    assert {s["id"] for s in envelope["failed_streams"]} == {11, 12}

    progress = prober.get_probe_progress()
    assert progress["in_progress"] is False
    assert progress["status"] == "completed"
    assert progress["total"] == 3
    assert progress["success_count"] == 1
    assert progress["failed_count"] == 2

    # And the run is recorded in history (shared with probe-all).
    history = prober.get_probe_history()
    assert len(history) == 1
    assert history[0]["total"] == 3
    assert history[0]["success_count"] == 1
    assert history[0]["failed_count"] == 2


@pytest.mark.asyncio
async def test_bulk_run_rejected_when_probe_in_progress():
    """Single-probe-at-a-time invariant: returns already_running, probes nothing."""
    client = AsyncMock()
    prober = _make_prober(client)
    prober._probing_in_progress = True  # Simulate a probe already running.

    probe_stream_mock = AsyncMock()
    with patch.object(prober, "_fetch_all_streams", AsyncMock()) as fetch_mock, \
         patch.object(prober, "probe_stream", probe_stream_mock):
        result = await prober.probe_streams_by_ids([10, 11])

    assert result["status"] == "already_running"
    fetch_mock.assert_not_called()
    probe_stream_mock.assert_not_called()
    # The in-progress flag must be left untouched (we did not own the run).
    assert prober._probing_in_progress is True


@pytest.mark.asyncio
async def test_bulk_run_honors_auto_reorder_setting():
    """When auto_reorder_after_probe is on, bulk probe reorders affected channels."""
    client = AsyncMock()
    prober = _make_prober(client, auto_reorder_after_probe=True)

    streams = [{"id": 10, "url": "http://example.com/10", "name": "Stream 10"}]

    async def fake_probe_stream(sid, url, name):
        return {"stream_id": sid, "probe_status": "success"}

    reorder_mock = AsyncMock(return_value=[{"channel_id": 1, "channel_name": "Ch", "stream_count": 2}])

    with patch.object(prober, "_fetch_all_streams", AsyncMock(return_value=streams)), \
         patch.object(prober, "probe_stream", side_effect=fake_probe_stream), \
         patch.object(prober, "_auto_reorder_channels_for_streams", reorder_mock):
        await prober.probe_streams_by_ids([10])

    reorder_mock.assert_awaited_once_with([10])


@pytest.mark.asyncio
async def test_bulk_run_skips_reorder_when_setting_off():
    """Default (auto_reorder_after_probe off): bulk probe does not reorder."""
    client = AsyncMock()
    prober = _make_prober(client)  # default auto_reorder_after_probe=False

    streams = [{"id": 10, "url": "http://example.com/10", "name": "Stream 10"}]

    async def fake_probe_stream(sid, url, name):
        return {"stream_id": sid, "probe_status": "success"}

    reorder_mock = AsyncMock()
    with patch.object(prober, "_fetch_all_streams", AsyncMock(return_value=streams)), \
         patch.object(prober, "probe_stream", side_effect=fake_probe_stream), \
         patch.object(prober, "_auto_reorder_channels_for_streams", reorder_mock):
        await prober.probe_streams_by_ids([10])

    reorder_mock.assert_not_called()


@pytest.mark.parametrize("strategy", ["priority", "points"])
@pytest.mark.asyncio
async def test_probe_completion_reorder_uses_configured_smart_sort(strategy):
    client = AsyncMock()
    client.get_channels.return_value = {
        "results": [{"id": 1, "name": "Channel", "streams": [20, 10]}],
        "next": None,
    }
    point_rules = (
        (PointRule("resolution", "gte", 1080, 10),)
        if strategy == "points"
        else ()
    )
    prober = _make_prober(
        client,
        stream_sort_strategy=strategy,
        stream_sort_point_rules=point_rules,
    )
    stats = [
        SimpleNamespace(
            stream_id=20,
            stream_name="720p",
            probe_status="success",
            resolution="1280x720",
        ),
        SimpleNamespace(
            stream_id=10,
            stream_name="1080p",
            probe_status="success",
            resolution="1920x1080",
        ),
    ]
    session = MagicMock()
    session.__enter__.return_value.query.return_value.filter.return_value.all.return_value = stats

    with patch("stream_prober.get_session", return_value=session), patch(
        "stream_prober.journal.log_entry"
    ):
        reordered = await prober._auto_reorder_channels_for_streams([20])

    assert reordered == [{
        "channel_id": 1,
        "channel_name": "Channel",
        "stream_count": 2,
    }]
    client.update_channel.assert_awaited_once_with(1, {"streams": [10, 20]})
    client.get_streams_by_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_priority_reorder_does_not_report_disabled_black_screen_bucket():
    client = AsyncMock()
    client.get_channels.return_value = {
        "results": [{"id": 1, "name": "Channel", "streams": [1, 2]}],
        "next": None,
    }
    prober = _make_prober(
        client,
        stream_sort_priority=["resolution"],
        stream_sort_enabled={"resolution": True},
        deprioritize_black_screen=False,
    )
    stats = [
        SimpleNamespace(
            stream_id=1,
            stream_name="Healthy 720p",
            probe_status="success",
            resolution="1280x720",
            bitrate=3_000_000,
            is_black_screen=False,
            is_low_fps=False,
        ),
        SimpleNamespace(
            stream_id=2,
            stream_name="Black screen 1080p",
            probe_status="success",
            resolution="1920x1080",
            bitrate=5_000_000,
            is_black_screen=True,
            is_low_fps=False,
        ),
    ]
    session = MagicMock()
    session.__enter__.return_value.query.return_value.filter.return_value.all.return_value = stats

    with patch("stream_prober.get_session", return_value=session), patch(
        "stream_prober.journal.log_entry"
    ) as journal_log:
        reordered = await prober._auto_reorder_channels_for_streams([1])

    assert reordered[0]["channel_id"] == 1
    client.update_channel.assert_awaited_once_with(1, {"streams": [2, 1]})
    journal_entry = journal_log.call_args.kwargs
    assert "deprioritized" not in journal_entry["description"]
    assert journal_entry["after_value"] is None


@pytest.mark.asyncio
async def test_bulk_priority_journal_reports_missing_stats_as_not_probed():
    client = AsyncMock()
    client.get_channels.return_value = {
        "results": [{"id": 1, "name": "Channel", "streams": [1, 2]}],
        "next": None,
    }
    prober = _make_prober(client)
    stats = [
        SimpleNamespace(
            stream_id=2,
            stream_name="Healthy",
            probe_status="success",
            resolution="1920x1080",
            bitrate=5_000_000,
            is_black_screen=False,
            is_low_fps=False,
        ),
    ]
    session = MagicMock()
    session.__enter__.return_value.query.return_value.filter.return_value.all.return_value = stats

    with patch("stream_prober.get_session", return_value=session), patch(
        "stream_prober.journal.log_entry"
    ) as journal_log:
        reordered = await prober._auto_reorder_channels_for_streams([1])

    assert reordered[0]["channel_id"] == 1
    client.update_channel.assert_awaited_once_with(1, {"streams": [2, 1]})
    journal_entry = journal_log.call_args.kwargs
    assert journal_entry["after_value"] == {
        "deprioritized": [
            {"id": 1, "name": "Stream 1", "reason": "not_probed"},
        ]
    }
    assert "1 not probed deprioritized" in journal_entry["description"]


@pytest.mark.asyncio
async def test_probe_completion_reorder_uses_one_settings_snapshot():
    client = AsyncMock()
    client.get_channels.return_value = {
        "results": [{"id": 1, "name": "Channel", "streams": [1, 2]}],
        "next": None,
    }
    prober = _make_prober(
        client,
        stream_sort_strategy="points",
        stream_sort_point_rules=(
            PointRule("custom_streams", "eq", True, 10),
        ),
    )

    async def fetch_metadata(_stream_ids):
        prober.update_sort_settings(
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True},
            m3u_account_priorities={},
            stream_sort_strategy="priority",
            stream_sort_point_rules=(),
        )
        return [
            {"id": 1, "is_custom": False},
            {"id": 2, "is_custom": True},
        ]

    client.get_streams_by_ids.side_effect = fetch_metadata
    stats = [
        SimpleNamespace(
            stream_id=1,
            stream_name="1080p",
            probe_status="success",
            resolution="1920x1080",
            is_black_screen=False,
            is_low_fps=False,
        ),
        SimpleNamespace(
            stream_id=2,
            stream_name="720p custom",
            probe_status="success",
            resolution="1280x720",
            is_black_screen=False,
            is_low_fps=False,
        ),
    ]
    session = MagicMock()
    session.__enter__.return_value.query.return_value.filter.return_value.all.return_value = stats

    with patch("stream_prober.get_session", return_value=session), patch(
        "stream_prober.journal.log_entry"
    ):
        await prober._auto_reorder_channels_for_streams([1])

    client.update_channel.assert_awaited_once_with(1, {"streams": [2, 1]})
    assert prober.stream_sort_strategy == "priority"


@pytest.mark.asyncio
async def test_bulk_run_counts_missing_streams_as_failed():
    """Stream IDs not present in Dispatcharr are tallied as failures, not dropped."""
    client = AsyncMock()
    prober = _make_prober(client)

    streams = [{"id": 10, "url": "http://example.com/10", "name": "Stream 10"}]

    async def fake_probe_stream(sid, url, name):
        return {"stream_id": sid, "probe_status": "success"}

    with patch.object(prober, "_fetch_all_streams", AsyncMock(return_value=streams)), \
         patch.object(prober, "probe_stream", side_effect=fake_probe_stream):
        # 99 does not exist in Dispatcharr.
        result = await prober.probe_streams_by_ids([10, 99])

    assert result["total"] == 2
    assert result["success"] == 1
    assert result["failed"] == 1
    failed = prober.get_probe_results()["failed_streams"]
    assert any(s["id"] == 99 for s in failed)
