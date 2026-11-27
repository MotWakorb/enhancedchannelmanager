"""88yso: real admission policy, recorded account + synthetic finite profiles."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_stream_probe_capacity import (
    URL,
    prepare_probe_all,
    unlimited_probe as unlimited_probe,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("vlc_stream_user_agent")]


@pytest.fixture
def finite_probe(unlimited_probe):
    prober, account, stream = unlimited_probe
    account["max_streams"] = account["profiles"][0]["max_streams"] = 2
    prober.max_concurrent_probes = 2
    prober.probe_capacity_wait_timeout = 0
    return prober, account, stream


def add_spare(account):
    spare = deepcopy(account["profiles"][0])
    spare.update(id=13, is_default=False, max_streams=1,
                 search_pattern="/user/password/", replace_pattern="/other/login/")
    account["profiles"].append(spare)
    return spare


async def test_finite_provider_with_one_free_slot_is_measured(finite_probe):
    prober, _, _ = finite_probe
    prober.client.get_channel_stats.return_value = {"channels": [{"m3u_profile_id": 12}]}
    assert (await prober.probe_stream(7, URL))["probe_status"] == "success"


@pytest.mark.parametrize("lane", ["single", "bulk", "all_parallel", "all_sequential"])
async def test_secondary_login_not_blocked_by_default_account_limit(finite_probe, lane):
    prober, account, _ = finite_probe
    account["max_streams"] = account["profiles"][0]["max_streams"] = 1
    add_spare(account)
    prober.client.get_channel_stats.return_value = {"channels": [{"m3u_profile_id": 12}]}
    prepare_probe_all(prober, lane != "all_sequential")
    if lane == "single":
        result = await prober.probe_stream(7, URL)
        assert result["probe_status"] == "success"
    elif lane == "bulk":
        assert (await prober.probe_streams_by_ids([7]))["status"] == "completed"
    else:
        assert (await prober.probe_all_streams(skip_m3u_refresh=True))["status"] == "completed"
    prober._run_ffprobe.assert_awaited_once_with(
        URL.replace("/user/password/", "/other/login/"), user_agent="VLC/3.0.20 LibVLC/3.0.20"
    )


async def test_wait_rechecks_then_measures_without_health_failure(finite_probe):
    prober, _, _ = finite_probe
    prober.probe_capacity_wait_timeout = 1
    prober.client.get_channel_stats.side_effect = [
        {"channels": [{"m3u_profile_id": 12}] * 2}, {"channels": []},
    ]
    result = await prober.probe_stream(7, URL)
    assert result["probe_status"] == "success"
    assert prober.client.get_channel_stats.await_count == 2
    assert prober._save_probe_result.call_count == 1


async def test_capacity_timeout_has_specific_reason_and_no_health_write(finite_probe):
    prober, _, _ = finite_probe
    prober.client.get_channel_stats.return_value = {"channels": [{"m3u_profile_id": 12}] * 2}
    result = await prober.probe_stream(7, URL)
    assert result["probe_status"] == "skipped"
    assert "full" in result["deferred_reason"].lower()
    assert "profile 12: 2/2 connections" in result["deferred_reason"]
    assert "coordination" not in result["deferred_reason"]
    prober._save_probe_result.assert_not_called()
    prober._run_ffprobe.assert_not_awaited()


async def test_status_failure_uses_successful_snapshot_but_counts_local_probes(finite_probe):
    prober, _, _ = finite_probe
    assert (await prober.probe_stream(7, URL))["probe_status"] == "success"
    prober.client.get_channel_stats.side_effect = RuntimeError("unavailable")
    assert (await prober.probe_stream(7, URL))["probe_status"] == "success"
    prober._probe_profile_connections[12] = 2
    result = await prober.probe_stream(7, URL)
    assert result["probe_status"] == "skipped"


async def test_status_cache_expires_at_thirty_seconds(finite_probe, monkeypatch):
    prober, _, _ = finite_probe
    clock = [100.0]
    monkeypatch.setattr(prober, "_capacity_now", lambda: clock[0], raising=False)
    assert (await prober.probe_stream(7, URL))["probe_status"] == "success"
    prober.client.get_channel_stats.side_effect = RuntimeError("unavailable")
    clock[0] = 130.0
    assert (await prober.probe_stream(7, URL))["probe_status"] == "success"
    clock[0] = 130.001
    result = await prober.probe_stream(7, URL)
    assert result["probe_status"] == "skipped"
    assert "status" in result["deferred_reason"].lower()
    assert prober._run_ffprobe.await_count == 2


@pytest.mark.parametrize("lane", ["bulk", "all_parallel", "all_sequential", "quality", "queued"])
async def test_full_provider_wait_does_not_block_another_provider(finite_probe, lane, monkeypatch):
    prober, account, stream = finite_probe
    prober.probe_capacity_wait_timeout = 1
    other = deepcopy(account)
    other["id"] = 20
    other["profiles"][0]["id"] = 21
    prober.client.get_m3u_accounts.return_value.append(other)
    second = {**stream, "id": 8, "m3u_account": 20}
    rows = [stream, {**stream, "id": 9}, {**stream, "id": 10}, second]
    prober._fetch_all_streams.return_value = rows
    prober.client.get_channel_stats.return_value = {"channels": [{"m3u_profile_id": 12}] * 2}
    prepare_probe_all(prober, lane != "all_sequential")
    prober._fetch_channel_stream_ids.return_value = ({7, 8, 9, 10}, {}, {})
    measured = asyncio.Event()

    async def media(*args, **kwargs):
        measured.set()
        return {"streams": []}

    prober._run_ffprobe.side_effect = media
    if lane in ("quality", "queued"):
        from channel_pipeline_engine import ChannelPipelineEngine

        engine = ChannelPipelineEngine.__new__(ChannelPipelineEngine)
        engine._stream_stats_cache = {}
        engine._load_stream_stats = AsyncMock()
        contexts = [SimpleNamespace(stream_id=s["id"], stream_url=s["url"], stream_name=s["name"]) for s in rows]
        rule = SimpleNamespace(id=1, sort_field="quality", probe_on_sort=True)
        results = {"execution_log": [], "dry_run_results": []}
        if lane == "quality":
            operation = engine._probe_unprobed_streams([(ctx, rule, [], []) for ctx in contexts], [rule], results, False)
        else:
            operation = engine._batch_probe_streams({7, 8, 9, 10}, contexts, results, False)
        # Match production get_prober lookup without replacing admission.
        monkeypatch.setattr("stream_prober.get_prober", lambda: prober)
    else:
        operation = (prober.probe_streams_by_ids([7, 9, 10, 8]) if lane == "bulk"
                     else prober.probe_all_streams(skip_m3u_refresh=True))
    task = asyncio.create_task(operation)
    try:
        await asyncio.wait_for(measured.wait(), 0.3)
        assert not task.done(), "full provider should still be queued"
        result = await asyncio.wait_for(task, 4)
        if lane in ("quality", "queued"):
            assert "3 skipped" in str(results["execution_log"])
        else:
            assert result["status"] == "deferred"
            assert prober.get_probe_progress()["success_count"] == 1
            assert prober.get_probe_progress()["skipped_count"] == 3
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_global_media_limit_applies_across_single_callers(finite_probe):
    prober, _, stream = finite_probe
    prober.max_concurrent_probes = 1
    prober.probe_capacity_wait_timeout = 1
    started, release = asyncio.Event(), asyncio.Event()
    active = 0
    peak = 0

    async def media(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.set()
        await release.wait()
        active -= 1
        return {"streams": []}

    prober._run_ffprobe.side_effect = media
    first = asyncio.create_task(prober.probe_stream(7, URL))
    await asyncio.wait_for(started.wait(), 1)
    second = asyncio.create_task(prober.probe_stream(8, URL, stream={**stream, "id": 8}))
    try:
        await asyncio.sleep(0.02)
        assert prober._run_ffprobe.await_count == 1
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 2)
    assert peak == 1


async def test_round_robin_is_shared_by_single_calls(finite_probe):
    prober, account, _ = finite_probe
    add_spare(account)
    prober.profile_distribution_strategy = "round_robin"
    for _ in range(3):
        assert (await prober.probe_stream(7, URL))["probe_status"] == "success"
    assert [call.args[0] for call in prober._run_ffprobe.await_args_list] == [
        URL, URL.replace("/user/password/", "/other/login/"), URL,
    ]


@pytest.mark.parametrize("replacement", ["$1/other/login/$2", "$<host>/other/login/$<tail>"])
async def test_dispatcharr_javascript_backreferences_are_supported(finite_probe, replacement):
    prober, account, _ = finite_probe
    spare = add_spare(account)
    spare.update(search_pattern=r"(?<host>https://[^/]+)/user/password/(?<tail>.*)",
                 replace_pattern=replacement)
    prober.client.get_channel_stats.return_value = {"channels": [{"m3u_profile_id": 12}] * 2}
    assert (await prober.probe_stream(7, URL))["probe_status"] == "success"
    assert prober._run_ffprobe.await_args.args[0] == URL.replace("/user/password/", "/other/login/")


async def test_capacity_wait_is_cancellable(finite_probe):
    prober, _, _ = finite_probe
    prober.probe_capacity_wait_timeout = 300
    prober.client.get_channel_stats.return_value = {"channels": [{"m3u_profile_id": 12}] * 2}
    runner = asyncio.create_task(prober.probe_stream(7, URL))
    await asyncio.sleep(0.01)
    assert not runner.done()
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(runner, 1)
    assert not prober._probe_account_connections
    assert not prober._probe_tasks


async def test_five_minute_capacity_deadline_with_virtual_clock(finite_probe, monkeypatch):
    prober, _, _ = finite_probe
    prober.probe_capacity_wait_timeout = 300
    prober.client.get_channel_stats.return_value = {"channels": [{"m3u_profile_id": 12}] * 2}
    clock = [0.0]
    real_sleep = asyncio.sleep

    async def advance(seconds):
        clock[0] += seconds
        await real_sleep(0)

    monkeypatch.setattr(prober, "_capacity_now", lambda: clock[0])
    monkeypatch.setattr(asyncio, "sleep", advance)
    result = await prober.probe_stream(7, URL)
    assert result["probe_status"] == "skipped"
    assert clock[0] == 300
    assert prober.client.get_channel_stats.await_count > 1
    prober._run_ffprobe.assert_not_awaited()


@pytest.mark.parametrize("fault", ["no_snapshot", "changed_attribution", "malformed_status"])
async def test_unusable_counts_cannot_authorize_media(finite_probe, fault):
    prober, account, _ = finite_probe
    if fault != "no_snapshot":
        assert (await prober.probe_stream(7, URL))["probe_status"] == "success"
    if fault == "changed_attribution":
        other = deepcopy(account)
        other["id"] = 20
        other["profiles"][0]["id"] = 21
        prober.client.get_m3u_accounts.return_value.append(other)
    if fault == "malformed_status":
        prober.client.get_channel_stats.return_value = {"channels": [{}]}
        prober._probe_status_snapshot = None
    else:
        prober.client.get_channel_stats.side_effect = RuntimeError("unavailable")
    before = prober._run_ffprobe.await_count
    assert (await prober.probe_stream(7, URL))["probe_status"] == "skipped"
    assert prober._run_ffprobe.await_count == before


async def test_two_configured_probes_downshift_to_one_available_slot(finite_probe):
    prober, _, stream = finite_probe
    prober.probe_capacity_wait_timeout = 1
    prober.client.get_channel_stats.return_value = {"channels": [{"m3u_profile_id": 12}]}
    release, started = asyncio.Event(), asyncio.Event()
    peak = 0

    async def media(*args, **kwargs):
        nonlocal peak
        peak = max(peak, sum(prober._probe_profile_connections.values()))
        started.set()
        await release.wait()
        return {"streams": []}

    prober._run_ffprobe.side_effect = media
    first = asyncio.create_task(prober.probe_stream(7, URL))
    await asyncio.wait_for(started.wait(), 1)
    second = asyncio.create_task(prober.probe_stream(8, URL, stream={**stream, "id": 8}))
    try:
        await asyncio.sleep(0.02)
        assert prober._run_ffprobe.await_count == 1
    finally:
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), 2)
    assert all(r["probe_status"] == "success" for r in results)
    assert peak == 1


async def test_scan_local_media_ceiling_is_not_held_by_capacity_waiters(finite_probe):
    prober, account, stream = finite_probe
    prober.probe_capacity_wait_timeout = 1
    other = deepcopy(account)
    other["id"] = 20
    other["profiles"][0]["id"] = 21
    prober.client.get_m3u_accounts.return_value.append(other)
    prober._fetch_all_streams.return_value.append({**stream, "id": 8, "m3u_account": 20})
    prober.client.get_channel_stats.return_value = {"channels": [{"m3u_profile_id": 12}] * 2}
    semaphore = asyncio.Semaphore(1)
    first = asyncio.create_task(prober.probe_stream(7, URL, _media_semaphore=semaphore))
    try:
        await asyncio.sleep(0.02)
        assert not semaphore.locked()
        second = await asyncio.wait_for(prober.probe_stream(8, URL, _media_semaphore=semaphore), 0.3)
        assert second["probe_status"] == "success"
        assert not first.done()
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
    assert not semaphore.locked()


async def test_scan_cancellation_flag_stops_capacity_wait(finite_probe):
    prober, _, _ = finite_probe
    prober.probe_capacity_wait_timeout = 300
    prober.client.get_channel_stats.return_value = {"channels": [{"m3u_profile_id": 12}] * 2}
    cancelled = False
    runner = asyncio.create_task(prober.probe_stream(7, URL, _cancel_check=lambda: cancelled))
    await asyncio.sleep(0.01)
    cancelled = True
    result = await asyncio.wait_for(runner, 1)
    assert result["probe_status"] == "skipped"
    assert "cancelled" in result["error_message"]
    prober._run_ffprobe.assert_not_awaited()


async def test_unprovable_route_defers_immediately_not_after_five_minutes(finite_probe):
    prober, _, _ = finite_probe
    prober.probe_capacity_wait_timeout = 300
    prober.client.get_stream.side_effect = None
    prober.client.get_stream.return_value = {"id": 7}  # Missing URL/account is not live-slot contention.
    result = await asyncio.wait_for(prober.probe_stream(7, URL), 0.3)
    assert result["probe_status"] == "skipped"
    assert "route" in result["error_message"]
    prober.client.get_stream.assert_awaited_once()
    prober._run_ffprobe.assert_not_awaited()
