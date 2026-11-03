"""GH1032 admission/lifecycle tests. Status rows are source-derived, not live captures.

Account shape: recorded Dispatcharr 0.27.2 response. Finite limits below are
intentional test variations; see docs/probe-capacity.md for upstream provenance.
"""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from security.stream_outbound import ValidatedSubprocessInput
from stream_prober import StreamProber

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("vlc_stream_user_agent")]
URL = "https://provider.invalid/user/password/live.ts"
FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def capacity_probe():
    account = json.loads(
        (FIXTURES / "bd_igqcy/dispatcharr_v0272_m3u_account.json").read_text()
    )
    account["max_streams"] = 2
    account["profiles"][0]["max_streams"] = 2
    stream = {"id": 7, "name": "Test", "url": URL, "m3u_account": account["id"]}
    client = AsyncMock()
    client.get_stream.return_value = stream
    client.get_m3u_accounts.return_value = [account]
    client.get_channel_stats.return_value = {"channels": [], "count": 0}
    prober = StreamProber(client, max_concurrent_probes=4)
    prober._run_ffprobe = AsyncMock(return_value={"streams": []})
    prober._measure_stream_bitrate = AsyncMock(return_value=1234)
    prober._push_stats_to_dispatcharr = AsyncMock()
    prober._save_probe_result = MagicMock(
        side_effect=lambda *a: {"probe_status": a[3], "error_message": a[4]}
    )
    prober._persist_probe_history = MagicMock()
    prober._create_probe_notification = AsyncMock()
    prober._update_probe_notification = AsyncMock()
    prober._finalize_probe_notification = AsyncMock()
    prober._fetch_all_streams = AsyncMock(return_value=[stream])

    async def current_stream(stream_id):
        rows = prober._fetch_all_streams.return_value
        row = next(
            (row for row in rows if row["id"] == stream_id), {**stream, "id": stream_id}
        )
        fresh = deepcopy(row)
        fresh.pop("_selected_profile", None)  # Internal hint is not API metadata.
        return fresh

    client.get_stream.side_effect = current_stream
    return prober, account, stream


def viewers(prober, count, profile_id=12):
    # One upstream channel = one provider connection, not client_count viewers.
    prober.client.get_channel_stats.return_value = {
        "channels": [
            {"m3u_profile_id": profile_id, "client_count": 9} for _ in range(count)
        ],
        "count": count,
    }


def assert_skipped(prober, result):
    assert result["probe_status"] == "skipped"
    assert "capacity" in result["error_message"].lower()
    assert URL not in result["error_message"]
    prober._run_ffprobe.assert_not_awaited()
    prober._measure_stream_bitrate.assert_not_awaited()
    prober._save_probe_result.assert_not_called()


async def test_saturated_single_skips_without_overwriting_health(
    capacity_probe, caplog
):
    prober, _, _ = capacity_probe
    viewers(prober, 2)
    assert_skipped(prober, await prober.probe_stream(7, URL, "Test"))
    assert "capacity" in caplog.text.lower()
    assert URL not in caplog.text


async def test_saturated_bulk_records_skip_not_failure(capacity_probe):
    prober, _, _ = capacity_probe
    viewers(prober, 2)
    result = await prober.probe_streams_by_ids([7], allow_reorder_after_probe=False)
    assert result["success"] == result["failed"] == 0
    progress = prober.get_probe_progress()
    assert progress["skipped_count"] == 1
    assert prober.get_probe_results()["skipped_streams"][0]["reason"]
    prober._run_ffprobe.assert_not_awaited()


def prepare_probe_all(prober, parallel):
    prober.parallel_probing_enabled = parallel
    prober.skip_recently_probed_hours = 0
    prober._resolve_channel_group_ids = AsyncMock(return_value=None)
    prober._fetch_channel_stream_ids = AsyncMock(
        return_value=({7}, {7: ["Channel"]}, {7: 1})
    )


@pytest.mark.parametrize("parallel", [False, True])
async def test_saturated_probe_all_finishes_with_skip(capacity_probe, parallel):
    prober, _, _ = capacity_probe
    prepare_probe_all(prober, parallel)
    viewers(prober, 2)
    result = await asyncio.wait_for(
        prober.probe_all_streams(
            skip_m3u_refresh=True, allow_reorder_after_probe=False
        ),
        1,
    )
    assert result["status"] == "completed"
    assert prober.get_probe_progress()["skipped_count"] == 1
    prober._run_ffprobe.assert_not_awaited()


@pytest.mark.parametrize("parallel", [False, True])
async def test_probe_all_stale_scheduler_snapshot_cannot_bypass_admission(
    capacity_probe, parallel
):
    prober, _, _ = capacity_probe
    prepare_probe_all(prober, parallel)
    viewers(prober, 2)
    prober._get_profile_active_connections = AsyncMock(return_value={})
    result = await asyncio.wait_for(
        prober.probe_all_streams(
            skip_m3u_refresh=True, allow_reorder_after_probe=False
        ),
        2,
    )
    assert result["status"] == "completed"
    assert prober.get_probe_progress()["skipped_count"] == 1
    assert prober.get_probe_progress()["failed_count"] == 0
    prober._run_ffprobe.assert_not_awaited()


async def test_probe_all_cancellation_awaits_media_cleanup_before_releasing(
    capacity_probe,
):
    prober, _, _ = capacity_probe
    prepare_probe_all(prober, True)
    started, cleanup_done = asyncio.Event(), asyncio.Event()
    children = []

    async def media(*args, **kwargs):
        children.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await cleanup_done.wait()

    prober._run_ffprobe.side_effect = media
    runner = asyncio.create_task(
        prober.probe_all_streams(skip_m3u_refresh=True, allow_reorder_after_probe=False)
    )
    try:
        await asyncio.wait_for(started.wait(), 1)
        runner.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not runner.done(), (
            "probe-all must wait for cancelled provider connection cleanup"
        )
    finally:
        cleanup_done.set()
        await asyncio.gather(runner, return_exceptions=True)
        for child in children:
            if not child.done():
                child.cancel()
        await asyncio.gather(*children, return_exceptions=True)
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections


async def test_probe_all_pause_does_not_reserve_capacity(capacity_probe):
    prober, _, _ = capacity_probe
    prepare_probe_all(prober, True)
    fetching, continue_fetch = asyncio.Event(), asyncio.Event()

    async def fetch(*args, **kwargs):
        fetching.set()
        await continue_fetch.wait()
        return {7}, {7: ["Channel"]}, {7: 1}

    prober._fetch_channel_stream_ids.side_effect = fetch
    runner = asyncio.create_task(
        prober.probe_all_streams(skip_m3u_refresh=True, allow_reorder_after_probe=False)
    )
    try:
        await asyncio.wait_for(fetching.wait(), 1)
        assert prober.pause_probe()["status"] == "paused"
        continue_fetch.set()
        await asyncio.sleep(0.05)
        assert not prober._probe_account_connections
        prober._run_ffprobe.assert_not_awaited()
        prober.resume_probe()
        assert (await asyncio.wait_for(runner, 2))["status"] == "completed"
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


@pytest.mark.parametrize("lane", ["quality", "queued"])
async def test_saturated_pipeline_uses_shared_admission(capacity_probe, lane):
    from channel_pipeline_engine import ChannelPipelineEngine

    prober, _, _ = capacity_probe
    viewers(prober, 2)
    engine = ChannelPipelineEngine.__new__(ChannelPipelineEngine)
    engine._stream_stats_cache = {}
    engine._load_stream_stats = AsyncMock()
    ctx = SimpleNamespace(stream_id=7, stream_url=URL, stream_name="Test")
    rule = SimpleNamespace(id=1, sort_field="quality", probe_on_sort=True)
    results = {"execution_log": [], "dry_run_results": []}
    with patch("stream_prober.get_prober", return_value=prober):
        if lane == "quality":
            await engine._probe_unprobed_streams(
                [(ctx, rule, [], [])], [rule], results, False
            )
        else:
            await engine._batch_probe_streams({7}, [ctx], results, False)
    prober._run_ffprobe.assert_not_awaited()
    assert (
        "skipped"
        in results["execution_log"][-1]["actions_executed"][0]["description"].lower()
    )
    if lane == "queued":
        assert results["streams_probed"] == 0


async def test_one_free_slot_cannot_be_claimed_by_concurrent_entrypoints(
    capacity_probe,
):
    prober, _, stream = capacity_probe
    viewers(prober, 1)
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        started.set()
        await release.wait()
        return {"streams": []}

    prober._run_ffprobe.side_effect = blocked
    first = asyncio.create_task(prober.probe_stream(7, URL, stream=stream))
    try:
        await asyncio.wait_for(started.wait(), 1)
        second = await asyncio.wait_for(
            prober.probe_stream(8, URL, stream={**stream, "id": 8}), 1
        )
        assert second["probe_status"] == "skipped"
        assert prober._run_ffprobe.await_count == 1
    finally:
        release.set()
        await asyncio.wait_for(first, timeout=10)
    assert (await prober.probe_stream(9, URL, stream={**stream, "id": 9}))[
        "probe_status"
    ] == "success"


@pytest.mark.parametrize(
    "fault",
    [
        "missing_account",
        "account_error",
        "status_error",
        "status_shape",
        "missing_limit",
        "negative_limit",
        "boolean_limit",
        "missing_stream_account",
        "unknown_profile",
    ],
)
async def test_unknown_capacity_fails_closed(capacity_probe, fault):
    prober, account, stream = capacity_probe
    if fault == "missing_account":
        prober.client.get_m3u_accounts.return_value = []
    elif fault == "account_error":
        prober.client.get_m3u_accounts.side_effect = RuntimeError(URL)
    elif fault == "status_error":
        prober.client.get_channel_stats.side_effect = RuntimeError(URL)
    elif fault == "status_shape":
        prober.client.get_channel_stats.return_value = {}
    elif fault == "missing_limit":
        del account["max_streams"]
    elif fault == "negative_limit":
        account["max_streams"] = -1
    elif fault == "boolean_limit":
        account["max_streams"] = False
    elif fault == "missing_stream_account":
        del stream["m3u_account"]
    else:
        viewers(prober, 1, profile_id=999)
    assert_skipped(prober, await prober.probe_stream(7, URL, stream=stream))


async def test_recorded_unlimited_account_preserves_direct_measurement(capacity_probe):
    prober, account, stream = capacity_probe
    recorded = json.loads(
        (FIXTURES / "bd_igqcy/dispatcharr_v0272_m3u_account.json").read_text()
    )
    account.update(recorded)
    viewers(prober, 8)
    result = await prober.probe_stream(7, URL, stream=stream)
    assert result["probe_status"] == "success"
    prober._run_ffprobe.assert_awaited_once_with(
        URL, user_agent="VLC/3.0.20 LibVLC/3.0.20"
    )
    prober._measure_stream_bitrate.assert_awaited_once_with(
        URL, user_agent="VLC/3.0.20 LibVLC/3.0.20"
    )


async def test_null_account_custom_stream_does_not_need_account_status(capacity_probe):
    prober, _, stream = capacity_probe
    stream["m3u_account"] = None
    assert (await prober.probe_stream(7, URL, stream=stream))[
        "probe_status"
    ] == "success"
    prober.client.get_m3u_accounts.assert_not_awaited()
    prober.client.get_channel_stats.assert_not_awaited()


async def test_profile_cap_uses_current_status_not_stored_current_viewers(
    capacity_probe,
):
    prober, account, stream = capacity_probe
    account["profiles"][0].update(max_streams=1, current_viewers=0)
    account["max_streams"] = 5
    viewers(prober, 1)
    assert_skipped(prober, await prober.probe_stream(7, URL, stream=stream))


async def test_selected_profile_does_not_silently_switch_to_free_profile(
    capacity_probe,
):
    prober, account, stream = capacity_probe
    account["max_streams"] = 5
    other = deepcopy(account["profiles"][0])
    other.update(id=13, is_default=False, max_streams=1)
    account["profiles"].append(other)
    stream["_selected_profile"] = other
    viewers(prober, 1, profile_id=13)
    assert_skipped(prober, await prober.probe_stream(7, URL, stream=stream))


@pytest.mark.parametrize("unlimited", ["account", "profile"])
async def test_one_unlimited_limit_does_not_disable_the_other_cap(
    capacity_probe, unlimited
):
    prober, account, stream = capacity_probe
    if unlimited == "account":
        account["max_streams"] = 0
    else:
        account["profiles"][0]["max_streams"] = 0
    viewers(prober, 2)
    assert_skipped(prober, await prober.probe_stream(7, URL, stream=stream))


async def test_account_limit_sums_current_connections_across_profiles(capacity_probe):
    prober, account, stream = capacity_probe
    other = deepcopy(account["profiles"][0])
    other.update(id=13, is_default=False)
    account["profiles"].append(other)
    viewers(prober, 2, profile_id=13)
    assert_skipped(prober, await prober.probe_stream(7, URL, stream=stream))


async def test_unrelated_account_connections_are_not_charged_to_target(capacity_probe):
    prober, account, stream = capacity_probe
    other = deepcopy(account)
    other["id"] = 20
    other["profiles"][0]["id"] = 21
    prober.client.get_m3u_accounts.return_value.append(other)
    viewers(prober, 8, profile_id=21)
    assert (await prober.probe_stream(7, URL, stream=stream))[
        "probe_status"
    ] == "success"


async def test_stored_profile_viewer_count_does_not_override_fresh_status(
    capacity_probe,
):
    prober, account, stream = capacity_probe
    account["profiles"][0]["current_viewers"] = 999
    assert (await prober.probe_stream(7, URL, stream=stream))[
        "probe_status"
    ] == "success"


@pytest.mark.parametrize(
    "fault",
    [
        "no_profiles",
        "no_default",
        "inactive_default",
        "bad_profile_limit",
        "missing_attribution",
        "malformed_channel",
        "malformed_selected_profile",
        "unknown_selected_profile",
    ],
)
async def test_ambiguous_profile_or_status_skips_without_rerouting(
    capacity_probe, fault
):
    prober, account, stream = capacity_probe
    if fault == "no_profiles":
        account["profiles"] = []
    elif fault == "no_default":
        account["profiles"][0]["is_default"] = False
    elif fault == "inactive_default":
        account["profiles"][0]["is_active"] = False
    elif fault == "bad_profile_limit":
        account["profiles"][0]["max_streams"] = None
    elif fault == "malformed_selected_profile":
        stream["_selected_profile"] = 12
    elif fault == "unknown_selected_profile":
        stream["_selected_profile"] = {"id": 99}
    else:
        prober.client.get_channel_stats.return_value = {
            "channels": [{} if fault == "missing_attribution" else None]
        }
    assert_skipped(prober, await prober.probe_stream(7, URL, stream=stream))


@pytest.mark.parametrize(
    "phase", ["_run_resdet", "_measure_stream_bitrate", "_detect_black_screen"]
)
async def test_permit_covers_optional_media_and_is_released_on_cancel(
    capacity_probe, phase
):
    prober, _, stream = capacity_probe
    prober.use_resdet_for_resolution = phase == "_run_resdet"
    prober.black_screen_detection_enabled = phase == "_detect_black_screen"
    viewers(prober, 1)
    started = asyncio.Event()

    async def blocked(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    setattr(prober, phase, AsyncMock(side_effect=blocked))
    first = asyncio.create_task(prober.probe_stream(7, URL, stream=stream))
    try:
        await asyncio.wait_for(started.wait(), 1)
        result = await prober.probe_stream(8, URL, stream={**stream, "id": 8})
        assert result["probe_status"] == "skipped"
        assert prober._probe_account_connections == {11: 1}
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections


async def test_cancelling_admission_waiter_does_not_leak_or_release_other_permit(
    capacity_probe,
):
    prober, _, stream = capacity_probe
    fetching, release = asyncio.Event(), asyncio.Event()

    async def status():
        fetching.set()
        await release.wait()
        return {"channels": []}

    prober.client.get_channel_stats.side_effect = status
    first = asyncio.create_task(prober.probe_stream(7, URL, stream=stream))
    second = None
    try:
        await asyncio.wait_for(fetching.wait(), 1)
        second = asyncio.create_task(
            prober.probe_stream(8, URL, stream={**stream, "id": 8})
        )
        await asyncio.sleep(0)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(second, timeout=10)
    finally:
        release.set()
        await asyncio.gather(
            first, *([second] if second else []), return_exceptions=True
        )
    assert first.result()["probe_status"] == "success"
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections


async def test_viewer_arrival_is_seen_without_five_second_cache(capacity_probe):
    prober, _, stream = capacity_probe
    assert (await prober.probe_stream(7, URL, stream=stream))[
        "probe_status"
    ] == "success"
    prober._run_ffprobe.reset_mock()
    prober._measure_stream_bitrate.reset_mock()
    prober._save_probe_result.reset_mock()
    viewers(prober, 2)
    assert_skipped(prober, await prober.probe_stream(7, URL, stream=stream))


@pytest.mark.parametrize("outcome", ["cancel", "failure", "timeout"])
async def test_reservation_released_after_cleanup(capacity_probe, outcome):
    prober, _, stream = capacity_probe
    viewers(prober, 1)
    cleanup_started, cleanup_done = asyncio.Event(), asyncio.Event()

    async def media(*args, **kwargs):
        if outcome == "cancel":
            try:
                cleanup_started.set()
                await asyncio.Event().wait()
            finally:
                await cleanup_done.wait()
        raise TimeoutError() if outcome == "timeout" else RuntimeError("media failed")

    prober._run_ffprobe.side_effect = media
    first = asyncio.create_task(prober.probe_stream(7, URL, stream=stream))
    if outcome == "cancel":
        await asyncio.wait_for(cleanup_started.wait(), 1)
        first.cancel()
        await asyncio.sleep(0)
        second = asyncio.create_task(
            prober.probe_stream(8, URL, stream={**stream, "id": 8})
        )
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert second.done(), (
                "capacity rejection must not start another media operation"
            )
            assert second.result()["probe_status"] == "skipped"
        finally:
            cleanup_done.set()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)
        assert first.cancelled()
    else:
        assert (await first)["probe_status"] in {"failed", "timeout"}
    prober._run_ffprobe.side_effect = None
    assert (await prober.probe_stream(9, URL, stream={**stream, "id": 9}))[
        "probe_status"
    ] == "success"


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize(
    "rewrite",
    [
        "missing",
        "no_match",
        "invalid_pattern",
        "invalid_template",
        "oversize",
        "timeout",
        "identity_full",
    ],
)
async def test_scheduler_rewrite_fallback_cannot_charge_spare_profile(
    capacity_probe, parallel, rewrite
):
    prober, account, stream = capacity_probe
    prepare_probe_all(prober, parallel)
    account["max_streams"] = 5
    account["profiles"][0]["max_streams"] = 1
    selected = deepcopy(account["profiles"][0])
    selected.update(id=13, is_default=False, max_streams=2)
    selected.update(search_pattern=r"/user/password/", replace_pattern="/other/login/")
    if rewrite == "missing":
        del selected["search_pattern"]
    elif rewrite == "no_match":
        selected["search_pattern"] = "not-present-in-stream-url"
    elif rewrite == "invalid_pattern":
        selected["search_pattern"] = "(unclosed"
    elif rewrite == "invalid_template":
        selected.update(search_pattern="(user)", replace_pattern=r"\2")
    elif rewrite == "oversize":
        selected["search_pattern"] = "a" * 501
    elif rewrite == "timeout":
        stream["url"] = "https://provider.invalid/" + "a" * 30 + "!"
        selected["search_pattern"] = "(a|aa)+b"
    else:
        selected.update(search_pattern="^(.*)$", replace_pattern=r"\1")
    account["profiles"].append(selected)
    viewers(prober, 1, profile_id=12)
    with patch.object(
        prober, "_rewrite_url_for_profile", wraps=prober._rewrite_url_for_profile
    ) as rewrite_call:
        result = await asyncio.wait_for(
            prober.probe_all_streams(
                skip_m3u_refresh=True, allow_reorder_after_probe=False
            ),
            3,
        )
    assert result["status"] == "completed"
    assert stream["_selected_profile"]["id"] == 13, (
        "the real scheduler must select the spare profile"
    )
    assert rewrite_call.call_count >= 1
    assert prober.get_probe_progress()["skipped_count"] == 1
    assert prober.get_probe_progress()["failed_count"] == 0
    prober._run_ffprobe.assert_not_awaited()
    prober._measure_stream_bitrate.assert_not_awaited()
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("rewrite", ["changed", "identity"])
async def test_scheduler_successful_rewrite_reserves_actual_media_route(
    capacity_probe, parallel, rewrite
):
    prober, account, stream = capacity_probe
    prepare_probe_all(prober, parallel)
    account["max_streams"] = 5
    selected = deepcopy(account["profiles"][0])
    selected.update(
        id=13, is_default=False, search_pattern="^(.*)$", replace_pattern=r"\1"
    )
    if rewrite == "changed":
        selected.update(
            search_pattern=r"/user/password/", replace_pattern="/other/login/"
        )
    account["profiles"].append(selected)
    prober.profile_distribution_strategy = "round_robin"
    prober._round_robin_index[11] = 0
    seen = []

    async def media(url, **_kwargs):
        seen.append((url, dict(prober._probe_profile_connections)))
        return {"streams": []}

    prober._run_ffprobe.side_effect = media
    result = await asyncio.wait_for(
        prober.probe_all_streams(
            skip_m3u_refresh=True, allow_reorder_after_probe=False
        ),
        3,
    )
    assert result["status"] == "completed"
    assert stream["_selected_profile"]["id"] == 13
    expected_url = (
        URL.replace("/user/password/", "/other/login/") if rewrite == "changed" else URL
    )
    expected_permits = {13: 1} if rewrite == "changed" else {12: 1, 13: 1}
    assert seen == [(expected_url, expected_permits)]
    assert prober.get_probe_progress()["success_count"] == 1
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections


@pytest.mark.parametrize("repeated_parent_cancel", [False, True])
async def test_two_scheduler_children_single_cancel_keeps_permits_through_delayed_cleanup(
    capacity_probe, repeated_parent_cancel
):
    prober, account, stream = capacity_probe
    prepare_probe_all(prober, True)
    # Separate accounts bypass the initial per-account ramp=1, exercising two
    # real scheduler children without replacing the scheduler or admission.
    other = deepcopy(account)
    other["id"] = 20
    other["profiles"][0]["id"] = 21
    prober.client.get_m3u_accounts.return_value.append(other)
    second_stream = {**stream, "id": 8, "m3u_account": 20}
    prober._fetch_all_streams.return_value = [stream, second_stream]
    prober._fetch_channel_stream_ids.return_value = ({7, 8}, {}, {})
    started = [asyncio.Event(), asyncio.Event()]
    cleaning = [asyncio.Event(), asyncio.Event()]
    release_cleanup = asyncio.Event()
    cleaned = set()
    children = []

    async def media(*args, **kwargs):
        index = len(children)
        children.append(asyncio.current_task())
        started[index].set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning[index].set()
            if index == 1:
                await release_cleanup.wait()
            cleaned.add(index)

    prober._run_ffprobe.side_effect = media
    runner = asyncio.create_task(
        prober.probe_all_streams(skip_m3u_refresh=True, allow_reorder_after_probe=False)
    )
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), 2)
        assert prober.cancel_probe()["status"] == "cancelling"  # Exactly ONE call.
        await asyncio.wait_for(cleaning[1].wait(), 1)
        # The first child completes cancellation, waking FIRST_COMPLETED; the
        # other child is still in its connection cleanup at this point.
        await asyncio.sleep(0.05)
        if repeated_parent_cancel:
            runner.cancel()
            await asyncio.sleep(0)
            runner.cancel()
            await asyncio.sleep(0.05)
        assert not runner.done(), "scheduler must join the second child's media cleanup"
        assert 1 not in cleaned
        assert prober._probe_account_connections == {20: 1}
        assert prober._probe_profile_connections == {21: 1}
        assert len(prober._bulk_probe_tasks) == 1
        assert all(not child.done() for child in prober._bulk_probe_tasks)
        assert not children[1].done()
        assert prober._probing_in_progress
    finally:
        release_cleanup.set()
        await asyncio.gather(runner, return_exceptions=True)
        await asyncio.gather(*children, return_exceptions=True)
    assert cleaned == {0, 1}
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections
    assert not prober._bulk_probe_tasks
    assert not prober._probing_in_progress
    if repeated_parent_cancel:
        assert runner.cancelled(), "joining cleanup must preserve parent CancelledError"


@pytest.mark.parametrize("entrypoint", ["single", "probe_all", "bulk"])
async def test_repeated_parent_cancellation_joins_real_process_reap(
    capacity_probe, monkeypatch, entrypoint
):
    """Real _run_ffprobe/kill/wait; a disposable Python child replaces ffprobe.

    Only the input relay and executable are substituted, not cancellation,
    scheduler, admission, subprocess communication or OS process reaping.
    """
    prober, account, stream = capacity_probe
    prepare_probe_all(prober, True)
    prober._run_ffprobe = StreamProber._run_ffprobe.__get__(prober)
    count = 1 if entrypoint == "single" else 2
    if count == 2:
        other = deepcopy(account)
        other["id"] = 20
        other["profiles"][0]["id"] = 21
        prober.client.get_m3u_accounts.return_value.append(other)
        prober._fetch_all_streams.return_value = [
            stream,
            {**stream, "id": 8, "m3u_account": 20},
        ]
        prober._fetch_channel_stream_ids.return_value = ({7, 8}, {}, {})
    started = [asyncio.Event() for _ in range(count)]
    cleaning = [asyncio.Event() for _ in range(count)]
    release = asyncio.Event()
    processes, reaped = [], set()
    real_spawn = asyncio.create_subprocess_exec

    @asynccontextmanager
    async def input_substitute(url, **_kwargs):
        yield ValidatedSubprocessInput(argument=url)

    async def spawn(*args, **kwargs):
        process = await real_spawn(
            sys.executable, "-c", "import time; time.sleep(60)", **kwargs
        )
        index = len(processes)
        processes.append(process)
        real_wait = process.wait

        async def delayed_wait():
            result = await real_wait()
            cleaning[index].set()
            await release.wait()
            reaped.add(index)
            return result

        process.wait = delayed_wait
        started[index].set()
        return process

    monkeypatch.setattr("stream_prober.validated_subprocess_input", input_substitute)
    monkeypatch.setattr("stream_prober.asyncio.create_subprocess_exec", spawn)
    if entrypoint == "single":
        operation = prober.probe_stream(7, URL, stream=stream)
    elif entrypoint == "bulk":
        operation = prober.probe_streams_by_ids([7, 8], allow_reorder_after_probe=False)
    else:
        operation = prober.probe_all_streams(
            skip_m3u_refresh=True, allow_reorder_after_probe=False
        )
    runner = asyncio.create_task(operation)
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), 3)
        runner.cancel()
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in cleaning)), 3)
        runner.cancel()
        await asyncio.sleep(0)
        runner.cancel()
        await asyncio.sleep(0.05)
        assert not runner.done(), "repeated cancellation must wait for process cleanup"
        assert not reaped
        assert sum(prober._probe_account_connections.values()) == count
        assert sum(prober._probe_profile_connections.values()) == count
    finally:
        release.set()
        await asyncio.gather(runner, return_exceptions=True)
        for process in processes:
            if process.returncode is None:
                process.kill()
            await process.wait()
    assert runner.cancelled()
    assert reaped == set(range(count))
    for process in processes:
        assert process.returncode == -9
        with pytest.raises(ProcessLookupError):
            os.kill(process.pid, 0)
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections
    assert not prober._bulk_probe_tasks


@pytest.mark.parametrize("lane", ["quality", "queued"])
@pytest.mark.parametrize("reassigned_to", ["provider", "accountless"])
async def test_pipeline_stale_url_cannot_borrow_reassigned_stream_capacity(
    capacity_probe, lane, reassigned_to
):
    from channel_pipeline_engine import ChannelPipelineEngine

    prober, account, stream = capacity_probe
    account["max_streams"] = account["profiles"][0]["max_streams"] = 1
    viewers(prober, 1, profile_id=12)
    fresh_account = deepcopy(account)
    fresh_account["id"] = 20
    fresh_account["profiles"][0].update(id=21, max_streams=2)
    fresh_account["max_streams"] = 2
    prober.client.get_m3u_accounts.return_value.append(fresh_account)
    prober.client.get_stream.side_effect = None
    prober.client.get_stream.return_value = {
        **stream,
        "url": "https://fresh-provider.invalid/new/live.ts",
        "m3u_account": 20 if reassigned_to == "provider" else None,
    }
    engine = ChannelPipelineEngine.__new__(ChannelPipelineEngine)
    engine._stream_stats_cache = {}
    engine._load_stream_stats = AsyncMock()
    ctx = SimpleNamespace(stream_id=7, stream_url=URL, stream_name="Old route")
    rule = SimpleNamespace(id=1, sort_field="quality", probe_on_sort=True)
    results = {"execution_log": [], "dry_run_results": []}
    with patch("stream_prober.get_prober", return_value=prober):
        if lane == "quality":
            await engine._probe_unprobed_streams(
                [(ctx, rule, [], [])], [rule], results, False
            )
        else:
            await engine._batch_probe_streams({7}, [ctx], results, False)
    prober.client.get_stream.assert_awaited_once_with(7)
    prober._run_ffprobe.assert_not_awaited()
    prober._measure_stream_bitrate.assert_not_awaited()
    prober._save_probe_result.assert_not_called()
    assert (
        "1 skipped"
        in results["execution_log"][-1]["actions_executed"][0]["description"]
    )
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections


ROUTE_IDENTITY_CASES = [
    (source, route, fault)
    for source in ("fetched", "cached")
    for route in ("provider", "accountless", "selected_identity", "selected_changed")
    for fault in (
        "none",
        "supplied_url",
        "fresh_id",
        "fresh_id_bool",
        "fresh_url_missing",
        "fresh_url_type",
        "fresh_account_bool",
        "cached_id",
        "cached_url",
        "cached_account",
        "cached_missing_id",
    )
    if source == "cached"
    or (not route.startswith("selected") and not fault.startswith("cached"))
]


@pytest.mark.parametrize("source,route,fault", ROUTE_IDENTITY_CASES)
async def test_shared_route_identity_matrix(capacity_probe, source, route, fault):
    prober, account, stream = capacity_probe
    fresh = deepcopy(stream)
    cached = deepcopy(stream)
    supplied_url = URL
    if route == "accountless":
        fresh["m3u_account"] = cached["m3u_account"] = None
    elif route.startswith("selected"):
        selected = deepcopy(account["profiles"][0])
        selected.update(
            id=13, is_default=False, search_pattern="^(.*)$", replace_pattern=r"\1"
        )
        if route == "selected_changed":
            selected.update(
                search_pattern="/user/password/", replace_pattern="/other/login/"
            )
            supplied_url = URL.replace("/user/password/", "/other/login/")
        account["profiles"].append(selected)
        cached["_selected_profile"] = selected
    if fault == "supplied_url":
        supplied_url = "https://old-route.invalid/old/live.ts"
    elif fault == "fresh_id":
        fresh["id"] = 8
    elif fault == "fresh_id_bool":
        fresh["id"] = True
    elif fault == "fresh_url_missing":
        del fresh["url"]
    elif fault == "fresh_url_type":
        fresh["url"] = [URL]
    elif fault == "fresh_account_bool":
        fresh["m3u_account"] = False
    elif fault == "cached_id":
        cached["id"] = 8
    elif fault == "cached_url":
        cached["url"] = "https://stale-cache.invalid/live.ts"
    elif fault == "cached_account":
        cached["m3u_account"] = None if route != "accountless" else 11
    elif fault == "cached_missing_id":
        del cached["id"]
    prober.client.get_stream.side_effect = None
    prober.client.get_stream.return_value = fresh
    result = await prober.probe_stream(
        7, supplied_url, stream=cached if source == "cached" else None
    )
    if fault == "none":
        assert result["probe_status"] == "success"
        prober._run_ffprobe.assert_awaited_once_with(
            supplied_url, user_agent="VLC/3.0.20 LibVLC/3.0.20"
        )
    else:
        assert_skipped(prober, result)
    prober.client.get_stream.assert_awaited_once_with(7)
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections


PROFILE_CLASSIFICATION_CASES = [
    (route, fault, subject)
    for route in ("unselected", "selected_identity", "selected_changed")
    for fault in (
        "str_false",
        "int_zero",
        "none",
        "missing",
        "duplicate_default",
        "missing_default",
        "active_string",
    )
    for subject in ("default", "alternate")
    if fault not in ("duplicate_default", "missing_default") or subject == "alternate"
]


@pytest.mark.parametrize("route,fault,subject", PROFILE_CLASSIFICATION_CASES)
async def test_shared_profile_classification_matrix(
    capacity_probe, route, fault, subject
):
    prober, account, stream = capacity_probe
    account["max_streams"] = 5
    selected = deepcopy(account["profiles"][0])
    selected.update(
        id=13, is_default=False, search_pattern="^(.*)$", replace_pattern=r"\1"
    )
    if route == "selected_changed":
        selected.update(
            search_pattern="/user/password/", replace_pattern="/other/login/"
        )
    account["profiles"].append(selected)
    cached = deepcopy(stream)
    if route != "unselected":
        cached["_selected_profile"] = {"id": 13}
    malformed = account["profiles"][0] if subject == "default" else selected
    values = {"str_false": "false", "int_zero": 0, "none": None}
    if fault in values:
        malformed["is_default"] = values[fault]
    elif fault == "missing":
        del malformed["is_default"]
    elif fault == "duplicate_default":
        selected["is_default"] = True
    elif fault == "missing_default":
        account["profiles"][0]["is_default"] = False
    else:
        malformed["is_active"] = "true"
    supplied_url = (
        URL.replace("/user/password/", "/other/login/")
        if route == "selected_changed"
        else URL
    )
    assert_skipped(prober, await prober.probe_stream(7, supplied_url, stream=cached))
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections


@pytest.mark.parametrize("url", [None, ""])
async def test_missing_url_preserves_failed_result_without_route_reads(
    capacity_probe, url
):
    prober, _, _ = capacity_probe
    result = await prober.probe_stream(7, url, stream={"id": "unverifiable"})
    assert result == {"probe_status": "failed", "error_message": "No URL available"}
    prober._save_probe_result.assert_called_once_with(
        7, None, None, "failed", "No URL available"
    )
    prober.client.get_stream.assert_not_awaited()
    prober.client.get_m3u_accounts.assert_not_awaited()
    prober._run_ffprobe.assert_not_awaited()


async def test_recorded_stream_shape_crosses_identity_and_capacity_validation(
    capacity_probe,
):
    prober, account, _ = capacity_probe
    recorded = json.loads(
        (FIXTURES / "dispatcharr_stream_provider_channel_number.json").read_text()
    )
    # Join recorded stream/account shapes by adapting only the fixture account
    # ID; this is a synthetic association, not a new live response capture.
    account["id"] = recorded["m3u_account"]
    account["profiles"][0]["account"]["id"] = account["id"]
    prober._fetch_all_streams.return_value = [recorded]
    result = await prober.probe_stream(recorded["id"], recorded["url"], stream=recorded)
    assert result["probe_status"] == "success"
    prober.client.get_stream.assert_awaited_once_with(recorded["id"])
    prober._run_ffprobe.assert_awaited_once_with(
        recorded["url"], user_agent="VLC/3.0.20 LibVLC/3.0.20"
    )
