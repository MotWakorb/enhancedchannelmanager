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


class TestLogEntries:
    """Tests for log_entries() batch logging."""

    def test_log_entries_empty_batch_noops(self):
        """Empty batches succeed without opening a database session."""
        with patch("journal.get_session") as mock_get_session:
            from journal import log_entries

            result = log_entries([])

        assert result is True
        mock_get_session.assert_not_called()

    def test_log_entries_creates_records_in_one_batch(self, test_session):
        """log_entries creates all requested journal rows."""
        from models import JournalEntry

        entries = [
            {
                "category": "auto_creation",
                "action_type": "merge_stream",
                "entity_id": 1,
                "entity_name": "ESPN",
                "description": "Merged stream 201 into channel 'ESPN'",
                "before_value": {"stream_ids": [101]},
                "after_value": {"stream_ids": [101, 201]},
                "user_initiated": False,
                "batch_id": "42",
            },
            {
                "category": "auto_creation",
                "action_type": "merge_stream",
                "entity_id": 2,
                "entity_name": "CNN",
                "description": "Merged stream 202 into channel 'CNN'",
                "before_value": {"stream_ids": []},
                "after_value": {"stream_ids": [202]},
                "user_initiated": False,
                "batch_id": "42",
            },
        ]

        with patch("journal.get_session", return_value=test_session):
            from journal import log_entries

            result = log_entries(entries)

        assert result is True
        rows = (
            test_session.query(JournalEntry)
            .filter(JournalEntry.batch_id == "42")
            .order_by(JournalEntry.entity_id)
            .all()
        )
        assert len(rows) == 2
        assert rows[0].entity_name == "ESPN"
        assert rows[0].before_value == json.dumps({"stream_ids": [101]})
        assert rows[0].after_value == json.dumps({"stream_ids": [101, 201]})
        assert rows[0].user_initiated is False
        assert rows[1].entity_name == "CNN"

    def test_log_entries_rolls_back_on_error(self):
        """A batch logging failure rolls back and reports False."""
        mock_session = MagicMock()
        mock_session.add_all.side_effect = Exception("Database error")

        with patch("journal.get_session", return_value=mock_session):
            from journal import log_entries

            result = log_entries([{
                "category": "auto_creation",
                "action_type": "merge_stream",
                "entity_name": "ESPN",
                "description": "Merged stream",
            }])

        assert result is False
        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()


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


class TestAutoCreationMergeJournalRoundTrip:
    """bd-0emgo.5: end-to-end round-trip — a LIVE auto-creation merge writes a
    real journal entry recoverable via get_entries(batch_id=...).

    Unlike the executor unit tests (which mock journal.log_entries), this drives
    the executor against the real journal module + a test DB session, then
    queries by batch_id to confirm the (channel_id, stream_id) audit trail an
    operator would use to recover from a bad run.
    """

    def _run_live_merge(self, execution_id, stream_id, channel):
        import asyncio
        from unittest.mock import MagicMock, AsyncMock
        from auto_creation_executor import ActionExecutor, ExecutionContext
        from auto_creation_evaluator import StreamContext

        client = MagicMock()
        client.update_channel = AsyncMock()
        executor = ActionExecutor(
            client,
            existing_channels=[channel],
            execution_id=execution_id,
        )
        stream_ctx = StreamContext(
            stream_id=stream_id,
            stream_name=f"Stream {stream_id}",
            stream_url=f"http://provider.example/secret-token/{stream_id}.ts",
            m3u_account_id=1,
            tvg_id="ESPN.US",
        )
        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "name_exact",
            "find_channel_value": "ESPN",
        }
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext())
        )
        executor._flush_journal_buffer()
        return result

    def test_live_merge_round_trips_through_journal_by_batch_id(self, test_session):
        """A live merge entry is queryable by its execution_id batch_id."""
        from journal import get_entries

        channel = {"id": 1, "name": "ESPN", "tvg_id": "ESPN.US",
                   "channel_number": 100, "streams": [101], "auto_created": True}

        with patch("journal.get_session", return_value=test_session):
            result = self._run_live_merge(execution_id=555, stream_id=201,
                                          channel=channel)
            assert result.success is True

            entries = get_entries(batch_id="555")

        assert entries["count"] == 1
        row = entries["results"][0]
        assert row["category"] == "auto_creation"
        assert row["action_type"] == "merge_stream"
        assert row["entity_id"] == 1  # channel_id
        assert row["batch_id"] == "555"
        assert row["user_initiated"] is False
        # Stream IDs recoverable from before/after (to_dict parses the JSON);
        # no credentials leaked.
        assert row["before_value"] == {"stream_ids": [101]}
        assert row["after_value"] == {"stream_ids": [101, 201]}
        assert "secret-token" not in json.dumps(row)

    def test_multi_merge_run_groups_all_pairs_under_one_batch_id(self, test_session):
        """All pairs a run touched are recoverable under the shared batch_id."""
        from journal import get_entries

        # Two channels, two live merges in the same run (execution_id=777).
        ch_a = {"id": 10, "name": "ESPN", "tvg_id": "ESPN.US",
                "channel_number": 100, "streams": [1001], "auto_created": True}
        ch_b = {"id": 20, "name": "ESPN", "tvg_id": "ESPN.US",
                "channel_number": 101, "streams": [], "auto_created": True}

        with patch("journal.get_session", return_value=test_session):
            self._run_live_merge(execution_id=777, stream_id=2001, channel=ch_a)
            self._run_live_merge(execution_id=777, stream_id=2002, channel=ch_b)

            entries = get_entries(batch_id="777")

        assert entries["count"] == 2
        pairs = {
            (r["entity_id"], r["after_value"]["stream_ids"][-1])
            for r in entries["results"]
        }
        assert pairs == {(10, 2001), (20, 2002)}


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
