import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import sessionmaker

import tasks.epg_event_probe as epg_event_probe
from tasks.epg_event_probe import EPGEventProbeTask


NOW = datetime(2026, 9, 3, 16, 30, tzinfo=timezone.utc)


def _event(**overrides):
    return {
        "id": 501,
        "start_time": "2026-09-03T16:00:00Z",
        "end_time": "2026-09-03T17:00:00Z",
        "title": "Premier League Live",
        "tvg_id": "sports.example",
        **overrides,
    }


def _task(
    *,
    events=None,
    channels=None,
    epg_data=None,
    claim_trigger=None,
    release_claims=None,
    persistent_claims=False,
):
    if not persistent_claims and claim_trigger is None:
        claimed = set()

        def claim_trigger(key):
            if key in claimed:
                return False
            claimed.add(key)
            return True

        release_claims = lambda keys: claimed.difference_update(keys)
    client = SimpleNamespace(
        get_epg_grid=AsyncMock(return_value=list(events or [])),
        get_epg_data=AsyncMock(return_value=list(epg_data or [])),
        get_channels=AsyncMock(return_value={
            "results": list(channels or []),
            "next": None,
        }),
        get_channel=AsyncMock(side_effect=lambda channel_id: next(
            channel for channel in (channels or []) if channel["id"] == channel_id
        )),
    )
    prober = SimpleNamespace(
        client=client,
        _probing_in_progress=False,
        probe_streams_by_ids=AsyncMock(return_value={
            "status": "completed",
            "probed": 2,
            "total": 2,
            "success": 2,
            "failed": 0,
        }),
    )
    task = EPGEventProbeTask(
        now_fn=lambda: NOW,
        claim_trigger=claim_trigger,
        release_claims=release_claims,
    )
    task.set_prober(prober)
    task._enabled = True
    task.prepare_invocation_parameters(
        "scheduled",
        17,
        {"title_pattern": "Premier League", "allow_reorder_after_probe": False},
    )
    task.update_config({
        "title_pattern": "Premier League",
        "allow_reorder_after_probe": False,
    })
    return task, prober, client


@pytest.mark.asyncio
async def test_matching_active_event_probes_every_stream_on_resolved_channel_once():
    channel = {
        "id": 42,
        "uuid": "channel-uuid",
        "name": "Sports",
        "tvg_id": None,
        "epg_data_id": 88,
        "streams": [10, 11, 10],
    }
    task, prober, client = _task(
        events=[_event()],
        channels=[channel],
        epg_data=[{"id": 88, "tvg_id": "sports.example"}],
    )

    result = await task.execute()

    assert result.success is True
    assert result.total_items == 2
    prober.probe_streams_by_ids.assert_awaited_once_with(
        [10, 11],
        start_send_alerts=True,
        allow_reorder_after_probe=False,
    )
    client.get_epg_data.assert_awaited_once_with(max_results=50001)
    assert len(result.details["trigger_keys"]) == 1
    assert result.details["matched_channels"] == [42]


@pytest.mark.parametrize(
    "events",
    [
        [_event(title="Post-match analysis")],
        [],
        [_event(start_time="2026-09-03T16:31:00Z")],
        [_event(end_time="2026-09-03T16:30:00Z")],
    ],
    ids=["non-matching", "missing-epg", "not-started", "ended"],
)
@pytest.mark.asyncio
async def test_non_triggering_event_states_do_not_probe(events):
    task, prober, _ = _task(
        events=events,
        channels=[{
            "id": 42,
            "uuid": "channel-uuid",
            "tvg_id": "sports.example",
            "streams": [10],
        }],
    )

    result = await task.execute()

    assert result.success is True
    assert result.total_items == 0
    prober.probe_streams_by_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_trigger_does_not_read_epg_or_probe():
    task, prober, client = _task(events=[_event()])
    task._enabled = False

    result = await task.execute()

    assert result.success is True
    client.get_epg_grid.assert_not_awaited()
    prober.probe_streams_by_ids.assert_not_awaited()


def test_malformed_title_expression_is_rejected_at_configuration_boundary():
    with pytest.raises(ValueError, match="title_pattern must be a valid regex"):
        EPGEventProbeTask.validate_schedule_parameters({"title_pattern": "["})


@pytest.mark.asyncio
async def test_duplicate_schedule_evaluation_does_not_probe_same_event_channel_twice():
    claimed = set()

    def claim_trigger(key):
        if key in claimed:
            return False
        claimed.add(key)
        return True

    task, prober, _ = _task(
        events=[_event()],
        channels=[{
            "id": 42,
            "uuid": "channel-uuid",
            "tvg_id": "sports.example",
            "streams": [10, 11],
        }],
        claim_trigger=claim_trigger,
        release_claims=lambda keys: claimed.difference_update(keys),
    )

    first = await task.execute()
    second = await task.execute()

    assert first.total_items == 2
    assert second.total_items == 0
    prober.probe_streams_by_ids.assert_awaited_once()


@pytest.mark.asyncio
async def test_interrupted_probe_claim_survives_task_restart(test_engine, monkeypatch):
    session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(epg_event_probe, "get_session", session_factory)
    channels = [{
        "id": 42,
        "uuid": "channel-uuid",
        "tvg_id": "sports.example",
        "streams": [10, 11],
    }]
    interrupted, interrupted_prober, _ = _task(
        events=[_event()],
        channels=channels,
        persistent_claims=True,
    )
    interrupted_prober.probe_streams_by_ids.side_effect = KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        await interrupted.execute()

    restarted, restarted_prober, _ = _task(
        events=[_event()], channels=channels, persistent_claims=True
    )
    result = await restarted.execute()

    assert result.total_items == 0
    restarted_prober.probe_streams_by_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_tasks_atomically_claim_event_channel_once(test_engine, monkeypatch):
    session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(epg_event_probe, "get_session", session_factory)
    channels = [{
        "id": 42,
        "uuid": "channel-uuid",
        "tvg_id": "sports.example",
        "streams": [10, 11],
    }]
    first, first_prober, _ = _task(
        events=[_event()], channels=channels, persistent_claims=True
    )
    second, second_prober, _ = _task(
        events=[_event()], channels=channels, persistent_claims=True
    )

    results = await asyncio.gather(first.execute(), second.execute())

    assert sorted(result.total_items for result in results) == [0, 2]
    assert (
        first_prober.probe_streams_by_ids.await_count
        + second_prober.probe_streams_by_ids.await_count
    ) == 1


@pytest.mark.asyncio
async def test_already_running_refusal_releases_claim_for_retry():
    claimed = set()

    def claim_trigger(key):
        if key in claimed:
            return False
        claimed.add(key)
        return True

    task, prober, _ = _task(
        events=[_event()],
        channels=[{
            "id": 42,
            "uuid": "channel-uuid",
            "tvg_id": "sports.example",
            "streams": [10],
        }],
        claim_trigger=claim_trigger,
        release_claims=lambda keys: claimed.difference_update(keys),
    )
    prober.probe_streams_by_ids.side_effect = [
        {"status": "already_running"},
        {"status": "completed", "total": 1, "success": 1, "failed": 0},
    ]

    first = await task.execute()
    second = await task.execute()

    assert first.error == "ALREADY_RUNNING"
    assert second.success is True
    assert prober.probe_streams_by_ids.await_count == 2


@pytest.mark.asyncio
async def test_channel_uuid_resolves_dummy_epg_event_without_epg_data_lookup():
    task, prober, client = _task(
        events=[_event(tvg_id="channel-uuid")],
        channels=[{
            "id": 42,
            "uuid": "channel-uuid",
            "tvg_id": None,
            "epg_data_id": None,
            "streams": [10],
        }],
    )

    await task.execute()

    prober.probe_streams_by_ids.assert_awaited_once()


@pytest.mark.asyncio
async def test_broad_match_resolves_with_one_epg_fetch_and_one_channel_index_pass():
    class CountingChannel(dict):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.resolution_reads = 0

        def get(self, key, default=None):
            if key in {"epg_data_id", "tvg_id", "uuid"}:
                self.resolution_reads += 1
            return super().get(key, default)

    channels = [
        CountingChannel(id=1, epg_data_id=88, tvg_id="wrong", streams=[30, 10]),
        CountingChannel(
            id=2, epg_data_id=None, tvg_id="sports.example", streams=[10, 20]
        ),
        CountingChannel(
            id=3,
            epg_data_id=None,
            tvg_id=None,
            uuid="news.example",
            streams=[20, 40],
        ),
    ]
    claimed = set()
    task, prober, client = _task(
        events=[
            _event(id=501, tvg_id="sports.example"),
            _event(id=502, tvg_id="sports.example"),
            _event(id=503, tvg_id="news.example"),
        ],
        channels=channels,
        epg_data=[{"id": 88, "tvg_id": "sports.example"}],
        claim_trigger=lambda key: key not in claimed and not claimed.add(key),
        release_claims=lambda keys: claimed.difference_update(keys),
    )

    await task.execute()

    client.get_epg_data.assert_awaited_once_with(max_results=50001)
    assert [channel.resolution_reads for channel in channels] == [1, 3, 3]
    prober.probe_streams_by_ids.assert_awaited_once_with(
        [30, 10, 20, 40],
        start_send_alerts=True,
        allow_reorder_after_probe=False,
    )


@pytest.mark.asyncio
async def test_epg_identity_listing_over_existing_ceiling_fails_closed():
    task, prober, client = _task(
        events=[_event()],
        channels=[{"id": 42, "epg_data_id": 88, "streams": [10]}],
        epg_data=[{"id": index, "tvg_id": f"channel-{index}"} for index in range(50001)],
    )

    with pytest.raises(
        RuntimeError,
        match="EPG data pagination exceeded the 50000-row safety limit",
    ):
        await task.execute()

    client.get_epg_data.assert_awaited_once_with(max_results=50001)
    prober.probe_streams_by_ids.assert_not_awaited()
