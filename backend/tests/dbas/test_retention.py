"""Tests for bead 0i2vt.9 — DBAS backup retention policy.

Retention algorithm: last-N is the FLOOR (always keep the newest N successful
backups regardless of age); age ADDITIONALLY prunes only backups BEYOND the
newest N AND older than X days. NEVER-ZERO: deletion runs ONLY after a
checksum-verified-successful new backup/upload to THAT destination completes —
a partial/failed run prunes nothing. Per-destination (local + each successful
cloud target). Canonical sort key = the FILENAME timestamp (never mtime).

All filesystem + adapter calls are mocked at module level — NO live cloud calls,
no real deletions outside tmp_path.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from dbas import retention


# ---------------------------------------------------------------------------
# Helpers — build timestamped backup-artifact filenames.
# ---------------------------------------------------------------------------
def _name(dt: datetime) -> str:
    """Canonical backup-artifact filename for a given UTC datetime."""
    return "ecm-backup-%s.zip" % dt.strftime("%Y-%m-%d_%H%M%S")


_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)


def _days_ago(n: int) -> datetime:
    return _NOW - timedelta(days=n)


# ---------------------------------------------------------------------------
# select_prunable — the pure selection algorithm (last-N floor + age).
# ---------------------------------------------------------------------------
def test_last_n_floor_prunes_oldest_beyond_n():
    """N+1 backups all within the age threshold → only the oldest (beyond the
    newest N) is pruned; the newest N are kept."""
    names = [_name(_days_ago(i)) for i in range(8)]  # 8 backups, days 0..7 old
    kept, pruned = retention.select_prunable(
        names, last_n=7, max_age_days=30, now=_NOW
    )
    assert len(kept) == 7
    assert len(pruned) == 1
    # The pruned one is the oldest (7 days old).
    assert pruned[0][0] == _name(_days_ago(7))
    assert pruned[0][1] == "last_n"


def test_age_prunes_beyond_n_and_older_than_threshold():
    """A backup BEYOND the newest N AND older than the threshold → pruned with
    reason 'age'. One within the newest N but old → KEPT (floor wins). One
    beyond N but newer than threshold → KEPT."""
    names = [
        _name(_days_ago(1)),    # newest
        _name(_days_ago(40)),   # within newest N=2 but very old → KEPT (floor)
        _name(_days_ago(10)),   # beyond N, newer than 30d → KEPT
        _name(_days_ago(50)),   # beyond N AND older than 30d → pruned (age)
    ]
    kept, pruned = retention.select_prunable(
        names, last_n=2, max_age_days=30, now=_NOW
    )
    kept_names = set(kept)
    pruned_map = dict(pruned)

    # Floor keeps the newest 2 by filename timestamp: day-1 and day-10.
    assert _name(_days_ago(1)) in kept_names
    assert _name(_days_ago(10)) in kept_names
    # The 40-day-old is NOT in the newest 2 (10 and 50 and 40 → newest 2 are
    # 1 and 10). 40 is beyond N AND older than 30d → pruned (age).
    assert pruned_map.get(_name(_days_ago(40))) == "age"
    assert pruned_map.get(_name(_days_ago(50))) == "age"


def test_within_newest_n_but_old_is_kept_floor_wins():
    """last-N floor wins over age: an old backup that is within the newest N is
    KEPT even though it is older than the threshold."""
    names = [_name(_days_ago(100)), _name(_days_ago(200))]
    kept, pruned = retention.select_prunable(
        names, last_n=7, max_age_days=30, now=_NOW
    )
    assert len(kept) == 2
    assert pruned == []


# ---------------------------------------------------------------------------
# NEVER-ZERO invariant.
# ---------------------------------------------------------------------------
def test_never_zero_last_n_1_keeps_exactly_one():
    """last_n=1 + a verified new backup → exactly 1 remains."""
    names = [_name(_days_ago(i)) for i in range(5)]
    kept, pruned = retention.select_prunable(
        names, last_n=1, max_age_days=30, now=_NOW
    )
    assert len(kept) == 1
    assert kept[0] == _name(_days_ago(0))  # the newest
    assert len(pruned) == 4


def test_never_zero_all_older_than_threshold_keeps_most_recent():
    """ALL backups older than the threshold → the most recent is still kept
    (the last-N floor, with N>=1, prevents zero)."""
    names = [_name(_days_ago(d)) for d in (365, 400, 500)]
    kept, pruned = retention.select_prunable(
        names, last_n=1, max_age_days=30, now=_NOW
    )
    assert len(kept) == 1
    assert kept[0] == _name(_days_ago(365))  # the most recent of the three
    assert len(pruned) == 2


def test_last_n_zero_is_floored_to_one_never_zero():
    """A misconfigured last_n=0 must NOT erode to zero backups — it is floored
    to keep at least the most recent (NEVER-ZERO hard invariant)."""
    names = [_name(_days_ago(d)) for d in (1, 2, 3)]
    kept, pruned = retention.select_prunable(
        names, last_n=0, max_age_days=1, now=_NOW
    )
    assert len(kept) >= 1
    assert kept[0] == _name(_days_ago(1))


# ---------------------------------------------------------------------------
# Canonical sort key = FILENAME timestamp (never mtime).
# ---------------------------------------------------------------------------
def test_selection_uses_filename_timestamp_not_input_order():
    """Selection sorts by the filename timestamp regardless of the order the
    names are supplied in — a shuffled input picks the same file to prune."""
    names = [
        _name(_days_ago(2)),
        _name(_days_ago(50)),  # oldest by filename ts
        _name(_days_ago(1)),
    ]
    kept, pruned = retention.select_prunable(
        names, last_n=2, max_age_days=10, now=_NOW
    )
    assert [p[0] for p in pruned] == [_name(_days_ago(50))]


def test_non_matching_filenames_are_never_selected():
    """A filename that does NOT match the backup-artifact allowlist pattern is
    NEVER selected for deletion (it is filtered out before selection)."""
    names = [
        _name(_days_ago(1)),
        _name(_days_ago(2)),
        _name(_days_ago(3)),
        "not-a-backup.zip",
        "ecm-backup-bad.zip",
        "../../etc/passwd",
        "ecm-backup-2026-06-18_120000.zip.bak",
    ]
    kept, pruned = retention.select_prunable(
        names, last_n=1, max_age_days=1, now=_NOW
    )
    pruned_names = {p[0] for p in pruned}
    # Only the two oldest VALID backups are pruned; none of the junk is ever
    # in the pruned set.
    assert "not-a-backup.zip" not in pruned_names
    assert "ecm-backup-bad.zip" not in pruned_names
    assert "../../etc/passwd" not in pruned_names
    assert "ecm-backup-2026-06-18_120000.zip.bak" not in pruned_names
    assert pruned_names == {_name(_days_ago(2)), _name(_days_ago(3))}


# ---------------------------------------------------------------------------
# Local prune — filesystem-derived (glob), allowlist-guarded, audited.
# ---------------------------------------------------------------------------
def test_prune_local_deletes_only_beyond_n_and_audits(tmp_path, monkeypatch):
    """A verified new local backup → beyond-N files are deleted from disk; an
    audit row is written per deletion with the reason + destination 'local'."""
    backups = tmp_path / "backups"
    backups.mkdir()
    files = [_name(_days_ago(i)) for i in range(5)]
    for f in files:
        (backups / f).write_bytes(b"zip")
    # A non-matching file that must NEVER be touched.
    (backups / "keepme.txt").write_text("x")

    audit = MagicMock()
    monkeypatch.setattr(retention, "BACKUPS_DIR", backups)
    monkeypatch.setattr(retention.journal, "log_entry", audit)

    result = retention.prune_local_backups(
        verified_success=True, last_n=2, max_age_days=30, now=_NOW
    )

    remaining = {p.name for p in backups.glob("ecm-backup-*.zip")}
    assert remaining == {_name(_days_ago(0)), _name(_days_ago(1))}
    assert (backups / "keepme.txt").exists()  # untouched
    assert result["deleted"] == 3
    # One audit row per deletion, each carrying reason + destination.
    assert audit.call_count == 3
    for call in audit.call_args_list:
        desc = call.kwargs.get("description", "")
        assert "local" in desc
        assert "last_n" in desc or "age" in desc


def test_prune_local_no_op_on_unverified(tmp_path, monkeypatch):
    """NEVER-ZERO / no-prune-on-failure: a FAILED/PARTIAL new backup
    (verified_success=False) → retention does NOT run; nothing is deleted."""
    backups = tmp_path / "backups"
    backups.mkdir()
    files = [_name(_days_ago(i)) for i in range(5)]
    for f in files:
        (backups / f).write_bytes(b"zip")

    audit = MagicMock()
    monkeypatch.setattr(retention, "BACKUPS_DIR", backups)
    monkeypatch.setattr(retention.journal, "log_entry", audit)

    result = retention.prune_local_backups(
        verified_success=False, last_n=1, max_age_days=1, now=_NOW
    )

    remaining = {p.name for p in backups.glob("ecm-backup-*.zip")}
    assert len(remaining) == 5  # nothing deleted
    assert result["deleted"] == 0
    assert result["skipped_reason"] == "unverified"
    audit.assert_not_called()


def test_prune_local_never_deletes_outside_allowlist(tmp_path, monkeypatch):
    """The filename-allowlist guard: a file in BACKUPS_DIR that does not match
    the artifact pattern is NEVER unlinked, even if it sorts as 'oldest'."""
    backups = tmp_path / "backups"
    backups.mkdir()
    # Only one valid backup → it is the floor and is kept.
    (backups / _name(_days_ago(1))).write_bytes(b"zip")
    # Junk files that must survive.
    (backups / "evil.sh").write_text("rm -rf /")
    (backups / "ecm-backup-NOPE.zip").write_text("x")

    monkeypatch.setattr(retention, "BACKUPS_DIR", backups)
    monkeypatch.setattr(retention.journal, "log_entry", MagicMock())

    retention.prune_local_backups(
        verified_success=True, last_n=1, max_age_days=1, now=_NOW
    )

    assert (backups / "evil.sh").exists()
    assert (backups / "ecm-backup-NOPE.zip").exists()
    assert (backups / _name(_days_ago(1))).exists()


# ---------------------------------------------------------------------------
# Cloud prune — routes through the adapter (chokepoint) + allowlist guard.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_prune_cloud_lists_and_deletes_via_adapter(monkeypatch):
    """A verified successful upload to a cloud destination → the adapter's
    list_files is called, prunable artifacts are deleted via adapter.delete
    (which routes through the .5 SSRF chokepoint + executor-wrap), and an audit
    row is written per deletion with the destination name + reason."""
    names = [_name(_days_ago(i)) for i in range(5)]
    adapter = AsyncMock()
    adapter.list_files = AsyncMock(return_value=list(names))
    adapter.delete = AsyncMock(return_value=True)

    audit = MagicMock()
    monkeypatch.setattr(retention.journal, "log_entry", audit)

    result = await retention.prune_cloud_destination(
        adapter=adapter,
        remote_dir="/backups",
        dest_name="my-s3",
        verified_success=True,
        last_n=2,
        max_age_days=30,
        now=_NOW,
    )

    adapter.list_files.assert_awaited_once()
    # 3 oldest beyond the floor of 2 are deleted.
    assert adapter.delete.await_count == 3
    deleted_args = {c.args[0] if c.args else c.kwargs.get("remote_path")
                    for c in adapter.delete.await_args_list}
    # delete is called with the remote path (dir + filename).
    for fn in (_name(_days_ago(2)), _name(_days_ago(3)), _name(_days_ago(4))):
        assert any(fn in str(a) for a in deleted_args)
    assert result["deleted"] == 3
    assert audit.call_count == 3
    for call in audit.call_args_list:
        desc = call.kwargs.get("description", "")
        assert "my-s3" in desc


@pytest.mark.asyncio
async def test_prune_cloud_no_op_on_unverified(monkeypatch):
    """No-prune-on-failure (per-destination): a destination whose upload was
    NOT verified-successful → list/delete are never called."""
    adapter = AsyncMock()
    adapter.list_files = AsyncMock(return_value=[_name(_days_ago(i)) for i in range(5)])
    adapter.delete = AsyncMock(return_value=True)
    monkeypatch.setattr(retention.journal, "log_entry", MagicMock())

    result = await retention.prune_cloud_destination(
        adapter=adapter,
        remote_dir="/backups",
        dest_name="my-s3",
        verified_success=False,
        last_n=1,
        max_age_days=1,
        now=_NOW,
    )

    adapter.list_files.assert_not_awaited()
    adapter.delete.assert_not_awaited()
    assert result["deleted"] == 0
    assert result["skipped_reason"] == "unverified"


@pytest.mark.asyncio
async def test_prune_cloud_never_deletes_non_matching_remote_names(monkeypatch):
    """The filename-allowlist guard applies to cloud listings too: a remote
    object whose name does not match the artifact pattern is NEVER deleted."""
    names = [
        _name(_days_ago(1)),
        _name(_days_ago(2)),
        "unrelated-object.bin",
        "ecm-backup-bogus.zip",
    ]
    adapter = AsyncMock()
    adapter.list_files = AsyncMock(return_value=names)
    adapter.delete = AsyncMock(return_value=True)
    monkeypatch.setattr(retention.journal, "log_entry", MagicMock())

    await retention.prune_cloud_destination(
        adapter=adapter,
        remote_dir="/backups",
        dest_name="my-s3",
        verified_success=True,
        last_n=1,
        max_age_days=1,
        now=_NOW,
    )

    deleted = {str(c.args[0] if c.args else c.kwargs.get("remote_path"))
               for c in adapter.delete.await_args_list}
    assert all("unrelated-object.bin" not in d for d in deleted)
    assert all("ecm-backup-bogus.zip" not in d for d in deleted)
    # Only the day-2 valid backup beyond the floor of 1 is deleted.
    assert adapter.delete.await_count == 1
    assert any(_name(_days_ago(2)) in d for d in deleted)


@pytest.mark.asyncio
async def test_prune_cloud_audits_destination_name_only_no_credentials(monkeypatch):
    """Credential-clean audit: the audit description carries the destination
    NAME + filename + reason — never a URL, credential, or SDK-exception body."""
    adapter = AsyncMock()
    adapter.list_files = AsyncMock(return_value=[_name(_days_ago(i)) for i in range(3)])
    adapter.delete = AsyncMock(return_value=True)
    audit = MagicMock()
    monkeypatch.setattr(retention.journal, "log_entry", audit)

    await retention.prune_cloud_destination(
        adapter=adapter,
        remote_dir="/backups",
        dest_name="prod-webdav",
        verified_success=True,
        last_n=1,
        max_age_days=30,
        now=_NOW,
    )

    assert audit.call_count >= 1
    for call in audit.call_args_list:
        desc = call.kwargs.get("description", "")
        assert "prod-webdav" in desc
        # No URL scheme or credential-shaped material in the audit text.
        assert "http://" not in desc
        assert "https://" not in desc
        assert "s3://" not in desc
        assert "secret" not in desc.lower()
        assert "password" not in desc.lower()


@pytest.mark.asyncio
async def test_prune_cloud_delete_failure_is_not_fatal(monkeypatch):
    """A single adapter.delete returning False (or raising) must not abort the
    whole prune — the remaining deletions still proceed and the count reflects
    only the successful deletions."""
    adapter = AsyncMock()
    adapter.list_files = AsyncMock(return_value=[_name(_days_ago(i)) for i in range(4)])
    # First delete fails, rest succeed.
    adapter.delete = AsyncMock(side_effect=[False, True, True])
    monkeypatch.setattr(retention.journal, "log_entry", MagicMock())

    result = await retention.prune_cloud_destination(
        adapter=adapter,
        remote_dir="/backups",
        dest_name="my-s3",
        verified_success=True,
        last_n=1,
        max_age_days=1,
        now=_NOW,
    )

    assert adapter.delete.await_count == 3
    assert result["deleted"] == 2  # one failed
    assert result["failed"] == 1
