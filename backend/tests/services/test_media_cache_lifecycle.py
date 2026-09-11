"""Shared lifecycle contract for unchanged provider caches (eal0l.2).

Patch module-local references, restoring prior singleton identities on teardown;
no network, global clock patch, shared metrics registry, or persistent settings.
"""
import asyncio
from contextlib import nullcontext
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def providers(monkeypatch):
    cases = []
    for name, client_name in [('emby', 'Emby'), ('plex', 'Plex'), ('jellyfin', 'Jellyfin')]:
        module = import_module(f'services.{name}_cache')
        settings = SimpleNamespace(**{
            f'{name}_enabled': True, f'{name}_base_url': f'http://{name}.invalid',
            f'{name}_api_key': 'synthetic', f'{name}_token': 'synthetic',
        })
        clock = SimpleNamespace(now=100.0)
        sessions = [SimpleNamespace(now_playing_item_name=name, now_playing_queue_item_id=None)]
        client = SimpleNamespace(get_sessions=AsyncMock(return_value=sessions), close=AsyncMock())
        constructor = Mock(return_value=client)
        monkeypatch.setattr(module, '_cached_entry', None)
        monkeypatch.setattr(module, '_fetch_lock', None)
        monkeypatch.setattr(module, 'get_settings', lambda s=settings: s)
        monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda c=clock: c.now))
        monkeypatch.setattr(module, 'observability', Mock())
        # Metric timers need a context manager; all metrics stay case-local.
        module.observability.get_metric.return_value.labels.return_value.time.side_effect = nullcontext
        monkeypatch.setattr(module, f'{client_name}Client', constructor)
        cases.append(SimpleNamespace(
            name=name, module=module, settings=settings, clock=clock, sessions=sessions,
            client=client, constructor=constructor,
            error=getattr(module, f'{client_name}ClientError'),
            get=getattr(module, f'get_cached_{name}_sessions'),
        ))
    return cases


@pytest.fixture(params=range(3), ids=['emby', 'plex', 'jellyfin'])
def cache(request, providers):
    return providers[request.param]


async def test_monotonic_exact_ttl_and_store_after_close(cache):
    async def close():
        assert cache.module._cached_entry is None
        cache.clock.now = 102.0
    cache.client.close.side_effect = close
    first = await cache.get()
    assert first == cache.sessions
    assert cache.module._cached_entry.cached_at == 102.0
    cache.client.close.side_effect = None
    cache.clock.now = 106.999
    assert await cache.get() is first
    assert cache.client.get_sessions.await_count == 1
    cache.clock.now = 107.0
    await cache.get()
    assert cache.client.get_sessions.await_count == 2
    assert cache.client.close.await_count == 2


@pytest.mark.parametrize('setting,value', [('enabled', False), ('base_url', ''), ('base_url', None)])
async def test_disabled_or_unconfigured_does_not_touch_state(cache, setting, value):
    cache.module._store_entry(cache.sessions)
    entry = cache.module._cached_entry
    setattr(cache.settings, f'{cache.name}_{setting}', value)
    assert await cache.get() == []
    assert cache.module._cached_entry is entry
    assert cache.module._fetch_lock is None
    cache.constructor.assert_not_called()


async def test_concurrent_misses_recheck_under_lock(cache):
    entered, release = asyncio.Event(), asyncio.Event()
    async def fetch():
        entered.set()
        await release.wait()
        return cache.sessions
    cache.client.get_sessions.side_effect = fetch
    tasks = [asyncio.create_task(cache.get()) for _ in range(20)]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert all(result == cache.sessions for result in results)
        assert cache.constructor.call_count == 1
        assert cache.client.get_sessions.await_count == 1
        assert cache.client.close.await_count == 1
        assert not cache.module._get_lock().locked()
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize('stale', [False, True])
@pytest.mark.parametrize('failure', ['provider', 'unexpected', 'cancel'])
async def test_fetch_failures_preserve_timestamp_and_retry(cache, stale, failure):
    if stale:
        await cache.get()
    entry = cache.module._cached_entry
    cache.clock.now += 5.0
    error = {'provider': cache.error('synthetic'), 'unexpected': RuntimeError('synthetic'),
             'cancel': asyncio.CancelledError()}[failure]
    cache.client.get_sessions.side_effect = error
    for _ in range(2):
        before = cache.client.get_sessions.await_count
        if failure == 'provider':
            assert await cache.get() == (cache.sessions if stale else [])
        else:
            with pytest.raises(type(error)):
                await cache.get()
        assert cache.client.get_sessions.await_count == before + 1
        assert cache.client.close.await_count == cache.client.get_sessions.await_count
        assert cache.module._cached_entry is entry
        if entry:
            assert entry.cached_at == 100.0
        assert not cache.module._get_lock().locked()
    cache.client.get_sessions.side_effect = None
    assert await cache.get() == cache.sessions


async def test_queued_cancellation_does_not_construct_or_close_client(cache):
    lock = cache.module._get_lock()
    await lock.acquire()
    task = asyncio.create_task(cache.get())
    try:
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        cache.constructor.assert_not_called()
        cache.client.close.assert_not_awaited()
        assert lock.locked()
    finally:
        lock.release()
    assert await cache.get() == cache.sessions


@pytest.mark.parametrize('stale', [False, True])
@pytest.mark.parametrize('failure', ['provider', 'unexpected', 'cancel'])
async def test_close_failure_policy_does_not_store_success(cache, stale, failure):
    if stale:
        await cache.get()
    entry = cache.module._cached_entry
    cache.clock.now += 5.0
    error = {'provider': cache.error('close'), 'unexpected': RuntimeError('close'),
             'cancel': asyncio.CancelledError()}[failure]
    cache.client.close.side_effect = error
    if failure == 'provider':
        assert await cache.get() == (cache.sessions if stale else [])
    else:
        with pytest.raises(type(error)):
            await cache.get()
    assert cache.module._cached_entry is entry
    assert cache.client.close.await_count == cache.client.get_sessions.await_count
    assert not cache.module._get_lock().locked()
    cache.client.close.side_effect = None
    assert await cache.get() == cache.sessions


async def test_providers_have_distinct_cache_lock_and_client_resources(providers):
    for cache in providers:
        assert await cache.get() == cache.sessions
    assert len({id(c.module._cached_entry) for c in providers}) == 3
    assert len({id(c.module._get_lock()) for c in providers}) == 3
    for cache in providers:
        assert await cache.get() == cache.sessions
        cache.constructor.assert_called_once()
        assert cache.module._cached_entry.sessions == cache.sessions


async def test_cancelling_fetch_holder_releases_lock_for_next_caller(cache):
    entered = asyncio.Event()
    async def fetch():
        entered.set()
        await asyncio.Event().wait()
    cache.client.get_sessions.side_effect = fetch
    holder = asyncio.create_task(cache.get())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        holder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await holder
        cache.client.close.assert_awaited_once()
        assert cache.module._cached_entry is None
        assert not cache.module._get_lock().locked()
        cache.client.get_sessions.side_effect = None
        assert await asyncio.wait_for(cache.get(), 1) == cache.sessions
    finally:
        holder.cancel()
        await asyncio.gather(holder, return_exceptions=True)


async def test_jellyfin_enrichment_precedes_close_and_store_without_mutating_input(providers):
    from jellyfin_client import JellyfinSession

    cache = providers[2]
    sessions = [JellyfinSession(
        session_id=str(i), user_id='user', user_name='synthetic', remote_endpoint='127.0.0.1',
        now_playing_item_name=None, now_playing_channel_name=None,
        last_activity_date='2026-01-01T00:00:00Z', now_playing_queue_item_id=str(i),
    ) for i in range(2)]
    cache.client.get_sessions.return_value = sessions
    # get_user_item owns per-item transport failure -> None. One failed lookup
    # must not prevent the following session from being enriched.
    cache.client.get_user_item = AsyncMock(side_effect=[None, {'Name': 'Channel', 'ChannelNumber': '7'}])
    async def close():
        assert cache.client.get_user_item.await_count == 2
        assert cache.module._cached_entry is None
        assert all(s.now_playing_item_name is None for s in sessions)
    cache.client.close.side_effect = close
    result = await cache.get()
    assert result is not sessions
    assert result[0] is sessions[0]
    assert result[1] is not sessions[1]
    assert result[1].now_playing_item_name == 'Channel'
    assert result[1].channel_number == '7'
    assert cache.module._cached_entry.sessions is result
    assert sessions[1].now_playing_item_name is None
