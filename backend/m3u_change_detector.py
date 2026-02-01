"""
M3U Change Detection Service.

Compares current M3U state with previous snapshots to detect changes.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Set

from sqlalchemy.orm import Session

from models import M3USnapshot, M3UChangeLog

logger = logging.getLogger(__name__)


@dataclass
class GroupChange:
    """Represents a change in a group."""
    group_name: str
    change_type: str  # 'added', 'removed'
    stream_count: int = 0
    enabled: bool = False  # Whether the group is enabled in the M3U


@dataclass
class StreamChange:
    """Represents stream changes within a group."""
    group_name: str
    change_type: str  # 'streams_added', 'streams_removed'
    stream_names: List[str] = field(default_factory=list)
    count: int = 0
    enabled: bool = False  # Whether the group is enabled in the M3U


@dataclass
class M3UChangeSet:
    """
    Complete set of changes detected between two M3U states.
    """
    m3u_account_id: int
    previous_snapshot_id: Optional[int]
    current_snapshot_id: Optional[int]
    change_time: datetime = field(default_factory=datetime.utcnow)

    # Group-level changes
    groups_added: List[GroupChange] = field(default_factory=list)
    groups_removed: List[GroupChange] = field(default_factory=list)

    # Stream-level changes per group
    streams_added: List[StreamChange] = field(default_factory=list)
    streams_removed: List[StreamChange] = field(default_factory=list)

    # Summary stats
    total_streams_before: int = 0
    total_streams_after: int = 0

    @property
    def has_changes(self) -> bool:
        """Check if any changes were detected."""
        return bool(
            self.groups_added or
            self.groups_removed or
            self.streams_added or
            self.streams_removed
        )

    @property
    def total_changes(self) -> int:
        """Count total number of change records."""
        return (
            len(self.groups_added) +
            len(self.groups_removed) +
            len(self.streams_added) +
            len(self.streams_removed)
        )

    @property
    def stream_count_delta(self) -> int:
        """Net change in stream count."""
        return self.total_streams_after - self.total_streams_before

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "m3u_account_id": self.m3u_account_id,
            "previous_snapshot_id": self.previous_snapshot_id,
            "current_snapshot_id": self.current_snapshot_id,
            "change_time": self.change_time.isoformat() + "Z",
            "has_changes": self.has_changes,
            "total_changes": self.total_changes,
            "groups_added": [{"group_name": g.group_name, "stream_count": g.stream_count} for g in self.groups_added],
            "groups_removed": [{"group_name": g.group_name, "stream_count": g.stream_count} for g in self.groups_removed],
            "streams_added": [{"group_name": s.group_name, "count": s.count, "stream_names": s.stream_names[:10]} for s in self.streams_added],
            "streams_removed": [{"group_name": s.group_name, "count": s.count, "stream_names": s.stream_names[:10]} for s in self.streams_removed],
            "total_streams_before": self.total_streams_before,
            "total_streams_after": self.total_streams_after,
            "stream_count_delta": self.stream_count_delta,
        }


class M3UChangeDetector:
    """
    Service for detecting changes between M3U playlist states.
    """

    def __init__(self, db: Session):
        self.db = db

    def create_snapshot(
        self,
        m3u_account_id: int,
        groups_data: List[Dict],
        total_streams: int,
        dispatcharr_updated_at: Optional[str] = None,
        stream_names_by_group: Optional[Dict[str, List[str]]] = None,
    ) -> M3USnapshot:
        """
        Create a new M3U snapshot from current state.

        Args:
            m3u_account_id: The M3U account ID from Dispatcharr
            groups_data: List of dicts with 'name' and 'stream_count' for each group
            total_streams: Total number of streams in the playlist
            dispatcharr_updated_at: Dispatcharr's updated_at timestamp (for change monitoring)
            stream_names_by_group: Optional dict mapping group names to stream name lists
                                   (stored in snapshot to enable removed stream tracking)

        Returns:
            The created M3USnapshot
        """
        # Enrich groups_data with stream names if available
        enriched_groups = []
        groups_with_names = 0
        for group in groups_data:
            group_copy = dict(group)
            group_name = group.get("name")
            if stream_names_by_group and group_name in stream_names_by_group:
                group_copy["stream_names"] = stream_names_by_group[group_name]
                groups_with_names += 1
            enriched_groups.append(group_copy)

        logger.info(f"[M3U-SNAPSHOT-DEBUG] Creating snapshot: {len(groups_data)} groups, {groups_with_names} with stream_names")

        snapshot = M3USnapshot(
            m3u_account_id=m3u_account_id,
            snapshot_time=datetime.utcnow(),
            total_streams=total_streams,
            dispatcharr_updated_at=dispatcharr_updated_at,
        )
        snapshot.set_groups_data({"groups": enriched_groups})

        self.db.add(snapshot)
        self.db.commit()
        self.db.refresh(snapshot)

        logger.info(
            f"[M3U-SNAPSHOT] Created snapshot {snapshot.id} for account {m3u_account_id}: "
            f"{len(groups_data)} groups, {total_streams} streams"
        )

        return snapshot

    def get_latest_snapshot(self, m3u_account_id: int) -> Optional[M3USnapshot]:
        """Get the most recent snapshot for an M3U account."""
        return (
            self.db.query(M3USnapshot)
            .filter(M3USnapshot.m3u_account_id == m3u_account_id)
            .order_by(M3USnapshot.snapshot_time.desc())
            .first()
        )

    def get_previous_snapshot(self, m3u_account_id: int, before_snapshot_id: int) -> Optional[M3USnapshot]:
        """Get the snapshot before a given snapshot ID."""
        return (
            self.db.query(M3USnapshot)
            .filter(
                M3USnapshot.m3u_account_id == m3u_account_id,
                M3USnapshot.id < before_snapshot_id,
            )
            .order_by(M3USnapshot.snapshot_time.desc())
            .first()
        )

    def detect_changes(
        self,
        m3u_account_id: int,
        current_groups: List[Dict],
        current_total_streams: int,
        stream_names_by_group: Optional[Dict[str, List[str]]] = None,
        dispatcharr_updated_at: Optional[str] = None,
    ) -> M3UChangeSet:
        """
        Detect changes between current M3U state and previous snapshot.

        Args:
            m3u_account_id: The M3U account ID
            current_groups: List of dicts with 'name' and 'stream_count'
            current_total_streams: Total streams in current state
            stream_names_by_group: Optional dict mapping group names to stream name lists
                                   (enables detailed stream-level change detection)
            dispatcharr_updated_at: Dispatcharr's updated_at timestamp (for change monitoring)

        Returns:
            M3UChangeSet with all detected changes
        """
        # Get previous snapshot
        previous_snapshot = self.get_latest_snapshot(m3u_account_id)

        # Create new snapshot for current state (include stream names for future removal tracking)
        current_snapshot = self.create_snapshot(
            m3u_account_id=m3u_account_id,
            groups_data=current_groups,
            total_streams=current_total_streams,
            dispatcharr_updated_at=dispatcharr_updated_at,
            stream_names_by_group=stream_names_by_group,
        )

        change_set = M3UChangeSet(
            m3u_account_id=m3u_account_id,
            previous_snapshot_id=previous_snapshot.id if previous_snapshot else None,
            current_snapshot_id=current_snapshot.id,
            total_streams_after=current_total_streams,
        )

        if not previous_snapshot:
            # First snapshot - record all groups as "added" to establish baseline
            # Also record streams for groups that have them
            logger.info(f"[M3U-CHANGE] First snapshot for account {m3u_account_id}, recording initial state as added")
            for group in current_groups:
                stream_count = group.get("stream_count", 0)
                enabled = group.get("enabled", False)
                group_name = group["name"]
                change_set.groups_added.append(GroupChange(
                    group_name=group_name,
                    change_type="added",
                    stream_count=stream_count,
                    enabled=enabled,
                ))
                # Also record streams as "streams_added" so they appear in summaries
                # Include stream names if available (up to 50 per group)
                if stream_count > 0:
                    stream_names = []
                    if stream_names_by_group and group_name in stream_names_by_group:
                        stream_names = stream_names_by_group[group_name]
                    change_set.streams_added.append(StreamChange(
                        group_name=group_name,
                        change_type="streams_added",
                        stream_names=stream_names,
                        count=stream_count,
                        enabled=enabled,
                    ))
            return change_set

        change_set.total_streams_before = previous_snapshot.total_streams

        # Build lookup maps
        prev_data = previous_snapshot.get_groups_data()
        prev_groups = {g["name"]: g for g in prev_data.get("groups", [])}
        curr_groups = {g["name"]: g for g in current_groups}

        # Debug: count how many previous groups have stream_names
        prev_groups_with_names = sum(1 for g in prev_groups.values() if g.get("stream_names"))
        curr_groups_with_names = len(stream_names_by_group) if stream_names_by_group else 0
        logger.info(f"[M3U-CHANGE-DEBUG] Previous snapshot {previous_snapshot.id}: {len(prev_groups)} groups, {prev_groups_with_names} with stream_names")
        logger.info(f"[M3U-CHANGE-DEBUG] Current state: {len(curr_groups)} groups, {curr_groups_with_names} with stream_names in lookup")

        prev_group_names = set(prev_groups.keys())
        curr_group_names = set(curr_groups.keys())

        # Detect added groups
        for group_name in curr_group_names - prev_group_names:
            group = curr_groups[group_name]
            change_set.groups_added.append(GroupChange(
                group_name=group_name,
                change_type="added",
                stream_count=group.get("stream_count", 0),
                enabled=group.get("enabled", False),
            ))

        # Detect removed groups
        for group_name in prev_group_names - curr_group_names:
            group = prev_groups[group_name]
            change_set.groups_removed.append(GroupChange(
                group_name=group_name,
                change_type="removed",
                stream_count=group.get("stream_count", 0),
                enabled=group.get("enabled", False),
            ))

        # Detect stream count changes in existing groups
        for group_name in prev_group_names & curr_group_names:
            prev_count = prev_groups[group_name].get("stream_count", 0)
            curr_count = curr_groups[group_name].get("stream_count", 0)
            curr_enabled = curr_groups[group_name].get("enabled", False)

            # Get stream names from previous and current snapshots for comparison
            prev_stream_names = set(prev_groups[group_name].get("stream_names", []))
            curr_stream_names = set(stream_names_by_group.get(group_name, [])) if stream_names_by_group else set()

            # Debug log for groups with stream count changes
            if curr_count != prev_count:
                logger.info(f"[M3U-CHANGE-DEBUG] Group '{group_name}': prev_count={prev_count}, curr_count={curr_count}, "
                           f"prev_names_count={len(prev_stream_names)}, curr_names_count={len(curr_stream_names)}")

            if curr_count > prev_count:
                diff = curr_count - prev_count
                # Find actually added streams (in current but not in previous)
                if prev_stream_names and curr_stream_names:
                    added_streams = list(curr_stream_names - prev_stream_names)
                elif curr_stream_names:
                    # No previous stream names, show all current
                    added_streams = list(curr_stream_names)
                else:
                    added_streams = []

                logger.info(f"[M3U-CHANGE-DEBUG] Group '{group_name}': Recording {diff} streams added, {len(added_streams)} names captured")
                change_set.streams_added.append(StreamChange(
                    group_name=group_name,
                    change_type="streams_added",
                    stream_names=added_streams,
                    count=diff,
                    enabled=curr_enabled,
                ))
            elif curr_count < prev_count:
                diff = prev_count - curr_count
                # Find actually removed streams (in previous but not in current)
                if prev_stream_names and curr_stream_names:
                    removed_streams = list(prev_stream_names - curr_stream_names)
                elif prev_stream_names:
                    # No current stream names available, but we have previous - show what was there
                    # This can happen if the group was disabled
                    removed_streams = list(prev_stream_names)
                else:
                    removed_streams = []

                logger.info(f"[M3U-CHANGE-DEBUG] Group '{group_name}': Recording {diff} streams removed, {len(removed_streams)} names captured")
                change_set.streams_removed.append(StreamChange(
                    group_name=group_name,
                    change_type="streams_removed",
                    stream_names=removed_streams,
                    count=diff,
                    enabled=curr_enabled,
                ))

        if change_set.has_changes:
            logger.info(
                f"[M3U-CHANGE] Detected changes for account {m3u_account_id}: "
                f"+{len(change_set.groups_added)} groups, -{len(change_set.groups_removed)} groups, "
                f"+{sum(s.count for s in change_set.streams_added)} streams, "
                f"-{sum(s.count for s in change_set.streams_removed)} streams"
            )
        else:
            logger.debug(f"[M3U-CHANGE] No changes detected for account {m3u_account_id}")

        return change_set

    def persist_changes(self, change_set: M3UChangeSet) -> List[M3UChangeLog]:
        """
        Persist detected changes to the database.

        Args:
            change_set: The detected changes to persist

        Returns:
            List of created M3UChangeLog entries
        """
        if not change_set.has_changes:
            return []

        logs = []

        # Persist group additions
        for group in change_set.groups_added:
            log = M3UChangeLog(
                m3u_account_id=change_set.m3u_account_id,
                change_time=change_set.change_time,
                change_type="group_added",
                group_name=group.group_name,
                count=group.stream_count,
                enabled=group.enabled,
                snapshot_id=change_set.current_snapshot_id,
            )
            self.db.add(log)
            logs.append(log)

        # Persist group removals
        for group in change_set.groups_removed:
            log = M3UChangeLog(
                m3u_account_id=change_set.m3u_account_id,
                change_time=change_set.change_time,
                change_type="group_removed",
                group_name=group.group_name,
                count=group.stream_count,
                enabled=group.enabled,
                snapshot_id=change_set.current_snapshot_id,
            )
            self.db.add(log)
            logs.append(log)

        # Persist stream additions
        for stream_change in change_set.streams_added:
            log = M3UChangeLog(
                m3u_account_id=change_set.m3u_account_id,
                change_time=change_set.change_time,
                change_type="streams_added",
                group_name=stream_change.group_name,
                count=stream_change.count,
                enabled=stream_change.enabled,
                snapshot_id=change_set.current_snapshot_id,
            )
            log.set_stream_names(stream_change.stream_names)
            self.db.add(log)
            logs.append(log)

        # Persist stream removals
        for stream_change in change_set.streams_removed:
            log = M3UChangeLog(
                m3u_account_id=change_set.m3u_account_id,
                change_time=change_set.change_time,
                change_type="streams_removed",
                group_name=stream_change.group_name,
                count=stream_change.count,
                enabled=stream_change.enabled,
                snapshot_id=change_set.current_snapshot_id,
            )
            log.set_stream_names(stream_change.stream_names)
            self.db.add(log)
            logs.append(log)

        self.db.commit()

        logger.info(f"[M3U-CHANGE] Persisted {len(logs)} change log entries for account {change_set.m3u_account_id}")

        return logs

    def get_changes_since(
        self,
        since: datetime,
        m3u_account_id: Optional[int] = None,
    ) -> List[M3UChangeLog]:
        """
        Get all change logs since a given time.

        Args:
            since: Get changes after this time
            m3u_account_id: Optional filter by M3U account

        Returns:
            List of M3UChangeLog entries
        """
        query = self.db.query(M3UChangeLog).filter(M3UChangeLog.change_time >= since)

        if m3u_account_id is not None:
            query = query.filter(M3UChangeLog.m3u_account_id == m3u_account_id)

        return query.order_by(M3UChangeLog.change_time.desc()).all()

    def get_change_summary(
        self,
        since: datetime,
        m3u_account_id: Optional[int] = None,
    ) -> Dict:
        """
        Get aggregated summary of changes since a given time.

        Args:
            since: Get changes after this time
            m3u_account_id: Optional filter by M3U account

        Returns:
            Summary dict with counts by type
        """
        changes = self.get_changes_since(since, m3u_account_id)

        summary = {
            "total_changes": len(changes),
            "groups_added": 0,
            "groups_removed": 0,
            "streams_added": 0,
            "streams_removed": 0,
            "accounts_affected": set(),
            "since": since.isoformat() + "Z",
        }

        for change in changes:
            summary["accounts_affected"].add(change.m3u_account_id)
            if change.change_type == "group_added":
                summary["groups_added"] += 1
            elif change.change_type == "group_removed":
                summary["groups_removed"] += 1
            elif change.change_type == "streams_added":
                summary["streams_added"] += change.count
            elif change.change_type == "streams_removed":
                summary["streams_removed"] += change.count

        summary["accounts_affected"] = list(summary["accounts_affected"])

        return summary
