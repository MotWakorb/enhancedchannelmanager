"""Black screen scan must bound probe fan-out, like probe_streams_by_ids.

Regression: the scan started one coroutine per stream (2000+ in production), so
probe admission ran for all of them at once and flooded the 20-connection
Dispatcharr client with wait_for-wrapped API calls. Mass cancellation leaked httpx
pool slots and every Dispatcharr call in ECM then failed with PoolTimeout until
the container was restarted.
"""

import asyncio
from types import SimpleNamespace

import pytest
from models import StreamStats
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.asyncio

ACCOUNTS = (1, 2)
PER_ACCOUNT = 100
MAX_CONCURRENT = 3


@pytest.fixture
def scan_setup(test_engine, monkeypatch):
    sessions = sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr("tasks.black_screen_scan.get_session", sessions)
    streams = []
    with sessions() as session:
        for account in ACCOUNTS:
            for n in range(PER_ACCOUNT):
                sid = account * 1000 + n
                streams.append({"id": sid, "name": f"S{sid}", "url": f"http://x/{sid}", "m3u_account": account})
                session.add(StreamStats(stream_id=sid, stream_name=f"S{sid}", probe_status="success"))
        session.commit()

    state = {"in_flight": 0, "peak": 0, "calls": 0, "per_account_peak": {}, "per_account": {}}

    async def fetch_all_streams():
        return streams

    async def probe_stream(stream_id, url, name, **kwargs):
        account = stream_id // 1000
        state["calls"] += 1
        state["in_flight"] += 1
        state["per_account"][account] = state["per_account"].get(account, 0) + 1
        state["peak"] = max(state["peak"], state["in_flight"])
        state["per_account_peak"][account] = max(
            state["per_account_peak"].get(account, 0), state["per_account"][account]
        )
        await asyncio.sleep(0)  # let every other started coroutine run
        state["in_flight"] -= 1
        state["per_account"][account] -= 1
        return {"probe_status": "success", "is_black_screen": False}

    prober = SimpleNamespace(
        _probing_in_progress=False,
        _fetch_all_streams=fetch_all_streams,
        probe_stream=probe_stream,
        black_screen_sample_duration=5,
        auto_reorder_after_probe=False,
        _extract_m3u_account_id=lambda value: value,
    )
    return prober, state


async def test_scan_bounds_concurrent_probes_per_account(scan_setup):
    from tasks.black_screen_scan import BlackScreenScanTask

    prober, state = scan_setup
    task = BlackScreenScanTask()
    task.set_prober(prober)
    task._max_concurrent_override = MAX_CONCURRENT

    result = await task.execute()

    assert result.success, result.message
    assert state["calls"] == len(ACCOUNTS) * PER_ACCOUNT
    assert result.success_count == len(ACCOUNTS) * PER_ACCOUNT
    # Before the fix every stream was in flight at once (peak == 200).
    assert state["peak"] <= len(ACCOUNTS) * MAX_CONCURRENT
    assert all(peak <= MAX_CONCURRENT for peak in state["per_account_peak"].values())


async def test_cancel_stops_remaining_work(scan_setup):
    from tasks.black_screen_scan import BlackScreenScanTask

    prober, state = scan_setup
    task = BlackScreenScanTask()
    task.set_prober(prober)
    task._max_concurrent_override = MAX_CONCURRENT
    original = prober.probe_stream

    async def probe_then_cancel(*args, **kwargs):
        task._cancel_requested = True
        return await original(*args, **kwargs)

    prober.probe_stream = probe_then_cancel
    await task.execute()

    # Each worker finishes at most the stream it already started.
    assert state["calls"] <= len(ACCOUNTS) * MAX_CONCURRENT
