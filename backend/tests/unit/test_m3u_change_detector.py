"""
Unit tests for the M3U Change Detector module.

Tests the core change detection logic including:
- Dataclasses (GroupChange, StreamChange, M3UChangeSet)
- M3UChangeDetector class methods
- Snapshot creation and retrieval
- Change detection algorithms
- Persistence of change logs
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from m3u_change_detector import (
    GroupChange,
    StreamChange,
    M3UChangeSet,
    M3UChangeDetector,
)
from models import M3USnapshot, M3UChangeLog


class TestGroupChange:
    """Tests for the GroupChange dataclass."""

    def test_basic_creation(self):
        """Test creating a GroupChange with required fields."""
        change = GroupChange(group_name="Sports", change_type="added")
        assert change.group_name == "Sports"
        assert change.change_type == "added"
        assert change.stream_count == 0
        assert change.enabled is False

    def test_creation_with_all_fields(self):
        """Test creating a GroupChange with all fields."""
        change = GroupChange(
            group_name="Movies",
            change_type="removed",
            stream_count=150,
            enabled=True,
        )
        assert change.group_name == "Movies"
        assert change.change_type == "removed"
        assert change.stream_count == 150
        assert change.enabled is True


class TestStreamChange:
    """Tests for the StreamChange dataclass."""

    def test_basic_creation(self):
        """Test creating a StreamChange with required fields."""
        change = StreamChange(group_name="News", change_type="streams_added")
        assert change.group_name == "News"
        assert change.change_type == "streams_added"
        assert change.stream_names == []
        assert change.count == 0
        assert change.enabled is False

    def test_creation_with_stream_names(self):
        """Test creating a StreamChange with stream names."""
        stream_names = ["CNN HD", "BBC World", "Fox News"]
        change = StreamChange(
            group_name="News",
            change_type="streams_added",
            stream_names=stream_names,
            count=3,
            enabled=True,
        )
        assert change.stream_names == stream_names
        assert change.count == 3
        assert change.enabled is True


class TestM3UChangeSet:
    """Tests for the M3UChangeSet dataclass."""

    def test_empty_change_set(self):
        """Test that an empty change set reports no changes."""
        change_set = M3UChangeSet(
            m3u_account_id=1,
            previous_snapshot_id=1,
            current_snapshot_id=2,
        )
        assert change_set.has_changes is False
        assert change_set.total_changes == 0
        assert change_set.stream_count_delta == 0

    def test_has_changes_with_groups_added(self):
        """Test has_changes is True when groups are added."""
        change_set = M3UChangeSet(
            m3u_account_id=1,
            previous_snapshot_id=1,
            current_snapshot_id=2,
        )
        change_set.groups_added.append(
            GroupChange(group_name="Sports", change_type="added", stream_count=50)
        )
        assert change_set.has_changes is True
        assert change_set.total_changes == 1

    def test_has_changes_with_streams_removed(self):
        """Test has_changes is True when streams are removed."""
        change_set = M3UChangeSet(
            m3u_account_id=1,
            previous_snapshot_id=1,
            current_snapshot_id=2,
        )
        change_set.streams_removed.append(
            StreamChange(group_name="Movies", change_type="streams_removed", count=10)
        )
        assert change_set.has_changes is True

    def test_stream_count_delta(self):
        """Test stream count delta calculation."""
        change_set = M3UChangeSet(
            m3u_account_id=1,
            previous_snapshot_id=1,
            current_snapshot_id=2,
            total_streams_before=100,
            total_streams_after=120,
        )
        assert change_set.stream_count_delta == 20

    def test_stream_count_delta_negative(self):
        """Test stream count delta when streams are removed."""
        change_set = M3UChangeSet(
            m3u_account_id=1,
            previous_snapshot_id=1,
            current_snapshot_id=2,
            total_streams_before=100,
            total_streams_after=80,
        )
        assert change_set.stream_count_delta == -20

    def test_total_changes_count(self):
        """Test total changes counts all change types."""
        change_set = M3UChangeSet(
            m3u_account_id=1,
            previous_snapshot_id=1,
            current_snapshot_id=2,
        )
        change_set.groups_added.append(GroupChange("G1", "added"))
        change_set.groups_added.append(GroupChange("G2", "added"))
        change_set.groups_removed.append(GroupChange("G3", "removed"))
        change_set.streams_added.append(StreamChange("G1", "streams_added", count=5))
        change_set.streams_removed.append(StreamChange("G4", "streams_removed", count=3))

        assert change_set.total_changes == 5

    def test_to_dict(self):
        """Test conversion to dictionary for API responses."""
        change_set = M3UChangeSet(
            m3u_account_id=1,
            previous_snapshot_id=10,
            current_snapshot_id=11,
            total_streams_before=100,
            total_streams_after=110,
        )
        change_set.groups_added.append(
            GroupChange("Sports", "added", stream_count=50)
        )
        change_set.streams_added.append(
            StreamChange("Sports", "streams_added", stream_names=["ESPN", "Fox Sports"], count=10)
        )

        result = change_set.to_dict()

        assert result["m3u_account_id"] == 1
        assert result["previous_snapshot_id"] == 10
        assert result["current_snapshot_id"] == 11
        assert result["has_changes"] is True
        assert result["total_changes"] == 2
        assert result["total_streams_before"] == 100
        assert result["total_streams_after"] == 110
        assert result["stream_count_delta"] == 10
        assert len(result["groups_added"]) == 1
        assert result["groups_added"][0]["group_name"] == "Sports"
        assert result["groups_added"][0]["stream_count"] == 50


class TestM3UChangeDetector:
    """Tests for the M3UChangeDetector class."""

    def test_create_snapshot(self, test_session):
        """Test creating an M3U snapshot."""
        detector = M3UChangeDetector(test_session)
        groups_data = [
            {"name": "Sports", "stream_count": 50, "enabled": True},
            {"name": "Movies", "stream_count": 100, "enabled": False},
        ]

        snapshot = detector.create_snapshot(
            m3u_account_id=1,
            groups_data=groups_data,
            total_streams=150,
        )

        assert snapshot.id is not None
        assert snapshot.m3u_account_id == 1
        assert snapshot.total_streams == 150
        assert snapshot.snapshot_time is not None

        # Verify groups data
        stored_data = snapshot.get_groups_data()
        assert len(stored_data["groups"]) == 2
        assert stored_data["groups"][0]["name"] == "Sports"

    def test_create_snapshot_with_dispatcharr_timestamp(self, test_session):
        """Test creating snapshot with Dispatcharr's updated_at timestamp."""
        detector = M3UChangeDetector(test_session)
        dispatcharr_ts = "2026-01-29T10:00:00Z"

        snapshot = detector.create_snapshot(
            m3u_account_id=1,
            groups_data=[{"name": "Test", "stream_count": 10}],
            total_streams=10,
            dispatcharr_updated_at=dispatcharr_ts,
        )

        assert snapshot.dispatcharr_updated_at == dispatcharr_ts

    def test_get_latest_snapshot(self, test_session):
        """Test retrieving the latest snapshot for an account."""
        detector = M3UChangeDetector(test_session)

        # Create multiple snapshots
        detector.create_snapshot(1, [{"name": "G1", "stream_count": 10}], 10)
        detector.create_snapshot(1, [{"name": "G1", "stream_count": 15}], 15)
        latest = detector.create_snapshot(1, [{"name": "G1", "stream_count": 20}], 20)

        # Also create a snapshot for a different account
        detector.create_snapshot(2, [{"name": "G2", "stream_count": 5}], 5)

        result = detector.get_latest_snapshot(1)
        assert result.id == latest.id
        assert result.total_streams == 20

    def test_get_latest_snapshot_no_snapshots(self, test_session):
        """Test get_latest_snapshot returns None when no snapshots exist."""
        detector = M3UChangeDetector(test_session)
        result = detector.get_latest_snapshot(999)
        assert result is None

    def test_get_previous_snapshot(self, test_session):
        """Test retrieving the previous snapshot before a given ID."""
        detector = M3UChangeDetector(test_session)

        first = detector.create_snapshot(1, [{"name": "G1", "stream_count": 10}], 10)
        second = detector.create_snapshot(1, [{"name": "G1", "stream_count": 15}], 15)
        third = detector.create_snapshot(1, [{"name": "G1", "stream_count": 20}], 20)

        result = detector.get_previous_snapshot(1, third.id)
        assert result.id == second.id

        result = detector.get_previous_snapshot(1, second.id)
        assert result.id == first.id

    def test_detect_changes_first_snapshot(self, test_session):
        """Test change detection when no previous snapshot exists (initial state)."""
        detector = M3UChangeDetector(test_session)
        current_groups = [
            {"name": "Sports", "stream_count": 50, "enabled": True},
            {"name": "Movies", "stream_count": 100, "enabled": False},
        ]

        change_set = detector.detect_changes(
            m3u_account_id=1,
            current_groups=current_groups,
            current_total_streams=150,
        )

        # First snapshot records all groups as "added"
        assert len(change_set.groups_added) == 2
        assert change_set.groups_added[0].group_name == "Sports"
        assert change_set.groups_added[0].stream_count == 50
        assert change_set.groups_added[0].enabled is True
        assert change_set.groups_added[1].group_name == "Movies"
        assert change_set.groups_added[1].stream_count == 100

    def test_detect_changes_no_changes(self, test_session):
        """Test detection when M3U state hasn't changed."""
        detector = M3UChangeDetector(test_session)
        groups = [{"name": "Sports", "stream_count": 50, "enabled": True}]

        # Create initial snapshot
        detector.detect_changes(1, groups, 50)

        # Detect changes with same state
        change_set = detector.detect_changes(1, groups, 50)

        # Should have no group or stream changes (only streams_added from initial would show)
        assert len(change_set.groups_added) == 0
        assert len(change_set.groups_removed) == 0

    def test_detect_changes_group_added(self, test_session):
        """Test detection when a new group is added."""
        detector = M3UChangeDetector(test_session)

        # Initial state
        detector.detect_changes(
            1,
            [{"name": "Sports", "stream_count": 50}],
            50,
        )

        # Add a new group
        change_set = detector.detect_changes(
            1,
            [
                {"name": "Sports", "stream_count": 50},
                {"name": "Movies", "stream_count": 100},
            ],
            150,
        )

        assert len(change_set.groups_added) == 1
        assert change_set.groups_added[0].group_name == "Movies"
        assert change_set.groups_added[0].stream_count == 100

    def test_detect_changes_group_removed(self, test_session):
        """Test detection when a group is removed."""
        detector = M3UChangeDetector(test_session)

        # Initial state with two groups
        detector.detect_changes(
            1,
            [
                {"name": "Sports", "stream_count": 50},
                {"name": "Movies", "stream_count": 100},
            ],
            150,
        )

        # Remove Movies group
        change_set = detector.detect_changes(
            1,
            [{"name": "Sports", "stream_count": 50}],
            50,
        )

        assert len(change_set.groups_removed) == 1
        assert change_set.groups_removed[0].group_name == "Movies"
        assert change_set.groups_removed[0].stream_count == 100

    def test_detect_changes_streams_added_to_existing_group(self, test_session):
        """Test detection when streams are added to an existing group."""
        detector = M3UChangeDetector(test_session)

        # Initial state
        detector.detect_changes(
            1,
            [{"name": "Sports", "stream_count": 50, "enabled": True}],
            50,
        )

        # Add streams to Sports
        change_set = detector.detect_changes(
            1,
            [{"name": "Sports", "stream_count": 60, "enabled": True}],
            60,
        )

        assert len(change_set.streams_added) == 1
        assert change_set.streams_added[0].group_name == "Sports"
        assert change_set.streams_added[0].count == 10
        assert change_set.streams_added[0].enabled is True

    def test_detect_changes_streams_removed_from_existing_group(self, test_session):
        """Test detection when streams are removed from an existing group."""
        detector = M3UChangeDetector(test_session)

        # Initial state
        detector.detect_changes(
            1,
            [{"name": "Sports", "stream_count": 50}],
            50,
        )

        # Remove streams from Sports
        change_set = detector.detect_changes(
            1,
            [{"name": "Sports", "stream_count": 40}],
            40,
        )

        assert len(change_set.streams_removed) == 1
        assert change_set.streams_removed[0].group_name == "Sports"
        assert change_set.streams_removed[0].count == 10

    def test_detect_changes_with_stream_names(self, test_session):
        """Test detection with stream names provided."""
        detector = M3UChangeDetector(test_session)

        # Initial state
        detector.detect_changes(
            1,
            [{"name": "Sports", "stream_count": 2}],
            2,
        )

        # Add streams with names
        stream_names_by_group = {"Sports": ["ESPN HD", "Fox Sports 1", "NBC Sports", "CBS Sports"]}
        change_set = detector.detect_changes(
            1,
            [{"name": "Sports", "stream_count": 4}],
            4,
            stream_names_by_group=stream_names_by_group,
        )

        # Compare as sets since order is not guaranteed
        assert set(change_set.streams_added[0].stream_names) == set(stream_names_by_group["Sports"])

    def test_detect_changes_complex_scenario(self, test_session):
        """Test detection with multiple types of changes at once."""
        detector = M3UChangeDetector(test_session)

        # Initial state
        detector.detect_changes(
            1,
            [
                {"name": "Sports", "stream_count": 50},
                {"name": "Movies", "stream_count": 100},
                {"name": "News", "stream_count": 30},
            ],
            180,
        )

        # Complex changes:
        # - Sports: 50 -> 60 streams (added)
        # - Movies: removed entirely
        # - News: 30 -> 25 streams (removed)
        # - Kids: new group with 20 streams (added)
        change_set = detector.detect_changes(
            1,
            [
                {"name": "Sports", "stream_count": 60},
                {"name": "News", "stream_count": 25},
                {"name": "Kids", "stream_count": 20},
            ],
            105,
        )

        assert len(change_set.groups_added) == 1
        assert change_set.groups_added[0].group_name == "Kids"

        assert len(change_set.groups_removed) == 1
        assert change_set.groups_removed[0].group_name == "Movies"

        assert len(change_set.streams_added) == 1
        assert change_set.streams_added[0].group_name == "Sports"
        assert change_set.streams_added[0].count == 10

        assert len(change_set.streams_removed) == 1
        assert change_set.streams_removed[0].group_name == "News"
        assert change_set.streams_removed[0].count == 5

        assert change_set.total_streams_before == 180
        assert change_set.total_streams_after == 105
        assert change_set.stream_count_delta == -75

    def test_persist_changes_empty(self, test_session):
        """Test persisting an empty change set creates no logs."""
        detector = M3UChangeDetector(test_session)
        change_set = M3UChangeSet(
            m3u_account_id=1,
            previous_snapshot_id=1,
            current_snapshot_id=2,
        )

        logs = detector.persist_changes(change_set)
        assert logs == []

    def test_persist_changes(self, test_session):
        """Test persisting changes creates correct log entries."""
        detector = M3UChangeDetector(test_session)

        change_set = M3UChangeSet(
            m3u_account_id=1,
            previous_snapshot_id=None,
            current_snapshot_id=1,
        )
        change_set.groups_added.append(GroupChange("Sports", "added", 50, enabled=True))
        change_set.streams_added.append(
            StreamChange("Sports", "streams_added", ["ESPN", "Fox"], 50, enabled=True)
        )

        logs = detector.persist_changes(change_set)

        assert len(logs) == 2

        # Verify group added log
        group_log = next(l for l in logs if l.change_type == "group_added")
        assert group_log.group_name == "Sports"
        assert group_log.count == 50
        assert group_log.enabled is True

        # Verify streams added log
        stream_log = next(l for l in logs if l.change_type == "streams_added")
        assert stream_log.group_name == "Sports"
        assert stream_log.count == 50
        assert "ESPN" in stream_log.get_stream_names()

    def test_get_changes_since(self, test_session):
        """Test retrieving changes since a given time."""
        detector = M3UChangeDetector(test_session)

        # Create a snapshot for the change log to reference
        snapshot = detector.create_snapshot(1, [{"name": "Test", "stream_count": 10}], 10)

        # Create some change logs at different times
        now = datetime.utcnow()
        old_time = now - timedelta(hours=2)
        recent_time = now - timedelta(minutes=30)

        log1 = M3UChangeLog(
            m3u_account_id=1,
            change_time=old_time,
            change_type="group_added",
            group_name="Old Group",
            count=10,
            snapshot_id=snapshot.id,
        )
        log2 = M3UChangeLog(
            m3u_account_id=1,
            change_time=recent_time,
            change_type="streams_added",
            group_name="Recent Group",
            count=5,
            snapshot_id=snapshot.id,
        )
        test_session.add_all([log1, log2])
        test_session.commit()

        # Get changes from last hour
        since = now - timedelta(hours=1)
        changes = detector.get_changes_since(since)

        assert len(changes) == 1
        assert changes[0].group_name == "Recent Group"

    def test_get_changes_since_filter_by_account(self, test_session):
        """Test filtering changes by M3U account ID."""
        detector = M3UChangeDetector(test_session)

        # Create snapshots for change logs to reference
        snapshot1 = detector.create_snapshot(1, [{"name": "Test", "stream_count": 10}], 10)
        snapshot2 = detector.create_snapshot(2, [{"name": "Test", "stream_count": 10}], 10)

        now = datetime.utcnow()

        log1 = M3UChangeLog(
            m3u_account_id=1,
            change_time=now,
            change_type="group_added",
            group_name="Account 1 Group",
            count=10,
            snapshot_id=snapshot1.id,
        )
        log2 = M3UChangeLog(
            m3u_account_id=2,
            change_time=now,
            change_type="group_added",
            group_name="Account 2 Group",
            count=20,
            snapshot_id=snapshot2.id,
        )
        test_session.add_all([log1, log2])
        test_session.commit()

        # Filter by account 1
        since = now - timedelta(hours=1)
        changes = detector.get_changes_since(since, m3u_account_id=1)

        assert len(changes) == 1
        assert changes[0].m3u_account_id == 1

    def test_get_change_summary(self, test_session):
        """Test getting aggregated change summary."""
        detector = M3UChangeDetector(test_session)

        # Create snapshots for change logs
        snapshot1 = detector.create_snapshot(1, [{"name": "Test", "stream_count": 10}], 10)
        snapshot2 = detector.create_snapshot(2, [{"name": "Test", "stream_count": 10}], 10)

        now = datetime.utcnow()

        logs = [
            M3UChangeLog(m3u_account_id=1, change_time=now, change_type="group_added",
                        group_name="G1", count=10, snapshot_id=snapshot1.id),
            M3UChangeLog(m3u_account_id=1, change_time=now, change_type="group_removed",
                        group_name="G2", count=5, snapshot_id=snapshot1.id),
            M3UChangeLog(m3u_account_id=2, change_time=now, change_type="streams_added",
                        group_name="G3", count=20, snapshot_id=snapshot2.id),
            M3UChangeLog(m3u_account_id=2, change_time=now, change_type="streams_removed",
                        group_name="G4", count=8, snapshot_id=snapshot2.id),
        ]
        test_session.add_all(logs)
        test_session.commit()

        since = now - timedelta(hours=1)
        summary = detector.get_change_summary(since)

        assert summary["total_changes"] == 4
        assert summary["groups_added"] == 1
        assert summary["groups_removed"] == 1
        assert summary["streams_added"] == 20
        assert summary["streams_removed"] == 8
        assert set(summary["accounts_affected"]) == {1, 2}
