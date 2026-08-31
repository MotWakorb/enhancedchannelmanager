from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smart_sort_evaluator import PointRule
from stream_prober import StreamProber
from tasks.stream_probe import StreamProbeTask


@pytest.mark.parametrize("strategy", ["priority", "points"])
@pytest.mark.asyncio
async def test_scheduled_probe_uses_resolved_smart_sort_configuration(strategy):
    point_rules = (
        (PointRule("resolution", "gte", 1080, 10),)
        if strategy == "points"
        else ()
    )
    client = AsyncMock()
    client.get_channels.return_value = {
        "results": [{"id": 1, "name": "Scheduled", "streams": [20, 10]}],
        "next": None,
    }
    client.get_channel.return_value = {"streams": [20, 10]}
    prober = StreamProber(
        client=client,
        stream_sort_strategy=strategy,
        stream_sort_point_rules=point_rules,
    )
    stats = [
        SimpleNamespace(
            stream_id=20,
            stream_name="720p",
            probe_status="success",
            resolution="1280x720",
            bitrate=3_000_000,
            is_black_screen=False,
            is_low_fps=False,
        ),
        SimpleNamespace(
            stream_id=10,
            stream_name="1080p",
            probe_status="success",
            resolution="1920x1080",
            bitrate=5_000_000,
            is_black_screen=False,
            is_low_fps=False,
        ),
    ]
    session = MagicMock()
    session.__enter__.return_value.query.return_value.filter.return_value.all.return_value = stats
    scheduled_reordered = None

    async def run_scheduled_sort(**_kwargs):
        nonlocal scheduled_reordered
        scheduled_reordered = await prober._auto_reorder_channels()
        return {"status": "completed"}

    prober.probe_all_streams = AsyncMock(side_effect=run_scheduled_sort)
    task = StreamProbeTask()
    task.set_prober(prober)

    with patch("stream_prober.get_session", return_value=session), patch(
        "stream_prober.journal.log_entry"
    ):
        result = await task.execute()

    assert result.success is True
    assert scheduled_reordered[0]["channel_id"] == 1
    client.update_channel.assert_awaited_once_with(1, {"streams": [10, 20]})
    assert prober.stream_sort_strategy == strategy
    assert prober.stream_sort_point_rules == point_rules
    prober.probe_all_streams.assert_awaited_once_with(
        channel_groups_override=None,
        skip_m3u_refresh=False,
        start_send_alerts=True,
    )
