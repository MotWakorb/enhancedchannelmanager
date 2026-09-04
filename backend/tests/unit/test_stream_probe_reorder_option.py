from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from sqlalchemy.orm import sessionmaker

import stream_prober
from models import StreamStats
from stream_prober import StreamProber
from tasks.stream_probe import StreamProbeTask


def _task_prober() -> SimpleNamespace:
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
        auto_reorder_after_probe=True,
        client=SimpleNamespace(
            get_channel_groups=AsyncMock(
                return_value=[{"id": 7, "name": "Sports"}]
            )
        ),
        probe_all_streams=AsyncMock(return_value={"status": "completed"}),
        cancel_probe=MagicMock(),
        _failure_breakdown=MagicMock(return_value=[]),
    )


def test_stream_probe_config_rejects_non_boolean_reorder_option_fail_closed():
    task = StreamProbeTask()

    with pytest.raises(
        ValueError, match="allow_reorder_after_probe must be a boolean"
    ):
        task.update_config({"allow_reorder_after_probe": "false"})

    assert task.get_config()["allow_reorder_after_probe"] is False


def test_stream_probe_config_missing_reorder_option_keeps_legacy_default():
    task = StreamProbeTask()

    task.update_config({"channel_groups": [7]})

    assert task.get_config()["allow_reorder_after_probe"] is True


@pytest.mark.asyncio
async def test_scheduled_probe_reorder_choice_is_invocation_local_and_group_scoped():
    prober = _task_prober()
    task = StreamProbeTask()
    task.set_prober(prober)

    task.update_config({
        "channel_groups": [7],
        "allow_reorder_after_probe": False,
    })
    first = await task.execute()
    second = await task.execute()

    assert first.success is True
    assert second.success is True
    assert prober.probe_all_streams.await_args_list == [
        call(
            channel_groups_override=None,
            channel_group_ids_override=frozenset({7}),
            skip_m3u_refresh=False,
            start_send_alerts=True,
            completion_send_alerts=False,
            allow_reorder_after_probe=False,
        ),
        call(
            channel_groups_override=None,
            channel_group_ids_override=None,
            skip_m3u_refresh=False,
            start_send_alerts=True,
            completion_send_alerts=False,
            allow_reorder_after_probe=True,
        ),
    ]
    assert prober.auto_reorder_after_probe is True


@pytest.mark.parametrize(
    ("global_reorder", "invocation_override", "groups", "expected_order"),
    [
        (True, None, None, [20, 10]),
        (True, False, ["Sports"], [10, 20]),
        (False, True, None, [10, 20]),
    ],
    ids=["default-follows-global", "disabled", "does-not-force-global"],
)
@pytest.mark.asyncio
async def test_probe_all_reorder_gate_preserves_metadata_and_channel_order(
    test_engine,
    monkeypatch,
    global_reorder,
    invocation_override,
    groups,
    expected_order,
):
    session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(stream_prober, "get_session", session_factory)

    client = AsyncMock()
    client.get_channel_groups.return_value = [{"id": 7, "name": "Sports"}]
    client.get_m3u_accounts.return_value = []
    channel_order = [10, 20]
    prober = StreamProber(
        client=client,
        auto_reorder_after_probe=global_reorder,
        parallel_probing_enabled=False,
    )
    prober._persist_probe_history = lambda: None

    async def reorder(*, stream_to_channels, channel_group_ids_override):
        assert stream_to_channels == {20: ["Channel"]}
        assert channel_group_ids_override == (
            frozenset({7}) if groups is not None else None
        )
        channel_order[:] = [20, 10]
        return [{"channel_id": 1}]

    ffprobe_data = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30/1",
            }
        ],
        "format": {"format_name": "mpegts"},
    }
    fetch_channel_stream_ids = AsyncMock(
        return_value=({20}, {20: ["Channel"]}, {20: 1})
    )

    kwargs = {"channel_groups_override": groups}
    if invocation_override is not None:
        kwargs["allow_reorder_after_probe"] = invocation_override

    with patch.object(
        prober, "_fetch_channel_stream_ids", fetch_channel_stream_ids
    ), patch.object(
        prober,
        "_fetch_all_streams",
        AsyncMock(
            return_value=[
                {"id": 20, "name": "1080p", "url": "http://example.test/20"}
            ]
        ),
    ), patch.object(
        prober, "_run_ffprobe", AsyncMock(return_value=ffprobe_data)
    ), patch.object(
        prober, "_measure_stream_bitrate", AsyncMock(return_value=5_000_000)
    ), patch.object(
        prober, "_push_stats_to_dispatcharr", AsyncMock()
    ), patch.object(
        prober, "_create_probe_notification", AsyncMock()
    ), patch.object(
        prober, "_update_probe_notification", AsyncMock()
    ), patch.object(
        prober, "_finalize_probe_notification", AsyncMock()
    ), patch.object(
        prober, "_auto_reorder_channels", AsyncMock(side_effect=reorder)
    ) as reorder_mock, patch(
        "stream_prober.asyncio.sleep", AsyncMock()
    ):
        result = await prober.probe_all_streams(**kwargs)

    assert result["status"] == "completed"
    fetch_channel_stream_ids.assert_awaited_once_with(
        channel_group_ids_override=(
            frozenset({7}) if groups is not None else None
        )
    )
    assert channel_order == expected_order
    if expected_order == [20, 10]:
        reorder_mock.assert_awaited_once()
    else:
        reorder_mock.assert_not_awaited()

    session = session_factory()
    try:
        stats = session.query(StreamStats).filter_by(stream_id=20).one()
        assert stats.probe_status == "success"
        assert stats.resolution == "1920x1080"
        assert stats.video_codec == "h264"
        assert stats.video_bitrate == 5_000_000
    finally:
        session.close()
