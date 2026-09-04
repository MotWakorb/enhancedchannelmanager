from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from stream_prober import StreamProber
from models import StreamStats
from tasks.failed_stream_reprobe import FailedStreamReprobeTask
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
    ("parameters", "expected_groups", "expected_group_ids"),
    [
        pytest.param({}, None, None, id="unconfigured-probes-all"),
        pytest.param({"channel_groups": []}, None, frozenset(), id="explicit-empty-probes-none"),
        pytest.param({"channel_groups": [999]}, None, frozenset({999}), id="all-stale-probes-none"),
        pytest.param(
            {"channel_groups": [7, 999]},
            None,
            frozenset({7, 999}),
            id="mixed-probes-valid-only",
        ),
    ],
)
@pytest.mark.asyncio
async def test_scheduled_probe_preserves_resolved_group_scope(
    parameters, expected_groups, expected_group_ids
):
    prober = _task_prober([{"id": 7, "name": "Sports"}])
    task = StreamProbeTask()
    task.set_prober(prober)
    task.update_config(parameters)

    result = await task.execute()

    assert result.success is True
    prober.probe_all_streams.assert_awaited_once_with(
        channel_groups_override=expected_groups,
        channel_group_ids_override=expected_group_ids,
        skip_m3u_refresh=False,
        start_send_alerts=True,
        completion_send_alerts=False,
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


@pytest.mark.asyncio
async def test_scheduled_group_lookup_failure_fails_task_and_probe_history():
    client = AsyncMock()
    client.get_channel_groups.side_effect = RuntimeError("groups unavailable")
    prober = StreamProber(client=client, refresh_m3us_before_probe=True)
    prober._persist_probe_history = lambda: None
    prober._last_probe_scope_kind = "scoped"
    prober._last_probe_channel_stream_ids = {88}
    task = StreamProbeTask()
    task.set_prober(prober)
    task.update_config({"channel_groups": [7]})

    with patch.object(
        prober, "_fetch_all_streams", AsyncMock()
    ) as fetch_all_streams, patch.object(
        prober, "_create_probe_notification", AsyncMock()
    ) as create_notification, patch.object(
        prober, "_auto_reorder_channels", AsyncMock()
    ) as reorder:
        result = await task.execute()

    assert result.success is False
    assert "groups unavailable" in result.error
    assert prober._probe_progress_status == "failed"
    assert prober._probe_progress_current_stream == ""
    assert prober.get_probe_history()[0]["status"] == "failed"
    assert "groups unavailable" in prober.get_probe_history()[0]["error"]
    assert prober._last_probe_scope_kind == "scoped"
    assert prober._last_probe_channel_stream_ids == {88}
    client.refresh_all_m3u_accounts.assert_not_awaited()
    fetch_all_streams.assert_not_awaited()
    create_notification.assert_not_awaited()
    reorder.assert_not_awaited()


@pytest.mark.parametrize("failure_page", [1, 2])
@pytest.mark.asyncio
async def test_channel_pagination_failure_fails_without_writes_and_preserves_scope(
    failure_page,
):
    client = AsyncMock()
    client.get_channel_groups.return_value = [{"id": 7, "name": "Sports"}]
    first_page = {
        "results": [
            {
                "id": 1,
                "name": "Sports One",
                "channel_group_id": 7,
                "streams": [10],
            }
        ],
        "next": "page-2",
    }
    client.get_channels.side_effect = (
        RuntimeError("channels unavailable")
        if failure_page == 1
        else [first_page, RuntimeError("channels unavailable")]
    )
    prober = StreamProber(client=client, auto_reorder_after_probe=True)
    prober._persist_probe_history = lambda: None
    prober._last_probe_scope_kind = "scoped"
    prober._last_probe_channel_stream_ids = {88}
    task = StreamProbeTask()
    task.set_prober(prober)
    task.update_config({"channel_groups": [7]})

    with patch.object(
        prober, "_fetch_all_streams", AsyncMock()
    ) as fetch_all_streams, patch.object(
        prober, "_create_probe_notification", AsyncMock()
    ) as create_notification, patch.object(
        prober, "_auto_reorder_channels", AsyncMock()
    ) as reorder:
        result = await task.execute()

    assert result.success is False
    assert "channels unavailable" in result.error
    assert prober._probe_progress_status == "failed"
    assert prober.get_probe_history()[0]["status"] == "failed"
    assert prober._last_probe_scope_kind == "scoped"
    assert prober._last_probe_channel_stream_ids == {88}
    fetch_all_streams.assert_not_awaited()
    create_notification.assert_not_awaited()
    reorder.assert_not_awaited()
    client.update_channel.assert_not_awaited()


@pytest.mark.parametrize("failure_page", [1, 2])
@pytest.mark.asyncio
async def test_reorder_pagination_failure_fails_without_writes_and_preserves_scope(
    failure_page,
):
    client = AsyncMock()
    client.get_channel_groups.return_value = [{"id": 7, "name": "Sports"}]
    probe_page = {
        "results": [
            {
                "id": 1,
                "name": "Sports One",
                "channel_group_id": 7,
                "streams": [10],
            }
        ],
        "next": None,
    }
    reorder_page = {
        "results": [
            {"id": 1, "name": "Sports One", "channel_group_id": 7}
        ],
        "next": "page-2",
    }
    client.get_channels.side_effect = (
        [probe_page, RuntimeError("reorder channels unavailable")]
        if failure_page == 1
        else [
            probe_page,
            reorder_page,
            RuntimeError("reorder channels unavailable"),
        ]
    )
    client.get_m3u_accounts.return_value = []
    prober = StreamProber(
        client=client,
        auto_reorder_after_probe=True,
        refresh_m3us_before_probe=False,
    )
    prober._persist_probe_history = lambda: None
    prober._last_probe_scope_kind = "scoped"
    prober._last_probe_channel_stream_ids = {88}
    task = StreamProbeTask()
    task.set_prober(prober)
    task.update_config({"channel_groups": [7]})

    with patch.object(
        prober, "_fetch_all_streams", AsyncMock(return_value=[])
    ), patch.object(
        prober, "_create_probe_notification", AsyncMock()
    ), patch.object(
        prober, "_finalize_probe_notification", AsyncMock()
    ):
        result = await task.execute()

    assert result.success is False
    assert "reorder channels unavailable" in result.error
    assert prober._probe_progress_status == "failed"
    assert prober.get_probe_history()[0]["status"] == "failed"
    assert prober._last_probe_scope_kind == "scoped"
    assert prober._last_probe_channel_stream_ids == {88}
    client.get_channel.assert_not_awaited()
    client.update_channel.assert_not_awaited()


@pytest.mark.parametrize("configured_ids", [[], [999]])
@pytest.mark.asyncio
async def test_full_scheduled_zero_scope_completes_before_refresh_or_metadata(
    configured_ids,
):
    client = AsyncMock()
    client.get_channel_groups.return_value = [{"id": 7, "name": "Sports"}]
    prober = StreamProber(client=client, refresh_m3us_before_probe=True)
    prober._persist_probe_history = lambda: None
    task = StreamProbeTask()
    task.set_prober(prober)
    task.update_config({"channel_groups": configured_ids})

    with patch.object(
        prober, "_fetch_all_streams", AsyncMock()
    ) as fetch_all_streams, patch.object(
        prober, "_create_probe_notification", AsyncMock()
    ) as create_notification, patch.object(
        prober, "_auto_reorder_channels", AsyncMock()
    ) as reorder:
        result = await task.execute()

    assert result.success is True
    assert result.total_items == 0
    assert prober._probe_progress_status == "completed"
    assert prober._probe_progress_current_stream == ""
    assert prober._probe_progress_total == 0
    assert prober._last_probe_scope_kind == "scoped"
    assert prober._last_probe_channel_stream_ids == set()
    assert prober.get_probe_history()[0]["status"] == "completed"
    client.refresh_all_m3u_accounts.assert_not_awaited()
    client.get_channels.assert_not_awaited()
    fetch_all_streams.assert_not_awaited()
    create_notification.assert_not_awaited()
    reorder.assert_not_awaited()


@pytest.mark.parametrize("configured_ids", [[], [999]])
@pytest.mark.asyncio
async def test_zero_scope_then_failed_reprobe_does_not_widen(
    configured_ids, test_session
):
    test_session.add(
        StreamStats(
            stream_id=40,
            stream_name="outside",
            probe_status="failed",
            consecutive_failures=1,
        )
    )
    test_session.commit()
    client = AsyncMock()
    client.get_channel_groups.return_value = [{"id": 7, "name": "Sports"}]
    prober = StreamProber(client=client)
    prober._persist_probe_history = lambda: None

    prior = await prober.probe_all_streams(
        channel_group_ids_override=frozenset(configured_ids),
        skip_m3u_refresh=True,
    )
    assert prior["status"] == "completed"
    assert prober._last_probe_scope_kind == "scoped"
    assert prober._last_probe_channel_stream_ids == set()

    prober.probe_all_streams = AsyncMock()
    task = FailedStreamReprobeTask()
    task.set_prober(prober)
    with patch("tasks.failed_stream_reprobe.get_session", return_value=test_session):
        result = await task.execute()

    assert result.success is True
    assert result.total_items == 0
    prober.probe_all_streams.assert_not_awaited()
    client.update_channel.assert_not_awaited()


@pytest.mark.parametrize(
    ("group_ids", "channels", "expected_failed_ids", "expected_kind"),
    [
        (None, [{"channel_group_id": 7, "streams": [10]}], {10, 40}, "all"),
        (frozenset({7}), [{"channel_group_id": 7, "streams": [10]}], {10}, "scoped"),
    ],
)
@pytest.mark.asyncio
async def test_successful_prior_scope_controls_failed_reprobe(
    group_ids, channels, expected_failed_ids, expected_kind, test_session
):
    for stream_id in (10, 40):
        test_session.add(
            StreamStats(
                stream_id=stream_id,
                stream_name=f"s{stream_id}",
                probe_status="failed",
                consecutive_failures=1,
            )
        )
    test_session.commit()
    client = AsyncMock()
    client.get_channel_groups.return_value = [{"id": 7, "name": "Sports"}]
    client.get_channels.return_value = {"results": channels, "next": None}
    client.get_m3u_accounts.return_value = []
    prober = StreamProber(client=client, parallel_probing_enabled=False)
    prober._persist_probe_history = lambda: None

    with patch.object(prober, "_fetch_all_streams", AsyncMock(return_value=[])), patch.object(
        prober, "_create_probe_notification", AsyncMock()
    ), patch.object(prober, "_finalize_probe_notification", AsyncMock()):
        prior = await prober.probe_all_streams(
            channel_group_ids_override=group_ids,
            skip_m3u_refresh=True,
        )

    assert prior["status"] == "completed"
    assert prober._last_probe_scope_kind == expected_kind
    prober.probe_all_streams = AsyncMock(return_value={"status": "completed"})
    task = FailedStreamReprobeTask()
    task.set_prober(prober)
    with patch("tasks.failed_stream_reprobe.get_session", return_value=test_session):
        result = await task.execute()

    assert result.success is True
    assert set(prober.probe_all_streams.await_args.kwargs["stream_ids_filter"]) == expected_failed_ids


@pytest.mark.asyncio
async def test_scheduled_numeric_ids_ignore_stale_id_without_widening_duplicate_name():
    client = AsyncMock()
    client.get_channel_groups.return_value = [
        {"id": 7, "name": "Sports"},
        {"id": 8, "name": "Sports"},
    ]
    client.get_channels.return_value = {
        "results": [
            {"id": 1, "channel_group_id": 7, "streams": [10]},
            {"id": 2, "channel_group_id": 8, "streams": [20]},
        ],
        "next": None,
    }
    client.get_m3u_accounts.return_value = []
    prober = StreamProber(client=client, auto_reorder_after_probe=True)
    prober._persist_probe_history = lambda: None

    with patch.object(
        prober,
        "_fetch_all_streams",
        AsyncMock(return_value=[]),
    ), patch.object(
        prober, "_create_probe_notification", AsyncMock()
    ), patch.object(
        prober, "_finalize_probe_notification", AsyncMock()
    ), patch.object(
        prober, "_auto_reorder_channels", AsyncMock(return_value=[])
    ) as reorder:
        result = await prober.probe_all_streams(
            channel_group_ids_override=frozenset({7, 999}),
            skip_m3u_refresh=True,
        )

    assert result["status"] == "completed"
    assert prober._last_probe_channel_stream_ids == {10}
    reorder.assert_awaited_once_with(
        stream_to_channels={10: ["Channel 1"]},
        channel_group_ids_override=frozenset({7}),
    )
    assert client.get_channel_groups.await_count == 1


@pytest.mark.asyncio
async def test_ambiguous_name_scope_fails_closed_before_refresh_or_metadata():
    client = AsyncMock()
    client.get_channel_groups.return_value = [
        {"id": 7, "name": "Sports"},
        {"id": 8, "name": "Sports"},
    ]
    prober = StreamProber(client=client, refresh_m3us_before_probe=True)
    prober._persist_probe_history = lambda: None

    with patch.object(
        prober, "_fetch_all_streams", AsyncMock()
    ) as fetch_all_streams, patch.object(
        prober, "_auto_reorder_channels", AsyncMock()
    ) as reorder:
        result = await prober.probe_all_streams(channel_groups_override=["Sports"])

    assert result["status"] == "failed"
    assert "ambiguous" in result["error"].lower()
    client.refresh_all_m3u_accounts.assert_not_awaited()
    client.get_channels.assert_not_awaited()
    fetch_all_streams.assert_not_awaited()
    reorder.assert_not_awaited()


@pytest.mark.asyncio
async def test_name_scope_does_not_substitute_replacement_created_after_resolution():
    client = AsyncMock()
    client.get_channel_groups.return_value = [{"id": 7, "name": "Sports"}]
    client.get_channels.return_value = {
        "results": [
            {"id": 2, "name": "Replacement", "channel_group_id": 8, "streams": [20]}
        ],
        "next": None,
    }
    prober = StreamProber(client=client, refresh_m3us_before_probe=True)
    prober._persist_probe_history = lambda: None

    with patch.object(
        prober, "_fetch_all_streams", AsyncMock()
    ) as fetch_all_streams, patch.object(
        prober, "_create_probe_notification", AsyncMock()
    ) as create_notification, patch.object(
        prober, "_auto_reorder_channels", AsyncMock()
    ) as reorder:
        result = await prober.probe_all_streams(
            channel_groups_override=["Sports"],
            skip_m3u_refresh=True,
        )

    assert result == {"status": "completed", "probed": 0, "reordered_channels": 0}
    assert prober._last_probe_channel_stream_ids == set()
    assert client.get_channel_groups.await_count == 1
    fetch_all_streams.assert_not_awaited()
    create_notification.assert_not_awaited()
    reorder.assert_not_awaited()
