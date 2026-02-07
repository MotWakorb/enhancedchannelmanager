"""
Unit tests for the auto-creation engine service.

Tests the AutoCreationEngine class which orchestrates the entire auto-creation
pipeline, coordinating rules, streams, and executions.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime
import asyncio

from auto_creation_engine import (
    AutoCreationEngine,
    get_auto_creation_engine,
    set_auto_creation_engine,
    init_auto_creation_engine,
)
from auto_creation_evaluator import StreamContext


class TestAutoCreationEngineInit:
    """Tests for AutoCreationEngine initialization."""

    def test_init(self):
        """Initialize engine with client."""
        client = MagicMock()
        engine = AutoCreationEngine(client)

        assert engine.client == client
        assert engine._existing_channels is None
        assert engine._existing_groups is None
        assert engine._stream_stats_cache == {}


class TestAutoCreationEngineSingleton:
    """Tests for singleton pattern helpers."""

    def test_get_engine_default_none(self):
        """get_auto_creation_engine returns None by default."""
        # Reset global
        set_auto_creation_engine(None)
        assert get_auto_creation_engine() is None

    def test_set_and_get_engine(self):
        """set_auto_creation_engine and get work together."""
        client = MagicMock()
        engine = AutoCreationEngine(client)

        set_auto_creation_engine(engine)
        result = get_auto_creation_engine()

        assert result is engine

    def test_init_auto_creation_engine(self):
        """init_auto_creation_engine creates and sets engine."""
        client = MagicMock()

        result = asyncio.get_event_loop().run_until_complete(
            init_auto_creation_engine(client)
        )

        assert result is not None
        assert get_auto_creation_engine() is result


class TestAutoCreationEngineLoadData:
    """Tests for data loading methods."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.get_channels = AsyncMock(return_value=[
            {"id": 1, "name": "ESPN"},
            {"id": 2, "name": "CNN"},
        ])
        self.client.get_channel_groups = AsyncMock(return_value=[
            {"id": 1, "name": "Sports"},
            {"id": 2, "name": "News"},
        ])
        self.engine = AutoCreationEngine(self.client)

    def test_load_existing_data_success(self):
        """Load existing channels and groups successfully."""
        asyncio.get_event_loop().run_until_complete(
            self.engine._load_existing_data()
        )

        assert len(self.engine._existing_channels) == 2
        assert len(self.engine._existing_groups) == 2
        self.client.get_channels.assert_called_once()
        self.client.get_channel_groups.assert_called_once()

    def test_load_existing_data_api_failure(self):
        """Load existing data handles API failures gracefully."""
        self.client.get_channels = AsyncMock(side_effect=Exception("API error"))
        self.client.get_channel_groups = AsyncMock(side_effect=Exception("API error"))

        asyncio.get_event_loop().run_until_complete(
            self.engine._load_existing_data()
        )

        assert self.engine._existing_channels == []
        assert self.engine._existing_groups == []

    def test_load_existing_data_empty_response(self):
        """Load existing data handles empty responses."""
        self.client.get_channels = AsyncMock(return_value=None)
        self.client.get_channel_groups = AsyncMock(return_value=None)

        asyncio.get_event_loop().run_until_complete(
            self.engine._load_existing_data()
        )

        assert self.engine._existing_channels == []
        assert self.engine._existing_groups == []


class TestAutoCreationEngineLoadRules:
    """Tests for rule loading methods."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.engine = AutoCreationEngine(self.client)

    @patch("auto_creation_engine.get_session")
    def test_load_rules_all_enabled(self, mock_get_session):
        """Load all enabled rules sorted by priority."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_rule1 = MagicMock()
        mock_rule1.id = 1
        mock_rule1.priority = 0

        mock_rule2 = MagicMock()
        mock_rule2.id = 2
        mock_rule2.priority = 1

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = [mock_rule1, mock_rule2]
        mock_session.query.return_value = mock_query

        rules = asyncio.get_event_loop().run_until_complete(
            self.engine._load_rules()
        )

        assert len(rules) == 2
        mock_session.close.assert_called_once()

    @patch("auto_creation_engine.get_session")
    def test_load_rules_specific_ids(self, mock_get_session):
        """Load specific rules by ID."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_rule = MagicMock()
        mock_rule.id = 1

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = [mock_rule]
        mock_session.query.return_value = mock_query

        rules = asyncio.get_event_loop().run_until_complete(
            self.engine._load_rules(rule_ids=[1])
        )

        assert len(rules) == 1


class TestAutoCreationEngineFetchStreams:
    """Tests for stream fetching methods."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.get_m3u_accounts = AsyncMock(return_value=[
            {"id": 1, "name": "Provider A"},
            {"id": 2, "name": "Provider B"},
        ])
        self.client.get_streams_by_m3u = AsyncMock(return_value=[
            {"id": 101, "name": "ESPN HD", "group_title": "Sports"},
            {"id": 102, "name": "CNN HD", "group_title": "News"},
        ])
        self.engine = AutoCreationEngine(self.client)

    @patch("auto_creation_engine.get_session")
    def test_fetch_streams_all_accounts(self, mock_get_session):
        """Fetch streams from all M3U accounts."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_session.query.return_value.filter.return_value.all.return_value = []

        streams = asyncio.get_event_loop().run_until_complete(
            self.engine._fetch_streams()
        )

        # 2 accounts * 2 streams each
        assert len(streams) == 4
        assert all(isinstance(s, StreamContext) for s in streams)

    @patch("auto_creation_engine.get_session")
    def test_fetch_streams_specific_accounts(self, mock_get_session):
        """Fetch streams from specific M3U accounts."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_session.query.return_value.filter.return_value.all.return_value = []

        streams = asyncio.get_event_loop().run_until_complete(
            self.engine._fetch_streams(m3u_account_ids=[1])
        )

        # 1 account * 2 streams
        assert len(streams) == 2

    @patch("auto_creation_engine.get_session")
    def test_fetch_streams_api_failure(self, mock_get_session):
        """Fetch streams handles API failure gracefully."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_session.query.return_value.filter.return_value.all.return_value = []

        self.client.get_streams_by_m3u = AsyncMock(side_effect=Exception("API error"))

        streams = asyncio.get_event_loop().run_until_complete(
            self.engine._fetch_streams()
        )

        assert streams == []

    @patch("auto_creation_engine.get_session")
    def test_fetch_streams_from_rules(self, mock_get_session):
        """Fetch streams from accounts specified in rules."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_session.query.return_value.filter.return_value.all.return_value = []

        mock_rule = MagicMock()
        mock_rule.m3u_account_id = 1

        streams = asyncio.get_event_loop().run_until_complete(
            self.engine._fetch_streams(rules=[mock_rule])
        )

        # Only account 1
        assert len(streams) == 2


class TestAutoCreationEngineRunPipeline:
    """Tests for run_pipeline method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.get_channels = AsyncMock(return_value=[])
        self.client.get_channel_groups = AsyncMock(return_value=[])
        self.client.get_m3u_accounts = AsyncMock(return_value=[
            {"id": 1, "name": "Provider A"},
        ])
        self.client.get_streams_by_m3u = AsyncMock(return_value=[
            {"id": 101, "name": "ESPN HD", "group_title": "Sports"},
        ])
        self.client.create_channel = AsyncMock(return_value={"id": 1, "name": "ESPN HD"})
        self.engine = AutoCreationEngine(self.client)

    @patch("auto_creation_engine.get_session")
    def test_run_pipeline_no_rules(self, mock_get_session):
        """Run pipeline with no enabled rules."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.run_pipeline()
        )

        assert result["success"] is True
        assert result["message"] == "No enabled rules to process"
        assert result["streams_evaluated"] == 0

    @patch("auto_creation_engine.get_session")
    def test_run_pipeline_dry_run(self, mock_get_session):
        """Run pipeline in dry run mode."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        # Mock rule
        mock_rule = MagicMock()
        mock_rule.id = 1
        mock_rule.name = "Test Rule"
        mock_rule.priority = 0
        mock_rule.enabled = True
        mock_rule.m3u_account_id = None
        mock_rule.target_group_id = None
        mock_rule.stop_on_first_match = True
        mock_rule.get_conditions.return_value = [{"type": "always"}]
        mock_rule.get_actions.return_value = [{"type": "skip"}]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = [mock_rule]
        mock_session.query.return_value = mock_query

        # Mock execution
        mock_execution = MagicMock()
        mock_execution.id = 1
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock()
        mock_session.refresh = MagicMock()
        mock_session.merge = MagicMock()

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.run_pipeline(dry_run=True)
        )

        assert result["success"] is True
        assert result["mode"] == "dry_run"
        # Stream was skipped by rule
        assert result["streams_matched"] == 1

    @patch("auto_creation_engine.get_session")
    def test_run_rule(self, mock_get_session):
        """Run specific rule."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        # Empty rules for specific ID
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = []
        mock_session.query.return_value = mock_query

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.run_rule(rule_id=1, dry_run=True)
        )

        assert result["success"] is True
        assert result["message"] == "No enabled rules to process"


class TestAutoCreationEngineRollback:
    """Tests for rollback functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.delete_channel = AsyncMock()
        self.client.delete_channel_group = AsyncMock()
        self.client.update_channel = AsyncMock()
        self.engine = AutoCreationEngine(self.client)

    @patch("auto_creation_engine.get_session")
    def test_rollback_execution_not_found(self, mock_get_session):
        """Rollback returns error if execution not found."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.rollback_execution(999)
        )

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @patch("auto_creation_engine.get_session")
    def test_rollback_execution_already_rolled_back(self, mock_get_session):
        """Rollback returns error if already rolled back."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()
        mock_execution.status = "rolled_back"
        mock_session.query.return_value.filter.return_value.first.return_value = mock_execution

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.rollback_execution(1)
        )

        assert result["success"] is False
        assert "already rolled back" in result["error"].lower()

    @patch("auto_creation_engine.get_session")
    def test_rollback_dry_run_execution(self, mock_get_session):
        """Rollback returns error for dry-run executions."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()
        mock_execution.status = "completed"
        mock_execution.mode = "dry_run"
        mock_session.query.return_value.filter.return_value.first.return_value = mock_execution

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.rollback_execution(1)
        )

        assert result["success"] is False
        assert "dry-run" in result["error"].lower()

    @patch("auto_creation_engine.get_session")
    def test_rollback_execution_success(self, mock_get_session):
        """Rollback execution successfully."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()
        mock_execution.status = "completed"
        mock_execution.mode = "execute"
        mock_execution.get_created_entities.return_value = [
            {"type": "channel", "id": 1, "name": "ESPN"},
            {"type": "group", "id": 2, "name": "Sports"},
        ]
        mock_execution.get_modified_entities.return_value = [
            {"type": "channel", "id": 3, "name": "CNN", "previous": {"logo_url": "old.png"}},
        ]
        mock_session.query.return_value.filter.return_value.first.return_value = mock_execution

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.rollback_execution(1)
        )

        assert result["success"] is True
        assert result["entities_removed"] == 2
        assert result["entities_restored"] == 1

        # Verify delete calls
        self.client.delete_channel.assert_called_once_with(1)
        self.client.delete_channel_group.assert_called_once_with(2)
        self.client.update_channel.assert_called_once_with(3, {"logo_url": "old.png"})

        # Verify execution was marked as rolled back
        assert mock_execution.status == "rolled_back"
        assert mock_execution.rolled_back_at is not None

    @patch("auto_creation_engine.get_session")
    def test_rollback_execution_api_error(self, mock_get_session):
        """Rollback handles API errors gracefully."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()
        mock_execution.status = "completed"
        mock_execution.mode = "execute"
        mock_execution.get_created_entities.return_value = [
            {"type": "channel", "id": 1, "name": "ESPN"},
        ]
        mock_execution.get_modified_entities.return_value = []
        mock_session.query.return_value.filter.return_value.first.return_value = mock_execution

        # Make delete fail
        self.client.delete_channel = AsyncMock(side_effect=Exception("API error"))

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.rollback_execution(1)
        )

        # Should still succeed (errors are logged but don't fail rollback)
        assert result["success"] is True


class TestAutoCreationEngineProcessStreams:
    """Tests for stream processing logic."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.create_channel = AsyncMock(return_value={"id": 1, "name": "Test"})
        self.client.update_channel = AsyncMock()
        self.client.create_channel_group = AsyncMock(return_value={"id": 1, "name": "Test"})
        self.engine = AutoCreationEngine(self.client)
        self.engine._existing_channels = []
        self.engine._existing_groups = []

    def test_process_streams_no_match(self):
        """Process streams with no matching rules."""
        streams = [
            StreamContext(stream_id=1, stream_name="ESPN", m3u_account_id=1, m3u_account_name="Provider")
        ]

        mock_rule = MagicMock()
        mock_rule.id = 1
        mock_rule.priority = 0
        mock_rule.m3u_account_id = 2  # Different account
        mock_rule.get_conditions.return_value = [{"type": "always"}]
        mock_rule.get_actions.return_value = [{"type": "skip"}]
        mock_rule.stop_on_first_match = True

        mock_execution = MagicMock()
        mock_execution.id = 1

        result = asyncio.get_event_loop().run_until_complete(
            self.engine._process_streams(streams, [mock_rule], mock_execution, dry_run=True)
        )

        assert result["streams_evaluated"] == 1
        assert result["streams_matched"] == 0

    def test_process_streams_match_skip(self):
        """Process streams that match a skip rule."""
        streams = [
            StreamContext(stream_id=1, stream_name="ESPN", m3u_account_id=1, m3u_account_name="Provider")
        ]

        mock_rule = MagicMock()
        mock_rule.id = 1
        mock_rule.name = "Skip Rule"
        mock_rule.priority = 0
        mock_rule.m3u_account_id = None
        mock_rule.target_group_id = None
        mock_rule.get_conditions.return_value = [{"type": "always"}]
        mock_rule.get_actions.return_value = [{"type": "skip"}]
        mock_rule.stop_on_first_match = True

        mock_execution = MagicMock()
        mock_execution.id = 1

        result = asyncio.get_event_loop().run_until_complete(
            self.engine._process_streams(streams, [mock_rule], mock_execution, dry_run=True)
        )

        assert result["streams_evaluated"] == 1
        assert result["streams_matched"] == 1
        assert result["streams_skipped"] == 1

    def test_process_streams_multiple_rules_conflict(self):
        """Process streams that match multiple rules (conflict)."""
        streams = [
            StreamContext(stream_id=1, stream_name="ESPN", m3u_account_id=1, m3u_account_name="Provider")
        ]

        mock_rule1 = MagicMock()
        mock_rule1.id = 1
        mock_rule1.name = "Rule 1"
        mock_rule1.priority = 0
        mock_rule1.m3u_account_id = None
        mock_rule1.target_group_id = None
        mock_rule1.get_conditions.return_value = [{"type": "always"}]
        mock_rule1.get_actions.return_value = [{"type": "skip"}]
        mock_rule1.stop_on_first_match = False  # Allow checking more rules

        mock_rule2 = MagicMock()
        mock_rule2.id = 2
        mock_rule2.name = "Rule 2"
        mock_rule2.priority = 1
        mock_rule2.m3u_account_id = None
        mock_rule2.target_group_id = None
        mock_rule2.get_conditions.return_value = [{"type": "always"}]
        mock_rule2.get_actions.return_value = [{"type": "skip"}]
        mock_rule2.stop_on_first_match = True

        mock_execution = MagicMock()
        mock_execution.id = 1

        with patch("auto_creation_engine.get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_get_session.return_value = mock_session

            result = asyncio.get_event_loop().run_until_complete(
                self.engine._process_streams(streams, [mock_rule1, mock_rule2], mock_execution, dry_run=True)
            )

        # Should detect conflict
        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["winning_rule_id"] == 1
        assert result["conflicts"][0]["losing_rule_ids"] == [2]

    def test_process_streams_stop_processing(self):
        """Process streams stops on stop_processing action."""
        streams = [
            StreamContext(stream_id=1, stream_name="ESPN", m3u_account_id=1, m3u_account_name="Provider"),
            StreamContext(stream_id=2, stream_name="CNN", m3u_account_id=1, m3u_account_name="Provider"),
        ]

        mock_rule = MagicMock()
        mock_rule.id = 1
        mock_rule.name = "Stop Rule"
        mock_rule.priority = 0
        mock_rule.m3u_account_id = None
        mock_rule.target_group_id = None
        mock_rule.get_conditions.return_value = [{"type": "always"}]
        mock_rule.get_actions.return_value = [{"type": "stop_processing"}]
        mock_rule.stop_on_first_match = True

        mock_execution = MagicMock()
        mock_execution.id = 1

        result = asyncio.get_event_loop().run_until_complete(
            self.engine._process_streams(streams, [mock_rule], mock_execution, dry_run=True)
        )

        # Only first stream should be processed
        assert result["streams_evaluated"] == 1


class TestAutoCreationEngineExecutionTracking:
    """Tests for execution tracking methods."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.engine = AutoCreationEngine(self.client)

    @patch("auto_creation_engine.get_session")
    def test_create_execution(self, mock_get_session):
        """Create execution record."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        execution = asyncio.get_event_loop().run_until_complete(
            self.engine._create_execution(mode="execute", triggered_by="manual")
        )

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()

    @patch("auto_creation_engine.get_session")
    def test_save_execution(self, mock_get_session):
        """Save execution record."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()

        asyncio.get_event_loop().run_until_complete(
            self.engine._save_execution(mock_execution)
        )

        mock_session.merge.assert_called_once_with(mock_execution)
        mock_session.commit.assert_called_once()

    @patch("auto_creation_engine.get_session")
    def test_record_conflict(self, mock_get_session):
        """Record conflict in database."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()
        mock_execution.id = 1

        stream = StreamContext(
            stream_id=101,
            stream_name="ESPN HD",
            m3u_account_id=1,
        )

        winning_rule = MagicMock()
        winning_rule.id = 1
        winning_rule.name = "Rule 1"
        winning_rule.priority = 0

        losing_rule = MagicMock()
        losing_rule.id = 2

        asyncio.get_event_loop().run_until_complete(
            self.engine._record_conflict(
                execution=mock_execution,
                stream=stream,
                winning_rule=winning_rule,
                losing_rules=[losing_rule],
                conflict_type="duplicate_match"
            )
        )

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    @patch("auto_creation_engine.get_session")
    def test_update_rule_stats(self, mock_get_session):
        """Update rule statistics after execution."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_rule = MagicMock()
        mock_rule.id = 1

        results = {
            "channels_created": 5,
            "streams_matched": 10,
        }

        asyncio.get_event_loop().run_until_complete(
            self.engine._update_rule_stats([mock_rule], results)
        )

        assert mock_rule.last_run_at is not None
        mock_session.merge.assert_called_once_with(mock_rule)
        mock_session.commit.assert_called_once()


class TestAutoCreationEngineRollbackHelpers:
    """Tests for rollback helper methods."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.delete_channel = AsyncMock()
        self.client.delete_channel_group = AsyncMock()
        self.client.update_channel = AsyncMock()
        self.engine = AutoCreationEngine(self.client)

    def test_rollback_created_channel(self):
        """Rollback created channel by deleting it."""
        entity = {"type": "channel", "id": 1, "name": "ESPN"}

        asyncio.get_event_loop().run_until_complete(
            self.engine._rollback_created_entity(entity)
        )

        self.client.delete_channel.assert_called_once_with(1)

    def test_rollback_created_group(self):
        """Rollback created group by deleting it."""
        entity = {"type": "group", "id": 1, "name": "Sports"}

        asyncio.get_event_loop().run_until_complete(
            self.engine._rollback_created_entity(entity)
        )

        self.client.delete_channel_group.assert_called_once_with(1)

    def test_rollback_created_entity_api_error(self):
        """Rollback handles API error gracefully."""
        self.client.delete_channel = AsyncMock(side_effect=Exception("API error"))
        entity = {"type": "channel", "id": 1, "name": "ESPN"}

        # Should not raise
        asyncio.get_event_loop().run_until_complete(
            self.engine._rollback_created_entity(entity)
        )

    def test_rollback_modified_channel(self):
        """Rollback modified channel by restoring state."""
        entity = {
            "type": "channel",
            "id": 1,
            "name": "ESPN",
            "previous": {"logo_url": "old.png", "tvg_id": "ESPN.US"}
        }

        asyncio.get_event_loop().run_until_complete(
            self.engine._rollback_modified_entity(entity)
        )

        self.client.update_channel.assert_called_once_with(1, {"logo_url": "old.png", "tvg_id": "ESPN.US"})

    def test_rollback_modified_entity_no_previous(self):
        """Rollback skips entity with no previous state."""
        entity = {"type": "channel", "id": 1, "name": "ESPN"}

        asyncio.get_event_loop().run_until_complete(
            self.engine._rollback_modified_entity(entity)
        )

        self.client.update_channel.assert_not_called()

    def test_rollback_modified_entity_api_error(self):
        """Rollback handles API error gracefully."""
        self.client.update_channel = AsyncMock(side_effect=Exception("API error"))
        entity = {
            "type": "channel",
            "id": 1,
            "name": "ESPN",
            "previous": {"logo_url": "old.png"}
        }

        # Should not raise
        asyncio.get_event_loop().run_until_complete(
            self.engine._rollback_modified_entity(entity)
        )


class TestAutoCreationEngineIntegration:
    """Integration-style tests for the engine."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.get_channels = AsyncMock(return_value=[
            {"id": 1, "name": "ESPN", "channel_number": 100, "streams": [101]},
        ])
        self.client.get_channel_groups = AsyncMock(return_value=[
            {"id": 1, "name": "Sports"},
        ])
        self.client.get_m3u_accounts = AsyncMock(return_value=[
            {"id": 1, "name": "Provider A"},
        ])
        self.client.get_streams_by_m3u = AsyncMock(return_value=[
            {
                "id": 201,
                "name": "ESPN2 HD",
                "group_title": "Sports",
                "tvg_id": "ESPN2.US",
                "logo": "http://example.com/espn2.png",
            },
            {
                "id": 202,
                "name": "CNN HD",
                "group_title": "News",
                "tvg_id": "CNN.US",
            },
        ])
        self.client.create_channel = AsyncMock(return_value={"id": 2, "name": "ESPN2 HD"})
        self.engine = AutoCreationEngine(self.client)

    @patch("auto_creation_engine.get_session")
    def test_full_pipeline_dry_run(self, mock_get_session):
        """Run full pipeline in dry-run mode with real stream data."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        # Mock rule that matches streams by name pattern
        mock_rule = MagicMock()
        mock_rule.id = 1
        mock_rule.name = "Create ESPN Channels"
        mock_rule.priority = 0
        mock_rule.enabled = True
        mock_rule.m3u_account_id = None
        mock_rule.target_group_id = 1
        mock_rule.stop_on_first_match = True
        mock_rule.get_conditions.return_value = [
            {"type": "stream_name_contains", "value": "ESPN"}
        ]
        mock_rule.get_actions.return_value = [
            {"type": "create_channel", "params": {"name_template": "{stream_name}"}}
        ]

        # Rules query
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = [mock_rule]
        mock_session.query.return_value = mock_query

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.run_pipeline(dry_run=True)
        )

        assert result["success"] is True
        assert result["mode"] == "dry_run"
        assert result["streams_evaluated"] == 2
        assert result["streams_matched"] == 1  # Only ESPN2 matches
        assert len(result["dry_run_results"]) == 1
        assert "ESPN2" in result["dry_run_results"][0]["stream_name"]
