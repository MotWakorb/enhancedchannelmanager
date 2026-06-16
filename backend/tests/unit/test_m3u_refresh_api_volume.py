"""
Unit tests for M3U refresh API-call-volume optimizations (bead iwfr7).

Covers:
- get_streams_grouped: single bulk paginated pull replaces per-group probes,
  with correct client-side counting, enabled-only name capture, and the
  per-group name cap.
- M3URefreshTask.execute() gating: skip the capture sweep on a definitive
  "no change" (timestamp existed and never moved), but NEVER skip when the
  signal is uncertain (timestamp moved, or no usable timestamp -> fallback).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tasks.m3u_refresh import (
    get_streams_grouped,
    capture_m3u_changes,
    M3URefreshTask,
    MAX_STREAM_NAMES,
    STREAM_PULL_PAGE_SIZE,
)


def _paged_streams(rows, page_size=STREAM_PULL_PAGE_SIZE):
    """Build a fake get_streams side_effect that paginates `rows` like Dispatcharr.

    Returns an async callable matching api_client.get_streams(page=, page_size=, ...).
    """
    total = len(rows)

    async def _get_streams(page=1, page_size=page_size, **kwargs):
        start = (page - 1) * page_size
        end = start + page_size
        return {"count": total, "results": rows[start:end]}

    return _get_streams


@pytest.mark.asyncio
async def test_get_streams_grouped_counts_and_names():
    """Counts cover all groups; names captured only for enabled groups."""
    # group_lookup: id -> name. 3 groups, one disabled (Movies).
    group_lookup = {1: "Sports", 2: "News", 3: "Movies"}
    enabled = {"Sports", "News"}  # Movies disabled

    rows = (
        [{"channel_group": 1, "name": f"Sports {i}"} for i in range(3)]
        + [{"channel_group": 2, "name": f"News {i}"} for i in range(2)]
        + [{"channel_group": 3, "name": f"Movie {i}"} for i in range(4)]
    )

    client = MagicMock()
    client.get_streams = AsyncMock(side_effect=_paged_streams(rows))

    counts, names = await get_streams_grouped(client, 7, group_lookup, enabled)

    # Counts: every group with streams, enabled or not.
    assert counts == {"Sports": 3, "News": 2, "Movies": 4}
    # Names: only enabled groups.
    assert set(names.keys()) == {"Sports", "News"}
    assert names["Sports"] == ["Sports 0", "Sports 1", "Sports 2"]
    assert names["News"] == ["News 0", "News 1"]
    assert "Movies" not in names


@pytest.mark.asyncio
async def test_get_streams_grouped_is_single_paginated_pull_not_per_group():
    """One paginated loop, NOT one call per group: 9 rows / page_size>9 == 1 call."""
    group_lookup = {1: "A", 2: "B", 3: "C"}
    enabled = {"A", "B", "C"}
    rows = [{"channel_group": (i % 3) + 1, "name": f"s{i}"} for i in range(9)]

    client = MagicMock()
    client.get_streams = AsyncMock(side_effect=_paged_streams(rows))

    await get_streams_grouped(client, 1, group_lookup, enabled)

    # All 9 rows fit in one page (page_size=500), so exactly ONE get_streams call.
    assert client.get_streams.call_count == 1
    # And it must NOT be a per-group call (no channel_group_name filter).
    _, kwargs = client.get_streams.call_args
    assert "channel_group_name" not in kwargs
    assert kwargs["m3u_account"] == 1


@pytest.mark.asyncio
async def test_get_streams_grouped_paginates_when_over_page_size():
    """A group with > page_size streams is pulled across multiple pages and the
    name list is capped at MAX_STREAM_NAMES."""
    group_lookup = {1: "Big"}
    enabled = {"Big"}
    n = STREAM_PULL_PAGE_SIZE + 250  # forces a second page
    rows = [{"channel_group": 1, "name": f"s{i}"} for i in range(n)]

    client = MagicMock()
    client.get_streams = AsyncMock(side_effect=_paged_streams(rows))

    counts, names = await get_streams_grouped(client, 1, group_lookup, enabled)

    # Count reflects ALL rows.
    assert counts["Big"] == n
    # Two pages were needed.
    assert client.get_streams.call_count == 2
    # Names capped at MAX_STREAM_NAMES.
    assert len(names["Big"]) == MAX_STREAM_NAMES


@pytest.mark.asyncio
async def test_get_streams_grouped_skips_unknown_group_ids():
    """Streams whose channel_group isn't in group_lookup are ignored (parity)."""
    group_lookup = {1: "Known"}
    enabled = {"Known"}
    rows = [
        {"channel_group": 1, "name": "a"},
        {"channel_group": 999, "name": "orphan"},  # unknown group id
    ]
    client = MagicMock()
    client.get_streams = AsyncMock(side_effect=_paged_streams(rows))

    counts, names = await get_streams_grouped(client, 1, group_lookup, enabled)

    assert counts == {"Known": 1}
    assert names == {"Known": ["a"]}


@pytest.mark.asyncio
async def test_capture_m3u_changes_uses_bulk_pull_not_count_probes():
    """capture_m3u_changes must NOT call get_stream_groups_with_counts and must
    issue a bounded number of get_streams calls (one paginated loop)."""
    account_data = {
        "channel_groups": [
            {"channel_group": 1, "enabled": True},
            {"channel_group": 2, "enabled": False},
        ]
    }
    all_groups = [{"id": 1, "name": "Sports"}, {"id": 2, "name": "Movies"}]
    rows = (
        [{"channel_group": 1, "name": f"sp{i}"} for i in range(3)]
        + [{"channel_group": 2, "name": f"mv{i}"} for i in range(5)]
    )

    client = MagicMock()
    client.get_streams = AsyncMock(side_effect=_paged_streams(rows))
    client.get_stream_groups_with_counts = AsyncMock(
        side_effect=AssertionError("count-probe path must not be used")
    )

    detector = MagicMock()
    change_set = MagicMock()
    change_set.has_changes = False
    detector.detect_changes.return_value = change_set

    fake_db = MagicMock()

    with patch("tasks.m3u_refresh.get_client", return_value=client), \
         patch("database.get_session", return_value=fake_db), \
         patch("m3u_change_detector.M3UChangeDetector", return_value=detector):
        await capture_m3u_changes(
            account_id=7,
            account_name="Prov",
            account_data=account_data,
            all_channel_groups=all_groups,
        )

    # No count-probe call happened (AsyncMock side_effect would have raised).
    client.get_stream_groups_with_counts.assert_not_called()
    # Single paginated pull (8 rows < page_size) == 1 get_streams call.
    assert client.get_streams.call_count == 1

    # Contract to detect_changes: counts for ALL groups, names for ENABLED only.
    _, kwargs = detector.detect_changes.call_args
    groups = {g["name"]: g for g in kwargs["current_groups"]}
    assert groups["Sports"]["stream_count"] == 3
    assert groups["Movies"]["stream_count"] == 5
    assert kwargs["current_total_streams"] == 8
    assert set(kwargs["stream_names_by_group"].keys()) == {"Sports"}


@pytest.mark.asyncio
async def test_capture_m3u_changes_captures_is_stale_and_enabled_from_channel_groups():
    """enhancedchannelmanager-05h8w: capture_m3u_changes pulls the
    authoritative ``enabled`` + ``is_stale`` flags from channel_groups[]
    into current_groups, while stream_count stays sourced from the
    post-refresh bulk pull (NOT from channel_groups[])."""
    account_data = {
        "channel_groups": [
            # channel_groups[] carries a stream_count that MUST be ignored
            # for the count field (bulk pull is authoritative) but enabled
            # + is_stale ARE taken from here.
            {"channel_group": 1, "enabled": True, "is_stale": False, "stream_count": 999},
            {"channel_group": 2, "enabled": False, "is_stale": True, "stream_count": 999},
        ]
    }
    all_groups = [{"id": 1, "name": "Sports"}, {"id": 2, "name": "Movies"}]
    rows = (
        [{"channel_group": 1, "name": f"sp{i}"} for i in range(3)]
        + [{"channel_group": 2, "name": f"mv{i}"} for i in range(5)]
    )

    client = MagicMock()
    client.get_streams = AsyncMock(side_effect=_paged_streams(rows))

    detector = MagicMock()
    change_set = MagicMock()
    change_set.has_changes = False
    detector.detect_changes.return_value = change_set

    with patch("tasks.m3u_refresh.get_client", return_value=client), \
         patch("database.get_session", return_value=MagicMock()), \
         patch("m3u_change_detector.M3UChangeDetector", return_value=detector):
        await capture_m3u_changes(
            account_id=7,
            account_name="Prov",
            account_data=account_data,
            all_channel_groups=all_groups,
        )

    _, kwargs = detector.detect_changes.call_args
    groups = {g["name"]: g for g in kwargs["current_groups"]}
    # enabled + is_stale come from channel_groups[].
    assert groups["Sports"]["enabled"] is True
    assert groups["Sports"]["is_stale"] is False
    assert groups["Movies"]["enabled"] is False
    assert groups["Movies"]["is_stale"] is True
    # stream_count stays from the bulk pull, NOT channel_groups[].stream_count (999).
    assert groups["Sports"]["stream_count"] == 3
    assert groups["Movies"]["stream_count"] == 5


@pytest.mark.asyncio
async def test_capture_m3u_changes_is_stale_defaults_false_when_absent():
    """channel_groups[] entries without an is_stale key default to
    False (backward compatible with older Dispatcharr payloads)."""
    account_data = {"channel_groups": [{"channel_group": 1, "enabled": True}]}
    all_groups = [{"id": 1, "name": "Sports"}]
    rows = [{"channel_group": 1, "name": "sp0"}]

    client = MagicMock()
    client.get_streams = AsyncMock(side_effect=_paged_streams(rows))

    detector = MagicMock()
    change_set = MagicMock()
    change_set.has_changes = False
    detector.detect_changes.return_value = change_set

    with patch("tasks.m3u_refresh.get_client", return_value=client), \
         patch("database.get_session", return_value=MagicMock()), \
         patch("m3u_change_detector.M3UChangeDetector", return_value=detector):
        await capture_m3u_changes(
            account_id=7,
            account_name="Prov",
            account_data=account_data,
            all_channel_groups=all_groups,
        )

    _, kwargs = detector.detect_changes.call_args
    groups = {g["name"]: g for g in kwargs["current_groups"]}
    assert groups["Sports"]["is_stale"] is False


@pytest.mark.asyncio
async def test_capture_m3u_changes_fetches_when_no_prefetch_given():
    """When called standalone (change monitor path) it fetches account + groups."""
    client = MagicMock()
    client.get_m3u_account = AsyncMock(return_value={"channel_groups": []})
    client.get_channel_groups = AsyncMock(return_value=[])
    client.get_streams = AsyncMock(side_effect=_paged_streams([]))

    detector = MagicMock()
    cs = MagicMock()
    cs.has_changes = False
    detector.detect_changes.return_value = cs

    with patch("tasks.m3u_refresh.get_client", return_value=client), \
         patch("database.get_session", return_value=MagicMock()), \
         patch("m3u_change_detector.M3UChangeDetector", return_value=detector):
        await capture_m3u_changes(account_id=3, account_name="Standalone")

    client.get_m3u_account.assert_awaited_once_with(3)
    client.get_channel_groups.assert_awaited_once()


# ---------------------------------------------------------------------------
# Fix 2: execute() gating on a real change.
# ---------------------------------------------------------------------------

def _make_task():
    task = M3URefreshTask()
    task.account_ids = []
    task.skip_inactive = True
    return task


async def _run_execute_with_timestamps(initial_ts, polled_ts):
    """Run execute() against one account whose updated_at goes initial -> polled.

    Returns (capture_mock, captured_kwargs_or_None).
    """
    account = {"id": 1, "name": "Prov", "is_active": True}

    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=[account])
    client.get_channel_groups = AsyncMock(return_value=[{"id": 1, "name": "G"}])
    client.refresh_m3u_account = AsyncMock(return_value=None)

    # get_m3u_account: first call (initial) then poll call (polled).
    client.get_m3u_account = AsyncMock(side_effect=[
        {"id": 1, "updated_at": initial_ts, "channel_groups": []},
        {"id": 1, "updated_at": polled_ts, "channel_groups": []},
    ])

    capture = AsyncMock(return_value=None)

    # Patch sleep so the poll loop runs instantly. Drive "elapsed" so the 30s
    # fallback only triggers when we want it to (after the timestamp check).
    with patch("tasks.m3u_refresh.get_client", return_value=client), \
         patch("tasks.m3u_refresh.capture_m3u_changes", capture), \
         patch("tasks.m3u_refresh.POLL_INTERVAL_SECONDS", 0), \
         patch("tasks.m3u_refresh.asyncio.sleep", new=AsyncMock(return_value=None)), \
         patch("tasks.auto_creation.run_auto_creation_after_refresh",
               new=AsyncMock(return_value={})):
        task = _make_task()
        await task.execute()

    captured_kwargs = capture.call_args.kwargs if capture.called else None
    return capture, captured_kwargs


@pytest.mark.asyncio
async def test_execute_captures_when_timestamp_moved():
    """Positive change evidence (updated_at moved) -> capture runs."""
    capture, kwargs = await _run_execute_with_timestamps("2026-01-01T00:00:00Z",
                                                          "2026-01-02T00:00:00Z")
    assert capture.called
    # Gating passes the observed (moved) timestamp down for the snapshot.
    assert kwargs["dispatcharr_updated_at"] == "2026-01-02T00:00:00Z"


@pytest.mark.asyncio
async def test_execute_skips_when_timestamp_definitively_unchanged():
    """Definitive no-change (timestamp existed and never moved) -> skip capture.

    The poll loop sees the same non-null timestamp and eventually times out
    (we shorten MAX_WAIT so it ends fast). No fallback, real timestamp, no move
    == definitive no-change.
    """
    account = {"id": 1, "name": "Prov", "is_active": True}
    ts = "2026-01-01T00:00:00Z"

    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=[account])
    client.get_channel_groups = AsyncMock(return_value=[{"id": 1, "name": "G"}])
    client.refresh_m3u_account = AsyncMock(return_value=None)
    client.get_m3u_account = AsyncMock(return_value={
        "id": 1, "updated_at": ts, "channel_groups": []
    })

    capture = AsyncMock(return_value=None)

    # MAX_WAIT_SECONDS tiny so the unchanged-timestamp loop terminates via
    # timeout without ever hitting the 30s "assume complete" fallback.
    with patch("tasks.m3u_refresh.get_client", return_value=client), \
         patch("tasks.m3u_refresh.capture_m3u_changes", capture), \
         patch("tasks.m3u_refresh.POLL_INTERVAL_SECONDS", 0), \
         patch("tasks.m3u_refresh.MAX_WAIT_SECONDS", 0), \
         patch("tasks.m3u_refresh.asyncio.sleep", new=AsyncMock(return_value=None)), \
         patch("tasks.auto_creation.run_auto_creation_after_refresh",
               new=AsyncMock(return_value={})):
        task = _make_task()
        await task.execute()

    capture.assert_not_called()


@pytest.mark.asyncio
async def test_execute_captures_when_no_usable_timestamp_uncertain():
    """No usable timestamp -> the 30s fallback fires -> uncertain -> MUST capture.

    Never skip when uncertain: an account with no updated_at/last_refresh gives
    no definitive no-change signal, so capture has to run.
    """
    account = {"id": 1, "name": "Prov", "is_active": True}

    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=[account])
    client.get_channel_groups = AsyncMock(return_value=[{"id": 1, "name": "G"}])
    client.refresh_m3u_account = AsyncMock(return_value=None)
    # No updated_at / last_refresh anywhere -> latest_updated stays None.
    client.get_m3u_account = AsyncMock(return_value={"id": 1, "channel_groups": []})

    capture = AsyncMock(return_value=None)

    # Need elapsed > 30 to trip the fallback. We can't fast-forward wall clock,
    # so patch the fallback threshold path by making elapsed large: patch
    # datetime in the module is heavy; instead rely on the fact that with no
    # timestamp the loop only exits via the >30s fallback OR timeout, and either
    # way latest_updated is None => uncertain => capture. Force a quick timeout.
    with patch("tasks.m3u_refresh.get_client", return_value=client), \
         patch("tasks.m3u_refresh.capture_m3u_changes", capture), \
         patch("tasks.m3u_refresh.POLL_INTERVAL_SECONDS", 0), \
         patch("tasks.m3u_refresh.MAX_WAIT_SECONDS", 0), \
         patch("tasks.m3u_refresh.asyncio.sleep", new=AsyncMock(return_value=None)), \
         patch("tasks.auto_creation.run_auto_creation_after_refresh",
               new=AsyncMock(return_value={})):
        task = _make_task()
        await task.execute()

    # latest_updated is None -> have_definitive_no_change is False -> capture.
    capture.assert_called()
    assert capture.call_args.kwargs["dispatcharr_updated_at"] is None
