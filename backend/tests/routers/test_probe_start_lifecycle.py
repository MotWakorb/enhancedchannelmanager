"""Real endpoint/prober ownership: accepted starts reserve before returning.

HTTP checks cover busy/reset/stopped instances; direct endpoint invocations
control the exact pre-worker tick. Media and control APIs remain synthetic.
"""

import asyncio
from unittest.mock import MagicMock

import pytest
import stream_prober
from routers import stream_stats

from tests.unit.test_stream_probe_capacity import prepare_probe_all, unlimited_probe

# Preserve the existing fixture name while exercising real unlimited admission.
capacity_probe = unlimited_probe

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("vlc_stream_user_agent")]


@pytest.fixture
async def endpoint_probe(capacity_probe, monkeypatch):
    prober, _, _ = capacity_probe
    prepare_probe_all(prober, True)
    monkeypatch.setattr(stream_stats, "ensure_prober", lambda: prober)
    monkeypatch.setattr(stream_stats, "task_start_alerts_enabled", lambda _task: False)
    reset = MagicMock(wraps=prober.force_reset_probe_state)
    monkeypatch.setattr(prober, "force_reset_probe_state", reset)
    try:
        yield prober
    finally:
        await prober.stop()


def run_batch(prober, kind):
    if kind == "all":
        return prober.probe_all_streams(skip_m3u_refresh=True)
    return prober.probe_streams_by_ids([7])


async def post_start(client, kind):
    body = {"skip_m3u_refresh": True} if kind == "all" else {"stream_ids": [7]}
    response = await client.post(f"/api/stream-stats/probe/{kind}", json=body)
    assert response.status_code == 200
    return response.json()


async def call_start(kind):
    if kind == "all":
        return await stream_stats.probe_all_streams_endpoint(
            stream_stats.ProbeAllRequest(skip_m3u_refresh=True)
        )
    return await stream_stats.probe_bulk_streams(
        stream_stats.BulkProbeRequest(stream_ids=[7])
    )


@pytest.mark.parametrize("owner_kind", ["all", "bulk"])
@pytest.mark.parametrize("requested_kind", ["all", "bulk"])
async def test_http_busy_start_does_not_reset_or_cancel_original_run(
    endpoint_probe, async_client, owner_kind, requested_kind
):
    prober = endpoint_probe
    started, release = asyncio.Event(), asyncio.Event()

    async def media(*_args, **_kwargs):
        started.set()
        await release.wait()
        return {"streams": []}

    prober._run_ffprobe.side_effect = media
    owner = asyncio.create_task(run_batch(prober, owner_kind))
    try:
        await asyncio.wait_for(started.wait(), 3)
        result = await post_start(async_client, requested_kind)
        assert result["status"] == "already_running"
        prober.force_reset_probe_state.assert_not_called()
        assert not prober._probe_cancelled
        assert not owner.done()
        assert prober._probe_run_task is owner
        assert prober._probe_account_connections == {11: 1}
        release.set()
        assert (await asyncio.wait_for(owner, 3))["status"] == "completed"
        assert prober._run_ffprobe.await_count == 1
    finally:
        release.set()
        if not owner.done():
            owner.cancel()
        await asyncio.wait_for(asyncio.gather(owner, return_exceptions=True), 3)


@pytest.mark.parametrize("owner_kind", ["all", "bulk"])
@pytest.mark.parametrize("requested_kind", ["all", "bulk"])
async def test_http_reset_owner_draining_is_busy_then_legitimate_start_succeeds(
    endpoint_probe, async_client, owner_kind, requested_kind
):
    prober = endpoint_probe
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def media(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    prober._run_ffprobe.side_effect = media
    owner = asyncio.create_task(run_batch(prober, owner_kind))
    successor = None
    release_successor = asyncio.Event()
    try:
        await asyncio.wait_for(started.wait(), 3)
        prober.force_reset_probe_state()
        owner.cancel()
        await asyncio.wait_for(cleaning.wait(), 3)
        assert not prober._probing_in_progress  # Reset is not owner release.
        result = await post_start(async_client, requested_kind)
        assert result["status"] == "already_running"
        assert prober._probe_run_task is owner
        assert prober.force_reset_probe_state.call_count == 1
        assert prober._probe_account_connections == {11: 1}
        release.set()
        await asyncio.wait_for(asyncio.gather(owner, return_exceptions=True), 3)
        successor_started = asyncio.Event()

        async def new_media(*_args, **_kwargs):
            successor_started.set()
            await release_successor.wait()
            return {"streams": []}

        prober._run_ffprobe.side_effect = new_media
        assert (await post_start(async_client, requested_kind))["status"] == "started"
        await asyncio.wait_for(successor_started.wait(), 3)
        successor = prober._probe_run_task
        assert successor is not None and successor is not owner
        release_successor.set()
        assert (await asyncio.wait_for(successor, 3))["status"] == "completed"
    finally:
        release.set()
        release_successor.set()
        tasks = [task for task in (owner, successor) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)


@pytest.mark.parametrize("kind", ["all", "bulk"])
async def test_http_stopped_reference_never_acknowledges_started(
    endpoint_probe, async_client, kind
):
    prober = endpoint_probe
    await prober.stop()
    result = await post_start(async_client, kind)
    assert result["status"] == "cancelled"
    assert "stopped" in result["message"]
    prober._fetch_all_streams.assert_not_awaited()
    prober._run_ffprobe.assert_not_awaited()
    prober.force_reset_probe_state.assert_not_called()


@pytest.fixture
async def held_queue(monkeypatch):
    """Delay real background tasks before their first production instruction."""
    release = asyncio.Event()
    tasks = []
    real_create_task = asyncio.create_task

    def create_task(operation):
        async def delayed():
            await release.wait()
            return await operation

        task = real_create_task(delayed())
        # Also close a coroutine whose task was cancelled before delayed began.
        task.add_done_callback(lambda _task: operation.close())
        tasks.append(task)
        return task

    monkeypatch.setattr(stream_prober.asyncio, "create_task", create_task)
    try:
        yield release, tasks
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)


@pytest.mark.parametrize("first", ["all", "bulk"])
@pytest.mark.parametrize("second", ["all", "bulk"])
@pytest.mark.parametrize("concurrent", [False, True])
async def test_endpoint_claim_precedes_same_tick_or_concurrent_start_acknowledgements(
    endpoint_probe, held_queue, first, second, concurrent
):
    prober = endpoint_probe
    release, tasks = held_queue
    if concurrent:
        responses = await asyncio.gather(call_start(first), call_start(second))
    else:
        responses = [await call_start(first), await call_start(second)]
    assert [response["status"] for response in responses] == [
        "started",
        "already_running",
    ]
    assert len(tasks) == 1
    assert prober._probe_run_task is tasks[0]
    assert (await prober.probe_all_streams(skip_m3u_refresh=True))[
        "status"
    ] == "already_running"
    assert (await prober.probe_streams_by_ids([7]))["status"] == "already_running"
    prober.force_reset_probe_state.assert_not_called()
    prober._run_ffprobe.assert_not_awaited()
    release.set()
    assert (await asyncio.wait_for(tasks[0], 3))["status"] == "completed"
    assert prober.get_probe_progress()["status"] == "completed"
    assert prober._probe_run_task is None
    assert len(prober.get_probe_history()) == 1


@pytest.mark.parametrize("kind", ["all", "bulk"])
@pytest.mark.parametrize("control", ["reset", "cancel", "stop"])
async def test_queued_start_reset_cancel_or_stop_cannot_leak_or_revive_ownership(
    endpoint_probe, held_queue, kind, control
):
    prober = endpoint_probe
    release, tasks = held_queue
    assert (await call_start(kind))["status"] == "started"
    if control == "stop":
        await prober.stop()
        assert (await call_start(kind))["status"] == "cancelled"
    else:
        if control == "reset":
            prober.force_reset_probe_state()
        else:
            assert prober.cancel_probe()["status"] == "cancelling"
        assert (await call_start(kind))["status"] == "already_running"
        release.set()
        assert (await asyncio.wait_for(tasks[0], 3))["status"] == "cancelled"
    prober._fetch_all_streams.assert_not_awaited()
    prober._run_ffprobe.assert_not_awaited()
    assert prober._probe_run_task is None
    if control != "stop":
        assert (await call_start(kind))["status"] == "started"
        assert (await asyncio.wait_for(tasks[-1], 3))["status"] == "completed"


@pytest.mark.parametrize("kind", ["all", "bulk"])
async def test_background_task_creation_failure_releases_start_admission(
    endpoint_probe, monkeypatch, kind
):
    prober = endpoint_probe
    real_create_task = asyncio.create_task
    with monkeypatch.context() as patcher:

        def cannot_schedule(_operation):
            raise RuntimeError("Disposable scheduler unavailable")

        patcher.setattr(stream_prober.asyncio, "create_task", cannot_schedule)
        with pytest.raises(RuntimeError, match="Disposable scheduler unavailable"):
            await call_start(kind)
    assert prober._probe_run_task is None
    assert not prober._probing_in_progress
    started, release = asyncio.Event(), asyncio.Event()

    async def media(*_args, **_kwargs):
        started.set()
        await release.wait()
        return {"streams": []}

    prober._run_ffprobe.side_effect = media
    task = None
    try:
        assert (await call_start(kind))["status"] == "started"
        await asyncio.wait_for(started.wait(), 3)
        task = prober._probe_run_task
        assert task is not None
        release.set()
        assert (await asyncio.wait_for(task, 3))["status"] == "completed"
    finally:
        release.set()
        if task is not None:
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)
    assert asyncio.create_task is real_create_task


@pytest.mark.parametrize("kind", ["all", "bulk"])
async def test_eager_worker_cannot_finish_before_the_start_claim_is_installed(
    endpoint_probe, kind
):
    prober = endpoint_probe
    prober._fetch_channel_stream_ids.return_value = (set(), {}, {})
    prober._fetch_all_streams.return_value = []
    loop = asyncio.get_running_loop()
    previous_factory = loop.get_task_factory()
    task = None
    try:
        loop.set_task_factory(asyncio.eager_task_factory)
        assert (await call_start(kind))["status"] == "started"
        task = prober._probe_run_task
        assert task is not None
        await asyncio.wait_for(task, 3)
        assert prober.get_probe_progress()["status"] == "completed"
        assert prober.get_probe_history()[0]["status"] == "completed"
        assert prober._probe_run_task is None
    finally:
        loop.set_task_factory(previous_factory)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)
