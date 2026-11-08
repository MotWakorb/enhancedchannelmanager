"""PO-selected restriction: no media on finite or unverifiable providers.

The baseline parses the recorded 0.27.2 account. Finite/group/malformed cases
are explicit synthetic variations, not new live API captures. No policy mocks.
"""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import stream_prober
from models import StreamStats
from sqlalchemy.orm import sessionmaker

from tests.unit.test_stream_probe_capacity import prepare_probe_all
from tests.unit.test_stream_probe_capacity import (
    unlimited_probe as unlimited_probe,  # noqa: PLC0414 - pytest fixture re-export
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("vlc_stream_user_agent")]
REASON = "Probe deferred: provider capacity is limited or unverified; shared viewer coordination is unavailable"


@pytest.fixture
def policy_probe(unlimited_probe, test_engine, monkeypatch):
    prober, account, stream = unlimited_probe
    prober.refresh_m3us_before_probe = False
    prober.auto_reorder_after_probe = True
    prober._auto_reorder_channels = AsyncMock(return_value=[])
    prober._auto_reorder_channels_for_streams = AsyncMock(return_value=[])
    prober.use_resdet_for_resolution = True
    prober.black_screen_detection_enabled = True
    prober._run_resdet = AsyncMock(return_value=(640, 480))
    prober._detect_black_screen = AsyncMock(return_value=False)
    prober._last_probe_scope_kind = "scoped"
    prober._last_probe_channel_stream_ids = {99}
    sessions = sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(stream_prober, "get_session", sessions)
    monkeypatch.setattr("tasks.black_screen_scan.get_session", sessions)
    monkeypatch.setattr(stream_prober, "_prober", prober)
    with sessions() as session:
        session.add(
            StreamStats(
                stream_id=7,
                stream_name="Prior health",
                probe_status="success",
                video_bitrate=1234,
                is_black_screen=False,
            )
        )
        session.commit()
    return prober, account, stream, sessions


async def run_lane(prober, stream, lane):
    prepare_probe_all(prober, lane != "all_sequential")
    if lane == "single":
        return await prober.probe_stream(7, stream["url"])
    if lane == "bulk":
        return await prober.probe_streams_by_ids([7])
    if lane.startswith("all_"):
        return await prober.probe_all_streams(skip_m3u_refresh=True)
    if lane == "scheduled":
        from tasks.stream_probe import StreamProbeTask

        task = StreamProbeTask()
        task.set_prober(prober)
        return await task.execute()
    if lane == "black_scan":
        from tasks.black_screen_scan import BlackScreenScanTask

        task = BlackScreenScanTask()
        task.set_prober(prober)
        return await task.execute()
    from channel_pipeline_engine import ChannelPipelineEngine

    engine = ChannelPipelineEngine.__new__(ChannelPipelineEngine)
    engine._stream_stats_cache = {}
    engine._load_stream_stats = AsyncMock()
    ctx = SimpleNamespace(stream_id=7, stream_url=stream["url"], stream_name="Test")
    rule = SimpleNamespace(id=1, sort_field="quality", probe_on_sort=True)
    results = {"execution_log": [], "dry_run_results": []}
    if lane == "quality":
        await engine._probe_unprobed_streams(
            [(ctx, rule, [], [])], [rule], results, False
        )
    else:
        await engine._batch_probe_streams({7}, [ctx], results, False)
    return results


def assert_no_media_or_health_write(prober, sessions):
    for name in (
        "_run_ffprobe",
        "_run_resdet",
        "_measure_stream_bitrate",
        "_detect_black_screen",
    ):
        getattr(prober, name).assert_not_awaited()
    prober._save_probe_result.assert_not_called()
    prober._push_stats_to_dispatcharr.assert_not_awaited()
    prober._auto_reorder_channels.assert_not_awaited()
    prober._auto_reorder_channels_for_streams.assert_not_awaited()
    assert prober._last_probe_channel_stream_ids == {99}
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections
    with sessions() as session:
        saved = session.query(StreamStats).filter_by(stream_id=7).one()
        assert (
            saved.stream_name,
            saved.probe_status,
            saved.video_bitrate,
            saved.is_black_screen,
        ) == ("Prior health", "success", 1234, False)


@pytest.mark.parametrize(
    "lane",
    [
        "single",
        "bulk",
        "all_parallel",
        "all_sequential",
        "quality",
        "queued",
        "scheduled",
        "black_scan",
    ],
)
@pytest.mark.parametrize(
    "limit", ["account", "default", "alternate", "inactive_alternate"]
)
@pytest.mark.parametrize("viewers", [0, 1])
async def test_limited_provider_free_slot_never_admits_any_media(
    policy_probe, lane, limit, viewers
):
    prober, account, stream, sessions = policy_probe
    if limit == "account":
        account["max_streams"] = 2
    elif limit == "default":
        account["profiles"][0]["max_streams"] = 2
    else:
        alternate = deepcopy(account["profiles"][0])
        alternate.update(
            id=13,
            is_default=False,
            max_streams=2,
            is_active=limit != "inactive_alternate",
        )
        account["profiles"].append(alternate)
    prober.client.get_channel_stats.return_value = {
        "channels": [{"m3u_profile_id": 12} for _ in range(viewers)]
    }
    result = await run_lane(prober, stream, lane)
    assert_no_media_or_health_write(prober, sessions)
    if lane == "single":
        assert result["probe_status"] == "skipped"
        assert result["error_message"] == result["deferred_reason"] == REASON
    elif lane in ("scheduled", "black_scan"):
        assert not result.success
        assert result.error == "PROBE_DEFERRED"
        assert result.skipped_count == 1
        assert REASON in result.message
    elif lane in ("quality", "queued"):
        assert REASON in str(result["execution_log"])
    else:
        assert result["status"] == "deferred"
        assert result["deferred_reason"] == REASON
        progress = prober.get_probe_progress()
        assert progress["status"] == "deferred"
        assert progress["skipped_count"] == 1
        assert progress["deferred_reason"] == REASON
        assert prober.get_probe_history()[0]["status"] == "deferred"


@pytest.mark.parametrize(
    "fault",
    [
        "grouped_zero",
        "group_missing",
        "group_bool",
        "group_string",
        "accountless",
        "account_missing",
        "account_null_limit",
        "profile_null_limit",
        "account_bool_limit",
        "account_string_zero",
        "account_fetch_error",
        "status_fetch_error",
    ],
)
async def test_unknown_or_shared_capacity_never_becomes_unlimited(policy_probe, fault):
    prober, account, stream, sessions = policy_probe
    if fault == "grouped_zero":
        account["server_group"] = 20
    elif fault == "group_missing":
        del account["server_group"]
    elif fault in ("group_bool", "group_string"):
        account["server_group"] = True if fault == "group_bool" else "20"
    elif fault == "accountless":
        stream["m3u_account"] = None
    elif fault == "account_missing":
        del stream["m3u_account"]
    elif fault == "account_null_limit":
        account["max_streams"] = None
    elif fault == "profile_null_limit":
        account["profiles"][0]["max_streams"] = None
    elif fault == "account_bool_limit":
        account["max_streams"] = False
    elif fault == "account_string_zero":
        account["max_streams"] = "0"
    else:
        method = (
            "get_m3u_accounts"
            if fault == "account_fetch_error"
            else "get_channel_stats"
        )
        getattr(prober.client, method).side_effect = RuntimeError(
            "Disposable control failure"
        )
    result = await prober.probe_stream(7, stream["url"])
    assert result["probe_status"] == "skipped"
    assert result["deferred_reason"] == REASON
    assert_no_media_or_health_write(prober, sessions)


async def test_recorded_explicit_zero_is_unlimited_not_unknown(policy_probe):
    prober, account, stream, _ = policy_probe
    assert account["max_streams"] == account["profiles"][0]["max_streams"] == 0
    assert account["server_group"] is None
    result = await prober.probe_stream(7, stream["url"])
    assert result["probe_status"] == "success"
    for name in (
        "_run_ffprobe",
        "_run_resdet",
        "_measure_stream_bitrate",
        "_detect_black_screen",
    ):
        getattr(prober, name).assert_awaited_once()
    prober._run_ffprobe.assert_awaited_once_with(
        stream["url"], user_agent="VLC/3.0.20 LibVLC/3.0.20"
    )
    assert not prober._probe_account_connections


async def test_spare_unlimited_profile_cannot_evade_declared_account_route_limit(
    policy_probe,
):
    prober, account, stream, sessions = policy_probe
    account["profiles"][0]["max_streams"] = 2
    alternate = deepcopy(account["profiles"][0])
    alternate.update(
        id=13,
        is_default=False,
        max_streams=0,
        search_pattern="/user/password/",
        replace_pattern="/alternate/login/",
    )
    account["profiles"].append(alternate)
    stream["_selected_profile"] = alternate
    result = await prober.probe_stream(
        7, stream["url"].replace("/user/password/", "/alternate/login/"), stream=stream
    )
    assert result["probe_status"] == "skipped"
    assert result["deferred_reason"] == REASON
    assert_no_media_or_health_write(prober, sessions)


async def test_concurrent_start_and_restart_edges_do_not_open_limited_media(
    policy_probe,
):
    prober, account, stream, sessions = policy_probe
    account["max_streams"] = 2
    prepare_probe_all(prober, True)
    assert prober.schedule_probe(skip_m3u_refresh=True)["status"] == "started"
    owner = prober._probe_run_task
    assert prober.schedule_probe(stream_ids=[7])["status"] == "already_running"
    result = await prober.probe_stream(7, stream["url"])
    assert result["probe_status"] == "skipped"
    assert (await asyncio.wait_for(owner, 3))["status"] == "deferred"
    await prober.stop()
    assert prober.schedule_probe(stream_ids=[7])["status"] == "cancelled"
    assert_no_media_or_health_write(prober, sessions)


async def test_explicit_unlimited_standalone_scan_updates_only_black_screen_health(
    policy_probe,
):
    prober, _, stream, sessions = policy_probe
    prober._detect_black_screen.return_value = True
    result = await run_lane(prober, stream, "black_scan")
    assert result.success
    assert result.failed_count == 1  # Existing scan counter means black screens.
    prober._detect_black_screen.assert_awaited_once()
    prober._run_ffprobe.assert_not_awaited()
    prober._run_resdet.assert_not_awaited()
    prober._measure_stream_bitrate.assert_not_awaited()
    prober._save_probe_result.assert_not_called()
    with sessions() as session:
        saved = session.query(StreamStats).filter_by(stream_id=7).one()
        assert saved.is_black_screen is True
        assert saved.video_bitrate == 1234
        assert saved.probe_status == "success"
    assert not prober._probe_tasks
    assert not prober._probe_account_connections


async def test_failed_stream_reprobe_reports_real_policy_deferral(
    policy_probe, monkeypatch
):
    from tasks.failed_stream_reprobe import FailedStreamReprobeTask

    prober, account, _, sessions = policy_probe
    account["max_streams"] = 2
    prepare_probe_all(prober, True)
    prober._last_probe_channel_stream_ids = {7}
    monkeypatch.setattr("tasks.failed_stream_reprobe.get_session", sessions)
    with sessions() as session:
        saved = session.query(StreamStats).filter_by(stream_id=7).one()
        saved.probe_status = "failed"
        session.commit()
    task = FailedStreamReprobeTask()
    task.set_prober(prober)
    result = await task.execute()
    assert not result.success
    assert result.error == "PROBE_DEFERRED"
    assert result.skipped_count == 1
    assert result.message == REASON
    prober._run_ffprobe.assert_not_awaited()
    prober._save_probe_result.assert_not_called()
    with sessions() as session:
        assert (
            session.query(StreamStats).filter_by(stream_id=7).one().probe_status
            == "failed"
        )


async def test_epg_event_probe_reports_real_policy_deferral(policy_probe):
    from tests.unit.test_epg_event_probe import _event, _task

    prober, account, _, sessions = policy_probe
    account["max_streams"] = 2
    task, _, client = _task(
        events=[_event()],
        channels=[{"id": 42, "tvg_id": "sports.example", "streams": [7]}],
    )
    for name in ("get_epg_grid", "get_epg_data", "get_channels", "get_channel"):
        setattr(prober.client, name, getattr(client, name))
    task.set_prober(prober)
    result = await task.execute()
    assert not result.success
    assert result.error == "PROBE_DEFERRED"
    assert result.skipped_count == 1
    assert result.message == REASON
    assert_no_media_or_health_write(prober, sessions)


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("failed", [False, True])
async def test_deferral_preserves_cancel_then_aborting_failure_precedence(
    policy_probe, cancel, failed
):
    prober, _, _, _ = policy_probe
    prober._probe_progress_skipped_count = 1
    prober._probe_cancelled = cancel
    expected = "cancelled" if cancel else "failed" if failed else "deferred"
    assert prober._probe_terminal_status(failed=failed) == expected


async def test_deferred_notification_is_warning_not_success(policy_probe):
    prober, account, stream, sessions = policy_probe
    account["max_streams"] = 2
    prepare_probe_all(prober, True)
    prober._probe_notification_id = 1
    prober._notification_update_callback = AsyncMock()
    result = await prober.probe_streams_by_ids([stream["id"]])
    assert result["status"] == "deferred"
    await stream_prober.StreamProber._finalize_probe_notification(prober)
    notification = prober._notification_update_callback.call_args.kwargs
    assert notification["notification_type"] == "warning"
    assert notification["message"] == REASON
    assert notification["metadata"]["deferred_reason"] == REASON
    assert_no_media_or_health_write(prober, sessions)


async def test_cancelled_standalone_scan_retains_deferred_skip_count(policy_probe):
    from tasks.black_screen_scan import BlackScreenScanTask

    prober, account, _, sessions = policy_probe
    account["max_streams"] = 2
    prepare_probe_all(prober, True)
    task = BlackScreenScanTask()
    task.set_prober(prober)

    async def account_snapshot():
        task._cancel_requested = True
        return [account]

    prober.client.get_m3u_accounts.side_effect = account_snapshot
    result = await task.execute()
    assert result.error == "CANCELLED"
    assert result.skipped_count == 1
    assert_no_media_or_health_write(prober, sessions)


@pytest.mark.parametrize("lane", ["bulk", "all_parallel", "all_sequential"])
async def test_mixed_run_measures_only_unlimited_without_reorder_or_scope_widening(
    policy_probe, lane
):
    prober, account, stream, _ = policy_probe
    finite = deepcopy(account)
    finite.update(id=21, max_streams=2)
    finite["profiles"][0]["id"] = 22
    limited_stream = {**stream, "id": 8, "m3u_account": 21}
    prober.client.get_m3u_accounts.return_value = [account, finite]
    prober._fetch_all_streams.return_value = [stream, limited_stream]
    prepare_probe_all(prober, lane != "all_sequential")
    prober._fetch_channel_stream_ids.return_value = ({7, 8}, {}, {})

    if lane == "bulk":
        result = await prober.probe_streams_by_ids([7, 8])
    else:
        result = await prober.probe_all_streams(skip_m3u_refresh=True)

    assert result["status"] == "deferred"
    assert prober.get_probe_progress()["success_count"] == 1
    assert prober.get_probe_progress()["skipped_count"] == 1
    prober._run_ffprobe.assert_awaited_once()
    assert prober._save_probe_result.call_args.args[0] == 7
    assert prober._save_probe_result.call_count == 1
    prober._auto_reorder_channels.assert_not_awaited()
    prober._auto_reorder_channels_for_streams.assert_not_awaited()
    assert prober._last_probe_channel_stream_ids == {99}
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections
