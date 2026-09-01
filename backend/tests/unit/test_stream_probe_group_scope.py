from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from stream_prober import StreamProber
from tasks.stream_probe import StreamProbeTask


def _task_prober(current_groups):
    return SimpleNamespace(
        _probing_in_progress=False,
        _probe_progress_total=0,
        _probe_progress_current=0,
        _probe_progress_status="completed",
        _probe_progress_current_stream="",
        _probe_progress_success_count=0,
        _probe_progress_failed_count=0,
        _probe_progress_skipped_count=0,
        _probe_progress_black_screen_count=0,
        _probe_progress_low_fps_count=0,
        _probe_success_streams=[],
        _probe_failed_streams=[],
        probe_timeout=30,
        max_concurrent_probes=3,
        client=SimpleNamespace(
            get_channel_groups=AsyncMock(return_value=current_groups)
        ),
        probe_all_streams=AsyncMock(return_value={"status": "completed"}),
        cancel_probe=MagicMock(),
        _failure_breakdown=MagicMock(return_value=[]),
    )


@pytest.mark.parametrize(
    ("parameters", "expected_groups"),
    [
        pytest.param({}, None, id="unconfigured-probes-all"),
        pytest.param({"channel_groups": []}, [], id="explicit-empty-probes-none"),
        pytest.param({"channel_groups": [999]}, [], id="all-stale-probes-none"),
        pytest.param(
            {"channel_groups": [7, 999]},
            ["Sports"],
            id="mixed-probes-valid-only",
        ),
    ],
)
@pytest.mark.asyncio
async def test_scheduled_probe_preserves_resolved_group_scope(
    parameters, expected_groups
):
    prober = _task_prober([{"id": 7, "name": "Sports"}])
    task = StreamProbeTask()
    task.set_prober(prober)
    task.update_config(parameters)

    result = await task.execute()

    assert result.success is True
    prober.probe_all_streams.assert_awaited_once_with(
        channel_groups_override=expected_groups,
        skip_m3u_refresh=False,
        start_send_alerts=True,
        allow_reorder_after_probe=True,
    )


@pytest.mark.parametrize(
    ("groups", "expected_stream_ids", "expected_channel_fetches"),
    [
        pytest.param(None, {10, 20}, 1, id="unconfigured-probes-all"),
        pytest.param([], set(), 0, id="explicit-empty-probes-none"),
        pytest.param(["Deleted"], set(), 0, id="all-stale-probes-none"),
        pytest.param(
            ["Sports", "Deleted"], {10}, 1, id="mixed-probes-valid-only"
        ),
    ],
)
@pytest.mark.asyncio
async def test_prober_filters_channel_streams_to_resolved_group_scope(
    groups, expected_stream_ids, expected_channel_fetches
):
    client = AsyncMock()
    client.get_channel_groups.return_value = [
        {"id": 7, "name": "Sports"},
        {"id": 8, "name": "News"},
    ]
    client.get_channels.return_value = {
        "results": [
            {
                "id": 1,
                "name": "Sports One",
                "channel_group_id": 7,
                "channel_number": 1,
                "streams": [10],
            },
            {
                "id": 2,
                "name": "News One",
                "channel_group_id": 8,
                "channel_number": 2,
                "streams": [20],
            },
        ],
        "next": None,
    }
    prober = StreamProber(client=client)

    stream_ids, _, _ = await prober._fetch_channel_stream_ids(groups)

    assert stream_ids == expected_stream_ids
    assert client.get_channels.await_count == expected_channel_fetches


@pytest.mark.asyncio
async def test_explicit_empty_probe_completes_without_metadata_or_reorder_work():
    client = AsyncMock()
    prober = StreamProber(client=client, auto_reorder_after_probe=True)
    prober._persist_probe_history = lambda: None

    with patch.object(
        prober,
        "_fetch_channel_stream_ids",
        AsyncMock(return_value=(set(), {}, {})),
    ) as fetch_channel_stream_ids, patch.object(
        prober, "_fetch_all_streams", AsyncMock(return_value=[])
    ) as fetch_all_streams, patch.object(
        prober, "_create_probe_notification", AsyncMock()
    ) as create_notification, patch.object(
        prober, "_finalize_probe_notification", AsyncMock()
    ), patch.object(
        prober, "_auto_reorder_channels", AsyncMock()
    ) as reorder, patch.object(
        prober, "_save_probe_history", MagicMock()
    ) as save_history:
        result = await prober.probe_all_streams(channel_groups_override=[])

    assert result == {"status": "completed", "probed": 0, "reordered_channels": 0}
    fetch_channel_stream_ids.assert_not_awaited()
    fetch_all_streams.assert_not_awaited()
    create_notification.assert_not_awaited()
    reorder.assert_not_awaited()
    save_history.assert_called_once()
    client.refresh_all_m3u_accounts.assert_not_awaited()


@pytest.mark.parametrize(
    ("groups", "expected_channel_ids", "expected_channel_fetches"),
    [
        pytest.param([], [], 0, id="explicit-empty-reorders-none"),
        pytest.param(["Deleted"], [], 0, id="all-stale-reorders-none"),
        pytest.param(
            ["Sports", "Deleted"], [1], 1, id="mixed-reorders-valid-only"
        ),
    ],
)
@pytest.mark.asyncio
async def test_reorder_scope_never_widens_past_resolved_groups(
    groups, expected_channel_ids, expected_channel_fetches
):
    client = AsyncMock()
    client.get_channel_groups.return_value = [
        {"id": 7, "name": "Sports"},
        {"id": 8, "name": "News"},
    ]
    client.get_channels.return_value = {
        "results": [
            {"id": 1, "name": "Sports One", "channel_group_id": 7},
            {"id": 2, "name": "News One", "channel_group_id": 8},
        ],
        "next": None,
    }
    client.get_channel.side_effect = lambda channel_id: {"streams": [channel_id]}
    prober = StreamProber(client=client)

    result = await prober._auto_reorder_channels(groups)

    assert result == []
    assert client.get_channel.await_args_list == [
        call(channel_id) for channel_id in expected_channel_ids
    ]
    assert client.get_channels.await_count == expected_channel_fetches
