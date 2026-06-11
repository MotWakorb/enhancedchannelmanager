"""
Regression tests for the M3U digest memory blow-up (GH #473).

A test M3U digest on a large deployment loaded the entire change log into
memory with an unbounded ``.all()`` while ``M3UChangeLog.snapshot`` was an
eager (``lazy="joined"``) relationship. Each change row from a refresh shares
one snapshot whose ``groups_data`` JSON carries every group's full stream-name
list, so the joined blob was re-streamed once per row — an O(rows x blob)
fanout that drove RSS to ~18.5 GiB and corrupted the SQLite DB.

These tests lock in the two fixes:
  1. the snapshot relationship is load-on-access (not eager-joined), and
  2. ``execute()`` caps how many change rows it loads (MAX_DIGEST_CHANGES).
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import inspect as sa_inspect

from models import M3UChangeLog, M3UDigestSettings


def test_snapshot_relationship_is_not_eager_joined():
    """The snapshot relationship must be load-on-access, never eager-joined.

    Eager-joining re-streams the large groups_data blob once per change row
    sharing a snapshot — the GH #473 memory fanout. Nothing reads .snapshot
    (to_dict() uses the snapshot_id FK column), so it must stay lazy.
    """
    rel = sa_inspect(M3UChangeLog).relationships["snapshot"]
    assert rel.lazy == "select", (
        f"M3UChangeLog.snapshot lazy={rel.lazy!r}; must be 'select' to avoid the "
        "GH #473 joined-eager-load memory fanout"
    )


@pytest.mark.asyncio
async def test_execute_caps_changes_loaded(test_session):
    """execute() must not load more than MAX_DIGEST_CHANGES rows into memory."""
    from tasks import m3u_digest as digest_mod
    from tasks.m3u_digest import M3UDigestTask

    # Enabled settings with a recipient so the digest actually builds/sends.
    settings = M3UDigestSettings(
        enabled=True,
        frequency="weekly",
        include_group_changes=True,
        include_stream_changes=True,
        show_detailed_list=True,
        min_changes_threshold=1,
    )
    settings.set_email_recipients(["ops@example.com"])
    test_session.add(settings)
    test_session.commit()

    # Insert more change rows than the (patched-small) cap allows.
    cap = 5
    base = datetime.utcnow() - timedelta(hours=1)
    for i in range(cap + 7):  # 12 rows, cap is 5
        row = M3UChangeLog(
            m3u_account_id=1,
            change_time=base + timedelta(seconds=i),
            change_type="streams_added",
            group_name=f"Group {i}",
            count=1,
        )
        row.set_stream_names([f"Stream {i}"])
        test_session.add(row)
    test_session.commit()

    # get_session() inside the task returns our test session; close() is a no-op
    # so the fixture keeps ownership of cleanup.
    class _NoCloseSession:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def close(self):
            pass

    with patch.object(digest_mod, "MAX_DIGEST_CHANGES", cap), \
         patch.object(digest_mod, "get_session", return_value=_NoCloseSession(test_session)), \
         patch.object(M3UDigestTask, "_send_digest_email", new=AsyncMock(return_value=True)):
        task = M3UDigestTask()
        result = await task.execute(force=True)

    assert result.success is True
    # total_items == len(changes loaded); must be capped, not the full 12.
    assert result.total_items == cap, (
        f"digest loaded {result.total_items} rows; expected cap of {cap}"
    )
