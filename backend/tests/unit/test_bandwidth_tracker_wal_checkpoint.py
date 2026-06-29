"""The BandwidthTracker poll loop drives the mid-run WAL backstop.

bd-xjlxj / GH #473 (FALLBACK approach — see database.py comment block):
code verification disproved the bead's premise that the ~10s stats poller
holds a pinned read transaction across the poll interval (its sessions are
all short and synchronous). The residual WAL-growth vector on a busy install
is overlapping short readers keeping the per-commit PASSIVE auto-checkpoint
backing off, so the poll loop fires a count-based PASSIVE checkpoint as a
mid-run backstop.

These tests assert the loop INVOKES the trigger with the tracker's poll count
(count-based, not file-size-based — deterministic, not flaky) and that the
trigger advances each poll. They do NOT assert WAL byte sizes.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bandwidth_tracker import BandwidthTracker


def _make_tracker():
    client = MagicMock()
    return BandwidthTracker(client=client, poll_interval=10)


@pytest.mark.asyncio
async def test_poll_loop_invokes_periodic_wal_checkpoint_with_poll_count():
    """One poll iteration calls the WAL backstop with the current poll count."""
    tracker = _make_tracker()

    # Stub the network/DB heavy poll body so the loop runs instantly and the
    # poll counter advances exactly the way production advances it.
    async def _collect():
        tracker._poll_count += 1

    with patch.object(tracker, "_maybe_refresh_channel_map", new=AsyncMock()), \
         patch.object(tracker, "_maybe_refresh_m3u_accounts", new=AsyncMock()), \
         patch.object(tracker, "_collect_stats", new=AsyncMock(side_effect=_collect)), \
         patch("bandwidth_tracker.maybe_periodic_wal_checkpoint") as mock_ckpt, \
         patch("bandwidth_tracker.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        # Stop the loop after the first sleep so we run exactly one iteration.
        def _stop(*_a, **_k):
            tracker._running = False
        mock_sleep.side_effect = _stop

        tracker._running = True
        await tracker._poll_loop()

    mock_ckpt.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_poll_loop_advances_poll_count_passed_to_checkpoint():
    """Across multiple iterations the trigger receives a monotonically rising count."""
    tracker = _make_tracker()
    seen: list[int] = []

    async def _collect():
        tracker._poll_count += 1

    def _record(poll_count, *a, **k):
        seen.append(poll_count)
        if len(seen) >= 3:
            tracker._running = False

    with patch.object(tracker, "_maybe_refresh_channel_map", new=AsyncMock()), \
         patch.object(tracker, "_maybe_refresh_m3u_accounts", new=AsyncMock()), \
         patch.object(tracker, "_collect_stats", new=AsyncMock(side_effect=_collect)), \
         patch("bandwidth_tracker.maybe_periodic_wal_checkpoint", side_effect=_record), \
         patch("bandwidth_tracker.asyncio.sleep", new=AsyncMock()):
        tracker._running = True
        await tracker._poll_loop()

    assert seen == [1, 2, 3]
