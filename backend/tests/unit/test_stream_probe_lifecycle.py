"""Production-method cancellation/rebuild regressions; no provider traffic.

Reuse the recorded-account capacity fixture. Only external control/media work,
notifications and persistence are substituted, not admission or lifecycle code.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import stream_prober
from config import DispatcharrSettings

from tests.unit.test_stream_probe_capacity import (
    URL,
    prepare_probe_all,
)
from tests.unit.test_stream_probe_capacity import (
    capacity_probe as capacity_probe,  # noqa: PLC0414 - pytest fixture re-export
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("vlc_stream_user_agent")]


@pytest.mark.parametrize("parallel", [False, True])
async def test_probe_all_api_cancel_drains_then_skips_completion_tail(
    capacity_probe, parallel
):
    prober, _, _ = capacity_probe
    prepare_probe_all(prober, parallel)
    prober.auto_reorder_after_probe = True
    prober._auto_reorder_channels = AsyncMock(return_value=[{"channel_id": 1}])
    prober._last_probe_scope_kind = "scoped"
    prober._last_probe_channel_stream_ids = {99}
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def media(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    prober._run_ffprobe.side_effect = media
    runner = asyncio.create_task(prober.probe_all_streams(skip_m3u_refresh=True))
    try:
        await asyncio.wait_for(started.wait(), 3)
        assert prober.cancel_probe()["status"] == "cancelling"
        await asyncio.wait_for(cleaning.wait(), 3)
        assert not runner.done()
        assert prober._probe_account_connections == {11: 1}
        assert prober._probing_in_progress
        prober._auto_reorder_channels.assert_not_awaited()
        assert not prober.get_probe_history()
        release.set()
        result = await asyncio.wait_for(runner, 3)
        assert result == {"status": "cancelled", "probed": 0, "reordered_channels": 0}
        progress = prober.get_probe_progress()
        assert progress["status"] == "cancelled"
        assert not progress["in_progress"]
        assert progress["current_stream"] == ""
        assert prober.get_probe_history()[0]["status"] == "cancelled"
        assert prober.get_probe_history()[0]["reordered_channels"] == []
        assert prober._last_probe_scope_kind == "scoped"
        assert prober._last_probe_channel_stream_ids == {99}
        prober._auto_reorder_channels.assert_not_awaited()
        prober._save_probe_result.assert_not_called()
        prober._push_stats_to_dispatcharr.assert_not_awaited()
        assert not prober._bulk_probe_tasks
        assert not prober._probe_tasks
        assert prober._probe_run_task is None
        assert not prober._probe_account_connections
        assert not prober._probe_profile_connections
    finally:
        release.set()
        if not runner.done():
            runner.cancel()
        await asyncio.wait_for(asyncio.gather(runner, return_exceptions=True), 3)


def probe_lane(prober, stream, lane):
    """Invoke actual single, pipeline and batch consumers against one prober."""
    if lane == "single":
        return prober.probe_stream(7, URL)
    if lane == "bulk":
        return prober.probe_streams_by_ids([7], allow_reorder_after_probe=False)
    if lane in ("sequential", "parallel"):
        prepare_probe_all(prober, lane == "parallel")
        return prober.probe_all_streams(
            skip_m3u_refresh=True, allow_reorder_after_probe=False
        )

    from channel_pipeline_engine import ChannelPipelineEngine

    engine = ChannelPipelineEngine.__new__(ChannelPipelineEngine)
    engine._stream_stats_cache = {}
    engine._load_stream_stats = AsyncMock()
    ctx = SimpleNamespace(stream_id=7, stream_url=stream["url"], stream_name="Test")
    rule = SimpleNamespace(id=1, sort_field="quality", probe_on_sort=True)
    results = {"execution_log": [], "dry_run_results": []}
    if lane == "quality":
        return engine._probe_unprobed_streams(
            [(ctx, rule, [], [])], [rule], results, False
        )
    return engine._batch_probe_streams({7}, [ctx], results, False)


@pytest.mark.parametrize("parallel", [False, True])
async def test_probe_all_cancel_after_last_measurement_does_not_publish_success(
    capacity_probe, parallel
):
    prober, _, _ = capacity_probe
    prepare_probe_all(prober, parallel)
    prober.auto_reorder_after_probe = True
    prober._auto_reorder_channels = AsyncMock(return_value=[{"channel_id": 1}])
    prober._last_probe_scope_kind = "scoped"
    prober._last_probe_channel_stream_ids = {99}

    async def cancel_after_result(*_args, **_kwargs):
        if prober._probe_progress_current == 1:
            prober.cancel_probe()

    prober._update_probe_notification.side_effect = cancel_after_result
    result = await prober.probe_all_streams(skip_m3u_refresh=True)
    assert result["status"] == "cancelled"
    assert result["probed"] == 1
    assert prober.get_probe_history()[0]["status"] == "cancelled"
    assert prober._last_probe_channel_stream_ids == {99}
    assert prober._last_probe_scope_kind == "scoped"
    prober._auto_reorder_channels.assert_not_awaited()


@pytest.fixture
def rebuild_services(capacity_probe, monkeypatch):
    """Use the real settings rebuild, real singleton and real replacement prober."""
    from routers import settings as settings_router
    from task_registry import get_registry

    prober, _, _ = capacity_probe
    monkeypatch.setattr(stream_prober, "_prober", prober)
    monkeypatch.setattr(stream_prober, "check_ffprobe_available", lambda: True)
    monkeypatch.setattr(settings_router, "_rebuild_lock", asyncio.Lock())
    monkeypatch.setattr(settings_router, "get_tracker", lambda: None)
    monkeypatch.setattr(settings_router, "set_tracker", MagicMock())
    monkeypatch.setattr(
        settings_router, "BandwidthTracker", lambda *_a, **_kw: AsyncMock()
    )
    monkeypatch.setattr(settings_router, "get_client", lambda: prober.client)
    monkeypatch.setattr(
        "channel_pipeline_engine.reset_channel_pipeline_engine", MagicMock()
    )
    monkeypatch.setattr(get_registry(), "get_task_instance", lambda _tid: None)
    settings = DispatcharrSettings(
        url="https://dispatcharr.invalid",
        auth_method="api_key",
        dispatcharr_api_key="disposable-test-key",
    )

    async def rebuild():
        return await settings_router._restart_background_services(settings)

    return rebuild


@pytest.mark.parametrize(
    "lane", ["single", "quality", "queued", "sequential", "parallel", "bulk"]
)
@pytest.mark.parametrize("interrupt_rebuild", [False, True])
async def test_settings_rebuild_drains_active_probe_and_waiter_before_replacement(
    capacity_probe, rebuild_services, lane, interrupt_rebuild
):
    prober, account, stream = capacity_probe
    account["max_streams"] = account["profiles"][0]["max_streams"] = 1
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    waiter_fetching = asyncio.Event()
    original_get_stream = prober.client.get_stream.side_effect

    async def get_stream(stream_id):
        if stream_id == 8:
            waiter_fetching.set()
        return await original_get_stream(stream_id)

    async def media(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    prober.client.get_stream.side_effect = get_stream
    prober._run_ffprobe.side_effect = media
    runner = asyncio.create_task(probe_lane(prober, stream, lane))
    waiter = rebuild = None
    locked = False
    try:
        await asyncio.wait_for(started.wait(), 3)
        await prober._probe_capacity_lock.acquire()
        locked = True
        waiter = asyncio.create_task(prober.probe_stream(8, URL))
        await asyncio.wait_for(waiter_fetching.wait(), 3)
        rebuild = asyncio.create_task(rebuild_services())
        await asyncio.wait_for(cleaning.wait(), 3)
        if interrupt_rebuild:
            rebuild.cancel()
            await asyncio.sleep(0)
            rebuild.cancel()
        await asyncio.sleep(0)
        assert not rebuild.done(), "replacement must wait for old media cleanup"
        assert stream_prober.get_prober() is prober
        assert prober._probe_account_connections == {11: 1}
        assert prober._probe_profile_connections == {12: 1}
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiter, 3)
        # A reference captured before retirement must not admit more old work.
        reads = prober.client.get_stream.await_count
        stale = await asyncio.wait_for(prober.probe_stream(9, URL), 3)
        assert stale["probe_status"] == "skipped"
        assert "stopped" in stale["error_message"]
        assert prober.client.get_stream.await_count == reads
        release.set()
        if interrupt_rebuild:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(rebuild, 3)
            assert stream_prober.get_prober() is prober
            assert (await rebuild_services())["success"]
        else:
            assert (await asyncio.wait_for(rebuild, 3))["success"]
        replacement = stream_prober.get_prober()
        assert replacement is not prober
        assert not prober._probe_account_connections
        assert not prober._probe_profile_connections
        assert not prober._bulk_probe_tasks
        assert not prober._probe_tasks
        assert prober._probe_run_task is None
        # Once cleanup completes, the replacement may use the same one slot.
        replacement._run_ffprobe = AsyncMock(return_value={"streams": []})
        replacement._measure_stream_bitrate = AsyncMock(return_value=1234)
        replacement._save_probe_result = MagicMock(
            return_value={"probe_status": "success"}
        )
        replacement._push_stats_to_dispatcharr = AsyncMock()
        assert (await replacement.probe_stream(7, URL))["probe_status"] == "success"
        replacement._run_ffprobe.assert_awaited_once()
        prober._save_probe_result.assert_not_called()
    finally:
        release.set()
        if locked:
            prober._probe_capacity_lock.release()
        tasks = [task for task in (runner, waiter, rebuild) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)


async def test_retired_reference_cannot_be_revived_by_start_reset_or_batch_entrypoints(
    capacity_probe, rebuild_services
):
    prober, _, _ = capacity_probe
    assert (await rebuild_services())["success"]
    await prober.start()
    prober.force_reset_probe_state()
    prober.client.get_stream.reset_mock()
    prober._fetch_all_streams.reset_mock()
    before = prober.get_probe_progress()
    for url in (URL, None):
        result = await prober.probe_stream(7, url)
        assert result["probe_status"] == "skipped"
        assert "stopped" in result["error_message"]
    assert (await prober.probe_all_streams(skip_m3u_refresh=True))[
        "status"
    ] == "cancelled"
    assert (await prober.probe_streams_by_ids([7]))["status"] == "cancelled"
    assert prober.get_probe_progress() == before
    prober.client.get_stream.assert_not_awaited()
    prober._fetch_all_streams.assert_not_awaited()
    prober._run_ffprobe.assert_not_awaited()
    prober._save_probe_result.assert_not_called()


@pytest.mark.parametrize("lane", ["sequential", "parallel", "bulk"])
async def test_settings_rebuild_joins_batch_preparation_before_publishing(
    capacity_probe, rebuild_services, lane
):
    prober, _, stream = capacity_probe
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def fetching():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    prober._fetch_all_streams.side_effect = fetching
    runner = asyncio.create_task(probe_lane(prober, stream, lane))
    rebuild = None
    try:
        await asyncio.wait_for(started.wait(), 3)
        rebuild = asyncio.create_task(rebuild_services())
        await asyncio.wait_for(cleaning.wait(), 3)
        assert not rebuild.done()
        assert stream_prober.get_prober() is prober
        release.set()
        assert (await asyncio.wait_for(rebuild, 3))["success"]
        assert runner.done()
        prober._run_ffprobe.assert_not_awaited()
        assert not prober._probe_account_connections
    finally:
        release.set()
        tasks = [task for task in (runner, rebuild) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)


@pytest.mark.parametrize("empty_scope", [False, True])
async def test_probe_all_cancel_during_preparation_preserves_previous_scope(
    capacity_probe, empty_scope
):
    prober, _, _ = capacity_probe
    prepare_probe_all(prober, False)
    prober._last_probe_scope_kind = "scoped"
    prober._last_probe_channel_stream_ids = {99}

    async def resolve(*_args):
        prober.cancel_probe()
        return frozenset() if empty_scope else None

    prober._resolve_channel_group_ids.side_effect = resolve
    prober._fetch_channel_stream_ids.return_value = (set(), {}, {})
    result = await prober.probe_all_streams(skip_m3u_refresh=True)
    assert result["status"] == "cancelled"
    assert prober.get_probe_history()[0]["status"] == "cancelled"
    assert prober._last_probe_scope_kind == "scoped"
    assert prober._last_probe_channel_stream_ids == {99}
    prober._run_ffprobe.assert_not_awaited()


async def test_sequential_cancel_during_account_hold_cannot_start_media(
    capacity_probe, monkeypatch
):
    prober, _, _ = capacity_probe
    prepare_probe_all(prober, False)
    prober._get_account_hold_remaining = MagicMock(return_value=1)
    sleeping, release = asyncio.Event(), asyncio.Event()
    real_sleep = asyncio.sleep

    async def hold(seconds):
        if seconds == 1:
            sleeping.set()
            await release.wait()
        else:
            await real_sleep(0)

    monkeypatch.setattr(stream_prober.asyncio, "sleep", hold)
    runner = asyncio.create_task(prober.probe_all_streams(skip_m3u_refresh=True))
    try:
        await asyncio.wait_for(sleeping.wait(), 3)
        prober.cancel_probe()
        release.set()
        assert (await asyncio.wait_for(runner, 3))["status"] == "cancelled"
        prober._run_ffprobe.assert_not_awaited()
        prober._save_probe_result.assert_not_called()
    finally:
        release.set()
        if not runner.done():
            runner.cancel()
        await asyncio.wait_for(asyncio.gather(runner, return_exceptions=True), 3)


async def test_parallel_cancel_during_scheduler_status_read_cannot_start_media(
    capacity_probe,
):
    prober, _, _ = capacity_probe
    prepare_probe_all(prober, True)

    async def status():
        prober.cancel_probe()
        return {}

    prober._get_profile_active_connections = AsyncMock(side_effect=status)
    result = await prober.probe_all_streams(skip_m3u_refresh=True)
    assert result["status"] == "cancelled"
    prober._run_ffprobe.assert_not_awaited()
    prober._save_probe_result.assert_not_called()
    assert not prober._probe_account_connections


@pytest.mark.parametrize("parallel", [False, True])
async def test_probe_all_cancel_does_not_cancel_unrelated_single(
    capacity_probe, parallel
):
    prober, _, _ = capacity_probe
    prepare_probe_all(prober, parallel)
    started = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    children = []

    async def media(*_args, **_kwargs):
        index = len(children)
        children.append(asyncio.current_task())
        started[index].set()
        await release.wait()
        return {"streams": []}

    prober._run_ffprobe.side_effect = media
    single = asyncio.create_task(prober.probe_stream(8, URL))
    batch = None
    try:
        await asyncio.wait_for(started[0].wait(), 3)
        batch = asyncio.create_task(
            prober.probe_all_streams(
                skip_m3u_refresh=True, allow_reorder_after_probe=False
            )
        )
        await asyncio.wait_for(started[1].wait(), 3)
        prober.cancel_probe()
        assert (await asyncio.wait_for(batch, 3))["status"] == "cancelled"
        assert not single.done()
        assert not children[0].done()
        assert prober._probe_account_connections == {11: 1}
        release.set()
        assert (await asyncio.wait_for(single, 3))["probe_status"] == "success"
    finally:
        release.set()
        tasks = [task for task in (single, batch) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)


@pytest.mark.parametrize("parallel", [False, True])
async def test_probe_all_cancel_while_reordering_cannot_publish_successful_scope(
    capacity_probe, parallel
):
    prober, _, _ = capacity_probe
    prepare_probe_all(prober, parallel)
    prober.auto_reorder_after_probe = True
    prober._last_probe_scope_kind = "scoped"
    prober._last_probe_channel_stream_ids = {99}

    async def reorder(**_kwargs):
        prober.cancel_probe()
        # A change that already completed cannot be rolled back by cancellation.
        return [{"channel_id": 1}]

    prober._auto_reorder_channels = AsyncMock(side_effect=reorder)
    result = await prober.probe_all_streams(skip_m3u_refresh=True)
    assert result["status"] == "cancelled"
    assert result["reordered_channels"] == 1
    assert prober._last_probe_scope_kind == "scoped"
    assert prober._last_probe_channel_stream_ids == {99}
    assert prober.get_probe_history()[0]["status"] == "cancelled"
    assert prober.get_probe_history()[0]["reordered_channels"] == [{"channel_id": 1}]


@pytest.mark.parametrize("can_delete", [False, True])
async def test_cancelled_notification_never_becomes_a_completion_alert(
    capacity_probe, monkeypatch, can_delete
):
    prober, _, _ = capacity_probe
    prober._probe_cancelled = True
    prober._probe_notification_id = "disposable"
    update, delete, alert = AsyncMock(), AsyncMock(), AsyncMock()
    prober.set_notification_callbacks(
        None, update, delete_by_source_callback=delete if can_delete else None
    )
    monkeypatch.setattr("alert_methods.send_alert", alert)
    await stream_prober.StreamProber._finalize_probe_notification(prober)
    update.assert_not_awaited()
    alert.assert_not_awaited()
    assert delete.await_count == int(can_delete)


async def test_cancel_after_terminal_history_is_not_accepted_during_notification(
    capacity_probe,
):
    prober, _, _ = capacity_probe
    prepare_probe_all(prober, True)
    replies = []

    async def finalize(**_kwargs):
        assert prober.get_probe_history()[0]["status"] == "completed"
        replies.append(prober.cancel_probe())

    prober._finalize_probe_notification.side_effect = finalize
    result = await prober.probe_all_streams(skip_m3u_refresh=True)
    assert result["status"] == "completed"
    assert replies[0]["status"] == "no_probe_running"
    assert not prober._probe_cancelled


@pytest.mark.parametrize("reorder_error", [False, True])
async def test_bulk_cancel_during_reorder_preserves_terminal_status_and_landed_results(
    capacity_probe, reorder_error
):
    prober, _, _ = capacity_probe
    prober.auto_reorder_after_probe = True
    landed = [{"channel_id": 1, "stream_count": 2}]

    async def reorder(_stream_ids):
        assert prober.cancel_probe()["status"] == "cancelling"
        if reorder_error:
            raise RuntimeError("Disposable reorder failure")
        return landed

    prober._auto_reorder_channels_for_streams = AsyncMock(side_effect=reorder)
    result = await prober.probe_streams_by_ids([7])
    assert result["status"] == "cancelled"
    assert result["success"] == 1
    assert prober.get_probe_progress()["status"] == "cancelled"
    history = prober.get_probe_history()[0]
    assert history["status"] == "cancelled"
    assert history["reordered_channels"] == ([] if reorder_error else landed)
    assert prober.get_probe_progress()["current_stream"] == ""
    assert not prober._bulk_probe_tasks
    assert prober._probe_run_task is None
