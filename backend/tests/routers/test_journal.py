"""
Unit tests for journal endpoints.

Tests: GET /api/journal, GET /api/journal/stats, DELETE /api/journal/purge
Uses async_client fixture which patches database session for journal module.
"""
import pytest
from datetime import datetime, timedelta

from models import JournalEntry


def _create_journal_entry(session, **overrides):
    """Helper to create a JournalEntry with sensible defaults."""
    defaults = {
        "timestamp": datetime.utcnow(),
        "category": "channel",
        "action_type": "create",
        "entity_id": 1,
        "entity_name": "Test Channel",
        "description": "Created channel",
        "user_initiated": False,
    }
    defaults.update(overrides)
    entry = JournalEntry(**defaults)
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


class TestGetJournalEntries:
    """Tests for GET /api/journal endpoint."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_entries(self, async_client):
        """Returns empty results with pagination info."""
        response = await async_client.get("/api/journal")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["results"] == []
        assert data["page"] == 1

    @pytest.mark.asyncio
    async def test_returns_entries(self, async_client, test_session):
        """Returns journal entries ordered by newest first."""
        _old = _create_journal_entry(
            test_session,
            timestamp=datetime.utcnow() - timedelta(hours=2),
            entity_name="Old Entry",
        )
        _new = _create_journal_entry(
            test_session,
            timestamp=datetime.utcnow(),
            entity_name="New Entry",
        )

        response = await async_client.get("/api/journal")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert len(data["results"]) == 2
        # Newest first
        assert data["results"][0]["entity_name"] == "New Entry"
        assert data["results"][1]["entity_name"] == "Old Entry"

    @pytest.mark.asyncio
    async def test_pagination(self, async_client, test_session):
        """Pagination works correctly with page and page_size params."""
        for i in range(5):
            _create_journal_entry(
                test_session,
                timestamp=datetime.utcnow() - timedelta(minutes=i),
                entity_name=f"Entry {i}",
            )

        response = await async_client.get("/api/journal", params={"page": 2, "page_size": 2})
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 5
        assert data["page"] == 2
        assert data["page_size"] == 2
        assert len(data["results"]) == 2
        assert data["total_pages"] == 3

    @pytest.mark.asyncio
    async def test_filter_by_category(self, async_client, test_session):
        """Filters entries by category."""
        _create_journal_entry(test_session, category="channel")
        _create_journal_entry(test_session, category="task")
        _create_journal_entry(test_session, category="channel")

        response = await async_client.get("/api/journal", params={"category": "task"})
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["results"][0]["category"] == "task"

    @pytest.mark.asyncio
    async def test_filter_by_search(self, async_client, test_session):
        """Filters entries by search term in entity_name or description."""
        _create_journal_entry(test_session, entity_name="ESPN HD", description="Created")
        _create_journal_entry(test_session, entity_name="BBC One", description="Updated")

        response = await async_client.get("/api/journal", params={"search": "ESPN"})
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert "ESPN" in data["results"][0]["entity_name"]

    @pytest.mark.asyncio
    async def test_page_size_clamped(self, async_client, test_session):
        """page_size is clamped between 1 and 200."""
        _create_journal_entry(test_session)

        # Too large page_size gets clamped to 200
        response = await async_client.get("/api/journal", params={"page_size": 999})
        assert response.status_code == 200
        data = response.json()
        assert data["page_size"] == 200


class TestGetJournalStats:
    """Tests for GET /api/journal/stats endpoint."""

    @pytest.mark.asyncio
    async def test_stats_empty(self, async_client):
        """Returns zero counts when no entries exist."""
        response = await async_client.get("/api/journal/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_entries"] == 0
        assert data["by_category"] == {}
        assert data["by_action_type"] == {}

    @pytest.mark.asyncio
    async def test_stats_with_entries(self, async_client, test_session):
        """Returns correct category and action type breakdowns."""
        _create_journal_entry(test_session, category="channel", action_type="create")
        _create_journal_entry(test_session, category="channel", action_type="update")
        _create_journal_entry(test_session, category="task", action_type="create")

        response = await async_client.get("/api/journal/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_entries"] == 3
        assert data["by_category"]["channel"] == 2
        assert data["by_category"]["task"] == 1
        assert data["by_action_type"]["create"] == 2
        assert data["by_action_type"]["update"] == 1

    @pytest.mark.asyncio
    async def test_stats_includes_date_range(self, async_client, test_session):
        """Returns oldest and newest timestamps."""
        _create_journal_entry(
            test_session,
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
        )
        _create_journal_entry(
            test_session,
            timestamp=datetime(2025, 6, 15, 12, 0, 0),
        )

        response = await async_client.get("/api/journal/stats")
        data = response.json()
        assert data["date_range"]["oldest"] is not None
        assert data["date_range"]["newest"] is not None


class TestPurgeJournalEntries:
    """Tests for DELETE /api/journal/purge endpoint."""

    @pytest.mark.asyncio
    async def test_purge_old_entries(self, async_client, test_session):
        """Purges entries older than specified days."""
        # Create an old entry (100 days ago)
        _create_journal_entry(
            test_session,
            timestamp=datetime.utcnow() - timedelta(days=100),
            entity_name="Old",
        )
        # Create a recent entry
        _create_journal_entry(
            test_session,
            timestamp=datetime.utcnow(),
            entity_name="Recent",
        )

        response = await async_client.delete("/api/journal/purge", params={"days": 90})
        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] == 1
        assert data["days"] == 90

        # Verify only recent entry remains
        remaining = test_session.query(JournalEntry).count()
        assert remaining == 1

    @pytest.mark.asyncio
    async def test_purge_default_days(self, async_client, test_session):
        """Default purge is 90 days."""
        response = await async_client.delete("/api/journal/purge")
        assert response.status_code == 200
        data = response.json()
        assert data["days"] == 90

    @pytest.mark.asyncio
    async def test_purge_nothing_to_delete(self, async_client):
        """Returns 0 deleted when nothing is old enough."""
        response = await async_client.delete("/api/journal/purge", params={"days": 30})
        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] == 0
