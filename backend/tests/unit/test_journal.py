"""
Unit tests for the journal module.

Note: The journal module interacts with the database, so these tests use
the test fixtures from conftest.py to provide isolated database sessions.
"""
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


class TestLogEntry:
    """Tests for log_entry() function."""

    def test_log_entry_creates_record(self, test_session):
        """log_entry creates a journal entry in the database."""
        from models import JournalEntry

        # Mock get_session to return our test session
        with patch("journal.get_session", return_value=test_session):
            from journal import log_entry

            result = log_entry(
                category="channel",
                action_type="create",
                entity_name="Test Channel",
                description="Created test channel",
            )

            assert result is not None
            assert result.category == "channel"
            assert result.action_type == "create"
            assert result.entity_name == "Test Channel"

    def test_log_entry_stores_entity_id(self, test_session):
        """log_entry stores entity_id."""
        with patch("journal.get_session", return_value=test_session):
            from journal import log_entry

            result = log_entry(
                category="channel",
                action_type="update",
                entity_name="Test Channel",
                description="Updated channel",
                entity_id=42,
            )

            assert result.entity_id == 42

    def test_log_entry_stores_before_after_values(self, test_session):
        """log_entry serializes before/after values as JSON."""
        with patch("journal.get_session", return_value=test_session):
            from journal import log_entry

            before = {"name": "Old Name"}
            after = {"name": "New Name"}

            result = log_entry(
                category="channel",
                action_type="update",
                entity_name="Test Channel",
                description="Renamed channel",
                before_value=before,
                after_value=after,
            )

            assert result.before_value == json.dumps(before)
            assert result.after_value == json.dumps(after)

    def test_log_entry_stores_user_initiated_flag(self, test_session):
        """log_entry stores user_initiated flag."""
        with patch("journal.get_session", return_value=test_session):
            from journal import log_entry

            result = log_entry(
                category="channel",
                action_type="create",
                entity_name="Test Channel",
                description="Auto-created",
                user_initiated=False,
            )

            assert result.user_initiated is False

    def test_log_entry_stores_batch_id(self, test_session):
        """log_entry stores batch_id for grouping."""
        with patch("journal.get_session", return_value=test_session):
            from journal import log_entry

            result = log_entry(
                category="channel",
                action_type="create",
                entity_name="Test Channel",
                description="Part of batch",
                batch_id="batch-123",
            )

            assert result.batch_id == "batch-123"

    def test_log_entry_sets_timestamp(self, test_session):
        """log_entry sets timestamp to current time."""
        with patch("journal.get_session", return_value=test_session):
            from journal import log_entry

            before = datetime.utcnow()
            result = log_entry(
                category="channel",
                action_type="create",
                entity_name="Test Channel",
                description="Created",
            )
            after = datetime.utcnow()

            assert before <= result.timestamp <= after

    def test_log_entry_returns_none_on_error(self, test_session):
        """log_entry returns None on database error."""
        mock_session = MagicMock()
        mock_session.add.side_effect = Exception("Database error")

        with patch("journal.get_session", return_value=mock_session):
            from journal import log_entry

            result = log_entry(
                category="channel",
                action_type="create",
                entity_name="Test Channel",
                description="Created",
            )

            assert result is None


class TestGetEntries:
    """Tests for get_entries() function."""

    def test_get_entries_returns_dict(self, test_session):
        """get_entries returns a dictionary with expected keys."""
        with patch("journal.get_session", return_value=test_session):
            from journal import get_entries

            result = get_entries()

            assert isinstance(result, dict)
            assert "count" in result
            assert "page" in result
            assert "page_size" in result
            assert "total_pages" in result
            assert "results" in result

    def test_get_entries_default_pagination(self, test_session):
        """get_entries uses default pagination."""
        with patch("journal.get_session", return_value=test_session):
            from journal import get_entries

            result = get_entries()

            assert result["page"] == 1
            assert result["page_size"] == 50

    def test_get_entries_filters_by_category(self, test_session):
        """get_entries filters by category."""
        from tests.fixtures.factories import create_journal_entry

        create_journal_entry(test_session, category="channel")
        create_journal_entry(test_session, category="epg")
        create_journal_entry(test_session, category="channel")

        with patch("journal.get_session", return_value=test_session):
            from journal import get_entries

            result = get_entries(category="channel")

            assert result["count"] == 2
            for entry in result["results"]:
                assert entry["category"] == "channel"

    def test_get_entries_filters_by_action_type(self, test_session):
        """get_entries filters by action_type."""
        from tests.fixtures.factories import create_journal_entry

        create_journal_entry(test_session, action_type="create")
        create_journal_entry(test_session, action_type="update")
        create_journal_entry(test_session, action_type="create")

        with patch("journal.get_session", return_value=test_session):
            from journal import get_entries

            result = get_entries(action_type="create")

            assert result["count"] == 2

    def test_get_entries_filters_by_date_range(self, test_session):
        """get_entries filters by date range."""
        from tests.fixtures.factories import create_journal_entry

        now = datetime.utcnow()
        old = now - timedelta(days=10)
        recent = now - timedelta(days=1)

        create_journal_entry(test_session, timestamp=old)
        create_journal_entry(test_session, timestamp=recent)
        create_journal_entry(test_session, timestamp=now)

        with patch("journal.get_session", return_value=test_session):
            from journal import get_entries

            result = get_entries(
                date_from=now - timedelta(days=5),
                date_to=now + timedelta(days=1),
            )

            # Should get recent and now, but not old
            assert result["count"] == 2

    def test_get_entries_searches_entity_name(self, test_session):
        """get_entries searches entity_name."""
        from tests.fixtures.factories import create_journal_entry

        create_journal_entry(test_session, entity_name="ESPN")
        create_journal_entry(test_session, entity_name="CNN")
        create_journal_entry(test_session, entity_name="ESPN HD")

        with patch("journal.get_session", return_value=test_session):
            from journal import get_entries

            result = get_entries(search="ESPN")

            assert result["count"] == 2

    def test_get_entries_searches_description(self, test_session):
        """get_entries searches description."""
        from tests.fixtures.factories import create_journal_entry

        create_journal_entry(test_session, description="Created new channel")
        create_journal_entry(test_session, description="Updated settings")
        create_journal_entry(test_session, description="Created another channel")

        with patch("journal.get_session", return_value=test_session):
            from journal import get_entries

            result = get_entries(search="Created")

            assert result["count"] == 2

    def test_get_entries_filters_user_initiated(self, test_session):
        """get_entries filters by user_initiated flag."""
        from tests.fixtures.factories import create_journal_entry

        create_journal_entry(test_session, user_initiated=True)
        create_journal_entry(test_session, user_initiated=False)
        create_journal_entry(test_session, user_initiated=True)

        with patch("journal.get_session", return_value=test_session):
            from journal import get_entries

            result = get_entries(user_initiated=True)

            assert result["count"] == 2

    def test_get_entries_orders_by_timestamp_desc(self, test_session):
        """get_entries orders by timestamp descending (newest first)."""
        from tests.fixtures.factories import create_journal_entry

        now = datetime.utcnow()
        create_journal_entry(test_session, entity_name="First", timestamp=now - timedelta(hours=2))
        create_journal_entry(test_session, entity_name="Second", timestamp=now - timedelta(hours=1))
        create_journal_entry(test_session, entity_name="Third", timestamp=now)

        with patch("journal.get_session", return_value=test_session):
            from journal import get_entries

            result = get_entries()

            assert result["results"][0]["entity_name"] == "Third"
            assert result["results"][1]["entity_name"] == "Second"
            assert result["results"][2]["entity_name"] == "First"

    def test_get_entries_paginates_correctly(self, test_session):
        """get_entries paginates results correctly."""
        from tests.fixtures.factories import create_journal_entry

        # Create 15 entries
        for i in range(15):
            create_journal_entry(test_session, entity_name=f"Entry {i}")

        with patch("journal.get_session", return_value=test_session):
            from journal import get_entries

            page1 = get_entries(page=1, page_size=10)
            page2 = get_entries(page=2, page_size=10)

            assert page1["count"] == 15
            assert len(page1["results"]) == 10
            assert len(page2["results"]) == 5
            assert page1["total_pages"] == 2


class TestGetStats:
    """Tests for get_stats() function."""

    def test_get_stats_returns_dict(self, test_session):
        """get_stats returns dictionary with expected keys."""
        with patch("journal.get_session", return_value=test_session):
            from journal import get_stats

            result = get_stats()

            assert isinstance(result, dict)
            assert "total_entries" in result
            assert "by_category" in result
            assert "by_action_type" in result
            assert "date_range" in result

    def test_get_stats_counts_total(self, test_session):
        """get_stats counts total entries."""
        from tests.fixtures.factories import create_journal_entry

        create_journal_entry(test_session)
        create_journal_entry(test_session)
        create_journal_entry(test_session)

        with patch("journal.get_session", return_value=test_session):
            from journal import get_stats

            result = get_stats()

            assert result["total_entries"] == 3

    def test_get_stats_groups_by_category(self, test_session):
        """get_stats groups entries by category."""
        from tests.fixtures.factories import create_journal_entry

        create_journal_entry(test_session, category="channel")
        create_journal_entry(test_session, category="channel")
        create_journal_entry(test_session, category="epg")

        with patch("journal.get_session", return_value=test_session):
            from journal import get_stats

            result = get_stats()

            assert result["by_category"]["channel"] == 2
            assert result["by_category"]["epg"] == 1

    def test_get_stats_groups_by_action_type(self, test_session):
        """get_stats groups entries by action_type."""
        from tests.fixtures.factories import create_journal_entry

        create_journal_entry(test_session, action_type="create")
        create_journal_entry(test_session, action_type="create")
        create_journal_entry(test_session, action_type="update")

        with patch("journal.get_session", return_value=test_session):
            from journal import get_stats

            result = get_stats()

            assert result["by_action_type"]["create"] == 2
            assert result["by_action_type"]["update"] == 1

    def test_get_stats_returns_date_range(self, test_session):
        """get_stats returns date range of entries."""
        from tests.fixtures.factories import create_journal_entry

        now = datetime.utcnow()
        old = now - timedelta(days=5)

        create_journal_entry(test_session, timestamp=old)
        create_journal_entry(test_session, timestamp=now)

        with patch("journal.get_session", return_value=test_session):
            from journal import get_stats

            result = get_stats()

            assert result["date_range"]["oldest"] is not None
            assert result["date_range"]["newest"] is not None

    def test_get_stats_handles_empty_journal(self, test_session):
        """get_stats handles empty journal."""
        with patch("journal.get_session", return_value=test_session):
            from journal import get_stats

            result = get_stats()

            assert result["total_entries"] == 0
            assert result["by_category"] == {}
            assert result["by_action_type"] == {}
            assert result["date_range"]["oldest"] is None
            assert result["date_range"]["newest"] is None


class TestPurgeOldEntries:
    """Tests for purge_old_entries() function."""

    def test_purge_deletes_old_entries(self, test_session):
        """purge_old_entries deletes entries older than specified days."""
        from tests.fixtures.factories import create_journal_entry

        now = datetime.utcnow()
        create_journal_entry(test_session, timestamp=now - timedelta(days=100))
        create_journal_entry(test_session, timestamp=now - timedelta(days=100))
        create_journal_entry(test_session, timestamp=now)

        with patch("journal.get_session", return_value=test_session):
            from journal import purge_old_entries

            deleted = purge_old_entries(days=90)

            assert deleted == 2

    def test_purge_keeps_recent_entries(self, test_session):
        """purge_old_entries keeps recent entries."""
        from tests.fixtures.factories import create_journal_entry
        from models import JournalEntry

        now = datetime.utcnow()
        create_journal_entry(test_session, timestamp=now - timedelta(days=10))
        create_journal_entry(test_session, timestamp=now)

        with patch("journal.get_session", return_value=test_session):
            from journal import purge_old_entries

            purge_old_entries(days=90)

            # Check remaining entries
            remaining = test_session.query(JournalEntry).count()
            assert remaining == 2

    def test_purge_uses_default_days(self, test_session):
        """purge_old_entries uses default of 90 days."""
        from tests.fixtures.factories import create_journal_entry

        now = datetime.utcnow()
        create_journal_entry(test_session, timestamp=now - timedelta(days=100))
        create_journal_entry(test_session, timestamp=now - timedelta(days=50))

        with patch("journal.get_session", return_value=test_session):
            from journal import purge_old_entries

            # Default is 90 days
            deleted = purge_old_entries()

            assert deleted == 1  # Only the 100-day-old entry

    def test_purge_returns_zero_when_nothing_to_delete(self, test_session):
        """purge_old_entries returns 0 when no old entries."""
        from tests.fixtures.factories import create_journal_entry

        create_journal_entry(test_session, timestamp=datetime.utcnow())

        with patch("journal.get_session", return_value=test_session):
            from journal import purge_old_entries

            deleted = purge_old_entries(days=90)

            assert deleted == 0
