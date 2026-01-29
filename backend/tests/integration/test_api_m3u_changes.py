"""
Integration tests for the M3U Changes API endpoints.

These tests use the FastAPI test client with database session overrides
to test the M3U change tracking endpoints in isolation.
"""
import pytest
from datetime import datetime, timedelta

from models import M3USnapshot, M3UChangeLog


class TestGetM3UChanges:
    """Tests for GET /api/m3u/changes endpoint."""

    @pytest.mark.asyncio
    async def test_get_changes_empty(self, async_client):
        """GET /api/m3u/changes returns empty list when no changes exist."""
        response = await async_client.get("/api/m3u/changes")
        assert response.status_code == 200

        data = response.json()
        assert "results" in data
        assert "total" in data
        assert data["results"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_get_changes_with_data(self, async_client, test_session):
        """GET /api/m3u/changes returns change log entries."""
        # Create a snapshot first (required for change log foreign key)
        snapshot = M3USnapshot(
            m3u_account_id=1,
            snapshot_time=datetime.utcnow(),
            total_streams=100,
        )
        snapshot.set_groups_data({"groups": [{"name": "Sports", "stream_count": 100}]})
        test_session.add(snapshot)
        test_session.commit()
        test_session.refresh(snapshot)

        # Create change log entries
        change1 = M3UChangeLog(
            m3u_account_id=1,
            change_time=datetime.utcnow(),
            change_type="group_added",
            group_name="Sports",
            count=100,
            enabled=True,
            snapshot_id=snapshot.id,
        )
        change2 = M3UChangeLog(
            m3u_account_id=1,
            change_time=datetime.utcnow(),
            change_type="streams_added",
            group_name="Sports",
            count=50,
            enabled=True,
            snapshot_id=snapshot.id,
        )
        test_session.add_all([change1, change2])
        test_session.commit()

        response = await async_client.get("/api/m3u/changes")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 2
        assert len(data["results"]) == 2

    @pytest.mark.asyncio
    async def test_get_changes_pagination(self, async_client, test_session):
        """GET /api/m3u/changes supports pagination."""
        # Create snapshot
        snapshot = M3USnapshot(
            m3u_account_id=1,
            snapshot_time=datetime.utcnow(),
            total_streams=100,
        )
        snapshot.set_groups_data({"groups": []})
        test_session.add(snapshot)
        test_session.commit()
        test_session.refresh(snapshot)

        # Create 15 change log entries
        for i in range(15):
            change = M3UChangeLog(
                m3u_account_id=1,
                change_time=datetime.utcnow() - timedelta(minutes=i),
                change_type="streams_added",
                group_name=f"Group{i}",
                count=i + 1,
                snapshot_id=snapshot.id,
            )
            test_session.add(change)
        test_session.commit()

        # Get page 1 with page_size=10
        response = await async_client.get("/api/m3u/changes?page=1&page_size=10")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 15
        assert len(data["results"]) == 10
        assert data["page"] == 1
        assert data["total_pages"] == 2

        # Get page 2
        response = await async_client.get("/api/m3u/changes?page=2&page_size=10")
        data = response.json()
        assert len(data["results"]) == 5
        assert data["page"] == 2

    @pytest.mark.asyncio
    async def test_get_changes_filter_by_account(self, async_client, test_session):
        """GET /api/m3u/changes filters by m3u_account_id."""
        # Create snapshots for different accounts
        snapshot1 = M3USnapshot(m3u_account_id=1, snapshot_time=datetime.utcnow(), total_streams=50)
        snapshot1.set_groups_data({"groups": []})
        snapshot2 = M3USnapshot(m3u_account_id=2, snapshot_time=datetime.utcnow(), total_streams=75)
        snapshot2.set_groups_data({"groups": []})
        test_session.add_all([snapshot1, snapshot2])
        test_session.commit()
        test_session.refresh(snapshot1)
        test_session.refresh(snapshot2)

        # Create changes for different accounts
        change1 = M3UChangeLog(m3u_account_id=1, change_time=datetime.utcnow(),
                               change_type="group_added", group_name="G1", count=10, snapshot_id=snapshot1.id)
        change2 = M3UChangeLog(m3u_account_id=2, change_time=datetime.utcnow(),
                               change_type="group_added", group_name="G2", count=20, snapshot_id=snapshot2.id)
        test_session.add_all([change1, change2])
        test_session.commit()

        # Filter by account 1
        response = await async_client.get("/api/m3u/changes?m3u_account_id=1")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["results"][0]["m3u_account_id"] == 1

    @pytest.mark.asyncio
    async def test_get_changes_filter_by_type(self, async_client, test_session):
        """GET /api/m3u/changes filters by change_type."""
        snapshot = M3USnapshot(m3u_account_id=1, snapshot_time=datetime.utcnow(), total_streams=100)
        snapshot.set_groups_data({"groups": []})
        test_session.add(snapshot)
        test_session.commit()
        test_session.refresh(snapshot)

        change1 = M3UChangeLog(m3u_account_id=1, change_time=datetime.utcnow(),
                               change_type="group_added", group_name="G1", count=10, snapshot_id=snapshot.id)
        change2 = M3UChangeLog(m3u_account_id=1, change_time=datetime.utcnow(),
                               change_type="streams_added", group_name="G1", count=50, snapshot_id=snapshot.id)
        test_session.add_all([change1, change2])
        test_session.commit()

        response = await async_client.get("/api/m3u/changes?change_type=group_added")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["results"][0]["change_type"] == "group_added"

    @pytest.mark.asyncio
    async def test_get_changes_filter_by_enabled(self, async_client, test_session):
        """GET /api/m3u/changes filters by enabled status."""
        snapshot = M3USnapshot(m3u_account_id=1, snapshot_time=datetime.utcnow(), total_streams=100)
        snapshot.set_groups_data({"groups": []})
        test_session.add(snapshot)
        test_session.commit()
        test_session.refresh(snapshot)

        change1 = M3UChangeLog(m3u_account_id=1, change_time=datetime.utcnow(),
                               change_type="group_added", group_name="Enabled", count=10,
                               enabled=True, snapshot_id=snapshot.id)
        change2 = M3UChangeLog(m3u_account_id=1, change_time=datetime.utcnow(),
                               change_type="group_added", group_name="Disabled", count=20,
                               enabled=False, snapshot_id=snapshot.id)
        test_session.add_all([change1, change2])
        test_session.commit()

        response = await async_client.get("/api/m3u/changes?enabled=true")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["results"][0]["enabled"] is True

    @pytest.mark.asyncio
    async def test_get_changes_sorting(self, async_client, test_session):
        """GET /api/m3u/changes supports sorting."""
        snapshot = M3USnapshot(m3u_account_id=1, snapshot_time=datetime.utcnow(), total_streams=100)
        snapshot.set_groups_data({"groups": []})
        test_session.add(snapshot)
        test_session.commit()
        test_session.refresh(snapshot)

        change1 = M3UChangeLog(m3u_account_id=1, change_time=datetime.utcnow() - timedelta(hours=1),
                               change_type="group_added", group_name="First", count=10, snapshot_id=snapshot.id)
        change2 = M3UChangeLog(m3u_account_id=1, change_time=datetime.utcnow(),
                               change_type="group_added", group_name="Second", count=20, snapshot_id=snapshot.id)
        test_session.add_all([change1, change2])
        test_session.commit()

        # Default sort is desc by change_time
        response = await async_client.get("/api/m3u/changes")
        data = response.json()
        assert data["results"][0]["group_name"] == "Second"

        # Sort asc
        response = await async_client.get("/api/m3u/changes?sort_order=asc")
        data = response.json()
        assert data["results"][0]["group_name"] == "First"


class TestGetM3UChangesSummary:
    """Tests for GET /api/m3u/changes/summary endpoint."""

    @pytest.mark.asyncio
    async def test_get_summary_empty(self, async_client):
        """GET /api/m3u/changes/summary returns zeros when no changes exist."""
        response = await async_client.get("/api/m3u/changes/summary")
        assert response.status_code == 200

        data = response.json()
        assert data["total_changes"] == 0
        assert data["groups_added"] == 0
        assert data["groups_removed"] == 0
        assert data["streams_added"] == 0
        assert data["streams_removed"] == 0

    @pytest.mark.asyncio
    async def test_get_summary_with_data(self, async_client, test_session):
        """GET /api/m3u/changes/summary returns aggregated counts."""
        snapshot = M3USnapshot(m3u_account_id=1, snapshot_time=datetime.utcnow(), total_streams=100)
        snapshot.set_groups_data({"groups": []})
        test_session.add(snapshot)
        test_session.commit()
        test_session.refresh(snapshot)

        now = datetime.utcnow()
        changes = [
            M3UChangeLog(m3u_account_id=1, change_time=now, change_type="group_added",
                        group_name="G1", count=10, snapshot_id=snapshot.id),
            M3UChangeLog(m3u_account_id=1, change_time=now, change_type="group_removed",
                        group_name="G2", count=5, snapshot_id=snapshot.id),
            M3UChangeLog(m3u_account_id=1, change_time=now, change_type="streams_added",
                        group_name="G3", count=50, snapshot_id=snapshot.id),
            M3UChangeLog(m3u_account_id=1, change_time=now, change_type="streams_removed",
                        group_name="G4", count=20, snapshot_id=snapshot.id),
        ]
        test_session.add_all(changes)
        test_session.commit()

        response = await async_client.get("/api/m3u/changes/summary")
        assert response.status_code == 200

        data = response.json()
        assert data["total_changes"] == 4
        assert data["groups_added"] == 1
        assert data["groups_removed"] == 1
        assert data["streams_added"] == 50
        assert data["streams_removed"] == 20

    @pytest.mark.asyncio
    async def test_get_summary_hours_filter(self, async_client, test_session):
        """GET /api/m3u/changes/summary filters by hours parameter."""
        snapshot = M3USnapshot(m3u_account_id=1, snapshot_time=datetime.utcnow(), total_streams=100)
        snapshot.set_groups_data({"groups": []})
        test_session.add(snapshot)
        test_session.commit()
        test_session.refresh(snapshot)

        now = datetime.utcnow()
        # Recent change (within 1 hour)
        recent = M3UChangeLog(m3u_account_id=1, change_time=now - timedelta(minutes=30),
                              change_type="group_added", group_name="Recent", count=10, snapshot_id=snapshot.id)
        # Old change (48 hours ago)
        old = M3UChangeLog(m3u_account_id=1, change_time=now - timedelta(hours=48),
                           change_type="group_added", group_name="Old", count=20, snapshot_id=snapshot.id)
        test_session.add_all([recent, old])
        test_session.commit()

        # Default 24 hours - should only include recent
        response = await async_client.get("/api/m3u/changes/summary?hours=24")
        data = response.json()
        assert data["groups_added"] == 1

        # 72 hours - should include both
        response = await async_client.get("/api/m3u/changes/summary?hours=72")
        data = response.json()
        assert data["groups_added"] == 2

    @pytest.mark.asyncio
    async def test_get_summary_filter_by_account(self, async_client, test_session):
        """GET /api/m3u/changes/summary filters by m3u_account_id."""
        snapshot1 = M3USnapshot(m3u_account_id=1, snapshot_time=datetime.utcnow(), total_streams=50)
        snapshot1.set_groups_data({"groups": []})
        snapshot2 = M3USnapshot(m3u_account_id=2, snapshot_time=datetime.utcnow(), total_streams=75)
        snapshot2.set_groups_data({"groups": []})
        test_session.add_all([snapshot1, snapshot2])
        test_session.commit()
        test_session.refresh(snapshot1)
        test_session.refresh(snapshot2)

        now = datetime.utcnow()
        change1 = M3UChangeLog(m3u_account_id=1, change_time=now, change_type="streams_added",
                               group_name="G1", count=100, snapshot_id=snapshot1.id)
        change2 = M3UChangeLog(m3u_account_id=2, change_time=now, change_type="streams_added",
                               group_name="G2", count=200, snapshot_id=snapshot2.id)
        test_session.add_all([change1, change2])
        test_session.commit()

        response = await async_client.get("/api/m3u/changes/summary?m3u_account_id=1")
        assert response.status_code == 200
        data = response.json()
        assert data["streams_added"] == 100
        assert 1 in data["accounts_affected"]
        assert 2 not in data["accounts_affected"]


class TestGetM3UAccountChanges:
    """Tests for GET /api/m3u/accounts/{account_id}/changes endpoint."""

    @pytest.mark.asyncio
    async def test_get_account_changes_empty(self, async_client):
        """GET /api/m3u/accounts/{id}/changes returns empty for non-existent account."""
        response = await async_client.get("/api/m3u/accounts/999/changes")
        assert response.status_code == 200

        data = response.json()
        assert data["results"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_get_account_changes_with_data(self, async_client, test_session):
        """GET /api/m3u/accounts/{id}/changes returns changes for specific account."""
        snapshot = M3USnapshot(m3u_account_id=1, snapshot_time=datetime.utcnow(), total_streams=100)
        snapshot.set_groups_data({"groups": []})
        test_session.add(snapshot)
        test_session.commit()
        test_session.refresh(snapshot)

        change = M3UChangeLog(m3u_account_id=1, change_time=datetime.utcnow(),
                              change_type="group_added", group_name="Sports", count=50, snapshot_id=snapshot.id)
        test_session.add(change)
        test_session.commit()

        response = await async_client.get("/api/m3u/accounts/1/changes")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 1
        assert data["results"][0]["group_name"] == "Sports"
        assert data["results"][0]["m3u_account_id"] == 1

    @pytest.mark.asyncio
    async def test_get_account_changes_filter_by_type(self, async_client, test_session):
        """GET /api/m3u/accounts/{id}/changes filters by change_type."""
        snapshot = M3USnapshot(m3u_account_id=1, snapshot_time=datetime.utcnow(), total_streams=100)
        snapshot.set_groups_data({"groups": []})
        test_session.add(snapshot)
        test_session.commit()
        test_session.refresh(snapshot)

        change1 = M3UChangeLog(m3u_account_id=1, change_time=datetime.utcnow(),
                               change_type="group_added", group_name="G1", count=10, snapshot_id=snapshot.id)
        change2 = M3UChangeLog(m3u_account_id=1, change_time=datetime.utcnow(),
                               change_type="streams_added", group_name="G1", count=50, snapshot_id=snapshot.id)
        test_session.add_all([change1, change2])
        test_session.commit()

        response = await async_client.get("/api/m3u/accounts/1/changes?change_type=streams_added")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 1
        assert data["results"][0]["change_type"] == "streams_added"

    @pytest.mark.asyncio
    async def test_get_account_changes_pagination(self, async_client, test_session):
        """GET /api/m3u/accounts/{id}/changes supports pagination."""
        snapshot = M3USnapshot(m3u_account_id=1, snapshot_time=datetime.utcnow(), total_streams=100)
        snapshot.set_groups_data({"groups": []})
        test_session.add(snapshot)
        test_session.commit()
        test_session.refresh(snapshot)

        # Create 12 changes
        for i in range(12):
            change = M3UChangeLog(m3u_account_id=1, change_time=datetime.utcnow() - timedelta(minutes=i),
                                  change_type="streams_added", group_name=f"G{i}", count=i, snapshot_id=snapshot.id)
            test_session.add(change)
        test_session.commit()

        response = await async_client.get("/api/m3u/accounts/1/changes?page=1&page_size=5")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 12
        assert len(data["results"]) == 5
        assert data["page"] == 1
        assert data["total_pages"] == 3

    @pytest.mark.asyncio
    async def test_get_account_changes_only_returns_account_data(self, async_client, test_session):
        """GET /api/m3u/accounts/{id}/changes only returns changes for that account."""
        snapshot1 = M3USnapshot(m3u_account_id=1, snapshot_time=datetime.utcnow(), total_streams=50)
        snapshot1.set_groups_data({"groups": []})
        snapshot2 = M3USnapshot(m3u_account_id=2, snapshot_time=datetime.utcnow(), total_streams=75)
        snapshot2.set_groups_data({"groups": []})
        test_session.add_all([snapshot1, snapshot2])
        test_session.commit()
        test_session.refresh(snapshot1)
        test_session.refresh(snapshot2)

        change1 = M3UChangeLog(m3u_account_id=1, change_time=datetime.utcnow(),
                               change_type="group_added", group_name="Account1Group", count=10, snapshot_id=snapshot1.id)
        change2 = M3UChangeLog(m3u_account_id=2, change_time=datetime.utcnow(),
                               change_type="group_added", group_name="Account2Group", count=20, snapshot_id=snapshot2.id)
        test_session.add_all([change1, change2])
        test_session.commit()

        response = await async_client.get("/api/m3u/accounts/1/changes")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 1
        assert all(r["m3u_account_id"] == 1 for r in data["results"])
        assert data["results"][0]["group_name"] == "Account1Group"
